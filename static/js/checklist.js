/**
 * Checklist functionality for PlatPursuit
 * Handles voting, progress tracking, and edit operations
 */

(function() {
    'use strict';

    // API base URL
    const API_BASE = '/api/v1';

    // ==========================================
    // Form State Preservation
    // ==========================================
    // Saves unsaved form data before page reloads so users don't lose work

    const FORM_STATE_KEY = 'checklist_edit_form_state';

    function getChecklistId() {
        const container = document.getElementById('checklist-edit-container');
        return container ? container.dataset.checklistId : null;
    }

    function saveFormState() {
        const checklistId = getChecklistId();
        if (!checklistId) return;

        const state = {
            checklistId: checklistId,
            timestamp: Date.now(),
            title: document.getElementById('checklist-title')?.value || '',
            description: document.getElementById('checklist-description')?.value || '',
            sections: {}
        };

        // Save section data
        document.querySelectorAll('.checklist-section').forEach(section => {
            const sectionId = section.dataset.sectionId;
            if (!sectionId) return;

            const titleInput = section.querySelector('.section-title-input');
            const descInput = section.querySelector('.section-description-input');

            state.sections[sectionId] = {
                title: titleInput?.value || '',
                description: descInput?.value || ''
            };

            // Save unsaved item edits within this section
            const items = {};
            section.querySelectorAll('[data-item-id]').forEach(item => {
                const itemId = item.dataset.itemId;
                const textInput = item.querySelector('.item-text-input');
                if (itemId && textInput) {
                    items[itemId] = textInput.value || '';
                }
            });
            state.sections[sectionId].items = items;
        });

        sessionStorage.setItem(FORM_STATE_KEY, JSON.stringify(state));
    }

    function restoreFormState() {
        const checklistId = getChecklistId();
        if (!checklistId) return false;

        const savedState = sessionStorage.getItem(FORM_STATE_KEY);
        if (!savedState) return false;

        try {
            const state = JSON.parse(savedState);

            // Only restore if it's for the same checklist and not too old (5 minutes)
            if (state.checklistId !== checklistId) return false;
            if (Date.now() - state.timestamp > 5 * 60 * 1000) {
                clearFormState();
                return false;
            }

            let restoredFields = false;

            // Restore title and description
            const titleInput = document.getElementById('checklist-title');
            const descInput = document.getElementById('checklist-description');

            if (titleInput && state.title && titleInput.value !== state.title) {
                titleInput.value = state.title;
                restoredFields = true;
            }
            if (descInput && state.description && descInput.value !== state.description) {
                descInput.value = state.description;
                restoredFields = true;
            }

            // Restore section data
            if (state.sections) {
                Object.keys(state.sections).forEach(sectionId => {
                    const sectionData = state.sections[sectionId];
                    const section = document.querySelector(`.checklist-section[data-section-id="${sectionId}"]`);
                    if (!section) return;

                    const titleInput = section.querySelector('.section-title-input');
                    const descInput = section.querySelector('.section-description-input');

                    if (titleInput && sectionData.title && titleInput.value !== sectionData.title) {
                        titleInput.value = sectionData.title;
                        restoredFields = true;
                    }
                    if (descInput && sectionData.description && descInput.value !== sectionData.description) {
                        descInput.value = sectionData.description;
                        restoredFields = true;
                    }

                    // Restore item text
                    if (sectionData.items) {
                        Object.keys(sectionData.items).forEach(itemId => {
                            const item = section.querySelector(`[data-item-id="${itemId}"]`);
                            const textInput = item?.querySelector('.item-text-input');
                            if (textInput && sectionData.items[itemId] && textInput.value !== sectionData.items[itemId]) {
                                textInput.value = sectionData.items[itemId];
                                restoredFields = true;
                            }
                        });
                    }
                });
            }

            // Update character counters after restoration
            if (restoredFields) {
                updateAllCharacterCounters();
                PlatPursuit.ToastManager.show('Your unsaved changes have been restored', 'info');
            }

            // Clear the saved state after restoration
            clearFormState();

            return restoredFields;
        } catch (e) {
            console.error('Failed to restore form state:', e);
            clearFormState();
            return false;
        }
    }

    function clearFormState() {
        sessionStorage.removeItem(FORM_STATE_KEY);
    }

    function updateAllCharacterCounters() {
        // Update checklist title counter
        const titleInput = document.getElementById('checklist-title');
        if (titleInput) {
            const counter = document.querySelector('.char-count[data-target="checklist-title"]');
            if (counter) counter.textContent = titleInput.value.length;
        }

        // Update checklist description counter
        const descInput = document.getElementById('checklist-description');
        if (descInput) {
            const counter = document.querySelector('.char-count[data-target="checklist-description"]');
            if (counter) counter.textContent = descInput.value.length;
        }

        // Update section counters
        document.querySelectorAll('.checklist-section').forEach(section => {
            const titleInput = section.querySelector('.section-title-input');
            const descInput = section.querySelector('.section-description-input');

            if (titleInput) {
                const counter = section.querySelector('.section-title-count');
                if (counter) counter.textContent = titleInput.value.length;
            }
            if (descInput) {
                const counter = section.querySelector('.section-desc-count');
                if (counter) counter.textContent = descInput.value.length;
            }

            // Update item counters
            section.querySelectorAll('[data-item-id]').forEach(item => {
                const textInput = item.querySelector('.item-text-input');
                const counter = item.querySelector('.item-char-count');
                if (textInput && counter) {
                    counter.textContent = textInput.value.length;
                }
            });
        });
    }

    // Helper to reload page while preserving form state
    function reloadWithFormState() {
        isNavigatingAway = true;  // Bypass unsaved changes warning
        saveFormState();
        location.reload();
    }

    // ==========================================
    // Unsaved Changes Warning
    // ==========================================
    // Tracks form changes and warns user before leaving with unsaved work

    let originalFormState = {};
    let pendingNavigation = null;
    let isNavigatingAway = false;

    function captureOriginalState() {
        const checklistId = getChecklistId();
        if (!checklistId) return;

        originalFormState = {
            title: document.getElementById('checklist-title')?.value || '',
            description: document.getElementById('checklist-description')?.value || '',
            gameSelector: document.getElementById('checklist-game-selector')?.value || '',
            sections: {}
        };

        // Capture section data
        document.querySelectorAll('.checklist-section').forEach(section => {
            const sectionId = section.dataset.sectionId;
            if (!sectionId) return;

            const titleInput = section.querySelector('.section-title-input');
            const descInput = section.querySelector('.section-description-input');

            originalFormState.sections[sectionId] = {
                title: titleInput?.value || '',
                description: descInput?.value || '',
                items: {}
            };

            // Capture item data
            section.querySelectorAll('[data-item-id]').forEach(item => {
                const itemId = item.dataset.itemId;
                const textInput = item.querySelector('.item-text-input');
                const typeSelect = item.querySelector('.item-type-select');
                if (itemId) {
                    originalFormState.sections[sectionId].items[itemId] = {
                        text: textInput?.value || '',
                        type: typeSelect?.value || 'item'
                    };
                }
            });
        });
    }

    function hasUnsavedChanges() {
        const checklistId = getChecklistId();
        if (!checklistId || Object.keys(originalFormState).length === 0) return false;

        // Check title
        const currentTitle = document.getElementById('checklist-title')?.value || '';
        if (currentTitle !== originalFormState.title) return true;

        // Check description
        const currentDesc = document.getElementById('checklist-description')?.value || '';
        if (currentDesc !== originalFormState.description) return true;

        // Check game selector
        const currentGame = document.getElementById('checklist-game-selector')?.value || '';
        if (currentGame !== originalFormState.gameSelector) return true;

        // Check sections
        const sections = document.querySelectorAll('.checklist-section');
        for (const section of sections) {
            const sectionId = section.dataset.sectionId;
            if (!sectionId || !originalFormState.sections[sectionId]) continue;

            const origSection = originalFormState.sections[sectionId];
            const titleInput = section.querySelector('.section-title-input');
            const descInput = section.querySelector('.section-description-input');

            if ((titleInput?.value || '') !== origSection.title) return true;
            if ((descInput?.value || '') !== origSection.description) return true;

            // Check items
            section.querySelectorAll('[data-item-id]').forEach(item => {
                const itemId = item.dataset.itemId;
                if (!itemId || !origSection.items[itemId]) return;

                const origItem = origSection.items[itemId];
                const textInput = item.querySelector('.item-text-input');
                const typeSelect = item.querySelector('.item-type-select');

                if ((textInput?.value || '') !== origItem.text) return true;
                if ((typeSelect?.value || 'item') !== origItem.type) return true;
            });
        }

        return false;
    }

    function markFormAsClean() {
        // Recapture state after a successful save
        captureOriginalState();
    }

    function showUnsavedChangesModal(targetUrl) {
        const modal = document.getElementById('unsaved-changes-modal');
        if (!modal) {
            // Fallback if modal doesn't exist
            if (confirm('You have unsaved changes. Leave anyway?')) {
                isNavigatingAway = true;
                window.location.href = targetUrl;
            }
            return;
        }

        pendingNavigation = targetUrl;
        modal.showModal();
    }

    function initUnsavedChangesWarning(skipCaptureOriginalState = false) {
        const checklistId = getChecklistId();
        if (!checklistId) return;

        // Capture initial state after DOM is ready (unless we restored form state)
        if (!skipCaptureOriginalState) {
            captureOriginalState();
        }

        const modal = document.getElementById('unsaved-changes-modal');
        const stayBtn = document.getElementById('unsaved-stay-btn');
        const discardBtn = document.getElementById('unsaved-discard-btn');
        const saveBtn = document.getElementById('unsaved-save-btn');

        if (stayBtn) {
            stayBtn.addEventListener('click', () => {
                pendingNavigation = null;
                modal?.close();
            });
        }

        if (discardBtn) {
            discardBtn.addEventListener('click', () => {
                isNavigatingAway = true;
                modal?.close();
                if (pendingNavigation) {
                    window.location.href = pendingNavigation;
                }
            });
        }

        if (saveBtn) {
            saveBtn.addEventListener('click', async () => {
                const title = document.getElementById('checklist-title')?.value.trim();
                const description = document.getElementById('checklist-description')?.value.trim();

                if (!title) {
                    PlatPursuit.ToastManager.show('Title is required', 'error');
                    return;
                }

                try {
                    saveBtn.classList.add('loading');
                    await apiRequest(`${API_BASE}/checklists/${checklistId}/`, 'PATCH', {
                        title,
                        description,
                    });
                    PlatPursuit.ToastManager.show('Guide saved!', 'success');
                    clearFormState();
                    isNavigatingAway = true;
                    modal?.close();
                    if (pendingNavigation) {
                        window.location.href = pendingNavigation;
                    }
                } catch (error) {
                    PlatPursuit.ToastManager.show(error.message || 'Failed to save', 'error');
                } finally {
                    saveBtn.classList.remove('loading');
                }
            });
        }

        // Intercept link clicks
        document.addEventListener('click', (e) => {
            const link = e.target.closest('a');
            if (!link) return;

            // Skip links that open in new tab, have no href, or are anchors
            const href = link.getAttribute('href');
            if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
            if (link.target === '_blank') return;

            // Skip if already navigating or no unsaved changes
            if (isNavigatingAway || !hasUnsavedChanges()) return;

            e.preventDefault();
            showUnsavedChangesModal(href);
        });

        // Handle browser back/forward and closing tab
        window.addEventListener('beforeunload', (e) => {
            if (isNavigatingAway || !hasUnsavedChanges()) return;

            e.preventDefault();
            e.returnValue = '';
            return '';
        });

        // Handle popstate (browser back/forward buttons)
        window.addEventListener('popstate', () => {
            if (isNavigatingAway || !hasUnsavedChanges()) return;

            // Push the current state back to prevent navigation
            history.pushState(null, '', window.location.href);
            showUnsavedChangesModal(document.referrer || '/');
        });

        // Push initial state for popstate handling
        history.pushState(null, '', window.location.href);
    }

    // Get CSRF token from cookie
    // API helper
    async function apiRequest(url, method = 'GET', data = null) {
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': PlatPursuit.CSRFToken.get(),
            },
            credentials: 'same-origin',
        };
        if (data) {
            options.body = JSON.stringify(data);
        }
        const response = await fetch(url, options);
        const text = await response.text();
        let json;
        try {
            json = JSON.parse(text);
        } catch (e) {
            console.error('API returned non-JSON response:', text.substring(0, 200));
            throw new Error('Server error - please try again');
        }
        if (!response.ok) {
            throw new Error(json.error || json.detail || 'Request failed');
        }
        return json;
    }

    // Escape HTML for safe DOM insertion
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // ==========================================
    // Bulk Upload Functions
    // ==========================================

    /**
     * Parse bulk upload textarea into items array.
     * Rules:
     * - Split by newlines
     * - Trim whitespace from each line
     * - Ignore empty lines
     * - Lines starting with '#' → item_type: 'sub_header' (strip the #)
     * - Regular lines → item_type: 'item'
     * - Max 100 items
     */
    function parseBulkInput(textareaValue) {
        const lines = textareaValue.split('\n');
        const items = [];

        for (let line of lines) {
            line = line.trim();

            // Skip empty lines
            if (!line) continue;

            // Check if sub-header (starts with #)
            if (line.startsWith('#')) {
                const text = line.substring(1).trim();
                if (text) {
                    items.push({ text, item_type: 'sub_header' });
                }
            } else {
                items.push({ text: line, item_type: 'item' });
            }

            // Stop at 100 items
            if (items.length >= 100) break;
        }

        return items;
    }

    /**
     * Update the preview area and item count based on textarea input.
     */
    function updateBulkPreview(section) {
        const textarea = section.querySelector('.bulk-upload-textarea');
        const countEl = section.querySelector('.bulk-item-count');
        const previewEl = section.querySelector('.bulk-preview');
        const previewItemsEl = section.querySelector('.bulk-preview-items');
        const previewCountEl = section.querySelector('.bulk-preview-count');
        const uploadBtn = section.querySelector('.bulk-upload-btn');

        if (!textarea || !countEl || !uploadBtn) return;

        const items = parseBulkInput(textarea.value);

        // Update count
        countEl.textContent = items.length;

        // Enable/disable upload button
        uploadBtn.disabled = items.length === 0;

        // Show/hide preview
        if (items.length > 0 && previewEl && previewItemsEl && previewCountEl) {
            previewEl.classList.remove('hidden');
            previewCountEl.textContent = `${items.length} items`;

            // Render preview items
            let previewHtml = items.map((item, index) => {
                const isSubHeader = item.item_type === 'sub_header';
                return `
                    <div class="flex items-center gap-2 text-xs p-1 rounded ${isSubHeader ? 'bg-base-300/50 border-l-2 border-secondary' : 'bg-base-100'}">
                        <span class="text-base-content/40 font-mono">${index + 1}.</span>
                        ${isSubHeader
                            ? `<span class="font-semibold text-secondary">${escapeHtml(item.text)}</span>`
                            : `<span>${escapeHtml(item.text)}</span>`
                        }
                    </div>
                `;
            }).join('');

            // Show warning if at limit
            if (items.length === 100) {
                previewHtml += `<div class="text-warning text-xs mt-2">⚠️ Showing first 100 items. Additional items will be ignored.</div>`;
            }

            previewItemsEl.innerHTML = previewHtml;
        } else if (previewEl) {
            previewEl.classList.add('hidden');
        }
    }

    /**
     * Display detailed error modal for failed items.
     */
    function showBulkUploadErrors(errorResponse) {
        const { failed_items, summary } = errorResponse;

        const modalHtml = `
            <dialog id="bulk-error-modal" class="modal modal-open">
                <div class="modal-box max-w-2xl">
                    <h3 class="font-bold text-lg text-error">Upload Failed</h3>
                    <div class="py-4">
                        <div class="stats stats-horizontal shadow mb-4">
                            <div class="stat">
                                <div class="stat-title">Total</div>
                                <div class="stat-value text-sm">${summary.total_submitted}</div>
                            </div>
                            <div class="stat">
                                <div class="stat-title">Valid</div>
                                <div class="stat-value text-sm text-success">${summary.valid}</div>
                            </div>
                            <div class="stat">
                                <div class="stat-title">Failed</div>
                                <div class="stat-value text-sm text-error">${summary.failed}</div>
                            </div>
                        </div>

                        <p class="text-sm mb-2">The following items failed validation:</p>
                        <div class="max-h-64 overflow-y-auto space-y-2">
                            ${failed_items.map(item => `
                                <div class="alert alert-error text-xs p-2">
                                    <div>
                                        <span class="font-mono">Line ${item.index + 1}:</span>
                                        <span class="font-semibold">"${escapeHtml(item.text)}"</span>
                                        <br>
                                        <span class="text-error-content/70">${escapeHtml(item.error)}</span>
                                    </div>
                                </div>
                            `).join('')}
                        </div>

                        <p class="text-sm mt-4 text-base-content/70">
                            Please fix the errors and try again. Valid items were not uploaded (all-or-nothing).
                        </p>
                    </div>
                    <div class="modal-action">
                        <button class="btn btn-sm" onclick="this.closest('dialog').close(); this.closest('dialog').remove();">
                            Close
                        </button>
                    </div>
                </div>
            </dialog>
        `;

        document.body.insertAdjacentHTML('beforeend', modalHtml);
    }

    /**
     * Add created item to the DOM (reuse existing addItemToDOM function logic)
     */
    function addItemToDOM(section, item) {
        const itemsContainer = section.querySelector('.section-items');
        if (!itemsContainer) return;

        const itemTemplate = document.getElementById('item-template');
        if (!itemTemplate) return;

        const itemEl = itemTemplate.content.cloneNode(true);
        const itemDiv = itemEl.querySelector('.checklist-item-edit');

        // Set item data
        itemDiv.dataset.itemId = item.id;
        itemDiv.dataset.itemOrder = item.order;

        // Set text
        const textInput = itemDiv.querySelector('.item-text-input');
        if (textInput) {
            textInput.value = item.text;
        }

        // Set item type
        const typeSelect = itemDiv.querySelector('.item-type-select');
        if (typeSelect) {
            typeSelect.value = item.item_type;
            // Trigger visual update
            if (item.item_type === 'sub_header') {
                itemDiv.classList.add('sub-header-item');
            } else {
                itemDiv.classList.remove('sub-header-item');
            }
        }

        // Append to container
        itemsContainer.appendChild(itemEl);
    }

    /**
     * Submit bulk upload to API and handle response.
     */
    async function performBulkUpload(section) {
        const sectionId = section.dataset.sectionId;
        const textarea = section.querySelector('.bulk-upload-textarea');
        const uploadBtn = section.querySelector('.bulk-upload-btn');
        const progressEl = section.querySelector('.bulk-progress');
        const progressBar = progressEl ? progressEl.querySelector('.progress') : null;
        const progressText = progressEl ? progressEl.querySelector('.bulk-progress-text') : null;

        if (!sectionId) {
            PlatPursuit.ToastManager.show('Please save the section first', 'error');
            return;
        }

        const items = parseBulkInput(textarea.value);

        if (items.length === 0) {
            PlatPursuit.ToastManager.show('No items to upload', 'error');
            return;
        }

        try {
            // Show progress
            uploadBtn.disabled = true;
            uploadBtn.classList.add('loading');
            if (progressEl) {
                progressEl.classList.remove('hidden');
                if (progressBar) progressBar.value = 0;
                if (progressText) progressText.textContent = `Uploading ${items.length} items...`;
            }

            // Submit to API
            const result = await apiRequest(
                `${API_BASE}/checklists/sections/${sectionId}/items/bulk/`,
                'POST',
                { items }
            );

            // Success
            if (progressBar) progressBar.value = 100;
            if (progressText) progressText.textContent = `Successfully uploaded ${result.items_created} items!`;

            // Add items to DOM
            result.items.forEach(item => {
                addItemToDOM(section, item);
            });

            // Clear textarea and reset UI
            textarea.value = '';
            updateBulkPreview(section);

            // Hide progress after delay
            setTimeout(() => {
                if (progressEl) progressEl.classList.add('hidden');
            }, 2000);

            PlatPursuit.ToastManager.show(`${result.items_created} items added!`, 'success');

        } catch (error) {
            // Error handling
            if (progressEl) progressEl.classList.add('hidden');

            if (error.failed_items) {
                // Validation errors
                showBulkUploadErrors(error);
            } else {
                // Network or other error
                PlatPursuit.ToastManager.show(error.message || 'Failed to upload items', 'error');
            }
        } finally {
            uploadBtn.disabled = false;
            uploadBtn.classList.remove('loading');
        }
    }

    /**
     * Initialize bulk upload functionality for all sections.
     */
    function initBulkUpload() {
        document.querySelectorAll('.checklist-section').forEach(section => {
            const textarea = section.querySelector('.bulk-upload-textarea');
            const uploadBtn = section.querySelector('.bulk-upload-btn');
            const clearBtn = section.querySelector('.bulk-clear-btn');

            if (!textarea) return;

            // Update preview on input
            textarea.addEventListener('input', () => {
                updateBulkPreview(section);
            });

            // Upload button
            if (uploadBtn) {
                uploadBtn.addEventListener('click', () => {
                    performBulkUpload(section);
                });
            }

            // Clear button
            if (clearBtn) {
                clearBtn.addEventListener('click', () => {
                    textarea.value = '';
                    updateBulkPreview(section);
                });
            }
        });
    }

    // ==========================================
    // Checklist Detail Page Functions
    // ==========================================

    function initChecklistDetail() {
        const container = document.getElementById('checklist-detail-container');
        if (!container) return;

        const checklistId = container.dataset.checklistId;
        const canSaveProgress = container.dataset.canSaveProgress === 'true';
        const isPremium = container.dataset.isPremium === 'true';

        // Vote button handlers
        initVoteButtons(checklistId);

        // Progress tracking checkboxes
        initProgressCheckboxes(checklistId, canSaveProgress, isPremium);

        // Report button handler
        initReportButton(checklistId);

        // Table of contents
        initTableOfContents();
    }

    function initVoteButtons(checklistId) {
        document.querySelectorAll('.checklist-vote-btn').forEach(btn => {
            btn.addEventListener('click', async function() {
                const targetId = this.dataset.checklistId || checklistId;
                try {
                    const result = await apiRequest(`${API_BASE}/checklists/${targetId}/vote/`, 'POST');

                    // Update UI
                    const voteCount = document.querySelector('.checklist-vote-count');
                    if (voteCount) {
                        voteCount.textContent = result.upvote_count;
                    }

                    // Toggle button state
                    if (result.voted) {
                        this.classList.add('text-secondary');
                        this.querySelector('svg').setAttribute('fill', 'currentColor');
                    } else {
                        this.classList.remove('text-secondary');
                        this.querySelector('svg').setAttribute('fill', 'none');
                    }
                } catch (error) {
                    PlatPursuit.ToastManager.show(error.message || 'Failed to vote', 'error');
                }
            });
        });
    }

    function initProgressCheckboxes(checklistId, canSaveProgress, isPremium) {
        // Attach to regular items and trophy items, not sub-headers
        document.querySelectorAll('.checklist-item[data-item-type="item"] .checklist-item-checkbox, .checklist-trophy-item[data-item-type="trophy"] .checklist-item-checkbox').forEach(checkbox => {
            checkbox.addEventListener('change', async function() {
                const itemId = this.dataset.itemId;
                const isChecked = this.checked;
                const itemContainer = this.closest('.checklist-item, .checklist-trophy-item');
                const textSpan = itemContainer ? itemContainer.querySelector('.checklist-item-text') : null;

                // Skip API call for earned trophies (they're disabled but defensive check)
                if (itemContainer && itemContainer.dataset.earned === 'true') {
                    this.checked = true;
                    return;
                }

                // Update visual state immediately
                if (textSpan) {
                    if (isChecked) {
                        textSpan.classList.add('line-through', 'text-base-content/50');
                    } else {
                        textSpan.classList.remove('line-through', 'text-base-content/50');
                    }
                }

                // If user can't save progress, show premium upsell
                if (!canSaveProgress) {
                    // Still allow checking in session, just don't save
                    updateLocalProgress();
                    return;
                }

                // Save to API
                try {
                    const result = await apiRequest(
                        `${API_BASE}/checklists/${checklistId}/progress/toggle/${itemId}/`,
                        'POST'
                    );

                    // Update progress display (API returns fields at top level)
                    updateProgressDisplay({
                        percentage: result.progress_percentage,
                        items_completed: result.items_completed,
                        total_items: result.total_items
                    });
                } catch (error) {
                    // Revert checkbox on error
                    this.checked = !isChecked;
                    if (!isChecked) {
                        textSpan.classList.add('line-through', 'text-base-content/50');
                    } else {
                        textSpan.classList.remove('line-through', 'text-base-content/50');
                    }
                    PlatPursuit.ToastManager.show(error.message || 'Failed to save progress', 'error');
                }
            });
        });
    }

    function updateProgressDisplay(progress) {
        const progressBar = document.getElementById('progress-bar');
        const progressPercentage = document.getElementById('progress-percentage');
        const itemsCompleted = document.getElementById('items-completed-count');
        const itemsTotal = document.getElementById('items-total-count');

        // Get previous percentage to detect transition to 100%
        const previousPercentage = progressBar ? parseFloat(progressBar.value) : 0;

        // Count earned trophies that weren't manually checked (they're auto-checked but API doesn't track them)
        // We need to add earned trophies to the completed count, but only those not already in completed_items
        // Since the API's items_completed doesn't include earned trophies, we add the count of earned trophies
        const earnedTrophyCount = document.querySelectorAll('.checklist-trophy-item[data-earned="true"]').length;

        // Adjust the completed count to include earned trophies
        // total_items from API already includes trophy items, so we don't adjust that
        const adjustedCompleted = progress.items_completed + earnedTrophyCount;
        const adjustedPercentage = progress.total_items > 0 ? (adjustedCompleted / progress.total_items * 100) : 0;

        if (progressBar) {
            progressBar.value = adjustedPercentage;
        }
        if (progressPercentage) {
            progressPercentage.textContent = Math.round(adjustedPercentage) + '%';
        }
        if (itemsCompleted) {
            itemsCompleted.textContent = adjustedCompleted;
        }
        if (itemsTotal) {
            itemsTotal.textContent = progress.total_items;
        }

        // Update section counts
        updateSectionCounts();

        // Trigger celebration when reaching 100% (not when already at 100%)
        if (adjustedPercentage >= 100 && previousPercentage < 100) {
            celebrateCompletion();
        }
    }

    function celebrateCompletion() {
        // Check if confetti library is loaded
        if (typeof confetti !== 'function') return;

        // Fire confetti from both sides
        const duration = 3000;
        const end = Date.now() + duration;

        const frame = () => {
            // Left side burst
            confetti({
                particleCount: 3,
                angle: 60,
                spread: 55,
                origin: { x: 0, y: 0.6 },
                colors: ['#f472b6', '#a855f7', '#3b82f6', '#22c55e', '#eab308']
            });
            // Right side burst
            confetti({
                particleCount: 3,
                angle: 120,
                spread: 55,
                origin: { x: 1, y: 0.6 },
                colors: ['#f472b6', '#a855f7', '#3b82f6', '#22c55e', '#eab308']
            });

            if (Date.now() < end) {
                requestAnimationFrame(frame);
            }
        };

        frame();

        // Show toast message
        PlatPursuit.ToastManager.show('Checklist complete! Great job!', 'success');
    }

    function updateLocalProgress() {
        // Update progress display based on checked checkboxes (for non-premium users)
        // Count regular items and trophy items, not sub-headers
        const checkboxes = document.querySelectorAll('.checklist-item[data-item-type="item"] .checklist-item-checkbox, .checklist-trophy-item[data-item-type="trophy"] .checklist-item-checkbox');
        const checked = document.querySelectorAll('.checklist-item[data-item-type="item"] .checklist-item-checkbox:checked, .checklist-trophy-item[data-item-type="trophy"] .checklist-item-checkbox:checked');
        const total = checkboxes.length;
        const completed = checked.length;
        const percentage = total > 0 ? (completed / total * 100) : 0;

        const progressBar = document.getElementById('progress-bar');
        const progressPercentage = document.getElementById('progress-percentage');
        const itemsCompleted = document.getElementById('items-completed-count');

        // Get previous percentage to detect transition to 100%
        const previousPercentage = progressBar ? parseFloat(progressBar.value) : 0;

        if (progressBar) {
            progressBar.value = percentage;
        }
        if (progressPercentage) {
            progressPercentage.textContent = Math.round(percentage) + '%';
        }
        if (itemsCompleted) {
            itemsCompleted.textContent = completed;
        }

        // Update section counts
        updateSectionCounts();

        // Trigger celebration when reaching 100% (not when already at 100%)
        if (percentage >= 100 && previousPercentage < 100) {
            celebrateCompletion();
        }
    }

    function updateSectionCounts() {
        // Update the XX/YY counts for each section in both header and TOC
        document.querySelectorAll('.checklist-section').forEach(section => {
            const sectionId = section.dataset.sectionId;
            if (!sectionId) return;

            // Count completed items in this section (regular items and trophy items)
            const sectionItems = section.querySelectorAll('.checklist-item[data-item-type="item"] .checklist-item-checkbox, .checklist-trophy-item[data-item-type="trophy"] .checklist-item-checkbox');
            const sectionChecked = section.querySelectorAll('.checklist-item[data-item-type="item"] .checklist-item-checkbox:checked, .checklist-trophy-item[data-item-type="trophy"] .checklist-item-checkbox:checked');
            const completedCount = sectionChecked.length;
            const totalCount = sectionItems.length;

            // Update section header count
            const sectionHeaderCount = section.querySelector('.section-item-count .completed-count');
            if (sectionHeaderCount) {
                sectionHeaderCount.textContent = completedCount;
            }

            // Update TOC counts (both desktop and mobile)
            document.querySelectorAll(`.toc-item[data-section-id="${sectionId}"] .toc-section-count .completed-count`).forEach(tocCount => {
                tocCount.textContent = completedCount;
            });
        });
    }

    function initReportButton(checklistId) {
        const reportBtn = document.querySelector('.checklist-report-btn');
        const reportModal = document.getElementById('report-modal');
        const reportForm = document.getElementById('report-form');

        if (!reportBtn || !reportModal || !reportForm) return;

        reportBtn.addEventListener('click', () => {
            reportModal.showModal();
        });

        reportForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const formData = new FormData(this);
            const data = {
                reason: formData.get('reason'),
                details: formData.get('details'),
            };

            try {
                await apiRequest(`${API_BASE}/checklists/${checklistId}/report/`, 'POST', data);
                reportModal.close();
                reportForm.reset();
                PlatPursuit.ToastManager.show('Report submitted. Thank you!', 'success');
            } catch (error) {
                PlatPursuit.ToastManager.show(error.message || 'Failed to submit report', 'error');
            }
        });
    }

    function initTableOfContents() {
        // Select all TOC lists (both mobile and desktop)
        const tocLists = document.querySelectorAll('.toc-list');
        if (tocLists.length === 0) return; // TOC not present on this page

        const sections = document.querySelectorAll('.checklist-section');
        const tocItems = document.querySelectorAll('.toc-item');

        if (sections.length === 0 || tocItems.length === 0) return;

        // Track currently visible sections
        let visibleSections = new Set();

        // Create Intersection Observer
        const observerOptions = {
            rootMargin: '-80px 0px -60% 0px', // Trigger when section enters top 40% of viewport
            threshold: 0
        };

        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                const sectionId = entry.target.dataset.sectionId;

                if (entry.isIntersecting) {
                    visibleSections.add(sectionId);
                } else {
                    visibleSections.delete(sectionId);
                }
            });

            // Update active state - highlight the first visible section
            updateActiveTocItem();
        }, observerOptions);

        // Observe all sections
        sections.forEach(section => observer.observe(section));

        // Update active TOC item based on visible sections
        function updateActiveTocItem() {
            // Find the topmost visible section
            let activeSection = null;

            if (visibleSections.size > 0) {
                // Convert Set to Array and find the first section in DOM order
                sections.forEach(section => {
                    const sectionId = section.dataset.sectionId;
                    if (visibleSections.has(sectionId) && !activeSection) {
                        activeSection = sectionId;
                    }
                });
            }

            // Update TOC items
            tocItems.forEach(item => {
                const itemSectionId = item.dataset.sectionId;

                if (itemSectionId === activeSection) {
                    // Add active styling
                    item.classList.add('bg-secondary/10', 'border-l-4', 'border-secondary', 'font-semibold');
                    item.classList.remove('hover:bg-base-200');
                } else {
                    // Remove active styling
                    item.classList.remove('bg-secondary/10', 'border-l-4', 'border-secondary', 'font-semibold');
                    item.classList.add('hover:bg-base-200');
                }
            });
        }

        // Handle TOC item clicks - smooth scroll
        tocItems.forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();

                const sectionId = item.dataset.sectionId;
                const targetSection = document.getElementById(`section-${sectionId}`);

                if (targetSection) {
                    // Smooth scroll to section with offset for fixed header
                    const yOffset = -100; // Offset for navbar + breathing room
                    const y = targetSection.getBoundingClientRect().top + window.pageYOffset + yOffset;

                    window.scrollTo({
                        top: y,
                        behavior: 'smooth'
                    });
                }
            });
        });

        // Initialize on page load
        updateActiveTocItem();
    }

    // ==========================================
    // Checklist Edit Page Functions
    // ==========================================

    function initChecklistEdit() {
        const container = document.getElementById('checklist-edit-container');
        if (!container) return;

        const checklistId = container.dataset.checklistId;

        // Save checklist button
        initSaveChecklist(checklistId);

        // Publish/Unpublish buttons
        initPublishButtons(checklistId);

        // Delete checklist button
        initDeleteChecklist(checklistId);

        // Add section button
        initAddSection(checklistId);

        // Section operations
        initSectionOperations(checklistId);

        // Item operations
        initItemOperations(checklistId);

        // Character counters
        initCharacterCounters();

        // Type selectors
        initTypeSelectors();

        // Bulk upload
        initBulkUpload();

        // Image handling
        initImagePreviews();
        initImageUploads();
    }

    function initSaveChecklist(checklistId) {
        const saveBtn = document.getElementById('save-checklist-btn');
        if (!saveBtn) return;

        saveBtn.addEventListener('click', async function() {
            const title = document.getElementById('checklist-title').value.trim();
            const description = document.getElementById('checklist-description').value.trim();

            if (!title) {
                PlatPursuit.ToastManager.show('Title is required', 'error');
                return;
            }

            try {
                this.classList.add('loading');
                let errorCount = 0;
                let successCount = 0;

                // Save header
                try {
                    await apiRequest(`${API_BASE}/checklists/${checklistId}/`, 'PATCH', {
                        title,
                        description,
                    });
                    successCount++;
                } catch (error) {
                    errorCount++;
                    console.error('Failed to save header:', error);
                }

                // Save all sections
                const sections = document.querySelectorAll('.checklist-section');
                for (const section of sections) {
                    const sectionId = section.dataset.sectionId;
                    const subtitle = section.querySelector('.section-title-input')?.value.trim();
                    const sectionDescription = section.querySelector('.section-description-input')?.value.trim();

                    if (subtitle) {
                        try {
                            await apiRequest(`${API_BASE}/checklists/${checklistId}/sections/${sectionId}/`, 'PATCH', {
                                subtitle,
                                description: sectionDescription,
                            });
                            successCount++;
                        } catch (error) {
                            errorCount++;
                            console.error(`Failed to save section ${sectionId}:`, error);
                        }
                    }

                    // Save all items in this section
                    const items = section.querySelectorAll('.checklist-item-edit, .checklist-text-area-edit, .checklist-image-item');
                    for (const item of items) {
                        const itemId = item.dataset.itemId;
                        const itemType = item.dataset.itemType;

                        // Skip trophy items and image items without captions to edit
                        if (itemType === 'trophy') continue;

                        const textInput = item.querySelector('.item-text-input');
                        if (!textInput) continue;

                        const text = textInput.value.trim();
                        const typeSelect = item.querySelector('.item-type-select');
                        const finalItemType = typeSelect ? typeSelect.value : itemType;

                        // Only save if there's text (or if it's an image with optional caption)
                        if (text || itemType === 'image') {
                            try {
                                await apiRequest(`${API_BASE}/checklists/items/${itemId}/`, 'PATCH', {
                                    text: text || '',
                                    item_type: finalItemType,
                                });
                                successCount++;
                            } catch (error) {
                                errorCount++;
                                console.error(`Failed to save item ${itemId}:`, error);
                            }
                        }
                    }
                }

                // Show appropriate message
                if (errorCount === 0) {
                    PlatPursuit.ToastManager.show('All changes saved successfully!', 'success');
                } else if (successCount > 0) {
                    PlatPursuit.ToastManager.show(`Saved with ${errorCount} error(s). Check console for details.`, 'warning');
                } else {
                    PlatPursuit.ToastManager.show('Failed to save changes', 'error');
                }

                // Clear any saved form state since we just saved successfully
                clearFormState();
                // Mark form as clean so unsaved changes warning doesn't trigger
                markFormAsClean();
            } catch (error) {
                PlatPursuit.ToastManager.show(error.message || 'Failed to save', 'error');
            } finally {
                this.classList.remove('loading');
            }
        });
    }

    function initPublishButtons(checklistId) {
        const publishBtn = document.getElementById('publish-checklist-btn');
        const unpublishBtn = document.getElementById('unpublish-checklist-btn');

        if (publishBtn) {
            publishBtn.addEventListener('click', async function() {
                // Show confirmation dialog before publishing
                const confirmed = confirm(
                    `Ready to publish?\n\n` +
                    `Please double-check your guide before publishing:\n` +
                    `• All sections and items are complete\n` +
                    `• No typos or errors\n` +
                    `• Items are in the correct order\n\n` +
                    `Once published, you cannot add, edit, or delete sections and items ` +
                    `because users may start tracking their progress. ` +
                    `You can still edit the title and description.\n\n` +
                    `Publish this guide?`
                );

                if (!confirmed) {
                    return;
                }

                try {
                    this.classList.add('loading');
                    await apiRequest(`${API_BASE}/checklists/${checklistId}/publish/`, 'POST');
                    PlatPursuit.ToastManager.show('Guide published!', 'success');
                    reloadWithFormState();
                } catch (error) {
                    PlatPursuit.ToastManager.show(error.message || 'Failed to publish', 'error');
                } finally {
                    this.classList.remove('loading');
                }
            });
        }

        if (unpublishBtn) {
            unpublishBtn.addEventListener('click', async function() {
                try {
                    this.classList.add('loading');

                    // First check how many users are tracking progress
                    const statusResult = await apiRequest(`${API_BASE}/checklists/${checklistId}/publish/`, 'GET');
                    const trackerCount = statusResult.tracker_count || 0;

                    // If there are users tracking, show a confirmation dialog
                    if (trackerCount > 0) {
                        this.classList.remove('loading');
                        const userText = trackerCount === 1 ? '1 user is' : `${trackerCount} users are`;
                        const confirmed = confirm(
                            `Warning: ${userText} currently tracking progress on this checklist.\n\n` +
                            `Unpublishing will allow you to make structural changes (add/edit/delete items). ` +
                            `If you delete items, those items will be automatically removed from users' progress records.\n\n` +
                            `Are you sure you want to unpublish?`
                        );
                        if (!confirmed) {
                            return;
                        }
                        this.classList.add('loading');
                    }

                    // Proceed with unpublishing
                    await apiRequest(`${API_BASE}/checklists/${checklistId}/publish/`, 'DELETE');
                    PlatPursuit.ToastManager.show('Guide unpublished', 'info');
                    reloadWithFormState();
                } catch (error) {
                    PlatPursuit.ToastManager.show(error.message || 'Failed to unpublish', 'error');
                } finally {
                    this.classList.remove('loading');
                }
            });
        }
    }

    function initDeleteChecklist(checklistId) {
        const deleteBtn = document.getElementById('delete-checklist-btn');
        const confirmBtn = document.getElementById('confirm-delete-btn');
        const modal = document.getElementById('delete-confirm-modal');

        if (!deleteBtn || !confirmBtn || !modal) return;

        deleteBtn.addEventListener('click', () => {
            modal.showModal();
        });

        confirmBtn.addEventListener('click', async function() {
            try {
                this.classList.add('loading');
                await apiRequest(`${API_BASE}/checklists/${checklistId}/`, 'DELETE');
                PlatPursuit.ToastManager.show('Guide deleted', 'info');
                window.location.href = '/my-checklists/';
            } catch (error) {
                PlatPursuit.ToastManager.show(error.message || 'Failed to delete', 'error');
            } finally {
                this.classList.remove('loading');
            }
        });
    }

    function initAddSection(checklistId) {
        const addBtn = document.getElementById('add-section-btn');
        if (!addBtn) return;

        addBtn.addEventListener('click', async function() {
            try {
                this.classList.add('loading');
                await apiRequest(`${API_BASE}/checklists/${checklistId}/sections/`, 'POST', {
                    subtitle: 'New Section',
                });

                PlatPursuit.ToastManager.show('Section added!', 'success');

                // Reload page to show the new section with all features
                setTimeout(() => reloadWithFormState(), 500);
            } catch (error) {
                PlatPursuit.ToastManager.show(error.message || 'Failed to add section', 'error');
                this.classList.remove('loading');
            }
        });
    }


    function initSectionOperations(checklistId) {
        // Save section
        document.querySelectorAll('.section-save-btn').forEach(btn => {
            // Remove existing listeners
            btn.replaceWith(btn.cloneNode(true));
        });

        document.querySelectorAll('.section-save-btn').forEach(btn => {
            btn.addEventListener('click', async function() {
                const section = this.closest('.checklist-section');
                const sectionId = section.dataset.sectionId;
                const subtitle = section.querySelector('.section-title-input').value.trim();
                const description = section.querySelector('.section-description-input').value.trim();

                if (!subtitle) {
                    PlatPursuit.ToastManager.show('Section title is required', 'error');
                    return;
                }

                try {
                    await apiRequest(`${API_BASE}/checklists/${checklistId}/sections/${sectionId}/`, 'PATCH', {
                        subtitle,
                        description,
                    });
                    PlatPursuit.ToastManager.show('Section saved!', 'success');
                    // Clear saved form state since we just saved
                    clearFormState();
                    markFormAsClean();
                } catch (error) {
                    PlatPursuit.ToastManager.show(error.message || 'Failed to save section', 'error');
                }
            });
        });

        // Delete section
        document.querySelectorAll('.section-delete-btn').forEach(btn => {
            btn.replaceWith(btn.cloneNode(true));
        });

        document.querySelectorAll('.section-delete-btn').forEach(btn => {
            btn.addEventListener('click', async function() {
                if (!confirm('Delete this section and all its items?')) return;

                const section = this.closest('.checklist-section');
                const sectionId = section.dataset.sectionId;

                try {
                    await apiRequest(`${API_BASE}/checklists/${checklistId}/sections/${sectionId}/`, 'DELETE');
                    section.remove();
                    PlatPursuit.ToastManager.show('Section deleted', 'info');

                    // Show publish requirements if no sections left
                    const sections = document.querySelectorAll('.checklist-section');
                    if (sections.length === 0) {
                        const requirements = document.getElementById('publish-requirements');
                        if (requirements) {
                            requirements.classList.remove('hidden');
                        }
                    }
                } catch (error) {
                    PlatPursuit.ToastManager.show(error.message || 'Failed to delete section', 'error');
                }
            });
        });

        // Move section up
        document.querySelectorAll('.section-move-up-btn').forEach(btn => {
            btn.replaceWith(btn.cloneNode(true));
        });

        document.querySelectorAll('.section-move-up-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const section = this.closest('.checklist-section');
                const prev = section.previousElementSibling;
                if (prev && prev.classList.contains('checklist-section')) {
                    section.parentNode.insertBefore(section, prev);
                    reorderSections(checklistId);
                }
            });
        });

        // Move section down
        document.querySelectorAll('.section-move-down-btn').forEach(btn => {
            btn.replaceWith(btn.cloneNode(true));
        });

        document.querySelectorAll('.section-move-down-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const section = this.closest('.checklist-section');
                const next = section.nextElementSibling;
                if (next && next.classList.contains('checklist-section')) {
                    section.parentNode.insertBefore(next, section);
                    reorderSections(checklistId);
                }
            });
        });
    }

    async function reorderSections(checklistId) {
        const sections = document.querySelectorAll('.checklist-section');
        const ids = Array.from(sections).map(s => parseInt(s.dataset.sectionId));

        try {
            await apiRequest(`${API_BASE}/checklists/${checklistId}/sections/reorder/`, 'POST', { ids });
        } catch (error) {
            PlatPursuit.ToastManager.show('Failed to reorder sections', 'error');
        }
    }

    async function reorderItems(section) {
        const sectionId = section.dataset.sectionId;
        // Get all item types (regular, image, text area, and trophy) in document order
        const container = section.querySelector('.section-items-container');
        if (!container) return;

        const allItems = container.querySelectorAll('.checklist-item-edit, .checklist-image-item, .checklist-text-area-edit, .checklist-trophy-item-edit');
        const ids = Array.from(allItems).map(i => parseInt(i.dataset.itemId));

        try {
            await apiRequest(`${API_BASE}/checklists/sections/${sectionId}/items/reorder/`, 'POST', { ids });
        } catch (error) {
            PlatPursuit.ToastManager.show('Failed to reorder items', 'error');
        }
    }

    function initItemOperations(checklistId) {
        // Move item up
        document.querySelectorAll('.item-move-up-btn').forEach(btn => {
            btn.replaceWith(btn.cloneNode(true));
        });

        document.querySelectorAll('.item-move-up-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                // Support regular items, image items, text area items, and trophy items
                const item = this.closest('.checklist-item-edit') ||
                            this.closest('.checklist-image-item') ||
                            this.closest('.checklist-text-area-edit') ||
                            this.closest('.checklist-trophy-item-edit');
                if (!item) return;

                const prev = item.previousElementSibling;
                if (prev && (prev.classList.contains('checklist-item-edit') ||
                           prev.classList.contains('checklist-image-item') ||
                           prev.classList.contains('checklist-text-area-edit') ||
                           prev.classList.contains('checklist-trophy-item-edit'))) {
                    item.parentNode.insertBefore(item, prev);
                    const section = this.closest('.checklist-section');
                    reorderItems(section);
                }
            });
        });

        // Move item down
        document.querySelectorAll('.item-move-down-btn').forEach(btn => {
            btn.replaceWith(btn.cloneNode(true));
        });

        document.querySelectorAll('.item-move-down-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                // Support regular items, image items, text area items, and trophy items
                const item = this.closest('.checklist-item-edit') ||
                            this.closest('.checklist-image-item') ||
                            this.closest('.checklist-text-area-edit') ||
                            this.closest('.checklist-trophy-item-edit');
                if (!item) return;

                const next = item.nextElementSibling;
                if (next && (next.classList.contains('checklist-item-edit') ||
                           next.classList.contains('checklist-image-item') ||
                           next.classList.contains('checklist-text-area-edit') ||
                           next.classList.contains('checklist-trophy-item-edit'))) {
                    item.parentNode.insertBefore(next, item);
                    const section = this.closest('.checklist-section');
                    reorderItems(section);
                }
            });
        });

        // Add item
        document.querySelectorAll('.add-item-btn').forEach(btn => {
            btn.replaceWith(btn.cloneNode(true));
        });

        document.querySelectorAll('.add-item-btn').forEach(btn => {
            btn.addEventListener('click', async function() {
                const section = this.closest('.checklist-section');
                const sectionId = section.dataset.sectionId;
                const input = section.querySelector('.new-item-input');
                const typeSelect = section.querySelector('.new-item-type-select');
                const text = input.value.trim();
                const itemType = typeSelect ? typeSelect.value : 'item';

                if (!text) {
                    PlatPursuit.ToastManager.show('Item text is required', 'error');
                    return;
                }

                if (!sectionId) {
                    PlatPursuit.ToastManager.show('Please save the section first', 'error');
                    return;
                }

                const btn = this;
                try {
                    btn.classList.add('loading');
                    const result = await apiRequest(`${API_BASE}/checklists/sections/${sectionId}/items/`, 'POST', {
                        text,
                        item_type: itemType,
                    });

                    // Add item to DOM (API returns { success: true, item: {...} })
                    addItemToDOM(section, result.item);
                    input.value = '';

                    // Reset type selector to default
                    if (typeSelect) typeSelect.value = 'item';

                    // Reset the new item char counter
                    const wrapper = input.closest('.mt-3');
                    const counter = wrapper?.querySelector('.new-item-char-count');
                    if (counter) counter.textContent = '0';

                    PlatPursuit.ToastManager.show('Item added!', 'success');
                } catch (error) {
                    PlatPursuit.ToastManager.show(error.message || 'Failed to add item', 'error');
                } finally {
                    // Find the current add button in the section (may have been replaced by initItemOperations)
                    const currentBtn = section.querySelector('.add-item-btn');
                    if (currentBtn) currentBtn.classList.remove('loading');
                }
            });
        });

        // Also trigger add on Enter key
        document.querySelectorAll('.new-item-input').forEach(input => {
            input.replaceWith(input.cloneNode(true));
        });

        document.querySelectorAll('.new-item-input').forEach(input => {
            input.addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    const addBtn = this.closest('.checklist-section').querySelector('.add-item-btn');
                    addBtn.click();
                }
            });
        });

        // Save item
        document.querySelectorAll('.item-save-btn').forEach(btn => {
            btn.replaceWith(btn.cloneNode(true));
        });

        document.querySelectorAll('.item-save-btn').forEach(btn => {
            btn.addEventListener('click', async function() {
                // Support regular items and text area items
                const item = this.closest('.checklist-item-edit') ||
                            this.closest('.checklist-text-area-edit');

                if (!item) return;

                const itemId = item.dataset.itemId;
                const text = item.querySelector('.item-text-input').value.trim();
                const typeSelect = item.querySelector('.item-type-select');
                const itemType = typeSelect ? typeSelect.value : item.dataset.itemType;

                if (!text) {
                    PlatPursuit.ToastManager.show('Item text is required', 'error');
                    return;
                }

                try {
                    await apiRequest(`${API_BASE}/checklists/items/${itemId}/`, 'PATCH', {
                        text,
                        item_type: itemType,
                    });

                    // Update data attribute
                    item.dataset.itemType = itemType;

                    // Update visual styling based on type (only for regular items, not text_area)
                    if (item.classList.contains('checklist-item-edit')) {
                        const textInput = item.querySelector('.item-text-input');
                        if (itemType === 'sub_header') {
                            item.classList.add('bg-base-300/50', 'border-l-4', 'border-secondary');
                            item.classList.remove('bg-base-200');
                            textInput.classList.add('font-semibold', 'text-secondary');
                        } else {
                            item.classList.remove('bg-base-300/50', 'border-l-4', 'border-secondary');
                            item.classList.add('bg-base-200');
                            textInput.classList.remove('font-semibold', 'text-secondary');
                        }
                    }

                    PlatPursuit.ToastManager.show('Item saved!', 'success');
                    // Clear saved form state since we just saved
                    clearFormState();
                    markFormAsClean();
                } catch (error) {
                    PlatPursuit.ToastManager.show(error.message || 'Failed to save item', 'error');
                }
            });
        });

        // Delete item
        document.querySelectorAll('.item-delete-btn').forEach(btn => {
            btn.replaceWith(btn.cloneNode(true));
        });

        document.querySelectorAll('.item-delete-btn').forEach(btn => {
            btn.addEventListener('click', async function() {
                // Support regular items, image items, text area items, and trophy items
                const item = this.closest('.checklist-item-edit') ||
                            this.closest('.checklist-image-item') ||
                            this.closest('.checklist-text-area-edit') ||
                            this.closest('.checklist-trophy-item-edit');
                if (!item) return;

                const itemId = item.dataset.itemId;
                const section = this.closest('.checklist-section');

                try {
                    await apiRequest(`${API_BASE}/checklists/items/${itemId}/`, 'DELETE');
                    item.remove();

                    // Update item count (count all types of items)
                    const countBadge = section.querySelector('.section-item-count');
                    const regularItems = section.querySelectorAll('.checklist-item-edit');
                    const imageItems = section.querySelectorAll('.checklist-image-item');
                    const textAreaItems = section.querySelectorAll('.checklist-text-area-edit');
                    const trophyItems = section.querySelectorAll('.checklist-trophy-item-edit');
                    const totalItems = regularItems.length + imageItems.length + textAreaItems.length + trophyItems.length;
                    countBadge.textContent = totalItems + ' items';

                    // Show empty message if no items
                    if (totalItems === 0) {
                        const container = section.querySelector('.section-items-container');
                        container.innerHTML = '<p class="text-base-content/50 italic text-center py-2 empty-items-message">No items yet. Add your first item below.</p>';
                    }

                    PlatPursuit.ToastManager.show('Item deleted', 'info');
                } catch (error) {
                    PlatPursuit.ToastManager.show(error.message || 'Failed to delete item', 'error');
                }
            });
        });
    }

    function addItemToDOM(section, item) {
        const template = document.getElementById('item-template');
        const container = section.querySelector('.section-items-container');
        if (!template || !container) return;

        // Remove empty message if present
        const emptyMsg = container.querySelector('.empty-items-message');
        if (emptyMsg) emptyMsg.remove();

        const clone = template.content.cloneNode(true);
        const itemEl = clone.querySelector('.checklist-item-edit');

        itemEl.dataset.itemId = item.id;
        itemEl.dataset.itemOrder = item.order;
        itemEl.dataset.itemType = item.item_type || 'item';
        itemEl.querySelector('.item-text-input').value = item.text;

        // Set type selector
        const typeSelect = itemEl.querySelector('.item-type-select');
        if (typeSelect) {
            typeSelect.value = item.item_type || 'item';
        }

        // Apply styling based on type
        const textInput = itemEl.querySelector('.item-text-input');
        if (item.item_type === 'sub_header') {
            itemEl.classList.add('bg-base-300/50', 'border-l-4', 'border-secondary');
            itemEl.classList.remove('bg-base-200');
            textInput.classList.add('font-semibold', 'text-secondary');
            textInput.placeholder = 'Sub-header text...';
        } else {
            textInput.placeholder = 'Item text...';
        }

        container.appendChild(clone);

        // Update item count
        const countBadge = section.querySelector('.section-item-count');
        const items = container.querySelectorAll('.checklist-item-edit');
        countBadge.textContent = items.length + ' items';

        // Re-init event listeners
        initItemOperations(section.closest('#checklist-edit-container').dataset.checklistId);

        // Update char count for new item
        const charCount = itemEl.querySelector('.item-char-count');
        if (charCount) {
            charCount.textContent = item.text.length;
        }

        // Re-init character counters
        initCharacterCounters();
    }

    function initCharacterCounters() {
        // Use event delegation on the container to handle all character counting
        const container = document.getElementById('checklist-edit-container');
        if (!container) return;

        // Only attach listener once
        if (container.dataset.charCountInit) return;
        container.dataset.charCountInit = 'true';

        container.addEventListener('input', function(e) {
            const target = e.target;

            // Checklist title
            if (target.id === 'checklist-title') {
                const counter = document.querySelector('.char-count[data-target="checklist-title"]');
                if (counter) counter.textContent = target.value.length;
            }

            // Checklist description
            if (target.id === 'checklist-description') {
                const counter = document.querySelector('.char-count[data-target="checklist-description"]');
                if (counter) counter.textContent = target.value.length;
            }

            // Section title
            if (target.classList.contains('section-title-input')) {
                const section = target.closest('.checklist-section');
                const counter = section?.querySelector('.section-title-count');
                if (counter) counter.textContent = target.value.length;
            }

            // Section description
            if (target.classList.contains('section-description-input')) {
                const section = target.closest('.checklist-section');
                const counter = section?.querySelector('.section-desc-count');
                if (counter) counter.textContent = target.value.length;
            }

            // Item text
            if (target.classList.contains('item-text-input')) {
                const item = target.closest('.checklist-item-edit');
                const counter = item?.querySelector('.item-char-count');
                if (counter) counter.textContent = target.value.length;
            }

            // New item input
            if (target.classList.contains('new-item-input')) {
                const wrapper = target.closest('.mt-3');
                const counter = wrapper?.querySelector('.new-item-char-count');
                if (counter) counter.textContent = target.value.length;
            }
        });
    }

    function initTypeSelectors() {
        // Use event delegation for type selector changes
        const container = document.getElementById('checklist-edit-container');
        if (!container) return;

        // Only attach listener once
        if (container.dataset.typeSelectInit) return;
        container.dataset.typeSelectInit = 'true';

        container.addEventListener('change', function(e) {
            const target = e.target;

            // Item type selector
            if (target.classList.contains('item-type-select')) {
                const item = target.closest('.checklist-item-edit');
                const textInput = item.querySelector('.item-text-input');

                if (target.value === 'sub_header') {
                    textInput.placeholder = 'Sub-header text...';
                    textInput.classList.add('font-semibold', 'text-secondary');
                } else {
                    textInput.placeholder = 'Item text...';
                    textInput.classList.remove('font-semibold', 'text-secondary');
                }
            }

            // New item type selector
            if (target.classList.contains('new-item-type-select')) {
                const section = target.closest('.checklist-section');
                const input = section.querySelector('.new-item-input');

                if (target.value === 'sub_header') {
                    input.placeholder = 'Add new sub-header...';
                } else {
                    input.placeholder = 'Add new item...';
                }
            }
        });
    }

    // ==========================================
    // Checklist Section (Game Detail Page)
    // ==========================================

    function initChecklistSection() {
        const section = document.getElementById('checklist-section');
        if (!section) return;

        const conceptId = section.dataset.conceptId;
        const container = document.getElementById('checklist-grid');
        const loadingEl = document.getElementById('checklist-loading');
        const emptyEl = document.getElementById('checklist-empty');
        const errorEl = document.getElementById('checklist-error');
        const sortContainer = document.getElementById('checklist-sort-container');
        const countBadge = document.getElementById('checklist-count');

        // Toggle section
        const toggleBtn = document.getElementById('checklist-toggle');
        const content = document.getElementById('checklist-content');
        let isExpanded = false;
        let hasLoaded = false;

        if (toggleBtn && content) {
            toggleBtn.addEventListener('click', function() {
                isExpanded = !isExpanded;

                if (isExpanded) {
                    content.classList.remove('hidden');
                    content.classList.add('flex');
                    if (sortContainer) sortContainer.classList.remove('hidden');
                } else {
                    content.classList.add('hidden');
                    content.classList.remove('flex');
                    if (sortContainer) sortContainer.classList.add('hidden');
                }

                const icon = document.getElementById('checklist-toggle-icon');
                if (icon) {
                    icon.style.transform = isExpanded ? 'rotate(0deg)' : 'rotate(-90deg)';
                }

                // Load checklists on first expand
                if (isExpanded && !hasLoaded) {
                    hasLoaded = true;
                    loadChecklists(conceptId, container, loadingEl, emptyEl, errorEl, 'top', countBadge);
                }
            });
        }

        // Sort dropdown
        const sortSelect = document.getElementById('checklist-sort');
        if (sortSelect) {
            sortSelect.addEventListener('change', function() {
                loadChecklists(conceptId, container, loadingEl, emptyEl, errorEl, this.value, countBadge);
            });
        }
    }

    async function loadChecklists(conceptId, container, loadingEl, emptyEl, errorEl, sort = 'top', countBadge = null) {
        if (!container) return;

        // Show loading
        if (loadingEl) loadingEl.classList.remove('hidden');
        if (emptyEl) emptyEl.classList.add('hidden');
        if (errorEl) errorEl.classList.add('hidden');
        container.innerHTML = '';

        try {
            // Request HTML-rendered cards from server
            const result = await apiRequest(`${API_BASE}/checklists/concept/${conceptId}/?sort=${sort}&output=html`);
            const cardsHtml = result.cards_html || [];

            if (loadingEl) loadingEl.classList.add('hidden');

            // Update count badge
            if (countBadge) {
                countBadge.textContent = result.total_count || cardsHtml.length;
            }

            if (cardsHtml.length === 0) {
                if (emptyEl) emptyEl.classList.remove('hidden');
                return;
            }

            // Insert server-rendered cards
            cardsHtml.forEach(cardHtml => {
                container.insertAdjacentHTML('beforeend', cardHtml);
            });

            // Init vote buttons for new cards
            initVoteButtons();

        } catch (error) {
            if (loadingEl) loadingEl.classList.add('hidden');
            if (errorEl) errorEl.classList.remove('hidden');
            console.error('Failed to load guides:', error);
        }
    }

    // ==========================================
    // Image Handling Functions
    // ==========================================

    function initImagePreviews() {
        // Checklist thumbnail preview
        const checklistInput = document.getElementById('checklist-thumbnail-input');
        if (checklistInput) {
            checklistInput.addEventListener('change', function(e) {
                const file = e.target.files[0];
                if (file && validateImageFile(file, 5)) {
                    previewImage(file, 'checklist-thumbnail-preview');
                }
            });
        }

        // Section thumbnail previews (delegated event)
        document.addEventListener('change', function(e) {
            if (e.target.classList.contains('section-thumbnail-input')) {
                const file = e.target.files[0];
                if (file && validateImageFile(file, 2)) {
                    const section = e.target.closest('.section-card');
                    const preview = section ? section.querySelector('.section-thumbnail-preview') : null;
                    if (preview) {
                        previewImage(file, preview);
                    }
                }
            }
        });
    }

    function validateImageFile(file, maxSizeMB) {
        const validTypes = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];

        if (!validTypes.includes(file.type)) {
            PlatPursuit.ToastManager.show('Invalid format. Use JPG, PNG, WebP, or GIF.', 'error');
            return false;
        }

        const maxSize = maxSizeMB * 1024 * 1024;
        if (file.size > maxSize) {
            PlatPursuit.ToastManager.show(`Image must be under ${maxSizeMB}MB.`, 'error');
            return false;
        }

        return true;
    }

    function previewImage(file, previewElement) {
        const reader = new FileReader();
        reader.onload = function(e) {
            if (typeof previewElement === 'string') {
                previewElement = document.getElementById(previewElement);
            }

            if (previewElement) {
                const img = previewElement.querySelector('img');
                if (img) {
                    img.src = e.target.result;
                    previewElement.classList.remove('hidden');
                }
            }
        };
        reader.readAsDataURL(file);
    }

    function initImageUploads() {
        // Checklist thumbnail upload
        const checklistInput = document.getElementById('checklist-thumbnail-input');
        if (checklistInput) {
            checklistInput.addEventListener('change', async function(e) {
                const file = e.target.files[0];
                if (!file) return;

                const checklistId = this.dataset.checklistId;
                await uploadChecklistThumbnail(checklistId, file);
            });
        }

        // Checklist thumbnail removal
        const removeBtn = document.getElementById('remove-checklist-thumbnail-btn');
        if (removeBtn) {
            removeBtn.addEventListener('click', async function() {
                const checklistId = this.dataset.checklistId;
                await removeChecklistThumbnail(checklistId);
            });
        }

        // Section thumbnail uploads (delegated)
        document.addEventListener('change', async function(e) {
            if (e.target.classList.contains('section-thumbnail-input')) {
                const file = e.target.files[0];
                if (!file) return;

                const sectionId = e.target.dataset.sectionId;
                await uploadSectionThumbnail(sectionId, file);
            }
        });

        // Section thumbnail removal (delegated)
        document.addEventListener('click', async function(e) {
            if (e.target.closest('.section-remove-thumbnail-btn')) {
                const btn = e.target.closest('.section-remove-thumbnail-btn');
                const sectionId = btn.dataset.sectionId;
                await removeSectionThumbnail(sectionId);
            }
        });

        // Inline image uploads
        document.addEventListener('click', async function(e) {
            if (e.target.closest('.add-inline-image-btn')) {
                const btn = e.target.closest('.add-inline-image-btn');
                const container = btn.closest('.mt-3');
                const input = container.querySelector('.inline-image-input');
                const captionInput = container.querySelector('.inline-image-caption');

                const file = input.files[0];
                if (!file) {
                    PlatPursuit.ToastManager.show('Please select an image.', 'error');
                    return;
                }

                const sectionId = btn.dataset.sectionId;
                const caption = captionInput.value.trim();

                await uploadInlineImage(sectionId, file, caption);
            }
        });
    }

    async function uploadChecklistThumbnail(checklistId, file) {
        const formData = new FormData();
        formData.append('thumbnail', file);

        try {
            const response = await fetch(`/api/v1/checklists/${checklistId}/image/`, {
                method: 'POST',
                headers: {'X-CSRFToken': PlatPursuit.CSRFToken.get()},
                body: formData
            });

            const data = await response.json();

            if (response.ok) {
                PlatPursuit.ToastManager.show('Thumbnail uploaded!', 'success');
                reloadWithFormState();
            } else {
                PlatPursuit.ToastManager.show(data.error || 'Upload failed.', 'error');
            }
        } catch (error) {
            console.error('Upload error:', error);
            PlatPursuit.ToastManager.show('Network error.', 'error');
        }
    }

    async function removeChecklistThumbnail(checklistId) {
        if (!confirm('Remove guide thumbnail?')) return;

        try {
            const response = await fetch(`/api/v1/checklists/${checklistId}/image/`, {
                method: 'DELETE',
                headers: {'X-CSRFToken': PlatPursuit.CSRFToken.get()}
            });

            if (response.ok) {
                PlatPursuit.ToastManager.show('Thumbnail removed.', 'success');
                reloadWithFormState();
            } else {
                const data = await response.json();
                PlatPursuit.ToastManager.show(data.error || 'Failed to remove.', 'error');
            }
        } catch (error) {
            console.error('Remove error:', error);
            PlatPursuit.ToastManager.show('Network error.', 'error');
        }
    }

    async function uploadSectionThumbnail(sectionId, file) {
        const formData = new FormData();
        formData.append('thumbnail', file);

        try {
            const response = await fetch(`/api/v1/checklists/sections/${sectionId}/image/`, {
                method: 'POST',
                headers: {'X-CSRFToken': PlatPursuit.CSRFToken.get()},
                body: formData
            });

            const data = await response.json();

            if (response.ok) {
                PlatPursuit.ToastManager.show('Section thumbnail uploaded!', 'success');
                reloadWithFormState();
            } else {
                PlatPursuit.ToastManager.show(data.error || 'Upload failed.', 'error');
            }
        } catch (error) {
            console.error('Upload error:', error);
            PlatPursuit.ToastManager.show('Network error.', 'error');
        }
    }

    async function removeSectionThumbnail(sectionId) {
        if (!confirm('Remove section thumbnail?')) return;

        try {
            const response = await fetch(`/api/v1/checklists/sections/${sectionId}/image/`, {
                method: 'DELETE',
                headers: {'X-CSRFToken': PlatPursuit.CSRFToken.get()}
            });

            if (response.ok) {
                PlatPursuit.ToastManager.show('Section thumbnail removed.', 'success');
                reloadWithFormState();
            } else {
                const data = await response.json();
                PlatPursuit.ToastManager.show(data.error || 'Failed to remove.', 'error');
            }
        } catch (error) {
            console.error('Remove error:', error);
            PlatPursuit.ToastManager.show('Network error.', 'error');
        }
    }

    async function uploadInlineImage(sectionId, file, caption) {
        const formData = new FormData();
        formData.append('image', file);
        if (caption) formData.append('text', caption);

        try {
            const response = await fetch(`/api/v1/checklists/sections/${sectionId}/items/image/`, {
                method: 'POST',
                headers: {'X-CSRFToken': PlatPursuit.CSRFToken.get()},
                body: formData
            });

            const data = await response.json();

            if (response.ok) {
                PlatPursuit.ToastManager.show('Inline image added!', 'success');
                reloadWithFormState();
            } else {
                PlatPursuit.ToastManager.show(data.error || 'Upload failed.', 'error');
            }
        } catch (error) {
            console.error('Upload error:', error);
            PlatPursuit.ToastManager.show('Network error.', 'error');
        }
    }

    // ==========================================
    // Text Area Handlers
    // ==========================================

    function initTextAreaCharCounters() {
        // Character counter for new text area inputs
        document.addEventListener('input', function(e) {
            if (e.target.classList.contains('text-area-content-input')) {
                const sectionId = e.target.dataset.sectionId;
                const counter = document.querySelector(`.text-area-char-count[data-section-id="${sectionId}"]`);
                if (counter) {
                    const length = e.target.value.length;
                    counter.textContent = `${length}/2000`;

                    // Warning color when approaching limit
                    if (length > 1900) {
                        counter.classList.add('text-warning');
                    } else {
                        counter.classList.remove('text-warning');
                    }
                }
            }

            // Character counter for editing existing text areas
            if (e.target.classList.contains('item-text-input') &&
                e.target.closest('.checklist-text-area-edit')) {
                const itemId = e.target.dataset.itemId;
                const counter = document.querySelector(`.text-area-edit-count[data-item-id="${itemId}"]`);
                if (counter) {
                    const length = e.target.value.length;
                    counter.textContent = `${length}/2000`;

                    if (length > 1900) {
                        counter.classList.add('text-warning');
                    } else {
                        counter.classList.remove('text-warning');
                    }
                }
            }
        });
    }

    function initTextAreaHandlers() {
        // Add text area button
        document.addEventListener('click', async function(e) {
            if (e.target.closest('.add-text-area-btn')) {
                const btn = e.target.closest('.add-text-area-btn');
                const container = btn.closest('.mt-3');
                const textarea = container.querySelector('.text-area-content-input');
                const sectionId = btn.dataset.sectionId;

                const text = textarea.value.trim();
                if (!text) {
                    PlatPursuit.ToastManager.show('Please enter some content.', 'error');
                    return;
                }

                if (text.length > 2000) {
                    PlatPursuit.ToastManager.show('Content too long (max 2000 characters).', 'error');
                    return;
                }

                // Disable button during request
                btn.disabled = true;
                const originalHTML = btn.innerHTML;
                btn.innerHTML = '<span class="loading loading-spinner loading-xs"></span> Adding...';

                try {
                    const response = await fetch(`${API_BASE}/checklists/sections/${sectionId}/items/`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': PlatPursuit.CSRFToken.get()
                        },
                        body: JSON.stringify({
                            text: text,
                            item_type: 'text_area'
                        })
                    });

                    const data = await response.json();

                    if (response.ok) {
                        PlatPursuit.ToastManager.show('Text area added!', 'success');
                        textarea.value = '';  // Clear input
                        // Update character counter
                        const counter = document.querySelector(`.text-area-char-count[data-section-id="${sectionId}"]`);
                        if (counter) counter.textContent = '0/2000';

                        // Reload page to show new item
                        setTimeout(() => reloadWithFormState(), 500);
                    } else {
                        PlatPursuit.ToastManager.show(data.error || 'Failed to add text area.', 'error');
                        btn.disabled = false;
                        btn.innerHTML = originalHTML;
                    }
                } catch (error) {
                    console.error('Add text area error:', error);
                    PlatPursuit.ToastManager.show('Network error.', 'error');
                    btn.disabled = false;
                    btn.innerHTML = originalHTML;
                }
            }
        });
    }

    function initMarkdownPreview() {
        // Preview button for new text areas
        document.addEventListener('click', async function(e) {
            if (e.target.closest('.preview-text-area-btn')) {
                const btn = e.target.closest('.preview-text-area-btn');
                const container = btn.closest('.mt-3');
                const textarea = container.querySelector('.text-area-content-input');
                const text = textarea.value.trim();

                if (!text) {
                    PlatPursuit.ToastManager.show('Enter some content first.', 'warning');
                    return;
                }

                await showMarkdownPreviewModal(text);
            }

            // Preview button for editing text areas
            if (e.target.closest('.preview-item-markdown-btn')) {
                const btn = e.target.closest('.preview-item-markdown-btn');
                const itemId = btn.dataset.itemId;
                const editContainer = document.querySelector(`[data-item-id="${itemId}"].checklist-text-area-edit`);
                const textarea = editContainer.querySelector('.item-text-input');
                const previewArea = document.getElementById(`preview-${itemId}`);

                if (previewArea.classList.contains('hidden')) {
                    // Show preview
                    const text = textarea.value.trim();
                    if (!text) {
                        PlatPursuit.ToastManager.show('No content to preview.', 'warning');
                        return;
                    }

                    // Fetch rendered HTML from server
                    try {
                        const response = await fetch(`${API_BASE}/markdown/preview/`, {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-CSRFToken': PlatPursuit.CSRFToken.get()
                            },
                            body: JSON.stringify({ text })
                        });

                        const data = await response.json();
                        if (response.ok) {
                            previewArea.querySelector('.prose').innerHTML = data.html;
                            previewArea.classList.remove('hidden');
                            btn.textContent = 'Hide Preview';
                        } else {
                            PlatPursuit.ToastManager.show(data.error || 'Preview failed.', 'error');
                        }
                    } catch (error) {
                        console.error('Preview error:', error);
                        PlatPursuit.ToastManager.show('Preview failed.', 'error');
                    }
                } else {
                    // Hide preview
                    previewArea.classList.add('hidden');
                    btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg> Preview';
                }
            }
        });
    }

    async function showMarkdownPreviewModal(text) {
        // Create modal for preview
        const modalHTML = `
            <dialog id="markdown-preview-modal" class="modal modal-open">
                <div class="modal-box max-w-3xl">
                    <h3 class="font-bold text-lg mb-4">Markdown Preview</h3>
                    <div class="prose prose-sm max-w-none preview-content p-4 bg-base-200 rounded-lg">
                        <!-- Rendered content will go here -->
                    </div>
                    <div class="modal-action">
                        <button class="btn btn-sm" onclick="this.closest('dialog').remove()">Close</button>
                    </div>
                </div>
            </dialog>
        `;

        // Insert modal into DOM
        document.body.insertAdjacentHTML('beforeend', modalHTML);
        const modal = document.getElementById('markdown-preview-modal');

        // Fetch rendered HTML from server
        try {
            const response = await fetch(`${API_BASE}/markdown/preview/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': PlatPursuit.CSRFToken.get()
                },
                body: JSON.stringify({ text })
            });

            const data = await response.json();
            if (response.ok) {
                modal.querySelector('.preview-content').innerHTML = data.html;
            } else {
                modal.querySelector('.preview-content').innerHTML = '<p class="text-error">Preview failed.</p>';
            }
        } catch (error) {
            console.error('Preview error:', error);
            modal.querySelector('.preview-content').innerHTML = '<p class="text-error">Network error.</p>';
        }
    }

    // ==========================================
    // Checklist Image Modal
    // ==========================================

    function initChecklistImageModal() {
        const imageModal = document.getElementById('checklist-image-modal');
        const modalImage = document.getElementById('checklist-modal-image');
        const modalCaption = document.getElementById('checklist-modal-caption');

        if (!imageModal || !modalImage || !modalCaption) return;

        // Add click handlers to all clickable images (delegated event)
        document.addEventListener('click', function(e) {
            const clickableImage = e.target.closest('.checklist-image-clickable');
            if (clickableImage) {
                e.preventDefault();

                // Get image data from data attributes
                const imageSrc = clickableImage.dataset.imageSrc;
                const imageAlt = clickableImage.dataset.imageAlt || 'Checklist image';
                const imageCaption = clickableImage.dataset.imageCaption;

                // Set modal content
                modalImage.src = imageSrc;
                modalImage.alt = imageAlt;

                // Handle caption
                if (imageCaption) {
                    const captionText = modalCaption.querySelector('p');
                    if (captionText) {
                        captionText.textContent = imageCaption;
                        modalCaption.classList.remove('hidden');
                    }
                } else {
                    modalCaption.classList.add('hidden');
                }

                // Show modal
                imageModal.showModal();
            }
        });

        // Keyboard navigation - close on Escape
        imageModal.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                imageModal.close();
            }
        });
    }

    // ==========================================
    // Trophy Selection Functions
    // ==========================================

    /**
     * Handle game selection for checklist.
     */
    function handleGameSelection() {
        // Handle header game selector
        const headerSelector = document.getElementById('checklist-game-selector');
        if (headerSelector) {
            headerSelector.addEventListener('change', async function() {
                const checklistId = this.dataset.checklistId;
                const gameId = this.value;

                if (!gameId) return;

                try {
                    await apiRequest(
                        `/api/v1/checklists/${checklistId}/select-game/`,
                        'POST',
                        { game_id: parseInt(gameId) }
                    );

                    PlatPursuit.ToastManager.show('Game selected successfully', 'success');

                    // Enable trophy selector buttons
                    document.querySelectorAll('.open-trophy-selector-btn').forEach(btn => {
                        btn.disabled = false;
                    });

                    // Reload page to update UI
                    setTimeout(() => reloadWithFormState(), 1000);
                } catch (error) {
                    PlatPursuit.ToastManager.show(error.message, 'error');
                }
            });
        }

        // Handle legacy per-section selectors (for backwards compatibility)
        const selectors = document.querySelectorAll('.game-selector');
        selectors.forEach(selector => {
            selector.addEventListener('change', async function() {
                const checklistId = this.dataset.checklistId;
                const gameId = this.value;

                if (!gameId) return;

                try {
                    await apiRequest(
                        `/api/v1/checklists/${checklistId}/select-game/`,
                        'POST',
                        { game_id: parseInt(gameId) }
                    );

                    PlatPursuit.ToastManager.show('Game selected successfully', 'success');

                    // Enable trophy selector buttons
                    document.querySelectorAll('.open-trophy-selector-btn').forEach(btn => {
                        btn.disabled = false;
                    });

                    // Reload page to update UI
                    setTimeout(() => reloadWithFormState(), 1000);
                } catch (error) {
                    PlatPursuit.ToastManager.show(error.message, 'error');
                }
            });
        });
    }

    /**
     * Open trophy selector modal and load trophies.
     */
    async function openTrophySelector(sectionId, checklistId) {
        const modal = document.getElementById('trophy-selector-modal');
        const listContainer = document.getElementById('trophy-list-container');
        const groupFilter = document.getElementById('trophy-group-filter');

        // Store context
        modal.dataset.sectionId = sectionId;
        modal.dataset.checklistId = checklistId;

        // Show modal
        modal.showModal();

        // Load trophies
        try {
            const data = await apiRequest(
                `/api/v1/checklists/${checklistId}/available-trophies/`,
                'GET'
            );

            // Store trophies data on modal for filtering
            modal.dataset.trophies = JSON.stringify(data.trophies);

            // Populate trophy group filter
            populateTrophyGroupFilter(data.trophy_groups, groupFilter);

            renderTrophyList(data.trophies, listContainer);
        } catch (error) {
            listContainer.innerHTML = `
                <div class="alert alert-error">
                    <span>${error.message}</span>
                </div>
            `;
        }
    }

    /**
     * Render trophy list in selector.
     */
    function renderTrophyList(trophies, container) {
        if (trophies.length === 0) {
            container.innerHTML = `
                <div class="text-center py-8 text-base-content/50">
                    <p>No trophies available.</p>
                </div>
            `;
            return;
        }

        const html = trophies.map(trophy => {
            const isDisabled = trophy.is_used ? 'opacity-50 pointer-events-none' : '';
            const badgeClass = trophy.is_used ? 'badge-ghost' : `badge-${trophy.trophy_type}`;

            // Add DLC badge if not base game
            const dlcBadge = !trophy.is_base_game && trophy.trophy_group_name
                ? `<span class="badge badge-sm badge-info">DLC: ${escapeHtml(trophy.trophy_group_name)}</span>`
                : (!trophy.is_base_game ? '<span class="badge badge-sm badge-info">DLC</span>' : '');

            return `
                <div class="trophy-select-card flex items-center gap-3 p-3 bg-base-200 rounded-lg hover:bg-base-300 transition-colors cursor-pointer ${isDisabled}"
                     data-trophy-id="${trophy.id}"
                     data-trophy-name="${escapeHtml(trophy.trophy_name)}"
                     data-trophy-type="${trophy.trophy_type}"
                     data-trophy-group="${trophy.trophy_group_id}"
                     onclick="${trophy.is_used ? '' : 'selectTrophy(' + trophy.id + ')'}">
                    <img src="${trophy.trophy_icon_url}"
                         alt="${escapeHtml(trophy.trophy_name)}"
                         class="w-12 h-12 rounded shrink-0">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 flex-wrap mb-1">
                            <span class="font-semibold truncate">${escapeHtml(trophy.trophy_name)}</span>
                            <span class="badge badge-sm ${badgeClass}">
                                ${trophy.trophy_type.charAt(0).toUpperCase() + trophy.trophy_type.slice(1)}
                            </span>
                            ${dlcBadge}
                            ${trophy.is_used ? '<span class="badge badge-ghost badge-xs">Already Added</span>' : ''}
                        </div>
                        <p class="text-xs text-base-content/60 line-clamp-2">
                            ${escapeHtml(trophy.trophy_detail || '')}
                        </p>
                        <div class="flex items-center gap-2 mt-1">
                            <span class="text-xs text-base-content/50">${trophy.trophy_earn_rate.toFixed(1)}% earned</span>
                        </div>
                    </div>
                </div>
            `;
        }).join('');

        container.innerHTML = html;
    }

    /**
     * Select a trophy and add to section.
     * Exposed globally for onclick handler.
     */
    window.selectTrophy = async function(trophyId) {
        const modal = document.getElementById('trophy-selector-modal');
        const sectionId = modal.dataset.sectionId;

        try {
            await apiRequest(
                `/api/v1/checklists/sections/${sectionId}/items/`,
                'POST',
                {
                    item_type: 'trophy',
                    trophy_id: trophyId
                }
            );

            PlatPursuit.ToastManager.show('Trophy added successfully', 'success');
            modal.close();

            // Reload page to show new trophy item
            setTimeout(() => reloadWithFormState(), 500);
        } catch (error) {
            PlatPursuit.ToastManager.show(error.message, 'error');
        }
    }

    /**
     * Populate trophy group filter dropdown.
     */
    function populateTrophyGroupFilter(trophyGroups, selectElement) {
        if (!selectElement || !trophyGroups) return;

        // Clear existing options except "All Groups"
        selectElement.innerHTML = '<option value="">All Groups</option>';

        // Sort: Base game first, then DLC groups
        const sorted = trophyGroups.sort((a, b) => {
            if (a.is_base_game) return -1;
            if (b.is_base_game) return 1;
            return a.trophy_group_name.localeCompare(b.trophy_group_name);
        });

        sorted.forEach(group => {
            const option = document.createElement('option');
            option.value = group.trophy_group_id;
            option.textContent = group.is_base_game
                ? 'Base Game'
                : (group.trophy_group_name || `DLC (${group.trophy_group_id})`);
            selectElement.appendChild(option);
        });
    }

    /**
     * Filter trophies by search, type, and group.
     */
    function filterTrophies() {
        const searchInput = document.getElementById('trophy-search-input');
        const typeFilter = document.getElementById('trophy-type-filter');
        const groupFilter = document.getElementById('trophy-group-filter');

        const searchTerm = searchInput.value.toLowerCase();
        const selectedType = typeFilter.value.toLowerCase();
        const selectedGroup = groupFilter.value;

        const cards = document.querySelectorAll('.trophy-select-card');

        cards.forEach(card => {
            const name = card.dataset.trophyName.toLowerCase();
            const type = card.dataset.trophyType.toLowerCase();
            const group = card.dataset.trophyGroup;

            const matchesSearch = name.includes(searchTerm);
            const matchesType = !selectedType || type === selectedType;
            const matchesGroup = !selectedGroup || group === selectedGroup;

            card.style.display = (matchesSearch && matchesType && matchesGroup) ? 'flex' : 'none';
        });
    }

    /**
     * Initialize trophy selection handlers.
     */
    function initTrophySelection() {
        // Open modal buttons
        document.querySelectorAll('.open-trophy-selector-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const sectionId = this.dataset.sectionId;
                const checklistId = this.dataset.checklistId;
                openTrophySelector(sectionId, checklistId);
            });
        });

        // Search and filter
        const searchInput = document.getElementById('trophy-search-input');
        const typeFilter = document.getElementById('trophy-type-filter');
        const groupFilter = document.getElementById('trophy-group-filter');

        if (searchInput) {
            searchInput.addEventListener('input', filterTrophies);
        }

        if (typeFilter) {
            typeFilter.addEventListener('change', filterTrophies);
        }

        if (groupFilter) {
            groupFilter.addEventListener('change', filterTrophies);
        }
    }

    // ==========================================
    // Section Collapse/Expand
    // ==========================================

    function initSectionCollapse() {
        document.querySelectorAll('.section-collapse-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const sectionId = this.dataset.sectionId;
                const section = document.querySelector(`.checklist-section[data-section-id="${sectionId}"]`);
                if (!section) {
                    console.error('Section not found:', sectionId);
                    return;
                }

                const itemsContent = section.querySelector('.section-items-content');
                const icon = this.querySelector('.collapse-icon');

                if (!itemsContent) {
                    console.error('Items content not found');
                    return;
                }

                // Toggle collapsed state
                const isCollapsed = itemsContent.style.display === 'none';

                if (isCollapsed) {
                    // Expand
                    itemsContent.style.display = 'block';
                    icon.style.transform = 'rotate(0deg)';
                } else {
                    // Collapse
                    itemsContent.style.display = 'none';
                    icon.style.transform = 'rotate(-90deg)';
                }
            });
        });
    }

    // ==========================================
    // Check All / Uncheck All
    // ==========================================

    function initBulkCheckButtons() {
        // Check All buttons
        document.querySelectorAll('.section-check-all-btn').forEach(btn => {
            btn.addEventListener('click', async function() {
                const sectionId = this.dataset.sectionId;
                const section = document.querySelector(`.checklist-section[data-section-id="${sectionId}"]`);
                if (!section) {
                    console.error('Section not found for check all:', sectionId);
                    return;
                }

                const checklistContainer = document.getElementById('checklist-detail-container');
                if (!checklistContainer) {
                    console.error('Checklist container not found');
                    return;
                }

                const checklistId = checklistContainer.dataset.checklistId;
                const canSaveProgress = checklistContainer.dataset.canSaveProgress === 'true';

                if (!canSaveProgress) {
                    PlatPursuit.ToastManager.show('Progress cannot be saved for this guide.', 'warning');
                    return;
                }

                // Disable button during operation
                btn.disabled = true;

                try {
                    // Call bulk update API
                    const response = await apiRequest(
                        `${API_BASE}/checklists/${checklistId}/sections/${sectionId}/bulk-progress/`,
                        'POST',
                        { mark_complete: true }
                    );

                    if (response.success) {
                        // Update all checkboxes in the section
                        const checkboxes = section.querySelectorAll('.checklist-item[data-item-type="item"] .checklist-item-checkbox, .checklist-trophy-item[data-item-type="trophy"] .checklist-item-checkbox');
                        checkboxes.forEach(checkbox => {
                            checkbox.checked = true;
                            // Update text styling
                            const itemText = checkbox.closest('label')?.querySelector('.checklist-item-text');
                            if (itemText) {
                                itemText.classList.add('line-through', 'text-base-content/50');
                            }
                        });

                        // Update progress bar with correct object format
                        updateProgressDisplay({
                            percentage: response.progress_percentage,
                            items_completed: response.items_completed,
                            total_items: response.total_items
                        });

                        // Update section counts
                        updateSectionCounts();

                        PlatPursuit.ToastManager.show(`All items checked (${response.updated_count} items)`, 'success');
                    } else {
                        PlatPursuit.ToastManager.show(response.error || 'Failed to check all items', 'error');
                    }
                } catch (error) {
                    console.error('Bulk check error:', error);
                    PlatPursuit.ToastManager.show('Failed to check all items', 'error');
                } finally {
                    btn.disabled = false;
                }
            });
        });

        // Uncheck All buttons
        document.querySelectorAll('.section-uncheck-all-btn').forEach(btn => {
            btn.addEventListener('click', async function() {
                const sectionId = this.dataset.sectionId;
                const section = document.querySelector(`.checklist-section[data-section-id="${sectionId}"]`);
                if (!section) {
                    console.error('Section not found for uncheck all:', sectionId);
                    return;
                }

                const checklistContainer = document.getElementById('checklist-detail-container');
                if (!checklistContainer) {
                    console.error('Checklist container not found');
                    return;
                }

                const checklistId = checklistContainer.dataset.checklistId;
                const canSaveProgress = checklistContainer.dataset.canSaveProgress === 'true';

                if (!canSaveProgress) {
                    PlatPursuit.ToastManager.show('Progress cannot be saved for this guide.', 'warning');
                    return;
                }

                // Disable button during operation
                btn.disabled = true;

                try {
                    // Call bulk update API
                    const response = await apiRequest(
                        `${API_BASE}/checklists/${checklistId}/sections/${sectionId}/bulk-progress/`,
                        'POST',
                        { mark_complete: false }
                    );

                    if (response.success) {
                        // Update all checkboxes in the section
                        const checkboxes = section.querySelectorAll('.checklist-item[data-item-type="item"] .checklist-item-checkbox, .checklist-trophy-item[data-item-type="trophy"] .checklist-item-checkbox');
                        checkboxes.forEach(checkbox => {
                            // Don't uncheck earned trophies - they should stay checked
                            const itemContainer = checkbox.closest('.checklist-item, .checklist-trophy-item');
                            if (itemContainer && itemContainer.dataset.earned === 'true') {
                                return;
                            }
                            checkbox.checked = false;
                            // Update text styling
                            const itemText = checkbox.closest('label')?.querySelector('.checklist-item-text');
                            if (itemText) {
                                itemText.classList.remove('line-through', 'text-base-content/50');
                            }
                        });

                        // Update progress bar with correct object format
                        updateProgressDisplay({
                            percentage: response.progress_percentage,
                            items_completed: response.items_completed,
                            total_items: response.total_items
                        });

                        // Update section counts
                        updateSectionCounts();

                        PlatPursuit.ToastManager.show(`All items unchecked (${response.updated_count} items)`, 'success');
                    } else {
                        PlatPursuit.ToastManager.show(response.error || 'Failed to uncheck all items', 'error');
                    }
                } catch (error) {
                    console.error('Bulk uncheck error:', error);
                    PlatPursuit.ToastManager.show('Failed to uncheck all items', 'error');
                } finally {
                    btn.disabled = false;
                }
            });
        });
    }

    // ==========================================
    // Initialize
    // ==========================================

    document.addEventListener('DOMContentLoaded', function() {
        // Restore any unsaved form data from before page reload
        const hasRestoredFields = restoreFormState();

        initChecklistDetail();
        initChecklistEdit();
        initChecklistSection();
        initChecklistImageModal();
        // Text area functionality
        initTextAreaCharCounters();
        initTextAreaHandlers();
        initMarkdownPreview();
        // Trophy functionality
        handleGameSelection();
        initTrophySelection();
        // Section controls
        initSectionCollapse();
        initBulkCheckButtons();
        // Unsaved changes warning - skip capturing original state if we restored unsaved changes
        initUnsavedChangesWarning(hasRestoredFields);
    });

})();
