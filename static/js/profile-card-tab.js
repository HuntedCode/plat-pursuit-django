/**
 * The profile page's Card tab: fit the 1200x630 Profile Card into its frame, paint the chosen
 * ground onto the live preview, and wire the download button.
 *
 * The preview is the REAL card markup rendered server-side at fixed size, scaled with a
 * transform (the share modal's rule -- the preview is the artifact, not a re-implementation).
 * The scale clamps at 1 going up and has NO floor going down: the frame carries an inline
 * height and overflow hidden, so a floored scale would paint the card over surrounding chrome
 * (the same bug the share modal's fit() documents).
 *
 * Grounds follow the recap ceremony's pattern: the gradient is read back off the SWATCH the
 * server rendered (`--pc-theme-bg`), not from a JS theme registry -- the thing you clicked and
 * the thing that paints are the same value, and the palette cannot drift from the siblings'.
 * The scrims are INNER layers on the card, so painting the root's background is exactly what
 * the renderer will do to the PNG.
 *
 * Boot is idempotent per element (data marker) and re-runs on the HTMX tab swap, because the
 * panel is replaced wholesale on every tab change.
 */
(function () {
    'use strict';

    var currentFit = null;

    function boot(root) {
        var host = root.querySelector('[data-pfc]');
        if (!host || host.dataset.pfcBooted) { return; }
        host.dataset.pfcBooted = '1';

        var frame = host.querySelector('[data-pfc-frame]');
        var scaler = host.querySelector('[data-pfc-scaler]');
        if (frame && scaler) {
            var fit = function () {
                if (!document.body.contains(frame)) { currentFit = null; return; }
                var scale = Math.min(1, frame.clientWidth / 1200);
                scaler.style.transform = 'scale(' + scale + ')';
                frame.style.height = Math.round(630 * scale) + 'px';
            };
            fit();
            // One document-level listener, re-pointed at the live frame on each boot -- a
            // per-boot listener would pile up a closure per tab visit, each holding its dead DOM.
            currentFit = fit;
        }

        var themeValue = function () {
            var picked = host.querySelector('[data-pfc-theme]:checked');
            return picked ? picked.value : '';
        };

        var paintGround = function () {
            var card = scaler && scaler.querySelector('.share-image-content');
            var picked = host.querySelector('[data-pfc-theme]:checked');
            var label = picked && picked.closest('.pc-theme');
            var ground = label && getComputedStyle(label).getPropertyValue('--pc-theme-bg').trim();
            if (card && ground) { card.style.background = ground; }
        };

        host.querySelectorAll('[data-pfc-theme]').forEach(function (input) {
            input.addEventListener('change', paintGround);
        });
        // And once on boot: a soft reload restores radio state, and a checked Ember over a
        // freshly server-rendered Substrate card would download a ground the preview never showed.
        paintGround();

        var btn = host.querySelector('[data-pfc-download]');
        if (btn && window.PlatPursuit && window.PlatPursuit.CardDownload) {
            window.PlatPursuit.CardDownload.attach(btn, {
                // Resolved at click time, theme included -- a URL captured at bind time would
                // download the ground the hunter was looking at a swatch ago.
                url: function () {
                    var theme = themeValue();
                    return btn.dataset.url + (theme ? '?theme=' + encodeURIComponent(theme) : '');
                },
                filename: function () { return btn.dataset.filename; },
                toast: 'Profile card saved',
            });
        }
    }

    window.addEventListener('resize', function () {
        if (currentFit) { currentFit(); }
    });

    document.addEventListener('DOMContentLoaded', function () { boot(document); });
    document.addEventListener('htmx:afterSwap', function (e) {
        if (e.detail && e.detail.target && e.detail.target.id === 'tab-content') {
            boot(e.detail.target);
        }
    });
})();
