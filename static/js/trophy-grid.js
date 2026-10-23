/**
 * Shared trophy-grid behavior, on both List detail and the concept Game page:
 *
 *  - the control row's "More filters" panel (partials/trophy_grid/_controls.html, GL-10), on the house
 *    PlatPursuit.filterPanel, and the chip "pop". Exposed as PlatPursuit.TrophyControls for the pages
 *    that drive it from elsewhere (the minibar's Filters reach, a list switch's reset).
 *  - the adaptive "Jump to pack" menu that replaces the group-nav chip cloud on many-DLC lists
 *    (partials/trophy_grid/grid.html). The control is a native <details>, so open/close needs no JS.
 *  - the page bar's group label: the group under the pinned minibar, and the hunter's progress in it.
 *
 * Everything here is bound exactly once. The control row stands OUTSIDE every swap target on both pages,
 * so it is wired at load; the grid is htmx-swapped (filter swaps, list switches), so its jump menu is
 * document-delegated, and a swapped-in menu arrives fresh with nothing to rebind.
 * Jumping itself is not owned here either -- rows are plain anchors that game-detail.js upgrades to its
 * smooth-scroll + flash on List detail.
 */
document.addEventListener('DOMContentLoaded', () => {
    'use strict';

    const PP = window.PlatPursuit = window.PlatPursuit || {};
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ── The control row: More filters ────────────────────────────────────────────────────────────
    // The panel is the house filter drawer (PlatPursuit.filterPanel: toggle + aria, the height tween,
    // the count badge and its pop, open on load when a filter is already set). What is ours is the
    // count: only the filters INSIDE the panel (type, rarity, DLC), the ones a closed panel hides. Sort
    // and All / Unearned stand on the row itself. A hidden DLC field (a list without DLC on the Game
    // page) is not a filter that applies. No dim target: each page dims its own swap target.
    const handles = new WeakMap();
    function activeCount(row) {
        const panel = row.querySelector('[data-gd-more-panel]');
        if (!panel) return 0;
        let n = panel.querySelectorAll('input:checked').length;
        const dlc = panel.querySelector('[data-gd-dlc]:not([hidden]) select[name="dlc_filter"]');
        if (dlc && dlc.selectedIndex > 0) n += 1;
        return n;
    }
    document.querySelectorAll('.gd-controls').forEach((row) => {
        const form = row.closest('form');
        if (!form || !PP.filterPanel) return;
        const handle = PP.filterPanel({
            form, toggle: row.querySelector('[data-gd-more]'), panel: row.querySelector('[data-gd-more-panel]'),
            countEl: row.querySelector('[data-gd-more-count]'), count: () => activeCount(row), dimTarget: null,
        });
        if (handle) handles.set(row, handle);
    });
    // The row's count, after a swap: ticks from the number it showed to the new one. The last value is
    // kept on the element, not read back from its text, which a count-up still running would garble.
    function setCount(row, n) {
        const el = row && row.querySelector('[data-gd-count]');
        if (!el || isNaN(n)) return;
        const from = el.dataset.shown !== undefined ? parseInt(el.dataset.shown, 10)
            : parseInt(el.textContent.replace(/[^0-9]/g, ''), 10);
        el.dataset.shown = String(n);
        if (PP.countUp && !reduce && !isNaN(from) && from !== n) {
            el.dataset.countup = n;
            PP.countUp(el, 600, { from });
        } else {
            el.textContent = n.toLocaleString();
        }
    }
    // The row's loading bar, delay-gated: an instant filter never flashes it, only one that takes a beat
    // (a whale's list, a slow network) surfaces it.
    const busyTimers = new WeakMap();
    function setBusy(row, on) {
        const bar = row && row.querySelector('[data-gd-loadbar]');
        if (!bar) return;
        window.clearTimeout(busyTimers.get(row));
        if (on) busyTimers.set(row, window.setTimeout(() => bar.classList.add('is-active'), 220));
        else bar.classList.remove('is-active');
    }
    // For the pages that drive the row from their own swaps: the minibar's Filters reach, a list switch's
    // reset, the count and the bar.
    PP.TrophyControls = {
        open: (row) => { const h = handles.get(row); if (h) h.setOpen(true, true); },
        sync: (row) => { const h = handles.get(row); if (h) h.refresh(); },
        setCount, setBusy,
    };

    // A small spring on a type/rarity chip as it toggles.
    document.addEventListener('change', (e) => {
        if (reduce || !e.target.matches || !e.target.matches('.gd-controls .gd-chip input')) return;
        const face = e.target.nextElementSibling;
        if (face) { face.classList.remove('is-pop'); void face.offsetWidth; face.classList.add('is-pop'); }
    });

    // ── Jump to pack ─────────────────────────────────────────────────────────────────────────────

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

    // ── The page bar names the group under it ────────────────────────────────────────────────────
    // Group headers do not pin: with the navbar, the sub-nav and the minibar they held a third of a laptop
    // screen. Instead the pinned minibar shows the trophy group under it, with the hunter's progress in it
    // when the header has one, in place of the page title. Read off the headers on every scroll rather than
    // cached, so a filter swap or a list switch needs no wiring; a section with no box is in a view that is
    // not showing.
    const groupLabel = document.querySelector('[data-minibar-group]');
    const bar = groupLabel && groupLabel.closest('.gd-minibar');
    if (bar) {
        const nameEl = groupLabel.querySelector('[data-minibar-group-name]');
        const pctEl = groupLabel.querySelector('[data-minibar-group-pct]');
        let shown = null;
        // The group whose section has reached the bar's bottom. A group jump (scrollIntoView) parks its
        // section at the page's scroll padding instead, a little UNDER the bar, so a section sitting exactly
        // there counts too: it is the group the hunter jumped to, not the one ending just above it.
        const groupUnderBar = () => {
            if (!bar.classList.contains('is-pinned')) return null;
            const padding = parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop) || 0;
            const line = bar.getBoundingClientRect().bottom + 1;
            let hit = null;
            for (const section of document.querySelectorAll('.gd-group')) {
                if (!section.getClientRects().length) continue;
                const top = section.getBoundingClientRect().top;
                if (top > line && Math.abs(top - padding) > 1) break;
                hit = section;
            }
            return hit;
        };
        const sync = () => {
            const section = groupUnderBar();
            if (section === shown) return;
            shown = section;
            bar.classList.toggle('has-group', !!section);
            groupLabel.hidden = !section;
            if (!section) return;
            const name = section.querySelector('.gd-group__name');
            const pct = section.querySelector('.gd-group__pct');
            nameEl.textContent = name ? name.textContent.trim() : '';
            pctEl.textContent = pct ? pct.textContent.trim() : '';
            pctEl.hidden = !pct;
        };
        let raf = 0;
        const schedule = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; sync(); }); };
        window.addEventListener('scroll', schedule, { passive: true });
        window.addEventListener('resize', schedule);
        document.body.addEventListener('htmx:afterSettle', schedule);
        // The bar pins from an IntersectionObserver, which can land after the scroll frame, and a view switch
        // changes what is showing without a scroll.
        new MutationObserver(schedule).observe(bar, { attributes: true, attributeFilter: ['class', 'data-mb-active'] });
        bar.addEventListener('transitionend', schedule);   // its entrance and shift move the line it measures from
        schedule();
    }
});
