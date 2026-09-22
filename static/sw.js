// Operational data must never be replayed from an offline cache as live state.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('noc-cache-')).map(key => caches.delete(key)))).then(() => self.clients.claim())
));
