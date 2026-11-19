/* Service worker. Keeps ONLY the offline page and public static files. Never an HTML page of the app, a picture, a form post
   or any data: see main_app/pwa.py for why. Change nothing here without running main_app/tests/test_pwa.py. */
'use strict';
const CONFIG = {{ config_json|safe }};
const SHELL_CACHE = 'erp-shell-' + CONFIG.version;
const STATIC_CACHE = 'erp-static-' + CONFIG.version;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll([CONFIG.offlineUrl].concat(CONFIG.precache)))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys
        .filter((key) => key.startsWith('erp-') && key !== SHELL_CACHE && key !== STATIC_CACHE)
        .map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') { return; }                       // forms and AJAX posts always go to the server
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) { return; }            // other hosts (the CDN, reCAPTCHA) are not ours to touch

  if (request.mode === 'navigate') {                              // opening a page: the network, or the offline page
    event.respondWith(fetch(request).catch(() => caches.match(CONFIG.offlineUrl)));
    return;
  }

  if (url.pathname.startsWith('/static/')) {                      // public files only: show the saved copy, refresh it quietly
    event.respondWith(
      caches.open(STATIC_CACHE).then((cache) => cache.match(request).then((saved) => {
        const fresh = fetch(request).then((response) => {
          if (response.ok) { cache.put(request, response.clone()); }
          return response;
        }).catch(() => saved);
        return saved || fresh;
      }))
    );
  }
  // everything else (pages' data, /photo/, /attendance/ ...) is not intercepted: it goes to the network as usual
});
