/**
 * Tiers, Grids & Polls -- one prompt, and the author's tools on it.
 *
 * THE PAGE DECIDES WHAT EXISTS; THIS FILE ONLY WIRES WHAT IT FINDS. Every control here is rendered
 * behind a `can_*` flag that came from `prompt_service.frozen_acts`, so this file never asks whether a
 * poll is frozen or whether the viewer is the owner -- it queries for a control and does nothing if it
 * is absent. That is what keeps the freeze rules in one place: a reader's page simply has no
 * `[data-pd-row-delete]` to bind.
 *
 * THE ADDER IS `PP.GameAdder`, shared with the lists page. It is not a search box, it is four shipped
 * bug fixes wearing one -- see that component for what they were. Reusing it whole rather than
 * copying the markup is the difference between inheriting those fixes and re-earning them.
 *
 * WRITES GO THROUGH `PP.postJson`, which refuses to call a redirected HTML page a successful write.
 * `fetch` follows a bounce to the login page transparently and reports `200 text/html`, and every
 * caller that trusted it read `undefined` off a string -- which is how a toast came to say "Added
 * undefined." for a game the server never received.
 */
(function () {
    'use strict';

    var PP = window.PlatPursuit || {};

    // Matches `game_search.MIN_QUERY`. Below it the endpoint answers an empty list, so asking is pure
    // latency. Pinned against the server by a test so this literal and that constant cannot drift.
    var MIN_QUERY = 3;

    var root = null;
    var statusLine = null;

    function announce(message) {
        if (statusLine) { statusLine.textContent = message; }
    }

    function statusOf(err) {
        return (err && err.response && err.response.status) || 0;
    }

    /**
     * Say what actually failed, in the console, every time.
     *
     * A catch that drops its error collapses a 404, a refused rate limit and a 500 into one sentence
     * and discards the only evidence of which happened. It looks like handling and it converts a
     * diagnosable fault into a guessing game.
     */
    function logFailure(what, err) {
        if (!window.console || !window.console.error) { return; }
        window.console.error('[prompt-detail] ' + what + ' failed', {
            status: statusOf(err) || 'no response (network or CORS)',
            error: err,
        });
    }

    /**
     * Show the server's own words when it refused, and a usable sentence when it did not answer.
     *
     * THE SERVICE'S MESSAGE IS THE GOOD ONE. `PromptError` copy is written for the hunter ("a tier
     * list needs at least 5 games before it goes up. This one has 3"), and the endpoints pass it
     * through unchanged for exactly this. Replacing it with a generic apology here would throw away
     * the only part of the failure that tells somebody what to do next.
     */
    function toastError(err, fallback) {
        var message;
        if (err && err.signedOut) {
            message = 'You may have been signed out. Reload the page and try again.';
        } else if (statusOf(err) === 429) {
            message = 'Too many changes just now. Wait a moment and try again.';
        } else if (err && err.response && err.message) {
            message = err.message;
        } else {
            message = fallback;
        }
        if (PP.ToastManager) { PP.ToastManager.show(message, 'error'); }
        announce(message);
    }

    function post(url, body) {
        return PP.postJson(url, body || new FormData());
    }

    function field(name, value) {
        var body = new FormData();
        body.append(name, value);
        return body;
    }

    function setText(selector, value) {
        var el = root.querySelector(selector);
        if (el) { el.textContent = value; }
    }

    /**
     * Take focus somewhere sensible before removing the element that currently holds it.
     *
     * Deleting the row or card a Delete button lives in drops focus to `<body>`, so a keyboard user
     * working down a 36-slot grid loses their place on every deletion. The rest of this file is
     * careful about focus (the editor restores it to the pencil, the row adder refocuses its field);
     * the two removal paths were not.
     */
    function focusAfterRemoving(element) {
        var next = element.nextElementSibling || element.previousElementSibling;
        var target = next || element.parentElement;
        if (!target) { return; }
        // A container is not focusable by default; `tabindex="-1"` makes it focusable by script only,
        // which is what a "you are here now" landing spot wants.
        if (!next) { target.setAttribute('tabindex', '-1'); }
        var control = next ? next.querySelector('button, [href], input, select') : null;
        (control || target).focus();
    }

    /* ------------------------------------------------------------- partial refresh ---- */

    //: SERIALISED PER PANEL, one chain each, because a sequence NUMBER cannot do this job here.
    //:
    //: The first cut bumped a counter and checked it in `.then`, copying the list page. That guard
    //: cannot prevent what it claims to: htmx performs the swap INSIDE the request handler and
    //: resolves its promise afterwards, so by the time the check runs the stale response is already
    //: in the DOM. It suppressed the stale `armDrag` and nothing else. Two quick adds -- the exact
    //: workflow the fragment route exists to enable -- could leave five cards under a header reading
    //: six. Found by an audit; the list page has the same flaw and the same misleading comment.
    //:
    //: Chaining removes the overlap rather than trying to detect it: two requests for one panel can
    //: no longer be in flight together, so a late response cannot exist. It costs one round trip of
    //: latency on a burst, which is invisible next to being wrong.
    var refreshChain = { pool: Promise.resolve(), rows: Promise.resolve() };
    //: The publish gate's own chain, same reasoning. See `refreshPublishGate`.
    var gateChain = Promise.resolve();

    /**
     * Re-render ONE panel from the server.
     *
     * NOT A PAGE RELOAD, and that is the whole point. The adder keeps its results panel open after an
     * add so several games can be picked from one search; a reload threw that away and made adding
     * three games mean searching three times.
     *
     * NOT CLIENT-BUILT MARKUP EITHER. A pool card carries two server-built URLs and cover art that
     * arrives from one batched query; a row carries those plus a colour select and its edit gating.
     * Mirroring either in JS is a second copy of a template that drifts the first time one changes.
     *
     * A RESOLVED PROMISE IS NOT EVIDENCE OF A SWAP -- the lesson the list page records. htmx resolves
     * its ajax promise for every HTTP status; only a network error, an abort or a timeout rejects, and
     * its `responseHandling` maps 4xx/5xx to `swap: false`. So a 500 resolves successfully, never
     * swaps, and leaves the count reading N+1 over a panel still showing N. The node identity is the
     * signal: an `innerHTML` swap always builds new nodes, so an unchanged first child means nothing
     * landed.
     */
    function refreshPanel(which) {
        var panel = root.querySelector(which === 'pool' ? '[data-pd-pool]' : '[data-pd-rows]');
        if (!panel) { return Promise.resolve(); }
        if (!window.htmx) {
            // htmx ships on every page, so this is unreachable -- but returning a RESOLVED promise
            // would have the caller clear its field and announce success over an unchanged panel.
            return Promise.reject(new Error('htmx is not loaded'));
        }

        // Queued behind whatever is already refreshing this panel. `.catch` keeps one failure from
        // poisoning the chain for every refresh after it.
        var run = refreshChain[which].catch(function () {}).then(function () {
            var before = panel.firstElementChild;
            return window.htmx
                .ajax('GET', panel.dataset.refreshUrl, { target: panel, swap: 'innerHTML' })
                .then(function () {
                    // A RESOLVED PROMISE IS NOT EVIDENCE OF A SWAP. htmx resolves for every HTTP
                    // status -- only a network error, an abort or a timeout rejects -- and its
                    // `responseHandling` maps 4xx/5xx to `swap: false`. So a 500 resolves
                    // successfully, never swaps, and leaves the count reading N+1 over a panel
                    // showing N. An `innerHTML` swap always builds new nodes, so an unchanged first
                    // child means nothing landed.
                    if (panel.firstElementChild === before) {
                        throw new Error(which + ' panel did not swap');
                    }
                    // The fresh nodes have no Sortable on them and the panel may have crossed the
                    // one-item threshold in either direction, so the drag is re-armed against what
                    // is actually there now.
                    armDrag(which);
                });
        });
        refreshChain[which] = run;
        return run;
    }

    /**
     * Ask the server again why this cannot be published, and let the button follow the answer.
     *
     * THE STALE-PUBLISH BUG. The reason and the button's `disabled` were computed once, at page load.
     * That was invisible while every add reloaded the page. Once adds refreshed only their own panel,
     * a new grid kept "Add at least one row before publishing" and a dead button after its author had
     * added slots -- the server would have accepted the publish; the page never asked again.
     *
     * The rule stays in `prompt_service.publish_blocker`. This only fetches the server's sentence and
     * treats "is there one" as "is the button disabled", so the client holds no copy of the floor.
     *
     * `fetch` rather than `htmx.ajax`, because an EMPTY answer is a real answer here ("nothing blocks
     * you") and the node-identity swap proof `refreshPanel` uses cannot tell an empty swap from none.
     * So the response is checked directly: not ok, or redirected to a login page, is a failure.
     *
     * SERIALISED, like the panels, and for a sharper reason. Add a fifth game to a tier list and
     * immediately remove one: the add's answer ("nothing blocks you") can land after the remove's
     * ("needs at least 5 games"), leaving Publish ENABLED with no reason shown on a prompt below the
     * floor. The press is still refused by the transaction, so this was never a way to publish
     * something unready -- but it is exactly the staleness this whole mechanism exists to remove.
     *
     * A FAILURE IS NOT SILENT. It used to log to the console only, which left an author under a
     * stale reason with no hint that reloading would help. Safe, because the endpoint still refuses;
     * not usable, which is a different bar.
     */
    function refreshPublishGate() {
        var gate = root.querySelector('[data-pd-publish-gate]');
        if (!gate) { return Promise.resolve(); }      // published, or not the author: nothing to gate
        var button = root.querySelector('[data-pd-publish]');

        gateChain = gateChain.catch(function () {}).then(function () {
        return fetch(gate.dataset.refreshUrl, {
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
        })
            .then(function (response) {
                if (!response.ok || response.redirected) {
                    throw new Error('publish gate answered ' + response.status);
                }
                return response.text();
            })
            .then(function (html) {
                var was = gate.textContent.trim();
                gate.innerHTML = html;
                var now = gate.textContent.trim();
                // `aria-disabled`, not `disabled`: a disabled button leaves the tab order entirely,
                // so a keyboard user cannot reach it to discover it is off, and the reason sits in an
                // unassociated sibling. The click handler short-circuits instead -- see `wireVisibility`.
                if (button) {
                    button.setAttribute('aria-disabled', now !== '' ? 'true' : 'false');
                    button.classList.toggle('is-disabled', now !== '');
                }
                // ANNOUNCED HERE rather than left to the live region. The transition that matters is
                // reason -> EMPTY, and emptying an `aria-live` region announces nothing (`aria-relevant`
                // defaults to additions/text), so the one direction worth hearing was silent.
                if (was && !now) { announce('You can publish this now.'); }
                else if (now && now !== was) { announce(now); }
            })
            .catch(function (err) {
                logFailure('publish gate refresh', err);
                announce('Could not check whether this is ready to publish. Reload to be sure.');
            });
        });
        return gateChain;
    }

    /**
     * Retire the adder at the cap, and bring it back under it.
     *
     * A COMPARISON AGAINST A SERVER-RENDERED NUMBER, not a rule this file knows. `data-max` is written
     * by the template from `MAX_GAMES_PER_PROMPT`, and `add_concept` refuses at the cap regardless --
     * this only decides whether the control is on screen between renders. Without it, an author who
     * fills a 20-option poll keeps an adder whose every use 400s until they reload.
     */
    function syncAdderVisibility(total) {
        var pool = root.querySelector('[data-pd-pool]');
        var adder = root.querySelector('[data-pd-adder]');
        if (!pool || !adder) { return; }
        var max = parseInt(pool.dataset.max, 10);
        if (isNaN(max)) { return; }
        var full = total >= max;
        // FOCUS FIRST. Hiding an ancestor of the focused element drops focus to `<body>` -- and when
        // the cap is hit by an add, focus is inside the adder (the input, or the "Add" row in the
        // open results panel, which is a child of it).
        if (full && !adder.hidden && adder.contains(document.activeElement)) {
            var heading = root.querySelector('[data-pd-pool-panel] .pp-pdet__panel-title');
            if (heading) { heading.setAttribute('tabindex', '-1'); heading.focus(); }
            announce('That is the most games this can hold.');
        }
        adder.hidden = full;
    }

    /**
     * The same, for the row add form -- which had no equivalent at all.
     *
     * `can_add_rows` decided the form's existence at page load only, and the form lives outside the
     * swapped fragment by design, so adding rows up to the cap left it on screen with every further
     * submit refused. Reachable in one sitting now that a grid may have 36 slots.
     */
    function syncRowAddVisibility() {
        var rows = root.querySelector('[data-pd-rows]');
        var form = root.querySelector('[data-pd-row-add]');
        if (!rows || !form) { return; }
        var max = parseInt(rows.dataset.max, 10);
        if (isNaN(max)) { return; }
        var full = rows.querySelectorAll('[data-pd-row]').length >= max;
        if (full && !form.hidden && form.contains(document.activeElement)) {
            var heading = root.querySelector('[data-pd-rows-panel] .pp-pdet__panel-title');
            if (heading) { heading.setAttribute('tabindex', '-1'); heading.focus(); }
            announce('That is the most this can hold.');
        }
        form.hidden = full;
    }

    /* --------------------------------------------------------------------- dragging ---- */

    //: The live managers, so a re-arm after a swap replaces rather than stacks. Two Sortables on one
    //: container both fire on every drop, which posts the order twice and races the two writes.
    var draggers = { pool: null, rows: null };

    var DRAG = {
        pool: {
            panel: '[data-pd-pool]',
            item: '[data-pd-game]',
            grip: '[data-pd-game-grip]',
            param: 'game_ids[]',
            what: 'pool',
        },
        rows: {
            panel: '[data-pd-rows]',
            item: '[data-pd-row]',
            grip: '[data-pd-row-grip]',
            param: 'bucket_ids[]',
            what: 'rows',
        },
    };

    /**
     * (Re)arm drag reordering on one panel.
     *
     * GRIP-ONLY, unlike the list page's cards: a row carries a text input and a select, and making the
     * whole row draggable would eat the pointer events both of those need.
     *
     * LONG-PRESS ON TOUCH, immediate with a mouse. A finger resting on a row is how you begin a
     * scroll, so touch needs a deliberate hold before a drag arms; `delayOnTouchOnly` keeps the mouse
     * instant and the manager's own threshold lets a scroll cancel a pending pick-up.
     *
     * Pool order is not cosmetic: `attach_cover_games` bounds the browse mosaic on `position__lt=4`,
     * so the first four are the tile's cover art.
     */
    function armDrag(which) {
        var spec = DRAG[which];
        var panel = root.querySelector(spec.panel);

        if (draggers[which]) {
            draggers[which].destroy();
            draggers[which] = null;
        }
        if (!panel || !PP.DragReorderManager) { return; }
        // Nothing to reorder, and no grips to do it with -- a reader's page has neither.
        if (panel.querySelectorAll(spec.item).length < 2) { return; }
        if (!panel.querySelector(spec.grip)) { return; }

        draggers[which] = new PP.DragReorderManager({
            container: panel,
            itemSelector: spec.item,
            handleSelector: spec.grip,
            delay: 320,
            delayOnTouchOnly: true,
            // The manager hands back every id in the new order, so this never re-reads the DOM.
            // `data-item-id` is ITS attribute, not one chosen here -- it builds this list from
            // `evt.item.dataset.itemId`, and a differently-named one posts `undefined`.
            onReorder: function (itemId, position, allIds) {
                var body = new FormData();
                allIds.forEach(function (id) { body.append(spec.param, id); });
                post(panel.dataset.reorderUrl, body)
                    .catch(function (err) {
                        logFailure('reorder ' + spec.what, err);
                        toastError(err, 'That order could not be saved.');
                    });
            },
        });
    }

    /* ------------------------------------------------------------------ identity ---- */

    /**
     * Edit the title and description where they are shown.
     *
     * Both the read view and the form are server-rendered and this only swaps which is `hidden`, so
     * there is no markup built in the client and nothing to keep in step with the template.
     */
    function wireIdentity() {
        var wrap = root.querySelector('[data-pd-identity]');
        if (!wrap) { return; }
        var form = wrap.querySelector('[data-pd-identity-edit]');
        var view = wrap.querySelector('[data-pd-identity-view]');
        var open = wrap.querySelector('[data-pd-edit-open]');
        if (!form || !view || !open) { return; }          // a reader: no form rendered

        var titleField = form.querySelector('[data-pd-edit-title]');
        var descField = form.querySelector('[data-pd-edit-description]');

        function reset() {
            // RESYNC ON THE WAY IN, not only on cancel. The save writes the SERVER's normalized
            // values to the heading (`_check_title` trims and sanitizes) and left the fields holding
            // whatever was typed -- so saving "  My poll  " and reopening showed the padded string in
            // a form whose heading read the trimmed one.
            if (titleField) { titleField.value = root.querySelector('[data-pd-title]').textContent.trim(); }
            if (descField) {
                var p = root.querySelector('[data-pd-description]');
                descField.value = p && !p.hidden ? p.textContent.trim() : '';
            }
        }

        function show() {
            reset();
            view.hidden = true;
            form.hidden = false;
            if (titleField) {
                titleField.focus();
                titleField.setSelectionRange(titleField.value.length, titleField.value.length);
            }
        }

        function hide() {
            form.hidden = true;
            view.hidden = false;
            open.focus();
        }

        open.addEventListener('click', show);
        var cancel = form.querySelector('[data-pd-edit-cancel]');
        if (cancel) { cancel.addEventListener('click', hide); }

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var save = form.querySelector('[data-pd-edit-save]');
            if (save) { save.disabled = true; }

            var body = new FormData();
            body.append('title', titleField ? titleField.value : '');
            body.append('description', descField ? descField.value : '');
            var columns = form.querySelector('[data-pd-edit-columns]');
            if (columns) { body.append('grid_columns', columns.value); }
            var dupes = form.querySelector('[data-pd-edit-duplicates]');
            if (dupes) { body.append('allow_duplicates', dupes.checked ? 'true' : 'false'); }

            post(form.dataset.url, body)
                .then(function (data) {
                    // THE SERVER'S VALUES, not the typed ones: the service trims, collapses and
                    // sanitizes, so painting the input's contents here would show a title the row
                    // does not hold until the next reload.
                    setText('[data-pd-title]', data.title);
                    var p = root.querySelector('[data-pd-description]');
                    if (p) {
                        p.textContent = descField ? descField.value.trim() : '';
                        p.hidden = !p.textContent;
                    }
                    hide();
                    if (PP.ToastManager) { PP.ToastManager.show('Saved.', 'success'); }
                    announce('Saved.');
                    // The settings form can change `grid_columns` and the duplicates toggle,
                    // and a published prompt's floor is re-read on every save, so the reason to
                    // publish (or not) can move. The pool-size rule this once cited is gone.
                    refreshPublishGate();
                })
                .catch(function (err) {
                    logFailure('identity save', err);
                    toastError(err, 'That could not be saved.');
                })
                .finally(function () { if (save) { save.disabled = false; } });
        });
    }

    /* ---------------------------------------------------------------- visibility ---- */

    /**
     * Publish, unpublish, close, reopen, delete.
     *
     * PUBLISH AND UNPUBLISH ARE ONE CONTROL IN TWO STATES, both server-rendered and one hidden, so
     * this swaps visibility rather than rebuilding markup.
     *
     * A RELOAD FOLLOWS PUBLISHING AND UNPUBLISHING, deliberately, and it is the one place this file
     * does not patch the page in place. Crossing that line changes which controls may exist: a
     * published grid loses its row tools and its adder, a draft regains them. Those flags are
     * computed server-side from `frozen_acts`, and reproducing that derivation in JS would be a second
     * copy of the freeze rules -- the exact thing the template comments refuse to do. The server
     * already knows the answer; asking it is one request and cannot disagree. (A published grid
     * loses its row tools; it has no adder in any state, having no pool.)
     */
    function wireVisibility() {
        var wrap = root.querySelector('[data-pd-visibility]');
        if (!wrap) { return; }

        function setPublic(next, button) {
            if (button) { button.disabled = true; }
            post(wrap.dataset.url, field('is_public', next ? 'true' : 'false'))
                .then(function () { window.location.reload(); })
                .catch(function (err) {
                    if (button) { button.disabled = false; }
                    logFailure(next ? 'publish' : 'unpublish', err);
                    toastError(err, next ? 'That could not be published.'
                                         : 'That could not be unpublished.');
                });
        }

        var publish = wrap.querySelector('[data-pd-publish]');
        if (publish) {
            publish.addEventListener('click', function () {
                // `aria-disabled` keeps the button reachable, so the handler is what has to refuse.
                // The reason is already on screen and pointed at by `aria-describedby`; repeat it to
                // the live region, because a press is the moment somebody wants to know why not.
                if (publish.getAttribute('aria-disabled') === 'true') {
                    var gate = root.querySelector('[data-pd-publish-gate]');
                    announce((gate && gate.textContent.trim()) || 'This is not ready to publish yet.');
                    return;
                }
                setPublic(true, publish);
            });
        }
        var unpublish = wrap.querySelector('[data-pd-unpublish]');
        if (unpublish) {
            unpublish.addEventListener('click', function () { setPublic(false, unpublish); });
        }

        // CLOSING IS NOT UNPUBLISHING and does not change what may be edited, so it patches in place.
        var close = wrap.querySelector('[data-pd-close]');
        if (close) {
            close.addEventListener('click', function () {
                var label = close.querySelector('[data-pd-close-label]');
                var closing = !label || label.textContent.trim() === 'Close';
                close.disabled = true;
                post(close.dataset.url, field('closed', closing ? 'true' : 'false'))
                    .then(function (data) {
                        if (label) { label.textContent = data.is_closed ? 'Reopen' : 'Close'; }
                        var chip = root.querySelector('[data-pd-closed-chip]');
                        if (chip) { chip.hidden = !data.is_closed; }
                        announce(data.is_closed ? 'Closed to new answers.' : 'Open for answers.');
                    })
                    .catch(function (err) {
                        logFailure('close', err);
                        toastError(err, 'That could not be changed.');
                    })
                    .finally(function () { close.disabled = false; });
            });
        }
    }

    function wireDelete() {
        var button = root.querySelector('[data-pd-delete]');
        if (!button) { return; }
        button.addEventListener('click', function () {
            // A prompt is somebody's work and deleting it is not undoable. `confirm` is what the
            // lists page uses for the same act; a bespoke dialog is a different lane.
            if (!window.confirm('Delete this for good? Answers to it go too.')) { return; }
            button.disabled = true;
            post(button.dataset.url)
                .then(function () {
                    // Back to the browse for this shape. `referrer` is not trusted -- it may be the
                    // page of a prompt that no longer exists.
                    window.location.href = root.dataset.browseUrl || '/';
                })
                .catch(function (err) {
                    button.disabled = false;
                    logFailure('delete', err);
                    toastError(err, 'That could not be deleted.');
                });
        });
    }

    /* ---------------------------------------------------------------------- like ---- */

    function wireLike() {
        var button = root.querySelector('[data-pd-like]');
        if (!button) { return; }
        button.addEventListener('click', function () {
            var next = button.getAttribute('aria-pressed') !== 'true';
            button.disabled = true;
            post(button.dataset.url, field('liked', next ? 'true' : 'false'))
                .then(function (data) {
                    button.setAttribute('aria-pressed', data.liked ? 'true' : 'false');
                    button.classList.toggle('pp-cta--ghost', !data.liked);
                    var label = button.querySelector('[data-pd-like-label]');
                    if (label) { label.textContent = data.liked ? 'Liked' : 'Like'; }
                    var icon = button.querySelector('[data-pd-like-icon]');
                    if (icon) { icon.setAttribute('fill', data.liked ? 'currentColor' : 'none'); }
                    setText('[data-pd-tally-likes]', data.like_count.toLocaleString());
                })
                .catch(function (err) {
                    logFailure('like', err);
                    toastError(err, 'That could not be changed.');
                })
                .finally(function () { button.disabled = false; });
        });
    }

    /* ---------------------------------------------------------------------- rows ---- */

    /**
     * Add, rename, recolour, delete and reorder the rows.
     *
     * DELEGATED FROM THE CONTAINER rather than bound per row, because rows are created and destroyed
     * while the page lives. Per-row binding is what leaves a newly-added row inert until a reload --
     * the failure the lists page records from its own first cut.
     */
    function wireRows() {
        var panel = root.querySelector('[data-pd-rows-panel]');
        if (!panel) { return; }
        var rows = panel.querySelector('[data-pd-rows]');
        if (!rows) { return; }

        function count() { return rows.querySelectorAll('[data-pd-row]').length; }

        function refreshCount() {
            var el = panel.querySelector('[data-pd-row-count]');
            if (!el) { return; }
            var parts = el.textContent.split('/');
            el.textContent = count() + (parts.length > 1 ? '/' + parts[1] : '');
        }

        // ── rename and recolour: delegated, saved on change or blur ──
        rows.addEventListener('click', function (e) {
            var label = e.target.closest ? e.target.closest('[data-pd-row-label-view]') : null;
            if (!label) { return; }
            var row = label.closest('[data-pd-row]');
            var input = row && row.querySelector('[data-pd-row-label-input]');
            if (!input) { return; }
            label.hidden = true;
            input.hidden = false;
            input.focus();
            input.setSelectionRange(input.value.length, input.value.length);
        });

        function saveLabel(input) {
            var row = input.closest('[data-pd-row]');
            var label = row.querySelector('[data-pd-row-label-view]');
            var typed = input.value.trim();
            input.hidden = true;
            label.hidden = false;
            if (!typed || typed === label.textContent.trim()) {
                input.value = label.textContent.trim();     // nothing to save, and no empty label
                return;
            }
            post(row.dataset.updateUrl, field('label', typed))
                .then(function (data) {
                    // The server's normalized label, for the reason the identity save gives.
                    label.textContent = data.label;
                    input.value = data.label;
                    announce('Renamed to ' + data.label + '.');
                })
                .catch(function (err) {
                    input.value = label.textContent.trim();
                    logFailure('rename row', err);
                    toastError(err, 'That could not be renamed.');
                });
        }

        rows.addEventListener('blur', function (e) {
            if (e.target.matches && e.target.matches('[data-pd-row-label-input]')) {
                saveLabel(e.target);
            }
        }, true);           // capture: `blur` does not bubble

        rows.addEventListener('keydown', function (e) {
            if (!e.target.matches || !e.target.matches('[data-pd-row-label-input]')) { return; }
            if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); }
            if (e.key === 'Escape') {
                var row = e.target.closest('[data-pd-row]');
                e.target.value = row.querySelector('[data-pd-row-label-view]').textContent.trim();
                e.target.blur();
            }
        });

        rows.addEventListener('change', function (e) {
            if (!e.target.matches || !e.target.matches('[data-pd-row-colour-input]')) { return; }
            var select = e.target;
            var row = select.closest('[data-pd-row]');
            var previous = row.dataset.pdColour || '';
            post(row.dataset.updateUrl, field('colour', select.value))
                .then(function (data) {
                    if (previous) { row.classList.remove('pp-pdet__row--' + previous); }
                    if (data.colour) { row.classList.add('pp-pdet__row--' + data.colour); }
                    row.dataset.pdColour = data.colour || '';
                })
                .catch(function (err) {
                    select.value = previous;
                    logFailure('recolour row', err);
                    toastError(err, 'That could not be changed.');
                });
        });

        // ── delete ──
        rows.addEventListener('click', function (e) {
            var button = e.target.closest ? e.target.closest('[data-pd-row-delete]') : null;
            if (!button) { return; }
            var row = button.closest('[data-pd-row]');
            var name = row.querySelector('[data-pd-row-label-view]').textContent.trim();
            if (!window.confirm('Delete "' + name + '"? Anything placed in it comes loose.')) { return; }
            button.disabled = true;
            post(row.dataset.deleteUrl)
                .then(function () {
                    focusAfterRemoving(row);
                    row.remove();
                    refreshCount();
                    syncRowAddVisibility();
                    announce('Deleted ' + name + '.');
                    refreshPublishGate();
                })
                .catch(function (err) {
                    button.disabled = false;
                    logFailure('delete row', err);
                    toastError(err, 'That could not be deleted.');
                });
        });

        // ── add ──
        var form = panel.querySelector('[data-pd-row-add]');
        if (form) {
            form.addEventListener('submit', function (e) {
                e.preventDefault();
                var labelInput = form.querySelector('[data-pd-row-label]');
                var colourInput = form.querySelector('[data-pd-row-colour]');
                var typed = labelInput.value.trim();
                if (!typed) { return; }

                var body = new FormData();
                body.append('label', typed);
                body.append('colour', colourInput ? colourInput.value : '');
                var button = form.querySelector('button[type="submit"]');
                if (button) { button.disabled = true; }
                post(form.dataset.url, body)
                    .then(function () {
                        // THE PANEL, NOT THE PAGE. The field keeps its focus, so a hunter typing
                        // S / A / B / C / D gets five rows without touching the mouse.
                        refreshPublishGate();
                        // ITS OWN CATCH. `refreshPanel` throws by design when the swap does not land,
                        // and chained into the catch below that reported "That could not be added."
                        // for a row the server HAD created -- with the field left full, so the
                        // obvious next move was to press Add again and make a duplicate. The pool
                        // path already split these; this one did not.
                        return refreshPanel('rows').then(function () {
                            labelInput.value = '';
                            labelInput.focus();
                            refreshCount();
                            syncRowAddVisibility();
                            announce('Added ' + typed + '.');
                        }, function (err) {
                            logFailure('rows refresh after add', err);
                            labelInput.value = '';
                            if (PP.ToastManager) {
                                PP.ToastManager.show('Added. Reload to see it.', 'warning');
                            }
                            announce('Added ' + typed + '. Reload to see it.');
                        });
                    })
                    .catch(function (err) {
                        logFailure('add row', err);
                        toastError(err, 'That could not be added.');
                    })
                    .finally(function () { if (button) { button.disabled = false; } });
            });
        }

        // ── reorder ──
        //
        // Armed through the shared helper, because the panel is re-rendered on every add and the
        // fresh nodes need it again. See `armDrag` for the grip-only and long-press reasoning.
        armDrag('rows');
    }

    /* ---------------------------------------------------------------------- pool ---- */

    function wirePool() {
        var panel = root.querySelector('[data-pd-pool-panel]');
        if (!panel) { return; }
        var pool = panel.querySelector('[data-pd-pool]');
        if (!pool) { return; }

        function refreshCount(total) {
            var el = panel.querySelector('[data-pd-game-count]');
            if (!el) { return; }
            var parts = el.textContent.split('/');
            el.textContent = total + (parts.length > 1 ? '/' + parts[1] : '');
        }

        // ── remove ──
        pool.addEventListener('click', function (e) {
            var button = e.target.closest ? e.target.closest('[data-pd-game-remove]') : null;
            if (!button) { return; }
            var card = button.closest('[data-pd-game]');
            var name = card.querySelector('.pp-pdet__game-title').textContent.trim();
            button.disabled = true;
            post(card.dataset.removeUrl)
                .then(function (data) {
                    focusAfterRemoving(card);
                    card.remove();
                    refreshCount(data.game_count);
                    // BACK UNDER THE CAP, the mirror of hiding it. Filling a 20-option poll hid the
                    // adder correctly and removing one left it hidden until a reload, even though
                    // the endpoint would have accepted.
                    syncAdderVisibility(data.game_count);
                    announce('Removed ' + name + '.');
                    refreshPublishGate();
                    // QUEUED BEHIND ANY IN-FLIGHT ADD REFRESH. Removing locally is instant, but an
                    // earlier add's refresh may still be carrying a pre-removal snapshot; when it
                    // landed it painted the removed game back in, with a Remove button whose URL now
                    // 404s. Enqueuing here puts the truth last in the same chain.
                    return refreshPanel('pool').catch(function (err) {
                        logFailure('pool refresh after remove', err);
                    });
                })
                .catch(function (err) {
                    button.disabled = false;
                    logFailure('remove game', err);
                    toastError(err, 'That could not be removed.');
                });
        });

        // ── add, through the shared typeahead ──
        var adder = panel.querySelector('[data-pd-adder]');
        if (adder && PP.GameAdder) {
            PP.GameAdder(adder, {
                input: adder.querySelector('[data-pd-adder-input]'),
                panel: adder.querySelector('[data-pd-adder-results]'),
                // `panel`, not `adder`: the status line is a SIBLING of the search wrapper, because
                // `sr-only` is absolutely positioned and so is not a child of the field. Scoped to the
                // wrapper this returns null and every announcement is silently dropped -- the exact
                // bug the lists adder shipped with.
                status: panel.querySelector('[data-pd-adder-status]'),
                prefix: 'pp-adder',
                minQuery: MIN_QUERY,
                addLabel: 'Add',
                addedLabel: 'Added',
                addAria: function (title) { return 'Add ' + title; },
                addedAria: function (title) { return title + ' is already here'; },
                logLabel: 'prompt-detail',
                onAdded: function (data) {
                    refreshCount(data.game_count);
                    syncAdderVisibility(data.game_count);
                    announce('Added ' + data.title + '.');
                    // THE PANEL, NOT THE PAGE -- and NO TOAST. The results panel stays open on
                    // purpose so several games can be added from one search, the row itself flips to
                    // "Added" as the confirmation, and a toast per game would stack four deep over
                    // the panel being picked from. The list page toasts because its adder sits in a
                    // toolbar above a grid the hunter is not looking at.
                    refreshPublishGate();
                    return refreshPanel('pool').catch(function (err) {
                        logFailure('pool refresh after add', err);
                        if (PP.ToastManager) {
                            PP.ToastManager.show('Added. Reload to see it in the pool.', 'warning');
                        }
                    });
                },
            });
        }

        // ── reorder ──
        armDrag('pool');
    }

    /* ---------------------------------------------------------------------- boot ---- */

    function boot() {
        root = document.querySelector('[data-pd-root]');
        if (!root) { return; }
        statusLine = root.querySelector('[data-pd-status]');

        wireIdentity();
        wireVisibility();
        wireDelete();
        wireLike();
        wireRows();
        wirePool();
    }

    if (document.readyState !== 'loading') { boot(); }
    else { document.addEventListener('DOMContentLoaded', boot); }
})();
