/**
 * BadgeDetailTourManager - Coach-marks spotlight tour for the badge detail page.
 *
 * Teaches users how badge series work: tier progression, stage requirements,
 * games within stages, and XP leaderboards. Uses the same box-shadow
 * spotlight overlay pattern as GameDetailTourManager.
 *
 * Depends on: PlatPursuit.API (utils.js)
 */

const BADGE_COACH_STEPS = [
    {
        target: '#badge-header-card',
        title: 'Badge Overview',
        description: 'This is a badge series. Your progress ring shows how many stages you\'ve completed. Earn platinums (or 100%) in the games within each stage to advance through the tiers.',
        icon: '<circle cx="12" cy="8" r="7"/><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/>',
        position: 'bottom',
    },
    {
        target: '#tier-selector',
        title: 'Tier Progression',
        description: 'Badges have up to four tiers: Bronze, Silver, Gold, and Platinum. Each tier has its own requirements and XP reward. Tap a tier to see what it takes to earn it.',
        icon: '<path d="M12 2 2 7l10 5 10-5-10-5Z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
        position: 'bottom',
    },
    {
        target: '#stage-1',
        title: 'Stages & Games',
        description: 'Each stage contains a group of games. Tap a stage to expand it and see which games qualify. Earn the required trophies in those games to complete the stage.',
        icon: '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
        position: 'top',
    },
    {
        target: '#badge-leaderboard-section',
        title: 'Badge Leaderboards',
        description: 'Every stage you complete earns XP. Compete with other hunters on the badge leaderboard and climb the ranks.',
        icon: '<line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>',
        position: 'top',
    },
];

const CUTOUT_PADDING = 8;

class BadgeDetailTourManager {
    constructor() {
        this.overlay = null;
        this.tooltip = null;
        this.currentStep = 0;
        this.totalSteps = BADGE_COACH_STEPS.length;
        this.isOpen = false;
        this._dismissing = false;
        this._initialized = false;
        this._currentTarget = null;
        this._resizeHandler = null;
    }

    init(autoShow = false) {
        if (this._initialized) return;

        this.overlay = document.getElementById('badge-coach-overlay');
        this.tooltip = document.getElementById('badge-coach-tooltip');
        if (!this.overlay || !this.tooltip) return;

        this.titleEl = document.getElementById('badge-coach-title');
        this.descEl = document.getElementById('badge-coach-desc');
        this.svgEl = document.getElementById('badge-coach-svg');
        this.counterEl = document.getElementById('badge-coach-counter');
        this.prevBtn = document.getElementById('badge-coach-prev');
        this.nextBtn = document.getElementById('badge-coach-next');
        this.skipBtn = document.getElementById('badge-coach-skip');

        this._setupEventHandlers();
        this._initialized = true;

        if (autoShow) {
            setTimeout(() => this.open(), 1000);
        }
    }

    open() {
        if (!this._initialized) this.init(false);
        if (!this.overlay) return;

        this.currentStep = 0;
        this._dismissing = false;
        this.isOpen = true;

        this.overlay.classList.add('visible');
        this.tooltip.classList.add('visible');

        this._resizeHandler = () => {
            if (this.isOpen) this._positionCurrentStep();
        };
        window.addEventListener('resize', this._resizeHandler);

        this._showStep(0);
    }

    close() {
        this.isOpen = false;
        this.overlay.classList.remove('visible');
        this.tooltip.classList.remove('visible');

        if (this._currentTarget) {
            this._currentTarget.classList.remove('coach-target-highlight');
            this._currentTarget = null;
        }

        this.overlay.style.top = '';
        this.overlay.style.left = '';
        this.overlay.style.width = '';
        this.overlay.style.height = '';
        this.tooltip.classList.remove('positioned');

        if (this._resizeHandler) {
            window.removeEventListener('resize', this._resizeHandler);
            this._resizeHandler = null;
        }
    }

    next() {
        if (this.currentStep >= this.totalSteps - 1) {
            this.dismiss('complete');
            return;
        }
        this._showStep(this.currentStep + 1);
    }

    prev() {
        if (this.currentStep <= 0) return;
        this._showStep(this.currentStep - 1);
    }

    async dismiss(action = 'complete') {
        if (this._dismissing) return;
        this._dismissing = true;

        this.close();

        try {
            await PlatPursuit.API.post('/api/v1/tutorial/badge-detail/dismiss/', {
                action: action,
                last_step: this.currentStep + 1,
            });
        } catch (err) {
            console.warn('Badge detail tour dismiss failed:', err);
        }
    }

    // ------------------------------------------------------------------

    _showStep(stepIndex) {
        const step = BADGE_COACH_STEPS[stepIndex];
        if (!step) return;

        const target = document.querySelector(step.target);
        if (!target) {
            if (stepIndex < this.totalSteps - 1) {
                this._showStep(stepIndex + 1);
            } else {
                this.dismiss('complete');
            }
            return;
        }

        if (this._currentTarget) {
            this._currentTarget.classList.remove('coach-target-highlight');
        }

        this.currentStep = stepIndex;
        this._currentTarget = target;

        this._updateContent(step);
        this._updateControls();

        target.scrollIntoView({ behavior: 'smooth', block: 'center' });

        // Wait for scroll to settle via rAF polling
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

    _positionCurrentStep() {
        const step = BADGE_COACH_STEPS[this.currentStep];
        if (!step) return;
        const target = document.querySelector(step.target);
        if (!target) return;

        this._positionCutout(target);
        this._positionTooltip(target, step.position);
    }

    _positionCutout(target) {
        const rect = target.getBoundingClientRect();
        const pad = CUTOUT_PADDING;

        this.overlay.style.top = (rect.top - pad) + 'px';
        this.overlay.style.left = (rect.left - pad) + 'px';
        this.overlay.style.width = (rect.width + pad * 2) + 'px';
        this.overlay.style.height = (rect.height + pad * 2) + 'px';
    }

    _positionTooltip(target, preferredPosition) {
        const rect = target.getBoundingClientRect();
        const tooltipRect = this.tooltip.getBoundingClientRect();
        const pad = CUTOUT_PADDING;
        const gap = 12;
        const vw = window.innerWidth;
        const vh = window.innerHeight;

        let top, left;

        if (preferredPosition === 'top') {
            top = rect.top - pad - gap - tooltipRect.height;
            if (top < 8) top = rect.bottom + pad + gap;
        } else {
            top = rect.bottom + pad + gap;
            if (top + tooltipRect.height > vh - 8) top = rect.top - pad - gap - tooltipRect.height;
        }

        left = rect.left + (rect.width / 2) - (tooltipRect.width / 2);
        left = Math.max(8, Math.min(left, vw - tooltipRect.width - 8));

        this.tooltip.style.top = top + 'px';
        this.tooltip.style.left = left + 'px';

        if (!this.tooltip.classList.contains('positioned')) {
            requestAnimationFrame(() => this.tooltip.classList.add('positioned'));
        }
    }

    _updateContent(step) {
        if (this.titleEl) this.titleEl.textContent = step.title;
        if (this.descEl) this.descEl.textContent = step.description;
        if (this.svgEl) this.svgEl.innerHTML = step.icon;
    }

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

    _setupEventHandlers() {
        document.addEventListener('keydown', (e) => {
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

        this.overlay.addEventListener('click', () => {
            if (this.isOpen) this.dismiss('skip');
        });
    }
}

const badgeDetailTourInstance = new BadgeDetailTourManager();

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.BadgeDetailTourManager = badgeDetailTourInstance;
