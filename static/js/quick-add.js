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
    if (!PP.API) { return; }

    //: The live popover, its trigger, and the abort handle for an in-flight open. One of each,
    //: because only one popover is ever open.
    var pop = null;
    var openTrigger = null;
    var openSeq = 0;

    var EDGE = 8;          // keep this much clear of the viewport on every side

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

    /**
     * The server's own refusal, or null.
     *
     * `PP.API` throws an Error whose `.response` is the raw `fetch` Response, NOT a parsed body --
     * so `err.response.error` is always `undefined`, and reading it threw away every message the
     * server took trouble to write. A hunter tapping a list holding 200 games was told "That could
     * not be saved" instead of "A list holds 200 games. Remove one to make room, or start another
     * list." -- a sentence the service runs an extra query to get right.
     *
     * This is the THIRD copy of this shape (`list-detail.js` has it, the legacy controller had it).
     * Extracting it to `utils.js` is the right follow-up; doing it here first, correctly, beats a
     * fourth wrong one.
     */
    function failureMessage(err) {
        if (!err || !err.response || typeof err.response.json !== 'function') {
            return Promise.resolve(null);
        }
        return err.response.json()
            .then(function (data) { return (data && data.error) || null; })
            .catch(function () { return null; });
    }

    /* --------------------------------------------------------------- the popover ---- */

    function build() {
        var el = document.createElement('div');
        el.className = 'qa-pop';
        el.setAttribute('role', 'dialog');
        el.setAttribute('aria-label', 'Add this game to a list');
        el.hidden = true;
        document.body.appendChild(el);
        return el;
    }

    function ensure() {
        if (!pop) { pop = build(); }
        return pop;
    }

    /**
     * Put the popover beside its trigger, flipping at the viewport edges.
     *
     * `position: fixed` and viewport coordinates rather than absolute-inside-the-card: the trigger
     * sits in a grid cell with `overflow` ancestors and a transform-capable page wrapper, and an
     * absolutely-positioned panel would be clipped by the first of those it met. Fixed also means a
     * scroll closes it rather than dragging it out of alignment (see `onScroll`).
     */
    function place(trigger) {
        var el = ensure();
        var rect = trigger.getBoundingClientRect();
        el.hidden = false;

        // WHICH WAY, decided from the room rather than from a measurement taken under the wrong cap.
        var below = window.innerHeight - rect.bottom - EDGE - 6;
        var above = rect.top - EDGE - 6;
        var goUp = above > below;
        var room = Math.max(0, goUp ? above : below);

        // THE CAP GOES ON BEFORE THE HEIGHT IS READ. It was the other way round: `maxHeight` was
        // cleared, `offsetHeight` measured against the stylesheet's 21rem, `top` computed from that,
        // and only THEN was the cap raised to the real room -- so with more than about six lists the
        // panel grew downward from a `top` that assumed it was short, covering its own trigger and
        // running off the bottom of the screen.
        el.style.maxHeight = room + 'px';

        var width = el.offsetWidth;
        var height = el.offsetHeight;      // now bounded by the room, so `top` can trust it

        // No 140px floor. It was there to keep the panel usable in a cramped viewport and did the
        // opposite: on a short screen it forced a height larger than the room just measured, which
        // is the same overflow by a different route. A genuinely tiny gap gets a tiny scroller.
        var top = goUp ? Math.max(EDGE, rect.top - height - 6) : rect.bottom + 6;

        var left = rect.right - width;
        left = Math.max(EDGE, Math.min(left, window.innerWidth - width - EDGE));

        el.style.top = Math.round(top) + 'px';
        el.style.left = Math.round(left) + 'px';
    }

    function close(restoreFocus) {
        if (!pop) { return; }
        // WAS FOCUS IN HERE? Asked rather than passed in. Every path except Escape said "no", so a
        // keyboard user who scrolled, resized, or clicked away had the focused row deleted out from
        // under them and focus reset to <body> -- the next Tab restarting at the top of the document.
        // Whether focus needs restoring is a fact about the DOM, not a decision for the caller.
        var held = pop.contains(document.activeElement);
        pop.hidden = true;
        pop.innerHTML = '';
        if (openTrigger) {
            openTrigger.setAttribute('aria-expanded', 'false');
            // `isConnected`: an htmx swap can replace the grid under an open popover, and focusing a
            // detached node silently drops focus to <body> instead.
            if ((restoreFocus || held) && openTrigger.isConnected) { openTrigger.focus(); }
        }
        openTrigger = null;
        openSeq += 1;          // stands down any response still in flight
    }

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

    function render(data, gameName) {
        var el = ensure();
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

    function open(trigger) {
        // THE ROUTES COME FROM THE SERVER, on the trigger. They were string literals here, on the
        // same feature whose endpoint comment argues that hand-assembling a path is what breaks
        // silently the day one moves.
        if (!trigger.dataset.listsUrl) { return; }

        // A second press on the same trigger closes, which is what every menu on the site does.
        if (openTrigger === trigger) { close(true); return; }

        close(false);
        openTrigger = trigger;
        trigger.setAttribute('aria-expanded', 'true');

        var mine = ++openSeq;
        var el = ensure();
        el.innerHTML = '<p class="qa-pop__loading">Loading your lists…</p>';
        place(trigger);

        PP.API.get(trigger.dataset.listsUrl)
            .then(function (data) {
                if (mine !== openSeq) { return; }       // closed, or another trigger won
                render(data, trigger.dataset.gameName || 'this game');
                place(trigger);
                var first = el.querySelector('[data-row], input, a');
                if (first) { first.focus(); }
            })
            .catch(function (err) {
                if (mine !== openSeq) { return; }
                logFailure('loading the lists for a game', err);
                el.innerHTML = '<p class="qa-pop__loading">Your lists could not be loaded.</p>';
            });
    }

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
        if (!on) { body.append('concept_id', openTrigger ? openTrigger.dataset.conceptId : ''); }

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
        if (!openTrigger) { return; }

        form.dataset.busy = '1';
        var body = new FormData();
        body.append('name', name);

        PP.API.postFormData(openTrigger.dataset.createUrl, body)
            .then(function (data) {
                toast('Added to "' + (data && data.name ? data.name : name) + '".');
                close(true);
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

    document.body.addEventListener('click', function (e) {
        var trigger = e.target.closest && e.target.closest('[data-quick-add]');
        if (trigger) {
            // NO `stopPropagation`. It was here from when the button lived inside the card's <a>;
            // the wrapper made it a sibling, so nothing needs suppressing -- and stopping the click
            // one node below `document` is where the site's other outside-click closers listen, so
            // opening this left the nav search, the sub-nav menu and Browse Games' own discipline
            // popovers hanging open behind it. `preventDefault` goes too: a `type="button"` outside
            // a form and outside the link has nothing to prevent.
            open(trigger);
            return;
        }

        var row = e.target.closest && e.target.closest('[data-row]');
        if (row) { onRow(row); return; }

        // Anywhere else closes, which is the behaviour every menu on this site has.
        if (pop && !pop.hidden && !(e.target.closest && e.target.closest('.qa-pop'))) {
            close(false);
        }
    });

    document.body.addEventListener('submit', function (e) {
        var form = e.target.closest && e.target.closest('[data-new]');
        if (!form) { return; }
        e.preventDefault();
        onNew(form);
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && pop && !pop.hidden) { close(true); }
    });

    // A FIXED panel does not travel with the page, so a page scroll would leave it pointing at
    // nothing. Closing is the honest answer and the one a hunter expects from a menu. The panel's own
    // inner scrolling does not reach here: `overscroll-behavior: contain` stops it chaining to the
    // document, which otherwise closed the popover mid-flick for anybody with enough lists to scroll.
    //
    // ...BUT THE TRIGGER LEAVING IS THE TEST, not the scroll itself. The iOS keyboard scrolls the
    // document on its own: the panel focuses its New list field on open for a hunter with no lists --
    // precisely who the "Name one and this game starts it" copy is for -- the keyboard raises, the
    // page scrolls to lift the field clear of it, and a handler that closes on any scroll closed the
    // panel on the frame it appeared. Every time. The feature was unreachable for a first-run hunter
    // on an iPhone, which is the same keyboard the `resize` handler below already had to learn about.
    //
    // ASKING WHETHER THE FOCUS IS OURS DOES NOT WORK, and it is worth saying why, because it is the
    // obvious fix: `open()` focuses the panel on EVERY open, mouse included, so that test is true for
    // every loaded popover and scroll-to-close simply stops existing -- the panel then rides a
    // mouse-wheel scroll all the way off its card, and `place()` has no clamp in that direction, so it
    // ends up hanging over the site header still posting for a game nobody can see.
    //
    // What actually distinguishes the two cases is whether the CARD is still on screen. A keyboard
    // raise nudges it; scrolling away takes it out of the viewport entirely. So: follow while it is
    // visible, close once it is not. `place()` forces two reflows, so this rides a frame.
    var scrollPending = false;
    function onScrollSettled() {
        scrollPending = false;
        if (!pop || pop.hidden) { return; }
        // Gone from the DOM entirely, the branch `resize` has always had: an anchor that no longer
        // exists cannot be followed, and a panel left floating against a detached node is the state
        // the htmx closer below exists to prevent.
        if (!openTrigger || !openTrigger.isConnected) { close(false); return; }
        var r = openTrigger.getBoundingClientRect();
        var vh = window.innerHeight || document.documentElement.clientHeight;
        if (r.bottom <= 0 || r.top >= vh) { close(false); return; }
        place(openTrigger);
    }
    window.addEventListener('scroll', function () {
        if (!pop || pop.hidden || scrollPending) { return; }
        scrollPending = true;
        if (window.requestAnimationFrame) { requestAnimationFrame(onScrollSettled); }
        else { onScrollSettled(); }
    }, { passive: true });

    // REPOSITIONED, NOT CLOSED. Android fires `resize` when the virtual keyboard opens, and the
    // popover focuses its "New list" field on open for a hunter with no lists yet -- so closing on
    // resize meant the panel could vanish on the very frame it appeared, and vanish again every time
    // somebody tapped the field. A resize is a reason to move, not to give up.
    window.addEventListener('resize', function () {
        if (!pop || pop.hidden) { return; }
        if (openTrigger && openTrigger.isConnected) { place(openTrigger); }
        else { close(false); }
    });

    // AN HTMX SWAP REPLACES THE GRID under an open popover: Browse Games swaps on every filter
    // change. The panel would go on floating over the new results, anchored to a button that is no
    // longer in the document -- and a row click still posted, filing a game the hunter could no
    // longer see.
    document.body.addEventListener('htmx:afterSwap', function () {
        if (pop && !pop.hidden && (!openTrigger || !openTrigger.isConnected)) { close(false); }
    });
})();
