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
    var refreshSeq = 0;
    // Per-node, and NOT serializable -- see the header. A `data-` attribute here
    // survives htmx's history snapshot and disables the wiring it was meant to guard.
    var wired = new WeakSet();

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
    function refreshItems() {
        var form = document.getElementById('gl-detail-form');
        var base = (form && form.getAttribute('hx-get')) || window.location.pathname;
        var select = form && form.querySelector('select[name="sort"]');
        var url = base + (select && select.value
            ? '?sort=' + encodeURIComponent(select.value) : '');
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
        // The grid node identity is the signal: an `innerHTML` swap always builds new nodes, so if
        // `#gl-items` is the same object afterwards, nothing was swapped.
        var mine = ++refreshSeq;
        var before = document.getElementById('gl-items');
        return window.htmx.ajax('GET', url, { target: '#gl-items-panel', swap: 'innerHTML' })
            .then(function () {
                // A later refresh already superseded this one -- two quick removals issue two
                // independent GETs and the older response can land last, repainting the grid with a
                // row that is already deleted.
                if (mine !== refreshSeq) { return; }
                if (document.getElementById('gl-items') === before) {
                    throw new Error('the items panel did not swap');
                }
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
        panel.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { input.focus(); abandon(); return; }
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
            var tallies = document.querySelector('[data-gl-tallies]');
            if (tallies) { tallies.hidden = true; }
            nameField.focus();
            nameField.setSelectionRange(nameField.value.length, nameField.value.length);
        }

        function close() {
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

            var body = new FormData();
            body.append('name', nameField.value);
            body.append('description', descField.value);

            // Captured before the write so the announcement can say which thing changed. It used to
            // say "renamed" unconditionally, including when only the description was edited.
            var heading = root.querySelector('[data-gl-name]');
            var previousName = heading ? heading.textContent.trim() : '';

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
                    document.title = data.name;
                    // The breadcrumb is the third place the name appears, and it was the one left
                    // showing the old value until a reload.
                    var crumb = document.querySelector('[data-breadcrumb-current]');
                    if (crumb) { crumb.textContent = data.name; }
                    announce(data.name !== previousName
                             ? 'List renamed to ' + data.name + '.'
                             : 'List details saved.');
                    close();
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

        // The sort toolbar is rendered `{% if items %}` and lives OUTSIDE the swapped panel, so it
        // cannot appear or disappear on its own. Add the first game to an empty list and the grid
        // gains a tile while the toolbar stays absent until a manual reload; remove the last one and
        // a dead sort control is left behind. Crossing that boundary is rare and once per list, so
        // reload rather than teach the client to build a control the server owns.
        var hasItems = !!(grid && grid.querySelector('.pp-gtile'));
        var hasToolbar = !!document.getElementById('gl-detail-form');
        if (hasItems !== hasToolbar) { window.location.reload(); return; }

        restoreRemoveFocus();
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
        var buttons = document.querySelectorAll('#gl-items [data-gl-remove]');
        if (!buttons.length) { return; }
        // The row that took the removed one's place, or the new last row if it was the last.
        var next = buttons[Math.min(index, buttons.length - 1)];
        if (next) { next.focus(); }
    }

    function boot(first) {
        handledGrid = null;
        searchField = null;
        wireAdder();
        wireIdentityEditor();
        wireVisibility();
        initReveal();
        if (PP.wireCharCounters) { PP.wireCharCounters(); }
        if (first) {
            document.body.addEventListener('click', onBodyClick);
            document.body.addEventListener('htmx:afterSwap', onAfterSwap);
        }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
