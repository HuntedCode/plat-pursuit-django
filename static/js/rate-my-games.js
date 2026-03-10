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

    init(config) {
        this.config = config;
        this.initFilterButtons();
        this.initQueueTabs();
        this.initRecommendButtons();
        this.initReviewBody();
        this.initMarkdownHelp();
        this.initRatingValidation();
        this.initActionButtons();
        this.initTrophyToggle();
        this.loadQueue();
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

        // Update review section heading
        const reviewHeading = document.querySelector('#wizard-review-section h3');
        if (reviewHeading) {
            const pencilSvg = reviewHeading.querySelector('svg').outerHTML;
            const reviewTarget = game.is_dlc ? 'This DLC' : 'This Game';
            reviewHeading.innerHTML = `${pencilSvg} Review ${reviewTarget}`;
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
        const body = document.getElementById('wizard-review-body');
        if (body) body.value = '';
        this.updateWordProgress(0);
        this.updateRecommendButtons();
        this.updateChecklist();

        const charEl = document.getElementById('wizard-char-count');
        if (charEl) charEl.textContent = '0 / 8000';

        // Collapse markdown help
        const panel = document.getElementById('wizard-markdown-help-panel');
        const chevron = document.getElementById('wizard-markdown-chevron');
        if (panel) panel.classList.add('hidden');
        if (chevron) chevron.classList.remove('rotate-180');
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
        const canReview = !game.has_review;

        const form = document.getElementById('wizard-rating-form');
        const hours = parseInt(form?.querySelector('[name="hours_to_platinum"]')?.value) || 0;
        const hoursValid = hours > 0;

        const checkSvg = '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>';
        const rateLabel = game.has_rating ? 'Update Rating' : 'Submit Rating';

        // Rating button: label changes when review is ready
        if (hasValidReview && canReview) {
            ratingBtn.innerHTML = `${checkSvg} ${rateLabel} & Review`;
            ratingBtn.disabled = !hoursValid;
        } else if (bodyText.length > 0 && canReview) {
            // Review started but not valid yet: show combined label, disable
            ratingBtn.innerHTML = `${checkSvg} ${rateLabel} & Review`;
            ratingBtn.disabled = true;
        } else {
            ratingBtn.innerHTML = `${checkSvg} ${rateLabel}`;
            ratingBtn.disabled = !hoursValid;
        }

        // Review-only button: visible when game has no review
        if (reviewBtn) {
            reviewBtn.classList.toggle('hidden', !canReview);
            reviewBtn.disabled = !hasValidReview;
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

    async submit() {
        const game = this.queue[this.currentIndex];
        if (!game) return;

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

            // Submit review if form has valid content and not already reviewed
            if (!game.has_review && this.recommended !== null) {
                const body = document.getElementById('wizard-review-body');
                const bodyText = body ? body.value.trim() : '';
                if (bodyText.length >= 50) {
                    await PlatPursuit.API.post(
                        `/api/v1/reviews/${game.concept_id}/group/${game.trophy_group_id}/create/`,
                        { body: bodyText, recommended: this.recommended },
                    );
                    didReview = true;
                }
            }

            // Build appropriate toast message
            const rateWord = game.has_rating ? 'updated' : 'rated';
            let msg = game.unified_title;
            if (didRate && didReview) msg += ` ${rateWord} & reviewed!`;
            else if (didReview) msg += ' reviewed!';
            else if (didRate) msg += ` ${rateWord}!`;
            else msg += ' done!';

            PlatPursuit.ToastManager.success(msg);
            this.advance();

        } catch (error) {
            const errData = await error.response?.json().catch(() => null);
            PlatPursuit.ToastManager.error(errData?.error || 'Failed to submit. Please try again.');
            if (submitBtn) submitBtn.disabled = false;
        }
    },

    async submitReviewOnly() {
        const game = this.queue[this.currentIndex];
        if (!game || game.has_review) return;

        const reviewBtn = document.getElementById('wizard-submit-review-btn');
        if (reviewBtn) reviewBtn.disabled = true;

        const body = document.getElementById('wizard-review-body');
        const bodyText = body ? body.value.trim() : '';

        if (this.recommended === null || bodyText.length < 50) {
            PlatPursuit.ToastManager.error('Please complete the review form before submitting.');
            if (reviewBtn) reviewBtn.disabled = false;
            return;
        }

        try {
            await PlatPursuit.API.post(
                `/api/v1/reviews/${game.concept_id}/group/${game.trophy_group_id}/create/`,
                { body: bodyText, recommended: this.recommended },
            );

            PlatPursuit.ToastManager.success(`${game.unified_title} reviewed!`);
            this.advance();
        } catch (error) {
            const errData = await error.response?.json().catch(() => null);
            PlatPursuit.ToastManager.error(errData?.error || 'Failed to submit review. Please try again.');
            if (reviewBtn) reviewBtn.disabled = false;
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
