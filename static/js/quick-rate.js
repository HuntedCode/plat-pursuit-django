/*
 * The rating form, in two layers.
 *
 *   PlatPursuit.RatingFields -- the FORM. Prefill, live slider readouts, the blurb counter, the hours
 *                               gate, agree-to-guidelines-on-submit, the POST, and field-level error
 *                               surfacing. Knows nothing about how it is presented.
 *   PlatPursuit.QuickRate    -- the MODAL (`#gd-qr-modal`, quick_rate_modal.html). Opens, closes, and
 *                               hands its form to RatingFields.
 *
 * The split exists because the form has three hosts and only two of them are modals: the Game Detail
 * Ratings tab, the plat-card share modal, and the Rate My Games wizard, which renders it INLINE beside
 * a trophy list because its whole job is rating many games in a row. The wizard used to carry its own
 * copy, and the copies had drifted -- it had no quick take, no live readouts and no field-level errors.
 * That is the same drift QuickRate was created to end when Game Detail and the plat card had each grown
 * their own driver, so this time the form itself is the shared thing rather than the dialog around it.
 *
 *   PlatPursuit.RatingFields.attach(formEl, {
 *       conceptId, groupId,            // required -- the POST target, and re-settable per game
 *       existing, blurb,               // prefill; null for a fresh rating
 *       submitEl,                      // defaults to [data-gd-qr-submit] inside the form
 *       submitLabel, hoursLabel, playtimeHint,
 *       announcesSave,                 // the host toasts the save itself; otherwise this does
 *       onSaved(data, payload), onError(message), onChange(state),
 *   })  ->  { setTarget, prefill, submit, state, detach }
 *
 *   PlatPursuit.QuickRate.open({ ...the same, plus... title, cancelLabel,
 *                                onCancel(), onDismiss(), onOpen(), onClose() })  ->  bool
 *
 * The form INPUT NAMES are the API contract (see partials/_rating_fields.html) -- do not rename them.
 */
(function () {
    var PP = window.PlatPursuit = window.PlatPursuit || {};

    var SLIDERS = ['difficulty', 'grindiness', 'fun_ranking', 'overall_rating'];
    var DEFAULTS = { difficulty: 5, grindiness: 5, fun_ranking: 5, overall_rating: 3, hours_to_platinum: '' };
    var BLURB_MAX = 140;

    function el(id) { return document.getElementById(id); }
    function field(form, name) { return form.querySelector('[name="' + name + '"]'); }

    // The recommendation is a RADIO GROUP, so `field()` (which returns the first match) would always
    // report the first option's value rather than the chosen one. Read and write it through these.
    function recValue(form) {
        var picked = form.querySelector('[name="recommendation"]:checked');
        return picked ? picked.value : '';
    }

    /**
     * The just-saved rating, in the shape `prefill` reads -- for hosts that cache it so a re-open does not
     * wait on a refetch.
     *
     * Exported because BOTH hosts were rebuilding this object by hand, and when `recommendation` was added
     * neither was updated: game detail prefilled it correctly on first open (the server renders the
     * attribute) and then blank after any save, and the plat-card modal never prefilled it at all. The
     * server has the same one-definition rule in `UserConceptRating.as_prefill`.
     *
     * Prefers the server's stored value over the submitted one, for the same reason the blurb does: what
     * came back is what everyone else will see.
     */
    function prefillFrom(data, payload) {
        return {
            recommendation: (data && data.recommendation) || payload.recommendation,
            difficulty: payload.difficulty,
            grindiness: payload.grindiness,
            hours_to_platinum: payload.hours_to_platinum,
            fun_ranking: payload.fun_ranking,
            overall_rating: payload.overall_rating,
        };
    }
    function setRec(form, value) {
        form.querySelectorAll('[name="recommendation"]').forEach(function (input) {
            input.checked = Boolean(value) && input.value === value;
        });
    }

    function setReadout(form, name) {
        var out = form.querySelector('[data-gd-qr-val="' + name + '"]');
        var input = field(form, name);
        if (!out || !input) { return; }
        out.textContent = name === 'overall_rating' ? parseFloat(input.value).toFixed(1) : String(input.value);

        // Overall also drives a star bar, because that is the form the score takes everywhere else on the
        // site -- so the control shows the thing being set, not only the number setting it. Percentage of
        // 5 rather than a rounded glyph count: the slider steps in halves and the bar is clipped by width,
        // so 3.5 draws as three and a half stars.
        if (name === 'overall_rating') {
            var stars = form.querySelector('[data-gd-qr-stars]');
            if (stars) { stars.style.setProperty('--fill', (parseFloat(input.value) / 5) * 100 + '%'); }
        }
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
        // Cleared explicitly rather than through DEFAULTS, because its "default" is NOTHING PICKED. The
        // wizard calls prefill() with no argument between games, and a recommendation left checked from
        // the previous game would be pre-answered on the next one -- an answer the hunter never gave, on
        // the one field that is required.
        setRec(form, existing && existing.recommendation);
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

    /**
     * Attach the form behaviour to a <form>. The host owns where that form lives and what happens after
     * a save; everything between the first keystroke and the POST landing belongs here.
     */
    function attach(form, opts) {
        var o = opts || {};
        if (!form) { return null; }

        var submitEl = o.submitEl || form.querySelector('[data-gd-qr-submit]');
        var submitLabel = o.submitLabel || (o.existing ? 'Update rating' : 'Submit rating');
        var conceptId = o.conceptId;
        var groupId = o.groupId;
        var busy = false;

        function hoursValue() {
            var hoursEl = field(form, 'hours_to_platinum');
            return parseInt(hoursEl && hoursEl.value, 10);
        }
        function ready() {
            var h = hoursValue();
            // TWO gates now. The host's submit button is enabled off this and nothing else, so a
            // requirement missing from here is a requirement the button ignores -- the save would go out
            // and come back 400 from the server instead of being prevented.
            return Boolean(h) && h >= 1 && Boolean(recValue(form));
        }
        function announce() {
            if (o.onChange) {
                o.onChange({
                    ready: ready(),
                    hours: hoursValue() || null,
                    recommendation: recValue(form),
                });
            }
        }

        function onInput(e) {
            if (e.target.matches('[data-gd-qr-slider]')) { setReadout(form, e.target.name); }
            if (e.target.matches('[data-gd-qr-blurb]')) { refreshCount(form); }
            if (e.target.name === 'hours_to_platinum') { announce(); }
            // Radios fire `input` as well as `change`, so the existing delegated listener covers them.
            if (e.target.name === 'recommendation') { announce(); }
        }

        function doSubmit() {
            // One save in flight at a time, whatever fired it. The modal used to get this from its own
            // `modal.open` guard; a host with an external button (the wizard) has no such guard, and a
            // double-press would post twice.
            if (busy) { return; }
            var hours = hoursValue();
            if (!hours || hours < 1) {
                // The hours gate. A host that renders its own requirement line hears about it through
                // onChange and says so inline; one that does not gets the toast.
                if (!o.onChange && PP.ToastManager) { PP.ToastManager.show('Enter the hours it took you.', 'warning'); }
                announce();
                return;
            }
            var recommendation = recValue(form);
            if (!recommendation) {
                if (!o.onChange && PP.ToastManager) { PP.ToastManager.show('Pick a recommendation.', 'warning'); }
                announce();
                return;
            }
            var area = form.querySelector('[data-gd-qr-blurb]');
            var blurb = area ? area.value.trim() : '';
            // Hand-built rather than serialized from the form, so a field added to the markup and not to
            // this object is silently never sent -- and on an EDIT the server would then write its
            // default over the stored answer.
            var payload = {
                recommendation: recommendation,
                difficulty: parseInt(field(form, 'difficulty').value, 10),
                grindiness: parseInt(field(form, 'grindiness').value, 10),
                fun_ranking: parseInt(field(form, 'fun_ranking').value, 10),
                overall_rating: parseFloat(field(form, 'overall_rating').value),
                hours_to_platinum: hours,
                blurb: blurb,
            };
            busy = true;
            if (submitEl) { submitEl.disabled = true; submitEl.textContent = 'Saving...'; }

            // Posting a public quick take records guidelines agreement (the notice above the action IS
            // the fine print). Done FIRST so the rate call can't 403 with needs_guidelines. Idempotent.
            var agreed = form.dataset.guidelinesAgreed === '1';
            var pre = (blurb && !agreed)
                ? PP.API.post('/api/v1/guidelines/agree/', {})
                    .then(function () { form.dataset.guidelinesAgreed = '1'; })
                    .catch(function () { /* the rate call surfaces needs_guidelines if this failed */ })
                : Promise.resolve();

            return pre.then(function () {
                return PP.API.post('/api/v1/ratings/' + conceptId + '/group/' + groupId + '/rate/', payload);
            }).then(function (data) {
                busy = false;
                if (submitEl) { submitEl.disabled = false; submitEl.textContent = submitLabel; }
                if (o.onSaved) { o.onSaved(data || {}, payload); }

                // Confirming the save is the CONTROLLER's job unless a host claims it with
                // `announcesSave`. The error path below has always had this fallback and the success path
                // did not, which is a strange asymmetry to leave in a shared component: a host that
                // forgets its error handler still shows the error, while a host that forgets to confirm a
                // save shows nothing at all. That is exactly what happened to the plat-card modal --
                // editing a rating there closed the dialog and gave no sign it had worked.
                //
                // Runs AFTER the host's onSaved, which for QuickRate hosts is after the dialog closes, so
                // the toast lands on the page's container rather than the modal's popover (the dialog
                // takes that down with it). The server's message distinguishes a new rating from an
                // update; hosts wanting their own wording set the flag and say it themselves.
                if (!o.announcesSave && PP.ToastManager) {
                    PP.ToastManager.show((data && data.message) || 'Rating saved!', 'success');
                }
            }).catch(function (error) {
                // Never treat a failure as a save: the hunter's input is still in the form, and a caller
                // that downloads on success must not be told the rating landed when it didn't.
                busy = false;
                if (submitEl) { submitEl.disabled = false; submitEl.textContent = submitLabel; }
                return messageFor(error).then(function (msg) {
                    if (o.onError) { o.onError(msg); }
                    else if (PP.ToastManager) { PP.ToastManager.show(msg, 'error'); }
                });
            });
        }

        function onSubmitEvent(e) { e.preventDefault(); doSubmit(); }

        form.addEventListener('input', onInput);
        form.addEventListener('submit', onSubmitEvent);

        // The guidelines sheet is wired HERE, by the component that owns the link. `_rating_fields.html`
        // carries the only [data-gd-guidelines-open] trigger on the site, so every host of these fields
        // inherits the affordance -- and a host cannot ship the notice without the sheet behind it, which
        // is exactly what the wizard did: it included the dialog and rendered the link, but never called
        // the wiring, so the link was dead on the one surface built entirely around rating games.
        // Idempotent and a no-op when the sheet is absent, so hosts that also call it are unaffected.
        if (PP.wireGuidelinesSheet) { PP.wireGuidelinesSheet(); }

        // Labels and the playtime hint vary per host -- and in the wizard, per GAME, which is why they
        // are re-settable rather than read once at attach.
        function label(next) {
            if (next.submitLabel) {
                submitLabel = next.submitLabel;
                if (submitEl) { submitEl.textContent = submitLabel; }
            }
            var hoursLbl = form.querySelector('[data-gd-qr-hours-label]');
            if (hoursLbl && next.hoursLabel) { hoursLbl.textContent = next.hoursLabel; }
            // The hint is SSR'd where playtime is a page-level fact (Game Detail). Where it varies per
            // card or per game, the caller passes it in.
            if (next.playtimeHint !== undefined) {
                var hint = form.querySelector('.gd-qr__hint');
                if (hint) {
                    hint.textContent = next.playtimeHint || "We don't have your playtime for this game.";
                    hint.classList.toggle('gd-qr__hint--muted', !next.playtimeHint);
                }
            }
        }

        function fill(existing, blurb) {
            prefill(form, existing, blurb);
            announce();
        }

        // Order matters: reset the button FIRST, then prefill -- because prefilling announces readiness, and
        // a host that gates its button on that (the wizard, whose hours field starts empty) must get the
        // last word. Enabling after the announcement re-enabled a button its own host had just disabled.
        if (submitEl) { submitEl.disabled = false; submitEl.textContent = submitLabel; }
        label(o);
        fill(o.existing, o.blurb);

        return {
            // Re-point at the next game without re-binding anything -- the wizard advances through a
            // queue against one form, and detaching/attaching per game is how listeners stack up.
            setTarget: function (nextConceptId, nextGroupId) {
                conceptId = nextConceptId;
                groupId = nextGroupId;
            },
            prefill: fill,
            label: label,
            submit: doSubmit,
            state: function () { return { ready: ready(), busy: busy }; },
            detach: function () {
                form.removeEventListener('input', onInput);
                form.removeEventListener('submit', onSubmitEvent);
            },
        };
    }

    function open(opts) {
        var o = opts || {};
        var modal = el('gd-qr-modal');
        var form = el('gd-qr-form');
        if (!modal || !form || !modal.showModal) {
            // No modal on the page. Report it and let the caller decide -- a host that gates something
            // on this (the share flow gates a DOWNLOAD) must not have that swallowed by a missing
            // dialog. Callers check the return value; nothing is invoked here.
            return false;
        }
        // Already open: bail rather than binding a SECOND set of listeners. showModal() on an open modal
        // dialog is a silent no-op, so without this the listeners below would stack and one submit would
        // fire two POSTs -- two downloads on the share flow.
        if (modal.open) { return false; }

        var cancel = form.querySelector('[data-gd-qr-cancel]');
        // Everything else that closes: the header X, plus any other close control that ISN'T the
        // secondary action. A dismiss and a cancel are different events and callers rely on the split
        // (the share flow's secondary button means "skip, just download" -- the X must never download).
        var closers = Array.prototype.slice.call(
            modal.querySelectorAll('[data-gd-modal-close]:not([data-gd-qr-cancel])')
        );

        var fields = attach(form, {
            conceptId: o.conceptId,
            groupId: o.groupId,
            existing: o.existing,
            blurb: o.blurb,
            submitLabel: o.submitLabel || (o.existing ? 'Update rating' : 'Submit rating'),
            hoursLabel: o.hoursLabel,
            playtimeHint: o.playtimeHint,
            onSaved: function (data, payload) {
                close();
                if (o.onSaved) { o.onSaved(data, payload); }
            },
        });

        var title = el('gd-qr-title');
        if (title) { title.textContent = o.title || (o.existing ? 'Update your rating' : 'Rate this game'); }
        if (cancel && o.cancelLabel) { cancel.textContent = o.cancelLabel; }

        // Idempotent, and bound to the dialog's native `close` as well as to the explicit paths -- that
        // event fires HOWEVER it closes, including the swipe-to-dismiss `pp-dismissable` adds on mobile.
        // Tearing down only on the explicit paths left every listener attached after a swipe, so the next
        // open double-bound them and one submit fired twice.
        var torn = false;
        function teardown() {
            if (torn) { return; }
            torn = true;
            if (fields) { fields.detach(); }
            if (cancel) { cancel.removeEventListener('click', onCancel); }
            closers.forEach(function (b) { b.removeEventListener('click', onDismiss); });
            modal.removeEventListener('cancel', onEsc);
            modal.removeEventListener('click', onBackdrop);
            modal.removeEventListener('close', onClosed);
            if (o.onClose) { o.onClose(); }
        }
        function onClosed() {
            // The swipe path closes the dialog directly, so this is where it lands. Route it through the
            // same dismissal callback as the X and the backdrop -- the header comment promises that, and
            // a host that clears state on abandonment must hear about every abandonment.
            var swiped = modal.dataset.qrSwiped === '1';
            delete modal.dataset.qrSwiped;
            var wasTorn = torn;
            teardown();
            if (swiped && !wasTorn && o.onDismiss) { o.onDismiss(); }
        }
        function close() { if (modal.close && modal.open) { modal.close(); } else { teardown(); } }

        function onCancel(e) { e.preventDefault(); close(); if (o.onCancel) { o.onCancel(); } }
        function onDismiss() { close(); if (o.onDismiss) { o.onDismiss(); } }
        function onEsc(e) { e.preventDefault(); onDismiss(); }
        function onBackdrop(e) { if (e.target === modal) { onDismiss(); } }

        if (cancel) { cancel.addEventListener('click', onCancel); }
        closers.forEach(function (b) { b.addEventListener('click', onDismiss); });
        modal.addEventListener('cancel', onEsc);
        modal.addEventListener('click', onBackdrop);
        modal.addEventListener('close', onClosed);

        // Swipe-to-dismiss. Routed through onDismiss() like the X and the backdrop -- it IS a dismissal,
        // and a host that un-sets state on abandonment would otherwise never hear about this one path.
        // Wired once per element; the flag dies with the node on an htmx restore, so a fresh dialog
        // rewires correctly.
        if (PP.dismissableSheet && !modal.dataset.qrSwipeWired) {
            modal.dataset.qrSwipeWired = '1';
            PP.dismissableSheet(modal, {
                onClose: function () {
                    // `swiped` is read by onClosed below: teardown runs from the `close` event either
                    // way, and this tells it the close was a dismissal rather than a bare unmount.
                    modal.dataset.qrSwiped = '1';
                    if (modal.close && modal.open) { modal.close(); }
                },
            });
        }

        // Native showModal() scrolls the page to the dialog on mobile; put the scroll back.
        var y = window.scrollY;
        modal.showModal();
        if (window.scrollY !== y) { window.scrollTo(0, y); }
        if (o.onOpen) { o.onOpen(); }
        return true;
    }

    PP.RatingFields = {
        attach: attach, prefillFrom: prefillFrom, BLURB_MAX: BLURB_MAX, DEFAULTS: DEFAULTS,
    };
    PP.QuickRate = { open: open, BLURB_MAX: BLURB_MAX };
})();
