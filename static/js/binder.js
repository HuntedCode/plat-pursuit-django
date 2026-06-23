/* Binder Surface controller.
 * Each SET (badge type) is its own binder view, picked from the set tabs. Within a set,
 * two view modes: Single (one .pp-binder__page at a time) and Spread/Flipbook (one
 * .pp-binder__spread of two facing pages at a time, desktop only). The grid steps 4 -> 2
 * -> 1 column responsively in single mode (CSS).
 * STAGE 1: spread navigation is an instant swap (is-current). The 3D page-flip animation
 * layers on in stage 2. Namespaced under window.PlatPursuit.Binder. */
(function () {
    'use strict';

    var DESKTOP_MIN = 1024;

    function groupBy(els, attr) {
        var map = {}, order = [];
        els.forEach(function (el) {
            var k = el.getAttribute(attr) || '__none__';
            if (!map[k]) { map[k] = []; order.push(k); }
            map[k].push(el);
        });
        return { map: map, order: order };
    }

    function initBinder(root) {
        var pages = Array.prototype.slice.call(root.querySelectorAll('.pp-binder__page[data-set]'));
        var spreads = Array.prototype.slice.call(root.querySelectorAll('.pp-binder__spread'));
        var setTabs = Array.prototype.slice.call(root.querySelectorAll('[data-set-tab]'));
        var viewChips = Array.prototype.slice.call(root.querySelectorAll('[data-binder-view]'));
        var prevBtn = root.querySelector('[data-binder-prev]');
        var nextBtn = root.querySelector('[data-binder-next]');
        var counter = root.querySelector('[data-binder-counter]');

        var pg = groupBy(pages, 'data-set');
        var sp = groupBy(spreads, 'data-set');
        var pagesBySet = pg.map, spreadsBySet = sp.map;
        var setOrder = pg.order.length ? pg.order : sp.order;
        if (!setOrder.length) return;  // empty album

        var mode = 'single';                 // 'single' | 'spread' (the user's chosen view)
        var activeSet = setOrder[0];
        var pageIdx = {}, spreadIdx = {};    // remembered position per set, per mode
        setOrder.forEach(function (k) { pageIdx[k] = 0; spreadIdx[k] = 0; });

        function isDesktop() { return window.innerWidth >= DESKTOP_MIN; }
        function spreadActive() { return mode === 'spread' && isDesktop(); }

        // The Frame is a fixed width; a spread fits two pages only with the compact
        // variant (hidden plinth meta + smaller text). Toggle it only when the mode
        // actually flips, not on every set-switch render.
        var lastSpreadMode = null;
        function applyFrameVariant(spreadMode) {
            if (spreadMode === lastSpreadMode) return;
            lastSpreadMode = spreadMode;
            root.querySelectorAll('.pp-frame').forEach(function (f) {
                f.classList.toggle('pp-frame--compact', spreadMode);
                // Reset any flipped card to the front when shrinking to compact (the
                // back face isn't designed for the small size; flip is disabled there).
                if (spreadMode) f.classList.remove('is-flipped');
            });
        }

        function render() {
            var spreadMode = spreadActive();
            root.setAttribute('data-mode', spreadMode ? 'spread' : 'single');
            applyFrameVariant(spreadMode);

            pages.forEach(function (p) { p.classList.remove('is-current'); });
            spreads.forEach(function (s) { s.classList.remove('is-current'); });

            var list = (spreadMode ? spreadsBySet : pagesBySet)[activeSet] || [];
            var store = spreadMode ? spreadIdx : pageIdx;
            var idx = Math.min(Math.max(store[activeSet] || 0, 0), Math.max(list.length - 1, 0));
            store[activeSet] = idx;
            if (list[idx]) list[idx].classList.add('is-current');

            if (counter) {
                counter.textContent = (spreadMode ? 'Spread ' : 'Page ') +
                    (idx + 1) + ' / ' + Math.max(list.length, 1);
            }
            if (prevBtn) prevBtn.disabled = idx <= 0;
            if (nextBtn) nextBtn.disabled = idx >= list.length - 1;

            viewChips.forEach(function (c) {
                c.classList.toggle('pp-binder__view-chip--active',
                    c.getAttribute('data-binder-view') === (spreadMode ? 'spread' : 'single'));
            });
            setTabs.forEach(function (t) {
                var on = t.getAttribute('data-set-tab') === activeSet;
                t.classList.toggle('is-active', on);
                t.setAttribute('aria-selected', on ? 'true' : 'false');
            });
        }

        function go(delta) {
            var spreadMode = spreadActive();
            var store = spreadMode ? spreadIdx : pageIdx;
            var list = (spreadMode ? spreadsBySet : pagesBySet)[activeSet] || [];
            var n = Math.min(Math.max((store[activeSet] || 0) + delta, 0), list.length - 1);
            if (n !== store[activeSet]) { store[activeSet] = n; render(); }
        }
        if (prevBtn) prevBtn.addEventListener('click', function () { go(-1); });
        if (nextBtn) nextBtn.addEventListener('click', function () { go(1); });

        viewChips.forEach(function (chip) {
            chip.addEventListener('click', function () {
                var v = chip.getAttribute('data-binder-view');
                if (v === 'spread' && !isDesktop()) return;  // flipbook is desktop-only
                mode = v;
                render();
            });
        });

        setTabs.forEach(function (tab) {
            tab.addEventListener('click', function () {
                var key = tab.getAttribute('data-set-tab');
                if (key && key !== activeSet) { activeSet = key; render(); }
            });
        });

        // Sorting re-paginates server-side: reload with the chosen ?sort=.
        var sortSelect = root.querySelector('[data-binder-sort]');
        if (sortSelect) {
            sortSelect.addEventListener('change', function () {
                var params = new URLSearchParams(window.location.search);
                params.set('sort', sortSelect.value);
                window.location.search = params.toString();
            });
        }

        // Keep the displayed mode honest as the viewport crosses the desktop threshold
        // (a chosen flipbook downgrades to single below 1024, and restores on the way up).
        var raf;
        window.addEventListener('resize', function () {
            if (raf) return;
            raf = window.requestAnimationFrame(function () { raf = null; render(); });
        });

        // Deep-link to a card: land on its set + the page (single) or spread (spread mode).
        function jumpToHash() {
            if (!window.location.hash) return;
            var target = root.querySelector(window.location.hash);
            if (!target) return;
            var page = target.closest('.pp-binder__page');
            if (!page) return;
            var key = page.getAttribute('data-set');
            if (!key) return;
            activeSet = key;
            var pI = (pagesBySet[key] || []).indexOf(page);
            if (pI >= 0) pageIdx[key] = pI;
            var sI = (spreadsBySet[key] || []).indexOf(target.closest('.pp-binder__spread'));
            if (sI >= 0) spreadIdx[key] = sI;
            render();
        }

        render();
        jumpToHash();
        window.addEventListener('hashchange', jumpToHash);
    }

    function init() {
        document.querySelectorAll('.pp-binder').forEach(initBinder);
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.Binder = { init: init };
})();
