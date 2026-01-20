/**
 * Comment System JavaScript
 * Handles fetching, displaying, creating, editing, and interacting with comments
 */

class CommentSystem {
    constructor(sectionId = 'comment-section') {
        this.sectionId = sectionId;
        this.section = document.getElementById(sectionId);

        if (!this.section) {
            console.error(`Comment section not found: ${sectionId}`);
            return;
        }

        this.conceptId = this.section.dataset.conceptId;
        this.trophyId = this.section.dataset.trophyId || null;
        this.currentSort = 'top';

        // Determine if this is a trophy-level comment section
        this.isTrophySection = sectionId.startsWith('trophy-comment-section-');

        // DOM elements (scoped to section type)
        if (this.isTrophySection) {
            // Trophy-level selectors (scoped within the section)
            this.commentsList = this.section.querySelector('.trophy-comments-list');
            this.commentsLoading = this.section.querySelector('.trophy-comments-loading');
            this.commentsEmpty = this.section.querySelector('.trophy-comments-empty');
            this.commentsError = this.section.querySelector('.trophy-comments-error');
            this.sortSelect = this.section.querySelector('.trophy-comment-sort');
            this.createForm = this.section.querySelector('.trophy-comment-create-form');
            this.commentBody = this.section.querySelector('.trophy-comment-body');
            this.charCount = this.section.querySelector('.trophy-char-count');
            this.discussionToggle = this.section.querySelector('.trophy-discussion-toggle');
            this.discussionContent = this.section.querySelector('.trophy-discussion-content');
            this.discussionSortContainer = this.section.querySelector('.trophy-discussion-sort-container');
            this.discussionToggleIcon = this.section.querySelector('.trophy-toggle-icon');
            this.commentCountBadge = document.getElementById(`trophy-comment-count-badge-${this.trophyId}`);
            this.commentCount = document.getElementById(`trophy-comment-count-${this.trophyId}`);
        } else {
            // Game-level selectors (original IDs)
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
            this.commentCountBadge = null;
            this.commentCount = document.getElementById('comment-count');
        }

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
        // Discussion section toggle (game-level only)
        if (this.discussionToggle && !this.isTrophySection) {
            this.discussionToggle.addEventListener('click', () => {
                this.toggleDiscussionSection();
            });
        }

        // For trophy sections, load comments immediately since there's no toggle
        if (this.isTrophySection) {
            this.loadComments(false);
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

                // Check guidelines before allowing comment
                if (this.isTrophySection) {
                    checkTrophyGuidelinesBeforeComment(() => this.createComment());
                } else {
                    checkGuidelinesBeforeComment(() => this.createComment());
                }
            });
        }

        // Event delegation for dynamic buttons - scoped to this section
        this.section.addEventListener('click', (e) => {
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

        // Get submit button (scoped to section type)
        let submitBtn;
        if (this.isTrophySection) {
            submitBtn = this.section.querySelector('.trophy-comment-submit-btn');
        } else {
            submitBtn = document.getElementById('comment-submit-btn');
        }

        if (!submitBtn) return;

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
        // Scope selectors to this section to avoid conflicts between multiple CommentSystem instances
        const btn = this.section.querySelector(`.collapse-btn[data-comment-id="${commentId}"]`);
        const repliesContainer = this.section.querySelector(`.replies[data-replies-for="${commentId}"]`);
        const collapsedIndicator = this.section.querySelector(`.collapsed-indicator[data-indicator-for="${commentId}"]`);
        const loadMoreBtn = this.section.querySelector(`.load-more-replies-btn[data-comment-id="${commentId}"]`);

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
                },
                credentials: 'same-origin'
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
        // Check if reply form already exists for this comment
        const existingForm = this.section.querySelector(`.reply-form-container[data-parent-id="${commentId}"]`);
        if (existingForm) {
            existingForm.remove();
            return;
        }

        // Find the comment element
        const commentEl = this.section.querySelector(`.comment-item[data-comment-id="${commentId}"]`);
        if (!commentEl) return;

        // Create reply form HTML
        const replyFormHTML = `
            <div class="reply-form-container mt-4 p-4 bg-base-200 rounded-lg border-2 border-primary" data-parent-id="${commentId}">
                <h4 class="text-sm font-semibold mb-2">Reply to comment</h4>
                <form class="space-y-3">
                    <textarea
                        class="textarea textarea-bordered w-full h-24 resize-none reply-body"
                        placeholder="Write your reply..."
                        maxlength="2000"
                        required></textarea>
                    <div class="flex items-center justify-between">
                        <span class="text-xs text-base-content/60">
                            <span class="reply-char-count">0</span>/2000
                        </span>
                        <div class="flex gap-2">
                            <button type="button" class="btn btn-ghost btn-sm cancel-reply-btn">Cancel</button>
                            <button type="submit" class="btn btn-primary btn-sm submit-reply-btn">
                                <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/>
                                </svg>
                                Post Reply
                            </button>
                        </div>
                    </div>
                </form>
            </div>
        `;

        // Insert form at the end of the comment item (after everything)
        commentEl.insertAdjacentHTML('beforeend', replyFormHTML);

        // Get the inserted form
        const form = this.section.querySelector(`.reply-form-container[data-parent-id="${commentId}"]`);
        const textarea = form.querySelector('.reply-body');
        const charCount = form.querySelector('.reply-char-count');
        const cancelBtn = form.querySelector('.cancel-reply-btn');
        const submitBtn = form.querySelector('.submit-reply-btn');

        // Character count
        textarea.addEventListener('input', () => {
            charCount.textContent = textarea.value.length;
        });

        // Cancel button
        cancelBtn.addEventListener('click', () => {
            form.remove();
        });

        // Submit form
        form.querySelector('form').addEventListener('submit', async (e) => {
            e.preventDefault();

            // Check guidelines before allowing reply
            if (this.isTrophySection) {
                checkTrophyGuidelinesBeforeComment(() => this.submitReply(commentId, textarea.value, submitBtn, form));
            } else {
                checkGuidelinesBeforeComment(() => this.submitReply(commentId, textarea.value, submitBtn, form));
            }
        });

        // Focus textarea
        textarea.focus();
    }

    /**
     * Submit a reply to a comment
     */
    async submitReply(parentId, body, submitBtn, formEl) {
        if (!body || body.trim().length === 0) {
            this.showToast('Reply cannot be empty', 'error');
            return;
        }

        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="loading loading-spinner loading-sm"></span> Posting...';

        try {
            const formData = new FormData();
            formData.append('body', body.trim());
            formData.append('parent_id', parentId);

            const url = `${this.getApiUrl()}create/`;
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

            // Success - reload comments
            this.showToast('Reply posted successfully!', 'success');
            formEl.remove();
            this.currentOffset = 0;
            await this.loadComments(false);

        } catch (error) {
            console.error('Failed to post reply:', error);
            this.showToast(error.message || 'Failed to post reply', 'error');
            submitBtn.disabled = false;
            submitBtn.innerHTML = `
                <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/>
                </svg>
                Post Reply
            `;
        }
    }

    /**
     * Show edit form
     */
    showEditForm(commentId) {
        // Find the comment element
        const commentEl = this.section.querySelector(`.comment-item[data-comment-id="${commentId}"]`);
        if (!commentEl) return;

        // Check if edit form already exists
        const existingForm = commentEl.querySelector('.edit-form-container');
        if (existingForm) {
            existingForm.remove();
            // Restore the comment body display
            const bodyDiv = commentEl.querySelector('.prose');
            if (bodyDiv) bodyDiv.classList.remove('hidden');
            return;
        }

        // Get current comment body
        const bodyDiv = commentEl.querySelector('.prose');
        const bodyText = bodyDiv?.querySelector('p')?.textContent || '';

        if (!bodyDiv) return;

        // Hide the current body display
        bodyDiv.classList.add('hidden');

        // Create edit form HTML
        const editFormHTML = `
            <div class="edit-form-container mt-2 mb-3">
                <form class="space-y-3">
                    <textarea
                        class="textarea textarea-bordered w-full h-32 resize-none edit-body"
                        placeholder="Edit your comment..."
                        maxlength="2000"
                        required>${bodyText}</textarea>
                    <div class="flex items-center justify-between">
                        <span class="text-xs text-base-content/60">
                            <span class="edit-char-count">${bodyText.length}</span>/2000
                        </span>
                        <div class="flex gap-2">
                            <button type="button" class="btn btn-ghost btn-sm cancel-edit-btn">Cancel</button>
                            <button type="submit" class="btn btn-primary btn-sm submit-edit-btn">
                                <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
                                </svg>
                                Save Changes
                            </button>
                        </div>
                    </div>
                </form>
            </div>
        `;

        // Insert form after body
        bodyDiv.insertAdjacentHTML('afterend', editFormHTML);

        // Get the inserted form
        const form = commentEl.querySelector('.edit-form-container');
        const textarea = form.querySelector('.edit-body');
        const charCount = form.querySelector('.edit-char-count');
        const cancelBtn = form.querySelector('.cancel-edit-btn');
        const submitBtn = form.querySelector('.submit-edit-btn');

        // Character count
        textarea.addEventListener('input', () => {
            charCount.textContent = textarea.value.length;
        });

        // Cancel button
        cancelBtn.addEventListener('click', () => {
            form.remove();
            bodyDiv.classList.remove('hidden');
        });

        // Submit form
        form.querySelector('form').addEventListener('submit', async (e) => {
            e.preventDefault();
            await this.submitEdit(commentId, textarea.value, submitBtn, form, bodyDiv);
        });

        // Focus textarea and select all
        textarea.focus();
        textarea.select();
    }

    /**
     * Submit an edit to a comment
     */
    async submitEdit(commentId, body, submitBtn, formEl, bodyDiv) {
        if (!body || body.trim().length === 0) {
            this.showToast('Comment cannot be empty', 'error');
            return;
        }

        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="loading loading-spinner loading-sm"></span> Saving...';

        try {
            const response = await fetch(`/api/v1/comments/${commentId}/`, {
                method: 'PUT',
                headers: {
                    'X-CSRFToken': this.getCsrfToken(),
                    'Content-Type': 'application/json',
                },
                credentials: 'same-origin',
                body: JSON.stringify({ body: body.trim() })
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `HTTP ${response.status}`);
            }

            // Success - reload comments to show updated content
            this.showToast('Comment updated successfully!', 'success');
            formEl.remove();
            bodyDiv.classList.remove('hidden');
            this.currentOffset = 0;
            await this.loadComments(false);

        } catch (error) {
            console.error('Failed to edit comment:', error);
            this.showToast(error.message || 'Failed to edit comment', 'error');
            submitBtn.disabled = false;
            submitBtn.innerHTML = `
                <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
                </svg>
                Save Changes
            `;
        }
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
                },
                credentials: 'same-origin'
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `HTTP ${response.status}`);
            }

            this.showToast('Comment deleted successfully', 'success');
            this.currentOffset = 0;  // Reset pagination
            await this.loadComments(false);  // Full reload

        } catch (error) {
            console.error('Failed to delete comment:', error);
            this.showToast(error.message || 'Failed to delete comment', 'error');
        }
    }

    /**
     * Show report modal
     */
    showReportModal(commentId) {
        // Check if modal already exists
        let modal = document.getElementById('report-comment-modal');

        if (!modal) {
            // Create modal HTML
            const modalHTML = `
                <dialog id="report-comment-modal" class="modal">
                    <div class="modal-box">
                        <h3 class="font-bold text-lg mb-4">Report Comment</h3>

                        <form id="report-comment-form" class="space-y-4">
                            <div class="form-control">
                                <label class="label">
                                    <span class="label-text">Reason for reporting</span>
                                </label>
                                <select class="select select-bordered w-full" name="reason" required>
                                    <option value="" disabled selected>Select a reason</option>
                                    <option value="spam">Spam</option>
                                    <option value="harassment">Harassment or bullying</option>
                                    <option value="inappropriate">Inappropriate content</option>
                                    <option value="misinformation">Misinformation</option>
                                    <option value="other">Other</option>
                                </select>
                            </div>

                            <div class="form-control">
                                <label class="label">
                                    <span class="label-text">Additional details (optional)</span>
                                </label>
                                <textarea
                                    class="textarea textarea-bordered h-24"
                                    name="details"
                                    placeholder="Provide any additional context..."
                                    maxlength="500"></textarea>
                                <label class="label">
                                    <span class="label-text-alt"></span>
                                    <span class="label-text-alt report-details-count">0/500</span>
                                </label>
                            </div>

                            <div class="alert alert-info">
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="stroke-current shrink-0 w-6 h-6">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                                </svg>
                                <span class="text-sm">Reports are reviewed by moderators. False reports may result in action against your account.</span>
                            </div>

                            <div class="modal-action">
                                <button type="button" class="btn btn-ghost" onclick="document.getElementById('report-comment-modal').close()">Cancel</button>
                                <button type="submit" class="btn btn-error">Submit Report</button>
                            </div>
                        </form>
                    </div>
                    <form method="dialog" class="modal-backdrop">
                        <button>close</button>
                    </form>
                </dialog>
            `;

            // Insert modal into body
            document.body.insertAdjacentHTML('beforeend', modalHTML);
            modal = document.getElementById('report-comment-modal');

            // Character count for details
            const detailsTextarea = modal.querySelector('textarea[name="details"]');
            const detailsCount = modal.querySelector('.report-details-count');
            detailsTextarea.addEventListener('input', () => {
                detailsCount.textContent = `${detailsTextarea.value.length}/500`;
            });
        }

        // Store comment ID on the modal
        modal.dataset.commentId = commentId;

        // Reset form
        const form = modal.querySelector('#report-comment-form');
        form.reset();
        modal.querySelector('.report-details-count').textContent = '0/500';

        // Handle form submission
        form.onsubmit = async (e) => {
            e.preventDefault();
            await this.submitReport(commentId, form);
        };

        // Show modal (DaisyUI modal)
        modal.showModal();
    }

    /**
     * Submit a report for a comment
     */
    async submitReport(commentId, form) {
        const formData = new FormData(form);
        const reason = formData.get('reason');
        const details = formData.get('details') || '';

        const submitBtn = form.querySelector('button[type="submit"]');
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="loading loading-spinner loading-sm"></span> Submitting...';

        try {
            const response = await fetch(`/api/v1/comments/${commentId}/report/`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': this.getCsrfToken(),
                    'Content-Type': 'application/json',
                },
                credentials: 'same-origin',
                body: JSON.stringify({ reason, details })
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `HTTP ${response.status}`);
            }

            // Success
            this.showToast('Report submitted successfully. Thank you!', 'success');

            // Close modal
            const modal = document.getElementById('report-comment-modal');
            modal.close();

            // Update the report button to show "Reported" and disable it
            const reportBtn = document.querySelector(`.report-btn[data-comment-id="${commentId}"]`);
            if (reportBtn) {
                reportBtn.disabled = true;
                reportBtn.classList.remove('report-btn');
                reportBtn.innerHTML = `
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
                    </svg>
                    Reported
                `;
                reportBtn.setAttribute('aria-label', 'Already reported');
            }

        } catch (error) {
            console.error('Failed to submit report:', error);
            this.showToast(error.message || 'Failed to submit report', 'error');
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = 'Submit Report';
        }
    }

    /**
     * Update comment count badge
     * Note: Intentionally does nothing to prevent jarring count changes when loading comments
     */
    updateCommentCount() {
        // Don't update any count badges to prevent jarring changes
        // All counts (both game-level and trophy-level) should remain static at page load values
        // They only update when user creates a new comment (handled separately in comment creation flow)
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

        // Update error message (scoped to section type)
        const errorMessageEl = this.isTrophySection
            ? this.section.querySelector('.trophy-comments-error-message')
            : document.getElementById('comments-error-message');

        if (errorMessageEl) {
            errorMessageEl.textContent = message;
        }
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

// Community Guidelines handling
let hasAgreedToGuidelines = false;  // Will be set from data attribute in HTML
let pendingCommentSubmission = null;

// Initialize guidelines status from HTML data attribute
document.addEventListener('DOMContentLoaded', () => {
    const commentSection = document.getElementById('comment-section');
    if (commentSection && commentSection.dataset.guidelinesAgreed) {
        hasAgreedToGuidelines = commentSection.dataset.guidelinesAgreed === 'true';
    }
});

// Enable/disable the agree button based on checkbox state
document.addEventListener('DOMContentLoaded', () => {
    const agreeCheckbox = document.getElementById('agree-to-guidelines');
    const confirmBtn = document.getElementById('confirm-guidelines-btn');

    if (agreeCheckbox && confirmBtn) {
        agreeCheckbox.addEventListener('change', function() {
            confirmBtn.disabled = !this.checked;
        });
    }

    // Trophy comment agreement modal checkbox
    const trophyAgreeCheckbox = document.getElementById('trophy-agree-to-guidelines');
    const trophyConfirmBtn = document.getElementById('trophy-confirm-guidelines-btn');

    if (trophyAgreeCheckbox && trophyConfirmBtn) {
        trophyAgreeCheckbox.addEventListener('change', function() {
            trophyConfirmBtn.disabled = !this.checked;
        });
    }
});

// Check if user needs to agree to guidelines before commenting
function checkGuidelinesBeforeComment(callback) {
    if (hasAgreedToGuidelines) {
        // User has already agreed, proceed with comment
        callback();
    } else {
        // Store the callback to execute after agreement
        pendingCommentSubmission = callback;
        // Show the agreement modal
        document.getElementById('guidelines-agreement-modal').showModal();
    }
}

// Same for trophy comments
function checkTrophyGuidelinesBeforeComment(callback) {
    if (hasAgreedToGuidelines) {
        callback();
    } else {
        pendingCommentSubmission = callback;
        document.getElementById('trophy-guidelines-agreement-modal').showModal();
    }
}

// Handle guidelines agreement confirmation
async function confirmGuidelines() {
    const checkbox = document.getElementById('agree-to-guidelines');
    if (!checkbox.checked) {
        return;
    }

    try {
        // Send agreement to backend
        const response = await fetch('/api/v1/guidelines/agree/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfTokenFromPage()
            },
            credentials: 'same-origin'  // Include session cookie for authentication
        });

        const data = await response.json();

        if (data.success) {
            hasAgreedToGuidelines = true;
            document.getElementById('guidelines-agreement-modal').close();
            checkbox.checked = false;

            // Execute the pending comment submission
            if (pendingCommentSubmission) {
                pendingCommentSubmission();
                pendingCommentSubmission = null;
            }
        } else {
            alert(data.error || 'Failed to record agreement. Please try again.');
        }
    } catch (error) {
        console.error('Error agreeing to guidelines:', error);
        alert('An error occurred. Please try again.');
    }
}

// Handle trophy guidelines agreement confirmation
async function confirmTrophyGuidelines() {
    const checkbox = document.getElementById('trophy-agree-to-guidelines');
    if (!checkbox.checked) {
        return;
    }

    try {
        const response = await fetch('/api/v1/guidelines/agree/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfTokenFromPage()
            },
            credentials: 'same-origin'  // Include session cookie for authentication
        });

        const data = await response.json();

        if (data.success) {
            hasAgreedToGuidelines = true;
            document.getElementById('trophy-guidelines-agreement-modal').close();
            checkbox.checked = false;

            if (pendingCommentSubmission) {
                pendingCommentSubmission();
                pendingCommentSubmission = null;
            }
        } else {
            alert(data.error || 'Failed to record agreement. Please try again.');
        }
    } catch (error) {
        console.error('Error agreeing to guidelines:', error);
        alert('An error occurred. Please try again.');
    }
}

// Helper to get CSRF token
function getCsrfTokenFromPage() {
    return document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
           document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1] || '';
}

// Initialize comment system when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new CommentSystem();
});
