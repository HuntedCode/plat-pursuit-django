/**
 * Dashboard Module Manager
 *
 * Handles tabbed navigation, per-tab lazy-loading of modules,
 * drag-and-drop reordering (premium), module toggle (show/hide),
 * per-module settings, and the customize panel.
 *
 * Layout: tabbed carousel with single-column panels, capped width.
 */
(function () {
    'use strict';

    class DashboardManager {
        /**
         * @param {Object} config
         * @param {string} config.moduleDataUrl  - Base URL for lazy module API
         * @param {string} config.configUrl      - URL for config update API
         * @param {string} config.reorderUrl     - URL for reorder API
         * @param {boolean} config.isPremium     - Whether user is premium
         * @param {number} config.maxFreeHidden  - Max modules free users can hide
         * @param {string[]} config.hiddenModules - Currently hidden module slugs
         * @param {Object} config.moduleSettings  - Per-module settings from DB
         * @param {Object} config.tabs            - Tab config: {slug: {name, isActive, lazyModules}}
         */
        constructor(config) {
            this.moduleDataUrl = config.moduleDataUrl;
            this.configUrl = config.configUrl;
            this.reorderUrl = config.reorderUrl;
            this.isPremium = config.isPremium;
            this.maxFreeHidden = config.maxFreeHidden;
            this.hiddenModules = new Set(config.hiddenModules || []);
            this.moduleSettings = config.moduleSettings || {};
            this.tabsConfig = config.tabs || {};
            this._tabConfig = config.tabConfig || {};

            this.activeTab = null;
            this.loadedTabs = new Set();
            this.dragManagers = {};  // Per-tab drag managers
            this._moduleInits = {};

            // Debounced persistence
            this._debouncedSaveConfig = PlatPursuit.debounce(() => this._saveConfigNow(), 500);
            this._debouncedSaveSettings = PlatPursuit.debounce(() => this._saveSettingsNow(), 500);
            this._debouncedSaveOrder = PlatPursuit.debounce(() => this._saveOrderNow(), 500);
            this._debouncedSaveActiveTab = PlatPursuit.debounce(() => this._saveActiveTab(), 1000);
            this._pendingOrder = null;
            this._settingsDirty = false;
        }

        init() {
            this._bindTabs();
            this._bindCustomizePanel();

            // Find and load the active tab
            for (const [slug, tabConfig] of Object.entries(this.tabsConfig)) {
                if (tabConfig.isActive) {
                    this.activeTab = slug;
                    break;
                }
            }
            if (!this.activeTab && Object.keys(this.tabsConfig).length > 0) {
                this.activeTab = Object.keys(this.tabsConfig)[0];
            }

            // Load active tab's lazy modules
            if (this.activeTab) {
                this._loadTabModules(this.activeTab);
                if (this.isPremium) {
                    this._initDragForTab(this.activeTab);
                }
            }

            // Quick Settings module: bind auto-save controls (server-rendered, already in DOM)
            this._initQuickSettings();

            // Calendar Challenge module: paginated month navigation
            this.registerModuleInit('calendar_challenge', (el) => this._initCalendarPagination(el));

            // Rate My Games module: hover-to-scroll preview strip
            this.registerModuleInit('rate_my_games', (el) => this._initRateStrip(el));

            // Profile Card Preview module: fetch HTML preview and scale to fit
            this.registerModuleInit('profile_card_preview', (el) => this._initProfileCardPreview(el));

            // Share card preview modules: fetch + scale + download pattern
            this.registerModuleInit('recent_platinum_card', (el) => this._initShareCards(el));
            this.registerModuleInit('challenge_share_cards', (el) => this._initShareCards(el));
            this.registerModuleInit('recap_share_card', (el) => this._initShareCards(el));

            // Premium Settings module: theme picker + trophy case management
            this.registerModuleInit('premium_settings', (el) => this._initPremiumSettings(el));

            // Advanced Stats module: date range switcher
            this.registerModuleInit('advanced_stats', (el) => this._initAdvancedStatsRange(el));

            // Combined visualization module: heatmap + genre radar + year review + type breakdown
            this.registerModuleInit('trophy_visualizations', (el) => this._initTrophyVisualizations(el));

            // Badge analytics modules
            this.registerModuleInit('advanced_badge_stats', (el) => this._initAdvancedBadgeStatsRange(el));
            this.registerModuleInit('badge_series_overview', (el) => this._initBadgeSeriesOverview(el));
            this.registerModuleInit('badge_visualizations', (el) => this._initBadgeVisualizations(el));
        }

        // -----------------------------------------------------------------
        // Tab Navigation
        // -----------------------------------------------------------------

        _bindTabs() {
            this._tabButtons = [...document.querySelectorAll('.tab-btn')];

            this._tabButtons.forEach(btn => {
                btn.addEventListener('click', () => {
                    const slug = btn.dataset.tabSlug;
                    if (slug !== this.activeTab) {
                        this._switchTab(slug);
                    }
                });
            });

            // Keyboard navigation: arrow keys move between tabs
            const tabContainer = document.getElementById('dashboard-tabs');
            if (tabContainer) {
                tabContainer.addEventListener('keydown', (e) => {
                    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
                    e.preventDefault();

                    const currentIdx = this._tabButtons.findIndex(b => b.dataset.tabSlug === this.activeTab);
                    let newIdx;

                    if (e.key === 'ArrowRight') {
                        newIdx = (currentIdx + 1) % this._tabButtons.length;
                    } else if (e.key === 'ArrowLeft') {
                        newIdx = (currentIdx - 1 + this._tabButtons.length) % this._tabButtons.length;
                    } else if (e.key === 'Home') {
                        newIdx = 0;
                    } else if (e.key === 'End') {
                        newIdx = this._tabButtons.length - 1;
                    }

                    if (newIdx !== undefined && newIdx !== currentIdx) {
                        this._tabButtons[newIdx].focus();
                        this._switchTab(this._tabButtons[newIdx].dataset.tabSlug);
                    }
                });
            }

        }

        _switchTab(newSlug) {
            const oldSlug = this.activeTab;

            // Hide old panel, show new panel
            if (oldSlug) {
                const oldPanel = document.getElementById('tab-panel-' + oldSlug);
                if (oldPanel) oldPanel.classList.add('hidden');
            }
            const newPanel = document.getElementById('tab-panel-' + newSlug);
            if (newPanel) newPanel.classList.remove('hidden');

            // Update tab button states
            document.querySelectorAll('.tab-btn').forEach(btn => {
                const isActive = btn.dataset.tabSlug === newSlug;
                btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
                if (isActive) {
                    btn.classList.add('bg-primary', 'text-primary-content', 'shadow-sm');
                    btn.classList.remove('text-base-content/60', 'hover:text-base-content', 'hover:bg-base-300/50');
                } else {
                    btn.classList.remove('bg-primary', 'text-primary-content', 'shadow-sm');
                    btn.classList.add('text-base-content/60', 'hover:text-base-content', 'hover:bg-base-300/50');
                }
            });

            this.activeTab = newSlug;

            // Load lazy modules for this tab if not already loaded
            if (!this.loadedTabs.has(newSlug)) {
                this._loadTabModules(newSlug);
                if (this.isPremium) {
                    this._initDragForTab(newSlug);
                }
            }

            // Save active tab (debounced)
            this._debouncedSaveActiveTab();
        }

        async _loadTabModules(tabSlug) {
            const tabConfig = this.tabsConfig[tabSlug];
            if (!tabConfig || !tabConfig.lazyModules) return;

            this.loadedTabs.add(tabSlug);

            const panel = document.getElementById('tab-panel-' + tabSlug);
            if (panel) panel.dataset.tabLoaded = 'true';

            const promises = tabConfig.lazyModules.map(mod => this._loadModule(mod));
            await Promise.allSettled(promises);
        }

        // -----------------------------------------------------------------
        // Lazy Loading
        // -----------------------------------------------------------------

        async _loadModule(mod) {
            const el = document.getElementById(mod.elementId);
            if (!el) return;

            try {
                const data = await PlatPursuit.API.get(
                    this.moduleDataUrl + mod.slug + '/'
                );
                const skeleton = el.querySelector('.dashboard-module-skeleton');
                if (skeleton) {
                    skeleton.outerHTML = data.html;
                } else {
                    // Replace only the card, preserving drag handle and other siblings
                    const existingCard = el.querySelector('.card');
                    if (existingCard) {
                        const tmp = document.createElement('div');
                        tmp.innerHTML = data.html;
                        const newCard = tmp.querySelector('.card') || tmp.firstElementChild;
                        if (newCard) {
                            existingCard.replaceWith(newCard);
                        } else {
                            el.innerHTML = data.html;
                        }
                    } else {
                        el.innerHTML = data.html;
                    }
                }

                if (this._moduleInits[mod.slug]) {
                    this._moduleInits[mod.slug](el);
                }
            } catch (err) {
                const skeleton = el.querySelector('.dashboard-module-skeleton');
                const name = PlatPursuit.HTMLUtils.escape(mod.slug.replace(/_/g, ' '));
                const errorHtml = `
                    <div class="card bg-base-200/80 border-2 border-base-300 shadow-lg shadow-neutral">
                        <div class="card-body p-4 lg:p-6">
                            <p class="text-base-content/50 text-sm italic pr-1">
                                Failed to load "${name}". Try refreshing the page.
                            </p>
                        </div>
                    </div>`;
                if (skeleton) {
                    skeleton.outerHTML = errorHtml;
                } else {
                    el.innerHTML = errorHtml;
                }
            }
        }

        // -----------------------------------------------------------------
        // Module Init Registry
        // -----------------------------------------------------------------

        registerModuleInit(slug, fn) {
            this._moduleInits[slug] = fn;
        }

        // -----------------------------------------------------------------
        // Quick Settings (auto-save toggles, timezone detect, region select)
        // -----------------------------------------------------------------

        _initQuickSettings() {
            const saveQuickSetting = async (setting, value, revertFn) => {
                try {
                    await PlatPursuit.API.post('/api/v1/user/quick-settings/', { setting, value });
                    PlatPursuit.ToastManager.success('Setting saved.');
                } catch (err) {
                    PlatPursuit.ToastManager.error('Failed to save setting.');
                    if (revertFn) revertFn();
                }
            };

            // Boolean toggles
            document.querySelectorAll('.quick-setting-toggle').forEach(toggle => {
                toggle.addEventListener('change', () => {
                    saveQuickSetting(toggle.dataset.setting, toggle.checked, () => {
                        toggle.checked = !toggle.checked;
                    });
                });
            });

            // Select dropdowns (region)
            document.querySelectorAll('.quick-setting-select').forEach(select => {
                const original = select.value;
                select.addEventListener('change', () => {
                    saveQuickSetting(select.dataset.setting, select.value, () => {
                        select.value = original;
                    });
                });
            });

            // Detect timezone from browser
            document.querySelectorAll('.quick-setting-detect-tz').forEach(btn => {
                btn.addEventListener('click', async () => {
                    try {
                        const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
                        if (!detected) {
                            PlatPursuit.ToastManager.error('Could not detect timezone from browser.');
                            return;
                        }
                        await PlatPursuit.API.post('/api/v1/user/quick-settings/', {
                            setting: 'user_timezone',
                            value: detected,
                        });
                        // Update the displayed timezone text
                        const label = btn.closest('.flex')?.querySelector('.truncate');
                        if (label) label.textContent = detected;
                        PlatPursuit.ToastManager.success(`Timezone set to ${detected}.`);
                    } catch (err) {
                        PlatPursuit.ToastManager.error('Failed to save timezone.');
                    }
                });
            });
        }

        _initCalendarPagination(el) {
            const grid = el.querySelector('[data-calendar-grid]');
            const prevBtn = el.querySelector('[data-calendar-prev]');
            const nextBtn = el.querySelector('[data-calendar-next]');
            const label = el.querySelector('[data-calendar-label]');
            if (!grid || !prevBtn || !nextBtn) return;

            const months = Array.from(grid.querySelectorAll('[data-calendar-month]'));
            if (months.length === 0) return;

            const PAGE_SIZE = 3;
            const TOTAL_PAGES = Math.ceil(months.length / PAGE_SIZE);

            // Start on the page containing the current month
            const todayMonth = parseInt(el.querySelector('[data-calendar-month].ring-1')?.dataset.calendarMonth || '1', 10);
            let currentPage = Math.min(Math.floor((todayMonth - 1) / PAGE_SIZE), TOTAL_PAGES - 1);

            const render = () => {
                const start = currentPage * PAGE_SIZE;
                const end = start + PAGE_SIZE;
                months.forEach((m, i) => {
                    m.style.display = (i >= start && i < end) ? '' : 'none';
                });
                prevBtn.disabled = currentPage === 0;
                nextBtn.disabled = currentPage >= TOTAL_PAGES - 1;
                // Update label
                const firstName = months[start]?.querySelector('.text-xs.font-semibold')?.textContent?.trim() || '';
                const lastName = months[Math.min(end - 1, months.length - 1)]?.querySelector('.text-xs.font-semibold')?.textContent?.trim() || '';
                if (label) label.textContent = firstName === lastName ? firstName : `${firstName} - ${lastName}`;
            };

            prevBtn.addEventListener('click', () => { if (currentPage > 0) { currentPage--; render(); } });
            nextBtn.addEventListener('click', () => { if (currentPage < TOTAL_PAGES - 1) { currentPage++; render(); } });
            render();
        }

        _initRateStrip(el) {
            const track = el.querySelector('[data-rate-track]');
            if (!track) return;

            // The track contains two copies of the games (for seamless looping).
            // Animate translateX from 0 to -halfWidth so it loops perfectly.
            const halfWidth = track.scrollWidth / 2;
            if (halfWidth <= 0) return;

            // Speed: ~40px/s feels smooth
            const duration = halfWidth / 40;

            // Inject keyframes dynamically for this specific track width
            const id = 'rate-ticker-' + Date.now();
            const style = document.createElement('style');
            style.textContent = `
                @keyframes ${id} {
                    0% { transform: translateX(0); }
                    100% { transform: translateX(-${halfWidth}px); }
                }
            `;
            document.head.appendChild(style);

            track.style.animation = `${id} ${duration}s linear infinite`;

            // Pause on hover so users can interact with individual icons
            const strip = el.querySelector('[data-rate-strip]');
            if (strip) {
                strip.addEventListener('mouseenter', () => { track.style.animationPlayState = 'paused'; });
                strip.addEventListener('mouseleave', () => { track.style.animationPlayState = 'running'; });
            }
        }

        // -----------------------------------------------------------------
        // Advanced Stats (date range switcher)
        // -----------------------------------------------------------------

        _initAdvancedStatsRange(el) {
            const rangeContainer = el.querySelector('.advanced-stats-range');
            if (!rangeContainer) return;

            rangeContainer.addEventListener('click', async (e) => {
                const btn = e.target.closest('[data-range]');
                if (!btn) return;

                const range = btn.dataset.range;
                const moduleEl = el.closest('[id^="module-"]');
                if (!moduleEl) return;

                // Update button active states
                rangeContainer.querySelectorAll('[data-range]').forEach(b => {
                    b.classList.remove('btn-primary');
                    b.classList.add('btn-ghost');
                });
                btn.classList.remove('btn-ghost');
                btn.classList.add('btn-primary');

                // Show loading state
                const grid = el.querySelector('.grid');
                if (grid) grid.style.opacity = '0.5';

                try {
                    // Re-fetch the module with the new range setting
                    const resp = await PlatPursuit.API.get(
                        `${this.moduleDataUrl}advanced_stats/?settings=${encodeURIComponent(JSON.stringify({range}))}`
                    );
                    // Replace the card inside the module wrapper (preserve drag handle)
                    const card = moduleEl.querySelector('.card');
                    if (card) {
                        const tmp = document.createElement('div');
                        tmp.innerHTML = resp.html;
                        const newCard = tmp.querySelector('.card') || tmp.firstElementChild;
                        if (newCard) {
                            card.replaceWith(newCard);
                            this._initAdvancedStatsRange(moduleEl);
                        }
                    }
                } catch {
                    if (grid) grid.style.opacity = '1';
                    PlatPursuit.ToastManager.error('Failed to update stats.');
                }
            });
        }

        // -----------------------------------------------------------------
        // Trophy Visualizations (combined: heatmap + genre radar + year review + type breakdown)
        // -----------------------------------------------------------------

        _getChartColor(name) {
            return getComputedStyle(document.documentElement).getPropertyValue(`--color-${name}`).trim();
        }

        _initTrophyVisualizations(el) {
            // Year selector (controls all visualizations)
            const yearSelect = el.querySelector('.viz-year-select');
            if (yearSelect) {
                yearSelect.addEventListener('click', async (e) => {
                    const btn = e.target.closest('[data-year]');
                    if (!btn || btn.classList.contains('btn-primary')) return;

                    const year = btn.dataset.year;
                    const moduleEl = el.closest('[id^="module-"]');
                    if (!moduleEl) return;

                    yearSelect.querySelectorAll('[data-year]').forEach(b => {
                        b.classList.remove('btn-primary');
                        b.classList.add('btn-ghost');
                    });
                    btn.classList.remove('btn-ghost');
                    btn.classList.add('btn-primary');

                    try {
                        const resp = await PlatPursuit.API.get(
                            `${this.moduleDataUrl}trophy_visualizations/?settings=${encodeURIComponent(JSON.stringify({year}))}`
                        );
                        const card = moduleEl.querySelector('.card');
                        if (card) {
                            const tmp = document.createElement('div');
                            tmp.innerHTML = resp.html;
                            const newCard = tmp.querySelector('.card') || tmp.firstElementChild;
                            if (newCard) {
                                card.replaceWith(newCard);
                                this._initTrophyVisualizations(moduleEl);
                            }
                        }
                    } catch {
                        PlatPursuit.ToastManager.error('Failed to update visualizations.');
                    }
                });
            }

            // Genre Radar (Chart.js radar)
            const radarCanvas = el.querySelector('.genre-radar-canvas');
            if (radarCanvas && typeof Chart !== 'undefined') {
                const labels = JSON.parse(radarCanvas.dataset.labels || '[]');
                const counts = JSON.parse(radarCanvas.dataset.counts || '[]');
                if (labels.length) {
                    const accentColor = this._getChartColor('accent') || '#f59e0b';
                    new Chart(radarCanvas, {
                        type: 'radar',
                        data: {
                            labels: labels,
                            datasets: [{
                                label: 'Platinums',
                                data: counts,
                                backgroundColor: 'rgba(245, 158, 11, 0.15)',
                                borderColor: accentColor.includes('oklch') ? '#f59e0b' : accentColor,
                                borderWidth: 2,
                                pointBackgroundColor: accentColor.includes('oklch') ? '#f59e0b' : accentColor,
                                pointRadius: 4,
                                pointHoverRadius: 6,
                            }],
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: true,
                            plugins: { legend: { display: false } },
                            scales: {
                                r: {
                                    beginAtZero: true,
                                    ticks: { display: false },
                                    grid: { color: 'rgba(150, 150, 150, 0.15)' },
                                    angleLines: { color: 'rgba(150, 150, 150, 0.15)' },
                                    pointLabels: { color: 'rgba(150, 150, 150, 0.6)', font: { size: 10 } },
                                },
                            },
                        },
                    });
                }
            }

            // Yearly Totals with Quarterly Breakdown (Chart.js stacked bar, all-time mode)
            const yearlyCanvas = el.querySelector('.yearly-totals-canvas');
            if (yearlyCanvas && typeof Chart !== 'undefined') {
                const labels = JSON.parse(yearlyCanvas.dataset.labels || '[]');
                const q1 = JSON.parse(yearlyCanvas.dataset.q1 || '[]');
                const q2 = JSON.parse(yearlyCanvas.dataset.q2 || '[]');
                const q3 = JSON.parse(yearlyCanvas.dataset.q3 || '[]');
                const q4 = JSON.parse(yearlyCanvas.dataset.q4 || '[]');
                if (labels.length) {
                    new Chart(yearlyCanvas, {
                        type: 'bar',
                        data: {
                            labels: labels,
                            datasets: [
                                { label: 'Q1 (Jan-Mar)', data: q1, backgroundColor: 'rgba(59, 130, 246, 0.7)' },
                                { label: 'Q2 (Apr-Jun)', data: q2, backgroundColor: 'rgba(34, 197, 94, 0.7)' },
                                { label: 'Q3 (Jul-Sep)', data: q3, backgroundColor: 'rgba(245, 158, 11, 0.7)' },
                                { label: 'Q4 (Oct-Dec)', data: q4, backgroundColor: 'rgba(239, 68, 68, 0.7)' },
                            ],
                        },
                        options: {
                            responsive: true, maintainAspectRatio: true,
                            plugins: {
                                legend: { display: true, position: 'bottom', labels: { color: 'rgba(150,150,150,0.6)', font: { size: 9 }, boxWidth: 8, padding: 10 } },
                            },
                            scales: {
                                x: { stacked: true, ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { display: false } },
                                y: { stacked: true, beginAtZero: true, ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { color: 'rgba(150,150,150,0.1)' } },
                            },
                        },
                    });
                }
            }

            // Year in Review (Chart.js line)
            const lineCanvas = el.querySelector('.year-review-canvas');
            if (lineCanvas && typeof Chart !== 'undefined') {
                const labels = JSON.parse(lineCanvas.dataset.labels || '[]');
                const currentData = JSON.parse(lineCanvas.dataset.current || '[]');
                const prevData = JSON.parse(lineCanvas.dataset.prev || '[]');
                const currentYear = lineCanvas.dataset.currentYear;
                const prevYear = lineCanvas.dataset.prevYear;
                if (labels.length) {
                    const primaryColor = this._getChartColor('primary') || '#6366f1';
                    new Chart(lineCanvas, {
                        type: 'line',
                        data: {
                            labels: labels,
                            datasets: [
                                {
                                    label: currentYear,
                                    data: currentData,
                                    borderColor: primaryColor.includes('oklch') ? '#6366f1' : primaryColor,
                                    backgroundColor: 'rgba(99, 102, 241, 0.1)',
                                    fill: true,
                                    tension: 0.3,
                                    borderWidth: 2,
                                    pointRadius: 3,
                                    pointHoverRadius: 5,
                                },
                                {
                                    label: prevYear,
                                    data: prevData,
                                    borderColor: 'rgba(150, 150, 150, 0.4)',
                                    backgroundColor: 'transparent',
                                    fill: false,
                                    tension: 0.3,
                                    borderWidth: 1.5,
                                    borderDash: [4, 4],
                                    pointRadius: 2,
                                    pointHoverRadius: 4,
                                },
                            ],
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: true,
                            plugins: {
                                legend: {
                                    display: true, position: 'bottom',
                                    labels: { color: 'rgba(150, 150, 150, 0.6)', font: { size: 10 }, boxWidth: 10, padding: 12 },
                                },
                            },
                            scales: {
                                x: { ticks: { color: 'rgba(150, 150, 150, 0.5)', font: { size: 9 } }, grid: { display: false } },
                                y: { beginAtZero: true, ticks: { color: 'rgba(150, 150, 150, 0.5)', font: { size: 9 } }, grid: { color: 'rgba(150, 150, 150, 0.1)' } },
                            },
                        },
                    });
                }
            }

            // Games Started vs Completed (Chart.js line)
            const gamesCanvas = el.querySelector('.games-flow-canvas');
            if (gamesCanvas && typeof Chart !== 'undefined') {
                const labels = JSON.parse(gamesCanvas.dataset.labels || '[]');
                const started = JSON.parse(gamesCanvas.dataset.started || '[]');
                const completed = JSON.parse(gamesCanvas.dataset.completed || '[]');
                if (labels.length) {
                    new Chart(gamesCanvas, {
                        type: 'line',
                        data: {
                            labels: labels,
                            datasets: [
                                {
                                    label: 'Started',
                                    data: started,
                                    borderColor: 'rgba(59, 130, 246, 0.8)',
                                    backgroundColor: 'rgba(59, 130, 246, 0.08)',
                                    fill: true, tension: 0.3, borderWidth: 2, pointRadius: 2, pointHoverRadius: 4,
                                },
                                {
                                    label: 'Completed',
                                    data: completed,
                                    borderColor: 'rgba(34, 197, 94, 0.8)',
                                    backgroundColor: 'rgba(34, 197, 94, 0.08)',
                                    fill: true, tension: 0.3, borderWidth: 2, pointRadius: 2, pointHoverRadius: 4,
                                },
                            ],
                        },
                        options: {
                            responsive: true, maintainAspectRatio: true,
                            plugins: {
                                legend: { display: true, position: 'bottom', labels: { color: 'rgba(150,150,150,0.6)', font: { size: 10 }, boxWidth: 10, padding: 12 } },
                            },
                            scales: {
                                x: { ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { display: false } },
                                y: { beginAtZero: true, ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 }, stepSize: 1 }, grid: { color: 'rgba(150,150,150,0.1)' } },
                            },
                        },
                    });
                }
            }

            // Trophy Progress (Chart.js cumulative earned vs unearned)
            const progressCanvas = el.querySelector('.trophy-progress-canvas');
            if (progressCanvas && typeof Chart !== 'undefined') {
                const labels = JSON.parse(progressCanvas.dataset.labels || '[]');
                const earned = JSON.parse(progressCanvas.dataset.earned || '[]');
                const unearned = JSON.parse(progressCanvas.dataset.unearned || '[]');
                if (labels.length) {
                    const primaryColor = this._getChartColor('primary') || '#6366f1';
                    new Chart(progressCanvas, {
                        type: 'line',
                        data: {
                            labels: labels,
                            datasets: [
                                {
                                    label: 'Earned',
                                    data: earned,
                                    borderColor: primaryColor.includes('oklch') ? '#6366f1' : primaryColor,
                                    backgroundColor: 'rgba(99, 102, 241, 0.08)',
                                    fill: true, tension: 0.3, borderWidth: 2, pointRadius: 2, pointHoverRadius: 4,
                                },
                                {
                                    label: 'Unearned',
                                    data: unearned,
                                    borderColor: 'rgba(239, 68, 68, 0.5)',
                                    backgroundColor: 'rgba(239, 68, 68, 0.05)',
                                    fill: true, tension: 0.3, borderWidth: 2, pointRadius: 2, pointHoverRadius: 4,
                                },
                            ],
                        },
                        options: {
                            responsive: true, maintainAspectRatio: true,
                            plugins: {
                                legend: { display: true, position: 'bottom', labels: { color: 'rgba(150,150,150,0.6)', font: { size: 10 }, boxWidth: 10, padding: 12 } },
                            },
                            scales: {
                                x: { ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { display: false } },
                                y: { ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { color: 'rgba(150,150,150,0.1)' } },
                            },
                        },
                    });
                }
            }
        }

        // -----------------------------------------------------------------
        // Advanced Badge Stats (date range switcher)
        // -----------------------------------------------------------------

        _initAdvancedBadgeStatsRange(el) {
            const rangeContainer = el.querySelector('.badge-stats-range');
            if (!rangeContainer) return;

            rangeContainer.addEventListener('click', async (e) => {
                const btn = e.target.closest('[data-range]');
                if (!btn) return;

                const range = btn.dataset.range;
                const moduleEl = el.closest('[id^="module-"]');
                if (!moduleEl) return;

                rangeContainer.querySelectorAll('[data-range]').forEach(b => {
                    b.classList.remove('btn-primary');
                    b.classList.add('btn-ghost');
                });
                btn.classList.remove('btn-ghost');
                btn.classList.add('btn-primary');

                const grid = el.querySelector('.grid');
                if (grid) grid.style.opacity = '0.5';

                try {
                    const resp = await PlatPursuit.API.get(
                        `${this.moduleDataUrl}advanced_badge_stats/?settings=${encodeURIComponent(JSON.stringify({range}))}`
                    );
                    const card = moduleEl.querySelector('.card');
                    if (card) {
                        const tmp = document.createElement('div');
                        tmp.innerHTML = resp.html;
                        const newCard = tmp.querySelector('.card') || tmp.firstElementChild;
                        if (newCard) {
                            card.replaceWith(newCard);
                            this._initAdvancedBadgeStatsRange(moduleEl);
                        }
                    }
                } catch {
                    if (grid) grid.style.opacity = '1';
                    PlatPursuit.ToastManager.error('Failed to update badge stats.');
                }
            });
        }

        // -----------------------------------------------------------------
        // Badge Series Overview (stage progress bar + series XP radar)
        // -----------------------------------------------------------------

        _initBadgeSeriesOverview(el) {
            // Stage Progress bar chart
            const stageCanvas = el.querySelector('.badge-stage-progress-canvas');
            if (stageCanvas && typeof Chart !== 'undefined') {
                const labels = JSON.parse(stageCanvas.dataset.labels || '[]');
                const completed = JSON.parse(stageCanvas.dataset.completed || '[]');
                const remaining = JSON.parse(stageCanvas.dataset.remaining || '[]');
                const colors = JSON.parse(stageCanvas.dataset.colors || '[]');
                if (labels.length) {
                    new Chart(stageCanvas, {
                        type: 'bar',
                        data: {
                            labels: labels,
                            datasets: [
                                { label: 'Completed', data: completed, backgroundColor: 'rgba(34, 197, 94, 0.6)', borderRadius: 2 },
                                { label: 'Remaining', data: remaining, backgroundColor: colors.map(c => c + '33'), borderRadius: 2 },
                            ],
                        },
                        options: {
                            indexAxis: 'y',
                            responsive: true, maintainAspectRatio: false,
                            plugins: { legend: { display: false } },
                            scales: {
                                x: { stacked: true, ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { color: 'rgba(150,150,150,0.1)' } },
                                y: { stacked: true, ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { display: false } },
                            },
                        },
                    });
                }
            }

            // Series XP Radar
            const radarCanvas = el.querySelector('.badge-series-radar-canvas');
            if (radarCanvas && typeof Chart !== 'undefined') {
                const labels = JSON.parse(radarCanvas.dataset.labels || '[]');
                const counts = JSON.parse(radarCanvas.dataset.counts || '[]');
                if (labels.length) {
                    new Chart(radarCanvas, {
                        type: 'radar',
                        data: {
                            labels: labels,
                            datasets: [{
                                label: 'XP',
                                data: counts,
                                backgroundColor: 'rgba(245, 158, 11, 0.15)',
                                borderColor: '#f59e0b',
                                borderWidth: 2,
                                pointBackgroundColor: '#f59e0b',
                                pointRadius: 4,
                                pointHoverRadius: 6,
                            }],
                        },
                        options: {
                            responsive: true, maintainAspectRatio: true,
                            plugins: { legend: { display: false } },
                            scales: {
                                r: {
                                    beginAtZero: true,
                                    ticks: { display: false },
                                    grid: { color: 'rgba(150,150,150,0.15)' },
                                    angleLines: { color: 'rgba(150,150,150,0.15)' },
                                    pointLabels: { color: 'rgba(150,150,150,0.6)', font: { size: 10 } },
                                },
                            },
                        },
                    });
                }
            }
        }

        // -----------------------------------------------------------------
        // Badge Visualizations (stage progress + series breakdown + XP growth + completion tracking)
        // -----------------------------------------------------------------

        _initBadgeVisualizations(el) {
            // Year selector
            const yearSelect = el.querySelector('.badge-viz-year-select');
            if (yearSelect) {
                yearSelect.addEventListener('click', async (e) => {
                    const btn = e.target.closest('[data-year]');
                    if (!btn || btn.classList.contains('btn-primary')) return;

                    const year = btn.dataset.year;
                    const moduleEl = el.closest('[id^="module-"]');
                    if (!moduleEl) return;

                    yearSelect.querySelectorAll('[data-year]').forEach(b => {
                        b.classList.remove('btn-primary');
                        b.classList.add('btn-ghost');
                    });
                    btn.classList.remove('btn-ghost');
                    btn.classList.add('btn-primary');

                    try {
                        const resp = await PlatPursuit.API.get(
                            `${this.moduleDataUrl}badge_visualizations/?settings=${encodeURIComponent(JSON.stringify({year}))}`
                        );
                        const card = moduleEl.querySelector('.card');
                        if (card) {
                            const tmp = document.createElement('div');
                            tmp.innerHTML = resp.html;
                            const newCard = tmp.querySelector('.card') || tmp.firstElementChild;
                            if (newCard) {
                                card.replaceWith(newCard);
                                this._initBadgeVisualizations(moduleEl);
                            }
                        }
                    } catch {
                        PlatPursuit.ToastManager.error('Failed to update badge visualizations.');
                    }
                });
            }

            // XP Growth (Chart.js line, year mode)
            const xpCanvas = el.querySelector('.badge-xp-growth-canvas');
            if (xpCanvas && typeof Chart !== 'undefined') {
                const labels = JSON.parse(xpCanvas.dataset.labels || '[]');
                const cumulative = JSON.parse(xpCanvas.dataset.cumulative || '[]');
                if (labels.length) {
                    new Chart(xpCanvas, {
                        type: 'line',
                        data: {
                            labels: labels,
                            datasets: [{
                                label: 'Total XP',
                                data: cumulative,
                                borderColor: '#f59e0b',
                                backgroundColor: 'rgba(245, 158, 11, 0.08)',
                                fill: true, tension: 0.3, borderWidth: 2, pointRadius: 2, pointHoverRadius: 4,
                            }],
                        },
                        options: {
                            responsive: true, maintainAspectRatio: true,
                            plugins: { legend: { display: false } },
                            scales: {
                                x: { ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { display: false } },
                                y: { ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { color: 'rgba(150,150,150,0.1)' } },
                            },
                        },
                    });
                }
            }

            // Yearly XP Totals (Chart.js stacked bar, all-time mode)
            const yearlyCanvas = el.querySelector('.badge-yearly-xp-canvas');
            if (yearlyCanvas && typeof Chart !== 'undefined') {
                const labels = JSON.parse(yearlyCanvas.dataset.labels || '[]');
                const q1 = JSON.parse(yearlyCanvas.dataset.q1 || '[]');
                const q2 = JSON.parse(yearlyCanvas.dataset.q2 || '[]');
                const q3 = JSON.parse(yearlyCanvas.dataset.q3 || '[]');
                const q4 = JSON.parse(yearlyCanvas.dataset.q4 || '[]');
                if (labels.length) {
                    new Chart(yearlyCanvas, {
                        type: 'bar',
                        data: {
                            labels: labels,
                            datasets: [
                                { label: 'Q1', data: q1, backgroundColor: 'rgba(59, 130, 246, 0.7)' },
                                { label: 'Q2', data: q2, backgroundColor: 'rgba(34, 197, 94, 0.7)' },
                                { label: 'Q3', data: q3, backgroundColor: 'rgba(245, 158, 11, 0.7)' },
                                { label: 'Q4', data: q4, backgroundColor: 'rgba(239, 68, 68, 0.7)' },
                            ],
                        },
                        options: {
                            responsive: true, maintainAspectRatio: true,
                            plugins: {
                                legend: { display: true, position: 'bottom', labels: { color: 'rgba(150,150,150,0.6)', font: { size: 9 }, boxWidth: 8, padding: 10 } },
                            },
                            scales: {
                                x: { stacked: true, ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { display: false } },
                                y: { stacked: true, beginAtZero: true, ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { color: 'rgba(150,150,150,0.1)' } },
                            },
                        },
                    });
                }
            }

            // Stages by Series (Chart.js stacked bar with dynamic datasets)
            const seriesCanvas = el.querySelector('.badge-stages-by-series-canvas');
            if (seriesCanvas && typeof Chart !== 'undefined') {
                const labels = JSON.parse(seriesCanvas.dataset.labels || '[]');
                const datasets = JSON.parse(seriesCanvas.dataset.datasets || '[]');
                if (labels.length && datasets.length) {
                    new Chart(seriesCanvas, {
                        type: 'bar',
                        data: {
                            labels: labels,
                            datasets: datasets.map(ds => ({
                                label: ds.label,
                                data: ds.data,
                                backgroundColor: ds.color,
                            })),
                        },
                        options: {
                            responsive: true, maintainAspectRatio: true,
                            plugins: {
                                legend: { display: true, position: 'bottom', labels: { color: 'rgba(150,150,150,0.6)', font: { size: 9 }, boxWidth: 8, padding: 8 } },
                            },
                            scales: {
                                x: { stacked: true, ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { display: false } },
                                y: { stacked: true, beginAtZero: true, ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { color: 'rgba(150,150,150,0.1)' } },
                            },
                        },
                    });
                }
            }

            // Badge Growth (cumulative line)
            const growthCanvas = el.querySelector('.badge-growth-canvas');
            if (growthCanvas && typeof Chart !== 'undefined') {
                const labels = JSON.parse(growthCanvas.dataset.labels || '[]');
                const cumulative = JSON.parse(growthCanvas.dataset.cumulative || '[]');
                if (labels.length) {
                    new Chart(growthCanvas, {
                        type: 'line',
                        data: {
                            labels: labels,
                            datasets: [{
                                label: 'Badges',
                                data: cumulative,
                                borderColor: 'rgba(34, 197, 94, 0.8)',
                                backgroundColor: 'rgba(34, 197, 94, 0.08)',
                                fill: true, tension: 0.3, borderWidth: 2, pointRadius: 2, pointHoverRadius: 4,
                            }],
                        },
                        options: {
                            responsive: true, maintainAspectRatio: true,
                            plugins: { legend: { display: false } },
                            scales: {
                                x: { ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { display: false } },
                                y: { beginAtZero: true, ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { color: 'rgba(150,150,150,0.1)' } },
                            },
                        },
                    });
                }
            }

            // Badge Earning Rate (bar chart)
            const badgeRateCanvas = el.querySelector('.badge-earning-rate-canvas');
            if (badgeRateCanvas && typeof Chart !== 'undefined') {
                const labels = JSON.parse(badgeRateCanvas.dataset.labels || '[]');
                const monthly = JSON.parse(badgeRateCanvas.dataset.monthly || '[]');
                if (labels.length) {
                    new Chart(badgeRateCanvas, {
                        type: 'bar',
                        data: {
                            labels: labels,
                            datasets: [{
                                label: 'Badges',
                                data: monthly,
                                backgroundColor: 'rgba(34, 197, 94, 0.5)',
                                borderColor: 'rgba(34, 197, 94, 0.8)',
                                borderWidth: 1,
                                borderRadius: 2,
                            }],
                        },
                        options: {
                            responsive: true, maintainAspectRatio: true,
                            plugins: { legend: { display: false } },
                            scales: {
                                x: { ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { display: false } },
                                y: { beginAtZero: true, ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 }, stepSize: 1 }, grid: { color: 'rgba(150,150,150,0.1)' } },
                            },
                        },
                    });
                }
            }

            // Stage Growth (cumulative line)
            const stageGrowthCanvas = el.querySelector('.badge-stage-growth-canvas');
            if (stageGrowthCanvas && typeof Chart !== 'undefined') {
                const labels = JSON.parse(stageGrowthCanvas.dataset.labels || '[]');
                const cumulative = JSON.parse(stageGrowthCanvas.dataset.cumulative || '[]');
                if (labels.length) {
                    new Chart(stageGrowthCanvas, {
                        type: 'line',
                        data: {
                            labels: labels,
                            datasets: [{
                                label: 'Stages',
                                data: cumulative,
                                borderColor: 'rgba(59, 130, 246, 0.8)',
                                backgroundColor: 'rgba(59, 130, 246, 0.08)',
                                fill: true, tension: 0.3, borderWidth: 2, pointRadius: 2, pointHoverRadius: 4,
                            }],
                        },
                        options: {
                            responsive: true, maintainAspectRatio: true,
                            plugins: { legend: { display: false } },
                            scales: {
                                x: { ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { display: false } },
                                y: { beginAtZero: true, ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { color: 'rgba(150,150,150,0.1)' } },
                            },
                        },
                    });
                }
            }

            // Stage Completion Rate (bar chart)
            const rateCanvas = el.querySelector('.badge-stage-rate-canvas');
            if (rateCanvas && typeof Chart !== 'undefined') {
                const labels = JSON.parse(rateCanvas.dataset.labels || '[]');
                const monthly = JSON.parse(rateCanvas.dataset.monthly || '[]');
                if (labels.length) {
                    new Chart(rateCanvas, {
                        type: 'bar',
                        data: {
                            labels: labels,
                            datasets: [{
                                label: 'Stages',
                                data: monthly,
                                backgroundColor: 'rgba(59, 130, 246, 0.5)',
                                borderColor: 'rgba(59, 130, 246, 0.8)',
                                borderWidth: 1,
                                borderRadius: 2,
                            }],
                        },
                        options: {
                            responsive: true, maintainAspectRatio: true,
                            plugins: { legend: { display: false } },
                            scales: {
                                x: { ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 } }, grid: { display: false } },
                                y: { beginAtZero: true, ticks: { color: 'rgba(150,150,150,0.5)', font: { size: 9 }, stepSize: 1 }, grid: { color: 'rgba(150,150,150,0.1)' } },
                            },
                        },
                    });
                }
            }
        }

        // -----------------------------------------------------------------
        // Premium Settings (theme picker + background art)
        // -----------------------------------------------------------------

        _initPremiumSettings(el) {
            // Theme change button
            const changeBtn = el.querySelector('.premium-change-theme');
            if (changeBtn) {
                changeBtn.addEventListener('click', () => {
                    if (!PlatPursuit.getColorGridModal) {
                        PlatPursuit.ToastManager.error('Theme picker not available.');
                        return;
                    }
                    const cgm = PlatPursuit.getColorGridModal();
                    const currentTheme = el.querySelector('.premium-theme-swatch')?.dataset.themeKey || 'default';
                    cgm.open(currentTheme, async (themeKey) => {
                        try {
                            await PlatPursuit.API.post('/api/v1/user/quick-settings/', {
                                setting: 'selected_theme', value: themeKey || '',
                            });
                            // Update swatch preview
                            const swatch = el.querySelector('.premium-theme-swatch');
                            const nameEl = el.querySelector('.premium-theme-name');
                            const themes = window.GRADIENT_THEMES || {};
                            const t = themes[themeKey];
                            if (swatch) {
                                swatch.style = t ? `background: ${t.background};` : 'background: var(--b2);';
                                if (t && t.backgroundSize) swatch.style.backgroundSize = t.backgroundSize;
                                swatch.dataset.themeKey = themeKey || '';
                            }
                            if (nameEl) nameEl.textContent = t ? t.name : 'Default';
                            PlatPursuit.ToastManager.success('Theme updated!');
                            // Apply to page body
                            document.body.style = t ? `background: ${t.background}; background-attachment: fixed;` : '';
                        } catch (err) {
                            const errData = await err.response?.json().catch(() => null);
                            PlatPursuit.ToastManager.error(errData?.error || 'Failed to update theme.');
                        }
                    });
                });
            }

            // Clear theme button
            const clearThemeBtn = el.querySelector('.premium-clear-theme');
            if (clearThemeBtn) {
                clearThemeBtn.addEventListener('click', async () => {
                    try {
                        await PlatPursuit.API.post('/api/v1/user/quick-settings/', {
                            setting: 'selected_theme', value: '',
                        });
                        const swatch = el.querySelector('.premium-theme-swatch');
                        const nameEl = el.querySelector('.premium-theme-name');
                        if (swatch) {
                            swatch.style = 'background: var(--b2);';
                            swatch.dataset.themeKey = '';
                        }
                        if (nameEl) nameEl.textContent = 'Default';
                        clearThemeBtn.remove();
                        document.body.style = '';
                        PlatPursuit.ToastManager.success('Theme reset to default.');
                    } catch (err) {
                        const errData = await err.response?.json().catch(() => null);
                        PlatPursuit.ToastManager.error(errData?.error || 'Failed to reset theme.');
                    }
                });
            }

            // Background picker (use event delegation for dynamically replaced buttons)
            const bgSearch = el.querySelector('.premium-bg-search');
            const bgInput = el.querySelector('.premium-bg-search-input');
            const bgResults = el.querySelector('.premium-bg-search-results');

            // Open search (delegated)
            el.addEventListener('click', (e) => {
                if (!e.target.closest('.premium-change-bg')) return;
                const bgDisplay = el.querySelector('.premium-bg-display');
                if (bgDisplay) bgDisplay.classList.add('hidden');
                if (bgSearch) bgSearch.classList.remove('hidden');
                if (bgInput) { bgInput.value = ''; bgInput.focus(); }
                if (bgResults) bgResults.innerHTML = '<p class="text-xs text-base-content/40 text-center py-2">Type to search your platted games</p>';
            });

            // Cancel search
            el.addEventListener('click', (e) => {
                if (!e.target.closest('.premium-bg-search-cancel')) return;
                if (bgSearch) bgSearch.classList.add('hidden');
                const bgDisplay = el.querySelector('.premium-bg-display');
                if (bgDisplay) bgDisplay.classList.remove('hidden');
            });

            // Debounced search
            let bgSearchTimer;
            if (bgInput) {
                bgInput.addEventListener('input', () => {
                    clearTimeout(bgSearchTimer);
                    const q = bgInput.value.trim();
                    if (q.length < 2) {
                        if (bgResults) bgResults.innerHTML = '<p class="text-xs text-base-content/40 text-center py-2">Type to search your platted games</p>';
                        return;
                    }
                    bgSearchTimer = setTimeout(async () => {
                        try {
                            const resp = await PlatPursuit.API.get(`/api/v1/game-backgrounds/?q=${encodeURIComponent(q)}`);
                            if (!resp.results || resp.results.length === 0) {
                                bgResults.innerHTML = '<p class="text-xs text-base-content/40 text-center py-2">No games found with background art</p>';
                                return;
                            }
                            bgResults.innerHTML = resp.results.map(r => `
                                <button type="button" class="flex items-center gap-2 w-full p-1.5 rounded-lg hover:bg-base-300/60 transition-colors premium-bg-result" data-concept-id="${r.concept_id}" data-bg-url="${r.bg_url}" data-title="${PlatPursuit.HTMLUtils.escape(r.title_name)}">
                                    <div class="w-12 h-6 rounded overflow-hidden ring-1 ring-base-300 shrink-0">
                                        <img src="${r.bg_url}" alt="" class="w-full h-full object-cover" loading="lazy">
                                    </div>
                                    <span class="text-xs text-base-content/70 flex-1 text-left line-clamp-1 pr-1">${PlatPursuit.HTMLUtils.escape(r.title_name)}</span>
                                </button>
                            `).join('');
                        } catch {
                            bgResults.innerHTML = '<p class="text-xs text-error/60 text-center py-2">Search failed</p>';
                        }
                    }, 300);
                });
            }

            // Select background result
            if (bgResults) {
                bgResults.addEventListener('click', async (e) => {
                    const resultBtn = e.target.closest('.premium-bg-result');
                    if (!resultBtn) return;

                    const conceptId = resultBtn.dataset.conceptId;
                    const bgUrl = resultBtn.dataset.bgUrl;
                    const title = resultBtn.dataset.title;

                    try {
                        await PlatPursuit.API.post('/api/v1/user/quick-settings/', {
                            setting: 'selected_background', value: conceptId,
                        });
                        const display = el.querySelector('.premium-bg-display');
                        if (display) {
                            display.innerHTML = `
                                <div class="flex items-center gap-3">
                                    <div class="w-16 h-8 rounded-lg overflow-hidden ring-1 ring-base-300 shrink-0">
                                        <img src="${bgUrl}" alt="" class="w-full h-full object-cover">
                                    </div>
                                    <div class="flex-1 min-w-0">
                                        <div class="text-sm font-medium line-clamp-1 pr-1 premium-bg-title">${PlatPursuit.HTMLUtils.escape(title)}</div>
                                        <div class="text-xs text-base-content/40">Background art</div>
                                    </div>
                                    <div class="flex gap-1 shrink-0">
                                        <button type="button" class="btn btn-ghost btn-xs premium-change-bg" title="Change background">
                                            <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
                                            Change
                                        </button>
                                        <button type="button" class="btn btn-ghost btn-xs text-error premium-clear-bg" title="Remove background">
                                            <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                                        </button>
                                    </div>
                                </div>
                            `;
                            display.classList.remove('hidden');
                        }
                        if (bgSearch) bgSearch.classList.add('hidden');
                        PlatPursuit.ToastManager.success('Background updated!');
                    } catch (err) {
                        const errData = await err.response?.json().catch(() => null);
                        PlatPursuit.ToastManager.error(errData?.error || 'Failed to update background.');
                    }
                });
            }

            // Clear background (delegated)
            el.addEventListener('click', async (e) => {
                if (!e.target.closest('.premium-clear-bg')) return;
                try {
                    await PlatPursuit.API.post('/api/v1/user/quick-settings/', {
                        setting: 'selected_background', value: '',
                    });
                    const display = el.querySelector('.premium-bg-display');
                    if (display) {
                        display.innerHTML = `
                            <div class="flex items-center gap-3">
                                <div class="w-16 h-8 rounded-lg bg-base-300/50 flex items-center justify-center shrink-0">
                                    <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 text-base-content/20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>
                                </div>
                                <div class="flex-1 min-w-0">
                                    <div class="text-sm text-base-content/50">No background set</div>
                                    <div class="text-xs text-base-content/40">Choose concept art for your profile</div>
                                </div>
                                <button type="button" class="btn btn-ghost btn-xs premium-change-bg">Set Up</button>
                            </div>
                        `;
                    }
                    PlatPursuit.ToastManager.success('Background removed.');
                } catch {
                    PlatPursuit.ToastManager.error('Failed to remove background.');
                }
            });

        }

        // -----------------------------------------------------------------
        // Share Card Previews (generic: platinum, challenge, recap)
        // -----------------------------------------------------------------

        _initShareCards(el) {
            const themes = window.GRADIENT_THEMES || {};
            const scaleFns = [];

            // Apply theme to a share card's DOM (matches profile card pattern)
            const applyTheme = (themeKey, contentEl, gameImages) => {
                const shareContent = contentEl.querySelector('.share-image-content');
                if (!shareContent) return;
                if (!themes[themeKey]) return;

                const t = themes[themeKey];

                // Handle game art themes (need image URL composited into background)
                if (t.requiresGameImage && gameImages) {
                    const source = t.gameImageSource || 'game_image';
                    const imageUrl = gameImages[source] || gameImages.game_image;
                    if (imageUrl) {
                        shareContent.style.background = 'linear-gradient(rgba(26, 27, 31, 0.85), rgba(26, 27, 31, 0.9)), url("' + imageUrl + '")';
                        shareContent.style.backgroundSize = 'cover';
                        shareContent.style.backgroundPosition = 'center';
                        shareContent.style.backgroundRepeat = '';
                    } else {
                        return; // No image available, skip
                    }
                } else {
                    shareContent.style.background = t.background;
                    if (t.backgroundSize) shareContent.style.backgroundSize = t.backgroundSize;
                    else shareContent.style.backgroundSize = '';
                    if (t.backgroundPosition) shareContent.style.backgroundPosition = t.backgroundPosition;
                    else shareContent.style.backgroundPosition = '';
                    if (t.backgroundRepeat) shareContent.style.backgroundRepeat = t.backgroundRepeat;
                    else shareContent.style.backgroundRepeat = '';
                }

                // Update banner accent if theme provides one
                if (t.bannerBackground) {
                    const banner = shareContent.querySelector('[data-element]');
                    if (banner) banner.style.background = t.bannerBackground;
                }
            };

            // Create a shared full-size preview modal (once per module)
            let modal = el.querySelector('.share-card-modal');
            if (!modal) {
                modal = document.createElement('dialog');
                modal.className = 'share-card-modal modal backdrop-blur-sm';
                modal.innerHTML =
                    '<div class="modal-box max-w-none w-auto p-4 bg-base-200 border-2 border-base-300" style="max-height: none; overflow: visible;">'
                    + '<form method="dialog"><button class="btn btn-sm btn-circle btn-ghost absolute right-2 top-2 z-10">'
                    + '<svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>'
                    + '</button></form>'
                    + '<div class="share-card-modal-content flex justify-center"></div>'
                    + '</div>'
                    + '<form method="dialog" class="modal-backdrop"><button>close</button></form>';
                el.appendChild(modal);
            }
            const modalContent = modal.querySelector('.share-card-modal-content');

            // Find all preview containers within this module
            el.querySelectorAll('.share-card-preview').forEach(preview => {
                const baseHtmlUrl = preview.dataset.shareHtmlUrl;
                const basePngUrl = preview.dataset.sharePngUrl;
                if (!baseHtmlUrl) return;

                const content = preview.querySelector('.share-card-content');
                const loading = preview.querySelector('.share-card-loading');
                if (!content || !loading) return;

                let currentTheme = 'default';
                const supportsGameArt = preview.dataset.supportsGameArt === 'true';
                // Stores game image URLs from API response (for game art themes)
                let gameImages = null;

                const scaleToFit = () => {
                    if (content.style.display !== 'none') {
                        content.style.transform = 'scale(' + (preview.offsetWidth / 1200) + ')';
                    }
                };

                // Update game art swatch backgrounds once images are available
                let swatchGrid = null;
                const updateGameArtSwatches = () => {
                    if (!swatchGrid || !gameImages) return;
                    swatchGrid.querySelectorAll('[data-game-art]').forEach(btn => {
                        const t = themes[btn.dataset.themeKey];
                        if (!t) return;
                        const source = t.gameImageSource || 'game_image';
                        const url = gameImages[source] || gameImages.game_image;
                        if (url) {
                            btn.style.background = 'linear-gradient(rgba(26, 27, 31, 0.7), rgba(26, 27, 31, 0.8)), url("' + url + '")';
                            btn.style.backgroundSize = 'cover';
                            btn.style.backgroundPosition = 'center';
                        }
                    });
                };

                const fetchPreview = () => {
                    loading.style.display = '';
                    loading.innerHTML = '<span class="loading loading-spinner loading-sm"></span>';
                    content.style.display = 'none';

                    PlatPursuit.API.get(baseHtmlUrl)
                        .then(data => {
                            content.innerHTML = data.html;
                            content.style.display = 'block';
                            content.style.width = '1200px';
                            content.style.height = '630px';
                            loading.style.display = 'none';
                            // Capture game image URLs for game art themes
                            if (supportsGameArt) {
                                gameImages = {};
                                if (data.game_image_base64) gameImages.game_image = data.game_image_base64;
                                if (data.concept_bg_base64) gameImages.concept_bg_url = data.concept_bg_base64;
                                updateGameArtSwatches();
                            }
                            // Store rating metadata for download prompt (platinum cards)
                            if (data.concept_id) {
                                preview.dataset.conceptId = data.concept_id;
                                preview.dataset.hasRating = data.has_rating ? 'true' : 'false';
                                preview.dataset.isShovelware = data.is_shovelware ? 'true' : 'false';
                                preview.dataset.playtime = data.playtime || '';
                            }
                            requestAnimationFrame(() => {
                                scaleToFit();
                                if (currentTheme !== 'default') applyTheme(currentTheme, content, gameImages);
                            });
                        })
                        .catch(() => {
                            loading.innerHTML = '<span class="text-xs text-base-content/30">Unable to load preview</span>';
                        });
                };

                fetchPreview();

                // Click preview to open full-size modal
                preview.style.cursor = 'pointer';
                preview.addEventListener('click', (e) => {
                    if (e.target.closest('button') || e.target.closest('a')) return;
                    if (!modalContent || content.style.display === 'none') return;
                    modalContent.innerHTML = content.innerHTML;
                    const fullCard = modalContent.querySelector('.share-image-content');
                    if (fullCard) fullCard.style.transform = '';
                    if (currentTheme !== 'default') applyTheme(currentTheme, modalContent, gameImages);
                    modal.showModal();
                });

                // Populate theme swatches (skip game art themes unless this card supports them)
                const swatchContainer = preview.parentElement?.querySelector('.share-card-swatches');
                if (swatchContainer && Object.keys(themes).length > 0) {
                    swatchGrid = document.createElement('div');
                    swatchGrid.className = 'grid grid-cols-8 lg:grid-cols-10 gap-1';
                    for (const [key, t] of Object.entries(themes)) {
                        // Skip game art themes unless this preview supports them
                        if (t.requiresGameImage && !supportsGameArt) continue;

                        const btn = document.createElement('button');
                        btn.className = 'aspect-square rounded border-2 transition-all duration-150 hover:scale-105 cursor-pointer '
                            + (key === 'default' ? 'border-primary ring-1 ring-primary' : 'border-base-content/20 hover:border-base-content/40');
                        btn.style.background = t.background || '';
                        if (t.requiresGameImage) btn.dataset.gameArt = 'true';
                        btn.title = t.name || key;
                        btn.dataset.themeKey = key;
                        btn.addEventListener('click', () => {
                            currentTheme = key;
                            applyTheme(key, content, gameImages);
                            swatchGrid.querySelectorAll('button').forEach(s => {
                                const sel = s.dataset.themeKey === key;
                                s.classList.toggle('border-primary', sel);
                                s.classList.toggle('ring-1', sel);
                                s.classList.toggle('ring-primary', sel);
                                s.classList.toggle('border-base-content/20', !sel);
                            });
                        });
                        swatchGrid.appendChild(btn);
                    }
                    swatchContainer.appendChild(swatchGrid);
                }

                scaleFns.push(scaleToFit);
            });

            // Single shared resize handler for all previews in this module
            let resizeTimer;
            window.addEventListener('resize', () => {
                clearTimeout(resizeTimer);
                resizeTimer = setTimeout(() => scaleFns.forEach(fn => fn()), 150);
            });

            // Track which cards have already shown the rating prompt this session
            const promptedIds = new Set();

            // Build the download URL for a given preview
            const buildDownloadUrl = (preview) => {
                const basePngUrl = preview.dataset.sharePngUrl;
                if (!basePngUrl) return null;
                const swatchBtn = preview.parentElement?.querySelector('.share-card-swatches button.border-primary');
                const themeKey = swatchBtn?.dataset.themeKey || 'default';
                const sep = basePngUrl.includes('?') ? '&' : '?';
                return themeKey !== 'default' ? basePngUrl + sep + 'theme=' + encodeURIComponent(themeKey) : basePngUrl;
            };

            // Download buttons
            el.querySelectorAll('.share-card-download').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const nearestPreview = btn.closest('.card-body')?.querySelector('.share-card-preview')
                        || btn.closest('[class*="space-y"]')?.querySelector('.share-card-preview');
                    const basePngUrl = btn.dataset.pngUrl || nearestPreview?.dataset.sharePngUrl;
                    if (!basePngUrl) return;

                    const downloadUrl = nearestPreview ? buildDownloadUrl(nearestPreview) : basePngUrl;
                    if (!downloadUrl) return;

                    // Check if this card has rating data and should prompt
                    const conceptId = nearestPreview?.dataset.conceptId;
                    const hasRating = nearestPreview?.dataset.hasRating === 'true';
                    const isShovelware = nearestPreview?.dataset.isShovelware === 'true';
                    const playtime = nearestPreview?.dataset.playtime || '';

                    if (conceptId && !hasRating && !isShovelware && !promptedIds.has(conceptId)) {
                        promptedIds.add(conceptId);
                        this._showRatingPrompt(conceptId, playtime, downloadUrl, nearestPreview);
                        return;
                    }

                    window.location.href = downloadUrl;
                });
            });
        }

        // -----------------------------------------------------------------
        // Rate Before Download (platinum share cards)
        // -----------------------------------------------------------------

        _showRatingPrompt(conceptId, playtime, downloadUrl, preview) {
            const modal = document.getElementById('rate-before-download-modal');
            if (!modal) {
                console.warn('rate-before-download-modal not found in DOM. Rating prompt disabled.');
                window.location.href = downloadUrl;
                return;
            }

            // Set game title
            const titleEl = document.getElementById('rbd-game-title');
            if (titleEl) titleEl.textContent = preview?.dataset.gameName || 'Rate This Platinum';

            // Reset form
            const form = document.getElementById('rbd-rating-form');
            if (form) form.reset();

            // Update slider displays
            const mappings = {
                'overall_rating': 'rbd-overall-value',
                'difficulty': 'rbd-difficulty-value',
                'grindiness': 'rbd-grindiness-value',
                'fun_ranking': 'rbd-fun-value',
            };
            for (const [inputName, displayId] of Object.entries(mappings)) {
                const input = form?.querySelector('[name="' + inputName + '"]');
                const display = document.getElementById(displayId);
                if (input && display) {
                    display.textContent = inputName === 'overall_rating'
                        ? parseFloat(input.value).toFixed(1) : input.value;
                }
            }

            // Wire slider live updates (clone to remove old listeners)
            modal.querySelectorAll('input[type="range"]').forEach(slider => {
                const fresh = slider.cloneNode(true);
                slider.parentNode.replaceChild(fresh, slider);
                fresh.addEventListener('input', () => {
                    for (const [n, d] of Object.entries(mappings)) {
                        const inp = form?.querySelector('[name="' + n + '"]');
                        const disp = document.getElementById(d);
                        if (inp && disp) disp.textContent = n === 'overall_rating'
                            ? parseFloat(inp.value).toFixed(1) : inp.value;
                    }
                });
            });

            // Playtime hint
            const playtimeHint = document.getElementById('rbd-playtime-hint');
            if (playtimeHint) {
                if (playtime) {
                    playtimeHint.textContent = 'Your tracked playtime: ' + playtime;
                    playtimeHint.classList.remove('hidden');
                } else {
                    playtimeHint.classList.add('hidden');
                }
            }

            // Hours input enables submit
            const hoursInput = form?.querySelector('[name="hours_to_platinum"]');
            if (hoursInput) {
                const freshH = hoursInput.cloneNode(true);
                hoursInput.parentNode.replaceChild(freshH, hoursInput);
                freshH.addEventListener('input', () => {
                    const btn = document.getElementById('rbd-submit-btn');
                    if (btn) btn.disabled = !(parseInt(freshH.value) >= 1);
                });
            }

            // Submit: rate then download
            const submitBtn = document.getElementById('rbd-submit-btn');
            if (submitBtn) {
                const freshS = submitBtn.cloneNode(true);
                submitBtn.parentNode.replaceChild(freshS, submitBtn);
                freshS.addEventListener('click', async () => {
                    const fd = new FormData(form);
                    const hours = fd.get('hours_to_platinum');
                    if (!hours || parseInt(hours) < 1) {
                        PlatPursuit.ToastManager.error('Please enter hours to platinum (minimum 1)');
                        return;
                    }
                    freshS.classList.add('loading');
                    freshS.disabled = true;
                    try {
                        await PlatPursuit.API.post(
                            '/api/v1/reviews/' + conceptId + '/group/default/rate/',
                            {
                                difficulty: parseInt(fd.get('difficulty')),
                                grindiness: parseInt(fd.get('grindiness')),
                                fun_ranking: parseInt(fd.get('fun_ranking')),
                                hours_to_platinum: parseInt(hours),
                                overall_rating: parseFloat(fd.get('overall_rating')),
                            }
                        );
                        if (preview) preview.dataset.hasRating = 'true';
                        PlatPursuit.ToastManager.success('Rating submitted!');
                        modal.close();
                        window.location.href = downloadUrl;
                    } catch (err) {
                        const errData = await err.response?.json?.().catch(() => null);
                        PlatPursuit.ToastManager.error(errData?.error || 'Failed to submit rating.');
                    } finally {
                        freshS.classList.remove('loading');
                        freshS.disabled = false;
                    }
                });
            }

            // Skip: just download
            const skipBtn = document.getElementById('rbd-skip-btn');
            if (skipBtn) {
                const freshK = skipBtn.cloneNode(true);
                skipBtn.parentNode.replaceChild(freshK, skipBtn);
                freshK.addEventListener('click', () => {
                    modal.close();
                    window.location.href = downloadUrl;
                });
            }

            modal.showModal();
        }

        // -----------------------------------------------------------------
        // Profile Card Preview
        // -----------------------------------------------------------------

        _initProfileCardPreview(el) {
            const card = el.querySelector('[data-card-theme]');
            let currentTheme = card ? card.dataset.cardTheme : 'default';
            const isPremium = card && card.dataset.isPremium === 'true';
            const container = el.querySelector('.profile-card-module-preview');
            const content = el.querySelector('.profile-card-module-content');
            const loading = el.querySelector('.profile-card-module-loading');
            const downloadBtn = el.querySelector('.profile-card-module-download');
            const swatches = el.querySelectorAll('.theme-swatch');
            if (!container || !content || !loading) return;

            function getDownloadUrl() {
                return '/api/v1/profile-card/png/?theme=' + encodeURIComponent(currentTheme);
            }

            function scaleToFit() {
                if (content.style.display !== 'none') {
                    const scale = container.offsetWidth / 1200;
                    content.style.transform = 'scale(' + scale + ')';
                }
            }

            // Apply theme background to a card element (preview or modal)
            function applyTheme(themeKey, target) {
                const shareContent = (target || content).querySelector('.share-image-content');
                if (!shareContent) return;

                const themes = window.GRADIENT_THEMES;
                if (!themes || !themes[themeKey]) return;

                const t = themes[themeKey];
                shareContent.style.background = t.background;
                if (t.backgroundSize) shareContent.style.backgroundSize = t.backgroundSize;
                if (t.backgroundPosition) shareContent.style.backgroundPosition = t.backgroundPosition;
                if (t.backgroundRepeat) shareContent.style.backgroundRepeat = t.backgroundRepeat;

                // Update banner accent if present
                const banner = shareContent.querySelector('[data-element="profile-banner"]');
                if (banner && t.bannerBackground) {
                    banner.style.background = t.bannerBackground;
                }
            }

            // Update swatch selection UI
            function updateSwatchUI(themeKey) {
                swatches.forEach(function(s) {
                    const isSelected = s.dataset.themeKey === themeKey;
                    s.classList.toggle('border-primary', isSelected);
                    s.classList.toggle('ring-2', isSelected);
                    s.classList.toggle('ring-primary', isSelected);
                    s.classList.toggle('border-base-content/20', !isSelected);
                });
            }

            // Fetch and render HTML preview
            function fetchPreview() {
                PlatPursuit.API.get('/api/v1/profile-card/html/?image_format=landscape')
                    .then(function(data) {
                        content.innerHTML = data.html;
                        content.style.display = 'block';
                        content.style.width = '1200px';
                        content.style.height = '630px';
                        loading.style.display = 'none';
                        requestAnimationFrame(function() {
                            scaleToFit();
                            applyTheme(currentTheme);
                        });
                    })
                    .catch(function() {
                        loading.innerHTML = '<span class="text-sm text-base-content/40">Unable to load preview</span>';
                    });
            }

            fetchPreview();

            // Re-fetch preview when featured badge changes
            document.addEventListener('platpursuit:badge-changed', fetchPreview);

            // Theme swatch clicks
            swatches.forEach(function(swatch) {
                swatch.addEventListener('click', function() {
                    const key = swatch.dataset.themeKey;

                    // Free users can only use default
                    if (!isPremium && key !== 'default') {
                        PlatPursuit.ToastManager.show('Premium required for custom themes.', 'warning');
                        return;
                    }

                    // Apply immediately to preview
                    currentTheme = key;
                    applyTheme(key);
                    updateSwatchUI(key);

                    // Save to server
                    PlatPursuit.API.post('/api/v1/profile-card/settings/', { card_theme: key })
                        .catch(async function(err) {
                            const errData = await err.response?.json().catch(function() { return null; });
                            PlatPursuit.ToastManager.show(errData?.error || 'Failed to save theme.', 'error');
                        });
                });
            });

            // Download on button click
            if (downloadBtn) {
                downloadBtn.addEventListener('click', function() {
                    window.location.href = getDownloadUrl();
                });
            }

            // Preview click opens full-size modal
            const modal = el.querySelector('.profile-card-module-modal');
            const modalContent = el.querySelector('.profile-card-module-modal-content');
            container.addEventListener('click', function(e) {
                if (e.target.closest('.profile-card-module-download')) return;
                if (e.target.closest('.theme-swatch')) return;
                if (!modal || !modalContent) return;

                // Clone the full-size card HTML into the modal
                modalContent.innerHTML = content.innerHTML;
                // Reset the scale so it renders at full size
                const fullCard = modalContent.querySelector('.share-image-content');
                if (fullCard) {
                    fullCard.style.transform = '';
                }
                applyTheme(currentTheme, modalContent);
                modal.showModal();
            });

            // Rescale on resize
            let resizeTimer;
            window.addEventListener('resize', function() {
                clearTimeout(resizeTimer);
                resizeTimer = setTimeout(scaleToFit, 150);
            });
        }

        // -----------------------------------------------------------------
        // Customize Panel
        // -----------------------------------------------------------------

        _bindCustomizePanel() {
            const customizeBtn = document.getElementById('customize-btn');
            const modal = document.getElementById('customize-modal');
            const resetBtn = document.getElementById('customize-reset-btn');

            this._customizeDirty = false;

            if (customizeBtn && modal) {
                customizeBtn.addEventListener('click', () => modal.showModal());
                // Reload page when modal closes if structural changes were made
                modal.addEventListener('close', () => {
                    if (this._customizeDirty) {
                        window.location.reload();
                    }
                });
            }

            // Bind toggle switches
            document.querySelectorAll('.module-toggle').forEach(toggle => {
                toggle.addEventListener('change', (e) => {
                    const slug = e.target.dataset.slug;
                    const visible = e.target.checked;
                    this._handleToggle(slug, visible, e.target);
                });
            });

            // Bind section collapse toggles
            document.querySelectorAll('.tab-section-toggle').forEach(btn => {
                btn.addEventListener('click', () => {
                    const section = btn.closest('.customize-tab-section');
                    const content = section?.querySelector('.tab-section-content');
                    const icon = btn.querySelector('.tab-collapse-icon');
                    if (content) {
                        content.classList.toggle('hidden');
                        icon?.classList.toggle('-rotate-90');
                    }
                });
            });

            // Init settings controls and drag reorder in customize modal (premium only)
            if (this.isPremium) {
                this._initCustomizeDragReorder();
                this._bindSettingsControls();
                this._bindTabManagement();
                this._bindModuleMoveDropdowns();
            } else {
                document.querySelectorAll('.premium-settings-teaser').forEach(btn => {
                    btn.addEventListener('click', () => {
                        PlatPursuit.ToastManager.info('Module settings are a premium feature. Upgrade to customize your dashboard.');
                    });
                });
            }

            if (resetBtn) {
                resetBtn.addEventListener('click', () => this._resetToDefault());
            }
        }

        // -----------------------------------------------------------------
        // Tab Management (Premium)
        // -----------------------------------------------------------------

        _bindTabManagement() {
            // Sortable on custom tabs list for reordering
            const tabList = document.getElementById('custom-tabs-list');
            if (tabList && typeof Sortable !== 'undefined') {
                Sortable.create(tabList, {
                    animation: 200,
                    handle: '[data-custom-tab-slug]',
                    onEnd: async () => {
                        const newCustomOrder = [...tabList.querySelectorAll('[data-custom-tab-slug]')]
                            .map(el => el.dataset.customTabSlug);
                        const existing = this._getTabConfig();
                        // Keep default tab order, replace custom tab order
                        const currentOrder = existing.tab_order || [];
                        const customSet = new Set(newCustomOrder);
                        const withoutCustom = currentOrder.filter(s => !customSet.has(s));
                        existing.tab_order = [...withoutCustom, ...newCustomOrder];

                        try {
                            await PlatPursuit.API.post(this.configUrl, { tab_config: existing });
                            this._tabConfig = existing;
                            this._customizeDirty = true;
                            PlatPursuit.ToastManager.success('Tab order saved.');
                        } catch (err) {
                            PlatPursuit.ToastManager.error('Failed to save tab order.');
                        }
                    },
                });
            }

            const showFormBtn = document.getElementById('show-create-tab-btn');
            const createForm = document.getElementById('create-tab-form');
            const cancelBtn = document.getElementById('create-tab-cancel');
            const submitBtn = document.getElementById('create-tab-submit');
            const nameInput = document.getElementById('new-tab-name');

            let selectedIcon = null;

            // Show/hide create form
            showFormBtn?.addEventListener('click', () => {
                createForm?.classList.toggle('hidden');
                showFormBtn.classList.toggle('hidden');
                nameInput?.focus();
            });
            cancelBtn?.addEventListener('click', () => {
                createForm?.classList.add('hidden');
                showFormBtn?.classList.remove('hidden');
                nameInput.value = '';
                selectedIcon = null;
                document.querySelectorAll('.icon-pick-btn').forEach(b => b.classList.remove('ring-2', 'ring-primary'));
                submitBtn.disabled = true;
            });

            // Icon picker
            document.querySelectorAll('.icon-pick-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    document.querySelectorAll('.icon-pick-btn').forEach(b => b.classList.remove('ring-2', 'ring-primary'));
                    btn.classList.add('ring-2', 'ring-primary');
                    selectedIcon = btn.dataset.icon;
                    submitBtn.disabled = !(nameInput?.value.trim() && selectedIcon);
                });
            });

            // Enable submit when name + icon are set
            nameInput?.addEventListener('input', () => {
                submitBtn.disabled = !(nameInput.value.trim() && selectedIcon);
            });

            // Create tab
            submitBtn?.addEventListener('click', async () => {
                const name = nameInput?.value.trim();
                if (!name || !selectedIcon) return;

                const slug = 'custom_' + Date.now() + '_' + Math.random().toString(36).substring(2, 6);
                const existing = this._getTabConfig();
                if (!existing.custom_tabs) existing.custom_tabs = {};

                // Max 6 custom tabs
                if (Object.keys(existing.custom_tabs).length >= 6) {
                    PlatPursuit.ToastManager.warning('Maximum of 6 custom tabs allowed.');
                    return;
                }

                existing.custom_tabs[slug] = { name, icon: selectedIcon };

                // Add to tab_order if it exists
                if (existing.tab_order) {
                    existing.tab_order.push(slug);
                }

                try {
                    await PlatPursuit.API.post(this.configUrl, { tab_config: existing });
                    this._tabConfig = existing;

                    // Add tab to customize panel inline (no reload)
                    const tabList = document.getElementById('custom-tabs-list');
                    if (tabList) {
                        // Grab the selected icon's SVG from the picker button
                        const pickedBtn = document.querySelector(`.icon-pick-btn[data-icon="${selectedIcon}"]`);
                        const iconHtml = pickedBtn ? pickedBtn.innerHTML : '';

                        const row = document.createElement('div');
                        row.className = 'flex items-center gap-2 p-2 rounded bg-base-300/30 cursor-grab';
                        row.dataset.customTabSlug = slug;
                        row.innerHTML = `
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5 text-base-content/30 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line>
                                <line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line>
                            </svg>
                            <span class="w-4 h-4 text-accent">${iconHtml}</span>
                            <span class="flex-1 text-sm font-medium">${PlatPursuit.HTMLUtils.escape(name)}</span>
                            <span class="text-xs text-base-content/40">Reload to manage</span>
                        `;
                        tabList.appendChild(row);
                    }

                    // Reset form
                    nameInput.value = '';
                    selectedIcon = null;
                    document.querySelectorAll('.icon-pick-btn').forEach(b => b.classList.remove('ring-2', 'ring-primary'));
                    submitBtn.disabled = true;
                    createForm?.classList.add('hidden');
                    showFormBtn?.classList.remove('hidden');

                    this._customizeDirty = true;
                    PlatPursuit.ToastManager.success(`Tab "${name}" created.`);
                } catch (err) {
                    PlatPursuit.ToastManager.error('Failed to create tab.');
                }
            });

            // Rename tab
            document.querySelectorAll('.rename-tab-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const slug = btn.dataset.tabSlug;
                    const currentName = btn.dataset.tabName;
                    const newName = prompt('Rename tab:', currentName);
                    if (!newName || newName.trim() === currentName) return;

                    const trimmed = newName.trim().substring(0, 20);
                    const existing = this._getTabConfig();
                    if (existing.custom_tabs?.[slug]) {
                        existing.custom_tabs[slug].name = trimmed;
                    }

                    try {
                        await PlatPursuit.API.post(this.configUrl, { tab_config: existing });
                        this._tabConfig = existing;

                        // Update name in custom tabs list
                        const row = document.querySelector(`[data-custom-tab-slug="${slug}"]`);
                        const nameEl = row?.querySelector('.text-sm.font-medium');
                        if (nameEl) nameEl.textContent = trimmed;
                        btn.dataset.tabName = trimmed;

                        // Update name in the tab section header below
                        const section = document.querySelector(`[data-tab-section="${slug}"]`);
                        const sectionName = section?.querySelector('.tab-section-toggle .uppercase');
                        if (sectionName) sectionName.textContent = trimmed;

                        this._customizeDirty = true;
                        PlatPursuit.ToastManager.success('Tab renamed.');
                    } catch (err) {
                        PlatPursuit.ToastManager.error('Failed to rename tab.');
                    }
                });
            });

            // Delete tab
            document.querySelectorAll('.delete-tab-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const slug = btn.dataset.tabSlug;
                    const name = btn.dataset.tabName;
                    if (!confirm(`Delete tab "${name}"? Modules will return to their default tabs.`)) return;

                    const existing = this._getTabConfig();

                    // Remove from custom_tabs
                    if (existing.custom_tabs?.[slug]) {
                        delete existing.custom_tabs[slug];
                    }

                    // Remove from tab_order
                    if (existing.tab_order) {
                        existing.tab_order = existing.tab_order.filter(s => s !== slug);
                    }

                    // Remove module overrides pointing to this tab
                    if (existing.module_tab_overrides) {
                        for (const [modSlug, tabSlug] of Object.entries(existing.module_tab_overrides)) {
                            if (tabSlug === slug) {
                                delete existing.module_tab_overrides[modSlug];
                            }
                        }
                    }

                    // If active tab was deleted, reset to At a Glance
                    if (existing.active_tab === slug) {
                        existing.active_tab = 'at_a_glance';
                    }

                    try {
                        await PlatPursuit.API.post(this.configUrl, { tab_config: existing });
                        this._tabConfig = existing;

                        // Remove from DOM
                        const row = document.querySelector(`[data-custom-tab-slug="${slug}"]`);
                        if (row) row.remove();

                        // Remove the tab section from customize panel
                        const section = document.querySelector(`[data-tab-section="${slug}"]`);
                        if (section) section.remove();

                        this._customizeDirty = true;
                        PlatPursuit.ToastManager.success(`Tab "${name}" deleted.`);
                    } catch (err) {
                        PlatPursuit.ToastManager.error('Failed to delete tab.');
                    }
                });
            });
        }

        _bindModuleMoveDropdowns() {
            document.querySelectorAll('.module-move-select').forEach(select => {
                select.addEventListener('change', async () => {
                    const moduleSlug = select.dataset.moduleSlug;
                    const currentTab = select.dataset.currentTab;
                    const targetTab = select.value;

                    if (targetTab === currentTab) return;

                    const existing = this._getTabConfig();
                    if (!existing.module_tab_overrides) existing.module_tab_overrides = {};
                    existing.module_tab_overrides[moduleSlug] = targetTab;

                    try {
                        await PlatPursuit.API.post(this.configUrl, { tab_config: existing });

                        // Move the module row to the target tab section in the customize panel
                        const moduleRow = select.closest('.customize-module-row');
                        const targetSection = document.querySelector(`[data-tab-section="${targetTab}"] .tab-section-content`);
                        if (moduleRow && targetSection) {
                            targetSection.appendChild(moduleRow);
                            select.dataset.currentTab = targetTab;
                            const emptyMsg = targetSection.querySelector('p.italic');
                            if (emptyMsg && emptyMsg.textContent.includes('No modules')) emptyMsg.remove();
                            PlatPursuit.ToastManager.success('Module moved.');
                        } else {
                            PlatPursuit.ToastManager.info('Module moved. Close to apply.');
                        }

                        this._customizeDirty = true;
                    } catch (err) {
                        PlatPursuit.ToastManager.error('Failed to move module.');
                        select.value = currentTab;
                    }
                });
            });
        }

        _getTabConfig() {
            // Read current tab_config from the page's config object
            // This is initialized from the server-rendered template
            return { ...(this._tabConfig || {}) };
        }

        // -----------------------------------------------------------------
        // Module Settings (Premium)
        // -----------------------------------------------------------------

        _bindSettingsControls() {
            document.querySelectorAll('.module-settings-toggle').forEach(btn => {
                btn.addEventListener('click', () => {
                    const slug = btn.dataset.slug;
                    const panel = document.querySelector(`[data-settings-slug="${slug}"]`);
                    if (panel) panel.classList.toggle('hidden');
                });
            });

            document.querySelectorAll('.module-setting-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const slug = btn.dataset.slug;
                    const key = btn.dataset.settingKey;
                    let value = btn.dataset.settingValue;
                    if (!isNaN(value) && value !== '') value = Number(value);

                    this._updateSetting(slug, key, value);

                    const container = btn.closest('.join');
                    if (container) {
                        container.querySelectorAll('.module-setting-btn').forEach(b => {
                            if (b.dataset.settingKey === key) {
                                b.classList.remove('btn-primary');
                                b.classList.add('btn-ghost', 'border-base-300');
                            }
                        });
                        btn.classList.add('btn-primary');
                        btn.classList.remove('btn-ghost', 'border-base-300');
                    }
                });
            });

            document.querySelectorAll('.module-setting-toggle').forEach(toggle => {
                toggle.addEventListener('change', () => {
                    this._updateSetting(toggle.dataset.slug, toggle.dataset.settingKey, toggle.checked);
                });
            });
        }

        _updateSetting(slug, key, value) {
            if (!this.moduleSettings[slug]) this.moduleSettings[slug] = {};
            this.moduleSettings[slug][key] = value;
            this._settingsDirty = true;
            this._debouncedSaveSettings();
        }

        // -----------------------------------------------------------------
        // Toggle (Show/Hide)
        // -----------------------------------------------------------------

        _handleToggle(slug, visible, toggleEl) {
            if (visible) {
                this.hiddenModules.delete(slug);
            } else {
                if (!this.isPremium && this.hiddenModules.size >= this.maxFreeHidden) {
                    PlatPursuit.ToastManager.warning(
                        `Free users can hide up to ${this.maxFreeHidden} modules. Upgrade to premium for unlimited customization.`
                    );
                    toggleEl.checked = true;
                    return;
                }
                this.hiddenModules.add(slug);
            }

            const moduleEl = document.getElementById('module-' + slug);
            if (moduleEl) {
                if (visible) {
                    moduleEl.classList.remove('hidden');
                } else {
                    moduleEl.classList.add('hidden');
                }
            }

            this._updateHiddenCount();

            if (visible && !moduleEl) {
                this._saveConfigNow().then(() => window.location.reload());
            } else {
                this._debouncedSaveConfig();
            }
        }

        // -----------------------------------------------------------------
        // Reset to Default
        // -----------------------------------------------------------------

        async _resetToDefault() {
            if (!confirm('Reset your dashboard to default? This will remove all custom tabs, module moves, hidden modules, and settings.')) return;

            const resetBtn = document.getElementById('customize-reset-btn');
            if (resetBtn) {
                resetBtn.disabled = true;
                resetBtn.classList.add('loading', 'loading-sm');
            }

            try {
                this.hiddenModules.clear();
                this.moduleSettings = {};

                const payload = { hidden_modules: [], module_settings: {} };
                if (this.isPremium) {
                    payload.module_order = [];
                    payload.tab_config = {
                        active_tab: 'at_a_glance',
                        tab_order: [],
                        custom_tabs: {},
                        module_tab_overrides: {},
                    };
                }
                await PlatPursuit.API.post(this.configUrl, payload);

                PlatPursuit.ToastManager.success('Dashboard reset to default.');
                window.location.reload();
            } catch (err) {
                PlatPursuit.ToastManager.error('Failed to reset dashboard.');
            } finally {
                if (resetBtn) {
                    resetBtn.disabled = false;
                    resetBtn.classList.remove('loading', 'loading-sm');
                }
            }
        }

        // -----------------------------------------------------------------
        // Persistence
        // -----------------------------------------------------------------

        async _saveConfigNow() {
            try {
                await PlatPursuit.API.post(this.configUrl, {
                    hidden_modules: [...this.hiddenModules],
                });
            } catch (err) {
                PlatPursuit.ToastManager.error('Failed to save dashboard config.');
            }
        }

        async _saveSettingsNow() {
            if (!this._settingsDirty) return;
            this._settingsDirty = false;

            try {
                await PlatPursuit.API.post(this.configUrl, {
                    module_settings: this.moduleSettings,
                });

                // Refresh affected lazy modules
                for (const slug of Object.keys(this.moduleSettings)) {
                    for (const tabConfig of Object.values(this.tabsConfig)) {
                        const mod = (tabConfig.lazyModules || []).find(m => m.slug === slug);
                        if (mod) {
                            this._loadModule(mod);
                            break;
                        }
                    }
                }
            } catch (err) {
                PlatPursuit.ToastManager.error('Failed to save module settings.');
            }
        }

        async _saveActiveTab() {
            try {
                await PlatPursuit.API.post(this.configUrl, {
                    tab_config: { active_tab: this.activeTab },
                });
            } catch (err) {
                // Silent fail for tab persistence: not critical
            }
        }

        _updateHiddenCount() {
            const counter = document.querySelector('#customize-modal .text-warning');
            if (counter && !this.isPremium) {
                counter.textContent = `(${this.hiddenModules.size}/${this.maxFreeHidden} hidden. Upgrade for unlimited.)`;
            }
        }

        // -----------------------------------------------------------------
        // Drag Reorder (Premium)
        // -----------------------------------------------------------------

        _initDragForTab(tabSlug) {
            if (!PlatPursuit.DragReorderManager) return;
            if (this.dragManagers[tabSlug]) return;  // Already initialized

            const panel = document.getElementById('tab-panel-' + tabSlug);
            if (!panel) return;

            this.dragManagers[tabSlug] = new PlatPursuit.DragReorderManager({
                container: panel,
                itemSelector: '.dashboard-module',
                handleSelector: '.module-drag-handle',
                onReorder: (_itemId, _newPosition, allItemIds) => {
                    this._pendingOrder = allItemIds;
                    this._debouncedSaveOrder();
                },
            });

            // Show drag handles
            panel.querySelectorAll('.module-drag-handle').forEach(handle => {
                handle.classList.remove('hidden');
            });
        }

        _initCustomizeDragReorder() {
            if (!PlatPursuit.DragReorderManager) return;
            if (this._customizeDragInitialized) return;
            this._customizeDragInitialized = true;

            // Initialize drag reorder on each tab section's content container
            document.querySelectorAll('.tab-section-content').forEach(container => {
                new PlatPursuit.DragReorderManager({
                    container: container,
                    itemSelector: '.customize-module-row',
                    handleSelector: '.customize-drag-handle',
                    onReorder: () => {
                        const allIds = [...document.querySelectorAll('.customize-module-row')]
                            .map(el => el.dataset.itemId);
                        this._pendingOrder = allIds;
                        this._debouncedSaveOrder();
                        this._customizeDirty = true;
                    },
                });
            });
        }

        async _saveOrderNow() {
            if (!this._pendingOrder) return;
            const order = this._pendingOrder;
            this._pendingOrder = null;

            try {
                await PlatPursuit.API.post(this.reorderUrl, { module_order: order });
            } catch (err) {
                PlatPursuit.ToastManager.error('Failed to save layout.');
            }
        }
    }

    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.DashboardManager = DashboardManager;
})();
