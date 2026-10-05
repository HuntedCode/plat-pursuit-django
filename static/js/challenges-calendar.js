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
    // A square with a platinum on it opens a list of them. Three shared primitives do the work:
    //
    //   `.pp-detail-modal`     the shell, already used by badge detail, game detail and the landing
    //   `PP.takeover`          scroll lock, page-recede, focus capture AND RESTORE, Tab trap, Escape
    //   `PP.dismissableSheet`  the touch drag-to-dismiss, which `takeover` does not cover
    //
    // DELIBERATELY NOT A FOURTH HAND-ROLL. `game-detail.js` and `badge-detail.js` both fetch a fragment
    // into this same shell and both hand-roll their own focus trap; the first one's comment calls itself
    // a "refactor candidate: hoist this + badge-detail's copy into a shared PlatPursuit util". The util
    // they wanted is `takeover`.
    //
    // AND REUSING A PRIMITIVE MEANS READING ITS CONTRACT, which the first version of this did not. Three
    // of its rules are load-bearing here and all three were missed:
    //
    //   IT REMOVES ITS ROOT on close, so the root has to be re-attached per open. `monthly-recap.js`
    //   appends its container to `document.body` on every `openStage()` for exactly this reason. The
    //   first version passed a server-rendered element and let it be deleted: the sheet worked once,
    //   then wrote into a detached node and put a scrim over a blank page with Tab dead and scroll
    //   locked, recoverable only by Escape.
    //
    //   IT SCALES `#page-recede`, and a `position: fixed` overlay inside a transformed ancestor resolves
    //   against that ancestor rather than the viewport. So the shell is rendered in the detail page's
    //   `fixed_overlays` block, outside the receding wrapper -- which is where every other
    //   `.pp-detail-modal` on a receding surface lives, and `badge_detail.html` says why. This file does
    //   not move it; it only has to survive `takeover` removing it.
    //
    //   ITS `focusSel` IS A PLAIN `querySelector().focus()`. The heading carries `tabindex="-1"` so it
    //   can actually take focus -- without that, focus stayed on the square behind the scrim and the Tab
    //   trap never matched, because the trap only acts when the active element is the dialog's first or
    //   last focusable.
    function wireDaySheet(first) {
        if (!first) { return; }

        var session = null;      // the live `takeover` handle, or null when closed
        var token = 0;           // which request the sheet is waiting for
        var held = null;         // the shell while it is detached -- see `shell()`

        function shell() {
            // RESOLVED AT EVENT TIME, AND THE DETACHED ONE IS KEPT. Two things make this more than a
            // lookup:
            //
            //   `takeover` REMOVES THE SHELL on close, so after the first close it is not in the
            //   document and `getElementById` returns null -- but it is still the same element, with its
            //   `dismissableSheet` listeners attached, so holding the handle means re-appending restores
            //   a working sheet instead of building a new one.
            //
            //   AN `htmx:historyRestore` REPLACES THE PAGE CONTENT, so a delegate bound once (correctly,
            //   under `first`) would otherwise keep writing into nodes the restore threw away -- the
            //   sheet went silently dead after a Back, with a 200 in the network tab and nothing on
            //   screen. A live element therefore always wins over the held one.
            var live = document.getElementById('cal-day-modal');
            if (live) { held = live; }
            return held;
        }

        function close() {
            if (session) { var s = session; session = null; s.close(); }
        }

        function open(html) {
            var modal = shell();
            if (!modal) { return; }
            var body = modal.querySelector('[data-day-body]');
            var dialog = modal.querySelector('.pp-detail-modal__dialog');
            if (!body || !dialog) { return; }

            // A SHEET ALREADY UP IS TORN DOWN FIRST, synchronously. Without this a second open overwrote
            // `session` and orphaned the first `takeover` -- leaking its capture-phase keydown listener
            // for the life of the page, and leaving `body.style.overflow` restored to the 'hidden' the
            // orphan had captured, so the page became permanently unscrollable.
            close();

            // RE-ATTACHED IF `takeover` TOOK IT. `document.body` is where `fixed_overlays` renders
            // anyway, so this puts it back outside `#page-recede` exactly as the template did.
            if (!modal.parentNode) { document.body.appendChild(modal); }
            body.innerHTML = html;          // REPLACE, never append: the fragment's id must stay unique
            modal.hidden = false;

            // `exitMs: 0` MAKES THE TEARDOWN SYNCHRONOUS, and that closes a real race rather than saving
            // a frame. `takeover` defers its teardown 240ms by default, and nothing here adds the
            // `.is-closing` class those 240ms are for -- so it was a dead frame in which the sheet was
            // still painted but had already released Escape, and a click in that window let the OLD
            // teardown remove the root and empty the body under the NEW sheet.
            session = PP.takeover(modal, {
                exitMs: 0,
                focusSel: '.pp-cday__date',
                onClose: function () {
                    session = null;
                    body.innerHTML = '';
                    modal.hidden = true;
                },
            });

            if (PP.dismissableSheet) {
                // RE-WIRED PER OPEN, because the first version's comment ("the element outlives every
                // open") was false -- `takeover` removes it. `dismissableSheet` has no teardown, so a
                // re-append would otherwise come back without its gesture. Re-arming a surviving element
                // double-binds the touch listeners, which is why `data-pp-drag` marks the ones already
                // wired.
                if (!dialog.hasAttribute('data-pp-drag')) {
                    dialog.setAttribute('data-pp-drag', '');
                    PP.dismissableSheet(dialog, {
                        scrim: modal.querySelector('.pp-detail-modal__scrim'),
                        onClose: close,
                    });
                }
            }
        }

        document.body.addEventListener('click', function (e) {
            if (!e.target.closest) { return; }

            if (e.target.closest('[data-day-close]')) { close(); return; }

            var cell = e.target.closest('[data-day-url]');
            if (!cell) { return; }

            // A COUNTER, NOT THE URL. The url guard this replaces could not tell two clicks on the SAME
            // square apart, which is the case the slice was written for -- squares are 44px and adjacent,
            // so a double-tap is ordinary -- and both responses passed it, opening twice and orphaning a
            // takeover. A token is per-REQUEST, so a repeat click supersedes its own earlier one.
            token += 1;
            var mine = token;
            fetch(cell.getAttribute('data-day-url'),
                  { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
                // `r.ok` FIRST, which the fragment's docstring asks for by name: a hidden run and a
                // missing square both answer 404, and this project installs a GET-only `handler404`, so
                // an unchecked `.text()` would inject the 404 PAGE into the sheet.
                .then(function (r) { return r.ok ? r.text() : null; })
                .then(function (html) {
                    if (mine !== token || html === null) { return; }
                    open(html);
                })
                .catch(function () { /* offline or aborted: leave the board alone */ });
        });
    }

    if (PP.onPageReady) { PP.onPageReady(function (first) { boot(); wireDaySheet(first); }); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(); wireDaySheet(true); }); }
}());
