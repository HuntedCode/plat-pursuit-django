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
            // First arrival on Ratings: the tab's entrance (stagger + count-ups + star grow) --
            // one-shot, guarded inside reveal itself.
            if (name === 'ratings' && PP.RatingsTab) PP.RatingsTab.reveal(panels[name]);
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

        // Hero jumps (the facts line's players count) -> the named concept tab.
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
        if (PP.StickyReveal) PP.StickyReveal.init();

        // ── The control row (GL-10): a standing form OUTSIDE the viewport ─────────────────────────
        // Two things swap the viewport: a list chip (its own ?list= link, so that list starts
        // unfiltered) and this form (the list rides in its hidden input). After either, the row is
        // told what landed: the count, the list's own page for its link, and whether the list has DLC.
        const filterForm = document.getElementById('gp-filter-form');
        const listInput = filterForm ? filterForm.querySelector('[data-gp-list-input]') : null;
        const listLink = filterForm ? filterForm.querySelector('[data-gd-listlink]') : null;
        const dlcField = filterForm ? filterForm.querySelector('[data-gd-dlc]') : null;
        const controlRow = filterForm ? filterForm.querySelector('.gd-controls') : null;
        // The row's count, bar and filter badge are the shared PP.TrophyControls (trophy-grid.js).
        const controls = () => (controlRow ? PP.TrophyControls : null);

        function syncControls() {
            const meta = viewport && viewport.querySelector('[data-gp-vpmeta]');
            if (!meta || !controls()) return;
            controls().setCount(controlRow, parseInt(meta.dataset.count, 10));
            if (listLink && meta.dataset.listUrl) listLink.href = meta.dataset.listUrl;
            if (dlcField) dlcField.hidden = !meta.dataset.hasDlc;
            controls().sync(controlRow);
        }

        // A list switch starts the new list unfiltered (its chip's link carries no filters), so the row
        // goes back to its defaults to say so. Values only: nothing here fires `change`, which would
        // send the form's own request on top of the chip's.
        function resetControls() {
            if (!filterForm) return;
            filterForm.querySelectorAll('input[type="checkbox"]').forEach((c) => { c.checked = false; });
            filterForm.querySelectorAll('input[type="radio"][value="default"]').forEach((r) => { r.checked = true; });
            // The legacy "Earned" chip shows only while an old link applies it; a fresh list does not.
            filterForm.querySelectorAll('input[name="earned"][value="earned"]').forEach((r) => r.closest('label').remove());
            filterForm.querySelectorAll('select').forEach((sel) => { sel.selectedIndex = 0; });
            if (controls()) controls().sync(controlRow);
        }

        if (viewport) {
            document.body.addEventListener('htmx:beforeRequest', (e) => {
                if (e.detail.target !== viewport) return;
                viewport.classList.add('is-swapping', 'pointer-events-none');
                if (controls()) controls().setBusy(controlRow, true);
            });
            document.body.addEventListener('htmx:afterRequest', (e) => {
                // Error safety net (game-detail.js pattern): clear the dim and the bar unless this was an
                // hx-sync abort superseded by a newer request, which owns them now.
                if (e.detail.target !== viewport || !e.detail.xhr || e.detail.xhr.status === 0) return;
                viewport.classList.remove('is-swapping', 'pointer-events-none');
                if (controls()) controls().setBusy(controlRow, false);
            });
            document.body.addEventListener('htmx:afterSwap', (e) => {
                if (e.detail.target !== viewport) return;
                syncGroupJump();
                syncControls();
                fillBars(viewport);
            });
        }

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

            // Minibar list switch: a PROXY for the real chips, per the minibar contract (no
            // duplicate form fields, no second swap path) -- a selection CLICKS the matching
            // chip, so URL push, chip sync and the slide all run the one htmx path. The select
            // follows swaps from either side in afterSwap below.
            const listSel = document.querySelector('[data-minibar-listswitch]');
            if (listSel) {
                listSel.addEventListener('change', () => {
                    const chip = chips.find((c) => c.dataset.np === listSel.value);
                    if (chip) chip.click();
                });
            }

            let lastNp = listInput ? listInput.value : (viewport.dataset.defaultNp || '');
            const chipOrder = chips.map((c) => c.dataset.np);
            document.body.addEventListener('htmx:afterSwap', (e) => {
                if (e.detail.target !== viewport) return;
                // finalRequestPath, not requestPath: for the filter form (a GET) requestPath is the bare
                // hx-get path and the list rides in the query htmx appends, so reading requestPath sent
                // every filter back to the default list. Both are htmx 2.0.4 internals (the only consumer
                // in this repo); if a bump renames them, np silently falls back to the default and the
                // chips desync.
                const path = e.detail.pathInfo.finalRequestPath || e.detail.pathInfo.requestPath;
                const params = new URLSearchParams(path.split('?')[1] || '');
                const np = params.get('list') || defaultNp;
                if (listInput) listInput.value = np;   // the filter form asks about the list on screen
                // A filter change keeps the list: nothing to switch, push or ignite.
                if (np === lastNp) return;
                // State first (chips, the row, URL), decoration after -- never strand the switcher.
                syncChips(np);
                if (listSel) listSel.value = np;
                resetControls();
                // push, not replace: the chips are real <a>s whose navigation htmx swallowed --
                // stopping a navigation without pushing is what strands the Back button
                // (utils.js's own rule for swallowed-link tabs).
                PP.syncViewParam(np, { param: 'list', default: defaultNp, push: true });
                PP.slideViewIn(viewport, lastNp, np, chipOrder);
                lastNp = np;
                const chip = chips.find((c) => c.dataset.np === np);
                if (chip) PP.igniteTab(chip);
            });
            document.body.addEventListener('htmx:afterRequest', (e) => {
                // A FAILED swap never reaches afterSwap, so the minibar's list select would
                // keep showing the list the viewer picked while the page still shows the old
                // one (final-audit finding) -- walk it back to the list actually on screen.
                if (e.detail.target === viewport && e.detail.xhr && e.detail.xhr.status !== 0
                    && !e.detail.successful && listSel) listSel.value = lastNp;
            });
        }

        // The Ratings tab's own machinery -- per-group selector, quick-rate glue, live update,
        // entrance reveal -- lives in ratings-tab.js (the slim-down made this page its one host;
        // an older read-only selector port used to live here and was deleted with the gate).
    });
})();
