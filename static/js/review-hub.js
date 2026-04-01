/**
 * Review Hub: Review feed, voting, replies, ratings.
 *
 * Client-side rendering of review cards from JSON API responses.
 * Uses IntersectionObserver for infinite scroll.
 */
window.PlatPursuit = window.PlatPursuit || {};

PlatPursuit.ReviewHub = {
    config: null,
    observer: null,
    offset: 0,
    limit: 10,
    sort: 'helpful',
    isLoading: false,
    hasMore: true,
    loadedReplyIds: new Set(),
    guidelinesAgreed: false,
    _pendingAction: null,

    init(config) {
        this.config = config;
        this.offset = 0;
        this.hasMore = true;
        this.sort = 'helpful';
        this.loadedReplyIds = new Set();

        // Read guidelines status from container data attribute
        const container = document.getElementById('review-hub-container');
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

        // Clear stale pending action if user dismisses guidelines modal without agreeing
        const guidelinesModal = document.getElementById('guidelines-agreement-modal');
        if (guidelinesModal) {
            guidelinesModal.addEventListener('close', () => {
                if (!this.guidelinesAgreed && this._pendingAction) {
                    this._pendingAction = null;
                }
            });
        }

        this.initReviewFeed();
        this.initSortButtons();
        this.initReviewForm();
        this.initRatingForm();
        this.initYourReview();
        this.initUserRatingToggle();
        this.initRatingsToggle();
        this.initTrophyListToggle();
    },

    // ------------------------------------------------------------------ //
    //  Community Guidelines
    // ------------------------------------------------------------------ //

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
            } else {
                // No pending action: user agreed from the banner, reload to show the review form
                window.location.reload();
            }
        } catch (error) {
            console.error('Error agreeing to guidelines:', error);
            PlatPursuit.ToastManager.error('An error occurred. Please try again.');
        } finally {
            this._confirmingGuidelines = false;
        }
    },

    // ------------------------------------------------------------------ //
    //  Review Feed (Infinite Scroll)
    // ------------------------------------------------------------------ //

    initReviewFeed() {
        const sentinel = document.getElementById('review-sentinel');
        if (!sentinel) return;

        this.observer = new PlatPursuit.ZoomAwareObserver((entries) => {
            if (entries[0].isIntersecting && !this.isLoading && this.hasMore) {
                this.loadReviews();
            }
        }, { rootMargin: '200px', scrollBuffer: 200 });

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
            <button class="btn btn-xs btn-ghost" onclick="PlatPursuit.ReviewHub.startInlineEdit(${review.id})" aria-label="Edit">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
            </button>
            <button class="btn btn-xs btn-ghost text-error" onclick="PlatPursuit.ReviewHub.deleteReview(${review.id})" aria-label="Delete">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
            </button>` : '';

        const reportBtn = (!review.is_own && this.config.isAuthenticated) ? `
            <button class="btn btn-xs btn-ghost text-base-content/40 hover:text-warning" onclick="PlatPursuit.ReviewHub.reportReview(${review.id})" aria-label="Report">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 21v-4m0 0V5a2 2 0 012-2h6.5l1 1H21l-3 6 3 6h-8.5l-1-1H5a2 2 0 00-2 2zm9-13.5V9"/></svg>
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
                        <button class="btn btn-sm btn-ghost gap-1.5 vote-btn ${helpfulActive}" onclick="PlatPursuit.ReviewHub.toggleVote(${review.id}, 'helpful')" data-review-id="${review.id}" data-vote="helpful">
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor"><path d="M2 21h4V9H2v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L15.17 1 7.59 8.59C7.22 8.95 7 9.45 7 10v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/></svg>
                            Helpful <span class="helpful-count">${review.helpful_count}</span>
                        </button>
                        <button class="btn btn-sm btn-ghost gap-1.5 vote-btn ${funnyActive}" onclick="PlatPursuit.ReviewHub.toggleVote(${review.id}, 'funny')" data-review-id="${review.id}" data-vote="funny">
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M15.5 11c.5 0 .5-2 0-2s-.5 2 0 2zM8.5 11c.5 0 .5-2 0-2s-.5 2 0 2z"/><path d="M7 13c0 3 2.5 4.5 5 4.5s5-1.5 5-4.5H7z"/></svg>
                            Funny <span class="funny-count">${review.funny_count}</span>
                        </button>
                        <button class="btn btn-sm btn-ghost gap-1.5" onclick="PlatPursuit.ReviewHub.toggleReplies(${review.id})" aria-label="Toggle replies">
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
                    <button class="btn btn-sm btn-primary" onclick="PlatPursuit.ReviewHub.submitReply(${reviewId})">Reply</button>
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
            <button class="btn btn-xs btn-ghost" onclick="PlatPursuit.ReviewHub.editReply(${reply.id})" aria-label="Edit reply">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
            </button>
            <button class="btn btn-xs btn-ghost text-error" onclick="PlatPursuit.ReviewHub.deleteReply(${reply.id})" aria-label="Delete reply">
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

    submitReply(reviewId) {
        const input = document.querySelector(`.reply-input[data-review-id="${reviewId}"]`);
        if (!input || !input.value.trim()) return;

        this.checkGuidelines(async () => {
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
        });
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
        const progressEls = {
            bar: document.getElementById('review-progress-bar'),
            text: document.getElementById('review-progress-text'),
            icon: document.getElementById('review-progress-icon'),
        };

        if (textarea) {
            textarea.addEventListener('input', () => {
                if (counter) counter.textContent = `${textarea.value.length} / 8000`;
                updateSubmitState();

                const wordCount = textarea.value.trim() ? textarea.value.trim().split(/\s+/).length : 0;
                PlatPursuit.ReviewProgressTiers.updateWordProgress(progressEls, wordCount);
            });
        }

        // Submit
        form.addEventListener('submit', (e) => {
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

            this.checkGuidelines(async () => {
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

        // Edit char counter + word progress bar
        const editBody = document.getElementById('your-review-edit-body');
        const editCounter = document.getElementById('edit-char-count');
        const editProgressEls = {
            bar: document.getElementById('edit-progress-bar'),
            text: document.getElementById('edit-progress-text'),
            icon: document.getElementById('edit-progress-icon'),
        };

        if (editBody) {
            editBody.addEventListener('input', () => {
                if (editCounter) editCounter.textContent = `${editBody.value.length} / 8000`;
                const wordCount = editBody.value.trim() ? editBody.value.trim().split(/\s+/).length : 0;
                PlatPursuit.ReviewProgressTiers.updateWordProgress(editProgressEls, wordCount);
            });
        }
    },

    enterEditMode() {
        document.getElementById('your-review-display')?.classList.add('hidden');
        document.getElementById('your-review-edit')?.classList.remove('hidden');

        // Initialize progress bar with existing content
        const editBody = document.getElementById('your-review-edit-body');
        if (editBody) {
            const wordCount = editBody.value.trim() ? editBody.value.trim().split(/\s+/).length : 0;
            PlatPursuit.ReviewProgressTiers.updateWordProgress({
                bar: document.getElementById('edit-progress-bar'),
                text: document.getElementById('edit-progress-text'),
                icon: document.getElementById('edit-progress-icon'),
            }, wordCount);
        }
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
                <div class="mt-2">
                    <div class="flex items-center gap-2">
                        <div class="inline-edit-progress-icon shrink-0 transition-all duration-300" style="color: var(--color-base-content); opacity: 0.25;">
                            ${PlatPursuit.ReviewProgressTiers.trophyCupSvg}
                        </div>
                        <div class="flex-1 bg-base-300 rounded-full h-2 overflow-hidden">
                            <div class="inline-edit-progress-bar h-full rounded-full transition-all duration-300 ease-out bg-error" style="width: 0%"></div>
                        </div>
                    </div>
                    <p class="inline-edit-progress-text text-xs text-base-content/50 mt-1 italic pr-1">Write at least 10 words to get started...</p>
                </div>
                <div class="flex gap-2 mt-2">
                    <button class="btn btn-xs btn-primary inline-save-btn">Save</button>
                    <button class="btn btn-xs btn-ghost inline-cancel-btn">Cancel</button>
                </div>`;

            // Wire up progress bar
            const inlineProgressEls = {
                bar: bodyDisplay.querySelector('.inline-edit-progress-bar'),
                text: bodyDisplay.querySelector('.inline-edit-progress-text'),
                icon: bodyDisplay.querySelector('.inline-edit-progress-icon'),
            };
            const inlineTextarea = bodyDisplay.querySelector('.inline-edit-body');
            const initialWords = currentBody.trim() ? currentBody.trim().split(/\s+/).length : 0;
            PlatPursuit.ReviewProgressTiers.updateWordProgress(inlineProgressEls, initialWords);

            inlineTextarea.addEventListener('input', () => {
                const wc = inlineTextarea.value.trim() ? inlineTextarea.value.trim().split(/\s+/).length : 0;
                PlatPursuit.ReviewProgressTiers.updateWordProgress(inlineProgressEls, wc);
            });

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
    //  User Rating Panel Toggle
    // ------------------------------------------------------------------ //

    initUserRatingToggle() {
        const toggleBtn = document.getElementById('toggle-user-rating');
        const content = document.getElementById('user-rating-content');
        const chevron = document.getElementById('user-rating-chevron');
        if (!toggleBtn || !content) return;

        toggleBtn.addEventListener('click', () => {
            content.classList.toggle('hidden');
            if (chevron) chevron.classList.toggle('rotate-180');
        });
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

    initTrophyListToggle() {
        const toggleBtn = document.getElementById('toggle-trophy-list');
        const content = document.getElementById('trophy-list-content');
        const chevron = document.getElementById('trophy-list-chevron');
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
        const form = document.getElementById('rating-form');
        if (!form) return;

        // Live hours validation: disable submit until hours > 0
        const hoursInput = form.querySelector('[name="hours_to_platinum"]');
        const submitBtn = form.querySelector('button[type="submit"]');
        if (hoursInput && submitBtn) {
            const updateBtn = () => {
                submitBtn.disabled = !(parseInt(hoursInput.value) > 0);
            };
            hoursInput.addEventListener('input', updateBtn);
        }

        form.addEventListener('submit', async (e) => {
            e.preventDefault();

            const submitBtn = form.querySelector('button[type="submit"]');
            if (submitBtn) submitBtn.disabled = true;

            const formData = new FormData(form);
            const hours = parseInt(formData.get('hours_to_platinum')) || 0;
            if (hours <= 0) {
                PlatPursuit.ToastManager.error('Please enter hours to platinum.');
                if (submitBtn) submitBtn.disabled = false;
                return;
            }

            const payload = {
                difficulty: parseInt(formData.get('difficulty')),
                grindiness: parseInt(formData.get('grindiness')),
                hours_to_platinum: hours,
                fun_ranking: parseInt(formData.get('fun_ranking')),
                overall_rating: parseFloat(formData.get('overall_rating')),
            };

            try {
                const data = await PlatPursuit.API.post(
                    `/api/v1/reviews/${this.config.conceptId}/group/${this.config.activeGroup}/rate/`,
                    payload
                );
                PlatPursuit.ToastManager.success(data.message || 'Rating submitted!');

                // Update submit button text for subsequent submissions
                const submitBtn2 = form.querySelector('button[type="submit"]');
                if (submitBtn2) submitBtn2.textContent = 'Update Rating';

                // Update community averages display
                if (data.community_averages) {
                    this.updateCommunityStatsDisplay(data.community_averages);
                }
            } catch (error) {
                const errData = await error.response?.json().catch(() => null);
                PlatPursuit.ToastManager.error(
                    this._extractErrorMessage(errData, 'Failed to submit rating.')
                );
            } finally {
                if (submitBtn) submitBtn.disabled = false;
            }
        });
    },

    // ------------------------------------------------------------------ //
    //  Error Helpers
    // ------------------------------------------------------------------ //

    _extractErrorMessage(errData, fallback) {
        if (!errData) return fallback;
        if (errData.error) return errData.error;
        // Form validation errors come as {errors: {field: [messages]}}
        if (errData.errors && typeof errData.errors === 'object') {
            const firstField = Object.values(errData.errors)[0];
            if (Array.isArray(firstField) && firstField.length) return firstField[0];
        }
        return fallback;
    },

    // ------------------------------------------------------------------ //
    //  Dynamic Stats Updates
    // ------------------------------------------------------------------ //

    _getColorClass(value, thresholds) {
        for (const [max, cls] of thresholds) {
            if (value < max) return cls;
        }
        return thresholds[thresholds.length - 1][1];
    },

    _getProgressClass(value, thresholds) {
        for (const [max, cls] of thresholds) {
            if (value < max) return cls;
        }
        return thresholds[thresholds.length - 1][1];
    },

    updateCommunityStatsDisplay(avg) {
        if (!avg) return;
        const container = document.getElementById('ratings-display-content');
        if (!container) return;

        // Update each community stat inline
        const statMap = {
            difficulty: { val: avg.avg_difficulty, fmt: v => v.toFixed(1) },
            grindiness: { val: avg.avg_grindiness, fmt: v => v.toFixed(1) },
            hours: { val: avg.avg_hours, fmt: v => Math.round(v).toLocaleString() },
            fun: { val: avg.avg_fun, fmt: v => v.toFixed(1) },
            overall: { val: avg.avg_rating, fmt: v => v.toFixed(1) },
        };

        // Find all stat value spans within the community panel grid
        const grid = container.querySelector('[role="list"]');
        if (!grid) return;

        const items = grid.querySelectorAll('[role="listitem"]');
        const labels = ['Difficulty', 'Grindiness', 'Hours To Plat', 'Fun Ranking', 'Overall Rating'];
        const keys = ['difficulty', 'grindiness', 'hours', 'fun', 'overall'];

        const stdThresholds = [[4, 'text-success'], [8, 'text-warning'], [Infinity, 'text-error']];
        const stdBarThresholds = [[4, 'progress-success'], [8, 'progress-warning'], [Infinity, 'progress-error']];
        const funThresholds = [[4, 'text-error'], [8, 'text-warning'], [Infinity, 'text-success']];
        const funBarThresholds = [[4, 'progress-error'], [8, 'progress-warning'], [Infinity, 'progress-success']];
        const hoursThresholds = [[25, 'text-success'], [75, 'text-warning'], [100, 'text-accent'], [Infinity, 'text-error']];
        const hoursBarThresholds = [[25, 'progress-success'], [75, 'progress-warning'], [100, 'progress-accent'], [Infinity, 'progress-error']];
        const overallThresholds = [[2, 'text-error'], [4, 'text-warning'], [Infinity, 'text-success']];
        const overallBarThresholds = [[2, 'progress-error'], [4, 'progress-warning'], [Infinity, 'progress-success']];

        const thresholdSets = [stdThresholds, stdThresholds, hoursThresholds, funThresholds, overallThresholds];
        const barThresholdSets = [stdBarThresholds, stdBarThresholds, hoursBarThresholds, funBarThresholds, overallBarThresholds];

        items.forEach((item, i) => {
            if (i >= keys.length) return;
            const key = keys[i];
            const data = statMap[key];
            if (data.val == null) return;

            const valSpan = item.querySelector('.text-xl');
            const bar = item.querySelector('progress');
            if (valSpan) {
                valSpan.textContent = data.fmt(data.val);
                valSpan.className = valSpan.className.replace(/text-(success|warning|error|accent)/g, '');
                valSpan.classList.add(this._getColorClass(data.val, thresholdSets[i]));
            }
            if (bar) {
                bar.value = data.val;
                bar.className = bar.className.replace(/progress-(success|warning|error|accent)/g, '');
                bar.classList.add(this._getProgressClass(data.val, barThresholdSets[i]));
            }
        });

        // Update count text
        const countText = container.querySelector('.text-xs.text-center.italic');
        if (countText && avg.count != null) {
            countText.textContent = `Based on ${avg.count.toLocaleString()} rating${avg.count === 1 ? '' : 's'}.`;
        }
    },
};
