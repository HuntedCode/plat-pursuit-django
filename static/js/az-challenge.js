/**
 * A-Z Challenge JavaScript
 *
 * Handles:
 * - AZChallengeSetup: Guided wizard for picking games letter by letter
 * - AZChallengeEdit: Grid edit page with swap/clear per slot, search modal
 * - AZChallengeDetail: Detail page with share button
 *
 * Uses: PlatPursuit.API, PlatPursuit.ToastManager, PlatPursuit.HTMLUtils, PlatPursuit.debounce
 */

// ─── Shared Constants ────────────────────────────────────────────────────────

const TROPHY_CUP_SVG = '<svg class="w-3 h-3 inline-block flex-shrink-0" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" aria-hidden="true"><path d="M102.49,0c0,27.414,0,104.166,0,137.062c0,112.391,99.33,156.25,153.51,156.25c54.18,0,153.51-43.859,153.51-156.25c0-32.896,0-109.648,0-137.062H102.49z M256.289,50.551l-68.164,29.768v98.474l-0.049,19.53c-0.526-0.112-47.274-10.112-47.274-78.391c0-28.17,0-69.6,0-69.6h60.385L256.289,50.551z"/><polygon points="315.473,400.717 291.681,367.482 279.791,318.506 256,322.004 232.209,318.506 220.314,367.482 205.347,388.394 196.527,400.476 196.699,400.476 196.527,400.717"/><polygon points="366.93,432.24 366.93,432 145.07,432 145.07,511.598 145.07,511.76 145.07,511.76 145.07,512 366.93,512 366.93,432.402 366.93,432.24"/><path d="M511.638,96.668c-0.033-1.268-0.068-2.336-0.068-3.174V45.1h-73.889v38.736h35.152v9.658c0,1.127,0.037,2.557,0.086,4.258c0.389,13.976,1.303,46.707-21.545,70.203c-5.121,5.266-11.221,9.787-18.219,13.613c-3.883,17.635-10.109,33.564-18.104,47.814c26.561-6.406,48.026-17.898,64.096-34.422C513.402,159.734,512.121,113.918,511.638,96.668z"/><path d="M60.625,167.955c-22.848-23.496-21.934-56.227-21.541-70.203c0.047-1.701,0.082-3.131,0.082-4.258v-9.658h34.842h0.07l0,0h0.24V45.1H0.43v48.394c0,0.838-0.032,1.906-0.068,3.174c-0.482,17.25-1.76,63.066,32.494,98.293c16.068,16.524,37.531,28.014,64.092,34.422c-7.996-14.25-14.22-30.182-18.103-47.816C71.846,177.74,65.746,173.221,60.625,167.955z"/></svg>';
function trophyCup(color) {
    return TROPHY_CUP_SVG.replace('aria-hidden', `fill="${color}" aria-hidden`);
}
const REGION_COLORS = { NA: 'badge-primary', EU: 'badge-secondary', JP: 'badge-accent', AS: 'badge-warning', KR: 'badge-info' };
const GLOBE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 512 512" stroke="var(--color-success)" fill="var(--color-success)"><path d="M351.9 280l-190.9 0c2.9 64.5 17.2 123.9 37.5 167.4 11.4 24.5 23.7 41.8 35.1 52.4 11.2 10.5 18.9 12.2 22.9 12.2s11.7-1.7 22.9-12.2c11.4-10.6 23.7-28 35.1-52.4 20.3-43.5 34.6-102.9 37.5-167.4zM160.9 232l190.9 0C349 167.5 334.7 108.1 314.4 64.6 303 40.2 290.7 22.8 279.3 12.2 268.1 1.7 260.4 0 256.4 0s-11.7 1.7-22.9 12.2c-11.4 10.6-23.7 28-35.1 52.4-20.3 43.5-34.6 102.9-37.5 167.4zm-48 0C116.4 146.4 138.5 66.9 170.8 14.7 78.7 47.3 10.9 131.2 1.5 232l111.4 0zM1.5 280c9.4 100.8 77.2 184.7 169.3 217.3-32.3-52.2-54.4-131.7-57.9-217.3L1.5 280zm398.4 0c-3.5 85.6-25.6 165.1-57.9 217.3 92.1-32.7 159.9-116.5 169.3-217.3l-111.4 0zm111.4-48C501.9 131.2 434.1 47.3 342 14.7 374.3 66.9 396.4 146.4 399.9 232l111.4 0z"/></svg>';
const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('');
const SEARCH_LIMIT = 20;

// ─── Shared Delete Handler ───────────────────────────────────────────────────
// Binds to #delete-challenge-btn on any page that includes this script.

function _bindDeleteButton() {
    const btn = document.getElementById('delete-challenge-btn');
    if (!btn) return;

    btn.addEventListener('click', () => {
        const challengeId = btn.dataset.challengeId;
        if (!challengeId) return;

        // Inject confirmation modal if not already present
        if (!document.getElementById('delete-challenge-modal')) {
            document.body.insertAdjacentHTML('beforeend', `
                <dialog id="delete-challenge-modal" class="modal">
                    <div class="modal-box max-w-sm">
                        <h3 class="text-lg font-bold text-error">Delete Challenge</h3>
                        <p class="py-4 text-base-content/70">
                            Are you sure? This will permanently remove your challenge. Any progress on incomplete slots will be lost.
                        </p>
                        <div class="modal-action">
                            <form method="dialog">
                                <button class="btn btn-ghost btn-sm">Cancel</button>
                            </form>
                            <button id="confirm-delete-btn" class="btn btn-error btn-sm">Delete</button>
                        </div>
                    </div>
                    <form method="dialog" class="modal-backdrop"><button>close</button></form>
                </dialog>
            `);

            document.getElementById('confirm-delete-btn').addEventListener('click', async () => {
                const modal = document.getElementById('delete-challenge-modal');
                try {
                    await PlatPursuit.API.delete(`/api/v1/challenges/az/${challengeId}/delete/`);
                    modal.close();
                    PlatPursuit.ToastManager.success('Challenge deleted.');
                    window.location.href = '/my-challenges/';
                } catch (error) {
                    modal.close();
                    let msg = 'Failed to delete challenge.';
                    try { const errData = await error.response?.json(); msg = errData?.error || msg; } catch {}
                    PlatPursuit.ToastManager.error(msg);
                }
            });
        }

        document.getElementById('delete-challenge-modal').showModal();
    });
}

// Auto-bind on DOMContentLoaded so it works on pages without explicit module init
// (e.g. My Challenges page)
document.addEventListener('DOMContentLoaded', _bindDeleteButton);

// ─── Shared Inline Rename ────────────────────────────────────────────────────
// Binds click-to-edit rename on pages with the matching DOM structure.

function _bindInlineRename(challengeId, onRenamed) {
    const display = document.getElementById('challenge-name-display');
    const nameText = document.getElementById('challenge-name-text');
    const editBtn = document.getElementById('challenge-name-edit-btn');
    const editor = document.getElementById('challenge-name-editor');
    const input = document.getElementById('challenge-name-input');
    const saveBtn = document.getElementById('challenge-name-save-btn');
    const cancelBtn = document.getElementById('challenge-name-cancel-btn');
    const renameCounter = document.getElementById('rename-counter');

    if (!editBtn || !editor || !input || !saveBtn || !cancelBtn) return;

    function updateCounter() {
        if (renameCounter) renameCounter.textContent = `${input.value.length}/75`;
    }

    let originalName = nameText ? nameText.textContent.trim() : input.value.trim();
    let isSaving = false;

    function openEditor() {
        input.value = originalName;
        updateCounter();
        display.classList.add('hidden');
        editBtn.classList.add('hidden');
        editor.classList.remove('hidden');
        editor.classList.add('flex');
        input.focus();
        input.select();
    }

    function closeEditor() {
        editor.classList.add('hidden');
        editor.classList.remove('flex');
        display.classList.remove('hidden');
        editBtn.classList.remove('hidden');
    }

    async function saveName() {
        if (isSaving) return;

        const newName = input.value.trim();
        if (!newName) {
            PlatPursuit.ToastManager.error('Challenge name cannot be empty.');
            input.focus();
            return;
        }
        if (newName === originalName) {
            closeEditor();
            return;
        }

        isSaving = true;
        input.disabled = true;
        saveBtn.disabled = true;
        const savedHTML = saveBtn.innerHTML;
        saveBtn.innerHTML = '<span class="loading loading-spinner loading-xs"></span>';

        try {
            await PlatPursuit.API.patch(
                `/api/v1/challenges/az/${challengeId}/update/`,
                { name: newName }
            );

            originalName = newName;
            if (nameText) nameText.textContent = newName;
            input.value = newName;

            // Update page title
            document.title = document.title.replace(
                /^(Edit: )?.*? - /,
                (match, prefix) => `${prefix || ''}${newName} - `
            );

            closeEditor();
            PlatPursuit.ToastManager.success('Challenge renamed!');
            if (onRenamed) onRenamed(newName);

        } catch (err) {
            let msg = 'Failed to rename challenge.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);
        } finally {
            isSaving = false;
            input.disabled = false;
            saveBtn.disabled = false;
            saveBtn.innerHTML = savedHTML;
        }
    }

    editBtn.addEventListener('click', openEditor);
    if (nameText) {
        nameText.style.cursor = 'pointer';
        nameText.addEventListener('click', openEditor);
    }
    cancelBtn.addEventListener('click', closeEditor);
    saveBtn.addEventListener('click', saveName);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); saveName(); }
        else if (e.key === 'Escape') { e.preventDefault(); closeEditor(); }
    });
    input.addEventListener('input', updateCounter);
}

// ─── Shared Filter State ─────────────────────────────────────────────────────
// Persists across letter navigation for both Setup and Edit modules.

const _sharedFilterState = {
    platform: new Set(),
    region: new Set(),
    sort: 'popular',
};

const CHIP_ACTIVE_CLASSES = {
    platform: 'badge-primary',
    region: {
        global: 'badge-success',
        NA: 'badge-primary',
        EU: 'badge-secondary',
        JP: 'badge-accent',
        AS: 'badge-warning',
    }
};

// ─── Shared Helpers ──────────────────────────────────────────────────────────

/**
 * Bind multi-select chip filter buttons.
 * @param {string} chipSelector - CSS selector for chip buttons
 * @param {string} clearBtnId - ID of the clear button
 * @param {object} filterState - { platform: Set, region: Set }
 * @param {function} onChangeCallback - Called after any filter change
 */
function _bindChipFilters(chipSelector, clearBtnId, filterState, onChangeCallback) {
    const chips = document.querySelectorAll(chipSelector);
    const clearBtn = document.getElementById(clearBtnId);
    if (!chips.length) return;

    const updateChipVisual = (chip, active) => {
        const filterType = chip.dataset.filter;
        const value = chip.dataset.value;
        chip.classList.remove('badge-ghost', 'badge-primary', 'badge-secondary',
                              'badge-accent', 'badge-warning', 'badge-success');
        if (active) {
            const colorClass = filterType === 'platform'
                ? CHIP_ACTIVE_CLASSES.platform
                : (CHIP_ACTIVE_CLASSES.region[value] || 'badge-warning');
            chip.classList.add(colorClass);
        } else {
            chip.classList.add('badge-ghost');
        }
    };

    const updateClearBtn = () => {
        const total = filterState.platform.size + filterState.region.size;
        if (clearBtn) clearBtn.classList.toggle('hidden', total === 0);
    };

    // Restore visual state from existing filterState
    chips.forEach(chip => {
        const filterType = chip.dataset.filter;
        const value = chip.dataset.value;
        if (filterState[filterType]?.has(value)) {
            updateChipVisual(chip, true);
        }
    });
    updateClearBtn();

    chips.forEach(chip => {
        chip.addEventListener('click', () => {
            const filterType = chip.dataset.filter;
            const value = chip.dataset.value;
            if (filterState[filterType].has(value)) {
                filterState[filterType].delete(value);
                updateChipVisual(chip, false);
            } else {
                filterState[filterType].add(value);
                updateChipVisual(chip, true);
            }
            updateClearBtn();
            onChangeCallback();
        });
    });

    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            filterState.platform.clear();
            filterState.region.clear();
            chips.forEach(chip => updateChipVisual(chip, false));
            updateClearBtn();
            onChangeCallback();
        });
    }
}

/**
 * Build URL query params from filter state.
 */
function _buildFilterParams(filterState) {
    let params = '';
    if (filterState.platform.size > 0) {
        params += `&platform=${[...filterState.platform].join(',')}`;
    }
    if (filterState.region.size > 0) {
        params += `&region=${[...filterState.region].join(',')}`;
    }
    params += `&sort=${filterState.sort}`;
    return params;
}

/**
 * Render a game card for search results (shared between Setup and Edit).
 * Full-width single-row design with rich data.
 */
function _renderGameCard(game) {
    const e = PlatPursuit.HTMLUtils.escape;
    const imgUrl = game.title_icon_url || game.title_image || '';

    // Platform badges
    const platforms = (game.title_platform || []).map(p =>
        `<span class="badge badge-ghost badge-xs font-bold">${e(p)}</span>`
    ).join('');

    // Region badges
    let regionHTML = '';
    if (game.is_regional && game.region?.length) {
        regionHTML = game.region.map(r =>
            `<span class="badge badge-xs ${REGION_COLORS[r] || 'badge-warning'} font-bold italic">${e(r)}</span>`
        ).join('');
    } else {
        regionHTML = `<span class="badge badge-xs badge-ghost">${GLOBE_SVG}</span>`;
    }

    // Trophy counts
    const trophies = game.defined_trophies || {};
    const trophyParts = [];
    if (trophies.platinum) trophyParts.push(`<span class="flex items-center gap-0.5 font-bold" style="color: var(--color-trophy-platinum);">${trophyCup('var(--color-trophy-platinum)')}${trophies.platinum}</span>`);
    if (trophies.gold) trophyParts.push(`<span class="flex items-center gap-0.5" style="color: var(--color-trophy-gold);">${trophyCup('var(--color-trophy-gold)')}${trophies.gold}</span>`);
    if (trophies.silver) trophyParts.push(`<span class="flex items-center gap-0.5" style="color: var(--color-trophy-silver);">${trophyCup('var(--color-trophy-silver)')}${trophies.silver}</span>`);
    if (trophies.bronze) trophyParts.push(`<span class="flex items-center gap-0.5" style="color: var(--color-trophy-bronze);">${trophyCup('var(--color-trophy-bronze)')}${trophies.bronze}</span>`);
    const trophyHTML = trophyParts.length
        ? `<div class="flex items-center gap-3 text-xs mt-1">${trophyParts.join('')}</div>`
        : '';

    // Player count + plat earners
    const played = (game.played_count || 0).toLocaleString();
    const platEarners = (game.plat_earners || 0).toLocaleString();

    // Community ratings
    const ratings = game.community_ratings || {};
    let ratingsHTML = '';
    if (ratings.count) {
        const items = [];
        if (ratings.difficulty != null) items.push(`<span title="Difficulty">Diff: ${ratings.difficulty}</span>`);
        if (ratings.grindiness != null) items.push(`<span title="Grindiness">Grind: ${ratings.grindiness}</span>`);
        if (ratings.fun != null) items.push(`<span title="Fun">Fun: ${ratings.fun}</span>`);
        if (ratings.overall != null) items.push(`<span title="Overall Rating">\u2605${ratings.overall}</span>`);
        if (ratings.hours != null) items.push(`<span title="Hours to Platinum">~${ratings.hours}h</span>`);
        if (items.length) {
            ratingsHTML = `
                <div class="flex flex-wrap items-center gap-2 text-xs text-base-content/50 mt-1">
                    ${items.join('<span class="text-base-content/20">\u00B7</span>')}
                    <span class="text-base-content/30">(${ratings.count} rating${ratings.count !== 1 ? 's' : ''})</span>
                </div>`;
        }
    }

    return `
        <div class="card bg-base-100 border-2 border-base-300 hover:border-primary/50 cursor-pointer transition-all shadow-sm hover:shadow-md az-game-result"
             data-game-id="${game.id}"
             data-game-name="${e(game.title_name)}"
             data-game-icon="${e(imgUrl)}"
             data-game-platform="${e(JSON.stringify(game.title_platform || []))}">
            <div class="card-body p-3 gap-1">
                <div class="flex gap-3">
                    <div class="w-16 h-16 lg:w-20 lg:h-20 rounded-lg overflow-hidden flex-shrink-0 border border-base-300 bg-base-200">
                        <img src="${e(imgUrl)}" alt=""
                             class="w-full h-full object-cover"
                             loading="lazy" />
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="font-semibold text-sm lg:text-base leading-tight line-clamp-2">${e(game.title_name)}</p>
                        <div class="flex flex-wrap items-center gap-1 mt-1">
                            ${platforms} ${regionHTML}
                        </div>
                        ${trophyHTML}
                        <div class="flex items-center gap-3 text-xs text-base-content/50 mt-1">
                            <span>${played} players</span>
                            <span>${platEarners} plats earned</span>
                        </div>
                        ${ratingsHTML}
                    </div>
                </div>
            </div>
        </div>`;
}


// ============================================================
// Setup Wizard
// ============================================================
const AZChallengeSetup = {
    challengeId: null,
    currentLetter: 'A',
    slots: {},          // { A: { game: { id, title_name, title_icon_url, title_platform }, is_completed }, ... }
    skippedLetters: [],
    _searchDebounced: null,
    _isSearching: false,
    _searchOffset: 0,
    _scrollObserver: null,

    init(challengeId, existingSlots) {
        this.challengeId = challengeId;
        this.slots = {};
        this.skippedLetters = [];
        this._searchOffset = 0;

        // Build slots map from server data
        for (const slot of existingSlots) {
            this.slots[slot.letter] = {
                game: slot.game,
                is_completed: slot.is_completed,
            };
        }

        // Find first unfilled letter to start
        this.currentLetter = this._findNextUnfilled('A') || 'A';

        this._bindSearch();
        this._bindChipFilters();
        this._bindSortSelect();
        this._initScrollObserver();
        this._bindLetterButtons();
        this._bindSkipButton();
        this._bindFinishButton();

        this._updateProgressBar();
        this._updateStats();
        this._goToLetter(this.currentLetter);
    },

    // ── Navigation ──────────────────────────────────────────

    _goToLetter(letter) {
        this.currentLetter = letter;

        // Update display
        const display = document.getElementById('current-letter-display');
        const promptLetter = document.getElementById('prompt-letter');
        if (display) display.textContent = letter;
        if (promptLetter) {
            promptLetter.textContent = letter;
        }

        // Update prompt text based on slot state
        const prompt = document.getElementById('current-letter-prompt');
        if (prompt) {
            const slot = this.slots[letter];
            if (slot?.game) {
                const e = PlatPursuit.HTMLUtils.escape;
                prompt.innerHTML = `Currently: <strong>${e(slot.game.title_name)}</strong> \u2014 search to swap`;
            } else {
                prompt.innerHTML = `Pick a game starting with <strong>${letter}</strong>`;
            }
        }

        // Clear search input but keep filters
        const input = document.getElementById('az-search-input');
        if (input) {
            input.value = '';
        }

        // Highlight current letter in progress bar
        document.querySelectorAll('.az-letter-btn').forEach(btn => {
            btn.classList.remove('ring-2', 'ring-primary', 'ring-offset-2', 'ring-offset-base-100', 'scale-110');
            if (btn.dataset.letter === letter) {
                btn.classList.add('ring-2', 'ring-primary', 'ring-offset-2', 'ring-offset-base-100', 'scale-110');
            }
        });

        this._checkFinishReady();

        // Auto-populate games for this letter
        this._doSearch(false);
    },

    _findNextUnfilled(startLetter) {
        const startIdx = LETTERS.indexOf(startLetter);
        // Search from startLetter to Z
        for (let i = startIdx; i < 26; i++) {
            const l = LETTERS[i];
            if (!this.slots[l]?.game && !this.slots[l]?.is_completed) return l;
        }
        // Wrap from A to startLetter
        for (let i = 0; i < startIdx; i++) {
            const l = LETTERS[i];
            if (!this.slots[l]?.game && !this.slots[l]?.is_completed) return l;
        }
        return null; // All filled
    },

    _nextLetter() {
        const currentIdx = LETTERS.indexOf(this.currentLetter);
        const nextUnfilled = this._findNextUnfilled(LETTERS[(currentIdx + 1) % 26]);

        if (nextUnfilled) {
            this._goToLetter(nextUnfilled);
        } else {
            this._checkFinishReady();
        }
    },

    // ── Search ──────────────────────────────────────────────

    _bindSearch() {
        const input = document.getElementById('az-search-input');
        if (!input) return;

        this._searchDebounced = PlatPursuit.debounce(() => {
            this._doSearch(false);
        }, 300);

        input.addEventListener('input', () => this._searchDebounced());

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                this._doSearch(false);
            }
        });
    },

    _bindChipFilters() {
        _bindChipFilters('.az-filter-chip', 'az-clear-filters', _sharedFilterState, () => {
            this._doSearch(false);
        });
    },

    _bindSortSelect() {
        const select = document.getElementById('az-sort-select');
        if (!select) return;

        select.addEventListener('change', () => {
            _sharedFilterState.sort = select.value;
            this._doSearch(false);
        });
    },

    _initScrollObserver() {
        const sentinel = document.getElementById('az-scroll-sentinel');
        if (!sentinel) return;

        this._scrollObserver = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && !this._isSearching) {
                this._doSearch(true);
            }
        });
    },

    _destroyScrollObserver() {
        if (this._scrollObserver) {
            this._scrollObserver.disconnect();
            this._scrollObserver = null;
        }
    },

    async _doSearch(append) {
        if (this._isSearching) return;
        this._isSearching = true;

        const input = document.getElementById('az-search-input');
        const query = input?.value?.trim() || '';
        const loading = document.getElementById('az-search-loading');
        const results = document.getElementById('az-search-results');
        const noResults = document.getElementById('az-no-results');
        const sentinel = document.getElementById('az-scroll-sentinel');

        if (!append) {
            this._searchOffset = 0;
            if (results) results.innerHTML = '';
            if (sentinel && this._scrollObserver) this._scrollObserver.unobserve(sentinel);
        }

        if (!append && loading) loading.classList.remove('hidden');
        if (noResults) noResults.classList.add('hidden');
        if (sentinel) sentinel.classList.add('hidden');

        try {
            let url = `/api/v1/challenges/az/game-search/?letter=${this.currentLetter}&challenge_id=${this.challengeId}&limit=${SEARCH_LIMIT}&offset=${this._searchOffset}`;
            if (query.length >= 2) url += `&q=${encodeURIComponent(query)}`;
            url += _buildFilterParams(_sharedFilterState);

            const data = await PlatPursuit.API.get(url);

            if (!append && !data.results.length) {
                if (results) results.innerHTML = '';
                if (noResults) noResults.classList.remove('hidden');
            } else {
                if (noResults) noResults.classList.add('hidden');
                if (results) {
                    const html = data.results.map(g => _renderGameCard(g)).join('');
                    if (append) {
                        results.insertAdjacentHTML('beforeend', html);
                    } else {
                        results.innerHTML = html;
                    }

                    // Bind click handlers on newly added cards
                    const selector = append ? '.az-game-result:not([data-bound])' : '.az-game-result';
                    results.querySelectorAll(selector).forEach(card => {
                        card.setAttribute('data-bound', '1');
                        card.addEventListener('click', () => {
                            this._selectGame(card.dataset.gameId, card.dataset.gameName, card);
                        });
                    });
                }

                // Infinite scroll: observe sentinel if more results exist
                if (sentinel && this._scrollObserver) {
                    if (data.has_more) {
                        sentinel.classList.remove('hidden');
                        this._scrollObserver.observe(sentinel);
                    } else {
                        sentinel.classList.add('hidden');
                        this._scrollObserver.unobserve(sentinel);
                    }
                }

                this._searchOffset += data.results.length;
            }
        } catch (err) {
            let msg = 'Search failed.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);
        } finally {
            if (loading) loading.classList.add('hidden');
            this._isSearching = false;
        }
    },

    // ── Selection ───────────────────────────────────────────

    async _selectGame(gameId, gameName, cardEl) {
        if (cardEl) {
            cardEl.classList.add('opacity-50', 'pointer-events-none');
        }

        try {
            const data = await PlatPursuit.API.post(
                `/api/v1/challenges/az/${this.challengeId}/slots/${this.currentLetter}/assign/`,
                { game_id: parseInt(gameId, 10) }
            );

            // Update local slot data
            this.slots[this.currentLetter] = {
                game: data.game,
                is_completed: data.is_completed,
            };

            const e = PlatPursuit.HTMLUtils.escape;
            PlatPursuit.ToastManager.success(`${e(gameName)} assigned to ${this.currentLetter}!`);

            this._updateProgressBar();
            this._updateStats();
            this._nextLetter();

        } catch (err) {
            let msg = 'Failed to assign game.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);

            if (cardEl) {
                cardEl.classList.remove('opacity-50', 'pointer-events-none');
            }
        }
    },

    // ── Progress Bar ────────────────────────────────────────

    _bindLetterButtons() {
        document.querySelectorAll('.az-letter-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const letter = btn.dataset.letter;
                if (!this.slots[letter]?.is_completed) {
                    this._goToLetter(letter);
                }
            });
        });
    },

    _updateProgressBar() {
        document.querySelectorAll('.az-letter-btn').forEach(btn => {
            const letter = btn.dataset.letter;
            const slot = this.slots[letter];

            btn.classList.remove(
                'bg-success', 'text-success-content', 'border-success',
                'bg-primary/20', 'text-primary', 'border-primary/40',
                'bg-base-200', 'text-base-content/40', 'border-base-300',
                'hover:border-primary/40'
            );

            if (slot?.is_completed) {
                btn.classList.add('bg-success', 'text-success-content', 'border-success');
            } else if (slot?.game) {
                btn.classList.add('bg-primary/20', 'text-primary', 'border-primary/40');
            } else {
                btn.classList.add('bg-base-200', 'text-base-content/40', 'border-base-300', 'hover:border-primary/40');
            }
        });
    },

    _updateStats() {
        let filled = 0;
        let completed = 0;
        for (const letter of LETTERS) {
            if (this.slots[letter]?.game) filled++;
            if (this.slots[letter]?.is_completed) completed++;
        }

        const filledEl = document.getElementById('az-filled-count');
        const completedEl = document.getElementById('az-completed-count');
        if (filledEl) filledEl.textContent = `${filled}/26 filled`;
        if (completedEl) completedEl.textContent = `${completed}/26 completed`;
    },

    // ── Skip & Finish ───────────────────────────────────────

    _bindSkipButton() {
        const btn = document.getElementById('az-skip-btn');
        if (!btn) return;

        btn.addEventListener('click', () => {
            if (!this.skippedLetters.includes(this.currentLetter)) {
                this.skippedLetters.push(this.currentLetter);
            }
            this._nextLetter();
        });
    },

    _bindFinishButton() {
        const btn = document.getElementById('az-finish-btn');
        if (!btn) return;

        btn.addEventListener('click', () => {
            window.location.href = `/challenges/az/${this.challengeId}/`;
        });
    },

    _checkFinishReady() {
        const btn = document.getElementById('az-finish-btn');
        const skipBtn = document.getElementById('az-skip-btn');
        if (!btn) return;

        const nextUnfilled = this._findNextUnfilled('A');

        if (!nextUnfilled) {
            btn.classList.remove('hidden');
            btn.innerHTML = `
                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 mr-1" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>
                View Challenge
            `;
            if (skipBtn) skipBtn.classList.add('hidden');
        } else {
            const allVisited = LETTERS.every(l =>
                this.slots[l]?.game || this.slots[l]?.is_completed || this.skippedLetters.includes(l)
            );
            if (allVisited) {
                btn.classList.remove('hidden');
                btn.innerHTML = `
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 mr-1" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>
                    Finish Setup
                `;
                if (skipBtn) skipBtn.classList.remove('hidden');
            } else {
                btn.classList.add('hidden');
                if (skipBtn) skipBtn.classList.remove('hidden');
            }
        }
    },
};

window.AZChallengeSetup = AZChallengeSetup;


// ============================================================
// Edit Page
// ============================================================
const AZChallengeEdit = {
    challengeId: null,
    slots: {},
    coverLetter: '',
    _currentEditLetter: null,
    _modalSearchOffset: 0,
    _isModalSearching: false,
    _modalChipsBound: false,
    _modalScrollObserver: null,

    init(challengeId, existingSlots, coverLetter) {
        this.challengeId = challengeId;
        this.slots = {};
        this.coverLetter = coverLetter || '';
        this._modalSearchOffset = 0;

        for (const slot of existingSlots) {
            this.slots[slot.letter] = {
                game: slot.game,
                is_completed: slot.is_completed,
            };
        }

        this._bindSlotActions();
        this._bindModalSearch();
        this._bindModalSortSelect();
        this._initModalScrollObserver();
        _bindInlineRename(this.challengeId);
    },

    // ── Slot Actions ────────────────────────────────────────

    _bindSlotActions() {
        const grid = document.getElementById('az-edit-grid');
        if (!grid) return;

        grid.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-action]');
            if (!btn) return;

            const letter = btn.dataset.letter;
            const action = btn.dataset.action;

            if (action === 'edit' || action === 'assign' || action === 'swap') {
                this._openModal(letter);
            } else if (action === 'clear') {
                this._clearSlot(letter);
            } else if (action === 'set-cover') {
                this._setCover(letter);
            }
        });
    },

    // ── Modal ───────────────────────────────────────────────

    _openModal(letter) {
        this._currentEditLetter = letter;
        this._modalSearchOffset = 0;

        const modal = document.getElementById('az-search-modal');
        const letterDisplay = document.getElementById('modal-letter');
        const input = document.getElementById('modal-search-input');
        const results = document.getElementById('modal-search-results');
        const noResults = document.getElementById('modal-no-results');
        const sentinel = document.getElementById('modal-scroll-sentinel');

        if (letterDisplay) letterDisplay.textContent = letter;
        if (input) input.value = '';
        if (results) results.innerHTML = '';
        if (noResults) noResults.classList.add('hidden');
        // Re-append sentinel to results container (innerHTML cleared it)
        if (sentinel && results) {
            sentinel.classList.add('hidden');
            results.appendChild(sentinel);
        }

        // Bind chip filters once (they persist across modal opens)
        if (!this._modalChipsBound) {
            _bindChipFilters('.modal-filter-chip', 'modal-clear-filters', _sharedFilterState, () => {
                this._doModalSearch(false);
            });
            this._modalChipsBound = true;
        }

        // Sync sort dropdown with shared state
        const sortSelect = document.getElementById('modal-sort-select');
        if (sortSelect) sortSelect.value = _sharedFilterState.sort;

        if (modal) modal.showModal();

        // Auto-populate after modal is visible
        setTimeout(() => {
            if (input) input.focus();
            this._doModalSearch(false);
        }, 100);
    },

    _bindModalSearch() {
        const input = document.getElementById('modal-search-input');
        if (!input) return;

        const debouncedSearch = PlatPursuit.debounce(() => {
            this._doModalSearch(false);
        }, 300);

        input.addEventListener('input', () => debouncedSearch());
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                this._doModalSearch(false);
            }
        });
    },

    _bindModalSortSelect() {
        const select = document.getElementById('modal-sort-select');
        if (!select) return;

        select.addEventListener('change', () => {
            _sharedFilterState.sort = select.value;
            // Also sync the setup sort select if it exists
            const setupSort = document.getElementById('az-sort-select');
            if (setupSort) setupSort.value = select.value;
            this._doModalSearch(false);
        });
    },

    _initModalScrollObserver() {
        const sentinel = document.getElementById('modal-scroll-sentinel');
        const scrollContainer = document.getElementById('modal-search-results');
        if (!sentinel || !scrollContainer) return;

        this._modalScrollObserver = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && !this._isModalSearching) {
                this._doModalSearch(true);
            }
        }, { root: scrollContainer });
    },

    async _doModalSearch(append) {
        const letter = this._currentEditLetter;
        if (!letter || this._isModalSearching) return;
        this._isModalSearching = true;

        const query = document.getElementById('modal-search-input')?.value?.trim() || '';
        const loading = document.getElementById('modal-search-loading');
        const results = document.getElementById('modal-search-results');
        const noResults = document.getElementById('modal-no-results');
        const sentinel = document.getElementById('modal-scroll-sentinel');

        if (!append) {
            this._modalSearchOffset = 0;
            if (results) results.innerHTML = '';
            // Re-append sentinel (innerHTML cleared it)
            if (sentinel && results) results.appendChild(sentinel);
            if (sentinel && this._modalScrollObserver) this._modalScrollObserver.unobserve(sentinel);
        }

        if (!append && loading) loading.classList.remove('hidden');
        if (noResults) noResults.classList.add('hidden');
        if (sentinel) sentinel.classList.add('hidden');

        try {
            let url = `/api/v1/challenges/az/game-search/?letter=${letter}&challenge_id=${this.challengeId}&limit=${SEARCH_LIMIT}&offset=${this._modalSearchOffset}`;
            if (query.length >= 2) url += `&q=${encodeURIComponent(query)}`;
            url += _buildFilterParams(_sharedFilterState);

            const data = await PlatPursuit.API.get(url);

            if (!append && !data.results.length) {
                if (results) results.innerHTML = '';
                // Re-append sentinel (innerHTML cleared it)
                if (sentinel && results) results.appendChild(sentinel);
                if (noResults) noResults.classList.remove('hidden');
            } else {
                if (noResults) noResults.classList.add('hidden');
                if (results) {
                    const html = data.results.map(g => _renderGameCard(g)).join('');
                    if (append) {
                        // Insert before sentinel so it stays at the bottom
                        sentinel?.insertAdjacentHTML('beforebegin', html);
                    } else {
                        // Insert before sentinel (which was re-appended above)
                        sentinel?.insertAdjacentHTML('beforebegin', html);
                    }

                    const selector = append ? '.az-game-result:not([data-bound])' : '.az-game-result';
                    results.querySelectorAll(selector).forEach(card => {
                        card.setAttribute('data-bound', '1');
                        card.addEventListener('click', () => {
                            this._assignFromModal(
                                card.dataset.gameId,
                                card.dataset.gameName,
                                card
                            );
                        });
                    });
                }

                // Infinite scroll: observe sentinel if more results exist
                if (sentinel && this._modalScrollObserver) {
                    if (data.has_more) {
                        sentinel.classList.remove('hidden');
                        this._modalScrollObserver.observe(sentinel);
                    } else {
                        sentinel.classList.add('hidden');
                        this._modalScrollObserver.unobserve(sentinel);
                    }
                }

                this._modalSearchOffset += data.results.length;
            }
        } catch (err) {
            let msg = 'Search failed.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);
        } finally {
            if (loading) loading.classList.add('hidden');
            this._isModalSearching = false;
        }
    },

    async _assignFromModal(gameId, gameName, cardEl) {
        const letter = this._currentEditLetter;
        if (!letter) return;

        if (cardEl) cardEl.classList.add('opacity-50', 'pointer-events-none');

        try {
            const data = await PlatPursuit.API.post(
                `/api/v1/challenges/az/${this.challengeId}/slots/${letter}/assign/`,
                { game_id: parseInt(gameId, 10) }
            );

            this.slots[letter] = {
                game: data.game,
                is_completed: data.is_completed,
            };

            // Update cover if the API auto-set it
            if (data.cover_letter !== undefined) {
                const oldCover = this.coverLetter;
                this.coverLetter = data.cover_letter;
                if (oldCover && oldCover !== data.cover_letter) {
                    this._updateCoverIndicator(oldCover);
                    this._updateSlotOverlay(oldCover);
                }
            }

            this._updateSlotCard(letter);

            const modal = document.getElementById('az-search-modal');
            if (modal) modal.close();

            const e = PlatPursuit.HTMLUtils.escape;
            PlatPursuit.ToastManager.success(`${e(gameName)} assigned to ${letter}!`);

        } catch (err) {
            let msg = 'Failed to assign game.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);

            if (cardEl) cardEl.classList.remove('opacity-50', 'pointer-events-none');
        }
    },

    // ── Clear Slot ──────────────────────────────────────────

    async _clearSlot(letter) {
        if (!confirm(`Clear the game from slot ${letter}?`)) return;

        try {
            const data = await PlatPursuit.API.request(
                `/api/v1/challenges/az/${this.challengeId}/slots/${letter}/clear/`,
                { method: 'DELETE' }
            );

            // Update cover if the API re-picked
            if (data.cover_letter !== undefined) {
                const oldCover = this.coverLetter;
                this.coverLetter = data.cover_letter;
                if (oldCover && oldCover !== data.cover_letter) {
                    this._updateCoverIndicator(oldCover);
                    this._updateSlotOverlay(oldCover);
                }
                // Update the new cover slot
                if (data.cover_letter) {
                    this._updateCoverIndicator(data.cover_letter);
                    this._updateSlotOverlay(data.cover_letter);
                }
            }

            this.slots[letter] = { game: null, is_completed: false };
            this._updateSlotCard(letter);

            PlatPursuit.ToastManager.success(`Slot ${letter} cleared.`);

        } catch (err) {
            let msg = 'Failed to clear slot.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);
        }
    },

    // ── Set Cover ────────────────────────────────────────────

    async _setCover(letter) {
        try {
            await PlatPursuit.API.patch(
                `/api/v1/challenges/az/${this.challengeId}/update/`,
                { cover_letter: letter }
            );

            const oldCover = this.coverLetter;
            this.coverLetter = letter;

            // Update the old cover slot's overlay (remove "is cover" state)
            if (oldCover && oldCover !== letter) {
                this._updateCoverIndicator(oldCover);
                this._updateSlotOverlay(oldCover);
            }
            // Update the new cover slot
            this._updateCoverIndicator(letter);
            this._updateSlotOverlay(letter);

            PlatPursuit.ToastManager.success(`Cover image set to ${letter}!`);
        } catch (err) {
            let msg = 'Failed to set cover.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);
        }
    },

    _updateCoverIndicator(letter) {
        const container = document.querySelector(`[data-letter="${letter}"][data-slot-id]`);
        if (!container) return;

        // Remove existing cover indicator for this slot
        const existing = container.querySelector('.cover-indicator');
        if (existing) existing.remove();

        // Add indicator if this is the current cover
        if (letter === this.coverLetter) {
            container.insertAdjacentHTML('afterbegin', `
                <div class="absolute top-1 left-1 z-10 cover-indicator" data-letter="${letter}">
                    <div class="badge badge-primary badge-sm gap-0.5" title="Cover image">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="currentColor"><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>
                    </div>
                </div>`);
        }
    },

    _updateSlotOverlay(letter) {
        const container = document.querySelector(`[data-letter="${letter}"][data-slot-id]`);
        if (!container) return;

        const slot = this.slots[letter];
        if (!slot || slot.is_completed) return;

        // Remove existing overlay
        const existingOverlay = container.querySelector('.absolute.inset-0');
        if (existingOverlay) existingOverlay.remove();

        const isCover = (letter === this.coverLetter);

        let overlayHTML;
        if (slot.game) {
            const setCoverBtn = !isCover
                ? `<button class="btn btn-xs btn-ghost text-base-content/70" data-action="set-cover" data-letter="${letter}">
                       <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="currentColor"><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>
                       Set as Cover
                   </button>`
                : '';
            overlayHTML = `
                <div class="absolute inset-0 bg-base-300/80 opacity-0 group-hover:opacity-100 transition-opacity rounded-box flex flex-col items-center justify-center gap-1 cursor-pointer"
                     data-action="edit" data-letter="${letter}">
                    <div class="flex gap-1">
                        <button class="btn btn-xs btn-primary" data-action="swap" data-letter="${letter}">Swap</button>
                        <button class="btn btn-xs btn-error btn-outline" data-action="clear" data-letter="${letter}">Clear</button>
                    </div>
                    ${setCoverBtn}
                </div>`;
        } else {
            overlayHTML = `
                <div class="absolute inset-0 bg-base-300/80 opacity-0 group-hover:opacity-100 transition-opacity rounded-box flex flex-col items-center justify-center gap-1 cursor-pointer"
                     data-action="edit" data-letter="${letter}">
                    <button class="btn btn-xs btn-primary" data-action="assign" data-letter="${letter}">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3 mr-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
                        Assign
                    </button>
                </div>`;
        }

        container.insertAdjacentHTML('beforeend', overlayHTML);
    },

    // ── DOM Updates ─────────────────────────────────────────

    _updateSlotCard(letter) {
        const container = document.querySelector(`[data-letter="${letter}"][data-slot-id]`);
        if (!container) return;

        const slot = this.slots[letter];
        const e = PlatPursuit.HTMLUtils.escape;

        const cardDiv = container.querySelector('.card');
        if (!cardDiv) return;

        // Update border color
        cardDiv.classList.remove('border-success/50', 'border-primary/30');
        if (slot.is_completed) {
            cardDiv.classList.add('border-success/50');
        } else if (slot.game) {
            cardDiv.classList.add('border-primary/30');
        }

        // Update card body
        const body = cardDiv.querySelector('.card-body');
        if (!body) return;

        // Remove existing completion badge and cover indicator
        const existingBadge = cardDiv.querySelector('.absolute.top-1.right-1');
        if (existingBadge) existingBadge.remove();
        const existingCover = container.querySelector('.cover-indicator');
        if (existingCover) existingCover.remove();

        if (slot.is_completed) {
            cardDiv.insertAdjacentHTML('afterbegin', `
                <div class="absolute top-1 right-1 z-10">
                    <div class="badge badge-success badge-sm gap-1">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>
                    </div>
                </div>`);
        }

        const letterClass = slot.is_completed ? 'text-success' : (slot.game ? 'text-primary' : 'text-base-content/30');

        if (slot.game) {
            const imageUrl = slot.game.title_icon_url || slot.game.title_image || '';
            const platforms = (slot.game.title_platform || []).map(p =>
                `<span class="badge badge-xs badge-ghost font-mono">${e(p)}</span>`
            ).join('');

            body.innerHTML = `
                <div class="text-xl lg:text-2xl font-black ${letterClass}">${letter}</div>
                <div class="w-full aspect-square rounded-lg overflow-hidden bg-base-200">
                    <img src="${e(imageUrl)}" alt="${e(slot.game.title_name)}"
                         class="w-full h-full object-cover" loading="lazy" />
                </div>
                <p class="text-xs font-medium leading-tight line-clamp-2 min-h-[2rem]">${e(slot.game.title_name)}</p>
                <div class="flex flex-wrap gap-0.5 justify-center">${platforms}</div>
            `;
        } else {
            body.innerHTML = `
                <div class="text-xl lg:text-2xl font-black ${letterClass}">${letter}</div>
                <div class="w-full aspect-square rounded-lg bg-base-200/50 flex items-center justify-center">
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-8 h-8 text-base-content/15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10"/>
                        <path d="M8 12h8M12 8v8"/>
                    </svg>
                </div>
                <p class="text-xs text-base-content/30 italic min-h-[2rem]">Not assigned</p>
            `;
        }

        // Update cover indicator and edit overlay
        this._updateCoverIndicator(letter);
        this._updateSlotOverlay(letter);

        this._updateProgressHeader();
    },

    _updateProgressHeader() {
        let filled = 0;
        let completed = 0;
        for (const letter of LETTERS) {
            if (this.slots[letter]?.game) filled++;
            if (this.slots[letter]?.is_completed) completed++;
        }

        const progressBar = document.querySelector('progress.progress-primary');
        if (progressBar) progressBar.value = completed;

        const headerSection = document.querySelector('.card-body');
        if (headerSection) {
            const spans = headerSection.querySelectorAll('.flex.justify-between span');
            if (spans.length >= 2) {
                spans[0].textContent = `${completed}/26 Platinums`;
                spans[1].textContent = `${filled}/26 filled`;
            }
        }
    },
};

window.AZChallengeEdit = AZChallengeEdit;


// ============================================================
// Detail Page
// ============================================================
const AZChallengeDetail = {
    challengeId: null,

    init(challengeId) {
        this.challengeId = challengeId;
        this._bindShareButton();
        this._bindShareImageButton();
        this._bindPickNextGame();
        _bindInlineRename(this.challengeId, (newName) => {
            const shareBtn = document.getElementById('share-image-btn');
            const bannerBtn = document.getElementById('banner-share-image-btn');
            const modalTitle = document.getElementById('az-share-modal-title');
            if (shareBtn) shareBtn.dataset.challengeName = newName;
            if (bannerBtn) bannerBtn.dataset.challengeName = newName;
            if (modalTitle) modalTitle.textContent = `Share: ${newName}`;
        });
    },

    _bindShareButton() {
        const btn = document.getElementById('share-btn');
        if (!btn) return;

        btn.addEventListener('click', async () => {
            const url = btn.dataset.url || window.location.href;

            try {
                if (navigator.share) {
                    await navigator.share({
                        title: document.title,
                        url: url,
                    });
                } else {
                    await navigator.clipboard.writeText(url);
                    PlatPursuit.ToastManager.success('Link copied to clipboard!');
                }
            } catch (err) {
                if (err.name !== 'AbortError') {
                    try {
                        await navigator.clipboard.writeText(url);
                        PlatPursuit.ToastManager.success('Link copied to clipboard!');
                    } catch {
                        PlatPursuit.ToastManager.error('Failed to copy link.');
                    }
                }
            }
        });
    },

    _bindShareImageButton() {
        if (typeof AZChallengeShareManager === 'undefined') return;

        const openShareModal = (challengeName) => {
            const modal = document.getElementById('az-share-modal');
            const content = document.getElementById('az-share-modal-content');
            if (!modal || !content) return;

            const manager = new AZChallengeShareManager(this.challengeId, challengeName);
            content.innerHTML = manager.renderShareSection();
            manager.init();

            modal.showModal();
        };

        // Header "Share Image" button
        const btn = document.getElementById('share-image-btn');
        if (btn) {
            btn.addEventListener('click', () => openShareModal(btn.dataset.challengeName || ''));
        }

        // CTA banner "Create Share Card" button
        const bannerBtn = document.getElementById('banner-share-image-btn');
        if (bannerBtn) {
            bannerBtn.addEventListener('click', () => openShareModal(bannerBtn.dataset.challengeName || ''));
        }
    },

    // ─── Pick My Next Game Spinner ──────────────────────────────────────────

    _TILE_WIDTH: 104, // 96px tile (w-24) + 8px gap (gap-2)

    _spinnerSlots: [],
    _reelItems: [],
    _winner: null,
    _winnerTileIndex: -1,
    _finalTranslateX: 0,
    _isSpinning: false,
    _animFrameId: null,
    _lastWinnerLetter: null,
    _resultTimeoutId: null,
    _resultInnerTimeoutId: null,

    // Audio
    _audioCtx: null,
    _audioGain: null,
    _audioMuted: false,
    _noiseBuffer: null,
    _lastTickTime: 0,
    _lastTickTileIdx: -1,

    _DICE_SVG: `<svg xmlns="http://www.w3.org/2000/svg" class="w-full h-full" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="2"/><circle cx="8" cy="8" r="1.5" fill="currentColor"/><circle cx="16" cy="8" r="1.5" fill="currentColor"/><circle cx="8" cy="16" r="1.5" fill="currentColor"/><circle cx="16" cy="16" r="1.5" fill="currentColor"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/></svg>`,

    _CAMERA_SVG: `<svg xmlns="http://www.w3.org/2000/svg" class="w-full h-full" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>`,

    _FLAVOR_TEXTS: [
        'Your next platinum awaits!',
        'The hunt begins here.',
        'Destiny has spoken. Time to earn that plat.',
        'The trophies are calling. Will you answer?',
        'This one has your name on it.',
        'No trophy can hide from you.',
        'Lock in. This plat is yours.',
        'The pursuit continues.',
    ],

    _bindPickNextGame() {
        const btn = document.getElementById('pick-next-game-btn');
        if (!btn) return;

        this._spinnerSlots = window._SPINNER_SLOTS || [];

        if (this._spinnerSlots.length < 2) {
            btn.disabled = true;
            btn.classList.add('btn-disabled');
            btn.title = this._spinnerSlots.length === 0
                ? 'No assigned games to pick from'
                : 'Need at least 2 assigned games to spin';
            return;
        }

        btn.addEventListener('click', () => this._openSpinnerModal());
    },

    _openSpinnerModal() {
        if (!document.getElementById('pick-next-game-modal')) {
            document.body.insertAdjacentHTML('beforeend', this._buildSpinnerModalHTML());
            this._bindSpinnerEvents();

            // Set initial mute icon from localStorage
            this._audioMuted = localStorage.getItem('pp_spinner_muted') === '1';
            this._updateMuteIcon();
        }

        this._resetSpinner();
        document.getElementById('pick-next-game-modal').showModal();
    },

    _buildSpinnerModalHTML() {
        return `
            <dialog id="pick-next-game-modal" class="modal">
                <div class="modal-box max-w-2xl bg-base-200 p-0 overflow-hidden relative">
                    <canvas id="spinner-confetti-canvas" class="absolute inset-0 w-full h-full pointer-events-none z-50"></canvas>
                    <div class="bg-base-300/50 px-6 py-4 flex items-center justify-between">
                        <h3 class="font-bold text-lg flex items-center gap-2">
                            <span class="inline-flex w-5 h-5 text-secondary">${this._DICE_SVG}</span>
                            Pick My Next Game!
                        </h3>
                        <div class="flex items-center gap-1">
                            <button class="btn btn-sm btn-circle btn-ghost" id="spinner-mute-btn" title="Toggle sound">
                                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" id="spinner-mute-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                                    <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
                                    <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
                                </svg>
                            </button>
                            <button class="btn btn-sm btn-circle btn-ghost" id="spinner-close-btn">&#x2715;</button>
                        </div>
                    </div>

                    <div class="relative px-6 py-8" id="spinner-area">
                        <div class="absolute top-0 bottom-0 left-1/2 -translate-x-1/2 z-20 flex flex-col items-center pointer-events-none">
                            <div class="w-0 h-0 border-l-[10px] border-r-[10px] border-t-[14px]
                                        border-l-transparent border-r-transparent border-t-secondary
                                        drop-shadow-lg"></div>
                            <div class="w-0.5 flex-1 bg-secondary/30"></div>
                            <div class="w-0 h-0 border-l-[10px] border-r-[10px] border-b-[14px]
                                        border-l-transparent border-r-transparent border-b-secondary
                                        drop-shadow-lg"></div>
                        </div>

                        <div class="overflow-x-hidden overflow-y-visible rounded-lg py-2" id="spinner-viewport">
                            <div class="flex gap-2" id="spinner-strip" style="will-change: transform;"></div>
                        </div>

                        <div class="absolute left-6 top-8 bottom-8 w-16 bg-gradient-to-r from-base-200 to-transparent z-10 pointer-events-none rounded-l-lg"></div>
                        <div class="absolute right-6 top-8 bottom-8 w-16 bg-gradient-to-l from-base-200 to-transparent z-10 pointer-events-none rounded-r-lg"></div>
                    </div>

                    <div id="spinner-result" class="hidden px-6 py-6">
                        <div class="flex flex-col items-center gap-3">
                            <div class="w-24 h-24 rounded-xl overflow-hidden border-2 border-secondary shadow-lg shadow-secondary/30"
                                 id="spinner-result-icon"></div>
                            <div class="text-center">
                                <span class="badge badge-secondary badge-lg font-bold mb-1"
                                      id="spinner-result-letter"></span>
                                <h4 class="text-xl font-bold" id="spinner-result-name"></h4>
                                <p class="text-sm text-base-content/60 mt-1"
                                   id="spinner-result-progress"></p>
                                <p class="text-secondary font-medium mt-2 italic pr-1"
                                   id="spinner-result-flavor"></p>
                            </div>
                        </div>
                    </div>

                    <div class="px-6 pb-6 flex flex-col items-center gap-3">
                        <div class="flex gap-3">
                            <button class="btn btn-secondary btn-sm" id="spinner-spin-btn">
                                Spin!
                            </button>
                            <button class="btn btn-ghost btn-sm hidden" id="spinner-again-btn">
                                Spin Again
                            </button>
                        </div>
                        <button class="btn btn-ghost btn-xs hidden gap-1 text-base-content/50" id="spinner-set-cover-btn">
                            <span class="inline-flex w-3.5 h-3.5">${this._CAMERA_SVG}</span>
                            Set as Display Cover
                        </button>
                    </div>
                </div>
                <form method="dialog" class="modal-backdrop"><button>close</button></form>
            </dialog>
        `;
    },

    _bindSpinnerEvents() {
        document.getElementById('spinner-spin-btn').addEventListener('click', () => {
            this._startSpin();
        });

        document.getElementById('spinner-again-btn').addEventListener('click', () => {
            this._resetSpinner();
            this._startSpin();
        });

        document.getElementById('spinner-close-btn').addEventListener('click', () => {
            document.getElementById('pick-next-game-modal').close();
        });

        document.getElementById('spinner-set-cover-btn').addEventListener('click', () => {
            this._setWinnerAsCover();
        });

        document.getElementById('spinner-mute-btn').addEventListener('click', () => {
            this._toggleMute();
        });

        document.getElementById('pick-next-game-modal').addEventListener('close', () => {
            if (this._isSpinning) {
                this._isSpinning = false;
                if (this._animFrameId) {
                    cancelAnimationFrame(this._animFrameId);
                    this._animFrameId = null;
                }
            }

            // Cancel any pending result timeouts to prevent stale DOM manipulation
            if (this._resultTimeoutId) {
                clearTimeout(this._resultTimeoutId);
                this._resultTimeoutId = null;
            }
            if (this._resultInnerTimeoutId) {
                clearTimeout(this._resultInnerTimeoutId);
                this._resultInnerTimeoutId = null;
            }

            // Highlight winning slot on the grid
            if (this._lastWinnerLetter) {
                const gridSlot = document.querySelector(
                    `[data-letter="${this._lastWinnerLetter}"][data-slot-id]`
                );
                if (gridSlot) {
                    gridSlot.classList.add('spinner-grid-highlight');
                    setTimeout(() => gridSlot.classList.remove('spinner-grid-highlight'), 2500);
                }
            }
        });
    },

    _buildReelStrip() {
        const strip = document.getElementById('spinner-strip');
        strip.innerHTML = '';
        strip.style.transform = 'translateX(0)';

        const e = PlatPursuit.HTMLUtils.escape;
        const slots = this._spinnerSlots;

        const repeatCount = Math.max(Math.ceil(70 / slots.length), 8);
        this._reelItems = [];

        for (let i = 0; i < repeatCount; i++) {
            const shuffled = [...slots].sort(() => Math.random() - 0.5);
            for (const slot of shuffled) {
                this._reelItems.push(slot);
                strip.insertAdjacentHTML('beforeend', `
                    <div class="spinner-tile flex-shrink-0 w-24 h-32 rounded-lg border-2
                                border-base-300 bg-base-100 flex flex-col items-center
                                justify-center gap-1 p-1.5 transition-all duration-300">
                        <span class="text-xs font-black text-secondary">${e(slot.letter)}</span>
                        <img src="${e(slot.game_icon)}" alt=""
                             class="w-16 h-16 rounded object-cover" />
                        <span class="text-[0.6rem] text-base-content/50 leading-tight
                                     line-clamp-2 text-center w-full">${e(slot.game_name)}</span>
                    </div>
                `);
            }
        }
    },

    _pickWinner() {
        const idx = Math.floor(Math.random() * this._spinnerSlots.length);
        this._winner = this._spinnerSlots[idx];

        // Collect ALL tile indices matching the winner
        const candidates = [];
        for (let i = 0; i < this._reelItems.length; i++) {
            if (this._reelItems[i].letter === this._winner.letter) {
                candidates.push(i);
            }
        }

        // Filter to tiles past 30% of the reel (ensures decent scroll distance)
        const minIndex = Math.floor(this._reelItems.length * 0.3);
        const viable = candidates.filter(i => i >= minIndex);

        // Pick randomly from viable candidates (fallback to all candidates)
        const pool = viable.length > 0 ? viable : candidates;
        this._winnerTileIndex = pool[Math.floor(Math.random() * pool.length)];
    },

    _calculateFinalPosition() {
        const viewport = document.getElementById('spinner-viewport');
        const viewportWidth = viewport.offsetWidth;
        const tw = this._TILE_WIDTH;

        const tileCenter = (this._winnerTileIndex * tw) + (tw / 2);
        const viewportCenter = viewportWidth / 2;

        // Small random jitter so it doesn't always land dead-center
        const jitter = (Math.random() - 0.5) * (tw * 0.3);

        this._finalTranslateX = -(tileCenter - viewportCenter + jitter);
    },

    _startSpin() {
        const spinBtn = document.getElementById('spinner-spin-btn');
        const againBtn = document.getElementById('spinner-again-btn');
        spinBtn.disabled = true;
        spinBtn.innerHTML = '<span class="loading loading-spinner loading-xs"></span>';
        againBtn.classList.add('hidden');

        this._isSpinning = true;
        this._lastWinnerLetter = null;

        // Show spinner area, hide result
        document.getElementById('spinner-area').classList.remove('hidden');
        document.getElementById('spinner-result').classList.add('hidden');

        this._buildReelStrip();
        this._pickWinner();
        this._calculateFinalPosition();

        const strip = document.getElementById('spinner-strip');
        const endX = this._finalTranslateX;
        const totalDistance = Math.abs(endX);

        // Duration scales with distance, clamped between 3-6 seconds
        const duration = Math.min(Math.max(totalDistance * 1.2, 3000), 6000);
        const startTime = performance.now();

        this._onSpinStart();

        const animate = (now) => {
            if (!this._isSpinning) return;

            const elapsed = now - startTime;
            const progress = Math.min(elapsed / duration, 1);

            // Cubic ease-out: fast start, smooth deceleration
            const eased = 1 - Math.pow(1 - progress, 3);
            const currentX = endX * eased;
            strip.style.transform = `translateX(${currentX}px)`;

            this._onTick(progress);

            if (progress < 1) {
                this._animFrameId = requestAnimationFrame(animate);
            } else {
                this._onLandResult();
            }
        };

        this._animFrameId = requestAnimationFrame(animate);
    },

    _onLandResult() {
        this._isSpinning = false;
        this._lastWinnerLetter = this._winner.letter;

        // Play reveal fanfare
        this._playRevealFanfare();

        // Highlight the winning tile
        const tiles = document.querySelectorAll('#spinner-strip .spinner-tile');
        const winningTile = tiles[this._winnerTileIndex];
        if (winningTile) {
            winningTile.classList.add(
                'border-secondary', 'scale-110', 'spinner-winner-glow'
            );
        }

        // Brief pause, then show result
        this._resultTimeoutId = setTimeout(() => {
            this._resultTimeoutId = null;
            this._showResult();
        }, 600);
    },

    _showResult() {
        // Guard: skip if modal was closed before this fired
        const modal = document.getElementById('pick-next-game-modal');
        if (!modal || !modal.open) return;

        const e = PlatPursuit.HTMLUtils.escape;
        const w = this._winner;
        const spinnerArea = document.getElementById('spinner-area');

        // Populate result card while still hidden
        document.getElementById('spinner-result-icon').innerHTML =
            `<img src="${e(w.game_icon)}" alt="" class="w-full h-full object-cover" />`;
        document.getElementById('spinner-result-letter').textContent = w.letter;
        document.getElementById('spinner-result-name').textContent = w.game_name;

        const progressText = w.progress > 0
            ? `${w.progress}% complete`
            : 'Not started yet';
        document.getElementById('spinner-result-progress').textContent = progressText;

        const flavor = this._FLAVOR_TEXTS[Math.floor(Math.random() * this._FLAVOR_TEXTS.length)];
        document.getElementById('spinner-result-flavor').textContent = flavor;

        // Phase 1: Fade out the reel (opacity only, no layout shift)
        spinnerArea.classList.add('animate-spinner-fade-out');

        // Phase 2: Once fade completes, swap to result card
        this._resultInnerTimeoutId = setTimeout(() => {
            this._resultInnerTimeoutId = null;

            // Guard: skip if modal was closed during fade
            const innerModal = document.getElementById('pick-next-game-modal');
            if (!innerModal || !innerModal.open) return;

            spinnerArea.classList.add('hidden');
            spinnerArea.classList.remove('animate-spinner-fade-out');

            // Reveal result card
            const resultEl = document.getElementById('spinner-result');
            resultEl.classList.remove('hidden');
            resultEl.classList.add('animate-spinner-fade-in');

            // Update buttons
            document.getElementById('spinner-spin-btn').classList.add('hidden');
            document.getElementById('spinner-again-btn').classList.remove('hidden');

            // Show "Set as Display Cover" button
            const coverBtn = document.getElementById('spinner-set-cover-btn');
            if (coverBtn) {
                coverBtn.classList.remove('hidden', 'text-success');
                coverBtn.classList.add('text-base-content/50');
                coverBtn.disabled = false;
                coverBtn.innerHTML = `
                    <span class="inline-flex w-3.5 h-3.5">${this._CAMERA_SVG}</span>
                    Set as Display Cover
                `;
            }

            // Fire confetti + pop sound
            this._playConfettiPop();
            if (PlatPursuit.CelebrationManager) {
                PlatPursuit.CelebrationManager.loadConfetti().then(() => {
                    if (typeof confetti !== 'function') return;
                    const canvas = document.getElementById('spinner-confetti-canvas');
                    if (!canvas) return;
                    const myConfetti = confetti.create(canvas, { resize: true });
                    myConfetti({
                        particleCount: 80,
                        spread: 70,
                        origin: { x: 0.5, y: 0.5 },
                        colors: ['#67d1f8', '#f472b6', '#a855f7', '#3b82f6', '#22c55e', '#eab308'],
                        disableForReducedMotion: true,
                    });
                }).catch(() => {});
            }
        }, 400);
    },

    async _setWinnerAsCover() {
        const btn = document.getElementById('spinner-set-cover-btn');
        if (!btn || !this._winner) return;

        btn.disabled = true;
        btn.innerHTML = '<span class="loading loading-spinner loading-xs"></span>';

        try {
            await PlatPursuit.API.patch(
                `/api/v1/challenges/az/${this.challengeId}/update/`,
                { cover_letter: this._winner.letter }
            );
            btn.innerHTML = `
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>
                Display Cover Set!
            `;
            btn.classList.remove('text-base-content/50');
            btn.classList.add('text-success');
            PlatPursuit.ToastManager.success(`Display cover set to ${this._winner.game_name}`);
        } catch (error) {
            btn.disabled = false;
            btn.innerHTML = `
                <span class="inline-flex w-3.5 h-3.5">${this._CAMERA_SVG}</span>
                Set as Display Cover
            `;
            let msg = 'Failed to set display cover.';
            try { const errData = await error.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);
        }
    },

    _resetSpinner() {
        this._isSpinning = false;
        if (this._animFrameId) {
            cancelAnimationFrame(this._animFrameId);
            this._animFrameId = null;
        }
        if (this._resultTimeoutId) {
            clearTimeout(this._resultTimeoutId);
            this._resultTimeoutId = null;
        }
        if (this._resultInnerTimeoutId) {
            clearTimeout(this._resultInnerTimeoutId);
            this._resultInnerTimeoutId = null;
        }

        const result = document.getElementById('spinner-result');
        if (result) {
            result.classList.add('hidden');
            result.classList.remove('animate-spinner-fade-in');
        }

        // Show spinner area (clear both hidden and any mid-fade state)
        const spinnerArea = document.getElementById('spinner-area');
        if (spinnerArea) {
            spinnerArea.classList.remove('hidden', 'animate-spinner-fade-out');
        }

        const spinBtn = document.getElementById('spinner-spin-btn');
        if (spinBtn) {
            spinBtn.classList.remove('hidden');
            spinBtn.disabled = false;
            spinBtn.textContent = 'Spin!';
        }

        const againBtn = document.getElementById('spinner-again-btn');
        if (againBtn) againBtn.classList.add('hidden');

        const coverBtn = document.getElementById('spinner-set-cover-btn');
        if (coverBtn) {
            coverBtn.classList.add('hidden');
            coverBtn.classList.remove('text-success');
            coverBtn.classList.add('text-base-content/50');
            coverBtn.disabled = false;
            coverBtn.innerHTML = `
                <span class="inline-flex w-3.5 h-3.5">${this._CAMERA_SVG}</span>
                Set as Display Cover
            `;
        }

        this._buildReelStrip();
    },

    // ─── Audio System ─────────────────────────────────────────────────────

    _SPEAKER_ON_SVG: `
        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
            <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
            <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
        </svg>
    `,
    _SPEAKER_OFF_SVG: `
        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
            <line x1="23" y1="9" x2="17" y2="15"/>
            <line x1="17" y1="9" x2="23" y2="15"/>
        </svg>
    `,

    _initAudio() {
        if (this._audioCtx) return;

        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;

        this._audioCtx = new AudioCtx();
        if (this._audioCtx.state === 'suspended') this._audioCtx.resume();

        this._audioGain = this._audioCtx.createGain();
        this._audioGain.gain.value = 0.5;
        this._audioGain.connect(this._audioCtx.destination);

        // Read mute preference
        this._audioMuted = localStorage.getItem('pp_spinner_muted') === '1';
        this._updateMuteIcon();
    },

    _toggleMute() {
        this._audioMuted = !this._audioMuted;
        localStorage.setItem('pp_spinner_muted', this._audioMuted ? '1' : '0');
        this._updateMuteIcon();
    },

    _updateMuteIcon() {
        const btn = document.getElementById('spinner-mute-btn');
        if (!btn) return;
        btn.innerHTML = this._audioMuted ? this._SPEAKER_OFF_SVG : this._SPEAKER_ON_SVG;
        btn.title = this._audioMuted ? 'Unmute sound' : 'Mute sound';
    },

    _getNoiseBuffer() {
        if (this._noiseBuffer) return this._noiseBuffer;
        const size = Math.floor(this._audioCtx.sampleRate * 0.5);
        const buffer = this._audioCtx.createBuffer(1, size, this._audioCtx.sampleRate);
        const data = buffer.getChannelData(0);
        for (let i = 0; i < size; i++) {
            data[i] = Math.random() * 2 - 1;
        }
        this._noiseBuffer = buffer;
        return buffer;
    },

    _playWhoosh() {
        if (!this._audioCtx || this._audioMuted) return;

        const ctx = this._audioCtx;
        const now = ctx.currentTime;

        // Filtered white noise with rising cutoff
        const noise = ctx.createBufferSource();
        noise.buffer = this._getNoiseBuffer();

        const filter = ctx.createBiquadFilter();
        filter.type = 'lowpass';
        filter.frequency.setValueAtTime(200, now);
        filter.frequency.exponentialRampToValueAtTime(1200, now + 0.3);

        const gain = ctx.createGain();
        gain.gain.setValueAtTime(0, now);
        gain.gain.linearRampToValueAtTime(0.15, now + 0.05);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.4);

        noise.connect(filter);
        filter.connect(gain);
        gain.connect(this._audioGain);
        noise.start(now);
        noise.stop(now + 0.45);
    },

    _playTick() {
        if (!this._audioCtx || this._audioMuted) return;

        const ctx = this._audioCtx;
        const now = ctx.currentTime;

        const osc = ctx.createOscillator();
        osc.type = 'sine';
        osc.frequency.value = 800;

        const gain = ctx.createGain();
        gain.gain.setValueAtTime(0.08, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.02);

        osc.connect(gain);
        gain.connect(this._audioGain);
        osc.start(now);
        osc.stop(now + 0.03);
    },

    _playRevealFanfare() {
        if (!this._audioCtx || this._audioMuted) return;

        const ctx = this._audioCtx;
        const now = ctx.currentTime;
        const notes = [523.25, 659.25, 783.99, 1046.50]; // C5, E5, G5, C6

        for (let i = 0; i < notes.length; i++) {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();

            osc.type = 'sine';
            osc.frequency.value = notes[i];

            const start = now + i * 0.1;
            gain.gain.setValueAtTime(0, start);
            gain.gain.linearRampToValueAtTime(0.25, start + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.6);

            osc.connect(gain);
            gain.connect(this._audioGain);
            osc.start(start);
            osc.stop(now + 0.65);
        }
    },

    _playConfettiPop() {
        if (!this._audioCtx || this._audioMuted) return;

        const ctx = this._audioCtx;
        const now = ctx.currentTime;

        // Confetti cannon: bandpass-filtered noise burst ("pfft!")
        const noise = ctx.createBufferSource();
        noise.buffer = this._getNoiseBuffer();

        const bandpass = ctx.createBiquadFilter();
        bandpass.type = 'bandpass';
        bandpass.frequency.setValueAtTime(1200, now);
        bandpass.frequency.exponentialRampToValueAtTime(600, now + 0.1);
        bandpass.Q.value = 0.8;

        const gain = ctx.createGain();
        gain.gain.setValueAtTime(0.3, now);
        gain.gain.linearRampToValueAtTime(0.25, now + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.12);

        noise.connect(bandpass);
        bandpass.connect(gain);
        gain.connect(this._audioGain);
        noise.start(now);
        noise.stop(now + 0.15);
    },

    _onSpinStart() {
        this._initAudio();
        this._lastTickTime = 0;
        this._lastTickTileIdx = -1;
        this._playWhoosh();
    },

    _onTick(progress) {
        if (!this._audioCtx || this._audioMuted) return;

        // Calculate which tile the center indicator is currently over
        const viewport = document.getElementById('spinner-viewport');
        if (!viewport) return;
        const viewportCenter = viewport.offsetWidth / 2;
        const currentX = Math.abs(this._finalTranslateX * (1 - Math.pow(1 - progress, 3)));
        const currentTileIdx = Math.floor((currentX + viewportCenter) / this._TILE_WIDTH);

        // Play tick when we cross a new tile boundary
        if (currentTileIdx !== this._lastTickTileIdx) {
            const now = performance.now();
            // Rate-limit: skip if last tick was <40ms ago (avoid buzzing at high speed)
            if (now - this._lastTickTime > 40) {
                this._playTick();
                this._lastTickTime = now;
            }
            this._lastTickTileIdx = currentTileIdx;
        }
    },

};

window.AZChallengeDetail = AZChallengeDetail;
