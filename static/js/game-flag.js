/**
 * Game report ("Report an issue") — wires the SSR report modal (templates/trophies/partials/
 * game_detail/report_modal.html). The button opens it; a grouped flag-type select + optional details
 * (required when "Other") submit the report. Rebuilt to the rebuild modal standard (was a DaisyUI
 * modal injected from JS).
 *
 * Two submit shapes, decided by the markup the server rendered — never by which page this is:
 *
 *   - no version checkboxes: POST /api/v1/games/<id>/flag/ with {flag_type, details}
 *   - version checkboxes:    POST /api/v1/games/<id>/flag/versions/ with {game_ids, flag_type, details}
 *
 * The fan-out is server-side on purpose. Looping this endpoint per version would hit its 5/min
 * per-user limit mid-way through a six-list concept, having already filed some, with no way to tell
 * the reporter which ones landed.
 */
const GameFlag = (() => {
    function init() {
        const modal = document.getElementById('game-flag-modal');
        // Several surfaces open the same modal: the hero's Report button and the About tab's empty-state
        // CTA. Any element carrying [data-flag-open] joins in without touching this file again.
        const btns = document.querySelectorAll('#game-flag-btn, [data-flag-open]');
        if (!modal || !btns.length) return;

        const form = modal.querySelector('#game-flag-form');
        const select = modal.querySelector('[name="flag_type"]');
        const textarea = modal.querySelector('[name="details"]');
        const countEl = modal.querySelector('[data-flag-count]');
        const detailsLabel = modal.querySelector('[data-flag-details-label]');
        const submitBtn = modal.querySelector('[data-flag-submit]');
        const gameId = modal.dataset.gameId;
        const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        // The SERVER decides which shape this modal is, and says so with [data-flag-bulk]. Inferring
        // it from "are there checkboxes" would make the mode an accident of markup -- a future row
        // of checkboxes added for anything else would silently re-point the submit URL.
        const boxes = Array.from(modal.querySelectorAll('input[name="game_ids"]'));
        const isBulk = form.hasAttribute('data-flag-bulk') && boxes.length > 0;
        const vcountEl = modal.querySelector('[data-flag-vcount]');
        // The server's per-request cap (GameFlagService.MAX_BULK_VERSIONS), rendered onto the form.
        // Enforced here too so "All" on a concept with more trophy lists than the cap cannot walk
        // the reader into a 400 that nothing warned them about. 0 when absent = no client-side cap.
        const maxVersions = Number(form.dataset.flagMax) || 0;

        function checkedIds() {
            return boxes.filter((b) => b.checked).map((b) => Number(b.value));
        }

        function syncVersions() {
            if (!isBulk) return;
            const n = checkedIds().length;
            const tooMany = maxVersions > 0 && n > maxVersions;
            const blocked = n === 0 || tooMany;
            // Gate submit here rather than letting the POST come back with an error: the modal
            // already knows the answer, and a blocked button says so without a round trip.
            // `aria-disabled`, not `disabled`: a disabled button leaves the tab order and is
            // announced only as "unavailable", so the reason never reaches a screen-reader user.
            // This keeps it focusable and describable; the submit handler refuses both bad states.
            submitBtn.setAttribute('aria-disabled', blocked ? 'true' : 'false');
            submitBtn.classList.toggle('is-inert', blocked);
            if (vcountEl) {
                if (n === 0) {
                    vcountEl.textContent = 'Pick at least one version';
                } else if (tooMany) {
                    vcountEl.textContent = `${n} selected — ${maxVersions} is the most you can report at once`;
                } else {
                    vcountEl.textContent = `${n} of ${boxes.length} version${boxes.length === 1 ? '' : 's'} selected`;
                }
            }
        }

        function resetDetails() {
            textarea.required = false;
            detailsLabel.textContent = 'Additional details (optional)';
            countEl.textContent = '0';
        }

        boxes.forEach((b) => b.addEventListener('change', syncVersions));
        const allBtn = modal.querySelector('[data-flag-all]');
        const noneBtn = modal.querySelector('[data-flag-none]');
        if (allBtn) allBtn.addEventListener('click', () => {
            // Stops at the cap rather than checking every row and handing the reader a refusal.
            boxes.forEach((b, i) => { b.checked = maxVersions > 0 ? i < maxVersions : true; });
            syncVersions();
        });
        if (noneBtn) noneBtn.addEventListener('click', () => {
            boxes.forEach((b) => { b.checked = false; }); syncVersions();
        });

        // ── Open ──
        btns.forEach((btn) => btn.addEventListener('click', () => {
            if (!modal.showModal || modal.open) return;
            // `form.reset()` restores the SSR `checked` attributes (the viewed list), not whatever
            // the last open left behind — so re-opening always starts from the page's own selection.
            form.reset();
            resetDetails();
            syncVersions();
            // Native <dialog>.showModal() scrolls the page to the dialog on mobile; keep the scroll put.
            const y = window.scrollY;
            modal.showModal();
            if (window.scrollY !== y) window.scrollTo(0, y);
        }));

        // ── Field behaviour ──
        textarea.addEventListener('input', () => { countEl.textContent = String(textarea.value.length); });
        select.addEventListener('change', () => {
            const isOther = select.value === 'other';
            textarea.required = isOther;
            detailsLabel.textContent = isOther ? 'Describe the issue (required)' : 'Additional details (optional)';
        });

        // ── Choreographed close (matches the other rebuild modals: fade/scale out, then .close()) ──
        function close() {
            if (!modal.open) return;
            if (reduce) { modal.close(); return; }
            modal.classList.add('is-closing');
            let done = false;
            function finish() {
                if (done) return; done = true;
                modal.removeEventListener('animationend', onEnd);
                modal.classList.remove('is-closing');
                if (modal.open) modal.close();
            }
            function onEnd(e) { if (e.target === modal) finish(); }
            modal.addEventListener('animationend', onEnd);
            window.setTimeout(finish, 240);
        }
        modal.querySelectorAll('[data-gd-modal-close]').forEach((b) => b.addEventListener('click', close));
        modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
        modal.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
        if (window.PlatPursuit && PlatPursuit.dismissableSheet) {
            // `handle`: this is a sheet you OPERATE, and an accidental dismiss costs the whole form
            // (re-opening runs form.reset(), and there is no draft). utils.js exempts touches that
            // start on input/textarea/select, but a finger on a version ROW lands on the label's
            // <span> -- the checkbox is a sibling, so closest('input') is null and the drag armed.
            // With a 216px scrolling list sitting at scrollTop 0, one downward flick to scroll it
            // was a dismissal. Restricting the grab to the header makes the gesture deliberate.
            PlatPursuit.dismissableSheet(modal, {
                handle: '.gd-modal__head',
                onClose: function () { if (modal.close && modal.open) modal.close(); },
            });
        }

        // ── Submit ──
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const flagType = select.value;
            const details = textarea.value || '';
            const gameIds = isBulk ? checkedIds() : null;
            if (isBulk && (!gameIds.length || (maxVersions > 0 && gameIds.length > maxVersions))) {
                syncVersions();
                return;
            }
            const label = submitBtn.textContent;
            submitBtn.disabled = true;
            submitBtn.textContent = 'Submitting…';
            try {
                // The server owns the wording: it is the only side that knows how many versions were
                // NEW versus already reported, and a flat "Report submitted" for a submission that
                // filed nothing is the one message this modal could send that is untrue.
                const url = isBulk
                    ? `/api/v1/games/${gameId}/flag/versions/`
                    : `/api/v1/games/${gameId}/flag/`;
                const body = isBulk
                    ? { game_ids: gameIds, flag_type: flagType, details }
                    : { flag_type: flagType, details };
                const data = await PlatPursuit.API.post(url, body);
                // Only the BULK response's message is used. The single endpoint also returns one
                // ("Flag submitted successfully...") but it speaks the internal "Flag" vocabulary
                // this rebuilt modal deliberately calls "Report" -- reading it here silently
                // regressed the copy on the trophy list page, whose behaviour was meant to be
                // untouched. The bulk message is different in kind: only the server knows how many
                // versions were new versus already reported.
                const msg = (isBulk && data && data.message)
                    || 'Report submitted. Thanks for helping keep our data accurate!';
                PlatPursuit.ToastManager.show(msg, 'success');
                close();
            } catch (error) {
                let msg = 'Failed to submit report.';
                // `detail` as well as `error`: our own refusals use `error`, but a tripped rate
                // limit raises Ratelimited (a PermissionDenied subclass) which DRF renders as a 403
                // carrying `detail`. Reading only `error` told a rate-limited reporter nothing.
                try {
                    const d = await error.response?.json();
                    msg = d?.error || d?.detail || msg;
                } catch (_) { /* no body */ }
                PlatPursuit.ToastManager.show(msg, 'error');
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = label;
                syncVersions();
            }
        });
    }

    return { init };
})();

document.addEventListener('DOMContentLoaded', () => GameFlag.init());
