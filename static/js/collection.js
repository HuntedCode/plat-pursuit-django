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
            });
            try { localStorage.setItem(STORAGE_KEY, name); } catch (e) { /* private mode */ }
        }

        chips.forEach(function (c) {
            c.addEventListener('click', function () {
                setView(c.getAttribute('data-collection-view'));
            });
        });

        // Initial view: a #card-<id> deep-link lands in the binder, otherwise the stored
        // preference, else the Binder.
        var initial = 'binder';
        if (window.location.hash.indexOf('#card-') !== 0) {
            try { initial = localStorage.getItem(STORAGE_KEY) || 'binder'; } catch (e) { /* noop */ }
        }
        setView(initial);

        // Row "View ->" cross-link: show the binder before its hashchange handler flips
        // to the card's page (the card lives inside the binder view, hidden until now).
        // If the hash is ALREADY this card (re-click / arrived via #card-<id>), the browser
        // won't fire hashchange, so nudge binder.js to re-run its jump manually.
        root.querySelectorAll('[data-binder-link]').forEach(function (a) {
            a.addEventListener('click', function () {
                setView('binder');
                if (window.location.hash === a.getAttribute('href')) {
                    window.dispatchEvent(new Event('hashchange'));
                }
            });
        });
    }

    var TIER_ORDER = ['bronze', 'silver', 'gold', 'platinum'];
    var STATE_ORDER = ['earned', 'maintenance', 'in_progress', 'unearned'];

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

        function stateMatches(rowState, want) {
            if (want === 'all' || rowState === want) return true;
            // A "maintenance" badge is still held -> it counts as earned for filtering
            // (it has no dedicated chip; the lapse only matters in the binder).
            return want === 'earned' && rowState === 'maintenance';
        }

        function applyFilters() {
            var visible = 0;
            rows.forEach(function (row) {
                var ok = (filters.tier === 'all' || row.getAttribute('data-tier') === filters.tier)
                    && stateMatches(row.getAttribute('data-state'), filters.state)
                    && (filters.theme === 'all' || row.getAttribute('data-theme') === filters.theme)
                    && (searchTerm === ''
                        || row.getAttribute('data-series').indexOf(searchTerm) !== -1
                        || row.getAttribute('data-badge').indexOf(searchTerm) !== -1);
                row.style.display = ok ? '' : 'none';
                if (ok) visible++;
            });
            if (stats) stats.textContent = visible + ' of ' + totalRows;
            if (emptyMsg) emptyMsg.hidden = visible !== 0;
        }

        function sortValue(row, key) {
            switch (key) {
                case 'set_number': return parseInt(row.getAttribute('data-set-number'), 10) || 0;
                case 'series':     return row.getAttribute('data-series');
                case 'tier':       return TIER_ORDER.indexOf(row.getAttribute('data-tier'));
                case 'state':      return STATE_ORDER.indexOf(row.getAttribute('data-state'));
                case 'progress':   return parseFloat(row.getAttribute('data-progress')) || 0;
                case 'rarity':     return parseFloat(row.getAttribute('data-rarity-pct')) || 0;
                case 'rank':       return parseInt(row.getAttribute('data-rank'), 10) || 0;
                case 'theme':      return row.getAttribute('data-theme');
                default:           return 0;
            }
        }

        function applySort() {
            rows.slice().sort(function (a, b) {
                var av = sortValue(a, sortKey);
                var bv = sortValue(b, sortKey);
                if (av < bv) return sortDir === 'asc' ? -1 : 1;
                if (av > bv) return sortDir === 'asc' ? 1 : -1;
                return 0;
            }).forEach(function (row) { tbody.appendChild(row); });
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

        listRoot.querySelectorAll('[data-filter-tier], [data-filter-state], [data-filter-theme]').forEach(function (chip) {
            chip.addEventListener('click', function () {
                var dim = chip.hasAttribute('data-filter-tier') ? 'tier'
                    : chip.hasAttribute('data-filter-state') ? 'state' : 'theme';
                filters[dim] = chip.getAttribute('data-filter-' + dim);
                listRoot.querySelectorAll('[data-filter-' + dim + ']').forEach(function (c) {
                    c.classList.toggle('is-active', c === chip);
                });
                applyFilters();
            });
        });

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

    function init() {
        var root = document.querySelector('.pp-collection');
        if (!root) return;
        initViewToggle(root);
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
