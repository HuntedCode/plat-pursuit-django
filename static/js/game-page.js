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
        const minibar = document.querySelector('.gd-minibar');

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
            // The minibar's identity icon + per-view controls follow the active tab (CSS gates on
            // data-mb-active, the List-detail contract).
            if (minibar) minibar.dataset.mbActive = name;
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
                // Scroll the TABS ROW to the top, not the panel: the user lands seeing the
                // switcher + the panel below it. Browser-repro'd bug: scrolling the panel with
                // the hero above it went nowhere, because hiding the tall Lists panel shrinks the
                // document below the distance needed -- the min-height on the panels (CSS) is the
                // other half of this fix, guaranteeing the destination is reachable.
                const row = document.getElementById('gp-tabs-row');
                const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                if (row) row.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
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

        // ── Sticky minibar (List detail's pattern) ─────────────────────────────────────────────
        // The bar + sentinel live OUTSIDE #gp-viewport, so list swaps never tear them out and one
        // StickyReveal.init() holds for the page's life. The jump select mirrors the grid's group
        // nav (chips or the jump-menu rows -- both carry data-gd-groupjump + .gd-groupnav__name),
        // rebuilt after each swap since each list brings its own groups. Options go in via
        // new Option(text, value) so pack names land as text, never HTML.
        const jumpSel = document.querySelector('[data-minibar-groupjump]');
        function syncGroupJump() {
            if (!jumpSel) return;
            const rows = Array.from(document.querySelectorAll('#gp-viewport [data-gd-groupjump]'));
            if (rows.length <= 1) { jumpSel.hidden = true; return; }
            jumpSel.hidden = false;
            jumpSel.textContent = '';
            const ph = new Option('Jump to pack…', '');
            ph.disabled = true; ph.selected = true;
            jumpSel.add(ph);
            rows.forEach((r) => {
                const name = r.querySelector('.gd-groupnav__name');
                jumpSel.add(new Option(name ? name.textContent : r.dataset.gdGroupjump, r.dataset.gdGroupjump));
            });
        }
        syncGroupJump();
        if (jumpSel) {
            jumpSel.addEventListener('change', () => {
                const target = document.getElementById(jumpSel.value);
                jumpSel.selectedIndex = 0;   // reset to the placeholder so the same pack can be re-picked
                if (!target) return;
                const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                target.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
            });
        }
        document.body.addEventListener('htmx:afterSwap', (e) => {
            if (viewport && e.detail.target === viewport) syncGroupJump();
        });
        if (PP.StickyReveal) PP.StickyReveal.init();

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
