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
            sections: {},
            // Scroll restoration data
            scrollPosition: window.pageYOffset || document.documentElement.scrollTop,
            // Optional metadata for enhanced navigation
            metadata: {
                scrollToSection: null  // Set when we want to scroll to a specific section
            }
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

            // Restore scroll position or scroll to target section
            if (state.metadata?.scrollToSection) {
                // Enhanced navigation: scroll to specific section (e.g., newly added)
                const targetSection = document.querySelector(`.checklist-section[data-section-id="${state.metadata.scrollToSection}"]`);
                if (targetSection) {
                    // Use requestAnimationFrame to ensure DOM is fully rendered
                    requestAnimationFrame(() => {
                        const yOffset = -100; // Offset for navbar + breathing room
                        const y = targetSection.getBoundingClientRect().top + window.pageYOffset + yOffset;

                        window.scrollTo({
                            top: y,
                            behavior: 'smooth'  // Smooth scroll to highlight the new section
                        });

                        // Add brief highlight effect to make section more visible
                        targetSection.style.transition = 'background-color 0.3s ease';
                        targetSection.style.backgroundColor = 'rgba(var(--bs-primary-rgb), 0.1)';
                        setTimeout(() => {
                            targetSection.style.backgroundColor = '';
                        }, 2000);
                    });
                } else {
                    // Fallback: restore exact scroll position
                    restoreScrollPosition(state.scrollPosition);
                }
            } else if (state.scrollPosition !== undefined) {
                // Default: restore exact scroll position
                restoreScrollPosition(state.scrollPosition);
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

    function restoreScrollPosition(scrollY) {
        // Use requestAnimationFrame to ensure DOM is fully rendered before scrolling
        requestAnimationFrame(() => {
            window.scrollTo({
                top: scrollY,
                behavior: 'auto'  // Instant scroll - less jarring than jumping after visible render
            });
        });
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
        saveFormState();
        // Use forceNavigate to bypass unsaved changes warning
        PlatPursuit.UnsavedChangesManager.forceNavigate(window.location.href);
    }

    // Helper to reload page with scroll metadata (e.g., to scroll to newly added section)
    function reloadWithScrollTarget(sectionId = null) {
        saveFormState();

        // Enhance saved state with scroll target
        const savedState = sessionStorage.getItem(FORM_STATE_KEY);
        if (savedState) {
            try {
                const state = JSON.parse(savedState);
                if (sectionId) {
                    state.metadata = state.metadata || {};
                    state.metadata.scrollToSection = sectionId;
                }
                sessionStorage.setItem(FORM_STATE_KEY, JSON.stringify(state));
            } catch (e) {
                console.error('Failed to enhance form state:', e);
            }
        }

        // Use forceNavigate to bypass unsaved changes warning
        PlatPursuit.UnsavedChangesManager.forceNavigate(window.location.href);
    }

    // ==========================================
    // Unsaved Changes Warning
    // ==========================================
    // Tracks form changes and warns user before leaving with unsaved work
    // Uses the shared UnsavedChangesManager from utils.js

    let originalFormState = {};

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

    function initUnsavedChangesWarning(skipCaptureOriginalState = false) {
        const checklistId = getChecklistId();
        if (!checklistId) return;

        // Capture initial state after DOM is ready (unless we restored form state)
        if (!skipCaptureOriginalState) {
            captureOriginalState();
        }

        // Use the shared UnsavedChangesManager
        PlatPursuit.UnsavedChangesManager.init({
            hasUnsavedChanges: hasUnsavedChanges,
            showSaveButton: true,
            onSaveAndLeave: async () => {
                // Quick save of title/description before navigating
                const title = document.getElementById('checklist-title')?.value.trim();
                const description = document.getElementById('checklist-description')?.value.trim();

                if (!title) {
                    throw new Error('Title is required');
                }

                await apiRequest(`${API_BASE}/checklists/${checklistId}/`, 'PATCH', {
                    title,
                    description,
                });
                clearFormState();
            }
        });
    }

    // API helper — delegates to PlatPursuit.API with error-message extraction
    async function apiRequest(url, method = 'GET', data = null) {
        const options = {
            method,
            headers: { 'Content-Type': 'application/json' },
        };
        if (data) {
            options.body = JSON.stringify(data);
        }
        try {
            return await PlatPursuit.API.request(url, options);
        } catch (err) {
            // Extract server error message from response body
            let json;
            try { json = await err.response?.json(); } catch {}
            const error = new Error(json?.error || json?.detail || 'Request failed');
            error.responseData = json;
            throw error;
        }
    }

    // Escape HTML for safe DOM insertion (delegates to shared utility)
    const escapeHtml = PlatPursuit.HTMLUtils.escape;

    // ==========================================
    // Batch Trophy Selection State
    // ==========================================

    /**
     * Batch selection state manager for trophy multi-select.
     */
    const batchSelectionState = {
        enabled: false,
        selectedTrophyIds: new Set(),

        toggle(enabled) {
            this.enabled = enabled;
            if (!enabled) {
                this.clear();
            }
        },

        add(trophyId) {
            this.selectedTrophyIds.add(trophyId);
        },

        remove(trophyId) {
            this.selectedTrophyIds.delete(trophyId);
        },

        has(trophyId) {
            return this.selectedTrophyIds.has(trophyId);
        },

        clear() {
            this.selectedTrophyIds.clear();
        },

        getAll() {
            return Array.from(this.selectedTrophyIds);
        },

        count() {
            return this.selectedTrophyIds.size;
        }
    };

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
    function showBulkUploadErrors(errors) {
        if (!errors || errors.length === 0) return;

        // Prepare failed items text for copy-to-clipboard
        const failedItemsText = errors.map(item => item.text).join('\n');

        const modalHtml = `
            <dialog id="bulk-error-modal" class="modal modal-open">
                <div class="modal-box max-w-2xl">
                    <h3 class="font-bold text-lg text-warning">Partial Upload - Some Items Failed</h3>
                    <div class="py-4">
                        <p class="text-sm mb-3">Valid items were added successfully. The following ${errors.length} item(s) failed validation:</p>

                        <div class="max-h-80 overflow-y-auto space-y-2 mb-4">
                            ${errors.map(item => `
                                <div class="alert alert-error text-xs p-3">
                                    <div class="w-full">
                                        <div class="flex items-start gap-2 mb-1">
                                            <span class="badge badge-error badge-sm">Line ${item.index + 1}</span>
                                            <span class="font-mono text-xs flex-1">"${PlatPursuit.HTMLUtils.escape(item.text.length > 100 ? item.text.substring(0, 100) + '...' : item.text)}"</span>
                                        </div>
                                        <p class="text-error-content/80 text-xs mt-1">⚠ ${PlatPursuit.HTMLUtils.escape(item.error)}</p>
                                    </div>
                                </div>
                            `).join('')}
                        </div>

                        <button class="btn btn-sm btn-outline" onclick="
                            const text = \`${failedItemsText.replace(/`/g, '\\`')}\`;
                            navigator.clipboard.writeText(text).then(() => {
                                PlatPursuit.ToastManager.success('Failed items copied to clipboard');
                            }).catch(() => {
                                PlatPursuit.ToastManager.error('Failed to copy');
                            });
                        ">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/>
                            </svg>
                            Copy Failed Items
                        </button>

                        <p class="text-xs mt-3 text-base-content/60">
                            Fix the errors and paste them back to retry.
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

            // Add successful items to DOM
            if (result.items && result.items.length > 0) {
                result.items.forEach(item => {
                    addItemToDOM(section, item);
                });
            }

            // Handle response
            if (progressBar) progressBar.value = 100;

            if (result.failed === 0) {
                // Full success
                if (progressText) progressText.textContent = `Successfully uploaded ${result.created} items!`;
                PlatPursuit.ToastManager.success(`Added ${result.created} items!`);

                // Clear textarea
                textarea.value = '';
                updateBulkPreview(section);
            } else {
                // Partial success
                if (progressText) progressText.textContent = `Added ${result.created} items, ${result.failed} failed`;
                PlatPursuit.ToastManager.warning(
                    `Added ${result.created} items, ${result.failed} failed. See details below.`,
                    { duration: 5000 }
                );

                // Show error modal
                showBulkUploadErrors(result.errors);
            }

            // Hide progress after delay
            setTimeout(() => {
                if (progressEl) progressEl.classList.add('hidden');
            }, 2000);

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
                    updateProgressDisplay(result);
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

        // API now returns the correct adjusted count that includes earned trophies
        // No need for JavaScript to calculate anything
        const adjustedCompleted = progress.items_completed || 0;
        const adjustedPercentage = progress.progress_percentage || 0;

        // Ensure percentage is finite before setting (avoid NaN/Infinity errors)
        const safePercentage = Number.isFinite(adjustedPercentage) ? adjustedPercentage : 0;

        if (progressBar) {
            progressBar.value = safePercentage;
        }
        if (progressPercentage) {
            progressPercentage.textContent = Math.round(safePercentage) + '%';
        }
        if (itemsCompleted) {
            itemsCompleted.textContent = adjustedCompleted;
        }
        if (itemsTotal) {
            itemsTotal.textContent = progress.total_items || 0;
        }

        // Update section counts
        updateSectionCounts();

        // Trigger celebration when reaching 100% (not when already at 100%)
        if (safePercentage >= 100 && previousPercentage < 100) {
            celebrateCompletion();
        }
    }

    function celebrateCompletion() {
        // Use shared CelebrationManager (loads confetti dynamically)
        if (window.PlatPursuit?.CelebrationManager) {
            PlatPursuit.CelebrationManager.celebrateChecklistComplete();
        }
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

        // Handle TOC item clicks - instant scroll for reliability with lazy images
        tocItems.forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();

                const sectionId = item.dataset.sectionId;
                const targetSection = document.getElementById(`section-${sectionId}`);

                if (targetSection) {
                    // Instant scroll to section with offset for fixed header
                    const yOffset = -100; // Offset for navbar + breathing room
                    const y = targetSection.getBoundingClientRect().top + window.pageYOffset + yOffset;

                    window.scrollTo({
                        top: y,
                        behavior: 'auto'  // Instant jump - prevents race with lazy-loaded images
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
        const isPublished = container.dataset.isPublished === 'true';

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

        // Drag-and-drop reordering for items (only if not published)
        if (!isPublished) {
            initDragAndDrop(checklistId);
            initSectionArrowButtons(checklistId);
        }

        // Auto-save on blur
        initAutoSave(checklistId);

        // Collapse/expand controls
        if (!isPublished) {
            initCollapseExpand(checklistId);
        }

        // Character counters
        initCharacterCounters();

        // Type selectors
        initTypeSelectors();

        // Bulk upload
        initBulkUpload();

        // Image handling
        initImagePreviews();
        initImageUploads();

        // Keyboard shortcuts
        initKeyboardShortcuts();
    }

    function initKeyboardShortcuts() {
        // Intercept Ctrl/Cmd+S to show "Saved" message instead of browser save
        document.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                e.preventDefault();
                if (!window.PlatPursuit?.ToastManager) {
                    console.error('ToastManager not available');
                    return;
                }
                PlatPursuit.ToastManager.success('Saved', 2000);
            }
        });
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

                // Collect all section updates
                const sectionUpdates = [];
                const sections = document.querySelectorAll('.checklist-section');
                for (const section of sections) {
                    const sectionId = section.dataset.sectionId;
                    const subtitle = section.querySelector('.section-title-input')?.value.trim();
                    const sectionDescription = section.querySelector('.section-description-input')?.value.trim();

                    if (subtitle) {
                        sectionUpdates.push({
                            sectionId,
                            subtitle,
                            description: sectionDescription
                        });
                    }
                }

                // Save sections in parallel (limited concurrency)
                const sectionPromises = sectionUpdates.map(async (update) => {
                    try {
                        await apiRequest(`${API_BASE}/checklists/${checklistId}/sections/${update.sectionId}/`, 'PATCH', {
                            subtitle: update.subtitle,
                            description: update.description,
                        });
                        return { success: true };
                    } catch (error) {
                        console.error(`Failed to save section ${update.sectionId}:`, error);
                        return { success: false };
                    }
                });

                const sectionResults = await Promise.all(sectionPromises);
                successCount += sectionResults.filter(r => r.success).length;
                errorCount += sectionResults.filter(r => !r.success).length;

                // Collect all item updates for bulk update
                const itemUpdates = [];
                for (const section of sections) {
                    const items = section.querySelectorAll('.checklist-item-edit, .checklist-text-area-edit, .checklist-image-item');
                    for (const item of items) {
                        const itemId = item.dataset.itemId;
                        const itemType = item.dataset.itemType;

                        // Skip trophy items (they can't be edited)
                        if (itemType === 'trophy') continue;

                        const textInput = item.querySelector('.item-text-input');
                        if (!textInput) continue;

                        const text = textInput.value.trim();
                        const typeSelect = item.querySelector('.item-type-select');
                        const finalItemType = typeSelect ? typeSelect.value : itemType;

                        // Include all items with text (or images with optional caption)
                        if (text || itemType === 'image') {
                            itemUpdates.push({
                                id: parseInt(itemId, 10),
                                text: text || '',
                                item_type: finalItemType
                            });
                        }
                    }
                }

                // Bulk update all items in a single request
                if (itemUpdates.length > 0) {
                    try {
                        const result = await apiRequest(
                            `${API_BASE}/checklists/${checklistId}/items/bulk-update/`,
                            'POST',
                            { items: itemUpdates }
                        );
                        successCount += result.updated_count || 0;
                    } catch (error) {
                        errorCount++;
                        console.error('Failed to bulk update items:', error);

                        // If bulk update failed, show specific errors if available
                        const responseData = error.responseData;
                        if (responseData) {
                            console.error('Bulk update error details:', responseData);
                            if (responseData.failed_items) {
                                responseData.failed_items.forEach(item => {
                                    console.error(`Item ${item.id} (index ${item.index}): ${item.error}`);
                                });
                            }
                            if (responseData.summary) {
                                console.error('Summary:', responseData.summary);
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
                const response = await apiRequest(`${API_BASE}/checklists/${checklistId}/sections/`, 'POST', {
                    subtitle: 'New Section',
                });

                PlatPursuit.ToastManager.show('Section added!', 'success');

                // Get the new section ID from the response
                const newSectionId = response.section?.id;

                // Reload page and scroll to the new section
                setTimeout(() => {
                    if (newSectionId) {
                        reloadWithScrollTarget(newSectionId);
                    } else {
                        // Fallback: normal reload with scroll position preservation
                        reloadWithFormState();
                    }
                }, 500);
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

    function initDragAndDrop(checklistId) {
        // Sections now use arrow buttons - no drag needed for sections

        // Initialize drag-and-drop for items within each section
        // Note: This works even on hidden containers - the drag events will fire when sections are expanded
        const containers = document.querySelectorAll('.section-items-container');
        console.log(`Initializing drag for ${containers.length} item containers`);

        containers.forEach(container => {
            const sectionId = container.dataset.sectionId;
            if (!sectionId) {
                console.warn('Found container without section ID:', container);
                return;
            }

            const items = container.querySelectorAll('.checklist-item-edit, .checklist-image-item, .checklist-text-area-edit, .checklist-trophy-item-edit');
            console.log(`Section ${sectionId}: ${items.length} draggable items found`);

            new PlatPursuit.DragReorderManager({
                container: container,
                itemSelector: '.checklist-item-edit, .checklist-image-item, .checklist-text-area-edit, .checklist-trophy-item-edit',
                handleSelector: '.item-drag-handle',
                onReorder: async (itemId, newPosition, allItemIds) => {
                    console.log('Item reordered:', { itemId, newPosition, allItemIds });
                    try {
                        await apiRequest(`${API_BASE}/checklists/sections/${sectionId}/items/reorder/`, 'POST', {
                            ids: allItemIds.map(id => parseInt(id))
                        });
                        PlatPursuit.ToastManager.success('Reordered', 1500);
                    } catch (error) {
                        console.error('Failed to reorder items:', error);
                        PlatPursuit.ToastManager.error('Failed to reorder items');
                    }
                }
            });
        });
    }

    function initSectionArrowButtons(checklistId) {
        // Move up handler
        document.addEventListener('click', async (e) => {
            if (!e.target.closest('.section-move-up')) return;

            const section = e.target.closest('.checklist-section');
            if (!section) return;

            const prev = section.previousElementSibling;
            if (prev && prev.classList.contains('checklist-section')) {
                // Move section before its previous sibling
                section.parentNode.insertBefore(section, prev);

                // Save new order
                try {
                    const allSections = Array.from(document.querySelectorAll('.checklist-section'));
                    const sectionIds = allSections.map(s => parseInt(s.dataset.sectionId)).filter(id => !isNaN(id));

                    await apiRequest(`${API_BASE}/checklists/${checklistId}/sections/reorder/`, 'POST', {
                        ids: sectionIds
                    });
                    PlatPursuit.ToastManager.success('Moved up', 1500);
                } catch (error) {
                    console.error('Failed to save section order:', error);
                    PlatPursuit.ToastManager.error('Failed to save order');
                }
            }
        });

        // Move down handler
        document.addEventListener('click', async (e) => {
            if (!e.target.closest('.section-move-down')) return;

            const section = e.target.closest('.checklist-section');
            if (!section) return;

            const next = section.nextElementSibling;
            if (next && next.classList.contains('checklist-section')) {
                // Move section after its next sibling
                section.parentNode.insertBefore(next, section);

                // Save new order
                try {
                    const allSections = Array.from(document.querySelectorAll('.checklist-section'));
                    const sectionIds = allSections.map(s => parseInt(s.dataset.sectionId)).filter(id => !isNaN(id));

                    await apiRequest(`${API_BASE}/checklists/${checklistId}/sections/reorder/`, 'POST', {
                        ids: sectionIds
                    });
                    PlatPursuit.ToastManager.success('Moved down', 1500);
                } catch (error) {
                    console.error('Failed to save section order:', error);
                    PlatPursuit.ToastManager.error('Failed to save order');
                }
            }
        });
    }

    function initAutoSave(checklistId) {
        // Track original values for comparison
        const originalValues = {
            title: document.getElementById('checklist-title')?.value || '',
            description: document.getElementById('checklist-description')?.value || ''
        };

        // Auto-save checklist title on blur
        const titleInput = document.getElementById('checklist-title');
        if (titleInput) {
            titleInput.addEventListener('blur', async () => {
                const newTitle = titleInput.value.trim();
                if (newTitle && newTitle !== originalValues.title) {
                    try {
                        await PlatPursuit.API.patch(`${API_BASE}/checklists/${checklistId}/`, {
                            title: newTitle
                        });
                        originalValues.title = newTitle;
                        if (!window.PlatPursuit?.ToastManager) {
                            console.error('ToastManager not available');
                        } else {
                            PlatPursuit.ToastManager.success('Saved', 2000);
                        }
                    } catch (error) {
                        console.error('Failed to save checklist title:', error);
                        if (window.PlatPursuit?.ToastManager) {
                            PlatPursuit.ToastManager.error('Failed to save title');
                        }
                        titleInput.value = originalValues.title; // Revert on error
                    }
                }
            });
        }

        // Auto-save checklist description on blur
        const descInput = document.getElementById('checklist-description');
        if (descInput) {
            descInput.addEventListener('blur', async () => {
                const newDesc = descInput.value.trim();
                if (newDesc !== originalValues.description) {
                    try {
                        await PlatPursuit.API.patch(`${API_BASE}/checklists/${checklistId}/`, {
                            description: newDesc
                        });
                        originalValues.description = newDesc;
                        if (!window.PlatPursuit?.ToastManager) {
                            console.error('ToastManager not available');
                        } else {
                            PlatPursuit.ToastManager.success('Saved', 2000);
                        }
                    } catch (error) {
                        console.error('Failed to save checklist description:', error);
                        if (window.PlatPursuit?.ToastManager) {
                            PlatPursuit.ToastManager.error('Failed to save description');
                        }
                        descInput.value = originalValues.description; // Revert on error
                    }
                }
            });
        }

        // Auto-save section title on blur (delegated)
        document.addEventListener('blur', async (e) => {
            if (e.target.matches('.section-title-input')) {
                const section = e.target.closest('.checklist-section');
                const sectionId = section?.dataset.sectionId;
                if (!sectionId) return;

                const newTitle = e.target.value.trim();
                const originalTitle = e.target.dataset.originalValue || e.target.defaultValue;

                if (newTitle && newTitle !== originalTitle) {
                    try {
                        await PlatPursuit.API.patch(`${API_BASE}/checklists/${checklistId}/sections/${sectionId}/`, {
                            subtitle: newTitle
                        });
                        e.target.dataset.originalValue = newTitle;
                        if (!window.PlatPursuit?.ToastManager) {
                            console.error('ToastManager not available');
                        } else {
                            PlatPursuit.ToastManager.success('Saved', 2000);
                        }
                    } catch (error) {
                        console.error('Failed to save section title:', error);
                        if (window.PlatPursuit?.ToastManager) {
                            PlatPursuit.ToastManager.error('Failed to save');
                        }
                        e.target.value = originalTitle; // Revert on error
                    }
                }
            }
        }, true);

        // Auto-save section description on blur (delegated)
        document.addEventListener('blur', async (e) => {
            if (e.target.matches('.section-description-input')) {
                const section = e.target.closest('.checklist-section');
                const sectionId = section?.dataset.sectionId;
                if (!sectionId) return;

                const newDesc = e.target.value.trim();
                const originalDesc = e.target.dataset.originalValue || e.target.defaultValue;

                if (newDesc !== originalDesc) {
                    try {
                        await PlatPursuit.API.patch(`${API_BASE}/checklists/${checklistId}/sections/${sectionId}/`, {
                            description: newDesc
                        });
                        e.target.dataset.originalValue = newDesc;
                        if (!window.PlatPursuit?.ToastManager) {
                            console.error('ToastManager not available');
                        } else {
                            PlatPursuit.ToastManager.success('Saved', 2000);
                        }
                    } catch (error) {
                        console.error('Failed to save section description:', error);
                        if (window.PlatPursuit?.ToastManager) {
                            PlatPursuit.ToastManager.error('Failed to save');
                        }
                        e.target.value = originalDesc; // Revert on error
                    }
                }
            }
        }, true);

        // Auto-save item text on blur (delegated)
        document.addEventListener('blur', async (e) => {
            if (e.target.matches('.item-text-input')) {
                const itemId = e.target.dataset.itemId;
                if (!itemId) return;

                const newText = e.target.value.trim();
                const originalText = e.target.dataset.originalValue || e.target.defaultValue;

                if (newText && newText !== originalText) {
                    try {
                        await PlatPursuit.API.patch(`${API_BASE}/checklists/items/${itemId}/`, {
                            text: newText
                        });
                        e.target.dataset.originalValue = newText;
                        if (!window.PlatPursuit?.ToastManager) {
                            console.error('ToastManager not available');
                        } else {
                            PlatPursuit.ToastManager.success('Saved', 2000);
                        }
                    } catch (error) {
                        console.error('Failed to save item text:', error);
                        if (window.PlatPursuit?.ToastManager) {
                            PlatPursuit.ToastManager.error('Failed to save');
                        }
                        e.target.value = originalText; // Revert on error
                    }
                }
            }
        }, true);

        // Auto-save item type on change (delegated)
        document.addEventListener('change', async (e) => {
            if (e.target.matches('.item-type-select')) {
                const item = e.target.closest('.checklist-item-edit');
                const itemId = item?.dataset.itemId;
                if (!itemId) return;

                const newType = e.target.value;
                const textInput = item.querySelector('.item-text-input');

                try {
                    await PlatPursuit.API.patch(`${API_BASE}/checklists/items/${itemId}/`, {
                        item_type: newType
                    });

                    // Update styling based on type
                    if (newType === 'sub_header') {
                        textInput.placeholder = 'Sub-header text...';
                        textInput.classList.add('font-semibold', 'input-primary');
                    } else {
                        textInput.placeholder = 'Item text...';
                        textInput.classList.remove('font-semibold', 'input-primary');
                    }

                    if (!window.PlatPursuit?.ToastManager) {
                        console.error('ToastManager not available');
                    } else {
                        PlatPursuit.ToastManager.success('Saved', 2000);
                    }
                } catch (error) {
                    console.error('Failed to save item type:', error);
                    if (window.PlatPursuit?.ToastManager) {
                        PlatPursuit.ToastManager.error('Failed to save');
                    }
                    // Revert type selector on error
                    e.target.value = e.target.dataset.originalValue || 'item';
                }
            }
        }, true);
    }

    function initCollapseExpand(checklistId) {
        const collapseAllBtn = document.getElementById('collapse-all-btn');
        const expandAllBtn = document.getElementById('expand-all-btn');

        if (!collapseAllBtn || !expandAllBtn) return;

        // NEW BEHAVIOR: Track EXPANDED sections instead of collapsed
        // Sections are collapsed by default, expanded sections are tracked
        const newStorageKey = `checklist_${checklistId}_expanded_sections`;
        const oldStorageKey = `checklist_${checklistId}_collapsed_sections`;

        // Migration: Convert old collapsed_sections to new expanded_sections
        let expandedSections = [];
        if (sessionStorage.getItem(newStorageKey)) {
            // New format exists, use it
            expandedSections = JSON.parse(sessionStorage.getItem(newStorageKey) || '[]');
        } else if (sessionStorage.getItem(oldStorageKey)) {
            // Migrate from old format
            const allSectionIds = Array.from(document.querySelectorAll('.section-items-content'))
                .map(content => content.dataset.sectionId)
                .filter(id => id);

            const oldCollapsed = JSON.parse(sessionStorage.getItem(oldStorageKey) || '[]');
            // Expanded = all sections NOT in collapsed list
            expandedSections = allSectionIds.filter(id => !oldCollapsed.includes(id));

            // Save in new format and remove old
            sessionStorage.setItem(newStorageKey, JSON.stringify(expandedSections));
            sessionStorage.removeItem(oldStorageKey);
        }
        // If neither exists, expandedSections stays empty (all collapsed by default)

        // Template starts with all sections collapsed (content hidden)
        // We need to:
        // 1. Expand ONLY sections that are in expandedSections
        // 2. Keep all others collapsed (new default behavior)

        document.querySelectorAll('.section-items-content').forEach(content => {
            const sectionId = content.dataset.sectionId;
            const summary = document.querySelector(`.section-items-summary[data-section-id="${sectionId}"]`);
            const toggle = document.querySelector(`.section-items-toggle[data-section-id="${sectionId}"]`);
            const icon = toggle?.querySelector('.section-toggle-icon');

            const shouldBeExpanded = expandedSections.includes(sectionId);

            if (shouldBeExpanded) {
                // Expand this section
                if (summary) summary.classList.add('hidden');
                content.classList.remove('hidden');
                if (toggle) toggle.setAttribute('aria-expanded', 'true');
                if (icon) icon.style.transform = 'rotate(0deg)'; // Expanded: down-pointing
            } else {
                // Keep collapsed (new default)
                if (summary) summary.classList.remove('hidden');
                content.classList.add('hidden');
                if (toggle) toggle.setAttribute('aria-expanded', 'false');
                if (icon) icon.style.transform = 'rotate(-90deg)'; // Collapsed: right-pointing
            }
        });

        // Collapse all button - clears expanded list
        collapseAllBtn.addEventListener('click', () => {
            document.querySelectorAll('.section-items-content').forEach(content => {
                const sectionId = content.dataset.sectionId;
                const summary = document.querySelector(`.section-items-summary[data-section-id="${sectionId}"]`);

                // Show summary, hide content
                if (summary) summary.classList.remove('hidden');
                content.classList.add('hidden');

                const toggle = document.querySelector(`.section-items-toggle[data-section-id="${sectionId}"]`);
                if (toggle) {
                    toggle.setAttribute('aria-expanded', 'false');
                    const icon = toggle.querySelector('.section-toggle-icon');
                    if (icon) icon.style.transform = 'rotate(-90deg)'; // Collapsed: -90deg (right)
                }
            });
            // Clear expanded list (all collapsed)
            sessionStorage.setItem(newStorageKey, JSON.stringify([]));
        });

        // Expand all button - adds all sections to expanded list
        expandAllBtn.addEventListener('click', () => {
            const allSections = [];
            document.querySelectorAll('.section-items-content').forEach(content => {
                const sectionId = content.dataset.sectionId;
                const summary = document.querySelector(`.section-items-summary[data-section-id="${sectionId}"]`);

                // Hide summary, show content
                if (summary) summary.classList.add('hidden');
                content.classList.remove('hidden');

                const toggle = document.querySelector(`.section-items-toggle[data-section-id="${sectionId}"]`);
                if (toggle) {
                    toggle.setAttribute('aria-expanded', 'true');
                    const icon = toggle.querySelector('.section-toggle-icon');
                    if (icon) icon.style.transform = 'rotate(0deg)'; // Expanded: 0deg (down)
                }

                if (sectionId) allSections.push(sectionId);
            });
            // Save all as expanded
            sessionStorage.setItem(newStorageKey, JSON.stringify(allSections));
        });

        // Individual section toggle (existing toggle buttons)
        document.addEventListener('click', (e) => {
            const toggle = e.target.closest('.section-items-toggle');
            if (!toggle) return;

            const sectionId = toggle.dataset.sectionId;
            const summary = document.querySelector(`.section-items-summary[data-section-id="${sectionId}"]`);
            const content = document.querySelector(`.section-items-content[data-section-id="${sectionId}"]`);
            if (!content) return;

            const isExpanded = !content.classList.contains('hidden');

            if (isExpanded) {
                // Collapse - show summary, hide content
                if (summary) summary.classList.remove('hidden');
                content.classList.add('hidden');
                toggle.setAttribute('aria-expanded', 'false');
                const icon = toggle.querySelector('.section-toggle-icon');
                if (icon) icon.style.transform = 'rotate(-90deg)'; // Collapsed: -90deg (right)

                // Remove from expanded list
                const expanded = JSON.parse(sessionStorage.getItem(newStorageKey) || '[]');
                const filtered = expanded.filter(id => id !== sectionId);
                sessionStorage.setItem(newStorageKey, JSON.stringify(filtered));
            } else {
                // Expand - hide summary, show content
                if (summary) summary.classList.add('hidden');
                content.classList.remove('hidden');
                toggle.setAttribute('aria-expanded', 'true');
                const icon = toggle.querySelector('.section-toggle-icon');
                if (icon) icon.style.transform = 'rotate(0deg)'; // Expanded: 0deg (down)

                // Add to expanded list
                const expanded = JSON.parse(sessionStorage.getItem(newStorageKey) || '[]');
                if (!expanded.includes(sectionId)) {
                    expanded.push(sectionId);
                    sessionStorage.setItem(newStorageKey, JSON.stringify(expanded));
                }
            }
        });

        // Make summary clickable to expand
        document.querySelectorAll('.section-items-summary').forEach(summary => {
            summary.addEventListener('click', function() {
                const sectionId = this.dataset.sectionId;
                const section = this.closest('.checklist-section');
                if (!section) return;

                const toggleBtn = section.querySelector(`.section-items-toggle[data-section-id="${sectionId}"]`);
                if (toggleBtn) {
                    toggleBtn.click();
                }
            });
            summary.style.cursor = 'pointer';
        });
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
                const sectionId = section?.dataset.sectionId;

                // Capture item data before deletion for undo
                const itemType = item.dataset.itemType || 'item';
                const itemText = item.querySelector('.item-text-input, .item-text')?.value || item.querySelector('.item-text-input, .item-text')?.textContent || '';

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

    // Helper function to bind event handlers to dynamically added image items
    function bindItemEventHandlers(itemElement) {
        // Bind save button
        const saveBtn = itemElement.querySelector('.item-save-btn');
        if (saveBtn) {
            saveBtn.addEventListener('click', async function() {
                const item = this.closest('.checklist-image-item');
                if (!item) return;

                const itemId = item.dataset.itemId;
                const text = item.querySelector('.item-text-input').value.trim();

                try {
                    await apiRequest(`${API_BASE}/checklists/items/${itemId}/`, 'PATCH', {
                        text: text
                    });
                    PlatPursuit.ToastManager.show('Caption saved!', 'success');
                    clearFormState();
                    markFormAsClean();
                } catch (error) {
                    PlatPursuit.ToastManager.show(error.message || 'Failed to save caption', 'error');
                }
            });
        }

        // Bind delete button
        const deleteBtn = itemElement.querySelector('.item-delete-btn');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', async function() {
                const item = this.closest('.checklist-image-item');
                if (!item) return;

                const itemId = item.dataset.itemId;
                const section = this.closest('.checklist-section');

                try {
                    await apiRequest(`${API_BASE}/checklists/items/${itemId}/`, 'DELETE');
                    item.remove();

                    // Update item count
                    const countBadge = section.querySelector('.section-item-count');
                    if (countBadge) {
                        const items = section.querySelectorAll('[data-item-id]');
                        countBadge.textContent = `${items.length} item${items.length !== 1 ? 's' : ''}`;
                    }

                    PlatPursuit.ToastManager.show('Image deleted.', 'success');
                    clearFormState();
                    markFormAsClean();
                } catch (error) {
                    PlatPursuit.ToastManager.show(error.message || 'Failed to delete image', 'error');
                }
            });
        }

        // Bind move up button
        const moveUpBtn = itemElement.querySelector('.item-move-up-btn');
        if (moveUpBtn) {
            moveUpBtn.addEventListener('click', function() {
                const item = this.closest('.checklist-image-item');
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
        }

        // Bind move down button
        const moveDownBtn = itemElement.querySelector('.item-move-down-btn');
        if (moveDownBtn) {
            moveDownBtn.addEventListener('click', function() {
                const item = this.closest('.checklist-image-item');
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
        }

        // Bind character counter for caption input
        const textInput = itemElement.querySelector('.item-text-input');
        const charCount = itemElement.querySelector('.item-char-count');
        if (textInput && charCount) {
            textInput.addEventListener('input', function() {
                charCount.textContent = this.value.length;
            });
        }
    }

    // Helper function to create inline image DOM element
    function createInlineImageElement(itemData) {
        const div = document.createElement('div');
        div.className = 'checklist-image-item my-4 p-3 bg-secondary/5 border-2 border-secondary/20 rounded-lg';
        div.setAttribute('data-item-id', itemData.id);
        div.setAttribute('data-item-order', itemData.order);
        div.setAttribute('data-item-type', 'image');

        const caption = itemData.text || '';
        const imageUrl = itemData.image_url || itemData.image?.url || '';

        div.innerHTML = `
            <div class="flex items-start gap-3">
                <div class="flex flex-col gap-1">
                    <button class="btn btn-ghost btn-xs item-move-up-btn" title="Move up">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7"/>
                        </svg>
                    </button>
                    <button class="btn btn-ghost btn-xs item-move-down-btn" title="Move down">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
                        </svg>
                    </button>
                </div>
                <div class="flex-1">
                    <div class="flex items-center gap-2 mb-2">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-secondary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
                        </svg>
                        <span class="badge badge-secondary badge-sm">Inline Image</span>
                    </div>
                    <figure class="w-full max-w-xl mx-auto rounded-lg overflow-hidden shadow-md bg-base-300 mb-2">
                        <img src="${imageUrl}" alt="Guide image" class="w-full h-auto object-contain" loading="lazy">
                    </figure>
                    <input type="text" class="input input-sm input-bordered w-full item-text-input text-sm italic"
                           maxlength="500" placeholder="Optional caption...">
                    <span class="text-xs text-base-content/50 text-right mt-1">
                        <span class="item-char-count">0</span>/500
                    </span>
                </div>
                <div class="flex flex-col gap-1">
                    <button class="btn btn-ghost btn-xs item-save-btn" title="Save caption">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-success" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
                        </svg>
                    </button>
                    <button class="btn btn-ghost btn-xs item-delete-btn" title="Delete image">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-error" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                        </svg>
                    </button>
                </div>
            </div>
        `;

        // Set the caption value safely using DOM property (not HTML attribute)
        // This avoids any HTML escaping issues with quotes or special characters
        const captionInput = div.querySelector('.item-text-input');
        const charCount = div.querySelector('.item-char-count');
        if (captionInput && caption) {
            captionInput.value = caption;
            if (charCount) {
                charCount.textContent = caption.length;
            }
        }

        return div;
    }

    function addItemToDOM(section, item) {
        const template = document.getElementById('item-template');
        const container = section.querySelector('.section-items-container');
        if (!template || !container) return;

        // Auto-expand section if collapsed
        const sectionId = section.dataset.sectionId;
        if (sectionId) {
            expandSectionItems(sectionId);
        }

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
                if (file && validateImageFile(file, 5)) {
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
        // Get the file input element for disabling
        const fileInput = document.getElementById('checklist-thumbnail-input');
        const previewContainer = document.querySelector('.thumbnail-upload-area');

        // Disable input and show loading state
        if (fileInput) fileInput.disabled = true;
        let loadingIndicator = null;
        if (previewContainer) {
            loadingIndicator = document.createElement('div');
            loadingIndicator.className = 'loading loading-spinner loading-sm';
            loadingIndicator.style.position = 'absolute';
            loadingIndicator.style.top = '50%';
            loadingIndicator.style.left = '50%';
            loadingIndicator.style.transform = 'translate(-50%, -50%)';
            previewContainer.style.position = 'relative';
            previewContainer.appendChild(loadingIndicator);
        }

        const formData = new FormData();
        formData.append('thumbnail', file);

        try {
            const data = await PlatPursuit.API.postFormData(`/api/v1/checklists/${checklistId}/image/`, formData);

            PlatPursuit.ToastManager.show('Thumbnail uploaded!', 'success');

            // Update preview with server URL
            const preview = document.getElementById('checklist-thumbnail-preview');
            if (preview) {
                const img = preview.querySelector('img');
                if (img && data.thumbnail_url) {
                    img.src = data.thumbnail_url;
                }
                preview.classList.remove('hidden');
            }

            // Show remove button
            const removeBtn = document.getElementById('remove-checklist-thumbnail-btn');
            if (removeBtn) {
                removeBtn.classList.remove('hidden');
            }

            // Clear the file input
            if (fileInput) fileInput.value = '';
        } catch (error) {
            let msg = 'Upload failed.';
            try { const errData = await error.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.show(msg, 'error');

            // Restore state on error
            if (fileInput) {
                fileInput.disabled = false;
                fileInput.value = ''; // Clear the file input
            }
        } finally {
            // Remove loading indicator
            if (loadingIndicator) {
                loadingIndicator.remove();
            }
            if (fileInput) fileInput.disabled = false;
        }
    }

    async function removeChecklistThumbnail(checklistId) {
        if (!confirm('Remove guide thumbnail?')) return;

        try {
            await PlatPursuit.API.delete(`/api/v1/checklists/${checklistId}/image/`);

            PlatPursuit.ToastManager.show('Thumbnail removed.', 'success');

            // Hide preview
            const preview = document.getElementById('checklist-thumbnail-preview');
            if (preview) {
                preview.classList.add('hidden');
                const img = preview.querySelector('img');
                if (img) img.src = '';
            }

            // Hide remove button
            const removeBtn = document.getElementById('remove-checklist-thumbnail-btn');
            if (removeBtn) {
                removeBtn.classList.add('hidden');
            }
        } catch (error) {
            let msg = 'Failed to remove.';
            try { const errData = await error.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.show(msg, 'error');
        }
    }

    async function uploadSectionThumbnail(sectionId, file) {
        const section = document.querySelector(`.checklist-section[data-section-id="${sectionId}"]`);
        if (!section) return;

        // Get the file input for this specific section
        const fileInput = section.querySelector('.section-thumbnail-input');
        const previewContainer = section.querySelector('.section-thumbnail-container');

        // Disable input and show loading state
        if (fileInput) fileInput.disabled = true;
        let loadingIndicator = null;
        if (previewContainer) {
            loadingIndicator = document.createElement('div');
            loadingIndicator.className = 'loading loading-spinner loading-sm';
            loadingIndicator.style.position = 'absolute';
            loadingIndicator.style.top = '50%';
            loadingIndicator.style.left = '50%';
            loadingIndicator.style.transform = 'translate(-50%, -50%)';
            previewContainer.style.position = 'relative';
            previewContainer.appendChild(loadingIndicator);
        }

        const formData = new FormData();
        formData.append('thumbnail', file);

        try {
            const data = await PlatPursuit.API.postFormData(`/api/v1/checklists/sections/${sectionId}/image/`, formData);

            PlatPursuit.ToastManager.show('Section thumbnail uploaded!', 'success');

            // Find or create preview element
            let preview = section.querySelector('.section-thumbnail-preview');
            if (preview) {
                // Update existing preview
                const img = preview.querySelector('img');
                if (img && data.thumbnail_url) {
                    img.src = data.thumbnail_url;
                }
                preview.classList.remove('hidden');
            } else {
                // Create preview element
                const figure = document.createElement('figure');
                figure.className = 'section-thumbnail-preview w-full max-w-2xl mx-auto rounded-lg overflow-hidden shadow-md bg-base-300 mb-3';
                const img = document.createElement('img');
                img.src = data.thumbnail_url;
                img.alt = 'Section header image';
                img.className = 'w-full h-auto object-contain';
                img.loading = 'lazy';
                figure.appendChild(img);

                // Insert before the file input container
                const fileInputContainer = section.querySelector('.section-thumbnail-input').closest('.flex');
                fileInputContainer.parentElement.insertBefore(figure, fileInputContainer);
            }

            // Show remove button
            let removeBtn = section.querySelector('.section-remove-thumbnail-btn');
            if (removeBtn) {
                removeBtn.classList.remove('hidden');
            }

            // Clear the file input
            if (fileInput) fileInput.value = '';
        } catch (error) {
            let msg = 'Upload failed.';
            try { const errData = await error.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.show(msg, 'error');

            // Restore state on error
            if (fileInput) {
                fileInput.disabled = false;
                fileInput.value = ''; // Clear the file input
            }
        } finally {
            // Remove loading indicator
            if (loadingIndicator) {
                loadingIndicator.remove();
            }
            if (fileInput) fileInput.disabled = false;
        }
    }

    async function removeSectionThumbnail(sectionId) {
        if (!confirm('Remove section thumbnail?')) return;

        try {
            await PlatPursuit.API.delete(`/api/v1/checklists/sections/${sectionId}/image/`);

            PlatPursuit.ToastManager.show('Section thumbnail removed.', 'success');

            // Find the section
            const section = document.querySelector(`.checklist-section[data-section-id="${sectionId}"]`);
            if (!section) return;

            // Hide preview
            const preview = section.querySelector('.section-thumbnail-preview');
            if (preview) {
                preview.classList.add('hidden');
                const img = preview.querySelector('img');
                if (img) img.src = '';
            }

            // Hide remove button
            const removeBtn = section.querySelector('.section-remove-thumbnail-btn');
            if (removeBtn) {
                removeBtn.classList.add('hidden');
            }
        } catch (error) {
            let msg = 'Failed to remove.';
            try { const errData = await error.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.show(msg, 'error');
        }
    }

    async function uploadInlineImage(sectionId, file, caption) {
        const section = document.querySelector(`.checklist-section[data-section-id="${sectionId}"]`);
        if (!section) return;

        // Get the upload button and disable it with loading state
        const uploadBtn = section.querySelector('.add-inline-image-btn');
        const fileInput = section.querySelector('.inline-image-input');
        const captionInput = section.querySelector('.inline-image-caption');

        // Disable controls and show loading state
        let originalBtnHTML = '';
        if (uploadBtn) {
            uploadBtn.disabled = true;
            originalBtnHTML = uploadBtn.innerHTML;
            uploadBtn.innerHTML = '<span class="loading loading-spinner loading-xs"></span> Uploading...';
        }
        if (fileInput) fileInput.disabled = true;
        if (captionInput) captionInput.disabled = true;

        const formData = new FormData();
        formData.append('image', file);
        if (caption) formData.append('text', caption);

        try {
            const data = await PlatPursuit.API.postFormData(`/api/v1/checklists/sections/${sectionId}/items/image/`, formData);

            PlatPursuit.ToastManager.show('Inline image added!', 'success');

            // Create the new item DOM element
            const newItemElement = createInlineImageElement(data);

            // Bind event handlers to the new element
            bindItemEventHandlers(newItemElement);

            // Find items container and insert the new item
            const itemsContainer = section.querySelector('.section-items-container');
            if (itemsContainer) {
                itemsContainer.appendChild(newItemElement);
            }

            // Update item count badge
            const countBadge = section.querySelector('.section-item-count');
            if (countBadge) {
                const items = section.querySelectorAll('[data-item-id]');
                countBadge.textContent = `${items.length} item${items.length !== 1 ? 's' : ''}`;
            }

            // Expand the section if it's collapsed
            const itemsContent = section.querySelector('.section-items-content');
            if (itemsContent && itemsContent.classList.contains('hidden')) {
                itemsContent.classList.remove('hidden');
                // Update toggle icon if needed
                const toggleIcon = section.querySelector('.section-toggle-icon');
                if (toggleIcon) {
                    toggleIcon.style.transform = 'rotate(180deg)';
                }
            }

            // Clear the upload form
            if (fileInput) fileInput.value = '';
            if (captionInput) captionInput.value = '';

            // Scroll the new item into view smoothly
            setTimeout(() => {
                newItemElement.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }, 100);
        } catch (error) {
            let msg = 'Upload failed.';
            try { const errData = await error.response?.json(); msg = errData?.error || msg; } catch {}
            PlatPursuit.ToastManager.show(msg, 'error');

            // Restore controls on error
            if (uploadBtn) {
                uploadBtn.disabled = false;
                uploadBtn.innerHTML = originalBtnHTML;
            }
            if (fileInput) {
                fileInput.disabled = false;
                fileInput.value = ''; // Clear the file input
            }
            if (captionInput) captionInput.disabled = false;
        } finally {
            // Always restore controls on success (error already handled above)
            if (uploadBtn) {
                uploadBtn.disabled = false;
                uploadBtn.innerHTML = originalBtnHTML || 'Add Image';
            }
            if (fileInput) fileInput.disabled = false;
            if (captionInput) captionInput.disabled = false;
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
                    await PlatPursuit.API.post(`${API_BASE}/checklists/sections/${sectionId}/items/`, {
                        text: text,
                        item_type: 'text_area'
                    });

                    PlatPursuit.ToastManager.show('Text area added!', 'success');
                    textarea.value = '';  // Clear input
                    // Update character counter
                    const counter = document.querySelector(`.text-area-char-count[data-section-id="${sectionId}"]`);
                    if (counter) counter.textContent = '0/2000';

                    // Reload page to show new item
                    setTimeout(() => reloadWithFormState(), 500);
                } catch (error) {
                    let msg = 'Failed to add text area.';
                    try { const errData = await error.response?.json(); msg = errData?.error || msg; } catch {}
                    PlatPursuit.ToastManager.show(msg, 'error');
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
                        const data = await PlatPursuit.API.post(`${API_BASE}/markdown/preview/`, { text });
                        previewArea.querySelector('.prose').innerHTML = data.html;
                        previewArea.classList.remove('hidden');
                        btn.textContent = 'Hide Preview';
                    } catch (error) {
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
            const data = await PlatPursuit.API.post(`${API_BASE}/markdown/preview/`, { text });
            modal.querySelector('.preview-content').innerHTML = data.html;
        } catch (error) {
            modal.querySelector('.preview-content').innerHTML = '<p class="text-error">Preview failed.</p>';
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

        // Reset batch selection state
        batchSelectionState.clear();
        updateBatchSelectionUI();

        // Reset batch mode toggle (user can re-enable if desired)
        const batchModeToggle = document.getElementById('batch-selection-mode');
        if (batchModeToggle) {
            batchModeToggle.checked = false;
            batchSelectionState.toggle(false);
            const batchControls = document.getElementById('batch-selection-controls');
            const addBtn = document.getElementById('add-selected-trophies-btn');
            if (batchControls) {
                batchControls.style.display = 'none';
            }
            if (addBtn) {
                addBtn.style.display = 'none';
            }
        }

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

            // Apply filters to newly loaded trophies (important for when modal reopens)
            filterTrophies();
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
            const isSelected = batchSelectionState.has(trophy.id);

            // Add DLC badge if not base game
            const dlcBadge = !trophy.is_base_game && trophy.trophy_group_name
                ? `<span class="badge badge-sm badge-info">DLC: ${escapeHtml(trophy.trophy_group_name)}</span>`
                : (!trophy.is_base_game ? '<span class="badge badge-sm badge-info">DLC</span>' : '');

            // Checkbox HTML (only in batch mode and not used)
            const checkboxHtml = batchSelectionState.enabled && !trophy.is_used
                ? `<input type="checkbox"
                          class="checkbox checkbox-primary trophy-batch-checkbox"
                          data-trophy-id="${trophy.id}"
                          aria-label="Select ${escapeHtml(trophy.trophy_name)}"
                          onclick="toggleTrophySelection(${trophy.id}, event);"
                          ${isSelected ? 'checked' : ''}>`
                : '';

            // Click handler: batch mode uses checkbox toggle, single mode uses direct select
            const clickHandler = trophy.is_used
                ? ''
                : (batchSelectionState.enabled
                    ? `onclick="toggleTrophySelection(${trophy.id}, event)"`
                    : `onclick="selectTrophy(${trophy.id})"`);

            return `
                <div class="trophy-select-card flex items-center gap-3 p-3 bg-base-200 rounded-lg hover:bg-base-300 transition-colors cursor-pointer ${isDisabled}"
                     data-trophy-id="${trophy.id}"
                     data-trophy-name="${escapeHtml(trophy.trophy_name)}"
                     data-trophy-type="${trophy.trophy_type}"
                     data-trophy-group="${trophy.trophy_group_id}"
                     data-is-used="${trophy.is_used ? 'true' : 'false'}"
                     ${clickHandler}>
                    ${checkboxHtml}
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
     * Toggle trophy selection in batch mode.
     * Exposed globally for onclick handler.
     */
    window.toggleTrophySelection = function(trophyId, event) {
        if (!batchSelectionState.enabled) return;

        const checkbox = document.querySelector(`.trophy-batch-checkbox[data-trophy-id="${trophyId}"]`);
        if (!checkbox) return;

        // If the checkbox itself was clicked, don't toggle it again (browser already did)
        // Otherwise toggle it programmatically
        const isCheckboxClick = event && event.target === checkbox;

        if (!isCheckboxClick) {
            checkbox.checked = !checkbox.checked;
        }

        if (checkbox.checked) {
            batchSelectionState.add(trophyId);
        } else {
            batchSelectionState.remove(trophyId);
        }

        updateBatchSelectionUI();
    };

    /**
     * Update batch selection UI (count, button states).
     */
    function updateBatchSelectionUI() {
        const count = batchSelectionState.count();
        const countBadge = document.getElementById('batch-selection-count');
        const addBtn = document.getElementById('add-selected-trophies-btn');
        const addCountSpan = document.getElementById('add-selected-count');

        if (countBadge) {
            countBadge.textContent = `${count} selected`;
        }

        if (addBtn && addCountSpan) {
            addCountSpan.textContent = count;
            addBtn.disabled = count === 0;
        }
    }

    /**
     * Select all visible (non-used) trophies.
     */
    function selectAllTrophies() {
        const checkboxes = document.querySelectorAll('.trophy-batch-checkbox');
        checkboxes.forEach(checkbox => {
            const trophyId = parseInt(checkbox.dataset.trophyId);
            checkbox.checked = true;
            batchSelectionState.add(trophyId);
        });
        updateBatchSelectionUI();
    }

    /**
     * Deselect all trophies.
     */
    function deselectAllTrophies() {
        const checkboxes = document.querySelectorAll('.trophy-batch-checkbox');
        checkboxes.forEach(checkbox => {
            checkbox.checked = false;
        });
        batchSelectionState.clear();
        updateBatchSelectionUI();
    }

    /**
     * Select a trophy and add to section.
     * Exposed globally for onclick handler.
     */
    window.selectTrophy = async function(trophyId) {
        const modal = document.getElementById('trophy-selector-modal');
        const sectionId = modal.dataset.sectionId;

        try {
            const result = await apiRequest(
                `/api/v1/checklists/sections/${sectionId}/items/`,
                'POST',
                {
                    item_type: 'trophy',
                    trophy_id: trophyId
                }
            );

            PlatPursuit.ToastManager.show('Trophy added successfully', 'success');
            modal.close();

            // Get trophy data from modal cache
            const trophyData = getTrophyFromModalCache(trophyId);

            // Add trophy to DOM without page reload
            const section = document.querySelector(`.checklist-section[data-section-id="${sectionId}"]`);
            if (section && result.item) {
                addTrophyItemToDOM(section, result.item, trophyData);
            }

            // Mark trophy as used in modal
            markTrophyAsUsed(trophyId);
        } catch (error) {
            PlatPursuit.ToastManager.show(error.message, 'error');
        }
    }

    /**
     * Add all selected trophies to the current section.
     */
    async function addSelectedTrophies() {
        const modal = document.getElementById('trophy-selector-modal');
        const sectionId = modal.dataset.sectionId;
        const selectedIds = batchSelectionState.getAll();

        if (selectedIds.length === 0) {
            PlatPursuit.ToastManager.show('No trophies selected', 'warning');
            return;
        }

        // Get trophy data from cache and sort by trophy_id (game order)
        const trophies = JSON.parse(modal.dataset.trophies || '[]');
        const selectedTrophies = selectedIds
            .map(id => trophies.find(t => t.id === id))
            .filter(t => t) // Remove nulls
            .sort((a, b) => a.trophy_id - b.trophy_id); // Sort by game's trophy_id

        // Disable button during operation
        const addBtn = document.getElementById('add-selected-trophies-btn');
        if (addBtn) {
            addBtn.disabled = true;
            addBtn.innerHTML = '<span class="loading loading-spinner loading-sm"></span> Adding...';
        }

        let successCount = 0;
        let failCount = 0;
        const errors = [];

        // Add trophies sequentially
        for (const trophy of selectedTrophies) {
            try {
                const result = await apiRequest(
                    `/api/v1/checklists/sections/${sectionId}/items/`,
                    'POST',
                    {
                        item_type: 'trophy',
                        trophy_id: trophy.id
                    }
                );

                // Add trophy to DOM using existing function
                const section = document.querySelector(`.checklist-section[data-section-id="${sectionId}"]`);
                if (section && result.item) {
                    addTrophyItemToDOM(section, result.item, trophy);
                }

                // Mark as used in modal cache
                markTrophyAsUsed(trophy.id);

                // Remove from selection
                batchSelectionState.remove(trophy.id);

                successCount++;

            } catch (error) {
                failCount++;
                let errorMsg = 'Unknown error';

                // Extract error message from response
                if (error.responseData?.error) {
                    errorMsg = error.responseData.error;
                } else if (error.message) {
                    errorMsg = error.message;
                }

                errors.push({
                    trophyName: trophy.trophy_name,
                    error: errorMsg
                });
            }
        }

        // Re-render trophy list to update checkboxes and "Already Added" badges
        const listContainer = document.getElementById('trophy-list-container');
        if (listContainer && trophies.length > 0) {
            renderTrophyList(trophies, listContainer);
            filterTrophies(); // Re-apply active filters
        }

        // Show results
        if (successCount > 0 && failCount === 0) {
            PlatPursuit.ToastManager.show(
                `Successfully added ${successCount} ${successCount === 1 ? 'trophy' : 'trophies'}`,
                'success'
            );
            modal.close();
            batchSelectionState.clear();
        } else if (successCount > 0 && failCount > 0) {
            PlatPursuit.ToastManager.show(
                `Added ${successCount} trophies, ${failCount} failed. Check console for details.`,
                'warning',
                8000
            );
            console.warn('Failed trophy additions:', errors);
            // Don't close modal - let user see what failed
        } else {
            PlatPursuit.ToastManager.show(
                `Failed to add trophies. ${errors[0]?.error || 'Unknown error'}`,
                'error',
                8000
            );
            console.error('All trophy additions failed:', errors);
        }

        // Reset button
        if (addBtn) {
            addBtn.disabled = batchSelectionState.count() === 0;
            addBtn.innerHTML = `Add Selected (<span id="add-selected-count">${batchSelectionState.count()}</span>)`;
        }

        updateBatchSelectionUI();
    }

    /**
     * Get trophy data from modal's cached trophy list.
     */
    function getTrophyFromModalCache(trophyId) {
        const modal = document.getElementById('trophy-selector-modal');
        if (!modal || !modal.dataset.trophies) return null;

        try {
            const trophies = JSON.parse(modal.dataset.trophies);
            return trophies.find(t => t.id === trophyId) || null;
        } catch (e) {
            console.error('Failed to get trophy from cache:', e);
            return null;
        }
    }

    /**
     * Add trophy item to DOM without page reload.
     */
    function addTrophyItemToDOM(section, item, trophyData) {
        const container = section.querySelector('.section-items-container');
        if (!container) return;

        // Auto-expand section if collapsed
        const sectionId = section.dataset.sectionId;
        if (sectionId) {
            expandSectionItems(sectionId);
        }

        // Remove empty message if present
        const emptyMsg = container.querySelector('.empty-items-message');
        if (emptyMsg) emptyMsg.remove();

        // Get trophy details
        const trophy = trophyData || {};
        const trophyType = trophy.trophy_type || 'bronze';
        const trophyTypeTitle = trophyType.charAt(0).toUpperCase() + trophyType.slice(1);

        // Build DLC badge if applicable
        let dlcBadge = '';
        if (!trophy.is_base_game) {
            if (trophy.trophy_group_name) {
                dlcBadge = `<span class="badge badge-sm badge-info">DLC: ${escapeHtml(trophy.trophy_group_name)}</span>`;
            } else {
                dlcBadge = '<span class="badge badge-sm badge-info">DLC</span>';
            }
        }

        // Create trophy element
        const trophyEl = document.createElement('div');
        trophyEl.className = 'checklist-trophy-item-edit my-2 p-3 bg-warning/5 border-2 border-warning/20 rounded-lg';
        trophyEl.dataset.itemId = item.id;
        trophyEl.dataset.itemOrder = item.order;
        trophyEl.dataset.itemType = 'trophy';
        trophyEl.dataset.trophyId = item.trophy_id;

        trophyEl.innerHTML = `
            <div class="flex items-start gap-3">
                <div class="flex flex-col gap-1">
                    <button class="btn btn-ghost btn-xs item-move-up-btn" title="Move up">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7"/>
                        </svg>
                    </button>
                    <button class="btn btn-ghost btn-xs item-move-down-btn" title="Move down">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
                        </svg>
                    </button>
                </div>
                <div class="flex-1 flex items-center gap-3">
                    <img src="${trophy.trophy_icon_url || ''}" class="w-10 h-10 rounded shrink-0" alt="">
                    <div class="flex-1">
                        <div class="flex items-center gap-2 mb-1 flex-wrap">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-warning" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z"/>
                            </svg>
                            <span class="badge badge-warning badge-sm">Trophy</span>
                            <span class="badge badge-${trophyType} badge-sm">${trophyTypeTitle}</span>
                            ${dlcBadge}
                        </div>
                        <p class="font-semibold">${escapeHtml(item.text || trophy.trophy_name || '')}</p>
                        ${trophy.trophy_detail ? `<p class="text-xs text-base-content/60 line-clamp-2 mt-1">${escapeHtml(trophy.trophy_detail)}</p>` : ''}
                    </div>
                </div>
                <button class="btn btn-ghost btn-xs item-delete-btn text-error" title="Remove trophy">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                    </svg>
                </button>
            </div>
        `;

        container.appendChild(trophyEl);

        // Update item count
        const countBadge = section.querySelector('.section-item-count');
        if (countBadge) {
            const items = container.querySelectorAll('[data-item-id]');
            countBadge.textContent = items.length + ' items';
        }

        // Re-init event listeners for the new item
        const checklistId = document.getElementById('checklist-edit-container')?.dataset.checklistId;
        if (checklistId) {
            initItemOperations(checklistId);
        }
    }

    /**
     * Mark a trophy as used in the modal's cached data.
     */
    function markTrophyAsUsed(trophyId) {
        const modal = document.getElementById('trophy-selector-modal');
        if (!modal || !modal.dataset.trophies) return;

        try {
            const trophies = JSON.parse(modal.dataset.trophies);
            const trophy = trophies.find(t => t.id === trophyId);
            if (trophy) {
                trophy.is_used = true;
                modal.dataset.trophies = JSON.stringify(trophies);

                // Update visual state of the trophy card
                const card = document.querySelector(`.trophy-select-card[data-trophy-id="${trophyId}"]`);
                if (card) {
                    card.classList.add('opacity-50', 'pointer-events-none');
                    card.dataset.isUsed = 'true';
                    card.removeAttribute('onclick');

                    // Add "Already Added" badge if not present
                    const badgeContainer = card.querySelector('.flex-wrap');
                    if (badgeContainer && !badgeContainer.querySelector('.badge-ghost')) {
                        const badge = document.createElement('span');
                        badge.className = 'badge badge-ghost badge-xs';
                        badge.textContent = 'Already Added';
                        badgeContainer.appendChild(badge);
                    }
                }
            }
        } catch (e) {
            console.error('Failed to mark trophy as used:', e);
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
     * Filter trophies by search, type, group, and used status.
     */
    function filterTrophies() {
        const searchInput = document.getElementById('trophy-search-input');
        const typeFilter = document.getElementById('trophy-type-filter');
        const groupFilter = document.getElementById('trophy-group-filter');
        const hideUsedFilter = document.getElementById('trophy-hide-used-filter');

        const searchTerm = searchInput.value.toLowerCase();
        const selectedType = typeFilter.value.toLowerCase();
        const selectedGroup = groupFilter.value;
        const hideUsed = hideUsedFilter?.checked || false;

        const cards = document.querySelectorAll('.trophy-select-card');

        cards.forEach(card => {
            const name = card.dataset.trophyName.toLowerCase();
            const type = card.dataset.trophyType.toLowerCase();
            const group = card.dataset.trophyGroup;
            const isUsed = card.dataset.isUsed === 'true';

            const matchesSearch = name.includes(searchTerm);
            const matchesType = !selectedType || type === selectedType;
            const matchesGroup = !selectedGroup || group === selectedGroup;
            const matchesUsed = !hideUsed || !isUsed;

            card.style.display = (matchesSearch && matchesType && matchesGroup && matchesUsed) ? 'flex' : 'none';
        });
    }

    /**
     * Initialize trophy selection handlers.
     */
    function initTrophySelection() {
        // Open modal buttons (using event delegation for dynamically created buttons)
        document.addEventListener('click', function(e) {
            const btn = e.target.closest('.open-trophy-selector-btn');
            if (btn) {
                const sectionId = btn.dataset.sectionId;
                const checklistId = btn.dataset.checklistId;
                openTrophySelector(sectionId, checklistId);
            }
        });

        // Search and filter
        const searchInput = document.getElementById('trophy-search-input');
        const typeFilter = document.getElementById('trophy-type-filter');
        const groupFilter = document.getElementById('trophy-group-filter');
        const hideUsedFilter = document.getElementById('trophy-hide-used-filter');

        if (searchInput) {
            searchInput.addEventListener('input', filterTrophies);
        }

        if (typeFilter) {
            typeFilter.addEventListener('change', filterTrophies);
        }

        if (groupFilter) {
            groupFilter.addEventListener('change', filterTrophies);
        }

        if (hideUsedFilter) {
            hideUsedFilter.addEventListener('change', filterTrophies);
        }

        // Batch selection mode toggle
        const batchModeToggle = document.getElementById('batch-selection-mode');
        if (batchModeToggle) {
            batchModeToggle.addEventListener('change', function() {
                const enabled = this.checked;
                batchSelectionState.toggle(enabled);

                // Show/hide batch controls
                const batchControls = document.getElementById('batch-selection-controls');
                const addBtn = document.getElementById('add-selected-trophies-btn');

                if (batchControls) {
                    batchControls.style.display = enabled ? 'flex' : 'none';
                }

                if (addBtn) {
                    addBtn.style.display = enabled ? 'inline-flex' : 'none';
                }

                // Re-render trophy list with/without checkboxes
                const modal = document.getElementById('trophy-selector-modal');
                const listContainer = document.getElementById('trophy-list-container');
                if (modal?.dataset.trophies && listContainer) {
                    const trophies = JSON.parse(modal.dataset.trophies);
                    renderTrophyList(trophies, listContainer);
                    filterTrophies();
                }

                updateBatchSelectionUI();
            });
        }

        // Select All button
        const selectAllBtn = document.getElementById('select-all-trophies-btn');
        if (selectAllBtn) {
            selectAllBtn.addEventListener('click', selectAllTrophies);
        }

        // Deselect All button
        const deselectAllBtn = document.getElementById('deselect-all-trophies-btn');
        if (deselectAllBtn) {
            deselectAllBtn.addEventListener('click', deselectAllTrophies);
        }

        // Add Selected button
        const addSelectedBtn = document.getElementById('add-selected-trophies-btn');
        if (addSelectedBtn) {
            addSelectedBtn.addEventListener('click', addSelectedTrophies);
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
    // Section Items Toggle (Edit Page)
    // ==========================================


    /**
     * Expand a section's items view (used when adding items).
     */
    function expandSectionItems(sectionId) {
        const section = document.querySelector(`.checklist-section[data-section-id="${sectionId}"]`);
        if (!section) return;

        const toggleBtn = section.querySelector(`.section-items-toggle[data-section-id="${sectionId}"]`);
        if (toggleBtn && toggleBtn.getAttribute('aria-expanded') !== 'true') {
            toggleBtn.click();
        }
    }

    /**
     * Collapse all sections (for edit page).
     */
    function collapseAllSections() {
        const toggles = document.querySelectorAll('.section-items-toggle[aria-expanded="true"]');
        toggles.forEach(toggle => {
            toggle.click();
        });
    }

    /**
     * Expand all sections (for edit page).
     */
    function expandAllSections() {
        const toggles = document.querySelectorAll('.section-items-toggle[aria-expanded="false"]');
        toggles.forEach(toggle => {
            toggle.click();
        });
    }


    // ==========================================
    // Real-Time Form Validation
    // ==========================================

    /**
     * Validate a field and show/hide error message.
     * @param {string} fieldType - Type of field (title, section-title, etc.)
     * @param {string} value - Current field value
     * @param {string} context - Optional context (e.g., section ID)
     * @returns {boolean} - True if valid
     */
    function validateField(fieldType, value, context = null) {
        let isValid = true;
        let errorMessage = '';

        switch (fieldType) {
            case 'checklist-title':
                if (!value.trim()) {
                    isValid = false;
                    errorMessage = 'Title is required';
                } else if (value.length > 200) {
                    isValid = false;
                    errorMessage = 'Title must be 200 characters or less';
                }
                break;

            case 'section-title':
                if (!value.trim()) {
                    isValid = false;
                    errorMessage = 'Section title is required';
                } else if (value.length > 200) {
                    isValid = false;
                    errorMessage = 'Section title must be 200 characters or less';
                }
                break;

            case 'item-text':
                if (!value.trim()) {
                    isValid = false;
                    errorMessage = 'Item text is required';
                } else if (value.length > 500) {
                    isValid = false;
                    errorMessage = 'Item text must be 500 characters or less';
                }
                break;
        }

        // Find or create error element
        const fieldId = context ? `${fieldType}-${context}` : fieldType;
        let errorElement = document.getElementById(`${fieldId}-error`);

        if (!errorElement) {
            // Create error element if it doesn't exist
            const inputElement = document.getElementById(fieldId);
            if (inputElement && inputElement.parentElement) {
                errorElement = document.createElement('span');
                errorElement.id = `${fieldId}-error`;
                errorElement.className = 'text-error text-xs mt-1 hidden';
                inputElement.parentElement.appendChild(errorElement);
            }
        }

        if (errorElement) {
            if (isValid) {
                errorElement.classList.add('hidden');
            } else {
                errorElement.textContent = errorMessage;
                errorElement.classList.remove('hidden');
            }
        }

        return isValid;
    }

    /**
     * Setup real-time validation for form fields.
     */
    function initFormValidation() {
        // Checklist title validation
        const titleInput = document.getElementById('checklist-title');
        if (titleInput) {
            titleInput.addEventListener('blur', () => {
                validateField('checklist-title', titleInput.value);
            });
        }

        // Section title validation
        document.querySelectorAll('.section-title-input').forEach(input => {
            const section = input.closest('.checklist-section');
            const sectionId = section?.dataset.sectionId;

            if (sectionId) {
                input.addEventListener('blur', () => {
                    validateField('section-title', input.value, sectionId);
                });
            }
        });
    }

    /**
     * Update publishing requirements checklist in real-time.
     */
    function updatePublishingRequirements() {
        const titleInput = document.getElementById('checklist-title');
        const hasTitle = titleInput && titleInput.value.trim() !== '';

        const sections = document.querySelectorAll('.checklist-section');
        const hasSections = sections.length > 0;

        let allSectionsHaveItems = true;
        if (hasSections) {
            sections.forEach(section => {
                const items = section.querySelectorAll('[data-item-id]');
                if (items.length === 0) {
                    allSectionsHaveItems = false;
                }
            });
        } else {
            allSectionsHaveItems = false;
        }

        // Update UI
        updateRequirementStatus('title', hasTitle);
        updateRequirementStatus('sections', hasSections);
        updateRequirementStatus('items', allSectionsHaveItems);

        // Enable/disable publish button
        const publishBtn = document.getElementById('publish-checklist-btn');
        if (publishBtn) {
            publishBtn.disabled = !(hasTitle && hasSections && allSectionsHaveItems);
        }
    }

    function updateRequirementStatus(requirement, met) {
        const icon = document.getElementById(`req-${requirement}-icon`);
        const text = document.getElementById(`req-${requirement}-text`);

        if (!icon || !text) return;

        if (met) {
            icon.textContent = '✓';
            icon.classList.remove('text-error');
            icon.classList.add('text-success');
            text.classList.add('line-through', 'opacity-50');
        } else {
            icon.textContent = '✗';
            icon.classList.remove('text-success');
            icon.classList.add('text-error');
            text.classList.remove('line-through', 'opacity-50');
        }
    }

    /**
     * Highlight missing requirements when "Show Me What's Missing" is clicked.
     */
    window.highlightMissingRequirements = function() {
        const titleInput = document.getElementById('checklist-title');
        const hasTitle = titleInput && titleInput.value.trim() !== '';

        if (!hasTitle) {
            titleInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
            titleInput.focus();
            titleInput.classList.add('flash-error');
            setTimeout(() => titleInput.classList.remove('flash-error'), 1000);
            return;
        }

        const sections = document.querySelectorAll('.checklist-section');
        if (sections.length === 0) {
            const addSectionBtn = document.getElementById('add-section-btn');
            if (addSectionBtn) {
                addSectionBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
                addSectionBtn.classList.add('flash-error');
                setTimeout(() => addSectionBtn.classList.remove('flash-error'), 1000);
            }
            return;
        }

        // Find first empty section
        for (const section of sections) {
            const items = section.querySelectorAll('[data-item-id]');
            if (items.length === 0) {
                section.scrollIntoView({ behavior: 'smooth', block: 'center' });
                section.classList.add('flash-error');
                setTimeout(() => section.classList.remove('flash-error'), 1000);

                // Expand the section if collapsed
                const toggleBtn = section.querySelector('.section-items-toggle');
                if (toggleBtn && toggleBtn.getAttribute('aria-expanded') === 'false') {
                    toggleBtn.click();
                }
                return;
            }
        }
    };

    // ==========================================
    // Character Counter with Color Coding
    // ==========================================

    /**
     * Update character counter with color coding based on usage percentage.
     * @param {string} counterId - ID of the counter element
     * @param {number} currentLength - Current text length
     * @param {number} maxLength - Maximum allowed length
     */
    function updateCharCounter(counterId, currentLength, maxLength) {
        const counter = document.getElementById(counterId);
        if (!counter) return;

        counter.textContent = `${currentLength} / ${maxLength}`;

        const percentage = (currentLength / maxLength) * 100;

        // Remove all color classes
        counter.classList.remove('char-counter-safe', 'char-counter-warning', 'char-counter-danger');

        // Add appropriate class based on percentage
        if (percentage >= 100) {
            counter.classList.add('char-counter-danger');
        } else if (percentage >= 90) {
            counter.classList.add('char-counter-warning');
        } else {
            counter.classList.add('char-counter-safe');
        }
    }

    /**
     * Initialize character counters for all inputs with maxlength.
     */
    function initCharacterCounters() {
        // Find all inputs/textareas with char counters
        document.querySelectorAll('input[maxlength], textarea[maxlength]').forEach(input => {
            const maxLength = parseInt(input.getAttribute('maxlength'));

            // Find associated counter (various patterns in use)
            let counter = null;
            const inputId = input.id;

            if (inputId) {
                // Look for counter with matching ID pattern
                counter = document.getElementById(`char-counter-${inputId.replace('item-text-', '').replace('item-markdown-', '').replace('image-caption-', '')}`);

                // Or look for counter in parent's label
                if (!counter) {
                    const label = input.closest('.form-control')?.querySelector('.label');
                    counter = label?.querySelector('[id^="char-counter-"]');
                }
            }

            if (counter && maxLength) {
                // Initialize counter
                updateCharCounter(counter.id, input.value.length, maxLength);

                // Update on input
                input.addEventListener('input', () => {
                    updateCharCounter(counter.id, input.value.length, maxLength);
                });
            }
        });

        // Also handle existing char-count elements (old pattern)
        document.querySelectorAll('.char-count-input').forEach(input => {
            const target = input.id;
            const counter = document.querySelector(`.char-count[data-target="${target}"]`);
            const maxLength = parseInt(input.getAttribute('maxlength'));

            if (counter && maxLength) {
                input.addEventListener('input', () => {
                    const currentLength = input.value.length;
                    counter.textContent = currentLength;

                    const percentage = (currentLength / maxLength) * 100;
                    counter.classList.remove('char-counter-safe', 'char-counter-warning', 'char-counter-danger');

                    if (percentage >= 100) {
                        counter.classList.add('char-counter-danger');
                    } else if (percentage >= 90) {
                        counter.classList.add('char-counter-warning');
                    } else {
                        counter.classList.add('char-counter-safe');
                    }
                });

                // Initial color
                const currentLength = input.value.length;
                const percentage = (currentLength / maxLength) * 100;

                if (percentage >= 100) {
                    counter.classList.add('char-counter-danger');
                } else if (percentage >= 90) {
                    counter.classList.add('char-counter-warning');
                } else {
                    counter.classList.add('char-counter-safe');
                }
            }
        });
    }

    // ==========================================
    // Unified Item Creator
    // ==========================================

    /**
     * Handle item type change in unified creator.
     * Dynamically renders appropriate input fields based on selected type.
     */
    function handleItemTypeChange(sectionId, itemType) {
        const inputArea = document.getElementById(`item-input-area-${sectionId}`);
        const addBtn = document.getElementById(`add-item-btn-${sectionId}`);

        if (!inputArea || !addBtn) return;

        // Clear previous inputs
        inputArea.innerHTML = '';

        switch (itemType) {
            case 'item':
            case 'sub_header':
                // Text input with character counter
                inputArea.innerHTML = `
                    <div class="form-control">
                        <input type="text"
                               id="item-text-${sectionId}"
                               class="input input-bordered w-full ${itemType === 'sub_header' ? 'font-semibold' : ''}"
                               placeholder="${itemType === 'sub_header' ? 'Sub-header text...' : 'Item text...'}"
                               maxlength="500"
                               data-section-id="${sectionId}">
                        <label class="label">
                            <span class="label-text-alt" id="char-counter-${sectionId}">0 / 500</span>
                        </label>
                    </div>
                `;

                // Setup character counter
                const textInput = document.getElementById(`item-text-${sectionId}`);
                if (textInput) {
                    textInput.addEventListener('input', () => {
                        const counter = document.getElementById(`char-counter-${sectionId}`);
                        if (counter) {
                            counter.textContent = `${textInput.value.length} / 500`;
                        }
                    });
                }

                addBtn.innerHTML = `
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
                    </svg>
                    Add ${itemType === 'sub_header' ? 'Sub-Header' : 'Item'}
                `;
                addBtn.style.display = '';
                break;

            case 'trophy':
                // Trophy selection button
                const container = document.getElementById('checklist-edit-container');
                const hasGame = container && container.dataset.conceptId;
                const checklistId = container && container.dataset.checklistId;

                inputArea.innerHTML = `
                    <div class="alert ${hasGame ? 'alert-info' : 'alert-warning'} text-sm">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            ${hasGame ?
                                '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>' :
                                '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>'
                            }
                        </svg>
                        <span>${hasGame ? 'Select one or more trophies from the game\'s trophy list. Supports batch selection!' : 'Select a game in the header above before adding trophies.'}</span>
                    </div>
                    <button class="btn btn-warning btn-block gap-2 open-trophy-selector-btn mt-3"
                            data-section-id="${sectionId}"
                            data-checklist-id="${checklistId}"
                            ${!hasGame ? 'disabled' : ''}>
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z"/>
                        </svg>
                        Select Trophy(s)
                    </button>
                `;

                addBtn.style.display = 'none'; // Trophy modal handles adding
                break;

            case 'image':
                // Image upload with caption
                inputArea.innerHTML = `
                    <div class="space-y-3">
                        <div class="form-control">
                            <label class="label">
                                <span class="label-text">Image File</span>
                                <span class="label-text-alt">Max 5MB</span>
                            </label>
                            <input type="file"
                                   id="item-image-${sectionId}"
                                   class="file-input file-input-bordered file-input-secondary w-full"
                                   accept="image/jpeg,image/png,image/webp,image/gif">
                        </div>
                        <div id="image-preview-${sectionId}" class="hidden">
                            <img src="" class="rounded-lg max-h-48 mx-auto border border-base-300" />
                        </div>
                        <div class="form-control">
                            <label class="label">
                                <span class="label-text">Caption (optional)</span>
                                <span class="label-text-alt" id="caption-counter-${sectionId}">0 / 200</span>
                            </label>
                            <input type="text"
                                   id="image-caption-${sectionId}"
                                   class="input input-bordered w-full"
                                   placeholder="Optional caption..."
                                   maxlength="200">
                        </div>
                    </div>
                `;

                // Setup image preview
                const imageInput = document.getElementById(`item-image-${sectionId}`);
                if (imageInput) {
                    imageInput.addEventListener('change', function() {
                        const file = this.files[0];
                        const preview = document.getElementById(`image-preview-${sectionId}`);
                        if (file && preview) {
                            const reader = new FileReader();
                            reader.onload = (e) => {
                                const img = preview.querySelector('img');
                                if (img) {
                                    img.src = e.target.result;
                                    preview.classList.remove('hidden');
                                }
                            };
                            reader.readAsDataURL(file);
                        }
                    });
                }

                // Setup caption counter
                const captionInput = document.getElementById(`image-caption-${sectionId}`);
                if (captionInput) {
                    captionInput.addEventListener('input', () => {
                        const counter = document.getElementById(`caption-counter-${sectionId}`);
                        if (counter) {
                            counter.textContent = `${captionInput.value.length} / 200`;
                        }
                    });
                }

                addBtn.innerHTML = `
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
                    </svg>
                    Add Image
                `;
                addBtn.style.display = '';
                break;

            case 'text_area':
                // Markdown textarea with preview
                inputArea.innerHTML = `
                    <div class="space-y-2">
                        <div class="form-control">
                            <label class="label">
                                <span class="label-text">Content (Markdown supported)</span>
                                <span class="label-text-alt" id="char-counter-${sectionId}">0 / 2000</span>
                            </label>
                            <textarea id="item-markdown-${sectionId}"
                                      class="textarea textarea-bordered textarea-info w-full h-32"
                                      placeholder="Add detailed instructions, tips, warnings...&#10;&#10;**bold**, *italic*, \`code\`, [links](url), lists, and more!"
                                      maxlength="2000"></textarea>
                        </div>
                        <div class="flex justify-between items-center">
                            <span class="text-xs text-base-content/60">
                                <button type="button" class="link link-hover" onclick="document.getElementById('bulk-upload-help-modal').showModal()">
                                    Markdown formatting guide
                                </button>
                            </span>
                        </div>
                    </div>
                `;

                // Setup character counter
                const markdownInput = document.getElementById(`item-markdown-${sectionId}`);
                if (markdownInput) {
                    markdownInput.addEventListener('input', () => {
                        const counter = document.getElementById(`char-counter-${sectionId}`);
                        if (counter) {
                            counter.textContent = `${markdownInput.value.length} / 2000`;
                        }
                    });
                }

                addBtn.innerHTML = `
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
                    </svg>
                    Add Text Area
                `;
                addBtn.style.display = '';
                break;
        }
    }

    /**
     * Initialize unified item creator for all sections.
     */
    function initUnifiedItemCreator() {
        // Setup type selectors
        document.querySelectorAll('.unified-item-type-select').forEach(select => {
            const sectionId = select.dataset.sectionId;

            // Initialize with default (item)
            handleItemTypeChange(sectionId, 'item');

            // Handle type changes
            select.addEventListener('change', function() {
                handleItemTypeChange(sectionId, this.value);
            });
        });

        // Setup add buttons (delegation handles new buttons after DOM updates)
        document.addEventListener('click', async (e) => {
            const addBtn = e.target.closest('.unified-add-item-btn');
            if (!addBtn) return;

            const sectionId = addBtn.dataset.sectionId;
            const typeSelect = document.getElementById(`item-type-select-${sectionId}`);
            const itemType = typeSelect ? typeSelect.value : 'item';

            // Collect data based on type
            let itemData = { item_type: itemType };
            let isValid = true;

            switch (itemType) {
                case 'item':
                case 'sub_header':
                    const textInput = document.getElementById(`item-text-${sectionId}`);
                    const text = textInput ? textInput.value.trim() : '';
                    if (!text) {
                        PlatPursuit.ToastManager.show('Item text is required', 'error');
                        return;
                    }
                    itemData.text = text;
                    break;

                case 'image':
                    const imageFile = document.getElementById(`item-image-${sectionId}`)?.files[0];
                    const caption = document.getElementById(`image-caption-${sectionId}`)?.value.trim();

                    if (!imageFile) {
                        PlatPursuit.ToastManager.show('Please select an image', 'error');
                        return;
                    }

                    // Use FormData for file upload
                    itemData = new FormData();
                    itemData.append('item_type', 'image');
                    itemData.append('image', imageFile);
                    if (caption) {
                        itemData.append('caption', caption);
                    }
                    break;

                case 'text_area':
                    const markdownText = document.getElementById(`item-markdown-${sectionId}`)?.value.trim();
                    if (!markdownText) {
                        PlatPursuit.ToastManager.show('Text area content is required', 'error');
                        return;
                    }
                    itemData.text = markdownText;
                    break;

                case 'trophy':
                    // Trophy modal handles this
                    return;
            }

            // Make API call
            try {
                addBtn.classList.add('loading');
                addBtn.disabled = true;

                let response;
                if (itemData instanceof FormData) {
                    // Image upload - use fetch directly for FormData
                    const res = await fetch(`${API_BASE}/checklists/sections/${sectionId}/items/`, {
                        method: 'POST',
                        headers: {
                            'X-CSRFToken': PlatPursuit.CSRFToken.get()
                        },
                        body: itemData
                    });

                    if (!res.ok) {
                        const errorData = await res.json().catch(() => ({}));
                        throw new Error(errorData.error || 'Failed to upload image');
                    }

                    response = await res.json();
                } else {
                    // Regular JSON request
                    response = await apiRequest(`${API_BASE}/checklists/sections/${sectionId}/items/`, 'POST', itemData);
                }

                // Success! Reload page to show new item
                // TODO: In the future, dynamically add to DOM instead of reload
                PlatPursuit.ToastManager.show(`${itemType === 'sub_header' ? 'Sub-header' : itemType === 'text_area' ? 'Text area' : itemType === 'image' ? 'Image' : 'Item'} added!`, 'success');

                // Clear inputs
                if (itemType === 'item' || itemType === 'sub_header') {
                    const textInput = document.getElementById(`item-text-${sectionId}`);
                    if (textInput) textInput.value = '';
                    const counter = document.getElementById(`char-counter-${sectionId}`);
                    if (counter) counter.textContent = '0 / 500';
                } else if (itemType === 'image') {
                    document.getElementById(`item-image-${sectionId}`).value = '';
                    document.getElementById(`image-caption-${sectionId}`).value = '';
                    document.getElementById(`image-preview-${sectionId}`)?.classList.add('hidden');
                } else if (itemType === 'text_area') {
                    document.getElementById(`item-markdown-${sectionId}`).value = '';
                    const counter = document.getElementById(`char-counter-${sectionId}`);
                    if (counter) counter.textContent = '0 / 2000';
                }

                // Reload to show new item (temporary - will be replaced with DOM insertion)
                setTimeout(() => window.location.reload(), 500);

            } catch (error) {
                console.error('Add item error:', error);
                PlatPursuit.ToastManager.show(error.message || 'Failed to add item', 'error');
            } finally {
                addBtn.classList.remove('loading');
                addBtn.disabled = false;
            }
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

                // If user can't save progress, just update locally
                if (!canSaveProgress) {
                    const checkboxes = section.querySelectorAll('.checklist-item[data-item-type="item"] .checklist-item-checkbox, .checklist-trophy-item[data-item-type="trophy"] .checklist-item-checkbox');
                    let checkedCount = 0;
                    checkboxes.forEach(checkbox => {
                        // Skip earned trophies - they're already checked
                        const itemContainer = checkbox.closest('.checklist-item, .checklist-trophy-item');
                        if (itemContainer && itemContainer.dataset.earned === 'true') {
                            return;
                        }
                        if (!checkbox.checked) {
                            checkbox.checked = true;
                            checkedCount++;
                            // Update text styling
                            const itemText = checkbox.closest('label')?.querySelector('.checklist-item-text');
                            if (itemText) {
                                itemText.classList.add('line-through', 'text-base-content/50');
                            }
                        }
                    });
                    updateLocalProgress();
                    updateSectionCounts();
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
                        updateProgressDisplay(response);

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

                // If user can't save progress, just update locally
                if (!canSaveProgress) {
                    const checkboxes = section.querySelectorAll('.checklist-item[data-item-type="item"] .checklist-item-checkbox, .checklist-trophy-item[data-item-type="trophy"] .checklist-item-checkbox');
                    let uncheckedCount = 0;
                    checkboxes.forEach(checkbox => {
                        // Don't uncheck earned trophies - they should stay checked
                        const itemContainer = checkbox.closest('.checklist-item, .checklist-trophy-item');
                        if (itemContainer && itemContainer.dataset.earned === 'true') {
                            return;
                        }
                        if (checkbox.checked) {
                            checkbox.checked = false;
                            uncheckedCount++;
                            // Update text styling
                            const itemText = checkbox.closest('label')?.querySelector('.checklist-item-text');
                            if (itemText) {
                                itemText.classList.remove('line-through', 'text-base-content/50');
                            }
                        }
                    });
                    updateLocalProgress();
                    updateSectionCounts();
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
                        updateProgressDisplay(response);

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
        // Unified Item Creator
        initUnifiedItemCreator();
        // Character counters with color coding
        initCharacterCounters();
        // Form validation
        initFormValidation();
        // Publishing requirements
        updatePublishingRequirements();
        // Unsaved changes warning - skip capturing original state if we restored unsaved changes
        initUnsavedChangesWarning(hasRestoredFields);
    });

})();
