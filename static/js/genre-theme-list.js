/*
 * Genres & Themes list -- page motion + chrome. Filtering (search + sort) is HTMX-native via browse-filters.js.
 *
 * Two HTMX swap scopes (the shared switcher pattern -- see Badges / rebuild-playbook section 7):
 *   - The Genres/Themes .pp-switch chips swap the whole #gt-view island (toolbar + grid) so the toolbar
 *     re-renders in sync with the tab; we slide the island in directionally + re-init its chrome.
 *   - A search/sort change swaps only the inner #browse-results grid.
 * No pagination / infinite scroll (bounded taxonomy).
 */
document.addEventListener('DOMContentLoaded', function () {
    var PP = window.PlatPursuit || {};
    var ORDER = ['genres', 'themes'];

    function activeChip() { return document.querySelector('.pp-switch__chip.is-active[data-tab]'); }
    var _initChip = activeChip();
    var lastTab = _initChip ? _initChip.getAttribute('data-tab') : 'genres';

    // ── Header + .scard count-ups on load. ──
    if (PP.countUp) {
        var countEl = document.querySelector('[data-gtl-count]');
        if (countEl) { PP.countUp(countEl, 900); }
        document.querySelectorAll('[data-scard-count]').forEach(function (el) { PP.countUp(el, 900); });
    }

    // ── Switcher: keyboard model + ignite. The chips are HTMX <a> links (manual mode -- arrows move focus,
    //    click/Enter does the swap). ──
    if (PP.wireTablist) {
        PP.wireTablist(document.querySelectorAll('.pp-switch__chip[data-tab]'), { manual: true });
    }
    function igniteActive() {
        var chip = activeChip();
        if (chip && PP.igniteTab) { PP.igniteTab(chip); }
    }
    setTimeout(igniteActive, 240);

    function updateToggleActive(tab) {
        document.querySelectorAll('.pp-switch__chip[data-tab]').forEach(function (chip) {
            var on = chip.getAttribute('data-tab') === tab;
            chip.classList.toggle('is-active', on);
            chip.setAttribute('aria-selected', on ? 'true' : 'false');
        });
    }
    function updateTabLabels(tab) {
        var title = document.querySelector('.pp-minibar__title');
        if (title) { title.textContent = tab === 'themes' ? 'Themes' : 'Genres'; }
        var sub = document.getElementById('gtl-sublabel');
        if (sub) { sub.textContent = tab === 'themes' ? 'themes shown' : 'genres shown'; }
    }
    // Clicking the already-active tab shouldn't fire a redundant swap.
    document.body.addEventListener('htmx:beforeRequest', function (e) {
        var el = e.detail && e.detail.elt;
        if (el && el.classList && el.classList.contains('pp-switch__chip') && el.classList.contains('is-active')) {
            e.preventDefault();
        }
    });

    // ── Staggered tile reveal (shared with Browse / Badges). ──
    var revealHandle = null, handledGrid = null;
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

    // ── Header result-count tick (old -> new), read off the freshly-swapped grid's data-result-count. ──
    var countLast = null;
    function tickCount(grid) {
        var headEl = document.getElementById('gtl-count');
        if (!grid || !headEl || !PP.countUp) { return; }
        var newVal = parseFloat(grid.getAttribute('data-result-count'));
        if (isNaN(newVal)) { return; }
        if (countLast === null) { countLast = parseFloat(headEl.dataset.countup); }
        headEl.dataset.countup = newVal;
        if (!isNaN(countLast) && countLast !== newVal) { PP.countUp(headEl, 700, { from: countLast }); }
        else { headEl.textContent = newVal.toLocaleString(); }
        countLast = newVal;
        var mbCount = document.querySelector('[data-minibar-count]');
        if (mbCount) { mbCount.textContent = newVal.toLocaleString(); }
    }

    // ── Mini-bar quick-sort proxy. mbSort persists (outside the island); the real select is new after each
    //    tab swap. Bind the mbSort listener ONCE (reads the current real select at event time); re-sync values
    //    + bind the new real select on each swap. ──
    var mbSort = document.querySelector('[data-minibar-sort]');
    function currentSort() { return document.querySelector('#gtl-form select[name="sort"]'); }
    function syncSortProxy() {
        var real = currentSort();
        if (!mbSort || !real) { return; }
        mbSort.innerHTML = real.innerHTML;
        mbSort.value = real.value;
        real.addEventListener('change', function () { mbSort.value = real.value; });
    }
    if (mbSort) {
        mbSort.addEventListener('change', function () {
            var real = currentSort();
            if (real) { real.value = mbSort.value; real.dispatchEvent(new Event('change', { bubbles: true })); }
        });
    }

    document.body.addEventListener('htmx:afterSwap', function (e) {
        var t = (e.detail && e.detail.target) || e.target;
        if (!t) { return; }
        if (t.id === 'browse-results') {
            // Filter/search swap: just the grid.
            var grid = t.querySelector('#items-grid');
            if (grid && grid === handledGrid) { return; }
            handledGrid = grid;
            t.classList.remove('is-swapping');
            tickCount(grid);
            initReveal();
        } else if (t.id === 'gt-view') {
            // Tab swap: whole island. Sync chrome + slide the new island in directionally.
            var g = t.querySelector('#items-grid');
            var newTab = g ? g.getAttribute('data-active-tab') : lastTab;
            updateToggleActive(newTab);
            updateTabLabels(newTab);
            igniteActive();
            if (PP.slideViewIn) { PP.slideViewIn(t, lastTab, newTab, ORDER); }
            lastTab = newTab;
            handledGrid = g;
            syncSortProxy();
            if (PP.StickyReveal) { PP.StickyReveal.init(); }
            tickCount(g);
            initReveal();
        }
    });
    // Safety net: clear the settle dim when a swap request completes (incl. non-2xx / aborted).
    document.body.addEventListener('htmx:afterRequest', function (e) {
        var elt = e.detail && e.detail.elt;
        var isOurs = elt && (elt.id === 'gtl-form' || (elt.classList && elt.classList.contains('pp-switch__chip')));
        if (!isOurs) { return; }
        var r = document.getElementById('browse-results');
        if (r) { r.classList.remove('is-swapping', 'pointer-events-none'); }
    });

    initReveal();
    if (PP.StickyReveal) { PP.StickyReveal.init(); }
    syncSortProxy();
});
