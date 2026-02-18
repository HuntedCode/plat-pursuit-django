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
     * @param {Object|null} options.initialValue - Pre-selected value: { concept_id, title_name, icon_url }
     * @param {boolean} options.disabled - Whether the picker is disabled (non-premium)
     */
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        if (!this.container) return;

        this.onSelect = options.onSelect || (() => {});
        this.onClear = options.onClear || (() => {});
        this.initialValue = options.initialValue || null;
        this.disabled = options.disabled || false;
        this.selectedConcept = null;
        this._abortController = null;
        this._onDocumentClick = null;

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
                    <!-- Dropdown results -->
                    <div class="gbp-dropdown hidden absolute z-50 w-full mt-1 bg-base-200 border border-base-300 rounded-lg shadow-lg max-h-60 overflow-y-auto">
                        <div class="gbp-results"></div>
                        <div class="gbp-loading hidden flex items-center justify-center py-3">
                            <span class="loading loading-spinner loading-sm"></span>
                        </div>
                        <div class="gbp-empty hidden text-center text-sm text-base-content/50 py-3">
                            No games found
                        </div>
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
        const dropdown = this.container.querySelector('.gbp-dropdown');
        const clearBtn = this.container.querySelector('.gbp-clear-btn');

        if (!input) return;

        // Debounced search
        const debouncedSearch = PlatPursuit.debounce((query) => {
            this._search(query);
        }, 300);

        input.addEventListener('input', () => {
            const q = input.value.trim();
            if (q.length < 2) {
                this._hideDropdown();
                return;
            }
            debouncedSearch(q);
        });

        // Show dropdown on focus if there's text
        input.addEventListener('focus', () => {
            const q = input.value.trim();
            if (q.length > 0) {
                const results = this.container.querySelector('.gbp-results');
                if (results && results.children.length > 0) {
                    dropdown.classList.remove('hidden');
                }
            }
        });

        // Click outside to close (stored for cleanup in destroy())
        this._onDocumentClick = (e) => {
            if (!this.container.contains(e.target)) {
                this._hideDropdown();
            }
        };
        document.addEventListener('click', this._onDocumentClick);

        // Clear button
        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                this._clearSelection();
            });
        }

        // Delegate clicks on result rows
        const resultsContainer = this.container.querySelector('.gbp-results');
        if (resultsContainer) {
            resultsContainer.addEventListener('click', (e) => {
                const row = e.target.closest('.gbp-result-row');
                if (!row) return;

                const concept = {
                    concept_id: parseInt(row.dataset.conceptId, 10),
                    title_name: row.dataset.titleName,
                    bg_url: row.dataset.bgUrl,
                    icon_url: row.dataset.iconUrl,
                };

                this._showSelected(concept);
                this.selectedConcept = concept;
                this.onSelect(concept);
                this._hideDropdown();
            });
        }
    }

    async _search(query) {
        const dropdown = this.container.querySelector('.gbp-dropdown');
        const results = this.container.querySelector('.gbp-results');
        const loading = this.container.querySelector('.gbp-loading');
        const empty = this.container.querySelector('.gbp-empty');

        if (!dropdown || !results) return;

        // Cancel previous request
        if (this._abortController) {
            this._abortController.abort();
        }
        this._abortController = new AbortController();

        dropdown.classList.remove('hidden');
        loading.classList.remove('hidden');
        empty.textContent = 'No games found';
        empty.classList.add('hidden');
        results.innerHTML = '';

        try {
            const data = await PlatPursuit.API.get(
                `/api/v1/game-backgrounds/?q=${encodeURIComponent(query)}`,
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

            const e = PlatPursuit.HTMLUtils.escape;
            let html = '';
            for (const item of data.results) {
                const icon = e(item.icon_url || '');
                const name = e(item.title_name || 'Unknown');
                html += `
                    <div class="gbp-result-row flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-base-300 transition-colors"
                         data-concept-id="${item.concept_id}"
                         data-title-name="${e(item.title_name || '')}"
                         data-bg-url="${e(item.bg_url || '')}"
                         data-icon-url="${e(item.icon_url || '')}">
                        <div class="w-8 h-8 rounded overflow-hidden flex-shrink-0 border border-base-300">
                            ${icon
                                ? `<img src="${icon}" alt="" class="w-full h-full object-cover" loading="lazy" />`
                                : '<div class="w-full h-full bg-base-300"></div>'
                            }
                        </div>
                        <span class="text-sm truncate">${name}</span>
                    </div>
                `;
            }
            results.innerHTML = html;

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
        const selectedState = this.container.querySelector('.gbp-selected-state');
        const icon = this.container.querySelector('.gbp-selected-icon');
        const name = this.container.querySelector('.gbp-selected-name');

        if (!searchState || !selectedState) return;

        this.selectedConcept = concept;

        if (icon) {
            icon.src = concept.icon_url || '';
            icon.style.display = concept.icon_url ? '' : 'none';
        }
        if (name) {
            name.textContent = concept.title_name || 'Unknown';
        }

        searchState.classList.add('hidden');
        selectedState.classList.remove('hidden');
    }

    _clearSelection() {
        const searchState = this.container.querySelector('.gbp-search-state');
        const selectedState = this.container.querySelector('.gbp-selected-state');
        const input = this.container.querySelector(`#gbp-search-${this.container.id}`);

        if (!searchState || !selectedState) return;

        this.selectedConcept = null;

        selectedState.classList.add('hidden');
        searchState.classList.remove('hidden');

        if (input) {
            input.value = '';
            input.focus();
        }

        this.onClear();
    }

    _hideDropdown() {
        const dropdown = this.container.querySelector('.gbp-dropdown');
        if (dropdown) dropdown.classList.add('hidden');
    }

    /** Get the currently selected concept, or null */
    getSelected() {
        return this.selectedConcept;
    }

    /** Programmatically clear the selection */
    clear() {
        this._clearSelection();
    }

    /** Remove document-level listeners to prevent leaks when re-creating the picker */
    destroy() {
        if (this._onDocumentClick) {
            document.removeEventListener('click', this._onDocumentClick);
            this._onDocumentClick = null;
        }
        if (this._abortController) {
            this._abortController.abort();
            this._abortController = null;
        }
    }
}

// Export for global access
window.GameBackgroundPicker = GameBackgroundPicker;
