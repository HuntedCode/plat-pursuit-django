/**
 * Settings page (/users/settings/) -- rebuilt 2026-08.
 *
 * Three jobs, all element wiring (re-run on history restore via onPageReady):
 *   1. Section arrival (shared .pp-arrive observer; the template arms html.pp-arm pre-paint).
 *   2. The unlink confirm dialog (native <dialog>, scrim click closes).
 *   3. The timezone Detect button (Intl guess -> select + inline hint; a Save still commits it).
 */
(function () {
    'use strict';

    if (!window.PlatPursuit || !PlatPursuit.onPageReady) {
        document.documentElement.classList.remove('pp-arm');
        return;
    }
    if (!PlatPursuit.arriveOnScroll) {
        document.documentElement.classList.remove('pp-arm');
    }

    PlatPursuit.onPageReady(function (first) {
        if (first && PlatPursuit.arriveOnScroll) PlatPursuit.arriveOnScroll();

        // ---- unlink dialog -------------------------------------------------
        var dialog = document.getElementById('stg-unlink');
        if (dialog) {
            document.querySelectorAll('[data-unlink-open]').forEach(function (btn) {
                btn.addEventListener('click', function () { dialog.showModal(); });
            });
            document.querySelectorAll('[data-unlink-close]').forEach(function (btn) {
                btn.addEventListener('click', function () { dialog.close(); });
            });
            dialog.addEventListener('click', function (e) {
                if (e.target === dialog) dialog.close();
            });
        }

        // ---- timezone detect -----------------------------------------------
        var detect = document.querySelector('[data-tz-detect]');
        var select = document.getElementById('stg-tz');
        var hint = document.querySelector('[data-tz-hint]');
        if (detect && select) {
            detect.addEventListener('click', function () {
                var say = function (msg) {
                    if (hint) { hint.textContent = msg; hint.hidden = false; }
                };
                var zone = null;
                try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { /* fall through */ }
                if (!zone) { say('Could not detect a timezone from this browser.'); return; }
                var match = Array.prototype.find.call(select.options, function (o) { return o.value === zone; });
                if (!match) { say('Detected ' + zone + ', but it is not in the list.'); return; }
                select.value = zone;
                say('Detected ' + zone + '. Save to keep it.');
            });
        }
    });
})();
