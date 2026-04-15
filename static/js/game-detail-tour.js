/**
 * GameDetailTourManager - Coach-marks spotlight tour for the game detail page.
 *
 * Dims the page and highlights individual UI sections one at a time,
 * with positioned tooltips explaining each feature. Uses a clip-path
 * cutout on a dark overlay so the target element is visible without
 * z-index manipulation.
 *
 * Depends on: PlatPursuit.API (utils.js)
 */

const COACH_STEPS = [
    {
        target: '#community-stats-card',
        title: 'Community Stats',
        description: 'Every stat box is clickable. Tap any one to see which players earned trophies, who platinumed the game, completion rankings, and more.',
        icon: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
        position: 'top',
    },
    {
        target: '#game-status-section',
        title: 'Help Keep Data Accurate',
        description: 'See something wrong? Hit Report Issue to flag games as delisted, unobtainable, shovelware, or buggy. Your reports directly improve data quality for every hunter.',
        icon: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/>',
        position: 'bottom',
    },
    {
        target: '#community-tabs-section',
        title: 'Ratings, Reviews & Roadmaps',
        description: 'Rate the difficulty, time-to-beat, and fun factor. Share your experience and help other hunters. If a platinum roadmap exists, the step-by-step guide lives here too.',
        icon: '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
        position: 'bottom',
    },
    {
        target: '#quick-add-container',
        title: 'Build Your Lists',
        description: 'Quickly add any game to your personal lists. Track your backlog, plan your next platinum path, or curate themed collections.',
        icon: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
        position: 'top',
    },
];

const CUTOUT_PADDING = 8;  // px padding around the target in the cutout

class GameDetailTourManager {
    constructor() {
        this.overlay = null;
        this.tooltip = null;
        this.currentStep = 0;
        this.totalSteps = COACH_STEPS.length;
        this.isOpen = false;
        this._dismissing = false;
        this._initialized = false;
        this._currentTarget = null;
        this._resizeHandler = null;
        this._keydownHandler = null;
        this._overlayClickHandler = null;
    }

    /**
     * Initialize the tour. Call once on DOMContentLoaded.
     * @param {boolean} autoShow - Whether to auto-open after a delay.
     */
    init(autoShow = false) {
        if (this._initialized) return;

        this.overlay = document.getElementById('coach-overlay');
        this.tooltip = document.getElementById('coach-tooltip');
        if (!this.overlay || !this.tooltip) return;

        this.titleEl = document.getElementById('coach-tooltip-title');
        this.descEl = document.getElementById('coach-tooltip-desc');
        this.svgEl = document.getElementById('coach-tooltip-svg');
        this.counterEl = document.getElementById('coach-step-counter');
        this.prevBtn = document.getElementById('coach-prev-btn');
        this.nextBtn = document.getElementById('coach-next-btn');
        this.skipBtn = document.getElementById('coach-skip-btn');

        this._setupKeyboardHandling();
        this._initialized = true;

        if (autoShow) {
            setTimeout(() => this.open(), 1000);
        }
    }

    /**
     * Open the tour at step 1.
     */
    open() {
        if (!this._initialized) this.init(false);
        if (!this.overlay) return;

        this.currentStep = 0;
        this._dismissing = false;
        this.isOpen = true;

        // Show overlay
        this.overlay.classList.add('visible');
        this.tooltip.classList.add('visible');

        // Bind resize handler
        this._resizeHandler = () => {
            if (this.isOpen) this._positionCurrentStep();
        };
        window.addEventListener('resize', this._resizeHandler);

        this._showStep(0);
    }

    /**
     * Close the tour (no persistence).
     */
    close() {
        this.isOpen = false;
        this.overlay.classList.remove('visible');
        this.tooltip.classList.remove('visible');

        // Remove highlight from current target
        if (this._currentTarget) {
            this._currentTarget.classList.remove('coach-target-highlight');
            this._currentTarget = null;
        }

        // Reset overlay and tooltip positioning state
        this.overlay.style.top = '';
        this.overlay.style.left = '';
        this.overlay.style.width = '';
        this.overlay.style.height = '';
        this.tooltip.classList.remove('positioned');

        if (this._resizeHandler) {
            window.removeEventListener('resize', this._resizeHandler);
            this._resizeHandler = null;
        }
        if (this._keydownHandler) {
            document.removeEventListener('keydown', this._keydownHandler);
        }
        if (this._overlayClickHandler && this.overlay) {
            this.overlay.removeEventListener('click', this._overlayClickHandler);
        }
    }

    /**
     * Go to the next step, or finish if on the last step.
     */
    next() {
        if (this.currentStep >= this.totalSteps - 1) {
            this.dismiss('complete');
            return;
        }
        this._showStep(this.currentStep + 1);
    }

    /**
     * Go to the previous step.
     */
    prev() {
        if (this.currentStep <= 0) return;
        this._showStep(this.currentStep - 1);
    }

    /**
     * Dismiss the tour and persist to backend.
     * @param {'complete'|'skip'} action
     */
    async dismiss(action = 'complete') {
        if (this._dismissing) return;
        this._dismissing = true;

        this.close();

        try {
            await PlatPursuit.API.post('/api/v1/tutorial/game-detail/dismiss/', {
                action: action,
                last_step: this.currentStep + 1,
            });
        } catch (err) {
            console.warn('Game detail tour dismiss failed:', err);
        }
    }

    // ------------------------------------------------------------------
    // Internal methods
    // ------------------------------------------------------------------

    /**
     * Show a specific step: scroll to target, position overlay cutout, show tooltip.
     */
    _showStep(stepIndex) {
        let step = COACH_STEPS[stepIndex];
        if (!step) return;

        let target = document.querySelector(step.target);
        // Skip missing targets (e.g. quick-add not rendered for anonymous).
        // Loop instead of recurse to avoid stack growth if multiple
        // consecutive targets are absent.
        while (!target && stepIndex < this.totalSteps - 1) {
            stepIndex++;
            step = COACH_STEPS[stepIndex];
            target = document.querySelector(step.target);
        }
        if (!target) {
            this.dismiss('complete');
            return;
        }

        // Remove highlight from previous target
        if (this._currentTarget) {
            this._currentTarget.classList.remove('coach-target-highlight');
        }

        this.currentStep = stepIndex;
        this._currentTarget = target;

        // Update content first so tooltip dimensions are correct when we measure
        this._updateContent(step);
        this._updateControls();

        // Scroll target into view, then position once scrolling settles.
        // Use scrollIntoView + a scroll-end detection approach: wait for
        // the target's position to stabilize (no movement for 2 frames).
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });

        let lastTop = -1;
        let stableFrames = 0;
        const settle = () => {
            const currentTop = target.getBoundingClientRect().top;
            if (Math.abs(currentTop - lastTop) < 1) {
                stableFrames++;
            } else {
                stableFrames = 0;
            }
            lastTop = currentTop;

            if (stableFrames >= 3) {
                // Scroll has settled
                this._positionCutout(target);
                void this.tooltip.offsetHeight;
                this._positionTooltip(target, step.position);
                target.classList.add('coach-target-highlight');
            } else {
                requestAnimationFrame(settle);
            }
        };
        requestAnimationFrame(settle);
    }

    /**
     * Reposition the cutout and tooltip for the current step (used on resize).
     */
    _positionCurrentStep() {
        const step = COACH_STEPS[this.currentStep];
        if (!step) return;
        const target = document.querySelector(step.target);
        if (!target) return;

        this._positionCutout(target);
        this._positionTooltip(target, step.position);
    }

    /**
     * Position the overlay as a transparent window over the target, using a
     * massive box-shadow to darken everything else. More reliable than
     * clip-path across browsers.
     */
    _positionCutout(target) {
        const rect = target.getBoundingClientRect();
        const pad = CUTOUT_PADDING;

        this.overlay.style.top = (rect.top - pad) + 'px';
        this.overlay.style.left = (rect.left - pad) + 'px';
        this.overlay.style.width = (rect.width + pad * 2) + 'px';
        this.overlay.style.height = (rect.height + pad * 2) + 'px';
    }

    /**
     * Position the tooltip relative to the target element.
     * @param {Element} target
     * @param {'top'|'bottom'} preferredPosition
     */
    _positionTooltip(target, preferredPosition) {
        const rect = target.getBoundingClientRect();
        const tooltipRect = this.tooltip.getBoundingClientRect();
        const pad = CUTOUT_PADDING;
        const gap = 12; // space between cutout edge and tooltip
        const vw = window.innerWidth;
        const vh = window.innerHeight;

        let top, left;

        if (preferredPosition === 'top') {
            // Place tooltip above the target
            top = rect.top - pad - gap - tooltipRect.height;
            // If it would go off-screen, flip to bottom
            if (top < 8) {
                top = rect.bottom + pad + gap;
            }
        } else {
            // Place tooltip below the target
            top = rect.bottom + pad + gap;
            // If it would go off-screen, flip to top
            if (top + tooltipRect.height > vh - 8) {
                top = rect.top - pad - gap - tooltipRect.height;
            }
        }

        // Horizontally center on the target, clamped to viewport
        left = rect.left + (rect.width / 2) - (tooltipRect.width / 2);
        left = Math.max(8, Math.min(left, vw - tooltipRect.width - 8));

        this.tooltip.style.top = top + 'px';
        this.tooltip.style.left = left + 'px';

        // Enable smooth repositioning after the first placement
        if (!this.tooltip.classList.contains('positioned')) {
            requestAnimationFrame(() => this.tooltip.classList.add('positioned'));
        }
    }

    /**
     * Update tooltip content for the current step.
     */
    _updateContent(step) {
        if (this.titleEl) this.titleEl.textContent = step.title;
        if (this.descEl) this.descEl.textContent = step.description;
        if (this.svgEl) this.svgEl.innerHTML = step.icon;
    }

    /**
     * Update navigation controls for the current step.
     */
    _updateControls() {
        const step = this.currentStep;

        if (this.counterEl) {
            this.counterEl.textContent = `${step + 1} / ${this.totalSteps}`;
        }

        if (this.prevBtn) {
            this.prevBtn.classList.toggle('invisible', step === 0);
        }

        if (this.nextBtn) {
            if (step === this.totalSteps - 1) {
                this.nextBtn.innerHTML = `
                    Done
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>
                    </svg>`;
            } else {
                this.nextBtn.innerHTML = `
                    Next
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/>
                    </svg>`;
            }
        }

        if (this.skipBtn) {
            this.skipBtn.textContent = step === this.totalSteps - 1 ? 'Close' : 'Skip';
        }
    }

    /**
     * Keyboard navigation.
     */
    _setupKeyboardHandling() {
        this._keydownHandler = (e) => {
            if (!this.isOpen) return;

            switch (e.key) {
                case 'ArrowRight':
                    e.preventDefault();
                    this.next();
                    break;
                case 'ArrowLeft':
                    e.preventDefault();
                    this.prev();
                    break;
                case 'Escape':
                    e.preventDefault();
                    this.dismiss('skip');
                    break;
            }
        };
        document.addEventListener('keydown', this._keydownHandler);

        // Clicking the overlay (outside the cutout) dismisses
        this._overlayClickHandler = () => {
            if (this.isOpen) this.dismiss('skip');
        };
        this.overlay.addEventListener('click', this._overlayClickHandler);
    }
}

// Singleton instance
const gameDetailTourInstance = new GameDetailTourManager();

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.GameDetailTourManager = gameDetailTourInstance;
