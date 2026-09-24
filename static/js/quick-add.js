/**
 * Quick-add: putting a game on a list from wherever you happen to be looking at it.
 *
 * ONE POPOVER, NOT ONE PER CARD. A browse grid renders up to 24 cards and a hunter holds up to 25
 * lists; inlining a picker per card would be 600 rows of markup on a page where at most one is ever
 * opened. A single detached element is moved to whichever trigger was pressed and filled from one
 * request, which is also what the system this replaces did.
 *
 * DELEGATED FROM `document.body`, because every grid this appears on is htmx-swappable -- Browse
 * Games swaps on every filter change, and per-element wiring would silently stop working after the
 * first one. The trigger carries only a concept id and a title; everything else arrives when the
 * popover opens, so a grid stays exactly as cheap to render as it was before this existed.
 *
 * WHAT IT DELIBERATELY DOES NOT DO: show, on the card, whether a game is already on one of your
 * lists. That state would have to be computed for every concept on every grid -- four browse pages,
 * one of them the site's main catalogue -- and the whale rule is unambiguous about adding a per-user
 * query to a page that size. The button is an ACTION, not a status; the popover is where membership
 * is read, and it reads it for one game at a time.
 */
(function () {
    'use strict';

    var PP = window.PlatPursuit || {};
    // `AnchoredMenu` as well as `API`. This file gained a cross-file dependency when the panel was
    // extracted, and a browser holding a cached pre-extraction `utils.js` against a fresh
    // `quick-add.js` would throw at the constructor call -- killing the whole IIFE and taking the
    // "Create list" submit handler with it, not just the popover. `list-detail.js` guards both of
    // its menus this way; this file was the one left asymmetric.
    if (!PP.API || !PP.AnchoredMenu) { return; }

    //: The panel, its anchoring, its focus restore, its flip-and-clamp positioning, and the four
    //: document listeners that used to live in this file are now `PlatPursuit.AnchoredMenu`
    //: (utils.js). They were extracted in 2026-09 when the Game Lists editor needed two more menus
    //: of exactly this shape; every hard-won case in them was found HERE, and the comments went with
    //: the code. What stays is what is actually about adding a game to a list.
    var menu = null;

    /* ------------------------------------------------------------------ helpers ---- */

    function esc(text) {
        return PP.HTMLUtils ? PP.HTMLUtils.escape(text) : String(text == null ? '' : text);
    }

    // `escape` deliberately leaves quotes alone, which is right in a text node and wrong inside an
    // attribute -- the exact split `escapeAttr` documents itself as existing for. Both values that
    // use this are server-built routes today, so this is correctness rather than a live hole.
    function attr(text) {
        if (PP.HTMLUtils && PP.HTMLUtils.escapeAttr) { return PP.HTMLUtils.escapeAttr(text); }
        return esc(text);
    }

    function toast(message, kind) {
        if (!PP.ToastManager) { return; }
        if (kind === 'error') { PP.ToastManager.error(message); }
        else { PP.ToastManager.success(message); }
    }

    /**
     * Report a failure to the console with its context.
     *
     * A bare `.catch(function () {})` is what this project's own list controller was corrected for:
     * it turns a broken endpoint into a control that silently does nothing, which is the hardest
     * shape of bug to be told about.
     */
    function logFailure(what, err) {
        if (window.console && console.error) { console.error('[quick-add] ' + what, err); }
    }

    // The server's own refusal, or null. The body of this lived here, as its comment predicted it
    // shouldn't: it is now PlatPursuit.API.failureMessage in utils.js, shared with every other
    // controller that was hand-rolling the same six lines.
    //
    // Feature-tested, not called straight. Both call sites are inside `.catch` handlers, and the guard
    // at the top of this file passes on a CACHED pre-extraction `utils.js` (it has `postFormData`, so
    // the happy path works) -- a bare call would then throw "failureMessage is not a function" from
    // inside a rejection handler, losing the error toast entirely. The body being self-contained was
    // the one thing this file had that the extraction took away; this puts it back.
    function failureMessage(err) {
        return PP.API.failureMessage ? PP.API.failureMessage(err) : Promise.resolve(null);
    }

    /* --------------------------------------------------------------- the popover ---- */

    /* ----------------------------------------------------------------- rendering ---- */

    var ICON_ON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';
    var ICON_OFF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
    var ICON_FULL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="11" width="16" height="9" rx="1.6"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';

    function rowHtml(row) {
        // A FULL list renders disabled rather than absent: "no room" and "not a list" are different
        // answers, and a picker that hides the first is lying about where a game can go.
        if (row.is_full && !row.has_concept) {
            // A REAL BUTTON, `aria-disabled` rather than `disabled`, so it stays in the tab order and
            // is announced as an option that exists and cannot be used. As a role-less `<span>` it
            // was neither: a keyboard user never reached it, and a screen reader read "Backlog Full"
            // as loose text indistinguishable from the heading above -- which defeats the entire
            // reason for rendering a full list instead of hiding it.
            return '<button type="button" class="qa-pop__row qa-pop__row--full" aria-disabled="true">'
                + '<span class="qa-pop__icon">' + ICON_FULL + '</span>'
                + '<span class="qa-pop__name">' + esc(row.name) + '</span>'
                + '<span class="qa-pop__tag">Full</span></button>';
        }
        var on = !!row.has_concept;
        return '<button type="button" class="qa-pop__row' + (on ? ' is-on' : '') + '"'
            + ' data-row'
            + ' data-add-url="' + attr(row.add_url) + '"'
            + (row.remove_url ? ' data-remove-url="' + attr(row.remove_url) + '"' : '')
            + ' aria-pressed="' + (on ? 'true' : 'false') + '">'
            + '<span class="qa-pop__icon">' + (on ? ICON_ON : ICON_OFF) + '</span>'
            + '<span class="qa-pop__name">' + esc(row.name) + '</span>'
            + '<span class="qa-pop__count">' + row.game_count + '</span>'
            + '</button>';
    }

    function render(el, data, gameName) {
        var html = '<p class="qa-pop__head">Add <strong>' + esc(gameName) + '</strong> to</p>';

        if (data.lists.length) {
            html += '<div class="qa-pop__rows">' + data.lists.map(rowHtml).join('') + '</div>';
        } else {
            html += '<p class="qa-pop__empty">You have no lists yet. Name one and this game starts it.</p>';
        }

        if (data.can_create) {
            // `data.name_max_length`, not a literal: the ceiling is a model constant whose own
            // comment records having been written three times before a counter showed the number to
            // a hunter. This field was very nearly the fourth.
            html += '<form class="qa-pop__new" data-new>'
                + '<label class="sr-only" for="qa-new-name">New list name</label>'
                + '<input type="text" id="qa-new-name" name="name" class="qa-pop__input"'
                + ' maxlength="' + (data.name_max_length || 60) + '"'
                + ' autocomplete="off" placeholder="New list…" />'
                + '<button type="submit" class="qa-pop__go">Create</button>'
                + '</form>';
        } else {
            // The OTHER cap, said plainly. Offering "New list" to somebody already holding their
            // maximum is the remedy-that-refuses shape this project has now fixed twice.
            html += '<p class="qa-pop__capped">That is all ' + data.max_lists
                + ' of your lists. Remove one, or <a href="' + attr(data.support_url)
                + '">see membership</a>.</p>';
        }

        el.innerHTML = html;
    }

    /* ------------------------------------------------------------------- opening ---- */

    //: THE CONSUMER'S HALF. Everything about WHERE the panel goes, how it closes, whose focus it
    //: restores and what a scroll does to it belongs to `AnchoredMenu`; what is left here is loading
    //: this hunter's lists and rendering them.
    menu = PP.AnchoredMenu({
        trigger: '[data-quick-add]',
        className: 'qa-pop',
        label: 'Add this game to a list',
        // THE ROUTES COME FROM THE SERVER, on the trigger. They were string literals in this file,
        // on the same feature whose endpoint comment argues that hand-assembling a path is what
        // breaks silently the day one moves. A trigger without one simply does not open.
        canOpen: function (trigger) { return !!trigger.dataset.listsUrl; },
        item: '[data-row]',
        onItem: function (row) { onRow(row); },
        onOpen: function (trigger, el, seq) {
            el.innerHTML = '<p class="qa-pop__loading">Loading your lists…</p>';

            PP.API.get(trigger.dataset.listsUrl)
                .then(function (data) {
                    if (menu.stale(seq)) { return; }    // closed, or another trigger won
                    render(el, data, trigger.dataset.gameName || 'this game');
                    menu.reposition();                  // the panel just changed height
                    var first = el.querySelector('[data-row], input, a');
                    if (first) { first.focus(); }
                })
                .catch(function (err) {
                    if (menu.stale(seq)) { return; }
                    logFailure('loading the lists for a game', err);
                    el.innerHTML = '<p class="qa-pop__loading">Your lists could not be loaded.</p>';
                });
        },
    });
    if (!menu) { return; }

    /* --------------------------------------------------------------------- acting ---- */

    function onRow(row) {
        // BELT AND BRACES, and honestly labelled as such: a full row renders WITHOUT `data-row`, and
        // the delegated handler only arrives here through `closest('[data-row]')`, so today this
        // cannot fire. It stays because `aria-disabled` keeps a row focusable -- that is the trade for
        // being announced as an option that exists and cannot be used -- so the day somebody adds
        // `data-row` to the full branch to make it keyboard-reachable, the refusal is already here.
        // (An earlier version of this comment claimed the guard was load-bearing. It was not.)
        if (row.getAttribute('aria-disabled') === 'true') { return; }
        if (row.dataset.busy) { return; }
        var on = row.getAttribute('aria-pressed') === 'true';
        var url = on ? row.dataset.removeUrl : row.dataset.addUrl;
        if (!url) { return; }

        row.dataset.busy = '1';
        var body = new FormData();
        // `menu.current()` is the open panel's trigger -- the card this row is acting for. It was a
        // module-level `openTrigger`; the primitive owns that state now and hands it back.
        var anchor = menu.current();
        if (!on) { body.append('concept_id', anchor ? anchor.dataset.conceptId : ''); }

        PP.API.postFormData(url, body)
            .then(function (data) {
                // REPAINTED IN PLACE rather than re-fetching the popover: the only things this can
                // change are one row's state and one count, and a refetch would close and reopen the
                // thing under the hunter's cursor.
                var count = row.querySelector('.qa-pop__count');
                if (count && data && typeof data.game_count === 'number') {
                    count.textContent = data.game_count;
                }
                row.setAttribute('aria-pressed', on ? 'false' : 'true');
                row.classList.toggle('is-on', !on);
                var icon = row.querySelector('.qa-pop__icon');
                if (icon) { icon.innerHTML = on ? ICON_OFF : ICON_ON; }
                // The remove URL only exists once there is an item to remove, so it arrives with the
                // add and goes with the removal.
                if (on) { delete row.dataset.removeUrl; }
                else if (data && data.remove_url) { row.dataset.removeUrl = data.remove_url; }
                toast(on ? 'Removed from that list.' : 'Added to that list.');
            })
            .catch(function (err) {
                logFailure('toggling a game on a list', err);
                failureMessage(err).then(function (message) {
                    toast(message || 'That could not be saved.', 'error');
                });
            })
            .finally(function () { row.dataset.busy = ''; });
    }

    function onNew(form) {
        if (form.dataset.busy) { return; }
        var input = form.querySelector('input[name="name"]');
        var name = input ? input.value.trim() : '';
        if (!name) { if (input) { input.focus(); } return; }
        var anchor = menu.current();
        if (!anchor) { return; }

        form.dataset.busy = '1';
        var body = new FormData();
        body.append('name', name);

        PP.API.postFormData(anchor.dataset.createUrl, body)
            .then(function (data) {
                toast('Added to "' + (data && data.name ? data.name : name) + '".');
                menu.close(true);
            })
            .catch(function (err) {
                logFailure('creating a list from a game', err);
                failureMessage(err).then(function (message) {
                    toast(message || 'That list could not be created.', 'error');
                });
            })
            .finally(function () { form.dataset.busy = ''; });
    }

    /* -------------------------------------------------------------------- wiring ---- */

    //: ALL THAT IS LEFT. The trigger click, the row click, the outside click, Escape, the
    //: scroll-follow-then-close, the resize reposition and the htmx-detach close were four document
    //: listeners and two window listeners in this file; `AnchoredMenu` binds one set of them for
    //: every menu on the page instead of one set per menu, which is what made extracting it worth
    //: doing rather than merely tidy.
    //:
    //: The form submit stays because it is not menu behaviour: it is this feature's second write.
    document.body.addEventListener('submit', function (e) {
        var form = e.target.closest && e.target.closest('[data-new]');
        if (!form) { return; }
        e.preventDefault();
        onNew(form);
    });
})();
