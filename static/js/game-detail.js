/**
 * Game Detail Page controller (rebuild).
 *
 * Owns: the Trophies/Roadmap/Community/About view switcher (shared .pp-switch +
 * PlatPursuit.wireTablist/slideViewIn/igniteTab/syncViewParam), the hero screenshot
 * lightbox, the trophy-filter settle + scroll restore, hero count-ups, and deep-link
 * jumps ([data-gd-goto]). The quick-rate modal block is carried over from the legacy
 * ratings panel until that panel is rebuilt.
 */
document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('game-detail-container');
    if (!container) return;

    const scrollKey = container.dataset.scrollKey;
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // Shared "page steps back" recede (used by the contract modal AND the hero's stats/badges modals).
    // Pivots the scale on the current viewport centre so it's a gentle scale-in-place at any scroll depth.
    const zoomContainer = document.getElementById('zoom-container');
    function pageRecede(on) {
        const pr = document.getElementById('page-recede');
        if (on && pr) { pr.style.transformOrigin = '50% ' + (window.innerHeight / 2 - pr.getBoundingClientRect().top) + 'px'; }
        if (zoomContainer) zoomContainer.classList.toggle('pp-receded', on);
    }

    // About panel entrance, ONE-SHOT on first arrival (revisiting a tab shouldn't replay the show).
    // The panel is server-rendered but hidden, so the load-time fillBars(container) has already filled its
    // time-to-beat bars -- reset them to 0 first, otherwise there's nothing left to grow. Bands cascade via
    // the shared staggerReveal; both are skipped under reduced motion (staggerReveal self-gates too, but
    // the bar reset has to be guarded here or the bars would sit empty for those viewers).
    //
    // MUST be declared ABOVE the switcher IIFE: that IIFE runs immediately and honors an initial ?view=
    // by calling showView() during setup, so a `let` declared after it would still be in the temporal dead
    // zone and throw -- which aborted the rest of this file on any ?view=about reload.
    let aboutRevealed = false;
    function revealAbout(panel) {
        if (aboutRevealed || reduce) return;
        aboutRevealed = true;
        panel.querySelectorAll('[data-gd-fill]').forEach((b) => b.style.setProperty('--horizon-progress', '0%'));
        fillBars(panel);
        const wrap = panel.querySelector('.gd-about');
        if (!wrap || !PlatPursuit.staggerReveal) return;
        PlatPursuit.staggerReveal({
            grid: wrap, cardSelector: '.gd-acard, .gd-empty', step: 55, batchCap: 300,
            reveal: (el, delay) => el.animate(
                [{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }],
                { duration: 420, delay, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: 'backwards' }
            ),
        });
    }

    // Leaderboard panel. The ONLY panel not server-rendered: its cost scales with a game's popularity
    // and most visitors never open it, so it is fetched on first activation and then cached in the DOM.
    // Declared above the switcher IIFE for the same reason as revealAbout -- that IIFE honors an initial
    // ?view= during setup, so a `let` declared after it would still be in the temporal dead zone.
    const LB_XHR = { headers: { 'X-Requested-With': 'XMLHttpRequest' } };
    const lbText = (r) => (r.ok ? r.text() : Promise.reject(new Error('HTTP ' + r.status)));
    let lbLoaded = false;

    function loadLeaderboard(panel) {
        if (lbLoaded) return;
        lbLoaded = true;
        if (!panel.dataset.lbSrc) return;
        lbDelegate(panel);                                     // attach control/jump handlers once
        lbFetchPanel(panel, panel.dataset.lbSrc);              // whole panel (header + first page)
    }

    // The current view's query, read from the control toggles so every fetch preserves the active
    // filters/sort. earners is on by default, so only its OFF state is a param.
    function lbOptsUrl(panel, extra) {
        const params = new URLSearchParams();
        panel.querySelectorAll('[data-lb-opt]').forEach((btn) => {
            const on = btn.getAttribute('aria-pressed') === 'true';
            const key = btn.dataset.lbOpt;
            if (key === 'invert' && on) params.set('invert', '1');
            if (key === 'earners' && !on) params.set('earners', '0');
            if (key === 'registered' && on) params.set('registered', '1');
        });
        if (extra) Object.keys(extra).forEach((k) => params.set(k, extra[k]));
        const qs = params.toString();
        return qs ? panel.dataset.lbSrc + '?' + qs : panel.dataset.lbSrc;
    }

    // Fetch the WHOLE panel (initial load or after a control change) and re-wire its observers.
    function lbFetchPanel(panel, url) {
        fetch(url, LB_XHR).then(lbText)
            .then((html) => {
                if (panel._lbTeardown) panel._lbTeardown();
                panel.innerHTML = html;
                lbWire(panel);
            })
            .catch(() => {
                lbLoaded = false;                              // let a later tab visit retry
                if (panel._lbTeardown) panel._lbTeardown();
                panel.innerHTML = '<div class="gd-empty"><p class="gd-empty__title">Couldn\'t load the board</p>'
                    + '<p class="gd-empty__hint">Switch tabs and back to try again.</p></div>';
            });
    }

    // Delegated handlers live on the persistent panel element, attached ONCE -- innerHTML swaps replace
    // the controls, so per-wire listeners would stack.
    function lbDelegate(panel) {
        // Once only: loadLeaderboard can run again after a failed fetch (it resets lbLoaded), and the panel
        // element persists, so re-attaching here would stack a second handler per retry.
        if (panel._lbDelegated) return;
        panel._lbDelegated = true;
        panel.addEventListener('click', (e) => {
            const opt = e.target.closest('[data-lb-opt]');
            if (opt) {
                opt.setAttribute('aria-pressed', opt.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
                lbFetchPanel(panel, lbOptsUrl(panel));         // re-render the whole board in the new view
                return;
            }
            if (e.target.closest('[data-lb-jump]')) lbJumpToMe(panel);
        });
        panel.addEventListener('submit', (e) => {
            const form = e.target.closest('[data-lb-rankform]');
            if (!form) return;
            e.preventDefault();
            const n = parseInt(form.querySelector('[data-lb-rankinput]').value, 10);
            if (n >= 1) lbJumpToRank(panel, n);
        });
    }

    function lbWire(panel) {
        const list = panel.querySelector('[data-lb-list]');
        const observers = [];
        // Also disconnect the self-row observer, which a jump/append may have re-mounted OUTSIDE this
        // array (tracked only on panel._lbSelfObserver).
        panel._lbTeardown = () => {
            observers.forEach((o) => o.disconnect());
            if (panel._lbSelfObserver) { panel._lbSelfObserver.disconnect(); panel._lbSelfObserver = null; }
            panel._lbTeardown = null;
        };
        panel._lbBusy = false;                                 // a freshly-wired panel is never mid-fetch
        if (!list) return;

        // Infinite scroll: the trailing marker carries the next cursor AND the rank the next page starts
        // at, so appended rows keep numbering correctly (counting up, or down when inverted).
        const io = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting || panel._lbBusy) return;
                const marker = entry.target;
                panel._lbBusy = true;
                io.unobserve(marker);
                fetch(lbOptsUrl(panel, { after: marker.dataset.lbNext, from: marker.dataset.lbFrom || '1' }), LB_XHR)
                    .then(lbText)
                    .then((html) => {
                        // A control change may have replaced the whole panel while this was in flight;
                        // if so `list` is detached -- drop the stale page rather than writing into limbo.
                        if (!list.isConnected) return;
                        marker.remove();                       // consumed; the new page brings its own
                        list.insertAdjacentHTML('beforeend', html);
                        panel._lbBusy = false;
                        lbWatchTail(panel);
                        lbMountSelf(panel);                    // the viewer's row may have just appended
                    })
                    .catch(() => { if (list.isConnected) panel._lbBusy = false; });
            });
        }, { rootMargin: '300px 0px' });
        observers.push(io);
        panel._lbIO = io;
        lbWatchTail(panel);
        lbMountSelf(panel, observers);
    }

    function lbWatchTail(panel) {
        const list = panel.querySelector('[data-lb-list]');
        const marker = list && list.querySelector('.gd-lb__more');
        if (marker && panel._lbIO) panel._lbIO.observe(marker);
    }

    // Stop observing the current tail marker before a jump wipes the list, or the destroyed node leaks
    // as a retained observation target.
    function lbDropTail(panel) {
        const list = panel.querySelector('[data-lb-list]');
        const marker = list && list.querySelector('.gd-lb__more');
        if (marker && panel._lbIO) panel._lbIO.unobserve(marker);
    }

    // The pinned self-row: shown only while the viewer's real row is off screen, and flipped to the
    // TOP or BOTTOM edge depending on which way their place lies -- pointing the reminder toward them.
    // Re-mounted after any list swap because it observes a specific row node that swaps out.
    function lbMountSelf(panel, observers) {
        const list = panel.querySelector('[data-lb-list]');
        const self = panel.querySelector('[data-lb-self]');
        if (panel._lbSelfObserver) { panel._lbSelfObserver.disconnect(); panel._lbSelfObserver = null; }
        if (!self || !list) return;
        const mine = list.querySelector('.gd-lb__row--you');
        if (!mine) { self.hidden = false; lbSelfDir(panel, 'bottom'); return; }   // deeper than loaded
        self.hidden = true;                                    // real row present until scrolled away
        const w = new IntersectionObserver(([entry]) => {
            if (entry.isIntersecting) { self.hidden = true; return; }
            const rootTop = entry.rootBounds ? entry.rootBounds.top : 0;
            // Real row above the viewport top => the viewer is UP the board => pin the reminder to the TOP.
            lbSelfDir(panel, entry.boundingClientRect.top < rootTop ? 'top' : 'bottom');
            self.hidden = false;
        }, { threshold: 0 });
        w.observe(mine);
        panel._lbSelfObserver = w;
        if (observers) observers.push(w);
    }

    // Position the self-row at the chosen edge. A sticky element only pins toward the edge its DOM
    // position allows, so it's moved before/after the list to make top/bottom sticking actually engage.
    function lbSelfDir(panel, dir) {
        const self = panel.querySelector('[data-lb-self]');
        const list = panel.querySelector('[data-lb-list]');
        if (!self || !list) return;
        self.classList.toggle('gd-lb__self--top', dir === 'top');
        self.classList.toggle('gd-lb__self--bottom', dir === 'bottom');
        // Move only if it isn't already at the target edge (guards against thrashing the DOM every tick).
        if (dir === 'top' && list.previousElementSibling !== self) {
            list.insertAdjacentElement('beforebegin', self);
        } else if (dir === 'bottom' && list.nextElementSibling !== self) {
            list.insertAdjacentElement('afterend', self);
        }
    }

    function lbFlashScroll(row) {
        row.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' });
        row.classList.remove('is-found');
        void row.offsetWidth;                                  // restart the flash if jumped twice
        row.classList.add('is-found');
    }

    // Jump to the viewer's row. If it's already loaded, just scroll; otherwise the server opens a window
    // around them and we swap the list to it rather than paging forward hundreds of times.
    function lbJumpToMe(panel) {
        const list = panel.querySelector('[data-lb-list]');
        const here = list && list.querySelector('.gd-lb__row--you');
        if (here) { lbFlashScroll(here); return; }
        if (!list || panel._lbBusy) return;
        panel._lbBusy = true;
        fetch(lbOptsUrl(panel, { around: 'me' }), LB_XHR).then(lbText)
            .then((html) => {
                panel._lbBusy = false;
                if (html.indexOf('gd-lb__row') === -1) return;   // nothing to jump to; keep the list
                lbDropTail(panel);
                list.innerHTML = html;
                lbWatchTail(panel);
                lbMountSelf(panel);
                const row = list.querySelector('.gd-lb__row--you');
                if (row) lbFlashScroll(row);
            })
            .catch(() => { panel._lbBusy = false; });
    }

    // Jump to a typed rank: a window centred on that position (server clamps out-of-range).
    function lbJumpToRank(panel, n) {
        const list = panel.querySelector('[data-lb-list]');
        if (!list || panel._lbBusy) return;
        panel._lbBusy = true;
        fetch(lbOptsUrl(panel, { rank: n }), LB_XHR).then(lbText)
            .then((html) => {
                panel._lbBusy = false;
                if (html.indexOf('gd-lb__row') === -1) return;   // empty board; keep the list
                lbDropTail(panel);
                list.innerHTML = html;
                lbWatchTail(panel);
                lbMountSelf(panel);
                const target = list.querySelector('[data-lb-rank="' + n + '"]');
                if (target) lbFlashScroll(target);
                else list.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
            })
            .catch(() => { panel._lbBusy = false; });
    }

    // ============================================================
    // View switcher: Trophies (default) / Roadmap / Community / About
    // ============================================================
    (function () {
        const viewTabs = document.querySelectorAll('#gd-switch .pp-switch__chip[data-view]');
        const views = document.querySelectorAll('.gd-view');
        if (!viewTabs.length || !views.length) return;
        const VIEW_ORDER = ['trophies', 'roadmap', 'community', 'leaderboard', 'about'];
        // The minibar's per-view extras (sort / count / Filters) are gated by data-mb-active, which showView()
        // keeps in sync with the active view.
        const minibar = document.querySelector('.gd-minibar');

        function currentView() {
            let cur = null;
            views.forEach((v) => { if (!v.hidden) cur = v.dataset.view; });
            return cur;
        }

        let tablist;
        function showView(name) {
            const from = currentView();
            const changed = from !== name;
            let shown = null;
            views.forEach((v) => {
                const on = v.dataset.view === name;
                v.hidden = !on;
                if (on) shown = v;
            });
            if (changed && shown && PlatPursuit.slideViewIn) {
                PlatPursuit.slideViewIn(shown, from, name, VIEW_ORDER);
            }
            let activeTab = null;
            viewTabs.forEach((t) => {
                const on = t.dataset.view === name;
                t.classList.toggle('is-active', on);
                t.setAttribute('aria-selected', on ? 'true' : 'false');
                if (on) activeTab = t;
            });
            if (minibar) minibar.dataset.mbActive = name;   // gates the per-view extras (sort/count/Filters)
            if (tablist) tablist.syncTabindex();
            if (changed && activeTab && PlatPursuit.igniteTab) PlatPursuit.igniteTab(activeTab);
            if (PlatPursuit.syncViewParam) {
                PlatPursuit.syncViewParam(name, { default: 'trophies' });
            }
            // Decorative, so it runs LAST: tab state (panels, chips, URL) is fully synced before any
            // entrance animation, and a fault in the flourish can never strand the switcher mid-update.
            if (changed && shown && name === 'about') revealAbout(shown);
            if (shown && name === 'leaderboard') loadLeaderboard(shown);
        }

        tablist = PlatPursuit.wireTablist(viewTabs, { onSelect: (t) => showView(t.dataset.view) });

        // One-shot ignite on the active pill so it "comes alive" on load.
        const initTab = document.querySelector('#gd-switch .pp-switch__chip[data-view].is-active');
        if (initTab && PlatPursuit.igniteTab) {
            setTimeout(() => { if (initTab.classList.contains('is-active')) PlatPursuit.igniteTab(initTab); }, 260);
        }

        // Deep-link jumps (e.g. the hero roadmap teaser -> Roadmap tab).
        document.querySelectorAll('[data-gd-goto]').forEach((el) => {
            el.addEventListener('click', (e) => {
                e.preventDefault();
                showView(el.dataset.gdGoto);
                const strip = document.getElementById('gd-switch');
                if (strip) strip.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
            });
        });

        // Honor an initial ?view= (a shared/reloaded deep link).
        const initialView = new URLSearchParams(window.location.search).get('view');
        if (initialView && VIEW_ORDER.includes(initialView) && initialView !== 'trophies') {
            showView(initialView);
        }
    })();

    // ============================================================
    // Hero count-ups (completion %, glance stats) — reduced-motion safe (countUp jumps).
    // ============================================================
    if (PlatPursuit.countUp) {
        document.querySelectorAll('[data-gd-countup]').forEach((el) => {
            const n = parseInt(el.dataset.gdCountup, 10);
            if (!isNaN(n)) { el.dataset.countup = n; PlatPursuit.countUp(el, 700, { from: 0 }); }
        });
    }

    // Fill Horizon + composite group bars from 0 -> target within `root`. The width transition is
    // CSS-gated under reduced motion (horizon.css), so this just jumps there. Called on load (hero +
    // group headers) and again after each trophy-filter swap (the per-group bars re-render inside
    // #browse-results, so they'd otherwise stay at 0%).
    function fillBars(root) {
        const scope = root || document;
        scope.querySelectorAll('[data-gd-fill] .pp-horizon__fill').forEach((fill) => {
            const bar = fill.closest('[data-gd-fill]');
            const target = (bar.dataset.gdFill || '0') + '%';
            requestAnimationFrame(() => requestAnimationFrame(() => bar.style.setProperty('--horizon-progress', target)));
        });
        scope.querySelectorAll('.gd-groupbar__fill[data-gd-fill]').forEach((f) => {
            const target = (f.dataset.gdFill || '0') + '%';
            requestAnimationFrame(() => requestAnimationFrame(() => { f.style.width = target; }));
        });
    }
    fillBars(container);

    // Trophy-row entrance. Two tools by design (motion-patterns "reveal-stagger is three tools"):
    //  - INITIAL LOAD: shared staggerReveal. It adds .pp-reveal + stamps each row .is-revealed synchronously,
    //    so the WAAPI backwards-fill (opacity 0 through each row's stagger delay) is what actually holds a row
    //    hidden -- same as the game_list gallery. The .pp-reveal / :not(.is-revealed) CSS is a strand-proof
    //    safety net (a row is only ever hidden while un-revealed), NOT a first-paint guard.
    //  - FILTER SWAP: a bespoke WAAPI-only stagger (staggerSwappedRows). #browse-results PERSISTS across an
    //    innerHTML swap, so its load-time .pp-reveal would otherwise strand the freshly-swapped rows (the
    //    "cards disappear on filter" bug). We DROP .pp-reveal on swap and animate with NO persistent hide-
    //    class, so a row is visible by default and can never be left hidden if anything goes sideways.
    function revealTrophiesInitial() {
        const grid = document.getElementById('browse-results');
        if (!grid || !PlatPursuit.staggerReveal) return;
        PlatPursuit.staggerReveal({
            grid, cardSelector: '.gd-trophy', step: 18, batchCap: 480,
            reveal: (el, delay) => el.animate(
                [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }],
                { duration: 340, delay, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: 'backwards' }
            ),
        });
    }
    function staggerSwappedRows(root) {
        if (reduce || !root) return;
        root.querySelectorAll('.gd-trophy').forEach((el, i) => {
            if (!el.animate) return;
            el.animate(
                [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }],
                { duration: 300, delay: Math.min(i * 14, 260), easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: 'backwards' }
            );
        });
    }
    revealTrophiesInitial();

    // Trophy group-nav smooth-jump, delegated on the persistent container (the nav re-renders inside
    // #browse-results on every filter swap, so a per-chip listener wouldn't survive). The <a href="#..">
    // stays the no-JS fallback; this just upgrades it to a reduced-motion-aware smooth scroll.
    // Fire cb once scrolling has actually stopped (distance-independent, so a far jump waits as long as it
    // needs and a near one fires promptly). Re-arms on each scroll tick; fires 110ms after the last one, or
    // 110ms from now if nothing scrolls (target already in place).
    function afterScrollSettle(cb) {
        let idle = null;
        const fire = () => { window.removeEventListener('scroll', arm); cb(); };
        const arm = () => { window.clearTimeout(idle); idle = window.setTimeout(fire, 110); };
        window.addEventListener('scroll', arm, { passive: true });
        arm();
    }
    // Jump to a trophy group (group-nav chip OR minibar dropdown): scroll, then wash-highlight the header
    // once the scroll settles so your eye catches the landing.
    function jumpToGroup(id) {
        const target = id && document.getElementById(id);
        if (!target) return;
        target.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
        const head = target.querySelector('.gd-group__head');
        if (head && !reduce) afterScrollSettle(() => { head.classList.remove('is-flash'); void head.offsetWidth; head.classList.add('is-flash'); });
    }
    container.addEventListener('click', (e) => {
        const chip = e.target.closest('[data-gd-groupjump]');
        if (!chip) return;
        e.preventDefault();
        jumpToGroup(chip.dataset.gdGroupjump);
    });

    // Minibar "jump to group" dropdown: mirrors the group-nav (its chips reflect the current filtered groups),
    // hidden when there's 0-1 group (no DLC). Rebuilt on load + after each filter swap. Options are set via
    // new Option(text, value) so group names are inserted as text (no HTML injection).
    function syncGroupJump() {
        const sel = document.querySelector('[data-minibar-groupjump]');
        if (!sel) return;
        const chips = Array.from(document.querySelectorAll('#browse-results [data-gd-groupjump]'));
        if (chips.length <= 1) { sel.hidden = true; return; }
        sel.hidden = false;
        sel.textContent = '';
        const ph = new Option('Jump to group…', ''); ph.disabled = true; ph.selected = true;
        sel.add(ph);
        chips.forEach((c) => {
            const name = c.querySelector('.gd-groupnav__name');
            sel.add(new Option(name ? name.textContent : c.dataset.gdGroupjump, c.dataset.gdGroupjump));
        });
    }
    syncGroupJump();

    // Chip toggle "pop": a small spring on the type/rarity chip face when it changes. The toolbar isn't
    // re-rendered on filter swaps, so a one-time wire holds.
    if (!reduce) {
        document.querySelectorAll('.gd-chip input').forEach((inp) => {
            inp.addEventListener('change', () => {
                const face = inp.nextElementSibling;
                if (!face) return;
                face.classList.remove('is-pop'); void face.offsetWidth; face.classList.add('is-pop');
            });
        });
    }

    // Minibar controls: the Sort proxy mirrors the real (in-form) sort so there's ONE source of truth and no
    // duplicate form field; the Filters reach scrolls the real toolbar back into view. StickyReveal pins the
    // bar once the switcher scrolls under the chrome.
    (function () {
        const filterForm = document.getElementById('gd-filter-form');
        const realSort = filterForm ? filterForm.querySelector('select[name="sort"]') : null;
        const mbSort = document.querySelector('[data-minibar-sort]');
        if (mbSort && realSort) {
            mbSort.innerHTML = realSort.innerHTML;   // one source of truth for the options
            mbSort.value = realSort.value;
            mbSort.addEventListener('change', () => {
                realSort.value = mbSort.value;
                realSort.dispatchEvent(new Event('change', { bubbles: true }));   // drives the form's hx-trigger
            });
            realSort.addEventListener('change', () => { mbSort.value = realSort.value; });
        }
        const mbFilters = document.querySelector('[data-minibar-filters]');
        if (mbFilters && filterForm) {
            mbFilters.addEventListener('click', () => filterForm.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' }));
        }
        const mbGroupJump = document.querySelector('[data-minibar-groupjump]');
        if (mbGroupJump) {
            mbGroupJump.addEventListener('change', () => { jumpToGroup(mbGroupJump.value); mbGroupJump.selectedIndex = 0; });
        }
        if (PlatPursuit.StickyReveal) PlatPursuit.StickyReveal.init();
    })();

    // Mobile filter collapse: the toolbar body is collapsed by default on phones (CSS); this wires the toggle
    // + an active-filter count badge so it's clear when filters are applied while collapsed. At md+ the body is
    // always open (CSS) and the toggle is hidden, so this is a no-op there.
    (function () {
        const form = document.getElementById('gd-filter-form');
        const toggle = document.querySelector('[data-gd-filters-toggle]');
        const body = document.getElementById('gd-filter-body');
        const badge = document.querySelector('[data-gd-filter-count]');
        if (!form || !toggle || !body) return;
        function setOpen(open) {
            body.classList.toggle('is-open', open);
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        }
        // Active filters = checked type/rarity chips + a non-default Show/DLC select (sort is ordering, not a filter).
        function countActive() {
            let n = form.querySelectorAll('.gd-chip input:checked').length;
            form.querySelectorAll('select[name="earned"], select[name="dlc_filter"]').forEach((s) => { if (s.selectedIndex > 0) n++; });
            return n;
        }
        function refreshBadge() {
            if (!badge) return;
            const n = countActive();
            badge.textContent = String(n);
            badge.hidden = n === 0;
        }
        toggle.addEventListener('click', () => setOpen(!body.classList.contains('is-open')));
        form.addEventListener('change', refreshBadge);
        refreshBadge();
        // Pre-applied filters (e.g. a shared link) start expanded -- WITHOUT animating open on load (suppress
        // the grid-rows transition for this one state change, then restore it for user toggles).
        if (countActive() > 0) {
            body.style.transition = 'none';
            setOpen(true);
            void body.offsetWidth;
            body.style.transition = '';
        }
        // The minibar's "Filters" reach expands the panel too (its own handler does the scroll-to-toolbar).
        const mbFilters = document.querySelector('[data-minibar-filters]');
        if (mbFilters) mbFilters.addEventListener('click', () => setOpen(true));
    })();

    // ============================================================
    // Hero About fit: on desktop, clamp + fade the IGDB blurb so the main column
    // never pushes the hero taller than the cover + extras (the "header boundary").
    // The blurb is the one elastic element; it yields, the rest holds.
    // ============================================================
    (function () {
        const grid = container.querySelector('.gd-hero__grid');
        const main = container.querySelector('.gd-hero__main');
        const cover = container.querySelector('.gd-cover');
        const extras = container.querySelector('.gd-hero__extras');
        const about = container.querySelector('.gd-hero__about');
        const blurb = about ? about.querySelector('.gd-about-blurb') : null;
        if (!grid || !main || !cover || !about || !blurb) return;

        const ROW_GAP = 16;   // desktop grid row-gap (1rem), between cover and extras
        function lineHeight() {
            const lh = parseFloat(getComputedStyle(blurb).lineHeight);
            return isNaN(lh) ? 20 : lh;
        }
        function fit() {
            // Reset first so measurements reflect the natural (statically-clamped) height.
            blurb.style.maxHeight = '';
            about.classList.remove('is-clamped');
            if (window.innerWidth < 1024) return;   // only when cover + main sit side by side

            const leftH = cover.offsetHeight + (extras ? ROW_GAP + extras.offsetHeight : 0);
            const gap = parseFloat(getComputedStyle(main).rowGap) || 8;
            // Only FLOW children count toward the column height -- the "X Players" headline is absolutely
            // positioned at md+ (it lives in this column but takes no flow space), so including its height
            // over-counts mainH and falsely clamps the About blurb even when there's room.
            const kids = Array.from(main.children).filter((c) => getComputedStyle(c).position !== 'absolute');
            const mainH = kids.reduce((h, c) => h + c.offsetHeight, 0) + gap * Math.max(0, kids.length - 1);

            const overflow = mainH - leftH;
            if (overflow <= 4) return;               // fits within the boundary; leave the static clamp
            const target = Math.max(lineHeight() * 2, blurb.offsetHeight - overflow);   // never crush below ~2 lines
            if (target < blurb.scrollHeight) {
                blurb.style.maxHeight = Math.round(target) + 'px';
                about.classList.add('is-clamped');
            }
        }

        fit();
        // Recompute when the OUTER drivers change (never observe main -> the clamp mutates it = loop).
        if (window.ResizeObserver) {
            const ro = new ResizeObserver(() => window.requestAnimationFrame(fit));
            ro.observe(cover);
            if (extras) ro.observe(extras);
        }
        window.addEventListener('resize', () => window.requestAnimationFrame(fit));
    })();

    // ============================================================
    // Hero screenshot lightbox
    // ============================================================
    (function () {
        const modal = document.getElementById('gd-shot-modal');
        const dataEl = document.getElementById('gd-shot-data');
        if (!modal || !dataEl) return;
        let shots = [];
        try { shots = JSON.parse(dataEl.textContent) || []; } catch (_) { shots = []; }
        if (!shots.length) return;

        const img = document.getElementById('gd-shot-img');
        const countEl = document.getElementById('gd-shot-count');
        const strip = document.getElementById('gd-shot-strip');
        const thumbs = strip ? Array.from(strip.querySelectorAll('[data-shot-to]')) : [];
        let idx = 0;
        let source = null;   // element the current view "belongs to" (for the grow/shrink FLIP)
        let pageToken = 0;   // latest paging slide, so overlapping navs clear .is-paging correctly
        let ghost = null;    // the outgoing image's clone, slid out during a push swap
        let openToken = 0;   // open generation: invalidates a deferred (img-load) FLIP if superseded by a later open/close

        function rectOf(el) { return el ? el.getBoundingClientRect() : null; }
        function clearGhost() {
            if (!ghost) return;
            if (ghost.getAnimations) ghost.getAnimations().forEach((a) => a.cancel());
            if (ghost.parentNode) ghost.remove();
            ghost = null;
        }

        // FLIP: animate the big image between its full box and a source thumbnail's box, so it
        // appears to grow out of (open) / shrink back into (close) the thumbnail it came from.
        function flip(srcRect, opening, done) {
            const last = img.getBoundingClientRect();
            if (!srcRect || !srcRect.width || !last.width) { if (done) done(); return; }
            const scale = srcRect.width / last.width;
            const dx = (srcRect.left + srcRect.width / 2) - (last.left + last.width / 2);
            const dy = (srcRect.top + srcRect.height / 2) - (last.top + last.height / 2);
            const thumbT = `translate(${dx}px, ${dy}px) scale(${scale})`;
            const frames = opening
                ? [{ transform: thumbT, opacity: 0.4 }, { transform: 'none', opacity: 1 }]
                : [{ transform: 'none', opacity: 1 }, { transform: thumbT, opacity: 0.4 }];
            const a = img.animate(frames, { duration: opening ? 340 : 260, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: opening ? 'none' : 'forwards' });
            if (done) {
                a.onfinish = done;
                // Opening: if a later action cancels this, still clear is-animating (don't strand the chrome/image).
                // Closing: a cancel means we're REOPENING -> must NOT run finalize (it would close the fresh modal).
                if (opening) a.oncancel = done;
            }
        }

        // Warm the neighbours so a push swap never flashes an undecoded image (invisible-until-missing polish).
        function preloadAround() {
            if (shots.length < 2) return;
            [(idx + 1) % shots.length, (idx - 1 + shots.length) % shots.length].forEach((i) => {
                const im = new Image(); im.decoding = 'async'; im.src = shots[i];
            });
        }
        function paint() {
            idx = (idx + shots.length) % shots.length;
            if (img) img.src = shots[idx];
            if (countEl) countEl.textContent = (idx + 1) + ' / ' + shots.length;
            thumbs.forEach((t, i) => {
                const on = i === idx;
                t.classList.toggle('is-active', on);
                if (on) t.scrollIntoView({ block: 'nearest', inline: 'center', behavior: reduce ? 'auto' : 'smooth' });
            });
            preloadAround();
        }
        // Directional PUSH swap (arrows / filmstrip / keyboard): the outgoing image is pushed out
        // one way while the incoming one slides in from the other, edge-to-edge (a carousel push).
        function go(delta) {
            // Ignore switches while the modal's OPEN or CLOSE FLIP is running (is-animating spans the whole
            // open window, incl. the img-load wait). A push mid-open collides with the deferred FLIP-open and
            // corrupts the carousel -- the exact "switch as the modal is opening" bug.
            if (modal.classList.contains('is-closing') || modal.classList.contains('is-animating') || !delta) return;
            const dir = delta > 0 ? 1 : -1;         // +1 next (new from right), -1 prev (new from left)
            const oldSrc = shots[idx];              // capture the outgoing image BEFORE paint swaps it
            idx += delta; paint();                  // paint swaps img.src to the new shot + updates chrome
            if (reduce) return;
            // Stop + clean any in-flight swap first: overlapping WAAPI animations on the same <img>
            // composite and fight each other, wedging the stage (the "frozen carousel" bug).
            if (img.getAnimations) img.getAnimations().forEach((a) => a.cancel());
            clearGhost();
            // The ghost (outgoing image) sits directly above the real <img> but below the nav chrome.
            ghost = document.createElement('img');
            ghost.src = oldSrc;
            ghost.className = img.className + ' gd-shotmodal__ghost';
            ghost.setAttribute('aria-hidden', 'true');
            img.insertAdjacentElement('afterend', ghost);

            modal.classList.add('is-paging');       // clip the stage so the sliding pair can't overflow
            const my = ++pageToken;
            const local = ghost;
            const opts = { duration: 300, easing: 'cubic-bezier(0.4, 0, 0.2, 1)' };
            img.animate([{ transform: `translateX(${dir * 100}%)` }, { transform: 'none' }], opts);
            const g = local.animate([{ transform: 'none' }, { transform: `translateX(${dir * -100}%)` }],
                Object.assign({ fill: 'forwards' }, opts));   // hold the exit frame until removed (no snap-back flash)
            const done = () => {
                if (local.parentNode) local.remove();
                if (ghost === local) ghost = null;
                if (my === pageToken) modal.classList.remove('is-paging');
            };
            g.onfinish = done; g.oncancel = done;
        }

        // The filmstrip scales + fades in/out concurrently with the main image's grow/shrink.
        // Scale-only (no translate) so it can't briefly overflow the viewport and flash a scrollbar.
        function stripAnim(opening) {
            if (!strip || reduce) return;
            const small = { opacity: 0, transform: 'scale(0.94)' };
            const full = { opacity: 1, transform: 'none' };
            strip.animate(opening ? [small, full] : [full, small],
                { duration: opening ? 320 : 240, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: opening ? 'none' : 'forwards' });
        }

        function openAt(i, sourceEl) {
            source = sourceEl || null;
            const srcRect = rectOf(sourceEl);
            // Clear any stale animation state from a prior (possibly interrupted) open/close cycle,
            // and drop held (fill:forwards) transforms so the fresh FLIP starts clean.
            modal.classList.remove('is-closing', 'is-paging');
            if (img.getAnimations) img.getAnimations().forEach((a) => a.cancel());
            if (strip && strip.getAnimations) strip.getAnimations().forEach((a) => a.cancel());
            clearGhost();
            idx = i; paint();
            modal.classList.add('is-animating');   // chrome hidden until the image lands
            if (modal.showModal && !modal.open) modal.showModal();
            if (reduce) { modal.classList.remove('is-animating'); return; }
            // A slow image defers the FLIP-open until load; stamp this open so a later open/close can
            // invalidate the pending run (else it fires a FLIP-open onto a swapped/closed modal).
            const myOpen = ++openToken;
            const run = () => { if (myOpen !== openToken) return; flip(srcRect, true, () => modal.classList.remove('is-animating')); stripAnim(true); };
            if (img.complete && img.naturalWidth) { run(); } else { img.addEventListener('load', run, { once: true }); }
        }
        function finalize() { clearGhost(); modal.classList.remove('is-closing', 'is-animating', 'is-paging'); if (modal.close && modal.open) modal.close(); }
        function close() {
            if (!modal.open) return;
            ++openToken;   // supersede any pending deferred FLIP-open so it can't fire during/after the close
            // Stop any in-flight slide before the shrink so it can't composite into a corrupt transform.
            if (img.getAnimations) img.getAnimations().forEach((a) => a.cancel());
            if (strip && strip.getAnimations) strip.getAnimations().forEach((a) => a.cancel());
            clearGhost();
            modal.classList.add('is-closing', 'is-animating');   // chrome + filmstrip leave with the image
            if (reduce) { finalize(); return; }
            stripAnim(false);
            // Shrink back to the matching hero thumbnail if one is shown, else the opener.
            const home = document.querySelector('#gd-shots [data-shot="' + idx + '"]') || source;
            const homeRect = rectOf(home);
            if (homeRect && homeRect.width) { flip(homeRect, false, finalize); }
            else { img.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'scale(0.94)' }], { duration: 200, easing: 'ease', fill: 'forwards' }).onfinish = finalize; }
        }

        document.querySelectorAll('#gd-shots [data-shot]').forEach((btn) => {
            btn.addEventListener('click', () => openAt(parseInt(btn.dataset.shot, 10) || 0, btn));
        });
        thumbs.forEach((t) => t.addEventListener('click', () => go((parseInt(t.dataset.shotTo, 10) || 0) - idx)));
        modal.querySelector('[data-shot-close]')?.addEventListener('click', close);
        modal.querySelector('[data-shot-prev]')?.addEventListener('click', () => go(-1));
        modal.querySelector('[data-shot-next]')?.addEventListener('click', () => go(1));
        modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
        // Esc: run our choreographed close (shrink home) instead of the instant native close.
        modal.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
        document.addEventListener('keydown', (e) => {
            if (!modal.open) return;
            if (e.key === 'ArrowLeft') go(-1);
            else if (e.key === 'ArrowRight') go(1);
        });
        // Touch: a horizontal swipe on the IMAGE navigates prev/next. No swipe-down-to-close here (it
        // hijacked the whole dialog and fought the filmstrip's horizontal scroll), and the strip keeps
        // its own native scroll (we only listen on the stage). Close via the button / backdrop / Esc.
        const stage = modal.querySelector('.gd-shotmodal__stage');
        if (stage && shots.length > 1) {
            let sx = 0, sy = 0, tracking = false;
            stage.addEventListener('touchstart', (e) => {
                if (e.touches.length !== 1) { tracking = false; return; }
                sx = e.touches[0].clientX; sy = e.touches[0].clientY; tracking = true;
            }, { passive: true });
            stage.addEventListener('touchend', (e) => {
                if (!tracking) return;
                tracking = false;
                const t = e.changedTouches[0];
                const dx = t.clientX - sx, dy = t.clientY - sy;
                // Clear horizontal intent only (so a vertical drag doesn't accidentally page). Left -> next.
                if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy) * 1.4) { go(dx < 0 ? 1 : -1); }
            }, { passive: true });
        }
    })();

    // ============================================================
    // Spine cross-link modals (badges native <dialog>; stats). The CONTRACT row uses the
    // SHARED contract modal instead (data-contract-url -> pp-detail-modal, wired below).
    // ============================================================
    (function () {
        const badgesModal = document.getElementById('gd-badges-modal');
        const statsModal = document.getElementById('gd-stats-modal');
        const versionsModal = document.getElementById('gd-versions-modal');
        function wire(m) {
            if (!m) return;
            // Choreographed exit: fade/scale out (.is-closing) then .close(), so the modal leaves as
            // deliberately as it entered instead of popping shut.
            function closeGd() {
                if (!m.open) return;
                pageRecede(false);   // page steps forward as the modal leaves
                if (reduce) { m.close(); return; }
                m.classList.add('is-closing');
                let done = false;
                function finish() {
                    if (done) return; done = true;
                    m.removeEventListener('animationend', onEnd);
                    m.classList.remove('is-closing');
                    if (m.open) m.close();
                }
                function onEnd(e) { if (e.target === m) finish(); }
                m.addEventListener('animationend', onEnd);
                window.setTimeout(finish, 240);   // fallback if animationend doesn't fire
            }
            m.querySelectorAll('[data-gd-modal-close]').forEach((b) => b.addEventListener('click', closeGd));
            m.addEventListener('click', (e) => { if (e.target === m) closeGd(); });
            m.addEventListener('cancel', (e) => { e.preventDefault(); closeGd(); });   // Esc -> animated close
            // Swipe-down-to-close on touch (shared sheet gesture) animates the dialog off itself, so just close.
            if (PlatPursuit.dismissableSheet) { PlatPursuit.dismissableSheet(m, { onClose: function () { pageRecede(false); if (m.close && m.open) m.close(); } }); }
        }
        wire(badgesModal); wire(statsModal); wire(versionsModal);
        // Native <dialog>.showModal() scrolls the page to the dialog on mobile; keep the scroll put.
        function openKeepScroll(m) {
            const y = window.scrollY;
            m.showModal();
            if (window.scrollY !== y) window.scrollTo(0, y);
        }
        document.querySelectorAll('[data-spine-open="badges"]').forEach((op) => {
            op.addEventListener('click', () => {
                if (!badgesModal || !badgesModal.showModal || badgesModal.open) return;
                openKeepScroll(badgesModal);
                pageRecede(true);
                const grid = badgesModal.querySelector('[data-gd-badgegrid]');   // re-arm the staggered card reveal
                if (grid && !reduce) { grid.classList.remove('is-revealing'); void grid.offsetWidth; grid.classList.add('is-revealing'); }
            });
        });

        document.querySelectorAll('[data-versions-open]').forEach((op) => {
            op.addEventListener('click', () => {
                if (!versionsModal || !versionsModal.showModal || versionsModal.open) return;
                openKeepScroll(versionsModal);
                pageRecede(true);
            });
        });

        // My Stats: open + reveal choreography (count-ups, horizon fills, timeline stagger).
        if (statsModal) {
            const revealStats = () => {
                const root = statsModal.querySelector('[data-gd-mystats]');
                if (!root) return;
                root.classList.remove('is-revealing');
                void root.offsetWidth;            // re-arm the reveal animation on each open
                root.classList.add('is-revealing');
                if (PlatPursuit.countUp && !reduce) {
                    statsModal.querySelectorAll('[data-countup]').forEach((el, i) => {
                        const n = parseInt(el.dataset.countup, 10);
                        if (isNaN(n)) return;
                        window.setTimeout(() => PlatPursuit.countUp(el, 650, { from: 0 }), 60 + i * 45);
                    });
                }
                // Fill each Horizon bar from 0 -> its authored target (CSS-gated under reduced motion).
                statsModal.querySelectorAll('.pp-horizon__track').forEach((bar) => {
                    const target = bar.style.getPropertyValue('--horizon-progress') || '0%';
                    bar.style.setProperty('--horizon-progress', '0%');
                    requestAnimationFrame(() => requestAnimationFrame(() => bar.style.setProperty('--horizon-progress', target)));
                });
            };
            document.querySelectorAll('[data-stats-open]').forEach((op) => {
                op.addEventListener('click', () => {
                    if (statsModal.showModal && !statsModal.open) { openKeepScroll(statsModal); pageRecede(true); revealStats(); }
                });
            });
        }
    })();

    // ============================================================
    // Shared contract modal: a [data-contract-url] trigger lazily fetches the rich contract card
    // (contract_modal endpoint) into the .pp-detail-modal shell -- identical to badge-detail / career,
    // so the game page stays in lockstep. Non-linked / anon fetches 404 -> fall back to the career href.
    // (Refactor candidate: hoist this + badge-detail's copy into a shared PlatPursuit util.)
    // ============================================================
    (function () {
        const contractModal = document.getElementById('gd-contract-modal');
        if (!contractModal) return;
        const contractBody = contractModal.querySelector('[data-contract-body]');
        const dialogSel = '.pp-detail-modal__dialog';

        // Focus trap + opener restore (matches badge-detail's hand-rolled modalA11y).
        let opener = null;
        function focusables() {
            const dlg = contractModal.querySelector(dialogSel);
            if (!dlg) return [];
            return Array.prototype.slice.call(dlg.querySelectorAll(
                'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
            )).filter((el) => el.offsetWidth > 0 || el.offsetHeight > 0 || el === document.activeElement);
        }
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Tab' || contractModal.hidden) return;
            const dlg = contractModal.querySelector(dialogSel);
            const f = focusables();
            if (!f.length) { if (dlg) { e.preventDefault(); dlg.focus(); } return; }
            const first = f[0], last = f[f.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }, true);

        function restoreFocus() { if (opener && opener.focus) { try { opener.focus(); } catch (_) { /* gone */ } } opener = null; }
        function closeAnimated() {
            pageRecede(false);
            const dlg = contractModal.querySelector(dialogSel);
            const finish = () => {
                contractModal.hidden = true; contractModal.classList.remove('is-closing');
                document.body.style.overflow = ''; contractBody.innerHTML = ''; restoreFocus();
            };
            if (reduce || !dlg) { finish(); return; }
            contractModal.classList.add('is-closing');
            let done = false;
            const onEnd = (e) => { if (e.target !== dlg) return; done = true; dlg.removeEventListener('animationend', onEnd); finish(); };
            dlg.addEventListener('animationend', onEnd);
            window.setTimeout(() => { if (!done) { dlg.removeEventListener('animationend', onEnd); finish(); } }, 320);
        }

        const dlg0 = contractModal.querySelector(dialogSel);
        if (dlg0 && PlatPursuit.dismissableSheet) {
            PlatPursuit.dismissableSheet(dlg0, {
                scrim: contractModal.querySelector('.pp-detail-modal__scrim'),
                onClose: () => { pageRecede(false); contractModal.hidden = true; contractModal.classList.remove('is-closing'); document.body.style.overflow = ''; contractBody.innerHTML = ''; restoreFocus(); }
            });
        }

        document.addEventListener('click', (e) => {
            if (!e.target.closest) return;
            const link = e.target.closest('[data-contract-url]');
            if (link) {
                if (e.metaKey || e.ctrlKey || e.shiftKey) return;   // let modified clicks open the href
                e.preventDefault();
                opener = link;
                const loadTimer = window.setTimeout(() => link.classList.add('is-loading'), 150);
                const clearLoading = () => { window.clearTimeout(loadTimer); link.classList.remove('is-loading'); };
                fetch(link.getAttribute('data-contract-url'), { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
                    .then((r) => (r.ok ? r.text() : null))
                    .then((html) => {
                        if (html == null) { clearLoading(); window.location.href = link.getAttribute('href'); return; }
                        contractBody.innerHTML = html;
                        let i = 0;
                        contractBody.querySelectorAll('.rpm-job, .rpg').forEach((el) => { el.style.animationDelay = (i++ * 45) + 'ms'; el.classList.add('rpm-in'); });
                        contractBody.querySelectorAll('.rpg__prog-fill').forEach((f) => { if (f.dataset.fill) f.style.width = f.dataset.fill; });
                        contractModal.hidden = false;
                        pageRecede(true);
                        document.body.style.overflow = 'hidden';
                        clearLoading();
                        const d = contractModal.querySelector(dialogSel); if (d) d.focus();
                    })
                    .catch(() => { clearLoading(); window.location.href = link.getAttribute('href'); });
                return;
            }
            if (e.target.closest('[data-contract-close]')) closeAnimated();
        });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !contractModal.hidden) closeAnimated(); });
    })();

    // ============================================================
    // Trophy filter: dim-while-swapping settle + scroll restore
    // ============================================================
    // Result count-up + in-flight loading bar. The count reflects the active filters; on swap we read the
    // new total off the partial's hidden data element and tick from the last shown value.
    const gdCountEl = document.querySelector('[data-gd-count]');
    const gdLoadbar = document.querySelector('[data-gd-loadbar]');
    let loadbarTimer = null;   // only surface the bar if a filter actually takes a beat (instant ones show nothing)
    function loadbarStop() { window.clearTimeout(loadbarTimer); if (gdLoadbar) gdLoadbar.classList.remove('is-active'); }
    let trophyCountLast = gdCountEl ? parseInt(gdCountEl.textContent.replace(/[^0-9]/g, ''), 10) : null;
    function syncTrophyCount(root) {
        if (!gdCountEl || !root) return;
        const data = root.querySelector('#gd-count-data');
        if (!data) return;
        const n = parseInt(data.dataset.trophyCount, 10);
        if (isNaN(n)) return;
        if (trophyCountLast === null) trophyCountLast = n;
        if (PlatPursuit.countUp && !reduce && trophyCountLast !== n) {
            gdCountEl.dataset.countup = n;
            PlatPursuit.countUp(gdCountEl, 600, { from: trophyCountLast });
        } else {
            gdCountEl.textContent = n.toLocaleString();
        }
        trophyCountLast = n;
        const mb = document.querySelector('[data-minibar-count]');   // keep the minibar total in sync
        if (mb) mb.textContent = n.toLocaleString();
    }

    document.body.addEventListener('htmx:beforeRequest', (e) => {
        if (e.target && e.target.id === 'gd-filter-form') {
            const results = document.getElementById('browse-results');
            if (results) results.classList.add('is-swapping');
            // Delay-gate the bar: an instant filter never flashes it; only a slow one (whale library / slow net) surfaces it.
            if (gdLoadbar) { window.clearTimeout(loadbarTimer); loadbarTimer = window.setTimeout(() => gdLoadbar.classList.add('is-active'), 220); }
            try { localStorage.setItem(scrollKey, window.scrollY); } catch (_) { /* ignore */ }
        }
    });
    document.body.addEventListener('htmx:afterSwap', (e) => {
        if (e.detail && e.detail.target && e.detail.target.id === 'browse-results') {
            // Drop BOTH the settle dim and the load-time .pp-reveal hide -- the swapped rows are visible by
            // default now, so nothing can strand them -- then stagger them in with the bespoke WAAPI pass and
            // refill the per-group Horizons (they re-render at 0%).
            e.detail.target.classList.remove('is-swapping', 'pp-reveal');
            loadbarStop();
            syncTrophyCount(e.detail.target);
            syncGroupJump();   // the group-nav re-rendered -> refresh the minibar jump list + its visibility
            staggerSwappedRows(e.detail.target);
            fillBars(e.detail.target);
        }
    });
    // Safety net: clear the loading bar if the filter request errors (afterSwap won't fire). Skip aborts
    // (status 0 -- hx-sync replaced it): the superseding request owns the bar, so stopping here would clear
    // ITS pending timer and hide the affordance on a slow follow-up.
    document.body.addEventListener('htmx:afterRequest', (e) => {
        const d = e.detail;
        if (!d || !d.elt || d.elt.id !== 'gd-filter-form') return;
        if (d.xhr && d.xhr.status === 0) return;
        loadbarStop();
    });

    // Restore scroll after a full navigation that carried filter params.
    const savedScroll = localStorage.getItem(scrollKey);
    if (savedScroll) {
        window.scrollTo({ top: parseInt(savedScroll, 10) || 0, behavior: reduce ? 'auto' : 'smooth' });
        localStorage.removeItem(scrollKey);
    }

    // ============================================================
    // Quick Rate Modal (carried over from the legacy ratings panel; rebuilt in Phase 4)
    // ============================================================
    const quickRateModal = document.getElementById('quick-rate-modal');
    const quickRateForm = document.getElementById('quick-rate-form');

    if (quickRateModal && quickRateForm) {
        var _qrConceptId = null;
        var _qrGroupId = null;
        var _qrSourceBtn = null;

        var sliderDisplayMap = {
            difficulty: 'qr-difficulty-val',
            grindiness: 'qr-grindiness-val',
            fun_ranking: 'qr-fun-val',
            overall_rating: 'qr-overall-val'
        };

        function formatSliderValue(name, value) {
            return name === 'overall_rating' ? parseFloat(value).toFixed(1) : value;
        }

        quickRateForm.querySelectorAll('input[type="range"]').forEach(function(slider) {
            var valEl = document.getElementById(sliderDisplayMap[slider.name]);
            if (valEl) {
                valEl.textContent = formatSliderValue(slider.name, slider.value);
                slider.addEventListener('input', function() {
                    valEl.textContent = formatSliderValue(slider.name, slider.value);
                });
            }
        });

        document.addEventListener('click', function(e) {
            var btn = e.target.closest('.quick-rate-btn');
            if (!btn) return;

            _qrConceptId = btn.dataset.conceptId;
            _qrGroupId = btn.dataset.groupId;
            _qrSourceBtn = btn;

            var hoursLabel = document.getElementById('qr-hours-label');
            if (hoursLabel) hoursLabel.textContent = btn.dataset.hoursLabel || 'Hours to Platinum';

            var existing = btn.dataset.existing ? JSON.parse(btn.dataset.existing) : null;
            var form = quickRateForm;

            form.querySelector('[name="difficulty"]').value = existing ? existing.difficulty : 5;
            form.querySelector('[name="grindiness"]').value = existing ? existing.grindiness : 5;
            form.querySelector('[name="hours_to_platinum"]').value = existing ? existing.hours_to_platinum : '';
            form.querySelector('[name="fun_ranking"]').value = existing ? existing.fun_ranking : 5;
            form.querySelector('[name="overall_rating"]').value = existing ? existing.overall_rating : 3;

            for (var field in sliderDisplayMap) {
                var el = document.getElementById(sliderDisplayMap[field]);
                if (el) el.textContent = formatSliderValue(field, form.querySelector('[name="' + field + '"]').value);
            }

            var submitBtn = document.getElementById('quick-rate-submit');
            if (submitBtn) submitBtn.textContent = existing ? 'Update Rating' : 'Submit Rating';

            var title = document.getElementById('quick-rate-title');
            if (title) title.textContent = existing ? 'Update Your Rating' : 'Rate This Game';

            quickRateModal.showModal();
        });

        quickRateForm.addEventListener('submit', async function(e) {
            e.preventDefault();

            var hours = parseInt(quickRateForm.querySelector('[name="hours_to_platinum"]').value, 10);
            if (!hours || hours < 1) {
                PlatPursuit.ToastManager.show('Please enter the hours to complete.', 'warning');
                return;
            }

            var submitBtn = document.getElementById('quick-rate-submit');
            if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Saving...'; }

            try {
                var data = await PlatPursuit.API.post(
                    '/api/v1/ratings/' + _qrConceptId + '/group/' + _qrGroupId + '/rate/',
                    {
                        difficulty: parseInt(quickRateForm.querySelector('[name="difficulty"]').value, 10),
                        grindiness: parseInt(quickRateForm.querySelector('[name="grindiness"]').value, 10),
                        hours_to_platinum: hours,
                        fun_ranking: parseInt(quickRateForm.querySelector('[name="fun_ranking"]').value, 10),
                        overall_rating: parseFloat(quickRateForm.querySelector('[name="overall_rating"]').value)
                    }
                );

                PlatPursuit.ToastManager.show(data.message || 'Rating saved!', 'success');

                if (data.community_averages) {
                    var panel = _qrSourceBtn.closest('.community-tab-panel') || document.getElementById('community-tabs-section');
                    if (panel) {
                        var grid = panel.querySelector('[data-ratings-grid]');
                        if (grid) {
                            var avg = data.community_averages;
                            var allColors = ['success', 'warning', 'error', 'accent'];
                            var statMap = {
                                difficulty: { val: avg.avg_difficulty, max: 10, thresholds: [4, 8], colors: ['success', 'warning', 'error'] },
                                grindiness: { val: avg.avg_grindiness, max: 10, thresholds: [4, 8], colors: ['success', 'warning', 'error'] },
                                hours: { val: avg.avg_hours, max: 100, thresholds: [25, 75, 100], colors: ['success', 'warning', 'accent', 'error'] },
                                fun: { val: avg.avg_fun, max: 10, thresholds: [4, 8], colors: ['error', 'warning', 'success'] },
                                overall: { val: avg.avg_rating, max: 5, thresholds: [2, 4], colors: ['error', 'warning', 'success'] }
                            };

                            function getColor(val, thresholds, colors) {
                                for (var i = 0; i < thresholds.length; i++) {
                                    if (val < thresholds[i]) return colors[i];
                                }
                                return colors[colors.length - 1];
                            }

                            for (var stat in statMap) {
                                var s = statMap[stat];
                                var cell = grid.querySelector('[data-stat="' + stat + '"]');
                                if (!cell) continue;

                                var color = getColor(s.val, s.thresholds, s.colors);

                                var valEl = cell.querySelector('[data-stat-value]');
                                if (valEl) {
                                    var suffix = stat === 'hours' ? '<span class="text-[0.6rem] text-base-content/40 font-normal">h</span>' :
                                                 stat === 'overall' ? '<span class="text-[0.6rem] text-base-content/40 font-normal">/5</span>' : '';
                                    var display = stat === 'hours' ? Math.round(s.val).toLocaleString() : s.val.toFixed(1);
                                    valEl.innerHTML = display + suffix;
                                    allColors.forEach(function(c) { valEl.classList.remove('text-' + c); });
                                    valEl.classList.add('text-' + color);
                                    valEl.classList.remove('text-base-content/20');
                                }

                                var progress = cell.querySelector('progress');
                                if (progress) {
                                    progress.value = s.val;
                                    allColors.forEach(function(c) { progress.classList.remove('progress-' + c); });
                                    progress.classList.add('progress-' + color);
                                }
                            }
                        }
                        var avg = data.community_averages;
                        var countEl = panel.querySelector('[data-ratings-count]');
                        if (countEl && avg.count !== undefined) {
                            countEl.textContent = 'Based on ' + avg.count.toLocaleString() + ' community rating' + (avg.count === 1 ? '' : 's') + '.';
                        }
                    }
                }

                if (_qrSourceBtn) {
                    _qrSourceBtn.dataset.existing = JSON.stringify({
                        difficulty: parseInt(quickRateForm.querySelector('[name="difficulty"]').value, 10),
                        grindiness: parseInt(quickRateForm.querySelector('[name="grindiness"]').value, 10),
                        hours_to_platinum: hours,
                        fun_ranking: parseInt(quickRateForm.querySelector('[name="fun_ranking"]').value, 10),
                        overall_rating: parseFloat(quickRateForm.querySelector('[name="overall_rating"]').value)
                    });
                    _qrSourceBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg> Update Your Rating';
                }

                quickRateModal.close();
            } catch (error) {
                var msg = 'Failed to save rating.';
                try { var errData = await error.response?.json(); msg = errData?.error || msg; } catch (_) {}
                PlatPursuit.ToastManager.show(msg, 'error');
            } finally {
                if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = _qrSourceBtn?.dataset.existing ? 'Update Rating' : 'Submit Rating'; }
            }
        });
    }
});
