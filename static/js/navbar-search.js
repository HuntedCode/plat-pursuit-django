/**
 * Navbar Sync Search
 *
 * Handles the "Sync a PSN profile" dropdown form in the navbar. Previously
 * lived inside the hotbar (templates/partials/hotbar.html) as both a desktop
 * inline form and a collapsible mobile row, which crowded the hotbar's flex
 * row and caused Safari to overlap the sync-now button with the search
 * controls in the 768-1023px tablet band.
 *
 * Moved to the navbar so the hotbar can stay compact and so search is
 * reachable on every page that renders the navbar (the hotbar is only
 * rendered for auth users with a profile, but the navbar is global).
 *
 * The form posts to `search_sync_profile` to kick off the add, then polls
 * `add_sync_status` until either the new profile finishes basic ingestion
 * (data.account_id appears → show Visit link) or errors out.
 */
document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('navbar-sync-form');
    if (!form) return;

    const addSyncStatusUrl = form.dataset.urlAddSync;
    const errorText = document.getElementById('add-sync-error-text');
    let pollInterval;

    function show(el) { el?.classList.remove('hidden'); }
    function hide(el) { el?.classList.add('hidden'); }

    function getRefs() {
        return {
            btn:    form.querySelector('.add-sync-btn'),
            load:   form.querySelector('.add-sync-load'),
            anchor: form.querySelector('.add-sync-anchor'),
            input:  form.querySelector('.add-sync-input'),
        };
    }

    function checkAddSync(data, refs) {
        if (data.sync_status === 'error') {
            hide(refs.load);
            hide(refs.input);
            if (errorText) {
                errorText.textContent = 'Sync error: check spelling or account permissions, then refresh and try again.';
                show(errorText);
            }
            clearInterval(pollInterval);
        } else if (data.account_id) {
            hide(refs.load);
            if (refs.anchor) {
                refs.anchor.href = data.slug;
                show(refs.anchor);
            }
            clearInterval(pollInterval);
        }
    }

    function pollAddSync(psn_username, refs) {
        if (!psn_username || !addSyncStatusUrl) return;
        const url = `${addSyncStatusUrl}?psn_username=${encodeURIComponent(psn_username)}`;
        PlatPursuit.API.get(url)
            .then(data => checkAddSync(data, refs))
            .catch(error => console.error('Add sync polling error:', error));
    }

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const refs = getRefs();
        if (errorText) hide(errorText);

        const formData = new FormData(form);
        PlatPursuit.API.postFormData(form.action, formData)
            .then(data => {
                if (data.success) {
                    hide(refs.btn);
                    show(refs.load);
                    setTimeout(() => {
                        pollInterval = setInterval(() => pollAddSync(data.psn_username, refs), 2500);
                    }, 2500);
                } else {
                    PlatPursuit.ToastManager.error(data.error || 'Failed to sync profile. Check the username and try again.');
                }
            })
            .catch(error => {
                console.error('Sync form error:', error);
                PlatPursuit.ToastManager.error('Failed to sync profile. Please try again.');
            });
    });

    window.addEventListener('beforeunload', () => {
        if (pollInterval) clearInterval(pollInterval);
    });

    // --- First-load discovery hint ---
    // Pulse + tooltip bubble that points at the search button on first load.
    // Dismissed (and the localStorage flag set) on any of:
    //   - Click of the close 'x' inside the hint bubble
    //   - Click of the search button (user found it themselves)
    //   - Focus of the search button (keyboard nav, opening the dropdown)

    const HINT_STORAGE_KEY = 'navbar_search_hint_dismissed';
    const searchBtn = document.getElementById('navbar-sync-search-btn');
    const hint = document.getElementById('navbar-search-hint');
    const hintClose = document.getElementById('navbar-search-hint-close');

    if (hint && searchBtn && localStorage.getItem(HINT_STORAGE_KEY) !== 'true') {
        const dismissHint = () => {
            hint.classList.add('hidden');
            searchBtn.classList.remove('navbar-search-pulse');
            localStorage.setItem(HINT_STORAGE_KEY, 'true');
        };

        // Small delay so the page settles (avoids layout flash during font swap
        // and the alignStickyChrome() re-measure that runs on font-ready).
        setTimeout(() => {
            if (localStorage.getItem(HINT_STORAGE_KEY) === 'true') return;
            hint.classList.remove('hidden');
            searchBtn.classList.add('navbar-search-pulse');
        }, 800);

        hintClose?.addEventListener('click', (e) => {
            e.stopPropagation();
            dismissHint();
        });
        searchBtn.addEventListener('click', dismissHint, { once: true });
        searchBtn.addEventListener('focus', dismissHint, { once: true });
    }
});
