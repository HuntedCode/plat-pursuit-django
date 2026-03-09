/**
 * Review Hub Landing: Recent reviews feed with infinite scroll.
 *
 * Fetches reviews across all games from the API and renders
 * cards with game context (icon, title, link).
 */
window.PlatPursuit = window.PlatPursuit || {};

PlatPursuit.ReviewHubLanding = {
    config: null,
    observer: null,
    offset: 0,
    limit: 10,
    sort: 'newest',
    isLoading: false,
    hasMore: true,

    init(config) {
        this.config = config;
        this.offset = 0;
        this.hasMore = true;
        this.sort = 'newest';

        this.initFeed();
        this.initSortButtons();
    },

    // ------------------------------------------------------------------ //
    //  Infinite Scroll Feed
    // ------------------------------------------------------------------ //

    initFeed() {
        const sentinel = document.getElementById('landing-review-sentinel');
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

        const loading = document.getElementById('landing-review-loading');
        const emptyState = document.getElementById('landing-feed-empty');
        if (loading) loading.classList.remove('hidden');

        try {
            const url = `/api/v1/reviews/recent/?sort=${this.sort}&limit=${this.limit}&offset=${this.offset}`;
            const data = await PlatPursuit.API.get(url);

            const feed = document.getElementById('landing-review-feed');
            if (!feed) return;

            const reviews = data.reviews || [];

            reviews.forEach(review => {
                feed.insertAdjacentHTML('beforeend', this.buildLandingReviewCard(review));
            });

            const wasFirstLoad = this.offset === 0;
            this.offset += reviews.length;
            this.hasMore = data.has_more || false;

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

    // ------------------------------------------------------------------ //
    //  Sort
    // ------------------------------------------------------------------ //

    initSortButtons() {
        document.querySelectorAll('.landing-sort-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const newSort = btn.dataset.sort;
                if (newSort === this.sort) return;

                document.querySelectorAll('.landing-sort-btn').forEach(b => {
                    b.classList.remove('btn-primary');
                    b.classList.add('btn-ghost', 'border', 'border-base-300');
                });
                btn.classList.add('btn-primary');
                btn.classList.remove('btn-ghost', 'border', 'border-base-300');

                this.sort = newSort;
                this.offset = 0;
                this.hasMore = true;

                const feed = document.getElementById('landing-review-feed');
                if (feed) feed.innerHTML = '';

                this.loadReviews();
            });
        });
    },

    // ------------------------------------------------------------------ //
    //  Card Builder
    // ------------------------------------------------------------------ //

    buildLandingReviewCard(review) {
        const esc = PlatPursuit.HTMLUtils.escape;
        const author = review.author || {};
        const concept = review.concept || {};
        const timeAgo = PlatPursuit.TimeFormatter.relative(review.created_at);

        const thumbIcon = review.recommended
            ? '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><path d="M2 21h4V9H2v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L15.17 1 7.59 8.59C7.22 8.95 7 9.45 7 10v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/></svg>'
            : '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><path d="M15 3H6c-.83 0-1.54.5-1.84 1.22l-3.02 7.05c-.09.23-.14.47-.14.73v2c0 1.1.9 2 2 2h6.31l-.95 4.57-.03.32c0 .41.17.79.44 1.06L9.83 23l7.58-7.59c.36-.36.59-.86.59-1.41V5c0-1.1-.9-2-2-2zm4 0v12h4V3h-4z"/></svg>';

        const thumbColor = review.recommended ? 'text-success' : 'text-error';

        const conceptIcon = concept.concept_icon_url
            ? `<img src="${esc(concept.concept_icon_url)}" alt="" class="w-8 h-8 rounded object-cover shrink-0" loading="lazy" />`
            : `<div class="w-8 h-8 rounded bg-base-300 shrink-0"></div>`;

        return `
        <div class="card bg-base-100 border-2 border-base-300 shadow-sm">
            <div class="card-body p-4">
                <a href="/reviews/${esc(concept.slug || '')}/" class="flex items-center gap-3 mb-3 hover:opacity-80 transition-opacity">
                    ${conceptIcon}
                    <span class="font-bold text-sm text-primary line-clamp-1 pr-1">${esc(concept.unified_title || '')}</span>
                </a>
                <div class="flex items-start gap-3">
                    <div class="avatar shrink-0">
                        <div class="w-9 h-9 lg:w-10 lg:h-10 rounded-full ring-2 ring-primary">
                            <img src="${esc(author.avatar_url || '')}" alt="${esc(author.display_psn_username || '')}" class="rounded-full object-cover" loading="lazy" />
                        </div>
                    </div>
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 mb-1">
                            <span class="${thumbColor}">${thumbIcon}</span>
                            <a href="/profiles/${esc(author.psn_username || '')}/" class="text-sm font-bold ${author.is_premium ? 'legendary-title' : 'text-primary'} hover:underline">${esc(author.display_psn_username || author.psn_username || '')}</a>
                            ${author.displayed_title ? `<span class="text-xs ${author.is_premium ? 'legendary-title' : 'text-secondary'} italic pr-1 hidden lg:inline">${esc(author.displayed_title)}</span>` : ''}
                            <span class="text-xs text-base-content/40 ml-auto">${esc(timeAgo)}</span>
                        </div>
                        <div class="prose prose-sm max-w-none line-clamp-4 pr-1">${review.body_html || ''}</div>
                        <div class="flex items-center gap-3 mt-2 text-xs text-base-content/50">
                            <span class="flex items-center gap-1">
                                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><path d="M2 21h4V9H2v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L15.17 1 7.59 8.59C7.22 8.95 7 9.45 7 10v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/></svg>
                                ${review.helpful_count} helpful
                            </span>
                            <span class="flex items-center gap-1">
                                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>
                                ${review.reply_count} replies
                            </span>
                            <a href="/reviews/${esc(concept.slug || '')}/" class="text-primary hover:underline ml-auto">View in Review Hub</a>
                        </div>
                    </div>
                </div>
            </div>
        </div>`;
    },
};
