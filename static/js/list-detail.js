/*
 * Game list detail (/community/lists/<id>/) -- the owner's in-place edit surface (add, remove) and
 * the visitor's social acts (like, follow).
 *
 * Its own file rather than more of `gamelists.js`, matching the per-page convention the finished
 * browse pages already follow (`lists-browse.js`, `franchise-list.js`, `company-list.js`).
 * `gamelists.js` is the My Lists page -- a create dialog and a scope switcher, neither of which
 * exists here -- and the detail template was loading all of it to use none of it.
 *
 * THE REVEAL HERE IS A BUG FIX, not decoration. `detail_items.html` bakes `pp-reveal` onto the grid
 * on htmx requests (it must: htmx's settle step restores server attributes on id'd swapped elements,
 * so a class added afterwards is wiped), and `.pp-reveal .pp-gtile { opacity: 0 }` then holds every
 * tile hidden until an observer clears it. Nothing wired that observer for `#gl-items`, so sorting
 * the list rendered an empty-looking grid. That is the same failure My Lists shipped and had fixed;
 * the server half of the pattern was copied here and the client half was not.
 *
 * WRITES GO OUT AS FormData, not JSON. Every endpoint reads `request.POST`, which Django populates
 * for form and multipart bodies and leaves EMPTY for `application/json` -- so `API.post` (which
 * serializes JSON) would have posted a body the views cannot see, and `liked` would have read as
 * absent, i.e. false, on every press. `API.postFormData` is the matching half, and it is also what
 * the 27 other `request.POST` views on the site are called with.
 *
 * Wired via PlatPursuit.onPageReady(boot): element wiring re-runs on first load AND on an HTMX
 * Back/Forward history restore; body-level listeners are guarded by `first` so they bind once.
 */
(function () {
    'use strict';

    var PP = window.PlatPursuit || {};
    var revealHandle = null;
    var handledGrid = null;
    var searchField = null;

    /* ------------------------------------------------------------------ reveal ---- */

    // The same grammar and the same shared engine as every other tile grid on the site.
    function initReveal() {
        if (revealHandle) { revealHandle.disconnect(); revealHandle = null; }
        var grid = document.getElementById('gl-items');
        if (!grid || !PP.staggerReveal) { return; }
        var fadeEase = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
        var springEase = 'cubic-bezier(0.34, 1.4, 0.64, 1)';
        revealHandle = PP.staggerReveal({
            grid: grid, cardSelector: '.pp-gtile', step: 22,
            reveal: function (el, delayMs) {
                if (!el.animate) { return; }
                el.animate([{ opacity: 0 }, { opacity: 1 }],
                           { duration: 420, delay: delayMs, easing: fadeEase, fill: 'backwards' });
                el.animate([{ transform: 'translateY(14px) scale(0.965)' }, { transform: 'none' }],
                           { duration: 500, delay: delayMs, easing: springEase, fill: 'backwards' });
            },
        });
    }

    /* ----------------------------------------------------------------- helpers ---- */

    function setTally(selector, value) {
        if (typeof value !== 'number' || isNaN(value)) { return; }
        var el = document.querySelector(selector);
        if (el) { el.textContent = value.toLocaleString(); }
    }

    /**
     * Surface the server's own message when there is one.
     *
     * The endpoints answer `{"error": "..."}`, and that text is the useful half -- "You have reached
     * your list limit" beats "Something went wrong". A rate-limited request is the exception: it is
     * refused before the view runs and comes back as an HTML page, so `.json()` rejects and the
     * caller's fallback is used.
     */
    function toastError(err, fallback) {
        logFailure('write', err);
        var show = function (msg) {
            if (PP.ToastManager) { PP.ToastManager.show(msg || fallback, 'error'); }
        };
        if (err && err.response && typeof err.response.json === 'function') {
            err.response.json()
                .then(function (data) { show(data && data.error); })
                .catch(function () { show(null); });
            return;
        }
        show(null);
    }

    function statusOf(err) {
        return (err && err.response && err.response.status) || 0;
    }

    /**
     * Say what actually failed, in the console, every time.
     *
     * The first version of this file caught errors as `catch(function () { note('...') })` -- no
     * parameter at all. That collapsed a 404, a refused rate limit and a 500 into one sentence and
     * DISCARDED the only evidence of which had happened, so the first real failure became a guessing
     * game with nothing to go on. A catch that drops its error is worse than no catch: it converts a
     * diagnosable fault into a mystery, and it looks like handling.
     */
    function logFailure(what, err) {
        if (!window.console || !window.console.error) { return; }
        var status = statusOf(err);
        window.console.error(
            '[list-detail] ' + what + ' failed',
            { status: status || 'no response (network or CORS)', error: err });
    }

    /**
     * A person can act on "wait a moment"; they cannot act on "something went wrong".
     *
     * The bare status is included on purpose while this is behind the staff dev gate -- it is the
     * difference between one message and five round trips. REVISIT BEFORE LAUNCH: the parenthetical
     * is developer copy, and the site's rule is short and literal, never meta.
     */
    function failureCopy(status) {
        if (status === 429 || status === 403) {
            return 'Too many searches just now. Wait a moment and try again.';
        }
        if (status === 404) { return 'This list is no longer available.'; }
        if (status === 400) { return 'That search was too long.'; }
        return 'That search could not be run (error ' + (status || 'network') + ').';
    }

    /**
     * Re-render the games from the server rather than splicing a tile in client-side.
     *
     * Three things make this the right trade for one extra request. The sort is server-owned, so a
     * game added under "By name" belongs in its alphabetical position and not appended at the end.
     * The empty state and the "showing the first N" line are template concerns. And the tile markup
     * (cover chain, note, remove control) then lives in exactly one place instead of being mirrored
     * in JS. It reuses the sort toolbar's own contract: same URL, same target, same partial.
     */
    function refreshItems() {
        var form = document.getElementById('gl-detail-form');
        var base = (form && form.getAttribute('hx-get')) || window.location.pathname;
        var select = form && form.querySelector('select[name="sort"]');
        var url = base + (select && select.value
            ? '?sort=' + encodeURIComponent(select.value) : '');
        if (!window.htmx) { window.location.reload(); return Promise.resolve(); }
        // No `hx-push-url` here on purpose: this is a content update, not navigation. Pushing would
        // make Back step through every add and remove.
        return window.htmx.ajax('GET', url, { target: '#gl-items-panel', swap: 'innerHTML' });
    }

    /* ------------------------------------------------------------------ social ---- */

    // Like and follow are the same control with different words, so they are the same code with a
    // different config rather than two near-identical handlers.
    var TOGGLES = [
        {
            match: '[data-gl-like]', field: 'liked', on: 'Liked', off: 'Like',
            labelSel: '[data-gl-like-label]', countKey: 'like_count', countSel: '[data-like-count]',
            fallback: 'That like could not be saved.',
        },
        {
            match: '[data-gl-follow]', field: 'following', on: 'Following', off: 'Follow',
            labelSel: '[data-gl-follow-label]', countKey: 'follower_count', countSel: null,
            fallback: 'That follow could not be saved.',
        },
    ];

    // Mirrors exactly what the template renders for each state -- the ghost variant, the label, and
    // the filled/hollow icon -- so a toggled button is indistinguishable from a reloaded one.
    function paintToggle(btn, cfg, on) {
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        btn.classList.toggle('pp-cta--ghost', !on);
        var label = btn.querySelector(cfg.labelSel);
        if (label) { label.textContent = on ? cfg.on : cfg.off; }
        var icon = btn.querySelector('svg');
        if (icon) { icon.setAttribute('fill', on ? 'currentColor' : 'none'); }
    }

    function onToggle(btn, cfg) {
        if (btn.dataset.busy === '1') { return; }
        var was = btn.getAttribute('aria-pressed') === 'true';
        var next = !was;
        btn.dataset.busy = '1';

        // Optimistic: the button lights on the press, the way the tab chips do, because a like that
        // waits for a round trip feels broken. Reverted below if the write does not land.
        paintToggle(btn, cfg, next);

        var body = new FormData();
        body.append(cfg.field, next ? 'true' : 'false');
        PP.API.postFormData(btn.dataset.url, body)
            .then(function (data) {
                paintToggle(btn, cfg, !!data[cfg.field]);
                if (cfg.countSel) { setTally(cfg.countSel, data[cfg.countKey]); }
            })
            .catch(function (err) {
                paintToggle(btn, cfg, was);
                toastError(err, cfg.fallback);
            })
            .finally(function () { btn.dataset.busy = ''; });
    }

    /* ------------------------------------------------------------------ remove ---- */

    function onRemove(btn) {
        if (btn.dataset.busy === '1') { return; }
        btn.dataset.busy = '1';
        var tile = btn.closest('.gl-item');
        if (tile) { tile.classList.add('is-removing'); }

        PP.API.postFormData(btn.dataset.removeUrl, new FormData())
            .then(function (data) {
                setTally('[data-game-count]', data.game_count);
                return refreshItems();
            })
            .catch(function (err) {
                // The row comes back: nothing was removed, so nothing should look removed.
                if (tile) { tile.classList.remove('is-removing'); }
                btn.dataset.busy = '';
                toastError(err, 'That game could not be removed.');
            });
        // No `finally` resetting `busy` on the success path -- the button is about to be replaced by
        // the refresh, and clearing the flag would re-arm a control that is on its way out.
    }

    /* ------------------------------------------------------------------- adder ---- */

    function placeholderIcon() {
        var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '1.8');
        svg.setAttribute('aria-hidden', 'true');
        var rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('x', '2'); rect.setAttribute('y', '3');
        rect.setAttribute('width', '20'); rect.setAttribute('height', '14');
        rect.setAttribute('rx', '2');
        svg.appendChild(rect);
        return svg;
    }

    // Built with DOM APIs, never innerHTML: `title` is catalogue text and this is the one place on
    // the page where server strings become markup.
    function buildRow(result) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'gl-adder__opt';
        btn.dataset.conceptId = result.concept_id;

        var thumb = document.createElement('span');
        thumb.className = 'gl-adder__thumb';
        if (result.cover) {
            var img = document.createElement('img');
            img.src = result.cover;
            img.alt = '';
            img.loading = 'lazy';
            thumb.appendChild(img);
        } else {
            thumb.appendChild(placeholderIcon());
        }

        var name = document.createElement('span');
        name.className = 'gl-adder__name';
        name.textContent = result.title;

        var state = document.createElement('span');
        state.className = 'gl-adder__state';
        if (result.already_added) {
            btn.disabled = true;
            state.textContent = 'On this list';
            // Without this the disabled row's only distinction is colour.
            btn.setAttribute('aria-label', result.title + ' is already on this list');
        } else {
            state.textContent = 'Add';
            btn.setAttribute('aria-label', 'Add ' + result.title + ' to this list');
        }

        btn.appendChild(thumb);
        btn.appendChild(name);
        btn.appendChild(state);
        return btn;
    }

    function wireAdder() {
        var root = document.querySelector('[data-gl-adder]');
        if (!root || root.dataset.wired === '1') { return; }
        root.dataset.wired = '1';

        var input = root.querySelector('[data-gl-adder-input]');
        var panel = root.querySelector('[data-gl-adder-results]');
        var status = root.querySelector('[data-gl-adder-status]');
        if (!input || !panel) { return; }

        // Matches ListGameSearchView.MIN_QUERY. Below it the endpoint answers an empty list, so
        // asking is pure latency.
        var MIN_QUERY = 3;
        var seq = 0;

        function say(message) { if (status) { status.textContent = message; } }

        function closePanel() {
            panel.hidden = true;
            panel.textContent = '';
        }

        function note(message) {
            panel.textContent = '';
            var p = document.createElement('p');
            p.className = 'gl-adder__note';
            p.textContent = message;
            panel.appendChild(p);
            panel.hidden = false;
        }

        function render(results) {
            if (!results.length) {
                note('No games match that search.');
                say('No games found.');
                return;
            }
            panel.textContent = '';
            results.forEach(function (result) { panel.appendChild(buildRow(result)); });
            panel.hidden = false;
            say(results.length === 1 ? '1 game found.' : results.length + ' games found.');
        }

        var search = PP.debounce(function () {
            var query = input.value.trim();
            if (query.length < MIN_QUERY) {
                if (searchField) { searchField.setBusy(false); }
                closePanel();
                say('');
                return;
            }
            // Out-of-order responses: a slow request for "hol" must not overwrite the results for
            // "hollow" typed after it. Only the newest sequence number is allowed to render.
            var mine = ++seq;
            if (searchField) { searchField.setBusy(true); }
            PP.API.get(root.dataset.searchUrl + '?q=' + encodeURIComponent(query))
                .then(function (data) {
                    if (mine !== seq) { return; }
                    render((data && data.results) || []);
                })
                .catch(function (err) {
                    if (mine !== seq) { return; }
                    logFailure('search ' + root.dataset.searchUrl, err);
                    note(failureCopy(statusOf(err)));
                    say('Search failed.');
                })
                .finally(function () {
                    if (mine === seq && searchField) { searchField.setBusy(false); }
                });
        }, 220);

        // The shared search chrome: `.has-value` (clear button), Escape-to-clear, and `setBusy` for
        // the spinner. Same helper the browse toolbar and the game page's hunter search use.
        if (PP.wireSearchField) {
            searchField = PP.wireSearchField(input, {
                onClear: function () { seq++; closePanel(); say(''); },
            });
        }

        input.addEventListener('input', search);

        // Escape with an empty field closes the results; with text in it, wireSearchField clears
        // first (and its onClear closes the panel), which is the expected two-step.
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !input.value) { closePanel(); say(''); return; }
            if (e.key === 'ArrowDown') {
                var first = panel.querySelector('.gl-adder__opt:not(:disabled)');
                if (first) { e.preventDefault(); first.focus(); }
            }
        });

        // Arrow keys walk the results; Escape anywhere in them returns to the field. Rows are real
        // buttons, so Tab already works and this only adds the vertical shortcut.
        panel.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { input.focus(); closePanel(); say(''); return; }
            if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') { return; }
            var rows = Array.prototype.slice.call(
                panel.querySelectorAll('.gl-adder__opt:not(:disabled)'));
            var at = rows.indexOf(document.activeElement);
            if (at === -1) { return; }
            e.preventDefault();
            if (e.key === 'ArrowUp' && at === 0) { input.focus(); return; }
            var next = rows[at + (e.key === 'ArrowDown' ? 1 : -1)];
            if (next) { next.focus(); }
        });

        panel.addEventListener('click', function (e) {
            var row = e.target.closest ? e.target.closest('.gl-adder__opt') : null;
            if (!row || row.disabled || row.dataset.busy === '1') { return; }
            row.dataset.busy = '1';

            var body = new FormData();
            body.append('concept_id', row.dataset.conceptId);
            PP.API.postFormData(root.dataset.addUrl, body)
                .then(function (data) {
                    setTally('[data-game-count]', data.game_count);
                    // The row stays and flips to its added state rather than vanishing: somebody
                    // adding several games from one search should not have the list move under them.
                    row.disabled = true;
                    var state = row.querySelector('.gl-adder__state');
                    if (state) { state.textContent = 'On this list'; }
                    row.setAttribute('aria-label', data.title + ' is already on this list');
                    if (PP.ToastManager) {
                        PP.ToastManager.show('Added ' + data.title + '.', 'success');
                    }
                    return refreshItems();
                })
                .catch(function (err) {
                    row.dataset.busy = '';
                    toastError(err, 'That game could not be added.');
                });
        });
    }

    /* -------------------------------------------------------------------- boot ---- */

    // One delegated listener for every button on the page, bound to document.body exactly once.
    // Delegation rather than element wiring because the remove controls are inside the swapped panel
    // and are replaced on every refresh -- re-wiring them after each swap is a step that eventually
    // gets missed.
    function onBodyClick(e) {
        var target = e.target;
        if (!target || !target.closest) { return; }

        for (var i = 0; i < TOGGLES.length; i++) {
            var btn = target.closest(TOGGLES[i].match);
            if (btn) { onToggle(btn, TOGGLES[i]); return; }
        }

        var remove = target.closest('[data-gl-remove]');
        if (remove) { onRemove(remove); }
    }

    // Covers BOTH paths into the panel -- the sort toolbar's swap and `refreshItems` -- because both
    // land on the same target with the same partial.
    function onAfterSwap(e) {
        var target = (e.detail && e.detail.target) || e.target;
        if (!target || target.id !== 'gl-items-panel') { return; }
        var grid = target.querySelector('#gl-items');
        // htmx can fire afterSwap more than once for a single swap; re-revealing would replay the
        // animation over tiles that are already visible.
        if (grid && grid === handledGrid) { return; }
        handledGrid = grid;
        initReveal();
    }

    function boot(first) {
        handledGrid = null;
        searchField = null;
        wireAdder();
        initReveal();
        if (first) {
            document.body.addEventListener('click', onBodyClick);
            document.body.addEventListener('htmx:afterSwap', onAfterSwap);
        }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
