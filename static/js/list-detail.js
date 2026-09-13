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
 * so a class added afterwards is wiped), and `.pp-reveal .pp-gcard { opacity: 0 }` then holds every
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
 * Wired via PlatPursuit.onPageReady(boot). The contract is that element wiring re-runs on first
 * load AND on an HTMX Back/Forward restore, while body-level listeners are guarded by `first` so
 * they bind once. On THIS site the restore half never actually fires: base.html sets
 * `historyCacheSize = 0` and `refreshOnHistoryMiss = true`, so htmx stores no snapshot, misses on
 * Back, and does a full page reload instead. `boot` only ever sees `first === true`.
 *
 * The code still honours the contract rather than the current config, because the config is one
 * line away from changing and the failure would be silent. That is also why the wiring guard below
 * is a WeakSet and not a `data-` attribute: htmx's snapshot is `cloneNode(true).innerHTML`, so an
 * attribute survives serialization and would come back stamped "already wired" on nodes that carry
 * no listeners -- leaving the adder, the editor and publish inert while the delegated controls kept
 * working, which reads as half the page being broken rather than as a wiring bug.
 */
(function () {
    'use strict';

    var PP = window.PlatPursuit || {};
    var revealHandle = null;
    var handledGrid = null;
    var searchField = null;
    var pendingFocusIndex = null;
    // Set when an add-section write is in flight, so the field can be refocused once the refresh
    // that replaced it settles. See `restoreSectionFocus`.
    var pendingSectionFocus = false;
    // The item id of a card that was picked up when a refresh started, so the pick can be restored
    // on the fresh row. Crossing a section ALWAYS refreshes (the counts and the group membership
    // change), and the refresh tears down and re-attaches the drag -- which drops the pick. Without
    // this the keyboard path works exactly once per pick, which on the gesture it was added for
    // (walking one card down several headers) is the whole feature missing.
    var pendingPickId = null;
    var refreshSeq = 0;
    // The live SortableJS wrappers, so replaced grids' instances can be destroyed rather than leaked.
    // PLURAL since sections: a sectioned list renders a grid per group and every one of them is
    // armed, sharing a `group` name so a card can be dragged from one into another.
    var reorderManagers = [];
    // The grids those wrappers are attached to, so their click listeners come off with them.
    var dragGrids = [];
    // Whether the hunter has turned position editing ON. Distinct from whether the server
    // allows it (`data-gl-reorder`), which is a capability rather than an intent.
    var positioning = false;
    // Whether the in-place editor is open. The position bar is gated on it, so entering the mode is
    // always a deliberate second step rather than something a stray drag can start.
    var editorOpen = false;
    // The card the arrow keys act on -- picked up by clicking it. Not "selected": you are holding it,
    // and it moves when you press a key.
    var pickedRow = null;
    // Reorder writes run one at a time; see `saveOrder`.
    var orderChain = Promise.resolve();
    // Bumped when a reorder write fails, so anything already queued stands down rather than
    // re-applying the order the recovery refresh just undid.
    var orderGen = 0;
    // How many reorder writes are outstanding, so "Saved" is not shown over one still in flight.
    var pendingSaves = 0;
    // Set for one tick after a drop; see the `onEnd` note in `attachDrag`.
    var justDragged = false;
    // Per-node, and NOT serializable -- see the header. A `data-` attribute here
    // survives htmx's history snapshot and disables the wiring it was meant to guard.
    var wired = new WeakSet();

    /* ------------------------------------------------------------------ reveal ---- */

    // The same grammar and the same shared engine as every other tile grid on the site.
    function initReveal() {
        if (revealHandle) { revealHandle.disconnect(); revealHandle = null; }
        // THE ROOT, not `#gl-items`. A sectioned list renders a grid per group, so pointing this at
        // the flat grid's id revealed nothing on exactly the lists that have the most to reveal.
        // `staggerReveal` selects its cards as DESCENDANTS, so one handle over the root covers every
        // group -- and the stagger then runs continuously down the page rather than restarting at
        // each header, which is what somebody scanning the list actually sees.
        var grid = itemsRoot();
        if (!grid || !PP.staggerReveal) { return; }
        var fadeEase = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
        var springEase = 'cubic-bezier(0.34, 1.4, 0.64, 1)';
        revealHandle = PP.staggerReveal({
            grid: grid, cardSelector: '.pp-gcard', step: 22,
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

    // Owner actions were entirely silent to a screen reader: the add path toasted and the remove
    // path emitted nothing, and the toast is not a fallback -- `#toast-container` has no `aria-live`,
    // so ToastManager's output is never announced.
    function announce(message) {
        var el = document.querySelector('[data-gl-status]');
        if (el) { el.textContent = message; }
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
            // The toast is NOT a fallback for this: `#toast-container` carries no `aria-live`, as
            // both this file and the template note, so nothing ToastManager writes is announced.
            // Successes called `announce()` and failures did not, so a blind owner whose rename was
            // refused got silence -- no announcement, no inline error, and a form still open.
            announce(msg || fallback);
        };
        // A followed redirect, not a refusal -- so say the thing the person can act on rather than
        // a generic failure they would read as a bug in the list.
        if (err && err.signedOut) {
            show('You may have been signed out. Reload the page and try again.');
            return;
        }
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
     * POST, and refuse to call a redirected HTML page a successful write.
     *
     * `fetch` follows redirects transparently and reports only the FINAL response, so a bounce from
     * `LoginRequiredMixin`, `_LinkedProfileRequired` or the staff gate -- a session that expired, a
     * sign-out in another tab, a profile that became unlinked -- arrives here as `200 text/html`.
     * `API.request` sees `response.ok`, finds no JSON content type, and hands back the page as a
     * STRING. Every caller then read a property off it and got `undefined`:
     *
     *   like/follow -> the button silently reverted, reading as "the click did nothing"
     *   remove      -> a silent no-op, the entry still there
     *   add         -> the toast literally said "Added undefined." and the row flipped to
     *                  "On this list" for a game the server never received
     *
     * Server tests cannot see this: Django's test client does not follow redirects unless asked, so
     * a test asserting 302 passes while the browser gets 200. Checked here rather than in
     * `API.request`, because changing that helper changes every page on the site and is its own
     * decision.
     */
    function postJson(url, body) {
        return PP.API.postFormData(url, body).then(function (data) {
            if (data === null || typeof data !== 'object') {
                var err = new Error('expected JSON, got a redirected page');
                err.signedOut = true;
                throw err;
            }
            return data;
        });
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
        return 'That search could not be run. Try again in a moment.';
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
    function refreshItems(withChrome) {
        var form = document.getElementById('gl-detail-form');
        var base = (form && form.getAttribute('hx-get')) || window.location.pathname;
        var select = form && form.querySelector('select[name="sort"]');
        var params = [];
        if (select && select.value) { params.push('sort=' + encodeURIComponent(select.value)); }
        // `chrome=1` asks for the out-of-band arrange bar as well. Adding or deleting a SECTION can
        // change what that bar offers -- the first section on a Collection turns arranging on, the
        // last one deleted turns it off, and the numbering choice only exists while there is one --
        // and the bar lives outside the swap target, so without this it keeps offering yesterday's
        // capability until a reload. The SORT is kept across it, unlike `refreshAfterTypeChange`:
        // a section change does not invalidate the sort the hunter is reading the list in.
        if (withChrome) { params.push('chrome=1'); }
        var url = base + (params.length ? '?' + params.join('&') : '');
        // No `hx-push-url` here on purpose: this is a content update, not navigation. Pushing would
        // make Back step through every add and remove.
        //
        // A RESOLVED PROMISE IS NOT EVIDENCE OF A SWAP. htmx resolves its ajax promise for every
        // HTTP status -- only a network error, an abort or a timeout rejects -- and its
        // `responseHandling` maps 4xx/5xx to `swap: false`. So a 500 here resolved successfully,
        // never swapped, never fired `htmx:afterSwap`, and left the header tally reading N+1 over a
        // grid still showing N with no warning anywhere. Both `.catch` blocks that were written to
        // report exactly that were unreachable for the case that matters.
        //
        // The node identity is the signal: an `innerHTML` swap always builds new nodes, so if the
        // sentinel is the same object afterwards, nothing was swapped.
        var mine = ++refreshSeq;
        var before = itemsRoot();
        return window.htmx.ajax('GET', url, { target: '#gl-items-panel', swap: 'innerHTML' })
            .then(function () {
                // A later refresh already superseded this one -- two quick removals issue two
                // independent GETs and the older response can land last, repainting the grid with a
                // row that is already deleted.
                if (mine !== refreshSeq) { return; }
                if (itemsRoot() === before) {
                    throw new Error('the items panel did not swap');
                }
            });
    }

    /**
     * The swap sentinel, and it is NOT `#gl-items`.
     *
     * Both refresh helpers prove a swap happened by comparing the node they remembered against the
     * node that is there afterwards. `#gl-items` is rendered only by a FLAT list -- a sectioned one
     * renders a grid per group and no element by that id -- so on a sectioned list the comparison was
     * `null === null` and every successful refresh threw "the items panel did not swap", reporting a
     * failure over a swap that had just worked. `#gl-items-root` wraps every branch of the partial,
     * so it is there in all three shapes.
     */
    function itemsRoot() {
        return document.getElementById('gl-items-root');
    }

    /**
      * Re-render everything a type switch changed, in one request, without leaving the page.
      *
      * `?chrome=1` asks the items partial to append out-of-band copies of the sort <select> and the
      * position-bar slot. The main swap replaces the grid; htmx routes the OOB fragments to their own
      * ids elsewhere in the document. One round trip, and the server stays the only thing that
      * decides what a ranked list looks like.
      *
      * NO `sort` PARAMETER, deliberately: the new type has its own default (Ranked opens on "List
      * order"), and carrying the old one across would land a freshly-ranked list on A-Z. The address
      * bar is cleaned to match, so a reload does not resurrect a sort that no longer applies.
      */
    function refreshAfterTypeChange() {
        var path = window.location.pathname;
        if (window.history && window.history.replaceState) {
            window.history.replaceState({}, '', path);
        }
        // The same guard `refreshItems` documents at length, and for the same reason: htmx resolves
        // its ajax promise for EVERY status and maps 4xx/5xx to `swap: false`. Without this a 500
        // here resolved quietly, leaving numerals and grips on a list the server no longer calls
        // ranked, a sort control still offering "List order", and no sign anywhere.
        var mine = ++refreshSeq;
        var before = itemsRoot();
        return window.htmx.ajax('GET', path + '?chrome=1',
                                { target: '#gl-items-panel', swap: 'innerHTML' })
            .then(function () {
                if (mine !== refreshSeq) { return; }
                if (itemsRoot() === before) {
                    throw new Error('the items panel did not swap');
                }
            })
            .catch(function (err) {
                // The row is already saved -- only the view is stale -- so say what is true and let
                // them decide, rather than reloading out from under an open editor.
                logFailure('chrome refresh after a type change', err);
                announce('Saved. Reload the page to see the change.');
            });
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
        postJson(btn.dataset.url, body)
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

        // Remember the position ONLY if the keyboard is what pressed this, so the post-swap restore
        // does not yank focus away from a mouse user reading elsewhere on the page.
        if (document.activeElement === btn) {
            var all = Array.prototype.slice.call(
                document.querySelectorAll('#gl-items [data-gl-remove]'));
            pendingFocusIndex = all.indexOf(btn);
        }

        postJson(btn.dataset.removeUrl, new FormData())
            .then(function (data) {
                setTally('[data-game-count]', data.game_count);
                announce('Removed from the list.');
                // The refresh gets its OWN catch. Chained into the one below, a failed re-render
                // restored a row the server had already deleted and toasted "That game could not be
                // removed" -- the opposite of what happened. The write succeeded; only the view of
                // it did not.
                return refreshItems().catch(function (err) {
                    logFailure('items refresh after remove', err);
                    // The write LANDED, so the row is gone server-side -- but the grid was not
                    // repainted, so the button is not replaced after all. Without this it keeps
                    // `busy` and the tile keeps `.is-removing` (opacity .35, pointer-events none):
                    // a ghost row with a dead control, recoverable only by reloading.
                    btn.dataset.busy = '';
                    pendingFocusIndex = null;
                    if (PP.ToastManager) {
                        PP.ToastManager.show(
                            'Removed. Reload to see the updated list.', 'warning');
                    }
                });
            })
            .catch(function (err) {
                // The row comes back: nothing was removed, so nothing should look removed.
                if (tile) { tile.classList.remove('is-removing'); }
                btn.dataset.busy = '';
                // Cleared here too. It was only ever cleared by a SUCCESSFUL restore, so a failed
                // removal left it set until the next swap of any kind -- and then a sort change
                // yanked focus onto a remove button the reader never touched.
                pendingFocusIndex = null;
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
        if (!root || wired.has(root)) { return; }
        wired.add(root);

        var input = root.querySelector('[data-gl-adder-input]');
        var panel = root.querySelector('[data-gl-adder-results]');
        // `document`, not `root`: the status line is a SIBLING of the search field in the toolbar
        // bar, not a child of it. Scoped to `root` this silently returned null and every count
        // announcement was dropped -- no error, just an accessibility feature that quietly did
        // nothing. (`sr-only` is absolutely positioned, so it is not a flex item in the bar.)
        var status = document.querySelector('[data-gl-adder-status]');
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

        /**
         * Close the results AND orphan whatever is in flight.
         *
         * One helper because the `seq++` was applied to only two of the four exits, and the two it
         * missed are both reachable without the debounce to rescue them. The clean reproducer:
         * type "hol", results render; type "low", the debounce fires and a request goes out; press
         * ArrowDown into the panel then Escape. The panel closes, `seq` is untouched, the response
         * lands, passes `mine === seq` and RE-OPENS the panel over a field the reader has already
         * dismissed. The comment above the guard described exactly this and the fix had reached
         * half the exits.
         */
        function abandon() {
            seq++;
            if (searchField) { searchField.setBusy(false); }
            closePanel();
            say('');
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
                // `++seq` here too, and it is not cosmetic. Without it: type "hollow", then clear
                // the field while the request is in flight. This branch closes the panel but leaves
                // `seq` alone, so the in-flight response still satisfies `mine === seq`, renders,
                // and RE-OPENS the panel with twelve results for a query the field no longer holds.
                // The guard below only ever covered longer-query to longer-query.
                seq++;
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
                    // Same redirect trap `postJson` guards on the write side: `fetch` follows a
                    // bounce to the login page, `API.request` finds no JSON content type and hands
                    // back the page as a STRING, and `(data && data.results) || []` then reports
                    // "No games match that search" for a session that has simply expired.
                    if (data === null || typeof data !== 'object') {
                        var err = new Error('expected JSON, got a redirected page');
                        err.signedOut = true;
                        throw err;
                    }
                    render(data.results || []);
                })
                .catch(function (err) {
                    if (mine !== seq) { return; }
                    logFailure('search ' + root.dataset.searchUrl, err);
                    note(err && err.signedOut
                         ? 'You may have been signed out. Reload the page and try again.'
                         : failureCopy(statusOf(err)));
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
                // `setBusy(false)` is required, not tidiness. `seq++` orphans any in-flight request,
                // so its `.finally` fails the `mine === seq` test and never clears the busy flag --
                // leaving `.is-searching` set, which keeps the spinner turning on an empty field AND
                // hides the clear button, so the visible control is stuck until the next keystroke.
                onClear: abandon,
            });
        }

        input.addEventListener('input', search);

        // Escape with an empty field closes the results; with text in it, wireSearchField clears
        // first (and its onClear closes the panel), which is the expected two-step.
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !input.value) { abandon(); return; }
            if (e.key === 'ArrowDown') {
                var first = panel.querySelector('.gl-adder__opt:not(:disabled)');
                if (first) { e.preventDefault(); first.focus(); }
            }
        });

        // Arrow keys walk the results; Escape anywhere in them returns to the field. Rows are real
        // buttons, so Tab already works and this only adds the vertical shortcut.
        // `stopPropagation` as well as `preventDefault`, because the position mode listens for the
        // same keys on the DOCUMENT. The result rows are <button>s, so `isTyping` does not exclude
        // them, and arrowing through search results ALSO moved the picked card and fired a reorder
        // write -- Escape likewise both closed the panel and dropped the pick.
        panel.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { e.stopPropagation(); input.focus(); abandon(); return; }
            if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') { return; }
            e.stopPropagation();
            var rows = Array.prototype.slice.call(
                panel.querySelectorAll('.gl-adder__opt:not(:disabled)'));
            var at = rows.indexOf(document.activeElement);
            if (at === -1) { return; }
            e.preventDefault();
            if (e.key === 'ArrowUp' && at === 0) { input.focus(); return; }
            var next = rows[at + (e.key === 'ArrowDown' ? 1 : -1)];
            if (next) { next.focus(); }
        });

        // Now that the results FLOAT over the page, they have to be dismissable by clicking away --
        // in flow they merely pushed content down and could be left open harmlessly. Bound on the
        // document, and only while a panel is actually open, so it costs nothing at rest.
        document.addEventListener('click', function (e) {
            if (panel.hidden) { return; }
            if (root.contains(e.target)) { return; }
            abandon();
        });

        panel.addEventListener('click', function (e) {
            var row = e.target.closest ? e.target.closest('.gl-adder__opt') : null;
            if (!row || row.disabled || row.dataset.busy === '1') { return; }
            row.dataset.busy = '1';
            // NOT `abandon()` -- the panel stays open on purpose so several games can be added from
            // one search. But the in-flight search must still be orphaned: its response rebuilds
            // every row from an `already_added` snapshot taken BEFORE this add, which would undo the
            // flip below and re-enable a row for a game that is now on the list.
            seq++;

            var body = new FormData();
            body.append('concept_id', row.dataset.conceptId);
            postJson(root.dataset.addUrl, body)
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
                    announce('Added ' + data.title + ' to the list.');
                    // Its own catch, for the same reason as remove -- and worse here, because the
                    // success toast has ALREADY fired. Chained into the catch below, a failed
                    // re-render put "Added Hollow Knight." and "That game could not be added."
                    // on screen together, and re-armed a row that had just been disabled.
                    return refreshItems().catch(function (err) {
                        logFailure('items refresh after add', err);
                        if (PP.ToastManager) {
                            PP.ToastManager.show('Added. Reload to see it in the list.', 'warning');
                        }
                    });
                })
                .catch(function (err) {
                    row.dataset.busy = '';
                    toastError(err, 'That game could not be added.');
                });
        });
    }

    /* ------------------------------------------------------------------ rename ---- */

    /**
     * Edit the name and description where they are shown.
     *
     * Both the read view and the form are server-rendered and JS swaps which is `hidden`, so there is
     * no markup built in the client and nothing to keep in step with the template.
     */
    function wireIdentityEditor() {
        var root = document.querySelector('[data-gl-identity]');
        if (!root || wired.has(root)) { return; }
        var form = root.querySelector('[data-gl-identity-edit]');
        var view = root.querySelector('[data-gl-identity-view]');
        if (!form || !view) { return; }          // a visitor: no form rendered
        wired.add(root);

        var nameField = form.querySelector('[name="name"]');
        var descField = form.querySelector('[name="description"]');

        function open() {
            // RESYNC on the way in, not only on cancel. The save writes the SERVER's normalized
            // values to the heading (`_check_name` trims and sanitizes) but left the fields holding
            // whatever was typed -- so saving "  My List  " and reopening showed the padded string
            // in a form whose heading read the trimmed one.
            reset();
            view.hidden = true;
            form.hidden = false;
            editorOpen = true;
            syncPositionsVisibility();
            var tallies = document.querySelector('[data-gl-tallies]');
            if (tallies) { tallies.hidden = true; }
            nameField.focus();
            nameField.setSelectionRange(nameField.value.length, nameField.value.length);
        }

        function close() {
            // Leaving the editor leaves position editing, because the mode was entered FROM here:
            // a hunter who closes the panel has finished editing the list, and handles left live on
            // a page with no visible sign of why is how a drag happens by accident again.
            editorOpen = false;
            syncPositionsVisibility();
            form.hidden = true;
            view.hidden = false;
            var tallies = document.querySelector('[data-gl-tallies]');
            if (tallies) { tallies.hidden = false; }
            var opener = root.querySelector('[data-gl-edit-open]');
            if (opener) { opener.focus(); }       // focus goes back where it came from
        }

        function reset() {
            // Cancel restores from the DOM the server rendered, not from a snapshot taken at open --
            // a successful save updates that DOM, so a later cancel must not resurrect the old text.
            // `|| {}` LOOKED like a null guard and was the opposite: `{}.textContent` is
            // undefined, so `.trim()` threw and cancel died silently. Its sibling line below guards
            // correctly, which is what made the difference easy to miss.
            var heading = root.querySelector('[data-gl-name]');
            nameField.value = heading ? heading.textContent.trim() : '';
            var desc = root.querySelector('[data-gl-description]');
            descField.value = desc && !desc.hidden ? desc.textContent.trim() : '';
            // The TYPE resets too, and it did not. Pick Ranked, cancel, reopen to fix a typo, save --
            // and the abandoned radio was still checked, so the rename quietly took the type change
            // with it and reloaded the page underneath them. Every other field in this form is
            // restored from the server-rendered DOM; this one was reading whatever was left over.
            var current = form.querySelector('[name="list_type"][value="'
                                             + (root.dataset.listType || '') + '"]');
            if (current) { current.checked = true; }
            [nameField, descField].forEach(function (el) {
                el.dispatchEvent(new Event('input', { bubbles: true }));   // resync the counters
            });
        }

        var opener = root.querySelector('[data-gl-edit-open]');
        if (opener) { opener.addEventListener('click', open); }

        // Both dismissals refuse while a save is in flight. Neither was guarded, and the response
        // does not care that the form closed: press Save then Escape and `reset()` read the
        // PRE-save heading back into the fields, then the response wrote the NEW name into the
        // heading. Heading and form then disagreed, and pressing Save again renamed it back -- so an
        // Escape that read as "cancel" let the rename through and then offered to undo it.
        function dismiss() {
            var save = form.querySelector('[data-gl-edit-save]');
            if (save && save.dataset.busy === '1') { return; }
            reset();
            close();
        }

        var cancel = form.querySelector('[data-gl-edit-cancel]');
        if (cancel) { cancel.addEventListener('click', dismiss); }

        form.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { e.preventDefault(); dismiss(); }
        });

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var save = form.querySelector('[data-gl-edit-save]');
            if (save && save.dataset.busy === '1') { return; }
            if (save) { save.dataset.busy = '1'; }

            // Captured before the write so the announcement can say which thing changed. It used to
            // say "renamed" unconditionally, including when only the description was edited.
            var heading = root.querySelector('[data-gl-name]');
            var previousName = heading ? heading.textContent.trim() : '';
            var descEl = root.querySelector('[data-gl-description]');
            var previousDesc = descEl && !descEl.hidden ? descEl.textContent.trim() : '';

            // The TYPE decides three things the server renders -- which sorts the toolbar offers,
            // whether each card carries a numeral, and whether the handles exist -- so a change here
            // is answered with a reload rather than by teaching the client to assemble all three.
            var typeField = form.querySelector('[name="list_type"]:checked');
            var typeChanged = !!typeField && typeField.value !== (root.dataset.listType || '');

            // ONLY WHAT CHANGED IS SENT, which is not tidiness: `update_list` runs the restriction
            // gate whenever `name` or `description` is present, so posting them unconditionally made
            // every type switch a gated write. A restricted hunter could switch type per the service
            // (and a service test proves it) and could not per the product, because this form always
            // put a name in the body. The view reads membership (`'field' in request.POST`), so
            // omitting a field means "leave alone" -- an empty string still reaches it as a real
            // edit.
            var body = new FormData();
            if (nameField.value.trim() !== previousName) { body.append('name', nameField.value); }
            if (descField.value.trim() !== previousDesc) {
                body.append('description', descField.value);
            }
            if (typeChanged) { body.append('list_type', typeField.value); }

            // Nothing to say: close rather than asking the server to refuse an empty edit, which is
            // what "Nothing to change." was doing to anyone who opened the panel and thought better
            // of it.
            if (!body.has('name') && !body.has('description') && !body.has('list_type')) {
                if (save) { save.dataset.busy = ''; }
                close();
                return;
            }

            postJson(root.dataset.updateUrl, body)
                .then(function (data) {
                    // Render what the SERVER stored. `_check_name` trims and sanitizes, so echoing
                    // the typed value would show a name the database does not hold.
                    var heading = root.querySelector('[data-gl-name]');
                    if (heading) { heading.textContent = data.name; }
                    var desc = root.querySelector('[data-gl-description]');
                    if (desc) {
                        desc.textContent = data.description;
                        desc.hidden = !data.description;
                    }
                    // THE STORED TYPE, RE-ANCHORED. `typeChanged` above is measured against this
                    // attribute, and the in-place refresh does not re-render the block that carries
                    // it (only the grid, the sort control and the bar slot travel). So after one
                    // successful switch it still named the OLD type, and switching back computed
                    // `typeChanged === false`: nothing was sent, and with the name and description
                    // untouched the request was skipped entirely and the editor just closed. The
                    // second switch of a session silently did nothing.
                    //
                    // Read from the RESPONSE rather than from the radio, so it records what the
                    // server stored rather than what was asked for.
                    if (data.list_type) { root.dataset.listType = data.list_type; }
                    document.title = data.name;
                    // The breadcrumb is the third place the name appears, and it was the one left
                    // showing the old value until a reload.
                    var crumb = document.querySelector('[data-breadcrumb-current]');
                    if (crumb) { crumb.textContent = data.name; }
                    announce(data.name !== previousName
                             ? 'List renamed to ' + data.name + '.'
                             : 'List details saved.');
                    // A TYPE CHANGE REFRESHES THE LIST IN PLACE AND LEAVES THE EDITOR OPEN.
                    //
                    // It used to reload the whole page, because the type decides three things the
                    // server renders and the client should not assemble: the cards, the sorts on
                    // offer, and whether the position bar exists. The refresh below carries all
                    // three (the last two out-of-band), so the reload bought nothing except losing
                    // the hunter's place and making them re-open the panel to reach the positions
                    // they had just switched the list over to use.
                    //
                    // The editor stays open ONLY here. After a plain rename, closing it is the
                    // natural "done" -- the heading behind it has already updated and there is
                    // nothing further to do. After a type change there usually is.
                    if (typeChanged) {
                        refreshAfterTypeChange();
                    } else {
                        close();
                    }
                })
                .catch(function (err) { toastError(err, 'Those changes could not be saved.'); })
                .finally(function () { if (save) { save.dataset.busy = ''; } });
        });
    }

    /* ----------------------------------------------------------------- publish ---- */

    function wireVisibility() {
        var root = document.querySelector('[data-gl-visibility]');
        if (!root || wired.has(root)) { return; }
        wired.add(root);

        var privateState = root.querySelector('[data-gl-private-state]');
        var publicState = root.querySelector('[data-gl-public-state]');

        function paint(isPublic, celebrate) {
            if (privateState) { privateState.hidden = isPublic; }
            if (publicState) { publicState.hidden = !isPublic; }
            var chip = document.querySelector('[data-gl-private-chip]');
            if (chip) { chip.hidden = isPublic; }
            var likes = document.querySelector('[data-gl-tally-likes]');
            if (likes) { likes.hidden = !isPublic; }

            // The one moment on this page worth marking, and only in the publishing direction --
            // taking a list back down is housekeeping, not an achievement.
            if (!celebrate) { return; }
            var card = document.querySelector('[data-gl-identity]');
            card = card && card.closest('section');
            if (!card) { return; }
            card.classList.remove('gl-published');
            void card.offsetWidth;                 // restart the animation if it is replayed
            card.classList.add('gl-published');
            setTimeout(function () { card.classList.remove('gl-published'); }, 1000);
        }

        // The pressed button lives inside the block `paint()` is about to hide, so focus fell to
        // <body> and a keyboard user re-tabbed from the top of the document. `wireIdentityEditor`
        // solves the same problem correctly two functions up ("focus goes back where it came from")
        // and this path did not. Focus moves to the control that replaces it.
        function refocusAfter(isPublic) {
            var next = root.querySelector(isPublic ? '[data-gl-unpublish]' : '[data-gl-publish]');
            if (next && document.activeElement === document.body) { next.focus(); }
        }

        function set(btn, isPublic) {
            if (btn.dataset.busy === '1') { return; }
            btn.dataset.busy = '1';
            var body = new FormData();
            body.append('is_public', isPublic ? 'true' : 'false');
            postJson(root.dataset.updateUrl, body)
                .then(function (data) {
                    paint(data.is_public, data.is_public);
                    refocusAfter(data.is_public);
                    if (PP.ToastManager) {
                        PP.ToastManager.show(
                            data.is_public ? 'Published.' : 'This list is private again.',
                            'success');
                    }
                    announce(data.is_public ? 'List published.' : 'List is now private.');
                })
                .catch(function (err) {
                    toastError(err, isPublic ? 'That list could not be published.'
                                             : 'That list could not be made private.');
                })
                .finally(function () { btn.dataset.busy = ''; });
        }

        var publish = root.querySelector('[data-gl-publish]');
        if (publish) { publish.addEventListener('click', function () { set(publish, true); }); }
        var unpublish = root.querySelector('[data-gl-unpublish]');
        if (unpublish) { unpublish.addEventListener('click', function () { set(unpublish, false); }); }
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
        if (remove) { onRemove(remove); return; }

        // Section controls live inside the swapped panel and are replaced by the very refresh their
        // own handler triggers, so they are delegated for exactly the reason the remove control is.
        var rename = target.closest('[data-gl-section-rename]');
        if (rename) { onSectionRename(rename); return; }

        var dropSection = target.closest('[data-gl-section-delete]');
        if (dropSection) { onSectionDelete(dropSection); return; }

        // AFTER the section delete, because `[data-gl-delete]` is a prefix of nothing but reads like
        // one: keeping the whole-list delete last means a future `[data-gl-delete-*]` cannot be
        // swallowed by it. The list delete is the destructive one, so it is the one worth ordering
        // defensively.
        var del = target.closest('[data-gl-delete]');
        if (del) { onDeleteList(del); }
    }

    // Covers BOTH paths into the panel -- the sort toolbar's swap and `refreshItems` -- because both
    // land on the same target with the same partial.
    /**
     * Delete the whole list.
     *
     * A native `confirm()` and not the site's dialog primitive: that one is for things you are
     * composing, and this is a yes/no about a thing that already exists. Naming the list in the
     * prompt matters more than the chrome does -- somebody with four lists open in four tabs should
     * not have to guess which one they are about to lose.
     *
     * The service soft-deletes and is idempotent, so a double press is not an error. The redirect
     * comes from the SERVER rather than being built here: the list is gone, so the page it was on is
     * gone, and where to send somebody is a routing decision.
     */
    function onDeleteList(btn) {
        if (btn.dataset.busy === '1') { return; }
        var name = btn.dataset.listName || 'this list';
        if (!window.confirm('Delete "' + name + '"? This cannot be undone from here.')) { return; }

        btn.dataset.busy = '1';
        postJson(btn.dataset.deleteUrl, new FormData())
            .then(function (data) {
                announce('List deleted.');
                window.location.href = (data && data.redirect) || '/my-lists/';
            })
            .catch(function (err) {
                toastError(err, 'That list could not be deleted.');
                btn.dataset.busy = '';
            });
    }

    function onAfterSwap(e) {
        var target = (e.detail && e.detail.target) || e.target;
        if (!target || target.id !== 'gl-items-panel') { return; }
        // `#gl-items-root`, not `#gl-items`: a SECTIONED list renders a grid per group and no element
        // by the latter id, so `grid` was null, `null === handledGrid` matched on the very first
        // swap, and the reveal never ran -- every refreshed card stayed at `pp-reveal`'s starting
        // opacity. The root wraps all three shapes of the partial.
        var root = target.querySelector('#gl-items-root');
        // htmx can fire afterSwap more than once for a single swap; re-revealing would replay the
        // animation over tiles that are already visible.
        if (root && root === handledGrid) { return; }
        handledGrid = root;
        initReveal();

        // The sort toolbar is rendered `{% if items %}` and lives OUTSIDE the swapped panel, so it
        // cannot appear or disappear on its own. Add the first game to an empty list and the grid
        // gains a tile while the toolbar stays absent until a manual reload; remove the last one and
        // a dead sort control is left behind. Crossing that boundary is rare and once per list, so
        // reload rather than teach the client to build a control the server owns.
        // Across the whole swapped PANEL, not one grid. Scoped to `#gl-items` this asked "does the
        // flat grid hold a card", which on a sectioned list is always no -- so `hasItems` was false
        // while the toolbar was present, the two disagreed, and this reloaded the page. On every
        // swap. Including the one the reload itself causes.
        var hasItems = !!target.querySelector('.pp-gcard');
        var hasToolbar = !!document.getElementById('gl-detail-form');
        if (hasItems !== hasToolbar) { window.location.reload(); return; }

        restoreRemoveFocus();
    }

    /**
     * Re-wire the drag AFTER SETTLE, not after swap, and this distinction is the whole of it.
     *
     * htmx stabilises attributes on id'd elements: for an element present in both the old and new
     * content it copies the OLD node's attributes onto the new one before insertion, then restores
     * the real ones in a settle task ~20ms later. So at `htmx:afterSwap` the fresh `#gl-items` is
     * still wearing the previous grid's attributes, and `data-gl-reorder` reads as whatever the
     * PREVIOUS sort had.
     *
     * Both directions were broken by wiring on swap, and the second is the nastier one:
     *
     *   A-Z -> List order: the new grid should be draggable, reads as not, returns early. Settle
     *   then restores the attribute and nothing re-runs, so every grip on the page is inert until a
     *   reload -- the exact navigation somebody makes to get back to their order.
     *
     *   List order -> A-Z: the new grid should NOT be draggable, reads as draggable, gets wired AND
     *   added to `wired`. Settle cannot undo it, because the WeakSet now says the grid is handled.
     *
     * This file's own header documents the same mechanism as the reason `pp-reveal` is baked in
     * server-side, which is where I should have looked first.
     */
    function onAfterSettle(e) {
        var target = (e.detail && e.detail.target) || e.target;
        if (!target || target.id !== 'gl-items-panel') { return; }
        // The position slot may have been replaced out-of-band by the same response, so the toggle
        // is a new node with no listener. `wirePositioning` is WeakSet-guarded on that node, so this
        // is a no-op when nothing was swapped.
        wirePositioning();
        // The add-section form and the numbering checkbox live in that same out-of-band slot and are
        // replaced with it, so they need the same treatment and carry the same WeakSet guard. They
        // are NOT delegated like the header controls because both are form elements whose own events
        // (`submit`, `change`) do not usefully delegate from the body.
        wireSections();
        syncPositioning();
        // AFTER `wireSections`, so the field being focused is the live one with its listener
        // attached rather than the node that is about to be replaced.
        restoreSectionFocus();
        // AFTER `syncPositioning`, which re-attaches the drag -- and whose `detachDrag` drops any
        // pick. Restoring before it would be undone one line later.
        restorePick();
    }

    function wirePositioning() {
        var toggle = document.querySelector('[data-gl-positions-toggle]');
        if (!toggle || wired.has(toggle)) { return; }
        wired.add(toggle);
        toggle.addEventListener('click', function () {
            if (positioning) { exitPositioning(); } else { enterPositioning(); }
        });
        paintPositionsToggle();
    }

    /**
     * The grip picks a card up too, which it did not.
     *
     * It is a real <button> announced as "Reorder <game>", and pressing it -- by click, Enter or
     * Space -- did nothing at all: `onCardClick` requires a `.pp-gcard` ancestor and the grip is the
     * card's SIBLING, so the only thing that ever read it was the arrow-key handler. A button that
     * is inert on activation is a broken promise however good the arrow-key path is.
     *
     * Delegated on the body rather than bound per grip, because there is one per row and they are
     * replaced on every swap.
     */
    function onGrabClick(e) {
        // `justDragged` for the reason `onCardClick` carries it: SortableJS swallows the click after
        // a drop EXCEPT on Chrome for Android, where a drag begun on the grip would otherwise pick
        // the card straight back up the moment it landed.
        if (justDragged) { return; }
        if (!positioning) { return; }
        var grab = e.target.closest && e.target.closest('[data-gl-grab]');
        if (!grab) { return; }
        var row = grab.closest('.gl-item');
        if (row) { togglePicked(row); }
    }

    /**
     * Put the keyboard back where it was after a removal.
     *
     * The refresh replaces the whole panel, so the focused remove button is destroyed and focus
     * resets to <body>. Somebody pruning five games with the keyboard was thrown to the top of the
     * document five times, re-tabbing past the header, toolbar and adder each time.
     *
     * Only acts when focus actually WAS on a remove control (`pendingFocusIndex` is set by
     * `onRemove`), so a sort swap or a mouse-driven removal does not steal focus from wherever the
     * reader had it.
     */
    function restoreRemoveFocus() {
        if (pendingFocusIndex === null) { return; }
        var index = pendingFocusIndex;
        pendingFocusIndex = null;
        // `#gl-items-root`, because `#gl-items` exists only on a FLAT list -- so on a sectioned one
        // this selector matched nothing and focus fell to <body> after every removal, which is the
        // bug this function exists to prevent.
        var buttons = document.querySelectorAll('#gl-items-root [data-gl-remove]');
        if (!buttons.length) { return; }
        // The row that took the removed one's place, or the new last row if it was the last.
        var next = buttons[Math.min(index, buttons.length - 1)];
        if (next) { next.focus(); }
    }

    /**
     * Put the caret back in the add-section field after the refresh that replaced it.
     *
     * `refreshItems(true)` out-of-band swaps `#gl-positions-slot`, and the field lives in it -- so
     * adding "Playing" then "Finished" meant tabbing from the top of the document between them. The
     * same debt `restoreRemoveFocus` pays for the remove button, on a control somebody is mid-sentence
     * in, which makes it worse.
     *
     * Only after an ADD. Rename and delete are one-shot actions whose control is gone afterwards, and
     * dragging focus into a text field somebody did not open is its own bug.
     */
    function restoreSectionFocus() {
        if (!pendingSectionFocus) { return; }
        pendingSectionFocus = false;
        var input = document.getElementById('gl-section-new');
        if (!input) { return; }
        input.value = '';
        // ONLY IF NOBODY HAS MOVED ON. `onRemove` guards its own restore the same way and says why:
        // the refresh settles ~300ms later, and by then the owner may have clicked into the
        // description and started typing. Pulling the caret out of a textarea mid-sentence is worse
        // than making them click back into a field they can see. `<body>` (or the field's own
        // replaced node being gone) is the only state that means "focus was lost to the swap".
        var active = document.activeElement;
        if (active && active !== document.body && document.contains(active)) { return; }
        input.focus();
    }

    /**
     * Put the pick back on the card that carried it across a section boundary.
     *
     * The move refreshes the panel, so `pickedRow` points at a discarded node and `detachDrag` has
     * already dropped it. Restoring by ITEM ID rather than by index: the card moved groups, so its
     * position in the new render is not the one it had.
     */
    function restorePick() {
        if (pendingPickId === null) { return; }
        var id = pendingPickId;
        pendingPickId = null;
        if (!positioning) { return; }
        var row = document.querySelector('.gl-item[data-item-id="' + id + '"]');
        if (!row) { return; }
        togglePicked(row);
        // The grip only exists where ordering is live; in arrange-only mode the pick itself is the
        // affordance and `togglePicked` has already announced it.
        var grab = row.querySelector('[data-gl-grab]');
        if (grab) { grab.focus(); }
        else if (row.scrollIntoView) { row.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
    }

    /* ------------------------------------------------------------------ reorder ---- */

    /**
     * Drag to reorder a RANKED list.
     *
     * Wired only where the server said so. The grid carries `data-gl-reorder` when `can_reorder` is
     * true -- owner, ranked, showing the real sequence, and short enough to render whole -- so this
     * file never decides who may reorder; it reads the decision. `reorder` refuses a partial order by
     * design, so a handle on a truncated page would fail on every use.
     *
     * WeakSet-guarded on the GRID NODE rather than on a flag, because every sort swap and every
     * add/remove refresh replaces `#gl-items` with a fresh element. A boolean would leave the new
     * grid unwired; a `data-` attribute would be copied into htmx's snapshot and make a restored
     * page look wired when its SortableJS instance is gone.
     */
    /**
     * Turn the mode ON. Wires SortableJS against the live grid.
     *
     * `data-gl-reorder` is the server's statement that reordering is POSSIBLE here (owner, ranked,
     * showing the real sequence, short enough to render whole). The mode is the hunter's statement
     * that they want to do it now. Both are required, and they are different things -- conflating
     * them is what made dragging something you could do by accident.
     */
    // EVERY draggable grid, in document order -- which on a sectioned list is the loose bucket
    // followed by each section in its own order, exactly as `_grouped` builds it. That ordering is
    // load-bearing for `fullOrder` below.
    function arrangeGrids() {
        return Array.prototype.slice.call(document.querySelectorAll('[data-gl-arrange]'));
    }

    // Can a drop POSITION be honoured? The server answers per grid: `data-gl-reorder` is present only
    // at the real sequence. Read from the first grid because the flag is a property of the page's
    // sort, not of any one group -- the server sets it on all of them or none.
    function orderingLive(grids) {
        return !!(grids.length && grids[0].hasAttribute('data-gl-reorder'));
    }

    function enterPositioning() {
        var grids = arrangeGrids();
        // BOTH refusals happen before any state changes. `attachDrag` bails when SortableJS is
        // missing, and it used to do so AFTER `positioning` and `[data-positioning]` were already
        // set -- leaving the tray, the "Done" label and the hidden remove buttons over a grid with
        // no drag, no pick-up, and cards that still navigate.
        if (!grids.length || !PP.DragReorderManager) {
            // Not silent: the bar is on screen offering this, so if it cannot be honoured the bar is
            // wrong and should correct itself rather than the press appearing to do nothing.
            syncPositionsVisibility();
            return;
        }
        positioning = true;
        var panel = document.getElementById('gl-items-panel');
        // The flag lives on the PANEL, not on a grid. The panel is the swap TARGET, so its own
        // attributes survive; the grids are swapped content, and htmx would restore their
        // server-rendered attributes on settle and silently drop the flag -- the trap this file
        // already documents twice.
        if (panel) { panel.dataset.positioning = '1'; }
        attachDrag(grids);
        paintPositionsToggle();
        setPositionsStatus('');
        announce(orderingLive(grids)
            ? ('Arranging on. Drag a card, or click one to pick it up and move it with the arrow '
               + 'keys.')
            : ('Arranging on. Drag a card onto another section, or click one to pick it up and move '
               + 'it between sections with the arrow keys.'));
    }

    function exitPositioning(silent) {
        if (!positioning) { return; }
        positioning = false;
        var panel = document.getElementById('gl-items-panel');
        if (panel) { delete panel.dataset.positioning; }
        detachDrag();
        paintPositionsToggle();
        setPositionsStatus('');
        if (!silent) { announce('Arranging off.'); }
    }

    function paintPositionsToggle() {
        var toggle = document.querySelector('[data-gl-positions-toggle]');
        if (!toggle) { return; }
        // NOT `aria-pressed`. This carried both a pressed state AND a changing label, which APG
        // says a toggle button must not do: the two describe the same fact twice and disagree about
        // what the word means -- "Done, pressed" leaves a reader unsure whether "Done" is the state
        // or the action. The label change is the more useful half on a control this size, so the
        // button is an ACTION button naming what it will do next, and the mode change itself is
        // announced through the live region.
        toggle.removeAttribute('aria-pressed');
        var label = toggle.querySelector('[data-gl-positions-label]');
        // The off-state word depends on what the mode can actually do here, and it has to match the
        // server-rendered string in `detail_positions.html` -- otherwise the button says one thing on
        // load and another the first frame after boot, which is a bug this file already shipped once.
        // `data-gl-arrange-only` is the server's own answer, carried on the button rather than
        // re-derived from the grids, so the two cannot disagree.
        var arrangeOnly = toggle.hasAttribute('data-gl-arrange-only');
        if (label) {
            label.textContent = positioning
                ? 'Done'
                : (arrangeOnly ? 'Move games between sections' : 'Edit list positions');
        }

        // The BAR carries the state, not just the button. A label flipping between two words is easy
        // to miss; a full-width surface changing colour is not, and it is the difference between
        // knowing the mode is on and inferring it from the grips.
        var block = document.querySelector('[data-gl-positions]');
        if (block) { block.classList.toggle('is-on', positioning); }

        var hint = document.querySelector('[data-gl-positions-hint]');
        if (hint) {
            // THREE STATES, because the middle one is what was missing: the old copy said "use the
            // arrow keys on its grip", which required tabbing to a 26px control nobody had reason to
            // suspect -- so it described a key that, as far as anyone could tell, did nothing.
            if (arrangeOnly) {
                // THREE STATES HERE TOO, now that the arrow keys work in this mode. The comment that
                // stood here said there was no pick-up "because the arrow keys move a card through an
                // ORDER and this mode has none" -- true of the order, and wrong about the keys, which
                // cross HEADERS here. With no grip rendered (that is a `can_reorder` affordance) the
                // hint is the only place the keyboard path is mentioned at all.
                if (!positioning) {
                    hint.textContent = 'Then drag a card onto another section.';
                } else if (pickedRow) {
                    hint.textContent = 'Arrow keys move it to the next section. '
                        + 'Click it again or press Escape to drop it.';
                } else {
                    hint.textContent = 'Drag a card onto another section, or click one to pick it '
                        + 'up. Moves save as you make them.';
                }
            } else if (!positioning) {
                hint.textContent = 'Then drag a card, or click one to move it with the arrow keys.';
            } else if (pickedRow) {
                hint.textContent = 'Arrow keys move it. Click it again or press Escape to drop it.';
            } else {
                hint.textContent = 'Drag a card, or click one to pick it up. Moves save as you make them.';
            }
        }
    }

    // The SIGHTED save signal. Kept out of the accessibility tree on purpose: the spoken version
    // goes through `announce()` once per action, where a live region here would narrate "Saving"
    // and then "Saved" on top of it for every single move.
    function setPositionsStatus(text) {
        var el = document.querySelector('[data-gl-positions-status]');
        if (!el) { return; }
        el.textContent = text;
        el.hidden = !text;
        el.classList.toggle('is-failed', text === 'Not saved');
    }

    /**
     * Arm every grid on the page, as ONE drag surface.
     *
     * A sectioned list renders a grid per group, so this is N SortableJS instances sharing a `group`
     * name -- which is what makes a card draggable out of one header and into another. A flat list is
     * the same code with N of 1.
     *
     * The two payloads are decided HERE rather than per drop, because they are a property of the page
     * and not of the gesture:
     *
     * - Ordering live (`data-gl-reorder`, i.e. a Ranked list at its real sequence): the drop position
     *   is content, so the whole order travels, and a cross-group drop carries the assignment with it
     *   in the same write. `sort` stays on.
     * - Ordering not live (a Collection, or a Ranked list sorted A-Z): the position under the cursor
     *   belongs to the SORT, and sending it would rewrite the author's sequence to match a view of
     *   it. `sort: false` so the gesture cannot even promise an order, and the drop reports only the
     *   filing. The card then lands wherever the sort puts it, which is honest -- it never claimed
     *   otherwise.
     */
    function attachDrag(grids) {
        // Also drops any pick-up: a swap replaces every row, so `pickedRow` would be pointing at a
        // node that is no longer in the document and the arrow keys would move nothing.
        detachDrag();
        if (!PP.DragReorderManager) { return; }

        var ordering = orderingLive(grids);
        grids.forEach(function (grid) { attachDragTo(grid, ordering); });
        dragGrids = grids;

        // ON THE DOCUMENT, not on a grid. Bound to the grid, the arrow keys only fired while a GRIP
        // had focus -- which meant tabbing to a 26px control nobody had a reason to suspect, so in
        // practice the hint told people to use a key that did nothing. The keys now follow the PICKED
        // card instead, which is a thing you can see. Bound once for the whole surface rather than
        // once per grid, or a sectioned list would move a card N places per press.
        //
        // BOUND IN BOTH MODES. Gating this on `ordering` left a sectioned Collection with no keyboard
        // path whatsoever while its button said "Move games between sections" -- the pointer could
        // cross a header and nothing else could. In arrange-only mode the keys move a card ACROSS
        // headers and do nothing within one, which is exactly what that mode is.
        document.addEventListener('keydown', onPositionKey);
    }

    function attachDragTo(grid, ordering) {
        reorderManagers.push(new PP.DragReorderManager({
            container: grid,
            itemSelector: '.gl-item',
            // ONE SHARED GROUP so a card can leave its own grid. Constant rather than derived from
            // the list id: the page only ever shows one list, and two Sortables can only exchange
            // items when their group names match exactly.
            group: 'gl-items',
            sort: ordering,
            onMove: function (itemId, evt) { onCrossSectionDrop(grid, itemId, evt, ordering); },
            // NO `handleSelector`: the whole card drags. A grip-only drag was the safe first cut --
            // the card is an <a>, so anything else risked a tap being read as the wrong gesture --
            // but it makes the one action the mode exists for a 26px target on a 166px card, and
            // people reach for the thing itself. `onCardClick` below removes the risk instead of
            // designing around it. The grip stays: it is the keyboard affordance and the visual
            // statement that a card is movable.
            //
            // LONG-PRESS ON TOUCH, immediate with a mouse. A finger resting on a card is how you
            // begin a scroll, so touch needs a deliberate hold before a drag arms -- the gesture
            // every phone home screen already teaches. `delayOnTouchOnly` keeps the mouse instant,
            // and the threshold inside the manager lets a scroll cancel a pending pick-up.
            delay: 320,
            delayOnTouchOnly: true,
            // The grip is a button; a drag starting ON it still works, because it is inside the
            // draggable item. Nothing here needs excluding while the remove control is hidden in
            // this mode -- listed for the next control that is not.
            // `fullOrder()` and NOT the `allItemIds` the manager hands over: that argument is this
            // grid's rows, which on a sectioned list is one group out of several. `svc.reorder`
            // refuses a partial ordering by design, so posting it would turn every drag inside a
            // section into a refusal.
            onReorder: function () {
                saveOrder(grid, fullOrder(), 'Order saved.');
            },
            // Sortable swallows the click that follows a drop -- except on Chrome for Android, where
            // it skips registering that listener entirely. Without this, finishing a drag there
            // immediately picks the card back up (or drops the one that was held). Cleared on the
            // next tick, so a real click a moment later still works.
            onEnd: function () {
                justDragged = true;
                window.setTimeout(function () { justDragged = false; }, 0);
            },
        }));

        // WHILE ARRANGING, A CARD DOES NOT NAVIGATE. Now that the whole card is the drag surface, a
        // click that the browser did not classify as a drag would otherwise leave the page in the
        // middle of rearranging it -- and the shorter the drag, the likelier that is. The mode you
        // are in is the same answer a phone home screen gives while its icons are jiggling: tapping
        // does nothing until you leave it.
        grid.addEventListener('click', onCardClick);
    }

    /**
     * A card landed in a DIFFERENT grid -- it changed section.
     *
     * `evt.to`, AND NOT `grid`. SortableJS routes its `end` event to the Sortable the drag STARTED
     * in, so the manager that runs this — and therefore `grid`, which is its own container — is the
     * ORIGIN. Reading the section id off it posted the card straight back where it came from: the
     * arrange-only path then refreshed and the card visibly snapped home, and the ranked path wrote
     * the right order with the wrong filing, which is exactly the "correctly placed and wrongly
     * filed" state the one-request design exists to prevent. `utils.js` asserted the opposite routing
     * in a comment for a long time; the bundle says otherwise and is quoted there.
     *
     * The attribute is read for PRESENCE and used for its value, which may legitimately be empty: the
     * loose bucket is a real destination ("in no section"), and treating empty as missing is what
     * would make un-filing a card impossible.
     */
    function onCrossSectionDrop(grid, itemId, evt, ordering) {
        var landed = (evt && evt.to) || grid;
        var sectionId = landed.dataset.sectionId || '';
        if (ordering) {
            // One write. The order AND the filing changed, and sending them separately leaves a
            // window where the card sits in the right place under the wrong header -- which looks
            // correct until the page is reloaded. `svc.reorder` takes both and applies the assignment
            // first, so a refused section leaves the order untouched.
            //
            // `refresh: true` unlike a within-grid reorder, because a card CHANGING GROUP changes
            // things the optimistic repaint cannot reach: the count beside each header, and whether
            // the group it left still exists at all (the loose bucket is omitted when empty, so
            // emptying it by hand leaves a header reading 0 over nothing). Renumbering alone was
            // enough while a drag could only move a card within one grid.
            saveOrder(grid, fullOrder(), 'Moved.',
                      { movedItem: itemId, section: sectionId, refresh: true });
            return;
        }
        // Filing only. There is no order to send: `sort: false` means the drop index is wherever the
        // cursor happened to be over a grid the SERVER sorts, and posting it would overwrite the
        // author's sequence with the shape of a view.
        saveAssignment(itemId, sectionId, evt);
    }

    /**
     * The whole list's order, across every grid, in document order.
     *
     * `svc.reorder` refuses a partial ordering by design, so a drop inside one section cannot post
     * just that section -- the server would see a subset and (correctly) reject it. Document order is
     * the right answer rather than a workaround: `_grouped` renders the loose bucket first and then
     * the sections in their own order, so reading top to bottom IS the sequence a reader sees, and
     * writing it back is what makes continue-through numbering count down the page.
     */
    function fullOrder() {
        var ids = [];
        arrangeGrids().forEach(function (grid) {
            ids = ids.concat(itemIdsIn(grid));
        });
        return ids;
    }

    // Leaving the mode must actually leave it: a live Sortable instance on a grid whose grips are
    // now `display: none` would still accept a drag begun on the card itself.
    function detachDrag() {
        for (var i = 0; i < reorderManagers.length; i++) { reorderManagers[i].destroy(); }
        reorderManagers = [];
        document.removeEventListener('keydown', onPositionKey);
        dropPicked(true);
        dragGrids.forEach(function (grid) { grid.removeEventListener('click', onCardClick); });
        dragGrids = [];
    }

    /**
     * A click PICKS A CARD UP rather than doing nothing.
     *
     * Suppressing the navigation was necessary once the whole card became the drag surface, but it
     * left a click with no meaning at all -- and a card that visibly ignores you reads as broken.
     * Giving the click a job solves that and the keyboard problem at the same time: the picked card
     * is the one the arrow keys move, so the feature is reachable without a pointer AND without
     * knowing the grip exists.
     *
     * The same click drops it again, which is the behaviour every file manager and home screen has:
     * you should never have to hunt for the way out of a selection you made by accident.
     */
    function onCardClick(e) {
        if (!positioning || justDragged) { return; }
        var card = e.target.closest && e.target.closest('.pp-gcard');
        if (!card) { return; }
        // The navigation, not the event -- the grip's own click still has to reach it.
        e.preventDefault();
        var row = card.closest('.gl-item');
        if (row) { togglePicked(row); }
    }

    function setGrabPressed(row, pressed) {
        var grab = row && row.querySelector('[data-gl-grab]');
        if (grab) { grab.setAttribute('aria-pressed', pressed ? 'true' : 'false'); }
    }

    function togglePicked(row) {
        if (pickedRow === row) { dropPicked(); return; }
        dropPicked(true);
        pickedRow = row;
        row.classList.add('is-picked');
        // The GRIP carries the state, as a toggle button. Announcing the pick-up once through the
        // live region told somebody at the moment it happened and then left no way to ask again --
        // navigate away and back, or get interrupted by the "Saved" message, and nothing anywhere
        // says which card is held. "Reorder Elden Ring, toggle button, pressed" is that statement,
        // and it is on the control that already means this card.
        setGrabPressed(row, true);
        var card = row.querySelector('.pp-gcard');
        var name = (card && card.getAttribute('aria-label')) || 'Card';
        announce(name + ' picked up. Arrow keys move it, Escape drops it.');
        paintPositionsToggle();
    }

    function dropPicked(silent) {
        if (!pickedRow) { return; }
        pickedRow.classList.remove('is-picked');
        setGrabPressed(pickedRow, false);
        pickedRow = null;
        if (!silent) { announce('Dropped.'); }
        paintPositionsToggle();
    }

    /**
     * Keep the mode honest across swaps.
     *
     * Every sort change, add and remove replaces `#gl-items`, and the server's answer about whether
     * reordering is possible can change with it -- sorting a ranked list A-Z takes the capability
     * away. So after each settle: re-attach to the NEW grid if the mode is on and still allowed,
     * leave the mode if it is not, and show or hide the toggle to match. The toggle is rendered once
     * in the page header and never swapped, so without this it would sit there offering a mode the
     * grid below it can no longer support.
     */
    function syncPositioning() {
        var grids = arrangeGrids();
        syncPositionsVisibility();
        if (!grids.length) { return; }
        if (positioning) { attachDrag(grids); }
    }

    /**
     * Decide whether the bar is on screen, from the three things that actually govern it.
     *
     * 1. The SERVER allows reordering here (`data-gl-reorder` on the live grid) -- owner, ranked,
     *    real sequence, whole list rendered.
     * 2. The editor is open. That is the deliberate-entry half; without it the handles are live on a
     *    page nobody said they were editing, which is what made dragging feel accidental.
     *
     * There WAS a third: whether the type radio currently selected was still the ranked one. It
     * existed because saving a type change meant a full page reload, so between switching the radio
     * and saving, the bar would otherwise have offered to reorder a list the hunter had just said
     * was a shelf. Saving now refreshes the bar's slot from the server instead, so the condition is
     * gone along with the reload -- and what is left is more honest, because the list really is
     * still ranked until the save lands.
     */
    function syncPositionsVisibility() {
        // THE GRID DECIDES, NOT THE BAR. This used to bail when `[data-gl-positions]` was missing --
        // and switching Ranked -> Collection DELETES it, because the slot renders the bar only under
        // `can_reorder`. `exitPositioning` is the only thing that turns the mode off, so it became
        // unreachable in exactly the case that most needs it, and the mode stayed on forever:
        // `[data-positioning]` kept every remove button hidden on a Collection, the keydown listener
        // stayed bound, and `pickedRow` went on pointing at a detached row whose grid still carried
        // `data-reorder-url` -- so arrow keys silently rewrote positions on a list nobody could see.
        //
        // Reordering the two halves is the whole fix: leave the mode FIRST, then update the bar if
        // there still is one.
        var show = arrangeGrids().length > 0 && editorOpen;
        if (!show) { exitPositioning(true); }

        // The bar can be on screen with NO arrangeable grid behind it: a member owner of an empty or
        // section-less list still gets the "Add a section" row, which is not a drag affordance and
        // must not vanish with one. So the block's own visibility follows the editor, and only the
        // MODE follows the grids.
        var block = document.querySelector('[data-gl-positions]');
        if (block) { block.hidden = !editorOpen; }
    }

    //: Arrow keys move an entry one place through the ORDER, which is the axis a ranked list is
    //: about. Left/Up go earlier and Right/Down go later -- the grid wraps across several columns,
    //: so "up" cannot mean "one row up" without depending on the viewport width, and a keyboard user
    //: moving an item to rank 1 wants N presses in one direction rather than arithmetic.
    var GRAB_EARLIER = ['ArrowUp', 'ArrowLeft'];
    var GRAB_LATER = ['ArrowDown', 'ArrowRight'];

    // Text fields are the reason this cannot be a bare document listener: the identity editor is
    // OPEN whenever this mode is (that is how you get here), so an arrow key pressed while moving
    // the caret through a list's name would otherwise also move a card.
    function isTyping(el) {
        if (!el) { return false; }
        var tag = el.tagName;
        return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
    }

    function onPositionKey(e) {
        if (!positioning || isTyping(e.target)) { return; }

        if (e.key === 'Escape' && pickedRow) {
            e.preventDefault();
            dropPicked();
            return;
        }

        var earlier = GRAB_EARLIER.indexOf(e.key) !== -1;
        if (!earlier && GRAB_LATER.indexOf(e.key) === -1) { return; }

        // A FOCUSED GRIP WINS over the picked card. Both paths exist on purpose -- the grip is what a
        // keyboard reader reaches by tabbing and what a screen reader announces per card, and picking
        // a card up is what somebody with a pointer does -- and when both are live, the one the
        // hunter is actually touching is the focused one.
        var grab = e.target.closest && e.target.closest('[data-gl-grab]');
        var row = (grab && grab.closest('.gl-item')) || pickedRow;
        if (!row) { return; }
        var grid = row.parentElement;
        if (!grid) { return; }

        // IN ARRANGE-ONLY MODE THE KEYS DO ONE THING: cross a header. There is no order to move
        // through -- the page is showing a sort the list does not own -- so a within-group step would
        // rearrange something that snaps back on the next render. Skipping straight to the boundary
        // step is what gives this mode a keyboard at all; it had none, while its own button read
        // "Move games between sections".
        var neighbour = orderingLive(arrangeGrids())
            ? (earlier ? row.previousElementSibling : row.nextElementSibling)
            : null;

        if (!neighbour || !neighbour.classList.contains('gl-item')) {
            // AT THE EDGE OF ITS OWN GROUP -- which on a sectioned list is not the edge of anything
            // the hunter cares about. The keys stopped dead here, so a card at the top of "Backlog"
            // could not be moved into "Playing" by any key at all: a pointer could cross a boundary
            // and a keyboard could not, which is the whole feature missing for anyone who cannot
            // drag. Stepping into the adjacent grid is the same act the drag performs.
            var moved = stepIntoNeighbourGrid(row, grid, earlier);
            if (!moved) { return; }
            e.preventDefault();
            // The SECTION changed, so this saves through whichever of the two writes the page is
            // entitled to -- the same fork `onCrossSectionDrop` makes for a drag, because a keyboard
            // move across a header is the same act and must not reach a different endpoint.
            var landedIn = moved.grid.dataset.sectionId || '';
            if (orderingLive(arrangeGrids())) {
                announceAndSave(row, moved.grid, {
                    movedItem: row.dataset.itemId, section: landedIn, refresh: true,
                });
            } else {
                saveAssignment(row.dataset.itemId, landedIn, null);
            }
            // NOT `grab.focus()`: the refresh below replaces every row, so focusing a node that
            // is about to be discarded lands the caret on <body>. The pick is carried across
            // instead, and `restorePick` re-focuses the grip on the new row when there is one.
            // (In arrange-only mode there is no grip at all -- `[data-gl-grab]` renders only under
            // `can_reorder` -- so `grab` was always null on that path anyway.)
            if (row === pickedRow) { pendingPickId = row.dataset.itemId; }
            if (row.scrollIntoView) { row.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
            return;
        }

        e.preventDefault();
        if (earlier) { grid.insertBefore(row, neighbour); }
        else { grid.insertBefore(neighbour, row); }

        announceAndSave(row, grid, null);

        // `grab` is only set on the focused-grip path, and calling `.focus()` unconditionally would
        // throw on the picked-card path -- which is now the common one.
        if (grab) { grab.focus(); }
        // The card keeps its pick-up across the move, so the arrow key repeats. Dropping it after
        // each press would make moving something five places a five-click, five-press alternation.
        // A long list can also scroll the row out of view, and nothing else brings it back.
        if (row === pickedRow && row.scrollIntoView) {
            row.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }
    }

    /**
     * Move a row into the group before or after its own, at the near edge.
     *
     * The keyboard equivalent of dragging across a header. The ADJACENT group, empty or not: an empty
     * section is a legitimate destination -- filling it is the reason it exists -- so skipping past
     * one would make the single section a keyboard user most wants to reach the one they cannot.
     *
     * Returns the destination grid, or null when there is no group that way, which is how the caller
     * knows to leave the arrow key its normal meaning.
     */
    function stepIntoNeighbourGrid(row, grid, earlier) {
        var grids = arrangeGrids();
        var at = grids.indexOf(grid);
        var next = at + (earlier ? -1 : 1);
        if (at === -1 || next < 0 || next >= grids.length) { return null; }

        var target = grids[next];
        // Moving EARLIER lands at the end of the group above; moving LATER lands at the start of the
        // one below. Either way the card comes to rest against the boundary it just crossed, so a
        // second press carries on in the same direction rather than bouncing back off it.
        if (earlier) { target.appendChild(row); }
        else { target.insertBefore(row, target.firstElementChild); }
        // NOTHING TO CLEAR: the empty-section box is a `::before` on the grid gated on `:empty`,
        // so it disappears the instant the grid has a child and comes back on its own if the group
        // empties again. It was a real element once, and had to be removed by hand here.
        return { grid: target };
    }

    // SPOKEN THE WAY IT IS PRINTED. `rankOf` counts in whichever numbering mode the list is in, so
    // the live region and the plate on the card cannot say different numbers -- which they did
    // whenever restart-numbering was on, because the announcement counted straight through regardless.
    function announceAndSave(row, grid, move) {
        var where = rankOf(row);
        var message = 'Moved.';
        if (where) {
            message = 'Moved to number ' + where.rank + ' of ' + where.total
                + (where.section ? ' in ' + where.section : '') + '.';
        }
        return saveOrder(grid, fullOrder(), message, move);
    }

    // DOM order IS the order. Read here rather than trusted from an event, so the keyboard path and
    // the drag path agree by construction.
    function itemIdsIn(grid) {
        var ids = [];
        var rows = grid.querySelectorAll('.gl-item[data-item-id]');
        for (var i = 0; i < rows.length; i++) { ids.push(rows[i].dataset.itemId); }
        return ids;
    }

    /**
     * Persist a dragged order, and repaint the numerals.
     *
     * The numerals are renumbered HERE rather than by re-rendering the panel from the server. A
     * refresh would be the honest-looking choice and is the wrong one: it destroys the grid node
     * mid-interaction, which tears down the SortableJS instance the hunter is still holding, and it
     * spends a round trip redrawing forty covers that did not change. The rank text is the only
     * thing a reorder can alter on screen, so it is the only thing repainted.
     *
     * Repainted OPTIMISTICALLY, before the response: SortableJS has already moved the row, so
     * leaving the numbers until the server answers shows "3, 1, 2" against the new arrangement for
     * the length of a round trip. A failure reverts by refreshing from the server, which is the one
     * source of truth about what the order actually is.
     */
    function saveOrder(grid, itemIds, successMessage, move) {
        renumber();
        setPositionsStatus('Saving…');

        var body = new FormData();
        for (var i = 0; i < itemIds.length; i++) { body.append('item_ids[]', itemIds[i]); }
        // THE CROSS-SECTION HALF, when there is one. Sent on the same request as the order because a
        // card that changed both has to change both or neither -- two requests can leave it correctly
        // placed and wrongly filed, which reads as correct until the next reload.
        //
        // `section` is appended even when EMPTY. Empty means the loose bucket, which is a real
        // destination, and the server tells "no move" from "moved out of every section" by whether
        // `moved_item` was sent -- not by whether `section` has a value. Skipping the empty one here
        // would make dragging a card out of a section impossible.
        if (move && move.movedItem) {
            body.append('moved_item', move.movedItem);
            body.append('section', move.section || '');
        }

        // SERIALISED, because two drags in quick succession are a LAST-WRITER-WINS race that nothing
        // downstream can detect. Both orders are complete and valid, so both succeed; if they reach
        // the row lock out of order the database keeps the EARLIER one while the grid shows the
        // later one, with no error anywhere and no sign of it until the next reload. Fire-and-forget
        // is only safe for a write whose result does not depend on the others, and an ordering is
        // the opposite of that. `refreshItems` carries `refreshSeq` for the same class of bug.
        //
        // A chain rather than a sequence-number guard: the point is not to discard the stale
        // response, it is to stop the stale REQUEST from being written second.
        // A GENERATION, on top of the chain. The chain alone stops the writes racing; it does not
        // stop a queued write from UNDOING the recovery. Two quick arrow presses, the first POST
        // fails: its handler refreshes the grid back to the server's order, and then the second
        // request goes out carrying a body captured from the pre-refresh DOM -- a complete, valid
        // order that includes the move the hunter was just told was not saved. The server takes it,
        // the pill flips to "Saved", and the screen now disagrees with the database in the other
        // direction. Bumping the generation on failure makes everything already queued stand down.
        var generation = orderGen;
        pendingSaves += 1;
        orderChain = orderChain
            .catch(function () { /* a previous failure already reported itself; do not block this */ })
            .then(function () {
                if (generation !== orderGen) { return null; }
                return postJson(grid.dataset.reorderUrl, body);
            })
            .then(function (result) {
                if (generation !== orderGen || result === null) { return; }
                // Only when nothing is still in flight. "Saved" used to appear as soon as the FIRST
                // of two queued writes came back, so the pill claimed success over an outstanding
                // request and could then flip to "Not saved".
                if (pendingSaves <= 1) { setPositionsStatus('Saved'); }
                // ONE spoken message per action, said after the write rather than before it, so
                // "moved to number 3" is only ever heard about a move that actually persisted.
                announce(successMessage || 'Order saved.');
                // Only a group CHANGE asks for this, and only after the write landed -- see
                // `onCrossSectionDrop`. A plain reorder deliberately does not: refreshing would spend
                // a round trip redrawing forty covers that did not change, and the rank text is the
                // only thing a reorder can alter on screen.
                if (move && move.refresh) {
                    return refreshItems().catch(function (err) {
                        // The write LANDED; only the view is stale. Saying it was not saved would
                        // send somebody to redo a move they already made.
                        logFailure('items refresh after a cross-section drop', err);
                        announce('Moved. Reload the page to see the counts update.');
                    });
                }
            })
            .catch(function (err) {
                orderGen += 1;
                setPositionsStatus('Not saved');
                toastError(err, 'That new order could not be saved.');
                // The client and the server now disagree about the order, and the client is the one
                // that is wrong. Re-render rather than trying to undo the drag by hand.
                return refreshItems().catch(function (refreshErr) {
                    logFailure('items refresh after a failed reorder', refreshErr);
                    announce('The list could not be restored. Reload the page.');
                });
            })
            .then(function () { pendingSaves = Math.max(0, pendingSaves - 1); });
        return orderChain;
    }

    // 1-based, matching the template's `position|add:1`.
    //
    // BOTH the visible numeral and the link's `aria-label` -- the label is where the rank actually
    // reaches a screen reader (an `aria-label` overrides the name computed from descendants), so
    // repainting only the numeral would leave every row announcing its pre-drag position. An earlier
    // version repainted an `sr-only` span inside the link instead, which nothing ever announced.
    // ACROSS EVERY GRID, and it has to mirror `GameListDetailView._number` exactly -- this is the
    // optimistic repaint of a number the server will compute again on the next render, and a
    // disagreement between the two shows up as numerals that change on reload.
    //
    // Both modes count DOWN THE PAGE; only the reset differs. A per-grid `i + 1` was what this did
    // before sections, and on a sectioned list it would restart at 1 under every header regardless of
    // the setting -- silently showing the restart mode to somebody who chose continue-through.
    function renumber() {
        var restart = restartNumbering();
        var running = 0;
        arrangeGrids().forEach(function (grid) {
            if (restart) { running = 0; }
            var rows = grid.querySelectorAll('.gl-item');
            for (var i = 0; i < rows.length; i++) {
                running += 1;
                var badge = rows[i].querySelector('.gl-rank');
                if (badge) { badge.textContent = String(running); }

                var card = rows[i].querySelector('.pp-gcard');
                if (!card) { continue; }
                var label = card.getAttribute('aria-label') || '';
                // Replace an existing "Number N: " prefix rather than stacking another one on.
                card.setAttribute('aria-label',
                                  'Number ' + running + ': ' + label.replace(/^Number \d+:\s*/, ''));
            }
        });
    }

    /**
     * Which numbering mode to repaint in.
     *
     * THE CHECKBOX FIRST, because the toggle saves and repaints without a swap, so reading the value
     * the page was rendered with would renumber to the mode the hunter just left.
     *
     * THE SERVER'S ATTRIBUTE SECOND, and that fallback is not belt-and-braces. The checkbox renders
     * only under `can_manage_sections`, which requires membership -- but arranging does not, by
     * design, so a LAPSED member owns a sectioned ranked list with restart-numbering on and no
     * checkbox on the page. Reading the missing box as "continue through" repainted every badge and
     * every card label into the wrong mode on each drag, and the next load put them back: exactly the
     * client/server disagreement this repaint exists to avoid.
     *
     * Neither present (a flat list, or a Collection) means continue-through, which is the only
     * meaning it can have with nothing to restart at.
     */
    function restartNumbering() {
        var box = document.querySelector('[data-gl-numbering]');
        if (box) { return !!box.checked; }
        var root = itemsRoot();
        return !!(root && root.dataset.restartNumbering);
    }

    /**
     * Where a row sits, counted the way the page PRINTS it.
     *
     * `onPositionKey` announced `fullOrder().indexOf(id) + 1`, which is always a continue-through
     * number -- so with restart-numbering on, a card whose plate read "1" was announced as "number 24
     * of 40". A sighted owner and a blind one were told two different facts about the same move, and
     * the spoken one was the one nobody could check.
     *
     * Mirrors `renumber`'s walk deliberately rather than reading the badge it paints: the badge is
     * repainted inside `saveOrder`, after the message has been composed.
     */
    function rankOf(row) {
        var restart = restartNumbering();
        var grids = arrangeGrids();
        var running = 0;
        for (var g = 0; g < grids.length; g++) {
            if (restart) { running = 0; }
            var rows = grids[g].querySelectorAll('.gl-item');
            for (var i = 0; i < rows.length; i++) {
                running += 1;
                if (rows[i] === row) {
                    // The total is the span the NUMBER runs across, which is the group when the
                    // counter restarts in it and the whole list when it does not. "2 of 5" about a
                    // list of forty is not a smaller truth, it is a different one.
                    return {
                        rank: running,
                        total: restart ? rows.length : fullOrder().length,
                        section: restart ? sectionNameFor(grids[g]) : null,
                    };
                }
            }
        }
        return null;
    }

    // The heading immediately above a grid, which is how `detail_group.html` lays a group out. Used
    // only to make a restarted number unambiguous when it is spoken.
    function sectionNameFor(grid) {
        var head = grid.previousElementSibling;
        var name = head && head.querySelector && head.querySelector('.gl-section__name');
        return name ? name.textContent.trim() : null;
    }

    /**
     * Persist a FILING -- this game now belongs under that header -- and nothing else.
     *
     * The counterpart to `saveOrder`, used where a drop position has no meaning (see `attachDrag`).
     * It rides the same chain and the same generation counter, because the two never both apply on
     * one page but the failure handling is identical and a half-copied version of it is how the
     * recovery path rots.
     *
     * On success the panel is REFRESHED rather than left as dropped. SortableJS has already put the
     * card where the cursor let go, and the server sorts each group independently -- so without this
     * the card sits out of alphabetical order until something else re-renders, which reads as a bug
     * on the one sort whose whole promise is that it is alphabetical. The refresh also repaints both
     * section counts, which the drop just changed.
     */
    function saveAssignment(itemId, sectionId, evt) {
        setPositionsStatus('Saving…');
        var url = assignUrlFor(itemId, evt);
        if (!url) {
            // Nothing to post to means the markup and this code disagree, which is a bug rather than
            // a hunter error -- so it reports loudly instead of failing silently over a card that has
            // visibly already moved.
            logFailure('a section assignment with no endpoint', new Error('missing assign url'));
            setPositionsStatus('Not saved');
            return refreshItems().catch(function () {});
        }

        var body = new FormData();
        body.append('section', sectionId || '');

        var generation = orderGen;
        pendingSaves += 1;
        orderChain = orderChain
            .catch(function () { /* a previous failure already reported itself */ })
            .then(function () {
                if (generation !== orderGen) { return null; }
                return postJson(url, body);
            })
            .then(function (result) {
                if (generation !== orderGen || result === null) { return; }
                if (pendingSaves <= 1) { setPositionsStatus('Saved'); }
                announce('Moved.');
                return refreshItems().catch(function (err) {
                    // The write LANDED; only the view is stale. Say so rather than implying the move
                    // was lost, which would send somebody to redo a move they already made.
                    logFailure('items refresh after a section assignment', err);
                    announce('Moved. Reload the page to see it in order.');
                });
            })
            .catch(function (err) {
                orderGen += 1;
                setPositionsStatus('Not saved');
                toastError(err, 'That move could not be saved.');
                return refreshItems().catch(function (refreshErr) {
                    logFailure('items refresh after a failed assignment', refreshErr);
                    announce('The list could not be restored. Reload the page.');
                });
            })
            .then(function () { pendingSaves = Math.max(0, pendingSaves - 1); });
        return orderChain;
    }

    // The endpoint is per ITEM, so it is read off the row the drag moved rather than built from a
    // base path in here -- the server owns URL shapes, and a hand-assembled string is what breaks
    // silently the day a route moves. `evt.item` is the dragged row itself, which SortableJS has
    // already moved into the destination grid.
    function assignUrlFor(itemId, evt) {
        var row = (evt && evt.item)
            || document.querySelector('.gl-item[data-item-id="' + itemId + '"]');
        return (row && row.dataset.assignUrl) || null;
    }

    /* ---------------------------------------------------------------- sections ---- */

    /**
     * Create, rename, delete and the numbering choice.
     *
     * Wired in one place because they are one capability, and they all end the same way: a refresh of
     * the items panel WITH its chrome. A section change moves more than the grid -- the arrange bar
     * is outside the swap target and its contents depend on whether any section exists at all -- so
     * splicing a header in client-side would leave the bar describing the list as it was a moment
     * ago. One round trip, and the server stays the only thing that decides what a sectioned list
     * looks like.
     *
     * Delegated from the panel rather than bound per control, because every one of these nodes is
     * replaced by the refresh their own handler triggers.
     */
    /**
     * Run a section write BEHIND whatever arrangement writes are still in flight.
     *
     * The chain existed for the two drag writers only, and section writes went straight out beside
     * them. That is a real race, not a tidiness point: a queued `saveOrder` carries a body captured
     * from the DOM as it was, so a section delete that lands first leaves the reorder posting
     * `section=<deleted id>`. The server refuses it, `orderGen` bumps, the pill flips to "Not saved"
     * and the owner is told a move failed that they never made — after a delete that succeeded.
     *
     * Joining the chain also means the section write sees a consistent list, and the `refreshItems`
     * it ends with cannot land in the middle of an outstanding reorder.
     */
    function queueSectionWrite(run) {
        orderChain = orderChain
            .catch(function () { /* a previous failure already reported itself */ })
            .then(run);
        return orderChain;
    }

    function wireSections() {
        var add = document.querySelector('[data-gl-section-add]');
        if (add && !wired.has(add)) {
            wired.add(add);
            add.addEventListener('submit', function (e) {
                e.preventDefault();
                onSectionAdd(add);
            });
        }

        var numbering = document.querySelector('[data-gl-numbering]');
        if (numbering && !wired.has(numbering)) {
            wired.add(numbering);
            numbering.addEventListener('change', function () { onNumberingChange(numbering); });
        }
    }

    function onSectionAdd(form) {
        var input = form.querySelector('input[name="name"]');
        var name = input ? input.value.trim() : '';
        // The service refuses an empty name anyway; stopping here keeps a stray Enter in an empty
        // field from spending a round trip to be told so.
        if (!name) { if (input) { input.focus(); } return; }
        if (form.dataset.busy) { return; }
        form.dataset.busy = '1';

        var body = new FormData();
        body.append('name', name);
        queueSectionWrite(function () {
            return postJson(form.dataset.createUrl, body)
                .then(function (data) {
                    announce('Section "' + (data && data.name ? data.name : name) + '" added.');
                    // FOCUS COMES BACK, because `refreshItems(true)` out-of-band swaps the whole bar
                    // and takes this very field with it -- so adding three sections meant tabbing from
                    // the top of the document twice. `restoreRemoveFocus` solves the same problem for
                    // the remove button; this is the same debt on a field somebody is mid-sentence in.
                    pendingSectionFocus = true;
                    // THE REFRESH GETS ITS OWN CATCH. Without it a failed re-render fell into the
                    // handler below and reported "That section could not be added" over a section
                    // that exists -- so the owner adds it again, and `create_section` does not dedupe
                    // names. Two identical headers and two of twenty slots spent. `onRemove`, the
                    // adder and `saveOrder` all carry this guard and say the same thing.
                    return refreshItems(true).catch(function (refreshErr) {
                        pendingSectionFocus = false;
                        logFailure('items refresh after adding a section', refreshErr);
                        announce('Section added. Reload the page to see it.');
                    });
                })
                .catch(function (err) {
                    pendingSectionFocus = false;
                    toastError(err, 'That section could not be added.');
                })
                .finally(function () { form.dataset.busy = ''; });
        });
    }

    function onSectionRename(btn) {
        // A `prompt()` rather than an inline field, and the same reasoning the delete confirm
        // carries: the site's dialog primitive is for things you are composing, and this is one short
        // string on a thing that already exists. It also keeps the header's markup identical for
        // owners and readers, which is what stops the grid having two shapes.
        var current = btn.dataset.sectionName || '';
        var next = window.prompt('Rename this section', current);
        if (next === null) { return; }
        next = next.trim();
        if (!next || next === current) { return; }
        if (btn.dataset.busy) { return; }
        btn.dataset.busy = '1';

        var body = new FormData();
        body.append('name', next);
        queueSectionWrite(function () {
            return postJson(btn.dataset.renameUrl, body)
            .then(function (data) {
                announce('Renamed to "' + (data && data.name ? data.name : next) + '".');
                // The NAME is not the only thing that moved: the header, its controls and the
                // `aria-labelledby` on the grid all carry it, so the panel re-renders rather than
                // three nodes being patched in step. Its own catch, so a failed re-render is not
                // reported as a failed rename over a name that is already stored.
                return refreshItems(true).catch(function (refreshErr) {
                    logFailure('items refresh after renaming a section', refreshErr);
                    announce('Renamed. Reload the page to see it.');
                });
            })
            .catch(function (err) { toastError(err, 'That section could not be renamed.'); })
            .finally(function () { btn.dataset.busy = ''; });
        });
    }

    function onSectionDelete(btn) {
        var name = btn.dataset.sectionName || 'this section';
        // SAYS WHAT SURVIVES. Deleting a section keeps every game on the list -- they fall back into
        // the loose bucket -- and without that sentence this reads as "delete these twelve games",
        // which is the one thing it does not do.
        if (!window.confirm('Delete the section "' + name + '"?\n\n'
                            + 'Its games stay on the list and move back to "Not in a section".')) {
            return;
        }
        if (btn.dataset.busy) { return; }
        btn.dataset.busy = '1';

        // DISARMED IMMEDIATELY, before the write is even queued. The header and its grid stay on
        // screen for the whole round trip, and a card dropped into them in that window queues a
        // reorder carrying a section id that is about to stop existing -- the server refuses, and the
        // owner is told their ORDER could not be saved, which was never the problem. Stripping the
        // attributes takes the grid out of `arrangeGrids()` at once, so it is not a drop target and
        // not a keyboard destination.
        var doomed = document.querySelector(
            '[data-gl-arrange][data-section-id="' + (btn.dataset.sectionId || '') + '"]');
        if (doomed && btn.dataset.sectionId) {
            doomed.removeAttribute('data-gl-arrange');
            doomed.removeAttribute('data-gl-reorder');
        }

        queueSectionWrite(function () {
            return postJson(btn.dataset.deleteUrl, new FormData())
                .then(function () {
                    announce('Section "' + name + '" deleted. Its games are still on the list.');
                    // Its own catch: reporting "could not be deleted" over a section that is gone
                    // (and whose games have already been orphaned) sends the owner looking for a
                    // header that no longer exists.
                    return refreshItems(true).catch(function (refreshErr) {
                        logFailure('items refresh after deleting a section', refreshErr);
                        announce('Section deleted. Reload the page to see the list.');
                    });
                })
                .catch(function (err) { toastError(err, 'That section could not be deleted.'); })
                .finally(function () { btn.dataset.busy = ''; });
        });
    }

    /**
     * Restart-per-section, or straight through.
     *
     * Saves through `list_update` -- the same call the identity editor uses -- because it is a
     * property of the list exactly as its type is, and a field with two writers is a field that
     * drifts. Repaints the numerals immediately rather than refreshing: the ranks are the only thing
     * on screen this can change, and a swap mid-arrange would tear down the Sortable instances the
     * hunter is holding.
     */
    function onNumberingChange(box) {
        var wanted = box.checked;
        var body = new FormData();
        // 'on'/'' is what a checkbox posts, and `safe_bool` on the other end reads both -- the
        // literal 'true' a `=== 'true'` comparison would need is the bug `is_public` already shipped.
        body.append('restart_numbering', wanted ? 'on' : '');
        renumber();
        queueSectionWrite(function () {
            return postJson(box.dataset.updateUrl, body)
            .then(function () {
                announce(wanted ? 'Numbering restarts in each section.'
                                : 'Numbering runs straight through.');
            })
            .catch(function (err) {
                // Put the control back where the DATA is, not where the click left it, and repaint to
                // match -- otherwise the box says one thing and the numerals say the other.
                box.checked = !wanted;
                renumber();
                toastError(err, 'That numbering choice could not be saved.');
            });
        });
    }

    function boot(first) {
        handledGrid = null;
        searchField = null;
        // THE MODE'S STATE TOO. This file's header commits to honouring the `onPageReady` restore
        // contract even though the current htmx config never fires it -- and under that contract
        // these six carried over: a restored page would paint "Done" on a toggle whose panel has no
        // `[data-positioning]`, show the bar over a CLOSED editor, and keep a live Sortable and a
        // document keydown bound to a discarded grid.
        detachDrag();
        positioning = false;
        editorOpen = false;
        orderChain = Promise.resolve();
        pendingSaves = 0;
        pendingSectionFocus = false;
        pendingPickId = null;
        wireAdder();
        wireIdentityEditor();
        wireVisibility();
        wirePositioning();
        wireSections();
        initReveal();
        if (PP.wireCharCounters) { PP.wireCharCounters(); }
        if (first) {
            document.body.addEventListener('click', onBodyClick);
            document.body.addEventListener('click', onGrabClick);
            document.body.addEventListener('htmx:afterSwap', onAfterSwap);
            document.body.addEventListener('htmx:afterSettle', onAfterSettle);
        }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
