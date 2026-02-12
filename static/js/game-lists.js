/**
 * Game Lists JavaScript
 *
 * Handles:
 * - GameListEditor: edit page (game search, add/remove, reorder via drag-and-drop,
 *   inline note editing, settings toggle, modal confirmations)
 * - GameListDetail: detail page (like toggle, copy list)
 * - GameListQuickAdd: quick-add dropdown from game detail/card pages
 *
 * Uses: PlatPursuit.API, PlatPursuit.ToastManager, PlatPursuit.HTMLUtils, PlatPursuit.debounce
 */

// Shared SVG constants
const TROPHY_CUP_SVG = '<svg class="w-3 h-3 inline-block flex-shrink-0" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" aria-hidden="true"><path d="M102.49,0c0,27.414,0,104.166,0,137.062c0,112.391,99.33,156.25,153.51,156.25c54.18,0,153.51-43.859,153.51-156.25c0-32.896,0-109.648,0-137.062H102.49z M256.289,50.551l-68.164,29.768v98.474l-0.049,19.53c-0.526-0.112-47.274-10.112-47.274-78.391c0-28.17,0-69.6,0-69.6h60.385L256.289,50.551z"/><polygon points="315.473,400.717 291.681,367.482 279.791,318.506 256,322.004 232.209,318.506 220.314,367.482 205.347,388.394 196.527,400.476 196.699,400.476 196.527,400.717"/><polygon points="366.93,432.24 366.93,432 145.07,432 145.07,511.598 145.07,511.76 145.07,511.76 145.07,512 366.93,512 366.93,432.402 366.93,432.24"/><path d="M511.638,96.668c-0.033-1.268-0.068-2.336-0.068-3.174V45.1h-73.889v38.736h35.152v9.658c0,1.127,0.037,2.557,0.086,4.258c0.389,13.976,1.303,46.707-21.545,70.203c-5.121,5.266-11.221,9.787-18.219,13.613c-3.883,17.635-10.109,33.564-18.104,47.814c26.561-6.406,48.026-17.898,64.096-34.422C513.402,159.734,512.121,113.918,511.638,96.668z"/><path d="M60.625,167.955c-22.848-23.496-21.934-56.227-21.541-70.203c0.047-1.701,0.082-3.131,0.082-4.258v-9.658h34.842h0.07l0,0h0.24V45.1H0.43v48.394c0,0.838-0.032,1.906-0.068,3.174c-0.482,17.25-1.76,63.066,32.494,98.293c16.068,16.524,37.531,28.014,64.092,34.422c-7.996-14.25-14.22-30.182-18.103-47.816C71.846,177.74,65.746,173.221,60.625,167.955z"/></svg>';
const GLOBE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 512 512" stroke="var(--color-success)" fill="var(--color-success)"><path d="M351.9 280l-190.9 0c2.9 64.5 17.2 123.9 37.5 167.4 11.4 24.5 23.7 41.8 35.1 52.4 11.2 10.5 18.9 12.2 22.9 12.2s11.7-1.7 22.9-12.2c11.4-10.6 23.7-28 35.1-52.4 20.3-43.5 34.6-102.9 37.5-167.4zM160.9 232l190.9 0C349 167.5 334.7 108.1 314.4 64.6 303 40.2 290.7 22.8 279.3 12.2 268.1 1.7 260.4 0 256.4 0s-11.7 1.7-22.9 12.2c-11.4 10.6-23.7 28-35.1 52.4-20.3 43.5-34.6 102.9-37.5 167.4zm-48 0C116.4 146.4 138.5 66.9 170.8 14.7 78.7 47.3 10.9 131.2 1.5 232l111.4 0zM1.5 280c9.4 100.8 77.2 184.7 169.3 217.3-32.3-52.2-54.4-131.7-57.9-217.3L1.5 280zm398.4 0c-3.5 85.6-25.6 165.1-57.9 217.3 92.1-32.7 159.9-116.5 169.3-217.3l-111.4 0zm111.4-48C501.9 131.2 434.1 47.3 342 14.7 374.3 66.9 396.4 146.4 399.9 232l111.4 0z"/></svg>';
const REGION_COLORS = { NA: 'badge-primary', EU: 'badge-secondary', JP: 'badge-accent' };

function trophyCup(color) {
    return TROPHY_CUP_SVG.replace('aria-hidden', `fill="${color}" aria-hidden`);
}

// ============================================================
// Game List Detail Page (like, copy)
// ============================================================
const GameListDetail = {
    listId: null,

    init(listId) {
        this.listId = listId;
        this._bindLikeButton();
        this._bindCopyButton();
    },

    _bindLikeButton() {
        const btn = document.getElementById('like-btn');
        if (!btn) return;

        btn.addEventListener('click', async () => {
            try {
                const data = await PlatPursuit.API.post(`/api/v1/lists/${this.listId}/like/`, {});
                document.getElementById('like-count').textContent = data.like_count;

                const svg = btn.querySelector('svg');
                if (data.liked) {
                    btn.classList.remove('btn-outline');
                    btn.classList.add('btn-error');
                    svg.setAttribute('fill', 'currentColor');
                } else {
                    btn.classList.add('btn-outline');
                    btn.classList.remove('btn-error');
                    btn.classList.add('btn-error');
                    svg.setAttribute('fill', 'none');
                }

                const textNode = Array.from(btn.childNodes).find(n => n.nodeType === 3);
                if (textNode) textNode.textContent = data.liked ? ' Liked' : ' Like';

            } catch (err) {
                PlatPursuit.ToastManager.error('Failed to update like.');
            }
        });
    },

    _bindCopyButton() {
        const btn = document.getElementById('copy-btn');
        if (!btn) return;

        btn.addEventListener('click', async () => {
            if (!confirm('Copy this list to your account?')) return;
            try {
                const data = await PlatPursuit.API.post(`/api/v1/lists/${this.listId}/copy/`, {});
                if (data.success) {
                    PlatPursuit.ToastManager.success('List copied! Redirecting...');
                    setTimeout(() => {
                        window.location.href = `/lists/${data.list.id}/edit/`;
                    }, 1000);
                }
            } catch (err) {
                let msg = 'Failed to copy list.';
                try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
                PlatPursuit.ToastManager.error(msg);
            }
        });
    }
};


// ============================================================
// Game List Editor (edit page)
// ============================================================
const GameListEditor = {
    listId: null,
    isPremium: false,
    _pendingRemoveId: null,
    _pendingRemoveCard: null,

    init(listId, isPremium) {
        this.listId = listId;
        this.isPremium = isPremium;
        this._bindInlineEdit();
        this._bindVisibilityToggle();
        this._bindDeleteButton();
        this._bindGameSearch();
        this._bindDragAndDrop();
        this._bindPositionInputs();
        this._bindRemoveButtons();
        this._bindNoteEditing();
        this._bindSettingsToggle();
        this._bindThemeGrid();
    },

    // --- Settings Toggle ---
    _bindSettingsToggle() {
        const btn = document.getElementById('settings-toggle-btn');
        const panel = document.getElementById('list-settings-collapse');
        if (!btn || !panel) return;

        btn.addEventListener('click', () => {
            panel.classList.toggle('hidden');
            if (!panel.classList.contains('hidden')) {
                panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        });
    },

    // --- Inline Edit ---
    _bindInlineEdit() {
        const nameInput = document.getElementById('list-name');
        const descInput = document.getElementById('list-description');
        const nameCounter = document.getElementById('name-counter');
        const descCounter = document.getElementById('desc-counter');

        if (nameInput) {
            nameInput.addEventListener('input', () => {
                nameCounter.textContent = `${nameInput.value.length}/200`;
            });
            nameInput.addEventListener('blur', () => this._saveField('name', nameInput.value));
        }
        if (descInput) {
            descInput.addEventListener('input', () => {
                descCounter.textContent = descInput.value.length;
            });
            descInput.addEventListener('blur', () => this._saveField('description', descInput.value));
        }
    },

    async _saveField(field, value) {
        try {
            await PlatPursuit.API.patch(`/api/v1/lists/${this.listId}/update/`, { [field]: value });
        } catch (err) {
            PlatPursuit.ToastManager.error(`Failed to save ${field}.`);
        }
    },

    // --- Visibility Toggle ---
    _bindVisibilityToggle() {
        const toggle = document.getElementById('list-public-toggle');
        if (!toggle) return;

        toggle.addEventListener('change', async () => {
            try {
                await PlatPursuit.API.patch(`/api/v1/lists/${this.listId}/update/`, { is_public: toggle.checked });

                // Update visibility badge in header
                const badge = document.getElementById('visibility-badge');
                if (badge) {
                    if (toggle.checked) {
                        badge.className = 'badge badge-success badge-xs gap-1';
                        badge.innerHTML = `
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                                <circle cx="12" cy="12" r="3"></circle>
                            </svg>
                            Public`;
                    } else {
                        badge.className = 'badge badge-warning badge-xs gap-1';
                        badge.innerHTML = `
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                                <path d="M7 11V7a5 5 0 0110 0v4"></path>
                            </svg>
                            Private`;
                    }
                }

                PlatPursuit.ToastManager.success(toggle.checked ? 'List is now public.' : 'List is now private.');
            } catch (err) {
                toggle.checked = !toggle.checked;
                let msg = 'Failed to update visibility.';
                try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
                PlatPursuit.ToastManager.error(msg);
            }
        });
    },

    // --- Delete (with modal) ---
    _bindDeleteButton() {
        const btn = document.getElementById('delete-list-btn');
        const modal = document.getElementById('delete-list-modal');
        const confirmBtn = document.getElementById('confirm-delete-btn');
        if (!btn || !modal || !confirmBtn) return;

        btn.addEventListener('click', () => {
            modal.showModal();
        });

        confirmBtn.addEventListener('click', async () => {
            try {
                await PlatPursuit.API.request(`/api/v1/lists/${this.listId}/delete/`, {
                    method: 'DELETE',
                });
                modal.close();
                PlatPursuit.ToastManager.success('List deleted.');
                window.location.href = '/my-lists/';
            } catch (err) {
                modal.close();
                PlatPursuit.ToastManager.error('Failed to delete list.');
            }
        });
    },

    // --- Game Search ---
    _bindGameSearch() {
        const input = document.getElementById('game-search-input');
        const results = document.getElementById('game-search-results');
        const loading = document.getElementById('search-loading');
        if (!input || !results) return;

        const debouncedSearch = PlatPursuit.debounce(async (query) => {
            if (query.length < 2) {
                results.classList.add('hidden');
                if (loading) loading.classList.add('hidden');
                return;
            }
            if (loading) loading.classList.remove('hidden');
            try {
                const data = await PlatPursuit.API.get(
                    `/api/v1/games/search/?q=${encodeURIComponent(query)}&limit=10&exclude_list=${this.listId}`
                );
                this._renderSearchResults(data.results, results);
            } catch (err) {
                results.classList.add('hidden');
            }
            if (loading) loading.classList.add('hidden');
        }, 300);

        input.addEventListener('input', () => debouncedSearch(input.value.trim()));

        // Close on outside click
        document.addEventListener('click', (e) => {
            if (!input.contains(e.target) && !results.contains(e.target)) {
                results.classList.add('hidden');
            }
        });

        // Close on Escape
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                results.classList.add('hidden');
                input.blur();
            }
        });
    },

    _renderSearchResults(games, container) {
        if (!games.length) {
            container.innerHTML = '<div class="p-4 text-center text-base-content/50">No games found</div>';
            container.classList.remove('hidden');
            return;
        }

        const html = games.map(game => {
            const platforms = (game.title_platform || []).map(p =>
                `<span class="badge badge-ghost badge-xs font-bold">${PlatPursuit.HTMLUtils.escape(p)}</span>`
            ).join('');

            const disabled = game.already_in_list ? 'opacity-50 pointer-events-none' : 'cursor-pointer hover:bg-base-200';
            const label = game.already_in_list ? '<span class="badge badge-xs badge-success">In list</span>' : '';

            return `
                <div class="flex items-center gap-3 p-3 border-b border-base-200 ${disabled} game-search-result"
                     data-game-id="${game.id}" data-game-name="${PlatPursuit.HTMLUtils.escape(game.title_name)}">
                    <img src="${game.title_icon_url || game.title_image}" alt="" class="w-10 h-10 rounded object-cover flex-shrink-0" />
                    <div class="flex-1 min-w-0">
                        <p class="font-medium text-sm truncate">${PlatPursuit.HTMLUtils.escape(game.title_name)}</p>
                        <div class="flex gap-1 mt-1">${platforms}</div>
                    </div>
                    ${label}
                </div>
            `;
        }).join('');

        container.innerHTML = html;
        container.classList.remove('hidden');

        // Bind click to add
        container.querySelectorAll('.game-search-result:not(.opacity-50)').forEach(el => {
            el.addEventListener('click', () => this._addGame(el.dataset.gameId, el.dataset.gameName));
        });
    },

    /**
     * Build a game item card HTML string from API JSON data.
     * NOTE: This mirrors templates/partials/game_list_item_card.html (edit mode).
     * Update both in tandem when changing the card layout.
     */
    _buildGameItemCardHTML(item) {
        const e = PlatPursuit.HTMLUtils.escape;
        const imageUrl = item.display_image || item.title_icon_url || item.title_image;

        const platforms = (item.title_platform || []).map(p =>
            `<span class="badge badge-ghost badge-xs font-bold">${e(p)}</span>`
        ).join('');

        // Region badges
        let regionHTML = '';
        if (item.is_regional && item.region?.length) {
            regionHTML = item.region.map(r =>
                `<span class="badge badge-xs ${REGION_COLORS[r] || 'badge-warning'} font-bold italic">${e(r)}</span>`
            ).join('');
        } else {
            regionHTML = `<span class="badge badge-xs badge-ghost">${GLOBE_SVG}</span>`;
        }

        // Trophy icons
        const trophies = item.defined_trophies || {};
        let trophyHTML = '';
        if (trophies.gold !== undefined) {
            const platIcon = trophies.platinum
                ? `<span class="flex items-center gap-0.5" style="color: var(--color-trophy-platinum);">${trophyCup('var(--color-trophy-platinum)')}${trophies.platinum}</span>`
                : '';
            trophyHTML = `<span class="text-xs flex items-center gap-1">
                ${platIcon}
                <span class="flex items-center gap-0.5" style="color: var(--color-trophy-gold);">${trophyCup('var(--color-trophy-gold)')}${trophies.gold}</span>
                <span class="flex items-center gap-0.5" style="color: var(--color-trophy-silver);">${trophyCup('var(--color-trophy-silver)')}${trophies.silver}</span>
                <span class="flex items-center gap-0.5" style="color: var(--color-trophy-bronze);">${trophyCup('var(--color-trophy-bronze)')}${trophies.bronze}</span>
            </span>`;
        }

        let noteSection;
        if (this.isPremium) {
            noteSection = `
                <div class="note-display-container" data-item-id="${item.id}">
                    <button class="btn btn-ghost btn-xs text-base-content/40 note-add-btn gap-1" data-item-id="${item.id}">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/>
                        </svg>
                        Add note
                    </button>
                </div>
                <div class="note-edit-container hidden mt-1" data-item-id="${item.id}">
                    <textarea class="textarea textarea-bordered textarea-xs w-full note-input"
                              maxlength="500" rows="2"
                              placeholder="Add a personal note..."
                              data-item-id="${item.id}"></textarea>
                    <div class="flex items-center justify-between mt-1">
                        <span class="text-xs text-base-content/40"><span class="note-char-count">0</span>/500</span>
                        <div class="flex gap-1">
                            <button class="btn btn-ghost btn-xs note-cancel-btn" data-item-id="${item.id}">Cancel</button>
                            <button class="btn btn-primary btn-xs note-save-btn" data-item-id="${item.id}">Save</button>
                        </div>
                    </div>
                </div>`;
        } else {
            noteSection = `
                <span class="text-xs text-base-content/30 italic flex items-center gap-1">
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/>
                    </svg>
                    <a href="/beta-access/" class="link link-primary text-xs">Premium</a>: Add personal notes
                </span>`;
        }

        return `
        <div class="card bg-base-100 border-2 border-base-300 shadow-md transition-all duration-200 hover:shadow-lg group game-list-item-card"
             data-item-id="${item.id}" data-position="${item.position}" draggable="true">
            <div class="card-body p-3 sm:p-4">
                <div class="flex gap-3 items-start">
                    <div class="flex-col items-center justify-center gap-1 flex-shrink-0 cursor-grab active:cursor-grabbing drag-handle hidden sm:flex pt-3">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5 text-base-content/30" viewBox="0 0 24 24" fill="currentColor">
                            <circle cx="9" cy="5" r="1.5"/><circle cx="15" cy="5" r="1.5"/>
                            <circle cx="9" cy="10" r="1.5"/><circle cx="15" cy="10" r="1.5"/>
                            <circle cx="9" cy="15" r="1.5"/><circle cx="15" cy="15" r="1.5"/>
                        </svg>
                        <span class="text-xs font-mono text-base-content/40 position-label">#${item.position + 1}</span>
                    </div>
                    <a href="/games/${e(item.np_communication_id)}/" class="flex-shrink-0">
                        <figure class="w-16 h-16 sm:w-20 sm:h-20 rounded-lg overflow-hidden border-2 border-base-300">
                            <img src="${imageUrl}" alt="${e(item.title_name)}" class="w-full h-full object-cover" loading="lazy" />
                        </figure>
                    </a>
                    <div class="flex-1 min-w-0">
                        <a href="/games/${e(item.np_communication_id)}/" class="hover:underline">
                            <h3 class="font-bold text-sm sm:text-base line-clamp-1">${e(item.title_name)}</h3>
                        </a>
                        <div class="flex flex-wrap items-center gap-2 mt-1">
                            ${platforms}
                            ${regionHTML}
                            ${trophyHTML}
                        </div>
                        <div class="mt-2">${noteSection}</div>
                    </div>
                    <div class="flex flex-col items-center gap-1 flex-shrink-0">
                        <input type="number"
                               class="input input-bordered input-xs w-12 text-center font-mono game-list-position-input"
                               data-item-id="${item.id}" value="${item.position + 1}" min="1" title="Jump to position" />
                        <button class="btn btn-ghost btn-xs btn-square text-error game-list-remove-btn"
                                data-item-id="${item.id}" data-game-name="${e(item.title_name)}" title="Remove from list">
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
        </div>`;
    },

    async _addGame(gameId, gameName) {
        const results = document.getElementById('game-search-results');
        const input = document.getElementById('game-search-input');

        try {
            const data = await PlatPursuit.API.post(`/api/v1/lists/${this.listId}/items/`, {
                game_id: parseInt(gameId),
            });

            if (data.success) {
                PlatPursuit.ToastManager.success(`Added "${gameName}"`);
                results.classList.add('hidden');
                input.value = '';

                // Update count displays
                this._updateGameCount(data.game_count);

                // Remove empty state
                const emptyState = document.getElementById('empty-state');
                if (emptyState) emptyState.remove();

                // Append new card dynamically
                const container = document.getElementById('game-list-items');
                container.insertAdjacentHTML('beforeend', this._buildGameItemCardHTML(data.item));

                // Scroll into view
                container.lastElementChild.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        } catch (err) {
            let msg = 'Failed to add game.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);
        }
    },

    _updateGameCount(count) {
        const badge = document.getElementById('game-count-badge');
        if (badge) badge.textContent = count;

        const display = document.getElementById('item-count-display');
        if (display) {
            if (this.isPremium) {
                display.textContent = `${count} games`;
            } else {
                display.textContent = `${count}/100 games`;
            }
        }
    },

    // --- Drag and Drop ---
    _bindDragAndDrop() {
        const container = document.getElementById('game-list-items');
        if (!container) return;

        let draggedEl = null;
        let placeholder = null;

        container.addEventListener('dragstart', (e) => {
            const card = e.target.closest('.game-list-item-card');
            if (!card) return;

            draggedEl = card;
            card.classList.add('opacity-50', 'border-primary');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', card.dataset.itemId);

            placeholder = document.createElement('div');
            placeholder.className = 'border-2 border-dashed border-primary rounded-box h-16 bg-primary/5 transition-all';
        });

        container.addEventListener('dragover', (e) => {
            e.preventDefault();
            if (!draggedEl || !placeholder) return;
            e.dataTransfer.dropEffect = 'move';

            const afterEl = this._getDragAfterElement(container, e.clientY);
            if (afterEl) {
                container.insertBefore(placeholder, afterEl);
            } else {
                container.appendChild(placeholder);
            }
        });

        container.addEventListener('dragend', () => {
            if (draggedEl) {
                draggedEl.classList.remove('opacity-50', 'border-primary');
            }
            if (placeholder && placeholder.parentNode) {
                placeholder.remove();
            }
            draggedEl = null;
            placeholder = null;
        });

        container.addEventListener('drop', (e) => {
            e.preventDefault();
            if (!draggedEl || !placeholder) return;

            // Insert dragged element where placeholder is
            placeholder.replaceWith(draggedEl);
            draggedEl.classList.remove('opacity-50', 'border-primary');

            // Calculate new position from DOM order
            const cards = [...container.querySelectorAll('.game-list-item-card')];
            const newPosition = cards.indexOf(draggedEl);
            const itemId = parseInt(draggedEl.dataset.itemId);

            // Update all position labels
            this._refreshPositionLabels();

            // Save to server
            this._reorder(itemId, newPosition);

            draggedEl = null;
            placeholder = null;
        });
    },

    _getDragAfterElement(container, y) {
        const cards = [...container.querySelectorAll('.game-list-item-card:not(.opacity-50)')];
        const result = cards.reduce((closest, child) => {
            const box = child.getBoundingClientRect();
            const offset = y - box.top - box.height / 2;
            if (offset < 0 && offset > closest.offset) {
                return { offset, element: child };
            }
            return closest;
        }, { offset: Number.NEGATIVE_INFINITY });
        return result.element || null;
    },

    _refreshPositionLabels() {
        const container = document.getElementById('game-list-items');
        if (!container) return;
        const cards = container.querySelectorAll('.game-list-item-card');
        cards.forEach((card, idx) => {
            card.dataset.position = idx;
            const posLabel = card.querySelector('.position-label');
            if (posLabel) posLabel.textContent = `#${idx + 1}`;
            const posInput = card.querySelector('.game-list-position-input');
            if (posInput) posInput.value = idx + 1;
        });
    },

    async _reorder(itemId, newPosition) {
        try {
            const data = await PlatPursuit.API.post(`/api/v1/lists/${this.listId}/items/reorder/`, {
                item_id: itemId,
                new_position: newPosition,
            });
            if (!data.success) {
                PlatPursuit.ToastManager.error('Failed to reorder.');
            }
        } catch (err) {
            PlatPursuit.ToastManager.error('Failed to reorder.');
        }
    },

    // --- Position Input ---
    _bindPositionInputs() {
        const container = document.getElementById('game-list-items');
        if (!container) return;

        container.addEventListener('change', async (e) => {
            if (!e.target.classList.contains('game-list-position-input')) return;

            const itemId = parseInt(e.target.dataset.itemId);
            const newPos = parseInt(e.target.value) - 1; // 1-indexed to 0-indexed

            if (isNaN(newPos) || newPos < 0) {
                PlatPursuit.ToastManager.error('Invalid position.');
                return;
            }

            try {
                const data = await PlatPursuit.API.post(`/api/v1/lists/${this.listId}/items/reorder/`, {
                    item_id: itemId,
                    new_position: newPos,
                });

                if (data.success) {
                    // Move the card in the DOM
                    const card = container.querySelector(`[data-item-id="${itemId}"]`);
                    if (!card) return;

                    card.remove();
                    const cards = [...container.querySelectorAll('.game-list-item-card')];
                    const targetIdx = Math.min(data.position, cards.length);

                    if (targetIdx >= cards.length) {
                        container.appendChild(card);
                    } else {
                        container.insertBefore(card, cards[targetIdx]);
                    }

                    this._refreshPositionLabels();
                }
            } catch (err) {
                PlatPursuit.ToastManager.error('Failed to reorder.');
            }
        });
    },

    // --- Remove Buttons (with modal) ---
    _bindRemoveButtons() {
        const modal = document.getElementById('remove-game-modal');
        const confirmBtn = document.getElementById('confirm-remove-btn');
        const nameEl = document.getElementById('remove-game-name');
        if (!modal || !confirmBtn || !nameEl) return;

        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.game-list-remove-btn');
            if (!btn) return;
            e.preventDefault();

            this._pendingRemoveId = parseInt(btn.dataset.itemId);
            this._pendingRemoveCard = btn.closest('.game-list-item-card');
            nameEl.textContent = btn.dataset.gameName;
            modal.showModal();
        });

        confirmBtn.addEventListener('click', async () => {
            modal.close();
            if (this._pendingRemoveId && this._pendingRemoveCard) {
                await this._removeItem(this._pendingRemoveId, this._pendingRemoveCard);
            }
            this._pendingRemoveId = null;
            this._pendingRemoveCard = null;
        });
    },

    async _removeItem(itemId, cardEl) {
        try {
            const data = await PlatPursuit.API.request(
                `/api/v1/lists/${this.listId}/items/${itemId}/`,
                { method: 'DELETE' }
            );

            if (data.success) {
                cardEl.remove();
                this._updateGameCount(data.game_count);
                this._refreshPositionLabels();
                PlatPursuit.ToastManager.success('Game removed.');

                // Show empty state if no items left
                const container = document.getElementById('game-list-items');
                if (container && container.children.length === 0) {
                    container.insertAdjacentHTML('afterend', `
                        <div class="text-center py-12" id="empty-state">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-16 w-16 mx-auto text-base-content/20" viewBox="0 0 16 16" fill="currentColor">
                                <path d="M11.5 6.027a.5.5 0 1 1-1 0 .5.5 0 0 1 1 0m-1.5 1.5a.5.5 0 1 0 0-1 .5.5 0 0 0 0 1m2.5-.5a.5.5 0 1 1-1 0 .5.5 0 0 1 1 0m-1.5 1.5a.5.5 0 1 0 0-1 .5.5 0 0 0 0 1m-6.5-3h1v1h1v1h-1v1h-1v-1h-1v-1h1z"></path>
                                <path d="M3.051 3.26a.5.5 0 0 1 .354-.613l1.932-.518a.5.5 0 0 1 .62.39c.655-.079 1.35-.117 2.043-.117.72 0 1.443.041 2.12.126a.5.5 0 0 1 .622-.399l1.932.518a.5.5 0 0 1 .306.729q.211.136.373.297c.408.408.78 1.05 1.095 1.772.32.733.599 1.591.805 2.466s.34 1.78.364 2.606c.024.816-.059 1.602-.328 2.21a1.42 1.42 0 0 1-1.445.83c-.636-.067-1.115-.394-1.513-.773-.245-.232-.496-.526-.739-.808-.126-.148-.25-.292-.368-.423-.728-.804-1.597-1.527-3.224-1.527s-2.496.723-3.224 1.527c-.119.131-.242.275-.368.423-.243.282-.494.575-.739.808-.398.38-.877.706-1.513.773a1.42 1.42 0 0 1-1.445-.83c-.27-.608-.352-1.395-.329-2.21.024-.826.16-1.73.365-2.606.206-.875.486-1.733.805-2.466.315-.722.687-1.364 1.094-1.772a2.3 2.3 0 0 1 .433-.335l-.028-.079zm2.036.412c-.877.185-1.469.443-1.733.708-.276.276-.587.783-.885 1.465a14 14 0 0 0-.748 2.295 12.4 12.4 0 0 0-.339 2.406c-.022.755.062 1.368.243 1.776a.42.42 0 0 0 .426.24c.327-.034.61-.199.929-.502.212-.202.4-.423.615-.674.133-.156.276-.323.44-.504C4.861 9.969 5.978 9.027 8 9.027s3.139.942 3.965 1.855c.164.181.307.348.44.504.214.251.403.472.615.674.318.303.601.468.929.503a.42.42 0 0 0 .426-.241c.18-.408.265-1.02.243-1.776a12.4 12.4 0 0 0-.339-2.406 14 14 0 0 0-.748-2.295c-.298-.682-.61-1.19-.885-1.465-.264-.265-.856-.523-1.733-.708-.85-.179-1.877-.27-2.913-.27s-2.063.091-2.913.27"></path>
                            </svg>
                            <p class="text-base-content/50 mt-4 text-lg font-semibold">No games yet</p>
                            <p class="text-base-content/40 text-sm mt-1">Search above to start building your list</p>
                        </div>
                    `);
                }
            }
        } catch (err) {
            PlatPursuit.ToastManager.error('Failed to remove game.');
        }
    },

    // --- Note Editing ---
    _bindNoteEditing() {
        if (!this.isPremium) return;

        // Open note editor on click
        document.addEventListener('click', (e) => {
            const addBtn = e.target.closest('.note-add-btn');
            const noteDisplay = e.target.closest('.note-display');

            if (addBtn || noteDisplay) {
                const container = addBtn || noteDisplay.closest('.note-display-container');
                const itemId = container.dataset.itemId;
                this._openNoteEditor(itemId);
            }
        });

        // Cancel
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.note-cancel-btn');
            if (!btn) return;
            this._closeNoteEditor(btn.dataset.itemId);
        });

        // Save
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.note-save-btn');
            if (!btn) return;
            this._saveNote(btn.dataset.itemId);
        });

        // Character counter
        document.addEventListener('input', (e) => {
            if (!e.target.classList.contains('note-input')) return;
            const counter = e.target.closest('.note-edit-container')?.querySelector('.note-char-count');
            if (counter) counter.textContent = e.target.value.length;
        });
    },

    _openNoteEditor(itemId) {
        const displayContainer = document.querySelector(`.note-display-container[data-item-id="${itemId}"]`);
        const editContainer = document.querySelector(`.note-edit-container[data-item-id="${itemId}"]`);
        if (displayContainer) displayContainer.classList.add('hidden');
        if (editContainer) {
            editContainer.classList.remove('hidden');
            const textarea = editContainer.querySelector('.note-input');
            if (textarea) textarea.focus();
        }
    },

    _closeNoteEditor(itemId) {
        const displayContainer = document.querySelector(`.note-display-container[data-item-id="${itemId}"]`);
        const editContainer = document.querySelector(`.note-edit-container[data-item-id="${itemId}"]`);
        if (editContainer) editContainer.classList.add('hidden');
        if (displayContainer) displayContainer.classList.remove('hidden');
    },

    async _saveNote(itemId) {
        const editContainer = document.querySelector(`.note-edit-container[data-item-id="${itemId}"]`);
        const textarea = editContainer?.querySelector('.note-input');
        if (!textarea) return;

        const note = textarea.value.trim();
        const e = PlatPursuit.HTMLUtils.escape;

        try {
            const data = await PlatPursuit.API.patch(
                `/api/v1/lists/${this.listId}/items/${itemId}/update/`,
                { note }
            );

            if (data.success) {
                PlatPursuit.ToastManager.success('Note saved.');

                // Update display container
                const displayContainer = document.querySelector(`.note-display-container[data-item-id="${itemId}"]`);
                if (displayContainer) {
                    if (note) {
                        displayContainer.innerHTML = `
                            <div class="note-display flex items-start gap-2 px-2 py-1 bg-base-200 rounded-lg cursor-pointer hover:bg-base-300 transition-colors"
                                 title="Click to edit note">
                                <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3 mt-0.5 text-primary flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                                </svg>
                                <span class="text-xs text-base-content/70 italic line-clamp-2">${e(note)}</span>
                            </div>`;
                    } else {
                        displayContainer.innerHTML = `
                            <button class="btn btn-ghost btn-xs text-base-content/40 note-add-btn gap-1" data-item-id="${itemId}">
                                <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/>
                                </svg>
                                Add note
                            </button>`;
                    }
                }

                this._closeNoteEditor(itemId);
            }
        } catch (err) {
            let msg = 'Failed to save note.';
            try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.error(msg);
        }
    },

    // --- Background Theme Grid ---
    _bindThemeGrid() {
        const grid = document.getElementById('list-theme-grid');
        if (!grid || !this.isPremium) return;

        grid.addEventListener('click', async (e) => {
            const btn = e.target.closest('.theme-preview-btn');
            if (!btn) return;

            const themeKey = btn.dataset.themeKey;
            const themeName = btn.dataset.themeName;
            const currentSelected = grid.dataset.selected;

            // Toggle: clicking already-selected theme deselects it
            const newTheme = (themeKey === currentSelected) ? '' : themeKey;
            const newName = newTheme ? themeName : 'None';

            try {
                await PlatPursuit.API.patch(`/api/v1/lists/${this.listId}/update/`, {
                    selected_theme: newTheme,
                });

                // Update grid visual state
                grid.querySelectorAll('.theme-preview-btn').forEach(b => {
                    b.classList.remove('border-primary', 'ring-2', 'ring-primary');
                    b.classList.add('border-base-300');
                });
                if (newTheme) {
                    btn.classList.remove('border-base-300');
                    btn.classList.add('border-primary', 'ring-2', 'ring-primary');
                }
                grid.dataset.selected = newTheme;

                // Update name display
                const nameEl = document.getElementById('selected-theme-name');
                if (nameEl) nameEl.textContent = newName;

                // Update preview
                const preview = document.getElementById('theme-preview-container');
                if (preview) {
                    preview.style.background = newTheme
                        ? btn.style.background
                        : 'linear-gradient(to bottom right, #2a2e34, #32363d, #2a2e34)';
                }

                PlatPursuit.ToastManager.success(`Theme ${newTheme ? 'updated' : 'removed'}.`);
            } catch (err) {
                PlatPursuit.ToastManager.error('Failed to update theme.');
            }
        });
    }
};


// ============================================================
// Quick Add Dropdown (for game detail + game cards)
// ============================================================
const GameListQuickAdd = {
    _dropdownOpen: false,
    _currentGameId: null,

    /**
     * Auto-discover all .quick-add-trigger elements on the page and bind them.
     */
    init() {
        document.querySelectorAll('.quick-add-trigger').forEach(el => {
            const gameId = parseInt(el.dataset.gameId || el.closest('[data-game-id]')?.dataset.gameId);
            if (gameId) this.bind(el, gameId);
        });
    },

    /**
     * Create a quick-add dropdown for a game.
     * Call this from game detail pages or cards.
     *
     * @param {HTMLElement} triggerEl - The button element that triggers the dropdown
     * @param {number} gameId - The game's ID
     */
    bind(triggerEl, gameId) {
        triggerEl.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();

            // Close existing dropdowns
            document.querySelectorAll('.game-list-quick-add-dropdown').forEach(d => d.remove());

            this._currentGameId = gameId;

            try {
                const data = await PlatPursuit.API.get(`/api/v1/lists/my/?game_id=${gameId}`);
                this._renderDropdown(triggerEl, data, gameId);
            } catch (err) {
                PlatPursuit.ToastManager.error('Failed to load lists.');
            }
        });
    },

    _renderDropdown(triggerEl, data, gameId) {
        const dropdown = document.createElement('div');
        dropdown.className = 'game-list-quick-add-dropdown absolute z-50 mt-1 bg-base-100 border-2 border-base-300 rounded-box shadow-xl min-w-[220px] p-2';
        const esc = PlatPursuit.HTMLUtils.escape;

        const checkIcon = '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 text-success" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>';
        const plusIcon = '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 text-base-content/30" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 5v14M5 12h14"/></svg>';
        const lockIcon = '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 text-warning" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/></svg>';

        // Build the "create new" section (shared between empty and non-empty states)
        const buildCreateSection = () => {
            if (data.can_create) {
                return `
                    <div class="border-t border-base-300 mt-2 pt-2">
                        <button class="btn btn-ghost btn-xs btn-block quick-add-new-list-toggle">+ New List</button>
                        <div class="quick-add-new-list-form hidden mt-2">
                            <input type="text" class="input input-bordered input-xs w-full quick-add-new-list-name"
                                   placeholder="List name" maxlength="200" />
                            <div class="flex gap-1 mt-1">
                                <button class="btn btn-ghost btn-xs flex-1 quick-add-new-list-cancel">Cancel</button>
                                <button class="btn btn-primary btn-xs flex-1 quick-add-new-list-submit">Create & Add</button>
                            </div>
                        </div>
                    </div>`;
            } else if (!data.is_premium) {
                return `<div class="border-t border-base-300 mt-2 pt-2"><a href="/beta-access/" class="text-xs text-primary p-2 block text-center">Upgrade for more lists</a></div>`;
            }
            return '';
        };

        if (data.lists.length === 0) {
            dropdown.innerHTML = `
                <p class="text-sm text-base-content/50 p-2">No lists yet.</p>
                ${buildCreateSection()}
            `;
        } else {
            const listItems = data.lists.map(list => {
                const hasGame = list.has_game;
                const isFull = !data.is_premium && data.max_items !== null
                    && list.game_count >= data.max_items && !hasGame;

                const icon = hasGame ? checkIcon : (isFull ? lockIcon : plusIcon);

                const countBadge = isFull
                    ? `<span class="badge badge-xs badge-warning font-bold">Full</span>`
                    : `<span class="badge badge-xs badge-ghost">${list.game_count}</span>`;

                if (isFull) {
                    return `
                        <div class="flex items-center gap-2 w-full p-2 rounded text-left text-sm opacity-50 cursor-not-allowed"
                             title="List is full (${data.max_items}/${data.max_items}). Upgrade to Premium for unlimited.">
                            ${icon}
                            <span class="truncate flex-1">${esc(list.name)}</span>
                            ${countBadge}
                        </div>`;
                }

                return `
                    <button class="flex items-center gap-2 w-full p-2 rounded hover:bg-base-200 text-left text-sm quick-add-list-btn"
                            data-list-id="${list.id}" data-has-game="${hasGame}" data-action="${hasGame ? 'remove' : 'add'}">
                        ${icon}
                        <span class="truncate flex-1">${esc(list.name)}</span>
                        ${countBadge}
                    </button>`;
            }).join('');

            dropdown.innerHTML = listItems + buildCreateSection();
        }

        // Position dropdown below trigger using fixed positioning
        const rect = triggerEl.getBoundingClientRect();
        dropdown.style.position = 'fixed';
        dropdown.style.top = (rect.bottom + 4) + 'px';
        dropdown.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 232)) + 'px';
        dropdown.style.maxHeight = '300px';
        dropdown.style.overflowY = 'auto';
        document.body.appendChild(dropdown);

        // Bind add/remove clicks
        dropdown.querySelectorAll('.quick-add-list-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const listId = parseInt(btn.dataset.listId);
                const action = btn.dataset.action;

                try {
                    const result = await PlatPursuit.API.post('/api/v1/lists/quick-add/', {
                        list_id: listId,
                        game_id: gameId,
                        action,
                    });

                    if (result.success) {
                        PlatPursuit.ToastManager.success(
                            result.action === 'added' ? 'Added to list!' : 'Removed from list.'
                        );
                        dropdown.remove();
                    }
                } catch (err) {
                    let msg = 'Failed to update list.';
                    try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
                    PlatPursuit.ToastManager.error(msg);
                }
            });
        });

        // Bind inline create form
        const toggleBtn = dropdown.querySelector('.quick-add-new-list-toggle');
        const formDiv = dropdown.querySelector('.quick-add-new-list-form');
        if (toggleBtn && formDiv) {
            const nameInput = formDiv.querySelector('.quick-add-new-list-name');

            toggleBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                toggleBtn.classList.add('hidden');
                formDiv.classList.remove('hidden');
                nameInput.focus();
            });

            formDiv.querySelector('.quick-add-new-list-cancel').addEventListener('click', (e) => {
                e.stopPropagation();
                formDiv.classList.add('hidden');
                toggleBtn.classList.remove('hidden');
                nameInput.value = '';
                nameInput.classList.remove('input-error');
            });

            const doCreate = async () => {
                const name = nameInput.value.trim();
                if (!name) {
                    nameInput.classList.add('input-error');
                    return;
                }
                nameInput.classList.remove('input-error');

                const submitBtn = formDiv.querySelector('.quick-add-new-list-submit');
                submitBtn.disabled = true;
                submitBtn.textContent = 'Creating...';

                try {
                    const createResult = await PlatPursuit.API.post('/api/v1/lists/', { name, description: '' });
                    if (createResult.success) {
                        const addResult = await PlatPursuit.API.post('/api/v1/lists/quick-add/', {
                            list_id: createResult.list.id,
                            game_id: gameId,
                            action: 'add',
                        });
                        if (addResult.success) {
                            PlatPursuit.ToastManager.success(`Created "${name}" and added game!`);
                            dropdown.remove();
                        }
                    }
                } catch (err) {
                    let msg = 'Failed to create list.';
                    try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
                    PlatPursuit.ToastManager.error(msg);
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Create & Add';
                }
            };

            formDiv.querySelector('.quick-add-new-list-submit').addEventListener('click', (e) => {
                e.stopPropagation();
                doCreate();
            });

            nameInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    e.stopPropagation();
                    doCreate();
                }
            });
        }

        // Close on outside click
        setTimeout(() => {
            const closeHandler = (e) => {
                if (!dropdown.contains(e.target) && e.target !== triggerEl) {
                    dropdown.remove();
                    document.removeEventListener('click', closeHandler);
                }
            };
            document.addEventListener('click', closeHandler);
        }, 0);
    }
};

// Make available globally
window.GameListDetail = GameListDetail;
window.GameListEditor = GameListEditor;
window.GameListQuickAdd = GameListQuickAdd;
