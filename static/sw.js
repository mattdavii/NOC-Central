// Operational data must never be replayed from an offline cache as live state.
const APP_CACHE_PREFIXES = ['noc-cache-', 'nexopulse-cache-'];

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(
    caches.keys()
        .then(keys => Promise.all(keys
            .filter(key => APP_CACHE_PREFIXES.some(prefix => key.startsWith(prefix)))
            .map(key => caches.delete(key))))
        .then(() => self.clients.claim())
));
