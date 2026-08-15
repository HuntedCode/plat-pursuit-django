/**
 * Game Detail Page controller (rebuild).
 *
 * Owns: the Trophies/Ratings/Ranks/About view switcher (shared .pp-switch +
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

    // countReveal: zero a number now (it shows its SSR final value), then count 0->final after a stagger delay.
    function countReveal(el, delay, decimals) {
        if (!el || !PlatPursuit.countUp) return;
        const target = parseFloat((el.textContent || '').replace(/,/g, ''));
        if (isNaN(target)) return;   // empty state (— / blank): nothing to count
        el.dataset.countup = String(target);
        if (decimals) el.dataset.countupDecimals = String(decimals);
        el.textContent = decimals ? (0).toFixed(decimals) : '0';
        window.setTimeout(() => PlatPursuit.countUp(el, 650, { from: 0 }), delay);
    }
    // countTo: tick a number from its current value to a new one (the "your input mattered" reward on submit).
    function countTo(el, target, decimals) {
        if (!el) return;
        const t = parseFloat(target); if (isNaN(t)) return;
        const from = parseFloat((el.textContent || '').replace(/,/g, ''));
        el.dataset.countup = String(t);
        if (decimals) el.dataset.countupDecimals = String(decimals); else delete el.dataset.countupDecimals;
        if (PlatPursuit.countUp) PlatPursuit.countUp(el, 600, { from: isNaN(from) ? 0 : from });
        else el.textContent = decimals ? t.toFixed(decimals) : String(Math.round(t));
    }

    // Ratings panel entrance, ONE-SHOT on first arrival. Uses the SAME shared staggerReveal engine + motion the
    // About panel does (revealAbout), so the two sibling tabs open with one identical band cascade; then the
    // signature numbers (score / per-quality / hours) count up as their band lands -- parity with About's
    // growing bars and Career's hero count-ups. staggerReveal + countUp self-gate reduced motion; the extra
    // count-up + star-grow block is skipped there (the SSR values are already final).
    let ratingsRevealed = false;
    function revealRatings(panel) {
        if (ratingsRevealed) return;
        ratingsRevealed = true;
        const root = panel.querySelector('[data-gd-rate]');
        if (root && PlatPursuit.staggerReveal) {
            // ALL panels' cards (not just the active group's) so switching DLC never lands on an unrevealed band.
            PlatPursuit.staggerReveal({
                grid: root,
                cardSelector: '.gd-rate__snapshot, .gd-rate__sel, .gd-rate__panel > .gd-acard, .gd-rate__panel > .gd-blurbs',
                step: 55, batchCap: 300,
                reveal: (el, delay) => el.animate(
                    [{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }],
                    { duration: 420, delay, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: 'backwards' }
                ),
            });
        }
        if (reduce) return;
        const active = panel.querySelector('.gd-rate__panel:not(.is-hidden)') || panel;
        // Count the signature numbers up as the verdict band lands (~110ms into the cascade).
        countReveal(active.querySelector('[data-cond-score]'), 120, 1);
        active.querySelectorAll('[data-cond-num]').forEach((el, i) => countReveal(el, 150 + i * 40, 1));
        countReveal(active.querySelector('[data-cond-hours]'), 135, 0);
        panel.querySelectorAll('.gd-rate__stats [data-gd-countup]').forEach((el, i) => {
            const n = parseInt(el.dataset.gdCountup, 10);
            if (isNaN(n)) return;
            el.dataset.countup = n;
            el.textContent = '0';
            window.setTimeout(() => PlatPursuit.countUp(el, 650, { from: 0 }), i * 55);
        });
        // Grow the star bar from empty, pairing with the score count-up (mirrors About resetting its bars first).
        const stars = active.querySelector('[data-cond-stars]');
        if (stars) {
            const fill = stars.style.getPropertyValue('--fill') || '0%';
            stars.style.setProperty('--fill', '0%');
            requestAnimationFrame(() => requestAnimationFrame(() => stars.style.setProperty('--fill', fill)));
        }
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
        // Which board is active rides on the .gd-lb root, so every continuation fetch (range/suggest/at)
        // hits the SAME board the user is looking at, not the default one.
        const root = panel.querySelector('.gd-lb');
        const board = root && root.dataset.lbBoard;
        if (board) params.set('board', board);
        if (extra) Object.keys(extra).forEach((k) => params.set(k, extra[k]));
        const qs = params.toString();
        return qs ? panel.dataset.lbSrc + '?' + qs : panel.dataset.lbSrc;
    }

    // Fetch the WHOLE panel (initial load or after a control change) and re-wire its observers. `isSwitch`
    // marks a board/filter change (vs the first load): the outgoing list dims during the fetch, then the
    // new content settles in (see lbEntrance) so switching reads as a deliberate transition, not a flash.
    function lbFetchPanel(panel, url, isSwitch) {
        if (isSwitch && !reduce) {
            const root = panel.querySelector('.gd-lb');
            if (root) root.classList.add('is-swapping');
        }
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
            // [data-lb-board]) so clicks elsewhere in .gd-lb -- which carries the active board -- don't match.
            const chip = e.target.closest('.gd-lb__segchip');
            if (chip && chip.dataset.lbBoard) {
                lbFetchPanel(panel, lbOptsUrl(panel, { board: chip.dataset.lbBoard }), true);
                return;
            }
            const opt = e.target.closest('[data-lb-opt]');
            if (opt) {
                opt.setAttribute('aria-pressed', opt.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
                lbFetchPanel(panel, lbOptsUrl(panel), true);   // re-render the whole board in the new view
                return;
            }
            if (e.target.closest('[data-lb-jump]')) lbJumpToMe(panel);
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

    function lbScroller() { return document.scrollingElement || document.documentElement; }

    function lbWire(panel) {
        if (panel._lbTeardown) panel._lbTeardown();
        panel._lbTeardown = null;
        lbSyncMbRank(panel);
        lbSyncMbTitle(panel);
        lbWireFind(panel);
        lbVirtualize(panel);
        lbEntrance(panel);
    }

    // Entrance motion, after the virtualizer has mounted the first window. FIRST reveal (tab open): the
    // on-screen rows cascade in -- a one-time grand entrance. Every later load (board / filter switch): the
    // board content settles in with a soft rise+fade (the outgoing content was dimmed via .is-swapping while
    // the fetch was in flight). Reduced-motion skips it entirely.
    function lbEntrance(panel) {
        if (reduce) return;
        const root = panel.querySelector('.gd-lb');
        if (!root) return;
        const spring = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
        if (!panel._lbRevealed) {
            panel._lbRevealed = true;
            panel.querySelectorAll('.gd-lb__row').forEach((row, i) => {
                if (i > 13) return;                            // just the visible window -- keep it quick
                row.animate([{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }],
                            { duration: 340, delay: i * 26, easing: spring, fill: 'backwards' });
            });
            const tally = root.querySelector('.gd-lb__count .pp-tally');   // count the board size up, site-style
            if (tally && PlatPursuit.countUp) PlatPursuit.countUp(tally, 700, { from: 0 });
        } else {
            [root.querySelector('.gd-lb__head'), root.querySelector('.gd-lb__list')].forEach((el) => {
                if (el) el.animate([{ opacity: 0.5, transform: 'translateY(7px)' }, { opacity: 1, transform: 'none' }],
                                   { duration: 240, easing: spring, fill: 'backwards' });
            });
        }
    }

    // The virtualized list. The .gd-lb__list is a full-height spacer (total rows x --lb-row-h), so the
    // PAGE scrollbar spans the whole board; only the visible ~30 rows live in the DOM, absolutely positioned
    // by rank. This is why jumping is just a scroll position and scrolling never inserts rows above the
    // viewport -- the two things that made the old marker/prepend approach lurch.
    function lbVirtualize(panel) {
        const list = panel.querySelector('[data-lb-list]');
        const root = panel.querySelector('.gd-lb');
        if (!list || !root) return;
        const total = parseInt(list.dataset.lbTotal || root.dataset.lbTotal || '0', 10);
        if (!total) return;                                    // empty board -> nothing to virtualize
        const invert = root.dataset.lbInvert === '1';
        const readH = () => parseFloat(getComputedStyle(root).getPropertyValue('--lb-row-h')) || 44;
        let H = readH();                                       // --lb-row-h changes across the md breakpoint
        const BUFFER = 8;                                       // rows rendered beyond the viewport each way
        const EVICT = 30;                                       // keep rows within this of the window in the DOM
        const PAGE = parseInt(root.dataset.lbPageSize, 10) || 50;   // fetch granularity, from the server (no drift)

        const dataByPos = new Map();                           // display-pos (1-indexed) -> row HTML, cached
        const rendered = new Map();                            // display-pos -> element in the DOM
        const fetchedPages = new Set();                        // page indices already fetched / in flight
        let highlightDp = 0;                                  // display-pos kept lit after a jump
        let highlightAnchor = 0;                              // the destination scrollTop the jump scrolls TO
        let highlightArmed = false;                           // true once the jump scroll has actually arrived

        // Light `dp` and remember where the jump is scrolling to. `armed` stays false until that scroll
        // reaches the anchor, so the jump's own (smooth) travel is never mistaken for the user scrolling
        // away. render() arms it on arrival, then a row-plus of further movement is the user leaving.
        function setHighlight(dp, anchorY) {
            clearHighlight();
            highlightDp = dp;
            highlightAnchor = anchorY;
            highlightArmed = false;
            const el = rendered.get(dp);
            if (el) el.classList.add('is-found');
        }
        function clearHighlight() {
            if (!highlightDp) return;
            const el = rendered.get(highlightDp);
            if (el) el.classList.remove('is-found');
            highlightDp = 0;
            highlightArmed = false;
        }

        // Canonical rank of a display position, and vice versa. The label is canonical; layout is by position.
        const rankOf = (dp) => (invert ? total - dp + 1 : dp);
        const posOf = (rank) => (invert ? total - rank + 1 : rank);

        list.style.height = (total * H) + 'px';

        // Seed the cache + DOM from the server-rendered first window; convert those rows to absolute.
        list.querySelectorAll('.gd-lb__row').forEach((el) => {
            const dp = posOf(parseInt(el.dataset.lbRank, 10));
            el.style.top = ((dp - 1) * H) + 'px';
            dataByPos.set(dp, el.outerHTML);
            rendered.set(dp, el);
        });
        fetchedPages.add(0);                                   // first window == page 0

        function mount(dp) {
            const tmp = document.createElement('template');
            tmp.innerHTML = dataByPos.get(dp).trim();
            const el = tmp.content.firstElementChild;
            el.style.top = ((dp - 1) * H) + 'px';
            list.appendChild(el);
            rendered.set(dp, el);
            if (dp === highlightDp) el.classList.add('is-found');   // keep the jump target lit across remount
        }

        function fetchPage(p) {
            if (fetchedPages.has(p)) return;
            fetchedPages.add(p);
            const start = p * PAGE + 1;
            fetch(lbOptsUrl(panel, { range: start, from: rankOf(start), count: PAGE }), LB_XHR)
                .then(lbText)
                .then((html) => {
                    if (!list.isConnected) return;
                    const tmp = document.createElement('template');
                    tmp.innerHTML = html.trim();
                    tmp.content.querySelectorAll('.gd-lb__row').forEach((el, i) => dataByPos.set(start + i, el.outerHTML));
                    render();
                })
                .catch(() => { fetchedPages.delete(p); });     // allow a retry on the next scroll
        }

        function visible() {
            const rect = list.getBoundingClientRect();         // list top relative to the viewport
            const localTop = Math.max(0, -rect.top);
            const localBottom = Math.min(total * H, window.innerHeight - rect.top);
            const first = Math.max(1, Math.floor(localTop / H) + 1 - BUFFER);
            const last = Math.min(total, Math.ceil(localBottom / H) + BUFFER);
            return [first, last, localTop, localBottom];
        }

        function render() {
            // Keep the jump highlight lit through the jump's own scroll, drop it once the USER scrolls away.
            // Movement alone can't tell the two apart, so we ARM on arrival: while the (smooth) scroll is
            // still travelling toward the anchor it stays lit (not armed); once scrollTop lands within a row
            // of the anchor it's arrived (armed); after that, a row-plus of movement is the user leaving.
            if (highlightDp) {
                const dist = Math.abs(lbScroller().scrollTop - highlightAnchor);
                if (!highlightArmed) { if (dist <= H) highlightArmed = true; }
                else if (dist > H) clearHighlight();
            }
            const [first, last, localTop, localBottom] = visible();
            // Evict rows well outside the window.
            rendered.forEach((el, dp) => {
                if (dp < first - EVICT || dp > last + EVICT) { el.remove(); rendered.delete(dp); }
            });
            // Mount visible rows we have data for; fetch the pages for any we don't.
            for (let dp = first; dp <= last; dp++) {
                if (rendered.has(dp)) continue;
                if (dataByPos.has(dp)) mount(dp);
                else fetchPage(Math.floor((dp - 1) / PAGE));
            }
            updateArrow(localTop, localBottom);
        }

        // The minibar chevron: where does the viewer's row sit relative to what's on screen?
        function updateArrow(localTop, localBottom) {
            const widget = document.querySelector('[data-lb-mb-rank]');
            if (!widget || widget.hidden) return;
            const vr = parseInt(root.dataset.lbViewerRank || '', 10);
            if (!(vr >= 1)) return;
            const vTop = (posOf(vr) - 1) * H;
            widget.dataset.lbDir = vTop + H < localTop ? 'up' : (vTop > localBottom ? 'down' : 'here');
        }

        // Jump: smooth-scroll the PAGE so the target row lands ~a third down below the chrome, and keep it
        // lit on arrival. We anchor the highlight to the DESTINATION and let render() arm it once the scroll
        // gets there (see setHighlight) -- so the animation's own travel can't read as "scrolled away" and
        // clear the highlight before it lands. Reduced-motion users get an instant landing (armed at once).
        function jump(rank) {
            const dp = Math.max(1, Math.min(posOf(rank), total));
            const sc = lbScroller();
            const listTopDoc = window.scrollY + list.getBoundingClientRect().top;
            const inset = lbChromeInset();
            const maxTop = Math.max(0, sc.scrollHeight - sc.clientHeight);
            const y = Math.min(Math.max(0, listTopDoc + (dp - 1) * H - inset - (window.innerHeight - inset) * 0.34), maxTop);
            const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            setHighlight(dp, y);                               // anchor to where the scroll will land
            sc.scrollTo({ top: y, behavior: reduce ? 'instant' : 'smooth' });
            render();                                          // mount the current window; the travel mounts the rest
        }
        panel._lbJump = jump;

        // The row height changes across the md breakpoint, so re-read it, resize the spacer, and re-place
        // the rendered rows before rendering again.
        function relayout() {
            H = readH();
            list.style.height = (total * H) + 'px';
            rendered.forEach((el, dp) => { el.style.top = ((dp - 1) * H) + 'px'; });
            render();
        }

        // Both scroll and resize coalesce to one rAF (resize -> full relayout, scroll -> render).
        let ticking = false;
        function tick(fn) {
            if (ticking) return;
            ticking = true;
            requestAnimationFrame(() => { ticking = false; fn(); });
        }
        function onScroll() { tick(render); }
        function onResize() { tick(relayout); }
        window.addEventListener('scroll', onScroll, { passive: true });
        window.addEventListener('resize', onResize, { passive: true });
        panel._lbTeardown = () => {
            window.removeEventListener('scroll', onScroll);
            window.removeEventListener('resize', onResize);
            panel._lbJump = null;
            panel._lbTeardown = null;
        };

        render();
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
    function lbWireSearch(input, drop, form, panel) {
        if (!input || !drop || !form || !PlatPursuit.wireSearchField) return;
        const field = PlatPursuit.wireSearchField(input, { onClear: closeDrop });
        let items = [], active = -1, seq = 0;

        function closeDrop() {
            drop.hidden = true; drop.textContent = ''; items = []; active = -1;
            input.setAttribute('aria-expanded', 'false');
        }
        function setActive(i) {
            active = i;
            drop.querySelectorAll('.gd-lb__sugg').forEach((o, j) => o.classList.toggle('is-active', j === i));
        }
        function render(players) {
            drop.textContent = '';
            items = players || [];
            if (!items.length) { closeDrop(); return; }
            items.forEach((p) => {
                const row = document.createElement('button');
                row.type = 'button'; row.className = 'gd-lb__sugg'; row.setAttribute('role', 'option');
                row.dataset.rank = p.rank;
                const av = document.createElement('span'); av.className = 'gd-lb__sugg-av';
                if (p.avatar) { const img = document.createElement('img'); img.src = p.avatar; img.alt = ''; av.appendChild(img); }
                row.appendChild(av);
                const name = document.createElement('span'); name.className = 'gd-lb__sugg-name';
                name.textContent = p.display;                  // textContent: safe against a hostile name
                row.appendChild(name);
                const rank = document.createElement('span'); rank.className = 'gd-lb__sugg-rank';
                rank.textContent = '#' + Number(p.rank).toLocaleString();
                row.appendChild(rank);
                drop.appendChild(row);
            });
            drop.hidden = false;
            input.setAttribute('aria-expanded', 'true');
            setActive(0);
        }

        const doSuggest = PlatPursuit.debounce((q) => {
            const mine = ++seq;
            fetch(lbOptsUrl(panel, { suggest: q }), LB_XHR)
                .then((r) => (r.ok ? r.json() : Promise.reject()))
                .then((data) => { if (mine === seq) { field.setBusy(false); render(data.players); } })
                .catch(() => { if (mine === seq) { field.setBusy(false); closeDrop(); } });
        }, 180);

        // A bare number previews the hunter at that rank (?at= -> one JSON row); selecting it, or Enter,
        // jumps there. We hold the board total on the panel root, so a rank past the board skips the fetch.
        const doRank = PlatPursuit.debounce((n) => {
            const mine = ++seq;
            const root = panel.querySelector('.gd-lb');
            const total = parseInt(root ? root.dataset.lbTotal : '', 10);
            if (n < 1 || (total && n > total)) { field.setBusy(false); closeDrop(); return; }
            fetch(lbOptsUrl(panel, { at: n }), LB_XHR)
                .then((r) => (r.ok ? r.json() : Promise.reject()))
                .then((data) => { if (mine === seq) { field.setBusy(false); render(data.players); } })
                .catch(() => { if (mine === seq) { field.setBusy(false); closeDrop(); } });
        }, 180);

        input.addEventListener('input', () => {
            const q = input.value.trim();
            if (/^\d+$/.test(q)) { field.setBusy(true); doRank(parseInt(q, 10)); return; }
            if (q.length < 2) { field.setBusy(false); closeDrop(); return; }
            field.setBusy(true);
            doSuggest(q);
        });
        input.addEventListener('keydown', (e) => {
            if (drop.hidden) return;
            const n = drop.querySelectorAll('.gd-lb__sugg').length;
            if (e.key === 'ArrowDown') { e.preventDefault(); setActive(Math.min(active + 1, n - 1)); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(Math.max(active - 1, 0)); }
            else if (e.key === 'Escape') { e.preventDefault(); closeDrop(); }
        });
        form.addEventListener('submit', (e) => {
            e.preventDefault();
            const q = input.value.trim();
            if (/^\d+$/.test(q)) { lbJumpToRank(panel, parseInt(q, 10)); closeDrop(); input.blur(); return; }
            const pick = items[active >= 0 ? active : 0];
            if (pick) { lbJumpToRank(panel, parseInt(pick.rank, 10)); closeDrop(); input.blur(); }
        });
        // mousedown (not click) so it fires before the input's blur closes the dropdown.
        drop.addEventListener('mousedown', (e) => {
            const row = e.target.closest('.gd-lb__sugg');
            if (!row) return;
            e.preventDefault();
            lbJumpToRank(panel, parseInt(row.dataset.rank, 10));
            closeDrop(); input.blur();
        });
        input.addEventListener('blur', () => setTimeout(closeDrop, 120));
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
    // ranked. The rank rides on the .gd-lb root (data-lb-viewer-rank); a filter change re-ranks, so this
    // runs on every panel load.
    function lbSyncMbRank(panel) {
        const widget = document.querySelector('[data-lb-mb-rank]');
        if (!widget) return;
        const root = panel.querySelector('.gd-lb');
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

    // Mirror the active RATINGS group's name (Base Game / DLC) into the minibar (desktop only, via CSS). Reads
    // the active panel's group title, which the template renders ONLY when the game has DLC -- so base-only games
    // leave it empty and the CSS :not(:empty) keeps it hidden. Runs on ratings-tab open + group swap.
    function rateSyncMbTitle() {
        const el = document.querySelector('[data-rate-mb-title]');
        if (!el) return;
        const title = document.querySelector('.gd-rate__panel:not(.is-hidden) .gd-rate__grouptitle');
        el.textContent = title ? title.textContent.trim() : '';
    }

    // Both jumps resolve to a canonical rank, then hand off to the virtualizer's scroll-to-position.
    function lbJump(panel, rank) {
        if (rank >= 1 && panel._lbJump) panel._lbJump(rank);
    }
    function lbJumpToMe(panel) {
        const root = panel.querySelector('.gd-lb');
        lbJump(panel, parseInt(root ? root.dataset.lbViewerRank : '', 10));
    }
    function lbJumpToRank(panel, n) { lbJump(panel, n); }

    // ============================================================
    // View switcher: Trophies (default) / Ratings / Ranks / About
    // ============================================================
    (function () {
        const viewTabs = document.querySelectorAll('#gd-switch .pp-switch__chip[data-view]');
        const views = document.querySelectorAll('.gd-view');
        if (!viewTabs.length || !views.length) return;
        const VIEW_ORDER = ['trophies', 'ratings', 'leaderboard', 'about'];
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
            if (name === 'ratings') rateSyncMbTitle();       // fill the minibar with the current group (Base/DLC)
            if (tablist) tablist.syncTabindex();
            if (changed && activeTab && PlatPursuit.igniteTab) PlatPursuit.igniteTab(activeTab);
            if (PlatPursuit.syncViewParam) {
                PlatPursuit.syncViewParam(name, { default: 'trophies' });
            }
            // Decorative, so it runs LAST: tab state (panels, chips, URL) is fully synced before any
            // entrance animation, and a fault in the flourish can never strand the switcher mid-update.
            if (changed && shown && name === 'about') revealAbout(shown);
            if (changed && shown && name === 'ratings') revealRatings(shown);
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
                    const target = lbPanel.querySelector('.gd-lb__toolbar') || lbPanel;
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
    // Ratings tab: per-group (DLC) selector + quick-rate modal (rebuilt in Phase 4).
    // ============================================================
    (function ratingsTab() {
        const root = document.querySelector('[data-gd-rate]');
        if (!root) return;

        // ── Per-group selector: a pill row (few groups) OR a Base pill + DLC dropdown (many). Both drive the
        //    same panel toggle via [data-rate-ctg]; only the active group's [data-rate-panel] shows. ──
        const drop = root.querySelector('[data-rate-drop]');
        const dropBtn = root.querySelector('[data-rate-drop-toggle]');
        function closeDrop() {
            if (!dropBtn) return;
            dropBtn.setAttribute('aria-expanded', 'false');
            const m = drop && drop.querySelector('.gd-rate__dropmenu');
            if (m) m.hidden = true;
        }
        function selectGroup(ctgId, srcEl) {
            const target = root.querySelector('[data-rate-panel="' + ctgId + '"]');
            if (target && !target.classList.contains('is-hidden')) return;   // already showing this group -- no-op
            root.querySelectorAll('[data-rate-panel]').forEach((p) => p.classList.toggle('is-hidden', p.dataset.ratePanel !== ctgId));
            root.querySelectorAll('.gd-rate__segchip[data-rate-ctg]').forEach((c) => {
                const on = c.dataset.rateCtg === ctgId;
                c.classList.toggle('is-active', on);
                c.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
            // The base pill and the dropdown button are the two mutually exclusive actives. Keep the dropdown
            // label a stable "DLC" (the verdict card's group title now carries the current name) so the button
            // doesn't jump width as you switch between DLCs.
            if (drop && dropBtn) {
                dropBtn.classList.toggle('is-active', !!(srcEl && srcEl.classList.contains('gd-rate__dropitem')));
            }
            // Settle the newly-shown group's bands in -- the same soft rise+fade the leaderboard uses on a
            // board switch (game-detail.js lbEntrance), for one consistent sub-switch motion across the page.
            if (target && !reduce) {
                target.querySelectorAll('.gd-acard, .gd-blurbs:not(.is-empty)').forEach((el) => {
                    el.animate([{ opacity: 0.5, transform: 'translateY(7px)' }, { opacity: 1, transform: 'none' }],
                               { duration: 240, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: 'backwards' });
                });
            }
            rateSyncMbTitle();   // keep the minibar's Base/DLC label in sync with the shown group
        }
        root.addEventListener('click', (e) => {
            const toggle = e.target.closest('[data-rate-drop-toggle]');
            if (toggle) {
                const open = toggle.getAttribute('aria-expanded') === 'true';
                toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
                const menu = drop && drop.querySelector('.gd-rate__dropmenu');
                if (menu) {
                    menu.hidden = open;
                    // Move focus into the menu on open so the role=menu is keyboard-operable, not Tab-only.
                    if (!open) { const first = menu.querySelector('.gd-rate__dropitem'); if (first) first.focus(); }
                }
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
            // Arrow / Home / End roving focus between DLC items while the menu is open (menu-pattern semantics).
            drop.addEventListener('keydown', (e) => {
                if (!dropBtn || dropBtn.getAttribute('aria-expanded') !== 'true') return;
                if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
                const items = Array.from(drop.querySelectorAll('.gd-rate__dropitem'));
                if (!items.length) return;
                e.preventDefault();
                const i = items.indexOf(document.activeElement);
                const next = e.key === 'ArrowDown' ? (items[i + 1] || items[0])
                    : e.key === 'ArrowUp' ? (items[i - 1] || items[items.length - 1])
                    : e.key === 'Home' ? items[0] : items[items.length - 1];
                next.focus();
            });
        }

        // ── Quick-rate modal. Opens from any .quick-rate-btn (SSR'd per group), prefills from data-existing,
        //    POSTs the rating, and live-updates the source group's bars/values without a reload. The endpoint
        //    and the form input names are the API contract (see quick_rate_modal.html). ──
        // The form mechanics -- prefill, slider readouts, the blurb counter, the hours gate, the
        // guidelines agree-on-submit, the POST and every close affordance -- live in the SHARED
        // PlatPursuit.QuickRate (quick-rate.js), because the plat-card share modal composes this same
        // dialog. This file keeps only what is specific to this page: which button was clicked, and the
        // live panel update afterwards.
        if (!PlatPursuit.QuickRate) return;

        let srcBtn = null;

        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.quick-rate-btn');
            if (!btn) return;
            srcBtn = btn;
            const ex = btn.dataset.existing ? JSON.parse(btn.dataset.existing) : null;
            PlatPursuit.QuickRate.open({
                conceptId: btn.dataset.conceptId,
                groupId: btn.dataset.groupId,
                existing: ex,
                blurb: btn.dataset.existingBlurb || '',
                hoursLabel: btn.dataset.hoursLabel || 'Hours to Platinum',
                // Per-GROUP wording: a DLC pack has no platinum to be rough.
                recLabel: btn.dataset.recLabel,
                recLegend: btn.dataset.recLegend,
                // This host says it itself, at the end of applyRating -- after the whole card has been
                // live-updated, so the toast confirms a change you can already see rather than arriving
                // ahead of it. Without the flag RatingFields would add a second one.
                announcesSave: true,
                onOpen: () => pageRecede(true),
                onClose: () => pageRecede(false),
                onSaved: (data, payload) => applyRating(data, payload),
            });
        });

        // Tone + bar-fill thresholds MIRROR core/templatetags/custom_filters.py rating_tone + the SSR widthratio
        // percentages -- keep the two in sync so a live-updated bar matches a reloaded one.
        function toneOf(kind, v) {
            if (kind === 'difficulty' || kind === 'grindiness') return v < 4 ? 'good' : v < 8 ? 'warn' : 'bad';
            if (kind === 'hours') return v < 25 ? 'good' : v < 75 ? 'warn' : v < 100 ? 'high' : 'bad';
            if (kind === 'overall') return v < 2 ? 'bad' : v < 4 ? 'warn' : 'good';
            return v < 4 ? 'bad' : v < 8 ? 'warn' : 'good';   // fun
        }
        // Verdict words mirror core/templatetags/custom_filters.py rating_verdict (keep in sync).
        function verdictOf(kind, v) {
            if (kind === 'difficulty') return v < 2.5 ? 'A breeze' : v < 5 ? 'Fair' : v < 7.5 ? 'Tough' : 'Brutal';
            if (kind === 'grindiness') return v < 2.5 ? 'Breezy' : v < 5 ? 'Some grind' : v < 7.5 ? 'Grindy' : 'A slog';
            if (kind === 'fun') return v < 2.5 ? 'A chore' : v < 5 ? 'So-so' : v < 7.5 ? 'Fun' : 'A blast';
            if (kind === 'overall') return v < 2 ? 'Rough' : v < 3 ? 'Mixed' : v < 4 ? 'Solid' : v < 4.5 ? 'Great' : 'Beloved';
            return '';
        }
        // Synthesized summary sentence -- mirrors core/templatetags/custom_filters.py rating_summary.
        function summaryOf(a) {
            const d = a.avg_difficulty, g = a.avg_grindiness, f = a.avg_fun;
            const diff = d < 2.5 ? 'A breeze' : d < 5 ? 'Fairly easy' : d < 7.5 ? 'Tough' : 'Brutally hard';
            const grind = g < 2.5 ? 'not grindy' : g < 5 ? 'a little grindy' : g < 7.5 ? 'a real grind' : 'a serious slog';
            const fun = f < 2.5 ? 'a chore' : f < 5 ? 'just okay' : f < 7.5 ? 'good fun' : 'a blast to platinum';
            const conj = (f >= 5 && (d >= 5 || g >= 5)) ? 'but' : 'and';
            return diff + ', ' + grind + ', ' + conj + ' ' + fun + '.';
        }

        // Everything that happens AFTER a save lands. The controller has already closed the modal.
        function applyRating(data, payload) {
            const savedMsg = data.message || 'Rating saved!';
            const avg = data.community_averages;
            // Live-update the source group's panel in place (no reload): hero verdict/score/count, the
            // hours callout, and each quality's marker + verdict. Handles empty -> filled (drop --empty).
            const panel = srcBtn && srcBtn.closest('[data-rate-panel]');
            const card = panel && panel.querySelector('[data-rate-grid]');
            if (avg && card) {
                card.classList.remove('gd-cond--empty');
                const summary = card.querySelector('[data-cond-summary]'); if (summary) summary.textContent = summaryOf(avg);
                const sc = card.querySelector('[data-cond-score]'); if (sc) { countTo(sc, avg.avg_rating, 1); sc.classList.add('pp-tally--glow'); }
                const st = card.querySelector('[data-cond-stars]');
                if (st) { st.style.setProperty('--fill', (avg.avg_rating / 5 * 100) + '%'); st.setAttribute('aria-label', avg.avg_rating.toFixed(1) + ' out of 5'); }
                // From the PANEL, not the conditions grid: the total moved down to the action row so it no
                // longer sits beside the split's own "from N that answered" (two counts touching read as
                // one confused sentence). Everything else here is still queried from `card`.
                const ct = panel.querySelector('[data-rate-count]');
                if (ct && avg.count != null) ct.textContent = avg.count.toLocaleString() + ' rating' + (avg.count === 1 ? '' : 's');
                const hrs = card.querySelector('[data-cond-hours]'); if (hrs && avg.avg_hours != null) countTo(hrs, Math.round(avg.avg_hours), 0);
                const byStat = { difficulty: avg.avg_difficulty, grindiness: avg.avg_grindiness, fun: avg.avg_fun };
                Object.keys(byStat).forEach((kind) => {
                    const v = byStat[kind];
                    const tile = card.querySelector('.gd-cond__tile[data-stat="' + kind + '"]');
                    if (!tile || v == null) return;
                    tile.dataset.tone = toneOf(kind, v);
                    const vd = tile.querySelector('[data-cond-verdict]'); if (vd) vd.textContent = verdictOf(kind, v);
                    const nm = tile.querySelector('[data-cond-num]'); if (nm) countTo(nm, v, 1);
                });
                // The recommendation split. Guarded on the key existing rather than on its contents: the
                // averages dict is CACHED for an hour, so for a while after the field ships some panels
                // are drawing from a dict pickled before it existed. Missing key -> leave the row alone
                // rather than blanking a figure that is merely stale.
                const rec = avg.recommendation_split;
                const recEl = card.querySelector('[data-cond-rec]');
                if (recEl && rec && rec.answered) {
                    // Driven off the server's ORDERED options list rather than off known slugs, so the
                    // four display strings and the vocabulary itself stay server-side -- a reworded label
                    // or a new option lands here without a JS edit.
                    (rec.options || []).forEach((opt) => {
                        const cell = recEl.querySelector('[data-rec-cell="' + opt.value + '"]');
                        if (!cell) return;
                        const pct = cell.querySelector('[data-rec-pct]');
                        const n = cell.querySelector('[data-rec-count]');
                        if (pct) pct.textContent = opt.pct + '%';
                        if (n) n.textContent = opt.count.toLocaleString();
                        // A share that just went from 0 to 1 stops being held back, and one that is still
                        // 0 stays quiet -- both directions, or the dimming survives its own reason.
                        cell.classList.toggle('is-none', !opt.count);
                    });
                    const total = recEl.querySelector('[data-cond-rec-n]');
                    if (total) total.textContent = rec.answered.toLocaleString();
                    recEl.classList.remove('is-empty');
                }
                // Live-update the rating-spread chart bars + per-bar counts (10 columns keyed on the
                // integer half-step 1..10). Empty count -> clear to '' so the :empty label hides.
                if (avg.distribution) {
                    avg.distribution.forEach((row) => {
                        const el = card.querySelector('.gd-dist__col[data-dist-step="' + row.step + '"]');
                        if (!el) return;
                        const fill = el.querySelector('[data-dist-fill]'); if (fill) fill.style.height = row.bar + '%';
                        const dn = el.querySelector('[data-dist-n]'); if (dn) dn.textContent = row.count || '';
                    });
                }
            }
            // Live-sync the viewer's "Your take" comparison band (add / update / remove).
            if (card && avg) syncYouTake(card, payload, avg);
            // Live-sync the viewer's own quick take in this group's strip (add / replace / remove). Prefer
            // the server-echoed stored blurb (sanitized) over the raw typed text so the live card matches
            // what everyone else -- and the author on reload -- will see.
            syncOwnBlurb(panel, data.blurb ?? payload.blurb, payload.overall_rating);
            if (srcBtn) {
                // This attribute IS the prefill contract, and it is rebuilt from scratch here -- so
                // anything left out is silently absent the next time the modal opens. Built by the shared
                // `RatingFields.prefillFrom` rather than by hand, because the hand-built version was
                // missing `recommendation` when that field was added: the modal prefilled it fine on
                // first open (the SERVER renders this attribute) and then blank after any save, until a
                // reload put the server's version back.
                srcBtn.dataset.existing = JSON.stringify(
                    PlatPursuit.RatingFields.prefillFrom(data, payload)
                );
                srcBtn.dataset.existingBlurb = payload.blurb;
                const lbl = srcBtn.querySelector('span');
                if (lbl) lbl.textContent = 'Update rating';
            }
            // Toast AFTER close so it lands on the viewport #toast-container, not the modal's popover
            // (which the dialog takes down on close). The in-place live-update above is the primary
            // feedback; this is the persistent confirmation once the modal is gone.
            PlatPursuit.ToastManager.show(savedMsg, 'success');
        }

        // Build the viewer's own quick-take card (matches _blurb_card.html, You variant, no report). Name and
        // text go in via textContent -- never innerHTML -- so the viewer's own blurb can't inject markup.
        function buildBlurbCard(text, overall) {
            const li = document.createElement('li');
            li.className = 'gd-blurb gd-blurb--you';
            li.setAttribute('data-blurb-own', '');
            const url = root.dataset.viewerUrl || '#';
            const av = root.dataset.viewerAvatar || '';
            const fill = Math.max(0, Math.min(100, (overall / 5) * 100));
            li.innerHTML =
                '<a class="gd-blurb__av" href="' + url + '" tabindex="-1" aria-hidden="true">' +
                (av ? '<img alt="" loading="lazy" />' : '') + '</a>' +
                '<div class="gd-blurb__body"><div class="gd-blurb__head">' +
                '<a class="gd-blurb__name" href="' + url + '"></a>' +
                '<span class="gd-blurb__you">You</span>' +
                '<span class="gd-blurb__stars" style="--fill: ' + fill + '%;" role="img" aria-label="' + overall.toFixed(1) + ' out of 5"><span class="gd-blurb__stars-on"></span></span>' +
                '</div><p class="gd-blurb__text"></p>' +
                '<div class="gd-blurb__foot"><time class="gd-blurb__time">just now</time></div></div>';
            const img = li.querySelector('.gd-blurb__av img'); if (img) img.src = av;
            li.querySelector('.gd-blurb__name').textContent = root.dataset.viewerName || 'You';
            li.querySelector('.gd-blurb__text').textContent = text;
            return li;
        }
        // Add / replace / remove the viewer's own card in a group's Quick takes strip after they post.
        function syncOwnBlurb(panel, text, overall) {
            const wrap = panel && panel.querySelector('[data-blurbs]');
            if (!wrap) return;
            const list = wrap.querySelector('[data-blurbs-list]');
            const existing = list && list.querySelector('[data-blurb-own]');
            const countEl = wrap.querySelector('[data-blurbs-count]');
            const bump = (d) => { if (countEl) countEl.textContent = String(Math.max(0, (parseInt(countEl.textContent, 10) || 0) + d)); };
            if (!text) {   // blurb cleared -> drop the card + decrement; re-empty the strip if it was the only one
                if (existing) { existing.remove(); bump(-1); }
                if (list && !list.children.length) wrap.classList.add('is-empty');
                return;
            }
            const fresh = buildBlurbCard(text, overall);
            if (existing) { existing.replaceWith(fresh); }                            // edit -> total unchanged
            else { if (list) list.insertBefore(fresh, list.firstChild); bump(1); }    // new take -> increment
            wrap.classList.remove('is-empty');
        }

        // Synthesized "you vs community" sentence -- mirrors core/templatetags/custom_filters.py
        // rating_comparison (keep the T threshold + wording in sync).
        function comparisonOf(u, a) {
            const d = u.difficulty - a.avg_difficulty, g = u.grindiness - a.avg_grindiness, f = u.fun_ranking - a.avg_fun;
            const T = 0.8;
            if (Math.abs(d) < T && Math.abs(g) < T && Math.abs(f) < T) return 'Right in line with the community.';
            const diff = d >= T ? 'tougher than most' : d <= -T ? 'easier than most' : 'about as tough as most';
            const grind = g >= T ? 'grindier' : g <= -T ? 'less grindy' : 'about as grindy';
            const fun = f >= T ? 'more fun' : f <= -T ? 'less fun' : 'just as fun';
            const conj = (f >= T && (d >= T || g >= T)) ? 'but' : 'and';
            return 'You found it ' + diff + ', ' + grind + ', ' + conj + ' ' + fun + '.';
        }
        // Add / update / remove the "Your take" band after a submit. Shows only with >1 rating to compare to.
        function syncYouTake(card, u, a) {
            let band = card.querySelector('[data-cond-you]');
            if (!a || a.count <= 1) { if (band) band.remove(); return; }
            if (!band) {
                band = document.createElement('div');
                band.className = 'gd-cond__you';
                band.setAttribute('data-cond-you', '');
                band.innerHTML = '<span class="gd-cond__you-lbl">Your take</span>' +
                    '<p class="gd-cond__you-txt" data-cond-you-txt></p>' +
                    '<span class="gd-cond__you-scores" data-cond-you-scores></span>';
                const tiles = card.querySelector('.gd-cond__metrics');
                if (tiles) tiles.insertAdjacentElement('afterend', band); else card.appendChild(band);
            }
            const txt = band.querySelector('[data-cond-you-txt]'); if (txt) txt.textContent = comparisonOf(u, a);
            const sc = band.querySelector('[data-cond-you-scores]');
            // Numbers only (our own computed floats) -> innerHTML is safe here.
            if (sc) sc.innerHTML = 'You <b>' + u.overall_rating.toFixed(1) + '</b>' +
                '<span class="gd-cond__you-vs">vs</span>community <b>' + a.avg_rating.toFixed(1) + '</b>';
        }

        // ── Report a quick take. One shared modal, opened from any card's [data-blurb-report]. ──
        const reportModal = document.getElementById('gd-blurb-report-modal');
        const reportForm = document.getElementById('gd-blurb-report-form');
        if (reportModal && reportForm) {
            let reportId = null, reportCard = null;
            function closeReport() { pageRecede(false); if (reportModal.close && reportModal.open) reportModal.close(); }
            reportModal.querySelectorAll('[data-gd-modal-close]').forEach((b) => b.addEventListener('click', closeReport));
            reportModal.addEventListener('click', (e) => { if (e.target === reportModal) closeReport(); });
            reportModal.addEventListener('cancel', (e) => { e.preventDefault(); closeReport(); });
            if (PlatPursuit.dismissableSheet) PlatPursuit.dismissableSheet(reportModal, { onClose: closeReport });

            document.addEventListener('click', (e) => {
                const btn = e.target.closest('[data-blurb-report]');
                if (!btn) return;
                reportId = btn.dataset.ratingId;
                reportCard = btn.closest('.gd-blurb');
                reportForm.reset();
                if (reportModal.showModal && !reportModal.open) {
                    const y = window.scrollY; reportModal.showModal();
                    if (window.scrollY !== y) window.scrollTo(0, y);
                    pageRecede(true);
                }
            });

            reportForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                if (!reportId) return;
                const rSubmit = reportForm.querySelector('[data-gd-report-submit]');
                if (rSubmit) { rSubmit.disabled = true; rSubmit.textContent = 'Sending…'; }
                const payload = {
                    reason: reportForm.querySelector('[data-gd-report-reason]').value,
                    details: (reportForm.querySelector('[data-gd-report-details]').value || '').slice(0, 500),
                };
                try {
                    const data = await PlatPursuit.API.post('/api/v1/ratings/blurb/' + reportId + '/report/', payload);
                    if (reportCard) reportCard.classList.add('is-reported');
                    closeReport();
                    // Toast AFTER close: while the modal is open the toast host is its top-layer popover, which
                    // the dialog takes down with it on close -- so a success toast would flash and vanish. Once
                    // closed, ToastManager routes to the viewport #toast-container, where it persists.
                    PlatPursuit.ToastManager.show(data.message || 'Thanks, our team will take a look.', 'success');
                } catch (error) {
                    let msg = 'Could not submit report.';
                    try { const ed = await error.response?.json(); msg = ed?.error || msg; } catch (_) { /* ignore */ }
                    PlatPursuit.ToastManager.show(msg, 'error');
                } finally {
                    if (rSubmit) { rSubmit.disabled = false; rSubmit.textContent = 'Submit report'; }
                }
            });
        }

        // ── Community Guidelines sheet. Opens OVER the compose modal from its notice's [data-gd-guidelines-open]
        //    link, so reading the rules never loses the in-progress take. Read-only; agreement is recorded on
        //    submit. Stacking a second <dialog>.showModal() puts it on top; closing returns focus to the modal. ──
        //    Redundant now -- `RatingFields.attach` wires the sheet, and the only link to it lives inside
        //    the fields that attach composes. Kept as page-load belt-and-braces; both calls are idempotent.
        if (PlatPursuit.wireGuidelinesSheet) PlatPursuit.wireGuidelinesSheet();
    })();
});
