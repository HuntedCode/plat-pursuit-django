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
    var pendingFocusIndex = null;

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
                // `setBusy(false)` is required, not tidiness. `seq++` orphans any in-flight request,
                // so its `.finally` fails the `mine === seq` test and never clears the busy flag --
                // leaving `.is-searching` set, which keeps the spinner turning on an empty field AND
                // hides the clear button, so the visible control is stuck until the next keystroke.
                onClear: function () {
                    seq++;
                    if (searchField) { searchField.setBusy(false); }
                    closePanel();
                    say('');
                },
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

        // Now that the results FLOAT over the page, they have to be dismissable by clicking away --
        // in flow they merely pushed content down and could be left open harmlessly. Bound on the
        // document, and only while a panel is actually open, so it costs nothing at rest.
        document.addEventListener('click', function (e) {
            if (panel.hidden) { return; }
            if (root.contains(e.target)) { return; }
            closePanel();
            say('');
        });

        panel.addEventListener('click', function (e) {
            var row = e.target.closest ? e.target.closest('.gl-adder__opt') : null;
            if (!row || row.disabled || row.dataset.busy === '1') { return; }
            row.dataset.busy = '1';

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
        if (!root || root.dataset.wired === '1') { return; }
        var form = root.querySelector('[data-gl-identity-edit]');
        var view = root.querySelector('[data-gl-identity-view]');
        if (!form || !view) { return; }          // a visitor: no form rendered
        root.dataset.wired = '1';

        var nameField = form.querySelector('[name="name"]');
        var descField = form.querySelector('[name="description"]');

        function open() {
            view.hidden = true;
            form.hidden = false;
            nameField.focus();
            nameField.setSelectionRange(nameField.value.length, nameField.value.length);
        }

        function close() {
            form.hidden = true;
            view.hidden = false;
            var opener = root.querySelector('[data-gl-edit-open]');
            if (opener) { opener.focus(); }       // focus goes back where it came from
        }

        function reset() {
            // Cancel restores from the DOM the server rendered, not from a snapshot taken at open --
            // a successful save updates that DOM, so a later cancel must not resurrect the old text.
            nameField.value = (root.querySelector('[data-gl-name]') || {}).textContent.trim();
            var desc = root.querySelector('[data-gl-description]');
            descField.value = desc && !desc.hidden ? desc.textContent.trim() : '';
            [nameField, descField].forEach(function (el) {
                el.dispatchEvent(new Event('input', { bubbles: true }));   // resync the counters
            });
        }

        var opener = root.querySelector('[data-gl-edit-open]');
        if (opener) { opener.addEventListener('click', open); }

        var cancel = form.querySelector('[data-gl-edit-cancel]');
        if (cancel) { cancel.addEventListener('click', function () { reset(); close(); }); }

        form.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { e.preventDefault(); reset(); close(); }
        });

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var save = form.querySelector('[data-gl-edit-save]');
            if (save && save.dataset.busy === '1') { return; }
            if (save) { save.dataset.busy = '1'; }

            var body = new FormData();
            body.append('name', nameField.value);
            body.append('description', descField.value);

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
                    announce('List renamed to ' + data.name + '.');
                    close();
                })
                .catch(function (err) { toastError(err, 'Those changes could not be saved.'); })
                .finally(function () { if (save) { save.dataset.busy = ''; } });
        });
    }

    /* ----------------------------------------------------------------- publish ---- */

    function wireVisibility() {
        var root = document.querySelector('[data-gl-visibility]');
        if (!root || root.dataset.wired === '1') { return; }
        root.dataset.wired = '1';

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

        function set(btn, isPublic) {
            if (btn.dataset.busy === '1') { return; }
            btn.dataset.busy = '1';
            var body = new FormData();
            body.append('is_public', isPublic ? 'true' : 'false');
            postJson(root.dataset.updateUrl, body)
                .then(function (data) {
                    paint(data.is_public, data.is_public);
                    if (PP.ToastManager) {
                        PP.ToastManager.show(
                            data.is_public
                                ? 'Published. Anyone with the link can read this list.'
                                : 'This list is private again.',
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

    /* ----------------------------------------------------------------- reorder ---- */

    var dragManager = null;

    /**
     * Drag to reorder, when the server said it is possible.
     *
     * `data-can-reorder` is set by the view and requires owner + `sort=added` + an untruncated list.
     * The sort condition is the subtle one: dragging while sorted by NAME would compute an order from
     * rows the hunter is reading alphabetically and silently overwrite the order they curated.
     */
    function initDrag() {
        if (dragManager && dragManager.destroy) { dragManager.destroy(); }
        dragManager = null;

        var grid = document.getElementById('gl-items');
        if (!grid || !grid.hasAttribute('data-can-reorder') || !PP.DragReorderManager) { return; }

        dragManager = new PP.DragReorderManager({
            container: grid,
            itemSelector: '.gl-item',
            handleSelector: '[data-gl-drag]',
            placeholderClass: 'gl-item--ghost',
            onStart: function () { grid.classList.add('gl-items-dragging'); },
            onEnd: function () { grid.classList.remove('gl-items-dragging'); },
            onReorder: function () {
                // Read the order off the DOM after the drop rather than trusting the arguments: the
                // DOM is what the hunter can see, and it is the thing the server must be made to
                // agree with.
                var ids = Array.prototype.map.call(
                    grid.querySelectorAll('.gl-item'),
                    function (el) { return el.dataset.itemId; });

                var body = new FormData();
                ids.forEach(function (id) { body.append('item_ids[]', id); });

                return postJson(grid.dataset.reorderUrl, body)
                    .then(function () { announce('Order saved.'); })
                    .catch(function (err) {
                        // The server refused, so the DOM is now lying about the stored order. Re-render
                        // from the server rather than trying to undo the drop by hand.
                        toastError(err, 'That new order could not be saved.');
                        return refreshItems();
                    });
            },
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
        // The grid is a fresh node, so the old Sortable instance is bound to an element that is no
        // longer in the document. Re-created, or dropped entirely when the new render says reorder is
        // no longer available -- sorting by name removes `data-can-reorder`, and a drag left wired
        // there would rewrite a curated order from an alphabetical view of it.
        initDrag();

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
        initDrag();
        if (PP.wireCharCounters) { PP.wireCharCounters(); }
        if (first) {
            document.body.addEventListener('click', onBodyClick);
            document.body.addEventListener('htmx:afterSwap', onAfterSwap);
        }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
