/**
 * Game Flag Module
 *
 * Handles the "Flag" button on game detail pages, allowing users
 * to report data quality issues (delisted, unobtainable, shovelware,
 * VR tagging, online trophies, buggy trophies, regional errors).
 */
const GameFlag = (() => {
    let modalElement = null;

    function init() {
        const btn = document.getElementById('game-flag-btn');
        if (!btn) return;
        btn.addEventListener('click', () => showModal(btn.dataset.gameId));
    }

    function showModal(gameId) {
        if (!modalElement) {
            const html = `
                <dialog id="game-flag-modal" class="modal">
                    <div class="modal-box">
                        <h3 class="font-bold text-lg mb-4">Flag Game</h3>

                        <form id="game-flag-form" class="space-y-4">
                            <div class="form-control">
                                <label class="label">
                                    <span class="label-text">What would you like to report?</span>
                                </label>
                                <select class="select select-bordered w-full" name="flag_type" required>
                                    <option value="" disabled selected>Select an issue</option>
                                    <optgroup label="Store Status">
                                        <option value="delisted">Game has been delisted</option>
                                        <option value="not_delisted">Game is NOT delisted (incorrect flag)</option>
                                    </optgroup>
                                    <optgroup label="Trophy Obtainability">
                                        <option value="unobtainable">Trophies are unobtainable</option>
                                        <option value="obtainable">Trophies ARE obtainable (incorrect flag)</option>
                                    </optgroup>
                                    <optgroup label="Shovelware">
                                        <option value="is_shovelware">Game is shovelware</option>
                                        <option value="not_shovelware">Game is NOT shovelware (incorrect flag)</option>
                                    </optgroup>
                                    <optgroup label="VR Support">
                                        <option value="missing_vr">Game supports VR but is not tagged</option>
                                    </optgroup>
                                    <optgroup label="Online Trophies">
                                        <option value="has_online_trophies">Game has online-required trophies</option>
                                        <option value="no_online_trophies">Game does NOT have online trophies (incorrect flag)</option>
                                    </optgroup>
                                    <optgroup label="Buggy Trophies">
                                        <option value="has_buggy_trophies">Game has buggy/broken trophies</option>
                                        <option value="buggy_trophies_resolved">Buggy trophies have been fixed</option>
                                    </optgroup>
                                    <optgroup label="Regional Info">
                                        <option value="region_incorrect">Regional info is incorrect</option>
                                    </optgroup>
                                </select>
                            </div>

                            <div class="form-control">
                                <label class="label">
                                    <span class="label-text">Additional details (optional)</span>
                                </label>
                                <textarea
                                    class="textarea textarea-bordered h-24"
                                    name="details"
                                    placeholder="Provide any additional context that may help us review this flag..."
                                    maxlength="500"></textarea>
                                <label class="label">
                                    <span class="label-text-alt"></span>
                                    <span class="label-text-alt flag-details-count">0/500</span>
                                </label>
                            </div>

                            <div class="alert alert-info">
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="stroke-current shrink-0 w-6 h-6">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                                </svg>
                                <span class="text-sm">Flags are reviewed by our team and help us keep game data accurate. Thank you for contributing!</span>
                            </div>

                            <div class="modal-action">
                                <button type="button" class="btn btn-ghost" onclick="document.getElementById('game-flag-modal').close()">Cancel</button>
                                <button type="submit" class="btn btn-primary">Submit Flag</button>
                            </div>
                        </form>
                    </div>
                    <form method="dialog" class="modal-backdrop">
                        <button>close</button>
                    </form>
                </dialog>
            `;
            document.body.insertAdjacentHTML('beforeend', html);
            modalElement = document.getElementById('game-flag-modal');

            const textarea = modalElement.querySelector('textarea[name="details"]');
            const counter = modalElement.querySelector('.flag-details-count');
            textarea.addEventListener('input', () => {
                counter.textContent = `${textarea.value.length}/500`;
            });
        }

        modalElement.dataset.gameId = gameId;

        const form = modalElement.querySelector('#game-flag-form');
        form.reset();
        modalElement.querySelector('.flag-details-count').textContent = '0/500';

        form.onsubmit = async (e) => {
            e.preventDefault();
            await submitFlag(gameId, form);
        };

        modalElement.showModal();
    }

    async function submitFlag(gameId, form) {
        const formData = new FormData(form);
        const flagType = formData.get('flag_type');
        const details = formData.get('details') || '';

        const submitBtn = form.querySelector('button[type="submit"]');
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="loading loading-spinner loading-sm"></span> Submitting...';

        try {
            await PlatPursuit.API.post(`/api/v1/games/${gameId}/flag/`, {
                flag_type: flagType,
                details,
            });

            PlatPursuit.ToastManager.show(
                'Flag submitted successfully. Thank you for helping improve our data!',
                'success'
            );

            modalElement.close();

        } catch (error) {
            let msg = 'Failed to submit flag.';
            try {
                const errData = await error.response?.json();
                msg = errData?.error || msg;
            } catch {}
            PlatPursuit.ToastManager.show(msg, 'error');
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = 'Submit Flag';
        }
    }

    return { init };
})();

document.addEventListener('DOMContentLoaded', () => GameFlag.init());
