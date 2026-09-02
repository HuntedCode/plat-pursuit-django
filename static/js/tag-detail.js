/*
 * Genre / Theme DETAIL -- page motion + chrome. Filtering itself is HTMX-native via browse-filters.js; this
 * owns the header count-ups, the animated collapsible Filters panel, the staggered card reveal, infinite
 * scroll, the filter-swap settle, and the sticky mini-bar (search + sort proxy + Filters reach).
 *
 * A single game list (no view switcher), so it's the Browse Games chrome scoped to one tag. Wired via
 * PlatPursuit.onPageReady(boot): element wiring re-runs on first load AND on HTMX Back/Forward history
 * restore; the body-level listeners are guarded by `first` so they bind once (rebuild-playbook section 7).
 */
(function () {
    var PP = window.PlatPursuit || {};

    // Element state -- reassigned by boot()/initToolbar() on each fresh DOM (load + history restore).
    var mbSearch = null, mbSort = null, scroller = null, revealHandle = null, handledGrid = null, countLast = null;
    var form = null, panelHandle = null;

    // -- Toolbar chrome. `PlatPursuit.filterPanel` owns the drawer, the badge, the dim and the fades; this
    // page only says which elements and which params are display state rather than filters. --
    function initToolbar() {
        if (panelHandle && panelHandle.destroy) { panelHandle.destroy(); }
        form = document.getElementById('tagd-form');
        panelHandle = PP.filterPanel && PP.filterPanel({
            form: form,
            toggle: '#tagd-filters-toggle',
            panel: '#tagd-advanced',
            countEl: '#tagd-filter-count',
            // Platform/regions have NO default here (unlike Browse Games), so they ARE filters.
            skip: { page: 1, view: 1, sort: 1, query: 1 },
        });
    }

    // ── Grid: infinite scroll + staggered card reveal (`.pp-gcard`). ──
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
                var bar = el.querySelector('.pp-gcard__barfill');
                if (bar) {
                    bar.animate([{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }],
                                { duration: 540, delay: delayMs + 150, easing: fadeEase, fill: 'backwards' });
                }
            },
        });
    }
    function initScroller() {
        if (scroller && scroller.destroy) { scroller.destroy(); scroller = null; }
        if (!PP.InfiniteScroller) { return; }
        scroller = PP.InfiniteScroller.create({
            gridId: 'items-grid', sentinelId: 'tagd-sentinel', loadingId: 'tagd-loading',
            paginateBy: 30, cardSelector: '.pp-gcard',   // matches TagDetailBaseView.paginate_by
            onAppend: function (nodes) { if (revealHandle) { revealHandle.observe(nodes); } },
        });
    }

    // Filtered-count tick -> the mini-bar count (the header stat is the unfiltered catalogue total, stays put).
    function tickCount(grid) {
        var mbCount = document.querySelector('[data-minibar-count]');
        if (!grid || !mbCount) { return; }
        var newVal = parseFloat(grid.getAttribute('data-result-count'));
        if (isNaN(newVal)) { return; }
        if (countLast !== null && countLast === newVal) { return; }
        countLast = newVal;
        mbCount.textContent = newVal.toLocaleString();
    }

    // ── Mini-bar search + sort proxies (mirror the real toolbar controls) + Filters reach. ──
    function realSearch() { return form ? form.querySelector('input[name="query"]') : null; }
    function realSort() { return form ? form.querySelector('select[name="sort"]') : null; }
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
    function onMbFiltersClick() {
        var tg = document.getElementById('tagd-filters-toggle');
        if (tg && tg.getAttribute('aria-expanded') !== 'true' && panelHandle) { panelHandle.setOpen(true, true); }
        if (form) { form.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
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
        var mbFilters = document.querySelector('[data-minibar-filters]');
        if (mbFilters) { mbFilters.addEventListener('click', onMbFiltersClick); }
    }

    // ── Body-level listeners (bound once via `first`; document.body persists across a history restore). ──
    function onAfterSwap(e) {
        var t = (e.detail && e.detail.target) || e.target;
        if (!t || t.id !== 'browse-results') { return; }
        var grid = t.querySelector('#items-grid');
        if (grid && grid === handledGrid) { return; }
        handledGrid = grid;
        t.classList.remove('is-swapping');
        if (panelHandle) { panelHandle.refresh(); }
        tickCount(grid);
        initReveal();
        initScroller();
    }
    function onAfterRequest(e) {
        var elt = e.detail && e.detail.elt;
        if (!elt || elt.id !== 'tagd-form') { return; }
        var r = document.getElementById('browse-results');
        if (r) { r.classList.remove('is-swapping', 'pointer-events-none'); }
    }

    // ── Element wiring: runs on first load AND on each history restore (fresh DOM). ──
    function boot(first) {
        handledGrid = null;
        if (first && PP.countUp) {   // scard values count up on first load only
            document.querySelectorAll('[data-scard-count]').forEach(function (el) { PP.countUp(el, 900); });
        }
        initToolbar();
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
