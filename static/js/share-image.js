/**
 * ShareImageManager - Handles platinum share image generation and preview
 * Uses html2canvas for client-side image generation from HTML template
 * Depends on: PlatPursuit.API, PlatPursuit.ToastManager, html2canvas (CDN)
 *
 * Theme definitions can be loaded from server via window.GRADIENT_THEMES
 * (set by the gradient_themes_json template tag) for single source of truth.
 * Falls back to inline definitions if external themes aren't available.
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
                        // gameImages can be a string (backward compat) or object { game_image, concept_bg_url }
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
                        // Fallback to default if no game image
                        return styles.default ? styles.default.getStyle() : { background: theme.background };
                    }
                    : function() {
                        const result = {
                            background: theme.background,
                            backgroundSize: theme.backgroundSize || undefined,
                            backgroundPosition: theme.backgroundPosition || undefined,
                            backgroundRepeat: theme.backgroundRepeat || undefined
                        };
                        // Remove undefined properties
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

                <!-- Hidden container for full-size rendering -->
                <div id="share-render-container" style="position: absolute; left: -9999px; top: 0;"></div>

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
     * Game art themes are excluded from dropdown (they're in the color grid)
     */
    renderBackgroundOptions() {
        const entries = Object.entries(this.backgroundStyles);

        // Filter out game art themes - they're now in the color grid
        const filtered = entries.filter(([key, style]) => !style.gameImageSource);

        // Separate default from others
        const defaultEntry = filtered.find(([key]) => key === 'default');
        const otherEntries = filtered.filter(([key]) => key !== 'default');

        // Sort others alphabetically by name
        otherEntries.sort((a, b) => a[1].name.localeCompare(b[1].name));

        // Combine with default first
        const sortedEntries = defaultEntry ? [defaultEntry, ...otherEntries] : otherEntries;

        return sortedEntries
            .map(([key, style]) => `<option value="${key}">${style.name}</option>`)
            .join('');
    }

    /**
     * Apply selected background style to an element and its banner
     * @param {HTMLElement} element - The .share-image-content element to style
     */
    applyBackground(element) {
        if (!element) return;

        const styleKey = this.currentBackground;
        const styleDef = this.backgroundStyles[styleKey];

        if (!styleDef) return;

        // Pass both game images for styles that use them
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

                // Apply background
                if (bannerStyles.background) {
                    banner.style.background = bannerStyles.background;
                }

                // Apply border color (handles both landscape border-left and portrait border-top/bottom)
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

            // Attach modal trigger
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

        // Pass game images to modal for preview thumbnails
        const gameImages = {
            game_image: this.metadata.game_image || '',
            concept_bg_url: this.metadata.concept_bg_url || ''
        };

        // Open modal with current background, callback, format, and game images
        this.colorModal.open(this.currentBackground, (selectedTheme) => {
            // Update internal state
            this.currentBackground = selectedTheme;

            // Sync dropdown to match selection (if theme is in dropdown)
            const selectElement = document.getElementById('background-select');
            if (selectElement) {
                // Check if option exists in dropdown before setting
                const optionExists = Array.from(selectElement.options).some(opt => opt.value === selectedTheme);
                if (optionExists) {
                    selectElement.value = selectedTheme;
                } else {
                    // Game art theme selected - reset dropdown to default visual
                    selectElement.value = 'default';
                }
            }

            // Trigger preview re-render with new background
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
            // Fetch rendered HTML from server
            const html = await this.fetchCardHTML(this.currentFormat);

            const { width, height } = this.dimensions[this.currentFormat];

            // Calculate scale to fit in preview container (increased by 15% for better readability)
            const maxHeight = 437;
            const scale = Math.min(1, maxHeight / height, 575 / width);

            // Set container dimensions BEFORE inserting HTML to prevent flash
            container.style.width = `${width * scale}px`;
            container.style.height = `${height * scale}px`;

            // Insert the HTML
            container.innerHTML = html;

            // Scale the inner content and apply background
            const innerContent = container.querySelector('.share-image-content');
            if (innerContent) {
                innerContent.style.transform = `scale(${scale})`;
                innerContent.style.transformOrigin = 'top left';
                // Apply the selected background style
                this.applyBackground(innerContent);
            }
        } catch (error) {
            console.error('Failed to render preview:', error);
            container.innerHTML = '<div class="p-4 text-error">Failed to load preview</div>';
        } finally {
            loading?.classList.add('hidden');
        }
    }

    /**
     * Fetch the card HTML from the server
     */
    async fetchCardHTML(format) {
        // Note: Using 'image_format' instead of 'format' because DRF reserves 'format' for content negotiation
        const response = await PlatPursuit.API.get(
            `/api/v1/notifications/${this.notificationId}/share-image/html/?image_format=${format}`
        );

        if (response && response.html) {
            return response.html;
        }
        throw new Error('Failed to fetch card HTML');
    }

    /**
     * Generate image using html2canvas and download
     */
    async generateAndDownload(format) {
        const btn = document.getElementById('generate-image-btn');
        const bothBtn = document.getElementById('generate-both-btn');
        const errorEl = document.getElementById('share-error');
        const renderContainer = document.getElementById('share-render-container');

        if (!renderContainer) return;

        try {
            errorEl?.classList.add('hidden');
            btn.classList.add('loading');
            btn.disabled = true;
            bothBtn.disabled = true;

            const formats = format === 'both' ? ['landscape', 'portrait'] : [format];

            for (const fmt of formats) {
                await this.generateSingleImage(fmt, renderContainer);
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
            renderContainer.innerHTML = '';
        }
    }

    /**
     * Convert a single oklch color to rgba using canvas
     */
    oklchToRgba(oklchColor) {
        const canvas = document.createElement('canvas');
        canvas.width = 1;
        canvas.height = 1;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = oklchColor;
        ctx.fillRect(0, 0, 1, 1);
        const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
        return `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
    }

    /**
     * Convert oklch colors in a string to rgba
     */
    convertOklchInString(str) {
        // Match oklch(...) including nested parentheses for calc() etc.
        const oklchRegex = /oklch\([^)]+\)/g;
        return str.replace(oklchRegex, (match) => {
            return this.oklchToRgba(match);
        });
    }

    /**
     * Convert oklch colors to rgb for html2canvas compatibility
     * html2canvas doesn't support oklch() color function used by Tailwind CSS v4
     */
    convertOklchToRgb(element) {
        const allElements = [element, ...element.querySelectorAll('*')];

        // Properties that can contain colors
        const colorProperties = [
            'color', 'backgroundColor', 'borderColor',
            'borderTopColor', 'borderRightColor', 'borderBottomColor', 'borderLeftColor',
            'outlineColor', 'textDecorationColor', 'fill', 'stroke'
        ];

        allElements.forEach(el => {
            const computed = window.getComputedStyle(el);

            colorProperties.forEach(prop => {
                const value = computed[prop];
                if (value && value.includes('oklch')) {
                    el.style[prop] = this.convertOklchInString(value);
                }
            });

            // Handle box-shadow which may have multiple oklch colors
            const boxShadow = computed.boxShadow;
            if (boxShadow && boxShadow.includes('oklch')) {
                el.style.boxShadow = this.convertOklchInString(boxShadow);
            }

            // Handle gradient backgrounds which might use oklch
            const bgImage = computed.backgroundImage;
            if (bgImage && bgImage.includes('oklch')) {
                el.style.backgroundImage = this.convertOklchInString(bgImage);
            }
        });
    }

    /**
     * Generate a single image format
     */
    async generateSingleImage(format, renderContainer) {
        const { width, height } = this.dimensions[format];

        // Fetch the full-size HTML
        const html = await this.fetchCardHTML(format);

        // Create an iframe to isolate from global CSS (prevents oklch inheritance)
        const iframe = document.createElement('iframe');
        iframe.style.cssText = `width: ${width}px; height: ${height}px; position: absolute; left: 0; top: 0; border: none; visibility: hidden;`;
        renderContainer.appendChild(iframe);

        // Wait for iframe to be ready
        await new Promise(resolve => {
            iframe.onload = resolve;
            // Trigger load for about:blank
            iframe.src = 'about:blank';
        });

        const iframeDoc = iframe.contentDocument || iframe.contentWindow.document;

        // Write minimal HTML with no external CSS
        iframeDoc.open();
        iframeDoc.write(`
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    * { margin: 0; padding: 0; box-sizing: border-box; }
                    body { margin: 0; padding: 0; }
                    img { display: inline-block; }
                </style>
            </head>
            <body>${html}</body>
            </html>
        `);
        iframeDoc.close();

        const wrapper = iframeDoc.body.firstElementChild;

        // Apply the selected background style to the wrapper
        const shareImageContent = iframeDoc.querySelector('.share-image-content');
        if (shareImageContent) {
            this.applyBackground(shareImageContent);
        }

        // Wait for images to load inside iframe
        await this.waitForImages(iframeDoc.body);

        // Use html2canvas to generate the image (no oklch colors in isolated iframe)
        const canvas = await html2canvas(wrapper, {
            width: width,
            height: height,
            scale: 1,
            useCORS: true,
            allowTaint: false,
            backgroundColor: null,
            logging: true,
            windowWidth: width,
            windowHeight: height,
            foreignObjectRendering: true,
            window: iframe.contentWindow,
        });

        // Convert to blob and download
        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));

        // Create safe filename
        const gameName = (this.metadata.game_name || 'platinum')
            .replace(/[^a-z0-9]/gi, '_')
            .substring(0, 30);

        const filename = `platinum_${gameName}_${format}.png`;

        // Download the file
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        // Clean up
        renderContainer.removeChild(iframe);
    }

    /**
     * Wait for all images in container to load and convert to base64
     */
    async waitForImages(container) {
        const images = container.querySelectorAll('img');
        const promises = Array.from(images).map(async (img) => {
            // Wait for image to load first
            if (!img.complete) {
                await new Promise((resolve) => {
                    img.onload = resolve;
                    img.onerror = resolve;
                });
            }

            // Convert to base64 to avoid CORS issues with foreignObjectRendering
            try {
                const dataUrl = await this.imageToBase64(img.src);
                if (dataUrl) {
                    img.src = dataUrl;
                }
            } catch (e) {
                console.warn('Could not convert image to base64:', img.src, e);
            }
        });
        return Promise.all(promises);
    }

    /**
     * Convert an image URL to base64 data URL
     */
    async imageToBase64(url) {
        // Skip if already a data URL
        if (url.startsWith('data:')) {
            return url;
        }

        try {
            const response = await fetch(url, { mode: 'cors' });
            const blob = await response.blob();
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onloadend = () => resolve(reader.result);
                reader.onerror = reject;
                reader.readAsDataURL(blob);
            });
        } catch (e) {
            console.warn('Failed to fetch image for base64 conversion:', url, e);
            return null;
        }
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
