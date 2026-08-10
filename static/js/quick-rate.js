/*
 * QuickRate -- the one controller for the quick-rate modal (`#gd-qr-modal`, quick_rate_modal.html).
 *
 * The modal is composed on two surfaces now: the Game Detail Ratings tab and the plat-card share modal.
 * Both need the identical form behaviour -- prefill, live slider readouts, the blurb counter, the hours
 * gate, agree-to-guidelines-on-submit, the POST, and the close affordances -- and differ only in what
 * happens AFTER a save. So all of that lives here, and each page passes callbacks.
 *
 * Before this, each page carried its own driver. They had already drifted: only Game Detail surfaced the
 * endpoint's field-level `errors` (so a blurb rejected for a banned word explained itself on one page and
 * showed a generic failure on the other), and only Game Detail preserved scroll position on open.
 *
 *   PlatPursuit.QuickRate.open({
 *       conceptId, groupId,            // required -- the POST target
 *       existing,                      // {difficulty, grindiness, fun_ranking, overall_rating,
 *                                      //  hours_to_platinum} or null for a fresh rating
 *       blurb,                         // existing quick take, if any
 *       title, submitLabel, cancelLabel, hoursLabel, playtimeHint,
 *       onSaved(data, payload),        // the save landed
 *       onCancel(),                    // the explicit secondary button (NOT a dismiss)
 *       onDismiss(),                   // X / backdrop / Esc / swipe
 *       onOpen(), onClose(),           // lifecycle, for page chrome (e.g. the recede)
 *   });
 *
 * The form INPUT NAMES are the API contract (see quick_rate_modal.html) -- do not rename them.
 */
(function () {
    var PP = window.PlatPursuit = window.PlatPursuit || {};

    var SLIDERS = ['difficulty', 'grindiness', 'fun_ranking', 'overall_rating'];
    var DEFAULTS = { difficulty: 5, grindiness: 5, fun_ranking: 5, overall_rating: 3, hours_to_platinum: '' };
    var BLURB_MAX = 140;

    function el(id) { return document.getElementById(id); }
    function field(form, name) { return form.querySelector('[name="' + name + '"]'); }

    function setReadout(form, name) {
        var out = form.querySelector('[data-gd-qr-val="' + name + '"]');
        var input = field(form, name);
        if (!out || !input) { return; }
        out.textContent = name === 'overall_rating' ? parseFloat(input.value).toFixed(1) : String(input.value);
    }

    function refreshCount(form) {
        var area = form.querySelector('[data-gd-qr-blurb]');
        var count = form.querySelector('[data-gd-qr-count]');
        if (!area || !count) { return; }
        var left = BLURB_MAX - area.value.length;
        count.textContent = String(left);
        count.classList.toggle('is-low', left <= 20);
    }

    // Prefill every field, defaulting the ones an existing rating doesn't carry. The BLURB is included:
    // the payload always sends it, so opening with an empty textarea and saving would clear an existing
    // quick take -- an edit that destroys what it edits.
    function prefill(form, existing, blurb) {
        Object.keys(DEFAULTS).forEach(function (name) {
            var input = field(form, name);
            if (!input) { return; }
            var v = existing && existing[name] !== null && existing[name] !== undefined
                ? existing[name] : DEFAULTS[name];
            input.value = v;
        });
        var area = form.querySelector('[data-gd-qr-blurb]');
        if (area) { area.value = blurb || ''; }
        SLIDERS.forEach(function (n) { setReadout(form, n); });
        refreshCount(form);
    }

    // The endpoint returns field validation (a banned-word blurb, say) under `errors` as {field: [msgs]};
    // everything else uses `error`. Surface the first field message so a rejected take explains itself.
    function messageFor(error) {
        return Promise.resolve()
            .then(function () { return error && error.response ? error.response.json() : null; })
            .then(function (data) {
                if (!data) { return 'Failed to save rating.'; }
                var first = data.errors && Object.keys(data.errors)[0];
                return data.error || (first && data.errors[first] && data.errors[first][0]) || 'Failed to save rating.';
            })
            .catch(function () { return 'Failed to save rating.'; });
    }

    function open(opts) {
        var o = opts || {};
        var modal = el('gd-qr-modal');
        var form = el('gd-qr-form');
        if (!modal || !form || !modal.showModal) {
            // No modal on the page: treat it as an immediate dismissal so callers don't hang.
            if (o.onDismiss) { o.onDismiss(); }
            return false;
        }

        var submit = form.querySelector('[data-gd-qr-submit]');
        var cancel = form.querySelector('[data-gd-qr-cancel]');
        // Everything else that closes: the header X, plus any other close control that ISN'T the
        // secondary action. A dismiss and a cancel are different events and callers rely on the split
        // (the share flow's secondary button means "skip, just download" -- the X must never download).
        var closers = Array.prototype.slice.call(
            modal.querySelectorAll('[data-gd-modal-close]:not([data-gd-qr-cancel])')
        );
        var submitLabel = o.submitLabel || (o.existing ? 'Update rating' : 'Submit rating');

        prefill(form, o.existing, o.blurb);

        var title = el('gd-qr-title');
        if (title) { title.textContent = o.title || (o.existing ? 'Update your rating' : 'Rate this game'); }
        if (submit) { submit.disabled = false; submit.textContent = submitLabel; }
        if (cancel && o.cancelLabel) { cancel.textContent = o.cancelLabel; }
        var hoursLbl = form.querySelector('[data-gd-qr-hours-label]');
        if (hoursLbl && o.hoursLabel) { hoursLbl.textContent = o.hoursLabel; }
        // The hint is SSR'd where playtime is a page-level fact (Game Detail). Where it varies per card
        // (the share modal) the caller passes it in.
        if (o.playtimeHint !== undefined) {
            var hint = form.querySelector('.gd-qr__hint');
            if (hint) {
                hint.textContent = o.playtimeHint || "We don't have your playtime for this game.";
                hint.classList.toggle('gd-qr__hint--muted', !o.playtimeHint);
            }
        }

        function onSlider(e) { if (e.target.matches('[data-gd-qr-slider]')) { setReadout(form, e.target.name); } }
        function onBlurb(e) { if (e.target.matches('[data-gd-qr-blurb]')) { refreshCount(form); } }

        // Idempotent, and bound to the dialog's native `close` as well as to the explicit paths -- that
        // event fires HOWEVER it closes, including the swipe-to-dismiss `pp-dismissable` adds on mobile.
        // Tearing down only on the explicit paths left every listener attached after a swipe, so the next
        // open double-bound them and one submit fired twice.
        var torn = false;
        function teardown() {
            if (torn) { return; }
            torn = true;
            form.removeEventListener('input', onSlider);
            form.removeEventListener('input', onBlurb);
            form.removeEventListener('submit', onSubmit);
            if (cancel) { cancel.removeEventListener('click', onCancel); }
            closers.forEach(function (b) { b.removeEventListener('click', onDismiss); });
            modal.removeEventListener('cancel', onEsc);
            modal.removeEventListener('click', onBackdrop);
            modal.removeEventListener('close', onClosed);
            if (o.onClose) { o.onClose(); }
        }
        function onClosed() { teardown(); }
        function close() { if (modal.close && modal.open) { modal.close(); } else { teardown(); } }

        function onCancel(e) { e.preventDefault(); close(); if (o.onCancel) { o.onCancel(); } }
        function onDismiss() { close(); if (o.onDismiss) { o.onDismiss(); } }
        function onEsc(e) { e.preventDefault(); onDismiss(); }
        function onBackdrop(e) { if (e.target === modal) { onDismiss(); } }

        function onSubmit(e) {
            e.preventDefault();
            var hoursEl = field(form, 'hours_to_platinum');
            var hours = parseInt(hoursEl && hoursEl.value, 10);
            if (!hours || hours < 1) {
                if (PP.ToastManager) { PP.ToastManager.show('Enter the hours it took you.', 'warning'); }
                return;
            }
            var area = form.querySelector('[data-gd-qr-blurb]');
            var blurb = area ? area.value.trim() : '';
            var payload = {
                difficulty: parseInt(field(form, 'difficulty').value, 10),
                grindiness: parseInt(field(form, 'grindiness').value, 10),
                fun_ranking: parseInt(field(form, 'fun_ranking').value, 10),
                overall_rating: parseFloat(field(form, 'overall_rating').value),
                hours_to_platinum: hours,
                blurb: blurb,
            };
            if (submit) { submit.disabled = true; submit.textContent = 'Saving...'; }

            // Posting a public quick take records guidelines agreement (the notice above the action IS
            // the fine print). Done FIRST so the rate call can't 403 with needs_guidelines. Idempotent.
            var agreed = form.dataset.guidelinesAgreed === '1';
            var pre = (blurb && !agreed)
                ? PP.API.post('/api/v1/guidelines/agree/', {})
                    .then(function () { form.dataset.guidelinesAgreed = '1'; })
                    .catch(function () { /* the rate call surfaces needs_guidelines if this failed */ })
                : Promise.resolve();

            pre.then(function () {
                return PP.API.post('/api/v1/ratings/' + o.conceptId + '/group/' + o.groupId + '/rate/', payload);
            }).then(function (data) {
                close();
                if (o.onSaved) { o.onSaved(data || {}, payload); }
            }).catch(function (error) {
                // Never close on failure: the hunter's input is still in the form, and a caller that
                // downloads on success must not be told the rating saved when it didn't.
                if (submit) { submit.disabled = false; submit.textContent = submitLabel; }
                return messageFor(error).then(function (msg) {
                    if (PP.ToastManager) { PP.ToastManager.show(msg, 'error'); }
                });
            });
        }

        form.addEventListener('input', onSlider);
        form.addEventListener('input', onBlurb);
        form.addEventListener('submit', onSubmit);
        if (cancel) { cancel.addEventListener('click', onCancel); }
        closers.forEach(function (b) { b.addEventListener('click', onDismiss); });
        modal.addEventListener('cancel', onEsc);
        modal.addEventListener('click', onBackdrop);
        modal.addEventListener('close', onClosed);

        if (PP.dismissableSheet && !modal.dataset.qrSwipeWired) {
            modal.dataset.qrSwipeWired = '1';
            PP.dismissableSheet(modal, { onClose: function () { if (modal.close && modal.open) { modal.close(); } } });
        }

        // Native showModal() scrolls the page to the dialog on mobile; put the scroll back.
        var y = window.scrollY;
        modal.showModal();
        if (window.scrollY !== y) { window.scrollTo(0, y); }
        if (o.onOpen) { o.onOpen(); }
        return true;
    }

    PP.QuickRate = { open: open, BLURB_MAX: BLURB_MAX };
})();
