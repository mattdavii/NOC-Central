(function () {
    'use strict';

    const meta = document.querySelector('meta[name="csrf-token"]');
    const csrfToken = meta ? meta.getAttribute('content') : '';
    const originalFetch = window.fetch.bind(window);
    const safeMethods = new Set(['GET', 'HEAD', 'OPTIONS']);

    window.fetch = function (input, init) {
        const options = Object.assign({}, init || {});
        const method = String(options.method || (typeof input !== 'string' && input.method) || 'GET').toUpperCase();
        const target = typeof input === 'string' ? new URL(input, window.location.href) : new URL(input.url, window.location.href);
        if (csrfToken && target.origin === window.location.origin && !safeMethods.has(method)) {
            const headers = new Headers(options.headers || (typeof input !== 'string' ? input.headers : undefined));
            headers.set('X-CSRF-Token', csrfToken);
            options.headers = headers;
        }
        return originalFetch(input, options);
    };
})();
