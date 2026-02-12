/**
 * ShareImageManager - Handles platinum share image generation and preview
 * Downloads are rendered server-side via Playwright (headless Chromium).
 * Preview is still client-side HTML rendering.
 *
 * Depends on: PlatPursuit.API, PlatPursuit.ToastManager, PlatPursuit.CSRFToken
 *
 * Theme definitions are loaded from server via window.GRADIENT_THEMES
 * (set by the gradient_themes_json template tag) for single source of truth.
 */
class ShareImageManager {
    constructor(notificationId, metadata) {
        this.notificationId = notificationId;
        this.metadata = metadata || {};
        this.currentFormat = 'landscape';
        this.currentBackground = 'default';
        this.generatedImages = {};

        // Image dimensions
        this.dimensions = {
            landscape: { width: 1200, height: 630 },
            portrait: { width: 1080, height: 1350 }
        };

        // Build background styles from external themes if available
        this.backgroundStyles = this._buildBackgroundStyles();
    }

    /**
     * Build background styles from window.GRADIENT_THEMES
     */
    _buildBackgroundStyles() {
        // Check if themes are loaded from server
        if (window.GRADIENT_THEMES && Object.keys(window.GRADIENT_THEMES).length > 0) {
            return this._buildFromExternalThemes(window.GRADIENT_THEMES);
        }

        // Error: themes should always be provided by server
        console.error('GRADIENT_THEMES not loaded. Ensure gradient_themes_json template tag is included.');

        // Minimal fallback with just default theme
        return {
            'default': {
                name: 'Default',
                description: 'Default gradient',
                accentColor: '#67d1f8',
                getStyle: () => ({
                    background: 'linear-gradient(to bottom right, #2a2e34, #32363d, #2a2e34)'
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(135deg, rgba(103, 209, 248, 0.15) 0%, rgba(103, 209, 248, 0.05) 100%)',
                    borderColor: '#67d1f8'
                })
            }
        };
    }

    /**
     * Convert server-provided themes to the format expected by this class
     */
    _buildFromExternalThemes(themes) {
        const styles = {};

        for (const [key, theme] of Object.entries(themes)) {
            styles[key] = {
                name: theme.name,
                description: theme.description,
                accentColor: theme.accentColor,
                gameImageSource: theme.gameImageSource || null,
                getStyle: theme.requiresGameImage
                    ? function(gameImages) {
                        const source = theme.gameImageSource || 'game_image';
                        const imageUrl = typeof gameImages === 'string'
                            ? gameImages
                            : (gameImages?.[source] || gameImages?.game_image);

                        if (imageUrl) {
                            return {
                                background: `linear-gradient(rgba(26, 27, 31, 0.85), rgba(26, 27, 31, 0.9)), url("${imageUrl}")`,
                                backgroundSize: 'cover',
                                backgroundPosition: 'center'
                            };
                        }
                        return styles.default ? styles.default.getStyle() : { background: theme.background };
                    }
                    : function() {
                        const result = {
                            background: theme.background,
                            backgroundSize: theme.backgroundSize || undefined,
                            backgroundPosition: theme.backgroundPosition || undefined,
                            backgroundRepeat: theme.backgroundRepeat || undefined
                        };
                        Object.keys(result).forEach(k => result[k] === undefined && delete result[k]);
                        return result;
                    },
                getBannerStyle: function() {
                    return {
                        background: theme.bannerBackground,
                        borderColor: theme.bannerBorderColor
                    };
                }
            };
        }

        return styles;
    }

    /**
     * Render the share section UI
     */
    renderShareSection() {
        return `
            <div class="mt-6 border-t border-base-300 pt-6" id="share-image-section">
                <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z" />
                    </svg>
                    Share Your Achievement
                </h3>

                <!-- Format & Background Selectors -->
                <div class="flex flex-wrap gap-4 mb-4 items-center">
                    <div class="flex gap-2">
                        <button class="btn btn-sm format-btn btn-primary" data-format="landscape">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                            </svg>
                            Landscape
                        </button>
                        <button class="btn btn-sm format-btn btn-outline" data-format="portrait">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z" />
                            </svg>
                            Portrait
                        </button>
                    </div>

                    <div class="flex flex-col gap-3">
                        <div class="flex items-center gap-2">
                            <label for="background-select" class="text-sm text-base-content/70 whitespace-nowrap">Background:</label>
                            <select id="background-select" class="select select-sm select-bordered bg-base-200 flex-1">
                                ${this.renderBackgroundOptions()}
                            </select>
                            <button type="button" id="open-color-grid" class="btn btn-sm btn-primary btn-square" title="Choose from grid">
                                <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                                </svg>
                            </button>
                        </div>
                        <p class="text-xs text-base-content/50">Select from dropdown or click grid icon for visual picker</p>
                    </div>
                </div>

                <!-- Preview Container -->
                <div class="relative bg-base-300 rounded-lg overflow-hidden mb-4">
                    <div id="share-preview-container" class="w-full overflow-x-auto py-4 px-2" style="max-height: 450px;">
                        <div id="share-preview" class="mx-auto shadow-xl rounded-lg overflow-hidden" style="transform-origin: top center;">
                            <!-- Preview rendered here -->
                        </div>
                    </div>
                    <div id="preview-loading" class="absolute inset-0 flex items-center justify-center bg-base-300/80">
                        <span class="loading loading-spinner loading-lg text-primary"></span>
                    </div>
                </div>

                <!-- Format info -->
                <p class="text-xs text-base-content/60 mb-4" id="format-info">
                    Landscape (1200x630)
                </p>

                <!-- Actions -->
                <div class="flex gap-2 flex-wrap">
                    <button id="generate-image-btn" class="btn btn-primary">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                        </svg>
                        Download Image
                    </button>
                    <button id="generate-both-btn" class="btn btn-secondary btn-outline">
                        Download Both Formats
                    </button>
                </div>

                <!-- Error message -->
                <div id="share-error" class="alert alert-error mt-4 hidden">
                    <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <span id="share-error-message"></span>
                </div>
            </div>
        `;
    }

    /**
     * Render background dropdown options
     * Default is always first, then alphabetically sorted
     * Game art themes are included only when their required image is available
     */
    renderBackgroundOptions() {
        const entries = Object.entries(this.backgroundStyles);

        const filtered = entries.filter(([, style]) => {
            if (!style.gameImageSource) {
                return true;
            }
            const imageUrl = this.metadata[style.gameImageSource];
            return !!imageUrl;
        });

        const defaultEntry = filtered.find(([key]) => key === 'default');
        const otherEntries = filtered.filter(([key]) => key !== 'default');
        otherEntries.sort((a, b) => a[1].name.localeCompare(b[1].name));

        const sortedEntries = defaultEntry ? [defaultEntry, ...otherEntries] : otherEntries;

        return sortedEntries
            .map(([key, style]) => `<option value="${key}">${style.name}</option>`)
            .join('');
    }

    /**
     * Apply selected background style to an element and its banner (for preview only)
     * @param {HTMLElement} element - The .share-image-content element to style
     */
    async applyBackground(element) {
        if (!element) return;

        const styleKey = this.currentBackground;
        const styleDef = this.backgroundStyles[styleKey];

        if (!styleDef) return;

        const gameImages = {
            game_image: this.metadata.game_image || null,
            concept_bg_url: this.metadata.concept_bg_url || null
        };

        const styles = styleDef.getStyle(gameImages);

        // Apply background styles to main element
        Object.entries(styles).forEach(([prop, value]) => {
            element.style[prop] = value;
        });

        // Apply banner styles if getBannerStyle exists
        if (styleDef.getBannerStyle) {
            const banner = element.querySelector('[data-element="platinum-banner"]');
            if (banner) {
                const bannerStyles = styleDef.getBannerStyle();

                if (bannerStyles.background) {
                    banner.style.background = bannerStyles.background;
                }
                if (bannerStyles.borderColor) {
                    banner.style.borderLeftColor = bannerStyles.borderColor;
                    banner.style.borderTopColor = bannerStyles.borderColor;
                    banner.style.borderBottomColor = bannerStyles.borderColor;
                }
            }
        }
    }

    /**
     * Initialize event listeners
     */
    init() {
        // Format buttons
        document.querySelectorAll('.format-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.format-btn').forEach(b => {
                    b.classList.remove('btn-primary');
                    b.classList.add('btn-outline');
                });
                e.currentTarget.classList.remove('btn-outline');
                e.currentTarget.classList.add('btn-primary');
                this.currentFormat = e.currentTarget.dataset.format;
                this.updateFormatInfo();
                this.renderPreview();
            });
        });

        // Generate button
        document.getElementById('generate-image-btn')?.addEventListener('click', () => {
            this.generateAndDownload(this.currentFormat);
        });

        // Generate both button
        document.getElementById('generate-both-btn')?.addEventListener('click', () => {
            this.generateAndDownload('both');
        });

        // Background selector
        document.getElementById('background-select')?.addEventListener('change', (e) => {
            this.currentBackground = e.target.value;
            this.renderPreview();
        });

        // Get singleton color grid modal if available
        if (window.PlatPursuit?.getColorGridModal) {
            this.colorModal = window.PlatPursuit.getColorGridModal();

            const modalBtn = document.getElementById('open-color-grid');
            if (modalBtn) {
                modalBtn.addEventListener('click', () => {
                    this.openColorModal();
                });
            }
        }

        // Render initial preview
        this.renderPreview();
    }

    /**
     * Open color grid modal for visual theme selection
     */
    openColorModal() {
        if (!this.colorModal) {
            console.warn('ColorGridModal not initialized');
            return;
        }

        const gameImages = {
            game_image: this.metadata.game_image || '',
            concept_bg_url: this.metadata.concept_bg_url || ''
        };

        this.colorModal.open(this.currentBackground, (selectedTheme) => {
            this.currentBackground = selectedTheme;

            const selectElement = document.getElementById('background-select');
            if (selectElement) {
                const optionExists = Array.from(selectElement.options).some(opt => opt.value === selectedTheme);
                if (optionExists) {
                    selectElement.value = selectedTheme;
                } else {
                    selectElement.value = 'default';
                }
            }

            this.renderPreview();
        }, this.currentFormat, gameImages);
    }

    /**
     * Update format info text
     */
    updateFormatInfo() {
        const infoEl = document.getElementById('format-info');
        if (!infoEl) return;

        if (this.currentFormat === 'landscape') {
            infoEl.textContent = 'Landscape (1200x630)';
        } else {
            infoEl.textContent = 'Portrait (1080x1350)';
        }
    }

    /**
     * Fetch HTML template from server and render preview
     */
    async renderPreview() {
        const container = document.getElementById('share-preview');
        const loading = document.getElementById('preview-loading');

        if (!container) return;

        loading?.classList.remove('hidden');

        try {
            const html = await this.fetchCardHTML(this.currentFormat);

            const { width, height } = this.dimensions[this.currentFormat];

            const maxHeight = 437;
            const scale = Math.min(1, maxHeight / height, 575 / width);

            container.style.width = `${width * scale}px`;
            container.style.height = `${height * scale}px`;

            container.innerHTML = html;

            const innerContent = container.querySelector('.share-image-content');
            if (innerContent) {
                innerContent.style.transform = `scale(${scale})`;
                innerContent.style.transformOrigin = 'top left';
                await this.applyBackground(innerContent);
            }
        } catch (error) {
            console.error('Failed to render preview:', error);
            container.innerHTML = '<div class="p-4 text-error">Failed to load preview</div>';
        } finally {
            loading?.classList.add('hidden');
        }
    }

    /**
     * Fetch the card HTML from the server (used for preview)
     */
    async fetchCardHTML(format) {
        const response = await PlatPursuit.API.get(
            `/api/v1/notifications/${this.notificationId}/share-image/html/?image_format=${format}`
        );

        if (response && response.html) {
            return response.html;
        }
        throw new Error('Failed to fetch card HTML');
    }

    /**
     * Generate image via server-side Playwright rendering and download
     */
    async generateAndDownload(format) {
        const btn = document.getElementById('generate-image-btn');
        const bothBtn = document.getElementById('generate-both-btn');
        const errorEl = document.getElementById('share-error');

        try {
            errorEl?.classList.add('hidden');
            btn.classList.add('loading');
            btn.disabled = true;
            bothBtn.disabled = true;

            const formats = format === 'both' ? ['landscape', 'portrait'] : [format];

            for (const fmt of formats) {
                await this.generateSingleImage(fmt);
            }

            // Track download
            try {
                await PlatPursuit.API.post('/api/v1/tracking/site-event/', {
                    event_type: 'share_card_download',
                    object_id: String(this.notificationId || 'unknown')
                });
            } catch (trackError) {
                console.warn('Failed to track download:', trackError);
            }

            PlatPursuit.ToastManager.success('Image downloaded successfully!');
        } catch (error) {
            console.error('Image generation failed:', error);
            this.showError(error.message || 'Failed to generate image. Please try again.');
            PlatPursuit.ToastManager.error('Failed to generate image');
        } finally {
            btn.classList.remove('loading');
            btn.disabled = false;
            bothBtn.disabled = false;
        }
    }

    /**
     * Build the PNG endpoint URL for server-side rendering
     */
    getPngEndpoint(format) {
        return `/api/v1/notifications/${this.notificationId}/share-image/png/?image_format=${format}&theme=${this.currentBackground}`;
    }

    /**
     * Generate a single image format via server-side Playwright rendering
     */
    async generateSingleImage(format) {
        const url = this.getPngEndpoint(format);

        const response = await fetch(url, {
            credentials: 'same-origin',
            headers: {
                'X-CSRFToken': PlatPursuit.CSRFToken.get(),
            },
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Server rendering failed: ${response.status} ${errorText}`);
        }

        const blob = await response.blob();

        const gameName = (this.metadata.game_name || 'platinum')
            .replace(/[^a-z0-9]/gi, '_')
            .substring(0, 30);

        const filename = `platinum_${gameName}_${format}.png`;

        const downloadUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(downloadUrl);
    }

    /**
     * Show error message
     */
    showError(message) {
        const errorEl = document.getElementById('share-error');
        const errorMsg = document.getElementById('share-error-message');

        if (errorEl && errorMsg) {
            errorMsg.textContent = message;
            errorEl.classList.remove('hidden');
        }
    }
}

// Export for use
window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.ShareImageManager = ShareImageManager;
