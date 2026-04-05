const CACHE_NAME = 'noc-cache-v1';

// Opcional: Arquivos para manter em cache
const urlsToCache = ['/'];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => {
            return cache.addAll(urlsToCache);
        })
    );
});

// Busca na rede primeiro, se falhar tenta o cache (ideal para dashboards ao vivo)
self.addEventListener('fetch', event => {
    event.respondWith(
        fetch(event.request).catch(() => caches.match(event.request))
    );
});