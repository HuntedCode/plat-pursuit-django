/**
 * SectionBuilder - Visual section builder for structured notifications
 * Provides up/down arrow interface for creating and reordering notification sections
 */
class SectionBuilder {
    constructor(containerId, maxSections = 5) {
        this.container = document.getElementById(containerId);
        this.maxSections = maxSections;
        this.sections = [];
        this.nextId = 1;

        if (!this.container) {
            console.warn('SectionBuilder: Container not found:', containerId);
            return;
        }

        this.init();
    }

    init() {
        // Add 3 default sections with preset icons
        this.addSection({ header: '', icon: '✨', content: '' });
        this.addSection({ header: '', icon: '📋', content: '' });
        this.addSection({ header: '', icon: '🎯', content: '' });

        // Setup add section button
        const addBtn = document.getElementById('add-section-btn');
        if (addBtn) {
            addBtn.addEventListener('click', () => {
                this.addSection();
            });
        }

        this.updateMoveButtonStates();
    }

    addSection(data = null) {
        if (this.sections.length >= this.maxSections) {
            alert(`Maximum ${this.maxSections} sections allowed`);
            return;
        }

        const sectionId = data?.id || `section-${this.nextId++}`;
        const order = this.sections.length;

        const sectionData = {
            id: sectionId,
            header: data?.header || '',
            icon: data?.icon || '📝',
            content: data?.content || '',
            order: order
        };

        this.sections.push(sectionData);

        // Render section card
        const card = this.createSectionCard(sectionData);
        this.container.appendChild(card);

        // Attach event listeners
        this.attachSectionEvents(card, sectionId);

        // Update hidden input and toggle add button
        this.updateHiddenInput();
        this.toggleAddButton();
        this.updateMoveButtonStates();

        return sectionData;
    }

    createSectionCard(section) {
        const card = document.createElement('div');
        card.className = 'section-card bg-base-200 rounded-lg p-4 border-2 border-base-300';
        card.dataset.sectionId = section.id;

        card.innerHTML = `
            <div class="flex justify-between items-center mb-3">
                <div class="flex items-center gap-2">
                    <div class="flex flex-col gap-0.5">
                        <button type="button" class="move-up-btn btn btn-xs btn-ghost p-0 h-5 min-h-0" title="Move up">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                                <path fill-rule="evenodd" d="M14.707 12.707a1 1 0 01-1.414 0L10 9.414l-3.293 3.293a1 1 0 01-1.414-1.414l4-4a1 1 0 011.414 0l4 4a1 1 0 010 1.414z" clip-rule="evenodd"/>
                            </svg>
                        </button>
                        <button type="button" class="move-down-btn btn btn-xs btn-ghost p-0 h-5 min-h-0" title="Move down">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                                <path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/>
                            </svg>
                        </button>
                    </div>
                    <span class="badge badge-primary badge-sm">Section ${section.order + 1}</span>
                </div>
                <button type="button" class="remove-section-btn btn btn-xs btn-error btn-circle" title="Remove section">
                    ×
                </button>
            </div>

            <div class="space-y-3">
                <!-- Header -->
                <div class="form-control">
                    <div class="flex justify-between">
                        <label class="label-text font-semibold text-sm">Header</label>
                        <span class="label-text-alt text-xs section-header-count">0/100</span>
                    </div>
                    <input type="text" class="input input-bordered input-sm section-header" maxlength="100"
                           placeholder="e.g., New Features, Important Update"
                           value="${this.escapeHtml(section.header)}" />
                </div>

                <!-- Icon -->
                <div class="form-control">
                    <label class="label-text font-semibold text-sm">Icon (emoji)</label>
                    <input type="text" class="input input-bordered input-sm section-icon" maxlength="10"
                           placeholder="📋"
                           value="${this.escapeHtml(section.icon)}" />
                </div>

                <!-- Content -->
                <div class="form-control">
                    <div class="flex justify-between">
                        <label class="label-text font-semibold text-sm">Content</label>
                        <span class="label-text-alt text-xs section-content-count">0/800</span>
                    </div>
                    <textarea class="textarea textarea-bordered textarea-sm section-content" rows="4" maxlength="800"
                              placeholder="Use formatting: *bold*, _italic_, \`code\`, [link](url), - bullets">${this.escapeHtml(section.content)}</textarea>

                    <!-- Formatting Toolbar -->
                    <div class="flex gap-1 mt-2">
                        <button type="button" class="btn btn-xs btn-outline format-btn" data-format="bold" title="Bold">
                            <strong>B</strong>
                        </button>
                        <button type="button" class="btn btn-xs btn-outline format-btn" data-format="italic" title="Italic">
                            <em>I</em>
                        </button>
                        <button type="button" class="btn btn-xs btn-outline format-btn" data-format="code" title="Inline code">
                            <code>&lt;/&gt;</code>
                        </button>
                        <button type="button" class="btn btn-xs btn-outline format-btn" data-format="link" title="Link">
                            🔗
                        </button>
                        <button type="button" class="btn btn-xs btn-outline format-btn" data-format="list" title="Bullet list">
                            • List
                        </button>
                    </div>
                </div>
            </div>
        `;

        return card;
    }

    attachSectionEvents(card, sectionId) {
        // Character counters
        const headerInput = card.querySelector('.section-header');
        const contentInput = card.querySelector('.section-content');
        const iconInput = card.querySelector('.section-icon');

        headerInput?.addEventListener('input', (e) => {
            this.updateSectionData(sectionId, 'header', e.target.value);
            this.updateCharCount(card, '.section-header-count', e.target.value.length, 100);
        });

        contentInput?.addEventListener('input', (e) => {
            this.updateSectionData(sectionId, 'content', e.target.value);
            this.updateCharCount(card, '.section-content-count', e.target.value.length, 800);
        });

        iconInput?.addEventListener('input', (e) => {
            this.updateSectionData(sectionId, 'icon', e.target.value);
            // Trigger preview update
            if (window.adminNotificationManager) {
                window.adminNotificationManager.updatePreview();
            }
        });

        // Remove button
        card.querySelector('.remove-section-btn')?.addEventListener('click', () => {
            this.removeSection(sectionId);
        });

        // Move up button
        card.querySelector('.move-up-btn')?.addEventListener('click', () => {
            this.moveSection(sectionId, -1);
        });

        // Move down button
        card.querySelector('.move-down-btn')?.addEventListener('click', () => {
            this.moveSection(sectionId, 1);
        });

        // Format buttons
        card.querySelectorAll('.format-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const format = btn.dataset.format;
                this.applyFormatting(contentInput, format);
            });
        });

        // Initialize character counters
        this.updateCharCount(card, '.section-header-count', headerInput.value.length, 100);
        this.updateCharCount(card, '.section-content-count', contentInput.value.length, 800);
    }

    applyFormatting(textarea, format) {
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const text = textarea.value;
        const selectedText = text.substring(start, end) || 'text';

        let formatted = '';
        let cursorOffset = 0;

        switch (format) {
            case 'bold':
                formatted = `*${selectedText}*`;
                cursorOffset = 1;
                break;
            case 'italic':
                formatted = `_${selectedText}_`;
                cursorOffset = 1;
                break;
            case 'code':
                formatted = `\`${selectedText}\``;
                cursorOffset = 1;
                break;
            case 'link':
                formatted = `[${selectedText}](url)`;
                cursorOffset = selectedText.length + 3;
                break;
            case 'list':
                formatted = `- ${selectedText}`;
                cursorOffset = 2;
                break;
        }

        textarea.value = text.substring(0, start) + formatted + text.substring(end);
        textarea.focus();
        textarea.setSelectionRange(start + cursorOffset, start + cursorOffset);

        // Trigger input event to update data
        textarea.dispatchEvent(new Event('input'));
    }

    removeSection(sectionId) {
        if (this.sections.length <= 1) {
            alert('At least one section is required');
            return;
        }

        const idx = this.sections.findIndex(s => s.id === sectionId);
        if (idx === -1) return;

        this.sections.splice(idx, 1);

        // Remove card from DOM
        const card = this.container.querySelector(`[data-section-id="${sectionId}"]`);
        card?.remove();

        // Update order and badges
        this.reorderSections();
        this.updateHiddenInput();
        this.toggleAddButton();
        this.updateMoveButtonStates();
    }

    updateSectionData(sectionId, field, value) {
        const section = this.sections.find(s => s.id === sectionId);
        if (section) {
            section[field] = value;
            this.updateHiddenInput();

            // Trigger preview update if available
            if (window.adminNotificationManager) {
                window.adminNotificationManager.updatePreview();
            }
        }
    }

    moveSection(sectionId, direction) {
        const idx = this.sections.findIndex(s => s.id === sectionId);
        if (idx === -1) return;

        const newIdx = idx + direction;
        // Check bounds
        if (newIdx < 0 || newIdx >= this.sections.length) return;

        // Swap in sections array
        [this.sections[idx], this.sections[newIdx]] = [this.sections[newIdx], this.sections[idx]];

        // Swap in DOM
        const cards = Array.from(this.container.querySelectorAll('.section-card'));
        const currentCard = cards[idx];
        const targetCard = cards[newIdx];

        if (direction === -1) {
            // Moving up: insert before target
            this.container.insertBefore(currentCard, targetCard);
        } else {
            // Moving down: insert after target
            this.container.insertBefore(currentCard, targetCard.nextSibling);
        }

        // Update order and badges
        this.reorderSections();
        this.updateMoveButtonStates();
    }

    updateMoveButtonStates() {
        const cards = Array.from(this.container.querySelectorAll('.section-card'));
        cards.forEach((card, index) => {
            const upBtn = card.querySelector('.move-up-btn');
            const downBtn = card.querySelector('.move-down-btn');

            // Disable up button for first card
            if (upBtn) {
                upBtn.disabled = index === 0;
                upBtn.classList.toggle('btn-disabled', index === 0);
            }

            // Disable down button for last card
            if (downBtn) {
                downBtn.disabled = index === cards.length - 1;
                downBtn.classList.toggle('btn-disabled', index === cards.length - 1);
            }
        });
    }

    reorderSections() {
        // Update section order based on DOM order
        const cards = Array.from(this.container.querySelectorAll('.section-card'));

        cards.forEach((card, index) => {
            const sectionId = card.dataset.sectionId;
            const section = this.sections.find(s => s.id === sectionId);
            if (section) {
                section.order = index;
            }

            // Update badge
            const badge = card.querySelector('.badge');
            if (badge) {
                badge.textContent = `Section ${index + 1}`;
            }
        });

        this.updateHiddenInput();

        // Trigger preview update
        if (window.adminNotificationManager) {
            window.adminNotificationManager.updatePreview();
        }
    }

    updateHiddenInput() {
        const input = document.getElementById('sections-data-input');
        if (input) {
            input.value = JSON.stringify(this.sections);
        }
    }

    toggleAddButton() {
        const btn = document.getElementById('add-section-btn');
        if (btn) {
            btn.disabled = this.sections.length >= this.maxSections;
        }
    }

    updateCharCount(card, selector, count, max) {
        const counter = card.querySelector(selector);
        if (counter) {
            counter.textContent = `${count}/${max}`;
            counter.classList.toggle('text-error', count > max * 0.9);
        }
    }

    // For preview rendering
    getSections() {
        return this.sections;
    }

    escapeHtml(text) {
        return PlatPursuit.HTMLUtils.escape(text);
    }
}

// Export to global namespace
window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.SectionBuilder = SectionBuilder;
