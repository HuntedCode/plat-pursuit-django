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
    const errorText = document.getElementById('add-sync-error-text');
    const trigger = document.getElementById('navbar-sync-search-btn');

    // PSN Online IDs are 3-16 chars of [a-zA-Z0-9_-]; only offer the add-and-sync
    // fallback when the query could actually be one.
    const PSN_RE = /^[a-zA-Z0-9_-]{3,16}$/;

    let pollInterval = null;
    let addStartTimer = null;  // delay before the add-and-sync poll begins
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
        activeIndex = -1;
        items = [];
        resetAddSync();   // tear down any in-flight add-and-sync when the results dismiss
    }
    function refreshItems() {
        items = Array.from(panel.querySelectorAll(
            '.pp-navsearch__opt, [data-search-add]:not([hidden]), .add-sync-anchor:not(.hidden)'
        ));
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

    // ---- Reset any in-flight add-and-sync UI back to the resting bar ----
    function resetAddSync() {
        if (addStartTimer) { clearTimeout(addStartTimer); addStartTimer = null; }
        if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
        hide(spinner);
        hide(visitAnchor);
        if (visitAnchor) visitAnchor.href = '';
        hide(errorText);
        if (errorText) errorText.textContent = '';
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

        // Leading media: avatar (profile) or cover (game) when present, else a type glyph.
        if (type === 'profile' && item.avatar_url) { a.appendChild(thumb(item.avatar_url)); }
        else if (type === 'game' && item.image) { a.appendChild(thumb(item.image)); }
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
        resetAddSync();
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

        if (addTerm) addTerm.textContent = q;
        addBtn.hidden = !PSN_RE.test(q);

        refreshItems();
        // Only open when there's something to show (a suggestion or the add row); a 2-char
        // query with no matches and no PSN fallback would otherwise flash an empty panel.
        if (items.length) { openPanel(); setActive(0); }
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

    function onInput() {
        const q = input.value.trim();
        clearBtn.classList.toggle('hidden', !q);
        resetAddSync();
        if (q.length < 2) { closePanel(); return; }
        fetchSuggest(q);
    }
    input.addEventListener('input', onInput);
    input.addEventListener('focus', () => { if (input.value.trim().length >= 2) fetchSuggest(input.value.trim()); });

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
    function pollAddSync(psn_username) {
        if (!psn_username || !addSyncStatusUrl) return;
        PlatPursuit.API.get(`${addSyncStatusUrl}?psn_username=${encodeURIComponent(psn_username)}`)
            .then((data) => {
                if (data.sync_status === 'error') {
                    hide(spinner);
                    if (errorText) {
                        errorText.textContent = 'Sync error: check the spelling and account permissions, then try again.';
                        show(errorText);
                    }
                    input.focus();
                    if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
                } else if (data.account_id) {
                    hide(spinner);
                    if (visitAnchor) { visitAnchor.href = data.slug; show(visitAnchor); refreshItems(); }
                    if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
                }
            })
            .catch((error) => console.error('Add sync polling error:', error));
    }

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const q = input.value.trim();
        if (!PSN_RE.test(q)) { input.focus(); return; }

        hide(errorText);
        addBtn.hidden = true;
        list.textContent = '';
        show(spinner);
        openPanel();

        const formData = new FormData(form);
        PlatPursuit.API.postFormData(form.action, formData)
            .then((data) => {
                if (data.success) {
                    input.focus();
                    // First status lands a couple seconds after ingestion kicks off. Track the
                    // timer so resetAddSync (clear / retype / dismiss) can cancel it before it
                    // spins up an orphaned poll.
                    addStartTimer = setTimeout(() => {
                        addStartTimer = null;
                        pollInterval = setInterval(() => pollAddSync(data.psn_username), 2500);
                    }, 2500);
                } else {
                    hide(spinner);
                    PlatPursuit.ToastManager.error(data.error || 'Failed to sync profile. Check the username and try again.');
                }
            })
            .catch((error) => {
                console.error('Sync form error:', error);
                hide(spinner);
                PlatPursuit.ToastManager.error('Failed to sync profile. Please try again.');
            });
    });

    // ---- Clear ----
    clearBtn.addEventListener('click', () => {
        input.value = '';
        hide(clearBtn);
        resetAddSync();
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

    window.addEventListener('beforeunload', () => {
        if (pollInterval) clearInterval(pollInterval);
    });
});
