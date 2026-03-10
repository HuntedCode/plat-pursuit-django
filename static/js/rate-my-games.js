/**
 * Rate My Games Wizard: Cycle through platinumed games to rate/review them.
 *
 * Fetches a queue of unrated/unreviewed games from the API and presents
 * them one at a time with rating sliders and a review form side by side.
 */
window.PlatPursuit = window.PlatPursuit || {};

PlatPursuit.RateMyGames = {
    config: null,
    queue: [],
    currentIndex: 0,
    totalCount: 0,
    completedCount: 0,
    activeFilters: new Set(['unrated']),
    queueType: 'base',
    isLoading: false,
    recommended: null,
    offset: 0,
    limit: 20,

    // DLC queue: grouped response from API
    dlcGroups: [],
    dlcFlatQueue: [],
    hasMore: false,

    guidelinesAgreed: false,
    _pendingAction: null,
    isEditingReview: false,

    init(config) {
        this.config = config;

        // Read guidelines status from container data attribute
        const container = document.getElementById('wizard-container');
        this.guidelinesAgreed = container?.dataset.guidelinesAgreed === 'true';

        // Wire up guidelines modal checkbox to enable/disable confirm button
        const agreeCheckbox = document.getElementById('agree-to-guidelines');
        const confirmBtn = document.getElementById('confirm-guidelines-btn');
        if (agreeCheckbox && confirmBtn) {
            agreeCheckbox.addEventListener('change', function() {
                confirmBtn.disabled = !this.checked;
            });
        }

        // Expose confirmGuidelines globally for the modal onclick
        window.confirmGuidelines = () => this.confirmGuidelines();

        this.initFilterButtons();
        this.initQueueTabs();
        this.initRecommendButtons();
        this.initReviewBody();
        this.initMarkdownHelp();
        this.initRatingValidation();
        this.initActionButtons();
        this.initEditReviewButton();
        this.initTrophyToggle();
        this.loadQueue();
    },

    checkGuidelines(callback) {
        if (this.guidelinesAgreed) {
            callback();
        } else {
            this._pendingAction = callback;
            document.getElementById('guidelines-agreement-modal')?.showModal();
        }
    },

    async confirmGuidelines() {
        const checkbox = document.getElementById('agree-to-guidelines');
        if (!checkbox?.checked || this._confirmingGuidelines) return;
        this._confirmingGuidelines = true;

        try {
            await PlatPursuit.API.post('/api/v1/guidelines/agree/', {});
            this.guidelinesAgreed = true;
            document.getElementById('guidelines-agreement-modal')?.close();
            checkbox.checked = false;

            if (this._pendingAction) {
                this._pendingAction();
                this._pendingAction = null;
            }
        } catch (error) {
            console.error('Error agreeing to guidelines:', error);
            PlatPursuit.ToastManager.error('An error occurred. Please try again.');
        } finally {
            this._confirmingGuidelines = false;
        }
    },

    // ------------------------------------------------------------------ //
    //  Filter & DLC Controls
    // ------------------------------------------------------------------ //

    initFilterButtons() {
        document.querySelectorAll('.wizard-filter-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const filter = btn.dataset.filter;

                if (this.activeFilters.has(filter)) {
                    if (this.activeFilters.size <= 1) {
                        PlatPursuit.ToastManager.info('Keep at least one filter active.');
                        return;
                    }
                    this.activeFilters.delete(filter);
                    btn.classList.remove('btn-primary');
                    btn.classList.add('btn-ghost', 'border', 'border-base-300');
                } else {
                    this.activeFilters.add(filter);
                    btn.classList.add('btn-primary');
                    btn.classList.remove('btn-ghost', 'border', 'border-base-300');
                }

                this.resetQueue();
            });
        });
    },

    getFilterParam() {
        // Map active filter set to API param
        const has = (f) => this.activeFilters.has(f);
        if (has('unrated') && has('unreviewed')) return 'both';
        if (has('unreviewed')) return 'unreviewed';
        return 'unrated';
    },

    initQueueTabs() {
        document.querySelectorAll('.wizard-queue-tab').forEach(btn => {
            btn.addEventListener('click', () => {
                const newType = btn.dataset.queue;
                if (newType === this.queueType) return;

                this.queueType = newType;

                // Update tab button styles
                document.querySelectorAll('.wizard-queue-tab').forEach(b => {
                    const isActive = b.dataset.queue === newType;
                    b.classList.toggle('btn-primary', isActive);
                    b.classList.toggle('btn-ghost', !isActive);
                    b.classList.toggle('border', !isActive);
                    b.classList.toggle('border-base-300', !isActive);
                });

                this.resetQueue();
            });
        });
    },

    resetQueue() {
        this.queue = [];
        this.dlcGroups = [];
        this.dlcFlatQueue = [];
        this.hasMore = false;
        this.currentIndex = 0;
        this.completedCount = 0;
        this.offset = 0;
        this.totalCount = 0;
        this.loadQueue();
    },

    // ------------------------------------------------------------------ //
    //  Queue Management
    // ------------------------------------------------------------------ //

    async loadQueue() {
        if (this.isLoading) return;
        this.isLoading = true;

        const loading = document.getElementById('wizard-loading');
        const card = document.getElementById('wizard-card');
        const empty = document.getElementById('wizard-empty');

        if (loading) loading.classList.remove('hidden');
        if (card) card.classList.add('hidden');
        if (empty) empty.classList.add('hidden');

        try {
            const filter = this.getFilterParam();
            const url = `/api/v1/reviews/wizard/queue/?filter=${filter}&queue_type=${this.queueType}&limit=${this.limit}&offset=${this.offset}`;
            const data = await PlatPursuit.API.get(url);

            if (this.queueType === 'dlc') {
                this.loadDlcQueue(data);
            } else {
                this.loadBaseQueue(data);
            }
        } catch (error) {
            const errData = await error.response?.json().catch(() => null);
            PlatPursuit.ToastManager.error(errData?.error || 'Failed to load game queue.');
        } finally {
            this.isLoading = false;
            if (loading) loading.classList.add('hidden');
        }
    },

    loadBaseQueue(data) {
        if (this.offset === 0) {
            this.queue = data.queue || [];
            this.totalCount = data.count || 0;
            this.currentIndex = 0;
        } else {
            this.queue = this.queue.concat(data.queue || []);
        }

        if (this.queue.length === 0) {
            this.showEmpty();
        } else {
            this.showCurrentGame();
        }
    },

    loadDlcQueue(data) {
        this.hasMore = data.has_more || false;

        if (this.offset === 0) {
            this.dlcGroups = data.groups || [];
            this.dlcFlatQueue = this.flattenDlcGroups(this.dlcGroups);
            this.totalCount = data.total_items || 0;
            this.currentIndex = 0;
        } else {
            const newGroups = data.groups || [];
            this.dlcGroups = this.dlcGroups.concat(newGroups);
            this.dlcFlatQueue = this.dlcFlatQueue.concat(this.flattenDlcGroups(newGroups));
        }

        // Use dlcFlatQueue as the working queue for DLC
        this.queue = this.dlcFlatQueue;

        if (this.queue.length === 0) {
            this.showEmpty();
        } else {
            this.showCurrentGame();
        }
    },

    flattenDlcGroups(groups) {
        // Flatten grouped DLC response into a flat array of items,
        // each enriched with parent concept info for rendering
        const flat = [];
        for (const group of groups) {
            for (const item of group.items) {
                flat.push({
                    concept_id: group.concept_id,
                    unified_title: group.unified_title,
                    concept_icon_url: group.concept_icon_url,
                    slug: group.slug,
                    trophy_group_id: item.trophy_group_id,
                    trophy_group_name: item.trophy_group_name,
                    has_rating: item.has_rating,
                    has_review: item.has_review,
                    is_dlc: true,
                    existing_rating: item.existing_rating || null,
                });
            }
        }
        return flat;
    },

    showCurrentGame() {
        const card = document.getElementById('wizard-card');
        const empty = document.getElementById('wizard-empty');
        const loading = document.getElementById('wizard-loading');

        if (loading) loading.classList.add('hidden');

        if (this.currentIndex >= this.queue.length) {
            // Check if there are more pages to fetch
            const moreAvailable = this.queueType === 'dlc'
                ? this.hasMore
                : this.offset + this.limit < this.totalCount;

            if (moreAvailable) {
                this.offset += this.limit;
                this.loadQueue();
                return;
            }
            this.showEmpty();
            return;
        }

        if (empty) empty.classList.add('hidden');
        if (card) card.classList.remove('hidden');

        const game = this.queue[this.currentIndex];
        const esc = PlatPursuit.HTMLUtils.escape;

        // Game header
        const img = document.getElementById('wizard-game-img');
        if (img) {
            img.src = game.concept_icon_url || '';
            img.alt = game.unified_title || '';
        }

        const title = document.getElementById('wizard-game-title');
        if (title) title.textContent = game.unified_title || '';

        const groupName = document.getElementById('wizard-group-name');
        const dlcBadge = document.getElementById('wizard-dlc-badge');
        const isDlc = !!game.is_dlc;
        if (groupName) {
            if (isDlc || game.trophy_group_id !== 'default') {
                groupName.textContent = `\u25B8 ${game.trophy_group_name || ''}`;
                groupName.classList.remove('hidden');
                // DLC group names get stronger styling
                groupName.classList.toggle('text-secondary', isDlc);
                groupName.classList.toggle('font-semibold', isDlc);
                groupName.classList.toggle('text-base-content/50', !isDlc);
            } else {
                groupName.classList.add('hidden');
            }
        }
        if (dlcBadge) {
            dlcBadge.classList.toggle('hidden', !isDlc);
        }

        // Status badges
        const ratingBadge = document.getElementById('wizard-has-rating');
        const reviewBadge = document.getElementById('wizard-has-review');
        if (ratingBadge) ratingBadge.classList.toggle('hidden', !game.has_rating);
        if (reviewBadge) reviewBadge.classList.toggle('hidden', !game.has_review);

        // Hub link
        const hubLink = document.getElementById('wizard-hub-link');
        if (hubLink) hubLink.href = `/reviews/${esc(game.slug || '')}/`;

        // Reset forms and trophy section, then pre-fill if existing data
        this.resetRatingForm();
        this.resetReviewForm();
        this.updateTrophyPanel();

        // Pre-fill rating sliders if user has an existing rating
        if (game.existing_rating) {
            this.prefillRatingForm(game.existing_rating);
        }

        // Update rating section heading
        const ratingHeading = document.querySelector('#wizard-rating-section h3');
        if (ratingHeading) {
            const starSvg = ratingHeading.querySelector('svg').outerHTML;
            const rateTarget = game.is_dlc ? 'This DLC' : 'This Game';
            ratingHeading.innerHTML = `${starSvg} ${game.has_rating ? 'Update Your Rating' : `Rate ${rateTarget}`}`;
        }

        // Toggle between existing review display and form
        this.isEditingReview = false;
        const reviewFormContent = document.getElementById('wizard-review-form-content');
        const existingReviewEl = document.getElementById('wizard-existing-review');
        const reviewHeading = document.querySelector('#wizard-review-section h3');

        if (game.has_review && game.existing_review) {
            // Show existing review display
            if (reviewFormContent) {
                reviewFormContent.classList.add('hidden');
                reviewFormContent.classList.remove('flex');
            }
            if (existingReviewEl) {
                existingReviewEl.classList.remove('hidden');
                existingReviewEl.classList.add('flex');
            }
            this.populateExistingReview(game);
            if (reviewHeading) {
                const pencilSvg = reviewHeading.querySelector('svg').outerHTML;
                reviewHeading.innerHTML = `${pencilSvg} Your Review`;
            }
        } else {
            // Show empty form for new review
            if (reviewFormContent) {
                reviewFormContent.classList.remove('hidden');
                reviewFormContent.classList.add('flex');
            }
            if (existingReviewEl) {
                existingReviewEl.classList.add('hidden');
                existingReviewEl.classList.remove('flex');
            }
            if (reviewHeading) {
                const pencilSvg = reviewHeading.querySelector('svg').outerHTML;
                const reviewTarget = game.is_dlc ? 'This DLC' : 'This Game';
                reviewHeading.innerHTML = `${pencilSvg} Review ${reviewTarget}`;
            }
        }

        // Always show both sections (rating + review) for consistency
        const ratingSection = document.getElementById('wizard-rating-section');
        if (ratingSection) ratingSection.classList.remove('hidden');

        const reviewSection = document.getElementById('wizard-review-section');
        if (reviewSection) reviewSection.classList.remove('hidden');

        // Populate stats bar
        this.populateStats(game);

        this.updateProgress();
        this.updateSubmitButton();

        // Progress section
        const progressSection = document.getElementById('wizard-progress-section');
        if (progressSection) progressSection.classList.remove('hidden');

        const badge = document.getElementById('wizard-progress-badge');
        if (badge) badge.classList.remove('hidden');
    },

    showEmpty() {
        const card = document.getElementById('wizard-card');
        const empty = document.getElementById('wizard-empty');
        const loading = document.getElementById('wizard-loading');
        const progressSection = document.getElementById('wizard-progress-section');

        if (card) card.classList.add('hidden');
        if (loading) loading.classList.add('hidden');
        if (empty) empty.classList.remove('hidden');
        if (progressSection) progressSection.classList.add('hidden');
    },

    advance() {
        this.completedCount++;
        this.currentIndex++;

        // Pre-fetch next batch when running low
        const shouldPrefetch = this.queueType === 'dlc'
            ? this.hasMore
            : this.offset + this.limit < this.totalCount;

        if (
            this.currentIndex >= this.queue.length - 5
            && shouldPrefetch
            && !this.isLoading
        ) {
            this.offset += this.limit;
            this.prefetch();
        }

        this.showCurrentGame();
    },

    async prefetch() {
        try {
            const filter = this.getFilterParam();
            const url = `/api/v1/reviews/wizard/queue/?filter=${filter}&queue_type=${this.queueType}&limit=${this.limit}&offset=${this.offset}`;
            const data = await PlatPursuit.API.get(url);

            if (this.queueType === 'dlc') {
                this.hasMore = data.has_more || false;
                const newGroups = data.groups || [];
                this.dlcGroups = this.dlcGroups.concat(newGroups);
                const newFlat = this.flattenDlcGroups(newGroups);
                this.dlcFlatQueue = this.dlcFlatQueue.concat(newFlat);
                this.queue = this.dlcFlatQueue;
            } else {
                this.queue = this.queue.concat(data.queue || []);
            }
        } catch {
            // Silently fail on prefetch, main load will catch errors
        }
    },

    // ------------------------------------------------------------------ //
    //  Rating Form
    // ------------------------------------------------------------------ //

    resetRatingForm() {
        const form = document.getElementById('wizard-rating-form');
        if (!form) return;
        form.querySelector('[name="difficulty"]').value = 5;
        form.querySelector('[name="grindiness"]').value = 5;
        form.querySelector('[name="hours_to_platinum"]').value = '';
        form.querySelector('[name="fun_ranking"]').value = 5;
        form.querySelector('[name="overall_rating"]').value = 3;
        this.updateHoursChecklist();
    },

    prefillRatingForm(rating) {
        const form = document.getElementById('wizard-rating-form');
        if (!form || !rating) return;
        form.querySelector('[name="difficulty"]').value = rating.difficulty;
        form.querySelector('[name="grindiness"]').value = rating.grindiness;
        form.querySelector('[name="hours_to_platinum"]').value = rating.hours_to_platinum;
        form.querySelector('[name="fun_ranking"]').value = rating.fun_ranking;
        form.querySelector('[name="overall_rating"]').value = rating.overall_rating;
        this.updateHoursChecklist();
    },

    populateStats(game) {
        const bar = document.getElementById('wizard-stats-bar');
        if (!bar) return;

        const stats = game.stats;
        const hasAnyStats = stats || game.platinum_date;

        bar.classList.toggle('hidden', !hasAnyStats);
        if (!hasAnyStats) return;

        // Platinum date
        const platEl = document.getElementById('wizard-stat-plat');
        const platText = document.getElementById('wizard-stat-plat-text');
        if (platEl && platText) {
            if (game.platinum_date) {
                const d = new Date(game.platinum_date);
                platText.textContent = `Platted ${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}`;
                platEl.classList.remove('hidden');
            } else {
                platEl.classList.add('hidden');
            }
        }

        // Trophies
        const trophiesEl = document.getElementById('wizard-stat-trophies');
        const trophiesText = document.getElementById('wizard-stat-trophies-text');
        if (trophiesEl && trophiesText && stats) {
            trophiesText.textContent = `${stats.earned_trophies} / ${stats.total_trophies} trophies`;
            trophiesEl.classList.remove('hidden');
        } else if (trophiesEl) {
            trophiesEl.classList.add('hidden');
        }

        // Progress
        const progressEl = document.getElementById('wizard-stat-progress');
        const progressText = document.getElementById('wizard-stat-progress-text');
        if (progressEl && progressText && stats) {
            progressText.textContent = `${stats.progress}% complete`;
            progressEl.classList.remove('hidden');
        } else if (progressEl) {
            progressEl.classList.add('hidden');
        }

        // Playtime
        const playtimeEl = document.getElementById('wizard-stat-playtime');
        const playtimeText = document.getElementById('wizard-stat-playtime-text');
        if (playtimeEl && playtimeText && stats && stats.play_hours !== null) {
            playtimeText.textContent = `${stats.play_hours}h played`;
            playtimeEl.classList.remove('hidden');
        } else if (playtimeEl) {
            playtimeEl.classList.add('hidden');
        }
    },

    initRatingValidation() {
        const form = document.getElementById('wizard-rating-form');
        if (!form) return;

        const hoursInput = form.querySelector('[name="hours_to_platinum"]');
        if (hoursInput) {
            hoursInput.addEventListener('input', () => {
                this.updateHoursChecklist();
                this.updateSubmitButton();
            });
        }
    },

    updateHoursChecklist() {
        const el = document.getElementById('wizard-req-hours');
        if (!el) return;

        const form = document.getElementById('wizard-rating-form');
        const hours = parseInt(form?.querySelector('[name="hours_to_platinum"]')?.value) || 0;

        const x = el.querySelector('.wizard-req-icon-x');
        const check = el.querySelector('.wizard-req-icon-check');
        if (hours > 0) {
            if (x) x.classList.add('hidden');
            if (check) check.classList.remove('hidden');
        } else {
            if (x) x.classList.remove('hidden');
            if (check) check.classList.add('hidden');
        }
    },

    getRatingPayload() {
        const form = document.getElementById('wizard-rating-form');
        if (!form) return null;
        const fd = new FormData(form);
        return {
            difficulty: parseInt(fd.get('difficulty')),
            grindiness: parseInt(fd.get('grindiness')),
            hours_to_platinum: parseInt(fd.get('hours_to_platinum')) || 0,
            fun_ranking: parseInt(fd.get('fun_ranking')),
            overall_rating: parseFloat(fd.get('overall_rating')),
        };
    },

    // ------------------------------------------------------------------ //
    //  Review Form
    // ------------------------------------------------------------------ //

    resetReviewForm() {
        this.recommended = null;
        this.isEditingReview = false;
        const body = document.getElementById('wizard-review-body');
        if (body) body.value = '';
        this.updateWordProgress(0);
        this.updateRecommendButtons();
        this.updateChecklist();

        const charEl = document.getElementById('wizard-char-count');
        if (charEl) charEl.textContent = '0 / 8000';

        // Hide cancel edit button
        const cancelBtn = document.getElementById('wizard-cancel-edit-btn');
        if (cancelBtn) cancelBtn.classList.add('hidden');

        // Collapse markdown help
        const panel = document.getElementById('wizard-markdown-help-panel');
        const chevron = document.getElementById('wizard-markdown-chevron');
        if (panel) panel.classList.add('hidden');
        if (chevron) chevron.classList.remove('rotate-180');
    },

    populateExistingReview(game) {
        const review = game.existing_review;
        if (!review) return;

        const esc = PlatPursuit.HTMLUtils.escape;

        // Recommend badge
        const badge = document.getElementById('wizard-existing-recommend-badge');
        if (badge) {
            const thumbUp = '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><path d="M2 21h4V9H2v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L15.17 1 7.59 8.59C7.22 8.95 7 9.45 7 10v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/></svg>';
            const thumbDown = '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><path d="M15 3H6c-.83 0-1.54.5-1.84 1.22l-3.02 7.05c-.09.23-.14.47-.14.73v2c0 1.1.9 2 2 2h6.31l-.95 4.57-.03.32c0 .41.17.79.44 1.06L9.83 23l7.58-7.59c.36-.36.59-.86.59-1.41V5c0-1.1-.9-2-2-2zm4 0v12h4V3h-4z"/></svg>';
            if (review.recommended) {
                badge.className = 'badge badge-lg gap-1.5 font-semibold badge-success';
                badge.innerHTML = `${thumbUp} Recommended`;
            } else {
                badge.className = 'badge badge-lg gap-1.5 font-semibold badge-error';
                badge.innerHTML = `${thumbDown} Not Recommended`;
            }
        }

        // Review body text
        const bodyEl = document.getElementById('wizard-existing-review-body');
        if (bodyEl) bodyEl.textContent = review.body || '';

        // Hub link
        const hubLink = document.getElementById('wizard-review-hub-link');
        if (hubLink) hubLink.href = `/reviews/${esc(game.slug || '')}/`;
    },

    initEditReviewButton() {
        const editBtn = document.getElementById('wizard-edit-review-btn');
        if (editBtn) editBtn.addEventListener('click', () => this.editExistingReview());
    },

    editExistingReview() {
        const game = this.queue[this.currentIndex];
        if (!game?.existing_review) return;

        this.isEditingReview = true;
        const review = game.existing_review;

        // Switch to form view
        const reviewFormContent = document.getElementById('wizard-review-form-content');
        const existingReviewEl = document.getElementById('wizard-existing-review');
        if (reviewFormContent) {
            reviewFormContent.classList.remove('hidden');
            reviewFormContent.classList.add('flex');
        }
        if (existingReviewEl) {
            existingReviewEl.classList.add('hidden');
            existingReviewEl.classList.remove('flex');
        }

        // Pre-fill the form
        const body = document.getElementById('wizard-review-body');
        if (body) {
            body.value = review.body || '';
            body.dispatchEvent(new Event('input'));
        }
        this.recommended = review.recommended;
        this.updateRecommendButtons();
        this.updateChecklist();

        // Update heading
        const reviewHeading = document.querySelector('#wizard-review-section h3');
        if (reviewHeading) {
            const pencilSvg = reviewHeading.querySelector('svg').outerHTML;
            reviewHeading.innerHTML = `${pencilSvg} Edit Your Review`;
        }

        // Show cancel button
        const cancelBtn = document.getElementById('wizard-cancel-edit-btn');
        if (cancelBtn) cancelBtn.classList.remove('hidden');

        // Update submit buttons for edit mode
        this.updateSubmitButton();
    },

    cancelEditReview() {
        const game = this.queue[this.currentIndex];
        if (!game?.existing_review) return;

        this.isEditingReview = false;

        // Hide cancel button
        const cancelBtn = document.getElementById('wizard-cancel-edit-btn');
        if (cancelBtn) cancelBtn.classList.add('hidden');

        this.resetReviewForm();

        // Switch back to display view
        const reviewFormContent = document.getElementById('wizard-review-form-content');
        const existingReviewEl = document.getElementById('wizard-existing-review');
        if (reviewFormContent) {
            reviewFormContent.classList.add('hidden');
            reviewFormContent.classList.remove('flex');
        }
        if (existingReviewEl) {
            existingReviewEl.classList.remove('hidden');
            existingReviewEl.classList.add('flex');
        }

        // Restore heading
        const reviewHeading = document.querySelector('#wizard-review-section h3');
        if (reviewHeading) {
            const pencilSvg = reviewHeading.querySelector('svg').outerHTML;
            reviewHeading.innerHTML = `${pencilSvg} Your Review`;
        }

        this.updateSubmitButton();
    },

    initRecommendButtons() {
        document.querySelectorAll('.wizard-recommend-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this.recommended = btn.dataset.value === 'true';
                this.updateRecommendButtons();
                this.updateChecklist();
                this.updateSubmitButton();
            });
        });
    },

    updateRecommendButtons() {
        document.querySelectorAll('.wizard-recommend-btn').forEach(btn => {
            const isSelected = this.recommended !== null && String(this.recommended) === btn.dataset.value;
            btn.classList.toggle('btn-success', isSelected && btn.dataset.value === 'true');
            btn.classList.toggle('btn-error', isSelected && btn.dataset.value === 'false');
            btn.classList.toggle('btn-ghost', !isSelected);
            if (!isSelected) {
                btn.classList.add('border', 'border-base-300');
            } else {
                btn.classList.remove('border', 'border-base-300');
            }
        });
    },

    initReviewBody() {
        const body = document.getElementById('wizard-review-body');
        if (!body) return;
        body.addEventListener('input', () => {
            const text = body.value;
            const wordCount = text.trim() ? text.trim().split(/\s+/).length : 0;
            const charCount = text.length;
            this.updateWordProgress(wordCount);
            this.updateChecklist();
            const charEl = document.getElementById('wizard-char-count');
            if (charEl) charEl.textContent = `${charCount} / 8000`;
            this.updateSubmitButton();
        });
    },

    initMarkdownHelp() {
        const toggle = document.getElementById('wizard-markdown-help-toggle');
        const panel = document.getElementById('wizard-markdown-help-panel');
        const chevron = document.getElementById('wizard-markdown-chevron');
        if (!toggle || !panel) return;
        toggle.addEventListener('click', () => {
            panel.classList.toggle('hidden');
            if (chevron) chevron.classList.toggle('rotate-180', !panel.classList.contains('hidden'));
        });
    },

    // ------------------------------------------------------------------ //
    //  Trophy Panel (always visible on desktop, collapsible on tablet)
    // ------------------------------------------------------------------ //

    isDesktop() {
        return window.innerWidth >= 1024;
    },

    initTrophyToggle() {
        const toggle = document.getElementById('wizard-trophy-toggle');
        const content = document.getElementById('wizard-trophy-content');
        const chevron = document.getElementById('wizard-trophy-chevron');
        if (!toggle || !content) return;

        // On tablet: bind click to toggle collapse + lazy-load
        // On desktop: toggle is non-interactive (CSS handles visibility)
        toggle.addEventListener('click', () => {
            if (this.isDesktop()) return;

            const isHidden = content.classList.contains('hidden');
            content.classList.toggle('hidden');
            if (chevron) chevron.classList.toggle('rotate-180');

            // Lazy-load trophies on first expand
            if (isHidden && !content.dataset.loaded) {
                this.loadTrophies();
            }
        });
    },

    async loadTrophies() {
        const game = this.queue[this.currentIndex];
        if (!game) return;

        const content = document.getElementById('wizard-trophy-content');
        if (!content) return;

        content.innerHTML = '<div class="flex justify-center py-3"><span class="loading loading-dots loading-sm text-primary"></span></div>';

        try {
            const data = await PlatPursuit.API.get(
                `/api/v1/reviews/${game.concept_id}/group/${game.trophy_group_id}/trophies/`
            );

            content.innerHTML = PlatPursuit.TrophyListRenderer.buildList(data.trophies);
            content.dataset.loaded = 'true';

            const countBadge = document.getElementById('wizard-trophy-count');
            if (countBadge) countBadge.textContent = data.count;
        } catch (error) {
            const errData = await error.response?.json().catch(() => null);
            content.innerHTML = `<p class="text-sm text-error italic py-2 pr-1">${PlatPursuit.HTMLUtils.escape(errData?.error || 'Failed to load trophies.')}</p>`;
        }
    },

    updateTrophyPanel() {
        const content = document.getElementById('wizard-trophy-content');
        const chevron = document.getElementById('wizard-trophy-chevron');
        const countBadge = document.getElementById('wizard-trophy-count');

        if (countBadge) countBadge.textContent = '';

        if (content) {
            content.innerHTML = '';
            delete content.dataset.loaded;

            if (this.isDesktop()) {
                // Desktop: always visible, load immediately
                this.loadTrophies();
            } else {
                // Tablet: collapse and wait for user toggle
                content.classList.add('hidden');
                if (chevron) chevron.classList.remove('rotate-180');
            }
        }
    },

    updateWordProgress(wordCount) {
        PlatPursuit.ReviewProgressTiers.updateWordProgress({
            bar: document.getElementById('wizard-word-bar'),
            text: document.getElementById('wizard-word-text'),
            icon: document.getElementById('wizard-progress-icon'),
        }, wordCount);
    },

    updateChecklist() {
        const body = document.getElementById('wizard-review-body');
        const bodyText = body ? body.value.trim() : '';

        // Recommend requirement
        const recEl = document.getElementById('wizard-req-recommend');
        if (recEl) {
            const x = recEl.querySelector('.wizard-req-icon-x');
            const check = recEl.querySelector('.wizard-req-icon-check');
            if (this.recommended !== null) {
                if (x) x.classList.add('hidden');
                if (check) check.classList.remove('hidden');
            } else {
                if (x) x.classList.remove('hidden');
                if (check) check.classList.add('hidden');
            }
        }

        // Length requirement
        const lenEl = document.getElementById('wizard-req-length');
        if (lenEl) {
            const x = lenEl.querySelector('.wizard-req-icon-x');
            const check = lenEl.querySelector('.wizard-req-icon-check');
            if (bodyText.length >= 50) {
                if (x) x.classList.add('hidden');
                if (check) check.classList.remove('hidden');
            } else {
                if (x) x.classList.remove('hidden');
                if (check) check.classList.add('hidden');
            }
        }
    },

    updateSubmitButton() {
        const ratingBtn = document.getElementById('wizard-submit-btn');
        const reviewBtn = document.getElementById('wizard-submit-review-btn');
        if (!ratingBtn) return;

        const game = this.queue[this.currentIndex];
        if (!game) return;

        const body = document.getElementById('wizard-review-body');
        const bodyText = body ? body.value.trim() : '';
        const hasValidReview = this.recommended !== null && bodyText.length >= 50;
        // Can create new review, or is editing existing
        const canReview = !game.has_review || this.isEditingReview;

        const form = document.getElementById('wizard-rating-form');
        const hours = parseInt(form?.querySelector('[name="hours_to_platinum"]')?.value) || 0;
        const hoursValid = hours > 0;

        const checkSvg = '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>';
        const rateLabel = game.has_rating ? 'Update Rating' : 'Submit Rating';
        const reviewVerb = this.isEditingReview ? 'Update' : 'Submit';

        // Rating button: label changes when review is ready
        if (hasValidReview && canReview) {
            ratingBtn.innerHTML = `${checkSvg} ${rateLabel} & ${reviewVerb} Review`;
            ratingBtn.disabled = !hoursValid;
        } else if (bodyText.length > 0 && canReview) {
            // Review started but not valid yet: show combined label, disable
            ratingBtn.innerHTML = `${checkSvg} ${rateLabel} & ${reviewVerb} Review`;
            ratingBtn.disabled = true;
        } else {
            ratingBtn.innerHTML = `${checkSvg} ${rateLabel}`;
            ratingBtn.disabled = !hoursValid;
        }

        // Review-only button: visible when editing or when game has no review
        if (reviewBtn) {
            if (this.isEditingReview) {
                reviewBtn.classList.remove('hidden');
                reviewBtn.disabled = !hasValidReview;
                reviewBtn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg> Update Review`;
            } else {
                reviewBtn.classList.toggle('hidden', !canReview);
                reviewBtn.disabled = !hasValidReview;
                reviewBtn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg> Submit Review Only`;
            }
        }
    },

    // ------------------------------------------------------------------ //
    //  Action Buttons
    // ------------------------------------------------------------------ //

    initActionButtons() {
        const skipBtn = document.getElementById('wizard-skip-btn');
        const submitBtn = document.getElementById('wizard-submit-btn');
        const reviewBtn = document.getElementById('wizard-submit-review-btn');

        if (skipBtn) skipBtn.addEventListener('click', () => this.skip());
        if (submitBtn) submitBtn.addEventListener('click', () => this.submit());
        if (reviewBtn) reviewBtn.addEventListener('click', () => this.submitReviewOnly());
    },

    skip() {
        this.advance();
    },

    submit() {
        const game = this.queue[this.currentIndex];
        if (!game) return;

        const bodyEl = document.getElementById('wizard-review-body');
        const bodyText = bodyEl ? bodyEl.value.trim() : '';
        const hasValidReview = this.recommended !== null && bodyText.length >= 50;

        // Determine if this submission includes a new review (not edit: edits don't need guidelines)
        const willCreateReview = !game.has_review && hasValidReview && !this.isEditingReview;

        const doSubmit = async () => {
            const submitBtn = document.getElementById('wizard-submit-btn');
            if (submitBtn) submitBtn.disabled = true;

            let didRate = false;
            let didReview = false;

            try {
                // Always submit rating (create or update)
                const payload = this.getRatingPayload();
                if (payload) {
                    await PlatPursuit.API.post(
                        `/api/v1/reviews/${game.concept_id}/group/${game.trophy_group_id}/rate/`,
                        payload,
                    );
                    didRate = true;
                }

                // Handle review: create new or update existing
                if (this.isEditingReview && game.existing_review && hasValidReview) {
                    // Update existing review via PUT
                    await PlatPursuit.API.put(
                        `/api/v1/reviews/${game.existing_review.id}/`,
                        { body: bodyText, recommended: this.recommended },
                    );
                    game.existing_review.body = bodyText;
                    game.existing_review.recommended = this.recommended;
                    didReview = true;
                } else if (!game.has_review && this.recommended !== null && bodyText.length >= 50) {
                    // Create new review
                    await PlatPursuit.API.post(
                        `/api/v1/reviews/${game.concept_id}/group/${game.trophy_group_id}/create/`,
                        { body: bodyText, recommended: this.recommended },
                    );
                    didReview = true;
                }

                // Build appropriate toast message
                const rateWord = game.has_rating ? 'updated' : 'rated';
                const reviewWord = this.isEditingReview ? 'review updated' : 'reviewed';
                let msg = game.unified_title;
                if (didRate && didReview) msg += ` ${rateWord} & ${reviewWord}!`;
                else if (didReview) msg += ` ${reviewWord}!`;
                else if (didRate) msg += ` ${rateWord}!`;
                else msg += ' done!';

                PlatPursuit.ToastManager.success(msg);
                this.advance();

            } catch (error) {
                const errData = await error.response?.json().catch(() => null);
                PlatPursuit.ToastManager.error(errData?.error || 'Failed to submit. Please try again.');
                if (submitBtn) submitBtn.disabled = false;
            }
        };

        if (willCreateReview) {
            this.checkGuidelines(doSubmit);
        } else {
            doSubmit();
        }
    },

    submitReviewOnly() {
        const game = this.queue[this.currentIndex];
        if (!game) return;

        const bodyEl = document.getElementById('wizard-review-body');
        const bodyText = bodyEl ? bodyEl.value.trim() : '';

        if (this.recommended === null || bodyText.length < 50) {
            PlatPursuit.ToastManager.error('Please complete the review form before submitting.');
            return;
        }

        const doReviewSubmit = async () => {
            const reviewBtn = document.getElementById('wizard-submit-review-btn');
            if (reviewBtn) reviewBtn.disabled = true;

            try {
                if (this.isEditingReview && game.existing_review) {
                    // Update existing review via PUT
                    await PlatPursuit.API.put(
                        `/api/v1/reviews/${game.existing_review.id}/`,
                        { body: bodyText, recommended: this.recommended },
                    );
                    // Update local data and switch back to display
                    game.existing_review.body = bodyText;
                    game.existing_review.recommended = this.recommended;
                    this.isEditingReview = false;
                    this.populateExistingReview(game);

                    // Hide cancel button
                    const cancelBtn = document.getElementById('wizard-cancel-edit-btn');
                    if (cancelBtn) cancelBtn.classList.add('hidden');

                    // Switch to display view
                    const reviewFormContent = document.getElementById('wizard-review-form-content');
                    const existingReviewEl = document.getElementById('wizard-existing-review');
                    if (reviewFormContent) {
                        reviewFormContent.classList.add('hidden');
                        reviewFormContent.classList.remove('flex');
                    }
                    if (existingReviewEl) {
                        existingReviewEl.classList.remove('hidden');
                        existingReviewEl.classList.add('flex');
                    }
                    const reviewHeading = document.querySelector('#wizard-review-section h3');
                    if (reviewHeading) {
                        const pencilSvg = reviewHeading.querySelector('svg').outerHTML;
                        reviewHeading.innerHTML = `${pencilSvg} Your Review`;
                    }
                    this.updateSubmitButton();
                    PlatPursuit.ToastManager.success(`${game.unified_title} review updated!`);
                } else {
                    // Create new review
                    await PlatPursuit.API.post(
                        `/api/v1/reviews/${game.concept_id}/group/${game.trophy_group_id}/create/`,
                        { body: bodyText, recommended: this.recommended },
                    );
                    PlatPursuit.ToastManager.success(`${game.unified_title} reviewed!`);
                    this.advance();
                }
            } catch (error) {
                const errData = await error.response?.json().catch(() => null);
                PlatPursuit.ToastManager.error(errData?.error || 'Failed to submit review. Please try again.');
                if (reviewBtn) reviewBtn.disabled = false;
            }
        };

        // Only gate new reviews with guidelines check, not edits
        if (!this.isEditingReview) {
            this.checkGuidelines(doReviewSubmit);
        } else {
            doReviewSubmit();
        }
    },

    // ------------------------------------------------------------------ //
    //  Progress Tracking
    // ------------------------------------------------------------------ //

    updateProgress() {
        const current = this.completedCount + 1;
        const total = this.totalCount;

        const textEl = document.getElementById('wizard-progress-text');
        const pctEl = document.getElementById('wizard-progress-pct');
        const bar = document.getElementById('wizard-progress-bar');
        const currentNum = document.getElementById('wizard-current-num');
        const totalNum = document.getElementById('wizard-total-num');

        const label = this.queueType === 'dlc' ? 'DLC' : 'Game';
        if (textEl) textEl.textContent = `${label} ${current} of ${total}`;
        if (currentNum) currentNum.textContent = current;
        if (totalNum) totalNum.textContent = total;

        const pct = total > 0 ? Math.round((this.completedCount / total) * 100) : 0;
        if (pctEl) pctEl.textContent = `${pct}%`;
        if (bar) {
            bar.value = pct;
            bar.max = 100;
        }
    },
};
