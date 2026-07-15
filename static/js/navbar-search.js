/**
 * Navbar PSN search.
 *
 * A persistent typeahead bar (md+) that collapses to an icon trigger below md.
 * Two behaviours share one #navbar-sync-form:
 *   1. Typeahead over EXISTING tracked profiles (GET profile_suggest, debounced)
 *      -> a dropdown of matching hunters, each linking to their profile page.
 *   2. Add-and-sync fallback for a name that isn't tracked yet: the form posts
 *      to search_sync_profile, then polls add_sync_status until the new profile
 *      finishes basic ingestion (-> Visit link) or errors.
 *
 * Rows are built with DOM APIs (not innerHTML interpolation) so PSN-sourced
 * display names and avatar URLs can never inject markup.
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
    function personPlaceholder() {
        const span = document.createElement('span');
        span.className = 'pp-navsearch__ph';
        span.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
        return span;
    }
    function buildRow(p, i) {
        const a = document.createElement('a');
        a.className = 'pp-navsearch__opt';
        a.href = p.url;
        a.id = 'navsearch-opt-' + i;
        a.setAttribute('role', 'option');

        if (p.avatar_url) {
            const img = document.createElement('img');
            img.src = p.avatar_url;            // property assignment: no markup injection
            img.alt = '';
            img.loading = 'lazy';
            img.decoding = 'async';
            a.appendChild(img);
        } else {
            a.appendChild(personPlaceholder());
        }

        const name = document.createElement('b');
        name.textContent = p.display;          // textContent: safe
        a.appendChild(name);

        const plats = document.createElement('span');
        plats.className = 'pp-navsearch__plats';
        plats.textContent = Number(p.plats || 0).toLocaleString();
        plats.title = 'Platinums';
        a.appendChild(plats);
        return a;
    }
    function renderSuggestions(results, q) {
        resetAddSync();
        list.textContent = '';
        results.forEach((p, i) => list.appendChild(buildRow(p, i)));

        if (addTerm) addTerm.textContent = q;
        addBtn.hidden = !PSN_RE.test(q);

        refreshItems();
        // Only open when there's something to show (a suggestion or the add row); a 2-char
        // non-PSN query with no matches would otherwise flash an empty bordered panel.
        if (items.length) { openPanel(); setActive(0); }
        else { closePanel(); }
    }

    const fetchSuggest = PlatPursuit.debounce((q) => {
        if (!suggestUrl) return;
        PlatPursuit.API.get(`${suggestUrl}?q=${encodeURIComponent(q)}`)
            .then((data) => {
                if (input.value.trim() !== q) return;   // stale: input moved on
                renderSuggestions(data.results || [], q);
            })
            .catch(() => {
                // Network hiccup / throttle: keep the field usable. If the name is
                // a valid PSN id, still offer the add-and-sync fallback.
                if (input.value.trim() !== q) return;
                renderSuggestions([], q);
            });
    }, 180);

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
