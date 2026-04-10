/**
 * WelcomeTourManager - 6-step Welcome Tour for new PlatPursuit users.
 *
 * Steps 1-2 are intro slides (welcome splash + navigation explainer).
 * Steps 3-6 walk through the four hubs (Dashboard, Browse, Community,
 * My Pursuit) with real mobile chrome clones highlighting each hub.
 *
 * Auto-shows once on the first dashboard load after PSN linking + sync.
 * Can be re-triggered from the avatar dropdown "Replay Tour" button.
 *
 * Depends on: PlatPursuit.API (utils.js)
 */

// Sub-nav items per hub step (steps 3-6). Used to rebuild the sub-nav
// clone when transitioning between hub steps on mobile.
const TOUR_SUBNAV_ITEMS = {
    3: ['Dashboard', 'My Stats', 'Shareables', 'Recap'],
    4: ['Games', 'Trophies', 'Companies', 'Genres'],
    5: ['Hub', 'Profiles', 'Reviews', 'Challenges'],
    6: ['Badges', 'Milestones', 'Titles'],
};

// Steps 3-6 map to mobile tab bar indices 0-3
const TOUR_TAB_INDEX = { 3: 0, 4: 1, 5: 2, 6: 3 };

class WelcomeTourManager {
    constructor() {
        this.modal = null;
        this.steps = [];
        this.dots = [];
        this.currentStep = 1;
        this.totalSteps = 6;
        this.isOpen = false;
        this.isTransitioning = false;
        this._initialized = false;
        this._tabBarClone = null;
        this._subnavClone = null;
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
        this._setupSwipeHandling();
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

        // Clone the mobile tab bar into the modal (hidden on intro steps,
        // highlighted on hub steps 3-6). Sub-nav clone created per step.
        this._createTabBarClone();
        this._updateChromeForStep(1);

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
        this._removePopovers();
        this._removeTabBarClone();
        this._removeSubnavClone();
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

        // Update mobile chrome clones (tab bar highlight + sub-nav)
        this._updateChromeForStep(step);
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
     * Clone the mobile tab bar into the modal element so it renders inside
     * the modal's stacking context (above the backdrop blur). Only on <lg
     * viewports where the real tab bar is visible.
     */
    _createTabBarClone() {
        this._removeTabBarClone();
        if (window.innerWidth >= 1024) return;

        const realTabbar = document.querySelector('.mobile-tabbar');
        if (!realTabbar) return;

        const clone = realTabbar.cloneNode(true);
        clone.classList.add('tour-tabbar-clone', 'visible');
        clone.classList.remove('lg:hidden');
        clone.removeAttribute('role');
        clone.removeAttribute('aria-label');

        // Strip the page's active state so only the tour highlight shows,
        // and disable link clicks (the clone is for display only).
        clone.querySelectorAll('.mobile-tabbar-item').forEach(item => {
            item.classList.remove('active');
        });
        clone.querySelectorAll('a').forEach(a => {
            a.addEventListener('click', e => e.preventDefault());
            a.removeAttribute('href');
            a.style.cursor = 'default';
        });

        this.modal.appendChild(clone);
        this._tabBarClone = clone;
    }

    /**
     * Orchestrate mobile chrome for the current step:
     * - Step 1 (welcome): no highlights, no clones
     * - Step 2 (navigation): highlight ALL tab bar items + show sub-nav
     *   clone with popovers explaining both chrome elements
     * - Steps 3-6 (hubs): highlight the matching tab, show sub-nav clone
     */
    _updateChromeForStep(step) {
        if (window.innerWidth >= 1024) return;

        const stepEl = this.steps[step - 1];
        const accent = stepEl ? stepEl.dataset.accent : 'var(--color-primary)';

        // Always clear popovers first
        this._removePopovers();

        // Tab bar: clear all highlights
        if (this._tabBarClone) {
            this._tabBarClone.querySelectorAll('.mobile-tabbar-item').forEach(el => {
                el.classList.remove('tour-highlight');
                el.style.removeProperty('--tour-tab-color');
            });
        }

        if (step === 1) {
            // Welcome splash: no chrome
            this._removeSubnavClone();
        } else if (step === 2) {
            // Navigation intro: highlight each tab bar item with its hub's
            // accent color so the user sees the color coding before the hub slides.
            // Order: Dashboard=primary, Browse=accent, Community=success, My Pursuit=secondary
            const tabColors = [
                'var(--color-primary)',
                'var(--color-accent)',
                'var(--color-success)',
                'var(--color-secondary)',
            ];
            if (this._tabBarClone) {
                const tabItems = this._tabBarClone.querySelectorAll('.mobile-tabbar-item');
                tabItems.forEach((el, i) => {
                    el.classList.add('tour-highlight');
                    el.style.setProperty('--tour-tab-color', tabColors[i] || accent);
                });
            }
            this._createSubnavClone(3, accent); // Show dashboard sub-nav as example

            // Add popovers
            this._createPopover(
                'bottom',
                'These are your four main sections',
                accent
            );
            this._createPopover(
                'top',
                'Each section has sub-pages here',
                accent
            );
        } else {
            // Hub steps 3-6: highlight specific tab + show that hub's sub-nav
            if (this._tabBarClone) {
                const tabItems = this._tabBarClone.querySelectorAll('.mobile-tabbar-item');
                const tabIndex = TOUR_TAB_INDEX[step];
                if (tabIndex !== undefined && tabItems[tabIndex]) {
                    tabItems[tabIndex].classList.add('tour-highlight');
                    tabItems[tabIndex].style.setProperty('--tour-tab-color', accent);
                }
            }
            this._createSubnavClone(step, accent);
        }
    }

    /**
     * Create a small popover label attached to the top or bottom chrome clone.
     * @param {'top'|'bottom'} position - 'top' for sub-nav, 'bottom' for tab bar
     * @param {string} text - Label text
     * @param {string} accent - CSS color value
     */
    _createPopover(position, text, accent) {
        const popover = document.createElement('div');
        popover.className = 'tour-chrome-popover';
        popover.style.setProperty('--popover-color', accent);

        if (position === 'bottom') {
            // Above the tab bar (pointing down)
            const tabbar = this._tabBarClone;
            if (!tabbar) return;
            const tabbarHeight = tabbar.offsetHeight || 56;
            popover.style.bottom = (tabbarHeight + 8) + 'px';
            popover.innerHTML = `
                <div class="tour-chrome-popover-body">${text}</div>
                <svg width="12" height="8" viewBox="0 0 12 8" class="tour-chrome-popover-arrow down"><path d="M6 8L0 0h12z" fill="var(--popover-color)"/></svg>`;
        } else {
            // Below the sub-nav (pointing up). Defer to next frame so
            // the clone has its final rendered height, then measure.
            popover.style.top = '60px'; // safe default while measuring
            requestAnimationFrame(() => {
                const subnav = this._subnavClone;
                if (subnav) {
                    const rect = subnav.getBoundingClientRect();
                    popover.style.top = (rect.bottom + 6) + 'px';
                }
            });
            popover.innerHTML = `
                <svg width="12" height="8" viewBox="0 0 12 8" class="tour-chrome-popover-arrow up"><path d="M6 0L12 8H0z" fill="var(--popover-color)"/></svg>
                <div class="tour-chrome-popover-body">${text}</div>`;
        }

        this.modal.appendChild(popover);
        if (!this._popovers) this._popovers = [];
        this._popovers.push(popover);
    }

    /**
     * Remove all chrome popovers.
     */
    _removePopovers() {
        if (this._popovers) {
            this._popovers.forEach(p => p.remove());
            this._popovers = [];
        }
    }

    /**
     * Remove the cloned tab bar from the modal.
     */
    _removeTabBarClone() {
        if (this._tabBarClone) {
            this._tabBarClone.remove();
            this._tabBarClone = null;
        }
    }

    /**
     * Clone the real sub-nav into the modal, replacing its items with
     * the current step's hub sub-pages. Positioned fixed at top to
     * overlap the real sub-nav exactly.
     */
    _createSubnavClone(step, accent) {
        this._removeSubnavClone();
        if (window.innerWidth >= 1024) return;

        const realSubnav = document.querySelector('.hub-subnav');
        if (!realSubnav) return;

        const clone = realSubnav.cloneNode(true);
        clone.classList.add('tour-subnav-clone', 'visible');
        clone.classList.remove('sticky');
        clone.removeAttribute('aria-label');

        // Replace the sub-nav items with this step's hub items
        const ul = clone.querySelector('ul');
        if (ul) {
            const items = TOUR_SUBNAV_ITEMS[step] || [];
            ul.innerHTML = items.map((label, i) =>
                `<li class="shrink-0">
                    <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium whitespace-nowrap transition-all ${i === 0 ? 'bg-primary/15 border border-primary/30 shadow-sm' : 'text-base-content/70 border border-transparent'}"
                          style="${i === 0 ? 'color: ' + accent + ';' : ''}">${label}</span>
                </li>`
            ).join('');
        }

        // Disable link clicks
        clone.querySelectorAll('a').forEach(a => {
            a.addEventListener('click', e => e.preventDefault());
            a.removeAttribute('href');
        });

        this.modal.appendChild(clone);
        this._subnavClone = clone;
    }

    /**
     * Remove the cloned sub-nav from the modal.
     */
    _removeSubnavClone() {
        if (this._subnavClone) {
            this._subnavClone.remove();
            this._subnavClone = null;
        }
    }

    /**
     * Swipe left/right to navigate between steps on touch devices.
     * Uses a 50px threshold to distinguish swipes from taps/scrolls.
     */
    _setupSwipeHandling() {
        let startX = 0;
        let startY = 0;

        this.modal.addEventListener('touchstart', (e) => {
            if (!this.isOpen) return;
            startX = e.touches[0].clientX;
            startY = e.touches[0].clientY;
        }, { passive: true });

        this.modal.addEventListener('touchend', (e) => {
            if (!this.isOpen) return;
            const deltaX = e.changedTouches[0].clientX - startX;
            const deltaY = e.changedTouches[0].clientY - startY;

            // Only count horizontal swipes (ignore vertical scrolling)
            if (Math.abs(deltaX) < 50 || Math.abs(deltaY) > Math.abs(deltaX)) return;

            if (deltaX < 0) {
                this.next();
            } else {
                this.prev();
            }
        }, { passive: true });
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
