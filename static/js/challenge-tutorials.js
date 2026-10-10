/**
 * The Challenges tutorials: the system intro (My Challenges, the hub) and a run type's tutorial (run pages).
 *
 * ONE CONTROLLER FOR EVERY `[data-ctut]` MODAL, and the behaviour is `PlatPursuit.DetailModal`'s, not a copy
 * of it: auto-open once, focus restore, Escape, and an `onDismiss` that records at most once. What this file
 * adds is the wiring the markup cannot express -- which setting to post, and the recall links.
 *
 * THE MARKUP DECIDES EVERYTHING, so a page cannot open a tutorial the server did not arm:
 *   data-auto     the server armed it. Auto-opens, and the first dismissal posts `data-setting` /
 *                 `data-value` to quick-settings (a version for the intro, a flag for a type).
 *   data-preview  a team preview door. Auto-opens and does NOTHING else: no `onDismiss`, so closing it
 *                 cannot record, and no `seenKey`, because a key left by a failed write makes
 *                 `DetailModal` skip the open and retry the write -- a preview that shows nothing and
 *                 writes something.
 *   neither       the recall link's modal. Opens only from `[data-ctut-open="<id>"]`, records nothing.
 *
 * `autoOpenDelay` IS PASSED ONLY FOR THE FIRST TWO. `DetailModal` reads `armed` from `data-auto` for the
 * RECORDING half, but the OPENING half is that option alone -- passing it unconditionally would reopen the
 * tutorial on every visit.
 */
(function () {
    'use strict';

    var PP = window.PlatPursuit = window.PlatPursuit || {};
    var AUTO_OPEN_DELAY = 450;
    var SETTINGS_URL = '/api/v1/user/quick-settings/';

    function wire(el) {
        var opts = { closeSelector: '[data-ctut-close]' };
        if (el.hasAttribute('data-auto')) {
            var setting = el.getAttribute('data-setting');
            var value = el.getAttribute('data-value');
            opts.autoOpenDelay = AUTO_OPEN_DELAY;
            // THE VALUE IS IN THE KEY, so the device fallback for the beta intro cannot also spend the
            // live one, and one type's tutorial cannot spend another's.
            opts.seenKey = 'pp-ctut-' + el.id + '-' + value;
            opts.onDismiss = function () {
                // A rejection, never a silent success: it is what makes `DetailModal` park the `seenKey`.
                if (!setting || !value || !PP.API) { return Promise.reject(); }
                return PP.API.post(SETTINGS_URL, { setting: setting, value: value });
            };
        } else if (el.hasAttribute('data-preview')) {
            opts.autoOpenDelay = AUTO_OPEN_DELAY;
        }
        var api = PP.DetailModal(el, opts);

        // THE RECALL LINK, matched by the modal's id so two tutorials on one page cannot open each other.
        // A recall closes silently: `armed` was spent by the first dismissal, or never set.
        var selector = '[data-ctut-open="' + el.id + '"]';
        document.addEventListener('click', function (e) {
            if (!e.target.closest) { return; }
            var trigger = e.target.closest(selector);
            if (trigger) { e.preventDefault(); api.open(trigger); }
        });
    }

    // FIRST LOAD ONLY: `onPageReady` re-fires on an htmx history restore, and wiring twice would stack a
    // second set of listeners on the same modal.
    if (!PP.onPageReady) { return; }
    PP.onPageReady(function (first) {
        if (!first || !PP.DetailModal) { return; }
        Array.prototype.forEach.call(document.querySelectorAll('[data-ctut]'), wire);
    });
})();
