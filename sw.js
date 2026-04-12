// whatson.movie Service Worker
const CACHE_NAME = 'whatson-v1';
const OFFLINE_URL = '/listings/';

// Assets to pre-cache on install
const PRECACHE = [
  '/',
  '/listings/',
  '/preferences/',
];

// Install — pre-cache key pages
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(PRECACHE).catch(() => {});
    })
  );
  self.skipWaiting();
});

// Activate — clean up old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(key => key !== CACHE_NAME)
            .map(key => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

// Fetch — network first, fall back to cache
self.addEventListener('fetch', event => {
  // Only handle GET requests for our domain
  if (event.request.method !== 'GET') return;
  if (!event.request.url.startsWith(self.location.origin)) return;

  event.respondWith(
    fetch(event.request)
      .then(response => {
        // Cache successful responses for HTML pages
        if (response.ok && event.request.headers.get('accept')?.includes('text/html')) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() => {
        // Offline fallback — serve cached version if available
        return caches.match(event.request)
          .then(cached => cached || caches.match(OFFLINE_URL));
      })
  );
});
