/**
 * Trophy Comment Manager
 * Handles trophy-level comment sections on game detail page
 * Instantiates CommentSystem for each trophy on demand
 */

class TrophyCommentManager {
    constructor() {
        this.activeCommentSystems = new Map(); // trophy_id -> CommentSystem instance
        this.init();
    }

    init() {
        this.setupToggleListeners();
    }

    setupToggleListeners() {
        // Event delegation for trophy comment toggle buttons
        document.addEventListener('click', (e) => {
            const toggleBtn = e.target.closest('.trophy-comment-toggle');
            if (!toggleBtn) return;

            e.preventDefault();
            const trophyId = toggleBtn.dataset.trophyId;
            this.toggleTrophyComments(trophyId);
        });
    }

    toggleTrophyComments(trophyId) {
        const section = document.getElementById(`trophy-comment-section-${trophyId}`);
        const toggleBtn = document.querySelector(`.trophy-comment-toggle[data-trophy-id="${trophyId}"]`);
        const chevron = toggleBtn.querySelector('.trophy-toggle-chevron');

        if (!section) return;

        const isHidden = section.classList.contains('hidden');

        if (isHidden) {
            // Expand
            section.classList.remove('hidden');
            toggleBtn.setAttribute('aria-expanded', 'true');
            chevron.style.transform = 'rotate(0deg)';

            // Initialize CommentSystem for this trophy if not already done
            if (!this.activeCommentSystems.has(trophyId)) {
                this.initializeCommentSystem(trophyId);
            }
        } else {
            // Collapse
            section.classList.add('hidden');
            toggleBtn.setAttribute('aria-expanded', 'false');
            chevron.style.transform = 'rotate(-90deg)';
        }
    }

    initializeCommentSystem(trophyId) {
        const section = document.getElementById(`trophy-comment-section-${trophyId}`);
        if (!section) return;

        const conceptId = section.dataset.conceptId;
        const trophyIdValue = section.dataset.trophyId;

        // Create new CommentSystem instance for this trophy
        const commentSystem = new CommentSystem(`trophy-comment-section-${trophyId}`);

        // Store reference
        this.activeCommentSystems.set(trophyId, commentSystem);

        console.log(`Initialized comment system for trophy: ${trophyId}`);
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new TrophyCommentManager();
});
