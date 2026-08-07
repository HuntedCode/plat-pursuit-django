/*
 * Genres & Themes list -- page motion + chrome. Filtering (search + sort) is HTMX-native via browse-filters.js.
 *
 * Two HTMX swap scopes (the shared switcher pattern -- see Badges / rebuild-playbook section 7):
 *   - The Genres/Themes .pp-switch chips swap the whole #gt-view island (toolbar + grid) so the toolbar
 *     re-renders in sync with the tab; we slide the island in directionally + re-init its chrome.
 *   - A search/sort change swaps only the inner #browse-results grid.
 * No pagination / infinite scroll (bounded taxonomy).
 *
 * Wired via PlatPursuit.onPageReady(boot): boot(first) runs the element wiring on first load AND on HTMX
 * Back/Forward history restore (fresh DOM); the body-level listeners are guarded by `first` so they bind once.
 */
(function () {
    var PP = window.PlatPursuit || {};
    var ORDER = ['genres', 'themes'];

    // Element state -- reassigned by boot() on each fresh DOM (load + history restore).
    var tablist = null, mbSort = null, revealHandle = null, handledGrid = null, countLast = null, lastTab = 'genres';

    function activeChip() { return document.querySelector('.pp-switch__chip.is-active[data-tab]'); }
    function igniteActive() { var chip = activeChip(); if (chip && PP.igniteTab) { PP.igniteTab(chip); } }

    function updateToggleActive(tab) {
        document.querySelectorAll('.pp-switch__chip[data-tab]').forEach(function (chip) {
            var on = chip.getAttribute('data-tab') === tab;
            chip.classList.toggle('is-active', on);
            chip.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        if (tablist) { tablist.syncTabindex(); }   // re-point the roving tab-stop at the new active chip
    }
    function updateTabLabels(tab) {
        var title = document.querySelector('.pp-minibar__title');
        if (title) { title.textContent = tab === 'themes' ? 'Themes' : 'Genres'; }
        var sub = document.getElementById('gtl-sublabel');
        if (sub) { sub.textContent = tab === 'themes' ? 'themes shown' : 'genres shown'; }
    }

    // Staggered tile reveal (shared with Browse / Badges).
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

    // Header result-count tick (old -> new), read off the freshly-swapped grid's data-result-count.
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

    // Mini-bar quick-sort proxy. mbSort is re-resolved by boot (it's a fresh node after a restore); the real
    // select is new after each tab swap, so bind its sync each time (new node -> no leak).
    function currentSort() { return document.querySelector('#gtl-form select[name="sort"]'); }
    function onMbSortChange() {
        var real = currentSort();
        if (real) { real.value = mbSort.value; real.dispatchEvent(new Event('change', { bubbles: true })); }
    }
    function syncSortProxy() {
        var real = currentSort();
        if (!mbSort || !real) { return; }
        mbSort.innerHTML = real.innerHTML;
        mbSort.value = real.value;
        real.addEventListener('change', function () { mbSort.value = real.value; });
    }

    // ── Body-level listeners (bound once via `first`; document.body persists across a history restore). ──
    function onBeforeRequest(e) {
        var el = e.detail && e.detail.elt;
        if (el && el.classList && el.classList.contains('pp-switch__chip') && el.classList.contains('is-active')) {
            e.preventDefault();   // clicking the already-active tab shouldn't fire a redundant swap
        }
    }
    function onAfterSwap(e) {
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
            tickCount(g);
            initReveal();
        }
    }
    function onAfterRequest(e) {
        var elt = e.detail && e.detail.elt;
        var isOurs = elt && (elt.id === 'gtl-form' || (elt.classList && elt.classList.contains('pp-switch__chip')));
        if (!isOurs) { return; }
        var r = document.getElementById('browse-results');
        if (r) { r.classList.remove('is-swapping', 'pointer-events-none'); }
    }

    // ── Element wiring: runs on first load AND on each history restore (fresh DOM). ──
    function boot(first) {
        var chip = activeChip();
        lastTab = chip ? chip.getAttribute('data-tab') : 'genres';
        handledGrid = null;

        if (PP.countUp) {
            var countEl = document.querySelector('[data-gtl-count]');
            if (countEl) { PP.countUp(countEl, 900); }
            document.querySelectorAll('[data-scard-count]').forEach(function (el) { PP.countUp(el, 900); });
        }

        tablist = PP.wireTablist
            ? PP.wireTablist(document.querySelectorAll('.pp-switch__chip[data-tab]'), { manual: true })
            : null;
        setTimeout(igniteActive, 240);

        mbSort = document.querySelector('[data-minibar-sort]');
        if (mbSort) { mbSort.addEventListener('change', onMbSortChange); }
        syncSortProxy();

        initReveal();
        if (PP.StickyReveal) { PP.StickyReveal.init(); }

        if (first) {
            document.body.addEventListener('htmx:beforeRequest', onBeforeRequest);
            document.body.addEventListener('htmx:afterSwap', onAfterSwap);
            document.body.addEventListener('htmx:afterRequest', onAfterRequest);
        }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
