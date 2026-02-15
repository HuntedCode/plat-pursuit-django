/**
 * AZChallengeShareManager - Extends ShareImageManager for A-Z Challenge share cards.
 *
 * Adds a "Featured Game" selector populated from in-progress slots.
 * Depends on: ShareImageManager (share-image.js), PlatPursuit.API
 */
class AZChallengeShareManager extends ShareImageManager {
    constructor(challengeId, challengeName) {
        // No game image metadata (no game art themes)
        super(null, {});
        this.challengeId = challengeId;
        this.challengeName = challengeName;
        this.featuredLetter = '';
        this.inProgressSlots = null; // populated after first fetch
    }

    /**
     * Override: render the share section with an extra "Featured Game" selector
     */
    renderShareSection() {
        return `
            <div class="mt-6 border-t border-base-300 pt-6" id="share-image-section">
                <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z" />
                    </svg>
                    Share Your Challenge
                </h3>

                <!-- Featured Game Selector -->
                <div class="mb-4" id="featured-game-row">
                    <div class="flex items-center gap-2">
                        <label for="featured-game-select" class="text-sm text-base-content/70 whitespace-nowrap">Featured Game:</label>
                        <select id="featured-game-select" class="select select-sm select-bordered bg-base-200 flex-1">
                            <option value="">Loading...</option>
                        </select>
                    </div>
                    <p class="text-xs text-base-content/50 mt-1">Choose which in-progress game to spotlight on the card</p>
                </div>

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
     * Override: add featured game selector listener
     */
    init() {
        super.init();

        // Featured game selector
        document.getElementById('featured-game-select')?.addEventListener('change', (e) => {
            this.featuredLetter = e.target.value;
            this.renderPreview();
        });
    }

    /**
     * Populate the featured game dropdown from server data.
     */
    _populateFeaturedDropdown(inProgressSlots, defaultLetter) {
        const select = document.getElementById('featured-game-select');
        const row = document.getElementById('featured-game-row');
        if (!select) return;

        // Hide the dropdown if no in-progress slots
        if (!inProgressSlots || inProgressSlots.length === 0) {
            if (row) row.classList.add('hidden');
            return;
        }

        // Sort by letter
        const sorted = [...inProgressSlots].sort((a, b) => a.letter.localeCompare(b.letter));

        select.innerHTML = sorted
            .map(s => `<option value="${s.letter}" ${s.letter === defaultLetter ? 'selected' : ''}>${s.letter} - ${s.game_name} (${s.progress}%)</option>`)
            .join('');

        this.featuredLetter = defaultLetter || (sorted[0]?.letter || '');
    }

    /**
     * Override: fetch HTML from the challenge share endpoint
     */
    async fetchCardHTML(format) {
        const letterParam = this.featuredLetter ? `&featured_letter=${this.featuredLetter}` : '';
        const response = await PlatPursuit.API.get(
            `/api/v1/challenges/az/${this.challengeId}/share/html/?image_format=${format}${letterParam}`
        );

        if (response && response.html) {
            // Populate the featured game dropdown on first fetch
            if (this.inProgressSlots === null) {
                this.inProgressSlots = response.in_progress_slots || [];
                this._populateFeaturedDropdown(this.inProgressSlots, response.featured_letter || '');
            }
            return response.html;
        }
        throw new Error('Failed to fetch card HTML');
    }

    /**
     * Override: PNG endpoint for server-side rendering
     */
    getPngEndpoint(format) {
        const letterParam = this.featuredLetter ? `&featured_letter=${this.featuredLetter}` : '';
        return `/api/v1/challenges/az/${this.challengeId}/share/png/?image_format=${format}&theme=${this.currentBackground}${letterParam}`;
    }

    /**
     * Override: generate and download (tracking handled server-side in AZChallengeSharePNGView)
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
     * Override: custom filename for challenge downloads
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

        const name = (this.challengeName || 'az-challenge')
            .replace(/[^a-z0-9]/gi, '_')
            .substring(0, 30);

        const filename = `az_challenge_${name}_${format}.png`;

        const downloadUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(downloadUrl);
    }
}

// Export for global access
window.AZChallengeShareManager = AZChallengeShareManager;
