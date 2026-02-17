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
};

window.AZChallengeDetail = AZChallengeDetail;
