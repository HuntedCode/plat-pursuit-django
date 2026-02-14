/**
 * NotificationInboxManager - Gmail-style notification inbox
 * Handles split-pane layout, filtering, search, bulk actions, and infinite scroll
 * Depends on: PlatPursuit.API, PlatPursuit.ToastManager, PlatPursuit.TimeFormatter
 */
class NotificationInboxManager {
    constructor() {
        // DOM elements
        this.searchInput = document.getElementById('notification-search');
        this.filterType = document.getElementById('filter-type');
        this.filterStatus = document.getElementById('filter-status');
        this.listPane = document.getElementById('notification-list-pane');
        this.notificationItems = document.getElementById('notification-items');
        this.detailEmpty = document.getElementById('detail-empty');
        this.detailContent = document.getElementById('detail-content');
        this.selectAllCheckbox = document.getElementById('select-all');
        this.bulkMarkReadBtn = document.getElementById('bulk-mark-read');
        this.bulkDeleteBtn = document.getElementById('bulk-delete');
        this.listLoading = document.getElementById('list-loading');
        this.listEmpty = document.getElementById('list-empty');
        this.scrollLoader = document.getElementById('scroll-loader');
        this.countBadge = document.getElementById('notification-count');

        // State
        this.notifications = [];
        this.selectedIds = new Set();
        this.currentFilters = { type: '', status: '', search: '' };
        this.hasMore = true;
        this.loading = false;
        this.selectedNotification = null;
        this.debouncedSearch = PlatPursuit.debounce((value) => this.handleSearch(value), 300);
        this.currentShareManager = null;  // Reference to active ShareImageManager for refreshing after rating changes

        // Check if elements exist before initializing
        if (!this.listPane) {
            console.error('Inbox elements not found');
            return;
        }

        this.init();
    }

    init() {
        this.attachEventListeners();
        this.loadNotifications().then(() => {
            this.checkForNotificationIdParam();
        });
        this.setupInfiniteScroll();
    }

    checkForNotificationIdParam() {
        // Check if there's an 'id' query parameter and auto-select that notification
        const urlParams = new URLSearchParams(window.location.search);
        const notificationId = urlParams.get('id');

        if (notificationId) {
            const id = parseInt(notificationId);
            const notification = this.notifications.find(n => n.id === id);

            if (notification) {
                this.selectNotification(notification);
            } else {
                // Notification not in current list - fetch it directly
                this.fetchAndSelectNotification(id);
            }

            // Clean up the URL without the query parameter
            const url = new URL(window.location);
            url.searchParams.delete('id');
            window.history.replaceState({}, '', url);
        }
    }

    async fetchAndSelectNotification(notificationId) {
        try {
            const data = await PlatPursuit.API.get(`/api/v1/notifications/${notificationId}/`);
            if (data) {
                this.selectNotification(data);
            }
        } catch (error) {
            console.error('Failed to fetch notification:', error);
            PlatPursuit.ToastManager.error('Failed to load notification');
        }
    }

    attachEventListeners() {
        // Search with 300ms debounce
        this.searchInput.addEventListener('input', (e) => {
            this.debouncedSearch(e.target.value);
        });

        // Filter changes
        this.filterType.addEventListener('change', () => this.handleFilterChange());
        this.filterStatus.addEventListener('change', () => this.handleFilterChange());

        // Select all checkbox
        this.selectAllCheckbox.addEventListener('change', (e) => {
            this.handleSelectAll(e.target.checked);
        });

        // Bulk actions
        this.bulkMarkReadBtn.addEventListener('click', () => this.handleBulkMarkRead());
        this.bulkDeleteBtn.addEventListener('click', () => this.handleBulkDelete());

        // Event delegation for notification clicks and checkboxes
        this.notificationItems.addEventListener('click', (e) => {
            // Check if clicked element is a checkbox
            if (e.target.classList.contains('notification-checkbox')) {
                const card = e.target.closest('.notification-card');
                const notifId = parseInt(card.dataset.notificationId);
                this.handleCheckboxChange(notifId, e.target.checked);
                e.stopPropagation(); // Prevent card selection
            } else {
                // Card clicked (not checkbox)
                const card = e.target.closest('.notification-card');
                if (card) {
                    const notifId = parseInt(card.dataset.notificationId);
                    const notification = this.notifications.find(n => n.id === notifId);
                    if (notification) {
                        this.selectNotification(notification);
                    }
                }
            }
        });
    }

    async loadNotifications(append = false) {
        if (this.loading) return;

        this.loading = true;
        if (!append) {
            this.showLoading();
        } else {
            this.scrollLoader.classList.remove('hidden');
        }

        try {
            const params = new URLSearchParams({
                limit: 25,
                offset: append ? this.notifications.length : 0,
                type: this.currentFilters.type,
                unread_only: this.currentFilters.status === 'unread'
            });

            const data = await PlatPursuit.API.get(`/api/v1/notifications/?${params}`);

            if (append) {
                this.notifications.push(...data.notifications);
            } else {
                this.notifications = data.notifications;
            }

            this.hasMore = data.notifications.length === 25;
            this.renderNotificationList();
            this.updateCount(data.total_count);

            this.hideLoading();

            // Only hide scroll loader if there are no more notifications
            if (!this.hasMore) {
                this.scrollLoader.classList.add('hidden');
            } else {
                this.scrollLoader.classList.remove('hidden');
            }

            // Apply search filter if active
            if (this.currentFilters.search) {
                this.applySearchFilter();
            }

        } catch (error) {
            console.error('Failed to load notifications:', error);
            PlatPursuit.ToastManager.error('Failed to load notifications');
            this.hideLoading();
        } finally {
            this.loading = false;
        }
    }

    renderNotificationList() {
        if (this.notifications.length === 0) {
            this.showEmpty();
            return;
        }

        this.hideEmpty();

        const html = this.notifications.map(n => this.renderNotificationCard(n)).join('');
        this.notificationItems.innerHTML = html;

        // Restore selection state
        this.selectedIds.forEach(id => {
            const checkbox = this.notificationItems.querySelector(`[data-notification-id="${id}"] .notification-checkbox`);
            if (checkbox) {
                checkbox.checked = true;
            }
        });

        this.updateSelectionUI();
    }

    renderNotificationCard(notification) {
        const isUnread = !notification.is_read;
        const unreadClass = isUnread ? 'bg-primary/10 border-l-4 border-primary' : 'opacity-70';
        const isSelected = this.selectedIds.has(notification.id);
        const timeStr = PlatPursuit.TimeFormatter.relative(notification.created_at);

        // Get icon for notification type
        const priorityBadge = notification.priority === 'high' || notification.priority === 'urgent'
            ? `<span class="badge badge-error badge-xs">${notification.priority}</span>`
            : '';

        return `
            <div class="notification-card p-3 rounded-lg hover:bg-base-300 cursor-pointer transition-colors ${unreadClass}"
                 data-notification-id="${notification.id}"
                 data-read="${notification.is_read}">
                <div class="flex items-start gap-3">
                    <!-- Checkbox -->
                    <input type="checkbox"
                           class="notification-checkbox checkbox checkbox-primary checkbox-sm mt-1"
                           ${isSelected ? 'checked' : ''} />

                    <!-- Icon -->
                    <div class="text-2xl flex-shrink-0">${notification.icon}</div>

                    <!-- Content -->
                    <div class="flex-1 min-w-0">
                        <div class="flex items-start justify-between gap-2">
                            <h4 class="font-semibold text-sm truncate flex-1">${notification.title}</h4>
                            ${priorityBadge}
                        </div>
                        <p class="text-xs text-base-content/70 line-clamp-2 mt-1">${notification.message}</p>
                        <p class="text-xs text-base-content/50 mt-1">${timeStr}</p>
                    </div>

                    <!-- Unread indicator -->
                    ${isUnread ? '<div class="w-2 h-2 rounded-full bg-primary flex-shrink-0 mt-2"></div>' : ''}
                </div>
            </div>
        `;
    }

    selectNotification(notification) {
        this.selectedNotification = notification;
        this.renderNotificationDetail(notification);

        // Mark as read if unread
        if (!notification.is_read) {
            this.markAsRead(notification.id);
        }

        // Highlight selected card
        const cards = this.notificationItems.querySelectorAll('.notification-card');
        cards.forEach(card => {
            if (parseInt(card.dataset.notificationId) === notification.id) {
                card.classList.add('ring-2', 'ring-primary');
            } else {
                card.classList.remove('ring-2', 'ring-primary');
            }
        });

        // Initialize share image manager and rating section for platinum notifications
        if (notification.notification_type === 'platinum_earned') {
            // Use setTimeout to ensure DOM is rendered
            setTimeout(() => {
                // Initialize share image manager
                const shareContainer = document.getElementById('share-section-container');
                if (shareContainer && window.PlatPursuit && window.PlatPursuit.ShareImageManager) {
                    this.currentShareManager = new window.PlatPursuit.ShareImageManager(
                        notification.id,
                        notification.metadata
                    );
                    shareContainer.innerHTML = this.currentShareManager.renderShareSection();
                    this.currentShareManager.init();
                }

                // Load rating section if game has a concept
                if (notification.metadata?.concept_id) {
                    this.loadRatingSection(notification.id);
                }
            }, 50);
        }
    }

    async loadRatingSection(notificationId, justRated = false) {
        const container = document.getElementById('rating-section-container');
        if (!container) return;

        // Show loading state
        container.innerHTML = `
            <div class="flex justify-center items-center p-8">
                <span class="loading loading-spinner loading-lg text-primary"></span>
            </div>
        `;

        try {
            const url = justRated
                ? `/api/v1/notifications/${notificationId}/rating/?just_rated=1`
                : `/api/v1/notifications/${notificationId}/rating/`;
            const response = await PlatPursuit.API.get(url);
            container.innerHTML = response.html;
            this.initRatingForm(notificationId);
        } catch (error) {
            console.error('Failed to load rating section:', error);
            // Silently fail - don't show error to user, just hide the section
            container.innerHTML = '';
        }
    }

    initRatingForm(notificationId) {
        const form = document.getElementById('notification-rating-form');
        if (!form) return;

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            await this.submitRating(notificationId, new FormData(form));
        });
    }

    async submitRating(notificationId, formData) {
        const submitBtn = document.querySelector('#notification-rating-form button[type="submit"]');
        const originalText = submitBtn?.textContent;

        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span class="loading loading-spinner loading-sm"></span> Submitting...';
        }

        try {
            // Convert FormData to object
            const data = {};
            formData.forEach((value, key) => {
                // Parse numeric values
                if (['difficulty', 'grindiness', 'hours_to_platinum', 'fun_ranking'].includes(key)) {
                    data[key] = parseInt(value);
                } else if (key === 'overall_rating') {
                    data[key] = parseFloat(value);
                } else {
                    data[key] = value;
                }
            });

            const response = await PlatPursuit.API.post(`/api/v1/notifications/${notificationId}/rating/`, data);

            if (response.success) {
                PlatPursuit.ToastManager.success(response.message || 'Rating submitted!');
                // Reload the rating section, passing whether this was a first-time rating
                const wasFirstRating = response.message && response.message.includes('submitted');
                this.loadRatingSection(notificationId, wasFirstRating);

                // Refresh the share image preview with the new rating data
                if (this.currentShareManager) {
                    this.currentShareManager.renderPreview();
                }
            } else {
                PlatPursuit.ToastManager.error('Failed to submit rating. Please check your input.');
            }
        } catch (error) {
            console.error('Failed to submit rating:', error);
            PlatPursuit.ToastManager.error('Failed to submit rating. Please try again.');
        } finally {
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
            }
        }
    }

    renderNotificationDetail(notification) {
        this.detailEmpty.classList.add('hidden');
        this.detailContent.classList.remove('hidden');

        const timeStr = PlatPursuit.TimeFormatter.relative(notification.created_at);
        const absoluteTime = PlatPursuit.TimeFormatter.absolute(notification.created_at);

        // Priority badge styling
        let priorityClass = 'badge-info';
        if (notification.priority === 'high') priorityClass = 'badge-warning';
        if (notification.priority === 'urgent') priorityClass = 'badge-error';

        const actionButton = notification.action_url
            ? `<a href="${notification.action_url}" class="btn btn-primary btn-sm">
                ${notification.action_text || 'View'}
               </a>`
            : '';

        // Banner image section
        const bannerHtml = notification.banner_image
            ? `<div class="w-full overflow-hidden rounded-lg mb-4">
                 <img src="${notification.banner_image}"
                      alt="Notification Banner"
                      class="w-full h-auto object-contain"
                      loading="lazy"
                      onerror="this.style.display='none'" />
               </div>`
            : '';

        // Detail content section - check for structured sections or markdown
        let detailHtml = '';
        const hasStructuredSections = notification.sections && notification.sections.length > 0;

        if (hasStructuredSections) {
            // Render structured sections
            detailHtml = this.renderStructuredSections(notification.sections);
        } else if (notification.detail) {
            // Fall back to markdown
            detailHtml = `<div class="prose prose-sm max-w-none bg-base-200 rounded-lg p-4 mt-4">
                            ${this.renderMarkdown(notification.detail)}
                          </div>`;
        }

        // Render type-specific enhanced content
        let enhancedContent = '';
        const metadata = notification.metadata || {};

        switch (notification.notification_type) {
            case 'platinum_earned':
                enhancedContent = this.renderPlatinumDetail(metadata);
                break;
            case 'badge_awarded':
                enhancedContent = this.renderBadgeDetail(metadata);
                break;
            case 'milestone_achieved':
                enhancedContent = this.renderMilestoneDetail(metadata);
                break;
            case 'admin_announcement':
                enhancedContent = this.renderAdminAnnouncementDetail(metadata);
                break;
            case 'monthly_recap':
                enhancedContent = this.renderMonthlyRecapDetail(metadata);
                break;
            case 'challenge_completed':
                enhancedContent = this.renderChallengeDetail(metadata);
                break;
            default:
                enhancedContent = '';
        }

        this.detailContent.innerHTML = `
            <div class="p-6 space-y-4">
                <!-- Banner Image -->
                ${bannerHtml}

                <!-- Header -->
                <div class="flex items-start justify-between gap-4">
                    <div class="flex items-start gap-3 flex-1">
                        <div class="text-3xl">${notification.icon}</div>
                        <div class="flex-1">
                            <h2 class="text-xl font-bold">${notification.title}</h2>
                            <div class="flex flex-wrap items-center gap-2 mt-1">
                                <span class="badge ${priorityClass} badge-sm">${notification.priority}</span>
                                <span class="text-xs text-base-content/60">${timeStr}</span>
                                <span class="text-xs text-base-content/40" title="${absoluteTime}">${absoluteTime}</span>
                            </div>
                        </div>
                    </div>

                    <!-- Delete Button -->
                    <div class="flex gap-2">
                        <button class="btn btn-sm btn-ghost btn-circle"
                                onclick="window.notificationInbox.deleteNotification(${notification.id})"
                                title="Delete">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                        </button>
                    </div>
                </div>

                <!-- Message -->
                <div class="prose prose-sm max-w-none">
                    <p class="text-base whitespace-pre-wrap">${notification.message}</p>
                </div>

                <!-- Detail Content (new) -->
                ${detailHtml}

                <!-- Enhanced Type-Specific Content -->
                ${enhancedContent}

                <!-- Action Button -->
                ${actionButton ? `<div class="pt-2">${actionButton}</div>` : ''}

                <!-- Type Info Footer -->
                <div class="text-xs text-base-content/50 pt-4 border-t border-base-300">
                    <p>Type: ${notification.notification_type.replace('_', ' ')}</p>
                    ${notification.read_at ? `<p>Read: ${PlatPursuit.TimeFormatter.relative(notification.read_at)}</p>` : ''}
                </div>
            </div>
        `;
    }

    renderMarkdown(markdownText) {
        if (!markdownText) return '';
        try {
            // Use marked.js to convert markdown to HTML
            if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
                const rawHtml = marked.parse(markdownText);
                // Sanitize output to prevent XSS attacks
                return DOMPurify.sanitize(rawHtml);
            } else {
                // Fallback: just escape HTML and preserve line breaks
                const div = document.createElement('div');
                div.textContent = markdownText;
                return div.innerHTML.replace(/\n/g, '<br>');
            }
        } catch (error) {
            console.error('Markdown rendering failed:', error);
            // Fallback to plain text with line breaks
            const div = document.createElement('div');
            div.textContent = markdownText;
            return div.innerHTML.replace(/\n/g, '<br>');
        }
    }

    renderStructuredSections(sections) {
        if (!sections || sections.length === 0) return '';

        const sectionHtml = sections
            .sort((a, b) => a.order - b.order)
            .map(section => {
                const formatted = this.formatStructuredContent(section.content);

                return `
                    <div class="bg-base-200 rounded-lg p-4 border-l-4 border-primary hover:border-secondary transition-colors">
                        <div class="flex items-center gap-3 mb-3 pb-2 border-b border-base-300">
                            <span class="text-2xl">${this.escapeHtml(section.icon)}</span>
                            <h3 class="font-semibold text-base">${this.escapeHtml(section.header)}</h3>
                        </div>
                        <div class="prose prose-sm max-w-none">${formatted}</div>
                    </div>
                `;
            }).join('');

        return `<div class="space-y-4 mt-4">${sectionHtml}</div>`;
    }

    formatStructuredContent(text) {
        if (!text) return '';

        let formatted = this.escapeHtml(text);

        // Bold: *text*
        formatted = formatted.replace(/\*([^*]+)\*/g, '<strong>$1</strong>');

        // Italic: _text_
        formatted = formatted.replace(/_([^_]+)_/g, '<em>$1</em>');

        // Code: `text`
        formatted = formatted.replace(/`([^`]+)`/g, '<code class="bg-base-100 px-1 rounded">$1</code>');

        // Links: [text](url)
        formatted = formatted.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
            '<a href="$2" class="link link-primary">$1</a>');

        // Bullet lists: - item
        const lines = formatted.split('\n');
        let result = '';
        let inList = false;

        for (let line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('- ')) {
                if (!inList) {
                    result += '<ul class="list-disc list-inside my-1">';
                    inList = true;
                }
                result += `<li>${trimmed.substring(2)}</li>`;
            } else {
                if (inList) {
                    result += '</ul>';
                    inList = false;
                }
                if (trimmed) {
                    result += `<p class="my-1">${line}</p>`;
                }
            }
        }

        if (inList) result += '</ul>';

        return result;
    }

    escapeHtml(text) {
        return PlatPursuit.HTMLUtils.escape(text);
    }

    renderPlatinumDetail(metadata) {
        // Gracefully handle missing metadata
        if (!metadata) {
            return '';
        }

        // Parse and validate all fields with safe fallbacks
        const earnRate = parseFloat(metadata.trophy_earn_rate);
        const hasEarnRate = !isNaN(earnRate) && earnRate >= 0;

        const rarityLabel = metadata.rarity_label || null;
        const trophyDetail = metadata.trophy_detail || null;
        const trophyIcon = metadata.trophy_icon_url || null;
        const gameImage = metadata.game_image || null;
        const trophyRarity = parseInt(metadata.trophy_rarity);

        // Only show enhanced content if we have at least some data
        const hasEnhancedData = trophyDetail || gameImage || trophyIcon || hasEarnRate || rarityLabel;
        if (!hasEnhancedData) {
            return ''; // No enhanced data available
        }

        // Rarity color coding
        let rarityColorClass = 'badge-info';
        if (!isNaN(trophyRarity)) {
            if (trophyRarity === 0) rarityColorClass = 'badge-error'; // Ultra Rare
            else if (trophyRarity === 1) rarityColorClass = 'badge-warning'; // Very Rare
            else if (trophyRarity === 2) rarityColorClass = 'badge-accent'; // Rare
        }

        return `
            <div class="bg-base-300 rounded-lg p-4 space-y-4">
                <!-- Game Image -->
                ${gameImage ? `
                    <div class="w-full overflow-hidden rounded-lg">
                        <img src="${gameImage}"
                             alt="Game Banner"
                             class="w-full h-auto object-cover max-h-48"
                             loading="lazy"
                             onerror="this.style.display='none'" />
                    </div>
                ` : ''}

                <!-- Trophy Details Section -->
                ${(trophyIcon || trophyDetail || rarityLabel || hasEarnRate) ? `
                    <div class="space-y-3">
                        ${(trophyIcon || trophyDetail) ? `
                            <h3 class="text-lg font-semibold flex items-center gap-2">
                                ${trophyIcon ? `<img src="${trophyIcon}" alt="Trophy Icon" class="w-8 h-8 object-cover" loading="lazy" onerror="this.style.display='none'" />` : ''}
                                Trophy Details
                            </h3>
                        ` : ''}

                        <!-- Rarity Badge -->
                        ${(rarityLabel || hasEarnRate) ? `
                            <div class="flex items-center gap-2">
                                ${rarityLabel ? `
                                    <span class="badge ${rarityColorClass} badge-lg gap-2">
                                        ${rarityLabel}
                                    </span>
                                ` : ''}
                                ${hasEarnRate ? `
                                    <span class="text-sm text-base-content/70">
                                        Only ${earnRate.toFixed(1)}% of players earned this
                                    </span>
                                ` : ''}
                            </div>
                        ` : ''}

                        <!-- Trophy Description -->
                        ${trophyDetail ? `
                            <div class="bg-base-100 rounded-lg p-3">
                                <p class="text-sm text-base-content/80 whitespace-pre-wrap">${trophyDetail}</p>
                            </div>
                        ` : ''}
                    </div>
                ` : ''}
            </div>

            <!-- Play Statistics Section -->
            ${this.renderPlayStatistics(metadata)}

            <!-- Share Image Section Container -->
            <div id="share-section-container"></div>

            <!-- Rating Section Container (loaded via API) -->
            <div id="rating-section-container" class="mt-4"></div>
        `;
    }

    renderPlayStatistics(metadata) {
        const playDuration = metadata.play_duration_seconds;
        const firstPlayed = metadata.first_played_date_time;
        const earnedDate = metadata.earned_date_time;
        const earnedTrophies = metadata.earned_trophies_count;
        const totalTrophies = metadata.total_trophies_count;
        const progress = metadata.progress_percentage;

        // Only show if we have at least some play data
        if (!playDuration && !firstPlayed && !earnedDate) {
            return '';
        }

        // Format play time
        let playtimeStr = '';
        if (playDuration) {
            const hours = Math.floor(playDuration / 3600);
            const minutes = Math.floor((playDuration % 3600) / 60);
            playtimeStr = hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
        }

        return `
            <div class="bg-base-300 rounded-lg p-4 mt-4">
                <h3 class="text-lg font-semibold mb-3 flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10"></circle>
                        <polyline points="12 6 12 12 16 14"></polyline>
                    </svg>
                    Your Play Statistics
                </h3>
                <div class="grid grid-cols-2 gap-3">
                    ${playtimeStr ? `
                        <div class="bg-base-100 rounded-lg p-3">
                            <div class="text-xs text-base-content/60 uppercase tracking-wide">Play Time</div>
                            <div class="text-lg font-bold text-primary">${playtimeStr}</div>
                        </div>
                    ` : ''}
                    ${firstPlayed ? `
                        <div class="bg-base-100 rounded-lg p-3">
                            <div class="text-xs text-base-content/60 uppercase tracking-wide">First Played</div>
                            <div class="text-lg font-bold">${this.formatDate(firstPlayed)}</div>
                        </div>
                    ` : ''}
                    ${earnedDate ? `
                        <div class="bg-base-100 rounded-lg p-3">
                            <div class="text-xs text-base-content/60 uppercase tracking-wide">Platinum Earned</div>
                            <div class="text-lg font-bold text-warning">${this.formatDate(earnedDate)}</div>
                        </div>
                    ` : ''}
                    ${earnedTrophies && totalTrophies ? `
                        <div class="bg-base-100 rounded-lg p-3">
                            <div class="text-xs text-base-content/60 uppercase tracking-wide">Trophies</div>
                            <div class="text-lg font-bold">${earnedTrophies}/${totalTrophies}</div>
                            <div class="text-xs text-base-content/60">${progress || 0}% complete</div>
                        </div>
                    ` : ''}
                </div>
            </div>
        `;
    }

    formatDate(isoString) {
        if (!isoString) return '';
        try {
            const date = new Date(isoString);
            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
        } catch {
            return '';
        }
    }

    renderBadgeDetail(metadata) {
        // Gracefully handle missing metadata
        if (!metadata) {
            return '';
        }

        const tier = parseInt(metadata.badge_tier) || null;
        const tierName = metadata.badge_tier_name || null;
        const description = metadata.badge_description || null;
        const series = metadata.badge_series || null;
        const seriesSlug = metadata.series_slug || null;
        const badgeLayers = metadata.badge_layers || null;
        const badgeImage = metadata.badge_image_url || null;
        const isMaxTier = metadata.is_max_tier || false;
        const nextTierProgress = metadata.next_tier_progress || null;
        const stages = metadata.stages || [];
        const seriesXp = metadata.series_xp || 0;
        const totalXp = metadata.total_xp || 0;

        // Only show enhanced content if we have at least some data
        const hasEnhancedData = description || badgeLayers || badgeImage || tierName || series;
        if (!hasEnhancedData) {
            return '';
        }

        // Get tier color class
        const tierColorClass = this.getTierColorClass(tier);

        return `
            <div class="bg-base-300 rounded-lg p-4 space-y-4">
                <!-- Congratulations Header -->
                <div class="text-center">
                    <h3 class="text-lg font-bold text-primary">Congratulations!</h3>
                    <p class="text-sm text-base-content/70">You've earned a new badge!</p>
                </div>

                <!-- Badge Visual with Layers -->
                ${badgeLayers ? this.renderBadgeLayers(badgeLayers, tierName) : (badgeImage ? `
                    <div class="flex justify-center">
                        <img src="${badgeImage}"
                             alt="${tierName} Badge"
                             class="w-32 h-32 object-contain"
                             loading="lazy"
                             onerror="this.style.display='none'" />
                    </div>
                ` : '')}

                <!-- Badge Details -->
                <div class="space-y-3">
                    <div class="flex items-center justify-center gap-2">
                        <span class="badge ${tierColorClass} badge-lg">${tierName} Tier</span>
                        ${series ? `<span class="text-sm text-base-content/70">${series}</span>` : ''}
                    </div>

                    <!-- Badge Description -->
                    ${description ? `
                        <div class="bg-base-100 rounded-lg p-3">
                            <p class="text-sm text-base-content/80 whitespace-pre-wrap">${description}</p>
                        </div>
                    ` : ''}
                </div>

                <!-- Badge XP Stats -->
                ${this.renderBadgeXpStats(seriesXp, totalXp, series)}

                <!-- Next Tier Progress (if not max tier) -->
                ${!isMaxTier && nextTierProgress ? this.renderNextTierProgress(nextTierProgress) : ''}

                <!-- Stage Completion List (if not max tier and has stages) -->
                ${!isMaxTier && stages.length > 0 ? this.renderStageList(stages, seriesSlug) : ''}

                <!-- Max Tier Achievement Message -->
                ${isMaxTier ? `
                    <div class="bg-gradient-to-r from-amber-500/20 to-yellow-500/20 rounded-lg p-4 text-center">
                        <span class="text-2xl">&#127942;</span>
                        <p class="text-sm font-semibold text-warning mt-1">Maximum Tier Achieved!</p>
                        <p class="text-xs text-base-content/60">You've mastered this badge series</p>
                    </div>
                ` : ''}
            </div>
        `;
    }

    /**
     * Render composited badge with all layers (backdrop, main, foreground)
     * Mirrors the CSS structure from templates/partials/badge.html
     */
    renderBadgeLayers(layers, tierName) {
        const staticPrefix = '/static/';

        // Determine if main is already absolute (media URL) or needs static prefix
        const mainUrl = layers.main && layers.main.startsWith('/')
            ? layers.main  // Already absolute (media URL)
            : `${staticPrefix}${layers.main}`;  // Static path

        const backdropUrl = `${staticPrefix}${layers.backdrop}`;
        const foregroundUrl = layers.foreground
            ? `${staticPrefix}${layers.foreground}`
            : null;

        return `
            <div class="flex justify-center">
                <div class="relative w-32 h-32 rounded-box overflow-hidden">
                    <!-- Backdrop layer -->
                    <img src="${backdropUrl}"
                         alt="Badge Backdrop"
                         class="absolute inset-0 w-full h-full object-cover"
                         loading="lazy" />
                    <!-- Main badge layer -->
                    <img src="${mainUrl}"
                         alt="${tierName || 'Badge'}"
                         class="absolute inset-0 w-full h-full object-cover"
                         loading="lazy"
                         onerror="this.style.display='none'" />
                    ${foregroundUrl ? `
                        <!-- Foreground layer -->
                        <img src="${foregroundUrl}"
                             alt="Badge Foreground"
                             class="absolute inset-0 w-full h-full object-cover"
                             loading="lazy" />
                    ` : ''}
                </div>
            </div>
        `;
    }

    /**
     * Render progress towards next badge tier
     */
    renderNextTierProgress(progress) {
        const tierColorClass = this.getTierColorClass(progress.tier);
        const tierBgClass = this.getTierBgClass(progress.tier);

        return `
            <div class="bg-base-100 rounded-lg p-4">
                <h4 class="text-sm font-semibold mb-2 flex items-center gap-2">
                    <span>Progress to ${progress.tier_name} Tier</span>
                    <span class="badge ${tierColorClass} badge-sm">${progress.progress_percentage}%</span>
                </h4>
                <div class="w-full bg-base-300 rounded-full h-3 mb-2">
                    <div class="h-3 rounded-full transition-all duration-500 ${tierBgClass}"
                         style="width: ${progress.progress_percentage}%"></div>
                </div>
                <p class="text-xs text-base-content/60 text-center">
                    ${progress.completed_stages} / ${progress.required_stages} stages complete
                </p>
            </div>
        `;
    }

    /**
     * Render Badge XP statistics
     */
    renderBadgeXpStats(seriesXp, totalXp, seriesName) {
        // Format XP with commas
        const formatXp = (xp) => xp.toLocaleString();

        return `
            <div class="bg-base-100 rounded-lg p-4">
                <h4 class="text-sm font-semibold mb-3 flex items-center gap-2">
                    <svg class="w-4 h-4 text-primary" fill="currentColor" viewBox="0 0 20 20">
                        <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"/>
                    </svg>
                    Badge XP Earned
                </h4>
                <div class="grid grid-cols-2 gap-3">
                    <div class="bg-base-300 rounded-lg p-3 text-center">
                        <div class="text-xs text-base-content/60 uppercase tracking-wide mb-1">
                            ${seriesName || 'This Series'}
                        </div>
                        <div class="text-lg font-bold text-primary">${formatXp(seriesXp)} XP</div>
                    </div>
                    <div class="bg-base-300 rounded-lg p-3 text-center">
                        <div class="text-xs text-base-content/60 uppercase tracking-wide mb-1">Total Badge XP</div>
                        <div class="text-lg font-bold text-accent">${formatXp(totalXp)} XP</div>
                    </div>
                </div>
            </div>
        `;
    }

    /**
     * Render stage list with completion checkmarks and clickable links
     */
    renderStageList(stages, seriesSlug) {
        // Build the badge detail URL with stage anchor
        const getBadgeStageUrl = (stageNumber) => {
            if (!seriesSlug) return null;
            return `/badges/${seriesSlug}/#stage-${stageNumber}`;
        };

        const stageItems = stages.map(stage => {
            // Format stage title - avoid duplication like "Stage 1: Stage 1"
            const displayTitle = stage.stage_title && stage.stage_title !== `Stage ${stage.stage_number}`
                ? `Stage ${stage.stage_number}: ${stage.stage_title}`
                : `Stage ${stage.stage_number}`;

            const stageUrl = getBadgeStageUrl(stage.stage_number);
            const isClickable = !!stageUrl;

            return `
                <a href="${stageUrl || '#'}"
                   class="flex items-center gap-3 p-2 rounded-lg ${stage.is_complete ? 'bg-success/10' : 'bg-base-200'} ${isClickable ? 'hover:bg-base-300 cursor-pointer transition-colors' : ''}"
                   ${isClickable ? '' : 'onclick="event.preventDefault()"'}>
                    <!-- Stage Icon -->
                    <div class="w-10 h-10 flex-shrink-0 rounded-lg overflow-hidden bg-base-300 flex items-center justify-center">
                        ${stage.stage_icon
                            ? `<img src="${stage.stage_icon}" alt="Stage ${stage.stage_number}" class="w-full h-full object-cover" loading="lazy" onerror="this.parentElement.innerHTML='<span class=\\'text-lg\\'>${stage.stage_number}</span>'" />`
                            : `<span class="text-lg font-bold text-base-content/60">${stage.stage_number}</span>`
                        }
                    </div>

                    <!-- Stage Info -->
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-medium truncate">${displayTitle}</p>
                    </div>

                    <!-- Completion Status -->
                    <div class="flex-shrink-0">
                        ${stage.is_complete
                            ? `<svg class="w-5 h-5 text-success" fill="currentColor" viewBox="0 0 20 20">
                                 <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/>
                               </svg>`
                            : `<div class="w-5 h-5 rounded-full border-2 border-base-content/30"></div>`
                        }
                    </div>
                </a>
            `;
        }).join('');

        return `
            <div class="bg-base-100 rounded-lg p-4">
                <h4 class="text-sm font-semibold mb-3">Stage Progress</h4>
                <div class="space-y-2 max-h-64 overflow-y-auto">
                    ${stageItems}
                </div>
            </div>
        `;
    }

    /**
     * Get tier-specific color class for badges/labels
     */
    getTierColorClass(tier) {
        switch (tier) {
            case 1: return 'badge-warning';      // Bronze - amber/warning
            case 2: return 'badge-info';         // Silver - blue/info
            case 3: return 'badge-warning';      // Gold - amber/warning
            case 4: return 'badge-secondary';    // Platinum - purple/secondary
            default: return 'badge-accent';
        }
    }

    /**
     * Get tier-specific background class for progress bars
     */
    getTierBgClass(tier) {
        switch (tier) {
            case 1: return 'bg-amber-600';       // Bronze
            case 2: return 'bg-slate-400';       // Silver
            case 3: return 'bg-yellow-500';      // Gold
            case 4: return 'bg-purple-500';      // Platinum
            default: return 'bg-primary';
        }
    }

    /**
     * Get progress bar color class based on percentage.
     * Uses the same color scheme as milestone_list_items.html template.
     *
     * @param {number} percentage - Progress percentage (0-100)
     * @returns {string} Tailwind CSS background class
     */
    getProgressColorClass(percentage) {
        if (percentage >= 100) return 'bg-success';
        if (percentage >= 66) return 'bg-info';
        if (percentage >= 33) return 'bg-warning';
        return 'bg-error';
    }

    /**
     * Render congratulations header for achievement notifications.
     * Reusable for badges, milestones, and other achievements.
     *
     * @param {string} title - Main title (e.g., "Congratulations!")
     * @param {string} subtitle - Subtitle text
     * @returns {string} HTML for congratulations header
     */
    renderCongratsHeader(title, subtitle) {
        return `
            <div class="text-center">
                <h3 class="text-lg font-bold text-primary">${title}</h3>
                <p class="text-sm text-base-content/70">${subtitle}</p>
            </div>
        `;
    }

    renderMilestoneDetail(metadata) {
        // Gracefully handle missing metadata
        if (!metadata) {
            return '';
        }

        const milestoneName = metadata.milestone_name || 'Milestone';
        const description = metadata.milestone_description || null;
        const milestoneImage = metadata.milestone_image || null;
        const criteria = metadata.milestone_criteria || null;
        const target = parseFloat(metadata.milestone_target);
        const hasTarget = !isNaN(target) && target > 0;

        // New enhanced fields
        const nextMilestone = metadata.next_milestone || null;
        const currentTier = metadata.current_tier || null;
        const totalTiers = metadata.total_tiers || null;
        const isMaxTier = metadata.is_max_tier || false;
        const isOneOff = metadata.is_one_off || false;

        // Only show enhanced content if we have at least some data
        const hasEnhancedData = description || milestoneImage || hasTarget || milestoneName;
        if (!hasEnhancedData) {
            return '';
        }

        return `
            <div class="bg-base-300 rounded-lg p-4 space-y-4">
                <!-- Congratulations Header -->
                ${this.renderCongratsHeader('Congratulations!', 'You\'ve earned a new milestone!')}

                <!-- Milestone Image -->
                ${milestoneImage ? `
                    <div class="flex justify-center">
                        <img src="${milestoneImage}"
                             alt="${milestoneName}"
                             class="w-32 h-32 object-contain rounded-lg"
                             loading="lazy"
                             onerror="this.style.display='none'" />
                    </div>
                ` : ''}

                <!-- Milestone Details -->
                <div class="space-y-3">
                    <!-- Tier and Criteria Info -->
                    <div class="flex items-center justify-center gap-2 flex-wrap">
                        ${hasTarget ? `
                            <span class="badge badge-success badge-lg">
                                ${target.toLocaleString()}${criteria ? ` ${criteria}` : ''}
                            </span>
                        ` : ''}
                        ${currentTier && totalTiers ? `
                            <span class="badge badge-outline badge-sm">
                                Tier ${currentTier} of ${totalTiers}
                            </span>
                        ` : ''}
                    </div>

                    <!-- Milestone Description -->
                    ${description ? `
                        <div class="bg-base-100 rounded-lg p-3">
                            <p class="text-sm text-base-content/80 whitespace-pre-wrap">${description}</p>
                        </div>
                    ` : ''}
                </div>

                <!-- Next Milestone Progress (if not max tier and not one-off) -->
                ${!isMaxTier && !isOneOff && nextMilestone ? this.renderNextMilestoneProgress(nextMilestone) : ''}

                <!-- Max Tier Achievement Message -->
                ${isMaxTier && !isOneOff ? `
                    <div class="bg-gradient-to-r from-amber-500/20 to-yellow-500/20 rounded-lg p-4 text-center">
                        <span class="text-2xl">&#127942;</span>
                        <p class="text-sm font-semibold text-warning mt-1">Maximum Tier Achieved!</p>
                        <p class="text-xs text-base-content/60">You've completed all milestones of this type</p>
                    </div>
                ` : ''}

                <!-- One-Off Milestone Message -->
                ${isOneOff ? `
                    <div class="bg-gradient-to-r from-primary/10 to-secondary/10 rounded-lg p-4 text-center">
                        <span class="text-2xl">&#127919;</span>
                        <p class="text-sm font-semibold text-primary mt-1">Achievement Unlocked!</p>
                        <p class="text-xs text-base-content/60">This is a one-time milestone</p>
                    </div>
                ` : ''}
            </div>
        `;
    }

    renderChallengeDetail(metadata) {
        if (!metadata) return '';

        const challengeName = metadata.challenge_name || 'A-Z Challenge';
        const completed = metadata.completed_count || 26;
        const total = metadata.total_items || 26;

        // Build 26-letter progress grid
        const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('');
        const letterGrid = letters.map(l =>
            `<div class="w-7 h-7 rounded flex items-center justify-center text-xs font-bold bg-warning/30 text-warning">${l}</div>`
        ).join('');

        return `
            <div class="bg-base-300 rounded-lg p-4 space-y-4">
                ${this.renderCongratsHeader('Challenge Complete!', 'Welcome to the Hall of Fame!')}

                <div class="text-center">
                    <span class="text-4xl">&#127942;</span>
                </div>

                <div class="bg-base-100 rounded-lg p-3">
                    <p class="text-sm font-bold text-center">${PlatPursuit.HTMLUtils.escape(challengeName)}</p>
                    <div class="flex justify-center mt-2">
                        <span class="badge badge-warning badge-lg font-bold gap-1">
                            ${completed}/${total} Platinums
                        </span>
                    </div>
                </div>

                <div class="flex flex-wrap justify-center gap-1">
                    ${letterGrid}
                </div>
            </div>
        `;
    }

    /**
     * Render admin announcement notification detail.
     * Admin announcements can have sections, action buttons, and banner images
     * which are already handled by the base renderNotificationDetail method.
     * This method handles any additional admin-specific metadata.
     *
     * @param {Object} metadata - Notification metadata
     * @returns {string} HTML for admin announcement content
     */
    renderAdminAnnouncementDetail(metadata) {
        // Admin announcements primarily use sections, banner images, and action buttons
        // which are already rendered by the main detail method.
        // This method can render any additional admin-specific content if present.
        if (!metadata) {
            return '';
        }

        // Check for admin-specific fields that might be in metadata
        const source = metadata.source || null;
        const category = metadata.category || null;

        // Only show additional info if we have admin-specific metadata
        if (!source && !category) {
            return '';
        }

        return `
            <div class="bg-base-200 rounded-lg p-3 mt-4">
                <div class="flex items-center gap-2 text-xs text-base-content/60">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <span>Official Announcement</span>
                    ${category ? `<span class="badge badge-ghost badge-xs">${this.escapeHtml(category)}</span>` : ''}
                </div>
            </div>
        `;
    }

    /**
     * Render monthly recap notification detail as a teaser that entices
     * the user to view their full recap, mirroring the email template approach.
     * Shows rounded stats, a platinum highlight, and a "discover" list.
     */
    renderMonthlyRecapDetail(metadata) {
        if (!metadata) {
            return '';
        }

        const monthName = this.escapeHtml(metadata.month_name || 'Monthly');
        const activeDays = metadata.active_days || 0;
        const trophyTier = metadata.trophy_tier || (metadata.total_trophies ? metadata.total_trophies.toLocaleString() : '0');
        const gamesStarted = metadata.games_started || 0;
        const platinumsEarned = metadata.platinums_earned || 0;
        const badgesEarned = metadata.badges_earned || 0;
        const hasStreak = metadata.has_streak || false;

        // Platinum highlight (conditional)
        const platinumHtml = platinumsEarned > 0 ? `
            <div class="bg-info/10 border border-info/20 rounded-lg p-3 mt-3 text-center">
                <span class="font-semibold text-info">
                    &#128142; You earned ${platinumsEarned} platinum${platinumsEarned !== 1 ? 's' : ''} this month!
                </span>
            </div>
        ` : '';

        // Teaser list items (mirrors email template)
        const teaserItems = [
            'Your rarest trophy of the month (it might surprise you!)',
            'Your most active gaming day and hunting patterns',
        ];
        if (hasStreak) {
            teaserItems.push('Your longest trophy hunting streak');
        }
        if (badgesEarned > 0) {
            teaserItems.push(`${badgesEarned} new badge${badgesEarned !== 1 ? 's' : ''} you unlocked`);
        }
        teaserItems.push(
            'Fun quizzes to test your memory',
            'How you compared to your previous months',
            'A shareable recap card to show off your achievements',
        );

        const teaserListHtml = teaserItems.map(item => `
            <li class="flex items-start gap-2">
                <span class="text-primary mt-0.5 flex-shrink-0">&#8226;</span>
                <span>${item}</span>
            </li>
        `).join('');

        return `
            <div class="bg-base-300 rounded-lg p-4">
                <h3 class="text-lg font-semibold mb-3 flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
                        <line x1="16" y1="2" x2="16" y2="6"></line>
                        <line x1="8" y1="2" x2="8" y2="6"></line>
                        <line x1="3" y1="10" x2="21" y2="10"></line>
                    </svg>
                    Quick Look at Your ${monthName}
                </h3>
                <div class="grid grid-cols-3 gap-3">
                    <div class="bg-base-100 rounded-lg p-3 text-center">
                        <div class="text-xs text-base-content/60 uppercase tracking-wide">Active Days</div>
                        <div class="text-lg font-bold">${activeDays}</div>
                    </div>
                    <div class="bg-base-100 rounded-lg p-3 text-center">
                        <div class="text-xs text-base-content/60 uppercase tracking-wide">Trophies</div>
                        <div class="text-lg font-bold text-primary">${this.escapeHtml(String(trophyTier))}</div>
                    </div>
                    <div class="bg-base-100 rounded-lg p-3 text-center">
                        <div class="text-xs text-base-content/60 uppercase tracking-wide">Games Started</div>
                        <div class="text-lg font-bold">${gamesStarted}</div>
                    </div>
                </div>
                ${platinumHtml}
            </div>

            <div class="bg-base-300 rounded-lg p-4 mt-4">
                <h3 class="text-lg font-semibold mb-3 flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="20 12 20 22 4 22 4 12"></polyline>
                        <rect x="2" y="7" width="20" height="5"></rect>
                        <line x1="12" y1="22" x2="12" y2="7"></line>
                        <path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"></path>
                        <path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"></path>
                    </svg>
                    Inside your recap, you'll discover:
                </h3>
                <ul class="space-y-2 text-sm text-base-content/80 list-none pl-0 m-0">
                    ${teaserListHtml}
                </ul>
            </div>
        `;
    }

    /**
     * Render progress towards the next milestone in the same criteria type.
     * Similar pattern to renderNextTierProgress for badges.
     *
     * @param {Object} nextMilestone - Next milestone data from metadata
     * @returns {string} HTML for next milestone progress section
     */
    renderNextMilestoneProgress(nextMilestone) {
        const { name, image, required_value, progress_value, progress_percentage } = nextMilestone;
        const colorClass = this.getProgressColorClass(progress_percentage);

        return `
            <div class="bg-base-100 rounded-lg p-4">
                <h4 class="text-sm font-semibold mb-3 flex items-center gap-2">
                    <svg class="w-4 h-4 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                              d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/>
                    </svg>
                    Progress to Next Milestone
                </h4>

                <!-- Next Milestone Preview -->
                <div class="flex items-center gap-3 mb-3">
                    ${image ? `
                        <img src="${image}"
                             alt="${name}"
                             class="w-12 h-12 object-contain rounded-lg bg-base-300"
                             loading="lazy"
                             onerror="this.style.display='none'" />
                    ` : `
                        <div class="w-12 h-12 rounded-lg bg-base-300 flex items-center justify-center">
                            <svg class="w-6 h-6 text-base-content/40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                                      d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z"/>
                            </svg>
                        </div>
                    `}
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-medium truncate">${name}</p>
                        <p class="text-xs text-base-content/60">Target: ${required_value.toLocaleString()}</p>
                    </div>
                    <span class="badge ${progress_percentage >= 66 ? 'badge-info' : progress_percentage >= 33 ? 'badge-warning' : 'badge-error'} badge-sm">
                        ${progress_percentage}%
                    </span>
                </div>

                <!-- Progress Bar -->
                <div class="w-full bg-base-300 rounded-full h-3 mb-2">
                    <div class="h-3 rounded-full transition-all duration-500 ${colorClass}"
                         style="width: ${progress_percentage}%"></div>
                </div>
                <p class="text-xs text-base-content/60 text-center">
                    ${progress_value.toLocaleString()} / ${required_value.toLocaleString()}
                </p>
            </div>
        `;
    }

    async markAsRead(notificationId) {
        try {
            await PlatPursuit.API.post(`/api/v1/notifications/${notificationId}/read/`, {});

            // Update local state
            const notification = this.notifications.find(n => n.id === notificationId);
            if (notification) {
                notification.is_read = true;
                notification.read_at = new Date().toISOString();
            }

            // Update card styling
            const card = this.notificationItems.querySelector(`[data-notification-id="${notificationId}"]`);
            if (card) {
                card.classList.remove('bg-primary/10', 'border-l-4', 'border-primary');
                card.classList.add('opacity-70');
                card.dataset.read = 'true';

                // Remove unread dot
                const dot = card.querySelector('.bg-primary.rounded-full');
                if (dot) dot.remove();
            }

            // Update navbar badge if it exists
            if (window.notificationSystem) {
                window.notificationSystem.unreadCount = Math.max(0, window.notificationSystem.unreadCount - 1);
                window.notificationSystem.updateBadge();
            }

        } catch (error) {
            console.error('Failed to mark as read:', error);
        }
    }

    handleSearch(query) {
        this.currentFilters.search = query.toLowerCase();
        this.applySearchFilter();
    }

    applySearchFilter() {
        const query = this.currentFilters.search;

        if (!query) {
            // Show all notifications
            const cards = this.notificationItems.querySelectorAll('.notification-card');
            cards.forEach(card => card.style.display = '');
            return;
        }

        // Filter notifications client-side
        const cards = this.notificationItems.querySelectorAll('.notification-card');
        let visibleCount = 0;

        cards.forEach(card => {
            const notifId = parseInt(card.dataset.notificationId);
            const notification = this.notifications.find(n => n.id === notifId);

            if (notification) {
                const titleMatch = notification.title.toLowerCase().includes(query);
                const messageMatch = notification.message.toLowerCase().includes(query);

                if (titleMatch || messageMatch) {
                    card.style.display = '';
                    visibleCount++;
                } else {
                    card.style.display = 'none';
                }
            }
        });

        // Show empty state if no matches
        if (visibleCount === 0) {
            this.listEmpty.querySelector('p').textContent = 'No notifications match your search';
            this.showEmpty();
        } else {
            this.hideEmpty();
        }
    }

    handleFilterChange() {
        this.currentFilters.type = this.filterType.value;
        this.currentFilters.status = this.filterStatus.value;
        this.currentFilters.search = ''; // Reset search when changing filters
        this.searchInput.value = '';

        // Reset state
        this.notifications = [];
        this.selectedIds.clear();
        this.selectedNotification = null;
        this.hasMore = true;

        // Clear detail pane
        this.detailContent.classList.add('hidden');
        this.detailEmpty.classList.remove('hidden');

        // Reload with new filters
        this.loadNotifications();
        this.updateSelectionUI();
    }

    handleSelectAll(checked) {
        const checkboxes = this.notificationItems.querySelectorAll('.notification-checkbox');

        checkboxes.forEach(checkbox => {
            const card = checkbox.closest('.notification-card');
            const notifId = parseInt(card.dataset.notificationId);

            checkbox.checked = checked;

            if (checked) {
                this.selectedIds.add(notifId);
            } else {
                this.selectedIds.delete(notifId);
            }
        });

        this.updateSelectionUI();
    }

    handleCheckboxChange(notificationId, checked) {
        if (checked) {
            this.selectedIds.add(notificationId);
        } else {
            this.selectedIds.delete(notificationId);
        }

        this.updateSelectionUI();

        // Update select all checkbox state
        const totalCheckboxes = this.notificationItems.querySelectorAll('.notification-checkbox').length;
        const selectedCheckboxes = this.notificationItems.querySelectorAll('.notification-checkbox:checked').length;

        this.selectAllCheckbox.checked = totalCheckboxes > 0 && selectedCheckboxes === totalCheckboxes;
        this.selectAllCheckbox.indeterminate = selectedCheckboxes > 0 && selectedCheckboxes < totalCheckboxes;
    }

    async handleBulkMarkRead() {
        if (this.selectedIds.size === 0) return;

        try {
            const ids = Array.from(this.selectedIds);
            await PlatPursuit.API.post('/api/v1/notifications/mark-all-read/', {});

            // Update local state for selected notifications
            this.notifications.forEach(n => {
                if (this.selectedIds.has(n.id) && !n.is_read) {
                    n.is_read = true;
                    n.read_at = new Date().toISOString();
                }
            });

            // Clear selection and reload
            this.selectedIds.clear();
            this.renderNotificationList();
            this.updateSelectionUI();

            PlatPursuit.ToastManager.success(`Marked ${ids.length} notification(s) as read`);

            // Update navbar badge
            if (window.notificationSystem) {
                window.notificationSystem.loadNotifications();
            }

        } catch (error) {
            console.error('Failed to bulk mark as read:', error);
            PlatPursuit.ToastManager.error('Failed to mark notifications as read');
        }
    }

    async handleBulkDelete() {
        if (this.selectedIds.size === 0) return;

        const count = this.selectedIds.size;
        const totalCount = parseInt(this.countBadge.textContent);

        // Show additional warning if there are more notifications than visible
        let message = `Delete ${count} notification(s)? This cannot be undone.`;
        if (this.hasMore && count < totalCount) {
            message = `Delete ${count} selected notification(s)?\n\nNote: This will only delete the selected notifications. You have ${totalCount} total notifications.\n\nThis action cannot be undone.`;
        }

        if (!confirm(message)) {
            return;
        }

        try {
            const ids = Array.from(this.selectedIds);
            await PlatPursuit.API.post('/api/v1/notifications/bulk-delete/', { notification_ids: ids });

            // Remove from local state
            this.notifications = this.notifications.filter(n => !this.selectedIds.has(n.id));

            // Update count badge (decrement by count)
            const currentCount = parseInt(this.countBadge.textContent);
            this.updateCount(Math.max(0, currentCount - count));

            // If deleted notification was being viewed, clear detail pane
            if (this.selectedNotification && this.selectedIds.has(this.selectedNotification.id)) {
                this.selectedNotification = null;
                this.detailContent.classList.add('hidden');
                this.detailEmpty.classList.remove('hidden');
            }

            // Clear selection and reload
            this.selectedIds.clear();
            this.renderNotificationList();
            this.updateSelectionUI();

            PlatPursuit.ToastManager.success(`Deleted ${count} notification(s)`);

            // Update navbar badge
            if (window.notificationSystem) {
                window.notificationSystem.loadNotifications();
            }

        } catch (error) {
            console.error('Failed to bulk delete:', error);
            PlatPursuit.ToastManager.error('Failed to delete notifications');
        }
    }

    async deleteNotification(id) {
        if (!confirm('Delete this notification? This cannot be undone.')) {
            return;
        }

        try {
            await PlatPursuit.API.delete(`/api/v1/notifications/${id}/`);

            // Remove from local state
            this.notifications = this.notifications.filter(n => n.id !== id);
            this.selectedIds.delete(id);

            // Update count badge (decrement by 1)
            const currentCount = parseInt(this.countBadge.textContent);
            this.updateCount(Math.max(0, currentCount - 1));

            // If deleted notification was being viewed, clear detail pane
            if (this.selectedNotification && this.selectedNotification.id === id) {
                this.selectedNotification = null;
                this.detailContent.classList.add('hidden');
                this.detailEmpty.classList.remove('hidden');
            }

            // Reload
            this.renderNotificationList();
            this.updateSelectionUI();

            PlatPursuit.ToastManager.success('Notification deleted');

            // Update navbar badge
            if (window.notificationSystem) {
                window.notificationSystem.loadNotifications();
            }

        } catch (error) {
            console.error('Failed to delete notification:', error);
            PlatPursuit.ToastManager.error('Failed to delete notification');
        }
    }

    updateSelectionUI() {
        const hasSelection = this.selectedIds.size > 0;
        this.bulkMarkReadBtn.disabled = !hasSelection;
        this.bulkDeleteBtn.disabled = !hasSelection;

        // Update select-all checkbox state
        const totalCheckboxes = this.notificationItems.querySelectorAll('.notification-checkbox').length;
        const selectedCount = this.selectedIds.size;

        if (totalCheckboxes === 0 || selectedCount === 0) {
            this.selectAllCheckbox.checked = false;
            this.selectAllCheckbox.indeterminate = false;
        } else if (selectedCount === totalCheckboxes) {
            this.selectAllCheckbox.checked = true;
            this.selectAllCheckbox.indeterminate = false;
        } else {
            this.selectAllCheckbox.checked = false;
            this.selectAllCheckbox.indeterminate = true;
        }
    }

    updateCount(count) {
        this.countBadge.textContent = count;
    }

    setupInfiniteScroll() {
        const observer = new IntersectionObserver(
            (entries) => {
                if (entries[0].isIntersecting && this.hasMore && !this.loading) {
                    this.loadNotifications(true);
                }
            },
            { threshold: 0.1 }
        );

        observer.observe(this.scrollLoader);
    }

    showLoading() {
        this.listLoading.classList.remove('hidden');
        this.listEmpty.classList.add('hidden');
    }

    hideLoading() {
        this.listLoading.classList.add('hidden');
    }

    showEmpty() {
        this.listEmpty.classList.remove('hidden');
        this.notificationItems.innerHTML = '';
    }

    hideEmpty() {
        this.listEmpty.classList.add('hidden');
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('notification-list-pane')) {
        window.notificationInbox = new NotificationInboxManager();
    }
});
