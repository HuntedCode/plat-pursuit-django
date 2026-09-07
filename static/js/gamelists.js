/**
 * Game Lists page behaviour.
 *
 * Deliberately thin. The character counters come from the shared `PlatPursuit.wireCharCounters`, and
 * swipe-to-dismiss from `PlatPursuit.dismissableSheet` -- the same helper the guidelines sheet uses,
 * which is what gives the modal an iOS-style drag handle on touch instead of a phone-sized dialog
 * with no way to flick it away.
 *
 * The dialog is a NATIVE <dialog>, matching the house pattern for FORM sheets. That is not laziness:
 * it brings a real focus trap, Escape, and an inert background for free, all of which
 * `.pp-detail-modal` (the content-VIEW primitive) hand-rolls. What the first cut got wrong was the
 * skin, not the element -- it wore DaisyUI's `modal-box` and read as a different product.
 */
(function () {
    'use strict';

    function wireCreateDialog() {
        var dialog = document.getElementById('gl-create');
        if (!dialog || dialog.dataset.wired === '1') { return; }
        dialog.dataset.wired = '1';

        // Choreographed exit, the way `.gd-modal` does it: add `.is-closing`, wait for the
        // animation, then close. The standard asks for exits as carefully handled as entrances, and
        // the previous version's comment claimed a single close path existed "so a future exit
        // animation only has to be added in one place" -- this is that place, now used.
        var close = function () {
            if (!dialog.close || !dialog.open) { return; }
            var reduced = window.matchMedia
                && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (reduced) { dialog.close(); return; }
            if (dialog.classList.contains('is-closing')) { return; }
            dialog.classList.add('is-closing');
            var done = function () {
                dialog.classList.remove('is-closing');
                dialog.close();
            };
            dialog.addEventListener('animationend', done, { once: true });
            // A dropped `animationend` would strand the dialog open and un-closable.
            setTimeout(function () {
                if (dialog.classList.contains('is-closing')) { done(); }
            }, 400);
        };

        document.querySelectorAll('[data-gl-open]').forEach(function (opener) {
            opener.addEventListener('click', function () {
                if (dialog.showModal && !dialog.open) { dialog.showModal(); }
            });
        });

        dialog.querySelectorAll('[data-gl-close]').forEach(function (button) {
            button.addEventListener('click', close);
        });

        // Backdrop click. The <dialog> itself is the full-viewport element and the box is inside it,
        // so a click landing on the dialog and not the box is a click on the backdrop.
        dialog.addEventListener('click', function (e) { if (e.target === dialog) { close(); } });

        // Escape goes through the same path as every other close, so a future exit animation only
        // has to be added in one place.
        dialog.addEventListener('cancel', function (e) { e.preventDefault(); close(); });

        // Suggested names fill the field rather than submitting: they are a shortcut past a blank
        // input, not eight ways to create a list. Focus returns to the input afterwards so the
        // hunter can keep typing, and `input` is dispatched so the character counter follows.
        var nameField = dialog.querySelector('#gl-name');
        dialog.querySelectorAll('[data-gl-suggest]').forEach(function (chip) {
            chip.addEventListener('click', function () {
                if (!nameField) { return; }
                nameField.value = chip.textContent.trim();
                nameField.dispatchEvent(new Event('input', { bubbles: true }));
                nameField.focus();
                nameField.setSelectionRange(nameField.value.length, nameField.value.length);
            });
        });

        if (window.PlatPursuit && window.PlatPursuit.dismissableSheet) {
            // `handle` is REQUIRED here, and the helper's own docs say why: omit it on a sheet you
            // READ, pass one on a sheet you OPERATE, "where an accidental dismiss costs unsaved
            // work". This is a form holding a typed name and up to 300 characters of description.
            // Without a handle, a downward swipe starting on the header, a label, the chip row or
            // the footer armed the drag and destroyed the lot past 90px with no confirmation.
            window.PlatPursuit.dismissableSheet(dialog, {
                onClose: close,
                handle: '.gl-dialog__head',
            });
        }
    }


    /**
     * The Mine|Following scope switcher.
     *
     * HTMX does the fetch and the swap; this supplies everything HTMX does not: the roving tabindex
     * and Arrow/Home/End model (`wireTablist`), the one-shot activation bloom (`igniteTab`), and the
     * directional panel slide (`slideViewIn`) -- the three beats the design system and motion-patterns
     * ask a tab group for. `{manual: true}` because the chips are `<a hx-get>`: wireTablist must not
     * also bind click, or the panel switches twice.
     */
    function wireScopeSwitcher() {
        var strip = document.querySelector('[data-gl-scopes]');
        if (!strip || strip.dataset.wired === '1') { return; }
        strip.dataset.wired = '1';

        var chips = strip.querySelectorAll('.pp-switch__chip');
        var order = Array.prototype.map.call(chips, function (c) { return c.dataset.scope; });

        if (window.PlatPursuit && window.PlatPursuit.wireTablist) {
            window.PlatPursuit.wireTablist(chips, { manual: true });
        }

        // Active state moves on the REQUEST, not on the response: the chip should light the instant
        // it is pressed rather than after a round trip, which is what makes the switch feel local.
        document.body.addEventListener('htmx:beforeRequest', function (e) {
            var chip = e.target.closest ? e.target.closest('[data-gl-scopes] .pp-switch__chip') : null;
            if (!chip) { return; }
            Array.prototype.forEach.call(chips, function (c) {
                var on = c === chip;
                c.classList.toggle('is-active', on);
                c.setAttribute('aria-selected', on ? 'true' : 'false');
                c.tabIndex = on ? 0 : -1;
            });
            if (window.PlatPursuit && window.PlatPursuit.igniteTab) {
                window.PlatPursuit.igniteTab(chip);
            }
        });

        document.body.addEventListener('htmx:afterSwap', function (e) {
            var grid = document.getElementById('my-lists-grid');
            if (!grid) { return; }
            // Direction comes from the panel's own `data-scope`, which the server rendered -- so the
            // slide follows what actually arrived rather than what was clicked.
            var from = strip.dataset.lastScope || null;
            var to = grid.dataset.scope;
            if (window.PlatPursuit && window.PlatPursuit.slideViewIn) {
                window.PlatPursuit.slideViewIn(grid, from, to, order);
            }
            strip.dataset.lastScope = to;
        });

        var current = document.getElementById('my-lists-grid');
        if (current) { strip.dataset.lastScope = current.dataset.scope; }
    }

    function boot() {
        wireCreateDialog();
        wireScopeSwitcher();
    }

    document.addEventListener('DOMContentLoaded', boot);
    // `base.html` sets no `hx-history-elt`, so htmx replaces document.body WHOLESALE on a history
    // restore -- every node here is fresh and unwired after a browser Back, and listeners die with
    // the node they were on. Re-running is safe: both wirings guard on `dataset.wired`, which is
    // itself a fresh (absent) attribute on the restored nodes. Documented the same way in
    // plat-cards.js, which paid for this lesson first.
    document.body.addEventListener('htmx:historyRestore', boot);
})();
