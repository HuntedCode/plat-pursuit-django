/**
 * Game report ("Report an issue") — wires the SSR report modal (templates/trophies/partials/
 * game_detail/report_modal.html). The button opens it; a grouped flag-type select + optional details
 * (required when "Other") submit to POST /api/v1/games/<id>/flag/. Rebuilt to the rebuild modal
 * standard (was a DaisyUI modal injected from JS).
 */
const GameFlag = (() => {
    function init() {
        const modal = document.getElementById('game-flag-modal');
        const btn = document.getElementById('game-flag-btn');
        if (!modal || !btn) return;

        const form = modal.querySelector('#game-flag-form');
        const select = modal.querySelector('[name="flag_type"]');
        const textarea = modal.querySelector('[name="details"]');
        const countEl = modal.querySelector('[data-flag-count]');
        const detailsLabel = modal.querySelector('[data-flag-details-label]');
        const submitBtn = modal.querySelector('[data-flag-submit]');
        const gameId = modal.dataset.gameId;
        const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        function resetDetails() {
            textarea.required = false;
            detailsLabel.textContent = 'Additional details (optional)';
            countEl.textContent = '0';
        }

        // ── Open ──
        btn.addEventListener('click', () => {
            if (!modal.showModal || modal.open) return;
            form.reset();
            resetDetails();
            // Native <dialog>.showModal() scrolls the page to the dialog on mobile; keep the scroll put.
            const y = window.scrollY;
            modal.showModal();
            if (window.scrollY !== y) window.scrollTo(0, y);
        });

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
            PlatPursuit.dismissableSheet(modal, { onClose: function () { if (modal.close && modal.open) modal.close(); } });
        }

        // ── Submit ──
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const flagType = select.value;
            const details = textarea.value || '';
            const label = submitBtn.textContent;
            submitBtn.disabled = true;
            submitBtn.textContent = 'Submitting…';
            try {
                await PlatPursuit.API.post(`/api/v1/games/${gameId}/flag/`, { flag_type: flagType, details });
                PlatPursuit.ToastManager.show('Report submitted. Thanks for helping keep our data accurate!', 'success');
                close();
            } catch (error) {
                let msg = 'Failed to submit report.';
                try { const d = await error.response?.json(); msg = d?.error || msg; } catch (_) { /* no body */ }
                PlatPursuit.ToastManager.show(msg, 'error');
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = label;
            }
        });
    }

    return { init };
})();

document.addEventListener('DOMContentLoaded', () => GameFlag.init());
