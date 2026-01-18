/**
 * Comment System JavaScript
 * Handles fetching, displaying, creating, editing, and interacting with comments
 */

class CommentSystem {
    constructor(sectionId = 'comment-section') {
        this.section = document.getElementById(sectionId);
        if (!this.section) return;

        this.conceptId = this.section.dataset.conceptId;
        this.trophyId = this.section.dataset.trophyId || null;
        this.currentSort = 'top';

        // DOM elements
        this.commentsList = document.getElementById('comments-list');
        this.commentsLoading = document.getElementById('comments-loading');
        this.commentsEmpty = document.getElementById('comments-empty');
        this.commentsError = document.getElementById('comments-error');
        this.sortSelect = document.getElementById('comment-sort');
        this.createForm = document.getElementById('comment-create-form');
        this.commentBody = document.getElementById('comment-body');
        this.charCount = document.getElementById('char-count');
        this.discussionToggle = document.getElementById('discussion-toggle');
        this.discussionContent = document.getElementById('discussion-content');
        this.discussionSortContainer = document.getElementById('discussion-sort-container');
        this.discussionToggleIcon = document.getElementById('discussion-toggle-icon');

        // Pagination state
        this.currentOffset = 0;
        this.hasMoreComments = false;
        this.loadMoreBtn = null;

        this.init();
    }

    init() {
        // Setup event listeners first
        this.setupEventListeners();

        // Don't load comments initially since section starts collapsed
        // Comments will load when user expands the section
    }

    setupEventListeners() {
        // Discussion section toggle
        if (this.discussionToggle) {
            this.discussionToggle.addEventListener('click', () => {
                this.toggleDiscussionSection();
            });
        }

        // Sort change
        if (this.sortSelect) {
            this.sortSelect.addEventListener('change', (e) => {
                this.currentSort = e.target.value;
                this.currentOffset = 0;  // Reset pagination
                this.loadComments(false);  // Replace, not append
            });
        }

        // Character counter
        if (this.commentBody && this.charCount) {
            this.commentBody.addEventListener('input', (e) => {
                this.charCount.textContent = e.target.value.length;
            });
        }

        // Comment creation form
        if (this.createForm) {
            this.createForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.createComment();
            });
        }

        // Event delegation for dynamic buttons
        document.addEventListener('click', (e) => {
            // Collapse button
            if (e.target.closest('.collapse-btn')) {
                const btn = e.target.closest('.collapse-btn');
                this.toggleCollapse(btn.dataset.commentId);
            }

            // Upvote button
            if (e.target.closest('.upvote-btn')) {
                const btn = e.target.closest('.upvote-btn');
                this.toggleVote(btn.dataset.commentId);
            }

            // Reply button
            if (e.target.closest('.reply-btn')) {
                const btn = e.target.closest('.reply-btn');
                this.showReplyForm(btn.dataset.commentId);
            }

            // Edit button
            if (e.target.closest('.edit-btn')) {
                const btn = e.target.closest('.edit-btn');
                this.showEditForm(btn.dataset.commentId);
            }

            // Delete button
            if (e.target.closest('.delete-btn')) {
                const btn = e.target.closest('.delete-btn');
                this.deleteComment(btn.dataset.commentId);
            }

            // Report button
            if (e.target.closest('.report-btn')) {
                const btn = e.target.closest('.report-btn');
                this.showReportModal(btn.dataset.commentId);
            }

            // Load more replies button
            if (e.target.closest('.load-more-replies-btn')) {
                const btn = e.target.closest('.load-more-replies-btn');
                this.loadMoreReplies(
                    btn.dataset.commentId,
                    parseInt(btn.dataset.replyOffset)
                );
            }
        });
    }

    /**
     * Build API URL for comments
     */
    getApiUrl() {
        let url = `/api/v1/comments/concept/${this.conceptId}/`;
        if (this.trophyId) {
            url = `/api/v1/comments/concept/${this.conceptId}/trophy/${this.trophyId}/`;
        }
        return url;
    }

    /**
     * Load comments from API
     * @param {boolean} append - If true, append to existing comments. If false, replace all comments.
     */
    async loadComments(append = false) {
        if (!append) {
            this.currentOffset = 0;
            this.showLoading();
        }

        try {
            const url = `${this.getApiUrl()}?sort=${this.currentSort}&response_format=html&limit=5&offset=${this.currentOffset}`;
            const response = await fetch(url, {
                headers: {
                    'Accept': 'application/json',
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();

            if (append) {
                // Append new comments to existing list
                this.commentsList.insertAdjacentHTML('beforeend', data.html);
            } else {
                // Replace entire list
                this.renderComments(data.html);
            }

            this.updateCommentCount(data.count);
            this.hasMoreComments = data.has_more;
            this.currentOffset = data.next_offset || this.currentOffset + 5;

            // Show or hide Load More button
            this.updateLoadMoreButton(data.has_more, data.count);

        } catch (error) {
            console.error('Failed to load comments:', error);
            this.showError('Failed to load comments. Please try again.');
        }
    }

    /**
     * Render comments to DOM
     */
    renderComments(html) {
        if (!html || html.trim().length === 0) {
            this.showEmpty();
            return;
        }

        this.hideStates();
        this.commentsList.innerHTML = html;
    }

    /**
     * Update or remove the Load More button
     */
    updateLoadMoreButton(hasMore, totalCount) {
        // Remove existing button if present
        if (this.loadMoreBtn) {
            this.loadMoreBtn.remove();
            this.loadMoreBtn = null;
        }

        if (!hasMore) return;

        // Create and append Load More button
        const remaining = totalCount - this.currentOffset;
        this.loadMoreBtn = document.createElement('div');
        this.loadMoreBtn.className = 'flex justify-center mt-6';
        this.loadMoreBtn.innerHTML = `
            <button class="btn btn-primary btn-outline" id="load-more-comments-btn">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
                </svg>
                Load More Comments (${remaining} remaining)
            </button>
        `;

        // Insert after comments list container
        this.commentsList.parentElement.insertAdjacentElement('afterend', this.loadMoreBtn);

        // Add click handler
        this.loadMoreBtn.querySelector('button').addEventListener('click', () => {
            this.loadComments(true);  // append mode
        });
    }

    /**
     * Load more replies for a specific comment
     */
    async loadMoreReplies(commentId, currentOffset) {
        try {
            const url = `${this.getApiUrl()}?response_format=html&parent_id=${commentId}&reply_offset=${currentOffset}&reply_limit=3&sort=${this.currentSort}`;
            const response = await fetch(url, {
                headers: { 'Accept': 'application/json' }
            });

            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const data = await response.json();

            // Find the replies container for this comment
            const repliesContainer = document.querySelector(`.replies[data-replies-for="${commentId}"]`);
            if (!repliesContainer) return;

            // Find the Load More button
            const loadMoreBtn = document.querySelector(`.load-more-replies-btn[data-comment-id="${commentId}"]`);

            // Append new replies inside the replies container (at the end)
            repliesContainer.insertAdjacentHTML('beforeend', data.html);

            // Update or remove Load More button
            if (loadMoreBtn) {
                if (data.has_more) {
                    loadMoreBtn.dataset.replyOffset = data.shown;
                    const remaining = data.reply_count - data.shown;
                    const countSpan = loadMoreBtn.querySelector('span');
                    if (countSpan) {
                        countSpan.textContent = `${remaining} remaining`;
                    }
                } else {
                    loadMoreBtn.remove();
                }
            }

        } catch (error) {
            console.error('Failed to load more replies:', error);
            this.showToast('Failed to load replies. Please try again.', 'error');
        }
    }

    /**
     * Create a new comment
     */
    async createComment(parentId = null) {
        const formData = new FormData();
        const body = this.commentBody.value.trim();

        if (!body) {
            this.showToast('Please enter a comment', 'error');
            return;
        }

        formData.append('body', body);
        if (parentId) {
            formData.append('parent_id', parentId);
        }

        const imageInput = document.getElementById('comment-image');
        if (imageInput && imageInput.files.length > 0) {
            formData.append('image', imageInput.files[0]);
        }

        const submitBtn = document.getElementById('comment-submit-btn');
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="loading loading-spinner loading-sm"></span> Posting...';

        try {
            let url = `${this.getApiUrl()}create/`;
            const response = await fetch(url, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': this.getCsrfToken(),
                },
                body: formData
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `HTTP ${response.status}`);
            }

            // Success - reload comments (reset to first page to show in correct sort position)
            this.showToast('Comment posted successfully!', 'success');
            this.commentBody.value = '';
            if (this.charCount) this.charCount.textContent = '0';
            if (imageInput) imageInput.value = '';
            this.currentOffset = 0;  // Reset pagination
            await this.loadComments(false);  // Full reload, not append

        } catch (error) {
            console.error('Failed to create comment:', error);
            this.showToast(error.message || 'Failed to post comment', 'error');
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = `
                <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/>
                </svg>
                Post Comment
            `;
        }
    }

    /**
     * Toggle collapse/expand replies for a top-level comment
     */
    toggleCollapse(commentId) {
        const btn = document.querySelector(`.collapse-btn[data-comment-id="${commentId}"]`);
        const repliesContainer = document.querySelector(`.replies[data-replies-for="${commentId}"]`);
        const collapsedIndicator = document.querySelector(`.collapsed-indicator[data-indicator-for="${commentId}"]`);
        const loadMoreBtn = document.querySelector(`.load-more-replies-btn[data-comment-id="${commentId}"]`);

        if (!btn || !repliesContainer) return;

        const svg = btn.querySelector('svg');
        const isCollapsed = repliesContainer.classList.contains('hidden');

        if (isCollapsed) {
            // Expand
            repliesContainer.classList.remove('hidden');
            svg.style.transform = 'rotate(0deg)';

            // Hide collapsed indicator
            if (collapsedIndicator) {
                collapsedIndicator.classList.add('hidden');
                collapsedIndicator.classList.remove('flex');
            }

            // Show Load More button if it exists
            if (loadMoreBtn) {
                loadMoreBtn.classList.remove('hidden');
            }
        } else {
            // Collapse
            repliesContainer.classList.add('hidden');
            svg.style.transform = 'rotate(-90deg)';

            // Show collapsed indicator and update count
            if (collapsedIndicator) {
                // Count actual reply items in the container
                const replyCount = repliesContainer.querySelectorAll('.comment-item').length;
                const countSpan = collapsedIndicator.querySelector('span');
                if (countSpan && replyCount > 0) {
                    const replyWord = replyCount === 1 ? 'reply' : 'replies';
                    countSpan.textContent = `${replyCount} hidden ${replyWord}`;
                }

                collapsedIndicator.classList.remove('hidden');
                collapsedIndicator.classList.add('flex');
            }

            // Hide Load More button if it exists
            if (loadMoreBtn) {
                loadMoreBtn.classList.add('hidden');
            }
        }
    }

    /**
     * Toggle upvote on a comment
     */
    async toggleVote(commentId) {
        const btn = document.querySelector(`.upvote-btn[data-comment-id="${commentId}"]`);
        if (!btn) return;

        try {
            const response = await fetch(`/api/v1/comments/${commentId}/vote/`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': this.getCsrfToken(),
                    'Content-Type': 'application/json',
                }
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `HTTP ${response.status}`);
            }

            const data = await response.json();

            // Update UI
            const svg = btn.querySelector('svg');
            const countSpan = btn.querySelector('.upvote-count');

            if (data.voted) {
                btn.setAttribute('data-voted', 'true');
                svg.classList.remove('fill-current');
                svg.classList.add('fill-primary');
            } else {
                btn.removeAttribute('data-voted');
                svg.classList.remove('fill-primary');
                svg.classList.add('fill-current');
            }

            countSpan.textContent = data.upvote_count;

        } catch (error) {
            console.error('Failed to toggle vote:', error);
            this.showToast(error.message || 'Failed to vote', 'error');
        }
    }

    /**
     * Show reply form
     */
    showReplyForm(commentId) {
        // TODO: Implement reply form
        console.log('Reply to comment:', commentId);
        this.showToast('Reply functionality coming soon!', 'info');
    }

    /**
     * Show edit form
     */
    showEditForm(commentId) {
        // TODO: Implement edit form
        console.log('Edit comment:', commentId);
        this.showToast('Edit functionality coming soon!', 'info');
    }

    /**
     * Delete comment
     */
    async deleteComment(commentId) {
        if (!confirm('Are you sure you want to delete this comment? This action cannot be undone.')) {
            return;
        }

        try {
            const response = await fetch(`/api/v1/comments/${commentId}/`, {
                method: 'DELETE',
                headers: {
                    'X-CSRFToken': this.getCsrfToken(),
                }
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `HTTP ${response.status}`);
            }

            this.showToast('Comment deleted successfully', 'success');
            await this.loadComments();

        } catch (error) {
            console.error('Failed to delete comment:', error);
            this.showToast(error.message || 'Failed to delete comment', 'error');
        }
    }

    /**
     * Show report modal
     */
    showReportModal(commentId) {
        // TODO: Implement report modal
        console.log('Report comment:', commentId);
        this.showToast('Report functionality coming soon!', 'info');
    }

    /**
     * Update comment count badge
     */
    updateCommentCount(count) {
        const badge = document.getElementById('comment-count');
        if (badge) {
            badge.textContent = count;
        }
    }

    /**
     * UI State helpers
     */
    showLoading() {
        this.hideStates();
        this.commentsLoading.classList.remove('hidden');
    }

    showEmpty() {
        this.hideStates();
        this.commentsEmpty.classList.remove('hidden');
    }

    showError(message) {
        this.hideStates();
        this.commentsError.classList.remove('hidden');
        document.getElementById('comments-error-message').textContent = message;
    }

    hideStates() {
        this.commentsLoading.classList.add('hidden');
        this.commentsEmpty.classList.add('hidden');
        this.commentsError.classList.add('hidden');
    }

    /**
     * Show toast notification
     */
    showToast(message, type = 'info') {
        // TODO: Implement toast notifications (could use DaisyUI toast)
        console.log(`[${type.toUpperCase()}]`, message);
    }

    /**
     * Toggle discussion section visibility
     */
    toggleDiscussionSection() {
        const isCollapsed = this.discussionContent.classList.contains('hidden');

        if (isCollapsed) {
            // Expand
            this.discussionContent.classList.remove('hidden');
            this.discussionContent.classList.add('flex');
            this.discussionSortContainer.classList.remove('hidden');
            this.discussionToggleIcon.style.transform = 'rotate(0deg)';

            // Load comments on first expand if not already loaded
            if (!this.commentsList.innerHTML.trim()) {
                this.loadComments();
            }
        } else {
            // Collapse
            this.discussionContent.classList.add('hidden');
            this.discussionContent.classList.remove('flex');
            this.discussionSortContainer.classList.add('hidden');
            this.discussionToggleIcon.style.transform = 'rotate(-90deg)';
        }
    }

    /**
     * Utility functions
     */
    getCsrfToken() {
        return document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
               document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1] || '';
    }
}

// Initialize comment system when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new CommentSystem();
});
