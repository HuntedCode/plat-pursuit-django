/* Collection page controller.
 * Drives the Case/Gallery view toggle, the medallion physicality (flip/tilt/gyro), the first-earn
 * mint ceremony, the badge detail modal, the header count-ups + tappable tier stats, and the Gallery's
 * client-side filter / sort / search. Namespaced under window.PlatPursuit.Collection. */
(function () {
    'use strict';

    var STORAGE_KEY = 'pp-collection-view';

    // Medallion physicality (flip/tilt/gyro) now lives in the shared PlatPursuit.Medallion module
    // (static/js/medallion.js, loaded globally before this). Alias its motion gates so the grow/wiggle
    // checks below stay in sync with the tilt's -- one source of truth.
    var prefersReducedMotion = PlatPursuit.Medallion.prefersReducedMotion;

    function initViewToggle(root) {
        var views = Array.prototype.slice.call(root.querySelectorAll('.pp-collection__view'));
        var chips = Array.prototype.slice.call(root.querySelectorAll('.pp-switch__chip'));
        if (!views.length || !chips.length) return;

        // The Gallery's URL params -- mirrored in the URL only while the Gallery is active, and stripped
        // when you leave it (so a shared Case link stays clean). Kept in sync with initGallery.
        var GALLERY_PARAMS = ['tier', 'state', 'set', 'q', 'sort'];
        // View order (Case / Gallery / List) taken from the chip order, for the directional slide.
        var VIEW_ORDER = chips.map(function (c) { return c.getAttribute('data-collection-view'); });
        function currentView() {
            var cur = null;
            views.forEach(function (v) { if (!v.hidden) { cur = v.getAttribute('data-collection-view'); } });
            return cur;
        }
        var tablist;
        function setView(name, userAction) {
            var from = currentView();
            var changed = from !== name;
            var shown = null;
            views.forEach(function (v) {
                var on = v.getAttribute('data-collection-view') === name;
                v.hidden = !on;
                if (on) { shown = v; }
            });
            // Directional cross-fade: the incoming view slides in from the side it lives on (shared with Career).
            if (changed) { PlatPursuit.slideViewIn(shown, from, name, VIEW_ORDER); }
            var activeChip = null;
            chips.forEach(function (c) {
                var on = c.getAttribute('data-collection-view') === name;
                c.classList.toggle('is-active', on);
                c.setAttribute('aria-selected', on ? 'true' : 'false');
                if (on) { activeChip = c; }
            });
            if (tablist) { tablist.syncTabindex(); }                       // roving tabindex
            if (userAction && changed) { PlatPursuit.igniteTab(activeChip); }  // the newly-active chip blooms once
            try { localStorage.setItem(STORAGE_KEY, name); } catch (e) { /* private mode */ }
            // Reflect the active view in the URL (shareable + reload-safe); Case is the default so its URL
            // stays clean, and leaving the Gallery strips its filter params (a shared Case link stays clean).
            PlatPursuit.syncViewParam(name, { default: 'case', paramView: 'gallery', params: GALLERY_PARAMS });
        }
        // Click + Arrow/Home/End keyboard nav + roving tabindex via the shared tablist helper.
        tablist = PlatPursuit.wireTablist(chips, { onSelect: function (c) { setView(c.getAttribute('data-collection-view'), true); } });

        // Initial view: a #card-<id> deep-link lands in the Case; else an explicit ?view= wins (shared /
        // reloaded link); else the stored preference; else the Case. (legacy 'binder'->case, 'list'->gallery)
        var initial = 'case';
        var urlView = new URLSearchParams(location.search).get('view');
        if (window.location.hash.indexOf('#card-') === 0) {
            initial = 'case';
        } else if (urlView === 'gallery' || urlView === 'case') {
            initial = urlView;
        } else {
            try { initial = localStorage.getItem(STORAGE_KEY) || 'case'; } catch (e) { /* noop */ }
        }
        if (initial === 'binder') initial = 'case';       // legacy binder view -> Case
        if (initial === 'list') initial = 'gallery';       // the List was retired; its data lives in the Gallery
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
        // The set the case is currently showing (template marks the first tab active). Guards
        // activateSet against redundant re-activation -- and against a haptic tick when you tap the
        // set you're already on.
        var currentKey = tabs.length ? tabs[0].getAttribute('data-set-tab') : null;

        var setTablist;
        function activateSet(key, userAction) {
            if (key === currentKey) return;
            currentKey = key;
            shelves.forEach(function (s) { s.hidden = s.getAttribute('data-set') !== key; });
            var activeTab = null;
            tabs.forEach(function (t) {
                var on = t.getAttribute('data-set-tab') === key;
                t.classList.toggle('is-active', on);
                t.setAttribute('aria-selected', on ? 'true' : 'false');
                if (on) { activeTab = t; }
            });
            if (setTablist) { setTablist.syncTabindex(); }   // roving tabindex
            if (userAction) {
                PlatPursuit.igniteTab(activeTab);            // the newly-active set tab blooms once
                // A light tick confirms the switch on touch devices (desktop has no vibrate -- a no-op there).
                if (navigator.vibrate) { try { navigator.vibrate(5); } catch (e) {} }
            }
        }
        // Click + Arrow/Home/End keyboard nav + roving tabindex via the shared tablist helper.
        setTablist = PlatPursuit.wireTablist(tabs, { onSelect: function (t) { activateSet(t.getAttribute('data-set-tab'), true); } });

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


    // Badge detail ("pick it up" / "put it down"): tap a medallion -> the shared detail-modal controller
    // grows its detail partial into the modal and shrinks it back on close. The slot keeps its href to the
    // badge page as a no-JS fallback.
    function initDetail(root) {
        var modal = document.getElementById('collection-detail');
        if (!modal || !(window.PlatPursuit && PlatPursuit.Medallion && PlatPursuit.Medallion.detailModal)) return;
        var dm = PlatPursuit.Medallion.detailModal({ modal: modal });
        // Delegate across the page: any medallion with a data-modal-url (Case slots, Showcase, Chase,
        // Gallery cells) opens the detail modal. The List table's "View ->" uses a #card hash instead.
        root.addEventListener('click', function (e) {
            var slot = e.target.closest('[data-modal-url]');
            if (!slot) return;
            e.preventDefault();
            dm.open(slot.getAttribute('data-modal-url'), slot);
        });
    }

    // --- Shared filter/sort primitives (the List table + the Gallery wall filter the SAME flat badge
    // set on the SAME data-* attributes; only the presentation and the sort UI differ). ---
    var TIER_ORDER = ['bronze', 'silver', 'gold', 'platinum'];
    var STATE_ORDER = ['earned', 'maintenance', 'in_progress', 'unearned'];

    function stateMatches(elState, want) {
        if (want === 'all' || elState === want) return true;
        // A "maintenance" badge is still held -> it counts as earned for filtering
        // (it has no dedicated chip; the lapse only matters on the shelf).
        if (want === 'earned' && elState === 'maintenance') return true;
        // The Collection is scoped to ENGAGED series, so every badge here belongs to a series you're
        // working on. An "unearned" tier (0 progress -- a rung not yet started) therefore counts as
        // In Progress for FILTERING, so you can find those chase-rungs without dropping to "All". The
        // medallion still renders unearned (data-state is untouched); only the filter is widened.
        return want === 'in_progress' && elState === 'unearned';
    }

    function elMatches(el, filters, term) {
        if (filters.tier !== 'all' && el.getAttribute('data-tier') !== filters.tier) return false;
        if (!stateMatches(el.getAttribute('data-state'), filters.state)) return false;
        if (filters.theme !== 'all' && el.getAttribute('data-theme') !== filters.theme) return false;
        if (term === '') return true;
        if (el.getAttribute('data-series').indexOf(term) !== -1) return true;
        if (el.getAttribute('data-badge').indexOf(term) !== -1) return true;
        // A numeric query (optionally "#0042") also matches the badge's set number.
        var numeric = term.replace(/^#/, '');
        if (/^\d+$/.test(numeric)
            && parseInt(numeric, 10) === (parseInt(el.getAttribute('data-set-number'), 10) || 0)) return true;
        return false;
    }

    function sortValue(el, key) {
        switch (key) {
            case 'set_number': return parseInt(el.getAttribute('data-set-number'), 10) || 0;
            case 'series':     return el.getAttribute('data-series');
            case 'tier':       return TIER_ORDER.indexOf(el.getAttribute('data-tier'));
            case 'state':      return STATE_ORDER.indexOf(el.getAttribute('data-state'));
            case 'progress':   return parseFloat(el.getAttribute('data-progress')) || 0;
            case 'earned':     return parseInt(el.getAttribute('data-earned'), 10) || 0;   // earn epoch; 0 = not held
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

    // Wire the tier/state filter chips within `scope`: click sets the dimension + repaints the group's
    // active state, then re-applies. (The Set dimension is a <select>, wired separately in initGallery.)
    function wireFilterChips(scope, filters, applyFilters) {
        scope.querySelectorAll('[data-filter-tier], [data-filter-state]').forEach(function (chip) {
            chip.addEventListener('click', function () {
                if (navigator.vibrate) { try { navigator.vibrate(5); } catch (e) { /* no-op on desktop */ } }
                var dim = chip.hasAttribute('data-filter-tier') ? 'tier' : 'state';
                filters[dim] = chip.getAttribute('data-filter-' + dim);
                scope.querySelectorAll('[data-filter-' + dim + ']').forEach(function (c) {
                    c.classList.toggle('is-active', c === chip);
                });
                applyFilters();
            });
        });
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

        function reduced() { return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches; }
        function isShown(c) { return c.style.display !== 'none'; }

        // FLIP: run `mutate` (which changes cell visibility and/or DOM order), then glide the surviving
        // cards from their old grid positions to their new ones and fade/scale entering cards in. Hidden
        // cards just drop out (no exit tween -- the survivors' glide is the premium bit). The final layout
        // is whatever `mutate` produced, so a dropped frame never leaves the grid wrong. Cancels any active
        // reveal first (its animation-fill would otherwise override our inline transforms).
        var flipPending = [];   // cards carrying FLIP inline styles awaiting cleanup
        var flipTimer;
        function flipCleanup() {   // settle any in-flight FLIP: strip inline styles (transition first, so
            clearTimeout(flipTimer);   // clearing transform snaps rather than animating back)
            flipPending.forEach(function (c) { c.style.transition = ''; c.style.transform = ''; c.style.opacity = ''; });
            flipPending = [];
        }
        function flip(mutate) {
            if (reduced()) { mutate(); return; }
            flipCleanup();   // finish any prior FLIP before re-measuring -- no stale transforms, no mid-anim wipe
            gal.classList.remove('is-revealing');
            var first = new Map();
            cells.forEach(function (c) { if (isShown(c)) first.set(c, c.getBoundingClientRect()); });
            mutate();
            var moved = [];
            cells.forEach(function (c) {
                if (!isShown(c)) return;
                var last = c.getBoundingClientRect();
                var f = first.get(c);
                if (f) {
                    var dx = f.left - last.left, dy = f.top - last.top;
                    if (dx || dy) { c.style.transition = 'none'; c.style.transform = 'translate(' + dx + 'px, ' + dy + 'px)'; moved.push(c); }
                } else {
                    c.style.transition = 'none'; c.style.opacity = '0'; c.style.transform = 'scale(0.9)'; moved.push(c);
                }
            });
            if (!moved.length) return;
            flipPending = moved;
            requestAnimationFrame(function () { requestAnimationFrame(function () {
                moved.forEach(function (c) {
                    c.style.transition = 'transform 0.42s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.32s ease';
                    c.style.transform = ''; c.style.opacity = '';
                });
            }); });
            flipTimer = setTimeout(flipCleanup, 520);
        }

        var metaClear = gal.querySelector('.pp-gallery__meta [data-clear-filters]');
        function anyActive() {
            return filters.tier !== 'all' || filters.state !== 'all' || filters.theme !== 'all' || searchTerm !== '';
        }
        function syncClear() { if (metaClear) metaClear.hidden = !anyActive(); }

        // animate defaults on (discrete actions -- chips / dropdowns / clear / pills get the FLIP glide);
        // search passes false so per-keystroke typing filters instantly (FLIP every keystroke is janky).
        function applyFilters(animate) {
            function mutate() {
                cells.forEach(function (cell) {
                    cell.style.display = elMatches(cell, filters, searchTerm) ? '' : 'none';
                });
            }
            if (animate === false) mutate(); else flip(mutate);
            var visible = 0;
            cells.forEach(function (c) { if (isShown(c)) visible++; });
            if (stats) stats.textContent = visible + ' of ' + total;
            if (emptyMsg) emptyMsg.hidden = visible !== 0;
            updateSuggest(visible);
            syncClear();
            syncFilterCount();
            renderPills();
            syncURL();
        }

        wireFilterChips(gal, filters, applyFilters);   // tier + state chips (theme is a <select>, below)

        // Filters toggle: the tier/state/set groups live in a collapsible panel (compact default). The button
        // shows/hides it + carries an active-count badge (how many filter DIMENSIONS are narrowed -- search is
        // separate, in the bar). Mirrors the Badges browse gallery's Filters toggle.
        var advToggle = gal.querySelector('[data-gallery-filters-toggle]');
        var advPanel = gal.querySelector('#gallery-advanced') || gal.querySelector('.pp-gallery__advanced');
        var advCount = gal.querySelector('[data-gallery-filter-count]');
        function setAdvPanel(open) {
            if (!advPanel || !advToggle) { return; }
            if (open) { advPanel.removeAttribute('hidden'); } else { advPanel.setAttribute('hidden', ''); }
            advToggle.classList.toggle('is-open', open);
            advToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        }
        if (advToggle && advPanel) {
            advToggle.addEventListener('click', function () { setAdvPanel(advPanel.hasAttribute('hidden')); });
        }
        function syncFilterCount() {
            var n = (filters.tier !== 'all' ? 1 : 0) + (filters.state !== 'all' ? 1 : 0) + (filters.theme !== 'all' ? 1 : 0);
            if (advCount) { advCount.textContent = String(n); advCount.hidden = (n === 0); }
        }

        var search = gal.querySelector('[data-search]');
        var searchClear = gal.querySelector('[data-search-clear]');
        function syncSearchClear() { if (searchClear) searchClear.hidden = !searchTerm; }
        // Typing glides the wall too (FLIP), but DEBOUNCED so it animates once you pause -- a FLIP on
        // every keystroke is a jittery mess. Clearing / Esc animate immediately (discrete actions).
        var searchGlide = (window.PlatPursuit && PlatPursuit.debounce)
            ? PlatPursuit.debounce(function () { applyFilters(true); }, 320)
            : function () { applyFilters(true); };
        function runSearch(val, immediate) {
            searchTerm = (val || '').toLowerCase().trim();
            syncSearchClear();
            if (immediate) applyFilters(true); else searchGlide();
        }
        if (search) {
            search.addEventListener('input', function (e) { runSearch(e.target.value); });
            search.addEventListener('keydown', function (e) {
                if (e.key === 'Escape' && searchTerm) { e.preventDefault(); search.value = ''; runSearch('', true); }
            });
        }
        if (searchClear) {
            searchClear.addEventListener('click', function () {
                if (search) { search.value = ''; search.focus(); }
                runSearch('', true);
            });
        }

        // Set filter is a dropdown (the set types keep growing) -- change swaps the theme dimension.
        var themeSelect = gal.querySelector('[data-filter-theme-select]');
        if (themeSelect) {
            themeSelect.addEventListener('change', function () {
                filters.theme = themeSelect.value;
                applyFilters();
            });
        }

        function resetChips(dim) {   // point a chip group back at its "all" option
            gal.querySelectorAll('[data-filter-' + dim + ']').forEach(function (c) {
                c.classList.toggle('is-active', c.getAttribute('data-filter-' + dim) === 'all');
            });
        }
        function activateChip(dim, val) {   // light the chip whose value === val (used when restoring from URL)
            gal.querySelectorAll('[data-filter-' + dim + ']').forEach(function (c) {
                c.classList.toggle('is-active', c.getAttribute('data-filter-' + dim) === val);
            });
        }
        function chipHas(dim, val) {   // does a chip with this value exist? (validate untrusted URL values)
            var ok = false;
            gal.querySelectorAll('[data-filter-' + dim + ']').forEach(function (c) { if (c.getAttribute('data-filter-' + dim) === val) ok = true; });
            return ok;
        }
        function optHas(sel, val) {   // does a <select> have this option value?
            if (!sel) return false;
            for (var i = 0; i < sel.options.length; i++) if (sel.options[i].value === val) return true;
            return false;
        }
        function clearAll() {
            filters.tier = filters.state = filters.theme = 'all';
            searchTerm = '';
            if (search) search.value = '';
            if (themeSelect) themeSelect.value = 'all';
            resetChips('tier'); resetChips('state');
            applyFilters();
            syncSearchClear();
        }
        gal.querySelectorAll('[data-clear-filters]').forEach(function (b) { b.addEventListener('click', clearAll); });

        // Applied-filter pills: one removable token per active filter, so it's clear what's narrowing the
        // wall and any single one can be dropped (vs "Clear filters" nuking everything).
        var pillsBox = gal.querySelector('[data-gallery-pills]');
        var STATE_LABELS = { earned: 'Earned', in_progress: 'In Progress', unearned: 'Unearned', maintenance: 'Maintenance' };
        function filterLabel(dim, val) {
            if (dim === 'state') return STATE_LABELS[val] || val;
            if (dim === 'theme') {
                var opt = themeSelect && themeSelect.querySelector('option[value="' + val + '"]');
                return 'Set: ' + (opt ? opt.textContent.trim() : val);
            }
            if (dim === 'tier') return 'Tier: ' + val.charAt(0).toUpperCase() + val.slice(1);
            return val;
        }
        function removeFilter(dim) {
            if (dim === 'search') { searchTerm = ''; if (search) search.value = ''; syncSearchClear(); }
            else if (dim === 'theme') { filters.theme = 'all'; if (themeSelect) themeSelect.value = 'all'; }
            else { filters[dim] = 'all'; resetChips(dim); }
            applyFilters();
        }
        function renderPills() {
            if (!pillsBox) return;
            var active = [];
            if (filters.tier !== 'all') active.push(['tier', filterLabel('tier', filters.tier)]);
            if (filters.state !== 'all') active.push(['state', filterLabel('state', filters.state)]);
            if (filters.theme !== 'all') active.push(['theme', filterLabel('theme', filters.theme)]);
            if (searchTerm) active.push(['search', '“' + searchTerm + '”']);
            pillsBox.textContent = '';
            active.forEach(function (a) {
                var pill = document.createElement('button');
                pill.type = 'button';
                pill.className = 'pp-gallery__pill';
                pill.setAttribute('aria-label', 'Remove filter: ' + a[1]);
                var label = document.createElement('span'); label.textContent = a[1];
                var x = document.createElement('span'); x.className = 'pp-gallery__pill-x'; x.setAttribute('aria-hidden', 'true'); x.textContent = '×';
                pill.appendChild(label); pill.appendChild(x);
                pill.addEventListener('click', function () { removeFilter(a[0]); });
                pillsBox.appendChild(pill);
            });
            pillsBox.hidden = active.length === 0;
        }

        // Smart empty state: when a filter combo returns nothing, offer to drop the SINGLE filter whose
        // removal reveals the most badges ("Remove Silver to see 12"), like the Contracts board does.
        var suggestBtn = gal.querySelector('[data-empty-suggest]');
        function countIf(f, term) {
            var n = 0;
            cells.forEach(function (c) { if (elMatches(c, f, term)) n++; });
            return n;
        }
        function bestRelaxation() {
            var out = null;   // [dim, label, count]
            function consider(dim, f, term, label) {
                var n = countIf(f, term);
                if (n > 0 && (!out || n > out[2])) out = [dim, label, n];
            }
            if (filters.tier !== 'all') consider('tier', { tier: 'all', state: filters.state, theme: filters.theme }, searchTerm, filters.tier.charAt(0).toUpperCase() + filters.tier.slice(1));
            if (filters.state !== 'all') consider('state', { tier: filters.tier, state: 'all', theme: filters.theme }, searchTerm, STATE_LABELS[filters.state] || filters.state);
            if (filters.theme !== 'all') consider('theme', { tier: filters.tier, state: filters.state, theme: 'all' }, searchTerm, filterLabel('theme', filters.theme).replace(/^Set: /, ''));
            if (searchTerm) consider('search', filters, '', 'the search');
            return out;
        }
        function updateSuggest(visible) {
            if (!suggestBtn) return;
            var b = visible === 0 ? bestRelaxation() : null;
            if (!b) { suggestBtn.hidden = true; suggestBtn._dim = null; return; }
            suggestBtn.textContent = (b[0] === 'search' ? 'Clear the search to see ' : 'Remove ' + b[1] + ' to see ') + b[2];
            suggestBtn._dim = b[0];
            suggestBtn.hidden = false;
        }
        if (suggestBtn) suggestBtn.addEventListener('click', function () { if (suggestBtn._dim) removeFilter(suggestBtn._dim); });

        var sortSel = gal.querySelector('[data-gallery-sort]');
        var galleryView = root.querySelector('#collection-view-gallery');
        // The caption's second line reflects what you're sorting by: rarity / earned date / progress,
        // defaulting to the rarity flex for the name/tier/set sorts. The medallion already carries
        // tier + state, so this surfaces the ONE stat the object doesn't.
        function statText(cell, key) {
            if (key === 'earned') return cell.getAttribute('data-earned-label') || '';
            if (key === 'progress') {
                var st = cell.getAttribute('data-state');
                if (st === 'earned' || st === 'maintenance') return 'Complete';
                var p = parseFloat(cell.getAttribute('data-progress')) || 0;
                return p ? p + '%' : '';
            }
            var rarity = parseFloat(cell.getAttribute('data-rarity-pct')) || 0;
            return rarity ? 'Top ' + rarity + '%' : '';
        }
        function applySort(animate) {
            var spec = ((sortSel && sortSel.value) || 'series:asc').split(':');
            var reorder = function () { cells.slice().sort(compareBy(spec[0], spec[1] || 'asc')).forEach(function (c) { grid.appendChild(c); }); };
            if (animate) flip(reorder); else reorder();
            cells.forEach(function (c) {
                var el = c.querySelector('[data-gallery-stat]');
                if (el) el.textContent = statText(c, spec[0]);
            });
            syncURL();
        }

        // --- URL state (matches the Career tabs): mirror the Gallery's filters + sort in the URL while it
        //     is the active view, and restore them from a shared / reloaded ?view=gallery link. ---
        function syncURL() {
            if (!window.history || !history.replaceState) return;
            if (!galleryView || galleryView.hasAttribute('hidden')) return;   // only mirror while the Gallery is shown
            var qp = new URLSearchParams(location.search);
            qp.set('view', 'gallery');
            function put(k, v, def) { if (v && v !== def) qp.set(k, v); else qp.delete(k); }
            put('tier', filters.tier, 'all');
            put('state', filters.state, 'all');
            put('set', filters.theme, 'all');
            put('q', searchTerm, '');
            put('sort', sortSel ? sortSel.value : '', 'series:asc');
            var qps = qp.toString();
            history.replaceState(null, '', location.pathname + (qps ? '?' + qps : '') + location.hash);
        }
        function restoreFromURL() {
            var qp = new URLSearchParams(location.search);
            if (qp.get('view') !== 'gallery') return;   // only restore when deep-linked to the Gallery
            var t = qp.get('tier'), s = qp.get('state'), set = qp.get('set'), q = qp.get('q'), so = qp.get('sort');
            if (t && chipHas('tier', t)) { filters.tier = t; activateChip('tier', t); }
            if (s && chipHas('state', s)) { filters.state = s; activateChip('state', s); }
            if (set && optHas(themeSelect, set)) { filters.theme = set; themeSelect.value = set; }
            if (q) { searchTerm = q.toLowerCase().trim(); if (search) search.value = q; }
            if (so && optHas(sortSel, so)) { sortSel.value = so; }
            syncSearchClear();
        }

        // Remember the sort choice across visits (sort only) -- an explicit ?sort= URL wins (restored below).
        var SORT_KEY = 'pp-gallery-sort';
        if (sortSel) {
            try {
                var savedSort = localStorage.getItem(SORT_KEY);
                if (savedSort && optHas(sortSel, savedSort)) sortSel.value = savedSort;
            } catch (e) { /* private mode */ }
            sortSel.addEventListener('change', function () {
                try { localStorage.setItem(SORT_KEY, sortSel.value); } catch (e) { /* noop */ }
                applySort(true);
            });
        }
        restoreFromURL();      // a deep-linked ?view=gallery restores its filters + sort (overrides stored sort)
        applyFilters(false);   // apply any restored filters instantly (visually a no-op if none)
        applySort(false);      // initial / persisted / URL sort + fill the stat

        // Reveal stagger whenever the Gallery view becomes visible (view-switch or first load) -- matches
        // the Case, which staggers on every switch. Restart the class each show so the animation replays;
        // strip it after so its animation-fill can't shadow the FLIP transforms. (No is-touched hack.)
        // On show we also re-sync the URL (the view toggle strips the Gallery params when you leave it).
        var revealT;
        function reveal() {
            if (reduced()) return;
            gal.classList.remove('is-revealing');
            void gal.offsetWidth;   // force reflow so the animation restarts from 0
            gal.classList.add('is-revealing');
            clearTimeout(revealT);
            revealT = setTimeout(function () { gal.classList.remove('is-revealing'); }, 900);
        }
        if (galleryView) {
            if (!galleryView.hasAttribute('hidden')) reveal();
            new MutationObserver(function (muts) {
                for (var i = 0; i < muts.length; i++) {
                    if (muts[i].attributeName === 'hidden' && !galleryView.hasAttribute('hidden')) { reveal(); syncURL(); break; }
                }
            }).observe(galleryView, { attributes: true, attributeFilter: ['hidden'] });
        }

        // ("/" + Cmd/Ctrl+K focus is the shared global shortcut in utils.js, which targets the visible
        //  [data-page-search] -- the gallery search input carries it, so it only fires while Gallery shows.)

        // Arrow-key grid navigation: when a card is focused, move focus across the visible cards (Left/Right
        // step one, Up/Down step a full row). Enter opens it (native <a> activation). Columns are read live
        // from the layout so it works at any breakpoint / filtered set.
        var ARROWS = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: 0, ArrowDown: 0 };
        grid.addEventListener('keydown', function (e) {
            if (!(e.key in ARROWS)) return;
            var focused = document.activeElement;
            if (!focused || !focused.classList || !focused.classList.contains('pp-gallery__card')) return;
            var vis = cells.filter(isShown);
            var idx = vis.indexOf(focused);
            if (idx < 0) return;
            var cols = 1, top0 = vis[0].getBoundingClientRect().top;
            for (var k = 0; k < vis.length; k++) { if (Math.abs(vis[k].getBoundingClientRect().top - top0) < 4) cols = k + 1; else break; }
            var next = (e.key === 'ArrowUp') ? idx - cols : (e.key === 'ArrowDown') ? idx + cols : idx + ARROWS[e.key];
            if (next < 0 || next >= vis.length) return;   // at an edge -- leave the key to the browser
            e.preventDefault();
            vis[next].focus();
        });
    }

    // First-earn "minting" ceremony: the first time you see a newly-earned badge on the collection, it
    // mints in with a flash + light-sweep. "Seen" is tracked per-device in localStorage (no backend); the
    // first-ever visit initialises the set silently so your existing collection doesn't all mint at once.
    var MINT_KEY = 'pp-badges-minted';
    function isEarnedState(s) { return s === 'earned' || s === 'maintenance'; }
    // Play the mint ceremony across a list of medallions, staggered; clears the class after so hover/tilt
    // work again. (The .is-minting CSS is motion-gated, so this is a no-op under reduced motion.)
    function playMint(els) {
        els.forEach(function (el, i) {
            setTimeout(function () {
                el.classList.add('is-minting');
                setTimeout(function () { el.classList.remove('is-minting'); }, 1300);
            }, i * 200);
        });
    }
    // Dev/preview helper (exposed on window.PlatPursuit.Collection.previewMint): replay the ceremony on every
    // earned badge currently on screen, WITHOUT touching the localStorage "seen" set.
    function previewMint() {
        var root = document.querySelector('.pp-collection');
        if (!root) return;
        playMint(Array.prototype.slice.call(root.querySelectorAll('.pp-med[data-badge-id]')).filter(function (el) {
            return isEarnedState(el.getAttribute('data-state')) && el.offsetParent !== null;
        }));
    }
    function initMint(root) {
        if (prefersReducedMotion() || !window.IntersectionObserver) return;
        var meds = Array.prototype.slice.call(root.querySelectorAll('.pp-med[data-badge-id]'));
        var earnedIds = {};
        meds.forEach(function (el) { if (isEarnedState(el.getAttribute('data-state'))) earnedIds[el.getAttribute('data-badge-id')] = true; });

        var stored = null;
        try { stored = JSON.parse(localStorage.getItem(MINT_KEY) || 'null'); } catch (e) { /* corrupt */ }
        if (!Array.isArray(stored)) {   // first-ever visit (or a corrupt/non-array value): seed silently, no mint storm
            try { localStorage.setItem(MINT_KEY, JSON.stringify(Object.keys(earnedIds))); } catch (e) { /* private mode */ }
            return;
        }
        var seen = {};
        stored.forEach(function (id) { seen[id] = true; });
        function markSeen(id) {
            if (seen[id]) return;
            seen[id] = true;
            try { localStorage.setItem(MINT_KEY, JSON.stringify(Object.keys(seen))); } catch (e) { /* private mode */ }
        }
        // Fresh = earned now, badge_id never minted before.
        var fresh = meds.filter(function (el) {
            var id = el.getAttribute('data-badge-id');
            return earnedIds[id] && !seen[id];
        });
        if (!fresh.length) return;

        // Mint each new badge WHEN it scrolls into view, so a fresh badge far down the page (or in another
        // view / set tab) isn't missed -- it stays "unminted" until you actually see it. Marking the badge_id
        // seen the first time ANY of its instances mints means it won't re-play in another view.
        var io = new IntersectionObserver(function (entries) {
            var toMint = [];
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) return;
                var el = entry.target, id = el.getAttribute('data-badge-id');
                io.unobserve(el);
                if (seen[id]) return;   // already minted (another instance / view)
                toMint.push(el);
                markSeen(id);
            });
            if (toMint.length) playMint(toMint);   // staggers if several enter at once
        }, { threshold: 0.4 });
        fresh.forEach(function (el) { io.observe(el); });
    }

    function init() {
        var root = document.querySelector('.pp-collection');
        if (!root) return;
        // Header stats (earned tally + tier composition) count up on load, matched to the completion
        // bar's ~0.85s CSS entrance so the number and the bar rise together -- shared util; no-op if
        // reduced-motion or utils.js hasn't loaded.
        if (window.PlatPursuit && PlatPursuit.countUp) {
            root.querySelectorAll('[data-countup]').forEach(function (el) { PlatPursuit.countUp(el, 850); });
        }
        // Tappable tier stats: jump to the Gallery filtered to that tier ("stats are controls"). Reuses
        // the existing view-chip + filter-chip handlers by clicking them, so no new filter logic here.
        root.querySelectorAll('[data-tier-jump]').forEach(function (el) {
            el.addEventListener('click', function () {
                var tier = el.getAttribute('data-tier-jump');
                var galleryChip = root.querySelector('.pp-switch__chip[data-collection-view="gallery"]');
                if (galleryChip) galleryChip.click();
                var filterChip = root.querySelector('.pp-gallery [data-filter-tier="' + tier + '"]');
                if (filterChip) filterChip.click();
                // Bring the (now-filtered) wall into view so the jump reads as "here are your <tier>s".
                var galleryView = root.querySelector('#collection-view-gallery');
                if (galleryView) {
                    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                    galleryView.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
                }
            });
        });
        initViewToggle(root);
        initCase(root);
        initDetail(root);
        initGallery(root);
        initMint(root);
        var mintBtn = document.querySelector('[data-mint-preview]');   // dev-only replay button (settings.DEBUG)
        if (mintBtn) mintBtn.addEventListener('click', previewMint);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.Collection = { init: init, previewMint: previewMint };
})();
