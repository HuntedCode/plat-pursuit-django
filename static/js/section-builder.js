/**
 * SectionBuilder - Visual section builder for structured notifications
 * Provides drag-and-drop interface for creating notification sections
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

        this.setupDragAndDrop();
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

        return sectionData;
    }

    createSectionCard(section) {
        const card = document.createElement('div');
        card.className = 'section-card bg-base-200 rounded-lg p-4 border-2 border-base-300';
        card.dataset.sectionId = section.id;

        card.innerHTML = `
            <div class="flex justify-between items-center mb-3">
                <div class="flex items-center gap-2">
                    <button type="button" class="drag-handle btn btn-xs btn-ghost cursor-move" title="Drag to reorder">
                        ⋮⋮
                    </button>
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
        });

        // Remove button
        card.querySelector('.remove-section-btn')?.addEventListener('click', () => {
            this.removeSection(sectionId);
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
    }

    updateSectionData(sectionId, field, value) {
        const section = this.sections.find(s => s.id === sectionId);
        if (section) {
            section[field] = value;
            this.updateHiddenInput();

            // Trigger preview update if available
            if (window.notificationAdmin) {
                window.notificationAdmin.updatePreview();
            }
        }
    }

    setupDragAndDrop() {
        // Basic drag-and-drop using native API
        let draggedElement = null;

        this.container.addEventListener('dragstart', (e) => {
            const card = e.target.closest('.section-card');
            if (card && e.target.classList.contains('drag-handle')) {
                draggedElement = card;
                card.style.opacity = '0.5';
            }
        });

        this.container.addEventListener('dragend', (e) => {
            if (draggedElement) {
                draggedElement.style.opacity = '1';
                draggedElement = null;
            }
        });

        this.container.addEventListener('dragover', (e) => {
            e.preventDefault();
            const card = e.target.closest('.section-card');
            if (card && card !== draggedElement) {
                const rect = card.getBoundingClientRect();
                const midpoint = rect.top + rect.height / 2;
                if (e.clientY < midpoint) {
                    card.parentNode.insertBefore(draggedElement, card);
                } else {
                    card.parentNode.insertBefore(draggedElement, card.nextSibling);
                }
                this.reorderSections();
            }
        });

        // Make drag handles draggable
        this.container.addEventListener('mousedown', (e) => {
            if (e.target.classList.contains('drag-handle')) {
                const card = e.target.closest('.section-card');
                if (card) {
                    card.draggable = true;
                }
            }
        });

        this.container.addEventListener('mouseup', (e) => {
            const cards = this.container.querySelectorAll('.section-card');
            cards.forEach(card => card.draggable = false);
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
        if (window.notificationAdmin) {
            window.notificationAdmin.updatePreview();
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
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Export to global namespace
window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.SectionBuilder = SectionBuilder;
