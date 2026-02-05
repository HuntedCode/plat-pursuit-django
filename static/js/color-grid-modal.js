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
        this.gameImages = {
            game_image: '',
            concept_bg_url: ''
        };
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

            // Don't allow selection of disabled game art buttons
            if (btn.disabled) return;

            const themeKey = btn.dataset.themeKey;
            this.selectTheme(themeKey);
        });

        // Apply button handler
        this.applyButton?.addEventListener('click', (e) => {
            e.preventDefault();
            this.applySelection();
        });
    }

    /**
     * Update game art preview images in the grid
     * @param {string} gameImage - URL to game cover image
     * @param {string} conceptBgUrl - URL to concept background
     */
    updateGameArtPreviews(gameImage, conceptBgUrl) {
        this.gameImages = {
            game_image: gameImage || '',
            concept_bg_url: conceptBgUrl || ''
        };

        // Update all game art theme buttons
        this.grid.querySelectorAll('[data-game-art="true"]').forEach(btn => {
            const source = btn.dataset.gameImageSource;
            const imageUrl = source === 'concept_bg_url' ? conceptBgUrl : gameImage;

            if (imageUrl) {
                btn.style.backgroundImage = `linear-gradient(rgba(26, 27, 31, 0.85), rgba(26, 27, 31, 0.9)), url("${imageUrl}")`;
                btn.style.backgroundSize = 'cover';
                btn.style.backgroundPosition = 'center';
                btn.classList.remove('opacity-50', 'cursor-not-allowed');
                btn.disabled = false;
            } else {
                // Disable if no image available
                btn.style.backgroundImage = 'linear-gradient(rgba(26, 27, 31, 0.9), rgba(26, 27, 31, 0.95))';
                btn.classList.add('opacity-50', 'cursor-not-allowed');
                btn.disabled = true;
            }
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
                // For game art themes, use backgroundImage; for others, use background
                if (selectedBtn.dataset.gameArt === 'true') {
                    activePreview.style.backgroundImage = selectedBtn.style.backgroundImage;
                    activePreview.style.backgroundSize = 'cover';
                    activePreview.style.backgroundPosition = 'center';
                } else {
                    activePreview.style.backgroundImage = '';
                    activePreview.style.background = selectedBtn.style.background;
                }
            }
        }
    }

    /**
     * Open modal for theme selection
     * @param {string} currentTheme - Currently selected theme key
     * @param {function} onApply - Callback function when theme is applied (receives themeKey)
     * @param {string} format - 'landscape' or 'portrait' to match share card dimensions
     * @param {object} gameImages - { game_image: '...', concept_bg_url: '...' } for game art previews
     */
    open(currentTheme = 'default', onApply = null, format = 'landscape', gameImages = null) {
        if (!this.modal) return;

        this.selectedTheme = currentTheme;
        this.callback = onApply;
        this.currentFormat = format;

        // Update game art previews if provided
        if (gameImages) {
            this.updateGameArtPreviews(gameImages.game_image, gameImages.concept_bg_url);
        }

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
// Use singleton pattern to prevent multiple instances causing stale callback issues
window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.ColorGridModal = ColorGridModal;

// Singleton instance getter - ensures only one modal instance exists
window.PlatPursuit.getColorGridModal = function() {
    if (!window.PlatPursuit._colorGridModalInstance) {
        window.PlatPursuit._colorGridModalInstance = new ColorGridModal();
        window.PlatPursuit._colorGridModalInstance.init();
    }
    return window.PlatPursuit._colorGridModalInstance;
};
