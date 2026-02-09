/**
 * Hotbar Sync Status Manager
 * Handles real-time sync status polling and UI updates for the profile sync hotbar
 */

document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('hotbar-container');
    if (!container) return;

    // Read URLs and initial state from data attributes
    const syncStatusUrl = container.dataset.urlSync;
    const triggerSyncUrl = container.dataset.urlTrigger;
    const addSyncStatusUrl = container.dataset.urlAddSync;
    const initialStatus = container.dataset.syncStatus;
    const initialSeconds = parseInt(container.dataset.seconds) || 0;

    // DOM elements - Profile sync
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

    // DOM elements - Toggle
    const wrapper = document.getElementById('hotbar-wrapper');
    const toggleBtn = document.getElementById('hotbar-toggle');
    const toggleIcon = document.getElementById('toggle-icon');

    // DOM elements - Mobile search
    const mobileSearchToggle = document.getElementById('mobile-search-toggle');
    const mobileSearchRow = document.getElementById('mobile-search-row');

    // Polling state
    let pollingTimeout;
    let pollingStartTime;
    let addSyncInterval;
    const INITIAL_POLL_INTERVAL = 2000;
    const EXTENDED_POLL_INTERVAL = 10000;
    const EXTENSION_THRESHOLD = 60000;
    let activeInterval = INITIAL_POLL_INTERVAL;

    // Toggle state
    const STORAGE_KEY = 'hotbar_hidden';

    // --- Helpers ---

    function show(el) { el?.classList.remove('hidden'); }
    function hide(el) { el?.classList.add('hidden'); }

    // --- Profile Sync Status ---

    /**
     * Updates the hotbar UI based on sync status data
     */
    function updateHotbar(data) {
        if (data.sync_status === 'synced') {
            syncBadge?.classList.remove('badge-warning', 'badge-error');
            syncBadge?.classList.add('badge-success');
            if (syncBadge) syncBadge.textContent = 'Synced!';
            hide(syncDiv);

            if (syncAnnouncement) {
                syncAnnouncement.textContent = 'Profile sync completed successfully';
            }

            stopPolling();
            startSyncCountdown(data.seconds_to_next_sync);
            PlatPursuit.ToastManager.success('Profile sync complete!');

        } else if (data.sync_status === 'syncing') {
            syncBadge?.classList.remove('badge-success', 'badge-error');
            syncBadge?.classList.add('badge-warning');
            if (syncBadge) syncBadge.textContent = 'Syncing...';
            hide(syncBtn);
            show(syncDiv);

            if (syncProgress) {
                syncProgress.value = data.sync_percentage;
            }
            if (syncPercent) {
                syncPercent.textContent = `${parseInt(data.sync_percentage)}%`;
            }

            if (syncAnnouncement) {
                syncAnnouncement.textContent = `Syncing in progress: ${parseInt(data.sync_percentage)}% complete`;
            }

        } else {
            syncBadge?.classList.remove('badge-success', 'badge-warning');
            syncBadge?.classList.add('badge-error');
            if (syncBadge) syncBadge.textContent = 'Error';
            hide(syncDiv);

            if (syncAnnouncement) {
                syncAnnouncement.textContent = 'Profile sync encountered an error';
            }

            stopPolling();
            startSyncCountdown(data.seconds_to_next_sync);
        }
    }

    /**
     * Updates the sync button display with countdown or ready state
     */
    function updateSyncButton(seconds) {
        if (!syncBtn) return;

        if (seconds <= 0) {
            syncBtn.textContent = 'Sync Now!';
            syncBtn.disabled = false;
            syncBtn.setAttribute('aria-label', 'Sync profile now');
        } else {
            syncBtn.textContent = PlatPursuit.TimeFormatter.countdown(seconds);
            syncBtn.disabled = true;
            syncBtn.setAttribute('aria-label', `Sync available in ${PlatPursuit.TimeFormatter.countdown(seconds)}`);
        }
    }

    /**
     * Starts countdown timer for next available sync
     */
    function startSyncCountdown(seconds) {
        if (!syncBtn) return;

        show(syncBtn);
        updateSyncButton(seconds);

        const interval = setInterval(() => {
            seconds--;
            updateSyncButton(seconds);
            if (seconds <= 0) clearInterval(interval);
        }, 1000);
    }

    // --- Polling ---

    /**
     * Polls the server for current sync status
     */
    function pollSyncStatus() {
        if (!syncStatusUrl) return;

        PlatPursuit.API.get(syncStatusUrl)
            .then(data => updateHotbar(data))
            .catch(error => console.error('Polling error:', error));
    }

    /**
     * Starts polling with chained setTimeout for adaptive intervals
     */
    function startPolling() {
        pollingStartTime = Date.now();
        activeInterval = INITIAL_POLL_INTERVAL;

        function scheduleNext() {
            pollingTimeout = setTimeout(() => {
                pollSyncStatus();

                const elapsed = Date.now() - pollingStartTime;
                if (elapsed > EXTENSION_THRESHOLD) {
                    activeInterval = EXTENDED_POLL_INTERVAL;
                }

                scheduleNext();
            }, activeInterval);
        }

        pollSyncStatus();
        scheduleNext();
    }

    /**
     * Stops any active polling
     */
    function stopPolling() {
        if (pollingTimeout) {
            clearTimeout(pollingTimeout);
            pollingTimeout = null;
        }
    }

    /**
     * Triggers a manual sync
     */
    function triggerSync() {
        if (!triggerSyncUrl || !syncBtn || syncBtn.disabled) return;

        // Optimistic UI update
        syncBadge?.classList.remove('badge-success', 'badge-error');
        syncBadge?.classList.add('badge-warning');
        if (syncBadge) syncBadge.textContent = 'Syncing...';
        hide(syncBtn);
        show(syncDiv);

        PlatPursuit.API.post(triggerSyncUrl, {})
            .then(data => {
                if (data.error) {
                    console.error(data.error);
                    stopPolling();
                } else {
                    startPolling();
                }
            })
            .catch(error => {
                console.error('Sync trigger error:', error);
                stopPolling();
                PlatPursuit.ToastManager.error('Failed to start sync. Please try again.');
            });
    }

    if (syncBtn) {
        syncBtn.addEventListener('click', triggerSync);
    }

    // Initialize based on initial state
    if (initialStatus === 'synced') {
        startSyncCountdown(initialSeconds);
    } else if (initialStatus === 'syncing') {
        startPolling();
    }

    // --- Add Sync (Search) ---

    /**
     * Checks the status of a newly added sync
     */
    function checkAddSync(data) {
        if (data.sync_status === 'error') {
            hide(addSyncLoad);
            hide(addSyncInput);
            if (addSyncErrorText) {
                addSyncErrorText.textContent = 'Sync error: check spelling or account permissions, then refresh and try again.';
                show(addSyncErrorText);
            }
            clearInterval(addSyncInterval);
        } else if (data.account_id) {
            hide(addSyncLoad);
            if (addSyncAnchor) {
                addSyncAnchor.href = data.slug;
                show(addSyncAnchor);
            }
            clearInterval(addSyncInterval);
        }
    }

    /**
     * Polls for add sync status
     */
    function pollAddSync(psn_username) {
        if (!psn_username || !addSyncStatusUrl) return;

        const url = `${addSyncStatusUrl}?psn_username=${encodeURIComponent(psn_username)}`;
        PlatPursuit.API.get(url)
            .then(data => checkAddSync(data))
            .catch(error => console.error('Add sync polling error:', error));
    }

    /**
     * Handles sync form submission (shared between desktop and mobile forms)
     */
    function handleSyncFormSubmit(e) {
        e.preventDefault();

        const formData = new FormData(e.target);
        PlatPursuit.API.postFormData(e.target.action, formData)
            .then(data => {
                if (data.success) {
                    hide(addSyncBtn);
                    show(addSyncLoad);

                    setTimeout(() => {
                        addSyncInterval = setInterval(() => pollAddSync(data.psn_username), 2500);
                    }, 2500);
                } else {
                    PlatPursuit.ToastManager.error(data.error || 'Failed to sync profile. Check the username and try again.');
                }
            })
            .catch(error => {
                console.error('Sync form error:', error);
                PlatPursuit.ToastManager.error('Failed to sync profile. Please try again.');
            });
    }

    document.querySelectorAll('#sync-form, #sync-form-mobile').forEach(form => {
        form.addEventListener('submit', handleSyncFormSubmit);
    });

    // --- Mobile Search Toggle ---

    if (mobileSearchToggle && mobileSearchRow) {
        mobileSearchToggle.addEventListener('click', () => {
            const isCollapsed = mobileSearchRow.classList.contains('max-h-0');
            if (isCollapsed) {
                mobileSearchRow.classList.remove('max-h-0');
                mobileSearchRow.classList.add('max-h-24');
                const input = mobileSearchRow.querySelector('input');
                if (input) setTimeout(() => input.focus(), 300);
            } else {
                mobileSearchRow.classList.add('max-h-0');
                mobileSearchRow.classList.remove('max-h-24');
            }
        });
    }

    // --- Hotbar Toggle ---

    function isHotbarHidden() {
        return localStorage.getItem(STORAGE_KEY) === 'true';
    }

    function setHotbarHidden(hidden) {
        localStorage.setItem(STORAGE_KEY, hidden ? 'true' : 'false');
    }

    function collapseHotbar() {
        // Set explicit height so transition has a start value (can't transition from 'none')
        container.style.maxHeight = container.scrollHeight + 'px';
        // Force reflow, then collapse to 0
        void container.offsetHeight;
        container.style.maxHeight = '0';
        container.style.borderWidth = '0';
        // Pull toggle flush against navbar (negate main's py-2 padding)
        wrapper.style.top = '0px';
        wrapper.style.marginTop = '-8px';
        toggleIcon?.classList.remove('rotate-180');
        toggleBtn?.classList.add('hotbar-toggle-pulse');
        toggleBtn?.setAttribute('aria-label', 'Show hotbar');
    }

    function expandHotbar() {
        // Restore sticky gap from viewport top
        wrapper.style.top = '';
        wrapper.style.marginTop = '';
        container.style.borderWidth = '';
        container.style.maxHeight = container.scrollHeight + 'px';
        toggleIcon?.classList.add('rotate-180');
        toggleBtn?.classList.remove('hotbar-toggle-pulse');
        toggleBtn?.setAttribute('aria-label', 'Hide hotbar');
        // After transition, set to 'none' so content can reflow naturally
        container.addEventListener('transitionend', function handler() {
            if (!isHotbarHidden()) {
                container.style.maxHeight = 'none';
            }
            container.removeEventListener('transitionend', handler);
        });
    }

    function toggleHotbar() {
        const hidden = isHotbarHidden();
        setHotbarHidden(!hidden);
        if (hidden) {
            expandHotbar();
        } else {
            collapseHotbar();
        }
    }

    // Initialize toggle state immediately (no animation)
    if (isHotbarHidden()) {
        container.style.maxHeight = '0';
        container.style.borderWidth = '0';
        wrapper.style.top = '0px';
        wrapper.style.marginTop = '-8px';
        toggleIcon?.classList.remove('rotate-180');
        toggleBtn?.classList.add('hotbar-toggle-pulse');
        toggleBtn?.setAttribute('aria-label', 'Show hotbar');
    } else {
        container.style.maxHeight = 'none';
        toggleIcon?.classList.add('rotate-180');
        toggleBtn?.setAttribute('aria-label', 'Hide hotbar');
    }
    if (localStorage.getItem(STORAGE_KEY) === null) {
        setHotbarHidden(false);
    }

    if (toggleBtn) {
        toggleBtn.addEventListener('click', toggleHotbar);
    }

    // Remove loading class to reveal hotbar with fade-in
    container.classList.remove('hotbar-loading');

    // --- Cleanup ---

    window.addEventListener('beforeunload', () => {
        stopPolling();
        if (addSyncInterval) clearInterval(addSyncInterval);
    });
});
