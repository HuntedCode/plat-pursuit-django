/*
 * Plat Cards (/shareables/) -- page motion, chrome, and the share modal.
 *
 * Filtering (variant radios + search + sort + shovelware) is HTMX-native via browse-filters.js; the
 * variant toggle is a segmented FILTER, not a view island, so switching swaps the grid and keeps the
 * other controls. This owns the header count-up, the staggered reveal, infinite scroll, the settle,
 * the sticky mini-bar, and the share modal.
 *
 * REPLACES share-image.js + shareable-manager.js for this page. Those carried a format toggle
 * (portrait is gone), a theme-nudge flow, per-swatch game-art compositing and their own rating prompt
 * -- ~1,200 lines for a card that now has one format and six grounds. The preview here is the REAL
 * card markup from the HTML endpoint, so preview and download share one template and one theme list
 * and cannot drift.
 *
 * Wired via PlatPursuit.onPageReady(boot): element wiring re-runs on first load AND on HTMX
 * Back/Forward restore; body-level listeners are guarded by `first` so they bind once.
 */
(function () {
    var PP = window.PlatPursuit || {};

    var scroller = null, revealHandle = null, handledGrid = null, countLast = null;
    var mbSearch = null, mbSort = null;
    var dlg = null, current = null, reqToken = 0;

    // ── Grid motion ───────────────────────────────────────────────────────────────────────────────
    function initReveal() {
        if (revealHandle) { revealHandle.disconnect(); revealHandle = null; }
        var grid = document.getElementById('items-grid');
        if (!grid || !PP.staggerReveal) { return; }
        revealHandle = PP.staggerReveal({
            grid: grid, cardSelector: '.pcard', step: 20,
            reveal: function (el, delayMs) {
                if (!el.animate) { return; }
                el.animate([{ opacity: 0 }, { opacity: 1 }],
                           { duration: 400, delay: delayMs, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: 'backwards' });
                el.animate([{ transform: 'translateY(12px) scale(0.97)' }, { transform: 'none' }],
                           { duration: 480, delay: delayMs, easing: 'cubic-bezier(0.34, 1.4, 0.64, 1)', fill: 'backwards' });
            },
        });
    }
    function initScroller() {
        if (scroller && scroller.destroy) { scroller.destroy(); scroller = null; }
        if (!PP.InfiniteScroller) { return; }
        scroller = PP.InfiniteScroller.create({
            gridId: 'items-grid', sentinelId: 'pc-sentinel', loadingId: 'pc-loading',
            paginateBy: 24, cardSelector: '.pcard',      // matches PlatCardsView.paginate_by
            onAppend: function (nodes) { if (revealHandle) { revealHandle.observe(nodes); } },
        });
    }
    function tickCount(grid) {
        if (!grid) { return; }
        var newVal = parseFloat(grid.getAttribute('data-result-count'));
        var mbCount = document.querySelector('[data-minibar-count]');
        if (mbCount && !isNaN(newVal)) { mbCount.textContent = newVal.toLocaleString(); }
        countLast = newVal;
    }

    // ── Mini-bar proxies (mirror the real toolbar; the toolbar survives grid swaps) ────────────────
    function realSearch() { var f = document.getElementById('pc-form'); return f ? f.querySelector('input[name="query"]') : null; }
    function realSort() { var f = document.getElementById('pc-form'); return f ? f.querySelector('select[name="sort"]') : null; }
    function wireMinibar() {
        mbSearch = document.querySelector('[data-minibar-search]');
        mbSort = document.querySelector('[data-minibar-sort]');
        if (mbSearch) {
            mbSearch.addEventListener('input', function () {
                var real = realSearch();
                if (real) { real.value = mbSearch.value; real.dispatchEvent(new Event('input', { bubbles: true })); }
            });
            mbSearch.addEventListener('keydown', function (e) {
                if (e.key !== 'Enter') { return; }
                e.preventDefault();
                var real = realSearch();
                if (real) { real.value = mbSearch.value; real.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); }
            });
            var rs = realSearch();
            if (rs) { rs.addEventListener('input', function () { if (document.activeElement !== mbSearch) { mbSearch.value = rs.value; } }); }
        }
        if (mbSort) {
            var real = realSort();
            if (real) {
                mbSort.innerHTML = real.innerHTML; mbSort.value = real.value;
                real.addEventListener('change', function () { mbSort.value = real.value; });
            }
            mbSort.addEventListener('change', function () {
                var r = realSort();
                if (r) { r.value = mbSort.value; r.dispatchEvent(new Event('change', { bubbles: true })); }
            });
        }
    }

    // ── Share modal ───────────────────────────────────────────────────────────────────────────────
    var themeData = null;
    function cardThemes() {
        if (themeData) { return themeData; }
        var el = document.getElementById('pc-theme-data');
        try { themeData = el ? JSON.parse(el.textContent) : {}; } catch (err) { themeData = {}; }
        return themeData;
    }

    function theme() {
        var picked = dlg && dlg.querySelector('[data-share-theme]:checked');
        return picked ? picked.value : '';
    }

    // The card renders at a fixed 1200x630; scale it to whatever the frame is, so the preview keeps
    // the artifact's real proportions instead of reflowing into a different layout.
    function fit() {
        var frame = dlg && dlg.querySelector('[data-share-frame]');
        var scaler = dlg && dlg.querySelector('[data-share-preview]');
        if (!frame || !scaler) { return; }
        var scale = Math.min(1, frame.clientWidth / 1200);
        scaler.style.transform = 'scale(' + scale + ')';
        frame.style.height = Math.round(630 * scale) + 'px';
    }

    function setBusy(on) {
        var l = dlg && dlg.querySelector('[data-share-loading]');
        if (l) { l.hidden = !on; }
    }
    function showError(msg) {
        var e = dlg && dlg.querySelector('[data-share-error]');
        if (!e) { return; }
        e.hidden = !msg;
        e.textContent = msg || '';
    }

    function loadPreview() {
        if (!current || !dlg) { return; }
        var token = ++reqToken;                 // stale-response guard: theme clicks can outrun fetches
        setBusy(true); showError('');
        PP.API.request('/api/v1/shareables/completion/' + current.groupId + '/html/')
            .then(function (data) {
                if (token !== reqToken) { return; }
                var scaler = dlg.querySelector('[data-share-preview]');
                if (scaler) { scaler.innerHTML = data.html; }
                current.hasRating = !!data.has_rating;
                current.conceptId = data.concept_id;
                current.variant = data.variant;
                var label = dlg.querySelector('[data-share-download-label]');
                if (label) { label.textContent = data.variant === 'platinum' ? 'Download platinum card' : 'Download 100% card'; }
                fit();
            })
            .catch(function () {
                if (token !== reqToken) { return; }
                showError("Couldn't build that card. Try again in a moment.");
            })
            .finally(function () { if (token === reqToken) { setBusy(false); } });
    }

    // The ground is applied server-side at download time, so switching it only needs to restyle the
    // preview -- no refetch. The card's own scrim is an inner layer, so it survives this.
    function applyTheme() {
        var scaler = dlg && dlg.querySelector('[data-share-preview]');
        var card = scaler && scaler.querySelector('.share-image-content');
        var picked = dlg && dlg.querySelector('[data-share-theme]:checked');
        if (!card || !picked) { return; }
        var def = cardThemes()[picked.value];
        if (!def) { return; }
        if (def.is_game_art) {
            var src = def.source === 'game_image' ? current.gameImage : current.conceptBg;
            card.style.background = src
                ? 'linear-gradient(rgba(5, 8, 12, 0.62), rgba(5, 8, 12, 0.62)), url("' + src + '")'
                : def.background;
            card.style.backgroundSize = 'cover';
            card.style.backgroundPosition = 'center';
        } else {
            card.style.background = def.background;
            card.style.backgroundSize = '';
            card.style.backgroundPosition = '';
        }
    }

    function open(trigger) {
        if (!dlg) { return; }
        current = {
            groupId: trigger.dataset.trophyGroupId,
            gameName: trigger.dataset.gameName || '',
            gameImage: trigger.dataset.gameImage || '',
            conceptBg: trigger.dataset.conceptBgUrl || '',
            conceptId: trigger.dataset.conceptId || '',
        };
        var sub = dlg.querySelector('[data-share-game]');
        if (sub) { sub.textContent = current.gameName; }
        var scaler = dlg.querySelector('[data-share-preview]');
        if (scaler) { scaler.innerHTML = ''; }
        if (!dlg.open) { dlg.showModal(); }
        loadPreview();
    }

    function pngUrl() {
        return '/api/v1/shareables/completion/' + current.groupId + '/png/?theme=' + encodeURIComponent(theme());
    }

    // Rate-before-download. The card carries the hunter's own stars, difficulty and fun, so an unrated
    // game makes a visibly thinner card -- which is the honest reason to ask, and why the prompt only
    // ever offers, never blocks. Asked at most once per opened card (`asked`), so it can't nag.
    //
    // Implemented against the shared #rate-before-download-modal directly. share-image.js had its own
    // copy of this flow, but that file existed only for the page this one replaces, so extracting a
    // module for a single caller would be ceremony.
    var asked = false;

    function submitRating(modal) {
        var form = modal.querySelector('#rbd-rating-form');
        if (!form) { return Promise.resolve(); }
        var payload = {};
        new FormData(form).forEach(function (v, k) { payload[k] = v; });
        return PP.API.post('/api/v1/ratings/' + current.conceptId + '/group/default/rate/', payload);
    }

    function promptRating(then) {
        var modal = document.getElementById('rate-before-download-modal');
        if (!modal || !modal.showModal) { then(); return; }
        asked = true;

        var title = modal.querySelector('#rbd-game-title');
        if (title) { title.textContent = current.gameName; }
        var submit = modal.querySelector('#rbd-submit-btn');
        var skip = modal.querySelector('#rbd-skip-btn');
        if (submit) { submit.disabled = false; }

        function finish(didRate) {
            modal.removeEventListener('close', onClose);
            if (submit) { submit.removeEventListener('click', onSubmit); }
            if (skip) { skip.removeEventListener('click', onSkip); }
            if (modal.open) { modal.close(); }
            // A fresh rating changes what the card shows, so rebuild the preview before handing over.
            if (didRate) { current.hasRating = true; loadPreview(); }
            then();
        }
        function onSubmit(e) {
            e.preventDefault();
            if (submit) { submit.disabled = true; }
            submitRating(modal).then(function () { finish(true); }).catch(function () { finish(false); });
        }
        function onSkip(e) { e.preventDefault(); finish(false); }
        function onClose() { finish(false); }        // Esc / backdrop counts as skip

        if (submit) { submit.addEventListener('click', onSubmit); }
        if (skip) { skip.addEventListener('click', onSkip); }
        modal.addEventListener('close', onClose);
        modal.showModal();
    }

    function download() {
        if (!current) { return; }
        if (!current.hasRating && current.conceptId && !asked) {
            promptRating(function () { window.location.href = pngUrl(); });
            return;
        }
        window.location.href = pngUrl();
    }

    function close() { if (dlg && dlg.open) { dlg.close(); } }

    function wireModal(first) {
        dlg = document.getElementById('pc-share');
        if (!dlg) { return; }
        if (!first) { return; }        // the dialog lives outside the swap, so wire it exactly once

        dlg.addEventListener('click', function (e) {
            if (e.target === dlg) { close(); }                       // backdrop
            if (e.target.closest('[data-share-close]')) { close(); }
            if (e.target.closest('[data-share-download]')) { download(); }
        });
        dlg.addEventListener('change', function (e) {
            if (e.target.matches('[data-share-theme]')) { applyTheme(); }
        });
        dlg.addEventListener('close', function () { current = null; asked = false; });
        window.addEventListener('resize', fit);

        // Delegated so cards appended by infinite scroll work without re-binding.
        document.body.addEventListener('click', function (e) {
            var trigger = e.target.closest('[data-card-open]');
            if (trigger) { open(trigger); }
        });
    }

    // Deep links. `?c=` is the native form; `?et=` is what platinum-earned notifications already in
    // the wild send, and it resolves through the legacy alias to the same card.
    function autoOpen() {
        var params = new URLSearchParams(window.location.search);
        var groupId = params.get('c');
        var et = params.get('et');
        if (!groupId && !et) { return; }
        var selector = groupId
            ? '[data-card-open][data-trophy-group-id="' + CSS.escape(groupId) + '"]'
            : null;
        var trigger = selector && document.querySelector(selector);
        if (trigger) {
            open(trigger);
        } else if (et) {
            // The notification knows an EarnedTrophy, not a completion, and its game may not be on
            // page 1 of the grid -- so resolve it through the alias rather than hunting the DOM.
            dlg = document.getElementById('pc-share');
            PP.API.request('/api/v1/shareables/platinum/' + encodeURIComponent(et) + '/html/')
                .then(function (data) {
                    var el = document.querySelector('[data-card-open][data-trophy-group-id="' + CSS.escape(String(data.trophy_group_id)) + '"]');
                    if (el) { open(el); return; }
                    current = { groupId: data.trophy_group_id, gameName: '', gameImage: '', conceptBg: '', conceptId: data.concept_id };
                    if (dlg && !dlg.open) { dlg.showModal(); }
                    loadPreview();
                })
                .catch(function () { /* stale notification link; leave the page as-is */ });
        }
        // Clean the param so a refresh doesn't reopen the modal.
        params.delete('c'); params.delete('et');
        var qs = params.toString();
        window.history.replaceState({}, '', window.location.pathname + (qs ? '?' + qs : ''));
    }

    // ── Body-level listeners (bound once; body survives a history restore) ────────────────────────
    function onAfterSwap(e) {
        var t = (e.detail && e.detail.target) || e.target;
        if (!t || t.id !== 'browse-results') { return; }
        var grid = t.querySelector('#items-grid');
        if (grid && grid === handledGrid) { return; }
        handledGrid = grid;
        t.classList.remove('is-swapping');
        tickCount(grid);
        initReveal();
        initScroller();
    }
    function onFormChangeDim(e) {
        var t = e.target;
        if (t && (t.type === 'text' || t.type === 'search')) { return; }
        var r = document.getElementById('browse-results');
        if (r) { r.classList.add('is-swapping'); }
    }
    function onAfterRequest(e) {
        var elt = e.detail && e.detail.elt;
        if (!elt || elt.id !== 'pc-form') { return; }
        var r = document.getElementById('browse-results');
        if (r) { r.classList.remove('is-swapping', 'pointer-events-none'); }
    }

    function boot(first) {
        handledGrid = null;
        if (first && PP.countUp) {
            document.querySelectorAll('.scard__value[data-countup]').forEach(function (el) { PP.countUp(el, 900); });
        }
        var form = document.getElementById('pc-form');
        if (form) { form.addEventListener('change', onFormChangeDim); }
        wireMinibar();
        initReveal();
        initScroller();
        wireModal(first);
        if (PP.StickyReveal) { PP.StickyReveal.init(); }
        if (first) {
            document.body.addEventListener('htmx:afterSwap', onAfterSwap);
            document.body.addEventListener('htmx:afterRequest', onAfterRequest);
            autoOpen();
        }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
