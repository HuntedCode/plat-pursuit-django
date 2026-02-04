/**
 * ShareableManager - Extends ShareImageManager for the My Shareables page.
 * Works directly with EarnedTrophy IDs instead of Notification IDs.
 *
 * Depends on: ShareImageManager (share-image.js), PlatPursuit.API
 */
class ShareableManager extends ShareImageManager {
    constructor(earnedTrophyId, gameName, gameImage) {
        // Parent constructor expects notificationId, but we'll override fetchCardHTML
        super(null, { game_name: gameName, game_image: gameImage });
        this.earnedTrophyId = earnedTrophyId;
        this.gameName = gameName;
    }

    /**
     * Override fetchCardHTML to use the new shareable endpoint
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
     * Override to use game name from constructor for filename
     */
    getFilename(format) {
        // Sanitize game name for filename
        const safeName = this.gameName
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '_')
            .replace(/^_+|_+$/g, '')
            .substring(0, 50);
        return `platinum_${safeName}_${format}.png`;
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
    const manager = new ShareableManager(earnedTrophyId, gameName, gameImage);

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
    const resultsCount = document.getElementById('search-results-count');
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

    function filterPlatinums(query) {
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

        // If no query, show everything
        if (!query) {
            cards.forEach(card => card.classList.remove('hidden'));
            yearGroups.forEach(group => group.classList.remove('hidden'));
            resultsCount?.classList.add('hidden');
            return;
        }

        // Filter cards
        cards.forEach(card => {
            const gameName = card.dataset.searchName || '';
            const matches = gameName.includes(query);
            card.classList.toggle('hidden', !matches);
            if (matches) visibleCount++;
        });

        // Hide year groups with no visible cards
        yearGroups.forEach(group => {
            const visibleCards = group.querySelectorAll('.platinum-card:not(.hidden)');
            group.classList.toggle('hidden', visibleCards.length === 0);
        });

        // Update results count
        if (resultsCount) {
            resultsCount.classList.remove('hidden');
            if (visibleCount === 0) {
                resultsCount.textContent = `No platinums found matching "${query}"`;
            } else if (visibleCount === totalCount) {
                resultsCount.classList.add('hidden');
            } else {
                resultsCount.textContent = `Showing ${visibleCount} of ${totalCount} platinums`;
            }
        }
    }
}

// Initialize search on page load
document.addEventListener('DOMContentLoaded', initPlatinumSearch);

// Export for global access
window.ShareableManager = ShareableManager;
window.openShareModal = openShareModal;
window.initPlatinumSearch = initPlatinumSearch;
