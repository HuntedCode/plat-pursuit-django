/**
 * Profile Card utilities and share manager.
 *
 * This file is loaded on both the Dashboard (for badge selection) and
 * My Shareables (for the full share card manager). The standalone utilities
 * (setBadgeDisplayed) are defined first so they work even when
 * ShareImageManager is not available.
 *
 * Depends on: PlatPursuit.API, PlatPursuit.ToastManager (from utils.js)
 * Optional: ShareImageManager (share-image.js) - only needed for ProfileCardShareManager
 */

window.PlatPursuit = window.PlatPursuit || {};

// ------------------------------------------------------------------
// Standalone utilities (no ShareImageManager dependency)
// ------------------------------------------------------------------

/**
 * Set the displayed badge for the user's profile.
 * Called from the badge_showcase dashboard module.
 */
window.PlatPursuit.setBadgeDisplayed = async function(badgeId) {
    try {
        await PlatPursuit.API.post('/api/v1/badges/displayed/', {
            badge_id: badgeId,
        });

        // Update UI: toggle selection indicators
        document.querySelectorAll('.badge-showcase-item').forEach(function(item) {
            const id = parseInt(item.dataset.badgeId);
            const isSelected = id === badgeId;
            item.classList.toggle('border-secondary', isSelected);
            item.classList.toggle('bg-secondary/10', isSelected);
            item.classList.toggle('border-base-content/10', !isSelected);
            item.classList.toggle('bg-base-300/30', !isSelected);

            // Update checkmark
            const check = item.querySelector('.absolute');
            if (check) check.remove();
            if (isSelected) {
                item.insertAdjacentHTML('beforeend',
                    '<div class="absolute -top-1 -right-1 w-4 h-4 bg-secondary rounded-full flex items-center justify-center">' +
                    '<svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3 text-secondary-content" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg>' +
                    '</div>'
                );
            }
        });

        PlatPursuit.ToastManager.show('Featured badge updated!', 'success');

        // Notify other modules (e.g., profile card preview) that the badge changed
        document.dispatchEvent(new CustomEvent('platpursuit:badge-changed'));
    } catch (err) {
        const errData = await err.response?.json().catch(function() { return null; });
        PlatPursuit.ToastManager.show(
            errData?.error || 'Failed to update displayed badge.',
            'error'
        );
    }
};

// ------------------------------------------------------------------
// ProfileCardShareManager (requires ShareImageManager from share-image.js)
// ------------------------------------------------------------------

if (typeof ShareImageManager !== 'undefined') {

class ProfileCardShareManager extends ShareImageManager {
    constructor() {
        super(null, {});
        // No rating prompt for profile cards
        this.ratingData = { hasRating: true, conceptId: null, isShovelware: false };

        // Landscape only: no portrait format
        this.currentFormat = 'landscape';
        this.dimensions = {
            landscape: { width: 1200, height: 630 },
        };

        // Forum signature state
        this.sigSettings = null;
        this.sigFormat = 'png'; // 'png' or 'svg'
    }

    /**
     * Override renderShareSection to remove the format toggle (landscape only)
     */
    renderShareSection() {
        const html = super.renderShareSection();
        // Remove format buttons from the rendered HTML since we only support landscape
        const container = document.createElement('div');
        container.innerHTML = html;
        const formatBtns = container.querySelectorAll('.format-btn');
        formatBtns.forEach(btn => {
            if (btn.dataset.format === 'portrait') btn.remove();
        });
        // Remove "Generate Both" button if present
        const bothBtn = container.querySelector('#generate-both-btn');
        if (bothBtn) bothBtn.remove();
        // Remove format info text
        const formatInfo = container.querySelector('#format-info');
        if (formatInfo) formatInfo.remove();
        // Replace heading text
        const heading = container.querySelector('h3');
        if (heading) heading.childNodes.forEach(n => {
            if (n.nodeType === 3 && n.textContent.includes('Share Your Achievement')) {
                n.textContent = n.textContent.replace('Share Your Achievement', 'Customize & Download');
            }
        });
        return container.innerHTML;
    }

    /**
     * Override fetchCardHTML to use the profile card endpoint
     */
    async fetchCardHTML(format) {
        const response = await PlatPursuit.API.get(
            `/api/v1/profile-card/html/?image_format=${format}`
        );
        if (response && response.html) {
            return response.html;
        }
        throw new Error('Failed to fetch profile card HTML');
    }

    /**
     * Override PNG endpoint for server-side rendering
     */
    getPngEndpoint(format) {
        return `/api/v1/profile-card/png/?image_format=${format}&theme=${this.currentBackground}`;
    }

    /**
     * Override tracking ID
     */
    getTrackingId() {
        return 'profile-card';
    }

    /**
     * Override generateAndDownload to skip the rating prompt entirely
     */
    async generateAndDownload(format) {
        // Inline download logic since base _doGenerateAndDownload expects
        // a "generate-both-btn" which we removed (landscape only)
        if (this.isDownloading) return;
        this.isDownloading = true;

        const btn = document.getElementById('generate-image-btn');
        const errorEl = document.getElementById('share-error');

        try {
            errorEl?.classList.add('hidden');
            if (btn) { btn.classList.add('loading'); btn.disabled = true; }

            await this.generateSingleImage('landscape');

            try {
                await PlatPursuit.API.post('/api/v1/tracking/site-event/', {
                    event_type: 'share_card_download',
                    object_id: String(this.getTrackingId())
                });
            } catch (trackErr) {
                console.warn('Failed to track download:', trackErr);
            }

            PlatPursuit.ToastManager.success('Image downloaded successfully!');
        } catch (error) {
            console.error('Image generation failed:', error);
            this.showError(error.message || 'Failed to generate image. Please try again.');
            PlatPursuit.ToastManager.error('Failed to generate image');
        } finally {
            this.isDownloading = false;
            if (btn) { btn.classList.remove('loading'); btn.disabled = false; }
        }
    }

    // ------------------------------------------------------------------
    // Forum Signature Management
    // ------------------------------------------------------------------

    /**
     * Load current card settings from the API
     */
    async loadSettings() {
        try {
            this.sigSettings = await PlatPursuit.API.get('/api/v1/profile-card/settings/');
            return this.sigSettings;
        } catch (err) {
            console.error('Failed to load profile card settings:', err);
            return null;
        }
    }

    /**
     * Toggle the public forum signature on/off
     */
    async toggleSig(enabled) {
        try {
            const response = await PlatPursuit.API.post('/api/v1/profile-card/settings/', {
                public_sig_enabled: enabled,
            });
            this.sigSettings = response;

            if (enabled) {
                PlatPursuit.ToastManager.show(
                    'Forum signature enabled! Your sig will update automatically when your stats change.',
                    'success'
                );
            } else {
                PlatPursuit.ToastManager.show('Forum signature disabled.', 'info');
            }

            this._updateSigUI();
            return response;
        } catch (err) {
            const errData = await err.response?.json().catch(() => null);
            PlatPursuit.ToastManager.show(
                errData?.error || 'Failed to update signature settings.',
                'error'
            );
            return null;
        }
    }

    /**
     * Regenerate the public sig token (invalidates old embeds)
     */
    async regenerateToken() {
        try {
            const response = await PlatPursuit.API.post('/api/v1/profile-card/regenerate-token/', {});
            this.sigSettings.public_sig_token = response.public_sig_token;
            PlatPursuit.ToastManager.show(
                'Token regenerated. Old embed URLs are now invalid.',
                'success'
            );
            this._updateSigUI();
            return response;
        } catch (err) {
            const errData = await err.response?.json().catch(() => null);
            PlatPursuit.ToastManager.show(
                errData?.error || 'Failed to regenerate token.',
                'error'
            );
            return null;
        }
    }

    /**
     * Get the current sig URL based on format
     */
    getSigUrl(format) {
        if (!this.sigSettings?.public_sig_token) return '';
        const ext = format || this.sigFormat;
        return `${window.location.origin}/sig/${this.sigSettings.public_sig_token}.${ext}`;
    }

    /**
     * Copy sig URL to clipboard in different formats
     */
    copySigUrl(copyFormat) {
        const url = this.getSigUrl();
        let text = '';

        switch (copyFormat) {
            case 'bbcode':
                text = `[IMG]${url}[/IMG]`;
                break;
            case 'html':
                text = `<img src="${url}" alt="PSN Profile - ${this.sigSettings?.psn_username || 'Player'}" />`;
                break;
            case 'markdown':
                text = `![PSN Profile](${url})`;
                break;
            default:
                text = url;
        }

        navigator.clipboard.writeText(text).then(() => {
            PlatPursuit.ToastManager.show('Copied to clipboard!', 'success');
        }).catch(() => {
            // Fallback: select text in input
            const input = document.getElementById('sig-url-input');
            if (input) {
                input.value = text;
                input.select();
                document.execCommand('copy');
                PlatPursuit.ToastManager.show('Copied to clipboard!', 'success');
            }
        });
    }

    /**
     * Update the forum signature UI elements based on current settings
     */
    _updateSigUI() {
        const sigSection = document.getElementById('sig-management-section');
        if (!sigSection || !this.sigSettings) return;

        const toggle = document.getElementById('sig-enabled-toggle');
        const urlSection = document.getElementById('sig-url-section');
        const previewSection = document.getElementById('sig-preview-section');

        if (toggle) {
            toggle.checked = this.sigSettings.public_sig_enabled;
        }

        if (urlSection) {
            urlSection.style.display = this.sigSettings.public_sig_enabled ? 'block' : 'none';
        }

        if (previewSection) {
            previewSection.style.display = this.sigSettings.public_sig_enabled ? 'block' : 'none';
        }

        // Update URL input
        const urlInput = document.getElementById('sig-url-input');
        if (urlInput) {
            urlInput.value = this.getSigUrl();
        }

        // Update preview image
        this._updateSigPreview();
    }

    /**
     * Update the sig preview image
     */
    _updateSigPreview() {
        const preview = document.getElementById('sig-preview-img');
        if (!preview || !this.sigSettings?.public_sig_enabled) return;

        const url = this.getSigUrl();
        if (url) {
            preview.src = url + '?t=' + Date.now(); // Cache bust
            preview.style.display = 'block';
        }
    }

    /**
     * Initialize the sig management UI event listeners
     */
    initSigManagement() {
        // Toggle
        const toggle = document.getElementById('sig-enabled-toggle');
        if (toggle) {
            toggle.addEventListener('change', () => {
                this.toggleSig(toggle.checked);
            });
        }

        // Format toggle
        document.querySelectorAll('[data-sig-format]').forEach(btn => {
            btn.addEventListener('click', () => {
                this.sigFormat = btn.dataset.sigFormat;
                document.querySelectorAll('[data-sig-format]').forEach(b => {
                    b.classList.toggle('btn-primary', b.dataset.sigFormat === this.sigFormat);
                    b.classList.toggle('btn-ghost', b.dataset.sigFormat !== this.sigFormat);
                });
                this._updateSigUI();
            });
        });

        // Copy buttons
        document.getElementById('copy-url-btn')?.addEventListener('click', () => this.copySigUrl('url'));
        document.getElementById('copy-bbcode-btn')?.addEventListener('click', () => this.copySigUrl('bbcode'));
        document.getElementById('copy-html-btn')?.addEventListener('click', () => this.copySigUrl('html'));
        document.getElementById('copy-markdown-btn')?.addEventListener('click', () => this.copySigUrl('markdown'));

        // Regenerate token
        document.getElementById('regenerate-token-btn')?.addEventListener('click', () => {
            const modal = document.getElementById('regenerate-token-modal');
            if (modal) modal.showModal();
        });

        document.getElementById('confirm-regenerate-btn')?.addEventListener('click', () => {
            const modal = document.getElementById('regenerate-token-modal');
            if (modal) modal.close();
            this.regenerateToken();
        });
    }
}

window.PlatPursuit.ProfileCardShareManager = ProfileCardShareManager;

} // end ShareImageManager guard
