/* Pursuer Card -- Rarest/Recent showcase toggle.
 * Event-delegated on document so it keeps working when the card element is swapped in live (the
 * forge's post-sync refresh replaces it). JS only flips semantic state (.is-active on the tab, the
 * `hidden` attr on the shelves); all styling stays in CSS. No-op when no card is on the page. */
(function () {
    document.addEventListener('click', function (e) {
        var tab = e.target.closest && e.target.closest('.pursuer-card__tab');
        if (!tab) return;
        var root = tab.closest('[data-pursuer-card-switch]');
        if (!root) return;
        var target = tab.getAttribute('data-shelf');
        root.querySelectorAll('.pursuer-card__tab').forEach(function (t) {
            t.classList.toggle('is-active', t === tab);
        });
        root.querySelectorAll('.pursuer-card__shelf').forEach(function (s) {
            s.hidden = s.getAttribute('data-shelf') !== target;
        });
    });
})();
