/**
 * Roadmap Notes Controller
 *
 * Author back-channel comments layered onto the editor: inline threads on
 * each Step + TrophyGuide row, a General Notes drawer for guide-level
 * discussion, and a heads-up banner that fires on first open after new
 * notes have been left.
 *
 * Decoupled from `roadmap_editor.js` — they share only the editor DOM and
 * a small public hook for mounting indicators on newly-created rows. Posts
 * never require holding the edit lock; any writer+ can comment any time.
 */
(function() {
    'use strict';

    const editorEl = document.getElementById('roadmap-editor');
    if (!editorEl) return;

    const API = window.PlatPursuit.API;
    const Toast = window.PlatPursuit.ToastManager;

    const roadmapId = parseInt(editorEl.dataset.roadmapId, 10);
    const viewerProfileId = parseInt(editorEl.dataset.viewerProfileId, 10) || null;
    const isEditor = editorEl.dataset.authorCanDelete === 'true';
    const profilesById = JSON.parse(
        document.getElementById('roadmap-profiles-data')?.textContent || '{}'
    );

    // ------------------------------------------------------------------ //
    //  State
    // ------------------------------------------------------------------ //

    const NotesController = {
        notes: [],                  // canonical list, sorted by created_at
        byTarget: new Map(),        // 'step:<id>' / 'trophy_guide:<id>' / 'guide' -> [note, ...]
        threadsByKey: new Map(),    // same key -> rendered thread DOM node (lazy)
        markReadFired: false,

        targetKey(kind, id) {
            return kind === 'guide' ? 'guide' : `${kind}:${id}`;
        },

        async init() {
            try {
                const result = await API.get(`/api/v1/roadmap/${roadmapId}/notes/`);
                this.notes = result.notes || [];
                this.indexNotes();
                this.mountAllIndicators();
                this.renderGeneralNotesPanel();
                this.bindGlobalUI();
                // Deep-link from a notification: if the URL has ?note=<id>,
                // navigate to it (switch tab, expand thread, scroll, flash).
                this.handleNoteDeepLink();
                // Schedule mark-read after a short delay so the user actually
                // had a chance to see the heads-up. If they leave the editor
                // before this fires, the count stays unread for next time.
                setTimeout(() => this.markRead(), 5000);
            } catch (err) {
                console.warn('Notes failed to load:', err);
            }
        },

        handleNoteDeepLink() {
            const params = new URLSearchParams(window.location.search);
            const rawId = params.get('note');
            if (!rawId) return;
            const noteId = parseInt(rawId, 10);
            if (isNaN(noteId)) return;

            // Strip the query param so a refresh doesn't re-trigger the
            // navigation. Use replaceState — no extra history entry.
            const cleanUrl = window.location.pathname + window.location.hash;
            history.replaceState(null, '', cleanUrl);

            const note = this.notes.find(n => n.id === noteId);
            if (!note) {
                Toast.show(
                    'That note couldn\'t be found — it may have been deleted or resolved.',
                    'warning',
                );
                return;
            }
            // Slight delay so initial render settles before we start
            // switching tabs / expanding threads / scrolling.
            setTimeout(() => this.navigateToNote(note), 200);
        },

        navigateToNote(note) {
            // Guide-level notes live in the drawer.
            if (note.target_kind === 'guide') {
                this.openDrawer();
                this.dismissHeadsUp();
                setTimeout(() => this.flashNoteInList(
                    document.getElementById('general-notes-list'), note.id,
                ), 150);
                return;
            }

            // Section-anchored: figure out the row + parent tab.
            const rowEl = this.findRowForNote(note);
            if (!rowEl) {
                Toast.show('Couldn\'t locate that note\'s section.', 'warning');
                return;
            }
            const panel = rowEl.closest('.roadmap-tab-panel');
            const tabId = panel ? parseInt(panel.dataset.tabId, 10) : null;
            if (tabId && !isNaN(tabId)) {
                // Use the existing tab-switching click handler so the
                // active-state styling stays consistent.
                const btn = document.querySelector(
                    `.roadmap-tab-btn[data-tab-id="${tabId}"]`
                );
                btn?.click();
            }

            // Expand the thread if it isn't already.
            const targetId = noteTargetId(note);
            const key = this.targetKey(note.target_kind, targetId);
            const existing = document.querySelector(
                `.note-thread[data-target-key="${key}"]`
            );
            if (!existing) {
                this.toggleThread(rowEl, note.target_kind, targetId);
            }

            // Wait a frame for the thread to render, then scroll + flash.
            setTimeout(() => {
                const thread = document.querySelector(
                    `.note-thread[data-target-key="${key}"]`
                );
                this.flashNoteInList(thread, note.id, rowEl);
            }, 200);
        },

        findRowForNote(note) {
            // 'tab' kind was folded into 'guide' when each CTG became its
            // own roadmap; treat both as guide-level (the General Tips
            // card on the current page).
            if (note.target_kind === 'tab' || note.target_kind === 'guide') {
                return document.querySelector('.general-tips-card');
            }
            if (note.target_kind === 'step') {
                return document.querySelector(
                    `.step-card[data-step-id="${note.target_step_id}"]`
                );
            }
            if (note.target_kind === 'trophy_guide') {
                // Each editor session is per-CTG so a trophy_id matches
                // at most one row on the page.
                return document.querySelector(
                    `.trophy-guide-row[data-trophy-id="${note.target_trophy_id}"]`
                );
            }
            return null;
        },

        flashNoteInList(listOrThreadEl, noteId, fallbackRowEl) {
            // Find the rendered note row inside the thread / drawer list,
            // then scroll into view and add a brief highlight ring.
            const noteRow = listOrThreadEl?.querySelector(
                `.note-row[data-note-id="${noteId}"]`
            ) || fallbackRowEl;
            if (!noteRow) return;
            noteRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
            const flashClasses = ['ring-2', 'ring-info', 'ring-offset-2', 'ring-offset-base-200'];
            noteRow.classList.add(...flashClasses);
            setTimeout(() => {
                noteRow.classList.remove(...flashClasses);
            }, 3000);
        },

        indexNotes() {
            this.byTarget.clear();
            this.notes.forEach(n => {
                let key;
                // Per-CTG roadmaps collapsed the 'tab' kind into 'guide' (a
                // tab IS its roadmap now). Old notes with kind='tab' are
                // wiped by the migration so we only encounter 'guide' /
                // 'step' / 'trophy_guide' going forward.
                if (n.target_kind === 'guide' || n.target_kind === 'tab') key = 'guide';
                else if (n.target_kind === 'step') key = `step:${n.target_step_id}`;
                // Trophy_guide notes are keyed by trophy_id alone now,
                // since each editor session is on a single CTG (no need
                // to disambiguate by tab anymore).
                else if (n.target_kind === 'trophy_guide')
                    key = `trophy_guide:${n.target_trophy_id}`;
                else return;
                if (!this.byTarget.has(key)) this.byTarget.set(key, []);
                this.byTarget.get(key).push(n);
            });
        },

        // Public — called by roadmap_editor.js when a new row is rendered
        // (e.g. after addStep). New rows have negative client ids; we still
        // mount the indicator (count will be 0) but the composer is disabled
        // until the section is persisted.
        mountIndicator(rowEl, kind, targetId) {
            if (!rowEl) return;
            // Dedup: skip if this row already carries an indicator with the
            // same target (renderSteps rebuilds rows so we may be called
            // multiple times across the session).
            const existing = rowEl.querySelector('.note-indicator-wrap');
            if (existing && existing.dataset.targetKind === kind
                && existing.dataset.targetId === String(targetId)) return;
            if (existing) existing.remove();
            const isUnsaved = typeof targetId === 'number' && targetId < 0;
            const key = isUnsaved ? null : this.targetKey(kind, targetId);
            const count = key ? (this.byTarget.get(key) || []).filter(n => n.status === 'open').length : 0;

            const wrap = document.createElement('span');
            wrap.dataset.targetKind = kind;
            wrap.dataset.targetId = String(targetId);
            // Notes are an always-on back-channel — keep the indicator
            // clickable even when the editor is read-only (lock conflict).
            wrap.setAttribute('data-readonly-exempt', '');
            // Per-kind placement: trophy_guide rows are <details>, so we
            // sit on the summary line next to the other badges. Steps and
            // tabs have a content column we can append below the inputs.
            const isInline = (kind === 'trophy_guide' || kind === 'tab');
            wrap.className = isInline
                ? 'note-indicator-wrap inline-flex items-center shrink-0'
                : 'note-indicator-wrap mt-1 flex items-center gap-2';
            wrap.innerHTML = `
                <button class="note-indicator-btn inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] border transition-colors"
                        type="button"
                        ${isUnsaved ? 'disabled' : ''}
                        title="${isUnsaved ? 'Save first to leave notes on this section' : 'Leave a note for the team'}">
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>
                    <span class="note-indicator-count">${count}</span>
                </button>`;
            this.applyIndicatorTone(wrap.querySelector('.note-indicator-btn'), count);
            wrap.querySelector('.note-indicator-btn').addEventListener('click', (e) => {
                // Trophy guide indicator lives inside <summary>, so a bare
                // click would also toggle the <details>. Stop both default
                // and propagation to keep the row's open/closed state.
                if (kind === 'trophy_guide') {
                    e.preventDefault();
                    e.stopPropagation();
                }
                if (isUnsaved) return;
                this.toggleThread(rowEl, kind, targetId);
            });

            // Mount slot per row kind.
            let slot;
            if (kind === 'trophy_guide') {
                slot = rowEl.querySelector('.collapse-title') || rowEl;
            } else if (kind === 'tab') {
                // Tab notes attach to the General Tips card; the indicator
                // lives in the title row alongside the tag chips.
                slot = rowEl.querySelector('.note-indicator-slot') || rowEl;
            } else {
                slot = rowEl.querySelector('.flex-1.min-w-0') || rowEl;
            }
            slot.appendChild(wrap);
        },

        applyIndicatorTone(btn, count) {
            btn.classList.remove(
                'border-base-content/10', 'text-base-content/40', 'bg-transparent',
                'border-info/40', 'text-info', 'bg-info/10',
            );
            if (count > 0) {
                btn.classList.add('border-info/40', 'text-info', 'bg-info/10');
            } else {
                btn.classList.add('border-base-content/10', 'text-base-content/40', 'bg-transparent');
            }
        },

        mountAllIndicators() {
            document.querySelectorAll('.step-card').forEach(row => {
                const id = parseInt(row.dataset.stepId, 10);
                if (!isNaN(id)) this.mountIndicator(row, 'step', id);
            });
            document.querySelectorAll('.trophy-guide-row').forEach(row => {
                const trophyId = parseInt(row.dataset.trophyId, 10);
                if (!isNaN(trophyId)) {
                    // Each editor session is per-CTG, so the trophy_id
                    // alone identifies the row. Server resolves to a
                    // TrophyGuide via get_or_create on (roadmap, trophy_id).
                    this.mountIndicator(row, 'trophy_guide', trophyId);
                }
            });
            // Tab-level notes are now roadmap-level ('guide'). One General
            // Tips card per page.
            document.querySelectorAll('.general-tips-card').forEach(row => {
                this.mountIndicator(row, 'tab', null);
            });
        },

        toggleThread(rowEl, kind, targetId) {
            const key = this.targetKey(kind, targetId);
            // The thread can live in any of three slots depending on kind;
            // a global selector keyed on `data-target-key` finds it
            // regardless of where it was mounted.
            const existing = (
                this.threadsByKey.get(key)
                || document.querySelector(`.note-thread[data-target-key="${key}"]`)
            );
            if (existing) {
                existing.remove();
                this.threadsByKey.delete(key);
                return;
            }
            const tpl = document.getElementById('note-thread-template');
            const thread = tpl.content.firstElementChild.cloneNode(true);
            thread.dataset.targetKey = key;
            // Per-kind thread placement. All kinds mount INSIDE their row's
            // content container so the thread visually belongs to the row.
            //   - step: inside the step-card's content column
            //   - trophy_guide: inside the <details>'s collapse-content;
            //     auto-open the details so the thread is visible
            //   - tab: inside the General Tips card's card-body
            // The default template styling (mt-2 pt-2 border-t) reads as a
            // divider between the row's existing content and the thread.
            if (kind === 'trophy_guide') {
                if (!rowEl.open) rowEl.open = true;
                const slot = rowEl.querySelector('.collapse-content') || rowEl;
                slot.appendChild(thread);
            } else if (kind === 'tab') {
                const slot = rowEl.querySelector('.card-body') || rowEl;
                slot.appendChild(thread);
            } else {
                const slot = rowEl.querySelector('.flex-1.min-w-0') || rowEl;
                slot.appendChild(thread);
            }
            this.threadsByKey.set(key, thread);
            this.renderThread(thread, kind, targetId);
        },

        renderThread(threadEl, kind, targetId) {
            const key = this.targetKey(kind, targetId);
            const list = threadEl.querySelector('.note-list');
            list.innerHTML = '';
            const notes = (this.byTarget.get(key) || []).slice().sort(
                (a, b) => new Date(a.created_at) - new Date(b.created_at)
            );
            if (notes.length === 0) {
                list.innerHTML = '<p class="text-[10px] text-base-content/40 italic">No notes yet — be the first.</p>';
            } else {
                notes.forEach(n => list.appendChild(this.renderNoteRow(n)));
            }
            const composer = threadEl.querySelector('.note-composer');
            const postBtn = threadEl.querySelector('.note-post-btn');
            postBtn.addEventListener('click', () => this.handlePost(kind, targetId, composer));
            composer.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault();
                    this.handlePost(kind, targetId, composer);
                }
            });
        },

        renderNoteRow(n) {
            const tpl = document.getElementById('note-row-template');
            const row = tpl.content.firstElementChild.cloneNode(true);
            row.dataset.noteId = String(n.id);

            // Avatar
            const avatar = row.querySelector('.note-avatar');
            const author = n.author;
            if (author && author.avatar_url) {
                avatar.innerHTML = `<img src="${escapeAttr(author.avatar_url)}" alt="" class="w-full h-full object-cover">`;
            } else if (author) {
                avatar.textContent = (author.display_name || author.username || '?').slice(0, 1).toUpperCase();
            } else {
                avatar.textContent = '?';
            }

            row.querySelector('.note-author').textContent = author
                ? (author.display_name || author.username)
                : '<deleted>';
            row.querySelector('.note-timestamp').textContent = formatRelativeTime(n.created_at);

            // Body — render @mentions as profile links, otherwise plain text
            row.querySelector('.note-body').innerHTML = renderBodyWithMentions(n.body);

            const resolvedPill = row.querySelector('.note-resolved-pill');
            if (n.is_resolved) {
                resolvedPill.classList.remove('hidden');
                resolvedPill.classList.add('inline-flex');
                row.classList.add('opacity-60');
            }

            const actions = row.querySelector('.note-actions');
            const resolveBtn = row.querySelector('.note-resolve-btn');
            const reopenBtn = row.querySelector('.note-reopen-btn');
            const editBtn = row.querySelector('.note-edit-btn');
            const deleteBtn = row.querySelector('.note-delete-btn');

            const anyAction = n.can_edit || n.can_delete || n.can_resolve;
            if (anyAction) {
                actions.classList.remove('hidden');
                actions.classList.add('flex');
            }

            // Resolve / reopen toggle
            if (n.can_resolve) {
                if (n.is_resolved) {
                    resolveBtn.classList.add('hidden');
                    reopenBtn.classList.remove('hidden');
                    reopenBtn.addEventListener('click', () => this.handleResolveToggle(n.id, false));
                } else {
                    resolveBtn.addEventListener('click', () => this.handleResolveToggle(n.id, true));
                }
            } else {
                resolveBtn.classList.add('hidden');
            }

            if (n.can_edit) {
                editBtn.classList.remove('hidden');
                editBtn.addEventListener('click', () => this.handleEdit(n.id, row));
            }
            if (n.can_delete) {
                deleteBtn.classList.remove('hidden');
                deleteBtn.addEventListener('click', () => this.handleDelete(n.id));
            }
            return row;
        },

        async handlePost(kind, targetId, composerEl) {
            const body = composerEl.value.trim();
            if (!body) {
                Toast.show('Write something before posting.', 'warning');
                return;
            }
            // Per-CTG roadmaps collapsed the 'tab' note kind into 'guide'
            // (a note on a tab IS a note on its roadmap now). Translate
            // to keep the existing UI handlers working unchanged.
            let serverKind = kind;
            if (kind === 'tab') {
                serverKind = 'guide';
            }
            const payload = { body, target_kind: serverKind };
            if (kind === 'step') payload.target_step_id = targetId;
            if (kind === 'trophy_guide') {
                // Composite targetId — split off the trophy_id (the tab
                // half is irrelevant now since roadmap === tab).
                const parts = String(targetId).split(':').map(Number);
                payload.target_trophy_id = parts.length > 1 ? parts[1] : parts[0];
            }
            try {
                const note = await API.post(`/api/v1/roadmap/${roadmapId}/notes/`, payload);
                this.notes.push(note);
                this.indexNotes();
                composerEl.value = '';
                this.refreshTarget(kind, targetId);
                this.refreshIndicator(kind, targetId);
                if (kind === 'guide') this.renderGeneralNotesPanel();
                Toast.show('Note posted.', 'success');
            } catch (err) {
                const errData = await err.response?.json().catch(() => null);
                Toast.show(errData?.error || 'Failed to post note.', 'error');
            }
        },

        async handleEdit(noteId, rowEl) {
            const note = this.notes.find(n => n.id === noteId);
            if (!note) return;
            const next = prompt('Edit your note:', note.body);
            if (next === null) return;
            const body = next.trim();
            if (!body) {
                Toast.show('Note body is required.', 'warning');
                return;
            }
            try {
                const updated = await API.patch(
                    `/api/v1/roadmap/${roadmapId}/notes/${noteId}/`, { body }
                );
                Object.assign(note, updated);
                this.indexNotes();
                this.refreshTarget(note.target_kind, note.target_step_id || note.target_trophy_guide_id);
                if (note.target_kind === 'guide') this.renderGeneralNotesPanel();
            } catch (err) {
                const errData = await err.response?.json().catch(() => null);
                Toast.show(errData?.error || 'Failed to edit note.', 'error');
            }
        },

        async handleDelete(noteId) {
            if (!confirm('Delete this note? This cannot be undone.')) return;
            const note = this.notes.find(n => n.id === noteId);
            if (!note) return;
            try {
                await API.delete(`/api/v1/roadmap/${roadmapId}/notes/${noteId}/`);
                this.notes = this.notes.filter(n => n.id !== noteId);
                this.indexNotes();
                this.refreshTarget(note.target_kind, noteTargetId(note));
                this.refreshIndicator(note.target_kind, noteTargetId(note));
                if (note.target_kind === 'guide') this.renderGeneralNotesPanel();
                Toast.show('Note deleted.', 'success');
            } catch (err) {
                const errData = await err.response?.json().catch(() => null);
                Toast.show(errData?.error || 'Failed to delete note.', 'error');
            }
        },

        async handleResolveToggle(noteId, resolved) {
            const note = this.notes.find(n => n.id === noteId);
            if (!note) return;
            try {
                const updated = await API.post(
                    `/api/v1/roadmap/${roadmapId}/notes/${noteId}/resolve/`, { resolved }
                );
                Object.assign(note, updated);
                this.indexNotes();
                this.refreshTarget(note.target_kind, noteTargetId(note));
                this.refreshIndicator(note.target_kind, noteTargetId(note));
                if (note.target_kind === 'guide') this.renderGeneralNotesPanel();
            } catch (err) {
                const errData = await err.response?.json().catch(() => null);
                Toast.show(errData?.error || 'Failed to update note.', 'error');
            }
        },

        refreshTarget(kind, targetId) {
            const key = this.targetKey(kind, targetId);
            const thread = this.threadsByKey.get(key);
            if (thread) this.renderThread(thread, kind, targetId);
        },

        refreshIndicator(kind, targetId) {
            const sel = `.note-indicator-wrap[data-target-kind="${kind}"][data-target-id="${targetId}"]`;
            document.querySelectorAll(sel).forEach(wrap => {
                const key = this.targetKey(kind, targetId);
                const count = (this.byTarget.get(key) || []).filter(n => n.status === 'open').length;
                wrap.querySelector('.note-indicator-count').textContent = String(count);
                this.applyIndicatorTone(wrap.querySelector('.note-indicator-btn'), count);
            });
        },

        // ----------------- General Notes drawer ----------------- //

        bindGlobalUI() {
            const openBtn = document.getElementById('general-notes-btn');
            const closeBtn = document.getElementById('general-notes-close');
            const headsUpBtn = document.getElementById('notes-heads-up-open');
            const postBtn = document.getElementById('general-notes-post-btn');
            const composer = document.getElementById('general-notes-composer');
            const showResolvedToggle = document.querySelector('.notes-show-resolved-toggle');

            if (openBtn) openBtn.addEventListener('click', () => this.openDrawer());
            if (closeBtn) closeBtn.addEventListener('click', () => this.closeDrawer());
            if (headsUpBtn) headsUpBtn.addEventListener('click', () => {
                this.openDrawer();
                this.dismissHeadsUp();
            });
            if (postBtn) postBtn.addEventListener('click', () => {
                this.handlePost('guide', null, composer);
            });
            if (composer) {
                composer.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                        e.preventDefault();
                        this.handlePost('guide', null, composer);
                    }
                });
            }
            if (showResolvedToggle) {
                showResolvedToggle.addEventListener('change', () => this.renderGeneralNotesPanel());
            }
            this.updateGeneralNotesBadge();
        },

        openDrawer() {
            const drawer = document.getElementById('general-notes-drawer');
            if (!drawer) return;
            drawer.classList.remove('hidden');
            drawer.classList.add('flex');
            this.renderGeneralNotesPanel();
        },

        closeDrawer() {
            const drawer = document.getElementById('general-notes-drawer');
            if (!drawer) return;
            drawer.classList.add('hidden');
            drawer.classList.remove('flex');
        },

        dismissHeadsUp() {
            const banner = document.getElementById('notes-heads-up');
            if (banner) banner.classList.add('hidden');
        },

        renderGeneralNotesPanel() {
            const list = document.getElementById('general-notes-list');
            if (!list) return;
            const showResolved = document.querySelector('.notes-show-resolved-toggle')?.checked;
            const notes = (this.byTarget.get('guide') || [])
                .filter(n => showResolved || !n.is_resolved)
                .slice()
                .sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
            list.innerHTML = '';
            if (notes.length === 0) {
                list.innerHTML = '<p class="text-xs text-base-content/40 italic text-center py-6">No general notes yet.</p>';
            } else {
                notes.forEach(n => list.appendChild(this.renderNoteRow(n)));
            }
            this.updateGeneralNotesBadge();
        },

        updateGeneralNotesBadge() {
            const badge = document.getElementById('general-notes-badge');
            if (!badge) return;
            const open = (this.byTarget.get('guide') || []).filter(n => n.status === 'open').length;
            if (open > 0) {
                badge.textContent = String(open);
                badge.classList.remove('hidden');
                badge.classList.add('inline-flex');
            } else {
                badge.classList.add('hidden');
                badge.classList.remove('inline-flex');
            }
        },

        async markRead() {
            if (this.markReadFired) return;
            this.markReadFired = true;
            try {
                await API.post(`/api/v1/roadmap/${roadmapId}/notes/mark-read/`, {});
            } catch (err) {
                // best-effort
            }
        },
    };

    // ------------------------------------------------------------------ //
    //  Helpers
    // ------------------------------------------------------------------ //

    function noteTargetId(note) {
        if (note.target_kind === 'step') return note.target_step_id;
        if (note.target_kind === 'trophy_guide') {
            // Trophy_guide notes are keyed by trophy_id alone now (each
            // editor session is per-CTG, no need to disambiguate).
            return note.target_trophy_id;
        }
        return null;
    }

    const MENTION_RE = /(^|[^A-Za-z0-9_\-/@])@([A-Za-z0-9_\-]{3,16})/g;

    function renderBodyWithMentions(body) {
        // First escape, then walk for mentions on the escaped string —
        // safer than the other order (escapeHtml after splitting could
        // double-escape pre-existing entities in the body).
        const escaped = escapeHtml(body || '');
        return escaped.replace(MENTION_RE, (match, prefix, handle) => {
            const lower = handle.toLowerCase();
            // We don't have a complete profile lookup in the JS side, so
            // unconditionally render the link — server has already
            // validated/notified for matched handles. Bad handles just
            // show as a 404 link if clicked. Better to be permissive than
            // to drop the visual cue.
            return `${prefix}<a href="/community/profiles/${encodeURIComponent(lower)}/" class="text-info hover:underline" target="_blank" rel="noopener">@${escapeHtml(handle)}</a>`;
        });
    }

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function escapeAttr(s) {
        return escapeHtml(s);
    }

    function formatRelativeTime(iso) {
        const d = new Date(iso);
        const diff = (Date.now() - d.getTime()) / 1000;
        if (diff < 60) return 'just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
        return d.toLocaleDateString();
    }

    // ------------------------------------------------------------------ //
    //  @-Mention Autocomplete
    //
    //  Single shared dropdown anchored under whichever note composer the
    //  user is typing in (.note-composer in inline section threads, or
    //  #general-notes-composer in the drawer). Pre-loaded with all writer+
    //  profiles so filtering is purely client-side — no debounced search,
    //  no server roundtrips. Sized for cultivated teams; if the author
    //  list grows past a few hundred we'd switch to a search endpoint.
    // ------------------------------------------------------------------ //

    const MENTIONABLE_AUTHORS = JSON.parse(
        document.getElementById('roadmap-mentionable-authors')?.textContent || '[]'
    );
    const COMPOSER_SELECTOR = '.note-composer, #general-notes-composer';
    const MAX_VISIBLE_CANDIDATES = 8;

    const MentionAutocomplete = {
        dropdownEl: null,
        activeComposer: null,
        tokenStart: -1,         // index in textarea value of the '@' char
        candidates: [],
        selectedIndex: 0,

        init() {
            if (!MENTIONABLE_AUTHORS.length) return;
            this.dropdownEl = this._buildDropdown();
            document.body.appendChild(this.dropdownEl);
            // Event delegation keeps things simple — composers come and go
            // as threads expand/collapse.
            document.addEventListener('input', (e) => this._onInput(e));
            document.addEventListener('keydown', (e) => this._onKeyDown(e), true);
            document.addEventListener('focusout', (e) => this._onFocusOut(e));
            window.addEventListener('resize', () => {
                if (this.activeComposer) this._reposition();
            });
            window.addEventListener('scroll', () => {
                if (this.activeComposer) this._reposition();
            }, true);
        },

        _buildDropdown() {
            const el = document.createElement('div');
            el.id = 'mention-autocomplete-dropdown';
            // Hidden by default; toggle 'flex' on show.
            el.className = 'hidden fixed z-50 max-h-64 overflow-y-auto rounded-lg border-2 border-base-300 bg-base-200 shadow-xl flex-col';
            el.style.minWidth = '240px';
            el.setAttribute('data-readonly-exempt', '');
            // Don't blur the textarea on mousedown inside the dropdown.
            el.addEventListener('mousedown', (e) => e.preventDefault());
            return el;
        },

        _onInput(e) {
            const composer = e.target.closest(COMPOSER_SELECTOR);
            if (!composer) {
                this._hide();
                return;
            }
            const detected = this._detectMention(composer);
            if (!detected) {
                this._hide();
                return;
            }
            this.activeComposer = composer;
            this.tokenStart = detected.tokenStart;
            this.candidates = this._filter(detected.prefix);
            this.selectedIndex = 0;
            this._render();
            // Show BEFORE measuring — `display: none` returns 0 for
            // scrollHeight/getBoundingClientRect, which broke the
            // flip-above logic for composers near the bottom of the
            // viewport (the General Notes drawer's composer).
            this._show();
            this._reposition();
        },

        _onKeyDown(e) {
            if (!this._isVisible()) return;
            if (!e.target.matches(COMPOSER_SELECTOR)) return;
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                this.selectedIndex = (this.selectedIndex + 1) % this.candidates.length;
                this._render();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                this.selectedIndex = (this.selectedIndex - 1 + this.candidates.length) % this.candidates.length;
                this._render();
            } else if (e.key === 'Enter' || e.key === 'Tab') {
                if (this.candidates.length === 0) return;
                e.preventDefault();
                e.stopPropagation();
                this._commit();
            } else if (e.key === 'Escape') {
                e.preventDefault();
                this._hide();
            }
        },

        _onFocusOut(e) {
            // Hide if focus leaves the composer AND isn't moving into the
            // dropdown itself.
            const next = e.relatedTarget;
            if (next && this.dropdownEl?.contains(next)) return;
            // Defer slightly — clicking a dropdown item triggers focusout
            // before the click registers.
            setTimeout(() => {
                if (document.activeElement?.matches(COMPOSER_SELECTOR)) return;
                this._hide();
            }, 100);
        },

        _detectMention(composer) {
            const val = composer.value;
            const caret = composer.selectionStart;
            const before = val.substring(0, caret);
            // Match an @ that's at start-of-input or preceded by a non-word
            // char, followed by 0-16 username chars up to caret. Same regex
            // shape as the server-side mention parser so what autocompletes
            // is also what fires a notification.
            const m = before.match(/(^|[^A-Za-z0-9_\-/@])@([A-Za-z0-9_\-]{0,16})$/);
            if (!m) return null;
            return {
                prefix: m[2],
                tokenStart: caret - m[2].length - 1,  // position of '@'
            };
        },

        _filter(prefix) {
            const lower = prefix.toLowerCase();
            if (!lower) return MENTIONABLE_AUTHORS.slice(0, MAX_VISIBLE_CANDIDATES * 3);
            // Split into prefix matches (rank higher) and substring matches
            // on display_name (rank lower). Username is canonical so prefix
            // takes priority.
            const prefixMatches = [];
            const subMatches = [];
            MENTIONABLE_AUTHORS.forEach(a => {
                if (a.username.toLowerCase().startsWith(lower)) {
                    prefixMatches.push(a);
                } else if ((a.display_name || '').toLowerCase().includes(lower)) {
                    subMatches.push(a);
                }
            });
            return prefixMatches.concat(subMatches).slice(0, MAX_VISIBLE_CANDIDATES * 3);
        },

        _render() {
            this.dropdownEl.innerHTML = '';
            if (this.candidates.length === 0) {
                const empty = document.createElement('div');
                empty.className = 'px-3 py-2 text-xs text-base-content/40 italic';
                empty.textContent = 'No matching authors.';
                this.dropdownEl.appendChild(empty);
                return;
            }
            this.candidates.forEach((author, idx) => {
                const item = document.createElement('button');
                item.type = 'button';
                item.dataset.username = author.username;
                const isSelected = idx === this.selectedIndex;
                item.className = (
                    'mention-item w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 '
                    + (isSelected ? 'bg-info/15' : 'hover:bg-base-300/50')
                );
                const initial = (author.display_name || author.username).slice(0, 1).toUpperCase();
                const avatar = author.avatar_url
                    ? `<img src="${escapeAttr(author.avatar_url)}" class="w-full h-full object-cover" alt="">`
                    : `<span class="text-[10px] font-bold">${escapeHtml(initial)}</span>`;
                item.innerHTML = `
                    <span class="w-5 h-5 rounded-full bg-base-300 overflow-hidden flex items-center justify-center shrink-0">
                        ${avatar}
                    </span>
                    <span class="flex-1 min-w-0 truncate">
                        <span class="font-medium">${escapeHtml(author.display_name || author.username)}</span>
                        <span class="text-base-content/40 ml-1">@${escapeHtml(author.username)}</span>
                    </span>
                    <span class="text-[10px] text-base-content/50 capitalize shrink-0">${escapeHtml(author.role)}</span>
                `;
                item.addEventListener('click', () => {
                    this.selectedIndex = idx;
                    this._commit();
                });
                this.dropdownEl.appendChild(item);
            });
        },

        _reposition() {
            if (!this.activeComposer || !this.dropdownEl) return;
            const rect = this.activeComposer.getBoundingClientRect();
            this.dropdownEl.style.left = `${Math.round(rect.left)}px`;
            this.dropdownEl.style.width = `${Math.round(Math.min(rect.width, 360))}px`;
            // Decide above vs. below. Flip above when:
            //   - composer's bottom is within ~280px of the viewport bottom
            //     (would clip the dropdown), OR
            //   - composer is the General Notes drawer textarea, which
            //     structurally always sits at the bottom of the drawer
            //     (forcing above feels more natural and never collides).
            const viewportH = window.innerHeight;
            const dropH = Math.min(this.dropdownEl.scrollHeight || 256, 256);
            const isDrawerComposer = this.activeComposer.id === 'general-notes-composer';
            const wouldClip = rect.bottom + 4 + dropH > viewportH;
            const flipAbove = isDrawerComposer || wouldClip;
            if (flipAbove && rect.top - 4 - dropH > 0) {
                this.dropdownEl.style.top = `${Math.round(rect.top - 4 - dropH)}px`;
            } else {
                this.dropdownEl.style.top = `${Math.round(rect.bottom + 4)}px`;
            }
        },

        _commit() {
            if (!this.activeComposer || this.candidates.length === 0) return;
            const author = this.candidates[this.selectedIndex];
            const composer = this.activeComposer;
            const before = composer.value.substring(0, this.tokenStart);
            const after = composer.value.substring(composer.selectionStart);
            const inserted = `@${author.username} `;
            composer.value = before + inserted + after;
            const newCaret = before.length + inserted.length;
            composer.setSelectionRange(newCaret, newCaret);
            // Fire input event so any listeners (like the heads-up dirty
            // tracker, if we ever add one) see the change.
            composer.dispatchEvent(new Event('input', { bubbles: true }));
            composer.focus();
            this._hide();
        },

        _show() {
            this.dropdownEl.classList.remove('hidden');
            this.dropdownEl.classList.add('flex');
        },
        _hide() {
            this.dropdownEl.classList.add('hidden');
            this.dropdownEl.classList.remove('flex');
            this.activeComposer = null;
            this.tokenStart = -1;
        },
        _isVisible() {
            return this.dropdownEl && !this.dropdownEl.classList.contains('hidden');
        },
    };

    // Expose the public mountIndicator hook for roadmap_editor.js to call
    // when it renders new rows during the session (e.g., addStep).
    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.RoadmapNotes = NotesController;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            NotesController.init();
            MentionAutocomplete.init();
        });
    } else {
        NotesController.init();
        MentionAutocomplete.init();
    }
})();
