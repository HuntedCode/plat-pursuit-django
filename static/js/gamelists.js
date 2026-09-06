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

        var close = function () { if (dialog.close && dialog.open) { dialog.close(); } };

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
            window.PlatPursuit.dismissableSheet(dialog, { onClose: close });
        }
    }

    document.addEventListener('DOMContentLoaded', wireCreateDialog);
})();
