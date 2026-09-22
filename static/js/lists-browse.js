/*
 * Game Lists browse (/community/lists/) -- page motion + chrome. Filtering (search + sort + the
 * game-count range) is HTMX-native via browse-filters.js; this owns only what the shared controller
 * cannot: the header count-up, the staggered tile reveal, infinite scroll, the settle, and the sticky
 * mini-bar's search/sort proxies.
 *
 * Same shape as franchise-list.js deliberately -- it is the thinnest of the finished browse pages and
 * this page needs strictly less than it does (no type toggle, so no sublabel to keep in step).
 *
 * Wired via PlatPursuit.onPageReady(boot): element wiring re-runs on first load AND on an HTMX
 * Back/Forward history restore; body-level listeners are guarded by `first` so they bind once
 * (rebuild-playbook s7).
 */
(function () {
    var PP = window.PlatPursuit || {};

    var mbSearch = null, mbSort = null, scroller = null, revealHandle = null, handledGrid = null, countLast = null;

    // Staggered tile reveal -- the same grammar the other browse grids use, on the same shared engine.
    function initReveal() {
        if (revealHandle) { revealHandle.disconnect(); revealHandle = null; }
        var grid = document.getElementById('items-grid');
        if (!grid || !PP.staggerReveal) { return; }
        var fadeEase = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
        var springEase = 'cubic-bezier(0.34, 1.4, 0.64, 1)';
        revealHandle = PP.staggerReveal({
            grid: grid, cardSelector: '.pp-gcard', step: 22,
            reveal: function (el, delayMs) {
                if (!el.animate) { return; }
                el.animate([{ opacity: 0 }, { opacity: 1 }],
                           { duration: 420, delay: delayMs, easing: fadeEase, fill: 'backwards' });
                el.animate([{ transform: 'translateY(14px) scale(0.965)' }, { transform: 'none' }],
                           { duration: 500, delay: delayMs, easing: springEase, fill: 'backwards' });
            },
        });
    }

    function initScroller() {
        if (scroller && scroller.destroy) { scroller.destroy(); scroller = null; }
        if (!PP.InfiniteScroller) { return; }
        scroller = PP.InfiniteScroller.create({
            gridId: 'items-grid', sentinelId: 'gl-sentinel', loadingId: 'gl-loading',
            paginateBy: 24, cardSelector: '.pp-gcard',    // matches BrowseListsView.paginate_by
            onAppend: function (nodes) { if (revealHandle) { revealHandle.observe(nodes); } },
        });
    }

    // Header count ticks to the new total, read off the freshly-swapped grid rather than re-fetched.
    function tickCount(grid) {
        if (!grid) { return; }
        var newVal = parseFloat(grid.getAttribute('data-result-count'));
        var headEl = document.getElementById('gl-count');
        if (headEl && PP.countUp && !isNaN(newVal)) {
            if (countLast === null) { countLast = parseFloat(headEl.dataset.countup); }
            headEl.dataset.countup = newVal;
            if (!isNaN(countLast) && countLast !== newVal) { PP.countUp(headEl, 700, { from: countLast }); }
            else { headEl.textContent = newVal.toLocaleString(); }
            countLast = newVal;
        }
        var mbCount = document.querySelector('[data-minibar-count]');
        if (mbCount && !isNaN(newVal)) { mbCount.textContent = newVal.toLocaleString(); }
    }

    // Settle the results the instant a non-text filter changes, so the grid dims rather than sitting
    // stale until the response lands.
    function onFormChangeDim(e) {
        var t = e.target;
        if (!t) { return; }
        // Dim only for controls that ACTUALLY submit. `browse-filters.js` auto-submits checkboxes,
        // radios, selects and anything marked `[data-auto-submit]` -- and nothing else. The old
        // guard excluded text and search and let everything else through, which caught this
        // toolbar's `min_games` / `max_games` number inputs: changing one dimmed `#browse-results`
        // to 40% and no request ever fired to clear it, so the grid stayed greyed until an
        // unrelated filter change. A dim is a promise that something is coming.
        var submits = t.type === 'checkbox' || t.type === 'radio'
            || t.tagName === 'SELECT' || (t.closest && t.closest('[data-auto-submit]'));
        if (!submits) { return; }
        var r = document.getElementById('browse-results');
        if (r) { r.classList.add('is-swapping'); }
    }

    // -- Mini-bar proxies (mirror the real toolbar; the toolbar survives grid swaps, so it stays the
    //    source of truth and the mini-bar just forwards to it). --
    function realSearch() { var f = document.getElementById('gl-form'); return f ? f.querySelector('input[name="q"]') : null; }
    function realSort() { var f = document.getElementById('gl-form'); return f ? f.querySelector('select[name="sort"]') : null; }
    function onMbSearchInput() {
        var real = realSearch();
        if (real) { real.value = mbSearch.value; real.dispatchEvent(new Event('input', { bubbles: true })); }
    }
    function onMbSearchKeydown(e) {
        if (e.key !== 'Enter') { return; }
        e.preventDefault();
        var real = realSearch();
        if (real) { real.value = mbSearch.value; real.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); }
    }
    function onMbSortChange() {
        var real = realSort();
        if (real) { real.value = mbSort.value; real.dispatchEvent(new Event('change', { bubbles: true })); }
    }
    function wireMinibar() {
        mbSearch = document.querySelector('[data-minibar-search]');
        mbSort = document.querySelector('[data-minibar-sort]');
        if (mbSearch) {
            mbSearch.addEventListener('input', onMbSearchInput);
            mbSearch.addEventListener('keydown', onMbSearchKeydown);
            var rs = realSearch();
            if (rs) { rs.addEventListener('input', function () { if (document.activeElement !== mbSearch) { mbSearch.value = rs.value; } }); }
        }
        if (mbSort) {
            var real = realSort();
            if (real) { mbSort.innerHTML = real.innerHTML; mbSort.value = real.value; real.addEventListener('change', function () { mbSort.value = real.value; }); }
            mbSort.addEventListener('change', onMbSortChange);
        }
    }

    // -- Body-level listeners (bound once via `first`; document.body survives a history restore). --
    // THE SPOTLIGHT LIVES OUTSIDE THE SWAP TARGET, so filtering cannot remove it -- the band the
    // server sent on page load simply stayed put above the reader's search results. It collapses
    // instead, driven by the flag the grid carries.
    //
    // The flag comes FROM THE SERVER (`data-has-filters`) rather than being recomputed here.
    // "Is a filter on" has a precise definition in the view -- a `?q=` under three characters
    // narrows nothing and must not count -- and a second copy of that rule in JS is what made the
    // band disappear on the first keystroke of live search in the first place.
    //
    // `inert` alongside the class: the collapsed band is 0px tall and transparent, but without it
    // the link inside is still focusable and clickable. Opacity is not hit-testing.
    // THE REEL'S FADE IS CONDITIONAL, because CSS cannot ask whether a box overflowed. Without
    // this the last cover of a SHORT featured list -- or of any list on a very wide screen, where
    // the reel outgrows its covers -- was faded out while fully visible, with empty band beside
    // it. Measured rather than guessed from a cover count, since the threshold moves with the
    // viewport.
    function syncReel() {
        var reel = document.querySelector('.gl-spotlight__reel');
        if (!reel) { return; }
        reel.classList.toggle('is-overflowing', reel.scrollWidth > reel.clientWidth + 1);
    }

    function syncSpotlight(grid) {
        var wrap = document.querySelector('[data-spotlight-wrap]');
        // NOT `|| !grid`. A swap that returns no `#items-grid` -- an error page, a short response --
        // used to leave the band in whatever state it was already in, which after a filter means a
        // staff pick sitting over somebody's results. Absent a flag, the honest reading is
        // "unfiltered": the band is the canonical page's, and showing it is the recoverable error.
        if (!wrap) { return; }
        var filtered = !!grid && grid.dataset.hasFilters === '1';
        wrap.classList.toggle('is-collapsed', filtered);
        if (filtered) { wrap.setAttribute('inert', ''); }
        else { wrap.removeAttribute('inert'); }
    }

    function onAfterSwap(e) {
        var t = (e.detail && e.detail.target) || e.target;
        if (!t || t.id !== 'browse-results') { return; }
        var grid = t.querySelector('#items-grid');
        // Guard against re-handling the same grid: htmx can fire afterSwap more than once for one swap,
        // and re-running the reveal would replay the animation over already-visible tiles.
        if (grid && grid === handledGrid) { return; }
        handledGrid = grid;
        t.classList.remove('is-swapping');
        syncSpotlight(grid);
        tickCount(grid);
        initReveal();
        initScroller();
    }
    function onAfterRequest(e) {
        var elt = e.detail && e.detail.elt;
        if (!elt || elt.id !== 'gl-form') { return; }
        var r = document.getElementById('browse-results');
        if (r) { r.classList.remove('is-swapping', 'pointer-events-none'); }
    }

    function boot(first) {
        handledGrid = null;
        if (first && PP.countUp) {
            var headEl = document.getElementById('gl-count');
            if (headEl) { PP.countUp(headEl, 900); }
        }
        var form = document.getElementById('gl-form');
        if (form) { form.addEventListener('change', onFormChangeDim); }
        wireMinibar();
        // SELF-SUFFICIENT ON LOAD, rather than trusting the server stamp alone. It is correct today
        // only because `base.html` sets `historyCacheSize = 0`, making Back a real reload; flip
        // that and a restored snapshot would carry whatever class the DOM happened to hold, with
        // nothing re-asserting it. Free to call, so the JS does not depend on a config line
        // somewhere else staying put.
        syncSpotlight(document.getElementById('items-grid'));
        syncReel();
        initReveal();
        initScroller();
        if (PP.StickyReveal) { PP.StickyReveal.init(); }
        if (first) {
            document.body.addEventListener('htmx:afterSwap', onAfterSwap);
            document.body.addEventListener('htmx:afterRequest', onAfterRequest);
            // The reel's overflow depends on the viewport, so the fade has to be re-measured when
            // it changes -- not just once at the width the page happened to load at.
            if (window.ResizeObserver) {
                var reel = document.querySelector('.gl-spotlight__reel');
                if (reel) { new ResizeObserver(syncReel).observe(reel); }
            }
        }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
