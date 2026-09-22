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
    // ONE MODE, replacing the `editorOpen` + `positioning` pair (2026-09).
    //
    // There used to be two, nested: "Edit list" opened the identity panel and revealed a bar, and a
    // second press inside that bar turned on dragging. Meanwhile adding and removing games worked
    // whether either was on. Three different answers to "can I change this list right now", which is
    // what the rebuild was asked to fix.
    //
    // `editing` is the hunter's intent and the only thing they toggle.
    var editing = false;
    // Whether drag is actually attached, which is DERIVED and never toggled. The mode can be on over
    // a grid nothing can be dragged in -- a Collection with no sections has nothing to file, a ranked
    // list sorted A-Z has no position a drop could mean, and SortableJS may have failed to load. The
    // tray, the grips and the grab cursor follow THIS; the rest of editing follows `editing`.
    //
    // Keeping them apart is deliberate. Collapsing to one flag was the obvious reading of "one mode"
    // and it promises a drag the page cannot honour on exactly those three surfaces -- which the old
    // two-mode split was, by accident, preventing.
    var arranging = false;
    // The identity panel's own show/hide, published by `wireIdentityEditor` so the mode can drive
    // it. It is no longer a mode: it is one of the things entering the mode reveals.
    // Where the adder was docked when a refresh took it home, so it can go back. `null` means it
    // was not docked; '' would mean the loose bucket, which is why this is not a plain falsy check.
    var pendingDockSection = null;
    // Set when a section move changed ranks the client cannot recompute, so the write's success
    // path knows to fetch the page that can. See `moveSection`.
    var pendingSectionRenumber = false;
    var identityShow = null;
    var identityHide = null;
    // The single toggle. Looked up rather than closed over, because the header is re-rendered
    // underneath this on a save -- the bug `editOpener` was written for.
    function editToggle() { return document.querySelector('[data-gl-edit-open]'); }
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
            grid: grid, cardSelector: '.pp-gcard', cellSelector: '.gl-item', step: 22,
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
        // BEFORE THE SWAP. The adder may be docked under a section header inside the panel this is
        // about to replace; leaving it there destroys the node, its listeners and its WeakSet guard.
        parkAdder();
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
        var preview = previewParam();
        if (preview) { params.push(preview); }
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
     * The team-preview parameter, re-attached to every request this file builds.
     *
     * Read from the PANEL, which is htmx's swap target and keeps its own attributes, rather than from
     * `window.location` -- `refreshAfterTypeChange` rewrites the address bar with `replaceState`, so
     * the location is the one source guaranteed to have lost it by the time it is needed.
     *
     * Without this the preview ended at the first add, remove, sort or section change: the server saw
     * no parameter, computed the member's answer, and the out-of-band chrome swap restored the
     * section controls underneath a banner still saying "Previewing as a non-member".
     */
    function previewParam() {
        var panel = document.getElementById('gl-items-panel');
        var slug = panel && panel.dataset.preview;
        return slug ? 'preview=' + encodeURIComponent(slug) : '';
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
        // THE PREVIEW SURVIVES THE CLEAN-UP. This strips the sort from the address bar on purpose (the
        // new type has its own default), and it used to strip the preview with it -- so a reload
        // landed on the member's render while the banner still claimed otherwise.
        var preview = previewParam();
        var cleaned = path + (preview ? '?' + preview : '');
        if (window.history && window.history.replaceState) {
            window.history.replaceState({}, '', cleaned);
        }
        // The same guard `refreshItems` documents at length, and for the same reason: htmx resolves
        // its ajax promise for EVERY status and maps 4xx/5xx to `swap: false`. Without this a 500
        // here resolved quietly, leaving numerals and grips on a list the server no longer calls
        // ranked, a sort control still offering "List order", and no sign anywhere.
        var mine = ++refreshSeq;
        var before = itemsRoot();
        return window.htmx.ajax('GET', path + '?chrome=1' + (preview ? '&' + preview : ''),
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

    /* ─── the card's action menu ───────────────────────────────────────────────────────────────
     *
     * One trigger per card, opening `Move to…` plus `Remove`. Built on `PlatPursuit.AnchoredMenu`,
     * which owns the panel, the flip-and-clamp positioning, the focus restore and the four document
     * listeners -- all of it extracted from quick-add rather than written a ninth time.
     */
    var cardMenu = null;

    /**
     * The destinations, read from the RENDERED grouping rather than rebuilt.
     *
     * The server already decided what the sections are and what order they are in; the page is
     * rendered from that decision, so reading it back is one source of truth rather than two. A
     * client-side list assembled from somewhere else is how a menu comes to offer a section that was
     * deleted in the swap that just landed.
     *
     * Each header carries `id="gl-section-<pk>"`, and the loose bucket's is `gl-section-none` --
     * which is why the id is parsed rather than the name trusted: two sections MAY share a name
     * (the model's own constraint comment says so), so names cannot identify anything.
     */
    function sectionChoices() {
        var out = [];
        var heads = document.querySelectorAll('#gl-items-root .gl-section__name');
        Array.prototype.forEach.call(heads, function (h) {
            var id = (h.id || '').replace('gl-section-', '');
            if (!id || id === 'none') { return; }     // the loose bucket is appended below, always
            out.push({ id: id, name: h.textContent.trim() });
        });
        return out;
    }

    function cardMenuHtml(trigger) {
        var rows = '';
        // `data-current` is absent when the card cannot be filed at all -- a list with no sections,
        // or a viewer the server did not give `can_arrange`. Then the menu is just the remove row,
        // which is still worth a menu: the alternative is a bare destructive button back in the
        // corner the grip is trying to share.
        if (trigger.hasAttribute('data-current')) {
            var current = trigger.dataset.current || '';
            var choices = sectionChoices();
            if (choices.length) {
                rows += '<p class="gl-menu__head">Move to</p>';
                choices.forEach(function (c) {
                    rows += rowHtml(c.id, c.name, current === c.id);
                });
                // ALWAYS OFFERED, even when the loose bucket is not on screen. Un-filing is the one
                // move that has no header to drop onto once the bucket hides itself, and before this
                // menu existed the only way out of a section was to delete the whole section.
                rows += rowHtml('', 'No section', current === '');
            }
        }
        rows += '<div class="gl-menu__sep"></div>'
            + '<button type="button" class="gl-menu__item gl-menu__item--danger" data-gl-menu-remove>'
            + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>'
            + 'Remove from list</button>';
        return rows;
    }

    function rowHtml(id, name, isCurrent) {
        // `aria-current` rather than `aria-selected`, which belongs to listbox/tab roles this is not
        // using; and `disabled` rather than a click that quietly does nothing, so the one row that
        // cannot act says so to a screen reader instead of only to the eye.
        //
        // TWO DIFFERENT ESCAPERS, and the difference is not cosmetic. `escape` runs the HTML
        // fragment serializer, which deliberately leaves QUOTES alone because a text node has no
        // need of them -- correct for the name between the tags, and wrong one character later
        // inside an attribute's quotes, where an unescaped `"` closes it. `escapeAttr` is the one
        // its own docstring points at for that; the first cut of this used `escape` for both.
        return '<button type="button" class="gl-menu__item' + (isCurrent ? ' is-current' : '') + '"'
            + ' data-gl-menu-section="' + PP.HTMLUtils.escapeAttr(id) + '"'
            + (isCurrent ? ' aria-current="true" disabled' : '')
            + '>' + PP.HTMLUtils.escape(name) + '</button>';
    }

    function wireCardMenu() {
        if (cardMenu || !PP.AnchoredMenu) { return; }
        cardMenu = PP.AnchoredMenu({
            trigger: '[data-gl-card-menu]',
            className: 'gl-menu',
            label: 'Card actions',
            item: '.gl-menu__item',
            onOpen: function (trigger, el) {
                el.innerHTML = cardMenuHtml(trigger);
                var first = el.querySelector('.gl-menu__item:not([disabled])');
                if (first) { first.focus(); }
            },
            onItem: function (row, trigger) {
                if (row.hasAttribute('data-gl-menu-remove')) {
                    cardMenu.close(false);
                    onRemove(trigger);
                    return;
                }
                var target = row.getAttribute('data-gl-menu-section');
                if (target === null) { return; }
                var tile = trigger.closest('.gl-item');
                var grid = tile && tile.closest('[data-gl-arrange]');
                cardMenu.close(false);
                if (!tile || !grid) { return; }
                // THE SAME FORK THE DRAG TAKES. `moveItemToSection` decides between the reorder
                // endpoint and the assign one; posting `list_item_assign` directly from here would
                // silently diverge a ranked list's ordering, which is the defect this menu was most
                // likely to introduce.
                moveItemToSection(grid, tile.dataset.itemId, target);
            },
        });
    }

    function onRemove(btn) {
        if (btn.dataset.busy === '1') { return; }
        btn.dataset.busy = '1';
        var tile = btn.closest('.gl-item');
        if (tile) { tile.classList.add('is-removing'); }

        // Remember the position ONLY if the keyboard is what pressed this, so the post-swap restore
        // does not yank focus away from a mouse user reading elsewhere on the page.
        if (document.activeElement === btn) {
            var all = Array.prototype.slice.call(
                // `#gl-items-root`, NOT `#gl-items`. The latter is rendered only on a FLAT list
                // (`detail_group.html` gates it on `not groups`), so on a sectioned one this
                // matched nothing and `indexOf` returned -1. That is the same bug
                // `restoreRemoveFocus` documents having fixed for ITS selector two hundred lines
                // down -- the capture half was missed, so the restore half was handed an index
                // that could never be right and focus fell to `<body>` on every removal.
                document.querySelectorAll('#gl-items-root [data-gl-card-menu]'));
            var found = all.indexOf(btn);
            // -1 IS NOT null, and the restore guard only checks for null -- so a miss here used to
            // flow through as `buttons[-1]`, which is `undefined`, and the focus call silently did
            // nothing. Normalised at the source so a future miss degrades to "no restore" loudly
            // rather than to "restore the wrong thing" quietly.
            pendingFocusIndex = found < 0 ? null : found;
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

    /**
     * The list's game adder.
     *
     * THE BEHAVIOUR MOVED TO `PP.GameAdder` when Tiers/Grids/Polls needed the same control. What was
     * here was not a search box: it was four shipped bug fixes wearing one (the sequence guard on all
     * four exits, the short-query orphan, the signed-out redirect trap, and the status line looked up
     * from `document` because `sr-only` is not a flex child). Those now live in one place, so the
     * fifth lands for both callers instead of for whichever page somebody happened to be on.
     *
     * WHAT STAYS HERE is everything list-shaped: the class prefix its stylesheet already uses, the
     * "On this list" copy, the tally, and the server re-render. None of that is shareable, and none
     * of it was ever the hard part.
     */
    function wireAdder() {
        var root = document.querySelector('[data-gl-adder]');
        if (!root || wired.has(root)) { return; }
        wired.add(root);

        var input = root.querySelector('[data-gl-adder-input]');
        var panel = root.querySelector('[data-gl-adder-results]');
        if (!input || !panel) { return; }

        if (!PP.GameAdder) { return; }
        PP.GameAdder(root, {
            input: input,
            panel: panel,
            // `document`, not `root`: the status line is a SIBLING of the search field in the toolbar
            // bar, not a child of it. Scoped to `root` this silently returned null and every count
            // announcement was dropped -- no error, just an accessibility feature quietly doing
            // nothing. (`sr-only` is absolutely positioned, so it is not a flex item in the bar.)
            status: document.querySelector('[data-gl-adder-status]'),
            prefix: 'gl-adder',
            // Matches `game_search.MIN_QUERY`. Below it the endpoint answers an empty list, so asking
            // is pure latency. MIN_QUERY = 3 -- pinned against the server by a test, so this literal
            // and that constant cannot drift.
            minQuery: 3,
            addLabel: 'Add',
            addedLabel: 'On this list',
            addAria: function (title) { return 'Add ' + title + ' to this list'; },
            addedAria: function (title) { return title + ' is already on this list'; },
            logLabel: 'list-detail',
            // The module's own copy, passed through rather than restated. Writing it out again here
            // would have been a second set of sentences for the same four statuses.
            failureCopy: failureCopy,
            onAdded: function (data) {
                setTally('[data-game-count]', data.game_count);
                if (PP.ToastManager) {
                    PP.ToastManager.show('Added ' + data.title + '.', 'success');
                }
                announce('Added ' + data.title + ' to the list.');
                // Its own catch, for the same reason as remove -- and worse here, because the
                // success toast has ALREADY fired. Chained into a shared catch, a failed re-render
                // put "Added Hollow Knight." and "That game could not be added." on screen together,
                // and re-armed a row that had just been disabled.
                return refreshItems().catch(function (err) {
                    logFailure('items refresh after add', err);
                    if (PP.ToastManager) {
                        PP.ToastManager.show('Added. Reload to see it in the list.', 'warning');
                    }
                });
            },
        });
    }

    /* ------------------------------------------------------------------ report ---- */

    /**
     * Report this list's name or description.
     *
     * A native `<dialog>` on the `.gd-modal` recipe, so the focus trap, Escape and focus restoration
     * come from the element. `dismissableSheet` adds the touch grab pill and swipe-down dismiss.
     *
     * POSTS JSON rather than submitting, unlike the create dialog next door, and the difference is
     * the refusals. "You have already reported this list", "that is your own list" and "pick a
     * reason" are all things the reporter can act on; a plain submit would replace the page with an
     * error and lose what they typed.
     *
     * ONE-WAY. On success the dialog closes and says thank you, and the control does NOT flip to a
     * "reported" state: a button that remembers would tell anybody sharing the page how many people
     * had already reported it, which is a coordination signal for exactly the pile-on this feature
     * exists to catch. The server enforces one report per hunter regardless.
     */
    function wireReport() {
        var dialog = document.getElementById('gl-report');
        var open = document.querySelector('[data-gl-report-open]');
        if (!dialog || !open || !dialog.showModal || wired.has(dialog)) { return; }
        wired.add(dialog);

        var form = dialog.querySelector('[data-gl-report-form]');
        var error = dialog.querySelector('[data-gl-report-error]');
        var send = dialog.querySelector('[data-gl-report-send]');
        if (!form) { return; }

        // Captured BEFORE anything mutates it, so the busy label can be restored without a second
        // copy of the string living in the JS and drifting from the template's.
        var sendLabel = send ? send.textContent : '';

        // CHOREOGRAPHED EXIT, the routine `gamelists.js` and `game-flag.js` already run. The CSS for
        // it was written with this dialog (`.gl-dialog.is-closing` -> `glDialogOut`, plus the scrim's
        // `glScrimOut`) and the comment above it claimed the JS waits for `animationend` -- but
        // nothing here ever added the class, so both animations were dead and every close was a hard
        // cut. An exit written and not played is worse than no exit: the next reader believes it.
        // `after` runs once the dialog is ACTUALLY closed, which matters more than it looks.
        //
        // The exit made this asynchronous, and the success path was still firing its toast and its
        // `announce()` on the line after the call -- i.e. while the modal was still open. A modal
        // `<dialog>` makes everything outside it inert and takes it out of the accessibility tree,
        // and the status region lives outside; a live region updated while it is hidden from AT
        // announces nothing, and by the time the dialog went the text had already changed. So the
        // one confirmation a blind reporter gets was landing in a dead region on every non-reduced
        // -motion path. The toast had a milder version of the same problem: it renders behind the
        // top-layer `::backdrop` until the exit finishes.
        // Callbacks waiting on the CURRENT close, and the fallback timer that belongs to it.
        //
        // A QUEUE, not a single `after`, because the first version dropped it. Its early return
        // for "a close is already running" was the one exit that did not invoke the callback --
        // so: press Send, then press Escape while the request is in flight; the response lands
        // 180-400ms later, calls `closeReport(cb)`, hits that return, and the reporter is never
        // told their report was filed. No toast, and no `announce()`, which is the entire failure
        // this callback refactor existed to fix, re-created inside the fix.
        var pendingAfter = [];
        var closeTimer = null;

        function closeReport(after) {
            if (typeof after === 'function') { pendingAfter.push(after); }
            var drain = function () {
                var queued = pendingAfter;
                pendingAfter = [];
                queued.forEach(function (fn) { fn(); });
            };
            if (!dialog.close || !dialog.open) { drain(); return; }
            var reduced = window.matchMedia
                && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (reduced) { dialog.close(); drain(); return; }
            // Already closing: the callback is queued above and the in-flight `done()` will drain
            // it. Returning here is right; returning WITHOUT having queued was the bug.
            if (dialog.classList.contains('is-closing')) { return; }
            dialog.classList.add('is-closing');
            var done = function () {
                // CLEARED, or a stale fallback from a previous close fires inside a later one --
                // cutting that exit short and draining a queue that is no longer its own.
                if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
                dialog.classList.remove('is-closing');
                dialog.close();
                drain();
            };
            // TARGET-GUARDED, and `pseudoElement`-guarded too. `animationend` bubbles, so any
            // descendant animation finishing mid-exit would end the close early -- and the
            // `::backdrop`'s own `glScrimOut` fires on THIS element with a `pseudoElement` set, so
            // checking the target alone is not enough. Both are 0.18s today, so nothing truncates;
            // shortening the scrim later would have silently halved the dialog's exit.
            var onEnd = function (e) {
                if (e.target !== dialog || e.pseudoElement) { return; }
                dialog.removeEventListener('animationend', onEnd);
                done();
            };
            dialog.addEventListener('animationend', onEnd);
            // A dropped `animationend` would strand the dialog open and un-closable.
            closeTimer = setTimeout(function () {
                closeTimer = null;
                if (dialog.classList.contains('is-closing')) {
                    dialog.removeEventListener('animationend', onEnd);
                    done();
                }
            }, 400);
        }

        open.addEventListener('click', function () {
            if (error) { error.hidden = true; error.textContent = ''; }
            // A FRESH FORM EACH TIME, like both sibling report dialogs. Without it a refused report
            // reopens carrying the words that were just rejected, which reads as the refusal having
            // been ignored.
            form.reset();
            // `reset()` fires a `reset` event, never `input`, and `wireCharCounters` renders only
            // on `input` -- so a refused report with 312 typed characters reopened with an empty
            // textarea above a counter still reading "312/500", warning colour and all. The
            // identity editor's own `reset()` dispatches exactly this for exactly this reason.
            var details = dialog.querySelector('[data-gl-report-details]');
            if (details) { details.dispatchEvent(new Event('input', { bubbles: true })); }
            if (send) {
                send.disabled = false;
                send.textContent = sendLabel;
                send.removeAttribute('aria-busy');
            }
            dialog.showModal();
            // THE DIALOG, not the `<select>`. Focusing the select made the first arrow keypress
            // silently change which reason is being reported -- a keyboard user who orients with
            // arrow keys files a different report than the one they read.
            dialog.focus();
        });

        Array.prototype.forEach.call(
            dialog.querySelectorAll('[data-gl-report-close]'),
            function (button) { button.addEventListener('click', closeReport); });

        // Escape, routed through the choreographed close rather than the browser's instant one.
        dialog.addEventListener('cancel', function (e) { e.preventDefault(); closeReport(); });

        // `handle:` IS THE DATA-LOSS FIX. `dismissableSheet`'s own contract: "Omit on a sheet you
        // READ; pass one on a sheet you OPERATE, where an accidental dismiss costs unsaved work."
        // This sheet holds up to 500 typed characters and passed nothing, so on touch a downward
        // flick starting anywhere that is not the textarea itself -- the header, a field label, the
        // error box -- armed the drag and threw the draft away past 90px. Both sibling dialogs pass
        // one and both record fixing this same bug.
        if (PP.dismissableSheet) {
            PP.dismissableSheet(dialog, {
                handle: '.gl-dialog__head',
                // A DIRECT close, not the choreographed one, and `game-flag.js` does the same.
                // The helper has already animated the sheet off-screen by the time it calls this,
                // and it clears the transform first -- so handing it `closeReport` made a flicked
                // sheet slide away, POP BACK into view, then play a second 180ms exit. The
                // swipe IS the exit; buttons, Escape and the backdrop still get the choreographed
                // one below.
                onClose: function () { if (dialog.close && dialog.open) { dialog.close(); } },
            });
        }

        // Backdrop click, which both siblings honour and this one did not. On a native `<dialog>`
        // a click on the backdrop reports the dialog itself as the target.
        dialog.addEventListener('click', function (e) { if (e.target === dialog) { closeReport(); } });

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            // REFUSED ONCE THE EXIT HAS STARTED. The dialog stays interactive for up to 400ms while
            // it fades, and the `finally` below re-enables the button immediately -- so a second
            // press (or Enter, with focus still on Send) fired a second request. The service's row
            // lock makes that safe on the server, but it comes back as "You have already reported
            // this list" and paints that refusal over the success toast for the one press the
            // reporter actually made.
            if (dialog.classList.contains('is-closing') || !dialog.open) { return; }
            if (error) { error.hidden = true; }
            // BUSY, NOT BLOCKED. Disabling alone renders as `opacity: .55` plus a not-allowed
            // cursor, which reads as "you may not do this" rather than "this is happening" -- the
            // only feedback for the whole round trip was the button going dim and forbidding. Both
            // sibling report dialogs change the label instead.
            //
            // NO `aria-busy`, which the first cut added: it tells a screen reader the element is
            // mid-update and can cause it to SUPPRESS reporting the contents -- which is the
            // "Sending..." label that is the entire point. The label already says it, to everyone.
            if (send) {
                send.disabled = true;
                send.textContent = 'Sending…';
            }

            var body = new FormData();
            var reason = dialog.querySelector('[data-gl-report-reason]');
            var details = dialog.querySelector('[data-gl-report-details]');
            body.append('reason', reason ? reason.value : '');
            body.append('details', details ? details.value : '');

            postJson(form.dataset.url, body)
                .then(function () {
                    // BOTH AFTER THE CLOSE COMPLETES. `ratings-tab.js` states the rule for the
                    // toast ("Toast AFTER close") because a toast raised while a dialog is open can
                    // die with it; the announcement needs it for a stronger reason, since a live
                    // region outside an open modal is inert and says nothing at all.
                    closeReport(function () {
                        if (PP.ToastManager) {
                            PP.ToastManager.show('Report sent. A moderator will take a look.', 'success');
                        }
                        // The toast carries no `aria-live`, as this file notes elsewhere, so the
                        // status line is what actually announces it.
                        announce('Report sent. A moderator will take a look.');
                    });
                })
                .catch(function (err) {
                    logFailure('report', err);
                    if (!error) { return; }
                    var show = function (msg) {
                        // UNHIDDEN FIRST, THEN WRITTEN. Setting text into a `hidden` `role="alert"`
                        // and revealing it afterwards is announced inconsistently across screen
                        // readers -- some never see a change, because the node was out of the tree
                        // when it happened. Revealing an empty alert announces nothing, so this
                        // order is safe and is the one that reliably speaks.
                        error.hidden = false;
                        error.textContent = msg || 'That report could not be sent.';
                        // The error sits at the bottom of the dialog's ONLY scroll region, so with
                        // a soft keyboard up it could land off-screen: the reporter saw the button
                        // re-enable and nothing else. `nearest` so it does not yank a short form
                        // that was already showing it.
                        if (error.scrollIntoView) {
                            error.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                        }
                        announce(error.textContent);
                    };
                    if (err && err.signedOut) {
                        show('You may have been signed out. Reload the page and try again.');
                    } else if (err && err.response && typeof err.response.json === 'function') {
                        // The service's own words -- "you have already reported this list" is a
                        // different thing to learn than "that failed".
                        err.response.json()
                            .then(function (data) { show(data && data.error); })
                            .catch(function () { show(null); });
                    } else {
                        show(null);
                    }
                })
                .finally(function () {
                    // ONLY WHILE THE DIALOG IS STILL HERE. On success the exit is already running,
                    // and restoring the label there flickers "Sending..." back to "Send report"
                    // mid-fade. Reopening calls `form.reset()` and restores the label anyway, so
                    // the failure path is the only one that needs it.
                    if (send && dialog.open && !dialog.classList.contains('is-closing')) {
                        send.disabled = false;
                        send.textContent = sendLabel;
                        send.removeAttribute('aria-busy');
                    }
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

        // DOCUMENT-SCOPED, NOT `root`-scoped, and that is load-bearing.
        //
        // The opener used to be a pencil inside `[data-gl-identity]`, so `root.querySelector` found
        // it. It now lives in the `.gl-actions` band -- a sibling of that element, not a descendant
        // -- because a bare pencil beside the title read as an inline field edit rather than as the
        // way into the whole editor. The move left all three lookups returning null, so no click
        // listener was ever bound and the button did nothing at all.
        //
        // Looked up through one named helper rather than three call sites, so the next person who
        // moves this control breaks one line instead of silently unbinding it again. Not cached:
        // the header can be re-rendered underneath this.
        if (!root || wired.has(root)) { return; }
        var form = root.querySelector('[data-gl-identity-edit]');
        var view = root.querySelector('[data-gl-identity-view]');
        if (!form || !view) { return; }          // a visitor: no form rendered
        wired.add(root);

        var nameField = form.querySelector('[name="name"]');
        var descField = form.querySelector('[name="description"]');

        function open() {
            // RE-ENTRY DESTROYS UNSAVED WORK, and this guard is the whole of what stops it.
            //
            // The opener used to be a pencil INSIDE `[data-gl-identity-view]`, which the next line
            // hides -- so while the editor was open the control was simply gone and could not be
            // pressed again. Moving it into the `.gl-actions` band made it permanently visible,
            // and `open()` begins by calling `reset()`, which overwrites both fields from the
            // server-rendered DOM. So: open the editor, retype the name and a long description,
            // press "Edit list" again because it is sitting right there -- and everything typed is
            // silently discarded. A screen reader is led straight into it, because the button now
            // says `aria-expanded="true"`.
            //
            // Also refused MID-SAVE, for the reason `dismiss()` writes down for itself: `reset()`
            // would read the pre-save heading back into the fields, the response would then write
            // the new name into the heading, and the two would disagree until somebody pressed
            // Save again and renamed it back. That guard existed on two of the three entry points.
            var saving = form.querySelector('[data-gl-edit-save]');
            if (saving && saving.dataset.busy === '1') { return; }

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
            // JUST THE PANEL NOW. Leaving the mode is `exitEditing`, which calls this -- the panel
            // no longer decides anything about dragging, because it is not a mode. The comment that
            // stood here explained why closing the editor also left position editing; there is only
            // one thing to leave now, so the coupling it described has nothing left to describe.
            form.hidden = true;
            view.hidden = false;
            var tallies = document.querySelector('[data-gl-tallies]');
            if (tallies) { tallies.hidden = false; }
            var toggle = editToggle();
            if (toggle) { toggle.focus(); }        // focus goes back where it came from
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

        identityShow = open;
        identityHide = close;

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

        // The remove control moved INTO the card menu (`onItem` calls `onRemove` with the trigger,
        // which is the element carrying `data-remove-url`). The trigger's own click is
        // `AnchoredMenu`'s, delegated from `document.body`, so nothing is handled here any more --
        // and nothing should be, or two handlers would open and immediately close the panel.

        // Section controls live inside the swapped panel and are replaced by the very refresh their
        // own handler triggers, so they are delegated for exactly the reason the card menu is.
        //
        // Rename and delete no longer have buttons of their own -- they are rows in the section's
        // `...` menu, which calls `onSectionRename` / `onSectionDelete` with the TRIGGER, the
        // element still carrying `data-rename-url`, `data-delete-url` and `data-section-name`. The
        // menu trigger's own click belongs to `AnchoredMenu`.
        // `data-gl-add-to-section`, NOT `data-gl-section-add-game`. The latter was the first
        // name and it CONTAINS `data-gl-section-add`, which is the section-creation form in the
        // controls strip -- so a negative assertion about a free owner's access to that form started
        // matching this button instead. Exactly the hazard the `[data-gl-delete]` ordering comment
        // below warns about, arriving through a substring rather than through `closest`.
        var addHere = target.closest('[data-gl-add-to-section]');
        if (addHere) { onSectionAddGame(addHere); return; }

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
        // is a new node with no listener. `wireEditToggle` is WeakSet-guarded on that node, so this
        // is a no-op when nothing was swapped.
        wireEditToggle();
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
        // AFTER `wireSections` for the same reason `restoreSectionFocus` is: the header it re-docks
        // under has to be the live node, not the one about to be replaced.
        redockAdder();
    }

    function wireEditToggle() {
        var toggle = editToggle();
        if (!toggle || wired.has(toggle)) { return; }
        wired.add(toggle);
        toggle.addEventListener('click', function () {
            if (editing) { exitEditing(); } else { enterEditing(); }
        });
        paintEditToggle();
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
        if (!arranging) { return; }
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
        var buttons = document.querySelectorAll('#gl-items-root [data-gl-card-menu]');
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
        if (!arranging) { return; }
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

    /**
     * Attach dragging if this page can honour it. Called BY the mode, never by the hunter.
     *
     * Separated from entering the mode because the two can disagree: a hunter can be editing a list
     * that has nothing draggable in it, and must still be able to rename it, add to it and remove
     * from it. The old code could not express that -- entering the mode WAS attaching the drag, so a
     * failure to attach had to refuse the whole thing.
     */
    function startArranging() {
        var grids = arrangeGrids();
        // BOTH refusals happen before any state changes. `attachDrag` bails when SortableJS is
        // missing, and it used to do so AFTER the flag and the tray were already on -- leaving a
        // grid with no drag, no pick-up, and cards that still navigate, dressed as if it had all
        // three. Now a refusal simply means the mode runs without dragging, which is a state the
        // page can render honestly.
        if (arranging || !grids.length || !PP.DragReorderManager) { return; }
        arranging = true;
        var panel = document.getElementById('gl-items-panel');
        // The flag lives on the PANEL, not on a grid. The panel is the swap TARGET, so its own
        // attributes survive; the grids are swapped content, and htmx would restore their
        // server-rendered attributes on settle and silently drop the flag -- the trap this file
        // already documents twice.
        if (panel) { panel.dataset.glArranging = '1'; }
        attachDrag(grids);
    }

    function stopArranging() {
        if (!arranging) { return; }
        arranging = false;
        var panel = document.getElementById('gl-items-panel');
        if (panel) { delete panel.dataset.glArranging; }
        detachDrag();
    }

    /**
     * THE MODE. One toggle, everything inside it.
     *
     * The identity form, the section controls, the per-section adders, the remove buttons and the
     * dragging all arrive together and leave together, so "can I change this list right now" has one
     * answer the page states in one place.
     */
    function enterEditing() {
        if (editing) { return; }
        editing = true;
        var panel = document.getElementById('gl-items-panel');
        if (panel) { panel.dataset.glEditing = '1'; }

        // The controls strip: section add, numbering, the save status. Revealed by the mode rather
        // than gated behind a second press, which is what made "add a section" undiscoverable.
        var block = document.querySelector('[data-gl-positions]');
        if (block) { block.hidden = false; }

        if (identityShow) { identityShow(); }
        startArranging();
        paintEditToggle();
        setPositionsStatus('');
        announce(arranging
            ? (orderingLive(arrangeGrids())
                ? 'Editing on. Drag a card, or click one to pick it up and move it with the arrow keys.'
                : 'Editing on. Drag a card onto another section, or click one to pick it up and move it between sections with the arrow keys.')
            : 'Editing on.');
    }

    function exitEditing(silent) {
        if (!editing) { return; }
        editing = false;
        var panel = document.getElementById('gl-items-panel');
        if (panel) { delete panel.dataset.glEditing; }
        var block = document.querySelector('[data-gl-positions]');
        if (block) { block.hidden = true; }

        if (identityHide) { identityHide(); }
        stopArranging();
        // EVERYTHING THE MODE PUT ON SCREEN LEAVES WITH IT, and these two do not live inside the
        // gated subtree so the CSS cannot do it for them.
        //
        // The menus are panels on `document.body`: their triggers go `display: none` with the mode,
        // but an OPEN panel keeps floating -- anchored to a trigger that is still `isConnected`,
        // holding focus, offering "Remove from list" on a page that has left edit mode.
        //
        // The adder is worse, because it is actionable. Docked under a section header it keeps its
        // accent rail and its `data-section`, while the "+ Add game" toggle that would send it home
        // is now hidden -- so it claims a destination, accepts input, and files games into that
        // section from outside the mode, with no way back short of a refresh.
        if (cardMenu) { cardMenu.close(false); }
        if (sectionMenu) { sectionMenu.close(false); }
        parkAdder();
        pendingDockSection = null;      // leaving is not a refresh; do not come back docked
        paintEditToggle();
        setPositionsStatus('');
        if (!silent) { announce('Editing off.'); }
    }

    function paintEditToggle() {
        var toggle = editToggle();
        if (toggle) {
            // NOT `aria-pressed`. This carried both a pressed state AND a changing label, which APG
            // says a toggle button must not do: the two describe the same fact twice and disagree
            // about what the word means -- "Done, pressed" leaves a reader unsure whether "Done" is
            // the state or the action. The label change is the more useful half on a control this
            // size, so the button is an ACTION button naming what it will do next, and the mode
            // change itself is announced through the live region.
            //
            // `aria-expanded` goes for the same reason, and it is a CHANGE: the control used to
            // disclose a panel, so expanded was honest. It now enters a mode that reaches the whole
            // page -- the cards, the section headers, the adders -- and "expanded" describes none of
            // that. A control cannot be both a disclosure and a mode switch.
            toggle.removeAttribute('aria-pressed');
            toggle.removeAttribute('aria-expanded');
            var label = toggle.querySelector('[data-gl-edit-label]');
            // Must match the server-rendered string in detail.html, or the button says one thing on
            // load and another the first frame after boot -- a bug this file already shipped once.
            if (label) { label.textContent = editing ? 'Done' : 'Edit list'; }
        }

        // The BAR carries the state, not just the button. A label flipping between two words is easy
        // to miss; a full-width surface changing colour is not, and it is the difference between
        // knowing the mode is on and inferring it from the grips.
        var block = document.querySelector('[data-gl-positions]');
        if (block) { block.classList.toggle('is-on', editing); }

        var hint = document.querySelector('[data-gl-positions-hint]');
        if (!hint) { return; }
        // WHAT THIS MODE CAN ACTUALLY DO HERE, which is not the same on every list. The three
        // capabilities are independent: sections may be manageable with nothing draggable, and a
        // drag may file without ordering. Naming a gesture the page will refuse is worse than
        // naming none.
        if (!arranging) {
            // Editing with nothing to drag: a Collection with no sections, or a list of one game.
            // The mode is still doing something -- rename, add, remove -- so it says so.
            hint.textContent = 'Rename it, add games, or remove them. Changes save as you make them.';
        } else if (!orderingLive(arrangeGrids())) {
            // Filing only: a drop POSITION is an artefact of the sort, so the copy must not promise
            // ordering the server will discard.
            if (pickedRow) {
                hint.textContent = 'Arrow keys move it to the next section. '
                    + 'Click it again or press Escape to drop it.';
            } else {
                hint.textContent = 'Drag a card onto another section, or click one to pick it '
                    + 'up. Moves save as you make them.';
            }
        } else if (pickedRow) {
            hint.textContent = 'Arrow keys move it. Click it again or press Escape to drop it.';
        } else {
            hint.textContent = 'Drag a card, or click one to pick it up. Moves save as you make them.';
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
            // `ordering` is no longer passed: `moveItemToSection` derives it, so the drag and the
            // card menu cannot disagree about which endpoint a move goes to. Reading it at move
            // time rather than at attach time is also the stricter of the two -- `data-gl-reorder`
            // is server-rendered and stable through a drag, so the answer is the same, but a grid
            // re-rendered under a live manager would now be read correctly rather than from a
            // value captured before the swap.
            onMove: function (itemId, evt) { onCrossSectionDrop(grid, itemId, evt); },
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
            // THE MENU TRIGGER IS EXCLUDED; the grip deliberately is not.
            //
            // The grip is a button and a drag starting on it still works, because it is inside the
            // draggable item -- which is what you want from a control whose whole job is "this
            // thing moves". The `...` menu is the opposite: it is there to be PRESSED, and with
            // `forceFallback: true` and no mouse delay a mousedown plus three pixels of travel
            // starts dragging the card instead of opening it.
            //
            // The comment here used to say nothing needed excluding "while the remove control is
            // hidden in this mode". That stopped being true when the single mode landed: the CSS
            // flipped from hiding the control while ARRANGING to hiding it while NOT EDITING, so
            // the trigger is now on screen for the whole of the state in which cards are draggable.
            // The note said "listed for the next control that is not" -- this is that control.
            dragExclude: '.gl-item__menu',
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
    /**
     * Move one card into a section, whatever asked for it.
     *
     * THE THREE-WAY FORK LIVES HERE AND NOWHERE ELSE. Which endpoint a move goes to depends on what
     * a POSITION means on this page, and getting it wrong is silent:
     *
     *   ordering live  -> `list_reorder`, carrying the order AND the section in ONE write. Sending
     *                     them separately leaves a window where the card sits in the right place
     *                     under the wrong header, which looks correct until a reload.
     *   filing only    -> `list_item_assign`. There is no order to send: the drop index is wherever
     *                     the cursor happened to be over a grid the SERVER sorts, and posting it
     *                     would overwrite the author's sequence with the shape of a view.
     *
     * Extracted from `onCrossSectionDrop` when the card menu arrived, because a menu that always
     * posted an assignment would silently diverge a ranked list's ordering -- and the two callers
     * would have had to agree about a rule neither of them stated.
     *
     * `fullOrder()` is the CURRENT DOM order, which for a menu move is the unchanged one: "move to
     * Playing" is not a request to reshuffle. Positions stay global and the grouping is a render-time
     * overlay, so the card keeps its rank and simply appears under a different header.
     */
    function moveItemToSection(grid, itemId, sectionId, evt) {
        if (orderingLive(arrangeGrids())) {
            // `refresh: true` unlike a within-grid reorder, because a card CHANGING GROUP changes
            // things the optimistic repaint cannot reach: the count beside each header, and whether
            // the group it left still exists at all (the loose bucket is omitted when empty, so
            // emptying it by hand leaves a header reading 0 over nothing).
            // `placed` -- is the node ALREADY where it belongs? True after a drag, because
            // SortableJS moved it before this was ever called; false for the card menu, which moves
            // nothing and needs the repaint to do it. Without this the menu posted a move the page
            // never showed, and the drag would have had its card appended a second time.
            saveOrder(grid, fullOrder(), 'Moved.',
                      { movedItem: itemId, section: sectionId, refresh: true, placed: !!evt });
            return;
        }
        saveAssignment(itemId, sectionId, evt);
    }

    function onCrossSectionDrop(grid, itemId, evt) {
        // WHERE IT LANDED, not where it started: the `end` event fires on the SOURCE manager, so
        // `this.container` is the grid the drag began in. `evt.to` is the destination, and an empty
        // `data-section-id` there is a real answer -- the loose bucket -- rather than a missing one.
        var landed = (evt && evt.to) || grid;
        moveItemToSection(grid, itemId, landed.dataset.sectionId || '', evt);
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
        if (!arranging || justDragged) { return; }
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
        paintEditToggle();
    }

    function dropPicked(silent) {
        if (!pickedRow) { return; }
        pickedRow.classList.remove('is-picked');
        setGrabPressed(pickedRow, false);
        pickedRow = null;
        if (!silent) { announce('Dropped.'); }
        paintEditToggle();
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
        syncArranging();
        if (!grids.length) { return; }
        if (arranging) { attachDrag(grids); }
    }

    /**
     * Decide whether the bar is on screen, from the three things that actually govern it.
     *
     * 1. The SERVER allows reordering here (`data-gl-reorder` on the live grid) -- owner, ranked,
     *    real sequence. ("...and short enough to render whole" stood here too, and went when the size
     *    cap made a truncated list impossible.)
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
    function syncArranging() {
        // THE GRID DECIDES, AND IT STILL HAS TO. The hazard this function was written for survives
        // the collapse to one mode, only renamed: switching Ranked -> Collection, or sorting A-Z,
        // removes every `[data-gl-arrange]` from the page WHILE THE MODE IS STILL ON. Nothing else
        // turns dragging off, so without this it stayed on over a grid that no longer exists --
        // the keydown listener stayed bound, and `pickedRow` went on pointing at a detached row
        // whose grid still carried `data-reorder-url`, so arrow keys silently rewrote positions on
        // a list nobody could see.
        //
        // What CHANGED is that it no longer decides whether the hunter is editing. Losing the
        // draggable grids used to end the whole editing session, because the two were one state;
        // now it ends only the dragging, and the rename, the sections and the remove buttons stay
        // exactly where they were. A sort should not close the editor.
        if (!arrangeGrids().length) { stopArranging(); }
        else if (editing) { startArranging(); }

        // The controls strip follows the MODE, not the grids. A member owner of an empty or
        // section-less list still gets the "Add a section" row, which is not a drag affordance and
        // must not vanish with one.
        var block = document.querySelector('[data-gl-positions]');
        if (block) { block.hidden = !editing; }
        paintEditToggle();
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
        if (!arranging || isTyping(e.target)) { return; }

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
                    // `placed: true` -- `stepIntoNeighbourGrid` has ALREADY put the row where it
                    // goes, and for a forward step that is the START of the next group
                    // (`insertBefore`), which is also the order `fullOrder()` just posted. Without
                    // this the repaint appended it to the END instead: the database held "first",
                    // the screen showed "last", the spoken rank contradicted the plate, and a
                    // second press skipped a whole group.
                    movedItem: row.dataset.itemId, section: landedIn, refresh: true, placed: true,
                });
            } else {
                // `placed` for the same reason as the ordering branch above: the node has
                // already been moved by `stepIntoNeighbourGrid`.
                saveAssignment(row.dataset.itemId, landedIn, null, true);
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
                if (move && move.refresh
                        && !repaintAfterGroupChange(move.movedItem, move.section, move.placed)) {
                    // Only when the repaint could not finish the job -- today that means a card
                    // leaving its last section with no ungrouped bucket rendered to receive it.
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
    /**
     * The grid belonging to a group header, skipping anything docked between them.
     *
     * Not `nextElementSibling`: the adder docks BETWEEN a header and its grid, so the naive read
     * returns the adder whenever somebody is adding to that section.
     */
    function groupGridFor(head) {
        var next = head.nextElementSibling;
        while (next && !next.classList.contains('gl-group__grid')) {
            if (next.classList.contains('gl-section__head')) { return null; }
            next = next.nextElementSibling;
        }
        return next;
    }

    function gridForSection(sectionId) {
        return document.querySelector(
            '#gl-items-root [data-gl-arrange][data-section-id="' + (sectionId || '') + '"]');
    }

    /** The number beside each heading, recounted from the rows actually under it. */
    function syncGroupCounts() {
        var heads = document.querySelectorAll('#gl-items-root .gl-section__head');
        Array.prototype.forEach.call(heads, function (head) {
            var grid = groupGridFor(head);
            var badge = head.querySelector('.gl-section__count');
            if (!grid || !badge) { return; }
            // No thousands separator to reproduce: `MAX_ITEMS_PER_LIST` is 200, so `intcomma` in the
            // template is a no-op at every reachable value. If that cap ever rises this has to learn
            // the same formatting, or the count will disagree with itself across a refresh.
            badge.textContent = String(grid.querySelectorAll('.gl-item').length);
        });
    }

    /**
     * Drop the ungrouped bucket once it is empty, which is what the server does.
     *
     * The bucket renders only when it holds something (2026-09), so a client that files the last
     * loose card and leaves the heading behind is showing a state the next page load will not
     * reproduce. Only the LOOSE bucket: a named section is a real thing that legitimately sits empty.
     */
    function pruneEmptyLooseBucket() {
        var head = document.querySelector('#gl-items-root .gl-section__head--loose');
        if (!head) { return; }
        var grid = groupGridFor(head);
        if (!grid || grid.querySelector('.gl-item')) { return; }
        if (grid.parentNode) { grid.parentNode.removeChild(grid); }
        if (head.parentNode) { head.parentNode.removeChild(head); }
    }

    /**
     * Bring the page up to date after a card changed group, WITHOUT re-rendering it.
     *
     * Returns false when it cannot, and the caller falls back to a refresh.
     *
     * WHY THIS EXISTS: a cross-group move used to end in `refreshItems()`, so moving one card
     * between two headings re-rendered every card in the list and re-fetched every cover -- while
     * reordering a card WITHIN a section repainted in place and did not. The owner noticed the
     * asymmetry, which is the tell that the refresh was never about the move itself.
     *
     * It was about two things the old optimistic repaint could not reach, and both are reachable
     * now: the count beside each heading, and the ungrouped bucket vanishing as it empties. The
     * failure message the refresh carried said as much -- "Reload the page to see the counts update".
     *
     * THE ONE CASE IT STILL CANNOT DO is the mirror of that last one: filing a card OUT of every
     * section when no ungrouped bucket is on the page means a whole group has to appear, with a
     * heading, a grid, its `role`/`aria-labelledby` pair and the reorder endpoint the server owns.
     * Hand-assembling that markup in JS is the thing this codebase has been bitten by repeatedly, so
     * that case returns false and takes the refresh it needs.
     *
     * `placed` is whether the node is already where it belongs: true after a DRAG, because SortableJS
     * moved it before the write was ever queued, and false for the card menu, which moves nothing.
     */
    function repaintAfterGroupChange(itemId, sectionId, placed) {
        // ONLY WHERE THE CLIENT CAN REPRODUCE THE SERVER'S RENDER, which is narrower than the first
        // cut assumed and was found by audit rather than by use.
        //
        // `_number` (gamelists/views.py) does NOT number a sectioned list `position + 1`. Its own
        // docstring records that as "the first cut" and abandons it: once a list has sections, the
        // sections are part of the sequence, so continue-through runs a counter ACROSS the groups in
        // their order, and within each group it sorts by `position` rather than taking the rendered
        // order. Two things follow, and the first version of this function got both wrong:
        //
        //   - the rendered order is only the canonical order at the `rank` sort. Under A-Z the
        //     server keeps each entry's real rank and prints it out of sequence on the page, which
        //     is the point; `renumber()` walks the DOM, so it would relabel the list 1..N
        //     alphabetically and claim the alphabet was the author's ranking.
        //   - appending the card to the END of its new group is only right when the page is in
        //     position order. Anywhere else the server slots it by the page's sort and the client
        //     does not.
        //
        // `orderingLive()` is exactly "the page is at the real sequence" (`can_reorder` is
        // `arrangeable and sort == 'rank'`), so inside it DOM order IS canonical order and
        // `renumber()` reproduces `_number` line for line. Outside it, the honest answer is the
        // round trip -- which is what the `saveAssignment` docstring argued before this function
        // overruled it without updating it.
        if (!orderingLive(arrangeGrids())) { return false; }
        var dest = gridForSection(sectionId);
        if (!dest) { return false; }
        var row = document.querySelector('.gl-item[data-item-id="' + itemId + '"]');
        if (!row) { return false; }
        if (!placed) { dest.appendChild(row); }

        // THE CARD'S OWN MENU HAS TO BE TOLD. `data-current` is server-rendered per card and is what
        // the menu ticks and disables; it was always correct before, because the only way a card
        // changed group was a re-render that rebuilt the attribute with it.
        //
        // Skipping this is silent and confusing rather than broken: the card moves, and then its
        // menu still shows the section it LEFT as the current one -- ticked and unpressable -- while
        // offering the section it is actually in as somewhere to move to.
        var trigger = row.querySelector('[data-gl-card-menu]');
        if (trigger && trigger.hasAttribute('data-current')) {
            trigger.dataset.current = sectionId || '';
        }

        syncGroupCounts();
        pruneEmptyLooseBucket();
        // AFTER the prune, because restart-per-section numbering counts grids and a bucket that is
        // about to disappear would otherwise be counted as a group.
        renumber();
        return true;
    }

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

                // GATED ON THE BADGE, like the line above it. `detail_card.html` renders the
                // "Number N: " prefix under `{% if is_ranked %}` and the plate under the same flag,
                // so a list with no plates has no prefix either -- and writing one anyway announced
                // a ranking a Collection does not have, to screen readers only, on every card.
                if (!badge) { continue; }
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
     * On success the panel is REFRESHED rather than left as dropped, UNLESS the page is at the real
     * sequence. SortableJS has already put the card where the cursor let go, and the server sorts
     * each group independently -- so outside the `rank` sort the card would sit out of the page's
     * order until something else re-renders, which reads as a bug on the one sort whose whole
     * promise is that it is alphabetical.
     *
     * `repaintAfterGroupChange` takes the other case and refuses this one; it was briefly allowed to
     * take both, which made this paragraph false without anybody editing it. The refresh also repaints both
     * section counts, which the drop just changed.
     */
    function saveAssignment(itemId, sectionId, evt, placed) {
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
                // SAME TREATMENT AS THE ORDERING PATH. This is the filing-only fork -- a Collection,
                // or a ranked list being read A-Z -- and it was re-rendering the whole list for a
                // card changing heading, which is the asymmetry the owner reported: reordering
                // within a section repainted in place and moving between sections did not.
                if (repaintAfterGroupChange(itemId, sectionId, placed || !!evt)) { return; }
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

    /* ─── the section header's menu, and its adder ─────────────────────────────────────────────── */

    var sectionMenu = null;

    /**
     * The section headers in rendered order, which IS their order.
     *
     * `position` is dense and the server sorts on it, so the DOM is the ordering -- reading it back
     * avoids a second copy that can disagree with what the reader is looking at. The same argument
     * `sectionChoices` makes for the card menu's destinations.
     */
    function sectionTriggers() {
        return Array.prototype.slice.call(
            document.querySelectorAll('#gl-items-root [data-gl-section-menu]'));
    }

    /**
     * Move one section one place, by rewriting the WHOLE order and posting it.
     *
     * `reorder_sections` refuses a partial ordering -- deliberately, the same way `reorder` does for
     * items -- so a "move up" cannot be expressed as a delta. Swapping two entries of the full list
     * is the whole implementation.
     *
     * THIS ENDPOINT HAS BEEN LIVE AND UNCALLED SINCE IT SHIPPED. The service, the view and the URL
     * all existed with a test against the service and no client anywhere, which is why sections
     * could be renamed and deleted but never reordered.
     */
    /**
     * The nodes one section owns: its header, its grid, and anything docked between them.
     *
     * A section is NOT one element -- `detail_group.html` renders the header and the grid as
     * siblings, deliberately (the grid's `:empty::before` drop box needs the grid to have no element
     * children, so it cannot be wrapped). So "move a section" means moving a run of siblings, and
     * the run is however many nodes sit between this header and the next one: today that is the
     * grid, plus the adder when it is docked here, which should travel with the section it is
     * pointed at rather than being left behind under somebody else's heading.
     */
    function sectionBlock(trigger) {
        var head = trigger.closest('.gl-section__head');
        if (!head) { return []; }
        var nodes = [head];
        var next = head.nextElementSibling;
        while (next && !next.classList.contains('gl-section__head')) {
            nodes.push(next);
            next = next.nextElementSibling;
        }
        return nodes;
    }

    /**
     * Slide a section past its neighbour, in the DOM, with a FLIP settle.
     *
     * WHY NOT JUST REFRESH: it used to, through `refreshItems(true)`, which `innerHTML`-swaps the
     * whole items panel plus the out-of-band chrome. So moving one header re-rendered every card in
     * every section, re-fetched every cover, re-ran the arrival reveal and tore down every Sortable
     * -- for a change that is two headings trading places. The owner's word for it was "jumpy", and
     * the item drag had already reached the opposite conclusion for the same reason: a plain reorder
     * repaints in place precisely so it does not "redraw forty covers that did not change".
     *
     * THE RANKS DO HAVE TO CHANGE, and the first version of this said the opposite. It argued
     * that `position` is global and per-ITEM, so a section move cannot touch it, therefore
     * continue-through ranks are `position + 1` and nothing needs recomputing.
     *
     * `_number` in `gamelists/views.py` records `position + 1` as "the first cut" and ABANDONS it
     * for sectioned lists, in a docstring written after that exact mistake shipped: once a list has
     * sections, the sections are part of the sequence, so continue-through runs one counter across
     * the groups in their order. Move a section up and everything after it renumbers. Only
     * restart-per-section is genuinely unaffected.
     *
     * The lesson is narrower than "read the code": `position` really is global and really is
     * untouched here. What was wrong was inferring the DISPLAYED rank from the stored one, when the
     * function that computes the displayed rank exists and says not to.
     *
     * The menu's own disabled states are computed from DOM order when it opens, so those do follow
     * for free.
     *
     * FLIP, because an instant jump is the other half of what "jumpy" meant: measure, move, then
     * animate from the old box to the new one so the eye can follow the section rather than having
     * to re-find it. Reduced motion skips straight to the moved state.
     */
    function slideSectionBlock(block, before) {
        var reduced = window.matchMedia
            && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        var firsts = reduced ? null : block.map(function (n) {
            return n.getBoundingClientRect().top;
        });
        var parent = block[0].parentNode;
        block.forEach(function (n) { parent.insertBefore(n, before); });
        if (!firsts || !block[0].animate) { return; }
        block.forEach(function (n, i) {
            var delta = firsts[i] - n.getBoundingClientRect().top;
            if (!delta) { return; }
            n.animate(
                [{ transform: 'translateY(' + delta + 'px)' }, { transform: 'none' }],
                { duration: 260, easing: 'cubic-bezier(0.34, 1.2, 0.64, 1)' }
            );
        });
    }

    /**
     * The node that follows a section's whole block, for inserting AFTER it.
     *
     * `null` when it is the last section, which `insertBefore(n, null)` reads as "append" -- exactly
     * what moving to the end should do.
     */
    function nextHeadAfter(trigger) {
        var block = sectionBlock(trigger);
        if (!block.length) { return null; }
        return block[block.length - 1].nextElementSibling;
    }

    function moveSection(trigger, delta) {
        var sectionId = trigger.dataset.sectionId;
        var reorderUrl = trigger.dataset.reorderUrl;
        if (!sectionId || !reorderUrl) { return; }

        // EVERYTHING IS READ INSIDE THE QUEUE, and that placement is the whole correctness of this
        // function. The first version read the order and built the FormData out here, at click time,
        // and queued only the POST -- under a comment claiming the queue solved the race. It did not:
        // joining the chain delays the SEND, and the body was already frozen.
        //
        // The failure: open section B's menu, Delete; open section C's menu, Move up. The order
        // captured at click time still contains B. B's delete lands, the panel re-renders, then the
        // queued POST goes out carrying a section that no longer exists -- and `reorder_sections`
        // refuses a set that does not match the list ("That order does not match the list. Reload
        // and try again."). The owner is told a move they made failed, after a delete that worked.
        //
        // Read at SEND time and the order is whatever the DOM says once the writes ahead of it have
        // landed, which is the only order worth posting.
        queueSectionWrite(function () {
            var all = sectionTriggers();
            var at = -1;
            for (var i = 0; i < all.length; i++) {
                if (all[i].dataset.sectionId === sectionId) { at = i; break; }
            }
            // MATCHED ON THE ID, not on the node. The trigger this closure captured may have been
            // replaced by a refresh that ran while this was queued, so the original element is not
            // in the document any more even though its section still is.
            var to = at + delta;
            if (at < 0 || to < 0 || to >= all.length) {
                // The section was deleted, or it has already moved to the end, while this waited.
                // Nothing to post, and nothing went wrong: say nothing rather than reporting a
                // failure for a move the page no longer has a meaning for.
                return null;
            }

            var ids = all.map(function (b) { return b.dataset.sectionId; });
            var moved = ids.splice(at, 1)[0];
            ids.splice(to, 0, moved);

            var body = new FormData();
            ids.forEach(function (id) { body.append('section_ids[]', id); });

            // MOVED HERE, INSIDE THE QUEUED CALLBACK, not at click time. The optimistic repaint has
            // to happen against the same DOM the payload was read from, or the two describe
            // different lists -- and a rename or delete queued ahead of this one will have landed
            // and re-rendered by now. The queue is empty in the ordinary case, so this is still the
            // frame after the press.
            var block = sectionBlock(all[at]);
            var target = all[to].closest('.gl-section__head');
            if (block.length && target) {
                slideSectionBlock(block, delta < 0 ? target : nextHeadAfter(all[to]));
            }
            // THE RANKS MOVE WITH THE SECTIONS, which the first version of this denied in so many
            // words. `_number` runs its counter ACROSS the groups in their order, so continue-through
            // numbering is a function of group order -- move a section up and everything after it
            // renumbers. Only restart-per-section is unaffected, and `renumber()` handles both.
            //
            // At the `rank` sort `renumber()` reproduces `_number` exactly. Anywhere else it cannot
            // (see `repaintAfterGroupChange`), so a ranked list being read in another order takes
            // the refresh instead -- and a list with no ranks at all needs neither.
            if (orderingLive(arrangeGrids())) { renumber(); }
            else { pendingSectionRenumber = !!document.querySelector('#gl-items-root .gl-rank'); }

            setPositionsStatus('Saving…');
            return postJson(reorderUrl, body)
                .then(function () {
                    // GUARDED ON `pendingSaves`, like `saveOrder` and `saveAssignment`. Writing
                    // "Saved" unconditionally flips the pill over an outstanding reorder, which is
                    // the exact bug that guard was added for on the other two writers.
                    if (pendingSaves <= 1) { setPositionsStatus('Saved'); }
                    announce('Section moved.');
                    // NO REFRESH in the ordinary case: the DOM already shows the move and the
                    // server was only ever going to send back the same arrangement -- re-rendering
                    // every card to learn that is what made this feel jumpy. The chrome does not
                    // need it either, because the number of sections has not changed.
                    //
                    // The exception is a RANKED list being read in some order other than its own:
                    // the ranks changed with the group order and the client cannot compute them
                    // there, so it fetches the page that can.
                    if (pendingSectionRenumber) {
                        pendingSectionRenumber = false;
                        return refreshItems().catch(function () {});
                    }
                    return null;
                })
                .catch(function (err) {
                    // THE REFRESH IS THE FAILURE PATH NOW, and it has to be: the DOM was moved
                    // optimistically, so a refusal leaves the page showing an order the server does
                    // not have. Re-rendering is how it gets back to the truth.
                    setPositionsStatus('Not saved');
                    toastError(err, 'That section could not be moved.');
                    return refreshItems(true).catch(function () {});
                });
        });
    }

    function sectionMenuHtml(trigger) {
        var all = sectionTriggers();
        var at = all.indexOf(trigger);
        var rows = '';

        // RENDERED OR NOT, never rendered-and-refusing. `data-rename-url` is empty for a lapsed
        // member, because `rename_section` is the member gate and a row that exists to be refused is
        // the "remedy that refuses" shape this project has now fixed three times.
        if (trigger.dataset.renameUrl) {
            rows += menuRow('rename', 'Rename', ICON_PENCIL);
        }
        // Disabled at the ends rather than hidden, so the menu does not change height between the
        // first section and the middle ones -- a menu whose rows move as you go down the page is
        // harder to use than one with a greyed row in it.
        rows += menuRow('up', 'Move up', ICON_UP, at <= 0);
        rows += menuRow('down', 'Move down', ICON_DOWN, at < 0 || at >= all.length - 1);
        rows += '<div class="gl-menu__sep"></div>'
            + menuRow('delete', 'Delete section', ICON_X, false, 'gl-menu__item--danger');
        return rows;
    }

    var ICON_PENCIL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>';
    var ICON_UP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
    var ICON_DOWN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14M5 12l7 7 7-7"/></svg>';
    var ICON_X = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';

    function menuRow(action, label, icon, disabled, extra) {
        return '<button type="button" class="gl-menu__item' + (extra ? ' ' + extra : '') + '"'
            + ' data-gl-section-do="' + action + '"'
            + (disabled ? ' disabled' : '')
            + '>' + icon + PP.HTMLUtils.escape(label) + '</button>';
    }

    function wireSectionMenu() {
        if (sectionMenu || !PP.AnchoredMenu) { return; }
        sectionMenu = PP.AnchoredMenu({
            trigger: '[data-gl-section-menu]',
            className: 'gl-menu',
            label: 'Section actions',
            item: '.gl-menu__item',
            onOpen: function (trigger, el) {
                el.innerHTML = sectionMenuHtml(trigger);
                var first = el.querySelector('.gl-menu__item:not([disabled])');
                if (first) { first.focus(); }
            },
            onItem: function (row, trigger) {
                var action = row.getAttribute('data-gl-section-do');
                sectionMenu.close(false);
                if (action === 'rename') { onSectionRename(trigger); }
                else if (action === 'delete') { onSectionDelete(trigger); }
                else if (action === 'up') { moveSection(trigger, -1); }
                else if (action === 'down') { moveSection(trigger, 1); }
            },
        });
    }

    /* ─── the adder, relocated rather than duplicated ──────────────────────────────────────────── */

    /**
     * Bring the adder home, to the slot outside the swapped panel.
     *
     * MUST RUN BEFORE ANY SWAP OF `#gl-items-panel`. The section headers live inside it and are
     * replaced wholesale, so an adder parked in one is destroyed mid-type -- taking its listeners,
     * its WeakSet guard and any in-flight search with it, and leaving `wireAdder` to find nothing to
     * re-wire because the node it guarded no longer exists.
     *
     * Idempotent, so it is safe to call from both swap paths without either knowing about the other.
     */
    function parkAdder() {
        var adder = document.querySelector('[data-gl-adder]');
        var home = document.querySelector('[data-gl-adder-home]');
        if (!adder || !home || adder.parentElement === home) { return; }
        // REMEMBERED, so a refresh can put it back. Parking is unavoidable -- the header it is
        // docked under is about to be replaced -- but "unavoidable" is not the same as "the hunter
        // asked to stop adding to this section", and the two were indistinguishable until now.
        pendingDockSection = adder.dataset.section || '';
        home.appendChild(adder);
        delete adder.dataset.section;
        adder.classList.remove('gl-adder--docked');
    }

    /**
     * Put the adder back where a refresh took it from.
     *
     * THE MULTI-ADD SESSION IS THE WHOLE POINT of docking, and it worked exactly once. `GameAdder`
     * deliberately leaves its results open after an add so several games can go in from one search
     * -- and this page's `onAdded` returns `refreshItems()`, whose first act is `parkAdder()`. So the
     * first game was filed into the section, the adder was yanked back to the toolbar with its
     * `data-section` deleted, and the SECOND game from that same list of results went into the loose
     * bucket. Silently: no error, no toast, nothing said.
     *
     * Only while the mode is still on, and only if the header survived -- a section deleted during
     * the round trip has nowhere to go back to, and re-docking to a header that no longer exists
     * would put the adder in an arbitrary place pointing at a dead id.
     */
    function redockAdder() {
        var want = pendingDockSection;
        pendingDockSection = null;
        if (want === null || !editing) { return; }
        var trigger = document.querySelector(
            '#gl-items-root [data-gl-add-to-section][data-section-id="' + want + '"]');
        var head = trigger && trigger.closest('.gl-section__head');
        if (!head) { return; }
        dockAdderTo(head, want, { silent: true });
    }

    /**
     * Put the adder under one section's header and point it there.
     *
     * ONE INSTANCE MOVED, not one built per section. `GameAdder` binds a document listener and has
     * no teardown, so an adder per header on a panel that re-swaps on every write is a leak that
     * grows for the life of the tab. Moving the node keeps every one of those bindings.
     *
     * `data-section` is what `GameAdder` reads at SEND time to file the game on arrival -- see its
     * comment in utils.js for why it is read then rather than captured at wire time. This function
     * is that reason.
     */
    function dockAdderTo(head, sectionId) {
        var adder = document.querySelector('[data-gl-adder]');
        if (!adder || !head) { return; }
        head.insertAdjacentElement('afterend', adder);
        adder.dataset.section = sectionId || '';
        adder.classList.add('gl-adder--docked');
        var input = adder.querySelector('[data-gl-adder-input]');
        if (input) {
            input.value = '';
            input.focus();
        }
    }

    function onSectionAddGame(btn) {
        var head = btn.closest('.gl-section__head');
        if (!head) { return; }
        var adder = document.querySelector('[data-gl-adder]');
        // A SECOND PRESS ON THE SAME HEADER SENDS IT HOME, which every toggle on this page does.
        // Without it the only way back to adding loose games was a page load.
        if (adder && adder.previousElementSibling === head) { parkAdder(); return; }
        dockAdderTo(head, btn.dataset.sectionId);
        announce('Adding to ' + (head.querySelector('.gl-section__name') || {}).textContent + '.');
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
        // THE MODE'S STATE TOO. This file's header commits to honouring the `onPageReady` restore
        // contract even though the current htmx config never fires it -- and under that contract
        // these carried over: a restored page would paint "Done" on a toggle whose panel has no
        // `[data-gl-editing]`, show the controls strip over a closed panel, and keep a live Sortable
        // and a document keydown bound to a discarded grid.
        //
        // BOTH FLAGS, still. One mode to the hunter does not mean one variable: `arranging` is
        // derived, and a restore that reset only `editing` would leave a stale Sortable attached
        // with nothing able to detach it, which is the same leak by a shorter route.
        detachDrag();
        editing = false;
        arranging = false;

        // AND THE DOM, which the variables above do not reach. `data-gl-editing` and
        // `data-gl-arranging` are ATTRIBUTES on the panel, so they serialise into htmx's history
        // snapshot (`cloneNode(true)`) and come back set on a restored page -- where the CSS gates
        // read them, not the booleans. The old comment claimed resetting the booleans fixed the
        // restore case; it left grips, grab cursors, drop boxes and card menus over a page with no
        // editor, no Sortable and no keydown listener.
        //
        // Same for the controls strip's `hidden`, which `enterEditing` REMOVED, and for the adder,
        // which a restore would otherwise bring back docked inside the panel with its home slot
        // empty -- so `wireAdder` would bind a second `GameAdder`, leaking one per restore.
        var panel = document.getElementById('gl-items-panel');
        if (panel) {
            delete panel.dataset.glEditing;
            delete panel.dataset.glArranging;
        }
        var strip = document.querySelector('[data-gl-positions]');
        if (strip) { strip.hidden = true; }
        parkAdder();
        pendingDockSection = null;
        orderChain = Promise.resolve();
        pendingSaves = 0;
        pendingSectionFocus = false;
        pendingPickId = null;
        wireAdder();
        wireCardMenu();
        wireSectionMenu();
        wireIdentityEditor();
        wireVisibility();
        wireReport();
        wireEditToggle();
        wireSections();
        initReveal();
        if (PP.wireCharCounters) { PP.wireCharCounters(); }
        if (first) {
            document.body.addEventListener('click', onBodyClick);
            document.body.addEventListener('click', onGrabClick);
            // PARK THE ADDER BEFORE ANYTHING IS REPLACED. `refreshItems` calls `parkAdder` itself,
            // but the sort toolbar submits through htmx DIRECTLY (`hx-target="#gl-items-panel"`) and
            // never passes through it -- so sorting a list while the adder was docked under a
            // section header destroyed the adder, and the page came back with no way to add a game
            // until it was reloaded. Scoped to that one target, because this listener sees every
            // swap on the page.
            document.body.addEventListener('htmx:beforeSwap', function (e) {
                if (e.target && e.target.id === 'gl-items-panel') { parkAdder(); }
            });
            document.body.addEventListener('htmx:afterSwap', onAfterSwap);
            document.body.addEventListener('htmx:afterSettle', onAfterSettle);
        }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
