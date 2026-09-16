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
     * already knows the answer; asking it is one request and cannot disagree.
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
            publish.addEventListener('click', function () { setPublic(true, publish); });
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
                    row.remove();
                    refreshCount();
                    announce('Deleted ' + name + '.');
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
                post(form.dataset.url, body)
                    .then(function () {
                        // A RELOAD, not a client-built row. The row's markup carries four
                        // server-rendered URLs, a colour select seeded from the palette and the
                        // `can_edit_rows` gating -- rebuilding that here would be a second copy of
                        // `detail_row.html` that drifts the first time either changes. The lists page
                        // re-renders from the server for the same reason.
                        window.location.reload();
                    })
                    .catch(function (err) {
                        logFailure('add row', err);
                        toastError(err, 'That could not be added.');
                    });
            });
        }

        // ── reorder ──
        //
        // GRIP-ONLY, unlike the list page's cards: a row carries a text input and a select, and
        // making the whole row draggable would eat the pointer events both of those need.
        //
        // LONG-PRESS ON TOUCH, immediate with a mouse. A finger resting on a row is how you begin a
        // scroll, so touch needs a deliberate hold before a drag arms; `delayOnTouchOnly` keeps the
        // mouse instant and the manager's own threshold lets a scroll cancel a pending pick-up.
        if (PP.DragReorderManager && count() > 1 && rows.querySelector('[data-pd-row-grip]')) {
            new PP.DragReorderManager({
                container: rows,
                itemSelector: '[data-pd-row]',
                handleSelector: '[data-pd-row-grip]',
                delay: 320,
                delayOnTouchOnly: true,
                // The manager hands back every id in the new order, so this never re-reads the DOM.
                onReorder: function (itemId, position, allIds) {
                    var body = new FormData();
                    allIds.forEach(function (id) { body.append('bucket_ids[]', id); });
                    post(rows.dataset.reorderUrl, body)
                        .catch(function (err) {
                            logFailure('reorder rows', err);
                            toastError(err, 'That order could not be saved.');
                        });
                },
            });
        }
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
                    card.remove();
                    refreshCount(data.game_count);
                    announce('Removed ' + name + '.');
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
                    if (PP.ToastManager) {
                        PP.ToastManager.show('Added ' + data.title + '.', 'success');
                    }
                    announce('Added ' + data.title + '.');
                    // The pool card carries server-rendered URLs and cover art from a batched query;
                    // see the row-add note for why this asks the server rather than building one.
                    window.location.reload();
                },
            });
        }

        // ── reorder ──
        //
        // GRIP-ONLY and long-press on touch, for the reasons the row reorder above gives. Pool order
        // matters even where it looks cosmetic: `attach_cover_games` bounds the browse mosaic on
        // `position__lt=4`, so the first four are the tile's cover art.
        if (PP.DragReorderManager && pool.querySelector('[data-pd-game-grip]')) {
            new PP.DragReorderManager({
                container: pool,
                itemSelector: '[data-pd-game]',
                handleSelector: '[data-pd-game-grip]',
                delay: 320,
                delayOnTouchOnly: true,
                onReorder: function (itemId, position, allIds) {
                    var body = new FormData();
                    allIds.forEach(function (id) { body.append('game_ids[]', id); });
                    post(pool.dataset.reorderUrl, body)
                        .catch(function (err) {
                            logFailure('reorder pool', err);
                            toastError(err, 'That order could not be saved.');
                        });
                },
            });
        }
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
