/**
 * Shared trophy-grid behavior: the adaptive "Jump to pack" menu that replaces the group-nav
 * chip cloud on many-DLC lists (partials/trophy_grid/grid.html, both List detail and the
 * concept Game page).
 *
 * The control is a native <details>, so open/close needs no JS at all. Everything here is a
 * document-delegated ENHANCEMENT, bound exactly once: the grid is htmx-swapped on both hosts
 * (filter swaps on List detail, list switches on the Game page), and a swapped-in menu arrives
 * fresh and closed with nothing to rebind. Jumping itself is not owned here either -- rows are
 * plain anchors that game-detail.js upgrades to its smooth-scroll + flash on List detail.
 */
document.addEventListener('DOMContentLoaded', () => {
    'use strict';

    function closeMenu(menu) {
        if (!menu) return;
        menu.removeAttribute('open');
    }
    function openMenus() {
        return document.querySelectorAll('[data-gd-jumpmenu][open]');
    }

    // Picking a pack closes the menu (the jump itself is the anchor's / List detail's job).
    // Outside clicks close any open menu -- <details> has no native light-dismiss.
    document.addEventListener('click', (e) => {
        const row = e.target.closest && e.target.closest('[data-gd-jumpmenu] [data-gd-groupjump]');
        if (row) { closeMenu(row.closest('[data-gd-jumpmenu]')); return; }
        openMenus().forEach((m) => { if (!m.contains(e.target)) closeMenu(m); });
    });

    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        openMenus().forEach((m) => {
            closeMenu(m);
            const btn = m.querySelector('summary');
            if (btn) btn.focus();   // hand focus back so Escape doesn't strand the keyboard user
        });
    });

    // Typeahead: hide rows whose pack name doesn't contain the query.
    document.addEventListener('input', (e) => {
        const input = e.target.closest && e.target.closest('[data-gd-jumpfilter]');
        if (!input) return;
        const q = input.value.trim().toLowerCase();
        const menu = input.closest('[data-gd-jumpmenu]');
        if (!menu) return;
        let shown = 0;
        menu.querySelectorAll('.gd-jumpmenu__row').forEach((r) => {
            const name = r.querySelector('.gd-groupnav__name');
            r.hidden = !!q && !(name && name.textContent.toLowerCase().includes(q));
            if (!r.hidden) shown += 1;
        });
        // A query that empties the list must SAY so (final-audit finding: the popover collapsed
        // to a bare text field, with nothing for a screen reader). role="status" announces it.
        const none = menu.querySelector('[data-gd-jumpnone]');
        if (none) none.hidden = shown > 0;
    });

    // A closing menu resets its filter so it reopens showing every pack. 'toggle' doesn't
    // bubble, so listen in the capture phase (still one document-level binding).
    document.addEventListener('toggle', (e) => {
        const menu = e.target && e.target.matches && e.target.matches('[data-gd-jumpmenu]') ? e.target : null;
        if (!menu || menu.open) return;
        const input = menu.querySelector('[data-gd-jumpfilter]');
        if (input) input.value = '';
        menu.querySelectorAll('.gd-jumpmenu__row').forEach((r) => { r.hidden = false; });
        const none = menu.querySelector('[data-gd-jumpnone]');
        if (none) none.hidden = true;
    }, true);
});
