/**
 * Fundraiser Page and Admin JavaScript
 *
 * FundraiserPage: Donation form, badge picker modal, and claim workflow
 * FundraiserAdmin: Tabs, claim status management
 */
(function() {

const { API, ToastManager: Toast, HTMLUtils } = window.PlatPursuit;

// ─── Fundraiser Public Page ─────────────────────────────────────────────────

const FundraiserPage = {
    _state: {
        selectedAmount: null,
        selectedProvider: 'stripe',
        selectedBadgeId: null,
        selectedBadgeName: null,
        donationId: null,
    },

    init() {
        this._initHeroCTA();
        this._initDonationForm();
        this._initProviderToggle();
        this._initBadgePicker();
    },

    // ── Hero CTA Smooth Scroll ─────────────────────────────────────────

    _initHeroCTA() {
        const cta = document.getElementById('hero-donate-cta');
        const target = document.getElementById('donation-form');
        if (!cta || !target) return;

        cta.addEventListener('click', () => {
            target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
    },

    // ── Donation Form ────────────────────────────────────────────────────

    _initDonationForm() {
        const form = document.getElementById('donation-form');
        if (!form) return;

        const amountInput = document.getElementById('donation-amount');
        const presets = form.querySelectorAll('.amount-preset');
        const donateBtn = document.getElementById('donate-btn');
        const minAmount = parseFloat(form.dataset.min) || 1;

        // Preset buttons
        presets.forEach(btn => {
            btn.addEventListener('click', () => {
                presets.forEach(b => {
                    b.classList.remove('btn-primary', 'scale-105');
                });
                btn.classList.add('btn-primary', 'scale-105');
                amountInput.value = btn.dataset.amount;
                this._state.selectedAmount = parseFloat(btn.dataset.amount);
                this._updateDonateButton();
                this._updatePicksHint();
            });
        });

        // Custom amount input
        amountInput.addEventListener('input', () => {
            presets.forEach(b => {
                b.classList.remove('btn-primary', 'scale-105');
            });
            const val = parseFloat(amountInput.value);
            this._state.selectedAmount = isNaN(val) ? null : val;

            // Re-highlight preset if it matches
            if (this._state.selectedAmount) {
                presets.forEach(b => {
                    if (parseFloat(b.dataset.amount) === this._state.selectedAmount) {
                        b.classList.add('btn-primary', 'scale-105');
                    }
                });
            }
            this._updateDonateButton();
            this._updatePicksHint();
        });

        // Donate button
        if (donateBtn) {
            donateBtn.addEventListener('click', () => this._submitDonation());
        }
    },

    _updateDonateButton() {
        const btn = document.getElementById('donate-btn');
        const btnText = document.getElementById('donate-btn-text');
        const form = document.getElementById('donation-form');
        if (!btn || !form) return;

        const minAmount = parseFloat(form.dataset.min) || 1;
        const amount = this._state.selectedAmount;
        const warning = document.getElementById('min-amount-warning');

        if (amount && amount >= minAmount) {
            btn.disabled = false;
            btnText.textContent = `Donate $${parseInt(amount, 10)}`;
            if (warning) warning.classList.add('hidden');
        } else {
            btn.disabled = true;
            btnText.textContent = 'Donate';
            if (warning) warning.classList.toggle('hidden', !amount || amount <= 0);
        }
    },

    _updatePicksHint() {
        const hint = document.getElementById('picks-hint');
        if (!hint) return;

        const form = document.getElementById('donation-form');
        const divisor = parseInt(form?.dataset.pickDivisor, 10) || 10;
        const amount = this._state.selectedAmount;
        if (!amount || amount < divisor) {
            hint.innerHTML = amount
                ? `<span class="text-xs text-base-content/50">Donate $${divisor} or more to earn badge artwork picks!</span>`
                : '';
            return;
        }

        const picks = Math.floor(amount / divisor);
        hint.innerHTML = `<span class="badge badge-primary badge-sm font-semibold">This earns ${picks} badge artwork pick${picks !== 1 ? 's' : ''}!</span>`;
    },

    // ── Provider Toggle ──────────────────────────────────────────────────

    _initProviderToggle() {
        const options = document.querySelectorAll('.provider-option');
        if (!options.length) return;

        const borderColors = { stripe: 'border-primary', paypal: 'border-info' };

        options.forEach(option => {
            option.addEventListener('click', () => {
                options.forEach(o => {
                    o.classList.remove('selected-provider', 'border-primary', 'border-info');
                });
                const color = borderColors[option.dataset.provider] || 'border-primary';
                option.classList.add('selected-provider', color);
                option.querySelector('input[type="radio"]').checked = true;
                this._state.selectedProvider = option.dataset.provider;
            });
        });
    },

    // ── Submit Donation ──────────────────────────────────────────────────

    async _submitDonation() {
        const btn = document.getElementById('donate-btn');
        const form = document.getElementById('donation-form');
        if (!btn || !form || btn.disabled) return;

        const slug = form.dataset.slug;
        const amount = this._state.selectedAmount;
        const provider = this._state.selectedProvider;
        const isAnonymous = document.getElementById('donation-anonymous')?.checked || false;
        const message = document.getElementById('donation-message')?.value || '';

        btn.disabled = true;
        const btnText = document.getElementById('donate-btn-text');
        const originalText = btnText.textContent;
        btnText.textContent = 'Processing...';

        try {
            const data = await API.post(`/api/v1/fundraiser/${slug}/donate/`, {
                amount: amount,
                provider: provider,
                is_anonymous: isAnonymous,
                message: message,
            });

            if (data.redirect_url) {
                window.location.href = data.redirect_url;
            } else {
                Toast.show('Something went wrong. Please try again.', 'error');
                btn.disabled = false;
                btnText.textContent = originalText;
            }
        } catch (error) {
            let msg = 'Failed to create checkout. Please try again.';
            try {
                const errData = await error.response?.json();
                msg = errData?.error || msg;
            } catch {}
            Toast.show(msg, 'error');
            btn.disabled = false;
            btnText.textContent = originalText;
        }
    },

    // ── Badge Picker ─────────────────────────────────────────────────────

    _initBadgePicker() {
        const openBtn = document.getElementById('open-badge-picker-btn');
        const modal = document.getElementById('badge-picker-modal');
        if (!openBtn || !modal) return;

        openBtn.addEventListener('click', () => {
            this._resetPickerState();
            modal.showModal();
        });

        // Search/filter
        const searchInput = document.getElementById('badge-picker-search');
        if (searchInput) {
            searchInput.addEventListener('input', () => this._filterBadges(searchInput.value));
        }

        // Badge selection
        const grid = document.getElementById('badge-picker-grid');
        if (grid) {
            grid.addEventListener('click', (e) => {
                const option = e.target.closest('.badge-pick-option');
                if (!option) return;
                this._selectBadge(option);
            });
        }

        // Cancel claim
        const cancelBtn = document.getElementById('cancel-claim-btn');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => this._resetPickerState());
        }

        // Confirm claim
        const confirmBtn = document.getElementById('confirm-claim-btn');
        if (confirmBtn) {
            confirmBtn.addEventListener('click', () => this._confirmClaim());
        }
    },

    _filterBadges(query) {
        const grid = document.getElementById('badge-picker-grid');
        const emptyMsg = document.getElementById('badge-picker-empty');
        if (!grid) return;

        const normalizedQuery = query.toLowerCase().trim();
        const options = grid.querySelectorAll('.badge-pick-option');
        let visibleCount = 0;

        options.forEach(option => {
            const name = (option.dataset.badgeName || '').toLowerCase();
            const matches = !normalizedQuery || name.includes(normalizedQuery);
            option.classList.toggle('hidden', !matches);
            if (matches) visibleCount++;
        });

        if (emptyMsg) {
            emptyMsg.classList.toggle('hidden', visibleCount > 0);
        }
    },

    _selectBadge(option) {
        // Deselect previous
        const grid = document.getElementById('badge-picker-grid');
        grid.querySelectorAll('.badge-pick-option').forEach(o => {
            o.classList.remove('border-primary', 'border-4', 'bg-primary/5');
            o.classList.add('border-base-300', 'border-2');
        });

        // Select new
        option.classList.remove('border-base-300', 'border-2');
        option.classList.add('border-primary', 'border-4', 'bg-primary/5');

        this._state.selectedBadgeId = option.dataset.badgeId;
        this._state.selectedBadgeName = option.dataset.badgeName;

        // Show confirm area
        const confirmArea = document.getElementById('badge-claim-confirm');
        const claimName = document.getElementById('claim-badge-name');
        if (confirmArea && claimName) {
            claimName.textContent = this._state.selectedBadgeName;
            confirmArea.classList.remove('hidden');
        }
    },

    _resetPickerState() {
        this._state.selectedBadgeId = null;
        this._state.selectedBadgeName = null;

        const confirmArea = document.getElementById('badge-claim-confirm');
        if (confirmArea) confirmArea.classList.add('hidden');

        const grid = document.getElementById('badge-picker-grid');
        if (grid) {
            grid.querySelectorAll('.badge-pick-option').forEach(o => {
                o.classList.remove('border-primary', 'border-4', 'bg-primary/5');
                o.classList.add('border-base-300', 'border-2');
            });
        }

        const searchInput = document.getElementById('badge-picker-search');
        if (searchInput) {
            searchInput.value = '';
            this._filterBadges('');
        }
    },

    async _confirmClaim() {
        const badgeId = this._state.selectedBadgeId;
        if (!badgeId) return;

        const confirmBtn = document.getElementById('confirm-claim-btn');
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.textContent = 'Claiming...';
        }

        // Find the first donation with remaining picks
        const donationId = this._findDonationWithPicks();
        if (!donationId) {
            Toast.show('No badge picks remaining.', 'error');
            if (confirmBtn) {
                confirmBtn.disabled = false;
                confirmBtn.textContent = 'Confirm Claim';
            }
            return;
        }

        try {
            const data = await API.post('/api/v1/fundraiser/claim/', {
                badge_id: parseInt(badgeId),
                donation_id: donationId,
            });

            if (data.success) {
                Toast.show(
                    `Successfully claimed ${HTMLUtils.escape(data.claim.series_name)} for artwork!`,
                    'success'
                );

                // Close modal and reload to reflect changes
                const modal = document.getElementById('badge-picker-modal');
                if (modal) modal.close();

                // Reload to update picks count and available badges
                setTimeout(() => window.location.reload(), 1000);
            }
        } catch (error) {
            let msg = 'Failed to claim badge. Please try again.';
            try {
                const errData = await error.response?.json();
                msg = errData?.error || msg;
            } catch {}
            Toast.show(msg, 'error');
        } finally {
            if (confirmBtn) {
                confirmBtn.disabled = false;
                confirmBtn.textContent = 'Confirm Claim';
            }
        }
    },

    _findDonationWithPicks() {
        // Data-attribute approach: look for donation IDs embedded in the page
        const picksSection = document.querySelector('[data-donation-ids]');
        if (picksSection) {
            try {
                const ids = JSON.parse(picksSection.dataset.donationIds);
                return ids[0] || null;
            } catch {
                return null;
            }
        }

        // Fallback: if user_donations are rendered as data attributes
        const donationEls = document.querySelectorAll('[data-donation-id][data-picks-remaining]');
        for (const el of donationEls) {
            if (parseInt(el.dataset.picksRemaining) > 0) {
                return parseInt(el.dataset.donationId);
            }
        }

        return null;
    },
};


// ─── Fundraiser Admin Page ──────────────────────────────────────────────────

const FundraiserAdmin = {
    init() {
        this._initTabs();
        this._initClaimStatusUpdates();
    },

    // ── Tab Switching ────────────────────────────────────────────────────

    _initTabs() {
        const tabContainer = document.getElementById('fundraiser-admin-tabs');
        if (!tabContainer) return;

        tabContainer.addEventListener('click', (e) => {
            const tab = e.target.closest('.tab');
            if (!tab) return;

            // Update active tab
            tabContainer.querySelectorAll('.tab').forEach(t => t.classList.remove('tab-active'));
            tab.classList.add('tab-active');

            // Show corresponding panel
            const panels = document.querySelectorAll('.admin-panel');
            panels.forEach(p => p.classList.add('hidden'));

            const panelId = `panel-${tab.dataset.tab}`;
            const panel = document.getElementById(panelId);
            if (panel) panel.classList.remove('hidden');
        });
    },

    // ── Claim Status Updates ─────────────────────────────────────────────

    _initClaimStatusUpdates() {
        const selects = document.querySelectorAll('.claim-status-select');
        selects.forEach(select => {
            select.addEventListener('change', async (e) => {
                const claimId = select.dataset.claimId;
                const newStatus = e.target.value;
                if (!claimId || !newStatus) return;

                select.disabled = true;

                try {
                    const data = await API.post('/api/v1/admin/fundraiser/claim-status/', {
                        claim_id: parseInt(claimId),
                        status: newStatus,
                    });

                    if (data.success) {
                        Toast.show(
                            `Claim status updated to "${newStatus}".`,
                            'success'
                        );

                        // Update the status badge in the row
                        const row = select.closest('tr');
                        if (row) {
                            const badge = row.querySelector('.claim-status-badge');
                            if (badge) {
                                badge.className = 'claim-status-badge badge badge-sm';
                                if (newStatus === 'completed') {
                                    badge.classList.add('badge-success');
                                    badge.textContent = 'Artwork Complete';
                                } else if (newStatus === 'in_progress') {
                                    badge.classList.add('badge-warning');
                                    badge.textContent = 'Artwork In Progress';
                                }
                            }

                            // If completed, replace select with "Done" text
                            if (newStatus === 'completed') {
                                const td = select.closest('td');
                                if (td) {
                                    const now = new Date();
                                    const monthDay = now.toLocaleDateString('en-US', {
                                        month: 'short', day: 'numeric',
                                    });
                                    td.innerHTML = `<span class="text-xs text-success">Done ${HTMLUtils.escape(monthDay)}</span>`;
                                }
                            } else {
                                // Reset select for next transition
                                select.value = '';
                                select.disabled = false;
                            }
                        }
                    }
                } catch (error) {
                    let msg = 'Failed to update claim status.';
                    try {
                        const errData = await error.response?.json();
                        msg = errData?.error || msg;
                    } catch {}
                    Toast.show(msg, 'error');
                    select.value = '';
                    select.disabled = false;
                }
            });
        });
    },
};


// ─── Export ──────────────────────────────────────────────────────────────────

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.FundraiserPage = FundraiserPage;
window.PlatPursuit.FundraiserAdmin = FundraiserAdmin;

})();
