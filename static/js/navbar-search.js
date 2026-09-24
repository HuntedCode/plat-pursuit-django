/**
 * Navbar universal search.
 *
 * A persistent typeahead bar (md+) that collapses to an icon trigger below md.
 * Two behaviours share one #navbar-sync-form:
 *   1. Typeahead over the catalog (GET site_suggest, debounced) -> a dropdown of
 *      matches GROUPED by type (Games / Badges / Franchises / Hunters), each row
 *      an anchor to its detail page.
 *   2. Add-and-sync fallback for an Online ID that isn't tracked yet: the form
 *      posts to search_sync_profile, then polls add_sync_status until the new
 *      profile finishes basic ingestion (-> Visit link) or errors. Gated purely
 *      on the query matching PSN_RE, so it coexists with entity matches.
 *
 * THE WAIT OUTLIVES THE PANEL. A first sync pulls an entire trophy history through
 * rate-limited workers, so it is measured in minutes, not the seconds a dropdown
 * stays open. Cancelling that wait and repainting it were one function once
 * (`resetAddSync`), and every caller of it inherited the cancel:
 *
 *   - `closePanel` called it, so an outside click or Escape mid-sync silently threw
 *     the wait away, with no way back but re-submitting.
 *   - `renderSuggestions` called it, and the success handler's own `input.focus()`
 *     re-fired the typeahead (focus had been on the just-clicked submit button), whose
 *     response landed ~300ms into the poll's 2500ms start delay and cancelled it. So
 *     CLICKING "Sync X" posted the form and then restored the panel to exactly its
 *     pre-click state: no spinner, no poll, no link. The sync ran server-side and the
 *     hunter was never told. That is the bug this file was rewritten for.
 *
 * Now `abandonAddSync` (deliberate, and ONLY two callers: Clear, and a fresh submit) and
 * `paintAddSync` (idempotent, any render) are separate, one `addSync` object holds the
 * whole wait, and the status line is staged honestly the way the anon landing hero
 * stages the same flow in `landing.js`.
 *
 * Rows are built with DOM APIs (not innerHTML interpolation) so catalog- and
 * PSN-sourced labels and image URLs can never inject markup.
 */
document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('navbar-search');
    const form = document.getElementById('navbar-sync-form');
    if (!root || !form) return;

    const suggestUrl = form.dataset.urlSuggest;
    const addSyncStatusUrl = form.dataset.urlAddSync;
    const input = form.querySelector('.add-sync-input');
    const panel = document.getElementById('navbar-search-results');
    const list = form.querySelector('[data-search-list]');
    const addBtn = form.querySelector('[data-search-add]');
    const addTerm = form.querySelector('[data-search-term]');
    const visitAnchor = form.querySelector('.add-sync-anchor');
    const spinner = form.querySelector('.add-sync-load');
    const clearBtn = form.querySelector('[data-search-clear]');
    const statusText = document.getElementById('add-sync-status-text');
    const liveRegion = form.querySelector('[data-search-live]');
    const trigger = document.getElementById('navbar-sync-search-btn');

    // PSN Online IDs are 3-16 chars of [a-zA-Z0-9_-]; only offer the add-and-sync
    // fallback when the query could actually be one.
    const PSN_RE = /^[a-zA-Z0-9_-]{3,16}$/;

    const POLL_MS = 2500;
    const POLL_CAP = 120;   // 5 minutes, then rest with an honest line (landing.js's cap)

    // The server's own refusal, or `fallback`. Guarded rather than called directly because a browser
    // holding a cached pre-change `utils.js` against a fresh copy of this file would throw
    // "failureOr is not a function" from inside a rejection handler -- an unhandled rejection that
    // strands the panel on "Looking up X..." with the spinner running and no timer left to move it.
    // Reporting a failure must not be able to fail worse than the failure.
    function refusal(err, fallback) {
        const api = window.PlatPursuit && PlatPursuit.API;
        return api && api.failureOr ? api.failureOr(err, fallback) : Promise.resolve(fallback);
    }

    // ONE object for the whole wait, not a scatter of timer flags: now that the wait survives the
    // panel closing, "is a sync in flight, for whom, and how far along" has to be readable from any
    // render rather than inferred from which timers happen to still be alive.
    //   { query, name, phase, message, href, polls, startTimer, pollTimer }
    //   query : the name as the hunter typed it (matched against the input, and shown in copy)
    //   name  : the server's canonical spelling, used for the status lookup
    //   phase : waiting | syncing | ready | error | rest
    let addSync = null;

    let items = [];        // navigable elements currently in the panel
    let activeIndex = -1;  // keyboard-highlighted item

    const show = (el) => el && el.classList.remove('hidden');
    const hide = (el) => el && el.classList.add('hidden');

    // ---- Panel open / close + keyboard cursor ----
    function openPanel() {
        panel.hidden = false;
        input.setAttribute('aria-expanded', 'true');
    }
    function closePanel() {
        panel.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
        // Drop the cursor class BEFORE emptying `items`. `refreshItems` reconciles against the element
        // at the old `activeIndex`, so clearing the index first leaves nothing tracking the highlight,
        // and `addBtn` / `visitAnchor` survive re-renders -- arrow onto the Visit row, press Escape,
        // and it stayed classed, ready to show up highlighted alongside a real cursor later.
        items.forEach((el) => el.classList.remove('is-active'));
        activeIndex = -1;
        items = [];
        // Deliberately does NOT touch `addSync`. Dismissing a dropdown is not a decision to
        // abandon a multi-minute sync; the poll keeps running, the field spinner keeps saying so,
        // and re-focusing the input repaints the status (and the Visit link) straight back.
    }
    function refreshItems() {
        const prev = items[activeIndex] || null;   // the element the cursor was actually on
        items = Array.from(panel.querySelectorAll(
            '.pp-navsearch__opt, [data-search-add]:not([hidden]), .add-sync-anchor:not(.hidden)'
        ));
        // Rows come and go mid-flight now: the add button hides on submit, the Visit row appears when
        // the sync lands. An element that leaves `items` while still wearing `.is-active` keeps that
        // highlight forever, because `setActive` only ever toggles elements it can still see -- so
        // the add row would come back pre-highlighted, competing with the real cursor. Re-derive the
        // index from the ELEMENT rather than trusting a position the rebuild may have invalidated.
        const at = prev ? items.indexOf(prev) : -1;
        if (at >= 0) {
            activeIndex = at;
        } else {
            if (prev) { prev.classList.remove('is-active'); }
            activeIndex = -1;
            // The attribute has to go with the cursor. It was written only by `setActive` and cleared
            // only by `closePanel`, so a row leaving `items` while the panel stayed OPEN left the
            // combobox pointing at a hidden element (arrow onto the add row, press Enter: the keydown
            // handler only intercepts anchors, so it submits, which hides that row) or at a removed one.
            input.removeAttribute('aria-activedescendant');
        }
    }
    function setActive(i) {
        if (!items.length) { activeIndex = -1; return; }
        activeIndex = (i + items.length) % items.length;
        items.forEach((el, idx) => el.classList.toggle('is-active', idx === activeIndex));
        const el = items[activeIndex];
        if (el) {
            el.scrollIntoView({ block: 'nearest' });
            if (el.id) input.setAttribute('aria-activedescendant', el.id);
        }
    }

    // ---- Add-and-sync: cancelling and repainting, kept apart ----

    // Stop the timers but KEEP the state, so the resolved status stays on screen.
    function stopPolling() {
        if (!addSync) return;
        if (addSync.startTimer) { clearTimeout(addSync.startTimer); addSync.startTimer = null; }
        if (addSync.pollTimer) { clearInterval(addSync.pollTimer); addSync.pollTimer = null; }
    }

    // Throw the wait away. Exactly TWO callers: Clear, and a fresh submit superseding the last one.
    // Typing a different name does NOT abandon -- that was the round-1 behaviour, and it destroyed a
    // sync in progress the moment the bar was used to look up a game. Nothing incidental (closing the
    // panel, a typeahead refetch, regaining focus) may call it either, which is the whole correction.
    // `test_only_two_deliberate_acts_abandon_a_sync` pins the count.
    function abandonAddSync() {
        if (!addSync) return;
        stopPolling();
        addSync = null;
        paintAddSync();   // paints the resting bar AND clears the announcer, since it reads state
    }

    function setAddSync(phase, message, href) {
        if (!addSync) return;
        addSync.phase = phase;
        addSync.message = message;
        if (href !== undefined) { addSync.href = href; }
        paintAddSync();
    }

    // What the live region should say, derived from state rather than from a transition.
    //
    // Gated on `inView()`, not `statusVisible()`: the two differ exactly when the field still holds
    // the name but the panel is SHUT, which is the commonest way to wait out a first sync. Gating on
    // the wrong one meant a sync landing after an Escape announced the on-screen sentence ("Found X.
    // Trophies are still arriving...") while the Visit link it refers to was invisible -- so the one
    // sentence naming the way back reached nobody.
    //
    // Out of view, only a TERMINAL stage is worth interrupting for, and it gets NAMED: `COPY.queued`,
    // `COPY.missing` and `COPY.rest` name no hunter, and out of context they describe a state the
    // screen flatly contradicts. '' clears a stale line.
    //
    // This is the only channel that reaches a screen reader at all: `ToastManager` appends a plain div
    // and `#toast-container` has no `aria-live`.
    function announcementFor() {
        if (!addSync || !addSync.message) { return ''; }
        if (inView()) { return addSync.message; }
        if (addSync.phase === 'ready') { return COPY.readyAway(addSync.query); }
        if (addSync.phase === 'error' || addSync.phase === 'rest') { return addSync.query + ': ' + addSync.message; }
        return '';
    }

    // Is the field still showing the name we are syncing? RENDERING is gated on this; the WAIT is
    // not. Using the same bar to look up a game must not throw a sync away (it did, silently -- the
    // same "nobody was told" failure as the original bug, just reached by typing instead of clicking
    // away), but it must not stamp that sync's status over unrelated results either.
    function statusVisible() {
        return !!addSync && !!addSync.message && isLiveQuery(input.value.trim());
    }

    // Is any of it actually on screen? `statusVisible()` plus an open panel. What a hunter can SEE
    // decides whether a result needs announcing and toasting, or merely painting.
    function inView() {
        return statusVisible() && !panel.hidden;
    }

    // Paint `addSync` into the panel. Idempotent on purpose: a poll tick, a typeahead refetch and
    // reopening the bar all call it and all land on the same UI.
    function paintAddSync() {
        const showing = statusVisible();
        const busy = showing && (addSync.phase === 'waiting' || addSync.phase === 'syncing');
        if (busy) { show(spinner); } else { hide(spinner); }

        if (!showing) {
            hide(statusText);
            if (statusText) { statusText.textContent = ''; }
        } else if (statusText) {
            statusText.textContent = addSync.message;
            statusText.classList.toggle('pp-navsearch__msg--err', addSync.phase === 'error');
            statusText.classList.toggle('pp-navsearch__msg--ok', addSync.phase === 'ready');
            show(statusText);
        }

        if (showing && addSync.phase === 'ready' && addSync.href) {
            if (visitAnchor) { visitAnchor.href = addSync.href; show(visitAnchor); }
        } else {
            hide(visitAnchor);
            if (visitAnchor) { visitAnchor.href = ''; }
        }

        paintAddRow();
        refreshItems();
        announce(announcementFor());   // state in, one sentence out; no-ops when nothing changed
    }

    // Announce out of band. The visible line lives inside `role="listbox"`, where a <p> is not a
    // permitted child and the container is `hidden` half the time, so it cannot be the live region.
    //
    // Writes ONLY on a real change. `paintAddSync` calls this on every poll tick, and re-assigning an
    // identical string still replaces the text node, which some AT treats as a fresh announcement --
    // "Syncing X" every 2.5 seconds for five minutes.
    function announce(message) {
        const next = message || '';
        if (liveRegion && liveRegion.textContent !== next) { liveRegion.textContent = next; }
    }

    // Put the keyboard cursor on the Visit row, so Enter goes straight to the profile.
    //
    // `onlyIfIdle` for the async caller: a sync resolving while the hunter is arrowing through
    // suggestions would otherwise move the cursor under them and make their next Enter navigate to
    // the profile instead of the row they were looking at (setActive also scrolls). A cursor the user
    // is driving is theirs; an unset one is free.
    function focusVisitRow(onlyIfIdle) {
        if (onlyIfIdle && activeIndex !== -1) { return; }
        const i = items.indexOf(visitAnchor);
        if (i >= 0) { setActive(i); }
    }

    // ---- Typeahead rendering ----
    // Per-type fallback glyph, shown when a row has no image (badges/franchises always;
    // games/profiles when their art/avatar is missing).
    const GLYPHS = {
        profile: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
        game: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 12h4M8 10v4"/><circle cx="16" cy="11" r="1"/><circle cx="18" cy="14" r="1"/>',
        badge: '<circle cx="12" cy="8" r="6"/><path d="M8.2 13.5 7 22l5-3 5 3-1.2-8.5"/>',
        franchise: '<path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5"/><path d="m3 17 9 5 9-5"/>',
    };
    function glyphPlaceholder(type) {
        const span = document.createElement('span');
        span.className = 'pp-navsearch__ph';
        span.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            + (GLYPHS[type] || GLYPHS.profile) + '</svg>';
        return span;
    }
    function thumb(url) {
        const img = document.createElement('img');
        img.src = url;                 // property assignment: no markup injection
        img.alt = '';
        img.loading = 'lazy';
        img.decoding = 'async';
        return img;
    }
    // One row per suggestion, `i` a running index unique across ALL groups so
    // aria-activedescendant ids stay unique. Every row is an <a> (Enter navigates).
    function buildRow(item, type, i) {
        const a = document.createElement('a');
        a.className = 'pp-navsearch__opt pp-navsearch__opt--' + type;
        a.href = item.url;
        a.id = 'navsearch-opt-' + i;
        a.setAttribute('role', 'option');

        // Leading media: real artwork when we have it (game/franchise cover, badge medallion,
        // hunter avatar), else the per-type glyph.
        const art = item.image || (type === 'profile' ? item.avatar_url : '');
        if (art) { a.appendChild(thumb(art)); }
        else { a.appendChild(glyphPlaceholder(type)); }

        const name = document.createElement('b');
        name.textContent = item.label;          // textContent: safe
        a.appendChild(name);

        // Trailing metadata: platinum count for hunters, a type tag for franchises.
        if (type === 'profile') {
            const plats = document.createElement('span');
            plats.className = 'pp-navsearch__plats';
            plats.textContent = Number(item.plats || 0).toLocaleString();
            plats.title = 'Platinums';
            a.appendChild(plats);
        } else if (item.sublabel) {
            const sub = document.createElement('span');
            sub.className = 'pp-navsearch__sub';
            sub.textContent = item.sublabel;
            a.appendChild(sub);
        }
        return a;
    }
    function renderSuggestions(data, q) {
        list.textContent = '';
        let i = 0;
        (data.groups || []).forEach((group) => {
            if (!group.items || !group.items.length) return;
            const header = document.createElement('div');
            header.className = 'pp-navsearch__group';
            header.setAttribute('role', 'presentation');   // not a navigable option
            header.textContent = group.label;
            list.appendChild(header);
            group.items.forEach((item) => list.appendChild(buildRow(item, group.type, i++)));
        });

        // The add row and the status line are both painted by `paintAddSync` (which also refreshes
        // `items`), so there is nothing to set here: the status line and Visit row survive a refetch,
        // where they used to be wiped.
        paintAddSync();
        // Only open when there's something to show (a suggestion, the add row, or a live sync's
        // status); a 2-char query with no matches and no PSN fallback would otherwise flash an
        // empty panel. The status clause matters: with the add row suppressed mid-sync, `items` can
        // be empty while there is very much something to say. No `focusVisitRow` in that branch --
        // `items` is empty there by definition, so the anchor could never be found.
        if (items.length) { openPanel(); setActive(0); }
        else if (statusVisible()) { openPanel(); }
        else { closePanel(); }
    }

    let suggestAbort = null;   // cancels the superseded request when a new keystroke lands
    const fetchSuggest = PlatPursuit.debounce((q) => {
        if (!suggestUrl) return;
        if (suggestAbort) suggestAbort.abort();
        suggestAbort = new AbortController();
        PlatPursuit.API.get(`${suggestUrl}?q=${encodeURIComponent(q)}`, { signal: suggestAbort.signal })
            .then((data) => {
                if (input.value.trim() !== q) return;   // stale: input moved on
                renderSuggestions(data, q);
            })
            .catch((err) => {
                if (err && err.name === 'AbortError') return;   // superseded: ignore
                // Network hiccup / throttle: keep the field usable. If the name is
                // a valid PSN id, still offer the add-and-sync fallback.
                if (input.value.trim() !== q) return;
                renderSuggestions({ groups: [] }, q);
            });
    }, 200);

    // Is `q` the name we are currently syncing? Case-insensitive, because the server stores a new
    // profile lowercased and the status endpoint matches `__iexact`.
    function isLiveQuery(q) {
        return !!addSync && addSync.query.toLowerCase() === String(q).toLowerCase();
    }

    // Does the live sync own the add row for `q`, i.e. should "Sync X, a new hunter" stay hidden?
    //
    // Every phase but `error`. Offering to sync a hunter the panel is reporting on is the dropdown
    // arguing with itself; once the name has FAILED, that row IS the retry, and suppressing it left
    // the copy telling a hunter to check the spelling and search again while hiding the only control
    // that would have done it. `rest` (still syncing after 5 minutes) keeps it hidden on purpose: the
    // profile demonstrably exists by then, so a row reading "a new hunter" would be a lie, and its
    // copy already points at searching the name again -- which surfaces the real hunter row.
    function addSyncOwnsRow(q) {
        return isLiveQuery(q) && addSync.phase !== 'error';
    }

    // The add row, computed in ONE place so it cannot be set from two with a one-way override.
    function paintAddRow() {
        if (!addBtn) return;
        const q = input.value.trim();
        if (addTerm) { addTerm.textContent = q; }
        addBtn.hidden = !PSN_RE.test(q) || addSyncOwnsRow(q);
    }

    function onInput() {
        const q = input.value.trim();
        clearBtn.classList.toggle('hidden', !q);
        // NOTHING here abandons the wait. Typing a game name into the same bar used to destroy a sync
        // in progress with no toast and no trace; the sync now keeps running unwatched and reports by
        // toast, and `paintAddSync` simply declines to render it over unrelated results.
        paintAddSync();
        if (q.length < 2) { closePanel(); return; }
        fetchSuggest(q);
    }
    input.addEventListener('input', onInput);
    input.addEventListener('focus', () => {
        const q = input.value.trim();
        // Re-focusing DURING or AFTER a sync for this same name reopens the status instead of
        // re-running the typeahead over it. This is the way back in after the panel was dismissed
        // mid-wait, and (before the cancel/repaint split) the exact path that cancelled the sync.
        if (statusVisible()) { paintAddSync(); openPanel(); focusVisitRow(false); return; }
        if (q.length >= 2) { fetchSuggest(q); }
    });

    // ---- Keyboard navigation ----
    input.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') {
            if (panel.hidden) { onInput(); } else { setActive(activeIndex + 1); }
            e.preventDefault();
        } else if (e.key === 'ArrowUp') {
            if (!panel.hidden) { setActive(activeIndex - 1); e.preventDefault(); }
        } else if (e.key === 'Escape') {
            if (!panel.hidden) { closePanel(); e.preventDefault(); }
            else if (root.classList.contains('is-open')) { closeSheet(); }
        } else if (e.key === 'Enter') {
            const el = items[activeIndex];
            if (el && el.tagName === 'A') {
                // A highlighted link (a profile suggestion or the Visit anchor): navigate,
                // don't let the form submit and re-fire the add-and-sync.
                window.location.href = el.href;
                e.preventDefault();
            }
            // Otherwise fall through: the native submit fires the add-and-sync (below).
        }
    });

    // ---- Add-and-sync fallback (unknown name) ----

    // Copy is staged the way `landing.js` stages the same flow, and for the same reason: a first
    // sync is an entire history through rate-limited workers. Saying so turns the wait from a
    // broken control into a promise.
    //
    // DELIBERATELY SHORTER THAN LANDING'S. The anon hero's box is ~520px with 52ch body copy and the
    // explanation IS the pitch to a stranger; this is an 0.8rem line in a 351px panel (375px screen,
    // the sheet's 12px insets) shown to somebody already inside the app, where the house register is
    // "List deleted." The first drafts were landing's sentences verbatim and `missing` ran to three
    // lines. Where these read differently from `landing.js`, that is the reason -- not drift.
    const COPY = {
        looking: (name) => 'Looking up ' + name + '...',
        // "a few minutes", not landing's "give it a moment": this file's own header says minutes, and
        // COPY.rest says "a few minutes" five minutes later. Two stages must not promise different waits.
        queued: 'In the queue. A first sync takes a few minutes.',
        syncing: (name) => 'Syncing ' + name + '. Trophies are arriving now.',
        // The tail clause stays. It is the one charming beat in these strings, and this is the payoff.
        ready: (name) => 'Found ' + name + '. Trophies are still arriving, so the numbers keep climbing.',
        missing: "We couldn't find that name on PSN. It may be misspelled, or fully private.",
        rest: 'Still syncing. Check back in a few minutes.',
        // Used BOTH for the toast and for the live region when the sync lands out of view, so the
        // sentence naming the way back cannot drift between the two.
        readyAway: (name) => name + ' is ready. Search the name again to open the profile.',
    };

    function pollAddSync() {
        if (!addSync || !addSyncStatusUrl) return;
        addSync.polls += 1;
        // A dropdown must never poll forever. Without this, a sync suppressed server-side (an outage
        // makes initial_sync a no-op while the view still reports success) left a tab ticking every
        // 2.5s for as long as it stayed open, against a profile whose status would never move.
        if (addSync.polls > POLL_CAP) {
            stopPolling();
            const seen = inView();
            setAddSync('rest', COPY.rest);
            if (!seen && window.PlatPursuit && PlatPursuit.ToastManager) {
                PlatPursuit.ToastManager.info(addSync.query + ': ' + COPY.rest);
            }
            return;
        }
        // Capture the state OBJECT, not the name: `isLiveQuery` would still pass after a second
        // submit for the same hunter, letting a superseded request write into the new wait.
        const mine = addSync;
        PlatPursuit.API.get(`${addSyncStatusUrl}?psn_username=${encodeURIComponent(addSync.name)}`)
            .then((data) => {
                if (addSync !== mine) return;   // abandoned or superseded while in flight
                // A LINKABLE profile wins over a failed status, and is therefore tested first.
                // `sync_status` is a persisted field that stays 'error' after any failed sync, so
                // checking it first told a hunter that a tracked friend "couldn't be found on PSN"
                // and actively hid the link to the profile sitting right there.
                if (data.account_id && data.slug) {
                    stopPolling();
                    const seen = inView();
                    setAddSync('ready', COPY.ready(addSync.query), data.slug);
                    if (seen) {
                        focusVisitRow(true);   // never steal a cursor the hunter is driving
                    } else if (window.PlatPursuit && PlatPursuit.ToastManager) {
                        // Resolved out of view: the panel was dismissed, or the bar has moved on to
                        // another query. Both are now allowed, and a Visit link revealed where nobody
                        // is looking tells nobody anything, so say it out loud and name the way back.
                        PlatPursuit.ToastManager.success(COPY.readyAway(addSync.query));
                    }
                    return;
                }
                if (data.sync_status === 'error') {
                    stopPolling();
                    const seen = inView();
                    setAddSync('error', COPY.missing);
                    // A failure out of view needs a sighted channel too. Only `ready` toasted, so a
                    // hunter who looked away was told nothing at all while the live region told a
                    // screen-reader user everything.
                    if (!seen && window.PlatPursuit && PlatPursuit.ToastManager) {
                        PlatPursuit.ToastManager.error(addSync.query + ': ' + COPY.missing);
                    }
                    return;
                }
                // Queued -> actually running. Worth its own line: it is the first evidence that
                // something on the far side of the button moved.
                if (data.sync_status === 'syncing' && addSync.phase !== 'syncing') {
                    setAddSync('syncing', COPY.syncing(addSync.query));
                }
            })
            .catch((error) => console.error('Add sync polling error:', error));   // transient; next tick retries
    }

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const q = input.value.trim();
        if (!PSN_RE.test(q)) { input.focus(); return; }

        // Already RESOLVED for this name: Enter means "go", not "sync again". The cursor is not
        // necessarily on the Visit row, since `focusVisitRow(true)` declines to steal a driven one, and
        // the keydown handler only intercepts anchors -- so this would otherwise abandon the resolved
        // state and re-POST, replacing the link with "Looking up X..." and spending one of the
        // three-a-minute anon budget re-syncing a hunter already found.
        if (addSync && isLiveQuery(q) && addSync.phase === 'ready' && addSync.href) {
            window.location.href = addSync.href;
            return;
        }

        // Already IN FLIGHT for this name: show the wait again rather than spending a second request.
        // Reachable by pressing Enter twice, because after a submit the list is empty and the Enter
        // handler finds no row to navigate to, so it falls through to a native submit.
        //
        // Deliberately `waiting`/`syncing` only, not `addSyncOwnsRow`: that also covers `rest`, and
        // catching `rest` here made the five-minute "check back" line a dead end. Enter did nothing,
        // refocus did nothing, the add row is hidden for `rest`, and the list was emptied on submit --
        // one sentence, forever, escapable only by Clear. `error` and `rest` must fall through to a
        // fresh sync, which is what their copy invites.
        if (addSync && isLiveQuery(q) && (addSync.phase === 'waiting' || addSync.phase === 'syncing')) {
            paintAddSync(); openPanel(); input.focus();
            return;
        }

        abandonAddSync();   // a second submit supersedes the first
        addSync = { query: q, name: q, phase: 'waiting', message: '', href: '', polls: 0, startTimer: null, pollTimer: null };
        const mine = addSync;   // see the poll's note: identity, not the name, is what stays true

        list.textContent = '';
        // Paints the add row shut on its way through, via the single writer.
        setAddSync('waiting', COPY.looking(q));
        openPanel();
        // Focus the input NOW, not on success: the button that was just clicked is hidden on the
        // line above, and focus on a hidden element falls to <body>, dropping the keyboard user out
        // of the search entirely. Doing it here is also why it is safe -- the focus listener sees a
        // live sync for this name and repaints the status instead of re-running the typeahead.
        input.focus();

        PlatPursuit.API.postFormData(form.action, new FormData(form))
            .then((data) => {
                if (addSync !== mine) return;   // abandoned or superseded mid-request
                if (!data || !data.success) {
                    setAddSync('error', (data && data.error) || "We couldn't start that sync. Check the name and try again.");
                    return;
                }
                addSync.name = data.psn_username || q;   // the server's canonical spelling
                setAddSync('waiting', COPY.queued);
                // First status lands a couple of seconds after ingestion kicks off.
                addSync.startTimer = setTimeout(() => {
                    if (addSync !== mine) return;
                    addSync.startTimer = null;
                    addSync.pollTimer = setInterval(pollAddSync, POLL_MS);
                    pollAddSync();
                }, POLL_MS);
            })
            .catch(async (error) => {
                console.error('Sync form error:', error);
                if (addSync !== mine) return;
                // The server's OWN refusal. `API.request` throws on any non-ok, so the 429 ("Too
                // many searches. Please wait a minute and try again.") and the 503 PSN-outage line
                // used to be parsed by nobody and replaced with a generic failure -- the anon cap is
                // 3/min, so the message a hunter hit most often was the one always discarded.
                //
                // Read the body FIRST, then re-check identity. Reading it yields, and the guard above
                // is on the wrong side of that await on its own: a submit for a different name landing
                // during the read would have this refusal stamped into ITS wait.
                const refused = await refusal(error, "We couldn't start that sync. Try again in a moment.");
                if (addSync !== mine) return;
                setAddSync('error', refused);
            });
    });

    // ---- Clear ----
    clearBtn.addEventListener('click', () => {
        input.value = '';
        hide(clearBtn);
        abandonAddSync();   // an explicit act: this one really does mean "forget it"
        closePanel();
        input.focus();
    });

    // ---- Mobile sheet toggle ----
    function openSheet() {
        root.classList.add('is-open');
        if (trigger) trigger.setAttribute('aria-expanded', 'true');
        // Wait a frame so the now-visible input can receive focus.
        requestAnimationFrame(() => input.focus());
    }
    function closeSheet() {
        root.classList.remove('is-open');
        if (trigger) trigger.setAttribute('aria-expanded', 'false');
        closePanel();
    }
    if (trigger) {
        trigger.addEventListener('click', () => {
            if (root.classList.contains('is-open')) closeSheet(); else openSheet();
        });
    }

    // ---- Dismiss on outside click ----
    document.addEventListener('click', (e) => {
        if (root.contains(e.target)) return;
        closePanel();
        if (root.classList.contains('is-open')) closeSheet();
    });

    window.addEventListener('beforeunload', stopPolling);
});
