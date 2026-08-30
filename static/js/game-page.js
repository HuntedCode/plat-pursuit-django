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

        // Hero jumps ("More about this game", the players headline) -> the named concept tab.
        // The players <a> keeps ?view=ratings as its no-JS fallback; with JS we switch in place.
        document.querySelectorAll('[data-gp-goto]').forEach((el) => {
            el.addEventListener('click', (e) => {
                const view = el.dataset.gpGoto;
                if (!VIEW_ORDER.includes(view)) return;
                e.preventDefault();
                showView(view);
                const panel = panels[view];
                if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
            });
        });

        // Hero tallies (the players headline): countUp reads data-countup or falls back to the
        // element's own text, so the SSR number is the reduced-motion/no-JS truth.
        if (PP.countUp) {
            document.querySelectorAll('.gd-hero [data-gd-countup]').forEach((el) => PP.countUp(el));
        }

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
        // Whole document, not just the viewport: the About tab ships its time-to-beat bars at 0%
        // for THIS call to grow (game_about_card's own comment names the contract), and the
        // ratings band bars sit outside #gp-viewport too.
        fillBars();

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
            let lastNp = viewport.dataset.defaultNp || '';
            const chipOrder = chips.map((c) => c.dataset.np);
            document.body.addEventListener('htmx:afterSwap', (e) => {
                if (e.detail.target !== viewport) return;
                viewport.classList.remove('is-swapping', 'pointer-events-none');
                // pathInfo.requestPath is an htmx 2.0.4 internal (the only consumer in this
                // repo); if a bump renames it, np silently falls back to the default and the
                // chips desync -- the parity source-guard test names this line.
                const params = new URLSearchParams(e.detail.pathInfo.requestPath.split('?')[1] || '');
                const np = params.get('list') || defaultNp;
                // State first (chips + URL), decoration after -- never strand the switcher.
                syncChips(np);
                // push, not replace: the chips are real <a>s whose navigation htmx swallowed --
                // stopping a navigation without pushing is what strands the Back button
                // (utils.js's own rule for swallowed-link tabs).
                PP.syncViewParam(np, { param: 'list', default: defaultNp, push: true });
                PP.slideViewIn(viewport, lastNp, np, chipOrder);
                lastNp = np;
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

        // ── Ratings tab: per-group selector (ported from game-detail.js ratingsTab, read-only:
        //    no quick-rate here -- concept_tabs_readonly gates those CTAs server-side). ──
        (function ratingsSelector() {
            const root = document.querySelector('[data-gd-rate]');
            if (!root) return;
            const drop = root.querySelector('[data-rate-drop]');
            const dropBtn = root.querySelector('[data-rate-drop-toggle]');
            function closeDrop() {
                if (!dropBtn) return;
                dropBtn.setAttribute('aria-expanded', 'false');
                const m = drop && drop.querySelector('.gd-rate__dropmenu');
                if (m) m.hidden = true;
            }
            function selectGroup(ctgId, srcEl) {
                root.querySelectorAll('[data-rate-panel]').forEach((panel) => {
                    panel.classList.toggle('is-hidden', panel.dataset.ratePanel !== ctgId);
                });
                root.querySelectorAll('.gd-rate__segchip[data-rate-ctg]').forEach((c) => {
                    const on = c.dataset.rateCtg === ctgId;
                    c.classList.toggle('is-active', on);
                    c.setAttribute('aria-pressed', on ? 'true' : 'false');
                });
                if (drop && dropBtn) {
                    dropBtn.classList.toggle('is-active', !!(srcEl && srcEl.classList.contains('gd-rate__dropitem')));
                }
                fillBars(root);
            }
            root.addEventListener('click', (e) => {
                const toggle = e.target.closest('[data-rate-drop-toggle]');
                if (toggle) {
                    const open = toggle.getAttribute('aria-expanded') === 'true';
                    toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
                    const menu = drop && drop.querySelector('.gd-rate__dropmenu');
                    if (menu) menu.hidden = open;
                    return;
                }
                const chip = e.target.closest('[data-rate-ctg]');
                if (chip) { selectGroup(chip.dataset.rateCtg, chip); closeDrop(); }
            });
            if (drop) {
                document.addEventListener('click', (e) => { if (!drop.contains(e.target)) closeDrop(); });
                document.addEventListener('keydown', (e) => {
                    if (e.key === 'Escape' && dropBtn && dropBtn.getAttribute('aria-expanded') === 'true') { closeDrop(); dropBtn.focus(); }
                });
            }
        })();
    });
})();
