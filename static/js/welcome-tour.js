/**
 * WelcomeTourManager - 7-step Welcome Tour for new PlatPursuit users.
 *
 * Steps 1-2 are intro slides (welcome splash + navigation explainer).
 * Step 3 introduces the avatar menu (profile & settings).
 * Steps 4-7 walk through the four hubs (Dashboard, Browse, Community,
 * My Pursuit) with real navigation chrome cloned and highlighted.
 *
 * On desktop (>=1024px), the real navbar is cloned into the modal.
 * On mobile (<1024px), the real tab bar and sub-nav are cloned instead.
 *
 * Auto-shows once on the first dashboard load after PSN linking + sync.
 * Can be re-triggered from the avatar dropdown "Replay Tour" button.
 *
 * Depends on: PlatPursuit.API (utils.js), PlatPursuit.CelebrationManager
 */

// Sub-nav items per hub step (steps 3-6). Used to rebuild the sub-nav
// clone when transitioning between hub steps on mobile.
const TOUR_SUBNAV_ITEMS = {
    4: ['Dashboard', 'My Stats', 'Shareables', 'Recap'],
    5: ['Games', 'Trophies', 'New', 'Companies', 'Genres'],
    6: ['Hub', 'Profiles', 'Reviews', 'Challenges'],
    7: ['Badges', 'Milestones', 'Titles'],
};

// Hub labels and icons per step (shown in the sub-nav clone on desktop)
const TOUR_HUB_LABELS = {
    4: { label: 'Dashboard', icon: 'layout-dashboard' },
    5: { label: 'Browse', icon: 'compass' },
    6: { label: 'Community', icon: 'users' },
    7: { label: 'My Pursuit', icon: 'trophy' },
};

const TOUR_HUB_ICONS = {
    'layout-dashboard': '<rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/>',
    'compass': '<circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/>',
    'users': '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    'trophy': '<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/>',
};

// Steps 4-7 map to mobile tab bar indices 0-3
const TOUR_TAB_INDEX = { 4: 0, 5: 1, 6: 2, 7: 3 };

class WelcomeTourManager {
    constructor() {
        this.modal = null;
        this.steps = [];
        this.dots = [];
        this.currentStep = 1;
        this.totalSteps = 7;
        this.isOpen = false;
        this.isTransitioning = false;
        this._initialized = false;
        this._tabBarClone = null;
        this._subnavClone = null;
        this._navbarClone = null;
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

        // Pre-load confetti library so there's no CDN delay at completion
        if (window.PlatPursuit?.CelebrationManager?.loadConfetti) {
            PlatPursuit.CelebrationManager.loadConfetti();
        }

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

        // Clone navigation chrome into the modal. On mobile (<1024px), clone
        // the tab bar. On desktop (>=1024px), clone the full navbar. Sub-nav
        // clones are created per step.
        this._createTabBarClone();
        this._createNavbarClone();
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
        this._removeNavbarClone();
    }

    /**
     * Navigate to a specific step.
     * @param {number} step - Step number (1-7).
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

        // On complete (not skip), celebrate with confetti + welcome message
        if (action === 'complete') {
            this._showCompletionCelebration();
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

        // Slide out current (fast exit with scale)
        const easing = 'cubic-bezier(0.16, 1, 0.3, 1)';
        currentEl.style.transition = `opacity 0.2s ${easing}, transform 0.2s ${easing}`;
        currentEl.style.opacity = '0';
        currentEl.style.transform = `translateX(${outX}) scale(0.98)`;

        setTimeout(() => {
            currentEl.classList.add('hidden');
            currentEl.style.transition = '';
            currentEl.style.opacity = '';
            currentEl.style.transform = '';

            // Prepare target off-screen
            targetEl.style.opacity = '0';
            targetEl.style.transform = `translateX(${inX}) scale(0.98)`;
            targetEl.classList.remove('hidden');

            // Force reflow before animating in
            void targetEl.offsetHeight;

            targetEl.style.transition = `opacity 0.3s ${easing}, transform 0.3s ${easing}`;
            targetEl.style.opacity = '1';
            targetEl.style.transform = 'translateX(0) scale(1)';

            this.currentStep = targetStep;
            this._updateControls();
            this._animateCardsIn(targetStep);

            setTimeout(() => {
                targetEl.style.transition = '';
                this.isTransitioning = false;
            }, 300);
        }, 200);
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

        // Progress dots + connected bar
        this.dots.forEach((dot, i) => {
            const isActive = i === step - 1;
            dot.classList.toggle('active', isActive);
            if (isActive) {
                dot.style.setProperty('--dot-color', accent);
            }
        });

        // Update progress bar fill
        const progressBar = document.getElementById('tour-progress-bar');
        if (progressBar) {
            const fill = ((step - 1) / (this.totalSteps - 1)) * 100;
            progressBar.style.setProperty('--progress-fill', fill + '%');
            progressBar.style.setProperty('--dot-color', accent);
            this._updateProgressBarBounds();
        }

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

        // Update navigation chrome (desktop navbar clone or mobile tab bar)
        this._updateChromeForStep(step);
    }

    /**
     * Stagger-animate step elements into view.
     * Step 1 (welcome): text cascade (title, tagline, description, hint).
     * Steps 2-7: spring-scale feature cards.
     */
    _animateCardsIn(step) {
        const stepEl = this.steps[step - 1];
        if (!stepEl) return;

        // Step 1: staggered text cascade (logo shimmer handles itself via CSS)
        if (step === 1) {
            const textEls = stepEl.querySelectorAll('h2, p');
            textEls.forEach((el, i) => {
                el.style.opacity = '0';
                el.style.transform = 'translateY(8px)';
                el.style.transition = 'none';
                setTimeout(() => {
                    el.style.transition = 'opacity 0.4s ease-out, transform 0.4s ease-out';
                    el.style.opacity = '1';
                    el.style.transform = 'translateY(0)';
                }, 300 + i * 200);
            });
            return;
        }

        // Steps 2-7: spring-scale feature cards
        const springEasing = 'cubic-bezier(0.34, 1.56, 0.64, 1)';
        let delay = 80;

        const cards = stepEl.querySelectorAll('.tour-feature-card');
        cards.forEach((card, i) => {
            card.style.opacity = '0';
            card.style.transform = 'scale(0.92) translateY(12px)';
            card.style.transition = 'none';

            setTimeout(() => {
                card.style.transition = `opacity 0.35s ${springEasing}, transform 0.35s ${springEasing}`;
                card.style.opacity = '1';
                card.style.transform = 'scale(1) translateY(0)';
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
     * Orchestrate navigation chrome highlighting for the current step.
     * Delegates to viewport-specific helpers for desktop vs mobile.
     */
    _updateChromeForStep(step) {
        const stepEl = this.steps[step - 1];
        const accent = stepEl ? stepEl.dataset.accent : 'var(--color-primary)';

        this._removePopovers();

        if (window.innerWidth >= 1024) {
            this._updateDesktopChrome(step, accent);
        } else {
            this._updateMobileChrome(step, accent);
        }
    }

    /**
     * Desktop chrome: highlight elements in the cloned navbar.
     * - Step 1: no chrome
     * - Step 2: highlight all hub buttons with per-hub colors + popovers
     * - Step 3: highlight avatar area + popover
     * - Steps 4-7: highlight specific hub button + show subnav clone
     */
    _updateDesktopChrome(step, accent) {
        if (!this._navbarClone) return;

        // Clear all highlights
        this._navbarClone.querySelectorAll('.tour-hub-highlight').forEach(el => {
            el.classList.remove('tour-hub-highlight');
            el.style.removeProperty('--tour-highlight-color');
        });
        this._navbarClone.querySelectorAll('.tour-avatar-highlight').forEach(el => {
            el.classList.remove('tour-avatar-highlight');
        });

        if (step === 1) {
            // Welcome splash: hide navbar clone
            this._navbarClone.classList.remove('visible');
            this._removeSubnavClone();
        } else if (step === 2) {
            // Navigation intro: show navbar, highlight ALL hub buttons
            this._navbarClone.classList.add('visible');
            const hubMenu = this._navbarClone.querySelector('.menu-horizontal');
            const hubButtons = this._navbarClone.querySelectorAll('.menu-horizontal > li');
            const hubColors = [
                'var(--color-primary)',
                'var(--color-accent)',
                'var(--color-success)',
                'var(--color-secondary)',
            ];
            hubButtons.forEach((li, i) => {
                const btn = li.querySelector('a, button');
                if (btn) {
                    btn.classList.add('tour-hub-highlight');
                    btn.style.setProperty('--tour-highlight-color', hubColors[i] || accent);
                }
            });
            this._createSubnavClone(4, accent); // Dashboard subnav as example

            this._createPopover(
                'bottom',
                'These are your four main sections',
                accent,
                hubMenu
            );
            this._createPopover(
                'top',
                'Each section has sub-pages here',
                accent
            );
        } else if (step === 3) {
            // Avatar menu: highlight the avatar button
            this._navbarClone.classList.add('visible');
            const avatarBtn = this._navbarClone.querySelector('.btn-circle.avatar');
            if (avatarBtn) {
                avatarBtn.classList.add('tour-avatar-highlight');
            }
            this._removeSubnavClone();

            this._createPopover(
                'left',
                'Your profile, settings, and more live here',
                accent,
                avatarBtn
            );
        } else {
            // Hub steps 4-7: highlight specific hub button + show subnav
            this._navbarClone.classList.add('visible');
            const hubButtons = this._navbarClone.querySelectorAll('.menu-horizontal > li');
            const hubIndex = TOUR_TAB_INDEX[step];
            if (hubIndex !== undefined && hubButtons[hubIndex]) {
                const btn = hubButtons[hubIndex].querySelector('a, button');
                if (btn) {
                    btn.classList.add('tour-hub-highlight');
                    btn.style.setProperty('--tour-highlight-color', accent);
                }
            }
            this._createSubnavClone(step, accent);
        }
    }

    /**
     * Mobile chrome: highlight elements in the cloned tab bar.
     * - Step 1: no chrome
     * - Step 2: highlight all tabs with per-hub colors + popovers
     * - Step 3: no tab highlight (avatar is in navbar, not tab bar)
     * - Steps 4-7: highlight specific tab + show subnav clone
     */
    _updateMobileChrome(step, accent) {
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
            this._createSubnavClone(4, accent); // Dashboard sub-nav as example

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
        } else if (step === 3) {
            // Avatar step: no tab bar highlight on mobile
            this._removeSubnavClone();
        } else {
            // Hub steps 4-7: highlight specific tab + show that hub's sub-nav
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
     * Create a small popover label near a chrome clone element.
     * @param {'top'|'bottom'} position - 'top' for sub-nav, 'bottom' for tab bar/navbar
     * @param {string} text - Label text
     * @param {string} accent - CSS color value
     * @param {Element} [anchor] - Optional element to horizontally center on.
     *   When omitted, defaults to viewport center (mobile) or navbar center (desktop).
     */
    _createPopover(position, text, accent, anchor) {
        const popover = document.createElement('div');
        popover.className = 'tour-chrome-popover';
        popover.style.setProperty('--popover-color', accent);
        const isDesktop = window.innerWidth >= 1024;

        // Helper: horizontally center popover on an anchor element.
        // Overrides the default CSS `left: 50%` (viewport center).
        const alignToAnchor = (el) => {
            if (!el) return;
            const r = el.getBoundingClientRect();
            popover.style.left = (r.left + r.width / 2) + 'px';
        };

        // Special "left" position: popover sits to the LEFT of the anchor,
        // top-aligned. Used for elements near the right viewport edge.
        if (position === 'left' && anchor) {
            popover.style.left = 'auto';
            popover.style.transform = 'none';
            popover.innerHTML = `
                <div class="tour-chrome-popover-body">${text}</div>`;
            // Append first so we can measure
            this.modal.appendChild(popover);
            if (!this._popovers) this._popovers = [];
            this._popovers.push(popover);
            requestAnimationFrame(() => {
                const anchorRect = anchor.getBoundingClientRect();
                const popoverRect = popover.getBoundingClientRect();
                popover.style.top = anchorRect.top + 'px';
                popover.style.left = (anchorRect.left - popoverRect.width - 10) + 'px';
            });
            return;
        }

        if (position === 'bottom') {
            if (isDesktop) {
                // Below the navbar clone (pointing up toward it)
                popover.style.top = '60px'; // safe default while measuring
                requestAnimationFrame(() => {
                    const navbar = this._navbarClone;
                    if (navbar) {
                        const rect = navbar.getBoundingClientRect();
                        popover.style.top = (rect.bottom + 6) + 'px';
                    }
                    alignToAnchor(anchor);
                });
                popover.innerHTML = `
                    <svg width="12" height="8" viewBox="0 0 12 8" class="tour-chrome-popover-arrow up"><path d="M6 0L12 8H0z" fill="var(--popover-color)"/></svg>
                    <div class="tour-chrome-popover-body">${text}</div>`;
            } else {
                // Above the tab bar (pointing down)
                const tabbar = this._tabBarClone;
                if (!tabbar) return;
                const tabbarHeight = tabbar.offsetHeight || 56;
                popover.style.bottom = (tabbarHeight + 8) + 'px';
                popover.innerHTML = `
                    <div class="tour-chrome-popover-body">${text}</div>
                    <svg width="12" height="8" viewBox="0 0 12 8" class="tour-chrome-popover-arrow down"><path d="M6 8L0 0h12z" fill="var(--popover-color)"/></svg>`;
            }
        } else {
            // Below the sub-nav (pointing up). Defer to next frame so
            // the clone has its final rendered height, then measure.
            popover.style.top = '60px'; // safe default while measuring
            requestAnimationFrame(() => {
                const subnav = this._subnavClone;
                if (subnav) {
                    const subnavRect = subnav.getBoundingClientRect();
                    popover.style.top = (subnavRect.bottom + 6) + 'px';
                }
                // Anchor takes priority, then fall back to sub-nav items
                if (anchor) {
                    alignToAnchor(anchor);
                } else if (isDesktop && subnav) {
                    // Target the <ul> which shrink-wraps the pill items,
                    // not the full-width scroll container
                    const itemsList = subnav.querySelector('ul');
                    alignToAnchor(itemsList);
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

        const realSubnav = document.querySelector('.hub-subnav');
        if (!realSubnav) return;

        const clone = realSubnav.cloneNode(true);
        clone.classList.add('tour-subnav-clone', 'visible');
        clone.classList.remove('sticky');
        clone.removeAttribute('aria-label');

        // Replace the hub label and icon to match the current step
        const hubInfo = TOUR_HUB_LABELS[step];
        if (hubInfo) {
            const labelSpan = clone.querySelector('.hub-subnav-scroll .uppercase span');
            if (labelSpan) labelSpan.textContent = hubInfo.label;

            const iconSvg = clone.querySelector('.hub-subnav-scroll .uppercase svg');
            if (iconSvg && TOUR_HUB_ICONS[hubInfo.icon]) {
                iconSvg.innerHTML = TOUR_HUB_ICONS[hubInfo.icon];
            }
        }

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
     * Clone the real desktop navbar into the modal so it renders inside the
     * dialog's stacking context (above the backdrop blur). Only on >=1024px
     * viewports where the real navbar's hub buttons are visible.
     */
    _createNavbarClone() {
        this._removeNavbarClone();
        if (window.innerWidth < 1024) return;

        const realNavbar = document.querySelector('.navbar');
        if (!realNavbar) return;

        const clone = realNavbar.cloneNode(true);
        clone.classList.add('tour-navbar-clone');
        clone.classList.remove('sticky');
        clone.removeAttribute('role');
        clone.removeAttribute('aria-label');

        // Strip all link interactions (clone is display-only)
        clone.querySelectorAll('a').forEach(a => {
            a.addEventListener('click', e => e.preventDefault());
            a.removeAttribute('href');
            a.style.cursor = 'default';
        });

        // Prevent DaisyUI dropdown auto-toggle on the avatar and notification areas
        clone.querySelectorAll('[tabindex]').forEach(el => {
            el.removeAttribute('tabindex');
        });

        // Disable form submissions (logout form)
        clone.querySelectorAll('form').forEach(f => {
            f.addEventListener('submit', e => e.preventDefault());
        });

        // Disable buttons
        clone.querySelectorAll('button').forEach(btn => {
            btn.addEventListener('click', e => e.preventDefault());
            btn.style.cursor = 'default';
        });

        // Normalize hub buttons: remove active state so only tour highlights show
        clone.querySelectorAll('.menu-horizontal .btn-primary').forEach(btn => {
            btn.classList.remove('btn-primary');
            btn.classList.add('btn-ghost');
        });

        // Remove notification badge/dropdown content to simplify
        const notifDropdown = clone.querySelector('#notification-dropdown');
        if (notifDropdown) {
            const notifContent = notifDropdown.querySelector('.dropdown-content');
            if (notifContent) notifContent.remove();
        }

        // Remove the avatar dropdown content (menu items)
        const avatarDropdowns = clone.querySelectorAll('.dropdown-end');
        avatarDropdowns.forEach(dd => {
            const ddContent = dd.querySelector('.dropdown-content, ul.menu');
            if (ddContent) ddContent.remove();
        });

        this.modal.appendChild(clone);
        this._navbarClone = clone;
    }

    /**
     * Remove the cloned navbar from the modal.
     */
    _removeNavbarClone() {
        if (this._navbarClone) {
            this._navbarClone.remove();
            this._navbarClone = null;
        }
    }

    /**
     * Size and position the progress bar track to span exactly between the
     * centers of the first and last dots.
     */
    _updateProgressBarBounds() {
        const container = document.getElementById('tour-progress-dots');
        const bar = document.getElementById('tour-progress-bar');
        if (!container || !bar || this.dots.length < 2) return;

        const firstDot = this.dots[0];
        const lastDot = this.dots[this.dots.length - 1];
        const containerRect = container.getBoundingClientRect();
        const firstRect = firstDot.getBoundingClientRect();
        const lastRect = lastDot.getBoundingClientRect();

        const left = firstRect.left - containerRect.left + firstRect.width / 2;
        const right = lastRect.left - containerRect.left + lastRect.width / 2;
        bar.style.left = left + 'px';
        bar.style.width = (right - left) + 'px';
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
     * Post-tour showstopper: full-screen overlay with a personalized welcome
     * message, Platinum Pursuit logo, ambient particles, and confetti burst
     * on dismissal. Auto-dismisses after 6s or on click/keypress.
     */
    _showCompletionCelebration() {
        const username = this.modal?.dataset.psnUsername || '';
        const logoSrc = this.modal?.querySelector('img[alt="Platinum Pursuit"]')?.src || '';

        // Create dark overlay
        const overlay = document.createElement('div');
        overlay.className = 'tour-completion-overlay';
        document.body.appendChild(overlay);

        // Create centered welcome card with particles + logo + text
        const card = document.createElement('div');
        card.className = 'tour-completion-card';
        // Full-screen ember layer behind everything
        const embers = document.createElement('div');
        embers.className = 'tour-completion-embers';
        embers.setAttribute('aria-hidden', 'true');
        // Generate 30 embers spread across the full viewport
        const emberData = [
            { x:3, d:0, s:5, dur:2.5, drift:-25 }, { x:8, d:0.4, s:4, dur:3, drift:15 },
            { x:14, d:0.1, s:6, dur:2.8, drift:-10 }, { x:19, d:0.7, s:3, dur:3.2, drift:20 },
            { x:25, d:0.3, s:5, dur:2.6, drift:-18 }, { x:30, d:0.9, s:4, dur:3.5, drift:12 },
            { x:35, d:1.2, s:3, dur:2.4, drift:-30 }, { x:40, d:0.6, s:5, dur:2.9, drift:8 },
            { x:45, d:1.5, s:4, dur:3.1, drift:-22 }, { x:50, d:0.8, s:6, dur:2.7, drift:16 },
            { x:55, d:1.1, s:3, dur:3.3, drift:-14 }, { x:60, d:0.2, s:5, dur:2.3, drift:25 },
            { x:65, d:0.5, s:4, dur:3.4, drift:-20 }, { x:70, d:1.3, s:6, dur:2.6, drift:12 },
            { x:75, d:0.1, s:3, dur:3, drift:-28 }, { x:80, d:0.8, s:5, dur:2.5, drift:18 },
            { x:85, d:1.4, s:4, dur:3.2, drift:-15 }, { x:90, d:0.3, s:6, dur:2.8, drift:22 },
            { x:95, d:0.7, s:3, dur:3.1, drift:-12 }, { x:12, d:1.6, s:5, dur:2.4, drift:28 },
            { x:22, d:0.9, s:4, dur:3.5, drift:-8 }, { x:33, d:0.2, s:6, dur:2.7, drift:14 },
            { x:48, d:1.0, s:3, dur:3.3, drift:-26 }, { x:58, d:0.4, s:5, dur:2.5, drift:10 },
            { x:68, d:1.3, s:4, dur:2.9, drift:-18 }, { x:78, d:0.6, s:6, dur:3.4, drift:24 },
            { x:88, d:1.1, s:3, dur:2.6, drift:-20 }, { x:5, d:0.5, s:5, dur:3, drift:16 },
            { x:42, d:1.7, s:4, dur:2.8, drift:-12 }, { x:72, d:0.3, s:6, dur:3.2, drift:20 },
        ];
        embers.innerHTML = emberData.map(e =>
            `<div class="tour-ember" style="--e-x:${e.x}%;--e-delay:${e.d}s;--e-size:${e.s}px;--e-dur:${e.dur}s;--e-drift:${e.drift}px;"></div>`
        ).join('');
        document.body.appendChild(embers);

        card.innerHTML = `
            ${logoSrc
                ? `<img src="${logoSrc}" alt="" class="tour-completion-logo" />`
                : ''}
            <div class="tour-completion-title">Welcome to Platinum Pursuit${username ? ',' : ''}</div>
            ${username ? `<div class="tour-completion-username">${username}</div>` : ''}
            <div class="tour-completion-subtitle">Your journey starts now. Happy hunting!</div>`;
        document.body.appendChild(card);

        // Stagger entrance: overlay + embers first, then card scales in
        requestAnimationFrame(() => {
            overlay.classList.add('visible');
            embers.classList.add('visible');
            card.classList.add('visible');
        });

        // Fire confetti AFTER the card dismisses for a send-off effect
        const fireConfetti = () => {
            const cm = window.PlatPursuit?.CelebrationManager;
            if (!cm) return;
            cm.loadConfetti().then(() => {
                cm.fireSideConfetti(3500);
            }).catch(() => {});
        };

        const cleanup = () => {
            overlay.classList.remove('visible');
            card.classList.remove('visible');
            embers.classList.remove('visible');
            fireConfetti();
            setTimeout(() => {
                overlay.remove();
                card.remove();
                embers.remove();
            }, 500);
            document.removeEventListener('click', cleanup);
            document.removeEventListener('keydown', cleanup);
        };

        // Defer listener registration so the original "Get Started" click
        // finishes propagating before we start listening for dismissals.
        setTimeout(() => {
            setTimeout(cleanup, 6000);
            document.addEventListener('click', cleanup, { once: true });
            document.addEventListener('keydown', cleanup, { once: true });
        }, 50);
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
