/*
 * Franchise / Series list -- page motion + chrome. Filtering (search + sort + the Franchise/Series/All type
 * radios + show-solo) is HTMX-native via browse-filters.js. The type toggle is a segmented FILTER (radios),
 * not a view switcher, so switching just swaps the grid + preserves the other filters -- no island, no reset.
 * This owns the header count-up + type-aware sublabel, the staggered tile reveal, infinite scroll, the settle,
 * and the sticky mini-bar (search + sort proxy).
 *
 * Wired via PlatPursuit.onPageReady(boot): element wiring re-runs on first load AND on HTMX Back/Forward
 * history restore; the body-level listeners are guarded by `first` so they bind once (rebuild-playbook s7).
 */
(function () {
    var PP = window.PlatPursuit || {};

    var mbSearch = null, mbSort = null, scroller = null, revealHandle = null, handledGrid = null, countLast = null;

    // Staggered tile reveal (shared with the other grouping/browse pages).
    function initReveal() {
        if (revealHandle) { revealHandle.disconnect(); revealHandle = null; }
        var grid = document.getElementById('items-grid');
        if (!grid || !PP.staggerReveal) { return; }
        var fadeEase = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
        var springEase = 'cubic-bezier(0.34, 1.4, 0.64, 1)';
        revealHandle = PP.staggerReveal({
            grid: grid, cardSelector: '.pp-gtile', step: 22,
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
            gridId: 'items-grid', sentinelId: 'fr-sentinel', loadingId: 'fr-loading',
            paginateBy: 32, cardSelector: '.pp-gtile',   // matches FranchiseListView.paginate_by
            onAppend: function (nodes) { if (revealHandle) { revealHandle.observe(nodes); } },
        });
    }

    // Header count tick + type-aware sublabel, read off the freshly-swapped grid.
    function tickCount(grid) {
        if (!grid) { return; }
        var newVal = parseFloat(grid.getAttribute('data-result-count'));
        var headEl = document.getElementById('fr-count');
        if (headEl && PP.countUp && !isNaN(newVal)) {
            if (countLast === null) { countLast = parseFloat(headEl.dataset.countup); }
            headEl.dataset.countup = newVal;
            if (!isNaN(countLast) && countLast !== newVal) { PP.countUp(headEl, 700, { from: countLast }); }
            else { headEl.textContent = newVal.toLocaleString(); }
            countLast = newVal;
        }
        var mbCount = document.querySelector('[data-minibar-count]');
        if (mbCount && !isNaN(newVal)) { mbCount.textContent = newVal.toLocaleString(); }
        var type = grid.getAttribute('data-active-type');
        var sub = document.getElementById('fr-sublabel');
        if (sub && type) { sub.textContent = type === 'series' ? 'series shown' : (type === 'all' ? 'shown' : 'franchises shown'); }
    }

    // Settle the results the instant a non-text filter/type change fires (spans nothing here, but instant).
    function onFormChangeDim(e) {
        var t = e.target;
        if (t && (t.type === 'text' || t.type === 'search')) { return; }
        var r = document.getElementById('browse-results');
        if (r) { r.classList.add('is-swapping'); }
    }

    // ── Mini-bar search + sort proxies (mirror the real toolbar controls; toolbar persists across grid swaps). ──
    function realSearch() { var f = document.getElementById('fr-form'); return f ? f.querySelector('input[name="query"]') : null; }
    function realSort() { var f = document.getElementById('fr-form'); return f ? f.querySelector('select[name="sort"]') : null; }
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

    // ── Body-level listeners (bound once via `first`; document.body persists across a history restore). ──
    function onAfterSwap(e) {
        var t = (e.detail && e.detail.target) || e.target;
        if (!t || t.id !== 'browse-results') { return; }
        var grid = t.querySelector('#items-grid');
        if (grid && grid === handledGrid) { return; }
        handledGrid = grid;
        t.classList.remove('is-swapping');
        tickCount(grid);
        initReveal();
        initScroller();
    }
    function onAfterRequest(e) {
        var elt = e.detail && e.detail.elt;
        if (!elt || elt.id !== 'fr-form') { return; }
        var r = document.getElementById('browse-results');
        if (r) { r.classList.remove('is-swapping', 'pointer-events-none'); }
    }

    function boot(first) {
        handledGrid = null;
        if (first && PP.countUp) {
            var headEl = document.querySelector('[data-fr-count]');
            if (headEl) { PP.countUp(headEl, 900); }
        }
        var form = document.getElementById('fr-form');
        if (form) { form.addEventListener('change', onFormChangeDim); }
        wireMinibar();
        initReveal();
        initScroller();
        if (PP.StickyReveal) { PP.StickyReveal.init(); }
        if (first) {
            document.body.addEventListener('htmx:afterSwap', onAfterSwap);
            document.body.addEventListener('htmx:afterRequest', onAfterRequest);
        }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
