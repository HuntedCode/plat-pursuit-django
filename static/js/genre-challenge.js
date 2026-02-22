/**
 * Genre Challenge JavaScript
 *
 * Handles:
 * - GenreChallengeSetup: Guided wizard for picking concepts genre by genre
 * - GenreChallengeEdit: Grid edit page with swap/clear per slot, search modal
 * - GenreChallengeDetail: Detail page with inline rename, share, delete
 *
 * Uses: PlatPursuit.API, PlatPursuit.ToastManager, PlatPursuit.HTMLUtils, PlatPursuit.debounce
 */

// ─── Shared Constants ────────────────────────────────────────────────────────

const SEARCH_LIMIT = 20;
const REGION_COLORS = { NA: 'badge-primary', EU: 'badge-secondary', JP: 'badge-accent', AS: 'badge-warning', KR: 'badge-info' };
const GLOBE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 512 512" stroke="var(--color-success)" fill="var(--color-success)"><path d="M351.9 280l-190.9 0c2.9 64.5 17.2 123.9 37.5 167.4 11.4 24.5 23.7 41.8 35.1 52.4 11.2 10.5 18.9 12.2 22.9 12.2s11.7-1.7 22.9-12.2c11.4-10.6 23.7-28 35.1-52.4 20.3-43.5 34.6-102.9 37.5-167.4zM160.9 232l190.9 0C349 167.5 334.7 108.1 314.4 64.6 303 40.2 290.7 22.8 279.3 12.2 268.1 1.7 260.4 0 256.4 0s-11.7 1.7-22.9 12.2c-11.4 10.6-23.7 28-35.1 52.4-20.3 43.5-34.6 102.9-37.5 167.4zm-48 0C116.4 146.4 138.5 66.9 170.8 14.7 78.7 47.3 10.9 131.2 1.5 232l111.4 0zM1.5 280c9.4 100.8 77.2 184.7 169.3 217.3-32.3-52.2-54.4-131.7-57.9-217.3L1.5 280zm398.4 0c-3.5 85.6-25.6 165.1-57.9 217.3 92.1-32.7 159.9-116.5 169.3-217.3l-111.4 0zm111.4-48C501.9 131.2 434.1 47.3 342 14.7 374.3 66.9 396.4 146.4 399.9 232l111.4 0z"/></svg>';

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

// Shared filter state across setup and edit
const _genreFilterState = {
    platform: new Set(),
    region: new Set(),
    sort: 'popular',
    inBadge: false,
    myBacklog: false,
    newSubgenresOnly: false,
    subgenres: new Set(),
};


// ─── Shared Helpers ──────────────────────────────────────────────────────────

/**
 * Bind multi-select chip filter buttons.
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
        const total = filterState.platform.size + filterState.region.size
            + (filterState.inBadge ? 1 : 0) + (filterState.myBacklog ? 1 : 0)
            + (filterState.newSubgenresOnly ? 1 : 0);
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
            filterState.inBadge = false;
            filterState.myBacklog = false;
            filterState.newSubgenresOnly = false;
            filterState.subgenres.clear();
            chips.forEach(chip => updateChipVisual(chip, false));
            const filterRow = clearBtn.closest('[id$="filter-row"]');
            if (filterRow) {
                filterRow.querySelectorAll('[data-toggle]').forEach(tc => {
                    tc.classList.remove('badge-secondary', 'badge-info', 'badge-success');
                    tc.classList.add('badge-ghost');
                });
            }
            updateClearBtn();
            onChangeCallback();
        });
    }
}

/**
 * Bind boolean toggle filter chips (on/off).
 */
function _bindToggleFilters(chipSelector, filterState, onChangeCallback) {
    const chips = document.querySelectorAll(chipSelector);
    if (!chips.length) return;

    const TOGGLE_ACTIVE_CLASSES = { in_badge: 'badge-secondary', my_backlog: 'badge-info', new_subgenres: 'badge-success' };
    const TOGGLE_KEY_MAP = { in_badge: 'inBadge', my_backlog: 'myBacklog', new_subgenres: 'newSubgenresOnly' };

    const updateChipVisual = (chip, active) => {
        const key = chip.dataset.toggle;
        chip.classList.remove('badge-ghost', 'badge-secondary', 'badge-info', 'badge-success');
        chip.classList.add(active ? (TOGGLE_ACTIVE_CLASSES[key] || 'badge-secondary') : 'badge-ghost');
    };

    chips.forEach(chip => {
        const stateKey = TOGGLE_KEY_MAP[chip.dataset.toggle];
        if (stateKey && filterState[stateKey]) updateChipVisual(chip, true);
    });

    chips.forEach(chip => {
        chip.addEventListener('click', () => {
            const stateKey = TOGGLE_KEY_MAP[chip.dataset.toggle];
            if (!stateKey) return;
            filterState[stateKey] = !filterState[stateKey];
            updateChipVisual(chip, filterState[stateKey]);
            onChangeCallback();
        });
    });
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
    if (filterState.inBadge) params += '&in_badge=1';
    if (filterState.myBacklog) params += '&my_backlog=1';
    if (filterState.newSubgenresOnly) params += '&new_subgenres_only=1';
    if (filterState.subgenres.size > 0) params += `&subgenre=${[...filterState.subgenres].map(s => encodeURIComponent(s)).join(',')}`;
    return params;
}

/**
 * Render a concept card for search results.
 */
function _renderConceptCard(concept, collectedSubgenres) {
    const e = PlatPursuit.HTMLUtils.escape;
    const imgUrl = concept.concept_icon_url || '';

    // Platform badges
    const platforms = (concept.platforms || []).map(p =>
        `<span class="badge badge-ghost badge-xs font-bold">${e(p)}</span>`
    ).join('');

    // Community ratings
    const ratings = concept.community_ratings || {};
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

    // Player count
    const played = (concept.total_played || 0).toLocaleString();

    // Subgenre badges with "new" indicator
    const subgenres = (concept.subgenres || []);
    let subgenreHTML = '';
    if (subgenres.length) {
        const badges = subgenres.map(sg => {
            const isNew = sg.is_new;
            return `<span class="badge badge-ghost badge-xs text-[0.6rem] ${isNew ? 'ring-1 ring-success/50' : ''}">${e(sg.display)}${isNew ? '<span class="text-success ml-0.5 font-bold">+</span>' : ''}</span>`;
        }).join('');
        subgenreHTML = `<div class="flex flex-wrap gap-1 mt-1">${badges}</div>`;
    }

    return `
        <div class="card bg-base-100 border-2 border-base-300 hover:border-primary/50 cursor-pointer transition-all shadow-sm hover:shadow-md genre-concept-result"
             data-concept-id="${concept.id}"
             data-concept-name="${e(concept.unified_title)}"
             data-concept-icon="${e(imgUrl)}">
            <div class="card-body p-3 gap-1">
                <div class="flex gap-3">
                    <div class="w-16 h-16 lg:w-20 lg:h-20 rounded-lg overflow-hidden flex-shrink-0 border border-base-300 bg-base-200">
                        ${imgUrl
                            ? `<img src="${e(imgUrl)}" alt="" class="w-full h-full object-cover" loading="lazy" />`
                            : `<div class="w-full h-full flex items-center justify-center text-base-content/20">
                                <svg xmlns="http://www.w3.org/2000/svg" class="w-8 h-8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                               </div>`
                        }
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="font-semibold text-sm lg:text-base leading-tight line-clamp-2 pr-1">${e(concept.unified_title)}</p>
                        <div class="flex flex-wrap items-center gap-1 mt-1">
                            ${platforms}
                        </div>
                        <div class="flex items-center gap-3 text-xs text-base-content/50 mt-1">
                            <span>${played} players</span>
                        </div>
                        ${ratingsHTML}
                        ${subgenreHTML}
                    </div>
                </div>
            </div>
        </div>`;
}

/**
 * Render subgenre filter chips with live counts from the API.
 * @param {string} containerId - The container element ID for the chips
 * @param {Object} subgenreCounts - { SUBGENRE_KEY: count, ... } from API
 * @param {Array} allSubgenres - Full list of { key, display } subgenres
 * @param {Function} onClickCallback - Called when a chip is clicked (triggers search)
 */
function _renderSubgenreChips(containerId, subgenreCounts, allSubgenres, onClickCallback) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const e = PlatPursuit.HTMLUtils.escape;
    const activeSet = _genreFilterState.subgenres;

    const chips = allSubgenres
        .filter(sg => (subgenreCounts[sg.key] || 0) > 0 || activeSet.has(sg.key))
        .map(sg => {
            const count = subgenreCounts[sg.key] || 0;
            const isActive = activeSet.has(sg.key);
            return `<button type="button" class="badge ${isActive ? 'badge-accent' : 'badge-ghost'} badge-sm font-bold cursor-pointer hover:badge-outline transition-all subgenre-chip" data-subgenre="${e(sg.key)}">
                ${e(sg.display)} <span class="ml-0.5 text-[0.6rem] opacity-70">${count}</span>
            </button>`;
        });

    container.innerHTML = chips.join('');
    container.querySelectorAll('.subgenre-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            const key = chip.dataset.subgenre;
            if (activeSet.has(key)) {
                activeSet.delete(key);
            } else {
                activeSet.add(key);
            }
            onClickCallback();
        });
    });
}

/**
 * Render the subgenre bonus tracker grid.
 * @param {string} gridId - Container element ID
 * @param {Array} allSubgenres - Full list of { key, display } subgenres
 * @param {Array} collectedKeys - Array of collected subgenre keys
 */
function _renderSubgenreTracker(gridId, allSubgenres, collectedSubgenres) {
    const grid = document.getElementById(gridId);
    if (!grid) return;

    // Build status map from collected array: { key -> 'platted' | 'assigned' }
    const statusMap = {};
    for (const sg of collectedSubgenres) {
        statusMap[sg.key] = sg.status || 'assigned';
    }

    const e = PlatPursuit.HTMLUtils.escape;
    const checkSvg = '<svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3 mr-0.5" viewBox="0 0 24 24" fill="currentColor"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z"/></svg>';
    const starSvg = '<svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3 mr-0.5" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>';

    grid.innerHTML = allSubgenres.map(sg => {
        const sgStatus = statusMap[sg.key] || sg.status || 'uncollected';
        let cls, icon;
        if (sgStatus === 'platted') {
            cls = 'badge-warning';
            icon = starSvg;
        } else if (sgStatus === 'assigned') {
            cls = 'badge-primary';
            icon = checkSvg;
        } else {
            cls = 'badge-ghost text-base-content/30';
            icon = '';
        }
        return `<span class="badge ${cls} badge-sm font-medium">${icon}${e(sg.display)}</span>`;
    }).join('');
}


// ─── Shared Inline Rename ────────────────────────────────────────────────────

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
                `/api/v1/challenges/genre/${challengeId}/update/`,
                { name: newName }
            );

            originalName = newName;
            if (nameText) nameText.textContent = newName;
            input.value = newName;

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


// ─── Shared Delete Handler ───────────────────────────────────────────────────

function _bindDeleteButton(challengeId) {
    const btn = document.getElementById('delete-challenge-btn');
    if (!btn) return;

    btn.addEventListener('click', () => {
        const id = challengeId || btn.dataset.challengeId;
        if (!id) return;

        if (!document.getElementById('delete-challenge-modal')) {
            document.body.insertAdjacentHTML('beforeend', `
                <dialog id="delete-challenge-modal" class="modal">
                    <div class="modal-box max-w-sm">
                        <h3 class="text-lg font-bold text-error">Delete Challenge</h3>
                        <p class="py-4 text-base-content/70">
                            Are you sure? This will permanently remove your Genre Challenge. Any progress on incomplete slots will be lost.
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
                    await PlatPursuit.API.delete(`/api/v1/challenges/genre/${id}/delete/`);
                    modal.close();
                    PlatPursuit.ToastManager.success('Genre challenge deleted.');
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


// ============================================================
// Setup Wizard
// ============================================================
const GenreChallengeSetup = {
    challengeId: null,
    genres: [],             // Ordered list of genre keys
    genreDisplay: {},       // { ACTION: 'Action', ... }
    currentGenre: null,
    slots: {},              // { ACTION: { concept: { id, unified_title, ... }, is_completed }, ... }
    collectedSubgenres: [], // Array of { key, display, status } objects
    allSubgenres: [],       // Full list of { key, display, status } for tracker
    skippedGenres: [],
    _searchDebounced: null,
    _isSearching: false,
    _searchOffset: 0,
    _scrollObserver: null,

    init(challengeId, existingSlots, genreDisplayJson, subgenresJson) {
        this.challengeId = challengeId;
        this.genreDisplay = genreDisplayJson || {};
        this.genres = Object.keys(this.genreDisplay);
        this.allSubgenres = subgenresJson || [];
        this.slots = {};
        // Initialize collected subgenres from server-rendered data (has `status` field)
        this.collectedSubgenres = this.allSubgenres.filter(sg => sg.status !== 'uncollected');
        this.skippedGenres = [];
        this._searchOffset = 0;

        // Build slots map from server data
        for (const slot of existingSlots) {
            this.slots[slot.genre] = {
                concept: slot.concept,
                is_completed: slot.is_completed,
            };
        }

        // Find first unfilled genre to start
        this.currentGenre = this._findNextUnfilled(this.genres[0]) || this.genres[0];

        this._bindSearch();
        this._bindChipFilters();
        this._bindSortSelect();
        this._initScrollObserver();
        this._bindGenreButtons();
        this._bindSkipButton();
        this._bindFinishButton();

        // Initial render
        this._updateProgressBar();
        this._updateStats();
        this._renderSubgenreTracker();
        this._goToGenre(this.currentGenre);
    },

    // ── Navigation ──────────────────────────────────────────

    _goToGenre(genre) {
        this.currentGenre = genre;

        // Clear subgenre selections when switching genres (subgenres differ per genre)
        _genreFilterState.subgenres.clear();

        const display = document.getElementById('current-genre-display');
        const promptGenre = document.getElementById('prompt-genre');
        const genreName = this.genreDisplay[genre] || genre;
        if (display) display.textContent = genreName;
        if (promptGenre) promptGenre.textContent = genreName;

        // Update prompt text based on slot state
        const prompt = document.getElementById('current-genre-prompt');
        if (prompt) {
            const slot = this.slots[genre];
            if (slot?.concept) {
                const e = PlatPursuit.HTMLUtils.escape;
                prompt.innerHTML = `Currently: <strong>${e(slot.concept.unified_title)}</strong>. Search to swap.`;
            } else {
                prompt.innerHTML = `Pick a game tagged as <strong>${PlatPursuit.HTMLUtils.escape(genreName)}</strong>`;
            }
        }

        // Clear search input but keep filters
        const input = document.getElementById('genre-search-input');
        if (input) input.value = '';

        // Highlight current genre in progress bar
        document.querySelectorAll('.genre-slot-btn').forEach(btn => {
            btn.classList.remove('ring-2', 'ring-primary', 'ring-offset-2', 'ring-offset-base-100', 'scale-110');
            if (btn.dataset.genre === genre) {
                btn.classList.add('ring-2', 'ring-primary', 'ring-offset-2', 'ring-offset-base-100', 'scale-110');
            }
        });

        this._checkFinishReady();
        this._doSearch(false);
    },

    _findNextUnfilled(startGenre) {
        const startIdx = this.genres.indexOf(startGenre);
        if (startIdx === -1) return null;
        // Search from startGenre to end
        for (let i = startIdx; i < this.genres.length; i++) {
            const g = this.genres[i];
            if (!this.slots[g]?.concept && !this.slots[g]?.is_completed) return g;
        }
        // Wrap from beginning to startGenre
        for (let i = 0; i < startIdx; i++) {
            const g = this.genres[i];
            if (!this.slots[g]?.concept && !this.slots[g]?.is_completed) return g;
        }
        return null;
    },

    _nextGenre() {
        const currentIdx = this.genres.indexOf(this.currentGenre);
        const nextUnfilled = this._findNextUnfilled(this.genres[(currentIdx + 1) % this.genres.length]);

        if (nextUnfilled) {
            this._goToGenre(nextUnfilled);
        } else {
            this._checkFinishReady();
        }
    },

    // ── Search ──────────────────────────────────────────────

    _bindSearch() {
        const input = document.getElementById('genre-search-input');
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
        _bindChipFilters('.genre-filter-chip', 'genre-clear-filters', _genreFilterState, () => {
            this._doSearch(false);
        });
        _bindToggleFilters('.genre-toggle-chip', _genreFilterState, () => {
            this._doSearch(false);
        });
    },

    _bindSortSelect() {
        const select = document.getElementById('genre-sort-select');
        if (!select) return;

        select.addEventListener('change', () => {
            _genreFilterState.sort = select.value;
            this._doSearch(false);
        });
    },

    _initScrollObserver() {
        const sentinel = document.getElementById('genre-scroll-sentinel');
        if (!sentinel) return;

        this._scrollObserver = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && !this._isSearching) {
                this._doSearch(true);
            }
        });
    },

    async _doSearch(append) {
        if (this._isSearching) return;
        this._isSearching = true;

        const input = document.getElementById('genre-search-input');
        const query = input?.value?.trim() || '';
        const loading = document.getElementById('genre-search-loading');
        const results = document.getElementById('genre-search-results');
        const noResults = document.getElementById('genre-no-results');
        const sentinel = document.getElementById('genre-scroll-sentinel');

        if (!append) {
            this._searchOffset = 0;
            if (results) results.innerHTML = '';
            if (sentinel && this._scrollObserver) this._scrollObserver.unobserve(sentinel);
        }

        if (!append && loading) loading.classList.remove('hidden');
        if (noResults) noResults.classList.add('hidden');
        if (sentinel) sentinel.classList.add('hidden');

        try {
            let url = `/api/v1/challenges/genre/concept-search/?genre=${this.currentGenre}&challenge_id=${this.challengeId}&limit=${SEARCH_LIMIT}&offset=${this._searchOffset}`;
            if (query.length >= 2) url += `&q=${encodeURIComponent(query)}`;
            url += _buildFilterParams(_genreFilterState);

            const data = await PlatPursuit.API.get(url);

            // Update subgenre chips on fresh searches
            if (!append && data.subgenre_counts !== undefined) {
                _renderSubgenreChips('genre-subgenre-chips', data.subgenre_counts, this.allSubgenres, () => this._doSearch(false));
            }

            if (!append && !data.results.length) {
                if (results) results.innerHTML = '';
                if (noResults) noResults.classList.remove('hidden');
            } else {
                if (noResults) noResults.classList.add('hidden');
                if (results) {
                    const html = data.results.map(c => _renderConceptCard(c, this.collectedSubgenres)).join('');
                    if (append) {
                        results.insertAdjacentHTML('beforeend', html);
                    } else {
                        results.innerHTML = html;
                    }

                    // Bind click handlers
                    const selector = append ? '.genre-concept-result:not([data-bound])' : '.genre-concept-result';
                    results.querySelectorAll(selector).forEach(card => {
                        card.setAttribute('data-bound', '1');
                        card.addEventListener('click', () => {
                            this._selectConcept(card.dataset.conceptId, card.dataset.conceptName, card);
                        });
                    });
                }

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

    async _selectConcept(conceptId, conceptName, cardEl) {
        if (cardEl) {
            cardEl.classList.add('opacity-50', 'pointer-events-none');
        }

        try {
            const data = await PlatPursuit.API.post(
                `/api/v1/challenges/genre/${this.challengeId}/slots/${this.currentGenre}/assign/`,
                { concept_id: parseInt(conceptId, 10) }
            );

            // Update local slot data
            this.slots[this.currentGenre] = {
                concept: data.concept,
                is_completed: data.is_completed,
            };

            // Update subgenre tracker from API response (full objects with status)
            if (data.collected_subgenres) {
                this.collectedSubgenres = data.collected_subgenres;
                this._renderSubgenreTracker();
            }

            const e = PlatPursuit.HTMLUtils.escape;
            PlatPursuit.ToastManager.success(`${e(conceptName)} assigned to ${this.genreDisplay[this.currentGenre]}!`);

            this._updateProgressBar();
            this._updateStats();
            this._nextGenre();

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

    _bindGenreButtons() {
        document.querySelectorAll('.genre-slot-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const genre = btn.dataset.genre;
                if (!this.slots[genre]?.is_completed) {
                    this._goToGenre(genre);
                }
            });
        });
    },

    _updateProgressBar() {
        document.querySelectorAll('.genre-slot-btn').forEach(btn => {
            const genre = btn.dataset.genre;
            const slot = this.slots[genre];

            btn.classList.remove(
                'bg-success', 'text-success-content', 'border-success',
                'bg-primary/20', 'text-primary', 'border-primary/40',
                'bg-base-200', 'text-base-content/40', 'border-base-300',
                'hover:border-primary/40'
            );

            if (slot?.is_completed) {
                btn.classList.add('bg-success', 'text-success-content', 'border-success');
            } else if (slot?.concept) {
                btn.classList.add('bg-primary/20', 'text-primary', 'border-primary/40');
            } else {
                btn.classList.add('bg-base-200', 'text-base-content/40', 'border-base-300', 'hover:border-primary/40');
            }
        });
    },

    _updateStats() {
        let filled = 0;
        let completed = 0;
        for (const g of this.genres) {
            if (this.slots[g]?.concept) filled++;
            if (this.slots[g]?.is_completed) completed++;
        }

        const filledEl = document.getElementById('genre-filled-count');
        const completedEl = document.getElementById('genre-completed-count');
        const subgenreEl = document.getElementById('genre-subgenre-count');
        if (filledEl) filledEl.textContent = `${filled}/${this.genres.length} filled`;
        if (completedEl) completedEl.textContent = `${completed}/${this.genres.length} completed`;
        if (subgenreEl) subgenreEl.textContent = `${this.collectedSubgenres.length}/${this.allSubgenres.length} subgenres`;
    },

    _renderSubgenreTracker() {
        _renderSubgenreTracker('subgenre-tracker-grid', this.allSubgenres, this.collectedSubgenres);
        // Update count label
        const countEl = document.getElementById('subgenre-tracker-count');
        if (countEl) countEl.textContent = `${this.collectedSubgenres.length}/${this.allSubgenres.length}`;
    },

    // ── Skip & Finish ───────────────────────────────────────

    _bindSkipButton() {
        const btn = document.getElementById('genre-skip-btn');
        if (!btn) return;

        btn.addEventListener('click', () => {
            if (!this.skippedGenres.includes(this.currentGenre)) {
                this.skippedGenres.push(this.currentGenre);
            }
            this._nextGenre();
        });
    },

    _bindFinishButton() {
        const btn = document.getElementById('genre-finish-btn');
        if (!btn) return;

        btn.addEventListener('click', () => {
            window.location.href = `/challenges/genre/${this.challengeId}/`;
        });
    },

    _checkFinishReady() {
        const btn = document.getElementById('genre-finish-btn');
        const skipBtn = document.getElementById('genre-skip-btn');
        if (!btn) return;

        const nextUnfilled = this._findNextUnfilled(this.genres[0]);

        if (!nextUnfilled) {
            btn.classList.remove('hidden');
            btn.innerHTML = `
                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 mr-1" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>
                View Challenge
            `;
            if (skipBtn) skipBtn.classList.add('hidden');
        } else {
            const allVisited = this.genres.every(g =>
                this.slots[g]?.concept || this.slots[g]?.is_completed || this.skippedGenres.includes(g)
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

window.GenreChallengeSetup = GenreChallengeSetup;


// ============================================================
// Edit Page
// ============================================================
const GenreChallengeEdit = {
    challengeId: null,
    slots: {},              // { ACTION: { concept: { ... }, is_completed }, ... }
    bonusSlots: [],         // [{ id, concept: { ... }, is_completed }, ...]
    coverGenre: '',
    genreDisplay: {},
    allSubgenres: [],
    collectedSubgenres: [],
    _currentEditGenre: null,
    _modalMode: 'genre',    // 'genre' or 'bonus'
    _modalSearchOffset: 0,
    _isModalSearching: false,
    _modalChipsBound: false,
    _modalScrollObserver: null,
    _clearModalData: null,  // { genre, concept, conceptName }
    _moveModalData: null,   // { sourceType, sourceId, conceptName }

    init(challengeId, existingSlots, coverGenre, genreDisplayJson, subgenresJson, bonusSlotsJson) {
        this.challengeId = challengeId;
        this.coverGenre = coverGenre || '';
        this.genreDisplay = genreDisplayJson || {};
        this.allSubgenres = subgenresJson || [];
        this.bonusSlots = bonusSlotsJson || [];
        this.slots = {};
        // Initialize collected subgenres from server-rendered data (has `status` field)
        this.collectedSubgenres = this.allSubgenres.filter(sg => sg.status !== 'uncollected');
        this._modalSearchOffset = 0;

        for (const slot of existingSlots) {
            this.slots[slot.genre] = {
                concept: slot.concept,
                is_completed: slot.is_completed,
            };
        }

        this._bindSlotActions();
        this._bindBonusActions();
        this._bindModalSearch();
        this._bindModalSortSelect();
        this._initModalScrollObserver();
        this._bindClearModal();
        this._bindMoveModal();
        _bindInlineRename(this.challengeId);
        _bindDeleteButton(this.challengeId);

        // Render initial subgenre tracker from server data
        this._renderSubgenreTracker();
    },

    _renderSubgenreTracker() {
        _renderSubgenreTracker('subgenre-tracker-grid', this.allSubgenres, this.collectedSubgenres);
        const countEl = document.getElementById('edit-subgenre-tracker-count');
        if (countEl) countEl.textContent = `${this.collectedSubgenres.length}/${this.allSubgenres.length}`;
        const headerEl = document.getElementById('edit-subgenre-count');
        if (headerEl) headerEl.textContent = `${this.collectedSubgenres.length}/${this.allSubgenres.length} subgenres collected`;
    },

    // ── Slot Actions ────────────────────────────────────────

    _bindSlotActions() {
        const grid = document.getElementById('genre-edit-grid');
        if (!grid) return;

        grid.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-action]');
            if (!btn) return;

            const genre = btn.dataset.genre;
            const action = btn.dataset.action;

            if (action === 'edit' || action === 'assign' || action === 'swap') {
                this._openModal(genre, 'genre');
            } else if (action === 'clear') {
                this._showClearModal(genre);
            } else if (action === 'move') {
                this._showMoveModal('genre', genre);
            } else if (action === 'set-cover') {
                this._setCover(genre);
            }
        });
    },

    _bindBonusActions() {
        const grid = document.getElementById('bonus-grid');
        const addBtn = document.getElementById('add-bonus-btn');

        if (grid) {
            grid.addEventListener('click', (e) => {
                const btn = e.target.closest('[data-action]');
                if (!btn) return;

                const bonusId = parseInt(btn.dataset.bonusId, 10);
                const action = btn.dataset.action;

                if (action === 'bonus-remove') {
                    this._removeBonusSlot(bonusId);
                } else if (action === 'bonus-move') {
                    this._showMoveModal('bonus', bonusId);
                }
            });
        }

        if (addBtn) {
            addBtn.addEventListener('click', () => {
                this._openModal(null, 'bonus');
            });
        }
    },

    // ── Modal ───────────────────────────────────────────────

    _openModal(genre, mode) {
        this._currentEditGenre = genre;
        this._modalMode = mode || 'genre';
        this._modalSearchOffset = 0;

        const modal = document.getElementById('genre-search-modal');
        const genreDisplay = document.getElementById('modal-genre');
        const input = document.getElementById('modal-search-input');
        const results = document.getElementById('modal-search-results');
        const noResults = document.getElementById('modal-no-results');
        const sentinel = document.getElementById('modal-scroll-sentinel');

        // Toggle header/subtitle between genre and bonus mode
        const headerGenre = document.getElementById('modal-header-genre');
        const headerBonus = document.getElementById('modal-header-bonus');
        const subtitleGenre = document.getElementById('modal-subtitle-genre');
        const subtitleBonus = document.getElementById('modal-subtitle-bonus');

        if (this._modalMode === 'bonus') {
            if (headerGenre) headerGenre.classList.add('hidden');
            if (headerBonus) headerBonus.classList.remove('hidden');
            if (subtitleGenre) subtitleGenre.classList.add('hidden');
            if (subtitleBonus) subtitleBonus.classList.remove('hidden');
        } else {
            if (headerGenre) headerGenre.classList.remove('hidden');
            if (headerBonus) headerBonus.classList.add('hidden');
            if (subtitleGenre) subtitleGenre.classList.remove('hidden');
            if (subtitleBonus) subtitleBonus.classList.add('hidden');
            if (genreDisplay) genreDisplay.textContent = this.genreDisplay[genre] || genre;
        }

        if (input) input.value = '';
        if (results) results.innerHTML = '';
        if (noResults) noResults.classList.add('hidden');
        // Re-append sentinel to results container
        if (sentinel && results) {
            sentinel.classList.add('hidden');
            results.appendChild(sentinel);
        }

        // Bind chip filters once
        if (!this._modalChipsBound) {
            _bindChipFilters('.modal-filter-chip', 'modal-clear-filters', _genreFilterState, () => {
                this._doModalSearch(false);
            });
            _bindToggleFilters('.modal-toggle-chip', _genreFilterState, () => {
                this._doModalSearch(false);
            });
            this._modalChipsBound = true;
        }

        // Sync sort dropdown with shared state
        const sortSelect = document.getElementById('modal-sort-select');
        if (sortSelect) sortSelect.value = _genreFilterState.sort;

        if (modal) modal.showModal();

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
            _genreFilterState.sort = select.value;
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
        const genre = this._currentEditGenre;
        const mode = this._modalMode;
        if (mode === 'genre' && !genre) return;
        if (this._isModalSearching) return;
        this._isModalSearching = true;

        const query = document.getElementById('modal-search-input')?.value?.trim() || '';
        const loading = document.getElementById('modal-search-loading');
        const results = document.getElementById('modal-search-results');
        const noResults = document.getElementById('modal-no-results');
        const sentinel = document.getElementById('modal-scroll-sentinel');

        if (!append) {
            this._modalSearchOffset = 0;
            if (results) results.innerHTML = '';
            if (sentinel && results) results.appendChild(sentinel);
            if (sentinel && this._modalScrollObserver) this._modalScrollObserver.unobserve(sentinel);
        }

        if (!append && loading) loading.classList.remove('hidden');
        if (noResults) noResults.classList.add('hidden');
        if (sentinel) sentinel.classList.add('hidden');

        try {
            let url = `/api/v1/challenges/genre/concept-search/?challenge_id=${this.challengeId}&limit=${SEARCH_LIMIT}&offset=${this._modalSearchOffset}`;
            if (mode === 'bonus') {
                url += '&mode=bonus';
                // Use a placeholder genre param to satisfy server (won't be used in bonus mode)
                url += '&genre=ACTION';
            } else {
                url += `&genre=${genre}`;
            }
            if (query.length >= 2) url += `&q=${encodeURIComponent(query)}`;
            url += _buildFilterParams(_genreFilterState);

            const data = await PlatPursuit.API.get(url);

            // Update subgenre chips on fresh searches
            if (!append && data.subgenre_counts !== undefined) {
                _renderSubgenreChips('modal-subgenre-chips', data.subgenre_counts, this.allSubgenres, () => this._doModalSearch(false));
            }

            if (!append && !data.results.length) {
                if (results) results.innerHTML = '';
                if (sentinel && results) results.appendChild(sentinel);
                if (noResults) noResults.classList.remove('hidden');
            } else {
                if (noResults) noResults.classList.add('hidden');
                if (results) {
                    const html = data.results.map(c => _renderConceptCard(c, this.collectedSubgenres)).join('');
                    sentinel?.insertAdjacentHTML('beforebegin', html);

                    const selector = append ? '.genre-concept-result:not([data-bound])' : '.genre-concept-result';
                    results.querySelectorAll(selector).forEach(card => {
                        card.setAttribute('data-bound', '1');
                        card.addEventListener('click', () => {
                            this._assignFromModal(
                                card.dataset.conceptId,
                                card.dataset.conceptName,
                                card
                            );
                        });
                    });
                }

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

    async _assignFromModal(conceptId, conceptName, cardEl) {
        if (this._modalMode === 'bonus') {
            return this._addBonusFromModal(conceptId, conceptName, cardEl);
        }

        const genre = this._currentEditGenre;
        if (!genre) return;

        if (cardEl) cardEl.classList.add('opacity-50', 'pointer-events-none');

        try {
            const data = await PlatPursuit.API.post(
                `/api/v1/challenges/genre/${this.challengeId}/slots/${genre}/assign/`,
                { concept_id: parseInt(conceptId, 10) }
            );

            this.slots[genre] = {
                concept: data.concept,
                is_completed: data.is_completed,
            };

            // Update cover if the API auto-set it
            if (data.cover_genre !== undefined) {
                const oldCover = this.coverGenre;
                this.coverGenre = data.cover_genre;
                if (oldCover && oldCover !== data.cover_genre) {
                    this._updateCoverIndicator(oldCover);
                    this._updateSlotOverlay(oldCover);
                }
            }

            // Update subgenres
            if (data.collected_subgenres) {
                this.collectedSubgenres = data.collected_subgenres;
                this._renderSubgenreTracker();
            }

            this._updateSlotCard(genre);

            const modal = document.getElementById('genre-search-modal');
            if (modal) modal.close();

            const e = PlatPursuit.HTMLUtils.escape;
            PlatPursuit.ToastManager.success(`${e(conceptName)} assigned to ${this.genreDisplay[genre]}!`);

        } catch (err) {
            let msg = 'Failed to assign game.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);

            if (cardEl) cardEl.classList.remove('opacity-50', 'pointer-events-none');
        }
    },

    async _addBonusFromModal(conceptId, conceptName, cardEl) {
        if (cardEl) cardEl.classList.add('opacity-50', 'pointer-events-none');

        try {
            const data = await PlatPursuit.API.post(
                `/api/v1/challenges/genre/${this.challengeId}/bonus/add/`,
                { concept_id: parseInt(conceptId, 10) }
            );

            // Add to local bonus slots
            this.bonusSlots.push({
                id: data.id,
                concept: data.concept,
                is_completed: data.is_completed,
            });

            // Update subgenres
            if (data.collected_subgenres) {
                this.collectedSubgenres = data.collected_subgenres;
                this._renderSubgenreTracker();
            }

            this._renderBonusGrid();

            const modal = document.getElementById('genre-search-modal');
            if (modal) modal.close();

            const e = PlatPursuit.HTMLUtils.escape;
            PlatPursuit.ToastManager.success(`${e(conceptName)} added as a bonus game!`);

        } catch (err) {
            let msg = 'Failed to add bonus game.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);

            if (cardEl) cardEl.classList.remove('opacity-50', 'pointer-events-none');
        }
    },

    // ── Clear Flow (with "Move to Bonus" offer) ──────────────

    _showClearModal(genre) {
        const slot = this.slots[genre];
        if (!slot?.concept) return;

        const genreName = this.genreDisplay[genre] || genre;
        const conceptName = slot.concept.unified_title || 'this game';

        this._clearModalData = { genre, concept: slot.concept, conceptName };

        const nameEl = document.getElementById('clear-modal-game-name');
        const genreEl = document.getElementById('clear-modal-genre');
        const warningEl = document.getElementById('clear-modal-progress-warning');

        if (nameEl) nameEl.textContent = conceptName;
        if (genreEl) genreEl.textContent = genreName;
        if (warningEl) warningEl.classList.add('hidden');

        const modal = document.getElementById('genre-clear-modal');
        if (modal) modal.showModal();
    },

    _bindClearModal() {
        const moveBonusBtn = document.getElementById('clear-modal-move-bonus');
        const removeBtn = document.getElementById('clear-modal-remove');

        if (moveBonusBtn) {
            moveBonusBtn.addEventListener('click', () => {
                if (!this._clearModalData) return;
                const modal = document.getElementById('genre-clear-modal');
                if (modal) modal.close();
                this._moveToBonus(this._clearModalData.genre);
            });
        }

        if (removeBtn) {
            removeBtn.addEventListener('click', () => {
                if (!this._clearModalData) return;
                const modal = document.getElementById('genre-clear-modal');
                if (modal) modal.close();
                this._clearSlotConfirmed(this._clearModalData.genre);
            });
        }
    },

    async _moveToBonus(genre) {
        try {
            const data = await PlatPursuit.API.post(
                `/api/v1/challenges/genre/${this.challengeId}/move/`,
                { source_type: 'genre', source_id: genre, dest_type: 'bonus', dest_id: '' }
            );

            this._applyMoveResponse(data);

            const genreName = this.genreDisplay[genre] || genre;
            PlatPursuit.ToastManager.success(`Game moved from ${genreName} to Bonus!`);

        } catch (err) {
            let msg = 'Failed to move game.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);
        }
    },

    async _clearSlotConfirmed(genre) {
        const genreName = this.genreDisplay[genre] || genre;

        try {
            const data = await PlatPursuit.API.request(
                `/api/v1/challenges/genre/${this.challengeId}/slots/${genre}/clear/`,
                { method: 'DELETE' }
            );

            // Update cover if the API re-picked
            if (data.cover_genre !== undefined) {
                const oldCover = this.coverGenre;
                this.coverGenre = data.cover_genre;
                if (oldCover && oldCover !== data.cover_genre) {
                    this._updateCoverIndicator(oldCover);
                    this._updateSlotOverlay(oldCover);
                }
                if (data.cover_genre) {
                    this._updateCoverIndicator(data.cover_genre);
                    this._updateSlotOverlay(data.cover_genre);
                }
            }

            // Update subgenres
            if (data.collected_subgenres) {
                this.collectedSubgenres = data.collected_subgenres;
                this._renderSubgenreTracker();
            }

            this.slots[genre] = { concept: null, is_completed: false };
            this._updateSlotCard(genre);

            PlatPursuit.ToastManager.success(`${genreName} slot cleared.`);

        } catch (err) {
            let msg = 'Failed to clear slot.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);
        }
    },

    // ── Move Flow ──────────────────────────────────────────

    async _showMoveModal(sourceType, sourceId) {
        let conceptName = '';
        if (sourceType === 'genre') {
            const slot = this.slots[sourceId];
            if (!slot?.concept) return;
            conceptName = slot.concept.unified_title || 'this game';
        } else {
            const bonus = this.bonusSlots.find(bs => bs.id === sourceId);
            if (!bonus?.concept) return;
            conceptName = bonus.concept.unified_title || 'this game';
        }

        this._moveModalData = { sourceType, sourceId, conceptName };

        const nameEl = document.getElementById('move-modal-game-name');
        const targetsEl = document.getElementById('move-modal-targets');
        const loadingEl = document.getElementById('move-modal-loading');
        const emptyEl = document.getElementById('move-modal-empty');

        if (nameEl) nameEl.textContent = conceptName;
        if (targetsEl) targetsEl.innerHTML = '';
        if (loadingEl) loadingEl.classList.remove('hidden');
        if (emptyEl) emptyEl.classList.add('hidden');

        const modal = document.getElementById('genre-move-modal');
        if (modal) modal.showModal();

        try {
            const data = await PlatPursuit.API.get(
                `/api/v1/challenges/genre/${this.challengeId}/move-targets/?source_type=${sourceType}&source_id=${sourceType === 'bonus' ? sourceId : sourceId}`
            );

            if (loadingEl) loadingEl.classList.add('hidden');

            if (!data.targets?.length) {
                if (emptyEl) emptyEl.classList.remove('hidden');
                return;
            }

            this._renderMoveTargets(data.targets);

        } catch (err) {
            if (loadingEl) loadingEl.classList.add('hidden');
            let msg = 'Failed to load targets.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);
            if (modal) modal.close();
        }
    },

    _renderMoveTargets(targets) {
        const container = document.getElementById('move-modal-targets');
        if (!container) return;

        const e = PlatPursuit.HTMLUtils.escape;
        container.innerHTML = targets.map(t => {
            const isBonus = t.genre === 'BONUS';
            const badgeClass = isBonus ? 'badge-info' : 'badge-primary';
            const label = isBonus ? 'Bonus Slot' : e(t.genre_display);
            const statusText = isBonus ? '' : (t.has_game ? '<span class="text-warning text-xs">(occupied)</span>' : '<span class="text-success text-xs">(empty)</span>');

            return `
                <button class="btn btn-ghost btn-sm justify-start gap-2 move-target-btn"
                        data-dest-type="${isBonus ? 'bonus' : 'genre'}"
                        data-dest-id="${isBonus ? '' : e(t.genre)}">
                    <span class="badge ${badgeClass} badge-sm font-bold">${label}</span>
                    ${statusText}
                </button>`;
        }).join('');

        container.querySelectorAll('.move-target-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this._executeMove(btn.dataset.destType, btn.dataset.destId);
            });
        });
    },

    _bindMoveModal() {
        // No static bindings needed; targets are bound dynamically in _renderMoveTargets
    },

    async _executeMove(destType, destId) {
        if (!this._moveModalData) return;
        const { sourceType, sourceId, conceptName } = this._moveModalData;

        const modal = document.getElementById('genre-move-modal');
        if (modal) modal.close();

        try {
            const data = await PlatPursuit.API.post(
                `/api/v1/challenges/genre/${this.challengeId}/move/`,
                {
                    source_type: sourceType,
                    source_id: sourceType === 'bonus' ? parseInt(sourceId, 10) : sourceId,
                    dest_type: destType,
                    dest_id: destId,
                }
            );

            this._applyMoveResponse(data);

            const destLabel = destType === 'bonus' ? 'Bonus' : (this.genreDisplay[destId] || destId);
            const esc = PlatPursuit.HTMLUtils.escape;
            PlatPursuit.ToastManager.success(`${esc(conceptName)} moved to ${destLabel}!`);

        } catch (err) {
            let msg = 'Failed to move game.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);
        }
    },

    _applyMoveResponse(data) {
        // Update genre slots from response
        if (data.slots) {
            for (const [genre, slotData] of Object.entries(data.slots)) {
                this.slots[genre] = {
                    concept: slotData.concept,
                    is_completed: slotData.is_completed,
                };
                this._updateSlotCard(genre);
            }
        }

        // Update bonus slots from response
        if (data.bonus_slots) {
            this.bonusSlots = data.bonus_slots.map(bs => ({
                id: bs.id,
                concept: bs.concept,
                is_completed: bs.is_completed,
            }));
            this._renderBonusGrid();
        }

        // Update cover
        if (data.cover_genre !== undefined) {
            const oldCover = this.coverGenre;
            this.coverGenre = data.cover_genre;
            // Refresh cover indicators for all genres (move can change any slot)
            for (const genre of Object.keys(this.genreDisplay)) {
                this._updateCoverIndicator(genre);
                this._updateSlotOverlay(genre);
            }
        }

        // Update subgenres
        if (data.collected_subgenres) {
            this.collectedSubgenres = data.collected_subgenres;
            this._renderSubgenreTracker();
        }
    },

    // ── Bonus Slot Management ──────────────────────────────

    _removeBonusSlot(bonusId) {
        const bonus = this.bonusSlots.find(bs => bs.id === bonusId);
        if (!bonus) return;

        const conceptName = bonus.concept?.unified_title || 'this game';
        const modal = document.getElementById('genre-bonus-remove-modal');
        const nameEl = document.getElementById('bonus-remove-modal-name');
        const confirmBtn = document.getElementById('bonus-remove-modal-confirm');
        if (!modal || !confirmBtn) return;

        if (nameEl) nameEl.textContent = conceptName;

        // Replace listener to avoid stale closures
        const newBtn = confirmBtn.cloneNode(true);
        confirmBtn.parentNode.replaceChild(newBtn, confirmBtn);
        newBtn.addEventListener('click', async () => {
            modal.close();
            await this._doRemoveBonus(bonusId, conceptName);
        });

        modal.showModal();
    },

    async _doRemoveBonus(bonusId, conceptName) {
        try {
            const data = await PlatPursuit.API.request(
                `/api/v1/challenges/genre/${this.challengeId}/bonus/${bonusId}/clear/`,
                { method: 'DELETE' }
            );

            this.bonusSlots = this.bonusSlots.filter(bs => bs.id !== bonusId);

            if (data.collected_subgenres) {
                this.collectedSubgenres = data.collected_subgenres;
                this._renderSubgenreTracker();
            }

            this._renderBonusGrid();
            PlatPursuit.ToastManager.success(`${PlatPursuit.HTMLUtils.escape(conceptName)} removed from bonus.`);

        } catch (err) {
            let msg = 'Failed to remove bonus game.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);
        }
    },

    _renderBonusGrid() {
        const grid = document.getElementById('bonus-grid');
        const empty = document.getElementById('bonus-empty');
        const countEl = document.getElementById('bonus-count');

        if (countEl) countEl.textContent = `(${this.bonusSlots.length})`;

        if (!grid) return;

        if (!this.bonusSlots.length) {
            grid.innerHTML = '';
            if (empty) empty.classList.remove('hidden');
            return;
        }

        if (empty) empty.classList.add('hidden');

        const e = PlatPursuit.HTMLUtils.escape;
        grid.innerHTML = this.bonusSlots.map(slot => {
            const concept = slot.concept;
            if (!concept) return '';

            const iconUrl = concept.concept_icon_url || '';
            const isCompleted = slot.is_completed;
            const borderClass = isCompleted ? 'border-success/50' : 'border-info/30';
            const badgeClass = isCompleted ? 'badge-success' : 'badge-info';

            const checkmark = isCompleted
                ? `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 text-success" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>`
                : '';

            const overlay = isCompleted ? '' : `
                <div class="absolute inset-0 bg-base-300/80 opacity-0 group-hover:opacity-100 transition-opacity rounded-box flex flex-col items-center justify-center gap-1 cursor-pointer"
                     data-action="bonus-edit" data-bonus-id="${slot.id}">
                    <div class="flex gap-1">
                        <button class="btn btn-xs btn-primary" data-action="bonus-move" data-bonus-id="${slot.id}">Move to Genre</button>
                        <button class="btn btn-xs btn-error btn-outline" data-action="bonus-remove" data-bonus-id="${slot.id}">Remove</button>
                    </div>
                </div>`;

            return `
                <div class="relative group" data-bonus-id="${slot.id}">
                    <div class="card bg-base-100 border-2 ${borderClass} shadow-md h-full">
                        <div class="px-3 pt-3 pb-1">
                            <div class="flex items-center justify-between">
                                <span class="badge ${badgeClass} badge-sm font-bold">Bonus</span>
                                ${checkmark}
                            </div>
                        </div>
                        <div class="px-3 pb-3">
                            <div class="flex items-center gap-2 mt-1">
                                ${iconUrl
                                    ? `<img src="${e(iconUrl)}" alt="${e(concept.unified_title)}" class="w-10 h-10 rounded object-cover flex-shrink-0" loading="lazy" />`
                                    : `<div class="w-10 h-10 rounded bg-base-200 flex items-center justify-center flex-shrink-0">
                                        <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5 text-base-content/20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                                      </div>`
                                }
                                <div class="flex-1 min-w-0">
                                    <p class="text-sm font-semibold line-clamp-2 pr-1">${e(concept.unified_title)}</p>
                                </div>
                            </div>
                            ${(concept.resolved_subgenres || []).length
                                ? `<div class="flex flex-wrap gap-0.5 mt-1.5">${(concept.resolved_subgenres || []).map(sg => `<span class="badge badge-ghost badge-xs text-[0.55rem]">${e(sg.display)}</span>`).join('')}</div>`
                                : ''
                            }
                        </div>
                    </div>
                    ${overlay}
                </div>`;
        }).join('');
    },

    // ── Set Cover ────────────────────────────────────────────

    async _setCover(genre) {
        try {
            await PlatPursuit.API.patch(
                `/api/v1/challenges/genre/${this.challengeId}/update/`,
                { cover_genre: genre }
            );

            const oldCover = this.coverGenre;
            this.coverGenre = genre;

            if (oldCover && oldCover !== genre) {
                this._updateCoverIndicator(oldCover);
                this._updateSlotOverlay(oldCover);
            }
            this._updateCoverIndicator(genre);
            this._updateSlotOverlay(genre);

            PlatPursuit.ToastManager.success(`Cover image set to ${this.genreDisplay[genre]}!`);
        } catch (err) {
            let msg = 'Failed to set cover.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);
        }
    },

    _updateCoverIndicator(genre) {
        const container = document.querySelector(`[data-genre="${genre}"][data-slot-id]`);
        if (!container) return;

        const existing = container.querySelector('.cover-indicator');
        if (existing) existing.remove();

        if (genre === this.coverGenre) {
            container.insertAdjacentHTML('afterbegin', `
                <div class="absolute top-1 right-1 z-10 cover-indicator" data-genre="${genre}">
                    <div class="badge badge-primary badge-sm gap-0.5" title="Cover image">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="currentColor"><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>
                    </div>
                </div>`);
        }

        // Update header set-cover button visibility
        this._updateHeaderActions(genre);
    },

    _updateHeaderActions(genre) {
        const container = document.querySelector(`[data-genre="${genre}"][data-slot-id]`);
        if (!container) return;

        const headerActions = container.querySelector('.header-actions');
        if (!headerActions) return;

        const slot = this.slots[genre];
        const e = PlatPursuit.HTMLUtils.escape;

        let html = '';
        if (slot?.is_completed) {
            html += `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 text-success" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>`;
        }
        if (slot?.concept && genre !== this.coverGenre) {
            html += `
                <button class="btn btn-ghost btn-xs p-0 w-5 h-5 min-h-0 opacity-40 hover:opacity-100 transition-opacity"
                        data-action="set-cover" data-genre="${e(genre)}" title="Set as cover image">
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/>
                    </svg>
                </button>`;
        }
        headerActions.innerHTML = html;
    },

    _updateSlotOverlay(genre) {
        const container = document.querySelector(`[data-genre="${genre}"][data-slot-id]`);
        if (!container) return;

        const slot = this.slots[genre];
        if (slot?.is_completed) return; // Completed slots don't have overlays

        const existing = container.querySelector('[data-action="edit"]');
        if (!existing) return;

        // Rebuild the overlay buttons (set-cover now lives in the card header, not here)
        const e = PlatPursuit.HTMLUtils.escape;
        if (slot?.concept) {
            existing.innerHTML = `
                <div class="flex gap-1">
                    <button class="btn btn-xs btn-primary" data-action="swap" data-genre="${e(genre)}">Swap</button>
                    <button class="btn btn-xs btn-ghost" data-action="move" data-genre="${e(genre)}">Move</button>
                    <button class="btn btn-xs btn-error btn-outline" data-action="clear" data-genre="${e(genre)}">Clear</button>
                </div>`;
        } else {
            existing.innerHTML = `
                <button class="btn btn-xs btn-primary" data-action="assign" data-genre="${e(genre)}">
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3 mr-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
                    Assign
                </button>`;
        }
    },

    // ── Update Slot Card (after assign/clear) ───────────────

    _updateSlotCard(genre) {
        const container = document.querySelector(`[data-genre="${genre}"][data-slot-id]`);
        if (!container) return;

        const slot = this.slots[genre];
        const card = container.querySelector('.card');
        if (!card) return;

        const e = PlatPursuit.HTMLUtils.escape;

        // Update border color
        card.classList.remove('border-success/50', 'border-primary/30', 'border-base-300');
        if (slot?.is_completed) {
            card.classList.add('border-success/50');
        } else if (slot?.concept) {
            card.classList.add('border-primary/30');
        } else {
            card.classList.add('border-base-300');
        }

        // Update genre badge
        const badge = card.querySelector('.badge');
        if (badge) {
            badge.classList.remove('badge-success', 'badge-primary', 'badge-ghost');
            if (slot?.is_completed) {
                badge.classList.add('badge-success');
            } else if (slot?.concept) {
                badge.classList.add('badge-primary');
            } else {
                badge.classList.add('badge-ghost');
            }
        }

        // Rebuild content area (after header)
        const header = card.querySelector('.px-3.pt-3.pb-1');
        const existingContent = card.querySelector('.px-3.pb-3');
        if (existingContent) existingContent.remove();

        const contentDiv = document.createElement('div');
        contentDiv.className = 'px-3 pb-3';

        if (slot?.concept) {
            const concept = slot.concept;
            const iconUrl = concept.concept_icon_url || '';
            const subgenres = concept.resolved_subgenres || [];
            const sgHtml = subgenres.length
                ? `<div class="flex flex-wrap gap-0.5 mt-1.5">${subgenres.map(sg => `<span class="badge badge-ghost badge-xs text-[0.55rem]">${e(sg.display)}</span>`).join('')}</div>`
                : '';
            contentDiv.innerHTML = `
                <div class="flex items-center gap-2 mt-1">
                    ${iconUrl
                        ? `<img src="${e(iconUrl)}" alt="${e(concept.unified_title)}" class="w-10 h-10 rounded object-cover flex-shrink-0" loading="lazy" />`
                        : `<div class="w-10 h-10 rounded bg-base-200 flex items-center justify-center flex-shrink-0">
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5 text-base-content/20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>
                            </svg>
                          </div>`
                    }
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-semibold line-clamp-2 pr-1">${e(concept.unified_title)}</p>
                    </div>
                </div>${sgHtml}`;
        } else {
            contentDiv.innerHTML = `
                <div class="flex items-center justify-center h-16 text-base-content/20">
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-8 h-8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <path d="M12 5v14M5 12h14"/>
                    </svg>
                </div>`;
        }

        if (header) {
            header.after(contentDiv);
        }

        // Update overlay
        this._updateSlotOverlay(genre);
        this._updateCoverIndicator(genre);
    },
};

window.GenreChallengeEdit = GenreChallengeEdit;


// ============================================================
// Detail Page
// ============================================================
const GenreChallengeDetail = {
    challengeId: null,

    init(challengeId) {
        this.challengeId = challengeId;

        _bindInlineRename(this.challengeId);
        _bindDeleteButton(this.challengeId);
        this._bindShareButton();
    },

    _bindShareButton() {
        const btn = document.getElementById('share-btn');
        if (!btn) return;

        btn.addEventListener('click', async () => {
            const url = btn.dataset.url || window.location.href;
            try {
                await navigator.clipboard.writeText(url);
                PlatPursuit.ToastManager.success('Challenge URL copied to clipboard!');
            } catch {
                // Fallback: select and copy
                const input = document.createElement('input');
                input.value = url;
                document.body.appendChild(input);
                input.select();
                document.execCommand('copy');
                document.body.removeChild(input);
                PlatPursuit.ToastManager.success('Challenge URL copied to clipboard!');
            }
        });
    },
};

window.GenreChallengeDetail = GenreChallengeDetail;
