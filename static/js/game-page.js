// Game page (concept-level): concept tabs + the list switcher.
// Reuses the shared primitives (wireTablist / slideViewIn / igniteTab / syncViewParam /
// onPageReady) -- see docs/design/rebuild/rebuild-playbook.md. State ordering follows
// game-detail.js: panels, chips and URL are fully synced BEFORE any decorative motion, so a fault
// in a flourish can never strand the switcher.
(function () {
    'use strict';

    const PP = window.PlatPursuit || {};

    PP.onPageReady(function () {
        const page = document.getElementById('gp-switch');
        if (!page) return;

        // ── Concept tabs (?view=, default 'lists') ────────────────────────────────────────────
        const VIEW_ORDER = ['lists', 'ratings', 'about'];
        const tabs = Array.from(page.querySelectorAll('[role="tab"]'));
        const panels = {};
        VIEW_ORDER.forEach((v) => { panels[v] = document.getElementById('gp-view-' + v); });

        let currentView = 'lists';

        function showView(name, opts) {
            if (!panels[name] || name === currentView) return;
            const fromName = currentView;
            currentView = name;
            VIEW_ORDER.forEach((v) => { if (panels[v]) panels[v].hidden = (v !== name); });
            tabs.forEach((t) => {
                const active = t.dataset.view === name;
                t.classList.toggle('is-active', active);
                t.setAttribute('aria-selected', active ? 'true' : 'false');
            });
            tablist.syncTabindex();
            PP.syncViewParam(name, { default: 'lists' });
            // Decorative motion last (the game-detail.js ordering rule).
            PP.slideViewIn(panels[name], fromName, name, VIEW_ORDER);
            if (!opts || !opts.silent) {
                const chip = tabs.find((t) => t.dataset.view === name);
                if (chip) PP.igniteTab(chip);
            }
        }

        // wireTablist's automatic model owns the click/arrow handlers; onSelect is the contract.
        const tablist = PP.wireTablist(tabs, {
            onSelect: (tab) => showView(tab.dataset.view),
        });

        const initialView = new URLSearchParams(window.location.search).get('view');
        if (initialView && VIEW_ORDER.includes(initialView)) showView(initialView, { silent: true });

        // ── List switcher (?list=, default carried on the viewport) ───────────────────────────
        const viewport = document.getElementById('gp-viewport');
        const lswitch = document.querySelector('.gp-lswitch');
        const defaultNp = viewport ? viewport.dataset.defaultNp : '';

        function fillBars(root) {
            (root || document).querySelectorAll('[data-gd-fill] .pp-horizon__fill').forEach((fill) => {
                const bar = fill.closest('[data-gd-fill]');
                const target = (bar.dataset.gdFill || '0') + '%';
                requestAnimationFrame(() => requestAnimationFrame(() => bar.style.setProperty('--horizon-progress', target)));
            });
        }
        fillBars(viewport);

        if (lswitch && viewport) {
            const chips = Array.from(lswitch.querySelectorAll('[role="tab"]'));
            // manual: chips are real <a>s doing HTMX work -- arrows move focus, Enter/click activate.
            PP.wireTablist(chips, { manual: true });

            function syncChips(np) {
                chips.forEach((c) => {
                    const active = c.dataset.np === np;
                    c.classList.toggle('is-active', active);
                    c.setAttribute('aria-selected', active ? 'true' : 'false');
                });
            }

            document.body.addEventListener('htmx:beforeRequest', (e) => {
                if (e.detail.target === viewport) {
                    viewport.classList.add('is-swapping', 'pointer-events-none');
                }
            });
            document.body.addEventListener('htmx:afterSwap', (e) => {
                if (e.detail.target !== viewport) return;
                viewport.classList.remove('is-swapping', 'pointer-events-none');
                const params = new URLSearchParams(e.detail.pathInfo.requestPath.split('?')[1] || '');
                const np = params.get('list') || defaultNp;
                // State first (chips + URL), decoration after -- never strand the switcher.
                syncChips(np);
                PP.syncViewParam(np, { param: 'list', default: defaultNp });
                fillBars(viewport);
                const chip = chips.find((c) => c.dataset.np === np);
                if (chip) PP.igniteTab(chip);
            });
            document.body.addEventListener('htmx:afterRequest', (e) => {
                // Error safety net (game-detail.js pattern): clear the dim unless this was an
                // hx-sync abort superseded by a newer request.
                if (e.detail.target === viewport && e.detail.xhr && e.detail.xhr.status !== 0) {
                    viewport.classList.remove('is-swapping', 'pointer-events-none');
                }
            });
        }
    });
})();
