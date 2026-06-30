/* Pursuer Card -- Rarest/Recent showcase toggle.
 * JS only flips semantic state (.is-active on the tab, the `hidden` attr on the shelves);
 * all styling stays in CSS. Self-initializing + no-op when no card is on the page. */
(function () {
    function initSwitch(root) {
        var tabs = root.querySelectorAll('.pursuer-card__tab');
        var shelves = root.querySelectorAll('.pursuer-card__shelf');
        tabs.forEach(function (tab) {
            tab.addEventListener('click', function () {
                var target = tab.getAttribute('data-shelf');
                tabs.forEach(function (t) { t.classList.toggle('is-active', t === tab); });
                shelves.forEach(function (s) { s.hidden = s.getAttribute('data-shelf') !== target; });
            });
        });
    }
    function init() {
        document.querySelectorAll('[data-pursuer-card-switch]').forEach(initSwitch);
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
