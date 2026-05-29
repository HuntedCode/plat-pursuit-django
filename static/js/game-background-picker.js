/**
 * GameBackgroundPicker - Reusable search-autocomplete for selecting a game background.
 *
 * Used on:
 * - Calendar Challenge share card modal (select a game bg for the share image)
 * - Settings page (select profile background)
 *
 * Depends on: PlatPursuit.API, PlatPursuit.HTMLUtils, PlatPursuit.debounce
 */
class GameBackgroundPicker {
    /**
     * @param {string} containerId - ID of the container element to render into
     * @param {Object} options
     * @param {Function} options.onSelect - Called with concept object when user picks a game
     * @param {Function} options.onClear - Called when user clears the selection
     * @param {Object|null} options.initialValue - Pre-selected value: { concept_id, title_name, icon_url, image_url? }
     * @param {boolean} options.disabled - Whether the picker is disabled (non-premium)
     * @param {boolean} options.imagePicker - Two-step mode: after choosing a game,
     *        show a grid of that game's landscape images (artwork/screenshot/cover)
     *        and resolve onSelect with the chosen image as `image_url`. Also lists
     *        every platted/100% game (not just ones with PSN background art).
     */
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        if (!this.container) return;

        this.onSelect = options.onSelect || (() => {});
        this.onClear = options.onClear || (() => {});
        this.initialValue = options.initialValue || null;
        this.disabled = options.disabled || false;
        this.imagePicker = options.imagePicker || false;
        this.selectedConcept = null;
        this._pendingConcept = null;
        this._abortController = null;
        this._imagesAbortController = null;
        this._recentItemsHtml = null;

        this._render();
        this._bind();

        if (this.initialValue && this.initialValue.concept_id) {
            this._showSelected(this.initialValue);
        }
    }

    _render() {
        const e = PlatPursuit.HTMLUtils.escape;
        const disabledAttr = this.disabled ? 'disabled' : '';
        const disabledClass = this.disabled ? 'opacity-50 pointer-events-none' : '';

        this.container.innerHTML = `
            <div class="gbp-wrapper relative ${disabledClass}">
                <!-- Search state -->
                <div class="gbp-search-state">
                    <div class="relative">
                        <input type="text"
                               class="input input-sm input-bordered w-full pl-9 bg-base-200"
                               placeholder="Search your games..."
                               id="gbp-search-${this.container.id}"
                               autocomplete="off"
                               ${disabledAttr} />
                        <svg xmlns="http://www.w3.org/2000/svg" class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-base-content/40" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
                        </svg>
                    </div>
                    <!-- Results / recent games. Single in-flow list (no inner
                         scroll) so the modal or page provides the only scroll. -->
                    <div class="gbp-list mt-2">
                        <p class="gbp-list-label text-[0.65rem] text-base-content/40 uppercase font-medium tracking-wider mb-1.5">Recent Games</p>
                        <div class="gbp-loading hidden flex items-center justify-center py-4">
                            <span class="loading loading-spinner loading-sm"></span>
                        </div>
                        <div class="gbp-empty hidden text-center text-sm text-base-content/50 py-3">
                            No games found
                        </div>
                        <div class="gbp-items grid grid-cols-3 md:grid-cols-4 gap-2"></div>
                    </div>
                </div>

                <!-- Image grid state (two-step image picker, hidden by default) -->
                <div class="gbp-images-state hidden">
                    <div class="flex items-center gap-2 mb-2">
                        <button type="button" class="gbp-images-back btn btn-ghost btn-xs gap-1">
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7"/>
                            </svg>
                            Back
                        </button>
                        <span class="gbp-images-title text-sm font-medium truncate flex-1"></span>
                    </div>
                    <div class="gbp-images-loading flex items-center justify-center py-6">
                        <span class="loading loading-spinner loading-sm"></span>
                    </div>
                    <div class="gbp-images-grid grid grid-cols-2 md:grid-cols-3 gap-2"></div>
                    <div class="gbp-images-empty hidden text-center text-sm text-base-content/50 py-4">
                        No images available for this game
                    </div>
                </div>

                <!-- Selected state (hidden by default) -->
                <div class="gbp-selected-state hidden">
                    <div class="flex items-center gap-2 bg-base-200 border border-base-300 rounded-lg px-3 py-1.5">
                        <div class="w-8 h-8 rounded overflow-hidden flex-shrink-0 border border-base-300">
                            <img class="gbp-selected-icon w-full h-full object-cover" src="" alt="" />
                        </div>
                        <span class="gbp-selected-name text-sm font-medium truncate flex-1"></span>
                        <button type="button" class="gbp-clear-btn btn btn-ghost btn-xs btn-circle" title="Clear selection">
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    _bind() {
        if (this.disabled) return;

        const input = this.container.querySelector(`#gbp-search-${this.container.id}`);
        const clearBtn = this.container.querySelector('.gbp-clear-btn');

        if (!input) return;

        // Debounced search
        const debouncedSearch = PlatPursuit.debounce((query) => {
            this._search(query);
        }, 300);

        input.addEventListener('input', () => {
            const q = input.value.trim();
            if (q.length < 2) {
                // Restore the recent-games list when the query is cleared/short.
                this._showRecentGames();
                return;
            }
            debouncedSearch(q);
        });

        // Clear button
        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                this._clearSelection();
            });
        }

        // Click handler for result/recent cards
        const handleRowClick = (e) => {
            const row = e.target.closest('.gbp-result-row');
            if (!row) return;

            const concept = {
                concept_id: parseInt(row.dataset.conceptId, 10),
                title_name: row.dataset.titleName,
                bg_url: row.dataset.bgUrl,
                icon_url: row.dataset.iconUrl,
            };

            if (this.imagePicker) {
                // Two-step: choose the exact image before finalizing.
                this._openImagePicker(concept);
                return;
            }

            this._showSelected(concept);
            this.selectedConcept = concept;
            this.onSelect(concept);
        };

        // Delegate clicks on the unified items grid
        const items = this.container.querySelector('.gbp-items');
        if (items) items.addEventListener('click', handleRowClick);

        // Image-picker step: back button + image cell clicks
        const backBtn = this.container.querySelector('.gbp-images-back');
        if (backBtn) backBtn.addEventListener('click', () => this._closeImagePicker());

        const imagesGrid = this.container.querySelector('.gbp-images-grid');
        if (imagesGrid) {
            imagesGrid.addEventListener('click', (e) => {
                const cell = e.target.closest('.gbp-image-cell');
                if (!cell || !this._pendingConcept) return;
                this._selectImage(cell.dataset.imageUrl);
            });
        }

        // Load initial browse grid
        this._loadInitialGames();
    }

    /** Build the cards HTML for an array of game results. */
    _itemCardsHtml(results) {
        const e = PlatPursuit.HTMLUtils.escape;
        let html = '';
        for (const item of results) {
            const icon = e(item.icon_url || '');
            const name = e(item.title_name || 'Unknown');
            html += `
                <div class="gbp-result-row cursor-pointer group"
                     data-concept-id="${item.concept_id}"
                     data-title-name="${e(item.title_name || '')}"
                     data-bg-url="${e(item.bg_url || '')}"
                     data-icon-url="${e(item.icon_url || '')}">
                    <div class="aspect-[3/4] rounded-md overflow-hidden border border-base-content/5 bg-base-300 group-hover:border-primary transition-colors">
                        ${icon
                            ? `<img src="${icon}" alt="${name}" class="w-full h-full object-cover object-top" loading="lazy" />`
                            : '<div class="w-full h-full bg-base-300"></div>'
                        }
                    </div>
                    <p class="text-[0.65rem] text-center line-clamp-2 mt-1 px-0.5 text-base-content/70 group-hover:text-base-content transition-colors">${name}</p>
                </div>
            `;
        }
        return html;
    }

    _setListLabel(text) {
        const label = this.container.querySelector('.gbp-list-label');
        if (!label) return;
        if (text) {
            label.textContent = text;
            label.classList.remove('hidden');
        } else {
            label.classList.add('hidden');
        }
    }

    async _search(query) {
        const items = this.container.querySelector('.gbp-items');
        const loading = this.container.querySelector('.gbp-loading');
        const empty = this.container.querySelector('.gbp-empty');
        if (!items) return;

        // Cancel previous request
        if (this._abortController) {
            this._abortController.abort();
        }
        this._abortController = new AbortController();

        this._setListLabel('');
        loading.classList.remove('hidden');
        empty.textContent = 'No games found';
        empty.classList.add('hidden');
        items.innerHTML = '';

        try {
            const reqBg = this.imagePicker ? '&require_bg=0' : '';
            const data = await PlatPursuit.API.get(
                `/api/v1/game-backgrounds/?q=${encodeURIComponent(query)}${reqBg}`,
                { signal: this._abortController.signal }
            );

            // Check if this request was superseded
            if (!data || !data.results) {
                loading.classList.add('hidden');
                return;
            }

            loading.classList.add('hidden');

            if (data.results.length === 0) {
                empty.classList.remove('hidden');
                return;
            }

            items.innerHTML = this._itemCardsHtml(data.results);

        } catch (error) {
            // Ignore abort errors
            if (error.name === 'AbortError') return;
            console.error('[GameBackgroundPicker] Search error:', error);
            loading.classList.add('hidden');

            // Extract meaningful error message from API response
            let message = 'Search failed';
            try {
                const errData = await error.response?.json();
                if (errData?.error) message = errData.error;
            } catch { /* response already consumed or not available */ }

            empty.textContent = message;
            empty.classList.remove('hidden');
        }
    }

    _showSelected(concept) {
        const searchState = this.container.querySelector('.gbp-search-state');
        const imagesState = this.container.querySelector('.gbp-images-state');
        const selectedState = this.container.querySelector('.gbp-selected-state');
        const icon = this.container.querySelector('.gbp-selected-icon');
        const name = this.container.querySelector('.gbp-selected-name');

        if (!searchState || !selectedState) return;

        this.selectedConcept = concept;

        if (icon) {
            // Prefer the exact picked image as the thumbnail, else the game icon.
            const thumb = concept.image_url || concept.icon_url || '';
            icon.src = thumb;
            icon.style.display = thumb ? '' : 'none';
        }
        if (name) {
            name.textContent = concept.title_name || 'Unknown';
        }

        searchState.classList.add('hidden');
        if (imagesState) imagesState.classList.add('hidden');
        selectedState.classList.remove('hidden');
    }

    _openImagePicker(concept) {
        const searchState = this.container.querySelector('.gbp-search-state');
        const imagesState = this.container.querySelector('.gbp-images-state');
        const title = this.container.querySelector('.gbp-images-title');
        if (!searchState || !imagesState) return;

        this._pendingConcept = concept;
        if (title) title.textContent = concept.title_name || 'Choose an image';

        searchState.classList.add('hidden');
        imagesState.classList.remove('hidden');
        this._loadConceptImages(concept.concept_id);
    }

    _closeImagePicker() {
        const searchState = this.container.querySelector('.gbp-search-state');
        const imagesState = this.container.querySelector('.gbp-images-state');
        this._pendingConcept = null;
        if (imagesState) imagesState.classList.add('hidden');
        if (searchState) searchState.classList.remove('hidden');
    }

    async _loadConceptImages(conceptId) {
        const loading = this.container.querySelector('.gbp-images-loading');
        const grid = this.container.querySelector('.gbp-images-grid');
        const empty = this.container.querySelector('.gbp-images-empty');
        if (!grid) return;

        if (this._imagesAbortController) this._imagesAbortController.abort();
        this._imagesAbortController = new AbortController();

        grid.innerHTML = '';
        empty.classList.add('hidden');
        if (loading) loading.classList.remove('hidden');

        try {
            const data = await PlatPursuit.API.get(
                `/api/v1/game-backgrounds/${conceptId}/images/`,
                { signal: this._imagesAbortController.signal }
            );
            if (loading) loading.classList.add('hidden');

            const images = (data && data.images) || [];
            if (images.length === 0) {
                empty.classList.remove('hidden');
                return;
            }

            const e = PlatPursuit.HTMLUtils.escape;
            let html = '';
            for (const url of images) {
                const safeUrl = e(url);
                html += `
                    <button type="button" class="gbp-image-cell aspect-video rounded-md overflow-hidden border border-base-content/10 hover:border-primary focus:border-primary transition-colors"
                            data-image-url="${safeUrl}">
                        <img src="${safeUrl}" alt="" class="w-full h-full object-cover object-top" loading="lazy" />
                    </button>
                `;
            }
            grid.innerHTML = html;
        } catch (error) {
            if (error.name === 'AbortError') return;
            if (loading) loading.classList.add('hidden');
            empty.textContent = 'Could not load images';
            empty.classList.remove('hidden');
        }
    }

    _selectImage(imageUrl) {
        if (!this._pendingConcept || !imageUrl) return;
        const concept = { ...this._pendingConcept, image_url: imageUrl };
        this._pendingConcept = null;
        this._showSelected(concept);
        this.selectedConcept = concept;
        this.onSelect(concept);
    }

    _clearSelection() {
        const searchState = this.container.querySelector('.gbp-search-state');
        const imagesState = this.container.querySelector('.gbp-images-state');
        const selectedState = this.container.querySelector('.gbp-selected-state');
        const input = this.container.querySelector(`#gbp-search-${this.container.id}`);

        if (!searchState || !selectedState) return;

        this.selectedConcept = null;
        this._pendingConcept = null;

        selectedState.classList.add('hidden');
        if (imagesState) imagesState.classList.add('hidden');
        searchState.classList.remove('hidden');

        if (input) {
            input.value = '';
            input.focus();
        }
        // Back to the recent-games list now that the query is empty.
        this._showRecentGames();

        this.onClear();
    }

    /** Restore the cached recent-games list (re-fetches if not yet loaded). */
    _showRecentGames() {
        const items = this.container.querySelector('.gbp-items');
        const empty = this.container.querySelector('.gbp-empty');
        if (this._recentItemsHtml != null && items) {
            empty.classList.add('hidden');
            this._setListLabel('Recent Games');
            items.innerHTML = this._recentItemsHtml;
        } else {
            this._loadInitialGames();
        }
    }

    async _loadInitialGames() {
        const items = this.container.querySelector('.gbp-items');
        const loading = this.container.querySelector('.gbp-loading');
        const empty = this.container.querySelector('.gbp-empty');
        if (!items) return;

        this._setListLabel('Recent Games');
        empty.classList.add('hidden');
        loading.classList.remove('hidden');
        items.innerHTML = '';

        try {
            const browseUrl = this.imagePicker
                ? '/api/v1/game-backgrounds/?require_bg=0'
                : '/api/v1/game-backgrounds/';
            const data = await PlatPursuit.API.get(browseUrl);
            loading.classList.add('hidden');

            if (!data || !data.results || data.results.length === 0) {
                empty.textContent = 'No eligible games found';
                empty.classList.remove('hidden');
                return;
            }

            this._recentItemsHtml = this._itemCardsHtml(data.results);
            items.innerHTML = this._recentItemsHtml;
        } catch (error) {
            loading.classList.add('hidden');
            empty.textContent = 'Could not load games';
            empty.classList.remove('hidden');
        }
    }

    /** Get the currently selected concept, or null */
    getSelected() {
        return this.selectedConcept;
    }

    /** Programmatically clear the selection */
    clear() {
        this._clearSelection();
    }

    /** Abort any in-flight requests when re-creating the picker. */
    destroy() {
        if (this._abortController) {
            this._abortController.abort();
            this._abortController = null;
        }
        if (this._imagesAbortController) {
            this._imagesAbortController.abort();
            this._imagesAbortController = null;
        }
    }
}

// Export for global access
window.GameBackgroundPicker = GameBackgroundPicker;
