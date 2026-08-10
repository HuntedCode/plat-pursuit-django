/**
 * MonthSelector - Calendar-style month selector for Monthly Recap.
 *
 * Year navigation + keyboard shortcuts. No gating: every month a hunter earned a trophy in is theirs to
 * open, so the year arrows now run the full range back to their first trophy for everyone. The premium
 * lock, the upsell toast and the "non-premium is pinned to the current year" branch are gone.
 */
class MonthSelector {
    constructor(calendarData, currentYear, currentMonth) {
        this.data = calendarData;
        this.currentYear = currentYear;
        this.currentMonth = currentMonth;
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

    canGoBack() { return this.displayYear > this.data.earliest_year; }
    canGoForward() { return this.displayYear < this.data.current_year; }

    setupEventListeners() {
        // Previous Year = go BACKWARD in time (2024 -> 2023)
        this.prevYearBtn.addEventListener('click', () => {
            if (this.canGoBack()) {
                this.displayYear--;
                this.switchYear();
            }
        });

        // Next Year = go FORWARD in time (2023 -> 2024)
        this.nextYearBtn.addEventListener('click', () => {
            if (this.canGoForward()) {
                this.displayYear++;
                this.switchYear();
            }
        });

        // Keyboard navigation (Ctrl + arrows for year)
        document.addEventListener('keydown', (e) => {
            // Don't hijack keyboard when user is typing in inputs
            if (e.target.matches('input, textarea, select')) return;

            if (e.key === 'ArrowLeft' && e.ctrlKey) {
                e.preventDefault();
                if (this.canGoBack()) this.prevYearBtn.click();
            } else if (e.key === 'ArrowRight' && e.ctrlKey) {
                e.preventDefault();
                if (this.canGoForward()) this.nextYearBtn.click();
            }
        });
    }

    switchYear() {
        // Hide all year grids, show the selected one
        this.yearGrids.forEach(grid => {
            const gridYear = parseInt(grid.dataset.year);
            grid.style.display = (gridYear === this.displayYear) ? 'grid' : 'none';
        });

        this.yearDisplay.textContent = this.displayYear;
        this.updateYearNavigation();
    }

    updateYearNavigation() {
        // The only bounds are the hunter's own history: their first trophy, and today.
        const atEarliest = !this.canGoBack();
        this.prevYearBtn.disabled = atEarliest;
        this.prevYearBtn.classList.toggle('btn-disabled', atEarliest);

        const atCurrent = !this.canGoForward();
        this.nextYearBtn.disabled = atCurrent;
        this.nextYearBtn.classList.toggle('btn-disabled', atCurrent);
    }
}

// Export to PlatPursuit namespace
window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.MonthSelector = MonthSelector;
