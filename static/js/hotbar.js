/**
 * Hotbar Sync Status Manager
 * Handles real-time sync status polling and UI updates for the profile sync hotbar
 */

document.addEventListener('DOMContentLoaded', () => {
    // Get container with data attributes
    const container = document.getElementById('hotbar-container');
    if (!container) return;

    // Read URLs from data attributes
    const syncStatusUrl = container.dataset.urlSync;
    const triggerSyncUrl = container.dataset.urlTrigger;
    const addSyncStatusUrl = container.dataset.urlAddSync;
    const csrfToken = container.dataset.csrf;
    const initialStatus = container.dataset.syncStatus;
    const initialSeconds = parseInt(container.dataset.seconds) || 0;

    // DOM elements - Profile sync
    const syncUser = document.getElementById('sync-user');
    const syncBtn = document.getElementById('sync-now-btn');
    const syncBadge = document.getElementById('sync-badge');
    const syncDiv = document.getElementById('sync-progress-div');
    const syncProgress = document.getElementById('sync-progress-bar');
    const syncPercent = document.getElementById('sync-progress-percent');
    const syncAnnouncement = document.getElementById('sync-status-announcement');

    // DOM elements - Add sync
    const addSyncBtn = document.getElementById('add-sync-btn');
    const addSyncLoad = document.getElementById('add-sync-load');
    const addSyncAnchor = document.getElementById('add-sync-anchor');
    const addSyncInput = document.getElementById('add-sync-input');
    const addSyncErrorText = document.getElementById('add-sync-error-text');
    const syncForm = document.getElementById('sync-form');

    // DOM elements - Hotbar toggle
    const hotbarContainer = document.getElementById('hotbar-container');
    const toggleBtn = document.getElementById('hotbar-toggle');
    const toggleIcon = document.getElementById('toggle-icon');

    // Polling configuration
    let pollingInterval;
    let pollingStartTime;
    let addSyncInterval;
    const currentInterval = 2000;
    const extendedInterval = 10000;
    const extensionThreshold = 60000;
    let activeInterval = currentInterval;

    // Cookie configuration
    const cookieName = 'hotbar_hidden';
    const cookieDuration = 30 * 24 * 60 * 60 * 1000; // 30 days

    /**
     * Updates the hotbar UI based on sync status data
     * @param {Object} data - Sync status data from server
     */
    function updateHotbar(data) {
        console.log(data.sync_target);

        if (data.sync_status === 'synced') {
            // Update UI for synced status
            syncUser?.classList.remove('hidden', 'xl:block');
            syncBadge?.classList.remove('badge-warning', 'badge-error');
            syncBadge?.classList.add('badge-success');
            if (syncBadge) syncBadge.textContent = 'Synced!';
            if (syncDiv) syncDiv.hidden = true;

            // Announce to screen readers
            if (syncAnnouncement) {
                syncAnnouncement.textContent = 'Profile sync completed successfully';
            }

            // Stop polling and start countdown
            if (pollingInterval) clearInterval(pollingInterval);
            startSyncCountdown(data.seconds_to_next_sync);

        } else if (data.sync_status === 'syncing') {
            // Update UI for syncing status
            syncUser?.classList.add('hidden', 'xl:block');
            syncBadge?.classList.remove('badge-success', 'badge-error');
            syncBadge?.classList.add('badge-warning');
            if (syncBadge) syncBadge.textContent = 'Syncing...';
            if (syncBtn) syncBtn.hidden = true;
            if (syncDiv) {
                syncDiv.hidden = false;
                syncDiv.classList.remove('hidden');
            }
            if (syncProgress) {
                syncProgress.value = data.sync_progress;
                syncProgress.max = data.sync_target;
            }
            if (syncPercent) {
                syncPercent.textContent = `${parseInt(data.sync_percentage)}%`;
            }

            // Announce to screen readers
            if (syncAnnouncement) {
                syncAnnouncement.textContent = `Syncing in progress: ${parseInt(data.sync_percentage)}% complete`;
            }

        } else {
            // Update UI for error status
            syncBadge?.classList.remove('badge-success', 'badge-warning');
            syncBadge?.classList.add('badge-error');
            if (syncBadge) syncBadge.textContent = 'Error';
            if (syncDiv) syncDiv.hidden = true;

            // Announce to screen readers
            if (syncAnnouncement) {
                syncAnnouncement.textContent = 'Profile sync encountered an error';
            }

            // Stop polling and start countdown
            if (pollingInterval) clearInterval(pollingInterval);
            startSyncCountdown(data.seconds_to_next_sync);
        }
    }

    /**
     * Updates the sync button display
     * @param {number} seconds - Seconds until next sync is available
     */
    function updateSyncButton(seconds) {
        if (!syncBtn) return;

        if (seconds <= 0) {
            syncBtn.innerHTML = 'Sync Now!';
            syncBtn.disabled = false;
            syncBtn.setAttribute('aria-label', 'Sync profile now');
        } else {
            syncBtn.innerHTML = `${formatHMS(seconds)}`;
            syncBtn.disabled = true;
            syncBtn.setAttribute('aria-label', `Sync available in ${formatHMS(seconds)}`);
        }
    }

    /**
     * Formats seconds into HH:MM:SS format
     * @param {number} seconds - Seconds to format
     * @returns {string} Formatted time string
     */
    function formatHMS(seconds) {
        const h = Math.floor(seconds / 3600).toString().padStart(2, '0');
        const m = Math.floor((seconds % 3600) / 60).toString().padStart(2, '0');
        const s = Math.floor(seconds % 60).toString().padStart(2, '0');
        return `${h}:${m}:${s}`;
    }

    /**
     * Starts countdown timer for next available sync
     * @param {number} seconds - Initial seconds for countdown
     */
    function startSyncCountdown(seconds) {
        if (!syncBtn) return;

        syncBtn.hidden = false;
        syncBtn.classList.remove('hidden');

        const interval = setInterval(() => {
            if (seconds <= 0) {
                clearInterval(interval);
                updateSyncButton(seconds);
            } else {
                seconds--;
                updateSyncButton(seconds);
            }
        }, 1000);

        updateSyncButton(seconds);
    }

    /**
     * Polls the server for current sync status
     */
    function pollSyncStatus() {
        if (!syncStatusUrl) return;

        fetch(syncStatusUrl)
            .then(response => response.json())
            .then(data => updateHotbar(data))
            .catch(error => console.error('Polling error:', error));
    }

    /**
     * Starts polling for sync status with adaptive intervals
     */
    function startPolling() {
        pollingStartTime = Date.now();

        pollingInterval = setInterval(() => {
            const elapsed = Date.now() - pollingStartTime;

            // Extend polling interval after threshold
            if (elapsed > extensionThreshold && activeInterval !== extendedInterval) {
                clearInterval(pollingInterval);
                activeInterval = extendedInterval;
                pollingInterval = setInterval(pollSyncStatus, activeInterval);
            }

            pollSyncStatus();
        }, activeInterval);
    }

    /**
     * Triggers a manual sync when user clicks sync button
     */
    function triggerSync() {
        if (!triggerSyncUrl || !syncBtn || syncBtn.disabled) return;

        // Update UI to show syncing state
        syncUser?.classList.add('hidden', 'xl:block');
        syncBadge?.classList.remove('badge-success', 'badge-error');
        syncBadge?.classList.add('badge-warning');
        if (syncBadge) syncBadge.textContent = 'Syncing...';
        if (syncBtn) syncBtn.hidden = true;
        if (syncDiv) {
            syncDiv.hidden = false;
            syncDiv.classList.remove('hidden');
        }

        // Make API request to trigger sync
        fetch(triggerSyncUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({})
        })
        .then(response => {
            if (!response.ok) {
                throw new Error('Sync failed');
            }
            startPolling();
            return response.json();
        })
        .then(data => {
            if (data.error) {
                console.error(data.error);
                clearInterval(pollingInterval);
            }
        })
        .catch(error => {
            console.error('Sync trigger error:', error);
            clearInterval(pollingInterval);
        });
    }

    // Attach sync button event listener
    if (syncBtn) {
        syncBtn.addEventListener('click', triggerSync);
    }

    // Initialize sync status based on initial state
    if (initialStatus === 'synced') {
        startSyncCountdown(initialSeconds);
    } else if (initialStatus === 'syncing') {
        startPolling();
    }

    /**
     * Checks the status of a newly added sync
     * @param {Object} data - Add sync status data from server
     */
    function checkAddSync(data) {
        console.log(data);

        if (data.sync_status === 'error') {
            if (addSyncLoad) addSyncLoad.hidden = true;
            if (addSyncInput) addSyncInput.hidden = true;
            if (addSyncErrorText) {
                addSyncErrorText.classList.remove('hidden');
                addSyncErrorText.hidden = false;
            }
            clearInterval(addSyncInterval);
        } else if (data.account_id) {
            if (addSyncLoad) addSyncLoad.hidden = true;
            if (addSyncAnchor) {
                addSyncAnchor.href = data.slug;
                addSyncAnchor.classList.remove('hidden');
                addSyncAnchor.hidden = false;
            }
            clearInterval(addSyncInterval);
        }
    }

    /**
     * Polls the server for add sync status
     * @param {string} psn_username - PSN username to check
     */
    function pollAddSync(psn_username) {
        if (!psn_username || !addSyncStatusUrl) {
            console.error('psn_username is required for polling');
            return;
        }

        const url = `${addSyncStatusUrl}?psn_username=${encodeURIComponent(psn_username)}`;
        fetch(url)
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error. status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => checkAddSync(data))
            .catch(error => console.error('Polling error:', error));
    }

    /**
     * Handles the add sync form submission
     */
    if (syncForm) {
        syncForm.addEventListener('submit', (e) => {
            e.preventDefault();

            const formData = new FormData(e.target);
            fetch(e.target.action, {
                method: 'POST',
                body: formData,
                headers: {'X-Requested-With': 'XMLHttpRequest'}
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    if (addSyncBtn) addSyncBtn.hidden = true;
                    if (addSyncLoad) {
                        addSyncLoad.hidden = false;
                        addSyncLoad.classList.remove('hidden');
                    }

                    // Start polling after a delay
                    setTimeout(() => {
                        addSyncInterval = setInterval(() => pollAddSync(data.psn_username), 2500);
                    }, 2500);
                } else {
                    alert(data.error);
                }
            })
            .catch(error => console.error('Sync error:', error));
        });
    }

    // Hotbar toggle functionality

    /**
     * Gets a cookie value by name
     * @param {string} name - Cookie name
     * @returns {string|null} Cookie value or null
     */
    function getCookie(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop().split(';').shift();
        return null;
    }

    /**
     * Sets a cookie with name, value, and duration
     * @param {string} name - Cookie name
     * @param {string} value - Cookie value
     * @param {number} duration - Duration in milliseconds
     */
    function setCookie(name, value, duration) {
        const date = new Date();
        date.setTime(date.getTime() + duration);
        document.cookie = `${name}=${value}; expires=${date.toUTCString()}; path=/`;
    }

    /**
     * Toggles hotbar visibility
     */
    function toggleHotbar() {
        const isHidden = getCookie(cookieName) === 'true';

        if (isHidden) {
            // Show hotbar
            hotbarContainer?.classList.remove('-translate-y-[250%]', '-mb-[100px]');
            toggleIcon?.classList.add('rotate-180');
            toggleBtn?.classList.remove('-translate-y-[300%]');
            setCookie(cookieName, 'false', cookieDuration);
            if (toggleBtn) toggleBtn.setAttribute('aria-label', 'Hide hotbar');
        } else {
            // Hide hotbar - add negative margin to collapse the space
            hotbarContainer?.classList.add('-translate-y-[250%]', '-mb-[100px]');
            toggleIcon?.classList.remove('rotate-180');
            toggleBtn?.classList.add('-translate-y-[300%]');
            setCookie(cookieName, 'true', cookieDuration);
            if (toggleBtn) toggleBtn.setAttribute('aria-label', 'Show hotbar');
        }
    }

    // Initialize hotbar toggle state from cookie
    const hiddenCookie = getCookie(cookieName);
    if (hiddenCookie === 'true') {
        hotbarContainer?.classList.add('-translate-y-[250%]', '-mb-[100px]');
        toggleIcon?.classList.remove('rotate-180');
        toggleBtn?.classList.add('-translate-y-[300%]');
        if (toggleBtn) toggleBtn.setAttribute('aria-label', 'Show hotbar');
    } else {
        if (hotbarContainer) hotbarContainer.style.display = 'false';
        hotbarContainer?.classList.remove('-translate-y-[250%]', '-mb-[100px]');
        toggleIcon?.classList.add('rotate-180');
        toggleBtn?.classList.remove('-translate-y-[300%]');
        if (toggleBtn) toggleBtn.setAttribute('aria-label', 'Hide hotbar');

        setTimeout(() => {
            if (hotbarContainer) hotbarContainer.style.display = 'true';
        }, 500);

        if (hiddenCookie === null) {
            setCookie(cookieName, 'false', cookieDuration);
        }
    }

    // Attach toggle button event listener
    if (toggleBtn) {
        toggleBtn.addEventListener('click', toggleHotbar);
    }
});
