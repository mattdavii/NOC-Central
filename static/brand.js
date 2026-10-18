(function () {
    const STORAGE_KEY = 'nexopulse-theme';
    const root = document.documentElement;

    function preferredTheme() {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved === 'light' || saved === 'dark') return saved;
        return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }

    function apply(theme) {
        root.dataset.theme = theme;
        root.style.colorScheme = theme;
        document.querySelectorAll('.theme-toggle').forEach(button => {
            const light = theme === 'light';
            button.setAttribute('aria-label', light ? 'Ativar tema escuro' : 'Ativar tema claro');
            const icon = button.querySelector('i');
            if (icon) icon.className = light ? 'fa-solid fa-moon' : 'fa-solid fa-sun';
        });
    }

    async function registerApp() {
        if (!('serviceWorker' in navigator)) return;
        try {
            const registrations = await navigator.serviceWorker.getRegistrations();
            await Promise.all(registrations
                .filter(existing => new URL(existing.scope).pathname.startsWith('/static/'))
                .map(existing => existing.unregister()));
            const registration = await navigator.serviceWorker.register('/sw.js?v=nexopulse-2', {
                scope: '/',
                updateViaCache: 'none'
            });
            await registration.update();
        } catch (error) {
            console.warn('Não foi possível atualizar o aplicativo NexoPulse.', error);
        }
    }

    window.NexoPulseTheme = {
        apply,
        toggle() {
            const next = root.dataset.theme === 'light' ? 'dark' : 'light';
            localStorage.setItem(STORAGE_KEY, next);
            apply(next);
        }
    };

    apply(preferredTheme());
    document.addEventListener('DOMContentLoaded', () => {
        apply(root.dataset.theme || preferredTheme());
        registerApp();
    });
}());
