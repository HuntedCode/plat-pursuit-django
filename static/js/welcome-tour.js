/**
 * WelcomeTourManager - 4-step Welcome Tour for new PlatPursuit users.
 *
 * Auto-shows once on the first dashboard load after PSN linking + sync.
 * Can be re-triggered from the avatar dropdown "Replay Tour" button.
 *
 * Depends on: PlatPursuit.API (utils.js)
 */
class WelcomeTourManager {
    constructor() {
        this.modal = null;
        this.steps = [];
        this.dots = [];
        this.currentStep = 1;
        this.totalSteps = 4;
        this.isOpen = false;
        this.isTransitioning = false;
        this._initialized = false;
    }

    /**
     * Initialize the tour manager. Call once on DOMContentLoaded.
     * @param {boolean} autoShow - Whether to auto-open the tour after a delay.
     */
    init(autoShow = false) {
        if (this._initialized) return;

        this.modal = document.getElementById('welcome-tour-modal');
        if (!this.modal) return;

        this.steps = Array.from(this.modal.querySelectorAll('.tour-step'));
        this.dots = Array.from(this.modal.querySelectorAll('.tour-dot'));
        this.prevBtn = document.getElementById('tour-prev-btn');
        this.nextBtn = document.getElementById('tour-next-btn');
        this.skipBtn = document.getElementById('tour-skip-btn');
        this.stepCounter = document.getElementById('tour-step-counter');

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
        if (!this.modal) this.init(false);
        if (!this.modal) return;

        this.currentStep = 1;
        this._dismissing = false;
        this._renderStep(1, 'none');
        this.modal.showModal();
        this.isOpen = true;

        // Trigger entrance animation on cards
        requestAnimationFrame(() => {
            this._animateCardsIn(1);
        });
    }

    /**
     * Close the modal without persisting (used internally).
     */
    close() {
        if (!this.modal) return;
        this.modal.close();
        this.isOpen = false;
    }

    /**
     * Navigate to a specific step.
     * @param {number} step - Step number (1-4).
     */
    goToStep(step) {
        if (this.isTransitioning) return;
        if (step < 1 || step > this.totalSteps || step === this.currentStep) return;

        const direction = step > this.currentStep ? 'forward' : 'backward';
        this._transitionTo(step, direction);
    }

    /**
     * Go to the next step, or finish if on the last step.
     */
    next() {
        if (this.isTransitioning) return;

        if (this.currentStep >= this.totalSteps) {
            this.dismiss('complete');
            return;
        }

        this._transitionTo(this.currentStep + 1, 'forward');
    }

    /**
     * Go to the previous step.
     */
    prev() {
        if (this.isTransitioning) return;
        if (this.currentStep <= 1) return;

        this._transitionTo(this.currentStep - 1, 'backward');
    }

    /**
     * Dismiss the tour (skip or complete) and persist to backend.
     * Guarded against double-fire (e.g. Escape key + dialog close event).
     * @param {'complete'|'skip'} action
     */
    async dismiss(action = 'complete') {
        if (this._dismissing) return;
        this._dismissing = true;

        this.close();

        // On complete (not skip), briefly highlight the real navbar and sub-nav
        if (action === 'complete') {
            this._highlightRealNav();
        }

        try {
            await PlatPursuit.API.post('/api/v1/tutorial/welcome/dismiss/', {
                action: action,
                last_step: this.currentStep,
            });
        } catch (err) {
            // Best-effort persistence. If the API call fails, the tour
            // will re-show next page load, which is acceptable.
            console.warn('Welcome tour dismiss failed:', err);
        }
    }

    // ------------------------------------------------------------------
    // Internal methods
    // ------------------------------------------------------------------

    /**
     * Transition between steps with slide animation.
     */
    _transitionTo(targetStep, direction) {
        this.isTransitioning = true;

        const currentEl = this.steps[this.currentStep - 1];
        const targetEl = this.steps[targetStep - 1];

        // Determine slide offsets
        const outX = direction === 'forward' ? '-30px' : '30px';
        const inX = direction === 'forward' ? '30px' : '-30px';

        // Slide out current
        currentEl.style.transition = 'opacity 0.25s ease, transform 0.25s ease';
        currentEl.style.opacity = '0';
        currentEl.style.transform = `translateX(${outX})`;

        setTimeout(() => {
            currentEl.classList.add('hidden');
            currentEl.style.transition = '';
            currentEl.style.opacity = '';
            currentEl.style.transform = '';

            // Prepare target off-screen
            targetEl.style.opacity = '0';
            targetEl.style.transform = `translateX(${inX})`;
            targetEl.classList.remove('hidden');

            // Force reflow before animating in
            void targetEl.offsetHeight;

            targetEl.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
            targetEl.style.opacity = '1';
            targetEl.style.transform = 'translateX(0)';

            this.currentStep = targetStep;
            this._updateControls();
            this._animateCardsIn(targetStep);

            setTimeout(() => {
                targetEl.style.transition = '';
                this.isTransitioning = false;
            }, 300);
        }, 250);
    }

    /**
     * Render a specific step immediately (no animation). Used for initial open.
     */
    _renderStep(step, _mode) {
        this.steps.forEach((el, i) => {
            if (i === step - 1) {
                el.classList.remove('hidden');
                el.style.opacity = '1';
                el.style.transform = '';
            } else {
                el.classList.add('hidden');
            }
        });
        this.currentStep = step;
        this._updateControls();
    }

    /**
     * Update navigation controls (dots, buttons, counter) for the current step.
     */
    _updateControls() {
        const step = this.currentStep;
        const stepEl = this.steps[step - 1];
        const accent = stepEl ? stepEl.dataset.accent : 'var(--color-primary)';

        // Progress dots
        this.dots.forEach((dot, i) => {
            const isActive = i === step - 1;
            dot.classList.toggle('active', isActive);
            if (isActive) {
                dot.style.setProperty('--dot-color', accent);
            }
        });

        // Step counter
        if (this.stepCounter) {
            this.stepCounter.textContent = `Step ${step} of ${this.totalSteps}`;
        }

        // Back button: invisible (not hidden) on step 1 to prevent layout shift
        if (this.prevBtn) {
            this.prevBtn.classList.toggle('invisible', step === 1);
        }

        // Next button: changes to "Get Started" on last step
        if (this.nextBtn) {
            if (step === this.totalSteps) {
                this.nextBtn.innerHTML = `
                    Get Started
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>
                    </svg>`;
            } else {
                this.nextBtn.innerHTML = `
                    Next
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/>
                    </svg>`;
            }
        }

        // Skip button: changes to "Close" on last step
        if (this.skipBtn) {
            this.skipBtn.textContent = step === this.totalSteps ? 'Close' : 'Skip';
        }
    }

    /**
     * Stagger-animate step elements into view: nav path first, then cards.
     */
    _animateCardsIn(step) {
        const stepEl = this.steps[step - 1];
        if (!stepEl) return;

        let delay = 80;

        // Animate the nav path (navbar mockup + sub-nav preview)
        const navPath = stepEl.querySelector('.tour-nav-path');
        if (navPath) {
            navPath.style.opacity = '0';
            navPath.style.transform = 'translateY(8px)';
            navPath.style.transition = 'none';
            setTimeout(() => {
                navPath.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
                navPath.style.opacity = '1';
                navPath.style.transform = 'translateY(0)';
            }, delay);
            delay += 200;
        }

        // Stagger-animate feature cards
        const cards = stepEl.querySelectorAll('.tour-feature-card');
        cards.forEach((card, i) => {
            card.style.opacity = '0';
            card.style.transform = 'translateY(10px)';
            card.style.transition = 'none';

            setTimeout(() => {
                card.style.transition = 'opacity 0.25s ease, transform 0.25s ease';
                card.style.opacity = '1';
                card.style.transform = 'translateY(0)';
            }, delay + i * 60);
        });
    }

    /**
     * Dramatic post-tour spotlight: darkens the page, highlights the navbar
     * and sub-nav with elevated z-index, and shows a floating callout.
     * Clicks anywhere dismiss the spotlight.
     */
    _highlightRealNav() {
        window.scrollTo({ top: 0, behavior: 'smooth' });

        setTimeout(() => {
            const navbar = document.querySelector('.navbar');
            const subnav = document.querySelector('.hub-subnav');
            if (!navbar) return;

            // Create dark overlay
            const overlay = document.createElement('div');
            overlay.className = 'tour-spotlight-overlay';
            document.body.appendChild(overlay);

            // Elevate navbar + sub-nav above the overlay
            if (navbar) navbar.classList.add('tour-spotlight-target');
            if (subnav) subnav.classList.add('tour-spotlight-target');

            // Create floating callout below the sub-nav
            const callout = document.createElement('div');
            callout.className = 'tour-spotlight-callout';
            callout.innerHTML = `
                <div class="tour-spotlight-callout-inner">
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/>
                    </svg>
                    <span>Start your journey here!</span>
                </div>`;
            document.body.appendChild(callout);

            // Position the callout below the sub-nav (or navbar if no sub-nav)
            const anchor = subnav || navbar;
            const rect = anchor.getBoundingClientRect();
            callout.style.top = (rect.bottom + 12) + 'px';

            // Fade in
            requestAnimationFrame(() => {
                overlay.classList.add('visible');
                callout.classList.add('visible');
            });

            // Dismiss on click anywhere
            const cleanup = () => {
                overlay.classList.remove('visible');
                callout.classList.remove('visible');
                if (navbar) navbar.classList.remove('tour-spotlight-target');
                if (subnav) subnav.classList.remove('tour-spotlight-target');
                setTimeout(() => {
                    overlay.remove();
                    callout.remove();
                }, 300);
                document.removeEventListener('click', cleanup);
                document.removeEventListener('keydown', cleanup);
            };

            // Auto-dismiss after 5 seconds, or on click/keypress
            setTimeout(cleanup, 5000);
            document.addEventListener('click', cleanup, { once: true });
            document.addEventListener('keydown', cleanup, { once: true });
        }, 400);
    }

    /**
     * Handle keyboard navigation within the tour.
     */
    _setupKeyboardHandling() {
        this.modal.addEventListener('keydown', (e) => {
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
        });

        // Handle the dialog's native close event (e.g., backdrop click).
        // Guard with _dismissing so a backdrop click after dismiss('complete')
        // doesn't re-fire with the wrong action string.
        this.modal.addEventListener('close', () => {
            if (this.isOpen && !this._dismissing) {
                this.isOpen = false;
                this.dismiss('skip');
            }
        });
    }
}

// Singleton instance
const welcomeTourInstance = new WelcomeTourManager();

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.WelcomeTourManager = welcomeTourInstance;
