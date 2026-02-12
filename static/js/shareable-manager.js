/**
 * ShareableManager - Extends ShareImageManager for the My Shareables page.
 * Works directly with EarnedTrophy IDs instead of Notification IDs.
 *
 * Depends on: ShareImageManager (share-image.js), PlatPursuit.API
 */
class ShareableManager extends ShareImageManager {
    constructor(earnedTrophyId, gameName, gameImage, conceptBgUrl) {
        // Parent constructor expects notificationId, but we override getPngEndpoint/fetchCardHTML
        super(null, { game_name: gameName, game_image: gameImage, concept_bg_url: conceptBgUrl });
        this.earnedTrophyId = earnedTrophyId;
        this.gameName = gameName;
    }

    /**
     * Override fetchCardHTML to use the shareable endpoint (for preview)
     */
    async fetchCardHTML(format) {
        const response = await PlatPursuit.API.get(
            `/api/v1/shareables/platinum/${this.earnedTrophyId}/html/?image_format=${format}`
        );

        if (response && response.html) {
            return response.html;
        }
        throw new Error('Failed to fetch card HTML');
    }

    /**
     * Override to use shareable PNG endpoint for server-side rendering
     */
    getPngEndpoint(format) {
        return `/api/v1/shareables/platinum/${this.earnedTrophyId}/png/?image_format=${format}&theme=${this.currentBackground}`;
    }

    /**
     * Override to track earned trophy ID instead of notification ID
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

            // Track download (using earnedTrophyId)
            try {
                await PlatPursuit.API.post('/api/v1/tracking/site-event/', {
                    event_type: 'share_card_download',
                    object_id: String(this.earnedTrophyId)
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
}

/**
 * Open the share modal for a platinum trophy
 * Called from the platinum grid cards
 */
function openShareModal(cardElement) {
    const earnedTrophyId = cardElement.dataset.earnedTrophyId;
    const gameName = cardElement.dataset.gameName;
    const gameImage = cardElement.dataset.gameImage;
    const conceptBgUrl = cardElement.dataset.conceptBgUrl || '';

    if (!earnedTrophyId || !gameName) {
        console.error('Missing earned trophy data');
        return;
    }

    const modal = document.getElementById('share-modal');
    const modalContent = document.getElementById('share-modal-content');
    const modalTitle = document.getElementById('share-modal-title');

    if (!modal || !modalContent || !modalTitle) {
        console.error('Share modal elements not found');
        return;
    }

    // Update modal title
    modalTitle.textContent = `Share: ${gameName}`;

    // Show loading state
    modalContent.innerHTML = `
        <div class="flex justify-center items-center py-12">
            <span class="loading loading-spinner loading-lg"></span>
        </div>
    `;

    // Open modal
    modal.showModal();

    // Create manager and render share section
    const manager = new ShareableManager(earnedTrophyId, gameName, gameImage, conceptBgUrl);

    // Render the share section UI
    modalContent.innerHTML = manager.renderShareSection();

    // Initialize event listeners
    manager.init();
}

/**
 * Initialize platinum search functionality
 */
function initPlatinumSearch() {
    const searchInput = document.getElementById('platinum-search');
    const clearBtn = document.getElementById('clear-search');
    const jumpToYear = document.getElementById('jump-to-year');

    if (!searchInput) return;

    let debounceTimer;

    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.trim();

        // Update clear button visibility immediately (no debounce)
        if (clearBtn) {
            if (query) {
                clearBtn.classList.remove('hidden');
                clearBtn.classList.add('flex');
            } else {
                clearBtn.classList.add('hidden');
                clearBtn.classList.remove('flex');
            }
        }

        // Debounce the actual filtering
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            filterPlatinums(query.toLowerCase());
        }, 150);
    });

    // Clear button
    clearBtn?.addEventListener('click', () => {
        searchInput.value = '';
        filterPlatinums('');
        searchInput.focus();
    });

    // Keyboard shortcut: Escape to clear
    searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            searchInput.value = '';
            filterPlatinums('');
        }
    });

    // Jump to year dropdown
    jumpToYear?.addEventListener('change', (e) => {
        const year = e.target.value;
        if (!year) return;

        // Clear any active search first
        searchInput.value = '';
        filterPlatinums('');

        // Find the year group and scroll to it
        const yearGroup = document.querySelector(`.year-group[data-year="${year}"]`);
        if (yearGroup) {
            yearGroup.scrollIntoView({ behavior: 'smooth', block: 'start' });
            // Add a brief highlight effect
            yearGroup.classList.add('ring-2', 'ring-primary', 'ring-offset-2', 'ring-offset-base-100');
            setTimeout(() => {
                yearGroup.classList.remove('ring-2', 'ring-primary', 'ring-offset-2', 'ring-offset-base-100');
            }, 1500);
        }

        // Reset dropdown to placeholder
        e.target.value = '';
    });

    function filterPlatinums() {
        // Use combined filter function
        applyAllFilters();
    }
}

/**
 * Apply all filters (search + shovelware) together
 */
function applyAllFilters() {
    const searchInput = document.getElementById('platinum-search');
    const clearBtn = document.getElementById('clear-search');
    const resultsCount = document.getElementById('search-results-count');
    const shovelwareToggle = document.getElementById('hide-shovelware-toggle');

    const query = searchInput?.value?.trim().toLowerCase() || '';
    const hideShovelware = shovelwareToggle?.checked || false;

    const cards = document.querySelectorAll('.platinum-card');
    const yearGroups = document.querySelectorAll('.year-group');
    let visibleCount = 0;
    let totalCount = cards.length;

    // Show/hide clear button
    if (clearBtn) {
        if (query) {
            clearBtn.classList.remove('hidden');
            clearBtn.classList.add('flex');
        } else {
            clearBtn.classList.add('hidden');
            clearBtn.classList.remove('flex');
        }
    }

    // Apply both filters to each card
    cards.forEach(card => {
        const gameName = card.dataset.searchName || '';
        const isShovelware = card.dataset.isShovelware === 'true';

        const matchesSearch = !query || gameName.includes(query);
        const matchesShovelware = !hideShovelware || !isShovelware;

        const shouldShow = matchesSearch && matchesShovelware;
        card.classList.toggle('hidden', !shouldShow);

        if (shouldShow) visibleCount++;
    });

    // Hide year groups with no visible cards
    yearGroups.forEach(group => {
        const visibleCards = group.querySelectorAll('.platinum-card:not(.hidden)');
        group.classList.toggle('hidden', visibleCards.length === 0);
    });

    // Update results count
    const hasActiveFilter = query || hideShovelware;
    if (resultsCount) {
        if (!hasActiveFilter) {
            resultsCount.classList.add('hidden');
        } else if (visibleCount === 0) {
            resultsCount.classList.remove('hidden');
            if (query && hideShovelware) {
                resultsCount.textContent = `No platinums found matching "${query}" (shovelware hidden)`;
            } else if (query) {
                resultsCount.textContent = `No platinums found matching "${query}"`;
            } else {
                resultsCount.textContent = `All platinums are shovelware (hidden)`;
            }
        } else if (visibleCount === totalCount) {
            resultsCount.classList.add('hidden');
        } else {
            resultsCount.classList.remove('hidden');
            resultsCount.textContent = `Showing ${visibleCount} of ${totalCount} platinums`;
        }
    }
}

/**
 * Initialize shovelware filter toggle
 */
function initShovelwareFilter() {
    const toggle = document.getElementById('hide-shovelware-toggle');
    if (!toggle) return;

    // Load saved preference from localStorage
    const savedPref = localStorage.getItem('hideShareableShovelware');
    if (savedPref === 'true') {
        toggle.checked = true;
        // Apply filter after a brief delay to ensure DOM is ready
        setTimeout(() => applyAllFilters(), 0);
    }

    toggle.addEventListener('change', (e) => {
        const hide = e.target.checked;
        localStorage.setItem('hideShareableShovelware', hide);
        applyAllFilters();
    });
}

// Initialize filters on page load
document.addEventListener('DOMContentLoaded', () => {
    initPlatinumSearch();
    initShovelwareFilter();
});

// Export for global access
window.ShareableManager = ShareableManager;
window.openShareModal = openShareModal;
window.initPlatinumSearch = initPlatinumSearch;
window.initShovelwareFilter = initShovelwareFilter;
window.applyAllFilters = applyAllFilters;
