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

    document.addEventListener('DOMContentLoaded', wireCreateDialog);
})();
