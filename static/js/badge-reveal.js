/**
 * Badge Reveal Spinner - Staff tool for randomly selecting badge artwork reveals.
 *
 * Configures the shared ReelSpinner component for badge artwork selection.
 * Pool management is client-side: checkboxes toggle which badges are eligible
 * for the spin without any API calls.
 *
 * Overrides ReelSpinner.FLAVOR_TEXTS on page load with badge-reveal-themed
 * texts. This is safe because only one spinner exists per page load, and the
 * default texts are only relevant on challenge detail pages (separate loads).
 *
 * Uses: PlatPursuit.ReelSpinner, PlatPursuit.ToastManager
 */

(function() {
    'use strict';

    // Badge-reveal-themed flavor texts (replaces the default platinum-themed ones)
    var BADGE_REVEAL_FLAVORS = [
        'A new badge has entered the arena!',
        'Fresh artwork incoming!',
        'The community is going to love this one.',
        'Another masterpiece revealed.',
        'Badge hunters, rejoice!',
        'This one was worth the wait.',
        'The artist has spoken.',
        'Behold: new badge art!',
    ];

    var BadgeReveal = {
        _allBadges: [],
        _spinner: null,
        _rebuildTimeout: null,
        _resultObserver: null,

        init: function() {
            this._allBadges = window._BADGE_REVEAL_POOL || [];

            // Override the default flavor texts for this page
            PlatPursuit.ReelSpinner.FLAVOR_TEXTS = BADGE_REVEAL_FLAVORS;

            this._bindCheckboxes();
            this._bindSelectButtons();
            this._rebuildSpinner();
        },

        /**
         * Get the currently active pool (only checked badges).
         */
        _getActivePool: function() {
            var checkboxes = document.querySelectorAll('.pool-checkbox');
            var activeIds = new Set();
            checkboxes.forEach(function(cb) {
                if (cb.checked) activeIds.add(cb.dataset.badgeId);
            });
            return this._allBadges.filter(function(b) {
                return activeIds.has(String(b.badge_id));
            });
        },

        /**
         * Rebuild the ReelSpinner instance with the current active pool.
         * Safely removes any existing spinner modal (only when no spin is active).
         */
        _rebuildSpinner: function() {
            var activePool = this._getActivePool();

            // Clean up existing spinner modal (safe: only called when not spinning)
            if (this._resultObserver) {
                this._resultObserver.disconnect();
                this._resultObserver = null;
            }
            var existingModal = document.getElementById('pick-next-game-modal');
            if (existingModal) existingModal.remove();

            // Replace the spin button to strip stale event listeners from
            // previous ReelSpinner instances (each .bind() call adds a new one)
            var oldBtn = document.getElementById('badge-reveal-spin-btn');
            if (oldBtn) {
                var freshBtn = oldBtn.cloneNode(true);
                oldBtn.parentNode.replaceChild(freshBtn, oldBtn);
            }

            // Create new spinner with active pool
            this._spinner = new PlatPursuit.ReelSpinner({
                slots: activePool,
                challengeId: null,
                tileLabel: function(s) { return s.series_name.length > 12 ? s.series_name.substring(0, 11) + '\u2026' : s.series_name; },
                tileLabelClass: 'text-[0.6rem] font-bold leading-tight truncate max-w-[5.5rem]',
                tileIcon: function(s) { return s.icon; },
                tileName: function(s) { return s.series_name; },
                resultBadge: function(s) { return s.series_name; },
                coverApiUrl: null,
                coverPayload: null,
                gridSelector: null,
                winnerKey: function(s) { return String(s.badge_id); },
            });

            this._spinner.bind('badge-reveal-spin-btn');
            this._updatePoolCount(activePool.length);
            this._watchForSpinResult();
        },

        /**
         * Watch for the spin button click, then observe the result card.
         *
         * The ReelSpinner modal is created on first click (injected into DOM).
         * We listen for the button click, wait for the modal to exist, then
         * attach a MutationObserver on the result element to detect when
         * the spin result becomes visible so we can customize the display.
         */
        _watchForSpinResult: function() {
            var self = this;
            var btn = document.getElementById('badge-reveal-spin-btn');
            if (!btn || btn.disabled) return;

            btn.addEventListener('click', function onFirstClick() {
                // Poll briefly for the modal to be injected into the DOM
                var attempts = 0;
                var poll = setInterval(function() {
                    var resultEl = document.getElementById('spinner-result');
                    attempts++;
                    if (!resultEl && attempts < 20) return; // up to 1s
                    clearInterval(poll);
                    if (!resultEl) return;

                    // Observe the result card for visibility changes
                    self._resultObserver = new MutationObserver(function() {
                        if (!resultEl.classList.contains('hidden')) {
                            self._customizeResult();
                        }
                    });
                    self._resultObserver.observe(resultEl, { attributes: true, attributeFilter: ['class'] });
                }, 50);

                btn.removeEventListener('click', onFirstClick);
            });
        },

        /**
         * Replace the default "Not started yet" progress text with donor info,
         * hide the "Set as Display Cover" button (not applicable for badge reveals),
         * and update the last spin result card on the page.
         */
        _customizeResult: function() {
            if (!this._spinner || !this._spinner._winner) return;

            var winner = this._spinner._winner;

            // Override the progress text with donor info
            var progressEl = document.getElementById('spinner-result-progress');
            if (progressEl) {
                progressEl.textContent = winner.donor ? 'Claimed by: ' + winner.donor : '';
            }

            // Hide the "Set as Display Cover" button (no cover API for badge reveals)
            var coverBtn = document.getElementById('spinner-set-cover-btn');
            if (coverBtn) coverBtn.classList.add('hidden');

            // Update the "Last Pick" card below the spin section
            var lastResult = document.getElementById('last-spin-result');
            if (lastResult) {
                lastResult.classList.remove('hidden');
                var icon = document.getElementById('last-spin-icon');
                var name = document.getElementById('last-spin-name');
                var donor = document.getElementById('last-spin-donor');
                if (icon) icon.src = winner.icon || '';
                if (name) name.textContent = winner.series_name || '';
                if (donor) donor.textContent = winner.donor ? 'Claimed by: ' + winner.donor : '';
            }
        },

        /**
         * Bind checkbox change events to rebuild the spinner pool.
         * Debounced to prevent rapid rebuilds when clicking quickly.
         */
        _bindCheckboxes: function() {
            var self = this;
            document.querySelectorAll('.pool-checkbox').forEach(function(cb) {
                cb.addEventListener('change', function() {
                    var row = cb.closest('.pool-row');
                    if (row) {
                        row.classList.toggle('opacity-40', !cb.checked);
                    }
                    if (self._rebuildTimeout) clearTimeout(self._rebuildTimeout);
                    self._rebuildTimeout = setTimeout(function() {
                        self._rebuildSpinner();
                    }, 150);
                });
            });
        },

        /**
         * Bind the "All" and "None" selection buttons.
         */
        _bindSelectButtons: function() {
            var self = this;
            var allBtn = document.getElementById('pool-select-all');
            var noneBtn = document.getElementById('pool-select-none');

            if (allBtn) {
                allBtn.addEventListener('click', function() {
                    document.querySelectorAll('.pool-checkbox').forEach(function(cb) {
                        cb.checked = true;
                        var row = cb.closest('.pool-row');
                        if (row) row.classList.remove('opacity-40');
                    });
                    self._rebuildSpinner();
                });
            }

            if (noneBtn) {
                noneBtn.addEventListener('click', function() {
                    document.querySelectorAll('.pool-checkbox').forEach(function(cb) {
                        cb.checked = false;
                        var row = cb.closest('.pool-row');
                        if (row) row.classList.add('opacity-40');
                    });
                    self._rebuildSpinner();
                });
            }
        },

        /**
         * Update all pool count displays on the page.
         */
        _updatePoolCount: function(count) {
            var countStat = document.getElementById('pool-count-stat');
            if (countStat) countStat.textContent = count;

            var countLabel = document.getElementById('pool-count-label');
            if (countLabel) countLabel.textContent = count;
        },
    };

    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.BadgeReveal = BadgeReveal;
})();
