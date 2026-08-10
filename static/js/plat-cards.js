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
 * -- ~1,200 lines for a card that now has one format and a curated handful of grounds. The preview here is the REAL
 * card markup from the HTML endpoint, so preview and download share one template and one theme list
 * and cannot drift.
 *
 * Wired via PlatPursuit.onPageReady(boot): element wiring re-runs on first load AND on HTMX
 * Back/Forward restore; body-level listeners are guarded by `first` so they bind once.
 */
(function () {
    var PP = window.PlatPursuit || {};

    var scroller = null, revealHandle = null, handledGrid = null;
    var mbSearch = null, mbSort = null;
    var dlg = null, current = null, reqToken = 0;

    var REDUCE = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // The house "page steps back" cue, same as the Game Detail modals and the claim ceremony use
    // (#page-recede inside #zoom-container). Pivoting on the viewport centre keeps it a scale-in-place
    // at any scroll depth instead of yanking the page toward its own top.
    function pageRecede(on) {
        if (REDUCE) { return; }
        var zoom = document.getElementById('zoom-container');
        var pr = document.getElementById('page-recede');
        if (on && pr) {
            pr.style.transformOrigin = '50% ' + (window.innerHeight / 2 - pr.getBoundingClientRect().top) + 'px';
        }
        if (zoom) { zoom.classList.toggle('pp-receded', on); }
    }

    // A preview is stable for a given completion, so a card fetched once is reused -- which is what
    // makes hover prefetching worth doing rather than just noisy.
    //
    // "Stable", NOT immutable: the card renders the hunter's own rating (stars, difficulty, grind, fun,
    // their blurb), so rating a game changes it. This cache used to say immutable and had no invalidation
    // at all, which is why the preview kept showing the pre-rating card after a save -- loadPreview()
    // re-ran and was handed the stale entry straight back.
    var previewCache = Object.create(null);
    var inflight = Object.create(null);

    function invalidatePreview(groupId) {
        delete previewCache[groupId];
        delete inflight[groupId];
    }

    function fetchPreview(groupId) {
        if (previewCache[groupId]) { return Promise.resolve(previewCache[groupId]); }
        if (inflight[groupId]) { return inflight[groupId]; }
        var p = PP.API.request('/api/v1/shareables/completion/' + groupId + '/html/')
            .then(function (data) { previewCache[groupId] = data; delete inflight[groupId]; return data; })
            .catch(function (err) { delete inflight[groupId]; throw err; });
        inflight[groupId] = p;
        return p;
    }

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

    // -- Share modal ------------------------------------------------------------------------------
    var themeData = null;
    function cardThemes() {
        if (themeData) { return themeData; }
        var el = document.getElementById('pc-theme-data');
        try { themeData = el ? JSON.parse(el.textContent) : {}; } catch (err) { themeData = {}; }
        return themeData;
    }

    function picked() { return dlg && dlg.querySelector('[data-share-theme]:checked'); }
    function theme() { var p = picked(); return p ? p.value : ''; }
    function artIndex() {
        var p = picked();
        return p && p.dataset.artIndex ? p.dataset.artIndex : null;
    }
    function firstGround() { return dlg && dlg.querySelector('[data-share-theme]'); }

    // Art swatches are per-card: how many a game has, and what they look like, isn't known until one
    // is opened. Rebuilding them wipes the checked radio if an art ground was selected -- they're one
    // document-scoped group -- which left NOTHING checked, so picked() returned null, applyTheme()
    // bailed, and the choice silently reverted. Fall back to the first fixed ground when that happens.
    function buildArtSwatches(options) {
        var slot = dlg && dlg.querySelector('[data-share-art-slot]');
        if (!slot) { return; }
        var lostSelection = !!slot.querySelector('[data-share-theme]:checked');
        slot.innerHTML = '';
        (options || []).forEach(function (url, i) {
            var label = document.createElement('label');
            label.className = 'pc-theme pc-theme--art';
            var name = options.length > 1 ? 'Art ' + (i + 1) : 'Game Art';
            label.innerHTML =
                '<input type="radio" name="pc-theme" value="ppArt" class="sr-only" data-share-theme data-art-index="' + i + '" />' +
                '<span class="pc-theme__swatch" aria-hidden="true"></span>' +
                '<span class="pc-theme__name"></span>';
            label.querySelector('.pc-theme__name').textContent = name;
            // The swatch IS the image, so the choice is visible before it's made.
            label.querySelector('.pc-theme__swatch').style.backgroundImage = 'url("' + url + '")';
            var input = label.querySelector('[data-share-theme]');
            input.dataset.artUrl = url;
            input.setAttribute('aria-label', name);
            slot.appendChild(label);
        });
        if (lostSelection) {
            var fallback = firstGround();
            if (fallback) { fallback.checked = true; }
        }
    }

    // The card renders at a fixed 1200x630; scale it to whatever room the modal has, so the preview
    // keeps the artifact's real proportions instead of reflowing into a different layout.
    //
    // Bounded by HEIGHT as well as width, and that is the whole point: scaling on width alone let the
    // preview claim its full height no matter how little was left, so every extra row of grounds made
    // the box taller until it scrolled. The picker must never be scrolled to or collapsed behind a
    // "more" control -- every ground stays on screen -- so the preview is what gives way.
    //
    // The budget is computed from the VIEWPORT and the chrome, never from the box's current height:
    // the box is sized by its content, so measuring it here would feed this function its own last
    // answer. Header and controls are independent of the frame, so they can be measured directly.
    // Must match .pc-modal__box's max-height in plat-cards.css. If CSS drops below this the box hits the
    // smaller cap while JS still budgets 92vh, and since the box is `overflow: hidden` the controls are
    // clipped with no scrollbar -- a silent failure. Pinned by test_the_modal_height_budget_matches_css.
    var BOX_VH = 0.92;
    function fit() {
        // The resize listener is bound for the page's life, but a closed <dialog> is display:none, so
        // clientWidth is 0 and this would write scale(0) onto hidden nodes on every resize event.
        if (!dlg || !dlg.open) { return; }
        var frame = dlg.querySelector('[data-share-frame]');
        var scaler = dlg.querySelector('[data-share-preview]');
        if (!frame || !scaler) { return; }

        // Reset the width BEFORE measuring. fit() narrows the frame at the end, so reading clientWidth
        // without clearing it first would feed each run the previous run's answer and ratchet the card
        // smaller on every resize -- the same self-referential trap as measuring the box's own height.
        frame.style.width = '';
        var scale = Math.min(1, frame.clientWidth / 1200);
        var stage = frame.parentElement;
        var head = dlg.querySelector('.pc-modal__head');
        var controls = dlg.querySelector('.pc-modal__controls');
        var err = dlg.querySelector('[data-share-error]');
        if (stage && head && controls) {
            var pad = window.getComputedStyle(stage);
            var chrome = head.offsetHeight + controls.offsetHeight
                + parseFloat(pad.paddingTop) + parseFloat(pad.paddingBottom)
                + 2;                                        // the box's 1px borders
            // The error line is an in-flow sibling of the frame, so it eats the stage's room when shown.
            if (err && !err.hidden) { chrome += err.offsetHeight; }
            var room = (window.innerHeight * BOX_VH) - chrome;
            // Clamped at 0, NOT at a minimum preview size. A floor here looks harmless and is not: the box
            // is `overflow: hidden` and the frame carries an inline height, so any floor above the room
            // available makes the preview PAINT OVER the swatch row -- measured at 57-82px of overlap on
            // landscape phones, where the picker became unreachable. That is strictly worse than the
            // scrollbar this whole function exists to remove. The card yields all the way to nothing
            // before a single ground goes off screen.
            scale = Math.max(0, Math.min(scale, room / 630));
        }

        scaler.style.transform = 'scale(' + scale + ')';
        frame.style.height = Math.round(630 * scale) + 'px';
        // Width too, or a height-bound card sits flush-left (transform-origin: top left) in a full-width
        // frame and opens a dead --pp-bg-0 gutter down the right. Height binds on every short viewport now,
        // so this is the common case rather than the edge one.
        frame.style.width = Math.round(1200 * scale) + 'px';
    }

    function setBusy(on) {
        var l = dlg && dlg.querySelector('[data-share-loading]');
        var stage = dlg && dlg.querySelector('[data-share-frame]');
        if (l) { l.hidden = !on; }
        if (stage) { stage.setAttribute('aria-busy', on ? 'true' : 'false'); }
        previewBlocked = !!on;                 // nothing to download until the card exists
        syncDownloadEnabled();
    }
    function showError(msg) {
        var e = dlg && dlg.querySelector('[data-share-error]');
        if (!e) { return; }
        e.hidden = !msg;
        e.textContent = msg || '';
        if (msg) { previewBlocked = true; syncDownloadEnabled(); }
    }

    // Two things independently want the download button disabled -- the preview still loading (or
    // failed), and a download already in flight -- so `disabled` is derived from both rather than
    // written by each. They used to race: a theme swap re-disabled the button while the "Saved" revert
    // timer was still queued to re-enable it, and whichever fired last won.
    var previewBlocked = false, downloading = false;
    function syncDownloadEnabled() {
        var go = dlg && dlg.querySelector('[data-share-download]');
        if (go) { go.disabled = previewBlocked || downloading; }
    }

    function loadPreview() {
        if (!current || !dlg) { return; }
        var token = ++reqToken;                 // stale-response guard: clicks can outrun fetches
        setBusy(true); showError('');
        fetchPreview(current.groupId)
            .then(function (data) {
                if (token !== reqToken) { return; }
                var scaler = dlg.querySelector('[data-share-preview]');
                if (scaler) { scaler.innerHTML = data.html; scaler.classList.add('is-in'); }
                current.hasRating = !!data.has_rating;
                current.conceptId = data.concept_id;
                current.playtime = data.playtime || '';
                current.rating = data.user_rating || null;
                buildArtSwatches(data.art_options);
                syncRateButton();
                var label = dlg.querySelector('[data-share-download-label]');
                if (label) { label.textContent = data.variant === 'platinum' ? 'Download platinum card' : 'Download 100% card'; }
                // The card ships with its own inline ground; paint the SELECTED one over it straight
                // away so the preview matches the checked radio before anyone touches the picker.
                applyTheme();
                fit();
            })
            .catch(function () {
                if (token !== reqToken) { return; }
                showError("Couldn't build that card. Try again in a moment.");
            })
            .finally(function () { if (token === reqToken) { setBusy(false); } });
    }

    // The ground is applied server-side at download time, so switching it only restyles the preview --
    // no refetch. The card's own scrim is an inner layer, so it survives this.
    function applyTheme() {
        var scaler = dlg && dlg.querySelector('[data-share-preview]');
        var card = scaler && scaler.querySelector('.share-image-content');
        var choice = picked();
        if (!card || !choice) { return; }
        var def = cardThemes()[choice.value];
        if (!def) { return; }
        if (def.is_game_art) {
            var src = choice.dataset.artUrl || '';
            // 0.45 matches what the renderer composites server-side, so preview and download agree on
            // how heavily the art is knocked back.
            card.style.background = src
                ? 'linear-gradient(rgba(0, 0, 0, 0.45), rgba(0, 0, 0, 0.45)), url("' + src + '")'
                : def.background;
            card.style.backgroundSize = 'cover';
            card.style.backgroundPosition = 'center';
        } else {
            card.style.background = def.background;
            card.style.backgroundSize = '';
            card.style.backgroundPosition = '';
        }
    }

    function openFor(groupId, gameName) {
        current = { groupId: groupId, gameName: gameName || '', conceptId: '', hasRating: true,
                    playtime: '', rating: null };
        var sub = dlg.querySelector('[data-share-game]');
        if (sub) { sub.textContent = current.gameName; }
        setDownloadState('idle');       // never greet a new card with the previous one's "Saved"
        // Hidden until the card loads: there is nothing to rate yet, and the PREVIOUS card's label
        // would otherwise sit there offering to edit a rating that belongs to a different game.
        syncRateButton();
        var scaler = dlg.querySelector('[data-share-preview]');
        if (scaler) { scaler.innerHTML = ''; scaler.classList.remove('is-in'); }
        // Clear the PREVIOUS card's art options immediately. Leaving them up during the fetch let a
        // download go out with art=<i> indexed against the old game's list, which the server clamps
        // into range -- quietly shipping the wrong image.
        buildArtSwatches([]);
        var ground = firstGround();
        if (ground) { ground.checked = true; }
        if (!dlg.open) { dlg.showModal(); pageRecede(true); }
        fit();                                  // size the stage before the fetch, so it doesn't pop
        loadPreview();
    }
    function open(trigger) {
        if (!dlg) { return; }
        openFor(trigger.dataset.trophyGroupId, trigger.dataset.gameName);
    }

    function pngUrl() {
        var url = '/api/v1/shareables/completion/' + current.groupId + '/png/?theme=' + encodeURIComponent(theme());
        var i = artIndex();
        return i === null ? url : url + '&art=' + encodeURIComponent(i);
    }

    // Fetch-then-save rather than navigating. location.href is fine while the endpoint returns an
    // attachment, but its failure paths don't: a render error returns JSON and the 20/m rate limit
    // returns an HTML 403, either of which would replace the page with a bare error document and take
    // the open modal with it.
    // Download button state machine. The PNG is rendered by headless Chromium on the server and takes
    // a beat, so `busy` is the load-bearing state here: without it the button just goes inert and the
    // click reads as having done nothing. `done` then confirms the save, which is otherwise invisible
    // from the page -- the file lands in a folder we can't see.
    var revertTimer = null;
    var DL_LABELS = { idle: 'Download', busy: 'Processing...', done: 'Saved' };

    function setDownloadState(state) {
        clearTimeout(revertTimer);
        var go = dlg && dlg.querySelector('[data-share-download]');
        var label = dlg && dlg.querySelector('[data-share-download-label]');
        if (!go) { return; }
        go.classList.toggle('is-busy', state === 'busy');
        go.classList.toggle('is-done', state === 'done');
        downloading = state === 'busy';
        syncDownloadEnabled();
        if (label) { label.textContent = DL_LABELS[state]; }
        // Swapping the label mid-press is a change a screen reader should hear; the button is the only
        // progress indicator, so it has to announce like one.
        go.setAttribute('aria-busy', state === 'busy' ? 'true' : 'false');
        if (state === 'done') { revertTimer = setTimeout(function () { setDownloadState('idle'); }, 2400); }
    }

    function saveCard() {
        setDownloadState('busy');
        showError('');
        fetch(pngUrl(), { credentials: 'same-origin' })
            .then(function (res) {
                if (!res.ok) { throw new Error(String(res.status)); }
                return res.blob();
            })
            .then(function (blob) {
                var a = document.createElement('a');
                var href = URL.createObjectURL(blob);
                a.href = href;
                a.download = (current.gameName || 'plat-card').replace(/[^A-Za-z0-9 _-]/g, '').trim() + '.png';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                setTimeout(function () { URL.revokeObjectURL(href); }, 1000);
                setDownloadState('done');
                // Second, louder confirmation. ToastManager routes this into the modal's own
                // .modal-toast-container (a top-layer popover) rather than the page container, so it
                // lands ABOVE the dialog instead of behind its backdrop.
                if (PP.ToastManager) { PP.ToastManager.show('Card saved to your downloads.', 'success', 3200); }
            })
            .catch(function (err) {
                setDownloadState('idle');
                showError(err && err.message === '403'
                    ? 'Too many cards at once. Give it a minute.'
                    : "Couldn't render that card. Try again in a moment.");
            });
    }

    // Rate-before-download. The card carries the hunter's own stars, difficulty and fun, so an unrated
    // game makes a visibly thinner card -- the honest reason to ask, and why this only ever offers.
    // Asked at most once per opened card.
    //
    // Driven against the shared #rate-before-download-modal. dashboard.js drives the same partial and
    // is NOT loaded on this page, so everything it does for that modal has to happen here too: a reset
    // between opens, live slider readouts, and the hours gate -- the form rejects a blank or zero
    // hours_to_platinum with a 400, which is exactly why the template ships the submit disabled.
    var asked = false;
    var RBD_READOUTS = {
        overall_rating: 'rbd-overall-value',
        difficulty: 'rbd-difficulty-value',
        grindiness: 'rbd-grindiness-value',
        fun_ranking: 'rbd-fun-value',
    };

    function syncReadouts(form) {
        Object.keys(RBD_READOUTS).forEach(function (name) {
            var input = form.querySelector('[name="' + name + '"]');
            var out = document.getElementById(RBD_READOUTS[name]);
            if (input && out) {
                out.textContent = name === 'overall_rating'
                    ? parseFloat(input.value).toFixed(1) : input.value;
            }
        });
    }

    // The card's stats ARE the hunter's rating, so let them fix it without leaving the modal. Hidden
    // until a card is loaded (there's nothing to rate before then), and relabelled once one exists --
    // "Rate this game" and "Edit rating" are different offers.
    function syncRateButton() {
        var btn = dlg && dlg.querySelector('[data-share-rate]');
        var label = dlg && dlg.querySelector('[data-share-rate-label]');
        if (!btn) { return; }
        btn.hidden = !(current && current.conceptId);
        if (label) { label.textContent = current && current.hasRating ? 'Edit rating' : 'Rate this game'; }
    }

    // Prefill from the hunter's existing scores. Without this an "edit" opens on the form's defaults and
    // saving silently overwrites real scores with 3/5/5/5 -- an edit control that destroys the thing it
    // claims to edit. `blurb` is deliberately not touched: the form has no field for it, so a round-trip
    // through here must not be able to clear it (the API only writes the keys it's sent).
    function prefill(form, rating) {
        if (!rating) { return; }
        ['overall_rating', 'difficulty', 'grindiness', 'fun_ranking', 'hours_to_platinum'].forEach(function (name) {
            var input = form.querySelector('[name="' + name + '"]');
            if (input && rating[name] !== null && rating[name] !== undefined) { input.value = rating[name]; }
        });
    }

    function promptRating(proceed, opts) {
        var edit = !!(opts && opts.edit);
        var modal = document.getElementById('rate-before-download-modal');
        var form = modal && modal.querySelector('#rbd-rating-form');
        if (!modal || !form || !modal.showModal) { proceed(); return; }
        // Only the DOWNLOAD prompt is once-per-card. An explicit edit must never consume that, or opening
        // the editor would silence the prompt for a hunter who then skipped rating.
        if (!edit) { asked = true; }

        var title = modal.querySelector('#rbd-game-title');
        if (title) { title.textContent = current.gameName || 'Rate this game'; }
        form.reset();                       // otherwise card B opens on card A's slider positions
        if (edit) { prefill(form, current.rating); }
        syncReadouts(form);

        var hint = document.getElementById('rbd-playtime-hint');
        if (hint) {
            hint.textContent = current.playtime ? 'Your tracked playtime: ' + current.playtime : '';
            hint.classList.toggle('hidden', !current.playtime);
        }

        var submit = modal.querySelector('#rbd-submit-btn');
        var skip = modal.querySelector('#rbd-skip-btn');
        var hours = form.querySelector('[name="hours_to_platinum"]');
        // The shared modal is written for the download prompt ("Rate and Download" / "Skip, just
        // download"). Neither is true of an explicit edit -- nothing downloads -- so both are relabelled
        // and the skip becomes a plain cancel. Restored on close, since the same element serves both.
        var submitLabel = submit && submit.querySelector('[data-rbd-submit-label]');
        if (submitLabel) { submitLabel.textContent = edit ? 'Save rating' : 'Rate and Download'; }
        if (skip) { skip.textContent = edit ? 'Cancel' : 'Skip, just download'; }
        var blurb = modal.querySelector('#rbd-prompt-copy');
        if (blurb) { blurb.hidden = edit; }
        // Prefilled hours already satisfy the gate; leaving it disabled would make an edit look broken.
        if (submit) { submit.disabled = !(parseInt(hours && hours.value, 10) >= 1); }

        function onInput() { syncReadouts(form); }
        function onHours() { if (submit) { submit.disabled = !(parseInt(hours.value, 10) >= 1); } }
        function cleanup() {
            form.removeEventListener('input', onInput);
            if (hours) { hours.removeEventListener('input', onHours); }
            if (submit) { submit.removeEventListener('click', onSubmit); }
            if (skip) { skip.removeEventListener('click', onSkip); }
            modal.removeEventListener('close', onDismiss);
        }
        // A DISMISS is not a skip. The close button and the backdrop are both form method="dialog", so
        // both fire `close` -- treating that as "skip, then download" meant the universal
        // get-me-out-of-here affordance handed you a file. Only the explicit buttons continue.
        function finish(didRate, andDownload) {
            cleanup();
            if (modal.open) { modal.close(); }
            if (didRate) {
                current.hasRating = true;
                // The card RENDERS the rating, so the cached copy is now the wrong card. Without this
                // loadPreview() is handed the stale entry straight back and the preview never changes --
                // the reload was always here, the invalidation was not.
                invalidatePreview(current.groupId);
                loadPreview();
            }
            if (andDownload) { proceed(); }
        }
        function onSubmit(e) {
            e.preventDefault();
            if (!(parseInt(hours && hours.value, 10) >= 1)) { return; }
            submit.disabled = true;
            var payload = {};
            new FormData(form).forEach(function (v, k) { payload[k] = v; });
            PP.API.post('/api/v1/ratings/' + current.conceptId + '/group/default/rate/', payload)
                .then(function () { finish(true, true); })
                .catch(function () {
                    // Don't hand over a download while pretending the rating saved.
                    submit.disabled = false;
                    if (PP.ToastManager) { PP.ToastManager.error("Couldn't save that rating. Try again, or skip."); }
                });
        }
        // In edit mode the secondary button is a CANCEL, so it must not fall through to a download.
        function onSkip(e) { e.preventDefault(); finish(false, !edit); }
        function onDismiss() { finish(false, false); }

        form.addEventListener('input', onInput);
        if (hours) { hours.addEventListener('input', onHours); }
        if (submit) { submit.addEventListener('click', onSubmit); }
        if (skip) { skip.addEventListener('click', onSkip); }
        modal.addEventListener('close', onDismiss);
        modal.showModal();
    }

    function download() {
        if (!current) { return; }
        if (!current.hasRating && current.conceptId && !asked) { promptRating(saveCard); return; }
        saveCard();
    }

    // Exits are choreographed as carefully as entrances: a native close() is instant, which reads as
    // the modal being yanked away. Play the exit, then close for real. The page un-recedes on the same
    // beat so the two read as one movement.
    function close() {
        if (!dlg || !dlg.open || dlg.classList.contains('is-closing')) { return; }
        pageRecede(false);
        if (REDUCE) { dlg.close(); return; }
        dlg.classList.add('is-closing');
        var done = false;
        function finish() {
            if (done) { return; }
            done = true;
            dlg.classList.remove('is-closing');
            dlg.close();
        }
        dlg.addEventListener('animationend', finish, { once: true });
        setTimeout(finish, 260);                // the animation may never fire (hidden tab, etc.)
    }

    function wireModal(first) {
        dlg = document.getElementById('pc-share');
        if (!dlg) { return; }

        // Bound EVERY boot, not only the first. The dialog is outside the HTMX filter swap, but NOT
        // outside a history restore: base.html sets no hx-history-elt, so htmx replaces document.body
        // wholesale and this is a fresh, unwired node. Guarding these behind `first` left the modal
        // opening after a browser Back with Close, Download and every swatch inert. Listeners die with
        // the node they're on, so re-binding can't double up.
        dlg.addEventListener('click', function (e) {
            if (e.target === dlg) { close(); }                       // backdrop
            if (e.target.closest('[data-share-close]')) { close(); }
            if (e.target.closest('[data-share-download]')) { download(); }
            // An explicit edit: no download follows, so the "proceed" callback is a no-op.
            if (e.target.closest('[data-share-rate]') && current && current.conceptId) {
                promptRating(function () {}, { edit: true });
            }
        });
        dlg.addEventListener('change', function (e) {
            if (e.target.matches('[data-share-theme]')) { applyTheme(); }
        });
        // Esc dismisses natively without going through close(), so the recede is undone here as well.
        dlg.addEventListener('close', function () { current = null; asked = false; pageRecede(false); });
        dlg.addEventListener('cancel', function () { dlg.classList.remove('is-closing'); });

        if (PP.dismissableSheet) { PP.dismissableSheet(dlg, { onClose: close }); }

        if (first) {
            window.addEventListener('resize', fit);
            // Delegated so cards appended by infinite scroll work without re-binding.
            document.body.addEventListener('click', function (e) {
                var trigger = e.target.closest('[data-card-open]');
                if (trigger) { open(trigger); }
            });

            // Warm the card on hover INTENT. Generating a preview is the slowest step in this flow, so
            // starting it while the pointer settles usually means the modal opens with the card already
            // there.
            //
            // A prefetch is NOT free: on a cold cache the preview endpoint fetches the avatar, cover and
            // trophy icon, each a `requests.get(timeout=10)` server-side. So there are three separate
            // guards, and only the last one is a real ceiling:
            //   - dwell   -- the pointer has to settle; sweeping past a tile never fires
            //   - cache   -- a preview is immutable per completion, so a card is fetched at most once
            //   - BUDGET  -- a hard per-page cap, because the first two bound requests per CARD while a
            //                determined mouse can still visit a lot of cards. Past the cap, opening a
            //                card just fetches on click like it always did.
            if (window.matchMedia && window.matchMedia('(hover: hover)').matches) {
                var warmTimer = null;
                var warmBudget = 8;
                document.body.addEventListener('mouseover', function (e) {
                    var trigger = e.target.closest('[data-card-open]');
                    if (!trigger || warmBudget <= 0) { return; }
                    var id = trigger.dataset.trophyGroupId;
                    if (previewCache[id] || inflight[id]) { return; }   // don't spend dwell on a warm card
                    clearTimeout(warmTimer);
                    warmTimer = setTimeout(function () {
                        if (warmBudget <= 0 || previewCache[id] || inflight[id]) { return; }
                        warmBudget -= 1;
                        fetchPreview(id).catch(function () { /* opening the card will surface it */ });
                    }, 280);
                });
                document.body.addEventListener('mouseout', function (e) {
                    if (e.target.closest('[data-card-open]')) { clearTimeout(warmTimer); }
                });
            }
        }
    }

    // Deep links. `?c=` is the native form; `?et=` is what platinum-earned notifications already in
    // the wild send. Neither hunts the DOM for the card: with paginate_by=24 the target usually isn't
    // on page 1, and both endpoints are keyed on exactly these ids, so resolving through them works
    // wherever the completion falls in the list.
    function autoOpen() {
        var params = new URLSearchParams(window.location.search);
        var groupId = params.get('c');
        var et = params.get('et');
        if (!groupId && !et) { return; }

        // Strip the params first, so a refresh doesn't reopen and the InfiniteScroller (constructed
        // after this) doesn't inherit them into every ?page fetch.
        params.delete('c'); params.delete('et');
        var qs = params.toString();
        window.history.replaceState({}, '', window.location.pathname + (qs ? '?' + qs : ''));

        dlg = document.getElementById('pc-share');
        if (!dlg) { return; }

        function nameFor(id) {
            var el = document.querySelector('[data-card-open][data-trophy-group-id="' + CSS.escape(String(id)) + '"]');
            return el ? el.dataset.gameName : '';
        }
        if (groupId) { openFor(groupId, nameFor(groupId)); return; }

        // The notification knows an EarnedTrophy, not a completion. One request to translate it, then
        // the normal path.
        PP.API.request('/api/v1/shareables/platinum/' + encodeURIComponent(et) + '/html/')
            .then(function (data) { openFor(data.trophy_group_id, nameFor(data.trophy_group_id)); })
            .catch(function () { /* stale notification link; leave the page as-is */ });
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
        wireModal(first);
        if (first) { autoOpen(); }      // strips ?c= / ?et= BEFORE the scroller snapshots the query
        initScroller();
        if (PP.StickyReveal) { PP.StickyReveal.init(); }
        if (first) {
            document.body.addEventListener('htmx:afterSwap', onAfterSwap);
            document.body.addEventListener('htmx:afterRequest', onAfterRequest);
        }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
