/**
 * MonthSelector - Calendar-style month selector for Monthly Recap
 * Handles year navigation, premium gating, and keyboard shortcuts
 */
class MonthSelector {
    constructor(calendarData, currentYear, currentMonth, isPremium) {
        this.data = calendarData;
        this.currentYear = currentYear;
        this.currentMonth = currentMonth;
        this.isPremium = isPremium;
        this.displayYear = currentYear;  // Year currently shown

        this.prevYearBtn = null;
        this.nextYearBtn = null;
        this.yearDisplay = null;
        this.yearGrids = null;

        this.init();
    }

    init() {
        this.prevYearBtn = document.getElementById('prev-year-btn');
        this.nextYearBtn = document.getElementById('next-year-btn');
        this.yearDisplay = document.getElementById('calendar-year-display');
        this.yearGrids = document.querySelectorAll('.year-calendar');

        if (!this.prevYearBtn || !this.nextYearBtn || !this.yearDisplay) {
            console.warn('MonthSelector: Required elements not found');
            return;
        }

        this.setupEventListeners();
        this.updateYearNavigation();
    }

    setupEventListeners() {
        // Year navigation buttons
        // Previous Year = go BACKWARD in time (2024 → 2023)
        this.prevYearBtn.addEventListener('click', () => {
            if (this.isPremium && this.displayYear > this.data.earliest_year) {
                this.displayYear--;
                this.switchYear();
            }
        });

        // Next Year = go FORWARD in time (2023 → 2024)
        this.nextYearBtn.addEventListener('click', () => {
            if (this.displayYear < this.data.current_year) {
                this.displayYear++;
                this.switchYear();
            }
        });

        // Premium-locked month click handler (event delegation)
        document.addEventListener('click', (e) => {
            const lockedLink = e.target.closest('.premium-locked');
            if (!lockedLink) return;

            const isPremiumRequired = lockedLink.dataset.isPremiumRequired === 'true';
            if (isPremiumRequired && !this.isPremium) {
                e.preventDefault();
                this.showPremiumUpsell(lockedLink.dataset.monthName);
            }
        });

        // Keyboard navigation (arrow keys for year)
        document.addEventListener('keydown', (e) => {
            // Don't hijack keyboard when user is typing in inputs
            if (e.target.matches('input, textarea, select')) return;

            if (e.key === 'ArrowLeft' && e.ctrlKey) {
                // Ctrl+Left: Previous year (go backward in time)
                e.preventDefault();
                if (this.isPremium && this.displayYear > this.data.earliest_year) {
                    this.prevYearBtn.click();
                }
            } else if (e.key === 'ArrowRight' && e.ctrlKey) {
                // Ctrl+Right: Next year (go forward in time)
                e.preventDefault();
                if (this.displayYear < this.data.current_year) {
                    this.nextYearBtn.click();
                }
            }
        });
    }

    switchYear() {
        // Hide all year grids, show the selected one
        this.yearGrids.forEach(grid => {
            const gridYear = parseInt(grid.dataset.year);
            grid.style.display = (gridYear === this.displayYear) ? 'grid' : 'none';
        });

        // Update year display text
        this.yearDisplay.textContent = this.displayYear;

        // Update button states
        this.updateYearNavigation();
    }

    updateYearNavigation() {
        if (this.isPremium) {
            // Premium: can go back to earliest_year, forward to current_year
            const atEarliest = (this.displayYear <= this.data.earliest_year);
            this.prevYearBtn.disabled = atEarliest;

            // Explicitly remove/add class based on state
            if (atEarliest) {
                this.prevYearBtn.classList.add('btn-disabled');
            } else {
                this.prevYearBtn.classList.remove('btn-disabled');
            }

            const atCurrent = (this.displayYear >= this.data.current_year);
            this.nextYearBtn.disabled = atCurrent;

            // Explicitly remove/add class based on state
            if (atCurrent) {
                this.nextYearBtn.classList.add('btn-disabled');
            } else {
                this.nextYearBtn.classList.remove('btn-disabled');
            }
        } else {
            // Non-premium: locked to current year only
            this.prevYearBtn.disabled = true;
            this.nextYearBtn.disabled = true;
            this.prevYearBtn.classList.add('btn-disabled');
            this.nextYearBtn.classList.add('btn-disabled');
        }
    }

    showPremiumUpsell(monthName) {
        // Use PlatPursuit.ToastManager if available
        if (window.PlatPursuit?.ToastManager) {
            window.PlatPursuit.ToastManager.show(
                `Last month is free! Upgrade to Premium to view your full recap history!`,
                'warning',
                5000
            );
        } else {
            // Fallback to alert
            alert(`Last month is free! Upgrade to Premium to view your full recap history!`);
        }

        // Optional: Scroll to upgrade button and pulse it
        const upgradeBtn = document.querySelector('#month-calendar-card a[href*="subscribe"]');
        if (upgradeBtn) {
            upgradeBtn.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            upgradeBtn.classList.add('animate-pulse');
            setTimeout(() => upgradeBtn.classList.remove('animate-pulse'), 2000);
        }
    }
}

// Export to PlatPursuit namespace
window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.MonthSelector = MonthSelector;
