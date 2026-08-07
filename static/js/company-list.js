/*
 * Developers & Publishers list -- page motion + chrome. Filtering itself is HTMX-native via browse-filters.js;
 * this owns the header count-up (the "N shown" tally, which TICKS with the filtered total), the animated
 * collapsible Filters panel (Platform / Country / Genre / Badge -- the visible Role row lives outside it), the
 * staggered tile reveal, infinite scroll, the filter-swap settle, and the sticky mini-bar (search + sort proxy
 * + Filters reach). Same browse-chrome shape as tag-detail.js, adapted for the .pp-gtile grid.
 *
 * Wired via PlatPursuit.onPageReady(boot): element wiring re-runs on first load AND on HTMX Back/Forward
 * history restore; the body-level listeners are guarded by `first` so they bind once (rebuild-playbook s7).
 */
(function () {
    var PP = window.PlatPursuit || {};

    var mbSearch = null, mbSort = null, scroller = null, revealHandle = null, handledGrid = null, countLast = null;
    var form = null, toggle = null, panel = null, badge = null, panelAnimEnd = null, prevBadgeN = null;
    // Fields that DON'T count toward the Filters (panel) badge: paging/sort/search + the Role quick-filter row
    // (it's visible outside the panel, so it doesn't need a badge indicator or to auto-open the panel).
    var SKIP = { page: 1, sort: 1, query: 1, role: 1 };

    // ── Toolbar chrome (collapsible filter panel + active-count badge). ──
    function clearPanelAnim() {
        if (panelAnimEnd && panel) { panel.removeEventListener('transitionend', panelAnimEnd); }
        panelAnimEnd = null;
    }
    function setPanel(open, animate) {
        if (!toggle || !panel) { return; }
        toggle.classList.toggle('is-open', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        clearPanelAnim();
        var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (!animate || reduce) {
            panel.style.height = ''; panel.style.opacity = '';
            if (open) { panel.removeAttribute('hidden'); } else { panel.setAttribute('hidden', ''); }
            return;
        }
        var p = panel;
        if (open) {
            p.style.height = '0px'; p.style.opacity = '0';
            p.removeAttribute('hidden');
            var target = p.scrollHeight;
            void p.offsetHeight;
            p.style.height = target + 'px'; p.style.opacity = '1';
            panelAnimEnd = function (ev) {
                if (ev.target !== p || ev.propertyName !== 'height') { return; }
                p.removeEventListener('transitionend', panelAnimEnd); panelAnimEnd = null;
                p.style.height = ''; p.style.opacity = '';
            };
        } else {
            p.style.height = p.scrollHeight + 'px'; p.style.opacity = '1';
            void p.offsetHeight;
            p.style.height = '0px'; p.style.opacity = '0';
            panelAnimEnd = function (ev) {
                if (ev.target !== p || ev.propertyName !== 'height') { return; }
                p.removeEventListener('transitionend', panelAnimEnd); panelAnimEnd = null;
                p.setAttribute('hidden', ''); p.style.height = ''; p.style.opacity = '';
            };
        }
        p.addEventListener('transitionend', panelAnimEnd);
    }
    function activeCount() {
        if (!form) { return 0; }
        var n = 0;
        new FormData(form).forEach(function (value, key) {
            if (SKIP[key] || !value) { return; }
            n += 1;
        });
        return n;
    }
    function refreshBadge() {
        if (!badge) { return; }
        var n = activeCount();
        badge.textContent = n;
        badge.hidden = (n === 0);
        if (n > 0 && prevBadgeN !== null && n !== prevBadgeN) {
            badge.classList.remove('is-pop'); void badge.offsetWidth; badge.classList.add('is-pop');
        }
        prevBadgeN = n;
    }
    function onFormChange() { refreshBadge(); }
    function onFormChangeDim(e) {
        var t = e.target;
        if (t && (t.type === 'text' || t.type === 'search')) { return; }   // text submits via debounce, not change
        var r = document.getElementById('browse-results');
        if (r) { r.classList.add('is-swapping'); }
    }
    function initToolbar() {
        form = document.getElementById('co-form');
        toggle = document.getElementById('co-filters-toggle');
        panel = document.getElementById('co-advanced');
        badge = document.getElementById('co-filter-count');
        prevBadgeN = null;
        if (!form || !toggle || !panel) { return; }
        toggle.addEventListener('click', function () { setPanel(toggle.getAttribute('aria-expanded') !== 'true', true); });
        form.addEventListener('change', onFormChange);
        form.addEventListener('input', onFormChange);
        form.addEventListener('change', onFormChangeDim);
        refreshBadge();
        setPanel(activeCount() > 0, false);   // open on load only if a PANEL filter is already active
    }

    // ── Grid: staggered tile reveal + infinite scroll (`.pp-gtile`). ──
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
            gridId: 'items-grid', sentinelId: 'co-sentinel', loadingId: 'co-loading',
            paginateBy: 32, cardSelector: '.pp-gtile',   // matches CompanyListView.paginate_by
            onAppend: function (nodes) { if (revealHandle) { revealHandle.observe(nodes); } },
        });
    }

    // Header "N shown" tally + mini-bar count tick to the filtered total.
    function tickCount(grid) {
        if (!grid) { return; }
        var newVal = parseFloat(grid.getAttribute('data-result-count'));
        if (isNaN(newVal)) { return; }
        var headEl = document.getElementById('co-count');
        if (headEl && PP.countUp) {
            if (countLast === null) { countLast = parseFloat(headEl.dataset.countup); }
            if (!isNaN(countLast) && countLast !== newVal) { headEl.dataset.countup = newVal; PP.countUp(headEl, 700, { from: countLast }); }
            else { headEl.dataset.countup = newVal; headEl.textContent = newVal.toLocaleString(); }
            countLast = newVal;
        }
        var mbCount = document.querySelector('[data-minibar-count]');
        if (mbCount) { mbCount.textContent = newVal.toLocaleString(); }
    }

    // ── Mini-bar search + sort proxies + Filters reach. ──
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
        if (toggle && toggle.getAttribute('aria-expanded') !== 'true') { setPanel(true, true); }
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
        refreshBadge();
        tickCount(grid);
        initReveal();
        initScroller();
    }
    function onAfterRequest(e) {
        var elt = e.detail && e.detail.elt;
        if (!elt || elt.id !== 'co-form') { return; }
        var r = document.getElementById('browse-results');
        if (r) { r.classList.remove('is-swapping', 'pointer-events-none'); }
    }

    function boot(first) {
        handledGrid = null;
        if (first && PP.countUp) {
            var headEl = document.querySelector('[data-co-count]');
            if (headEl) { PP.countUp(headEl, 900); }
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
