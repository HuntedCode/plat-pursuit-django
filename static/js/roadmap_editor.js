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
    // Collectibles — purely client-side BranchProxy patterns. The server
    // never receives these URLs (only the merged branch_payload).
    const LEGACY_C_AREAS_LIST_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/collectible-areas/?$`
    );
    const LEGACY_C_AREAS_REORDER_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/collectible-areas/reorder/?$`
    );
    const LEGACY_C_AREAS_DETAIL_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/collectible-areas/(-?\\d+)/?$`
    );
    const LEGACY_C_TYPES_LIST_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/collectible-types/?$`
    );
    const LEGACY_C_TYPES_REORDER_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/collectible-types/reorder/?$`
    );
    const LEGACY_C_TYPES_DETAIL_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/collectible-types/(-?\\d+)/?$`
    );
    const LEGACY_C_ITEMS_LIST_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/collectible-types/(-?\\d+)/items/?$`
    );
    const LEGACY_C_ITEMS_REORDER_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/collectible-types/(-?\\d+)/items/reorder/?$`
    );
    const LEGACY_C_ITEMS_DETAIL_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/collectible-types/(-?\\d+)/items/(-?\\d+)/?$`
    );
    // Area-scoped item operations. Items live nested under a type on the
    // wire (the schema hasn't changed), but authoring is now per-area, so
    // the URL space mirrors how the editor thinks about them. The handler
    // resolves the type from the request body or the item's existing
    // record, then mutates the same nested storage.
    const LEGACY_C_AREA_ITEMS_LIST_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/collectible-areas/(-?\\d+|null)/items/?$`
    );
    const LEGACY_C_AREA_ITEMS_REORDER_PATTERN = new RegExp(
        `^/api/v1/roadmap/${roadmapId}/tab/(\\d+)/collectible-areas/(-?\\d+|null)/items/reorder/?$`
    );

    const COLLECTIBLE_COLORS = ['primary', 'secondary', 'accent', 'info', 'success', 'warning', 'error'];
    const COLLECTIBLE_DEFAULT_ICON = '🎯';
    // Used to assign a starter color for new types so a brand-new roadmap
    // doesn't end up with five primary-colored types. Cycles through the
    // palette in declaration order.
    // <summary> activation behavior fires on Space/Enter even when focus
    // is on a child <input>. The mechanism: Chrome synthesizes a click
    // event on the summary in response to the key, and that click is
    // what toggles the surrounding <details>. stopPropagation on the
    // input's keydown is NOT enough because the synthesized click is
    // dispatched independently after the key event.
    //
    // The reliable fix: a single capture-phase document listener that
    // suppresses keyboard-synthesized clicks (detail === 0) on a summary
    // whenever a form control inside that summary has focus. Real mouse
    // clicks have detail >= 1 and pass through untouched, so the chevron
    // / label / count badge still toggle the details normally.
    function _installSummaryKeyToggleSuppression() {
        if (window.__rmSummaryKeyToggleInstalled) return;
        window.__rmSummaryKeyToggleInstalled = true;
        document.addEventListener('click', (e) => {
            // detail === 0 → synthesized (keyboard-activated) click;
            // detail >= 1 → real mouse click. Only intercept the former.
            if (e.detail !== 0) return;
            const summary = e.target.closest && e.target.closest('summary');
            if (!summary) return;
            const focused = document.activeElement;
            if (!focused || focused === summary || !summary.contains(focused)) return;
            const tag = focused.tagName;
            if (tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') return;
            e.preventDefault();
            e.stopPropagation();
        }, true);
    }
    _installSummaryKeyToggleSuppression();

    // Defense-in-depth: also stop Space/Enter keydowns from bubbling out
    // of inputs nested inside summaries. Some browsers / future Chrome
    // versions may stop synthesizing the click and just bubble the
    // keydown to summary's activation handler — the keydown guard
    // catches that path too. Cheap, no downside.
    function _stopSummaryToggleKeys(inputEl) {
        if (!inputEl) return;
        inputEl.addEventListener('keydown', (e) => {
            if (e.key === ' ' || e.key === 'Enter') {
                e.stopPropagation();
            }
        });
    }

    // Default-name format for new collectible items: "<TypeName> #<N>"
    // where N is one past the existing count of items of that type.
    // Used by both "Add item" and the type-swap rename path so the
    // shape stays consistent across surfaces.
    function _defaultItemName(typeName, existingCount) {
        const t = (typeName || '').trim() || 'Item';
        return `${t} #${existingCount + 1}`;
    }

    // Strict match against the default-name pattern: same type name +
    // " #" + digits, end of string. Anything else (admin customization,
    // appended notes, different separator) is treated as user content
    // and left alone.
    function _isDefaultItemName(name, typeName) {
        const t = (typeName || '').trim();
        if (!t || !name) return false;
        const escaped = t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        return new RegExp(`^${escaped} #\\d+$`).test(name);
    }

    function _pickNextCollectibleColor(existingTypes) {
        const taken = new Set((existingTypes || []).map(t => t.color));
        for (const c of COLLECTIBLE_COLORS) {
            if (!taken.has(c)) return c;
        }
        return 'primary';
    }
    // Slugify mirrors Django's slugify enough for client-side preview.
    // Server still re-runs slugify on merge, so this is just for the
    // slug pill display while the type is being edited.
    function _clientSlugify(name) {
        return (name || '')
            .toString()
            .toLowerCase()
            .normalize('NFKD').replace(/[̀-ͯ]/g, '')
            .replace(/[^a-z0-9\s-]/g, '')
            .trim()
            .replace(/\s+/g, '-')
            .replace(/-+/g, '-')
            .substring(0, 50);
    }

    /**
     * Live attribution preview for the YouTube URL inputs (page-level,
     * step-level, per-trophy). Pairs each `.youtube-attribution-input`
     * with its sibling `.youtube-attribution-preview` element and runs a
     * debounced lookup against /api/youtube/attribution-lookup/ as the
     * author types, showing "Will be attributed to: CHANNEL" beneath the
     * input. Server still re-resolves on save — the preview is purely
     * advisory.
     */
    const YoutubeAttribution = {
        // Sit just above the longest field-save debounce (1000ms) so the
        // per-record save fires first and creates any branch stub the
        // lookup callback needs to write channel info into. Otherwise a
        // video-only first save races the lookup and the channel info
        // gets dropped on the floor.
        DEBOUNCE_MS: 1200,
        debouncers: new WeakMap(),

        findPreview(input) {
            // The preview <p> lives inside a wrapping `.youtube-attribution-group`
            // so the input itself can be nested under intermediate flex/icon
            // wrappers (step + per-trophy) without breaking the lookup.
            const group = input.closest('.youtube-attribution-group');
            return group?.querySelector('.youtube-attribution-preview') || null;
        },

        renderPreview(previewEl, message, channelName, channelUrl) {
            if (!previewEl) return;
            previewEl.textContent = '';
            if (!message && !channelName) return;
            if (message) previewEl.appendChild(document.createTextNode(message));
            if (channelName) {
                if (message) previewEl.appendChild(document.createTextNode(' '));
                let node;
                if (channelUrl) {
                    node = document.createElement('a');
                    node.href = channelUrl;
                    node.target = '_blank';
                    node.rel = 'noopener noreferrer';
                    node.className = 'text-primary hover:underline not-italic font-medium';
                } else {
                    node = document.createElement('span');
                    node.className = 'text-base-content/80 not-italic font-medium';
                }
                node.textContent = channelName;
                previewEl.appendChild(node);
            }
        },

        // `onResolve(channelName, channelUrl)` is called whenever the
        // lookup completes successfully. Used by call sites to push the
        // resolved attribution into the branch payload so the editor's
        // ?preview=true overlay can render it without waiting for merge.
        // Stored per-input via WeakMap so wire() stays a single argument.
        onResolveByInput: new WeakMap(),

        wire(input, onResolve) {
            if (!input) return;
            if (onResolve) this.onResolveByInput.set(input, onResolve);
            if (input.dataset.ytAttributionWired === '1') return;
            input.dataset.ytAttributionWired = '1';
            input.addEventListener('input', () => this.schedule(input));
        },

        showInitial(input, channelName, channelUrl) {
            const preview = this.findPreview(input);
            if (!preview) return;
            if (channelName) {
                this.renderPreview(preview, 'Attributed to:', channelName, channelUrl || '');
            } else {
                this.renderPreview(preview, '');
            }
        },

        schedule(input) {
            const existing = this.debouncers.get(input);
            if (existing) clearTimeout(existing);
            this.debouncers.set(input, setTimeout(() => this.lookup(input), this.DEBOUNCE_MS));
        },

        async lookup(input) {
            const url = input.value.trim();
            const preview = this.findPreview(input);
            if (!preview) return;
            const onResolve = this.onResolveByInput.get(input);
            if (!url) {
                this.renderPreview(preview, '');
                if (onResolve) onResolve('', '');
                return;
            }
            this.renderPreview(preview, 'Looking up channel…');
            try {
                const data = await API.get(
                    `/api/v1/youtube/attribution-lookup/?url=${encodeURIComponent(url)}`
                );
                // User may have kept typing; abort if value moved on.
                if (input.value.trim() !== url) return;
                if (data && data.channel_name) {
                    this.renderPreview(
                        preview, 'Will be attributed to:', data.channel_name, data.channel_url || ''
                    );
                    if (onResolve) onResolve(data.channel_name, data.channel_url || '');
                } else {
                    this.renderPreview(
                        preview, 'No channel info found — embed will show without attribution.'
                    );
                    if (onResolve) onResolve('', '');
                }
            } catch {
                // Lookup is advisory; swallow the error rather than nagging
                // the author with a toast for what's a non-blocking nicety.
                this.renderPreview(preview, '');
            }
        },
    };

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
        // `dirty` = branch has any work in it that differs from live records.
        // Includes work from PRIOR sessions (set by LockController when
        // resuming a non-fresh lock). Used by Cancel to know whether to
        // confirm before destroying the branch.
        //
        // `dirtyThisSession` = the user has edited something during this
        // page's lifetime. Used by beforeunload to decide whether to fire
        // the native browser warning dialog (no point warning if the work
        // was already on the server before they opened this tab).
        dirty: false,
        dirtyThisSession: false,

        init(initialPayload) {
            this.state = initialPayload || { payload_version: PAYLOAD_VERSION, tabs: [] };
            if (!this.state.payload_version) this.state.payload_version = PAYLOAD_VERSION;
            this.dirty = false;
            this.dirtyThisSession = false;
            // Resumed sessions can carry over negative-id entries the user
            // created last time. Reset nextLocalId below the lowest existing
            // negative so new sections don't collide with surviving ones.
            let minId = 0;
            (this.state.tabs || []).forEach(tab => {
                (tab.steps || []).forEach(s => {
                    if (typeof s.id === 'number' && s.id < minId) minId = s.id;
                });
                (tab.trophy_guides || []).forEach(g => {
                    if (typeof g.id === 'number' && g.id < minId) minId = g.id;
                });
            });
            this.nextLocalId = minId - 1;
        },

        findTab(tabId) {
            return this.state.tabs.find(t => t.id === tabId);
        },

        findStep(tabId, stepId) {
            const tab = this.findTab(tabId);
            return tab ? tab.steps.find(s => s.id === stepId) : null;
        },

        // Locate a collectible item by id across all types in a tab. Items
        // are nested under their owning type in branch state, but callers
        // (notably YT-attribution callbacks) only have the item id. Returns
        // null if the item isn't found in the live branch state.
        findCollectibleItem(tabId, itemId) {
            const tab = this.findTab(tabId);
            if (!tab) return null;
            for (const ct of (tab.collectible_types || [])) {
                const it = (ct.items || []).find(i => i.id === itemId);
                if (it) return it;
            }
            return null;
        },

        nextId() {
            return this.nextLocalId--;
        },

        schedulePush() {
            if (this.lockLost) return;
            hasUnsaved = true;
            this.dirty = true;
            this.dirtyThisSession = true;
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
                    // Channel attribution is server-derived on merge; mirror
                    // the empty seed so the local state stays schema-symmetric
                    // with what the server will eventually return.
                    youtube_channel_name: '',
                    youtube_channel_url: '',
                    order: tab.steps.length,
                    trophy_ids: [],
                    gallery_images: [],
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
                const incomingPhase = (body.phase || '').trim();
                const incomingYoutubeUrl = (body.youtube_url || '').trim();
                // Empty body deletes the guide UNLESS it carries gallery
                // images OR a phase tag OR a YouTube URL (all are independent
                // of body text and shouldn't disappear because the writer
                // cleared the prose). A guide can be video-only.
                if (!incomingBody.trim()) {
                    const hasGallery = guide && Array.isArray(guide.gallery_images) && guide.gallery_images.length > 0;
                    const keepForPhase = !!incomingPhase || (guide && guide.phase);
                    const keepForVideo = (
                        ('youtube_url' in body ? !!incomingYoutubeUrl : !!(guide && guide.youtube_url))
                    );
                    if (guide && !hasGallery && !keepForPhase && !keepForVideo) {
                        tab.trophy_guides = tab.trophy_guides.filter(g => g.trophy_id !== trophyId);
                        this.schedulePush();
                        return null;
                    }
                    if (!guide && (incomingPhase || incomingYoutubeUrl)) {
                        // Phase-only or video-only first save: create a stub
                        // guide with no body so the server has something to
                        // attach the metadata to.
                        guide = {
                            id: this.nextId(),
                            trophy_id: trophyId,
                            body: '',
                            youtube_url: '',
                            youtube_channel_name: '',
                            youtube_channel_url: '',
                            order: tab.trophy_guides.length,
                            is_missable: false,
                            is_online: false,
                            is_unobtainable: false,
                            phase: '',
                            gallery_images: [],
                            created_by_id: viewerProfileId,
                            last_edited_by_id: viewerProfileId,
                        };
                        tab.trophy_guides.push(guide);
                    }
                    if (guide) {
                        guide.body = '';
                        if ('phase' in body) guide.phase = incomingPhase;
                        if ('youtube_url' in body) guide.youtube_url = incomingYoutubeUrl;
                        this.schedulePush();
                    }
                    return null;
                }
                if (!guide) {
                    guide = {
                        id: this.nextId(),
                        trophy_id: trophyId,
                        body: '',
                        youtube_url: '',
                        youtube_channel_name: '',
                        youtube_channel_url: '',
                        order: tab.trophy_guides.length,
                        is_missable: false,
                        is_online: false,
                        is_unobtainable: false,
                        phase: '',
                        gallery_images: [],
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
                if ('phase' in body) guide.phase = incomingPhase;
                if ('youtube_url' in body) guide.youtube_url = incomingYoutubeUrl;
                this.schedulePush();
                return {
                    trophy_id: guide.trophy_id, body: guide.body,
                    is_missable: guide.is_missable, is_online: guide.is_online,
                    is_unobtainable: guide.is_unobtainable,
                    youtube_url: guide.youtube_url,
                };
            }

            // ── Collectible Areas ──────────────────────────────────────
            // Areas are roadmap-scoped (not per-step or per-trophy). We
            // store them on the tab object for legacy-shape symmetry; the
            // _legacy_to_flat shim lifts them to the top of the v2 payload.

            // Create area: POST /tab/Y/collectible-areas/
            if ((match = url.match(LEGACY_C_AREAS_LIST_PATTERN)) && m === 'post') {
                const tabId = parseInt(match[1], 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                if (!tab.collectible_areas) tab.collectible_areas = [];
                const newArea = {
                    id: this.nextId(),
                    name: (body.name || '').trim() || 'New Area',
                    slug: '',  // server assigns on merge; client preview uses name
                    order: tab.collectible_areas.length,
                    created_by_id: viewerProfileId,
                    last_edited_by_id: viewerProfileId,
                };
                tab.collectible_areas.push(newArea);
                this.schedulePush();
                return { ...newArea };
            }

            // Reorder areas: POST /tab/Y/collectible-areas/reorder/
            if ((match = url.match(LEGACY_C_AREAS_REORDER_PATTERN)) && m === 'post') {
                const tabId = parseInt(match[1], 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                const orderedIds = (body.area_ids || []).map(x => parseInt(x, 10));
                const m2 = {};
                (tab.collectible_areas || []).forEach(a => { m2[a.id] = a; });
                tab.collectible_areas = orderedIds.map(id => m2[id]).filter(Boolean);
                tab.collectible_areas.forEach((a, i) => { a.order = i; });
                this.schedulePush();
                return { status: 'ok' };
            }

            // Update / delete area: PATCH or DELETE /tab/Y/collectible-areas/A/
            if ((match = url.match(LEGACY_C_AREAS_DETAIL_PATTERN)) && (m === 'patch' || m === 'delete')) {
                const tabId = parseInt(match[1], 10);
                const areaId = parseInt(match[2], 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                if (!tab.collectible_areas) tab.collectible_areas = [];
                if (m === 'delete') {
                    tab.collectible_areas = tab.collectible_areas.filter(a => a.id !== areaId);
                    // Clear references to this area on items in this tab.
                    // FK is SET_NULL on the server too, so this matches the
                    // post-merge shape — items become "Unsorted".
                    (tab.collectible_types || []).forEach(ct => {
                        (ct.items || []).forEach(it => {
                            if (it.area_id === areaId) it.area_id = null;
                        });
                    });
                    this.schedulePush();
                    return null;
                }
                const area = tab.collectible_areas.find(a => a.id === areaId);
                if (!area) throw new Error(`Area ${areaId} not in branch.`);
                if ('name' in body) area.name = (body.name || '').trim() || area.name;
                if ('order' in body) area.order = body.order;
                area.last_edited_by_id = viewerProfileId;
                this.schedulePush();
                return { ...area };
            }

            // ── Collectible Types ──────────────────────────────────────

            // Create type: POST /tab/Y/collectible-types/
            if ((match = url.match(LEGACY_C_TYPES_LIST_PATTERN)) && m === 'post') {
                const tabId = parseInt(match[1], 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                if (!tab.collectible_types) tab.collectible_types = [];
                const newType = {
                    id: this.nextId(),
                    name: (body.name || '').trim() || 'New Collectible Type',
                    slug: '',
                    color: body.color || _pickNextCollectibleColor(tab.collectible_types),
                    icon: body.icon || COLLECTIBLE_DEFAULT_ICON,
                    description: '',
                    total_count: null,
                    order: tab.collectible_types.length,
                    items: [],
                    created_by_id: viewerProfileId,
                    last_edited_by_id: viewerProfileId,
                };
                tab.collectible_types.push(newType);
                this.schedulePush();
                return { ...newType };
            }

            // Reorder types: POST /tab/Y/collectible-types/reorder/
            if ((match = url.match(LEGACY_C_TYPES_REORDER_PATTERN)) && m === 'post') {
                const tabId = parseInt(match[1], 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                const orderedIds = (body.type_ids || []).map(x => parseInt(x, 10));
                const m2 = {};
                (tab.collectible_types || []).forEach(t => { m2[t.id] = t; });
                tab.collectible_types = orderedIds.map(id => m2[id]).filter(Boolean);
                tab.collectible_types.forEach((t, i) => { t.order = i; });
                this.schedulePush();
                return { status: 'ok' };
            }

            // Update / delete type: PATCH or DELETE /tab/Y/collectible-types/T/
            if ((match = url.match(LEGACY_C_TYPES_DETAIL_PATTERN)) && (m === 'patch' || m === 'delete')) {
                const tabId = parseInt(match[1], 10);
                const typeId = parseInt(match[2], 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                if (!tab.collectible_types) tab.collectible_types = [];
                if (m === 'delete') {
                    tab.collectible_types = tab.collectible_types.filter(t => t.id !== typeId);
                    this.schedulePush();
                    return null;
                }
                const ctype = tab.collectible_types.find(t => t.id === typeId);
                if (!ctype) throw new Error(`Type ${typeId} not in branch.`);
                if ('name' in body) ctype.name = (body.name || '').trim() || ctype.name;
                if ('color' in body && COLLECTIBLE_COLORS.includes(body.color)) ctype.color = body.color;
                if ('icon' in body) ctype.icon = (body.icon || '').slice(0, 8) || COLLECTIBLE_DEFAULT_ICON;
                if ('description' in body) ctype.description = (body.description || '').slice(0, 200);
                if ('total_count' in body) {
                    const tc = body.total_count;
                    ctype.total_count = (tc === '' || tc == null) ? null : parseInt(tc, 10);
                }
                if ('order' in body) ctype.order = body.order;
                ctype.last_edited_by_id = viewerProfileId;
                this.schedulePush();
                return { ...ctype, items: undefined };
            }

            // ── Collectible Items ──────────────────────────────────────

            // Create item: POST /tab/Y/collectible-types/T/items/
            if ((match = url.match(LEGACY_C_ITEMS_LIST_PATTERN)) && m === 'post') {
                const tabId = parseInt(match[1], 10);
                const typeId = parseInt(match[2], 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                const ctype = (tab.collectible_types || []).find(t => t.id === typeId);
                if (!ctype) throw new Error(`Type ${typeId} not in branch.`);
                if (!ctype.items) ctype.items = [];
                const newItem = {
                    id: this.nextId(),
                    name: (body.name || '').trim() || 'New Item',
                    area_id: body.area_id != null ? parseInt(body.area_id, 10) : null,
                    body: '',
                    youtube_url: '',
                    youtube_channel_name: '',
                    youtube_channel_url: '',
                    gallery_images: [],
                    is_missable: false,
                    is_dlc: false,
                    order: ctype.items.length,
                    created_by_id: viewerProfileId,
                    last_edited_by_id: viewerProfileId,
                };
                ctype.items.push(newItem);
                this.schedulePush();
                return { ...newItem };
            }

            // Reorder items within a type: POST /tab/Y/collectible-types/T/items/reorder/
            if ((match = url.match(LEGACY_C_ITEMS_REORDER_PATTERN)) && m === 'post') {
                const tabId = parseInt(match[1], 10);
                const typeId = parseInt(match[2], 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                const ctype = (tab.collectible_types || []).find(t => t.id === typeId);
                if (!ctype) throw new Error(`Type ${typeId} not in branch.`);
                const orderedIds = (body.item_ids || []).map(x => parseInt(x, 10));
                const m2 = {};
                (ctype.items || []).forEach(i => { m2[i.id] = i; });
                ctype.items = orderedIds.map(id => m2[id]).filter(Boolean);
                ctype.items.forEach((it, i) => { it.order = i; });
                this.schedulePush();
                return { status: 'ok' };
            }

            // ── Area-scoped item operations ────────────────────────────
            // The author-visible mental model: items belong to areas in
            // playthrough order, with type as a *property* of the item
            // (color/icon/slug). Storage stays nested under types because
            // that's what the snapshot/merge service expects.

            // Create item in area: POST /tab/Y/collectible-areas/A/items/
            // Body: { type_id, name }. area_id resolves to null if the
            // path segment is "null" (Unsorted) or to the area's int id.
            if ((match = url.match(LEGACY_C_AREA_ITEMS_LIST_PATTERN)) && m === 'post') {
                const tabId = parseInt(match[1], 10);
                const areaSeg = match[2];
                const areaId = areaSeg === 'null' ? null : parseInt(areaSeg, 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                const typeId = parseInt(body.type_id, 10);
                const ctype = (tab.collectible_types || []).find(t => t.id === typeId);
                if (!ctype) throw new Error(`Type ${typeId} not in branch.`);
                if (!ctype.items) ctype.items = [];
                // New item's order = end of the destination area. Items in
                // other areas keep their orders; this is the only area
                // we're appending to.
                const existingInArea = (tab.collectible_types || [])
                    .flatMap(ct => (ct.items || []).filter(it => (it.area_id ?? null) === areaId));
                const newItem = {
                    id: this.nextId(),
                    name: (body.name || '').trim() || 'New Item',
                    area_id: areaId,
                    body: '',
                    youtube_url: '',
                    youtube_channel_name: '',
                    youtube_channel_url: '',
                    gallery_images: [],
                    is_missable: false,
                    is_dlc: false,
                    order: existingInArea.length,
                    created_by_id: viewerProfileId,
                    last_edited_by_id: viewerProfileId,
                };
                ctype.items.push(newItem);
                this.schedulePush();
                return { ...newItem };
            }

            // Reorder items within an area: POST /tab/Y/collectible-areas/A/items/reorder/
            // Body: { item_ids: [...] }. The order field is set sequentially
            // for items in this area only — other areas keep their orders.
            if ((match = url.match(LEGACY_C_AREA_ITEMS_REORDER_PATTERN)) && m === 'post') {
                const tabId = parseInt(match[1], 10);
                const areaSeg = match[2];
                const areaId = areaSeg === 'null' ? null : parseInt(areaSeg, 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                const orderedIds = (body.item_ids || []).map(x => parseInt(x, 10));
                // Build a flat lookup across all types for this tab.
                const byId = {};
                (tab.collectible_types || []).forEach(ct => {
                    (ct.items || []).forEach(it => { byId[it.id] = it; });
                });
                orderedIds.forEach((id, i) => {
                    const item = byId[id];
                    if (!item) return;
                    // Defensive: ignore reorder requests for items not in
                    // this area (e.g. stale UI). The expected case is all
                    // ids belong to the area being reordered.
                    if ((item.area_id ?? null) !== areaId) return;
                    item.order = i;
                });
                this.schedulePush();
                return { status: 'ok' };
            }

            // Update / delete item: PATCH or DELETE /tab/Y/collectible-types/T/items/I/
            //
            // type_id in the URL is the item's *current* owning type. The
            // PATCH body may move the item to a new type via `type_id` —
            // we relocate it across the nested arrays in that case (storage
            // shape stays as types→items even though authoring is per-area).
            if ((match = url.match(LEGACY_C_ITEMS_DETAIL_PATTERN)) && (m === 'patch' || m === 'delete')) {
                const tabId = parseInt(match[1], 10);
                const typeId = parseInt(match[2], 10);
                const itemId = parseInt(match[3], 10);
                const tab = this.findTab(tabId);
                if (!tab) throw new Error(`Tab ${tabId} not in branch.`);
                const ctype = (tab.collectible_types || []).find(t => t.id === typeId);
                if (!ctype) throw new Error(`Type ${typeId} not in branch.`);
                if (m === 'delete') {
                    ctype.items = (ctype.items || []).filter(it => it.id !== itemId);
                    this.schedulePush();
                    return null;
                }
                let item = (ctype.items || []).find(it => it.id === itemId);
                if (!item) throw new Error(`Item ${itemId} not in branch.`);

                // Detect the area-change case BEFORE mutating, so we know
                // to recalc the destination order. Same for type-change.
                const incomingAreaSpecified = 'area_id' in body;
                const incomingArea = incomingAreaSpecified
                    ? (body.area_id == null || body.area_id === '' ? null : parseInt(body.area_id, 10))
                    : (item.area_id ?? null);
                const incomingTypeSpecified = 'type_id' in body && body.type_id != null;
                const incomingTypeId = incomingTypeSpecified ? parseInt(body.type_id, 10) : typeId;
                const oldArea = item.area_id ?? null;
                const areaChanged = incomingAreaSpecified && incomingArea !== oldArea;

                if ('name' in body) item.name = (body.name || '').trim() || item.name;
                if (incomingAreaSpecified) item.area_id = incomingArea;
                if ('body' in body) item.body = body.body || '';
                if ('youtube_url' in body) item.youtube_url = (body.youtube_url || '').trim();
                if ('is_missable' in body) item.is_missable = !!body.is_missable;
                if ('is_dlc' in body) item.is_dlc = !!body.is_dlc;
                if ('gallery_images' in body) item.gallery_images = body.gallery_images || [];
                if ('order' in body) item.order = body.order;
                item.last_edited_by_id = viewerProfileId;

                // Type change: pop the item from the old type's items[] and
                // push to the new type. Storage shape is types→items; the
                // FK is implicit in nesting. Server merge re-resolves the
                // type_id at write time (creates new RoadmapCollectibleItem
                // rows under the right type) so we just need to keep the
                // client mirror consistent.
                if (incomingTypeSpecified && incomingTypeId !== typeId) {
                    const newType = (tab.collectible_types || []).find(t => t.id === incomingTypeId);
                    if (newType) {
                        ctype.items = (ctype.items || []).filter(it => it.id !== itemId);
                        if (!newType.items) newType.items = [];
                        newType.items.push(item);
                    }
                }

                // Area change: assign order = end of destination area so
                // the item lands at the bottom of its new bucket. Author
                // can drag-reorder from there.
                if (areaChanged) {
                    const dest = (tab.collectible_types || [])
                        .flatMap(t => (t.items || []).filter(i => (i.area_id ?? null) === incomingArea && i.id !== itemId));
                    item.order = dest.length;
                }

                this.schedulePush();
                return { ...item };
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
                // Collectibles: keep negative ids on the wire for areas
                // and items. The merge service expects negative ids on
                // newly-created entries (so it can build an `area_id_map`
                // that translates an item's `area_id: -X` reference to
                // the freshly inserted area's live id). Types use the
                // same convention for symmetry; the merge differentiates
                // create vs. update by `id is None or id < 0`.
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
                // The page rendered tabsData from server-side LIVE state. If
                // the branch_payload differs (resumed session with unmerged
                // edits), mutate tabsData in place and push branch values
                // into the server-rendered DOM fields so the editor UI
                // reflects what the writer was working on, not the live
                // state. Must run BEFORE initMetadataFields and the
                // renderSteps/renderTrophyGuides calls below.
                hydrateTabsDataFromBranch(result.branch_payload);
                // If we're resuming an existing lock (held by self from a
                // prior tab/session), the branch may have unmerged work.
                // Mark dirty so Cancel confirms before destroying it. The
                // beforeunload native dialog stays gated on
                // `dirtyThisSession`, which only fires for edits made on
                // this specific page load.
                if (result.reacquired === false) {
                    BranchProxy.dirty = true;
                }
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
                BranchProxy.dirty = false;
                BranchProxy.dirtyThisSession = false;
                // Server deleted the lock as part of the merge — flag it on
                // the client so the upcoming beforeunload doesn't redundantly
                // hit /release/.
                BranchProxy.lockLost = true;
                setSaveStatus('saved');
                Toast.show(result.summary || 'Saved.', 'success');
                // Save = save & exit. Redirect to the game detail page (where
                // the writer came from) rather than reloading the editor;
                // reloading would acquire a fresh 1-hour lock unnecessarily.
                const slug = editorEl.dataset.gameSlug || '';
                setTimeout(() => {
                    window.location.href = slug ? `/games/${slug}/` : '/';
                }, 500);
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
                // Surface the SESSION (hard-cap) timer rather than idle —
                // for someone waiting on a lock, the cap is the firm ceiling
                // ("you'll wait at most this long"). Idle resets on every
                // keystroke, so it would be misleading.
                const mins = Math.floor((lock.hard_cap_seconds_remaining || 0) / 60);
                const baseMsg = lock.is_stale
                    ? `Locked by ${lock.holder_username || 'another author'} (idle). Reload to take over — their branch will be archived.`
                    : `Locked by ${lock.holder_username || 'another author'} (session ends in ~${mins} min). Editor is read-only — but you can still leave notes.`;
                msg.textContent = baseMsg;
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

    /**
     * Replace tabsData fields with the branch_payload returned by
     * /lock/acquire/, and push branch values into server-rendered DOM
     * fields that aren't otherwise hydrated (general_tips and youtube_url
     * textareas/inputs). Steps + trophy guides are rendered from tabsData
     * later in init(), so updating tabsData is enough for those.
     *
     * Called once during LockController.init(). Idempotent: for a fresh
     * lock, branch_payload == live snapshot, so the assignments are no-ops.
     */
    function hydrateTabsDataFromBranch(payload) {
        if (!payload || !Array.isArray(payload.tabs)) return;
        payload.tabs.forEach(branchTab => {
            const tabId = branchTab.id;
            const existing = tabsData.find(t => t.id === tabId);
            if (!existing) return;

            // Tab-level content + metadata
            existing.general_tips = branchTab.general_tips || '';
            existing.youtube_url = branchTab.youtube_url || '';
            existing.youtube_channel_name = branchTab.youtube_channel_name || '';
            existing.youtube_channel_url = branchTab.youtube_channel_url || '';
            existing.difficulty = branchTab.difficulty;
            existing.estimated_hours = branchTab.estimated_hours;
            existing.min_playthroughs = branchTab.min_playthroughs;
            existing.created_by_id = branchTab.created_by_id;
            existing.last_edited_by_id = branchTab.last_edited_by_id;

            // Steps — direct mapping (branch + tabsData both use a list)
            existing.steps = (branchTab.steps || []).map(s => ({
                id: s.id,
                title: s.title,
                description: s.description,
                youtube_url: s.youtube_url,
                youtube_channel_name: s.youtube_channel_name || '',
                youtube_channel_url: s.youtube_channel_url || '',
                order: s.order,
                gallery_images: Array.isArray(s.gallery_images) ? s.gallery_images.slice() : [],
                created_by_id: s.created_by_id,
                last_edited_by_id: s.last_edited_by_id,
                trophy_ids: s.trophy_ids || [],
            }));

            // Trophy guides — branch uses a list, tabsData uses a dict
            // keyed by trophy_id. Convert here.
            existing.trophy_guides = {};
            (branchTab.trophy_guides || []).forEach(tg => {
                existing.trophy_guides[tg.trophy_id] = {
                    body: tg.body || '',
                    youtube_url: tg.youtube_url || '',
                    youtube_channel_name: tg.youtube_channel_name || '',
                    youtube_channel_url: tg.youtube_channel_url || '',
                    is_missable: !!tg.is_missable,
                    is_online: !!tg.is_online,
                    is_unobtainable: !!tg.is_unobtainable,
                    phase: tg.phase || '',
                    gallery_images: Array.isArray(tg.gallery_images) ? tg.gallery_images.slice() : [],
                    created_by_id: tg.created_by_id,
                    last_edited_by_id: tg.last_edited_by_id,
                };
            });

            // Collectibles — branch + tabsData both use lists. Mirror them
            // verbatim so renderCollectibles can treat tabsData as the
            // single source of truth for in-DOM rendering decisions.
            existing.collectible_areas = (branchTab.collectible_areas || []).map(a => ({
                id: a.id,
                name: a.name || '',
                slug: a.slug || '',
                order: a.order,
                created_by_id: a.created_by_id,
                last_edited_by_id: a.last_edited_by_id,
            }));
            existing.collectible_types = (branchTab.collectible_types || []).map(ct => ({
                id: ct.id,
                name: ct.name || '',
                slug: ct.slug || '',
                color: ct.color || 'primary',
                icon: ct.icon || COLLECTIBLE_DEFAULT_ICON,
                description: ct.description || '',
                total_count: ct.total_count,
                order: ct.order,
                created_by_id: ct.created_by_id,
                last_edited_by_id: ct.last_edited_by_id,
                items: (ct.items || []).map(it => ({
                    id: it.id,
                    name: it.name || '',
                    area_id: it.area_id == null ? null : it.area_id,
                    body: it.body || '',
                    youtube_url: it.youtube_url || '',
                    youtube_channel_name: it.youtube_channel_name || '',
                    youtube_channel_url: it.youtube_channel_url || '',
                    gallery_images: Array.isArray(it.gallery_images) ? it.gallery_images.slice() : [],
                    is_missable: !!it.is_missable,
                    is_dlc: !!it.is_dlc,
                    order: it.order,
                    created_by_id: it.created_by_id,
                    last_edited_by_id: it.last_edited_by_id,
                })),
            }));

            // Server-rendered DOM fields that aren't hydrated by any
            // existing init function — push branch values into them.
            const tipsInput = document.querySelector(
                `.general-tips-input[data-tab-id="${tabId}"]`
            );
            if (tipsInput) tipsInput.value = existing.general_tips;
            const ytInput = document.querySelector(
                `.youtube-url-input[data-tab-id="${tabId}"]`
            );
            if (ytInput) {
                ytInput.value = existing.youtube_url;
                // Also push fresh channel info to the preview's data attrs so
                // the initTabFields() initial-render reads the branch value
                // rather than the original server-render value (which can be
                // stale after a prior session edited the URL but didn't merge).
                const preview = YoutubeAttribution.findPreview(ytInput);
                if (preview) {
                    preview.dataset.initialChannelName = existing.youtube_channel_name;
                    preview.dataset.initialChannelUrl = existing.youtube_channel_url;
                }
            }
        });
    }

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
            rowEl.querySelectorAll('input, textarea, button, select').forEach(el => {
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

    // Labels prefixed with "Auto-" so the autosave chip stays linguistically
    // distinct from the manual Save button — both surface "save" state but
    // operate on different layers (branch vs live roadmap). The cursor-help +
    // tooltip on the chip explains the distinction in full.
    function setSaveStatus(state) {
        if (!saveStatus) return;
        // Keep cursor-help so the explanatory tooltip on the parent stays.
        const baseClass = 'flex items-center gap-1 text-xs ml-auto shrink-0 cursor-help';
        switch (state) {
            case 'saving':
                saveStatus.className = `${baseClass} text-warning/70`;
                saveIcon.innerHTML = '<circle class="animate-spin origin-center" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" fill="none" stroke-dasharray="40 60"/>';
                saveText.textContent = 'Auto-saving…';
                break;
            case 'saved':
                saveStatus.className = `${baseClass} text-success/70`;
                saveIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>';
                saveText.textContent = 'Auto-saved';
                hasUnsaved = false;
                break;
            case 'unsaved':
                saveStatus.className = `${baseClass} text-warning/70`;
                saveIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4m0 4h.01"/>';
                saveText.textContent = 'Unsaved';
                hasUnsaved = true;
                break;
            case 'error':
                saveStatus.className = `${baseClass} text-error/70`;
                saveIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>';
                saveText.textContent = 'Auto-save error';
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
            const gallerySection = el.querySelector('.gallery-section');
            if (gallerySection) {
                GalleryController.mountSection(gallerySection, {
                    kind: 'step', id: step.id, tabId,
                });
            }
        });

        // First step's "up" and last step's "down" buttons are no-ops, so
        // disable them visually. Updated here (not in createStepElement)
        // because position depends on the full list being rendered.
        const stepCards = container.querySelectorAll('.step-card');
        stepCards.forEach((card, idx) => {
            const upBtn = card.querySelector('.step-move-up');
            const downBtn = card.querySelector('.step-move-down');
            if (upBtn) upBtn.disabled = idx === 0;
            if (downBtn) downBtn.disabled = idx === stepCards.length - 1;
        });

        initDragReorder(tabId);

        // Notes module mounts the 💬 N indicator on each step row. Lazy
        // optional-chain because notes JS may load slightly after editor JS
        // — when notes init eventually fires, it'll mount across the whole
        // editor. This call covers in-session re-renders (Add Step, etc.).
        window.PlatPursuit?.RoadmapNotes?.mountAllIndicators?.();
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

            // Trophy description under the name, hidden when empty.
            const pickerDetail = item.querySelector('.trophy-picker-detail');
            if (pickerDetail && t.detail) {
                pickerDetail.textContent = t.detail;
                pickerDetail.style.display = '';
            }

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
        const stepYoutubeInput = el.querySelector('.step-youtube-input');
        stepYoutubeInput.addEventListener('input', () => {
            setSaveStatus('unsaved');
            debouncedStepSave();
        });
        // Live attribution preview. Initial channel info comes from the
        // branch payload (snapshot-served on lock acquire); the first
        // paint matches what was saved without an extra round trip. The
        // onResolve callback pushes resolved channel into branch state so
        // ?preview=true overlay can render it without waiting for merge.
        YoutubeAttribution.wire(stepYoutubeInput, (channelName, channelUrl) => {
            const liveStep = BranchProxy.findStep(activeTabId, step.id);
            if (!liveStep) return;
            liveStep.youtube_channel_name = channelName;
            liveStep.youtube_channel_url = channelUrl;
            BranchProxy.schedulePush();
        });
        YoutubeAttribution.showInitial(
            stepYoutubeInput,
            step.youtube_channel_name || '',
            step.youtube_channel_url || '',
        );

        el.querySelector('.delete-step-btn').addEventListener('click', () => {
            deleteStep(step.id);
        });

        // Up/down reorder buttons are wired via event delegation in
        // initFormattingToolbars (one listener for the whole editor). For
        // non-editor+ users we actually REMOVE the column so the controls
        // don't render at all — applyOwnership's button-disable sweep would
        // otherwise leave them visible-but-dead, which the user reported as
        // misleading.

        // Render ownership badge + apply writer-scoping read-only state.
        // Writers can only edit steps they own (or untouched/ownerless ones);
        // editors and publishers bypass this.
        applyOwnership(el, step.created_by_id || null, 'step-owner');

        // Reorder (drag + up/down) is editor+ only. Strip the whole reorder
        // column for writers so the grab cursor + arrow buttons don't suggest
        // actions they can't actually perform.
        if (!bypassOwnershipScope) {
            const reorderCol = el.querySelector('.step-reorder-controls');
            if (reorderCol) reorderCol.remove();
        }

        return el;
    }

    async function moveStep(stepId, direction) {
        // Editor+ only. Writers don't see the buttons but defend in case
        // markup is tampered with.
        if (!bypassOwnershipScope) return;

        const tabData = tabsData.find(t => t.id === activeTabId);
        if (!tabData || !tabData.steps) return;

        const idx = tabData.steps.findIndex(s => s.id === stepId);
        if (idx < 0) return;
        const newIdx = idx + direction;
        if (newIdx < 0 || newIdx >= tabData.steps.length) return;

        // Build the post-swap step ID list and reuse the same endpoint the
        // drag handler hits. Local state updates only on success so a 403
        // / network failure leaves the visible order in sync with the API.
        const newSteps = tabData.steps.slice();
        const [moved] = newSteps.splice(idx, 1);
        newSteps.splice(newIdx, 0, moved);
        const stepIds = newSteps.map(s => s.id);

        try {
            await apiCall('post', `/api/v1/roadmap/${roadmapId}/tab/${activeTabId}/steps/reorder/`, {
                step_ids: stepIds,
            });
        } catch (e) {
            return;
        }

        tabData.steps = newSteps;
        renderSteps(activeTabId);
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

    // Event-delegated reorder. Per-element listeners attached during
    // createStepElement weren't surviving re-renders consistently; delegation
    // sidesteps that and gives us one listener for the whole editor. The
    // up/down columns are removed (not hidden) for non-editor+ users in
    // createStepElement, so the dispatch never fires for them anyway.
    function initStepReorderButtons() {
        editorEl.addEventListener('click', (e) => {
            const upBtn = e.target.closest('.step-move-up');
            const downBtn = e.target.closest('.step-move-down');
            const btn = upBtn || downBtn;
            if (!btn || btn.disabled) return;
            const card = btn.closest('.step-card');
            if (!card) return;
            const stepId = parseInt(card.dataset.stepId, 10);
            if (Number.isNaN(stepId)) return;
            moveStep(stepId, upBtn ? -1 : 1);
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
            delete dragManagers[tabId];
        }

        // Reorder is editor+ only on the server. Don't initialize the
        // SortableJS instance for writers — otherwise drags appear to
        // succeed in the DOM while the API rejects with 403.
        if (!bypassOwnershipScope) return;

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

            // Live attribution preview. The server-rendered initial channel
            // info comes from the data-* attrs on the preview element so the
            // first paint matches what's already saved without an extra round
            // trip; subsequent edits trigger the debounced lookup. The
            // onResolve callback pushes the resolved channel into the branch
            // payload so the editor's ?preview=true overlay shows attribution
            // without needing a full Save -> merge round-trip first.
            YoutubeAttribution.wire(input, (channelName, channelUrl) => {
                const tab = BranchProxy.findTab(tabId);
                if (!tab) return;
                tab.youtube_channel_name = channelName;
                tab.youtube_channel_url = channelUrl;
                BranchProxy.schedulePush();
            });
            const preview = YoutubeAttribution.findPreview(input);
            if (preview) {
                YoutubeAttribution.showInitial(
                    input,
                    preview.dataset.initialChannelName || '',
                    preview.dataset.initialChannelUrl || '',
                );
            }
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

        // Editor-only gate. Tab metadata fields (difficulty, estimated_hours,
        // min_playthroughs) are gated to editor+ on the server. Surface that
        // to writers by disabling the inputs and toning down the card so they
        // aren't confused about why saves on these fields fail. (Missable
        // count and online-required are no longer fields here — they're
        // derived from per-trophy flags.)
        if (!canDelete) {
            document.querySelectorAll('.guide-metadata-card').forEach(card => {
                card.classList.add('opacity-60');
                card.querySelectorAll('input, textarea, select, button').forEach(el => {
                    if (el.closest('[data-readonly-exempt]')) return;
                    el.disabled = true;
                });
            });
        }
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

            // PSN trophy description, shown muted under the name so writers
            // can scan their list and identify the trophy without expanding.
            // Default style="display:none" on the span; clear it when we have
            // text. Inline style trumps Tailwind class cascade unconditionally.
            const detailEl = el.querySelector('.trophy-guide-detail');
            if (detailEl && t.detail) {
                detailEl.textContent = t.detail;
                detailEl.style.display = '';
            }

            const typeBadge = el.querySelector('.trophy-guide-type');
            typeBadge.textContent = t.type;
            typeBadge.classList.add(TROPHY_TYPE_COLORS[t.type] || 'badge-ghost');

            const statusBadge = el.querySelector('.trophy-guide-status');
            const guideData = guides[t.trophy_id] || {};
            const body = typeof guideData === 'string' ? guideData : (guideData.body || '');
            const youtubeUrl = (typeof guideData === 'object' && guideData.youtube_url) || '';
            const textarea = el.querySelector('.trophy-guide-body');
            textarea.value = body;
            const youtubeInput = el.querySelector('.trophy-guide-youtube-input');
            if (youtubeInput) youtubeInput.value = youtubeUrl;

            // Load flag checkboxes
            const flagCheckboxes = el.querySelectorAll('.trophy-guide-flag');
            flagCheckboxes.forEach(cb => {
                const flag = cb.dataset.flag;
                if (typeof guideData === 'object' && guideData[flag]) {
                    cb.checked = true;
                }
            });

            // Load phase select. Platinum trophies don't get a phase — they
            // auto-pop when everything else is earned and render in their own
            // pinned section at the top of the published view. Hide the phase
            // controls on platinum rows and surface the small inline note.
            const phaseSelect = el.querySelector('.trophy-guide-phase');
            const isPlatinumRow = t.type === 'platinum';
            if (isPlatinumRow) {
                const phaseControls = el.querySelector('.phase-controls');
                if (phaseControls) phaseControls.classList.add('hidden');
                const platinumNote = el.querySelector('.platinum-phase-note');
                if (platinumNote) {
                    platinumNote.classList.remove('hidden');
                    platinumNote.classList.add('inline-flex');
                }
            } else if (phaseSelect && typeof guideData === 'object' && guideData.phase) {
                phaseSelect.value = guideData.phase;
            }

            if (body || youtubeUrl) {
                statusBadge.textContent = 'Written';
                statusBadge.classList.add('badge-success');
            } else {
                statusBadge.textContent = 'Empty';
            }

            // Open if has content
            if (body || youtubeUrl) {
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
                const phase = phaseSelect ? phaseSelect.value : '';
                const currentYoutubeUrl = youtubeInput ? youtubeInput.value.trim() : '';
                await apiCall('put', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/trophy-guides/${t.trophy_id}/`, {
                    body: currentBody,
                    phase: phase,
                    youtube_url: currentYoutubeUrl,
                    ...flags,
                });

                // Update status badge and local state. The guide is "present"
                // if any of body, phase, or youtube_url is set (gallery is
                // tracked separately via GalleryController).
                const tabData = tabsData.find(td => td.id === tabId);
                if (tabData) {
                    if (currentBody.trim() || phase || currentYoutubeUrl) {
                        tabData.trophy_guides[t.trophy_id] = {
                            body: currentBody, phase, youtube_url: currentYoutubeUrl, ...flags,
                        };
                    } else {
                        delete tabData.trophy_guides[t.trophy_id];
                    }
                    updateTrophyGuideCounter(tabId);
                }
                if (currentBody.trim() || currentYoutubeUrl) {
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

            if (phaseSelect && !isPlatinumRow) {
                phaseSelect.addEventListener('change', () => {
                    setSaveStatus('unsaved');
                    debouncedSave();
                });
            }

            if (youtubeInput) {
                youtubeInput.addEventListener('input', () => {
                    setSaveStatus('unsaved');
                    debouncedSave();
                });
                // Push resolved channel info into the branch payload so
                // ?preview=true overlay shows attribution before merge.
                // The guide may not exist in the branch yet (video-only
                // first save races the lookup); locate-or-noop pattern
                // falls back to the next debouncedSave to populate.
                YoutubeAttribution.wire(youtubeInput, (channelName, channelUrl) => {
                    const tab = BranchProxy.findTab(tabId);
                    const guide = tab?.trophy_guides?.find(g => g.trophy_id === t.trophy_id);
                    if (!guide) return;
                    guide.youtube_channel_name = channelName;
                    guide.youtube_channel_url = channelUrl;
                    BranchProxy.schedulePush();
                });
                YoutubeAttribution.showInitial(
                    youtubeInput,
                    (typeof guideData === 'object' && guideData.youtube_channel_name) || '',
                    (typeof guideData === 'object' && guideData.youtube_channel_url) || '',
                );
            }

            // Render ownership badge + apply writer-scoping read-only state.
            // Editors and publishers bypass; writers may only edit their own
            // (or untouched/ownerless) guides.
            const guideOwnerId = (typeof guideData === 'object' && guideData.created_by_id) || null;
            applyOwnership(el, guideOwnerId, 'trophy-guide-owner');

            container.appendChild(el);

            const gallerySection = el.querySelector('.gallery-section');
            if (gallerySection) {
                GalleryController.mountSection(gallerySection, {
                    kind: 'trophy_guide', id: t.trophy_id, tabId,
                });
            }
        });

        // Re-mount note indicators on the freshly-rendered trophy guide rows.
        window.PlatPursuit?.RoadmapNotes?.mountAllIndicators?.();
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

    // Save tooltip wording depends on publish state — published roadmaps push
    // changes live to readers immediately, draft roadmaps stay invisible until
    // a publisher publishes them. Kept here so it stays in sync with the
    // template's initial server-rendered tooltip in roadmap_edit.html.
    const SAVE_TOOLTIP_PUBLISHED = 'Commit your draft branch to the live roadmap. Readers will see your changes immediately.';
    const SAVE_TOOLTIP_DRAFT = "Commit your draft branch to the live roadmap. The roadmap is still in draft, so readers won't see your changes until a publisher publishes it.";

    function updatePublishUI(newStatus) {
        const badge = document.getElementById('status-badge');
        if (badge) {
            badge.textContent = newStatus.charAt(0).toUpperCase() + newStatus.slice(1);
            badge.classList.toggle('badge-warning', newStatus === 'draft');
            badge.classList.toggle('badge-success', newStatus === 'published');
        }

        const saveBtn = document.getElementById('roadmap-save-btn');
        if (saveBtn) {
            saveBtn.title = newStatus === 'published' ? SAVE_TOOLTIP_PUBLISHED : SAVE_TOOLTIP_DRAFT;
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

    // `shortcut` is the keyboard combo handled by initKeyboardShortcuts. Format:
    // `[shift+]<key>` (Ctrl/Cmd is implicit). Headings deliberately omitted —
    // Ctrl+2/3/4 conflict with browser tab switching, and authors typically
    // pick a heading level once per section, not often enough to justify a
    // bespoke combo.
    const FORMAT_MAP = {
        bold:      { before: '**', after: '**', placeholder: 'bold text', shortcut: 'b' },
        italic:    { before: '*',  after: '*',  placeholder: 'italic text', shortcut: 'i' },
        underline: { before: '__', after: '__', placeholder: 'underlined text', shortcut: 'u' },
        strike:    { before: '~~', after: '~~', placeholder: 'strikethrough', shortcut: 'shift+s' },
        spoiler:   { before: '||', after: '||', placeholder: 'spoiler text', shortcut: 'shift+x' },
        link:      { before: '[',  after: '](url)', placeholder: 'link text', shortcut: 'k' },
        list:      { linePrefix: '- ' },
        quote:     { linePrefix: '> ' },
        h2:        { linePrefix: '## ' },
        h3:        { linePrefix: '### ' },
        h4:        { linePrefix: '#### ' },
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

    // Insert/replace text via execCommand instead of textarea.value = ... so
    // the browser preserves its native undo stack (Ctrl+Z works after the
    // operation). Setting `.value` directly wipes the undo history; so does
    // setRangeText. execCommand('insertText') is the only widely-supported
    // path that preserves it. Spec-deprecated but every browser still ships
    // it and there's no replacement that meets the same need.
    //
    // Helper: inserts `text` at the textarea's CURRENT selection, replacing
    // any selected range. Returns the post-insert cursor position so callers
    // can adjust selection afterward (e.g. to highlight inner content).
    function insertTextPreservingUndo(textarea, text) {
        textarea.focus();
        const ok = document.execCommand('insertText', false, text);
        if (!ok) {
            // Fallback for environments where execCommand fails (rare). Loses
            // undo but keeps the editor functional.
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            textarea.value =
                textarea.value.substring(0, start) + text + textarea.value.substring(end);
            const cursor = start + text.length;
            textarea.setSelectionRange(cursor, cursor);
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
        }
        return textarea.selectionEnd;
    }

    function applyFormat(textarea, before, after, placeholder) {
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const text = textarea.value;
        const selected = text.substring(start, end) || placeholder;
        const replacement = before + selected + after;

        const insertEnd = insertTextPreservingUndo(textarea, replacement);
        // Position cursor to select the inner text (between `before` and `after`).
        const cursorEnd = insertEnd - after.length;
        const cursorStart = cursorEnd - selected.length;
        textarea.setSelectionRange(cursorStart, cursorEnd);
    }

    function applyLinePrefix(textarea, prefix) {
        const start = textarea.selectionStart;
        const text = textarea.value;
        const lineStart = text.lastIndexOf('\n', start - 1) + 1;

        // Move cursor to the start of the current line (collapsed selection),
        // insert the prefix there, then move cursor back to where the user was
        // (now shifted right by the prefix length).
        textarea.focus();
        textarea.setSelectionRange(lineStart, lineStart);
        insertTextPreservingUndo(textarea, prefix);
        const newPos = start + prefix.length;
        textarea.setSelectionRange(newPos, newPos);
    }

    // Pad needed to insert at `pos` so it's preceded by a blank line (i.e.
    // an empty line above). Markdown needs blank-line separation between
    // most block-level elements; without it consecutive callouts merge into
    // one big blockquote and tables glue onto a preceding line.
    function leadingBlankLinePad(text, pos) {
        if (pos === 0) return '';
        if (text[pos - 1] !== '\n') return '\n\n';
        if (pos === 1) return '';
        if (text[pos - 2] === '\n') return '';
        return '\n';
    }

    // Pad needed AFTER the inserted block to ensure a blank line follows.
    // `templateEndsWithNewline` flags whether the template already contributes
    // its own trailing \n (table does, callout doesn't).
    function trailingBlankLinePad(text, pos, templateEndsWithNewline) {
        if (pos >= text.length) return '';
        if (text[pos] === '\n') {
            return templateEndsWithNewline ? '' : '\n';
        }
        return templateEndsWithNewline ? '\n' : '\n\n';
    }

    function insertTable(textarea) {
        const start = textarea.selectionStart;
        const text = textarea.value;
        const template =
            '| Header | Header |\n' +
            '|--------|--------|\n' +
            '| Cell   | Cell   |\n' +
            '| Cell   | Cell   |\n';

        // Tables need blank-line separation on both sides — without a blank
        // line above, the table glues onto the previous line and stops being
        // recognized as a table; without one below, the next block (text,
        // another callout, etc.) merges in.
        const leadingPad = leadingBlankLinePad(text, start);
        const trailingPad = trailingBlankLinePad(text, start, true);
        const replacement = leadingPad + template + trailingPad;

        // Insert at cursor (collapsed selection — no replace range).
        textarea.focus();
        textarea.setSelectionRange(start, start);
        insertTextPreservingUndo(textarea, replacement);

        // Select the first "Header" cell so the writer can immediately
        // overtype with their first column name.
        const firstHeaderStart = start + leadingPad.length + '| '.length;
        const firstHeaderEnd = firstHeaderStart + 'Header'.length;
        textarea.setSelectionRange(firstHeaderStart, firstHeaderEnd);
    }

    function insertCallout(textarea, type) {
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const text = textarea.value;
        const selected = text.substring(start, end);
        const placeholder = `Your ${type.toLowerCase()} text here.`;
        const body = selected || placeholder;

        // Each line of the body needs to start with "> " for markdown to keep
        // it inside the same blockquote (so the [!TYPE] marker on line 1
        // stays grouped with the body lines). Multi-line selections respect
        // existing line breaks.
        const wrappedBody = body.split('\n').map((line) => `> ${line}`).join('\n');
        const template = `> [!${type}]\n${wrappedBody}`;

        // Callouts MUST have blank-line separation on both sides — without
        // that, consecutive callouts merge into a single blockquote and only
        // the first [!TYPE] marker is recognized by the renderer.
        const leadingPad = leadingBlankLinePad(text, start);
        const trailingPad = trailingBlankLinePad(text, end, false);
        const replacement = leadingPad + template + trailingPad;

        // Insert at cursor — selection (if any) gets replaced by the wrapped
        // template, since execCommand inserts in place of the active range.
        insertTextPreservingUndo(textarea, replacement);

        // Drop the cursor on the body so the writer can immediately edit. If
        // they had no selection, select the placeholder so they can overtype.
        const headerLen = leadingPad.length + `> [!${type}]\n> `.length;
        const bodyStart = start + headerLen;
        const bodyEnd = bodyStart + (selected ? selected.length : placeholder.length);
        textarea.setSelectionRange(bodyStart, bodyEnd);
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

            // Table button: insert 2x2 GFM pipe-table starter at cursor.
            if (fmtKey === 'table') {
                insertTable(textarea);
                return;
            }

            // Callout dropdown items: data-fmt="callout-note" / -tip / -warning
            // / -important. Insert the > [!TYPE]\n> body template at the cursor.
            if (fmtKey && fmtKey.startsWith('callout-')) {
                const type = fmtKey.slice('callout-'.length).toUpperCase();
                insertCallout(textarea, type);
                // Close the daisyUI dropdown after picking. Blurring the
                // active element is the lightest way to do this without
                // hooking the menu's open state directly.
                if (document.activeElement && document.activeElement.blur) {
                    document.activeElement.blur();
                }
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
    //  Image Upload Modal + Gallery Controller
    //
    //  The modal is a single shared component used in four modes:
    //   - inline-insert: insert markdown image at textarea cursor.
    //   - inline-edit:   modify the markdown image at the textarea cursor.
    //   - gallery-insert: append to a step or trophy_guide gallery.
    //   - gallery-edit:   modify a gallery item by index.
    //
    //  Inline images live as `![alt](url "caption")` markdown inside the
    //  textarea body. Gallery images live as structured rows in the entry's
    //  `gallery_images` JSONField, rendered as a thumbnail grid below the
    //  prose. The two paths are intentionally distinct: inline images flow
    //  with text, gallery images are a curated grid with reorder support.
    // ------------------------------------------------------------------ //

    // Returns true / false if the URL carries an explicit `?wm=1|0` flag,
    // null otherwise. The upload API stamps the flag on every new upload,
    // so a missing flag means an older image whose state we don't know.
    function parseWatermarkFromUrl(url) {
        if (!url) return null;
        const qIdx = url.indexOf('?');
        if (qIdx === -1) return null;
        try {
            const params = new URLSearchParams(url.slice(qIdx + 1));
            const v = params.get('wm');
            if (v === '1') return true;
            if (v === '0') return false;
            return null;
        } catch (e) {
            return null;
        }
    }

    function findImageTokenAtCursor(textarea) {
        if (!textarea || textarea.tagName !== 'TEXTAREA') return null;
        const RE = /!\[([^\]\n]*)\]\(([^)\s"']+)(?:\s+"([^"]*)")?\)/g;
        const value = textarea.value;
        const cursor = textarea.selectionStart;
        let match;
        while ((match = RE.exec(value)) !== null) {
            const start = match.index;
            const end = start + match[0].length;
            if (cursor >= start && cursor <= end) {
                return {
                    start, end,
                    alt: match[1] || '',
                    url: match[2] || '',
                    title: match[3] || '',
                };
            }
        }
        return null;
    }

    const ImageUploadModal = {
        modal: null,
        state: null,

        init() {
            this.modal = document.getElementById('image-upload-modal');
            if (!this.modal) return;

            this._fileInput().addEventListener('change', () => this._onFilePicked(this._fileInput().files[0]));
            this._submitBtn().addEventListener('click', () => this._onSubmit());
            this._deleteBtn().addEventListener('click', () => this._onDelete());
            this._cancelBtn().addEventListener('click', () => this.close());
            this._closeBtn().addEventListener('click', () => this.close());
            this._watermark().addEventListener('change', () => this._refreshPreviewWatermark());

            // Drop zone is the entire dotted area + supports drops over it.
            const dropzone = this._dropzone();
            ['dragenter', 'dragover'].forEach((evt) => {
                dropzone.addEventListener(evt, (e) => {
                    if (!e.dataTransfer?.types?.includes('Files')) return;
                    e.preventDefault();
                    dropzone.classList.add('border-primary/50', 'bg-white/[0.05]');
                });
            });
            ['dragleave', 'drop'].forEach((evt) => {
                dropzone.addEventListener(evt, (e) => {
                    e.preventDefault();
                    dropzone.classList.remove('border-primary/50', 'bg-white/[0.05]');
                });
            });
            dropzone.addEventListener('drop', (e) => {
                const file = e.dataTransfer?.files?.[0];
                if (file) this._onFilePicked(file);
            });

            // Live alt-text validation: enable submit only when alt is present
            // (and either a file is chosen for insert mode or we already have a URL for edit mode).
            this._altInput().addEventListener('input', () => this._refreshSubmitState());

            // Escape closes (only when open). Backdrop clicks intentionally
            // do NOT close — the writer is filling out a form with text
            // selection, so an accidental drag past the modal edge shouldn't
            // wipe their work. They use Cancel or X to dismiss.
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && this._isOpen()) this.close();
            });
        },

        open(opts = {}) {
            if (!this.modal) return;
            if (BranchProxy.lockLost) {
                Toast.show('Editor is read-only — lock has been lost.', 'warning');
                return;
            }
            this.state = {
                mode: opts.mode || 'inline-insert',
                textarea: opts.textarea || null,
                imageToken: opts.imageToken || null,
                galleryTarget: opts.galleryTarget || null,
                galleryIndex: typeof opts.galleryIndex === 'number' ? opts.galleryIndex : null,
                currentItem: opts.currentItem || null,
                selectedFile: null,
            };
            this._renderForState();
            this.modal.classList.remove('hidden');
            this.modal.classList.add('flex');
            if (opts.prefilledFile) this._onFilePicked(opts.prefilledFile);
            // Focus alt for fast editing
            setTimeout(() => this._altInput().focus(), 50);
        },

        close() {
            if (!this.modal) return;
            this.modal.classList.add('hidden');
            this.modal.classList.remove('flex');
            this.state = null;
            this._reset();
        },

        _isOpen() { return this.modal && !this.modal.classList.contains('hidden'); },
        _q(sel) { return this.modal.querySelector(sel); },
        _fileInput() { return this._q('#image-upload-file'); },
        _altInput() { return this._q('#image-upload-alt'); },
        _captionInput() { return this._q('#image-upload-caption'); },
        _watermark() { return this._q('#image-upload-watermark'); },
        _submitBtn() { return this._q('#image-upload-submit'); },
        _deleteBtn() { return this._q('#image-upload-delete'); },
        _cancelBtn() { return this._q('#image-upload-cancel'); },
        _closeBtn() { return this._q('#image-upload-close'); },
        _dropzone() { return this._q('#image-upload-dropzone'); },
        _statusEl() { return this._q('#image-upload-status'); },
        _titleText() { return this._q('#image-upload-title-text'); },
        _captionWrap() { return this._q('#image-upload-caption-wrap'); },
        _watermarkWrap() { return this._q('#image-upload-watermark-wrap'); },
        _empty() { return this._q('#image-upload-empty'); },
        _preview() { return this._q('#image-upload-preview'); },
        _previewImg() { return this._q('#image-upload-preview-img'); },
        _filename() { return this._q('#image-upload-filename'); },
        _replaceHint() { return this._q('#image-upload-replace-hint'); },

        _reset() {
            this._fileInput().value = '';
            this._altInput().value = '';
            this._captionInput().value = '';
            this._watermark().checked = true;
            this._empty().classList.remove('hidden');
            this._preview().classList.add('hidden');
            this._previewImg().src = '';
            this._filename().textContent = '';
            this._submitBtn().disabled = true;
            this._statusEl().textContent = '';
            this._cancelBtn().disabled = false;
            this._deleteBtn().classList.add('hidden');
            this._deleteBtn().disabled = false;
        },

        _renderForState() {
            this._reset();
            const s = this.state;
            const isEdit = s.mode.endsWith('-edit');
            const isGallery = s.mode.startsWith('gallery-');

            this._titleText().textContent = isEdit
                ? 'Edit Image'
                : (isGallery ? 'Add Gallery Image' : 'Insert Image');

            // Caption is always optional. For gallery items it renders as a
            // figcaption below the thumbnail; for inline images it becomes
            // the markdown title attribute (hover tooltip). When the writer
            // leaves it blank we fall back to the alt text so there's
            // always something on hover.
            this._captionWrap().style.display = '';
            const captionLabel = this.modal.querySelector('#image-upload-caption-label-text');
            const captionHint = this.modal.querySelector('#image-upload-caption-hint');
            if (captionLabel) {
                captionLabel.textContent = isGallery ? 'Caption' : 'Hover tooltip';
            }
            if (captionHint) {
                captionHint.textContent = isGallery
                    ? 'Optional. Shown below the thumbnail.'
                    : 'Optional. Shown when readers hover the image. Leave blank to use the alt text.';
            }

            // Watermark is always visible. In edit mode without a replacement
            // file it's disabled with a hint, because the watermark is baked
            // into the saved file and can only change on a fresh upload.
            this._watermarkWrap().style.display = '';

            this._submitBtn().textContent = isEdit
                ? 'Save'
                : (isGallery ? 'Upload & Add' : 'Upload & Insert');

            if (isEdit) {
                const item = s.imageToken || s.currentItem || {};
                this._altInput().value = item.alt || '';
                this._captionInput().value = item.title || item.caption || '';
                if (item.url) {
                    this._previewImg().src = item.url;
                    this._filename().textContent = '(current image)';
                    this._empty().classList.add('hidden');
                    this._preview().classList.remove('hidden');
                }
                this._replaceHint().textContent = 'Click to replace with a different image';
                this._deleteBtn().classList.remove('hidden');

                // Reflect the actual saved watermark state. Default to
                // checked when we have no signal (older images).
                const known = parseWatermarkFromUrl(item.url);
                this._watermark().checked = known === false ? false : true;
                this.state.knownWatermark = known;
                this._setWatermarkInteractivity(false);
            } else {
                this._replaceHint().textContent = 'Click to choose a different image';
                this._watermark().checked = true;
                this.state.knownWatermark = null;
                this._setWatermarkInteractivity(true);
            }

            this._refreshSubmitState();
        },

        // Toggle the watermark control between active and dimmed-with-hint.
        // Hint surfaces in edit mode (no file picked yet) so the writer
        // understands the baked-in nature of the watermark.
        _setWatermarkInteractivity(enabled) {
            const checkbox = this._watermark();
            const label = this.modal.querySelector('#image-upload-watermark-label');
            const lockedHint = this.modal.querySelector('#image-upload-watermark-locked-hint');
            const permanentHint = this.modal.querySelector('#image-upload-watermark-permanent-hint');
            checkbox.disabled = !enabled;
            if (enabled) {
                label?.classList.remove('opacity-50', 'cursor-not-allowed', 'pointer-events-none');
                label?.classList.add('cursor-pointer');
                lockedHint?.classList.add('hidden');
                permanentHint?.classList.remove('hidden');
            } else {
                label?.classList.add('opacity-50', 'cursor-not-allowed', 'pointer-events-none');
                label?.classList.remove('cursor-pointer');
                lockedHint?.classList.remove('hidden');
                permanentHint?.classList.add('hidden');
                if (lockedHint) {
                    const known = this.state?.knownWatermark;
                    if (known === true) {
                        lockedHint.textContent = 'This image was uploaded with a watermark. Replace the file to remove or change it.';
                    } else if (known === false) {
                        lockedHint.textContent = 'This image was uploaded without a watermark. Replace the file to add one.';
                    } else {
                        lockedHint.textContent = "Replace the file to change the watermark. It is baked into the saved image and can't be removed after upload.";
                    }
                }
            }
        },

        _refreshSubmitState() {
            if (!this.state) return;
            const altOk = !!this._altInput().value.trim();
            const isEdit = this.state.mode.endsWith('-edit');
            const hasImage = isEdit
                ? (!!this.state.selectedFile || !!(this.state.imageToken?.url || this.state.currentItem?.url))
                : !!this.state.selectedFile;
            this._submitBtn().disabled = !(altOk && hasImage);
        },

        _onFilePicked(file) {
            if (!file) return;
            if (file.size > 5 * 1024 * 1024) {
                Toast.show('Image must be under 5MB.', 'error');
                return;
            }
            if (!file.type.startsWith('image/')) {
                Toast.show("That file isn't an image.", 'error');
                return;
            }
            this.state.selectedFile = file;
            this.state.previewRawUrl = null;
            this.state.previewWatermarkedUrl = null;
            // In edit mode, picking a replacement file means the new file
            // will be processed. Re-enable the watermark control and seed
            // it with the existing image's choice so the writer's earlier
            // preference is preserved on re-upload.
            if (this.state.mode.endsWith('-edit')) {
                this._setWatermarkInteractivity(true);
                const known = this.state.knownWatermark;
                this._watermark().checked = known === false ? false : true;
            }

            // Default alt text to filename (writer can override).
            const baseName = file.name.replace(/\.[^.]+$/, '');
            if (!this._altInput().value.trim()) this._altInput().value = baseName;

            const reader = new FileReader();
            reader.onload = async (e) => {
                if (this.state?.selectedFile !== file) return;
                const rawUrl = e.target.result;
                this.state.previewRawUrl = rawUrl;
                // Pre-bake the watermarked variant so toggling is instant.
                try {
                    this.state.previewWatermarkedUrl = await this._composeWatermarkedDataUrl(rawUrl);
                } catch (err) {
                    // Canvas / decode failure — preview without watermark
                    // is fine; the actual upload still gets the real one.
                    this.state.previewWatermarkedUrl = rawUrl;
                }
                if (this.state?.selectedFile !== file) return;
                this._refreshPreviewWatermark();
            };
            reader.readAsDataURL(file);

            this._filename().textContent = file.name;
            this._empty().classList.add('hidden');
            this._preview().classList.remove('hidden');
            this._refreshSubmitState();
        },

        _refreshPreviewWatermark() {
            if (!this.state) return;
            const watermarked = this._watermark().checked;
            const url = watermarked
                ? (this.state.previewWatermarkedUrl || this.state.previewRawUrl)
                : (this.state.previewRawUrl || this.state.previewWatermarkedUrl);
            if (url) this._previewImg().src = url;
        },

        // Mirror of trophies.image_utils._apply_watermark just for the
        // preview thumbnail — same text, same proportional sizing, same
        // bottom-right placement. Server is still the source of truth for
        // what gets persisted.
        async _composeWatermarkedDataUrl(rawDataUrl) {
            const img = new Image();
            await new Promise((resolve, reject) => {
                img.onload = resolve;
                img.onerror = reject;
                img.src = rawDataUrl;
            });

            // Cap canvas size at the same 2400px the server applies, scaled
            // proportionally — keeps the preview cheap on huge phone photos.
            const MAX = 2400;
            let w = img.naturalWidth;
            let h = img.naturalHeight;
            if (w > MAX || h > MAX) {
                const ratio = Math.min(MAX / w, MAX / h);
                w = Math.round(w * ratio);
                h = Math.round(h * ratio);
            }
            const canvas = document.createElement('canvas');
            canvas.width = w;
            canvas.height = h;
            const ctx = canvas.getContext('2d');
            ctx.imageSmoothingQuality = 'high';
            ctx.drawImage(img, 0, 0, w, h);

            // Keep the canvas preview in lockstep with the Pillow output in
            // trophies/image_utils.py: same percentages, same Bold weight,
            // same right-bottom anchoring so descenders sit inside the
            // margin.
            const FONT_SIZE_PCT = 0.025;
            const MARGIN_PCT = 0.015;
            const SHADOW_PCT = 0.0015;

            const text = 'www.platpursuit.com';
            const fontSize = Math.max(20, Math.round(w * FONT_SIZE_PCT));
            // Match the server's bundled font (Poppins-Bold). Falls back to
            // Inter, then system-ui — the actual saved file is always
            // rendered with Poppins-Bold by Pillow.
            ctx.font = `700 ${fontSize}px "Poppins", "Inter", system-ui, sans-serif`;
            ctx.textBaseline = 'alphabetic';
            ctx.textAlign = 'right';

            const margin = Math.max(10, Math.round(w * MARGIN_PCT));
            const shadowOffset = Math.max(2, Math.round(w * SHADOW_PCT));
            // Estimate descender depth so the visible bottom of the glyphs
            // (not the baseline) lands `margin` pixels from the bottom edge.
            // Inter's descender depth is about 22% of font size.
            const descender = Math.round(fontSize * 0.22);
            const x = w - margin;
            const y = h - margin - descender;

            ctx.fillStyle = 'rgba(0, 0, 0, 0.78)';
            ctx.fillText(text, x + shadowOffset, y + shadowOffset);
            ctx.fillStyle = 'rgba(255, 255, 255, 0.92)';
            ctx.fillText(text, x, y);

            return canvas.toDataURL('image/jpeg', 0.88);
        },

        async _uploadCurrentFile() {
            const formData = new FormData();
            formData.append('image', this.state.selectedFile);
            formData.append('watermark', this._watermark().checked ? 'true' : 'false');
            const result = await API.postFormData('/api/v1/roadmap/upload-image/', formData);
            if (!result?.url) throw new Error('Upload returned no URL.');
            return result.url;
        },

        async _onSubmit() {
            const altText = this._altInput().value.trim();
            if (!altText) {
                this._altInput().focus();
                Toast.show('Alt text is required.', 'warning');
                return;
            }
            const captionText = this._captionInput().value.trim();
            const s = this.state;

            this._submitBtn().disabled = true;
            this._cancelBtn().disabled = true;
            this._deleteBtn().disabled = true;
            this._statusEl().textContent = s.selectedFile ? 'Uploading...' : 'Saving...';
            setSaveStatus('saving');

            try {
                let url;
                if (s.selectedFile) {
                    url = await this._uploadCurrentFile();
                } else {
                    url = s.imageToken?.url || s.currentItem?.url || '';
                }
                if (!url) throw new Error('Missing image URL.');

                if (s.mode.startsWith('inline-')) {
                    this._applyInlineMarkdown({ url, alt: altText, caption: captionText });
                } else {
                    this._applyGalleryUpdate({ url, alt: altText, caption: captionText });
                }

                this.close();
                setSaveStatus('saved');
                Toast.show(s.mode.endsWith('-edit') ? 'Image updated.' : 'Image added.', 'success');
            } catch (err) {
                setSaveStatus('error');
                const errData = await err.response?.json().catch(() => null);
                Toast.show(errData?.error || 'Image save failed.', 'error');
                this._statusEl().textContent = '';
                this._submitBtn().disabled = false;
                this._cancelBtn().disabled = false;
                this._deleteBtn().disabled = false;
            }
        },

        _onDelete() {
            const s = this.state;
            if (!s) return;
            if (!confirm('Delete this image? This cannot be undone.')) return;
            if (s.mode === 'inline-edit') {
                this._removeInlineToken();
            } else if (s.mode === 'gallery-edit') {
                GalleryController.removeImage(s.galleryTarget, s.galleryIndex);
            }
            this.close();
            setSaveStatus('saved');
            Toast.show('Image deleted.', 'success');
        },

        _applyInlineMarkdown({ url, alt, caption }) {
            const ta = this.state.textarea;
            if (!ta) return;
            const safeAlt = (alt || '').replace(/[\[\]]/g, '');
            // Always emit a title so the browser shows a hover tooltip.
            // Caption wins when set; otherwise alt does double duty.
            const titleText = (caption || alt || '').replace(/"/g, '\\"');
            const md = titleText
                ? `![${safeAlt}](${url} "${titleText}")`
                : `![${safeAlt}](${url})`;

            if (this.state.mode === 'inline-edit' && this.state.imageToken) {
                const tok = this.state.imageToken;
                // Replace the existing image token by selecting it and inserting.
                // execCommand preserves native undo so Ctrl+Z reverts the swap.
                ta.focus();
                ta.setSelectionRange(tok.start, tok.end);
                insertTextPreservingUndo(ta, md);
                const pos = tok.start + md.length;
                ta.setSelectionRange(pos, pos);
            } else {
                applyFormat(ta, md, '', '');
            }
        },

        _removeInlineToken() {
            const ta = this.state.textarea;
            const tok = this.state.imageToken;
            if (!ta || !tok) return;
            const v = ta.value;
            // If the token sits alone on a line (preceded only by whitespace
            // and followed by a newline), eat the trailing newline so the
            // surrounding paragraphs don't develop a blank line ghost.
            let end = tok.end;
            if (v[end] === '\n') end += 1;
            // Select the token (plus any swallowed newline) and insert empty
            // string. Preserves undo so Ctrl+Z restores the deleted image.
            ta.focus();
            ta.setSelectionRange(tok.start, end);
            insertTextPreservingUndo(ta, '');
            ta.setSelectionRange(tok.start, tok.start);
        },

        _applyGalleryUpdate({ url, alt, caption }) {
            const s = this.state;
            if (s.mode === 'gallery-insert') {
                GalleryController.appendImage(s.galleryTarget, { url, alt, caption });
            } else if (s.mode === 'gallery-edit') {
                GalleryController.updateImage(s.galleryTarget, s.galleryIndex, { url, alt, caption });
            }
        },
    };

    // ------------------------------------------------------------------ //
    //  Gallery Controller
    //
    //  Manages the per-step / per-trophy-guide gallery sections. Owns the
    //  "Add image" button, edit affordances on thumbnails, drag reorder,
    //  and persistence back into BranchProxy.state. The structured gallery
    //  is the canonical home for non-inline images; the merge service
    //  applies the field directly to the live row on Save.
    // ------------------------------------------------------------------ //

    const GalleryController = {
        init() {
            // No-op: sections are wired lazily as steps + trophy guides render.
        },

        mountSection(section, target) {
            if (!section) return;
            section.dataset.targetKind = target.kind;
            section.dataset.targetId = String(target.id);
            section.dataset.tabId = String(target.tabId);
            this._wireSection(section);
            this.refresh(section);
        },

        _wireSection(section) {
            if (section.dataset.galleryWired === 'true') return;
            section.dataset.galleryWired = 'true';

            const addBtn = section.querySelector('.gallery-add-btn');
            if (addBtn) {
                addBtn.addEventListener('click', () => {
                    const target = this._readTarget(section);
                    if (!target) return;
                    ImageUploadModal.open({
                        mode: 'gallery-insert',
                        galleryTarget: target,
                    });
                });
            }
        },

        _readTarget(section) {
            const kind = section.dataset.targetKind;
            const id = parseInt(section.dataset.targetId, 10);
            const tabId = parseInt(section.dataset.tabId, 10) || activeTabId;
            if (!kind || Number.isNaN(id)) return null;
            return { kind, id, tabId };
        },

        // Returns the BranchProxy entry that owns this gallery, lazily
        // creating a placeholder trophy guide if the writer is adding the
        // first image to a not-yet-bodied guide.
        _entryFor(target, { create } = {}) {
            const tab = (BranchProxy.state.tabs || []).find(t => t.id === target.tabId);
            if (!tab) return null;
            if (target.kind === 'step') {
                const step = (tab.steps || []).find(s => s.id === target.id);
                if (step && !Array.isArray(step.gallery_images)) step.gallery_images = [];
                return step;
            }
            if (target.kind === 'trophy_guide') {
                let guide = (tab.trophy_guides || []).find(g => g.trophy_id === target.id);
                if (!guide && create) {
                    guide = {
                        id: BranchProxy.nextId(),
                        trophy_id: target.id,
                        body: '',
                        order: tab.trophy_guides.length,
                        is_missable: false,
                        is_online: false,
                        is_unobtainable: false,
                        gallery_images: [],
                        created_by_id: viewerProfileId,
                        last_edited_by_id: viewerProfileId,
                    };
                    tab.trophy_guides.push(guide);
                }
                if (guide && !Array.isArray(guide.gallery_images)) guide.gallery_images = [];
                return guide;
            }
            if (target.kind === 'collectible_item') {
                // Collectible items live nested under a type. Find any type
                // that owns this item id; we don't need the type id from the
                // caller because item ids are roadmap-unique on the wire.
                for (const ct of (tab.collectible_types || [])) {
                    const item = (ct.items || []).find(i => i.id === target.id);
                    if (item) {
                        if (!Array.isArray(item.gallery_images)) item.gallery_images = [];
                        return item;
                    }
                }
                return null;
            }
            return null;
        },

        appendImage(target, item) {
            const entry = this._entryFor(target, { create: true });
            if (!entry) return;
            entry.gallery_images.push(this._normalize(item));
            BranchProxy.schedulePush();
            this.refresh(this._sectionFor(target));
        },

        updateImage(target, index, item) {
            const entry = this._entryFor(target);
            if (!entry || !Array.isArray(entry.gallery_images)) return;
            if (index < 0 || index >= entry.gallery_images.length) return;
            entry.gallery_images[index] = this._normalize(item);
            BranchProxy.schedulePush();
            this.refresh(this._sectionFor(target));
        },

        removeImage(target, index) {
            const entry = this._entryFor(target);
            if (!entry || !Array.isArray(entry.gallery_images)) return;
            if (index < 0 || index >= entry.gallery_images.length) return;
            entry.gallery_images.splice(index, 1);
            BranchProxy.schedulePush();
            this.refresh(this._sectionFor(target));
        },

        reorderImages(target, newOrder) {
            const entry = this._entryFor(target);
            if (!entry || !Array.isArray(entry.gallery_images)) return;
            const next = newOrder.map(i => entry.gallery_images[i]).filter(Boolean);
            entry.gallery_images = next;
            BranchProxy.schedulePush();
        },

        _normalize(item) {
            return {
                url: (item.url || '').trim(),
                alt: (item.alt || '').trim().slice(0, 200),
                caption: (item.caption || '').trim().slice(0, 300),
            };
        },

        _sectionFor(target) {
            return document.querySelector(
                `.gallery-section[data-target-kind="${target.kind}"][data-target-id="${target.id}"]`
            );
        },

        refresh(section) {
            if (!section) return;
            const target = this._readTarget(section);
            if (!target) return;
            const entry = this._entryFor(target);
            const gallery = (entry?.gallery_images) || [];

            const grid = section.querySelector('.gallery-grid');
            const countBadge = section.querySelector('.gallery-count-badge');
            const template = document.getElementById('gallery-thumb-template');
            if (!grid || !template) return;

            grid.innerHTML = '';
            if (countBadge) countBadge.textContent = gallery.length;

            gallery.forEach((item, index) => {
                const node = template.content.firstElementChild.cloneNode(true);
                node.dataset.itemId = String(index);
                const img = node.querySelector('.gallery-thumb-img');
                img.src = item.url;
                img.alt = item.alt || '';
                if (item.caption) img.title = item.caption;
                if (item.caption) {
                    const cap = node.querySelector('.gallery-thumb-caption');
                    cap.textContent = item.caption;
                    cap.classList.remove('hidden');
                }
                const editBtn = node.querySelector('.gallery-thumb-edit');
                editBtn.addEventListener('click', () => {
                    ImageUploadModal.open({
                        mode: 'gallery-edit',
                        galleryTarget: target,
                        galleryIndex: index,
                        currentItem: item,
                    });
                });
                grid.appendChild(node);
            });

            // Tear down the previous SortableJS instance for this section so
            // we don't stack multiple listeners on the same grid each refresh.
            if (section._galleryDragManager) {
                section._galleryDragManager.destroy?.();
                section._galleryDragManager = null;
            }
            if (gallery.length > 1 && window.PlatPursuit?.DragReorderManager) {
                section._galleryDragManager = new window.PlatPursuit.DragReorderManager({
                    container: grid,
                    itemSelector: '.gallery-thumb',
                    onReorder: (itemId, newPosition, allItemIds) => {
                        const newOrder = allItemIds.map(id => parseInt(id, 10));
                        this.reorderImages(target, newOrder);
                        // Re-render so the dataset indices match the new positions.
                        this.refresh(section);
                    },
                });
            }
        },
    };

    // ------------------------------------------------------------------ //
    //  Inline Image Strip + Toolbar Edit-Mode
    //
    //  Renders a small pill per `![alt](url)` token below each formatting
    //  toolbar — click a pill to edit that specific image regardless of
    //  cursor position. Also drives the toolbar's image button into
    //  "Edit image" mode (icon swap + label) when the cursor is inside an
    //  image token, so the writer learns the cursor-based shortcut by
    //  seeing the button transform under their fingers.
    // ------------------------------------------------------------------ //

    function findAllImageTokens(textarea) {
        if (!textarea || textarea.tagName !== 'TEXTAREA') return [];
        const RE = /!\[([^\]\n]*)\]\(([^)\s"']+)(?:\s+"([^"]*)")?\)/g;
        const v = textarea.value;
        const tokens = [];
        let m;
        while ((m = RE.exec(v)) !== null) {
            tokens.push({
                start: m.index,
                end: m.index + m[0].length,
                alt: m[1] || '',
                url: m[2] || '',
                title: m[3] || '',
            });
        }
        return tokens;
    }

    const InlineImageStrip = {
        init() {
            // Refresh on text edits + cursor moves. Bubble through document
            // so dynamically rendered step / trophy guide rows are covered
            // automatically without per-row wiring.
            document.addEventListener('input', (e) => {
                if (e.target?.tagName === 'TEXTAREA') this.refreshFor(e.target);
            });
            document.addEventListener('focusin', (e) => {
                if (e.target?.tagName === 'TEXTAREA') this.refreshFor(e.target);
            });
            // selectionchange fires on the document for caret moves; the
            // active element is the source. Cheap to filter.
            document.addEventListener('selectionchange', () => {
                const ta = document.activeElement;
                if (ta?.tagName === 'TEXTAREA') this.refreshFor(ta);
            });
            // Initial pass for textareas pre-populated with content.
            this.refreshAll();
        },

        refreshAll() {
            document.querySelectorAll('#roadmap-editor textarea').forEach(ta => {
                this.refreshFor(ta);
            });
        },

        refreshFor(textarea) {
            if (!textarea?.closest('#roadmap-editor')) return;
            const toolbar = this._toolbarFor(textarea);
            if (!toolbar) return;
            const strip = this._stripFor(toolbar);
            this._renderStrip(strip, textarea);
            this._refreshImageButton(toolbar, textarea);
        },

        _toolbarFor(textarea) {
            // Walk previous siblings within the same parent first (most
            // common: <toolbar> + <textarea> in the same wrapper).
            let el = textarea.previousElementSibling;
            while (el) {
                if (el.classList?.contains('formatting-toolbar')) return el;
                el = el.previousElementSibling;
            }
            // Fall back to a search up the parent chain — covers cases where
            // a strip wrapper has been inserted between toolbar and textarea.
            let parent = textarea.parentElement;
            while (parent && parent.id !== 'roadmap-editor') {
                const tb = parent.querySelector('.formatting-toolbar');
                if (tb && parent.contains(textarea)) return tb;
                parent = parent.parentElement;
            }
            return null;
        },

        _stripFor(toolbar) {
            // The strip is a sibling immediately after the toolbar.
            let el = toolbar.nextElementSibling;
            while (el) {
                if (el.classList?.contains('inline-images-strip')) return el;
                if (el.classList?.contains('formatting-toolbar')) return null;
                el = el.nextElementSibling;
            }
            return null;
        },

        _renderStrip(strip, textarea) {
            if (!strip) return;
            const tokens = findAllImageTokens(textarea);
            // Wipe and rebuild the pill list. The label span stays.
            strip.querySelectorAll('.inline-image-pill').forEach(p => p.remove());

            if (!tokens.length) {
                strip.classList.add('hidden');
                strip.classList.remove('flex');
                return;
            }
            strip.classList.remove('hidden');
            strip.classList.add('flex');

            const template = document.getElementById('inline-image-pill-template');
            if (!template) return;

            tokens.forEach((tok) => {
                const pill = template.content.firstElementChild.cloneNode(true);
                const thumb = pill.querySelector('.inline-image-pill-thumb');
                const altSpan = pill.querySelector('.inline-image-pill-alt');
                thumb.src = tok.url;
                thumb.alt = tok.alt || '';
                const label = tok.alt || tok.url.split('/').pop() || '(image)';
                altSpan.textContent = label;
                pill.title = `Edit image: ${tok.alt || tok.url}`;
                pill.addEventListener('click', () => {
                    // Re-scan in case the source has shifted since render.
                    const fresh = findAllImageTokens(textarea).find(
                        t => t.url === tok.url && t.start === tok.start
                    ) || tok;
                    ImageUploadModal.open({
                        mode: 'inline-edit',
                        textarea,
                        imageToken: fresh,
                    });
                });
                strip.appendChild(pill);
            });
        },

        _refreshImageButton(toolbar, textarea) {
            const btn = toolbar.querySelector('[data-fmt="image"]');
            if (!btn) return;
            const iconDefault = btn.querySelector('.image-icon-default');
            const iconEdit = btn.querySelector('.image-icon-edit');
            const label = btn.querySelector('.image-btn-label');
            // Only show edit-mode if the cursor is in this textarea AND
            // sits inside an image token. Otherwise stay in insert mode.
            const inToken = (document.activeElement === textarea)
                ? findImageTokenAtCursor(textarea) : null;

            if (inToken) {
                btn.classList.add('btn-active', 'text-primary');
                btn.title = 'Edit image at cursor';
                iconDefault?.classList.add('hidden');
                iconEdit?.classList.remove('hidden');
                label?.classList.remove('hidden');
            } else {
                btn.classList.remove('btn-active', 'text-primary');
                btn.title = 'Upload Image';
                iconDefault?.classList.remove('hidden');
                iconEdit?.classList.add('hidden');
                label?.classList.add('hidden');
            }
        },
    };

    // Toolbar image button + cursor-in-token edit detection.
    function uploadImage(textarea) {
        const tok = findImageTokenAtCursor(textarea);
        if (tok) {
            ImageUploadModal.open({
                mode: 'inline-edit',
                textarea,
                imageToken: tok,
            });
        } else {
            ImageUploadModal.open({
                mode: 'inline-insert',
                textarea,
            });
        }
    }

    // Drag-drop a file onto a textarea, or paste an image from the clipboard.
    // Both pre-fill the modal in inline-insert mode at the focused textarea.
    function initImageDropAndPaste() {
        document.addEventListener('paste', (e) => {
            const ta = e.target;
            if (!ta || ta.tagName !== 'TEXTAREA') return;
            if (!ta.closest('#roadmap-editor')) return;
            if (BranchProxy.lockLost) return;
            const items = e.clipboardData?.items || [];
            for (const item of items) {
                if (item.kind === 'file' && item.type.startsWith('image/')) {
                    const file = item.getAsFile();
                    if (file) {
                        e.preventDefault();
                        ImageUploadModal.open({
                            mode: 'inline-insert',
                            textarea: ta,
                            prefilledFile: file,
                        });
                        return;
                    }
                }
            }
        });

        document.addEventListener('dragover', (e) => {
            const ta = e.target?.closest?.('textarea');
            if (!ta || !ta.closest('#roadmap-editor')) return;
            if (e.dataTransfer?.types?.includes('Files')) {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'copy';
            }
        });
        document.addEventListener('drop', (e) => {
            const ta = e.target?.closest?.('textarea');
            if (!ta || !ta.closest('#roadmap-editor')) return;
            if (BranchProxy.lockLost) return;
            const file = e.dataTransfer?.files?.[0];
            if (file && file.type.startsWith('image/')) {
                e.preventDefault();
                ImageUploadModal.open({
                    mode: 'inline-insert',
                    textarea: ta,
                    prefilledFile: file,
                });
            }
        });
    }

    // ------------------------------------------------------------------ //
    //  Keyboard Shortcuts
    // ------------------------------------------------------------------ //

    // Shortcut keys map to FORMAT_MAP entry names. Ctrl/Cmd is implicit.
    // `shift+<key>` requires Shift; bare `<key>` requires no Shift. Lookup is
    // built once at module load from FORMAT_MAP.shortcut entries plus the
    // dedicated insertTable binding (which lives outside FORMAT_MAP).
    const SHORTCUT_TABLE = (() => {
        const table = {};
        Object.entries(FORMAT_MAP).forEach(([key, def]) => {
            if (def.shortcut) table[def.shortcut] = key;
        });
        return table;
    })();

    function initKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            if (!e.ctrlKey && !e.metaKey) return;
            const textarea = document.activeElement;
            if (!textarea || textarea.tagName !== 'TEXTAREA') return;
            if (!textarea.closest('#roadmap-editor')) return;

            const combo = (e.shiftKey ? 'shift+' : '') + e.key.toLowerCase();
            const action = SHORTCUT_TABLE[combo];
            if (!action) return;

            e.preventDefault();

            const fmt = FORMAT_MAP[action];
            if (!fmt) return;
            if (fmt.linePrefix) {
                applyLinePrefix(textarea, fmt.linePrefix);
            } else {
                applyFormat(textarea, fmt.before, fmt.after, fmt.placeholder);
            }
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
    //  Collectibles (areas + types + items)
    // ------------------------------------------------------------------ //
    /**
     * Per-roadmap collectible vocabulary editor.
     *
     * Areas, types, and items are all backed by BranchProxy state via
     * synthetic legacy URLs. The render functions tear down and rebuild
     * DOM rows from the in-memory tabsData mirror; field-level edits
     * patch the branch via debounced saves; reorders use DragReorderManager.
     *
     * The controller also owns the formatting-toolbar collectible insert
     * button and the `[[` autocomplete popover, since both surface the
     * same picker UI.
     */
    const CollectibleController = {
        // ── Public API ─────────────────────────────────────────────
        init() {
            this._wireAreaAddBtn();
            this._wireTypeAddBtn();
            this._wireGlobalKeyHandlers();
            this._wirePicker();
        },

        renderAll(tabId) {
            // BranchProxy.state is the canonical store for collectibles;
            // tabsData is a derived view that the rest of the editor reads
            // from. Re-mirror on every render so CRUD paths only have to
            // mutate one store (BranchProxy) and we never paint stale data.
            this._syncTabsDataFromBranch(tabId);
            this._renderTypes(tabId);
            this._renderAreas(tabId);
            this._refreshFormattingToolbarVisibility(tabId);
        },

        _syncTabsDataFromBranch(tabId) {
            const tab = tabsData.find(t => t.id === tabId);
            const branchTab = (BranchProxy.state?.tabs || []).find(t => t.id === tabId);
            if (!tab || !branchTab) return;
            tab.collectible_areas = (branchTab.collectible_areas || []).map(a => ({ ...a }));
            tab.collectible_types = (branchTab.collectible_types || []).map(ct => ({
                ...ct,
                items: (ct.items || []).map(it => ({ ...it })),
            }));
        },

        // Flatten items for an area across all types, sorted by `order`.
        // areaId === null returns the "Unsorted" bucket. Each result item
        // carries `_typeId/_typeName/_color/_icon` for render convenience.
        _itemsForArea(tab, areaId) {
            const out = [];
            (tab.collectible_types || []).forEach(ct => {
                (ct.items || []).forEach(it => {
                    if ((it.area_id ?? null) === areaId) {
                        out.push({
                            ...it,
                            _typeId: ct.id,
                            _typeName: ct.name || '(unnamed)',
                            _color: ct.color || 'primary',
                            _icon: ct.icon || COLLECTIBLE_DEFAULT_ICON,
                        });
                    }
                });
            });
            out.sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
            return out;
        },

        // True iff any item across all types has area_id === null. Used to
        // decide whether to render the "Unsorted" pseudo-area card.
        _hasUnsortedItems(tab) {
            return (tab.collectible_types || []).some(ct =>
                (ct.items || []).some(it => (it.area_id ?? null) === null)
            );
        },

        // ── Areas rendering ────────────────────────────────────────
        _renderAreas(tabId) {
            const tab = tabsData.find(t => t.id === tabId);
            if (!tab) return;
            const container = document.querySelector(
                `.collectible-areas-container[data-tab-id="${tabId}"]`
            );
            if (!container) return;
            container.innerHTML = '';

            const empty = container.parentElement.querySelector('.collectible-areas-empty');
            const countBadge = container.parentElement.querySelector('.collectible-areas-count');
            const areas = tab.collectible_areas || [];
            if (countBadge) countBadge.textContent = String(areas.length);

            const hasUnsorted = this._hasUnsortedItems(tab);
            // Empty-state visible only when there are no real areas AND no
            // orphaned items needing the Unsorted bucket.
            if (empty) empty.classList.toggle('hidden', areas.length > 0 || hasUnsorted);

            areas.forEach(area => {
                const el = this._buildAreaCard(tabId, tab, area);
                container.appendChild(el);
            });
            // Append a "Unsorted" pseudo-area at the end if any items have
            // area_id=null. Render-only: the editor doesn't create a real
            // RoadmapCollectibleArea row for it.
            if (hasUnsorted) {
                const el = this._buildAreaCard(tabId, tab, null);
                container.appendChild(el);
            }

            this._initAreaDragReorder(tabId);
        },

        _buildAreaCard(tabId, tab, area) {
            // `area === null` → render the "Unsorted" pseudo-card (no name
            // input, no delete, no drag handle, no real id).
            const tpl = document.getElementById('collectible-area-card-template');
            const el = tpl.content.firstElementChild.cloneNode(true);
            const isUnsorted = area === null;
            const areaId = isUnsorted ? null : area.id;
            el.dataset.areaId = isUnsorted ? 'null' : String(areaId);
            if (!isUnsorted) el.dataset.itemId = String(areaId);
            // Open by default so authors can see items without an extra click.
            el.open = true;

            const nameInput = el.querySelector('.collectible-area-name-input');
            const deleteBtn = el.querySelector('.collectible-area-delete-btn');
            const handle = el.querySelector('.collectible-area-handle');

            if (isUnsorted) {
                // Hide author-only chrome on the pseudo-card.
                nameInput.value = 'Unsorted';
                nameInput.disabled = true;
                nameInput.classList.add('italic', 'text-base-content/55');
                deleteBtn?.classList.add('hidden');
                handle?.classList.add('invisible');
            } else {
                nameInput.value = area.name || '';
                const debounced = debounce(
                    () => this._patchArea(tabId, areaId, { name: nameInput.value }),
                    600,
                );
                nameInput.addEventListener('input', () => {
                    setSaveStatus('unsaved');
                    debounced();
                });
                nameInput.addEventListener('click', (e) => e.stopPropagation());
                _stopSummaryToggleKeys(nameInput);
                deleteBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this._deleteArea(tabId, areaId);
                });
            }

            // Items list inside the area
            const items = this._itemsForArea(tab, areaId);
            const itemsContainer = el.querySelector('.collectible-items-container');
            const itemsEmpty = el.querySelector('.collectible-items-empty');
            const noTypesHint = el.querySelector('.collectible-no-types-hint');
            const countBadge = el.querySelector('.collectible-area-item-count');
            countBadge.textContent = `${items.length} item${items.length === 1 ? '' : 's'}`;

            const types = tab.collectible_types || [];
            const noTypes = types.length === 0;

            if (items.length === 0) {
                itemsContainer.classList.add('hidden');
                if (noTypes) {
                    itemsEmpty.classList.add('hidden');
                    noTypesHint.classList.remove('hidden');
                } else {
                    itemsEmpty.classList.remove('hidden');
                    noTypesHint.classList.add('hidden');
                }
            } else {
                itemsContainer.classList.remove('hidden');
                itemsEmpty.classList.add('hidden');
                noTypesHint.classList.add('hidden');
            }

            items.forEach(item => {
                const row = this._buildItemElement(tabId, tab, item, areaId);
                itemsContainer.appendChild(row);
            });

            // Bulk-paste (per-area, with type picker)
            const bulkBtn = el.querySelector('.collectible-bulk-toggle-btn');
            const bulkWrap = el.querySelector('.collectible-bulk-paste-wrap');
            const bulkInput = el.querySelector('.collectible-bulk-paste-input');
            const bulkApply = el.querySelector('.collectible-bulk-apply-btn');
            const bulkCancel = el.querySelector('.collectible-bulk-cancel-btn');
            const bulkTypeSelect = el.querySelector('.collectible-bulk-type-select');
            this._populateTypeSelect(bulkTypeSelect, types, types[0]?.id);
            // Disable the bulk-paste path entirely when there are no types.
            bulkBtn.disabled = noTypes;
            bulkBtn.title = noTypes
                ? 'Add a Collectible Type above first'
                : 'Bulk-paste a list of item names';
            bulkBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (noTypes) return;
                bulkWrap.classList.toggle('hidden');
                if (!bulkWrap.classList.contains('hidden')) bulkInput.focus();
            });
            bulkCancel.addEventListener('click', (e) => {
                e.stopPropagation();
                bulkInput.value = '';
                bulkWrap.classList.add('hidden');
            });
            bulkApply.addEventListener('click', async (e) => {
                e.stopPropagation();
                const lines = bulkInput.value
                    .split('\n')
                    .map(l => l.trim())
                    .filter(l => l.length > 0);
                if (lines.length === 0) return;
                const typeId = parseInt(bulkTypeSelect.value, 10);
                if (!typeId) return;
                bulkApply.disabled = true;
                try {
                    const seg = isUnsorted ? 'null' : String(areaId);
                    for (const line of lines) {
                        await apiCall(
                            'post',
                            `/api/v1/roadmap/${roadmapId}/tab/${tabId}/collectible-areas/${seg}/items/`,
                            { type_id: typeId, name: line.slice(0, 200) }
                        );
                    }
                    bulkInput.value = '';
                    bulkWrap.classList.add('hidden');
                    Toast.show(`Added ${lines.length} item${lines.length === 1 ? '' : 's'}.`, 'success');
                    this.renderAll(tabId);
                } catch (err) {
                    Toast.show('Failed to add items.', 'error');
                } finally {
                    bulkApply.disabled = false;
                }
            });

            // Add item button
            const addBtn = el.querySelector('.collectible-add-item-btn');
            addBtn.disabled = noTypes;
            addBtn.title = noTypes ? 'Add a Collectible Type above first' : 'Add an item to this area';
            addBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (noTypes) return;
                this._addItemToArea(tabId, areaId);
            });

            // Per-area item drag reorder, attached after the items list is
            // populated.
            this._initItemDragReorderForArea(tabId, areaId, itemsContainer);

            return el;
        },

        _populateTypeSelect(selectEl, types, currentValue) {
            selectEl.innerHTML = '';
            (types || []).forEach(t => {
                const opt = document.createElement('option');
                opt.value = String(t.id);
                opt.textContent = `${t.icon || COLLECTIBLE_DEFAULT_ICON} ${t.name || '(unnamed)'}`;
                selectEl.appendChild(opt);
            });
            if (currentValue != null) selectEl.value = String(currentValue);
        },

        async _addArea(tabId) {
            try {
                const created = await apiCall(
                    'post',
                    `/api/v1/roadmap/${roadmapId}/tab/${tabId}/collectible-areas/`,
                    { name: 'New Area' }
                );
                this.renderAll(tabId);
                // Focus the new chip's input so the writer can immediately type.
                requestAnimationFrame(() => {
                    const newChip = document.querySelector(
                        `.collectible-area-chip[data-area-id="${created.id}"] .collectible-area-name-input`
                    );
                    newChip?.focus();
                    newChip?.select();
                });
            } catch (err) {
                Toast.show('Failed to add area.', 'error');
            }
        },

        async _patchArea(tabId, areaId, patch) {
            try {
                await apiCall(
                    'patch',
                    `/api/v1/roadmap/${roadmapId}/tab/${tabId}/collectible-areas/${areaId}/`,
                    patch
                );
                // Update local mirror without full re-render to preserve focus.
                const tab = tabsData.find(t => t.id === tabId);
                const a = (tab?.collectible_areas || []).find(x => x.id === areaId);
                if (a && 'name' in patch) a.name = patch.name;
            } catch (err) {
                Toast.show('Failed to update area.', 'error');
            }
        },

        async _deleteArea(tabId, areaId) {
            const tab = tabsData.find(t => t.id === tabId);
            const area = (tab?.collectible_areas || []).find(a => a.id === areaId);
            const used = (tab?.collectible_types || []).reduce((acc, ct) => {
                return acc + (ct.items || []).filter(i => i.area_id === areaId).length;
            }, 0);
            if (used > 0) {
                if (!confirm(`Delete "${area?.name || 'this area'}"? ${used} item${used === 1 ? '' : 's'} will become "Unsorted".`)) return;
            }
            try {
                await apiCall(
                    'delete',
                    `/api/v1/roadmap/${roadmapId}/tab/${tabId}/collectible-areas/${areaId}/`
                );
                if (tab) {
                    tab.collectible_areas = (tab.collectible_areas || []).filter(a => a.id !== areaId);
                    (tab.collectible_types || []).forEach(ct => {
                        (ct.items || []).forEach(it => {
                            if (it.area_id === areaId) it.area_id = null;
                        });
                    });
                }
                this.renderAll(tabId);
            } catch (err) {
                Toast.show('Failed to delete area.', 'error');
            }
        },

        _initAreaDragReorder(tabId) {
            const container = document.querySelector(
                `.collectible-areas-container[data-tab-id="${tabId}"]`
            );
            if (!container) return;
            new window.PlatPursuit.DragReorderManager({
                container,
                handleSelector: '.collectible-area-handle',
                itemSelector: '.collectible-area-card',
                onReorder: async (_itemId, _pos, allItemIds) => {
                    // The Unsorted pseudo-card has data-area-id="null" and
                    // is excluded from the server's reorder set — its sort
                    // position is conceptually fixed (always at the end).
                    const realIds = allItemIds.filter(x => x !== 'null').map(x => parseInt(x, 10));
                    try {
                        await apiCall(
                            'post',
                            `/api/v1/roadmap/${roadmapId}/tab/${tabId}/collectible-areas/reorder/`,
                            { area_ids: realIds }
                        );
                        const tab = tabsData.find(t => t.id === tabId);
                        if (tab) {
                            const m = {};
                            (tab.collectible_areas || []).forEach(a => { m[a.id] = a; });
                            tab.collectible_areas = realIds.map(id => m[id]).filter(Boolean);
                            tab.collectible_areas.forEach((a, i) => { a.order = i; });
                        }
                    } catch (err) {
                        Toast.show('Failed to reorder areas.', 'error');
                    }
                },
            });
        },

        // ── Types rendering ────────────────────────────────────────
        _renderTypes(tabId) {
            const tab = tabsData.find(t => t.id === tabId);
            if (!tab) return;
            const container = document.querySelector(
                `.collectible-types-container[data-tab-id="${tabId}"]`
            );
            if (!container) return;
            container.innerHTML = '';

            const empty = container.parentElement.querySelector('.collectible-types-empty');
            const countBadge = container.parentElement.querySelector('.collectible-types-count');
            const types = tab.collectible_types || [];
            if (countBadge) countBadge.textContent = String(types.length);
            if (empty) empty.classList.toggle('hidden', types.length > 0);

            types.forEach(ct => {
                const el = this._buildTypeElement(tabId, ct, tab.collectible_areas || []);
                container.appendChild(el);
            });

            this._initTypeDragReorder(tabId);
        },

        _buildTypeElement(tabId, ctype, _areas) {
            // _areas was used by the legacy per-type items list; kept for
            // call-site compatibility but unused now (items live in areas).
            const tpl = document.getElementById('collectible-type-template');
            const el = tpl.content.firstElementChild.cloneNode(true);
            el.dataset.typeId = ctype.id;
            el.dataset.itemId = ctype.id;
            el.dataset.tabId = tabId;

            // Name + slug pill
            const nameInput = el.querySelector('.collectible-type-name-input');
            nameInput.value = ctype.name || '';
            const slugPill = el.querySelector('.collectible-type-slug-pill');
            const updateSlugPill = () => {
                const slug = ctype.slug || _clientSlugify(ctype.name) || '...';
                slugPill.textContent = `[[${slug}]]`;
                slugPill.classList.toggle('hidden', !ctype.name);
            };
            updateSlugPill();

            // Color swatch + icon glyph in summary
            const swatch = el.querySelector('.collectible-type-color-swatch');
            swatch.dataset.color = ctype.color || 'primary';
            const iconGlyph = el.querySelector('.collectible-type-icon-glyph');
            iconGlyph.textContent = ctype.icon || COLLECTIBLE_DEFAULT_ICON;

            // Total count + items-using-this-type badge. The badge counts
            // items still nested under this type in BranchProxy state —
            // useful as a "what depends on this type" gut-check before
            // deleting it.
            const totalInput = el.querySelector('.collectible-type-total-input');
            totalInput.value = ctype.total_count == null ? '' : String(ctype.total_count);
            const itemCountBadge = el.querySelector('.collectible-type-item-count');
            itemCountBadge.textContent = String((ctype.items || []).length);

            // Color picker (in expanded body)
            const colorPicker = el.querySelector('.collectible-type-color-picker');
            colorPicker.querySelectorAll('.collectible-color-btn').forEach(btn => {
                btn.classList.toggle('is-selected', btn.dataset.color === (ctype.color || 'primary'));
                btn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    this._patchType(tabId, ctype.id, { color: btn.dataset.color });
                    colorPicker.querySelectorAll('.collectible-color-btn').forEach(b =>
                        b.classList.toggle('is-selected', b === btn)
                    );
                    swatch.dataset.color = btn.dataset.color;
                    if (ctype) ctype.color = btn.dataset.color;
                });
            });

            // Icon input
            const iconInput = el.querySelector('.collectible-type-icon-input');
            iconInput.value = ctype.icon || COLLECTIBLE_DEFAULT_ICON;
            const debouncedIcon = debounce(() => {
                const v = iconInput.value.trim().slice(0, 8) || COLLECTIBLE_DEFAULT_ICON;
                this._patchType(tabId, ctype.id, { icon: v });
                iconGlyph.textContent = v;
                ctype.icon = v;
            }, 500);
            iconInput.addEventListener('input', () => { setSaveStatus('unsaved'); debouncedIcon(); });

            // Description input
            const descInput = el.querySelector('.collectible-type-description-input');
            descInput.value = ctype.description || '';
            const debouncedDesc = debounce(() => {
                const v = descInput.value.slice(0, 200);
                this._patchType(tabId, ctype.id, { description: v });
                ctype.description = v;
            }, 600);
            descInput.addEventListener('input', () => { setSaveStatus('unsaved'); debouncedDesc(); });

            // Name editing — also updates slug pill preview
            const debouncedName = debounce(() => {
                const v = nameInput.value.trim();
                this._patchType(tabId, ctype.id, { name: v });
                ctype.name = v;
                updateSlugPill();
            }, 600);
            nameInput.addEventListener('input', () => { setSaveStatus('unsaved'); debouncedName(); });
            nameInput.addEventListener('click', (e) => e.stopPropagation());
            _stopSummaryToggleKeys(nameInput);

            // Total count
            const debouncedTotal = debounce(() => {
                const raw = totalInput.value;
                const v = raw === '' ? null : Math.max(0, parseInt(raw, 10) || 0);
                this._patchType(tabId, ctype.id, { total_count: v });
                ctype.total_count = v;
            }, 500);
            totalInput.addEventListener('input', () => { setSaveStatus('unsaved'); debouncedTotal(); });
            totalInput.addEventListener('click', (e) => e.stopPropagation());
            _stopSummaryToggleKeys(totalInput);

            // Delete type
            el.querySelector('.collectible-type-delete-btn').addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                const itemsCount = (ctype.items || []).length;
                const msg = itemsCount > 0
                    ? `Delete "${ctype.name || 'this type'}" and ${itemsCount} item${itemsCount === 1 ? '' : 's'}? This can't be undone (until you discard the branch).`
                    : `Delete "${ctype.name || 'this type'}"?`;
                if (!confirm(msg)) return;
                this._deleteType(tabId, ctype.id);
            });

            // Stop summary clicks within these inputs from toggling the <details>.
            el.querySelector('summary')?.addEventListener('click', (e) => {
                // Only toggle when the click is on the summary background or chevron.
                const t = e.target;
                if (t.closest('input, select, button, .collectible-color-btn')) {
                    e.preventDefault();
                }
            });

            return el;
        },

        async _addType(tabId) {
            try {
                const created = await apiCall(
                    'post',
                    `/api/v1/roadmap/${roadmapId}/tab/${tabId}/collectible-types/`,
                    { name: 'New Collectible Type' }
                );
                this.renderAll(tabId);
                requestAnimationFrame(() => {
                    const card = document.querySelector(
                        `.collectible-type-card[data-type-id="${created.id}"]`
                    );
                    if (card) {
                        card.open = true;
                        card.querySelector('.collectible-type-name-input')?.focus();
                        card.querySelector('.collectible-type-name-input')?.select();
                    }
                });
            } catch (err) {
                Toast.show('Failed to add collectible type.', 'error');
            }
        },

        async _patchType(tabId, typeId, patch) {
            try {
                await apiCall(
                    'patch',
                    `/api/v1/roadmap/${roadmapId}/tab/${tabId}/collectible-types/${typeId}/`,
                    patch
                );
            } catch (err) {
                Toast.show('Failed to update type.', 'error');
            }
        },

        async _deleteType(tabId, typeId) {
            try {
                await apiCall(
                    'delete',
                    `/api/v1/roadmap/${roadmapId}/tab/${tabId}/collectible-types/${typeId}/`
                );
                const tab = tabsData.find(t => t.id === tabId);
                if (tab) {
                    tab.collectible_types = (tab.collectible_types || []).filter(t => t.id !== typeId);
                }
                this.renderAll(tabId);
            } catch (err) {
                Toast.show('Failed to delete type.', 'error');
            }
        },

        _initTypeDragReorder(tabId) {
            const container = document.querySelector(
                `.collectible-types-container[data-tab-id="${tabId}"]`
            );
            if (!container) return;
            new window.PlatPursuit.DragReorderManager({
                container,
                handleSelector: '.collectible-type-handle',
                itemSelector: '.collectible-type-card',
                onReorder: async (_itemId, _pos, allItemIds) => {
                    try {
                        await apiCall(
                            'post',
                            `/api/v1/roadmap/${roadmapId}/tab/${tabId}/collectible-types/reorder/`,
                            { type_ids: allItemIds.map(x => parseInt(x, 10)) }
                        );
                        const tab = tabsData.find(t => t.id === tabId);
                        if (tab) {
                            const m = {};
                            (tab.collectible_types || []).forEach(t => { m[t.id] = t; });
                            tab.collectible_types = allItemIds.map(id => m[parseInt(id, 10)]).filter(Boolean);
                            tab.collectible_types.forEach((t, i) => { t.order = i; });
                        }
                    } catch (err) {
                        Toast.show('Failed to reorder types.', 'error');
                    }
                },
            });
        },

        // ── Items rendering ────────────────────────────────────────
        // Items are rendered inside their containing area card (not under
        // a type) so authors see playthrough order. The owning type is a
        // PROPERTY of the row (color/icon swatch + dropdown selector).
        // Storage stays nested under types in BranchProxy state — we look
        // up the current type from the in-memory tab on each interaction.

        _findTypeForItem(tab, itemId) {
            for (const ct of (tab.collectible_types || [])) {
                if ((ct.items || []).some(i => i.id === itemId)) return ct;
            }
            return null;
        },

        _buildItemElement(tabId, tab, item, areaId) {
            // `item` here is enriched with `_typeId/_typeName/_color/_icon`
            // by `_itemsForArea`. `areaId` is the area this row belongs to
            // (null for the Unsorted bucket).
            const tpl = document.getElementById('collectible-item-template');
            const el = tpl.content.firstElementChild.cloneNode(true);
            el.dataset.itemId = item.id;
            el.dataset.itemRowId = item.id;
            el.dataset.typeId = String(item._typeId);

            // Type swatch (color + icon) reflecting the item's current type
            const swatch = el.querySelector('.collectible-item-type-swatch');
            swatch.dataset.color = item._color;
            const iconSpan = el.querySelector('.collectible-item-type-icon');
            iconSpan.textContent = item._icon;

            // Name input
            const nameInput = el.querySelector('.collectible-item-name-input');
            nameInput.value = item.name || '';
            const debouncedName = debounce(() => {
                this._patchItem(tabId, item._typeId, item.id, { name: nameInput.value });
                item.name = nameInput.value;
            }, 600);
            nameInput.addEventListener('input', () => { setSaveStatus('unsaved'); debouncedName(); });

            // Type dropdown — switching reassigns the owning type. The
            // BranchProxy handler relocates the item across the nested
            // collectible_types[*].items[] arrays; the merge service's
            // pre-pass re-points the FK on the live row. Selecting a new
            // type triggers a full re-render so the swatch/icon refresh.
            const typeSelect = el.querySelector('.collectible-item-type-select');
            this._populateTypeSelect(typeSelect, tab.collectible_types || [], item._typeId);
            typeSelect.addEventListener('change', () => {
                const newTypeId = parseInt(typeSelect.value, 10);
                if (!newTypeId || newTypeId === item._typeId) return;
                // If the current name still matches the OLD type's
                // default-name pattern, regenerate the default for the
                // NEW type. Anything else is treated as admin
                // customization and left alone.
                const types = tab.collectible_types || [];
                const oldType = types.find(t => t.id === item._typeId);
                const newType = types.find(t => t.id === newTypeId);
                const patch = { type_id: newTypeId };
                if (_isDefaultItemName(item.name, oldType?.name)) {
                    patch.name = _defaultItemName(
                        newType?.name,
                        (newType?.items || []).length,
                    );
                }
                this._patchItem(tabId, item._typeId, item.id, patch);
                // Re-render is the simplest path — swatch, dropdown, and
                // ownership-state all change at once.
                this.renderAll(tabId);
            });

            const missableCb = el.querySelector('.collectible-item-missable');
            missableCb.checked = !!item.is_missable;
            missableCb.addEventListener('change', () => {
                this._patchItem(tabId, item._typeId, item.id, { is_missable: missableCb.checked });
                item.is_missable = missableCb.checked;
            });
            const dlcCb = el.querySelector('.collectible-item-dlc');
            dlcCb.checked = !!item.is_dlc;
            dlcCb.addEventListener('change', () => {
                this._patchItem(tabId, item._typeId, item.id, { is_dlc: dlcCb.checked });
                item.is_dlc = dlcCb.checked;
            });

            // Native <label> behavior should already toggle the wrapped
            // checkbox when the user clicks the text — but something in
            // the editor is intercepting that flow. Take ownership of
            // the label-click and toggle manually so the entire label
            // (text + box + gap) is consistently clickable.
            [missableCb, dlcCb].forEach(cb => {
                const label = cb.closest('label');
                if (!label) return;
                label.addEventListener('click', (e) => {
                    // Direct click on the input itself: let the native
                    // toggle proceed so we don't double-flip.
                    if (e.target === cb) return;
                    e.preventDefault();
                    cb.checked = !cb.checked;
                    cb.dispatchEvent(new Event('change', { bubbles: true }));
                });
            });

            // Rich content panel toggle + indicators
            const richToggle = el.querySelector('.collectible-item-rich-toggle');
            const richPanel = el.querySelector('.collectible-item-rich-panel');
            const richEmptyIcon = el.querySelector('.collectible-item-rich-empty');
            const richFilledIcon = el.querySelector('.collectible-item-rich-filled');
            const updateRichIndicator = () => {
                const hasContent = !!(item.body || item.youtube_url || (item.gallery_images || []).length);
                richEmptyIcon.classList.toggle('hidden', hasContent);
                richFilledIcon.classList.toggle('hidden', !hasContent);
            };
            updateRichIndicator();
            richToggle.addEventListener('click', () => {
                richPanel.classList.toggle('hidden');
            });

            // Body textarea
            const bodyTextarea = el.querySelector('.collectible-item-body');
            bodyTextarea.value = item.body || '';
            const debouncedBody = debounce(() => {
                this._patchItem(tabId, item._typeId, item.id, { body: bodyTextarea.value });
                item.body = bodyTextarea.value;
                updateRichIndicator();
            }, 800);
            bodyTextarea.addEventListener('input', () => { setSaveStatus('unsaved'); debouncedBody(); });

            // YouTube input + attribution
            const ytInput = el.querySelector('.collectible-item-youtube-input');
            ytInput.value = item.youtube_url || '';
            const debouncedYt = debounce(() => {
                this._patchItem(tabId, item._typeId, item.id, { youtube_url: ytInput.value });
                item.youtube_url = ytInput.value;
                updateRichIndicator();
            }, 800);
            ytInput.addEventListener('input', () => { setSaveStatus('unsaved'); debouncedYt(); });
            // The wire callback fires when the live attribution preview
            // resolves (debounced ~1.2s after typing). It must update the
            // *live BranchProxy* record so the autosaved branch payload
            // carries channel info — `item` here is the flattened render
            // copy from `_itemsForArea`, not the canonical source. Steps
            // / trophy guides do the same lookup-and-write pattern.
            YoutubeAttribution.wire(ytInput, (channelName, channelUrl) => {
                const liveItem = BranchProxy.findCollectibleItem(tabId, item.id);
                if (!liveItem) return;
                liveItem.youtube_channel_name = channelName || '';
                liveItem.youtube_channel_url = channelUrl || '';
                BranchProxy.schedulePush();
            });
            YoutubeAttribution.showInitial(ytInput, item.youtube_channel_name, item.youtube_channel_url);

            // Gallery section
            const gallerySection = el.querySelector('.gallery-section');
            if (gallerySection) {
                GalleryController.mountSection(gallerySection, {
                    kind: 'collectible_item', id: item.id, tabId,
                });
            }

            // Delete item
            el.querySelector('.collectible-item-delete-btn').addEventListener('click', () => {
                if (!confirm(`Delete "${item.name || 'this item'}"?`)) return;
                this._deleteItem(tabId, item._typeId, item.id);
            });

            return el;
        },

        async _addItemToArea(tabId, areaId) {
            const tab = tabsData.find(t => t.id === tabId);
            const types = tab?.collectible_types || [];
            if (types.length === 0) return;
            // Default new items to the LAST-edited type (heuristic: the
            // type whose most-recently-touched item lives in this area;
            // fall back to first type). This makes consecutive "Add item"
            // clicks within an area "sticky" to whatever type the author
            // is currently working in.
            const itemsHere = this._itemsForArea(tab, areaId);
            const lastTypeId = itemsHere.length > 0
                ? itemsHere[itemsHere.length - 1]._typeId
                : types[0].id;
            // Default name: "<Type> #<N>" where N is one past the existing
            // global count for that type. Authors usually only need to
            // tweak the suffix or replace entirely; pre-filling is a
            // bigger time-saver than an empty input.
            const ourType = types.find(t => t.id === lastTypeId);
            const defaultName = _defaultItemName(
                ourType?.name,
                (ourType?.items || []).length,
            );
            const seg = areaId == null ? 'null' : String(areaId);
            try {
                await apiCall(
                    'post',
                    `/api/v1/roadmap/${roadmapId}/tab/${tabId}/collectible-areas/${seg}/items/`,
                    { type_id: lastTypeId, name: defaultName }
                );
                this.renderAll(tabId);
                requestAnimationFrame(() => {
                    // Target the last row in the destination area's items
                    // container — new items always append, so the tail
                    // row is the one we just created. More robust than
                    // querying by id (which could clash with stale ids
                    // across entity types or fail on type coercion).
                    const areaCard = document.querySelector(
                        `.collectible-area-card[data-area-id="${seg}"]`
                    );
                    const itemsContainer = areaCard?.querySelector(
                        '.collectible-items-container'
                    );
                    const rows = itemsContainer?.querySelectorAll(
                        '.collectible-item-row'
                    );
                    const lastRow = rows?.[rows.length - 1];
                    const input = lastRow?.querySelector(
                        '.collectible-item-name-input'
                    );
                    if (input) {
                        input.focus();
                        input.select();
                    }
                });
            } catch (err) {
                Toast.show('Failed to add item.', 'error');
            }
        },

        async _patchItem(tabId, typeId, itemId, patch) {
            try {
                await apiCall(
                    'patch',
                    `/api/v1/roadmap/${roadmapId}/tab/${tabId}/collectible-types/${typeId}/items/${itemId}/`,
                    patch
                );
            } catch (err) {
                Toast.show('Failed to update item.', 'error');
            }
        },

        async _deleteItem(tabId, typeId, itemId) {
            try {
                await apiCall(
                    'delete',
                    `/api/v1/roadmap/${roadmapId}/tab/${tabId}/collectible-types/${typeId}/items/${itemId}/`
                );
                this.renderAll(tabId);
            } catch (err) {
                Toast.show('Failed to delete item.', 'error');
            }
        },

        _initItemDragReorderForArea(tabId, areaId, container) {
            if (!container) return;
            const seg = areaId == null ? 'null' : String(areaId);
            new window.PlatPursuit.DragReorderManager({
                container,
                handleSelector: '.collectible-item-handle',
                itemSelector: '.collectible-item-row',
                onReorder: async (_itemId, _pos, allItemIds) => {
                    const itemIds = allItemIds.map(x => parseInt(x, 10));
                    try {
                        await apiCall(
                            'post',
                            `/api/v1/roadmap/${roadmapId}/tab/${tabId}/collectible-areas/${seg}/items/reorder/`,
                            { item_ids: itemIds }
                        );
                        // Refresh order on local mirror (handler did it on
                        // the BranchProxy state, but tabsData is a copy).
                        this._syncTabsDataFromBranch(tabId);
                    } catch (err) {
                        Toast.show('Failed to reorder items.', 'error');
                    }
                },
            });
        },

        // ── Add buttons ────────────────────────────────────────────
        _wireAreaAddBtn() {
            document.querySelectorAll('.add-collectible-area-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const tabId = parseInt(btn.dataset.tabId, 10);
                    this._addArea(tabId);
                });
            });
        },

        _wireTypeAddBtn() {
            document.querySelectorAll('.add-collectible-type-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const tabId = parseInt(btn.dataset.tabId, 10);
                    this._addType(tabId);
                });
            });
        },

        // ── Formatting toolbar Collectible insert button ───────────
        _refreshFormattingToolbarVisibility(tabId) {
            // Show/hide the toolbar's collectible-insert button based on
            // whether any types are defined. Empty roadmaps don't need the
            // button — there's nothing to reference.
            const tab = tabsData.find(t => t.id === tabId);
            const hasTypes = (tab?.collectible_types || []).length > 0;
            document.querySelectorAll('.fmt-collectible-btn').forEach(btn => {
                btn.classList.toggle('hidden', !hasTypes);
            });
        },

        // ── Collectible reference picker (toolbar + autocomplete) ──
        _picker: {
            el: null, listEl: null, filterEl: null, emptyEl: null,
            anchorTextarea: null, anchorRange: null, mode: 'toolbar',
            highlightIdx: 0, filteredTypes: [],
        },

        _wirePicker() {
            const el = document.getElementById('collectible-picker');
            if (!el) return;
            this._picker.el = el;
            this._picker.listEl = document.getElementById('collectible-picker-list');
            this._picker.filterEl = document.getElementById('collectible-picker-filter');
            this._picker.emptyEl = document.getElementById('collectible-picker-empty');

            this._picker.filterEl.addEventListener('input', () => {
                this._renderPickerList(this._picker.filterEl.value || '');
            });
            this._picker.filterEl.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    e.preventDefault();
                    this._closePicker();
                } else if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    this._highlightPickerRow(this._picker.highlightIdx + 1);
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    this._highlightPickerRow(this._picker.highlightIdx - 1);
                } else if (e.key === 'Enter') {
                    e.preventDefault();
                    const t = this._picker.filteredTypes[this._picker.highlightIdx];
                    if (t) this._applyPickerSelection(t);
                }
            });

            // Toolbar button: opens picker anchored under the active textarea.
            // Wired here (not in initFormattingToolbars) so the picker is
            // owned end-to-end by the controller.
            document.addEventListener('click', (e) => {
                const btn = e.target.closest('.fmt-collectible-btn');
                if (!btn) return;
                e.preventDefault();
                const toolbar = btn.closest('.formatting-toolbar');
                const textarea = getTargetTextarea(toolbar);
                if (!textarea) return;
                this._openPickerForToolbar(textarea);
            });

            // Click-outside / Escape global handler.
            document.addEventListener('mousedown', (e) => {
                if (this._picker.el.classList.contains('hidden')) return;
                if (e.target.closest('#collectible-picker')) return;
                if (e.target.closest('.fmt-collectible-btn')) return;
                this._closePicker();
            });
        },

        _wireGlobalKeyHandlers() {
            // `[[` autocomplete: when the writer types `[[` in any roadmap
            // textarea, open the picker anchored at the caret. Selecting a
            // type completes the token to `[[slug]]`. Escape closes without
            // completing.
            document.addEventListener('input', (e) => {
                const ta = e.target;
                if (!(ta instanceof HTMLTextAreaElement)) return;
                if (!ta.closest('.collectibles-card, .step-card, .trophy-guide-row, .general-tips-card, .collectible-item-rich-panel')) return;
                const pos = ta.selectionStart;
                if (pos < 2) return;
                const justTyped = ta.value.slice(pos - 2, pos);
                if (justTyped === '[[') {
                    this._openPickerForAutocomplete(ta);
                }
            });
        },

        _openPickerForToolbar(textarea) {
            this._picker.mode = 'toolbar';
            this._picker.anchorTextarea = textarea;
            this._picker.anchorRange = null;
            const rect = textarea.getBoundingClientRect();
            this._showPickerAt(rect.left + window.scrollX, rect.bottom + window.scrollY + 4);
        },

        _openPickerForAutocomplete(textarea) {
            this._picker.mode = 'autocomplete';
            this._picker.anchorTextarea = textarea;
            // Anchor at caret. We don't have exact pixel caret math here,
            // so anchor under the textarea — close enough for the dropdown
            // to feel attached without complex offset measurement.
            const rect = textarea.getBoundingClientRect();
            this._showPickerAt(rect.left + window.scrollX, rect.bottom + window.scrollY + 4);
        },

        _showPickerAt(x, y) {
            const el = this._picker.el;
            el.style.top = `${y}px`;
            el.style.left = `${x}px`;
            el.classList.remove('hidden');
            this._picker.filterEl.value = '';
            this._renderPickerList('');
            requestAnimationFrame(() => this._picker.filterEl.focus());
        },

        _closePicker() {
            this._picker.el.classList.add('hidden');
            this._picker.anchorTextarea = null;
        },

        _renderPickerList(query) {
            const tab = tabsData.find(t => t.id === activeTabId);
            const types = (tab?.collectible_types || []);
            const q = (query || '').toLowerCase().trim();
            this._picker.filteredTypes = q
                ? types.filter(t => (t.name || '').toLowerCase().includes(q) || (t.slug || _clientSlugify(t.name)).includes(q))
                : types.slice();
            this._picker.highlightIdx = 0;

            this._picker.listEl.innerHTML = '';
            this._picker.emptyEl.classList.toggle('hidden', this._picker.filteredTypes.length > 0);

            const tpl = document.getElementById('collectible-picker-row-template');
            this._picker.filteredTypes.forEach((ct, idx) => {
                const row = tpl.content.firstElementChild.cloneNode(true);
                const swatch = row.querySelector('.collectible-picker-row-swatch');
                swatch.dataset.color = ct.color || 'primary';
                row.querySelector('.collectible-picker-row-icon').textContent = ct.icon || COLLECTIBLE_DEFAULT_ICON;
                row.querySelector('.collectible-picker-row-name').textContent = ct.name || '(unnamed)';
                const slug = ct.slug || _clientSlugify(ct.name) || 'slug';
                row.querySelector('.collectible-picker-row-slug').textContent = `[[${slug}]]`;
                const ic = (ct.items || []).length;
                row.querySelector('.collectible-picker-row-count').textContent = ic === 0 ? '' : `${ic}`;
                row.addEventListener('click', () => this._applyPickerSelection(ct));
                row.addEventListener('mouseenter', () => this._highlightPickerRow(idx));
                this._picker.listEl.appendChild(row);
            });
            this._highlightPickerRow(0);
        },

        _highlightPickerRow(idx) {
            const rows = this._picker.listEl.querySelectorAll('.collectible-picker-row');
            if (rows.length === 0) return;
            const next = ((idx % rows.length) + rows.length) % rows.length;
            rows.forEach((r, i) => r.classList.toggle('bg-primary/15', i === next));
            this._picker.highlightIdx = next;
            rows[next]?.scrollIntoView({ block: 'nearest' });
        },

        _applyPickerSelection(ctype) {
            const ta = this._picker.anchorTextarea;
            if (!ta) {
                this._closePicker();
                return;
            }
            const slug = ctype.slug || _clientSlugify(ctype.name) || 'slug';
            const token = `[[${slug}]]`;
            if (this._picker.mode === 'autocomplete') {
                // The writer just typed `[[`; replace those two chars with
                // the full token so we don't double-up brackets.
                const pos = ta.selectionStart;
                const before = ta.value.slice(0, pos - 2);
                const after = ta.value.slice(pos);
                ta.value = before + token + after;
                ta.selectionStart = ta.selectionEnd = (before + token).length;
            } else {
                insertTextPreservingUndo(ta, token);
            }
            ta.dispatchEvent(new Event('input', { bubbles: true }));
            ta.focus();
            this._closePicker();
        },
    };

    // ------------------------------------------------------------------ //
    //  Initialization
    // ------------------------------------------------------------------ //

    async function init() {
        // Acquire the lock first; if it's held by someone else the rest of
        // the editor still renders but inputs are disabled.
        await LockController.init();

        initTabs();
        initAddStepButtons();
        initStepReorderButtons();
        initTabFields();
        initMetadataFields();
        initPublishButtons();
        initFormattingToolbars();
        ImageUploadModal.init();
        GalleryController.init();
        initImageDropAndPaste();
        InlineImageStrip.init();
        initKeyboardShortcuts();
        initAutoResize();
        initSaveCancelButtons();
        CollectibleController.init();

        // Render all tabs
        tabsData.forEach(tab => {
            renderSteps(tab.id);
            renderTrophyGuides(tab.id);
            updateTrophyGuideCounter(tab.id);
            CollectibleController.renderAll(tab.id);
        });

        // Now that step + trophy guide textareas exist, populate the inline
        // image strips for any pre-existing markdown image tokens.
        InlineImageStrip.refreshAll();

        // Reset any input wiring that BranchProxy.lockLost flipped before render
        if (BranchProxy.lockLost) {
            enterReadOnlyMode('Editor is read-only.');
        }

        // One-shot flag: set by the in-page navigation confirm so we don't
        // immediately re-prompt with the browser's native dialog. Cleared
        // after beforeunload fires (so the next navigation is treated
        // independently). Tab close / refresh / browser-back still get
        // the native warning since they don't go through the click handler.
        let suppressNativeUnload = false;
        window.__roadmapEditorSuppressNativeUnload = () => { suppressNativeUnload = true; };

        // On navigate-away:
        //   - If the branch has edits made THIS session, flush them to
        //     /lock/branch/ via keepalive so the work survives the unload,
        //     and KEEP the lock alive. Advisory resume restores the session
        //     when the writer comes back.
        //   - Native browser dialog only fires when the user has edits this
        //     session that haven't synced (otherwise no point — server
        //     already has the branch). Suppressed if the in-page confirm
        //     just ran, to avoid double-prompting.
        // Both fetches use keepalive — fetch survives unload AND carries the
        // CSRF token DRF requires.
        window.addEventListener('beforeunload', (e) => {
            if (BranchProxy.dirtyThisSession && !suppressNativeUnload) {
                e.preventDefault();
                e.returnValue = '';
            }
            // Reset for the next navigation (in case this one is canceled).
            suppressNativeUnload = false;

            if (BranchProxy.lockLost) return;
            const csrfToken = window.PlatPursuit.CSRFToken?.get?.() || '';
            try {
                if (BranchProxy.dirtyThisSession) {
                    fetch(`/api/v1/roadmap/${roadmapId}/lock/branch/`, {
                        method: 'PATCH',
                        headers: {
                            'X-CSRFToken': csrfToken,
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ branch_payload: BranchProxy.state }),
                        keepalive: true,
                        credentials: 'same-origin',
                    });
                } else if (!BranchProxy.dirty) {
                    // Branch is fully clean (no resumed work either) —
                    // release the lock so others aren't held up.
                    fetch(`/api/v1/roadmap/${roadmapId}/lock/release/`, {
                        method: 'POST',
                        headers: {
                            'X-CSRFToken': csrfToken,
                            'Content-Type': 'application/json',
                        },
                        body: '{}',
                        keepalive: true,
                        credentials: 'same-origin',
                    });
                }
                // Else: resumed session with no new edits — leave lock + branch
                // alone, server already has the latest state.
            } catch (err) {
                // Best-effort; lock auto-expires anyway.
            }
        });

        // Informative warning for in-page navigation (navbar links,
        // breadcrumbs, etc.). The browser's beforeunload dialog can't be
        // customized in modern browsers, so we intercept clicks on internal
        // links and surface our own message that explains the advisory lock
        // model: leaving doesn't lose work, just keeps the session open.
        document.addEventListener('click', (e) => {
            if (!BranchProxy.dirty || BranchProxy.lockLost) return;
            const link = e.target.closest('a[href]');
            if (!link) return;
            // Skip anything that wouldn't actually unload this page.
            if (link.target === '_blank' || link.hasAttribute('download')) return;
            const href = link.getAttribute('href');
            if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
            // Skip editor-internal Cancel/Save buttons (those are <button>s
            // anyway, but Preview is an <a target="_blank">). Also skip the
            // formatting toolbar buttons that may use anchor-style markup.
            if (link.closest('[data-readonly-exempt]')) return;
            const proceed = confirm(
                'You have unsaved roadmap changes.\n\n'
                + 'Your branch is preserved on the server and your edit lock stays '
                + 'open — you can come back any time and resume right where you '
                + 'left off. The lock only releases when you save, click Cancel, '
                + 'or it goes idle long enough for another author to claim it.\n\n'
                + 'Leave the editor?'
            );
            if (!proceed) {
                e.preventDefault();
                e.stopPropagation();
            } else {
                // User already confirmed via the in-page dialog — suppress
                // the immediately-following native beforeunload warning so
                // they don't have to dismiss two dialogs for one navigation.
                window.__roadmapEditorSuppressNativeUnload?.();
            }
        }, true);  // capture-phase so we run before bubbling handlers navigate
    }

    function initSaveCancelButtons() {
        const saveBtn = document.getElementById('roadmap-save-btn');
        const cancelBtn = document.getElementById('roadmap-cancel-btn');
        if (saveBtn) {
            saveBtn.addEventListener('click', () => LockController.save());
        }
        if (cancelBtn) {
            cancelBtn.addEventListener('click', async () => {
                if (BranchProxy.dirty && !confirm(
                    'Discard your unsaved branch and exit the editor?\n\n'
                    + 'Cancel will permanently release your edit lock and '
                    + 'delete the branch. Any unsaved changes — including '
                    + 'work from a previous session you resumed — will be '
                    + 'lost.\n\n'
                    + '(If you just want to step away and come back later, '
                    + 'close the tab instead — your work will be preserved.)'
                )) return;
                // Prevent the native beforeunload dialog from firing on top
                // of the confirm the user just answered.
                window.__roadmapEditorSuppressNativeUnload?.();
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
