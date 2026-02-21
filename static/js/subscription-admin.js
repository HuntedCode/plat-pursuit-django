(function() {
    'use strict';

    const API = window.PlatPursuit.API;
    const Toast = window.PlatPursuit.ToastManager;

    // ── Tab Switching ────────────────────────────────────────────────

    const tabs = document.querySelectorAll('#sub-tabs .tab');
    const panels = document.querySelectorAll('.sub-tab-panel');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.dataset.tab;

            // Toggle active tab
            tabs.forEach(t => t.classList.remove('tab-active'));
            tab.classList.add('tab-active');

            // Toggle panels
            panels.forEach(p => p.classList.add('hidden'));
            const targetPanel = document.getElementById('tab-' + target);
            if (targetPanel) targetPanel.classList.remove('hidden');
        });
    });

    // ── Helpers ──────────────────────────────────────────────────────

    async function _extractError(error, fallback) {
        try {
            const d = await error.response?.json();
            return d?.error || d?.detail || fallback;
        } catch {
            return fallback;
        }
    }

    function _formatDate(isoString) {
        if (!isoString) return '-';
        const d = new Date(isoString);
        if (isNaN(d.getTime())) return '-';
        return d.toLocaleDateString('en-US', {
            month: 'short', day: 'numeric', year: 'numeric',
            hour: 'numeric', minute: '2-digit',
        });
    }

    function _escapeHtml(str) {
        return PlatPursuit.HTMLUtils.escape(str || '');
    }

    /**
     * Disable a button during an async action, re-enable on completion.
     * Accepts either a DOM element or an event (uses event.target).
     */
    async function _withButtonLock(btnOrEvent, asyncFn) {
        const btn = btnOrEvent?.target || btnOrEvent;
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        try {
            await asyncFn();
        } finally {
            btn.disabled = false;
        }
    }

    // Currently viewed user in the detail modal
    let _modalUserId = null;

    // ── API Actions ──────────────────────────────────────────────────

    async function resendEmail(userId, isFinal, event) {
        await _withButtonLock(event, async () => {
            const action = isFinal ? 'resend_payment_email_final' : 'resend_payment_email';
            const label = isFinal ? 'final warning' : 'payment warning';

            try {
                const data = await API.post('/api/v1/admin/subscriptions/action/', {
                    action: action,
                    user_id: userId,
                });
                if (data.success) {
                    Toast.show(data.message || `Sent ${label} email`, 'success');
                } else {
                    Toast.show(data.message || `Failed to send ${label} email`, 'warning');
                }
            } catch (error) {
                const msg = await _extractError(error, `Failed to send ${label} email`);
                Toast.show(msg, 'error');
            }
        });
    }

    async function resendNotification(userId, event) {
        await _withButtonLock(event, async () => {
            try {
                const data = await API.post('/api/v1/admin/subscriptions/action/', {
                    action: 'resend_notification',
                    user_id: userId,
                });
                Toast.show(data.message || 'Notification sent', 'success');
            } catch (error) {
                const msg = await _extractError(error, 'Failed to send notification');
                Toast.show(msg, 'error');
            }
        });
    }

    async function showUserDetail(userId) {
        _modalUserId = userId;
        const modal = document.getElementById('user-detail-modal');
        const loading = document.getElementById('detail-modal-loading');
        const content = document.getElementById('detail-modal-content');

        // Show modal with loading state
        loading.classList.remove('hidden');
        content.classList.add('hidden');
        modal.showModal();

        try {
            const data = await API.get(`/api/v1/admin/subscriptions/user/${userId}/`);

            // Populate user info
            const user = data.user;
            document.getElementById('detail-modal-title').textContent =
                `${user.psn_username} - ${user.tier_display || 'No Tier'}`;
            document.getElementById('detail-user-info').innerHTML =
                `<div class="flex flex-wrap gap-x-4 gap-y-1 text-sm">
                    <span><strong>Email:</strong> ${_escapeHtml(user.email)}</span>
                    <span><strong>Tier:</strong> ${_escapeHtml(user.tier_display || 'None')}</span>
                    <span><strong>Provider:</strong> ${_escapeHtml(user.provider || 'None')}</span>
                </div>`;

            // Populate notification history
            const notiBody = document.getElementById('detail-notifications');
            if (data.notifications.length === 0) {
                notiBody.innerHTML = '<tr><td colspan="5" class="text-center opacity-60">No notifications</td></tr>';
            } else {
                notiBody.innerHTML = data.notifications.map(n => `
                    <tr>
                        <td>${_escapeHtml(n.type)}</td>
                        <td>${_escapeHtml(n.title)}</td>
                        <td><span class="badge badge-xs ${n.priority === 'urgent' ? 'badge-error' : n.priority === 'high' ? 'badge-warning' : 'badge-ghost'}">${_escapeHtml(n.priority)}</span></td>
                        <td>${n.is_read ? 'Yes' : 'No'}</td>
                        <td>${_formatDate(n.created_at)}</td>
                    </tr>
                `).join('');
            }

            // Populate email logs
            const emailBody = document.getElementById('detail-emails');
            if (data.email_logs.length === 0) {
                emailBody.innerHTML = '<tr><td colspan="4" class="text-center opacity-60">No email logs</td></tr>';
            } else {
                emailBody.innerHTML = data.email_logs.map(e => `
                    <tr>
                        <td>${_escapeHtml(e.email_type)}</td>
                        <td><span class="badge badge-xs ${e.status === 'sent' ? 'badge-success' : e.status === 'suppressed' ? 'badge-warning' : 'badge-error'}">${_escapeHtml(e.status)}</span></td>
                        <td>${_escapeHtml(e.triggered_by)}</td>
                        <td>${_formatDate(e.created_at)}</td>
                    </tr>
                `).join('');
            }

            // Populate subscription periods
            const periodBody = document.getElementById('detail-periods');
            if (data.periods.length === 0) {
                periodBody.innerHTML = '<tr><td colspan="5" class="text-center opacity-60">No subscription periods</td></tr>';
            } else {
                periodBody.innerHTML = data.periods.map(p => `
                    <tr>
                        <td>${_escapeHtml(p.provider)}</td>
                        <td>${_formatDate(p.started_at)}</td>
                        <td>${p.ended_at ? _formatDate(p.ended_at) : '<span class="badge badge-success badge-xs">Active</span>'}</td>
                        <td>${p.duration_days !== null ? p.duration_days + ' days' : 'Ongoing'}</td>
                        <td class="text-sm opacity-70">${_escapeHtml(p.notes)}</td>
                    </tr>
                `).join('');
            }

            loading.classList.add('hidden');
            content.classList.remove('hidden');

        } catch (error) {
            const msg = await _extractError(error, 'Failed to load user details');
            Toast.show(msg, 'error');
            modal.close();
        }
    }

    async function sendWelcomeEmail(event) {
        if (!_modalUserId) return;
        await _withButtonLock(event, async () => {
            try {
                const data = await API.post('/api/v1/admin/subscriptions/action/', {
                    action: 'send_welcome_email',
                    user_id: _modalUserId,
                });
                if (data.success) {
                    Toast.show(data.message || 'Welcome email sent', 'success');
                } else {
                    Toast.show(data.message || 'Failed to send welcome email', 'warning');
                }
            } catch (error) {
                const msg = await _extractError(error, 'Failed to send welcome email');
                Toast.show(msg, 'error');
            }
        });
    }

    async function sendPaymentSucceededEmail(event) {
        if (!_modalUserId) return;
        await _withButtonLock(event, async () => {
            try {
                const data = await API.post('/api/v1/admin/subscriptions/action/', {
                    action: 'send_payment_succeeded_email',
                    user_id: _modalUserId,
                });
                if (data.success) {
                    Toast.show(data.message || 'Payment succeeded email sent', 'success');
                } else {
                    Toast.show(data.message || 'Failed to send payment succeeded email', 'warning');
                }
            } catch (error) {
                const msg = await _extractError(error, 'Failed to send payment succeeded email');
                Toast.show(msg, 'error');
            }
        });
    }

    function confirmDeactivate(userId, username) {
        document.getElementById('deactivate-user-id').value = userId;
        document.getElementById('deactivate-username').textContent = username;
        document.getElementById('deactivate-notes').value = '';
        document.getElementById('deactivate-modal').showModal();
    }

    async function executeDeactivate(event) {
        const userId = parseInt(document.getElementById('deactivate-user-id').value, 10);
        if (!userId || isNaN(userId)) {
            Toast.show('Invalid user ID', 'error');
            return;
        }

        await _withButtonLock(event, async () => {
            const notes = document.getElementById('deactivate-notes').value;

            try {
                const data = await API.post('/api/v1/admin/subscriptions/action/', {
                    action: 'force_deactivate',
                    user_id: userId,
                    notes: notes,
                });

                if (data.success) {
                    Toast.show(data.message || 'Subscription deactivated', 'success');
                    document.getElementById('deactivate-modal').close();
                    // Reload page to reflect changes
                    setTimeout(() => window.location.reload(), 1000);
                } else {
                    Toast.show(data.message || 'Failed to deactivate', 'error');
                }
            } catch (error) {
                const msg = await _extractError(error, 'Failed to deactivate subscription');
                Toast.show(msg, 'error');
            }
        });
    }

    async function resendActionRequiredEmail(userId, event) {
        await _withButtonLock(event, async () => {
            try {
                const data = await API.post('/api/v1/admin/subscriptions/action/', {
                    action: 'resend_action_required_email',
                    user_id: userId,
                });
                if (data.success) {
                    Toast.show(data.message || 'Action required email sent', 'success');
                } else {
                    Toast.show(data.message || 'Failed to send email', 'warning');
                }
            } catch (error) {
                const msg = await _extractError(error, 'Failed to send action required email');
                Toast.show(msg, 'error');
            }
        });
    }

    // ── Expose to global scope ───────────────────────────────────────

    window.PlatPursuit.SubAdmin = {
        resendEmail,
        resendNotification,
        resendActionRequiredEmail,
        showUserDetail,
        sendWelcomeEmail,
        sendPaymentSucceededEmail,
        confirmDeactivate,
        executeDeactivate,
    };

})();
