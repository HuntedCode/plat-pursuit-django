/*
 * Genres & Themes list -- page motion + chrome. Filtering (search + sort) is HTMX-native via
 * browse-filters.js; this owns the header count-up, the staggered tile reveal, the search/sort settle, the
 * sticky mini-bar (+ sort proxy), and the directional Genres/Themes slide.
 *
 * Simpler than the Browse/Recently-Added scripts: genres/themes are a small bounded taxonomy, so there is no
 * pagination / infinite scroll and no collapsible filter panel. The tab switch is a full-page nav (the
 * .pp-switch links), so the slide plays once on load when the tab actually changed (tracked in sessionStorage).
 */
document.addEventListener('DOMContentLoaded', function () {
    var PP = window.PlatPursuit || {};

    // ── Header + .scard count-ups. ──
    if (PP.countUp) {
        var countEl = document.querySelector('[data-gtl-count]');
        if (countEl) { PP.countUp(countEl, 900); }
        document.querySelectorAll('[data-scard-count]').forEach(function (el) { PP.countUp(el, 900); });
    }

    // ── Directional tab slide: only when the tab genuinely changed (not a refresh / first visit). ──
    var ORDER = ['genres', 'themes'];
    var activeChip = document.querySelector('.pp-switch__chip.is-active[data-tab]');
    var currentTab = activeChip ? activeChip.getAttribute('data-tab') : null;
    if (currentTab) {
        try {
            var prevTab = sessionStorage.getItem('gtl:tab');
            if (prevTab && prevTab !== currentTab && PP.slideViewIn) {
                PP.slideViewIn(document.getElementById('items-grid'), prevTab, currentTab, ORDER);
            }
            sessionStorage.setItem('gtl:tab', currentTab);
        } catch (e) { /* sessionStorage unavailable -- skip the slide, no-op */ }
    }
    if (activeChip && PP.igniteTab) {
        setTimeout(function () { if (activeChip.classList.contains('is-active')) { PP.igniteTab(activeChip); } }, 240);
    }

    var form = document.getElementById('gtl-form');

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

    // Settle the results the instant search/sort changes, cleared when the fresh grid swaps in.
    if (form) {
        form.addEventListener('change', function () {
            var r = document.getElementById('browse-results');
            if (r) { r.classList.add('is-swapping'); }
        });
    }

    var countLast = null;
    document.body.addEventListener('htmx:afterSwap', function (e) {
        var t = (e.detail && e.detail.target) || e.target;
        if (!t || t.id !== 'browse-results') { return; }
        var grid = t.querySelector('#items-grid');
        if (grid && grid === handledGrid) { return; }
        handledGrid = grid;
        t.classList.remove('is-swapping');
        // Header result-count tick (old -> new), read off the freshly-swapped grid's data-result-count.
        var headEl = document.getElementById('gtl-count');
        if (grid && headEl && PP.countUp) {
            var newVal = parseFloat(grid.getAttribute('data-result-count'));
            if (!isNaN(newVal)) {
                if (countLast === null) { countLast = parseFloat(headEl.dataset.countup); }
                headEl.dataset.countup = newVal;
                if (!isNaN(countLast) && countLast !== newVal) {
                    PP.countUp(headEl, 700, { from: countLast });
                } else {
                    headEl.textContent = newVal.toLocaleString();
                }
                countLast = newVal;
                var mbCount = document.querySelector('[data-minibar-count]');
                if (mbCount) { mbCount.textContent = newVal.toLocaleString(); }
            }
        }
        initReveal();
    });
    // Safety net: clear the settle dim when the search/sort request completes (incl. non-2xx / aborted).
    document.body.addEventListener('htmx:afterRequest', function (e) {
        var elt = e.detail && e.detail.elt;
        if (!elt || elt.id !== 'gtl-form') { return; }
        var r = document.getElementById('browse-results');
        if (r) { r.classList.remove('is-swapping', 'pointer-events-none'); }
    });

    initReveal();

    // ── Sticky mini-bar: identity + count + a quick sort proxy. ──
    if (PP.StickyReveal) { PP.StickyReveal.init(); }
    var realSort = form ? form.querySelector('select[name="sort"]') : null;
    var mbSort = document.querySelector('[data-minibar-sort]');
    if (mbSort && realSort) {
        mbSort.innerHTML = realSort.innerHTML;
        mbSort.value = realSort.value;
        mbSort.addEventListener('change', function () {
            realSort.value = mbSort.value;
            realSort.dispatchEvent(new Event('change', { bubbles: true }));
        });
        realSort.addEventListener('change', function () { mbSort.value = realSort.value; });
    }
});
