(function() {
    'use strict';

    var API = window.PlatPursuit.API;
    var ToastManager = window.PlatPursuit.ToastManager;

    var MyTitlesPage = {
        _earnedGrid: null,
        _banner: null,

        init: function() {
            this._earnedGrid = document.getElementById('earned-grid');
            this._banner = document.getElementById('equipped-banner');
            this._bindEvents();
        },

        _bindEvents: function() {
            // Event delegation on earned grid
            if (this._earnedGrid) {
                this._earnedGrid.addEventListener('click', function(e) {
                    // Let link clicks pass through normally
                    if (e.target.closest('a')) return;

                    var card = e.target.closest('[data-title-id]');
                    if (!card) return;

                    // If already equipped, do nothing
                    if (card.classList.contains('ring-secondary')) return;

                    var titleId = parseInt(card.dataset.titleId, 10);
                    if (titleId) MyTitlesPage._equipTitle(titleId);
                });
            }

            // Unequip button in banner
            if (this._banner) {
                this._banner.addEventListener('click', function(e) {
                    var btn = e.target.closest('[data-action="unequip"]');
                    if (btn) {
                        MyTitlesPage._unequipTitle();
                    }
                });
            }
        },

        _equipTitle: function(titleId) {
            API.post('/api/v1/equip-title/', { title_id: titleId })
                .then(function(data) {
                    if (data.success) {
                        MyTitlesPage._updateUI(titleId, data.title_name);
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
            API.post('/api/v1/equip-title/', { title_id: null })
                .then(function(data) {
                    if (data.success) {
                        MyTitlesPage._updateUI(null, null);
                        ToastManager.show('Title removed.', 'success');
                    }
                })
                .catch(function() {
                    ToastManager.show('Failed to remove title.', 'error');
                });
        },

        _updateUI: function(equippedTitleId, titleName) {
            if (!this._earnedGrid) return;

            // Update all earned cards
            var cards = this._earnedGrid.querySelectorAll('[data-title-id]');
            cards.forEach(function(card) {
                var cardTitleId = parseInt(card.dataset.titleId, 10);
                var isEquipped = equippedTitleId !== null && cardTitleId === equippedTitleId;
                var footer = card.querySelector('.card-body .border-t');
                if (!footer) return;

                // Update ring styling
                if (isEquipped) {
                    card.classList.add('ring-2', 'ring-secondary');
                } else {
                    card.classList.remove('ring-2', 'ring-secondary');
                }

                // Update button / badge in footer
                var actionEl = footer.querySelector('.btn, .badge-secondary');
                if (actionEl) {
                    if (isEquipped) {
                        // Replace button with "Equipped" badge
                        var badge = document.createElement('span');
                        badge.className = 'badge badge-secondary badge-sm';
                        badge.textContent = 'Equipped';
                        actionEl.replaceWith(badge);
                    } else if (actionEl.classList.contains('badge-secondary')) {
                        // Replace badge with "Equip" button
                        var btn = document.createElement('button');
                        btn.type = 'button';
                        btn.className = 'btn btn-secondary btn-xs';
                        btn.dataset.action = 'equip';
                        btn.dataset.titleId = cardTitleId;
                        btn.textContent = 'Equip';
                        actionEl.replaceWith(btn);
                    }
                }
            });

            // Update banner
            if (this._banner) {
                var bannerBody = this._banner.querySelector('.card-body');
                if (!bannerBody) return;

                var nameEl = bannerBody.querySelector('#equipped-title-name');
                var unequipBtn = bannerBody.querySelector('[data-action="unequip"]');

                if (equippedTitleId !== null && titleName) {
                    // Show equipped state
                    if (nameEl) {
                        nameEl.textContent = titleName;
                        nameEl.className = 'text-xl ' + (document.body.dataset.premium === 'true' ? 'legendary-title' : 'text-secondary') + ' italic pr-1 font-bold';
                    } else {
                        nameEl = document.createElement('p');
                        nameEl.id = 'equipped-title-name';
                        nameEl.className = 'text-xl text-secondary italic pr-1 font-bold';
                        nameEl.textContent = titleName;
                        var label = bannerBody.querySelector('p');
                        if (label) label.after(nameEl);
                    }
                    if (!unequipBtn) {
                        unequipBtn = document.createElement('button');
                        unequipBtn.type = 'button';
                        unequipBtn.className = 'btn btn-ghost btn-xs text-base-content/50';
                        unequipBtn.dataset.action = 'unequip';
                        unequipBtn.setAttribute('aria-label', 'Unequip title');
                        unequipBtn.textContent = 'Remove Title';
                        bannerBody.appendChild(unequipBtn);
                    }
                } else {
                    // Show empty state
                    if (nameEl) {
                        nameEl.textContent = 'No title equipped';
                        nameEl.className = 'text-lg text-base-content/40 italic';
                    }
                    if (unequipBtn) unequipBtn.remove();
                }
            }
        }
    };

    window.PlatPursuit.MyTitlesPage = MyTitlesPage;
})();
