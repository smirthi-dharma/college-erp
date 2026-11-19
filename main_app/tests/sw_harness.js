// Runs the real service worker file in a tiny fake worker scope and reports what it did. Used by test_pwa.py (needs Node).
// Usage: node sw_harness.js <path to the rendered sw.js>
const fs = require('fs');
const vm = require('vm');

const ORIGIN = 'https://demo.example.org';
const code = fs.readFileSync(process.argv[2], 'utf8');

const stores = new Map();           // cache name -> Map(url -> Response)
const log = { cachePuts: [], addAll: [], deleted: [], skipWaiting: false, claimed: false };
let networkUp = true;
let networkBody = 'network page';

function makeCache(name) {
  if (!stores.has(name)) stores.set(name, new Map());
  const store = stores.get(name);
  const key = (r) => new URL(typeof r === 'string' ? r : r.url, ORIGIN).href;
  return {
    addAll: async (urls) => { log.addAll.push([name, urls]); for (const u of urls) store.set(key(u), new Response('cached ' + u)); },
    put: async (r, response) => { log.cachePuts.push([name, key(r)]); store.set(key(r), response); },
    match: async (r) => store.get(key(r)),
  };
}
const caches = {
  open: async (name) => makeCache(name),
  match: async (r) => { for (const [, store] of stores) { const hit = store.get(new URL(typeof r === 'string' ? r : r.url, ORIGIN).href); if (hit) return hit; } },
  keys: async () => [...stores.keys()],
  delete: async (name) => { log.deleted.push(name); return stores.delete(name); },
};

const handlers = {};
const self = {
  location: { origin: ORIGIN }, caches,
  addEventListener: (type, fn) => { handlers[type] = fn; },
  skipWaiting: async () => { log.skipWaiting = true; },
  clients: { claim: async () => { log.claimed = true; } },
};
const sandbox = { self, caches, URL, Response, Request, Promise, console,
  fetch: async (request) => { if (!networkUp) throw new TypeError('offline'); return new Response(networkBody, { status: 200 }); } };
vm.createContext(sandbox);
vm.runInContext(code, sandbox);

async function dispatch(type, init) {
  let work = null;
  let answer = null;
  const event = Object.assign({}, init, {
    waitUntil: (p) => { work = p; }, respondWith: (p) => { answer = p; },
  });
  handlers[type](event);
  if (work) await work;
  return answer === null ? { intercepted: false } : { intercepted: true, response: await answer };
}
const req = (path, method = 'GET', mode = 'cors', origin = ORIGIN) => ({ url: origin + path, method, mode });
const text = async (r) => (r && r.response ? await r.response.text() : null);

(async () => {
  const out = {};
  await dispatch('install', {});
  out.install = { addAll: log.addAll, skipWaiting: log.skipWaiting };

  // an old cache from a previous version and one that is not ours
  stores.set('erp-shell-OLD', new Map()); stores.set('erp-static-OLD', new Map()); stores.set('unrelated-cache', new Map());
  await dispatch('activate', {});
  out.activate = { deleted: log.deleted.slice(), kept: [...stores.keys()], claimed: log.claimed };
  log.cachePuts.length = 0;

  networkUp = true;
  let r = await dispatch('fetch', { request: req('/students/5/', 'GET', 'navigate') });
  out.navigateOnline = { intercepted: r.intercepted, body: await text(r), puts: log.cachePuts.length };

  networkUp = false;
  r = await dispatch('fetch', { request: req('/students/5/', 'GET', 'navigate') });
  out.navigateOffline = { intercepted: r.intercepted, body: await text(r), puts: log.cachePuts.length };

  networkUp = true;
  const skipped = {};
  for (const [label, request] of [['post', req('/attendance/class/1/', 'POST', 'cors')], ['navigatePost', req('/login', 'POST', 'navigate')],
      ['crossOrigin', req('/npm/chart.js', 'GET', 'no-cors', 'https://cdn.jsdelivr.net')], ['photo', req('/photo/3/')],
      ['ajaxData', req('/students/5/')], ['attendance', req('/attendance/class/1/')], ['json', req('/get_attendance')],
      ['staticLookalike', req('/not-static/app.css')]]) {
    const result = await dispatch('fetch', { request });
    skipped[label] = result.intercepted;
  }
  out.notIntercepted = skipped;
  out.putsAfterSkipped = log.cachePuts.length;

  networkBody = 'css v1';
  r = await dispatch('fetch', { request: req('/static/dist/app.css') });
  out.staticFirst = { intercepted: r.intercepted, body: await text(r) };
  await new Promise((resolve) => setTimeout(resolve, 20));
  out.staticPuts = log.cachePuts.filter((p) => p[1].endsWith('/static/dist/app.css')).map((p) => p[0]);
  networkUp = false;
  r = await dispatch('fetch', { request: req('/static/dist/app.css') });
  out.staticOffline = { intercepted: r.intercepted, body: await text(r) };
  r = await dispatch('fetch', { request: req('/static/never-seen.css') });
  out.staticOfflineUnseen = { intercepted: r.intercepted, response: r.response === undefined ? 'none' : 'something' };
  out.allPuts = log.cachePuts.map((p) => p[1].replace(ORIGIN, ''));
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
