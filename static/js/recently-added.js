/*
 * Recently Added -- page motion + chrome. Filtering itself is HTMX-native via browse-filters.js; this owns
 * the header count-up, the animated collapsible Filters panel, the staggered card reveal, infinite scroll,
 * the filter-swap settle, the sticky mini-bar (+ sort proxy), and the directional category slide.
 *
 * Modelled on the Browse Games inline script, trimmed to this page's smaller control set (no text search, no
 * discipline dropdowns, no long scroll-fade chip lists). The category switch is a full-page nav (the .pp-switch
 * links), so the slide is played once on load when the category actually changed (tracked in sessionStorage).
 */
document.addEventListener('DOMContentLoaded', function () {
    var PP = window.PlatPursuit || {};

    // ── Header + .scard count-ups (same opening beat as the Tally). ──
    if (PP.countUp) {
        var countEl = document.querySelector('[data-radded-count]');
        if (countEl) { PP.countUp(countEl, 900); }
        document.querySelectorAll('[data-scard-count]').forEach(function (el) { PP.countUp(el, 900); });
    }

    // ── Directional category slide: only when the category genuinely changed (not a refresh / first visit). ──
    var ORDER = ['base_games', 'dlc'];
    var activeChip = document.querySelector('.pp-switch__chip.is-active[data-category]');
    var currentCat = activeChip ? activeChip.getAttribute('data-category') : null;
    if (currentCat) {
        try {
            var prevCat = sessionStorage.getItem('radded:cat');
            if (prevCat && prevCat !== currentCat && PP.slideViewIn) {
                PP.slideViewIn(document.getElementById('items-grid'), prevCat, currentCat, ORDER);
            }
            sessionStorage.setItem('radded:cat', currentCat);
        } catch (e) { /* sessionStorage unavailable -- skip the slide, no-op */ }
    }
    // One-shot ignite on the active chip so it "comes alive".
    if (activeChip && PP.igniteTab) {
        setTimeout(function () { if (activeChip.classList.contains('is-active')) { PP.igniteTab(activeChip); } }, 240);
    }

    var form = document.getElementById('radded-form');
    var toggle = document.getElementById('radded-filters-toggle');
    var panel = document.getElementById('radded-advanced');
    var badge = document.getElementById('radded-filter-count');
    if (!form || !toggle || !panel) { return; }

    // ── Animated expand/collapse of the filter panel (height + opacity tween; `hidden` toggled at the
    //    animation edges so collapsed controls stay out of the tab order). Reduced-motion falls back to the
    //    instant hidden toggle. ──
    var panelAnimEnd = null;
    function clearPanelAnim() {
        if (panelAnimEnd) { panel.removeEventListener('transitionend', panelAnimEnd); panelAnimEnd = null; }
    }
    function setPanel(open, animate) {
        toggle.classList.toggle('is-open', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        clearPanelAnim();
        var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (!animate || reduce) {
            panel.style.height = ''; panel.style.opacity = '';
            if (open) { panel.removeAttribute('hidden'); }
            else { panel.setAttribute('hidden', ''); }
            return;
        }
        if (open) {
            panel.style.height = '0px'; panel.style.opacity = '0';
            panel.removeAttribute('hidden');
            var target = panel.scrollHeight;
            void panel.offsetHeight;
            panel.style.height = target + 'px'; panel.style.opacity = '1';
            panelAnimEnd = function (e) {
                if (e.target !== panel || e.propertyName !== 'height') { return; }
                clearPanelAnim();
                panel.style.height = ''; panel.style.opacity = '';
            };
        } else {
            panel.style.height = panel.scrollHeight + 'px'; panel.style.opacity = '1';
            void panel.offsetHeight;
            panel.style.height = '0px'; panel.style.opacity = '0';
            panelAnimEnd = function (e) {
                if (e.target !== panel || e.propertyName !== 'height') { return; }
                clearPanelAnim();
                panel.setAttribute('hidden', ''); panel.style.height = ''; panel.style.opacity = '';
            };
        }
        panel.addEventListener('transitionend', panelAnimEnd);
    }

    // ── Active content-filter count for the toggle badge. Platform has NO default here (unlike Browse), so it
    //    IS a real filter and gets counted; the category axis + sort/page are the only skips. ──
    var SKIP = { page: 1, sort: 1, category: 1 };
    function activeCount() {
        var n = 0;
        new FormData(form).forEach(function (value, key) {
            if (SKIP[key] || !value) { return; }
            n += 1;
        });
        return n;
    }
    var prevBadgeN = null;
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
    toggle.addEventListener('click', function () { setPanel(toggle.getAttribute('aria-expanded') !== 'true', true); });
    form.addEventListener('change', refreshBadge);

    // ── Infinite scroll + staggered card reveal (shared with Browse / Badges). `.pp-gcard` matches both the
    //    base cards and the DLC `.pp-gcard--dlc` siblings. ──
    var scroller = null, revealHandle = null, handledGrid = null;
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
            gridId: 'items-grid', sentinelId: 'radded-sentinel', loadingId: 'radded-loading',
            paginateBy: 30, cardSelector: '.pp-gcard',   // matches RecentlyAddedView.paginate_by
            onAppend: function (nodes) { if (revealHandle) { revealHandle.observe(nodes); } },
        });
    }

    // Settle the results the instant a filter/sort changes, cleared when the fresh grid swaps in.
    form.addEventListener('change', function () {
        var r = document.getElementById('browse-results');
        if (r) { r.classList.add('is-swapping'); }
    });

    var countLast = null;
    document.body.addEventListener('htmx:afterSwap', function (e) {
        var t = (e.detail && e.detail.target) || e.target;
        if (!t || t.id !== 'browse-results') { return; }
        var grid = t.querySelector('#items-grid');
        if (grid && grid === handledGrid) { return; }
        handledGrid = grid;
        t.classList.remove('is-swapping');
        refreshBadge();
        // Header result-count tick (old -> new), read off the freshly-swapped grid's data-result-count.
        var headEl = document.getElementById('radded-count');
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
        initReveal();   // before initScroller so revealHandle exists for its onAppend hook
        initScroller();
    });
    // Safety net: clear the settle dim when the filter request completes (incl. non-2xx / aborted).
    document.body.addEventListener('htmx:afterRequest', function (e) {
        var elt = e.detail && e.detail.elt;
        if (!elt || elt.id !== 'radded-form') { return; }
        var r = document.getElementById('browse-results');
        if (r) { r.classList.remove('is-swapping', 'pointer-events-none'); }
    });

    refreshBadge();
    setPanel(activeCount() > 0, false);   // collapsed on load; open if a filter is already applied
    initReveal();
    initScroller();

    // ── Sticky mini-bar: identity + count + a Filters reach + a quick sort proxy. ──
    if (PP.StickyReveal) { PP.StickyReveal.init(); }
    var mbFilters = document.querySelector('[data-minibar-filters]');
    if (mbFilters) {
        mbFilters.addEventListener('click', function () {
            if (toggle.getAttribute('aria-expanded') !== 'true') { setPanel(true, true); }
            form.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    }
    // Sort proxy: mirror the real (in-form) sort select so there's a single source of truth + no duplicate
    // name="sort" field. The real select's options are already category-scoped server-side.
    var realSort = form.querySelector('select[name="sort"]');
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
