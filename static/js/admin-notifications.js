/**
 * AdminNotificationManager - Handles admin notification center functionality
 */
(function() {
    'use strict';

    class AdminNotificationManager {
        constructor() {
            this.form = document.getElementById('notification-form');
            this.targetTypeSelect = document.getElementById('target-type');
            this.scheduleToggle = document.getElementById('schedule-toggle');
            this.scheduleDatetime = document.getElementById('schedule-datetime');
            this.sendBtn = document.getElementById('send-btn');
            this.scheduleBtn = document.getElementById('schedule-btn');
            this.recipientEstimateEl = document.getElementById('recipient-estimate');
            this.previewEl = document.getElementById('notification-preview');
            this.userSearchInput = document.getElementById('user-search');
            this.userSearchDropdown = document.getElementById('user-search-dropdown');
            this.selectedUsersEl = document.getElementById('selected-users');
            this.userIdsInput = document.getElementById('user-ids-input');

            // Form inputs for preview
            this.titleInput = document.getElementById('title-input');
            this.messageInput = document.getElementById('message-input');
            this.detailInput = document.getElementById('detail-input');
            this.bannerImageInput = document.getElementById('banner-image-input');
            this.iconInput = document.getElementById('icon-input');
            this.priorityInput = document.getElementById('priority-input');

            // Character counters
            this.titleCount = document.getElementById('title-count');
            this.messageCount = document.getElementById('message-count');
            this.detailCount = document.getElementById('detail-count');

            // Banner image preview
            this.bannerPreview = document.getElementById('banner-preview');
            this.bannerPreviewImg = document.getElementById('banner-preview-img');
            this.clearBannerBtn = document.getElementById('clear-banner-btn');

            this.selectedUsers = [];
            this.searchTimeout = null;

            this.init();
        }

        init() {
            if (!this.form) return;

            // Initialize section builder
            if (window.PlatPursuit && window.PlatPursuit.SectionBuilder) {
                const sectionsContainer = document.getElementById('sections-list');
                if (sectionsContainer) {
                    window.sectionBuilder = new window.PlatPursuit.SectionBuilder('sections-list', 5);
                    // Trigger initial preview after section builder initializes
                    setTimeout(() => {
                        this.updatePreview();
                    }, 100);
                }
            }

            // Legacy mode toggle
            const legacyToggle = document.getElementById('legacy-mode-toggle');
            if (legacyToggle) {
                legacyToggle.addEventListener('change', (e) => {
                    this.toggleLegacyMode(e.target.checked);
                });
            }

            // Target type change
            if (this.targetTypeSelect) {
                this.targetTypeSelect.addEventListener('change', () => {
                    this.handleTargetTypeChange();
                    this.updateRecipientCount();
                });
            }

            // Schedule toggle
            if (this.scheduleToggle) {
                this.scheduleToggle.addEventListener('change', (e) => {
                    this.handleScheduleToggle(e.target.checked);
                });
            }

            // Form input changes for preview and character counts
            if (this.titleInput) {
                this.titleInput.addEventListener('input', () => {
                    this.updatePreview();
                    this.updateCharCount(this.titleInput, this.titleCount, 255);
                });
            }

            if (this.messageInput) {
                this.messageInput.addEventListener('input', () => {
                    this.updatePreview();
                    this.updateCharCount(this.messageInput, this.messageCount, 1000);
                });
            }

            if (this.iconInput) {
                this.iconInput.addEventListener('input', () => this.updatePreview());
            }

            if (this.priorityInput) {
                this.priorityInput.addEventListener('change', () => this.updatePreview());
            }

            if (this.detailInput) {
                this.detailInput.addEventListener('input', () => {
                    this.updatePreview();
                    this.updateCharCount(this.detailInput, this.detailCount, 2500);
                });
            }

            // Banner image preview
            if (this.bannerImageInput) {
                this.bannerImageInput.addEventListener('change', (e) => {
                    this.handleBannerImageChange(e);
                });
            }

            if (this.clearBannerBtn) {
                this.clearBannerBtn.addEventListener('click', () => {
                    this.clearBannerImage();
                });
            }

            // User search for individual targeting
            if (this.userSearchInput) {
                this.userSearchInput.addEventListener('input', (e) => {
                    clearTimeout(this.searchTimeout);
                    this.searchTimeout = setTimeout(() => {
                        this.searchUsers(e.target.value);
                    }, 300);
                });

                // Close dropdown when clicking outside
                document.addEventListener('click', (e) => {
                    if (!e.target.closest('#user-search') && !e.target.closest('#user-search-dropdown')) {
                        this.hideSearchDropdown();
                    }
                });
            }

            // Initial setup
            this.handleTargetTypeChange();
            this.updateRecipientCount();
        }

        handleTargetTypeChange() {
            const targetType = this.targetTypeSelect?.value;
            const individualCriteria = document.getElementById('criteria-individual');

            // Hide all criteria sections
            if (individualCriteria) {
                if (targetType === 'individual') {
                    individualCriteria.classList.remove('hidden');
                } else {
                    individualCriteria.classList.add('hidden');
                }
            }
        }

        handleScheduleToggle(isScheduled) {
            if (isScheduled) {
                this.scheduleDatetime?.classList.remove('hidden');
                this.sendBtn?.classList.add('hidden');
                this.scheduleBtn?.classList.remove('hidden');
            } else {
                this.scheduleDatetime?.classList.add('hidden');
                this.sendBtn?.classList.remove('hidden');
                this.scheduleBtn?.classList.add('hidden');
            }
        }

        toggleLegacyMode(isLegacy) {
            const structured = document.getElementById('structured-sections-container');
            const legacy = document.getElementById('legacy-markdown-container');

            if (structured && legacy) {
                structured.classList.toggle('hidden', isLegacy);
                legacy.classList.toggle('hidden', !isLegacy);

                // Clear hidden input if switching to legacy
                if (isLegacy) {
                    const sectionsInput = document.getElementById('sections-data-input');
                    if (sectionsInput) {
                        sectionsInput.value = '';
                    }
                }

                this.updatePreview();
            }
        }

        updateCharCount(input, countEl, max) {
            if (!input || !countEl) return;
            const len = input.value.length;
            countEl.textContent = `${len}/${max}`;
            if (len > max * 0.9) {
                countEl.classList.add('text-warning');
            } else {
                countEl.classList.remove('text-warning');
            }
        }

        async updateRecipientCount() {
            const targetType = this.targetTypeSelect?.value || 'all';
            const criteria = this.buildCriteria();

            try {
                const response = await fetch('/api/v1/admin/notifications/target-count/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': this.getCsrfToken()
                    },
                    body: JSON.stringify({
                        target_type: targetType,
                        criteria: criteria
                    })
                });

                if (response.ok) {
                    const data = await response.json();
                    if (this.recipientEstimateEl) {
                        this.recipientEstimateEl.textContent = data.count.toLocaleString();
                    }
                }
            } catch (error) {
                console.error('Failed to get recipient count:', error);
                if (this.recipientEstimateEl) {
                    this.recipientEstimateEl.textContent = '?';
                }
            }
        }

        buildCriteria() {
            const targetType = this.targetTypeSelect?.value;
            const criteria = {};

            if (targetType === 'individual') {
                criteria.user_ids = this.selectedUsers.map(u => u.id);
            }

            return criteria;
        }

        updatePreview() {
            if (!this.previewEl) return;

            const title = this.titleInput?.value || 'Notification Title';
            const message = this.messageInput?.value || 'Notification message will appear here...';
            const icon = this.iconInput?.value || '📢';
            const priority = this.priorityInput?.value || 'normal';

            const priorityBadge = {
                low: 'badge-ghost',
                normal: 'badge-info',
                high: 'badge-warning',
                urgent: 'badge-error'
            }[priority] || 'badge-info';

            // Check if we're in legacy mode or structured mode
            const legacyToggle = document.getElementById('legacy-mode-toggle');
            const isLegacy = legacyToggle?.checked || false;

            let detailHtml = '';
            if (isLegacy) {
                // Legacy markdown mode
                const detail = this.detailInput?.value || '';
                if (detail) {
                    detailHtml = `
                        <div class="prose prose-sm max-w-none bg-base-200 rounded-lg p-3 mt-3">
                            ${this.renderMarkdown(detail)}
                        </div>
                    `;
                }
            } else {
                // Structured sections mode
                const sections = window.sectionBuilder?.getSections() || [];
                if (sections.length > 0) {
                    detailHtml = this.renderStructuredSections(sections);
                }
            }

            // Show banner preview if image is loaded
            const bannerHtml = (this.bannerPreviewImg && !this.bannerPreview.classList.contains('hidden')) ? `
                <div class="w-full overflow-hidden rounded-lg mb-3">
                    <img src="${this.bannerPreviewImg.src}" alt="Banner" class="w-full h-auto object-contain max-h-48" />
                </div>
            ` : '';

            this.previewEl.innerHTML = `
                ${bannerHtml}
                <div class="flex gap-3">
                    <div class="text-3xl">${this.escapeHtml(icon)}</div>
                    <div class="flex-1">
                        <div class="flex items-center gap-2 mb-1">
                            <span class="font-semibold">${this.escapeHtml(title)}</span>
                            <span class="badge ${priorityBadge} badge-sm">${priority}</span>
                        </div>
                        <p class="text-sm text-base-content/80">${this.escapeHtml(message)}</p>
                        ${detailHtml}
                        <div class="text-xs text-base-content/60 mt-2">Just now</div>
                    </div>
                </div>
            `;

            // Also update modal preview if open
            const modalPreview = document.getElementById('modal-preview-content');
            if (modalPreview) {
                modalPreview.innerHTML = this.previewEl.innerHTML;
            }
        }

        renderMarkdown(text) {
            if (!text) return '';
            try {
                // Use marked.js if available, otherwise just escape and preserve line breaks
                if (typeof marked !== 'undefined') {
                    return marked.parse(text);
                } else {
                    return this.escapeHtml(text).replace(/\n/g, '<br>');
                }
            } catch (e) {
                console.error('Markdown rendering failed:', e);
                return this.escapeHtml(text).replace(/\n/g, '<br>');
            }
        }

        renderStructuredSections(sections) {
            if (!sections || sections.length === 0) return '';

            return sections
                .sort((a, b) => a.order - b.order)
                .map(section => {
                    const formatted = this.formatStructuredContent(section.content);

                    return `
                        <div class="bg-base-300 rounded-lg p-3 mt-3 border-l-4 border-primary">
                            <div class="flex items-center gap-2 mb-2">
                                <span class="text-xl">${this.escapeHtml(section.icon)}</span>
                                <h4 class="font-semibold text-sm">${this.escapeHtml(section.header)}</h4>
                            </div>
                            <div class="prose prose-sm text-xs">${formatted}</div>
                        </div>
                    `;
                }).join('');
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

        handleBannerImageChange(e) {
            const file = e.target.files[0];
            if (!file) {
                this.clearBannerImage();
                return;
            }

            // Validate file size (5MB max)
            const maxSize = 5 * 1024 * 1024; // 5MB in bytes
            if (file.size > maxSize) {
                alert('Banner image must be under 5MB.');
                this.clearBannerImage();
                return;
            }

            // Validate file type
            const allowedTypes = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
            if (!allowedTypes.includes(file.type)) {
                alert('Banner image must be JPEG, PNG, WEBP, or GIF format.');
                this.clearBannerImage();
                return;
            }

            // Show preview
            const reader = new FileReader();
            reader.onload = (e) => {
                this.bannerPreviewImg.src = e.target.result;
                this.bannerPreview.classList.remove('hidden');
                this.updatePreview();
            };
            reader.readAsDataURL(file);
        }

        clearBannerImage() {
            if (this.bannerImageInput) {
                this.bannerImageInput.value = '';
            }
            if (this.bannerPreview) {
                this.bannerPreview.classList.add('hidden');
            }
            if (this.bannerPreviewImg) {
                this.bannerPreviewImg.src = '';
            }
            this.updatePreview();
        }

        async searchUsers(query) {
            if (query.length < 2) {
                this.hideSearchDropdown();
                return;
            }

            try {
                const response = await fetch(`/api/v1/admin/notifications/user-search/?q=${encodeURIComponent(query)}`, {
                    headers: {
                        'X-CSRFToken': this.getCsrfToken()
                    }
                });

                if (response.ok) {
                    const data = await response.json();
                    this.showSearchResults(data.users);
                }
            } catch (error) {
                console.error('User search failed:', error);
            }
        }

        showSearchResults(users) {
            if (!this.userSearchDropdown) return;

            if (users.length === 0) {
                this.hideSearchDropdown();
                return;
            }

            // Filter out already selected users
            const filteredUsers = users.filter(u => !this.selectedUsers.find(s => s.id === u.id));

            if (filteredUsers.length === 0) {
                this.hideSearchDropdown();
                return;
            }

            this.userSearchDropdown.innerHTML = filteredUsers.map(user => `
                <div class="p-2 hover:bg-base-200 cursor-pointer flex items-center gap-2"
                     data-user-id="${user.id}"
                     data-psn-username="${this.escapeHtml(user.psn_username)}"
                     data-email="${this.escapeHtml(user.email)}">
                    <div>
                        <div class="font-medium">${this.escapeHtml(user.psn_username)}</div>
                        <div class="text-xs text-base-content/60">${this.escapeHtml(user.email)}</div>
                    </div>
                </div>
            `).join('');

            // Add click handlers
            this.userSearchDropdown.querySelectorAll('[data-user-id]').forEach(item => {
                item.addEventListener('click', () => {
                    this.addSelectedUser({
                        id: parseInt(item.dataset.userId),
                        psn_username: item.dataset.psnUsername,
                        email: item.dataset.email
                    });
                    this.userSearchInput.value = '';
                    this.hideSearchDropdown();
                });
            });

            this.userSearchDropdown.classList.remove('hidden');
        }

        hideSearchDropdown() {
            if (this.userSearchDropdown) {
                this.userSearchDropdown.classList.add('hidden');
            }
        }

        addSelectedUser(user) {
            if (this.selectedUsers.find(u => u.id === user.id)) return;

            this.selectedUsers.push(user);
            this.renderSelectedUsers();
            this.updateRecipientCount();
        }

        removeSelectedUser(userId) {
            this.selectedUsers = this.selectedUsers.filter(u => u.id !== userId);
            this.renderSelectedUsers();
            this.updateRecipientCount();
        }

        renderSelectedUsers() {
            if (!this.selectedUsersEl) return;

            this.selectedUsersEl.innerHTML = this.selectedUsers.map(user => `
                <div class="badge badge-lg gap-2 badge-primary">
                    ${this.escapeHtml(user.psn_username)}
                    <button type="button" class="hover:text-error" onclick="window.adminNotificationManager.removeSelectedUser(${user.id})">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="inline-block w-4 h-4 stroke-current">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                        </svg>
                    </button>
                </div>
            `).join('');

            if (this.userIdsInput) {
                this.userIdsInput.value = this.selectedUsers.map(u => u.id).join(',');
            }
        }

        escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        getCsrfToken() {
            const cookie = document.cookie.split(';').find(c => c.trim().startsWith('csrftoken='));
            if (cookie) {
                return cookie.split('=')[1];
            }
            // Fallback to hidden input
            const input = document.querySelector('[name="csrfmiddlewaretoken"]');
            return input ? input.value : '';
        }
    }

    // Initialize on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        window.adminNotificationManager = new AdminNotificationManager();
    });
})();
