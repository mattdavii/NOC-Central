(function () {
    const STORAGE_KEY = 'nexopulse-theme';
    const THEME_COLORS = { dark: '#082E56', light: '#F3F8FC' };
    const root = document.documentElement;

    // Navegação privada ou cookies bloqueados podem impedir o acesso ao
    // localStorage; o tema continua funcionando, apenas sem lembrar a escolha.
    function readSaved() {
        try { return localStorage.getItem(STORAGE_KEY); } catch (error) { return null; }
    }

    function save(theme) {
        try { localStorage.setItem(STORAGE_KEY, theme); } catch (error) { /* sem persistência */ }
    }

    function preferredTheme() {
        const saved = readSaved();
        if (saved === 'light' || saved === 'dark') return saved;
        return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }

    function apply(theme) {
        root.dataset.theme = theme;
        root.style.colorScheme = theme;
        const themeColor = document.querySelector('meta[name="theme-color"]');
        if (themeColor) themeColor.setAttribute('content', THEME_COLORS[theme]);
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
            save(next);
            apply(next);
        }
    };

    // No celular, tabelas .cards-mobile viram cartões; cada célula mostra o
    // título da sua coluna (via data-label, lido pelo CSS).
    function labelTables() {
        document.querySelectorAll('table.cards-mobile').forEach(table => {
            const titulos = [...table.querySelectorAll('thead th')].map(th => th.textContent.trim());
            table.querySelectorAll('tbody tr').forEach(tr => {
                [...tr.children].forEach((td, i) => { if (!td.hasAttribute('data-label')) td.dataset.label = titulos[i] || ''; });
            });
        });
    }

    apply(preferredTheme());
    document.addEventListener('DOMContentLoaded', () => {
        apply(root.dataset.theme || preferredTheme());
        registerApp();
        labelTables();
    });
}());
