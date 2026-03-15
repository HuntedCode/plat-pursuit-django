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
                    el.innerHTML = data.html;
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
                            // Remove "no modules" empty text from target if present
                            const emptyMsg = targetSection.querySelector('p.italic');
                            if (emptyMsg && emptyMsg.textContent.includes('No modules')) emptyMsg.remove();
                        }

                        this._customizeDirty = true;
                        PlatPursuit.ToastManager.success('Module moved.');
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
            const customizeList = document.getElementById('customize-module-list');
            if (!customizeList || !PlatPursuit.DragReorderManager) return;

            const headers = customizeList.querySelectorAll('h4');

            this.customizeDragManager = new PlatPursuit.DragReorderManager({
                container: customizeList,
                itemSelector: '.customize-module-row',
                handleSelector: '.customize-drag-handle',
                onReorder: (_itemId, _newPosition, allItemIds) => {
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
