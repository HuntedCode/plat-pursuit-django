/*
 * Recently Added -- page motion + chrome. Filtering itself is HTMX-native via browse-filters.js.
 *
 * Two HTMX swap scopes (the shared switcher pattern -- see Badges / rebuild-playbook section 7):
 *   - The New Games/New DLC .pp-switch chips swap the whole #ra-view island (toolbar + grid + infinite-scroll
 *     sentinel) so the category-scoped sorts + Has Platinum filter re-render in sync; we slide the island in
 *     directionally + re-init its chrome (filter panel, badge, scroller, sort proxy).
 *   - A filter/sort change (or an InfiniteScroller page fetch) swaps only the inner #browse-results grid.
 *
 * Wired via PlatPursuit.onPageReady(boot): boot(first) runs the element wiring on first load AND on HTMX
 * Back/Forward history restore (fresh DOM); the body-level listeners are guarded by `first` so they bind once.
 */
(function () {
    var PP = window.PlatPursuit || {};
    var ORDER = ['base_games', 'dlc'];

    // Element state -- reassigned by boot()/initToolbar() on each fresh DOM (load + history restore + tab swap).
    var tablist = null, mbSort = null, scroller = null, revealHandle = null, handledGrid = null, countLast = null;
    var lastCat = 'base_games';
    var form = null, toggle = null, panel = null, badge = null, panelAnimEnd = null, prevBadgeN = null;
    var SKIP = { page: 1, sort: 1, category: 1 };

    function activeChip() { return document.querySelector('.pp-switch__chip.is-active[data-category]'); }
    function igniteActive() { var chip = activeChip(); if (chip && PP.igniteTab) { PP.igniteTab(chip); } }

    function updateToggleActive(cat) {
        document.querySelectorAll('.pp-switch__chip[data-category]').forEach(function (chip) {
            var on = chip.getAttribute('data-category') === cat;
            chip.classList.toggle('is-active', on);
            chip.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        if (tablist) { tablist.syncTabindex(); }   // re-point the roving tab-stop at the new active chip
    }
    function updateCatLabels(cat) {
        var sub = document.getElementById('radded-sublabel');
        if (sub) { sub.textContent = cat === 'dlc' ? 'packs shown' : 'games shown'; }
    }

    // ── Toolbar chrome (filter panel + active-count badge). Re-resolved by initToolbar on each fresh toolbar
    //    (load, history restore, and category swap -- the toolbar rides the #ra-view island). ──
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
    function onFormChangeDim() {
        var r = document.getElementById('browse-results');
        if (r) { r.classList.add('is-swapping'); }
    }
    function initToolbar() {
        form = document.getElementById('radded-form');
        toggle = document.getElementById('radded-filters-toggle');
        panel = document.getElementById('radded-advanced');
        badge = document.getElementById('radded-filter-count');
        prevBadgeN = null;
        if (!form || !toggle || !panel) { return; }
        toggle.addEventListener('click', function () { setPanel(toggle.getAttribute('aria-expanded') !== 'true', true); });
        form.addEventListener('change', onFormChange);
        form.addEventListener('change', onFormChangeDim);   // settle the results the instant a filter changes
        refreshBadge();
        setPanel(activeCount() > 0, false);   // collapsed on load; open if a filter is already applied
    }

    // ── Grid: infinite scroll + staggered card reveal. `.pp-gcard` matches base + DLC (`--dlc`) cards. ──
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

    function tickCount(grid) {
        var headEl = document.getElementById('radded-count');
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

    // ── Mini-bar quick-sort proxy + Filters reach. ──
    function currentSort() { return document.querySelector('#radded-form select[name="sort"]'); }
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
    function onMbFiltersClick() {
        var tg = document.getElementById('radded-filters-toggle');
        if (tg && tg.getAttribute('aria-expanded') !== 'true') { setPanel(true, true); }
        var f = document.getElementById('radded-form');
        if (f) { f.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    }

    // ── Body-level listeners (bound once via `first`; document.body persists across a history restore). ──
    function onBeforeRequest(e) {
        var el = e.detail && e.detail.elt;
        if (el && el.classList && el.classList.contains('pp-switch__chip') && el.classList.contains('is-active')) {
            e.preventDefault();   // clicking the already-active category shouldn't fire a redundant swap
        }
    }
    function onAfterSwap(e) {
        var t = (e.detail && e.detail.target) || e.target;
        if (!t) { return; }
        if (t.id === 'browse-results') {
            // Filter/sort swap: just the grid.
            var grid = t.querySelector('#items-grid');
            if (grid && grid === handledGrid) { return; }
            handledGrid = grid;
            t.classList.remove('is-swapping');
            refreshBadge();
            tickCount(grid);
            initReveal();   // before initScroller so revealHandle exists for its onAppend hook
            initScroller();
        } else if (t.id === 'ra-view') {
            // Category swap: whole island. Sync chrome + slide the new island in directionally.
            var g = t.querySelector('#items-grid');
            var newCat = g ? g.getAttribute('data-active-category') : lastCat;
            updateToggleActive(newCat);
            updateCatLabels(newCat);
            igniteActive();
            if (PP.slideViewIn) { PP.slideViewIn(t, lastCat, newCat, ORDER); }
            lastCat = newCat;
            handledGrid = g;
            initToolbar();          // re-wire the freshly-rendered toolbar (toggle / panel / badge)
            syncSortProxy();
            tickCount(g);
            initReveal();
            initScroller();
        }
    }
    function onAfterRequest(e) {
        var elt = e.detail && e.detail.elt;
        var ours = elt && (
            elt.id === 'radded-form'
            || (elt.classList && (elt.classList.contains('pp-switch__chip') || elt.classList.contains('pp-gbrowse__act')))
        );
        if (!ours) { return; }
        var r = document.getElementById('browse-results');
        if (r) { r.classList.remove('is-swapping', 'pointer-events-none'); }
    }

    // ── Element wiring: runs on first load AND on each history restore (fresh DOM). ──
    function boot(first) {
        var chip = activeChip();
        lastCat = chip ? chip.getAttribute('data-category') : 'base_games';
        handledGrid = null;

        if (PP.countUp) {
            var countEl = document.querySelector('[data-radded-count]');
            if (countEl) { PP.countUp(countEl, 900); }
            document.querySelectorAll('[data-scard-count]').forEach(function (el) { PP.countUp(el, 900); });
        }

        tablist = PP.wireTablist
            ? PP.wireTablist(document.querySelectorAll('.pp-switch__chip[data-category]'), { manual: true })
            : null;
        setTimeout(igniteActive, 240);

        initToolbar();

        mbSort = document.querySelector('[data-minibar-sort]');
        if (mbSort) { mbSort.addEventListener('change', onMbSortChange); }
        syncSortProxy();
        var mbFilters = document.querySelector('[data-minibar-filters]');
        if (mbFilters) { mbFilters.addEventListener('click', onMbFiltersClick); }

        initReveal();
        initScroller();
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
