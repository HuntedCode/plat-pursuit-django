/**
 * Settings page (/users/settings/) -- rebuilt 2026-08.
 *
 * Four jobs, all element wiring (re-run on history restore via onPageReady):
 *   1. Section arrival (shared .pp-arrive observer; the template arms html.pp-arm pre-paint).
 *   2. The unlink + delete confirm dialogs (native <dialog>, scrim click closes).
 *   3. The timezone Detect button (Intl guess -> select + inline hint; a Save still commits it).
 *   4. The delete dialog's typed-phrase gate (client-side friction; the password field is the
 *      server-side gate, so a JS-less submit is still safe).
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

        // ---- confirm dialogs (unlink + delete share the wiring shape) ------
        [
            { id: 'stg-unlink', open: '[data-unlink-open]', close: '[data-unlink-close]' },
            { id: 'stg-delete', open: '[data-delete-open]', close: '[data-delete-close]' },
        ].forEach(function (cfg) {
            var dialog = document.getElementById(cfg.id);
            if (!dialog) return;
            document.querySelectorAll(cfg.open).forEach(function (btn) {
                btn.addEventListener('click', function () { dialog.showModal(); });
            });
            document.querySelectorAll(cfg.close).forEach(function (btn) {
                btn.addEventListener('click', function () { dialog.close(); });
            });
            dialog.addEventListener('click', function (e) {
                if (e.target === dialog) dialog.close();
            });
        });

        // ---- delete: typed-phrase gate -------------------------------------
        var phrase = document.getElementById('stg-delete-phrase');
        var go = document.querySelector('[data-delete-go]');
        if (phrase && go) {
            phrase.addEventListener('input', function () {
                // The label shows the phrase in quotes, so typing the quotes counts too.
                var typed = phrase.value.trim().toLowerCase().replace(/^["']+|["']+$/g, '').trim();
                go.disabled = typed !== 'delete my account';
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
