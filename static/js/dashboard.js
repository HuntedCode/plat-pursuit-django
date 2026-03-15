/**
 * Dashboard Module Manager
 *
 * Handles lazy-loading of dashboard modules, drag-and-drop reordering
 * (premium), module toggle (show/hide), and the customize panel.
 *
 * Layout: single-column, capped width. Drag reorder = vertical priority.
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
         * @param {Array<{slug: string, elementId: string}>} config.lazyModules - Modules to lazy-load
         */
        constructor(config) {
            this.container = document.getElementById('dashboard-modules');
            this.emptyState = document.getElementById('dashboard-empty');
            this.moduleDataUrl = config.moduleDataUrl;
            this.configUrl = config.configUrl;
            this.reorderUrl = config.reorderUrl;
            this.isPremium = config.isPremium;
            this.maxFreeHidden = config.maxFreeHidden;
            this.hiddenModules = new Set(config.hiddenModules || []);
            this.lazyModules = config.lazyModules || [];
            this.moduleSettings = config.moduleSettings || {};

            this.dragManager = null;
            this.customizeDragManager = null;
            this._moduleInits = {};

            // Debounced persistence: batches rapid changes into a single API call
            this._debouncedSaveConfig = PlatPursuit.debounce(() => this._saveConfigNow(), 500);
            this._debouncedSaveSettings = PlatPursuit.debounce(() => this._saveSettingsNow(), 500);
            this._debouncedSaveOrder = PlatPursuit.debounce(() => this._saveOrderNow(), 500);
            this._pendingOrder = null;
            this._settingsDirty = false;
        }

        init() {
            this._loadLazyModules();
            this._bindCustomizePanel();
            if (this.isPremium) {
                this._initDragReorder();
            }
            this._updateEmptyState();
        }

        // -----------------------------------------------------------------
        // Lazy Loading
        // -----------------------------------------------------------------

        async _loadLazyModules() {
            const promises = this.lazyModules.map(mod => this._loadModule(mod));
            await Promise.allSettled(promises);
        }

        async _loadModule(mod) {
            const el = document.getElementById(mod.elementId);
            if (!el) return;

            try {
                const data = await PlatPursuit.API.get(
                    this.moduleDataUrl + mod.slug + '/'
                );
                // Replace the skeleton with the rendered HTML
                const skeleton = el.querySelector('.dashboard-module-skeleton');
                if (skeleton) {
                    skeleton.outerHTML = data.html;
                } else {
                    el.innerHTML = data.html;
                }

                // Call per-module JS init if registered
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

        /**
         * Register a JS initializer for a specific module.
         * Called after the module's HTML is loaded via AJAX.
         * @param {string} slug
         * @param {Function} fn - Receives the module container element
         */
        registerModuleInit(slug, fn) {
            this._moduleInits[slug] = fn;
        }

        // -----------------------------------------------------------------
        // Customize Panel
        // -----------------------------------------------------------------

        _bindCustomizePanel() {
            const customizeBtn = document.getElementById('customize-btn');
            const modal = document.getElementById('customize-modal');
            const resetBtn = document.getElementById('customize-reset-btn');

            if (customizeBtn && modal) {
                customizeBtn.addEventListener('click', () => {
                    modal.showModal();
                });
            }

            // Bind toggle switches
            const toggles = document.querySelectorAll('.module-toggle');
            toggles.forEach(toggle => {
                toggle.addEventListener('change', (e) => {
                    const slug = e.target.dataset.slug;
                    const visible = e.target.checked;
                    this._handleToggle(slug, visible, e.target);
                });
            });

            // Init drag reorder and settings controls (premium only)
            if (this.isPremium) {
                this._initCustomizeDragReorder();
                this._bindSettingsControls();
            } else {
                // Free user: show toast when clicking locked settings gear
                document.querySelectorAll('.premium-settings-teaser').forEach(btn => {
                    btn.addEventListener('click', () => {
                        PlatPursuit.ToastManager.info('Module settings are a premium feature. Upgrade to customize your dashboard.');
                    });
                });
            }

            // Reset to default
            if (resetBtn) {
                resetBtn.addEventListener('click', () => {
                    this._resetToDefault();
                });
            }
        }

        // -----------------------------------------------------------------
        // Module Settings (Premium)
        // -----------------------------------------------------------------

        _bindSettingsControls() {
            // Gear icon toggles the settings panel
            document.querySelectorAll('.module-settings-toggle').forEach(btn => {
                btn.addEventListener('click', () => {
                    const slug = btn.dataset.slug;
                    const panel = document.querySelector(`[data-settings-slug="${slug}"]`);
                    if (panel) {
                        panel.classList.toggle('hidden');
                    }
                });
            });

            // Select buttons (e.g., item count: 3/6/10)
            document.querySelectorAll('.module-setting-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const slug = btn.dataset.slug;
                    const key = btn.dataset.settingKey;
                    let value = btn.dataset.settingValue;

                    // Coerce numeric values
                    if (!isNaN(value) && value !== '') value = Number(value);

                    this._updateSetting(slug, key, value);

                    // Update button group visual state
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

            // Toggle switches (e.g., show_unstarted)
            document.querySelectorAll('.module-setting-toggle').forEach(toggle => {
                toggle.addEventListener('change', () => {
                    const slug = toggle.dataset.slug;
                    const key = toggle.dataset.settingKey;
                    this._updateSetting(slug, key, toggle.checked);
                });
            });
        }

        _updateSetting(slug, key, value) {
            if (!this.moduleSettings[slug]) {
                this.moduleSettings[slug] = {};
            }
            this.moduleSettings[slug][key] = value;
            this._settingsDirty = true;
            this._debouncedSaveSettings();
        }

        _handleToggle(slug, visible, toggleEl) {
            if (visible) {
                this.hiddenModules.delete(slug);
            } else {
                // Check free user limit
                if (!this.isPremium && this.hiddenModules.size >= this.maxFreeHidden) {
                    PlatPursuit.ToastManager.warning(
                        `Free users can hide up to ${this.maxFreeHidden} modules. Upgrade to premium for unlimited customization.`
                    );
                    // Revert the toggle
                    toggleEl.checked = true;
                    return;
                }
                this.hiddenModules.add(slug);
            }

            // Update DOM: show/hide the module if it exists in the page
            const moduleEl = document.getElementById('module-' + slug);
            if (moduleEl) {
                if (visible) {
                    moduleEl.classList.remove('hidden');
                } else {
                    moduleEl.classList.add('hidden');
                }
            }

            // Update hidden count display
            this._updateHiddenCount();
            this._updateEmptyState();

            if (visible && !moduleEl) {
                // Module wasn't rendered server-side (was hidden at page load).
                // Save immediately and reload to get its HTML.
                this._saveConfigNow().then(() => window.location.reload());
            } else {
                // Module is in the DOM: save debounced for instant visual feedback
                this._debouncedSaveConfig();
            }
        }

        // -----------------------------------------------------------------
        // Reset to Default
        // -----------------------------------------------------------------

        async _resetToDefault() {
            const resetBtn = document.getElementById('customize-reset-btn');
            if (resetBtn) {
                resetBtn.disabled = true;
                resetBtn.classList.add('loading', 'loading-sm');
            }

            try {
                // Unhide all modules
                this.hiddenModules.clear();

                // Update all toggles
                document.querySelectorAll('.module-toggle').forEach(toggle => {
                    toggle.checked = true;
                });

                // Show all module elements
                if (this.container) {
                    this.container.querySelectorAll('.dashboard-module').forEach(el => {
                        el.classList.remove('hidden');
                    });
                }

                // Clear all module settings
                this.moduleSettings = {};

                this._updateHiddenCount();
                this._updateEmptyState();

                // Atomic reset: single POST with all fields cleared
                const payload = { hidden_modules: [], module_settings: {} };
                if (this.isPremium) {
                    payload.module_order = [];
                }
                await PlatPursuit.API.post(this.configUrl, payload);

                PlatPursuit.ToastManager.success('Dashboard reset to default.');

                // Reload to get default module order
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
        // Persistence (all debounced to batch rapid changes)
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

                // Refresh affected lazy modules so changes are visible immediately
                for (const slug of Object.keys(this.moduleSettings)) {
                    const mod = this.lazyModules.find(m => m.slug === slug);
                    if (mod) {
                        this._loadModule(mod);
                    }
                }
            } catch (err) {
                PlatPursuit.ToastManager.error('Failed to save module settings.');
            }
        }

        _updateHiddenCount() {
            // Update the counter in the customize panel header
            const counter = document.querySelector('#customize-modal .text-warning');
            if (counter && !this.isPremium) {
                counter.textContent = `(${this.hiddenModules.size}/${this.maxFreeHidden} hidden. Upgrade for unlimited.)`;
            }
        }

        // -----------------------------------------------------------------
        // Drag Reorder (Premium)
        // -----------------------------------------------------------------

        _initDragReorder() {
            if (!this.container || !PlatPursuit.DragReorderManager) return;

            this.dragManager = new PlatPursuit.DragReorderManager({
                container: this.container,
                itemSelector: '.dashboard-module',
                handleSelector: '.module-drag-handle',
                onReorder: (_itemId, _newPosition, allItemIds) => {
                    this._pendingOrder = allItemIds;
                    this._debouncedSaveOrder();
                },
            });

            // Show drag handles for premium users
            this.container.querySelectorAll('.module-drag-handle').forEach(handle => {
                handle.classList.remove('hidden');
            });
        }

        _initCustomizeDragReorder() {
            const customizeList = document.getElementById('customize-module-list');
            if (!customizeList || !PlatPursuit.DragReorderManager) return;

            // Hide category headers during drag for a clean flat-list reorder UX
            const headers = customizeList.querySelectorAll('h4');

            this.customizeDragManager = new PlatPursuit.DragReorderManager({
                container: customizeList,
                itemSelector: '.customize-module-row',
                handleSelector: '.customize-drag-handle',
                onReorder: (_itemId, _newPosition, allItemIds) => {
                    this._reorderMainPageModules(allItemIds);
                    this._pendingOrder = allItemIds;
                    this._debouncedSaveOrder();
                },
                onStart: () => {
                    headers.forEach(h => h.classList.add('opacity-0', 'h-0', 'overflow-hidden', '!mt-0', '!mb-0'));
                },
                onEnd: () => {
                    headers.forEach(h => h.classList.remove('opacity-0', 'h-0', 'overflow-hidden', '!mt-0', '!mb-0'));
                },
            });
        }

        /**
         * Reorder main page module DOM elements to match the given slug order.
         * Gives instant visual feedback when reordering from the customize modal.
         */
        _reorderMainPageModules(slugOrder) {
            if (!this.container) return;

            const fragment = document.createDocumentFragment();
            for (const slug of slugOrder) {
                const el = document.getElementById('module-' + slug);
                if (el) fragment.appendChild(el);
            }
            this.container.appendChild(fragment);
        }

        async _saveOrderNow() {
            if (!this._pendingOrder) return;
            const order = this._pendingOrder;
            this._pendingOrder = null;

            try {
                await PlatPursuit.API.post(this.reorderUrl, {
                    module_order: order,
                });
            } catch (err) {
                PlatPursuit.ToastManager.error('Failed to save layout.');
            }
        }

        // -----------------------------------------------------------------
        // Helpers
        // -----------------------------------------------------------------

        _updateEmptyState() {
            if (!this.emptyState || !this.container) return;

            const visibleCount = this.container.querySelectorAll(
                '.dashboard-module:not(.hidden)'
            ).length;

            if (visibleCount === 0) {
                this.emptyState.classList.remove('hidden');
            } else {
                this.emptyState.classList.add('hidden');
            }
        }
    }

    // Export to PlatPursuit namespace
    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.DashboardManager = DashboardManager;
})();
