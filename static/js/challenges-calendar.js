/**
 * The Plat Calendar board's month switcher.
 *
 * THERE WAS A LENS SWITCHER, AND NOW THERE IS ONE LENS. A day is filled by a shovelware-free platinum
 * (owner, 2026-10-04), so the three-way control is gone along with the `:has()` rules that drove the
 * tint off its checked radio. Nothing about it was ever in this file by the end -- an earlier version
 * DID write a `data-view` attribute here, and that was the defect: the chip's active state was pure CSS
 * while the tint needed the script, so with JavaScript off pressing a lens lit its chip and left every
 * square showing the previous one. Moving it into CSS fixed that, and the collapse then removed it.
 *
 * WHAT IS LEFT IS THE MONTH TABLIST, and that goes through `PlatPursuit.wireTablist` rather than a
 * hand-rolled copy. The first version of this file reimplemented roving tabindex, Arrow/Home/End with
 * wrap and the focus/activate split by hand -- all of which the shared helper already does, and it is
 * explicitly "markup/class-agnostic -- pass the tab elements and a select callback". Using it also buys
 * `slideViewIn`'s directional panel slide, which every other switcher on the site has. (Its other
 * companion, `igniteTab`, is deliberately NOT taken -- see the note at the call.)
 *
 * STILL NO SCRIPT NEEDED FOR A CORRECT BOARD. The server marks the inactive eleven panels `hidden`, so a
 * hunter with no JavaScript gets January with the right squares filled. What they lose is reaching the
 * other eleven months, which is navigation rather than information: the crests are `<button>`s, so
 * nothing is a broken link, and the board below them is complete and true for the month it shows.
 *
 * KNOWN GAP, recorded rather than half-built: the chosen month is not in the URL, so it does not
 * survive a reload or a shared link. `PlatPursuit.syncViewParam` is the helper for it, but writing a
 * param the server does not read produces "a URL that lies about what is on screen" (its own words), so
 * honouring it means the view reading `?month=` too. That is a deliberate follow-up, not an oversight.
 */
(function () {
    'use strict';

    var PP = window.PlatPursuit = window.PlatPursuit || {};

    function boot() {
        var board = document.querySelector('.pp-cal');
        if (!board) { return; }

        // NO `strip` LOCAL. One was read once, in a guard that `tabs.length` already implies -- the
        // crests live inside the strip, so there cannot be tabs without it. `wireTablist` binds to the
        // tabs themselves rather than delegating from the container, so nothing else needed it.
        var tabs = Array.prototype.slice.call(board.querySelectorAll('.pp-cal__crest'));
        var panels = board.querySelectorAll('.pp-cal__panel');
        if (!tabs.length || !panels.length) { return; }

        // THE SLUG ORDER, read off the TABS rather than hardcoded, so `slideViewIn` can tell a forward
        // month change from a backward one without this file carrying a second copy of the calendar.
        // (An earlier comment said "off the panels", which is not what the line below does.)
        var order = tabs.map(function (tab) { return tab.id.replace('cal-tab-', ''); });
        var current = order[0];

        function show(index) {
            var tab = tabs[index];
            if (!tab) { return; }
            // THE PANEL COMES FROM `aria-controls`, NOT FROM A MATCHING INDEX. Pairing the two lists by
            // position works only while both are rendered in the same order, and it fails SILENTLY when
            // they are not -- the script would show one month while the accessible tree named another.
            // Resolving through the attribute makes the relationship the markup already declares the one
            // thing that decides, so there is no second ordering to keep in step.
            var shown = document.getElementById(tab.getAttribute('aria-controls'));
            if (!shown) { return; }

            tabs.forEach(function (t) {
                t.setAttribute('aria-selected', t === tab ? 'true' : 'false');
            });
            // `hidden` RATHER THAN A CLASS, matching what the server rendered. One mechanism for "this
            // panel is not showing" means no state where an attribute and a class disagree, which is how
            // a panel ends up visible to a screen reader and not to an eye.
            panels.forEach(function (panel) { panel.hidden = panel !== shown; });

            var next = order[index];
            if (PP.slideViewIn) { PP.slideViewIn(shown, current, next, order); }
            current = next;

            // `block: 'nearest'` SO THE PAGE DOES NOT SCROLL. The crest row is a horizontal snap strip at
            // mobile, so an off-screen crest has to be brought along -- but the default would also scroll
            // the document vertically to centre a 44px control, which on a phone throws the board off
            // screen entirely.
            if (tab.scrollIntoView) { tab.scrollIntoView({ block: 'nearest', inline: 'center' }); }
        }

        if (!PP.wireTablist) { return; }
        // `api` IS REFERENCED INSIDE `onSelect` AND ASSIGNED BY THIS STATEMENT, which is safe because the
        // callback cannot fire until a hunter interacts -- long after the assignment completes. The
        // helper's own `syncTabindex` has to be called AFTER `show` rewrites `aria-selected`, since that
        // attribute is what `isActive` reads: without it the roving tabindex would keep pointing at the
        // month that was active before the switch.
        var api = PP.wireTablist(tabs, {
            // The server renders the active tab's `aria-selected`, so the roving tabindex reads the
            // attribute the markup already carries rather than a class this file would have to add.
            isActive: function (tab) { return tab.getAttribute('aria-selected') === 'true'; },
            // NO `ignite: true`, which is the one companion this switcher does not take. `ppTabIgnite`
            // animates a `box-shadow: 0 0 20px 3px` on the element it is given; every other consumer is
            // a `.pp-switch__chip` with a 5px radius, whereas a crest is a SQUARE 44px button wrapping a
            // ROUND coin -- so the bloom would be a square halo around a circle, and it reaches ~23px
            // beyond a box that sits in a clipped scroll strip behind a mask fade. The lift and the ring
            // are the activation cue instead. `slideViewIn` below is taken, because a panel can host it.
            onSelect: function (tab) {
                show(tabs.indexOf(tab));
                api.syncTabindex();
            },
        });
    }

    // ── the day sheet ────────────────────────────────────────────────────────────────────────────────
    //
    // A square with a platinum on it opens a list of them. Three shared primitives do the work and this
    // file supplies only the glue:
    //
    //   `.pp-detail-modal`     the shell, already used by badge detail, game detail and the landing
    //   `PP.takeover`          scroll lock, page-recede, focus capture AND RESTORE, Tab trap, Escape
    //   `PP.dismissableSheet`  the touch drag-to-dismiss, which `takeover` does not cover
    //
    // DELIBERATELY NOT A FOURTH HAND-ROLL. `game-detail.js` and `badge-detail.js` both fetch a fragment
    // into this same shell and both hand-roll their own focus trap; the first one's comment calls itself
    // a "refactor candidate: hoist this + badge-detail's copy into a shared PlatPursuit util". The util
    // it wanted is `takeover`, whose docstring says it exists "because three surfaces now do it and
    // every one re-implemented the accessibility parts slightly differently". Hoisting those two is a
    // `refactor/` branch; this one just declines to add to the pile.
    function wireDaySheet(first) {
        var modal = document.getElementById('cal-day-modal');
        var body = modal && modal.querySelector('[data-day-body]');
        var dialog = modal && modal.querySelector('.pp-detail-modal__dialog');
        if (!modal || !body || !dialog) { return; }

        var session = null;      // the live `takeover` handle, or null when closed
        var pending = null;      // the in-flight request's url, so a stale response cannot win

        function close() {
            if (session) { var s = session; session = null; s.close(); }
        }

        function finish() {
            // THE BODY IS EMPTIED ON CLOSE, which is what keeps the fragment's one requirement true:
            // the next open REPLACES rather than appends, so `id="cal-day-title"` is never duplicated.
            body.innerHTML = '';
            modal.hidden = true;
            session = null;
        }

        function open(html, opener) {
            body.innerHTML = html;          // replace, never append
            modal.hidden = false;
            // `takeover` captures the active element itself and restores it on close, so the opener does
            // not have to be tracked here -- it is passed only as the thing to focus FIRST, because the
            // dialog's own close button is a poor landing spot for a reader who just asked a question.
            session = PP.takeover(dialog, {
                focusSel: '.pp-cday__date',
                onClose: finish,
            });
            if (opener && !session) { opener.focus(); }
        }

        // `first` GUARDS THE BODY LISTENER, per `onPageReady`'s contract: element wiring re-runs on an
        // HTMX history restore and `document.body` survives it, so an unguarded delegate would bind
        // twice and fetch twice per click.
        if (first) {
            document.body.addEventListener('click', function (e) {
                if (!e.target.closest) { return; }

                if (e.target.closest('[data-day-close]')) { close(); return; }

                var cell = e.target.closest('[data-day-url]');
                if (!cell) { return; }
                var url = cell.getAttribute('data-day-url');
                pending = url;
                fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
                    // `r.ok` FIRST, AND THE FRAGMENT'S DOCSTRING ASKS FOR THIS BY NAME. A hidden run and
                    // a missing square both answer 404, and this project installs a GET-only
                    // `handler404` -- so without the check the 404 PAGE would be injected into the sheet.
                    .then(function (r) { return r.ok ? r.text() : null; })
                    .then(function (html) {
                        // A SECOND CLICK WINS. Squares are small and close together, so two in quick
                        // succession is ordinary; without this the slower response could land after the
                        // faster one and the sheet would show the wrong day.
                        if (pending !== url) { return; }
                        if (html === null) { return; }
                        open(html, cell);
                    })
                    .catch(function () { /* offline or aborted: leave the board alone */ });
            });
        }

        // THE DRAG GESTURE IS WIRED ONCE PER DIALOG ELEMENT, not per open: `dismissableSheet` adds
        // listeners to the element it is given, and the element outlives every open.
        if (PP.dismissableSheet) {
            PP.dismissableSheet(dialog, {
                scrim: modal.querySelector('.pp-detail-modal__scrim'),
                onClose: close,
            });
        }
    }

    if (PP.onPageReady) { PP.onPageReady(function (first) { boot(); wireDaySheet(first); }); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(); wireDaySheet(true); }); }
}());
