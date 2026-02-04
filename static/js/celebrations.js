/**
 * CelebrationManager - Shared celebration effects for PlatPursuit
 * Handles confetti animations and celebration toasts
 * Depends on: PlatPursuit.ToastManager
 */
const CelebrationManager = {
    confettiLoaded: false,
    confettiLoadPromise: null,
    colors: ['#f472b6', '#a855f7', '#3b82f6', '#22c55e', '#eab308'],

    /**
     * Dynamically load the confetti library
     * @returns {Promise} Resolves when confetti is ready
     */
    loadConfetti() {
        // Already loaded
        if (this.confettiLoaded && typeof confetti === 'function') {
            return Promise.resolve();
        }

        // Loading in progress
        if (this.confettiLoadPromise) {
            return this.confettiLoadPromise;
        }

        // Start loading
        this.confettiLoadPromise = new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/canvas-confetti@1.9.3/dist/confetti.browser.min.js';
            script.onload = () => {
                this.confettiLoaded = true;
                resolve();
            };
            script.onerror = () => {
                this.confettiLoadPromise = null;
                reject(new Error('Failed to load confetti library'));
            };
            document.head.appendChild(script);
        });

        return this.confettiLoadPromise;
    },

    /**
     * Fire confetti from both sides of the screen
     * @param {number} duration - Animation duration in ms (default 3000)
     */
    fireSideConfetti(duration = 3000) {
        if (typeof confetti !== 'function') return;

        const end = Date.now() + duration;

        const frame = () => {
            // Left side burst
            confetti({
                particleCount: 3,
                angle: 60,
                spread: 55,
                origin: { x: 0, y: 0.6 },
                colors: this.colors
            });
            // Right side burst
            confetti({
                particleCount: 3,
                angle: 120,
                spread: 55,
                origin: { x: 1, y: 0.6 },
                colors: this.colors
            });

            if (Date.now() < end) {
                requestAnimationFrame(frame);
            }
        };

        frame();
    },

    /**
     * Celebrate a platinum trophy achievement
     * @param {string} gameName - Name of the game
     * @param {number} count - Number of platinum notifications
     */
    async celebratePlatinum(gameName, count = 1) {
        try {
            await this.loadConfetti();
            this.fireSideConfetti(3000);

            // Show appropriate toast message
            let message;
            if (count === 1) {
                message = `Congratulations on your platinum for ${gameName}!`;
            } else {
                message = `Congratulations! You earned ${count} new platinum trophies!`;
            }

            PlatPursuit.ToastManager.show(message, 'success');
        } catch (error) {
            console.error('Failed to show platinum celebration:', error);
        }
    },

    /**
     * Celebrate checklist completion (backwards compatible)
     */
    async celebrateChecklistComplete() {
        try {
            await this.loadConfetti();
            this.fireSideConfetti(3000);
            PlatPursuit.ToastManager.show('Checklist complete! Great job!', 'success');
        } catch (error) {
            console.error('Failed to show checklist celebration:', error);
        }
    }
};

// Export to PlatPursuit namespace
window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.CelebrationManager = CelebrationManager;
