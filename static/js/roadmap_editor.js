/**
 * Roadmap Editor
 *
 * Staff-only editor for creating/editing game roadmaps.
 * Reuses: PlatPursuit.API, DragReorderManager, ToastManager, debounce, HTMLUtils
 */
(function() {
    'use strict';

    const API = window.PlatPursuit.API;
    const Toast = window.PlatPursuit.ToastManager;
    const debounce = window.PlatPursuit.debounce;

    const TROPHY_TYPE_COLORS = {
        platinum: 'badge-info',
        gold: 'badge-warning',
        silver: 'badge-ghost',
        bronze: 'badge-accent',
    };

    // ------------------------------------------------------------------ //
    //  State
    // ------------------------------------------------------------------ //

    const editorEl = document.getElementById('roadmap-editor');
    if (!editorEl) return;

    const roadmapId = parseInt(editorEl.dataset.roadmapId, 10);
    const tabsData = JSON.parse(document.getElementById('roadmap-tabs-data')?.textContent || '[]');
    const trophiesByGroup = JSON.parse(document.getElementById('roadmap-trophies-data')?.textContent || '{}');
    const profilesById = JSON.parse(document.getElementById('roadmap-profiles-data')?.textContent || '{}');
    const authorRole = editorEl.dataset.authorRole || 'writer';
    const canDelete = editorEl.dataset.authorCanDelete === 'true';
    const canPublish = editorEl.dataset.authorCanPublish === 'true';
    const viewerProfileId = parseInt(editorEl.dataset.viewerProfileId, 10) || null;
    // Editors and publishers bypass section-ownership scoping. Writers can
    // only edit sections they own (or untouched/ownerless sections).
    const bypassOwnershipScope = canDelete; // canDelete proxies "is editor+"

    let activeTabId = tabsData.length ? tabsData[0].id : null;
    let hasUnsaved = false;
    let dragManagers = {};

    // ------------------------------------------------------------------ //
    //  Lock + Branch-and-Merge Controller
    //
    //  The editor never mutates live records directly. On load we acquire a
    //  RoadmapEditLock; autosaves go to lock.branch_payload via PATCH /branch/;
    //  an explicit Save click POSTs /merge/, which atomically applies the
    //  payload to live records and creates a RoadmapRevision. The lock auto-
    //  expires after 15 min of idle or 1 hour absolute. JS heartbeats every
    //  2 min keep the idle timer fresh.
    // ------------------------------------------------------------------ //

    // Heartbeat: timer fires every 2 min as a keep-alive, but only sends a
    // heartbeat if the user has been active recently. Activity itself can also
    // trigger a heartbeat (debounced), so locks stay fresh during real work
    // and quietly go stale when the writer steps away.
    const HEARTBEAT_TIMER_MS = 2 * 60 * 1000;
    const HEARTBEAT_ACTIVITY_DEBOUNCE_MS = 60 * 1000;
    const ACTIVITY_RECENT_WINDOW_MS = 5 * 60 * 1000;
    // Warning thresholds (seconds-until-expiry).
    const IDLE_WARNING_S = 180;     // T-3min: gentle toast
    const IDLE_CRITICAL_S = 30;     // T-30s: banner
    const HARDCAP_WARNING_S = 300;  // T-5min on the hour cap: banner
    const PAYLOAD_VERSION = 1;
    const LEGACY_TAB_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/?$`
    );
    const LEGACY_STEP_LIST_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/steps/?$`
    );
    const LEGACY_STEP_REORDER_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/steps/reorder/?$`
    );
    const LEGACY_STEP_DETAIL_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/steps/(-?\\d+)/?$`
    );
    const LEGACY_STEP_TROPHIES_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/steps/(-?\\d+)/trophies/?$`
    );
    const LEGACY_TROPHY_GUIDE_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/trophy-guides/(\\d+)/?$`
    );

    /**
     * Maintains the in-memory branch_payload (canonical wire shape) and
     * mutates it in response to legacy-style API calls from the editor UI.
     * A single debounced PATCH /lock/branch/ pushes the latest state.
     */
    const BranchProxy = {
        state: null,
        nextLocalId: -1,
        pushTimer: null,
        lockLost: false,

        init(initialPayload) {
            this.state = initialPayload || { payload_version: PAYLOAD_VERSION, tabs: [] };
            if (!this.state.payload_version) this.state.payload_version = PAYLOAD_VERSION;
        },

        findTab(tabId) {
            return this.state.tabs.find(t => t.id === tabId);
        },

        findStep(tabId, stepId) {
            const tab = this.findTab(tabId);
            return tab ? tab.steps.find(s => s.id === stepId) : null;
        },

        nextId() {
            return this.nextLocalId--;
        },

        schedulePush() {
            if (this.lockLost) return;
            hasUnsaved = true;
            setSaveStatus('unsaved');
            if (this.pushTimer) clearTimeout(this.pushTimer);
            this.pushTimer = setTimeout(() => this.push(), 1500);
        },

        async pushNow() {
            if (this.pushTimer) {
                clearTimeout(this.pushTimer);
                this.pushTimer = null;
            }
            return this.push();
        },

        async push() {
            if (this.lockLost) return;
            try {
                setSaveStatus('saving');
                const result = await API.patch(
                    `/api/v1/roadmap/${roadmapId}/lock/branch/`,
                    { branch_payload: this.state }
                );
                if (result.lock_lost) {
                    LockController.handleLockLost();
                    return;
                }
                LockController.applyLockState(result.lock);
                setSaveStatus('saved');
            } catch (err) {
                const errData = await err.response?.json().catch(() => null);
                if (errData?.lock_lost || err.response?.status === 409) {
                    LockController.handleLockLost();
                    return;
                }
                setSaveStatus('error');
                Toast.show(errData?.error || 'Autosave failed.', 'error');
            }
        },

        // Translate the legacy URL+method+body into a state mutation.
        // Returns a synthetic response that matches what the legacy endpoint used to.
        handle(method, url, body) {
            if (this.lockLost) {
                throw new Error('Lock has been lost. Refresh to continue editing.');
            }

            const m = method.toLowerCase();
            let match;

            // Tab field update: PATCH /tab/Y/
            if ((match = url.match(LEGACY_TAB_PATTERN)) && m === 'patch') {
                const tabId = parseInt(match[1], 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                Object.keys(body || {}).forEach(k => {
                    if (k in tab) tab[k] = body[k];
                });
                this.schedulePush();
                return { ...body };
            }

            // Step create: POST /tab/Y/steps/
            if ((match = url.match(LEGACY_STEP_LIST_PATTERN)) && m === 'post') {
                const tabId = parseInt(match[1], 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                const newStep = {
                    id: this.nextId(),
                    title: (body.title || '').trim() || 'New Step',
                    description: body.description || '',
                    youtube_url: body.youtube_url || '',
                    order: tab.steps.length,
                    trophy_ids: [],
                    // Stamp the viewer as owner client-side so the badge says
                    // "You" immediately. Server stamps the actual created_by
                    // on merge using the merging profile's id.
                    created_by_id: viewerProfileId,
                    last_edited_by_id: viewerProfileId,
                };
                tab.steps.push(newStep);
                this.schedulePush();
                return { ...newStep };
            }

            // Step reorder: POST /tab/Y/steps/reorder/
            if ((match = url.match(LEGACY_STEP_REORDER_PATTERN)) && m === 'post') {
                const tabId = parseInt(match[1], 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                const orderedIds = (body.step_ids || []).map(x => parseInt(x, 10));
                const stepMap = {};
                tab.steps.forEach(s => { stepMap[s.id] = s; });
                tab.steps = orderedIds.map(id => stepMap[id]).filter(Boolean);
                tab.steps.forEach((s, i) => { s.order = i; });
                this.schedulePush();
                return { status: 'ok' };
            }

            // Step update / delete: PATCH or DELETE /tab/Y/steps/Z/
            if ((match = url.match(LEGACY_STEP_DETAIL_PATTERN)) && (m === 'patch' || m === 'delete')) {
                const tabId = parseInt(match[1], 10);
                const stepId = parseInt(match[2], 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                if (m === 'delete') {
                    tab.steps = tab.steps.filter(s => s.id !== stepId);
                    this.schedulePush();
                    return null;
                }
                const step = tab.steps.find(s => s.id === stepId);
                if (!step) throw new Error(`Step ${stepId} not in branch.`);
                Object.keys(body || {}).forEach(k => {
                    if (k in step) step[k] = body[k];
                });
                this.schedulePush();
                return { id: step.id, title: step.title, description: step.description, order: step.order };
            }

            // Step trophy associations: PUT /tab/Y/steps/Z/trophies/
            if ((match = url.match(LEGACY_STEP_TROPHIES_PATTERN)) && m === 'put') {
                const tabId = parseInt(match[1], 10);
                const stepId = parseInt(match[2], 10);
                const step = this.findStep(tabId, stepId);
                if (!step) throw new Error(`Step ${stepId} not in branch.`);
                step.trophy_ids = (body.trophy_ids || []).map(x => parseInt(x, 10));
                this.schedulePush();
                return { status: 'ok' };
            }

            // Trophy guide upsert / delete: PUT or DELETE /tab/Y/trophy-guides/T/
            if ((match = url.match(LEGACY_TROPHY_GUIDE_PATTERN)) && (m === 'put' || m === 'delete')) {
                const tabId = parseInt(match[1], 10);
                const trophyId = parseInt(match[2], 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                if (m === 'delete') {
                    tab.trophy_guides = tab.trophy_guides.filter(g => g.trophy_id !== trophyId);
                    this.schedulePush();
                    return null;
                }
                let guide = tab.trophy_guides.find(g => g.trophy_id === trophyId);
                const incomingBody = body.body || '';
                // Empty body deletes the guide (matching legacy endpoint behavior).
                if (!incomingBody.trim()) {
                    if (guide) {
                        tab.trophy_guides = tab.trophy_guides.filter(g => g.trophy_id !== trophyId);
                    }
                    this.schedulePush();
                    return null;
                }
                if (!guide) {
                    guide = {
                        id: this.nextId(),
                        trophy_id: trophyId,
                        body: '',
                        order: tab.trophy_guides.length,
                        is_missable: false,
                        is_online: false,
                        is_unobtainable: false,
                        // Stamp viewer as owner client-side; server re-stamps on merge.
                        created_by_id: viewerProfileId,
                        last_edited_by_id: viewerProfileId,
                    };
                    tab.trophy_guides.push(guide);
                }
                guide.body = incomingBody;
                if ('is_missable' in body) guide.is_missable = !!body.is_missable;
                if ('is_online' in body) guide.is_online = !!body.is_online;
                if ('is_unobtainable' in body) guide.is_unobtainable = !!body.is_unobtainable;
                this.schedulePush();
                return {
                    trophy_id: guide.trophy_id, body: guide.body,
                    is_missable: guide.is_missable, is_online: guide.is_online,
                    is_unobtainable: guide.is_unobtainable,
                };
            }

            throw new Error(`BranchProxy: unhandled ${method.toUpperCase()} ${url}`);
        },

        // Convert local state to wire format (negative ids -> null for new rows).
        toWirePayload() {
            const cloned = JSON.parse(JSON.stringify(this.state));
            (cloned.tabs || []).forEach(tab => {
                (tab.steps || []).forEach(step => {
                    if (typeof step.id === 'number' && step.id < 0) step.id = null;
                });
                (tab.trophy_guides || []).forEach(g => {
                    if (typeof g.id === 'number' && g.id < 0) g.id = null;
                });
            });
            return cloned;
        },
    };

    /**
     * Live timer chip + threshold-based warnings.
     *
     * Ticks every second using the most recent server lock state. Threshold
     * crossings (idle T-3min, idle T-30s, hard-cap T-5min) emit informational
     * warnings (no blocking modals — work is preserved either way under the
     * advisory model). Flags are reset on each successful heartbeat so a
     * writer who steps away repeatedly is warned each idle period.
     */
    const LockTimer = {
        lockSnapshot: null,
        tickHandle: null,
        idleWarningFired: false,
        idleCriticalFired: false,
        hardcapWarningFired: false,

        update(lock) {
            if (!lock) return;
            // Server gives us seconds_until_expiry / hard_cap_seconds_remaining
            // computed at request time. Re-anchor to local clock for tick math.
            const now = Date.now();
            this.lockSnapshot = {
                idleExpiresAtMs: now + (lock.seconds_until_expiry || 0) * 1000,
                hardCapAtMs: now + (lock.hard_cap_seconds_remaining || 0) * 1000,
                heldBySelf: !!lock.held_by_self,
                holderUsername: lock.holder_username,
            };
            // A successful heartbeat reset means we're no longer in any warning
            // window — clear flags so the next stale period gets re-warned.
            if ((lock.seconds_until_expiry || 0) > IDLE_WARNING_S) this.idleWarningFired = false;
            if ((lock.seconds_until_expiry || 0) > IDLE_CRITICAL_S) this.idleCriticalFired = false;
            if ((lock.hard_cap_seconds_remaining || 0) > HARDCAP_WARNING_S) this.hardcapWarningFired = false;
        },

        start() {
            if (this.tickHandle) clearInterval(this.tickHandle);
            this.tickHandle = setInterval(() => this.tick(), 1000);
            this.tick();
        },

        stop() {
            if (this.tickHandle) clearInterval(this.tickHandle);
            this.tickHandle = null;
        },

        tick() {
            if (!this.lockSnapshot || !this.lockSnapshot.heldBySelf) {
                this.renderChip(null);
                return;
            }
            const now = Date.now();
            const idleS = Math.max(0, Math.round((this.lockSnapshot.idleExpiresAtMs - now) / 1000));
            const capS = Math.max(0, Math.round((this.lockSnapshot.hardCapAtMs - now) / 1000));
            this.renderChip({ idleS, capS });

            if (idleS <= IDLE_WARNING_S && idleS > IDLE_CRITICAL_S && !this.idleWarningFired) {
                this.idleWarningFired = true;
                Toast.show(
                    'Session going idle in 3 min — others can claim the guide if you don\'t return.',
                    'info'
                );
            }
            if (idleS <= IDLE_CRITICAL_S && !this.idleCriticalFired) {
                this.idleCriticalFired = true;
                LockController.renderBanner('idle-critical', null);
                Toast.show(
                    'Lock about to go stale — your branch is safe, but others can take over.',
                    'warning'
                );
            }
            if (capS <= HARDCAP_WARNING_S && !this.hardcapWarningFired) {
                this.hardcapWarningFired = true;
                LockController.renderBanner('hardcap-warning', null);
                Toast.show(
                    'Editing for nearly an hour — consider clicking Save to checkpoint.',
                    'info'
                );
            }
        },

        renderChip(state) {
            const chip = document.getElementById('lock-timer-chip');
            if (!chip) return;
            const label = chip.querySelector('.lock-timer-label');
            const tooltip = chip.querySelector('.lock-timer-tooltip');
            if (!state) {
                chip.classList.add('hidden');
                chip.classList.remove('inline-flex');
                return;
            }
            chip.classList.remove('hidden');
            chip.classList.add('inline-flex');
            const fmt = (s) => {
                const m = Math.floor(s / 60);
                const r = s % 60;
                return `${m}:${r.toString().padStart(2, '0')}`;
            };
            // The chip displays the SESSION (hard-cap) countdown — that's the
            // meaningful "how much time do I have" number while actively
            // working. The idle timer just resets on every keystroke, so it's
            // noise in the chip; it's still surfaced in the tooltip on hover
            // and via toasts when it actually approaches expiry.
            chip.classList.remove(
                'border-success/30', 'text-success/80', 'bg-success/5',
                'border-warning/40', 'text-warning', 'bg-warning/10',
                'border-error/40', 'text-error', 'bg-error/10',
            );
            if (state.capS <= 60) {
                chip.classList.add('border-error/40', 'text-error', 'bg-error/10');
            } else if (state.capS <= 300) {
                chip.classList.add('border-warning/40', 'text-warning', 'bg-warning/10');
            } else {
                chip.classList.add('border-success/30', 'text-success/80', 'bg-success/5');
            }
            label.textContent = 'Session ' + fmt(state.capS);
            // Update only the inner tooltip text. We avoid `chip.title` because
            // the native browser tooltip refreshes on every attribute change,
            // which flickers annoyingly while hovering during the 1s ticks.
            // The CSS-hover tooltip below stays put and just shows new text.
            if (tooltip) {
                tooltip.textContent = `Session: ${fmt(state.capS)} · Idle: ${fmt(state.idleS)} · typing extends idle`;
            }
        },
    };

    /**
     * Lock lifecycle: acquire on load, activity-aware heartbeat, merge on
     * Save click, release on close. Advisory expiry semantics — a stale lock
     * still held by self resumes cleanly; takeover archives the displaced
     * branch as a recovery revision.
     */
    const LockController = {
        lockState: null,
        heartbeatTimerHandle: null,
        lastActivityAt: 0,
        lastHeartbeatAt: 0,

        async init() {
            try {
                const result = await API.post(`/api/v1/roadmap/${roadmapId}/lock/acquire/`, {});
                this.applyLockState(result.lock);
                BranchProxy.init(result.branch_payload);
                this.lastActivityAt = Date.now();
                this.installActivityListeners();
                this.startHeartbeatTimer();
                LockTimer.start();
                if (result.resumed_stale) {
                    this.renderBanner('resumed-stale', result.lock);
                } else if (result.archived_predecessor_revision_id) {
                    Toast.show(
                        'Previous editor session was idle — taken over and archived as a revision.',
                        'info'
                    );
                }
                return true;
            } catch (err) {
                const errData = await err.response?.json().catch(() => null);
                if (err.response?.status === 409 && errData?.lock) {
                    this.lockState = errData.lock;
                    BranchProxy.lockLost = true;
                    this.renderBanner('conflict', errData.lock);
                    enterReadOnlyMode(`Editor locked by ${errData.lock.holder_username || 'another author'}.`);
                    return false;
                }
                Toast.show(errData?.error || 'Failed to acquire edit lock.', 'error');
                enterReadOnlyMode('Failed to acquire edit lock.');
                return false;
            }
        },

        applyLockState(lock) {
            if (!lock) return;
            this.lockState = lock;
            LockTimer.update(lock);
        },

        installActivityListeners() {
            const onActivity = () => {
                this.lastActivityAt = Date.now();
                if (BranchProxy.lockLost) return;
                // Debounced "first activity in a while" heartbeat.
                if (Date.now() - this.lastHeartbeatAt > HEARTBEAT_ACTIVITY_DEBOUNCE_MS) {
                    this.heartbeat();
                }
            };
            ['keydown', 'mousedown', 'pointerdown', 'input'].forEach(evt => {
                editorEl.addEventListener(evt, onActivity, { passive: true });
            });
        },

        startHeartbeatTimer() {
            if (this.heartbeatTimerHandle) clearInterval(this.heartbeatTimerHandle);
            this.heartbeatTimerHandle = setInterval(() => {
                if (BranchProxy.lockLost) return;
                // Keep-alive only if the user has been active in the recent window.
                // Otherwise, let the lock go stale so others can take over.
                if (Date.now() - this.lastActivityAt < ACTIVITY_RECENT_WINDOW_MS) {
                    this.heartbeat();
                }
            }, HEARTBEAT_TIMER_MS);
        },

        async heartbeat() {
            if (BranchProxy.lockLost) return;
            this.lastHeartbeatAt = Date.now();
            try {
                const result = await API.post(
                    `/api/v1/roadmap/${roadmapId}/lock/heartbeat/`, {}
                );
                if (result.lock_lost) {
                    this.handleLockLost(true);
                    return;
                }
                this.applyLockState(result.lock);
                if (result.was_stale) {
                    Toast.show('Welcome back — your session resumed.', 'success');
                }
                this.renderBanner('hidden', null);
            } catch (err) {
                if (err.response?.status === 404 || err.response?.status === 409) {
                    this.handleLockLost(true);
                }
            }
        },

        handleLockLost(takenOver) {
            if (BranchProxy.lockLost) return;
            BranchProxy.lockLost = true;
            if (this.heartbeatTimerHandle) clearInterval(this.heartbeatTimerHandle);
            LockTimer.stop();
            LockTimer.renderChip(null);
            const msg = takenOver
                ? 'Your previous session was claimed by another author. Your in-progress work is archived in revisions (admin can recover it). Refresh to start a new session.'
                : 'Your edit lock was released. Refresh to take it back.';
            enterReadOnlyMode(msg);
            this.renderBanner('lost', null);
            Toast.show(takenOver ? 'Lock claimed by another author.' : 'Lock lost.', 'error');
        },

        async forceUnlock() {
            if (!confirm(
                'Force-break the current edit lock? The other writer\'s unsaved changes ' +
                'will be archived as a revision (recoverable from admin), ' +
                'but they will lose their session. Continue?'
            )) return;
            try {
                await API.post(`/api/v1/roadmap/${roadmapId}/lock/break/`, {});
                Toast.show('Lock broken. Refresh to acquire.', 'success');
                window.location.reload();
            } catch (err) {
                const errData = await err.response?.json().catch(() => null);
                Toast.show(errData?.error || 'Failed to break lock.', 'error');
            }
        },

        async save() {
            if (BranchProxy.lockLost) {
                Toast.show('Lock has been lost. Refresh to edit.', 'error');
                return;
            }
            await BranchProxy.pushNow();
            try {
                setSaveStatus('saving');
                const result = await API.post(
                    `/api/v1/roadmap/${roadmapId}/lock/merge/`,
                    { branch_payload: BranchProxy.toWirePayload() }
                );
                if (result.lock_lost) {
                    this.handleLockLost(true);
                    return;
                }
                hasUnsaved = false;
                setSaveStatus('saved');
                Toast.show(result.summary || 'Saved.', 'success');
                setTimeout(() => window.location.reload(), 500);
            } catch (err) {
                const errData = await err.response?.json().catch(() => null);
                if (errData?.lock_lost || err.response?.status === 409) {
                    this.handleLockLost(true);
                    return;
                }
                setSaveStatus('error');
                Toast.show(errData?.error || 'Save failed. Your changes are still in the branch.', 'error');
            }
        },

        async release() {
            if (BranchProxy.lockLost) return;
            try {
                if (BranchProxy.pushTimer) {
                    await BranchProxy.pushNow();
                }
                await API.post(`/api/v1/roadmap/${roadmapId}/lock/release/`, {});
            } catch (err) {
                // Best-effort release; lock auto-expires anyway.
            }
        },

        renderBanner(state, lock) {
            const banner = document.getElementById('lock-banner');
            if (!banner) return;
            banner.classList.remove(
                'hidden',
                'border-success/40', 'bg-success/10',
                'border-info/40', 'bg-info/10',
                'border-warning/40', 'bg-warning/10',
                'border-error/40', 'bg-error/10',
            );
            const msg = banner.querySelector('.lock-banner-msg');
            const action = banner.querySelector('.lock-banner-action');
            if (action) action.innerHTML = '';

            if (state === 'hidden') {
                banner.classList.add('hidden');
                return;
            }
            if (state === 'resumed-stale') {
                banner.classList.add('border-info/40', 'bg-info/10');
                msg.textContent = 'Welcome back — your previous session was idle and is now resumed. Your branch is intact.';
                return;
            }
            if (state === 'idle-critical') {
                banner.classList.add('border-warning/40', 'bg-warning/10');
                msg.textContent = 'Session about to go stale. Your branch is safe — others can take over the lock unless you interact.';
                return;
            }
            if (state === 'hardcap-warning') {
                banner.classList.add('border-warning/40', 'bg-warning/10');
                msg.textContent = 'You\'ve been editing for nearly an hour. Consider clicking Save to checkpoint your progress.';
                return;
            }
            if (state === 'conflict' && lock) {
                banner.classList.add('border-warning/40', 'bg-warning/10');
                const mins = Math.floor((lock.seconds_until_expiry || 0) / 60);
                msg.textContent = lock.is_stale
                    ? `Locked by ${lock.holder_username || 'another author'} (idle). Reload to take over — their branch will be archived.`
                    : `Locked by ${lock.holder_username || 'another author'} (expires in ~${mins} min). Editor is read-only.`;
                if (action && canPublish && !lock.is_stale) {
                    action.innerHTML = '<button id="force-unlock-btn" class="btn btn-xs btn-warning">Force unlock</button>';
                    action.querySelector('#force-unlock-btn').addEventListener('click', () => this.forceUnlock());
                }
                return;
            }
            if (state === 'lost') {
                banner.classList.add('border-error/40', 'bg-error/10');
                msg.textContent = 'Lock lost. Editor is read-only. Refresh to start a new session.';
                return;
            }
        },
    };

    function enterReadOnlyMode(reason) {
        editorEl.querySelectorAll('input, textarea, select, button').forEach(el => {
            if (el.closest('[data-readonly-exempt]')) return;
            el.disabled = true;
        });
        editorEl.classList.add('roadmap-editor-readonly');
    }

    /**
     * Render the "Owned by X" badge on a section row and apply a writer-
     * scoping read-only state if the current user can't edit it.
     *
     * @param rowEl  The DOM node representing the row (step card or trophy guide).
     * @param ownerId  Profile id of the row's owner (null for ownerless / fresh).
     * @param prefix  CSS prefix used in the row's owner-badge classnames
     *                (e.g. "step-owner" or "trophy-guide-owner").
     * @returns boolean — whether the current user can edit this row.
     */
    function applyOwnership(rowEl, ownerId, prefix) {
        const isOwnerless = !ownerId;
        const isMine = ownerId === viewerProfileId;
        const canEdit = bypassOwnershipScope || isOwnerless || isMine;

        const badge = rowEl.querySelector(`.${prefix}-badge`);
        if (badge) {
            const profile = ownerId ? profilesById[ownerId] : null;
            if (!profile) {
                // Ownerless: hide badge entirely.
                badge.classList.add('hidden');
                badge.classList.remove('inline-flex');
            } else {
                badge.classList.remove('hidden');
                badge.classList.add('inline-flex');
                const nameEl = badge.querySelector(`.${prefix}-name`);
                const avatarEl = badge.querySelector(`.${prefix}-avatar`);
                const fallbackEl = badge.querySelector(`.${prefix}-icon-fallback`);
                if (nameEl) nameEl.textContent = isMine ? 'You' : profile.display_name;
                if (avatarEl && profile.avatar_url) {
                    avatarEl.src = profile.avatar_url;
                    avatarEl.classList.remove('hidden');
                    if (fallbackEl) {
                        fallbackEl.classList.add('hidden');
                        fallbackEl.classList.remove('flex');
                    }
                } else if (fallbackEl) {
                    fallbackEl.textContent = (profile.display_name || '?').slice(0, 1).toUpperCase();
                    fallbackEl.classList.remove('hidden');
                    fallbackEl.classList.add('flex');
                    if (avatarEl) avatarEl.classList.add('hidden');
                }
                // Color the pill: muted for editable, warning tone when locked
                // out, success-y when it's mine.
                badge.classList.remove(
                    'bg-base-300/40', 'text-base-content/60',
                    'bg-warning/15', 'text-warning',
                    'bg-success/15', 'text-success',
                );
                if (!canEdit) {
                    badge.classList.add('bg-warning/15', 'text-warning');
                } else if (isMine) {
                    badge.classList.add('bg-success/15', 'text-success');
                } else {
                    badge.classList.add('bg-base-300/40', 'text-base-content/60');
                }
            }
        }

        if (!canEdit) {
            rowEl.classList.add('opacity-60');
            rowEl.querySelectorAll('input, textarea, button').forEach(el => {
                if (el.closest('[data-readonly-exempt]')) return;
                el.disabled = true;
            });
        }
        return canEdit;
    }

    // ------------------------------------------------------------------ //
    //  Save Status
    // ------------------------------------------------------------------ //

    const saveIcon = document.getElementById('save-icon');
    const saveText = document.getElementById('save-text');
    const saveStatus = document.getElementById('save-status');

    function setSaveStatus(state) {
        if (!saveStatus) return;
        switch (state) {
            case 'saving':
                saveStatus.className = 'flex items-center gap-1 text-xs text-warning/70 ml-auto shrink-0';
                saveIcon.innerHTML = '<circle class="animate-spin origin-center" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" fill="none" stroke-dasharray="40 60"/>';
                saveText.textContent = 'Saving...';
                break;
            case 'saved':
                saveStatus.className = 'flex items-center gap-1 text-xs text-success/70 ml-auto shrink-0';
                saveIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>';
                saveText.textContent = 'Saved';
                hasUnsaved = false;
                break;
            case 'unsaved':
                saveStatus.className = 'flex items-center gap-1 text-xs text-warning/70 ml-auto shrink-0';
                saveIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4m0 4h.01"/>';
                saveText.textContent = 'Unsaved';
                hasUnsaved = true;
                break;
            case 'error':
                saveStatus.className = 'flex items-center gap-1 text-xs text-error/70 ml-auto shrink-0';
                saveIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>';
                saveText.textContent = 'Error';
                break;
        }
    }

    /**
     * Legacy compatibility shim. The editor was originally written to mutate
     * live records via per-field/per-row REST endpoints; we keep all those
     * call sites unchanged but now route them through BranchProxy, which
     * mutates the in-memory branch_payload and schedules a single debounced
     * /lock/branch/ push. Publish toggles still hit the live endpoint
     * (publisher-only, server creates a published/unpublished revision).
     */
    async function apiCall(method, url, body) {
        if (url.includes(`/api/v1/roadmap/${roadmapId}/publish/`)) {
            try {
                return await API[method](url, body);
            } catch (err) {
                const errData = await err.response?.json().catch(() => null);
                Toast.show(errData?.error || 'Publish action failed.', 'error');
                throw err;
            }
        }
        try {
            return BranchProxy.handle(method, url, body);
        } catch (err) {
            Toast.show(err.message || 'An error occurred.', 'error');
            throw err;
        }
    }

    // ------------------------------------------------------------------ //
    //  Tab Switching
    // ------------------------------------------------------------------ //

    function initTabs() {
        document.querySelectorAll('.roadmap-tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const tabId = parseInt(btn.dataset.tabId, 10);
                switchTab(tabId);
            });
        });
    }

    function switchTab(tabId) {
        activeTabId = tabId;

        // Update button states
        document.querySelectorAll('.roadmap-tab-btn').forEach(btn => {
            const isActive = parseInt(btn.dataset.tabId, 10) === tabId;
            btn.classList.toggle('bg-primary', isActive);
            btn.classList.toggle('text-primary-content', isActive);
            btn.classList.toggle('shadow-sm', isActive);
            btn.classList.toggle('text-base-content/60', !isActive);
            btn.classList.toggle('hover:text-base-content', !isActive);
            btn.classList.toggle('hover:bg-base-300/50', !isActive);
            btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });

        // Show/hide panels
        document.querySelectorAll('.roadmap-tab-panel').forEach(panel => {
            const isActive = parseInt(panel.dataset.tabId, 10) === tabId;
            panel.classList.toggle('hidden', !isActive);
        });
    }

    // ------------------------------------------------------------------ //
    //  Step Rendering
    // ------------------------------------------------------------------ //

    function renderSteps(tabId) {
        const tabData = tabsData.find(t => t.id === tabId);
        if (!tabData) return;

        const container = document.querySelector(`.steps-container[data-tab-id="${tabId}"]`);
        if (!container) return;

        container.innerHTML = '';
        const emptyState = container.parentElement.querySelector('.steps-empty-state');

        if (tabData.steps.length === 0) {
            emptyState?.classList.remove('hidden');
            return;
        }

        emptyState?.classList.add('hidden');

        tabData.steps.forEach((step, index) => {
            const el = createStepElement(step, index + 1, tabData.trophy_group_id);
            container.appendChild(el);
        });

        initDragReorder(tabId);
    }

    function createStepElement(step, number, groupId) {
        const template = document.getElementById('step-template');
        const el = template.content.firstElementChild.cloneNode(true);

        el.dataset.stepId = step.id;
        el.dataset.itemId = step.id;  // DragReorderManager reads data-item-id
        el.querySelector('.step-number').textContent = number;
        el.querySelector('.step-title-input').value = step.title;
        el.querySelector('.step-desc-input').value = step.description;
        el.querySelector('.step-youtube-input').value = step.youtube_url || '';

        // Trophy picker
        const picker = el.querySelector('.trophy-picker');
        const trophies = trophiesByGroup[groupId] || [];
        const associatedIds = new Set(step.trophy_ids || []);
        const countBadge = el.querySelector('.trophy-count-badge');
        countBadge.textContent = associatedIds.size;

        trophies.forEach(t => {
            const itemTemplate = document.getElementById('trophy-picker-item-template');
            const item = itemTemplate.content.firstElementChild.cloneNode(true);

            const checkbox = item.querySelector('.trophy-checkbox');
            checkbox.dataset.trophyId = t.trophy_id;
            checkbox.checked = associatedIds.has(t.trophy_id);

            const icon = item.querySelector('.trophy-picker-icon');
            icon.src = t.icon_url || '';
            icon.alt = t.name;

            item.querySelector('.trophy-picker-name').textContent = t.name;

            const typeBadge = item.querySelector('.trophy-picker-type');
            typeBadge.textContent = t.type;
            typeBadge.classList.add(TROPHY_TYPE_COLORS[t.type] || 'badge-ghost');

            checkbox.addEventListener('change', () => {
                saveTrophyAssociations(step.id, el);
            });

            picker.appendChild(item);
        });

        // Event listeners
        const debouncedStepSave = debounce(() => saveStep(step.id, el), 800);
        el.querySelector('.step-title-input').addEventListener('input', () => {
            setSaveStatus('unsaved');
            debouncedStepSave();
        });
        el.querySelector('.step-desc-input').addEventListener('input', () => {
            setSaveStatus('unsaved');
            debouncedStepSave();
        });
        el.querySelector('.step-youtube-input').addEventListener('input', () => {
            setSaveStatus('unsaved');
            debouncedStepSave();
        });

        el.querySelector('.delete-step-btn').addEventListener('click', () => {
            deleteStep(step.id);
        });

        // Render ownership badge + apply writer-scoping read-only state.
        // Writers can only edit steps they own (or untouched/ownerless ones);
        // editors and publishers bypass this.
        applyOwnership(el, step.created_by_id || null, 'step-owner');

        return el;
    }

    async function saveStep(stepId, el) {
        const title = el.querySelector('.step-title-input').value.trim();
        const description = el.querySelector('.step-desc-input').value;
        const youtube_url = el.querySelector('.step-youtube-input').value.trim();

        if (!title) return;

        await apiCall('patch', `/api/v1/roadmap/${roadmapId}/tab/${activeTabId}/steps/${stepId}/`, {
            title, description, youtube_url
        });
    }

    async function saveTrophyAssociations(stepId, stepEl) {
        const checkboxes = stepEl.querySelectorAll('.trophy-checkbox:checked');
        const trophyIds = Array.from(checkboxes).map(cb => parseInt(cb.dataset.trophyId, 10));

        const countBadge = stepEl.querySelector('.trophy-count-badge');
        countBadge.textContent = trophyIds.length;

        await apiCall('put', `/api/v1/roadmap/${roadmapId}/tab/${activeTabId}/steps/${stepId}/trophies/`, {
            trophy_ids: trophyIds
        });
    }

    async function deleteStep(stepId) {
        if (!confirm('Delete this step? This cannot be undone.')) return;

        await apiCall('delete', `/api/v1/roadmap/${roadmapId}/tab/${activeTabId}/steps/${stepId}/`);

        // Remove from local state and re-render
        const tabData = tabsData.find(t => t.id === activeTabId);
        if (tabData) {
            tabData.steps = tabData.steps.filter(s => s.id !== stepId);
            renderSteps(activeTabId);
        }

        Toast.show('Step deleted.', 'success');
    }

    // ------------------------------------------------------------------ //
    //  Add Step
    // ------------------------------------------------------------------ //

    function initAddStepButtons() {
        document.querySelectorAll('.add-step-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const tabId = parseInt(btn.dataset.tabId, 10);
                await addStep(tabId);
            });
        });
    }

    async function addStep(tabId) {
        const result = await apiCall('post', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/steps/`, {
            title: 'New Step',
            description: '',
        });

        // Add to local state and re-render
        const tabData = tabsData.find(t => t.id === tabId);
        if (tabData && result) {
            tabData.steps.push({
                id: result.id,
                title: result.title,
                description: result.description,
                youtube_url: result.youtube_url || '',
                order: result.order,
                created_by_id: result.created_by_id,
                last_edited_by_id: result.last_edited_by_id,
                trophy_ids: [],
            });
            renderSteps(tabId);

            // Focus the new step's title input
            const container = document.querySelector(`.steps-container[data-tab-id="${tabId}"]`);
            const lastStep = container?.lastElementChild;
            if (lastStep) {
                lastStep.querySelector('.step-title-input')?.focus();
                lastStep.querySelector('.step-title-input')?.select();
            }
        }

        Toast.show('Step added.', 'success');
    }

    // ------------------------------------------------------------------ //
    //  Drag Reorder
    // ------------------------------------------------------------------ //

    function initDragReorder(tabId) {
        const container = document.querySelector(`.steps-container[data-tab-id="${tabId}"]`);
        if (!container || container.children.length < 2) return;

        // Destroy existing manager for this tab
        if (dragManagers[tabId]) {
            dragManagers[tabId].destroy?.();
        }

        dragManagers[tabId] = new window.PlatPursuit.DragReorderManager({
            container: container,
            itemSelector: '.step-card',
            handleSelector: '.step-handle',
            onReorder: async (itemId, newPosition, allItemIds) => {
                const stepIds = allItemIds.map(id => parseInt(id, 10));
                await apiCall('post', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/steps/reorder/`, {
                    step_ids: stepIds
                });

                // Update local state
                const tabData = tabsData.find(t => t.id === tabId);
                if (tabData) {
                    const stepMap = {};
                    tabData.steps.forEach(s => { stepMap[s.id] = s; });
                    tabData.steps = stepIds.map(id => stepMap[id]).filter(Boolean);
                }

                // Re-number the step badges
                container.querySelectorAll('.step-card').forEach((el, idx) => {
                    el.querySelector('.step-number').textContent = idx + 1;
                });
            },
        });
    }

    // ------------------------------------------------------------------ //
    //  General Tips & YouTube (debounced autosave)
    // ------------------------------------------------------------------ //

    function initTabFields() {
        document.querySelectorAll('.general-tips-input').forEach(textarea => {
            const tabId = parseInt(textarea.dataset.tabId, 10);
            const debouncedSave = debounce(async () => {
                await apiCall('patch', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/`, {
                    general_tips: textarea.value
                });
            }, 1000);

            textarea.addEventListener('input', () => {
                setSaveStatus('unsaved');
                debouncedSave();
            });
        });

        document.querySelectorAll('.youtube-url-input').forEach(input => {
            const tabId = parseInt(input.dataset.tabId, 10);
            const debouncedSave = debounce(async () => {
                await apiCall('patch', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/`, {
                    youtube_url: input.value
                });
            }, 1000);

            input.addEventListener('input', () => {
                setSaveStatus('unsaved');
                debouncedSave();
            });
        });

        // Per-tab gating: render the General Tips owner badge / disable the
        // textarea for writers who don't own this tab; lock the YouTube URL
        // input to publishers only.
        tabsData.forEach(tabData => {
            applyTabFieldGates(tabData);
        });
    }

    function applyTabFieldGates(tabData) {
        const tabId = tabData.id;
        const tipsCard = document.querySelector(`.general-tips-card[data-tab-id="${tabId}"]`);
        const tipsInput = document.querySelector(`.general-tips-input[data-tab-id="${tabId}"]`);
        const youtubeCard = document.querySelector(`.youtube-guide-card[data-tab-id="${tabId}"]`);
        const youtubeInput = document.querySelector(`.youtube-url-input[data-tab-id="${tabId}"]`);

        // General Tips: writer-or-tab-owner can edit. Editor+ bypasses.
        if (tipsCard && tipsInput) {
            const ownerId = tabData.created_by_id || null;
            const canEdit = applyOwnership(tipsCard, ownerId, 'general-tips-owner');
            if (!canEdit) {
                tipsInput.disabled = true;
            }
        }

        // YouTube URL: publisher-only. Always-locked for writers and editors,
        // regardless of tab ownership.
        if (youtubeCard && youtubeInput) {
            if (!canPublish) {
                youtubeInput.disabled = true;
                youtubeCard.classList.add('opacity-60');
                // Disable any future formatting toolbar buttons inside this card too.
                youtubeCard.querySelectorAll('button').forEach(b => {
                    if (b.closest('[data-readonly-exempt]')) return;
                    b.disabled = true;
                });
            }
        }
    }

    // ------------------------------------------------------------------ //
    //  Metadata Fields
    // ------------------------------------------------------------------ //

    function initMetadataFields() {
        // Populate initial values from tabsData
        tabsData.forEach(tab => {
            document.querySelectorAll(`.metadata-input[data-tab-id="${tab.id}"]`).forEach(input => {
                const field = input.dataset.field;
                const val = tab[field];
                if (val !== null && val !== undefined) input.value = val;
            });
            document.querySelectorAll(`.metadata-toggle[data-tab-id="${tab.id}"]`).forEach(toggle => {
                const field = toggle.dataset.field;
                toggle.checked = !!tab[field];
                const label = toggle.closest('label')?.querySelector('.online-required-label');
                if (label) label.textContent = toggle.checked ? 'Yes' : 'No';
            });
        });

        // Debounced save for number inputs
        document.querySelectorAll('.metadata-input').forEach(input => {
            const tabId = parseInt(input.dataset.tabId, 10);
            const field = input.dataset.field;
            const debouncedSave = debounce(async () => {
                const body = {};
                body[field] = input.value;
                await apiCall('patch', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/`, body);
            }, 1000);

            input.addEventListener('input', () => {
                setSaveStatus('unsaved');
                debouncedSave();
            });
        });

        // Immediate save for toggle
        document.querySelectorAll('.metadata-toggle').forEach(toggle => {
            const tabId = parseInt(toggle.dataset.tabId, 10);
            const field = toggle.dataset.field;

            toggle.addEventListener('change', async () => {
                const label = toggle.closest('label')?.querySelector('.online-required-label');
                if (label) label.textContent = toggle.checked ? 'Yes' : 'No';
                const body = {};
                body[field] = toggle.checked;
                await apiCall('patch', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/`, body);
            });
        });
    }

    // ------------------------------------------------------------------ //
    //  Trophy Guides
    // ------------------------------------------------------------------ //

    function renderTrophyGuides(tabId) {
        const tabData = tabsData.find(t => t.id === tabId);
        if (!tabData) return;

        const container = document.querySelector(`.trophy-guides-container[data-tab-id="${tabId}"]`);
        if (!container) return;

        const groupId = container.dataset.groupId;
        const trophies = trophiesByGroup[groupId] || [];
        const guides = tabData.trophy_guides || {};

        container.innerHTML = '';

        trophies.forEach(t => {
            const template = document.getElementById('trophy-guide-template');
            const el = template.content.firstElementChild.cloneNode(true);

            el.dataset.trophyId = t.trophy_id;

            const icon = el.querySelector('.trophy-guide-icon');
            icon.src = t.icon_url || '';
            icon.alt = t.name;

            el.querySelector('.trophy-guide-name').textContent = t.name;

            const typeBadge = el.querySelector('.trophy-guide-type');
            typeBadge.textContent = t.type;
            typeBadge.classList.add(TROPHY_TYPE_COLORS[t.type] || 'badge-ghost');

            const statusBadge = el.querySelector('.trophy-guide-status');
            const guideData = guides[t.trophy_id] || {};
            const body = typeof guideData === 'string' ? guideData : (guideData.body || '');
            const textarea = el.querySelector('.trophy-guide-body');
            textarea.value = body;

            // Load flag checkboxes
            const flagCheckboxes = el.querySelectorAll('.trophy-guide-flag');
            flagCheckboxes.forEach(cb => {
                const flag = cb.dataset.flag;
                if (typeof guideData === 'object' && guideData[flag]) {
                    cb.checked = true;
                }
            });

            if (body) {
                statusBadge.textContent = 'Written';
                statusBadge.classList.add('badge-success');
            } else {
                statusBadge.textContent = 'Empty';
            }

            // Open if has content
            if (body) {
                el.setAttribute('open', '');
            }

            // Collect current flags from checkboxes
            function getFlags() {
                const flags = {};
                flagCheckboxes.forEach(cb => { flags[cb.dataset.flag] = cb.checked; });
                return flags;
            }

            // Debounced autosave
            const debouncedSave = debounce(async () => {
                const currentBody = textarea.value;
                const flags = getFlags();
                await apiCall('put', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/trophy-guides/${t.trophy_id}/`, {
                    body: currentBody,
                    ...flags,
                });

                // Update status badge and local state
                const tabData = tabsData.find(td => td.id === tabId);
                if (tabData) {
                    if (currentBody.trim()) {
                        tabData.trophy_guides[t.trophy_id] = { body: currentBody, ...flags };
                    } else {
                        delete tabData.trophy_guides[t.trophy_id];
                    }
                    updateTrophyGuideCounter(tabId);
                }
                if (currentBody.trim()) {
                    statusBadge.textContent = 'Written';
                    statusBadge.className = 'trophy-guide-status badge badge-xs badge-success ml-auto shrink-0';
                } else {
                    statusBadge.textContent = 'Empty';
                    statusBadge.className = 'trophy-guide-status badge badge-xs badge-ghost ml-auto shrink-0';
                }
            }, 1000);

            textarea.addEventListener('input', () => {
                setSaveStatus('unsaved');
                debouncedSave();
            });

            flagCheckboxes.forEach(cb => {
                cb.addEventListener('change', () => {
                    setSaveStatus('unsaved');
                    debouncedSave();
                });
            });

            // Render ownership badge + apply writer-scoping read-only state.
            // Editors and publishers bypass; writers may only edit their own
            // (or untouched/ownerless) guides.
            const guideOwnerId = (typeof guideData === 'object' && guideData.created_by_id) || null;
            applyOwnership(el, guideOwnerId, 'trophy-guide-owner');

            container.appendChild(el);
        });
    }

    // ------------------------------------------------------------------ //
    //  Publish / Unpublish
    // ------------------------------------------------------------------ //

    function initPublishButtons() {
        const publishBtn = document.getElementById('publish-btn');
        const unpublishBtn = document.getElementById('unpublish-btn');

        if (publishBtn) {
            publishBtn.addEventListener('click', async () => {
                await apiCall('post', `/api/v1/roadmap/${roadmapId}/publish/`, { action: 'publish' });
                Toast.show('Roadmap published!', 'success');
                updatePublishUI('published');
            });
        }

        if (unpublishBtn) {
            unpublishBtn.addEventListener('click', async () => {
                await apiCall('post', `/api/v1/roadmap/${roadmapId}/publish/`, { action: 'unpublish' });
                Toast.show('Roadmap unpublished.', 'info');
                updatePublishUI('draft');
            });
        }
    }

    function updatePublishUI(newStatus) {
        const badge = document.getElementById('status-badge');
        if (badge) {
            badge.textContent = newStatus.charAt(0).toUpperCase() + newStatus.slice(1);
            badge.classList.toggle('badge-warning', newStatus === 'draft');
            badge.classList.toggle('badge-success', newStatus === 'published');
        }

        // Swap the publish/unpublish button
        const publishBtn = document.getElementById('publish-btn');
        const unpublishBtn = document.getElementById('unpublish-btn');

        if (newStatus === 'published') {
            if (publishBtn) publishBtn.classList.add('hidden');
            if (unpublishBtn) {
                unpublishBtn.classList.remove('hidden');
            } else {
                // Create unpublish button
                const btnContainer = publishBtn?.parentElement;
                if (btnContainer && publishBtn) {
                    const btn = document.createElement('button');
                    btn.id = 'unpublish-btn';
                    btn.className = 'btn btn-sm btn-warning gap-1';
                    btn.innerHTML = `
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"/>
                        </svg>
                        <span class="hidden md:inline">Unpublish</span>`;
                    btn.addEventListener('click', async () => {
                        await apiCall('post', `/api/v1/roadmap/${roadmapId}/publish/`, { action: 'unpublish' });
                        Toast.show('Roadmap unpublished.', 'info');
                        updatePublishUI('draft');
                    });
                    publishBtn.replaceWith(btn);
                }
            }
        } else {
            if (unpublishBtn) unpublishBtn.classList.add('hidden');
            if (publishBtn) {
                publishBtn.classList.remove('hidden');
            } else {
                const btnContainer = unpublishBtn?.parentElement;
                if (btnContainer && unpublishBtn) {
                    const btn = document.createElement('button');
                    btn.id = 'publish-btn';
                    btn.className = 'btn btn-sm btn-success gap-1';
                    btn.innerHTML = `
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
                        </svg>
                        <span class="hidden md:inline">Publish</span>`;
                    btn.addEventListener('click', async () => {
                        await apiCall('post', `/api/v1/roadmap/${roadmapId}/publish/`, { action: 'publish' });
                        Toast.show('Roadmap published!', 'success');
                        updatePublishUI('published');
                    });
                    unpublishBtn.replaceWith(btn);
                }
            }
        }
    }

    // ------------------------------------------------------------------ //
    //  Formatting Toolbar
    // ------------------------------------------------------------------ //

    const FORMAT_MAP = {
        bold:      { before: '**', after: '**', placeholder: 'bold text' },
        italic:    { before: '*',  after: '*',  placeholder: 'italic text' },
        underline: { before: '__', after: '__', placeholder: 'underlined text' },
        strike:    { before: '~~', after: '~~', placeholder: 'strikethrough' },
        link:      { before: '[',  after: '](url)', placeholder: 'link text' },
        list:      { linePrefix: '- ' },
        quote:     { linePrefix: '> ' },
    };

    // Track last-focused textarea so toolbar clicks can target it
    let lastFocusedTextarea = null;

    document.addEventListener('focusin', (e) => {
        if (e.target.tagName === 'TEXTAREA') {
            lastFocusedTextarea = e.target;
        }
    });

    function getTargetTextarea(toolbarEl) {
        // Walk siblings to find the nearest textarea
        let el = toolbarEl.nextElementSibling;
        while (el) {
            if (el.tagName === 'TEXTAREA') return el;
            const inner = el.querySelector?.('textarea');
            if (inner) return inner;
            el = el.nextElementSibling;
        }
        // Fallback: search within the same parent container
        return toolbarEl.parentElement?.querySelector('textarea') || null;
    }

    function applyFormat(textarea, before, after, placeholder) {
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const text = textarea.value;
        const selected = text.substring(start, end) || placeholder;
        const replacement = before + selected + after;

        // Direct value manipulation (reliable across all browsers)
        textarea.value = text.substring(0, start) + replacement + text.substring(end);

        // Position cursor to select the inserted text
        const cursorStart = start + before.length;
        const cursorEnd = cursorStart + selected.length;
        textarea.focus();
        textarea.setSelectionRange(cursorStart, cursorEnd);

        textarea.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function applyLinePrefix(textarea, prefix) {
        const start = textarea.selectionStart;
        const text = textarea.value;
        const lineStart = text.lastIndexOf('\n', start - 1) + 1;

        textarea.value = text.substring(0, lineStart) + prefix + text.substring(lineStart);
        const newPos = start + prefix.length;
        textarea.focus();
        textarea.setSelectionRange(newPos, newPos);

        textarea.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function initFormattingToolbars() {
        // Formatting buttons
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.fmt-btn');
            if (!btn) return;
            e.preventDefault();

            const toolbar = btn.closest('.formatting-toolbar');
            const textarea = getTargetTextarea(toolbar) || lastFocusedTextarea;
            if (!textarea) return;

            const fmtKey = btn.dataset.fmt;

            // Trophy link button
            if (fmtKey === 'trophy-link') {
                showTrophyLinkPicker(textarea);
                return;
            }

            // Image upload button
            if (fmtKey === 'image') {
                uploadImage(textarea);
                return;
            }

            const fmt = FORMAT_MAP[fmtKey];
            if (!fmt) return;

            if (fmt.linePrefix) {
                applyLinePrefix(textarea, fmt.linePrefix);
            } else {
                applyFormat(textarea, fmt.before, fmt.after, fmt.placeholder);
            }
        });

        // Help toggle - show/hide global help panel
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.fmt-help-toggle');
            if (!btn) return;

            const helpPanel = document.getElementById('global-formatting-help');
            if (helpPanel) {
                helpPanel.classList.toggle('hidden');
                helpPanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        });
    }

    // ------------------------------------------------------------------ //
    //  Trophy Link Picker
    // ------------------------------------------------------------------ //

    function showTrophyLinkPicker(textarea) {
        // Find the active tab's trophy group
        const activePanel = document.querySelector(`.roadmap-tab-panel:not(.hidden)`);
        const groupId = activePanel?.dataset.groupId;
        const trophies = trophiesByGroup[groupId] || [];

        if (!trophies.length) {
            Toast.show('No trophies available for this tab.', 'warning');
            return;
        }

        // Remove any existing picker
        document.getElementById('trophy-link-picker')?.remove();

        const picker = document.createElement('div');
        picker.id = 'trophy-link-picker';
        picker.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50';
        picker.innerHTML = `
            <div class="bg-base-200 border-2 border-base-300 rounded-xl shadow-2xl w-[90vw] max-w-md max-h-[70vh] flex flex-col">
                <div class="flex items-center justify-between p-3 border-b border-base-300">
                    <h3 class="text-sm font-bold">Link to Trophy Guide</h3>
                    <button class="btn btn-ghost btn-xs btn-circle" id="trophy-link-close">&times;</button>
                </div>
                <div class="p-2 border-b border-base-300">
                    <input type="text" class="input input-bordered input-sm w-full" id="trophy-link-search" placeholder="Search trophies...">
                </div>
                <div class="overflow-y-auto flex-1 p-2 space-y-1" id="trophy-link-list"></div>
            </div>
        `;

        document.body.appendChild(picker);

        const list = picker.querySelector('#trophy-link-list');
        const search = picker.querySelector('#trophy-link-search');

        function renderList(filter) {
            const filtered = filter
                ? trophies.filter(t => t.name.toLowerCase().includes(filter.toLowerCase()))
                : trophies;

            list.innerHTML = filtered.map(t => `
                <button class="trophy-link-option flex items-center gap-2 w-full p-2 rounded-lg hover:bg-white/[0.05] transition-colors text-left" data-trophy-id="${t.trophy_id}" data-trophy-name="${t.name.replace(/"/g, '&quot;')}">
                    <img src="${t.icon_url}" alt="" class="w-7 h-7 rounded object-cover shrink-0">
                    <span class="text-sm truncate flex-1">${t.name}</span>
                    <span class="badge badge-xs ${TROPHY_TYPE_COLORS[t.type] || 'badge-ghost'} shrink-0">${t.type}</span>
                </button>
            `).join('');
        }

        renderList();
        search.focus();
        search.addEventListener('input', () => renderList(search.value));

        // Select trophy
        list.addEventListener('click', (e) => {
            const opt = e.target.closest('.trophy-link-option');
            if (!opt) return;

            const trophyId = opt.dataset.trophyId;
            const trophyName = opt.dataset.trophyName;

            applyFormat(textarea, '[', `](#trophy-guide-${trophyId})`, trophyName);
            picker.remove();
        });

        // Close
        picker.querySelector('#trophy-link-close').addEventListener('click', () => picker.remove());
        picker.addEventListener('click', (e) => {
            if (e.target === picker) picker.remove();
        });
        document.addEventListener('keydown', function escHandler(e) {
            if (e.key === 'Escape') {
                picker.remove();
                document.removeEventListener('keydown', escHandler);
            }
        });
    }

    // ------------------------------------------------------------------ //
    //  Image Upload
    // ------------------------------------------------------------------ //

    function uploadImage(textarea) {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/jpeg,image/png,image/webp,image/gif';

        input.addEventListener('change', async () => {
            const file = input.files[0];
            if (!file) return;

            if (file.size > 5 * 1024 * 1024) {
                Toast.show('Image must be under 5MB.', 'error');
                return;
            }

            setSaveStatus('saving');
            try {
                const formData = new FormData();
                formData.append('image', file);

                const result = await API.postFormData('/api/v1/roadmap/upload-image/', formData);
                if (result?.url) {
                    applyFormat(textarea, '![', `](${result.url})`, file.name.replace(/\.[^.]+$/, ''));
                    setSaveStatus('saved');
                    Toast.show('Image uploaded.', 'success');
                }
            } catch (err) {
                setSaveStatus('error');
                const errData = await err.response?.json().catch(() => null);
                Toast.show(errData?.error || 'Image upload failed.', 'error');
            }
        });

        input.click();
    }

    // ------------------------------------------------------------------ //
    //  Keyboard Shortcuts
    // ------------------------------------------------------------------ //

    function initKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            if (!e.ctrlKey && !e.metaKey) return;

            const textarea = document.activeElement;
            if (!textarea || textarea.tagName !== 'TEXTAREA') return;

            let fmt;
            switch (e.key.toLowerCase()) {
                case 'b': fmt = FORMAT_MAP.bold; break;
                case 'i': fmt = FORMAT_MAP.italic; break;
                case 'u': fmt = FORMAT_MAP.underline; break;
                default: return;
            }

            e.preventDefault();
            wrapSelection(textarea, fmt.before, fmt.after, fmt.placeholder);
        });
    }

    // ------------------------------------------------------------------ //
    //  Textarea Auto-Resize
    // ------------------------------------------------------------------ //

    function autoResize(textarea) {
        textarea.style.height = 'auto';
        textarea.style.height = textarea.scrollHeight + 'px';
    }

    function initAutoResize() {
        document.addEventListener('input', (e) => {
            if (e.target.classList.contains('auto-resize')) {
                autoResize(e.target);
            }
        });

        // Initial resize for textareas with content
        requestAnimationFrame(() => {
            document.querySelectorAll('textarea.auto-resize').forEach(autoResize);
        });
    }

    // ------------------------------------------------------------------ //
    //  Trophy Guide Counter
    // ------------------------------------------------------------------ //

    function updateTrophyGuideCounter(tabId) {
        const tabData = tabsData.find(t => t.id === tabId);
        if (!tabData) return;

        const groupId = document.querySelector(`.trophy-guides-container[data-tab-id="${tabId}"]`)?.dataset.groupId;
        const totalTrophies = (trophiesByGroup[groupId] || []).length;
        const writtenGuides = Object.keys(tabData.trophy_guides || {}).filter(
            k => {
                const v = tabData.trophy_guides[k];
                const body = typeof v === 'string' ? v : (v?.body || '');
                return body.trim();
            }
        ).length;

        const counter = document.querySelector(`.trophy-guide-counter[data-tab-id="${tabId}"]`);
        if (counter) {
            counter.textContent = `Individual tips for each trophy. ${writtenGuides} of ${totalTrophies} written. Supports markdown.`;
        }
    }

    // ------------------------------------------------------------------ //
    //  Initialization
    // ------------------------------------------------------------------ //

    async function init() {
        // Acquire the lock first; if it's held by someone else the rest of
        // the editor still renders but inputs are disabled.
        await LockController.init();

        initTabs();
        initAddStepButtons();
        initTabFields();
        initMetadataFields();
        initPublishButtons();
        initFormattingToolbars();
        initKeyboardShortcuts();
        initAutoResize();
        initSaveCancelButtons();

        // Render all tabs
        tabsData.forEach(tab => {
            renderSteps(tab.id);
            renderTrophyGuides(tab.id);
            updateTrophyGuideCounter(tab.id);
        });

        // Reset any input wiring that BranchProxy.lockLost flipped before render
        if (BranchProxy.lockLost) {
            enterReadOnlyMode('Editor is read-only.');
        }

        // Warn on navigation with unsaved changes; release lock on close.
        window.addEventListener('beforeunload', (e) => {
            if (hasUnsaved) {
                e.preventDefault();
                e.returnValue = '';
            }
            // Best-effort release — fires sendBeacon if available so the
            // request survives the unload.
            if (!BranchProxy.lockLost && navigator.sendBeacon) {
                const csrfToken = window.PlatPursuit.CSRFToken?.get?.();
                const blob = new Blob([JSON.stringify({})], { type: 'application/json' });
                // sendBeacon doesn't support custom headers; the release endpoint
                // doesn't strictly need CSRF since lock ownership is verified server-side.
                navigator.sendBeacon(`/api/v1/roadmap/${roadmapId}/lock/release/`, blob);
            }
        });
    }

    function initSaveCancelButtons() {
        const saveBtn = document.getElementById('roadmap-save-btn');
        const cancelBtn = document.getElementById('roadmap-cancel-btn');
        if (saveBtn) {
            saveBtn.addEventListener('click', () => LockController.save());
        }
        if (cancelBtn) {
            cancelBtn.addEventListener('click', async () => {
                if (hasUnsaved && !confirm(
                    'Discard your unsaved branch and exit the editor? '
                    + 'You will lose any changes made since the last save.'
                )) return;
                await LockController.release();
                window.location.href = `/games/${editorEl.dataset.gameSlug || ''}/`;
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
