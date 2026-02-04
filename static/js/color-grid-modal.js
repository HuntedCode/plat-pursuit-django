/**
 * ColorGridModal - Reusable color grid modal for theme selection
 * Works with any manager class via callback pattern
 */
class ColorGridModal {
    constructor(modalId = 'color-grid-modal') {
        this.modalId = modalId;
        this.modal = null;
        this.grid = null;
        this.previewLandscape = null;
        this.previewPortrait = null;
        this.applyButton = null;
        this.selectedTheme = 'default';
        this.currentFormat = 'landscape';
        this.callback = null;
        this.themes = window.GRADIENT_THEMES || {};
    }

    init() {
        this.modal = document.getElementById(this.modalId);
        this.grid = document.getElementById('modal-theme-grid');
        this.previewLandscape = document.getElementById('modal-theme-preview-landscape');
        this.previewPortrait = document.getElementById('modal-theme-preview-portrait');
        this.applyButton = document.getElementById('modal-apply-theme');

        if (!this.modal || !this.grid) {
            console.warn('ColorGridModal: Required elements not found');
            return;
        }

        this.setupEventListeners();
    }

    setupEventListeners() {
        // Event delegation for grid buttons (follows settings.html pattern)
        this.grid.addEventListener('click', (e) => {
            const btn = e.target.closest('.theme-preview-btn');
            if (!btn) return;

            const themeKey = btn.dataset.themeKey;
            this.selectTheme(themeKey);
        });

        // Apply button handler
        this.applyButton?.addEventListener('click', (e) => {
            e.preventDefault();
            this.applySelection();
        });
    }

    selectTheme(themeKey) {
        this.selectedTheme = themeKey;

        // Update visual selection (same pattern as settings.html lines 261-275)
        this.grid.querySelectorAll('.theme-preview-btn').forEach(b => {
            b.classList.remove('border-primary', 'ring-2', 'ring-primary');
            b.classList.add('border-base-300');
        });

        const selectedBtn = this.grid.querySelector(`[data-theme-key="${themeKey}"]`);
        if (selectedBtn) {
            selectedBtn.classList.remove('border-base-300');
            selectedBtn.classList.add('border-primary', 'ring-2', 'ring-primary');

            // Update the active preview container
            const activePreview = this.currentFormat === 'landscape'
                ? this.previewLandscape
                : this.previewPortrait;

            if (activePreview) {
                activePreview.style.background = selectedBtn.style.background;
            }
        }
    }

    /**
     * Open modal for theme selection
     * @param {string} currentTheme - Currently selected theme key
     * @param {function} onApply - Callback function when theme is applied (receives themeKey)
     * @param {string} format - 'landscape' or 'portrait' to match share card dimensions
     */
    open(currentTheme = 'default', onApply = null, format = 'landscape') {
        if (!this.modal) return;

        this.selectedTheme = currentTheme;
        this.callback = onApply;
        this.currentFormat = format;

        // Show/hide appropriate preview based on format
        if (this.previewLandscape && this.previewPortrait) {
            if (format === 'landscape') {
                this.previewLandscape.classList.remove('hidden');
                this.previewLandscape.classList.add('flex');
                this.previewPortrait.classList.add('hidden');
                this.previewPortrait.classList.remove('flex');
            } else {
                this.previewPortrait.classList.remove('hidden');
                this.previewPortrait.classList.add('flex');
                this.previewLandscape.classList.add('hidden');
                this.previewLandscape.classList.remove('flex');
            }
        }

        // Set initial selection in grid
        this.selectTheme(currentTheme);

        // Show modal
        this.modal.showModal();
    }

    applySelection() {
        if (this.callback) {
            this.callback(this.selectedTheme);
        }
        this.modal.close();
    }
}

// Export to global namespace for access by other scripts
window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.ColorGridModal = ColorGridModal;
