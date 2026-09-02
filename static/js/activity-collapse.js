/* Collapsing a game inside a day.
 *
 * The TWEEN is `PlatPursuit.animatePanel`, the shared collapsible-panel helper the career, collection and
 * badge filter panels all use. A first cut reimplemented it and got two things wrong that it gets right:
 * `transitionend` BUBBLES, so the handler needs an `ev.target !== panel` guard and not just a property
 * check; and a second click mid-tween must clear the previous listener rather than stack another. It also
 * toggles the panel's `hidden` attribute, which is what actually takes a collapsed panel out of the
 * accessibility tree -- `height: 0; overflow: hidden` hides it from the eye only, so a screen reader would
 * still read every trophy while the control claimed `aria-expanded="false"`.
 *
 * Everything is presentational: the trophies are already in the HTML on both surfaces (the day modal and
 * the standalone day page), so nothing is fetched and the page is complete if this never runs.
 *
 * Delegated from the DOCUMENT rather than bound per button, because one surface is injected -- the profile
 * page fetches the day modal into a mount, and per-element binding would miss every session arriving that
 * way. Binding once also survives the modal being opened, closed and re-fetched.
 */
(function () {
    'use strict';

    if (window.__ppActivityCollapse) { return; }
    window.__ppActivityCollapse = true;

    document.addEventListener('click', function (e) {
        var btn = e.target.closest && e.target.closest('[data-act-collapse]');
        if (!btn) { return; }

        var panel = document.getElementById(btn.getAttribute('aria-controls'));
        if (!panel || !window.PlatPursuit || !PlatPursuit.animatePanel) { return; }

        var open = btn.getAttribute('aria-expanded') !== 'false';
        btn.setAttribute('aria-expanded', open ? 'false' : 'true');
        PlatPursuit.animatePanel(panel, !open);
    });
})();
