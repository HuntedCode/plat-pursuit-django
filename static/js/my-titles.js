(function() {
    'use strict';

    var API = window.PlatPursuit.API;
    var ToastManager = window.PlatPursuit.ToastManager;

    var MyTitlesPage = {
        _earnedGrid: null,
        _earnedList: null,
        _specialGrid: null,
        _specialList: null,
        _lockedGrid: null,
        _lockedList: null,
        _banner: null,
        _filterTabs: null,
        _sortToggle: null,
        _viewToggle: null,
        _currentFilter: 'all',
        _currentSort: 'newest',
        _currentView: 'grid',
        _isPremium: false,
        _sparkleClickCount: 0,
        _sparkleTimer: null,

        init: function() {
            this._earnedGrid = document.getElementById('earned-grid');
            this._earnedList = document.getElementById('earned-list');
            this._specialGrid = document.getElementById('special-grid');
            this._specialList = document.getElementById('special-list');
            this._lockedGrid = document.getElementById('locked-grid');
            this._lockedList = document.getElementById('locked-list');
            this._banner = document.getElementById('equipped-banner');
            this._filterTabs = document.getElementById('title-filter-tabs');
            this._sortToggle = document.getElementById('title-sort-toggle');
            this._viewToggle = document.getElementById('title-view-toggle');
            // Detect premium status from any server-rendered title element
            var premiumEl = document.querySelector('.legendary-title');
            this._isPremium = !!premiumEl;
            this._restoreState();
            this._bindEvents();
        },

        // ── Event Binding ──────────────────────────────────────────────

        _bindContainerClick: function(container, equippedClass) {
            if (!container) return;
            var self = this;
            container.addEventListener('click', function(e) {
                if (e.target.closest('a')) return;
                var el = e.target.closest('[data-title-id]');
                if (!el || el.classList.contains(equippedClass)) return;
                var titleId = parseInt(el.dataset.titleId, 10);
                if (titleId) self._equipTitle(titleId);
            });
        },

        _bindEvents: function() {
            var self = this;

            // Equip click delegation on all title containers
            this._bindContainerClick(this._earnedGrid, 'ring-secondary');
            this._bindContainerClick(this._earnedList, 'border-l-secondary');
            this._bindContainerClick(this._specialGrid, 'ring-secondary');
            this._bindContainerClick(this._specialList, 'border-l-secondary');

            // Unequip button in banner
            if (this._banner) {
                this._banner.addEventListener('click', function(e) {
                    if (e.target.closest('[data-action="unequip"]')) {
                        self._unequipTitle();
                    }
                    // Easter egg: rapid sparkle clicks
                    if (e.target.closest('[data-sparkle]')) {
                        self._handleSparkleClick();
                    }
                });
            }

            // Filter tabs
            if (this._filterTabs) {
                this._filterTabs.addEventListener('click', function(e) {
                    var tab = e.target.closest('[data-filter]');
                    if (!tab) return;
                    self._setFilter(tab.dataset.filter);
                });
            }

            // Sort toggle
            if (this._sortToggle) {
                this._sortToggle.addEventListener('click', function() {
                    var next = self._currentSort === 'newest' ? 'az' : 'newest';
                    self._setSort(next);
                });
            }

            // View toggle (grid / list)
            if (this._viewToggle) {
                this._viewToggle.addEventListener('click', function() {
                    var next = self._currentView === 'grid' ? 'list' : 'grid';
                    self._setView(next);
                });
            }
        },

        // ── Equip / Unequip ────────────────────────────────────────────

        _equipTitle: function(titleId) {
            var self = this;
            API.post('/api/v1/equip-title/', { title_id: titleId })
                .then(function(data) {
                    if (data.success) {
                        // Update cards/rows immediately (no animation delay needed)
                        self._updateGridCards(self._earnedGrid, titleId);
                        self._updateListRows(self._earnedList, titleId);
                        self._updateGridCards(self._specialGrid, titleId);
                        self._updateListRows(self._specialList, titleId);
                        // Play animation which handles banner update mid-fade
                        self._playEquipAnimation(titleId, data.title_name);
                        ToastManager.show('Title equipped: ' + data.title_name, 'success');
                    }
                })
                .catch(function(error) {
                    var msg = 'Failed to equip title.';
                    if (error.response) {
                        error.response.json().then(function(errData) {
                            ToastManager.show(errData.error || msg, 'error');
                        }).catch(function() {
                            ToastManager.show(msg, 'error');
                        });
                    } else {
                        ToastManager.show(msg, 'error');
                    }
                });
        },

        _unequipTitle: function() {
            var self = this;
            API.post('/api/v1/equip-title/', { title_id: null })
                .then(function(data) {
                    if (data.success) {
                        self._updateUI(null, null);
                        ToastManager.show('Title removed.', 'success');
                    }
                })
                .catch(function() {
                    ToastManager.show('Failed to remove title.', 'error');
                });
        },

        // ── UI Updates ─────────────────────────────────────────────────

        _updateUI: function(equippedTitleId, titleName) {
            // Update earned containers
            this._updateGridCards(this._earnedGrid, equippedTitleId);
            this._updateListRows(this._earnedList, equippedTitleId);
            // Update special containers
            this._updateGridCards(this._specialGrid, equippedTitleId);
            this._updateListRows(this._specialList, equippedTitleId);
            // Update banner
            this._updateBanner(equippedTitleId, titleName);
        },

        _updateGridCards: function(container, equippedTitleId) {
            if (!container) return;
            var cards = container.querySelectorAll(':scope > [data-title-id]');
            cards.forEach(function(card) {
                var cardTitleId = parseInt(card.dataset.titleId, 10);
                var isEquipped = equippedTitleId !== null && cardTitleId === equippedTitleId;
                var footer = card.querySelector('.card-body .border-t');
                if (!footer) return;

                // Update ring + shadow styling
                if (isEquipped) {
                    card.classList.add('ring-2', 'ring-secondary', 'shadow-lg', 'shadow-secondary/20');
                    card.removeAttribute('title');
                } else {
                    card.classList.remove('ring-2', 'ring-secondary', 'shadow-lg', 'shadow-secondary/20');
                    card.setAttribute('title', 'Click to equip');
                }

                // Update button / badge in footer
                var actionEl = footer.querySelector('.btn, .badge-secondary');
                if (actionEl) {
                    if (isEquipped) {
                        var badge = document.createElement('span');
                        badge.className = 'badge badge-secondary badge-sm gap-1';
                        badge.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>Equipped';
                        actionEl.replaceWith(badge);
                    } else if (actionEl.classList.contains('badge-secondary')) {
                        var btn = document.createElement('button');
                        btn.type = 'button';
                        btn.className = 'btn btn-secondary btn-sm gap-1';
                        btn.dataset.action = 'equip';
                        btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>Equip';
                        actionEl.replaceWith(btn);
                    }
                }
            });
        },

        _updateListRows: function(container, equippedTitleId) {
            if (!container) return;
            var rows = container.querySelectorAll(':scope > [data-title-id]');
            rows.forEach(function(row) {
                var rowTitleId = parseInt(row.dataset.titleId, 10);
                var isEquipped = equippedTitleId !== null && rowTitleId === equippedTitleId;

                // Update border-left highlight
                if (isEquipped) {
                    row.classList.add('bg-secondary/5', 'border-l-secondary');
                    row.classList.remove('border-l-transparent');
                    row.removeAttribute('title');
                } else {
                    row.classList.remove('bg-secondary/5', 'border-l-secondary');
                    row.classList.add('border-l-transparent');
                    row.setAttribute('title', 'Click to equip');
                }

                // Update action element (last child: btn or badge)
                var actionEl = row.querySelector('.btn, .badge-secondary');
                if (actionEl) {
                    if (isEquipped) {
                        var badge = document.createElement('span');
                        badge.className = 'badge badge-secondary badge-sm gap-1 shrink-0';
                        badge.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>Equipped';
                        actionEl.replaceWith(badge);
                    } else if (actionEl.classList.contains('badge-secondary')) {
                        var btn = document.createElement('button');
                        btn.type = 'button';
                        btn.className = 'btn btn-secondary btn-xs gap-1 shrink-0';
                        btn.dataset.action = 'equip';
                        btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>Equip';
                        actionEl.replaceWith(btn);
                    }
                }
            });
        },

        _updateBanner: function(equippedTitleId, titleName) {
            if (!this._banner) return;
            var bannerBody = this._banner.querySelector('.card-body');
            if (!bannerBody) return;

            var nameEl = bannerBody.querySelector('#equipped-title-name');
            var unequipBtn = bannerBody.querySelector('[data-action="unequip"]');
            var flavorEl = bannerBody.querySelector('[data-flavor]');

            if (equippedTitleId !== null && titleName) {
                // Show equipped state with sparkles
                if (nameEl) {
                    // Check if sparkle wrapper exists, if not create it
                    var sparkleWrapper = nameEl.closest('.flex.items-center.gap-3');
                    if (!sparkleWrapper) {
                        sparkleWrapper = document.createElement('div');
                        sparkleWrapper.className = 'flex items-center gap-3';
                        var sparkleSvg = '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 text-secondary/60 shrink-0" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" data-sparkle><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>';
                        nameEl.parentNode.insertBefore(sparkleWrapper, nameEl);
                        sparkleWrapper.innerHTML = sparkleSvg;
                        sparkleWrapper.appendChild(nameEl);
                        sparkleWrapper.insertAdjacentHTML('beforeend', sparkleSvg);
                    }
                    nameEl.textContent = titleName;
                    nameEl.className = 'text-2xl lg:text-3xl ' + (this._isPremium ? 'legendary-title' : 'text-secondary') + ' italic pr-1 font-bold';
                }
                // Update flavor text
                if (flavorEl) {
                    flavorEl.textContent = 'Your legend, your identity.';
                    flavorEl.className = 'text-xs text-base-content/40 italic';
                    flavorEl.setAttribute('data-flavor', '');
                }
                if (!unequipBtn) {
                    unequipBtn = document.createElement('button');
                    unequipBtn.type = 'button';
                    unequipBtn.className = 'btn btn-ghost btn-sm gap-1 text-base-content/50 mt-1';
                    unequipBtn.dataset.action = 'unequip';
                    unequipBtn.setAttribute('aria-label', 'Unequip title');
                    unequipBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>Remove Title';
                    bannerBody.appendChild(unequipBtn);
                }
            } else {
                // Show empty state, remove sparkle wrapper
                var sparkleWrapper = nameEl ? nameEl.closest('.flex.items-center.gap-3') : null;
                if (sparkleWrapper) {
                    sparkleWrapper.parentNode.insertBefore(nameEl, sparkleWrapper);
                    sparkleWrapper.remove();
                }
                if (nameEl) {
                    nameEl.textContent = 'No title equipped';
                    nameEl.className = 'text-lg text-base-content/40 italic';
                }
                if (flavorEl) {
                    flavorEl.textContent = 'Every legend needs a name. Equip a title below to claim yours.';
                    flavorEl.className = 'text-xs text-base-content/30 italic';
                    flavorEl.setAttribute('data-flavor', '');
                }
                if (unequipBtn) unequipBtn.remove();
            }
        },

        // ── Equip Animation ────────────────────────────────────────────

        _playEquipAnimation: function(titleId, titleName) {
            // Flash glow on the active container's element (check earned then special)
            var selector = '[data-title-id="' + titleId + '"]';
            var containers = this._currentView === 'list'
                ? [this._earnedList, this._specialList]
                : [this._earnedGrid, this._specialGrid];
            var el = null;
            for (var i = 0; i < containers.length; i++) {
                if (containers[i]) {
                    el = containers[i].querySelector(selector);
                    if (el) break;
                }
            }
            if (el) {
                el.classList.add('title-equip-glow');
                el.addEventListener('animationend', function handler() {
                    el.classList.remove('title-equip-glow');
                    el.removeEventListener('animationend', handler);
                });
            }

            // Banner crossfade: fade out old text → update → fade in new text
            var self = this;
            if (this._banner) {
                this._banner.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                var nameEl = this._banner.querySelector('#equipped-title-name');
                if (nameEl) {
                    nameEl.classList.add('title-fade-out');
                    setTimeout(function() {
                        self._updateBanner(titleId, titleName);
                        // Re-query since _updateBanner may restructure DOM
                        var newNameEl = self._banner.querySelector('#equipped-title-name');
                        if (newNameEl) {
                            newNameEl.classList.remove('title-fade-out');
                        }
                    }, 250);
                }
            }
        },

        // ── Filter & Sort ──────────────────────────────────────────────

        _setFilter: function(filter) {
            this._currentFilter = filter;

            // Update tab active state
            if (this._filterTabs) {
                var tabs = this._filterTabs.querySelectorAll('[data-filter]');
                tabs.forEach(function(tab) {
                    tab.classList.toggle('tab-active', tab.dataset.filter === filter);
                });
            }

            this._applyFilterAndSort();
            this._saveState();
        },

        _setSort: function(sort) {
            this._currentSort = sort;

            // Update sort button label
            var label = document.getElementById('sort-label');
            if (label) {
                label.textContent = sort === 'newest' ? 'Newest' : 'A\u2013Z';
            }

            this._applyFilterAndSort();
            this._saveState();
        },

        _setView: function(view) {
            this._currentView = view;
            var isGrid = view === 'grid';

            // Toggle earned containers
            if (this._earnedGrid) this._earnedGrid.classList.toggle('hidden', !isGrid);
            if (this._earnedList) this._earnedList.classList.toggle('hidden', isGrid);

            // Toggle special containers
            if (this._specialGrid) this._specialGrid.classList.toggle('hidden', !isGrid);
            if (this._specialList) this._specialList.classList.toggle('hidden', isGrid);

            // Toggle locked containers
            if (this._lockedGrid) this._lockedGrid.classList.toggle('hidden', !isGrid);
            if (this._lockedList) this._lockedList.classList.toggle('hidden', isGrid);

            // Update toggle button icon and tooltip
            if (this._viewToggle) {
                var tooltip = this._viewToggle.closest('.tooltip');
                if (isGrid) {
                    // In grid mode: show list icon (what you'll switch TO)
                    this._viewToggle.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>';
                    if (tooltip) tooltip.setAttribute('data-tip', 'Switch to List View');
                } else {
                    // In list mode: show grid icon (what you'll switch TO)
                    this._viewToggle.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>';
                    if (tooltip) tooltip.setAttribute('data-tip', 'Switch to Grid View');
                }
                this._viewToggle.dataset.view = view;
            }

            this._saveState();
        },

        _applyFilterAndSort: function() {
            var filter = this._currentFilter;
            var sort = this._currentSort;

            // Process both grid and list containers
            var containers = [this._earnedGrid, this._earnedList];
            for (var i = 0; i < containers.length; i++) {
                var container = containers[i];
                if (!container) continue;
                var items = Array.from(container.querySelectorAll(':scope > [data-title-id]'));

                // Filter
                items.forEach(function(item) {
                    var sourceType = item.dataset.sourceType;
                    var show = filter === 'all' ||
                               sourceType === filter ||
                               sourceType === 'both';
                    item.style.display = show ? '' : 'none';
                });

                // Sort visible items
                var visibleItems = items.filter(function(c) { return c.style.display !== 'none'; });

                if (sort === 'az') {
                    visibleItems.sort(function(a, b) {
                        return (a.dataset.titleName || '').localeCompare(b.dataset.titleName || '');
                    });
                } else {
                    visibleItems.sort(function(a, b) {
                        return (b.dataset.earnedAt || '').localeCompare(a.dataset.earnedAt || '');
                    });
                }

                // Reorder DOM
                visibleItems.forEach(function(item) {
                    container.appendChild(item);
                });

                // Move hidden items to end
                items.forEach(function(item) {
                    if (item.style.display === 'none') {
                        container.appendChild(item);
                    }
                });
            }
        },

        _saveState: function() {
            try {
                sessionStorage.setItem('titles_filter', this._currentFilter);
                sessionStorage.setItem('titles_sort', this._currentSort);
                sessionStorage.setItem('titles_view', this._currentView);
            } catch (e) { /* ignore */ }
        },

        _restoreState: function() {
            try {
                var view = sessionStorage.getItem('titles_view');
                var filter = sessionStorage.getItem('titles_filter');
                var sort = sessionStorage.getItem('titles_sort');

                // Set all state values first (no side effects)
                if (view && ['grid', 'list'].indexOf(view) !== -1) this._currentView = view;
                if (filter && ['all', 'badge', 'milestone'].indexOf(filter) !== -1) this._currentFilter = filter;
                if (sort && ['newest', 'az'].indexOf(sort) !== -1) this._currentSort = sort;

                // Apply view toggle (controls container visibility)
                this._setView(this._currentView);

                // Update filter tab active state
                if (this._filterTabs) {
                    var self = this;
                    this._filterTabs.querySelectorAll('[data-filter]').forEach(function(tab) {
                        tab.classList.toggle('tab-active', tab.dataset.filter === self._currentFilter);
                    });
                }

                // Update sort label
                var label = document.getElementById('sort-label');
                if (label) label.textContent = this._currentSort === 'newest' ? 'Newest' : 'A\u2013Z';

                // Single apply with complete state
                this._applyFilterAndSort();
            } catch (e) { /* ignore */ }
        },

        // ── Easter Egg: Sparkle Clicks ─────────────────────────────────

        _handleSparkleClick: function() {
            var self = this;
            this._sparkleClickCount++;
            clearTimeout(this._sparkleTimer);

            if (this._sparkleClickCount >= 5) {
                this._sparkleClickCount = 0;
                ToastManager.show(
                    'You found a secret! ...but titles are earned, not clicked into existence.',
                    'info'
                );
                return;
            }

            this._sparkleTimer = setTimeout(function() {
                self._sparkleClickCount = 0;
            }, 2000);
        }
    };

    window.PlatPursuit.MyTitlesPage = MyTitlesPage;
})();
