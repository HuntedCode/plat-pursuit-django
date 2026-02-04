/**
 * NotificationSystem - Handles in-app notifications
 * Depends on: PlatPursuit.ToastManager, PlatPursuit.CSRFToken, PlatPursuit.TimeFormatter, PlatPursuit.API
 */
class NotificationSystem {
    constructor() {
        console.log('NotificationSystem: Constructor called');
        this.dropdown = document.getElementById('notification-dropdown');

        // Only initialize if dropdown exists (user is authenticated)
        if (!this.dropdown) {
            console.log('NotificationSystem: Dropdown not found, user not authenticated');
            return;
        }

        console.log('NotificationSystem: Dropdown found, initializing...');
        this.badge = document.getElementById('notification-badge');
        this.list = document.getElementById('notification-list');
        this.loadingEl = document.getElementById('notification-loading');
        this.emptyEl = document.getElementById('notification-empty');
        this.markAllBtn = document.getElementById('mark-all-read-btn');

        console.log('NotificationSystem: Elements found:', {
            badge: !!this.badge,
            list: !!this.list,
            loadingEl: !!this.loadingEl,
            emptyEl: !!this.emptyEl,
            markAllBtn: !!this.markAllBtn
        });

        this.eventSource = null;
        this.unreadCount = 0;
        this.loaded = false;

        this.init();
    }

    init() {
        console.log('NotificationSystem: init() called');

        // Connect to SSE for real-time notifications
        this.connectSSE();

        // Load notifications when dropdown is opened
        // DaisyUI dropdowns work with focus/blur, not just click
        const dropdownButton = this.dropdown.querySelector('button');
        const dropdownContent = this.dropdown.querySelector('.dropdown-content');
        console.log('NotificationSystem: Dropdown button found:', !!dropdownButton);
        console.log('NotificationSystem: Dropdown content found:', !!dropdownContent);

        if (dropdownButton) {
            console.log('NotificationSystem: Adding focus listener to button');

            // Listen for when the dropdown button gets focus (opens dropdown)
            dropdownButton.addEventListener('focus', () => {
                console.log('NotificationSystem: Button focused (dropdown opened)!');
                if (!this.loaded) {
                    this.loadNotifications();
                    this.loaded = true;
                } else {
                    console.log('NotificationSystem: Already loaded, skipping');
                }
            });

            // Also listen for click as backup
            dropdownButton.addEventListener('click', () => {
                console.log('NotificationSystem: Button clicked!');
                if (!this.loaded) {
                    this.loadNotifications();
                    this.loaded = true;
                }
            });
        }

        // Mark all as read button
        if (this.markAllBtn) {
            this.markAllBtn.addEventListener('click', () => this.markAllAsRead());
        }

        // Event delegation for notification clicks - navigate to inbox with notification ID
        this.list.addEventListener('click', (e) => {
            const notificationEl = e.target.closest('[data-notification-id]');
            if (notificationEl) {
                const notificationId = notificationEl.dataset.notificationId;
                // Navigate to notification inbox with the notification ID as a query parameter
                window.location.href = `/notifications/?id=${notificationId}`;
            }
        });

        // Load initial unread count
        this.loadUnreadCount();

        // Check for platinum celebration on page load
        this.checkForPlatinumCelebration();

        // Clean up SSE connection when navigating away
        window.addEventListener('beforeunload', () => {
            this.destroy();
        });
    }

    async checkForPlatinumCelebration() {
        // Only celebrate once per browser session
        // Check immediately to prevent race conditions with multiple calls
        if (sessionStorage.getItem('platinum_celebrated')) {
            return;
        }

        // Set flag immediately to prevent duplicate checks while async operation is in flight
        sessionStorage.setItem('platinum_celebrated', 'checking');

        try {
            // Fetch unread platinum notifications
            const data = await PlatPursuit.API.get('/api/v1/notifications/?unread_only=true&type=platinum_earned&limit=5');

            if (data.notifications && data.notifications.length > 0) {
                // Mark as fully celebrated
                sessionStorage.setItem('platinum_celebrated', 'true');

                // Get game name from first platinum notification
                const gameName = data.notifications[0].metadata?.game_name || 'a game';

                // Trigger celebration
                await PlatPursuit.CelebrationManager.celebratePlatinum(gameName, data.notifications.length);
            } else {
                // No platinum notifications, clear the flag so we check again next session
                sessionStorage.removeItem('platinum_celebrated');
            }
        } catch (error) {
            console.error('Failed to check for platinum celebrations:', error);
            // On error, clear flag so we can retry next page load
            sessionStorage.removeItem('platinum_celebrated');
        }
    }

    markPlatinumCelebrationPending() {
        // Clear the celebration flag so next page load will check for platinums and celebrate
        sessionStorage.removeItem('platinum_celebrated');
    }

    connectSSE() {
        try {
            this.eventSource = new EventSource('/api/v1/notifications/sse/');

            this.eventSource.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);

                    if (data.type === 'notification') {
                        this.handleNewNotification(data);
                    } else if (data.type === 'connected') {
                        console.log('Notification stream connected');
                    }
                    // Ignore heartbeat messages
                } catch (e) {
                    console.error('Error parsing SSE message:', e);
                }
            };

            this.eventSource.onerror = (error) => {
                console.error('SSE connection error:', error);
                this.eventSource.close();

                // Retry connection after 5 seconds
                setTimeout(() => {
                    console.log('Reconnecting to notification stream...');
                    this.connectSSE();
                }, 5000);
            };
        } catch (error) {
            console.error('Failed to connect to SSE:', error);
        }
    }

    handleNewNotification(notification) {
        // Increment unread count
        this.unreadCount++;
        this.updateBadge();

        // Mark platinum notifications for celebration on next page load
        if (notification.notification_type === 'platinum_earned') {
            this.markPlatinumCelebrationPending();
        }

        // Show toast for all notification types
        const toastMessage = `
            <div class="flex items-center gap-2">
                <span class="text-xl">${notification.icon}</span>
                <div>
                    <div class="font-bold">${this.escapeHtml(notification.title)}</div>
                    <div class="text-xs">${this.escapeHtml(notification.message.substring(0, 100))}${notification.message.length > 100 ? '...' : ''}</div>
                </div>
            </div>
        `;

        // Use appropriate toast type based on priority
        const toastType = notification.priority === 'urgent' ? 'warning' : 'info';
        PlatPursuit.ToastManager.show(toastMessage, toastType, 5000);

        // Add to list if dropdown has been opened
        if (this.loaded) {
            this.prependNotification(notification);
        }
    }

    async loadNotifications() {
        console.log('Loading notifications...');
        try {
            this.showLoading();

            // Use refactored API utility
            const data = await PlatPursuit.API.get('/api/v1/notifications/?limit=10');
            console.log('Received notifications data:', data);

            this.hideLoading();
            this.renderNotifications(data.notifications);
            this.unreadCount = data.unread_count;
            this.updateBadge();
        } catch (error) {
            console.error('Failed to load notifications:', error);
            this.hideLoading();
            this.showEmpty(); // Show empty state on error
            PlatPursuit.ToastManager.error('Failed to load notifications');
        }
    }

    async loadUnreadCount() {
        try {
            const data = await PlatPursuit.API.get('/api/v1/notifications/?limit=0');
            this.unreadCount = data.unread_count;
            this.updateBadge();
        } catch (error) {
            console.error('Failed to load unread count:', error);
        }
    }

    renderNotifications(notifications) {
        // Remove existing notification cards but preserve loading/empty elements
        const existingCards = this.list.querySelectorAll('.notification-card');
        existingCards.forEach(card => card.remove());

        if (notifications.length === 0) {
            this.showEmpty();
            return;
        }

        this.hideEmpty();

        // Insert new notifications after the loading/empty elements
        const notificationsHtml = notifications.map(n => this.renderNotificationCard(n)).join('');
        this.list.insertAdjacentHTML('beforeend', notificationsHtml);
    }

    prependNotification(notification) {
        // Hide empty state if showing
        this.hideEmpty();

        // Create notification element
        const notificationHtml = this.renderNotificationCard(notification);
        this.list.insertAdjacentHTML('afterbegin', notificationHtml);

        // Limit to 10 notifications in the list
        const notificationCards = this.list.querySelectorAll('.notification-card');
        if (notificationCards.length > 10) {
            notificationCards[notificationCards.length - 1].remove();
        }
    }

    renderNotificationCard(notification) {
        const unreadClass = !notification.is_read ? 'bg-primary/10' : 'opacity-60';
        const unreadDot = !notification.is_read ? '<div class="w-2 h-2 rounded-full bg-primary"></div>' : '';

        // Use refactored TimeFormatter
        const timeStr = PlatPursuit.TimeFormatter.relative(notification.created_at);

        // Escape HTML to prevent XSS
        const title = this.escapeHtml(notification.title);
        const message = this.escapeHtml(notification.message);

        return `
            <div
                class="notification-card p-3 rounded-lg hover:bg-base-300 cursor-pointer transition-colors ${unreadClass}"
                data-notification-id="${notification.id}"
                data-read="${notification.is_read}">

                <div class="flex items-start gap-3">
                    <div class="text-2xl">${notification.icon}</div>
                    <div class="flex-1 min-w-0">
                        <h4 class="font-semibold text-sm truncate">${title}</h4>
                        <p class="text-xs text-base-content/70 line-clamp-2 mt-1">${message}</p>
                        <p class="text-xs text-base-content/50 mt-1">${timeStr}</p>
                    </div>
                    ${unreadDot}
                </div>

                ${notification.action_url ? `
                    <a href="${this.escapeHtml(notification.action_url)}" class="btn btn-xs btn-primary mt-2" onclick="event.stopPropagation()">
                        ${this.escapeHtml(notification.action_text || 'View')}
                    </a>
                ` : ''}
            </div>
        `;
    }

    async markAsRead(notificationId) {
        try {
            // Use refactored API utility (pass empty object for POST data)
            await PlatPursuit.API.post(`/api/v1/notifications/${notificationId}/read/`, {});

            this.unreadCount = Math.max(0, this.unreadCount - 1);
            this.updateBadge();

            // Update UI
            const el = document.querySelector(`[data-notification-id="${notificationId}"]`);
            if (el) {
                el.classList.remove('bg-primary/10');
                el.classList.add('opacity-60');
                el.dataset.read = 'true';

                // Remove unread dot
                const dot = el.querySelector('.w-2.h-2.rounded-full');
                if (dot) {
                    dot.remove();
                }
            }
        } catch (error) {
            console.error('Failed to mark as read:', error);
        }
    }

    async markAllAsRead() {
        try {
            console.log('Mark all as read - CSRF token:', PlatPursuit.CSRFToken.get());
            // Use refactored API utility (pass empty object for POST data)
            await PlatPursuit.API.post('/api/v1/notifications/mark-all-read/', {});

            this.unreadCount = 0;
            this.updateBadge();

            // Reload notifications to reflect changes
            this.loadNotifications();

            PlatPursuit.ToastManager.success('All notifications marked as read');
        } catch (error) {
            console.error('Failed to mark all as read:', error);
            console.error('Error details:', error.response);
            PlatPursuit.ToastManager.error('Failed to mark notifications as read');
        }
    }

    updateBadge() {
        if (this.unreadCount > 0) {
            this.badge.textContent = this.unreadCount > 99 ? '99+' : this.unreadCount;
            this.badge.classList.remove('hidden');
        } else {
            this.badge.classList.add('hidden');
        }
    }

    showLoading() {
        if (this.loadingEl) {
            this.loadingEl.classList.remove('hidden');
        }
    }

    hideLoading() {
        if (this.loadingEl) {
            this.loadingEl.classList.add('hidden');
        }
    }

    showEmpty() {
        this.hideLoading(); // Always hide loading when showing empty
        if (this.emptyEl) {
            this.emptyEl.classList.remove('hidden');
        }
    }

    hideEmpty() {
        if (this.emptyEl) {
            this.emptyEl.classList.add('hidden');
        }
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    destroy() {
        // Close SSE connection when needed
        if (this.eventSource) {
            this.eventSource.close();
        }
    }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    console.log('NotificationSystem: DOMContentLoaded event fired');
    // Check if notification dropdown exists (user is authenticated)
    const dropdown = document.getElementById('notification-dropdown');
    console.log('NotificationSystem: Dropdown element exists:', !!dropdown);

    if (dropdown) {
        console.log('NotificationSystem: Creating new NotificationSystem instance');
        window.notificationSystem = new NotificationSystem();
    } else {
        console.log('NotificationSystem: No dropdown found, not initializing');
    }
});
