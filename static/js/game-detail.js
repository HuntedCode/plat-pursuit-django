/**
 * Game Detail Page JavaScript
 * Handles carousel navigation and form filtering with scroll position preservation
 */

document.addEventListener('DOMContentLoaded', () => {
    // Get container with data attributes
    const container = document.getElementById('game-detail-container');
    if (!container) return;

    const baseUrl = container.dataset.baseUrl;
    const scrollKey = container.dataset.scrollKey;

    // Parse initial query params from URL
    const urlParams = new URLSearchParams(window.location.search);
    const queryParams = new URLSearchParams();
    let page = 1;
    let nextPageUrl = '';

    // Copy URL params to queryParams
    for (const [key, value] of urlParams) {
        if (key !== 'page') {
            queryParams.append(key, value);
        } else {
            page = parseInt(value) || 1;
        }
    }

    // Update nextPageUrl
    nextPageUrl = `${baseUrl}?page=${page + 1}&${queryParams.toString()}`;

    // ====================
    // Carousel Navigation
    // ====================
    const carouselNavLinks = document.querySelectorAll('[data-slide-to]');
    const carousel = document.getElementById('screenshot-carousel');

    if (carousel && carouselNavLinks.length > 0) {
        carouselNavLinks.forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const targetId = e.currentTarget.dataset.slideTo;
                const targetSlide = document.getElementById(targetId);
                if (targetSlide) {
                    carousel.scrollTo({
                        left: targetSlide.offsetLeft,
                        behavior: 'smooth'
                    });
                }
            });
        });
    }

    // ====================
    // Form Filtering with Scroll Position Preservation
    // ====================
    const form = document.getElementById('filter-form');
    const unearnedToggle = document.getElementById('unearned-toggle');

    // Save scroll position before form submit
    if (form) {
        form.addEventListener('submit', () => {
            localStorage.setItem(scrollKey, window.scrollY);
            page = 2;
            nextPageUrl = `${baseUrl}?page=${page}&${queryParams.toString()}`;
        });
    }

    if (unearnedToggle) {
        unearnedToggle.addEventListener('submit', () => {
            localStorage.setItem(scrollKey, window.scrollY);
            page = 2;
            nextPageUrl = `${baseUrl}?page=${page}&${queryParams.toString()}`;
        });
    }

    // Restore scroll position after page load
    const savedScroll = localStorage.getItem(scrollKey);
    if (savedScroll) {
        window.scrollTo({
            top: parseInt(savedScroll),
            behavior: 'smooth'
        });
        localStorage.removeItem(scrollKey);
    }

    // ====================
    // Quick Rate Modal
    // ====================
    const quickRateModal = document.getElementById('quick-rate-modal');
    const quickRateForm = document.getElementById('quick-rate-form');

    if (quickRateModal && quickRateForm) {
        var _qrConceptId = null;
        var _qrGroupId = null;
        var _qrSourceBtn = null;

        // Map slider names to display element IDs
        var sliderDisplayMap = {
            difficulty: 'qr-difficulty-val',
            grindiness: 'qr-grindiness-val',
            fun_ranking: 'qr-fun-val',
            overall_rating: 'qr-overall-val'
        };

        function formatSliderValue(name, value) {
            return name === 'overall_rating' ? parseFloat(value).toFixed(1) : value;
        }

        // Live value display for sliders
        quickRateForm.querySelectorAll('input[type="range"]').forEach(function(slider) {
            var valEl = document.getElementById(sliderDisplayMap[slider.name]);
            if (valEl) {
                valEl.textContent = formatSliderValue(slider.name, slider.value);
                slider.addEventListener('input', function() {
                    valEl.textContent = formatSliderValue(slider.name, slider.value);
                });
            }
        });

        // Open modal from Quick Rate button
        document.addEventListener('click', function(e) {
            var btn = e.target.closest('.quick-rate-btn');
            if (!btn) return;

            _qrConceptId = btn.dataset.conceptId;
            _qrGroupId = btn.dataset.groupId;
            _qrSourceBtn = btn;

            // Set hours label
            var hoursLabel = document.getElementById('qr-hours-label');
            if (hoursLabel) hoursLabel.textContent = btn.dataset.hoursLabel || 'Hours to Platinum';

            // Pre-fill from existing rating or reset to defaults
            var existing = btn.dataset.existing ? JSON.parse(btn.dataset.existing) : null;
            var form = quickRateForm;

            form.querySelector('[name="difficulty"]').value = existing ? existing.difficulty : 5;
            form.querySelector('[name="grindiness"]').value = existing ? existing.grindiness : 5;
            form.querySelector('[name="hours_to_platinum"]').value = existing ? existing.hours_to_platinum : '';
            form.querySelector('[name="fun_ranking"]').value = existing ? existing.fun_ranking : 5;
            form.querySelector('[name="overall_rating"]').value = existing ? existing.overall_rating : 3;

            // Update display values
            for (var field in sliderDisplayMap) {
                var el = document.getElementById(sliderDisplayMap[field]);
                if (el) el.textContent = formatSliderValue(field, form.querySelector('[name="' + field + '"]').value);
            }

            // Update submit button text
            var submitBtn = document.getElementById('quick-rate-submit');
            if (submitBtn) submitBtn.textContent = existing ? 'Update Rating' : 'Submit Rating';

            // Update title
            var title = document.getElementById('quick-rate-title');
            if (title) title.textContent = existing ? 'Update Your Rating' : 'Rate This Game';

            quickRateModal.showModal();
        });

        // Submit rating
        quickRateForm.addEventListener('submit', async function(e) {
            e.preventDefault();

            var hours = parseInt(quickRateForm.querySelector('[name="hours_to_platinum"]').value, 10);
            if (!hours || hours < 1) {
                PlatPursuit.ToastManager.show('Please enter the hours to complete.', 'warning');
                return;
            }

            var submitBtn = document.getElementById('quick-rate-submit');
            if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Saving...'; }

            try {
                var data = await PlatPursuit.API.post(
                    '/api/v1/reviews/' + _qrConceptId + '/group/' + _qrGroupId + '/rate/',
                    {
                        difficulty: parseInt(quickRateForm.querySelector('[name="difficulty"]').value, 10),
                        grindiness: parseInt(quickRateForm.querySelector('[name="grindiness"]').value, 10),
                        hours_to_platinum: hours,
                        fun_ranking: parseInt(quickRateForm.querySelector('[name="fun_ranking"]').value, 10),
                        overall_rating: parseFloat(quickRateForm.querySelector('[name="overall_rating"]').value)
                    }
                );

                PlatPursuit.ToastManager.show(data.message || 'Rating saved!', 'success');

                // Update the ratings grid live
                if (data.community_averages) {
                    var panel = _qrSourceBtn.closest('.community-tab-panel') || document.getElementById('community-tabs-section');
                    if (panel) {
                        var grid = panel.querySelector('[data-ratings-grid]');
                        if (grid) {
                            var avg = data.community_averages;
                            var allColors = ['success', 'warning', 'error', 'accent'];
                            var statMap = {
                                difficulty: { val: avg.avg_difficulty, max: 10, thresholds: [4, 8], colors: ['success', 'warning', 'error'] },
                                grindiness: { val: avg.avg_grindiness, max: 10, thresholds: [4, 8], colors: ['success', 'warning', 'error'] },
                                hours: { val: avg.avg_hours, max: 100, thresholds: [25, 75, 100], colors: ['success', 'warning', 'accent', 'error'] },
                                fun: { val: avg.avg_fun, max: 10, thresholds: [4, 8], colors: ['error', 'warning', 'success'] },
                                overall: { val: avg.avg_rating, max: 5, thresholds: [2, 4], colors: ['error', 'warning', 'success'] }
                            };

                            function getColor(val, thresholds, colors) {
                                for (var i = 0; i < thresholds.length; i++) {
                                    if (val < thresholds[i]) return colors[i];
                                }
                                return colors[colors.length - 1];
                            }

                            for (var stat in statMap) {
                                var s = statMap[stat];
                                var cell = grid.querySelector('[data-stat="' + stat + '"]');
                                if (!cell) continue;

                                var color = getColor(s.val, s.thresholds, s.colors);

                                // Update value text + color
                                var valEl = cell.querySelector('[data-stat-value]');
                                if (valEl) {
                                    var suffix = stat === 'hours' ? '<span class="text-[0.6rem] text-base-content/40 font-normal">h</span>' :
                                                 stat === 'overall' ? '<span class="text-[0.6rem] text-base-content/40 font-normal">/5</span>' : '';
                                    var display = stat === 'hours' ? Math.round(s.val).toLocaleString() : s.val.toFixed(1);
                                    valEl.innerHTML = display + suffix;
                                    allColors.forEach(function(c) { valEl.classList.remove('text-' + c); });
                                    valEl.classList.add('text-' + color);
                                    valEl.classList.remove('text-base-content/20');
                                }

                                // Update progress bar value + color
                                var progress = cell.querySelector('progress');
                                if (progress) {
                                    progress.value = s.val;
                                    allColors.forEach(function(c) { progress.classList.remove('progress-' + c); });
                                    progress.classList.add('progress-' + color);
                                }
                            }
                        }
                        var avg = data.community_averages;
                        var countEl = panel.querySelector('[data-ratings-count]');
                        if (countEl && avg.count !== undefined) {
                            countEl.textContent = 'Based on ' + avg.count.toLocaleString() + ' community rating' + (avg.count === 1 ? '' : 's') + '.';
                        }
                    }
                }

                // Update button text and data
                if (_qrSourceBtn) {
                    _qrSourceBtn.dataset.existing = JSON.stringify({
                        difficulty: parseInt(quickRateForm.querySelector('[name="difficulty"]').value, 10),
                        grindiness: parseInt(quickRateForm.querySelector('[name="grindiness"]').value, 10),
                        hours_to_platinum: hours,
                        fun_ranking: parseInt(quickRateForm.querySelector('[name="fun_ranking"]').value, 10),
                        overall_rating: parseFloat(quickRateForm.querySelector('[name="overall_rating"]').value)
                    });
                    _qrSourceBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg> Update Your Rating';
                }

                quickRateModal.close();
            } catch (error) {
                var msg = 'Failed to save rating.';
                try { var errData = await error.response?.json(); msg = errData?.error || msg; } catch (_) {}
                PlatPursuit.ToastManager.show(msg, 'error');
            } finally {
                if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = _qrSourceBtn?.dataset.existing ? 'Update Rating' : 'Submit Rating'; }
            }
        });
    }
});
