/* Collection page controller (baseline).
 * Two concerns: the Surface-level view toggle (Binder vs List, the binder's own
 * Binder/Gallery presentation toggle is separate and owned by binder.js), and the
 * list view's client-side sort / filter / search. The list's "View ->" links set a
 * #card-<id> hash; binder.js flips to that page on hashchange, and this controller just
 * makes the binder visible first. (The binder links back via per-series detail-page links,
 * which are plain server-rendered anchors.) Namespaced under window.PlatPursuit.Collection. */
(function () {
    'use strict';

    var STORAGE_KEY = 'pp-collection-view';

    function initViewToggle(root) {
        var views = Array.prototype.slice.call(root.querySelectorAll('.pp-collection__view'));
        var chips = Array.prototype.slice.call(root.querySelectorAll('.pp-collection__view-chip'));
        if (!views.length || !chips.length) return;

        function setView(name) {
            views.forEach(function (v) {
                v.hidden = v.getAttribute('data-collection-view') !== name;
            });
            chips.forEach(function (c) {
                var on = c.getAttribute('data-collection-view') === name;
                c.classList.toggle('is-active', on);
                c.setAttribute('aria-selected', on ? 'true' : 'false');
                c.tabIndex = on ? 0 : -1;   // roving tabindex: only the active view chip is in the tab order
            });
            try { localStorage.setItem(STORAGE_KEY, name); } catch (e) { /* private mode */ }
        }

        chips.forEach(function (c, i) {
            c.addEventListener('click', function () {
                setView(c.getAttribute('data-collection-view'));
            });
            // WAI-ARIA tabs keyboard model: arrows/Home/End move focus AND switch the view.
            c.addEventListener('keydown', function (e) {
                var next = -1;
                if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (i + 1) % chips.length;
                else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (i - 1 + chips.length) % chips.length;
                else if (e.key === 'Home') next = 0;
                else if (e.key === 'End') next = chips.length - 1;
                else return;
                e.preventDefault();
                setView(chips[next].getAttribute('data-collection-view'));
                chips[next].focus();
            });
        });

        // Initial view: a #card-<id> deep-link lands in the Case, otherwise the stored preference
        // (legacy 'binder' maps to 'case'), else the Case.
        var initial = 'case';
        if (window.location.hash.indexOf('#card-') !== 0) {
            try { initial = localStorage.getItem(STORAGE_KEY) || 'case'; } catch (e) { /* noop */ }
        }
        if (initial === 'binder') initial = 'case';
        setView(initial);

        // Row "View ->" cross-link: show the Case first; initCase's hashchange handler then activates
        // the badge's set + scrolls to it. If the hash is ALREADY this card, nudge a hashchange manually.
        root.querySelectorAll('[data-binder-link]').forEach(function (a) {
            a.addEventListener('click', function () {
                setView('case');
                if (window.location.hash === a.getAttribute('href')) {
                    window.dispatchEvent(new Event('hashchange'));
                }
            });
        });
    }

    // The Case: set tabs switch shelves; a #card-<id> deep-link (or list "View ->") lands on the
    // badge's set and scrolls to it.
    function initCase(root) {
        var caseEl = root.querySelector('.pp-case');
        if (!caseEl) return;
        var tabs = Array.prototype.slice.call(caseEl.querySelectorAll('[data-set-tab]'));
        var shelves = Array.prototype.slice.call(caseEl.querySelectorAll('.pp-case__shelf[data-set]'));
        if (!shelves.length) return;

        function activateSet(key) {
            shelves.forEach(function (s) { s.hidden = s.getAttribute('data-set') !== key; });
            tabs.forEach(function (t) {
                var on = t.getAttribute('data-set-tab') === key;
                t.classList.toggle('is-active', on);
                t.setAttribute('aria-selected', on ? 'true' : 'false');
                t.tabIndex = on ? 0 : -1;   // roving tabindex: only the active tab is in the tab order
            });
        }
        tabs.forEach(function (tab, i) {
            tab.addEventListener('click', function () { activateSet(tab.getAttribute('data-set-tab')); });
            // WAI-ARIA tabs keyboard model: arrows/Home/End move focus AND activate the tab.
            tab.addEventListener('keydown', function (e) {
                var next = -1;
                if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (i + 1) % tabs.length;
                else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (i - 1 + tabs.length) % tabs.length;
                else if (e.key === 'Home') next = 0;
                else if (e.key === 'End') next = tabs.length - 1;
                else return;
                e.preventDefault();
                activateSet(tabs[next].getAttribute('data-set-tab'));
                tabs[next].focus();
            });
        });
        tabs.forEach(function (t) { t.tabIndex = t.classList.contains('is-active') ? 0 : -1; });

        function jumpToCard() {
            if (window.location.hash.indexOf('#card-') !== 0) return;
            var target = caseEl.querySelector(window.location.hash);
            if (!target) return;
            var shelf = target.closest('.pp-case__shelf[data-set]');
            if (shelf) activateSet(shelf.getAttribute('data-set'));
            (target.closest('.pp-case__slot') || target).scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        jumpToCard();
        window.addEventListener('hashchange', jumpToCard);
    }

    // Badge detail ("pick it up"): tap a medallion -> fetch its detail partial into the modal. The slot
    // keeps its href to the badge page as a no-JS fallback.
    function initDetail(root) {
        var modal = document.getElementById('collection-detail');
        if (!modal) return;
        var body = modal.querySelector('[data-detail-body]');
        var dialog = modal.querySelector('.pp-detail-modal__dialog');
        var lastFocus = null, busy = false;

        function open(url) {
            if (busy) return;
            busy = true;
            lastFocus = document.activeElement;   // capture the trigger before async work moves focus
            fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.text() : null; })
                .then(function (html) {
                    busy = false;
                    if (html == null) return;
                    body.innerHTML = html;
                    modal.hidden = false;
                    document.body.style.overflow = 'hidden';
                    if (dialog) dialog.focus();
                })
                .catch(function () { busy = false; });
        }
        function close() {
            modal.hidden = true;
            body.innerHTML = '';
            document.body.style.overflow = '';
            if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) { /* gone */ } }
        }

        // Delegate across the whole page: any medallion with a data-modal-url (Case slots, Showcase,
        // Chase, Gallery cells) opens the detail modal. The List table's "View ->" uses a #card hash
        // instead, so it doesn't match here.
        root.addEventListener('click', function (e) {
            var slot = e.target.closest('[data-modal-url]');
            if (!slot) return;
            e.preventDefault();
            open(slot.getAttribute('data-modal-url'));
        });
        modal.querySelectorAll('[data-detail-close]').forEach(function (b) { b.addEventListener('click', close); });
        document.addEventListener('keydown', function (e) {
            if (modal.hidden) return;
            if (e.key === 'Escape') { close(); return; }
            if (e.key === 'Tab') {   // trap focus within the dialog
                var f = Array.prototype.slice.call(dialog.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'))
                    .filter(function (el) { return el.offsetParent !== null; });
                if (!f.length) { e.preventDefault(); dialog.focus(); return; }
                var first = f[0], last = f[f.length - 1];
                if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
            }
        });
    }

    // Showcase mode toggle (Rarest / Newest / Top tier): swap the visible row, persisted. A lightweight
    // form of curation until per-badge picking ships with the customization update.
    function initShowcase(root) {
        var sc = root.querySelector('.pp-showcase');
        if (!sc) return;
        var modes = Array.prototype.slice.call(sc.querySelectorAll('[data-showcase-mode]'));
        var rows = Array.prototype.slice.call(sc.querySelectorAll('[data-showcase-row]'));
        if (modes.length < 2) return;   // one mode -> nothing to toggle
        var KEY = 'pp-collection-showcase';
        function setMode(name) {
            if (!rows.some(function (r) { return r.getAttribute('data-showcase-row') === name; })) return;
            rows.forEach(function (r) { r.hidden = r.getAttribute('data-showcase-row') !== name; });
            modes.forEach(function (m) { var on = m.getAttribute('data-showcase-mode') === name; m.classList.toggle('is-active', on); m.setAttribute('aria-pressed', on ? 'true' : 'false'); });
            try { localStorage.setItem(KEY, name); } catch (e) { /* private mode */ }
        }
        modes.forEach(function (m) { m.addEventListener('click', function () { setMode(m.getAttribute('data-showcase-mode')); }); });
        var stored = null;
        try { stored = localStorage.getItem(KEY); } catch (e) { /* noop */ }
        if (stored) setMode(stored);   // else leave the server default (first mode active)
    }

    // --- Shared filter/sort primitives (the List table + the Gallery wall filter the SAME flat badge
    // set on the SAME data-* attributes; only the presentation and the sort UI differ). ---
    var TIER_ORDER = ['bronze', 'silver', 'gold', 'platinum'];
    var STATE_ORDER = ['earned', 'maintenance', 'in_progress', 'unearned'];

    function stateMatches(elState, want) {
        if (want === 'all' || elState === want) return true;
        // A "maintenance" badge is still held -> it counts as earned for filtering
        // (it has no dedicated chip; the lapse only matters on the shelf).
        return want === 'earned' && elState === 'maintenance';
    }

    function elMatches(el, filters, term) {
        return (filters.tier === 'all' || el.getAttribute('data-tier') === filters.tier)
            && stateMatches(el.getAttribute('data-state'), filters.state)
            && (filters.theme === 'all' || el.getAttribute('data-theme') === filters.theme)
            && (term === ''
                || el.getAttribute('data-series').indexOf(term) !== -1
                || el.getAttribute('data-badge').indexOf(term) !== -1);
    }

    function sortValue(el, key) {
        switch (key) {
            case 'set_number': return parseInt(el.getAttribute('data-set-number'), 10) || 0;
            case 'series':     return el.getAttribute('data-series');
            case 'tier':       return TIER_ORDER.indexOf(el.getAttribute('data-tier'));
            case 'state':      return STATE_ORDER.indexOf(el.getAttribute('data-state'));
            case 'progress':   return parseFloat(el.getAttribute('data-progress')) || 0;
            case 'rarity':     return parseFloat(el.getAttribute('data-rarity-pct')) || 0;
            case 'rank':       return parseInt(el.getAttribute('data-rank'), 10) || 0;
            case 'theme':      return el.getAttribute('data-theme');
            default:           return 0;
        }
    }

    function compareBy(key, dir) {
        return function (a, b) {
            var av = sortValue(a, key), bv = sortValue(b, key);
            if (av < bv) return dir === 'asc' ? -1 : 1;
            if (av > bv) return dir === 'asc' ? 1 : -1;
            return 0;
        };
    }

    // Wire the tier/state/set filter chips within `scope`: click sets the dimension + repaints the group's
    // active state, then re-applies. Shared by the List and the Gallery (identical chip markup).
    function wireFilterChips(scope, filters, applyFilters) {
        scope.querySelectorAll('[data-filter-tier], [data-filter-state], [data-filter-theme]').forEach(function (chip) {
            chip.addEventListener('click', function () {
                var dim = chip.hasAttribute('data-filter-tier') ? 'tier'
                    : chip.hasAttribute('data-filter-state') ? 'state' : 'theme';
                filters[dim] = chip.getAttribute('data-filter-' + dim);
                scope.querySelectorAll('[data-filter-' + dim + ']').forEach(function (c) {
                    c.classList.toggle('is-active', c === chip);
                });
                applyFilters();
            });
        });
    }

    // The List: the data table. Column-header sort (asc/desc toggle + aria-sort) over the shared engine.
    function initList(root) {
        var listRoot = root.querySelector('.pp-list');
        if (!listRoot) return;
        var tbody = listRoot.querySelector('.pp-list__table tbody');
        if (!tbody) return;

        var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        var stats = listRoot.querySelector('[data-visible-count]');
        var emptyMsg = listRoot.querySelector('[data-empty-message]');
        var totalRows = rows.length;
        var filters = { tier: 'all', state: 'all', theme: 'all' };
        var searchTerm = '';
        var sortKey = 'series';
        var sortDir = 'asc';

        function applyFilters() {
            var visible = 0;
            rows.forEach(function (row) {
                var ok = elMatches(row, filters, searchTerm);
                row.style.display = ok ? '' : 'none';
                if (ok) visible++;
            });
            if (stats) stats.textContent = visible + ' of ' + totalRows;
            if (emptyMsg) emptyMsg.hidden = visible !== 0;
        }

        function applySort() {
            rows.slice().sort(compareBy(sortKey, sortDir)).forEach(function (row) { tbody.appendChild(row); });
            listRoot.querySelectorAll('th[data-sort]').forEach(function (th) {
                th.classList.remove('is-sorted-asc', 'is-sorted-desc');
                if (th.getAttribute('data-sort') === sortKey) {
                    th.classList.add(sortDir === 'asc' ? 'is-sorted-asc' : 'is-sorted-desc');
                    th.setAttribute('aria-sort', sortDir === 'asc' ? 'ascending' : 'descending');
                } else {
                    th.setAttribute('aria-sort', 'none');
                }
            });
        }

        wireFilterChips(listRoot, filters, applyFilters);

        var search = listRoot.querySelector('[data-search]');
        if (search) {
            search.addEventListener('input', function (e) {
                searchTerm = e.target.value.toLowerCase().trim();
                applyFilters();
            });
        }

        listRoot.querySelectorAll('th[data-sort]').forEach(function (th) {
            function toggleSort() {
                var key = th.getAttribute('data-sort');
                if (sortKey === key) {
                    sortDir = sortDir === 'asc' ? 'desc' : 'asc';
                } else {
                    sortKey = key;
                    sortDir = 'asc';
                }
                applySort();
            }
            th.addEventListener('click', toggleSort);
            th.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleSort(); }
            });
        });

        applySort();  // default: series ascending (set numbers are sparse pre-launch)
    }

    // The Gallery: the visual filter wall. Same filters as the List, but a flat medallion grid and a
    // sort <select> (value = "key:dir") instead of column headers.
    function initGallery(root) {
        var gal = root.querySelector('.pp-gallery');
        if (!gal) return;
        var grid = gal.querySelector('[data-gallery-grid]');
        var cells = Array.prototype.slice.call(gal.querySelectorAll('[data-gallery-cell]'));
        if (!grid || !cells.length) return;

        var stats = gal.querySelector('[data-visible-count]');
        var emptyMsg = gal.querySelector('[data-empty-message]');
        var total = cells.length;
        var filters = { tier: 'all', state: 'all', theme: 'all' };
        var searchTerm = '';

        function applyFilters() {
            var visible = 0;
            cells.forEach(function (cell) {
                var ok = elMatches(cell, filters, searchTerm);
                cell.style.display = ok ? '' : 'none';
                if (ok) visible++;
            });
            if (stats) stats.textContent = visible + ' of ' + total;
            if (emptyMsg) emptyMsg.hidden = visible !== 0;
        }

        wireFilterChips(gal, filters, applyFilters);

        var search = gal.querySelector('[data-search]');
        if (search) {
            search.addEventListener('input', function (e) {
                searchTerm = e.target.value.toLowerCase().trim();
                applyFilters();
            });
        }

        var sortSel = gal.querySelector('[data-gallery-sort]');
        function applySort() {
            var spec = ((sortSel && sortSel.value) || 'series:asc').split(':');
            cells.slice().sort(compareBy(spec[0], spec[1] || 'asc')).forEach(function (c) { grid.appendChild(c); });
        }
        if (sortSel) sortSel.addEventListener('change', applySort);
        applySort();  // default matches the select's first option (series A-Z)
    }

    function init() {
        var root = document.querySelector('.pp-collection');
        if (!root) return;
        initViewToggle(root);
        initCase(root);
        initShowcase(root);
        initDetail(root);
        initGallery(root);
        initList(root);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.Collection = { init: init };
})();
