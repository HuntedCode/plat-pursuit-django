/**
 * Game Detail Page controller (rebuild).
 *
 * Owns: the Trophies/Ranks view switcher (shared .pp-switch +
 * PlatPursuit.wireTablist/slideViewIn/igniteTab/syncViewParam), the trophy-filter
 * settle + scroll restore, hero count-ups, and deep-link jumps ([data-gd-goto]).
 * The hero screenshot lightbox lives in shot-lightbox.js and the ratings machinery
 * in ratings-tab.js -- both on the concept Game page since the list-detail
 * slim-down moved Ratings/About there.
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

    // Leaderboard panel. The ONLY panel not server-rendered: its cost scales with a game's popularity
    // and most visitors never open it, so it is fetched on first activation and then cached in the DOM.
    // Declared above the switcher IIFE: it runs immediately and honors an initial
    // ?view= during setup, so a `let` declared after it would still be in the temporal dead zone.
    const LB_XHR = { headers: { 'X-Requested-With': 'XMLHttpRequest' } };
    const lbText = (r) => (r.ok ? r.text() : Promise.reject(new Error('HTTP ' + r.status)));
    let lbLoaded = false;

    function loadLeaderboard(panel) {
        if (lbLoaded) return;
        lbLoaded = true;
        if (!panel.dataset.lbSrc) return;
        lbDelegate(panel);                                     // attach control/jump handlers once
        panel.innerHTML = lbSkeleton();                        // instant placeholder -- the panel is lazy-loaded,
        lbFetchPanel(panel, panel.dataset.lbSrc);              // so there's nothing to dim; a skeleton reads better
    }

    // Shimmer placeholder shown while the first fetch is in flight (a skeleton, not a spinner, to match the
    // rest of the site). Swapped out when the real panel lands.
    function lbSkeleton() {
        let rows = '';
        for (let i = 0; i < 7; i++) {
            rows += '<div class="gd-lb__skel-row"><span class="gd-lb__skel-cell gd-lb__skel-rank"></span>'
                  + '<span class="gd-lb__skel-cell gd-lb__skel-av"></span>'
                  + '<span class="gd-lb__skel-cell gd-lb__skel-name"></span>'
                  + '<span class="gd-lb__skel-cell gd-lb__skel-meta"></span></div>';
        }
        return '<div class="gd-lb__skel" aria-hidden="true"><span class="gd-lb__skel-cell gd-lb__skel-bar"></span>'
             + '<div class="gd-lb__skel-list">' + rows + '</div></div>';
    }

    // The current view's query, read from the FILTER FORM so every fetch preserves the active slice.
    // It read `aria-pressed` off toggle chips and translated each one by hand; the filters are
    // `.lb-filters` selects now, like every other board, so serializing the form IS the query -- and the
    // hand-translation cannot drift from what the server parses. (`invert` was one of those hands, and is
    // gone entirely.)
    function lbOptsUrl(panel, extra) {
        const form = panel.querySelector('[data-filter-form]');
        const params = new URLSearchParams(form ? new FormData(form) : undefined);
        // Which board is active rides on the .gd-lb root, so every continuation fetch (range/suggest/at)
        // hits the SAME board the user is looking at, not the default one.
        const root = panel.querySelector('.gd-lb');
        const board = root && root.dataset.lbBoardparam;
        if (board) params.set('board', board);
        if (extra) Object.keys(extra).forEach((k) => params.set(k, extra[k]));
        const qs = params.toString();
        return qs ? panel.dataset.lbSrc + '?' + qs : panel.dataset.lbSrc;
    }

    // Fetch the WHOLE panel (initial load or after a control change) and re-wire its observers. `isSwitch`
    // marks a board/filter change (vs the first load): the outgoing list dims during the fetch, then the
    // new content settles in (see lbEntrance) so switching reads as a deliberate transition, not a flash.
    // Which panel fetch is the live one. Board chips and filter selects both call `lbFetchPanel`, so two
    // in quick succession race -- and without a token whichever RESPONDS last wins the DOM, which need not
    // be the one asked for last. It fails silently: the panel and its controls agree with each other, they
    // just describe the wrong board. The landing has carried this guard since it was built.
    let lbSeq = 0;

    function lbFetchPanel(panel, url, isSwitch) {
        if (isSwitch && !reduce) {
            const root = panel.querySelector('.gd-lb');
            if (root) root.classList.add('is-swapping');
        }
        const seq = ++lbSeq;
        fetch(url, LB_XHR).then(lbText)
            .then((html) => {
                if (seq !== lbSeq) return;                     // a later switch overtook this one
                if (panel._lbTeardown) panel._lbTeardown();
                panel.innerHTML = html;
                lbWire(panel);
            })
            .catch(() => {
                if (seq !== lbSeq) return;                     // a superseded fetch must not hijack it
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
            // DLC dropdown toggle: open/close its menu. Checked before the chip handler -- the toggle is a
            // .gd-lb__segchip for styling but carries no board of its own.
            const toggle = e.target.closest('[data-lb-drop-toggle]');
            if (toggle) {
                const menu = toggle.parentElement.querySelector('[data-lb-dropmenu]');
                const willOpen = menu && menu.hidden;
                lbCloseDrops(panel);
                if (willOpen) { menu.hidden = false; toggle.setAttribute('aria-expanded', 'true'); }
                return;
            }
            // Board switcher: select a board and re-fetch the whole panel. Match the CHIP class (not
            // the attribute) so clicks elsewhere in .gd-lb -- which carries the active board -- don't match.
            //
            // The attribute is `data-lb-boardparam`, NOT `data-lb-board`. That name belongs to the shared
            // engine: `leaderboard_board.html` marks its root with it and `wireBoard` finds the root by
            // it. This page briefly used the same name for "which board is selected" on the `.gd-lb`
            // wrapper AND on every chip -- and since the wrapper is the outermost, a
            // `querySelector('[data-lb-board]')` returned IT rather than the board root. That element
            // has no `data-lb-total`, so the engine read a size of 0 and declined to mount: no viewer
            // highlight, no jump, no infinite scroll, and (once the wall started shipping as flow) no
            // visible breakage to point at either.
            const chip = e.target.closest('.gd-lb__segchip');
            if (chip && chip.dataset.lbBoardparam) {
                lbFetchPanel(panel, lbOptsUrl(panel, { board: chip.dataset.lbBoardparam }), true);
                return;
            }
            // The jump chip is wired by `PlatPursuit.wireBoard`, along with the rank box, so this
            // handler no longer owns it -- one implementation of "jump", shared with the other boards.
        });
        // Filters are selects now, so they fire `change` rather than `click`. Delegated from the panel,
        // because the form arrives with every fetched panel and is replaced by the next one.
        panel.addEventListener('change', (e) => {
            if (!e.target.closest('[data-filter-form] select')) return;
            lbFetchPanel(panel, lbOptsUrl(panel), true);   // re-render the whole board in the new view
        });
        // Close an open DLC dropdown on any click outside a dropdown (once; the panel element persists).
        document.addEventListener('click', (e) => {
            if (!e.target.closest('[data-lb-drop]')) lbCloseDrops(panel);
        });
        // Escape closes it and returns focus to the trigger (menu a11y).
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            const openToggle = panel.querySelector('[data-lb-drop-toggle][aria-expanded="true"]');
            if (!openToggle) return;
            lbCloseDrops(panel);
            openToggle.focus();
        });
    }

    function lbCloseDrops(panel) {
        panel.querySelectorAll('[data-lb-dropmenu]').forEach((m) => { m.hidden = true; });
        panel.querySelectorAll('[data-lb-drop-toggle]').forEach((t) => t.setAttribute('aria-expanded', 'false'));
    }


    function lbWire(panel) {
        if (panel._lbTeardown) panel._lbTeardown();
        panel._lbTeardown = null;
        lbSyncMbRank(panel);
        lbSyncMbTitle(panel);
        lbWireFind(panel);
        lbVirtualize(panel);
        lbEntrance(panel);
    }

    // Entrance motion. FIRST reveal (tab open) cascades the on-screen rows in via the SHARED
    // `PlatPursuit.boardEntrance`, which also ticks the board card's Tally. Every later load (a board or
    // filter switch) settles the content in with a soft rise+fade instead -- the outgoing content was
    // dimmed via .is-swapping while the fetch was in flight, so a second cascade would read as a reload
    // rather than as a change. Reduced-motion is handled inside the shared helper.
    function lbEntrance(panel) {
        const root = panel.querySelector('[data-lb-board]');
        if (!panel._lbRevealed) {
            panel._lbRevealed = true;
            PlatPursuit.boardEntrance(root, panel);
            return;
        }
        // A later load (board or filter switch) settles rather than cascading -- the outgoing content was
        // dimmed while the fetch was in flight, so a second cascade would read as a reload.
        //
        // The TALLY still has to tick, though, and it lives inside `boardEntrance`. Calling that helper
        // only on first reveal reproduced the exact bug it was written to fix: the card is replaced by
        // every switch and the new figure simply appeared. `countUp` runs before the reduced-motion
        // return inside the helper, so this is a count-up without a cascade.
        PlatPursuit.boardEntrance(null, panel);
        if (reduce) return;
        const spring = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
        // `.lb-controls` is the chrome card; the column-header strip this used to animate beside the wall
        // was deleted site-wide.
        [panel.querySelector('.lb-controls'), panel.querySelector('[data-lb-wall]')].forEach((el) => {
            if (el) el.animate([{ opacity: 0.5, transform: 'translateY(7px)' }, { opacity: 1, transform: 'none' }],
                               { duration: 240, easing: spring, fill: 'backwards' });
        });
    }

    // The board, on the SHARED mount. `PlatPursuit.wireBoard` reads the totals, page size, rows URL,
    // slice and viewer rank off the `[data-lb-board]` root that `leaderboard_board.html` renders (a
    // name this page must not reuse -- see the switcher handler above), mounts
    // `virtualBoard`, and wires the jump chip and the rank box -- so this board runs the same code path as
    // the Global Boards, badge detail and job detail rather than a fourth copy of the wiring.
    //
    // What stays here is what is genuinely this board's: the minibar chevron, which no other board has.
    function lbVirtualize(panel) {
        // GUARDED like every other shared helper this file reaches for (staggerReveal, countUp,
        // wireSearchField). A stale cached `utils.js` should leave the board as the flow list the server
        // shipped, not throw a TypeError inside the panel-load path and take the rest of the wiring with
        // it -- which is exactly the failure mode a no-hash static file makes reachable.
        if (!window.PlatPursuit || !PlatPursuit.wireBoard) return;
        const root = panel.querySelector('[data-lb-board]');
        const handle = PlatPursuit.wireBoard(root, {
            scope: panel,
            chromeInset: lbChromeInset,
            // Where the viewer's row sits relative to what is on screen, for the minibar chevron.
            onRender: (localTop, localBottom, posOf) => {
                const widget = document.querySelector('[data-lb-mb-rank]');
                if (!widget || widget.hidden || !root) return;
                const vr = parseInt(root.dataset.lbViewerRank || '', 10);
                if (!(vr >= 1)) return;
                const wall = root.querySelector('[data-lb-wall]');
                const H = parseFloat(getComputedStyle(wall).getPropertyValue('--lb-row-h')) || 62;
                const vTop = (posOf(vr) - 1) * H;
                widget.dataset.lbDir = vTop + H < localTop ? 'up' : (vTop > localBottom ? 'down' : 'here');
            },
        });

        panel._lbJump = handle.jump;
        panel._lbTeardown = () => {
            handle.destroy();
            panel._lbJump = null;
            panel._lbTeardown = null;
        };
    }

    // The toolbar search field, wired per panel fetch (its listeners live on the replaced DOM, so they die
    // with it -- no leak).
    function lbWireFind(panel) {
        lbWireSearch(panel.querySelector('[data-lb-find]'), panel.querySelector('[data-lb-suggest]'),
                     panel.querySelector('[data-lb-findform]'), panel);
    }

    // The search behaviour, shared by the toolbar field AND the minibar field. One input for both jumps: a
    // bare number jumps to that rank; text runs a debounced typeahead over the hunters on this board
    // (?suggest= -> JSON) and selecting one jumps to their rank. `panel` supplies the current options
    // (lbOptsUrl reads its toggles) and the jump target, so the minibar field drives the same board below.
    // Reuses PlatPursuit.wireSearchField (clear button + spinner) and debounce.
    // The search behaviour is `PlatPursuit.wireBoardSearch` now -- the same typeahead the other three
    // boards run. What stays here is this board's two injections: where a suggestion comes from (its own
    // `?suggest=`, carrying the board param and filters) and what a pick jumps to. Game detail also has a
    // `?at=` rank PREVIEW, which no other board offers, so it passes `rankUrl` and they do not.
    function lbWireSearch(input, drop, form, panel) {
        if (!PlatPursuit.wireBoardSearch) return;
        PlatPursuit.wireBoardSearch({
            input: input, drop: drop, form: form,
            suggestUrl: (q) => lbOptsUrl(panel, { suggest: q }),
            rankUrl: (n) => lbOptsUrl(panel, { at: n }),
            jump: (rank) => lbJumpToRank(panel, rank),
            total: () => {
                const root = panel.querySelector('[data-lb-board]');
                return parseInt(root ? root.dataset.lbTotal : '', 10) || 0;
            },
        });
    }

    // Observe both end markers (bottom = next page, top = previous page after a jump). unobserve THEN
    // observe forces a fresh initial callback even for a marker that's already observed and still in view --
    // otherwise, when BOTH markers land in the 300px zone at once (a short jump window fits inside it), only
    // the first fires and the other direction is starved until the user scrolls it out and back. Re-arming
    // pumps the starved side; it self-terminates because each load grows the list past the zone.
    // Height of the fixed chrome above the board: the measured nav (--sticky-top, kept accurate by main.js)
    // plus the 52px minibar. Insets the jump target so it lands just below the chrome.
    function lbChromeInset() {
        const nav = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--sticky-top')) || 58;
        return Math.round(nav + 52);
    }

    // Fill the minibar's "You #N" widget from the freshly-loaded panel and show it only when the viewer is
    // ranked. A filter change re-ranks, so this runs on every panel load.
    //
    // The rank rides on the BOARD root now, not the panel root: `data-lb-viewer-rank` is part of the
    // shared board contract (`leaderboard_board.html`) and moved there with the rest of the virtualizer's
    // attributes. Reading it off `.gd-lb` silently produced NaN, which hid the widget rather than
    // erroring -- so the minibar would simply have stopped showing your rank.
    function lbSyncMbRank(panel) {
        const widget = document.querySelector('[data-lb-mb-rank]');
        if (!widget) return;
        const root = panel.querySelector('[data-lb-board]');
        const rank = parseInt(root ? root.dataset.lbViewerRank : '', 10);
        if (rank >= 1) {
            widget.querySelector('[data-lb-mb-rank-n]').textContent = '#' + rank.toLocaleString();
            widget.hidden = false;
        } else {
            widget.hidden = true;
        }
    }

    // Mirror the active board's title into the minibar (shown at desktop widths via CSS). Runs on every
    // panel load, so switching boards updates it. The title rides on the .gd-lb root (data-lb-title).
    function lbSyncMbTitle(panel) {
        const el = document.querySelector('[data-lb-mb-title]');
        if (!el) return;
        const root = panel.querySelector('.gd-lb');
        el.textContent = root ? (root.dataset.lbTitle || '') : '';
    }

    // Both jumps resolve to a canonical rank, then hand off to the virtualizer's scroll-to-position.
    function lbJump(panel, rank) {
        if (rank >= 1 && panel._lbJump) panel._lbJump(rank);
    }
    function lbJumpToMe(panel) {
        // The BOARD root, not `.gd-lb`. `data-lb-viewer-rank` moved onto the shared board root with the
        // rest of the engine's contract; three readers had to follow it and only two did, so the minibar's
        // jump button kept its label, its hover, its chevron and its `aria-label` and silently did nothing
        // -- `undefined` parses to NaN and `lbJump`'s `rank >= 1` guard swallows it.
        const root = panel.querySelector('[data-lb-board]');
        lbJump(panel, parseInt(root ? root.dataset.lbViewerRank : '', 10));
    }
    function lbJumpToRank(panel, n) { lbJump(panel, n); }

    // ============================================================
    // View switcher: Trophies (default) / Ranks
    // ============================================================
    (function () {
        const viewTabs = document.querySelectorAll('#gd-switch .pp-switch__chip[data-view]');
        const views = document.querySelectorAll('.gd-view');
        if (!viewTabs.length || !views.length) return;
        const VIEW_ORDER = ['trophies', 'leaderboard'];   // Ratings/About live on the concept Game page (slim-down)
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
            if (shown && name === 'leaderboard') loadLeaderboard(shown);
        }

        tablist = PlatPursuit.wireTablist(viewTabs, { onSelect: (t) => showView(t.dataset.view) });

        // One-shot ignite on the active pill so it "comes alive" on load.
        const initTab = document.querySelector('#gd-switch .pp-switch__chip[data-view].is-active');
        if (initTab && PlatPursuit.igniteTab) {
            setTimeout(() => { if (initTab.classList.contains('is-active')) PlatPursuit.igniteTab(initTab); }, 260);
        }

        // Deep-link jumps (e.g. the hero "X Players" link -> Ranks tab).
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

        // Leaderboard minibar controls, wired ONCE to the persistent leaderboard panel (its innerHTML swaps,
        // but the element and its data-lb-src / current toggles persist). The panel loads lazily, but these
        // controls are hidden until the Ranks tab is active, which loads it -- so by the time they're usable
        // the panel exists and lbOptsUrl reads its live toggle state.
        const lbPanel = document.getElementById('gd-view-leaderboard');
        if (lbPanel) {
            lbWireSearch(document.querySelector('[data-lb-mb-find]'), document.querySelector('[data-lb-mb-suggest]'),
                         document.querySelector('[data-lb-mb-findform]'), lbPanel);
            const mbLbFilters = document.querySelector('[data-lb-mb-filters]');
            if (mbLbFilters) {
                mbLbFilters.addEventListener('click', () => {
                    // `.lb-controls` is the shared chrome card the filters now live on -- it replaced
                    // this panel's own `.gd-lb__toolbar`, which no longer exists.
                    const target = lbPanel.querySelector('.lb-controls') || lbPanel;
                    target.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
                });
            }
            const mbRank = document.querySelector('[data-lb-mb-rank]');
            if (mbRank) mbRank.addEventListener('click', () => lbJumpToMe(lbPanel));
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
    // Hero screenshot lightbox: EXTRACTED to shot-lightbox.js (shared with the concept
    // Game page, which adopted this hero). Loaded as its own script tag beside this file.
    // ============================================================

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

});
