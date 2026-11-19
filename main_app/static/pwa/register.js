/* Registers the service worker and offers "Install app" when the browser says the app can be installed. */
(function () {
  'use strict';
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function () { /* the app works without it */ });
    });
  }

  var KEY = 'erp-install-dismissed';
  var deferred = null;

  function remembered() {
    try { return window.localStorage.getItem(KEY) === '1'; } catch (e) { return false; }
  }
  function remember() {
    try { window.localStorage.setItem(KEY, '1'); } catch (e) { /* private mode: ask again next time */ }
  }
  function hide() {
    var box = document.getElementById('pwa-install-box');
    if (box) { box.remove(); }
  }
  function show() {
    if (document.getElementById('pwa-install-box') || remembered()) { return; }
    var box = document.createElement('div');
    box.id = 'pwa-install-box';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-label', 'Install the app');
    box.style.cssText = 'position:fixed;left:12px;right:12px;bottom:12px;z-index:2000;max-width:420px;margin:0 auto;' +
      'background:#fff;border-radius:14px;box-shadow:0 4px 20px rgba(0,0,0,.25);padding:12px 14px;display:flex;' +
      'align-items:center;gap:10px;font:14px system-ui,sans-serif;color:#1f2430';
    var text = document.createElement('span');
    text.style.flex = '1';
    text.textContent = 'Install this app on your phone for faster opening.';
    var install = document.createElement('button');
    install.type = 'button';
    install.textContent = 'Install';
    install.style.cssText = 'border:0;border-radius:8px;background:#5e64ff;color:#fff;font-weight:600;padding:9px 14px';
    var later = document.createElement('button');
    later.type = 'button';
    later.textContent = 'Not now';
    later.style.cssText = 'border:0;background:transparent;color:#4a5163;padding:9px 6px';
    install.addEventListener('click', function () {
      if (!deferred) { return; }
      deferred.prompt();
      deferred.userChoice.then(function () { deferred = null; hide(); });
    });
    later.addEventListener('click', function () { remember(); hide(); });
    box.appendChild(text); box.appendChild(install); box.appendChild(later);
    document.body.appendChild(box);
  }

  window.addEventListener('beforeinstallprompt', function (event) {
    event.preventDefault();
    deferred = event;
    show();
  });
  window.addEventListener('appinstalled', function () { deferred = null; hide(); });
}());
