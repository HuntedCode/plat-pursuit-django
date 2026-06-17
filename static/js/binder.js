/* Binder Surface controller (baseline).
 * Single view-mode page navigation (one .pp-binder__page at a time), the
 * Binder/Gallery presentation toggle, and the mobile force-snap to Gallery
 * below 1024px. Spread / 3D page-flip is deferred and not handled here.
 * Namespaced under window.PlatPursuit.Binder. */
(function () {
    'use strict';

    function initBinder(root) {
        var pages = Array.prototype.slice.call(root.querySelectorAll('.pp-binder__page'));
        var prevBtn = root.querySelector('[data-binder-prev]');
        var nextBtn = root.querySelector('[data-binder-next]');
        var counter = root.querySelector('[data-binder-counter]');

        var idx = 0;
        for (var i = 0; i < pages.length; i++) {
            if (pages[i].classList.contains('is-current')) { idx = i; break; }
        }

        function render() {
            pages.forEach(function (p, i) { p.classList.toggle('is-current', i === idx); });
            if (counter) counter.textContent = 'Page ' + (idx + 1) + ' / ' + pages.length;
            if (prevBtn) prevBtn.disabled = idx <= 0;
            if (nextBtn) nextBtn.disabled = idx >= pages.length - 1;
        }
        function go(delta) {
            var n = Math.min(Math.max(idx + delta, 0), pages.length - 1);
            if (n !== idx) { idx = n; render(); }
        }
        if (prevBtn) prevBtn.addEventListener('click', function () { go(-1); });
        if (nextBtn) nextBtn.addEventListener('click', function () { go(1); });

        function setPresentation(pres) {
            root.setAttribute('data-binder-presentation', pres);
            root.querySelectorAll('button[data-binder-presentation]').forEach(function (b) {
                b.classList.toggle('pp-binder__control-chip--active',
                    b.getAttribute('data-binder-presentation') === pres);
            });
        }
        root.querySelectorAll('button[data-binder-presentation]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                setPresentation(btn.getAttribute('data-binder-presentation'));
            });
        });

        // Mobile: below 1024px the binder chrome fights the viewport -> force Gallery.
        function snap() {
            if (window.innerWidth < 1024 && root.getAttribute('data-binder-presentation') === 'binder') {
                setPresentation('gallery');
            }
        }
        window.addEventListener('resize', snap);

        // A deep-link to a specific card (#card-NNNN) should land on that card's page.
        function jumpToHash() {
            if (!window.location.hash) return;
            var target = root.querySelector(window.location.hash);
            if (!target) return;
            var page = target.closest('.pp-binder__page');
            if (!page) return;
            var n = pages.indexOf(page);
            if (n >= 0) { idx = n; render(); }
        }

        snap();
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
