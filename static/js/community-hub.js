/**
 * Community Hub: Review feed, voting, replies, ratings.
 *
 * Client-side rendering of review cards from JSON API responses.
 * Uses IntersectionObserver for infinite scroll.
 */
window.PlatPursuit = window.PlatPursuit || {};

PlatPursuit.CommunityHub = {
    config: null,
    observer: null,
    offset: 0,
    limit: 10,
    sort: 'helpful',
    isLoading: false,
    hasMore: true,
    loadedReplyIds: new Set(),

    init(config) {
        this.config = config;
        this.offset = 0;
        this.hasMore = true;
        this.sort = 'helpful';
        this.loadedReplyIds = new Set();

        this.initReviewFeed();
        this.initSortButtons();
        this.initReviewForm();
        this.initRatingForm();
        this.initYourReview();
        this.initRatingsToggle();
    },

    // ------------------------------------------------------------------ //
    //  Review Feed (Infinite Scroll)
    // ------------------------------------------------------------------ //

    initReviewFeed() {
        const sentinel = document.getElementById('review-sentinel');
        if (!sentinel) return;

        this.observer = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && !this.isLoading && this.hasMore) {
                this.loadReviews();
            }
        }, { rootMargin: '200px' });

        this.observer.observe(sentinel);
        this.loadReviews();
    },

    async loadReviews() {
        if (this.isLoading || !this.hasMore) return;
        this.isLoading = true;

        const loading = document.getElementById('review-loading');
        const emptyState = document.getElementById('review-feed-empty');
        if (loading) loading.classList.remove('hidden');

        try {
            const url = `/api/v1/reviews/${this.config.conceptId}/group/${this.config.activeGroup}/?sort=${this.sort}&limit=${this.limit}&offset=${this.offset}`;
            const data = await PlatPursuit.API.get(url);

            const feed = document.getElementById('review-feed');
            if (!feed) return;

            const reviews = data.reviews || [];

            reviews.forEach(review => {
                feed.insertAdjacentHTML('beforeend', this.buildReviewCard(review));
            });

            const wasFirstLoad = this.offset === 0;
            this.offset += reviews.length;
            this.hasMore = data.has_more || false;

            // Show empty state only on first load with no results
            if (wasFirstLoad && reviews.length === 0 && emptyState) {
                emptyState.classList.remove('hidden');
            } else if (emptyState) {
                emptyState.classList.add('hidden');
            }
        } catch (error) {
            const errData = await error.response?.json().catch(() => null);
            PlatPursuit.ToastManager.error(errData?.error || 'Failed to load reviews.');
        } finally {
            this.isLoading = false;
            if (loading) loading.classList.add('hidden');
        }
    },

    buildReviewCard(review) {
        const esc = PlatPursuit.HTMLUtils.escape;
        const author = review.author || {};
        const stats = review.reviewer_stats || {};
        const recommended = review.recommended;
        const timeAgo = PlatPursuit.TimeFormatter.relative(review.created_at);

        const thumbIcon = recommended
            ? '<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor"><path d="M2 21h4V9H2v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L15.17 1 7.59 8.59C7.22 8.95 7 9.45 7 10v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/></svg>'
            : '<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor"><path d="M15 3H6c-.83 0-1.54.5-1.84 1.22l-3.02 7.05c-.09.23-.14.47-.14.73v2c0 1.1.9 2 2 2h6.31l-.95 4.57-.03.32c0 .41.17.79.44 1.06L9.83 23l7.58-7.59c.36-.36.59-.86.59-1.41V5c0-1.1-.9-2-2-2zm4 0v12h4V3h-4z"/></svg>';

        const thumbColor = recommended ? 'text-success' : 'text-error';
        const thumbText = recommended ? 'Recommended' : 'Not Recommended';

        const hoursDisplay = stats.hours_played != null ? `${stats.hours_played}h` : '';
        const statsLine = [
            `${stats.completion_pct || 0}%`,
            hoursDisplay,
        ].filter(Boolean).join(' | ');
        const platImg = `<img src="/static/images/plat.png" alt="Platinum" class="w-4 h-4 inline-block${stats.has_plat ? '' : ' grayscale opacity-40'}" loading="lazy" />`;

        const helpfulActive = review.user_voted_helpful ? 'btn-active btn-primary' : '';
        const funnyActive = review.user_voted_funny ? 'btn-active btn-secondary' : '';

        const editedTag = review.is_edited ? ' (edited)' : '';
        const ownActions = review.is_own ? `
            <button class="btn btn-xs btn-ghost" onclick="PlatPursuit.CommunityHub.startInlineEdit(${review.id})" aria-label="Edit">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
            </button>
            <button class="btn btn-xs btn-ghost text-error" onclick="PlatPursuit.CommunityHub.deleteReview(${review.id})" aria-label="Delete">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
            </button>` : '';

        const reportBtn = (!review.is_own && this.config.isAuthenticated) ? `
            <button class="btn btn-xs btn-ghost text-base-content/40 hover:text-warning" onclick="PlatPursuit.CommunityHub.reportReview(${review.id})" aria-label="Report">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 21v-16l5 3 4-4 4 4 5-3v16l-5-3-4 4-4-4-5 3z"/></svg>
            </button>` : '';

        return `
        <div class="review-card border-2 border-base-300 rounded-box p-4" data-review-id="${review.id}">
            <div class="flex flex-col md:flex-row lg:flex-row gap-4">
                <div class="flex flex-row md:flex-col lg:flex-col items-center gap-3 md:w-32 lg:w-36 shrink-0">
                    <div class="avatar">
                        <div class="w-10 h-10 md:w-11 md:h-11 lg:w-12 lg:h-12 rounded-full ring-2 ring-primary">
                            <img src="${esc(author.avatar_url || '')}" alt="${esc(author.display_psn_username || '')}" class="rounded-full object-cover" loading="lazy" />
                        </div>
                    </div>
                    <div class="text-center md:text-left lg:text-left">
                        <a href="/profiles/${esc(author.psn_username || '')}/" class="font-bold text-sm ${author.is_premium ? 'legendary-title' : 'text-primary'} hover:underline line-clamp-1 pr-1">${esc(author.display_psn_username || author.psn_username || '')}</a>
                        ${author.displayed_title ? (author.title_source
                            ? `<div class="tooltip tooltip-bottom cursor-help" data-tip="${esc(author.title_source)}"><p class="text-xs ${author.is_premium ? 'legendary-title' : 'text-secondary'} italic pr-1 line-clamp-1">${esc(author.displayed_title)}</p></div>`
                            : `<p class="text-xs ${author.is_premium ? 'legendary-title' : 'text-secondary'} italic pr-1 line-clamp-1">${esc(author.displayed_title)}</p>`)
                        : ''}
                        <p class="text-xs text-base-content/50 flex items-center gap-1 mt-1">${platImg} ${esc(statsLine)}</p>
                    </div>
                </div>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 mb-2">
                        <span class="${thumbColor}">${thumbIcon}</span>
                        <span class="font-bold text-sm ${thumbColor}">${thumbText}</span>
                        ${ownActions}
                        ${reportBtn}
                    </div>
                    <div class="review-body-display prose prose-sm max-w-none">${review.body_html || ''}</div>
                    <div class="flex flex-wrap items-center gap-3 mt-4 text-sm">
                        <button class="btn btn-sm btn-ghost gap-1.5 vote-btn ${helpfulActive}" onclick="PlatPursuit.CommunityHub.toggleVote(${review.id}, 'helpful')" data-review-id="${review.id}" data-vote="helpful">
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor"><path d="M2 21h4V9H2v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L15.17 1 7.59 8.59C7.22 8.95 7 9.45 7 10v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/></svg>
                            Helpful <span class="helpful-count">${review.helpful_count}</span>
                        </button>
                        <button class="btn btn-sm btn-ghost gap-1.5 vote-btn ${funnyActive}" onclick="PlatPursuit.CommunityHub.toggleVote(${review.id}, 'funny')" data-review-id="${review.id}" data-vote="funny">
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M15.5 11c.5 0 .5-2 0-2s-.5 2 0 2zM8.5 11c.5 0 .5-2 0-2s-.5 2 0 2z"/><path d="M7 13c0 3 2.5 4.5 5 4.5s5-1.5 5-4.5H7z"/></svg>
                            Funny <span class="funny-count">${review.funny_count}</span>
                        </button>
                        <button class="btn btn-sm btn-ghost gap-1.5" onclick="PlatPursuit.CommunityHub.toggleReplies(${review.id})" aria-label="Toggle replies">
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>
                            Replies <span class="reply-count">${review.reply_count}</span>
                        </button>
                        <span class="text-xs text-base-content/40 ml-auto">${esc(timeAgo)}${editedTag}</span>
                    </div>
                    <div class="replies-container hidden mt-4 pl-4 border-l-2 border-base-300" data-review-id="${review.id}"></div>
                </div>
            </div>
        </div>`;
    },

    // ------------------------------------------------------------------ //
    //  Sort
    // ------------------------------------------------------------------ //

    initSortButtons() {
        document.querySelectorAll('.sort-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const newSort = btn.dataset.sort;
                if (newSort === this.sort) return;

                document.querySelectorAll('.sort-btn').forEach(b => {
                    b.classList.remove('btn-primary');
                    b.classList.add('btn-ghost', 'border', 'border-base-300');
                });
                btn.classList.add('btn-primary');
                btn.classList.remove('btn-ghost', 'border', 'border-base-300');

                this.sort = newSort;
                this.offset = 0;
                this.hasMore = true;
                this.loadedReplyIds = new Set();

                const feed = document.getElementById('review-feed');
                if (feed) feed.innerHTML = '';

                this.loadReviews();
            });
        });
    },

    // ------------------------------------------------------------------ //
    //  Voting
    // ------------------------------------------------------------------ //

    async toggleVote(reviewId, voteType) {
        if (!this.config.isAuthenticated) {
            PlatPursuit.ToastManager.error('Log in to vote on reviews.');
            return;
        }

        try {
            const data = await PlatPursuit.API.post(`/api/v1/reviews/${reviewId}/vote/`, { vote_type: voteType });

            // Update all vote buttons for this review
            const card = document.querySelector(`[data-review-id="${reviewId}"]`);
            if (!card) return;

            const helpfulBtn = card.querySelector('.vote-btn[data-vote="helpful"]');
            const funnyBtn = card.querySelector('.vote-btn[data-vote="funny"]');

            if (helpfulBtn) {
                helpfulBtn.querySelector('.helpful-count').textContent = data.helpful_count;
                helpfulBtn.classList.toggle('btn-active', data.voted && voteType === 'helpful');
                helpfulBtn.classList.toggle('btn-primary', data.voted && voteType === 'helpful');
            }
            if (funnyBtn) {
                funnyBtn.querySelector('.funny-count').textContent = data.funny_count;
                funnyBtn.classList.toggle('btn-active', data.voted && voteType === 'funny');
                funnyBtn.classList.toggle('btn-secondary', data.voted && voteType === 'funny');
            }
        } catch (error) {
            const errData = await error.response?.json().catch(() => null);
            PlatPursuit.ToastManager.error(errData?.error || 'Failed to vote.');
        }
    },

    // ------------------------------------------------------------------ //
    //  Replies
    // ------------------------------------------------------------------ //

    async toggleReplies(reviewId) {
        const container = document.querySelector(`.replies-container[data-review-id="${reviewId}"]`);
        if (!container) return;

        const isHidden = container.classList.contains('hidden');
        container.classList.toggle('hidden');

        // Load replies on first expand
        if (isHidden && !this.loadedReplyIds.has(reviewId)) {
            await this.loadReplies(reviewId);
            this.loadedReplyIds.add(reviewId);
        }
    },

    async loadReplies(reviewId) {
        const container = document.querySelector(`.replies-container[data-review-id="${reviewId}"]`);
        if (!container) return;

        container.innerHTML = '<div class="flex justify-center py-2"><span class="loading loading-dots loading-sm text-primary"></span></div>';

        try {
            const data = await PlatPursuit.API.get(`/api/v1/reviews/${reviewId}/replies/`);
            const replies = data.replies || [];

            let html = '';
            replies.forEach(reply => {
                html += this.buildReplyElement(reply);
            });

            // Add reply form if authenticated
            if (this.config.isAuthenticated) {
                html += `
                <div class="mt-3 flex gap-2">
                    <input type="text" class="input input-bordered input-sm flex-1 reply-input" placeholder="Write a reply..." maxlength="2000" data-review-id="${reviewId}" />
                    <button class="btn btn-sm btn-primary" onclick="PlatPursuit.CommunityHub.submitReply(${reviewId})">Reply</button>
                </div>`;
            }

            container.innerHTML = html || '<p class="text-sm text-base-content/50 italic py-2">No replies yet.</p>';

            // Add enter key handler
            const input = container.querySelector('.reply-input');
            if (input) {
                input.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        this.submitReply(reviewId);
                    }
                });
            }
        } catch (error) {
            container.innerHTML = '<p class="text-sm text-error italic">Failed to load replies.</p>';
        }
    },

    buildReplyElement(reply) {
        const esc = PlatPursuit.HTMLUtils.escape;
        const author = reply.author || {};
        const timeAgo = PlatPursuit.TimeFormatter.relative(reply.created_at);
        const editedTag = reply.is_edited ? ' (edited)' : '';

        const ownActions = reply.is_own ? `
            <button class="btn btn-xs btn-ghost" onclick="PlatPursuit.CommunityHub.editReply(${reply.id})" aria-label="Edit reply">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
            </button>
            <button class="btn btn-xs btn-ghost text-error" onclick="PlatPursuit.CommunityHub.deleteReply(${reply.id})" aria-label="Delete reply">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
            </button>` : '';

        return `
        <div class="reply-item flex gap-2 py-2" data-reply-id="${reply.id}">
            <div class="avatar shrink-0">
                <div class="w-7 h-7 rounded-full ring-2 ring-primary">
                    <img src="${esc(author.avatar_url || '')}" alt="" class="rounded-full object-cover" loading="lazy" />
                </div>
            </div>
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                    <a href="/profiles/${esc(author.psn_username || '')}/" class="text-sm font-bold ${author.is_premium ? 'legendary-title' : 'text-primary'} hover:underline">${esc(author.display_psn_username || author.psn_username || '')}</a>
                    <span class="text-xs text-base-content/40">${esc(timeAgo)}${editedTag}</span>
                    ${ownActions}
                </div>
                <p class="reply-body text-sm text-base-content mt-0.5">${esc(reply.body || '')}</p>
            </div>
        </div>`;
    },

    async submitReply(reviewId) {
        const input = document.querySelector(`.reply-input[data-review-id="${reviewId}"]`);
        if (!input || !input.value.trim()) return;

        try {
            const data = await PlatPursuit.API.post(`/api/v1/reviews/${reviewId}/replies/`, {
                body: input.value.trim(),
            });

            // Insert new reply before the reply form
            const container = document.querySelector(`.replies-container[data-review-id="${reviewId}"]`);
            const formDiv = container?.querySelector('.flex.mt-3');
            if (container && formDiv) {
                formDiv.insertAdjacentHTML('beforebegin', this.buildReplyElement(data.reply || data));
            }

            input.value = '';

            // Update reply count in the card
            const card = document.querySelector(`[data-review-id="${reviewId}"]`);
            const countEl = card?.querySelector('.reply-count');
            if (countEl) {
                countEl.textContent = parseInt(countEl.textContent) + 1;
            }

            PlatPursuit.ToastManager.success('Reply posted!');
        } catch (error) {
            const errData = await error.response?.json().catch(() => null);
            PlatPursuit.ToastManager.error(errData?.error || 'Failed to post reply.');
        }
    },

    async editReply(replyId) {
        const replyEl = document.querySelector(`[data-reply-id="${replyId}"]`);
        if (!replyEl) return;

        const bodyEl = replyEl.querySelector('.reply-body');
        const currentText = bodyEl.textContent;

        bodyEl.innerHTML = `
            <div class="flex gap-2 mt-1">
                <input type="text" class="input input-bordered input-xs flex-1 edit-reply-input" value="${PlatPursuit.HTMLUtils.escape(currentText)}" maxlength="2000" />
                <button class="btn btn-xs btn-primary save-reply-btn">Save</button>
                <button class="btn btn-xs btn-ghost cancel-reply-btn">Cancel</button>
            </div>`;

        const saveBtn = bodyEl.querySelector('.save-reply-btn');
        const cancelBtn = bodyEl.querySelector('.cancel-reply-btn');
        const editInput = bodyEl.querySelector('.edit-reply-input');

        cancelBtn.addEventListener('click', () => {
            bodyEl.textContent = currentText;
        });

        const saveFn = async () => {
            const newText = editInput.value.trim();
            if (!newText) return;

            try {
                await PlatPursuit.API.put(`/api/v1/reviews/replies/${replyId}/`, { body: newText });
                bodyEl.textContent = newText;
                PlatPursuit.ToastManager.success('Reply updated.');
            } catch (error) {
                const errData = await error.response?.json().catch(() => null);
                PlatPursuit.ToastManager.error(errData?.error || 'Failed to update reply.');
            }
        };

        saveBtn.addEventListener('click', saveFn);
        editInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); saveFn(); }
            if (e.key === 'Escape') { bodyEl.textContent = currentText; }
        });
        editInput.focus();
    },

    async deleteReply(replyId) {
        if (!confirm('Delete this reply?')) return;

        try {
            await PlatPursuit.API.delete(`/api/v1/reviews/replies/${replyId}/`);

            const replyEl = document.querySelector(`[data-reply-id="${replyId}"]`);
            if (replyEl) {
                // Find the parent review card to decrement reply count
                const container = replyEl.closest('.replies-container');
                const reviewId = container?.dataset.reviewId;
                replyEl.remove();

                if (reviewId) {
                    const card = document.querySelector(`[data-review-id="${reviewId}"]`);
                    const countEl = card?.querySelector('.reply-count');
                    if (countEl) {
                        countEl.textContent = Math.max(0, parseInt(countEl.textContent) - 1);
                    }
                }
            }

            PlatPursuit.ToastManager.success('Reply deleted.');
        } catch (error) {
            const errData = await error.response?.json().catch(() => null);
            PlatPursuit.ToastManager.error(errData?.error || 'Failed to delete reply.');
        }
    },

    // ------------------------------------------------------------------ //
    //  Review Form (Create)
    // ------------------------------------------------------------------ //

    initReviewForm() {
        const form = document.getElementById('review-create-form');
        if (!form) return;

        // Thumbs toggle (neither selected by default)
        let recommended = null;
        const submitBtn = document.getElementById('review-submit-btn');

        const reqRecommend = document.getElementById('req-recommend');
        const reqLength = document.getElementById('req-length');

        const updateSubmitState = () => {
            const charCount = textarea ? textarea.value.trim().length : 0;
            const hasRecommend = recommended !== null;
            const hasLength = charCount >= 50;

            [reqRecommend, reqLength].forEach((el, i) => {
                if (!el) return;
                const met = i === 0 ? hasRecommend : hasLength;
                el.querySelector('.req-icon-x').classList.toggle('hidden', met);
                el.querySelector('.req-icon-check').classList.toggle('hidden', !met);
            });
            if (submitBtn) submitBtn.disabled = !hasRecommend || !hasLength;
        };

        form.querySelectorAll('.recommend-toggle').forEach(btn => {
            btn.addEventListener('click', () => {
                recommended = btn.dataset.value === 'true';
                form.querySelectorAll('.recommend-toggle').forEach(b => {
                    const isActive = b.dataset.value === String(recommended);
                    b.classList.toggle('btn-success', isActive && recommended);
                    b.classList.toggle('btn-error', isActive && !recommended);
                    b.classList.toggle('active', isActive);
                    b.classList.toggle('btn-ghost', !isActive);
                    b.classList.toggle('border', !isActive);
                    b.classList.toggle('border-base-300', !isActive);
                    b.setAttribute('aria-pressed', String(isActive));
                });
                updateSubmitState();
            });
        });

        // Markdown help toggle
        const helpToggle = document.getElementById('markdown-help-toggle');
        const helpPanel = document.getElementById('markdown-help-panel');
        const helpChevron = document.getElementById('markdown-help-chevron');
        if (helpToggle && helpPanel) {
            helpToggle.addEventListener('click', () => {
                helpPanel.classList.toggle('hidden');
                if (helpChevron) helpChevron.classList.toggle('rotate-180');
            });
        }

        // Character counter + word progress bar
        const textarea = document.getElementById('review-body');
        const counter = document.getElementById('review-char-count');
        const progressBar = document.getElementById('review-progress-bar');
        const progressText = document.getElementById('review-progress-text');
        const progressIcon = document.getElementById('review-progress-icon');

        // Trophy icon tiers using project CSS vars (fewer breakpoints than text)
        const iconTiers = [
            { words: 0,   cssColor: 'var(--color-base-content)', opacity: '0.25' },
            { words: 25,  cssColor: 'var(--color-trophy-bronze)', opacity: '1' },
            { words: 75,  cssColor: 'var(--color-trophy-silver)', opacity: '1' },
            { words: 100, cssColor: 'var(--color-trophy-gold)',   opacity: '1' },
        ];

        // Text tiers with countdown messages toward the next threshold
        const progressTiers = [
            { words: 0,   pct: 0,   color: 'bg-error',   nextWords: 10,  nextLabel: 'getting started' },
            { words: 10,  pct: 15,  color: 'bg-error',   nextWords: 25,  nextLabel: 'a solid start' },
            { words: 25,  pct: 35,  color: 'bg-warning',  nextWords: 50,  nextLabel: 'a good review' },
            { words: 50,  pct: 55,  color: 'bg-warning',  nextWords: 75,  nextLabel: 'a great review' },
            { words: 75,  pct: 75,  color: 'bg-info',     nextWords: 100, nextLabel: 'an excellent review' },
            { words: 100, pct: 90,  color: 'bg-success',  nextWords: 150, nextLabel: 'an outstanding review' },
            { words: 150, pct: 100, color: 'bg-success',  nextWords: null, nextLabel: null },
        ];

        let currentIconTier = -1;

        if (textarea) {
            textarea.addEventListener('input', () => {
                // Character count + submit gate
                if (counter) counter.textContent = `${textarea.value.length} / 8000`;
                updateSubmitState();

                // Word count progress
                if (!progressBar || !progressText) return;
                const wordCount = textarea.value.trim() ? textarea.value.trim().split(/\s+/).length : 0;

                let tier = progressTiers[0];
                for (let i = progressTiers.length - 1; i >= 0; i--) {
                    if (wordCount >= progressTiers[i].words) {
                        tier = progressTiers[i];
                        break;
                    }
                }

                // Smooth width between tiers
                let barWidth = tier.pct;
                const tierIdx = progressTiers.indexOf(tier);
                if (tierIdx < progressTiers.length - 1) {
                    const next = progressTiers[tierIdx + 1];
                    const progress = (wordCount - tier.words) / (next.words - tier.words);
                    barWidth = tier.pct + (next.pct - tier.pct) * Math.min(progress, 1);
                }

                progressBar.style.width = `${barWidth}%`;
                progressBar.className = `h-full rounded-full transition-all duration-300 ease-out ${tier.color}`;

                // Countdown text toward next threshold
                if (tier.nextWords) {
                    const remaining = tier.nextWords - wordCount;
                    progressText.textContent = `${remaining} word${remaining === 1 ? '' : 's'} until ${tier.nextLabel}!`;
                } else {
                    progressText.textContent = 'Outstanding review! The community thanks you.';
                }

                // Trophy icon color (uses CSS vars, updates at fewer breakpoints)
                if (progressIcon) {
                    let newIconTier = 0;
                    for (let i = iconTiers.length - 1; i >= 0; i--) {
                        if (wordCount >= iconTiers[i].words) {
                            newIconTier = i;
                            break;
                        }
                    }
                    if (newIconTier !== currentIconTier) {
                        currentIconTier = newIconTier;
                        const it = iconTiers[newIconTier];
                        progressIcon.style.color = it.cssColor;
                        progressIcon.style.opacity = it.opacity;
                    }
                }
            });
        }

        // Submit
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const body = textarea?.value?.trim();
            if (!body || body.length < 50) {
                PlatPursuit.ToastManager.error('Review must be at least 50 characters.');
                return;
            }
            if (recommended === null) {
                PlatPursuit.ToastManager.error('Please select whether you recommend this game.');
                return;
            }

            if (submitBtn) submitBtn.disabled = true;

            try {
                await PlatPursuit.API.post(
                    `/api/v1/reviews/${this.config.conceptId}/group/${this.config.activeGroup}/create/`,
                    { body, recommended }
                );
                PlatPursuit.ToastManager.success('Review submitted! Refreshing...');
                setTimeout(() => window.location.reload(), 1000);
            } catch (error) {
                const errData = await error.response?.json().catch(() => null);
                PlatPursuit.ToastManager.error(errData?.error || 'Failed to submit review.');
                updateSubmitState();
            }
        });
    },

    // ------------------------------------------------------------------ //
    //  Your Review (Edit / Delete)
    // ------------------------------------------------------------------ //

    initYourReview() {
        const card = document.getElementById('your-review-card');
        if (!card) return;

        const editBtn = document.getElementById('your-review-edit-btn');
        const deleteBtn = document.getElementById('your-review-delete-btn');
        const cancelBtn = document.getElementById('your-review-cancel-btn');
        const saveBtn = document.getElementById('your-review-save-btn');

        if (editBtn) editBtn.addEventListener('click', () => this.enterEditMode());
        if (deleteBtn) deleteBtn.addEventListener('click', () => this.deleteOwnReview());
        if (cancelBtn) cancelBtn.addEventListener('click', () => this.exitEditMode());
        if (saveBtn) saveBtn.addEventListener('click', () => this.saveOwnReviewEdit());

        // Edit recommend toggles
        document.querySelectorAll('.edit-recommend-toggle').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.edit-recommend-toggle').forEach(b => {
                    const isThis = b === btn;
                    b.classList.toggle('btn-success', isThis && b.dataset.value === 'true');
                    b.classList.toggle('btn-error', isThis && b.dataset.value === 'false');
                    b.classList.toggle('active', isThis);
                    b.classList.toggle('btn-ghost', !isThis);
                    b.classList.toggle('border', !isThis);
                    b.classList.toggle('border-base-300', !isThis);
                });
            });
        });

        // Edit char counter
        const editBody = document.getElementById('your-review-edit-body');
        const editCounter = document.getElementById('edit-char-count');
        if (editBody && editCounter) {
            editBody.addEventListener('input', () => {
                editCounter.textContent = `${editBody.value.length} / 8000`;
            });
        }
    },

    enterEditMode() {
        document.getElementById('your-review-display')?.classList.add('hidden');
        document.getElementById('your-review-edit')?.classList.remove('hidden');
    },

    exitEditMode() {
        document.getElementById('your-review-display')?.classList.remove('hidden');
        document.getElementById('your-review-edit')?.classList.add('hidden');
    },

    async saveOwnReviewEdit() {
        const reviewId = this.config.userReviewId;
        if (!reviewId) return;

        const body = document.getElementById('your-review-edit-body')?.value?.trim();
        if (!body || body.length < 50) {
            PlatPursuit.ToastManager.error('Review must be at least 50 characters.');
            return;
        }

        const activeToggle = document.querySelector('.edit-recommend-toggle.active');
        const recommended = activeToggle?.dataset.value === 'true';

        try {
            await PlatPursuit.API.put(`/api/v1/reviews/${reviewId}/`, { body, recommended });
            PlatPursuit.ToastManager.success('Review updated! Refreshing...');
            setTimeout(() => window.location.reload(), 1000);
        } catch (error) {
            const errData = await error.response?.json().catch(() => null);
            PlatPursuit.ToastManager.error(errData?.error || 'Failed to update review.');
        }
    },

    async deleteOwnReview() {
        if (!confirm('Delete your review? This cannot be undone.')) return;

        const reviewId = this.config.userReviewId;
        if (!reviewId) return;

        try {
            await PlatPursuit.API.delete(`/api/v1/reviews/${reviewId}/`);
            PlatPursuit.ToastManager.success('Review deleted. Refreshing...');
            setTimeout(() => window.location.reload(), 1000);
        } catch (error) {
            const errData = await error.response?.json().catch(() => null);
            PlatPursuit.ToastManager.error(errData?.error || 'Failed to delete review.');
        }
    },

    // ------------------------------------------------------------------ //
    //  Inline Edit (for feed reviews owned by user)
    // ------------------------------------------------------------------ //

    startInlineEdit(reviewId) {
        const card = document.querySelector(`.review-card[data-review-id="${reviewId}"]`);
        if (!card) return;

        const bodyDisplay = card.querySelector('.review-body-display');
        if (!bodyDisplay) return;

        // Fetch current body text from API for editing
        PlatPursuit.API.get(`/api/v1/reviews/${reviewId}/`).then(data => {
            const review = data.review || data;
            const currentBody = review.body || '';
            const currentRec = review.recommended;

            bodyDisplay.innerHTML = `
                <div class="flex items-center gap-2 mb-2">
                    <span class="text-sm font-bold">Recommend?</span>
                    <button type="button" class="btn btn-xs inline-rec-toggle ${currentRec ? 'btn-success active' : 'btn-ghost border border-base-300'}" data-value="true">Yes</button>
                    <button type="button" class="btn btn-xs inline-rec-toggle ${!currentRec ? 'btn-error active' : 'btn-ghost border border-base-300'}" data-value="false">No</button>
                </div>
                <textarea class="textarea textarea-bordered w-full text-sm inline-edit-body" rows="4" maxlength="8000">${PlatPursuit.HTMLUtils.escape(currentBody)}</textarea>
                <div class="flex gap-2 mt-2">
                    <button class="btn btn-xs btn-primary inline-save-btn">Save</button>
                    <button class="btn btn-xs btn-ghost inline-cancel-btn">Cancel</button>
                </div>`;

            // Toggle handlers
            bodyDisplay.querySelectorAll('.inline-rec-toggle').forEach(btn => {
                btn.addEventListener('click', () => {
                    bodyDisplay.querySelectorAll('.inline-rec-toggle').forEach(b => {
                        const isThis = b === btn;
                        b.classList.toggle('btn-success', isThis && b.dataset.value === 'true');
                        b.classList.toggle('btn-error', isThis && b.dataset.value === 'false');
                        b.classList.toggle('active', isThis);
                        b.classList.toggle('btn-ghost', !isThis);
                        b.classList.toggle('border', !isThis);
                        b.classList.toggle('border-base-300', !isThis);
                    });
                });
            });

            // Cancel
            bodyDisplay.querySelector('.inline-cancel-btn').addEventListener('click', () => {
                bodyDisplay.innerHTML = review.body_html || '';
            });

            // Save
            bodyDisplay.querySelector('.inline-save-btn').addEventListener('click', async () => {
                const newBody = bodyDisplay.querySelector('.inline-edit-body').value.trim();
                if (!newBody || newBody.length < 50) {
                    PlatPursuit.ToastManager.error('Review must be at least 50 characters.');
                    return;
                }
                const activeToggle = bodyDisplay.querySelector('.inline-rec-toggle.active');
                const rec = activeToggle?.dataset.value === 'true';

                try {
                    const updated = await PlatPursuit.API.put(`/api/v1/reviews/${reviewId}/`, { body: newBody, recommended: rec });
                    const updatedReview = updated.review || updated;
                    bodyDisplay.innerHTML = updatedReview.body_html || '';

                    // Update thumb indicator
                    const thumbContainer = card.querySelector('.flex.items-center.gap-2.mb-2');
                    if (thumbContainer) {
                        const thumbSpan = thumbContainer.querySelector('span:first-child');
                        const textSpan = thumbContainer.querySelector('span:nth-child(2)');
                        if (thumbSpan) thumbSpan.className = rec ? 'text-success' : 'text-error';
                        if (textSpan) {
                            textSpan.className = `font-bold text-sm ${rec ? 'text-success' : 'text-error'}`;
                            textSpan.textContent = rec ? 'Recommended' : 'Not Recommended';
                        }
                    }

                    PlatPursuit.ToastManager.success('Review updated.');
                } catch (error) {
                    const errData = await error.response?.json().catch(() => null);
                    PlatPursuit.ToastManager.error(errData?.error || 'Failed to update review.');
                }
            });
        }).catch(() => {
            PlatPursuit.ToastManager.error('Failed to load review for editing.');
        });
    },

    async deleteReview(reviewId) {
        if (!confirm('Delete this review?')) return;

        try {
            await PlatPursuit.API.delete(`/api/v1/reviews/${reviewId}/`);

            const card = document.querySelector(`.review-card[data-review-id="${reviewId}"]`);
            if (card) card.remove();

            PlatPursuit.ToastManager.success('Review deleted.');
        } catch (error) {
            const errData = await error.response?.json().catch(() => null);
            PlatPursuit.ToastManager.error(errData?.error || 'Failed to delete review.');
        }
    },

    // ------------------------------------------------------------------ //
    //  Report
    // ------------------------------------------------------------------ //

    async reportReview(reviewId) {
        const reason = prompt('Why are you reporting this review?\n\nOptions: spam, harassment, inappropriate, spoiler, misinformation, other');
        if (!reason) return;

        const validReasons = ['spam', 'harassment', 'inappropriate', 'spoiler', 'misinformation', 'other'];
        const normalizedReason = reason.toLowerCase().trim();

        if (!validReasons.includes(normalizedReason)) {
            PlatPursuit.ToastManager.error('Invalid reason. Choose from: ' + validReasons.join(', '));
            return;
        }

        try {
            await PlatPursuit.API.post(`/api/v1/reviews/${reviewId}/report/`, {
                reason: normalizedReason,
            });
            PlatPursuit.ToastManager.success('Review reported. Thank you.');
        } catch (error) {
            const errData = await error.response?.json().catch(() => null);
            PlatPursuit.ToastManager.error(errData?.error || 'Failed to report review.');
        }
    },

    // ------------------------------------------------------------------ //
    //  Ratings Display Toggle
    // ------------------------------------------------------------------ //

    initRatingsToggle() {
        const toggleBtn = document.getElementById('toggle-ratings-display');
        const content = document.getElementById('ratings-display-content');
        const chevron = document.getElementById('ratings-chevron');
        if (!toggleBtn || !content) return;

        toggleBtn.addEventListener('click', () => {
            content.classList.toggle('hidden');
            if (chevron) chevron.classList.toggle('rotate-180');
        });
    },

    // ------------------------------------------------------------------ //
    //  Rating Form
    // ------------------------------------------------------------------ //

    initRatingForm() {
        const toggleBtn = document.getElementById('toggle-rating-form');
        const container = document.getElementById('rating-form-container');
        const form = document.getElementById('rating-form');

        if (toggleBtn && container) {
            toggleBtn.dataset.originalText = toggleBtn.textContent;
            toggleBtn.addEventListener('click', () => {
                container.classList.toggle('hidden');
                toggleBtn.textContent = container.classList.contains('hidden')
                    ? toggleBtn.dataset.originalText
                    : 'Cancel';
            });
        }

        if (!form) return;

        form.addEventListener('submit', async (e) => {
            e.preventDefault();

            const submitBtn = form.querySelector('button[type="submit"]');
            if (submitBtn) submitBtn.disabled = true;

            const formData = new FormData(form);
            const payload = {
                difficulty: parseInt(formData.get('difficulty')),
                grindiness: parseInt(formData.get('grindiness')),
                hours_to_platinum: parseInt(formData.get('hours_to_platinum')) || 0,
                fun_ranking: parseInt(formData.get('fun_ranking')),
                overall_rating: parseFloat(formData.get('overall_rating')),
            };

            try {
                const data = await PlatPursuit.API.post(
                    `/api/v1/reviews/${this.config.conceptId}/group/${this.config.activeGroup}/rate/`,
                    payload
                );
                PlatPursuit.ToastManager.success(data.message || 'Rating submitted!');

                // Update community averages display
                if (data.community_averages) {
                    setTimeout(() => window.location.reload(), 1000);
                }
            } catch (error) {
                const errData = await error.response?.json().catch(() => null);
                PlatPursuit.ToastManager.error(errData?.error || 'Failed to submit rating.');
            } finally {
                if (submitBtn) submitBtn.disabled = false;
            }
        });
    },
};
