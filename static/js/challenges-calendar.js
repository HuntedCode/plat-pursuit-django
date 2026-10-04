/**
 * The Plat Calendar board's month switcher.
 *
 * THE LENS SWITCHER IS NOT HERE, AND THAT IS THE POINT. It used to be: the radios wrote `data-view` onto
 * the board and CSS tinted off that attribute. Which meant that with JavaScript off, clicking "Contracts"
 * visibly activated the Contracts chip -- `.pp-switch__chip:has(input:checked)` is pure CSS -- while all
 * 31 squares stayed tinted for the previous lens and the tally kept its old figure. False feedback on the
 * one dimension the board exists to express, which is worse than a control that does nothing.
 * `.pp-cal:has(input[value="..."]:checked)` moves the whole thing into CSS, so the lens works with no
 * script at all and there is no second mechanism to disagree with the first.
 *
 * WHAT IS LEFT IS THE MONTH TABLIST, and that goes through `PlatPursuit.wireTablist` rather than a
 * hand-rolled copy. The first version of this file reimplemented roving tabindex, Arrow/Home/End with
 * wrap and the focus/activate split by hand -- all of which the shared helper already does, and it is
 * explicitly "markup/class-agnostic -- pass the tab elements and a select callback". Using it also buys
 * the two companions a hand-rolled version silently skipped: `igniteTab`'s bloom on the chip that became
 * active, and `slideViewIn`'s directional panel slide, which every other switcher on the site has.
 *
 * STILL NO SCRIPT NEEDED FOR A CORRECT BOARD. The server marks the inactive eleven panels `hidden` and
 * checks the radio for the lens the run leads with -- so a hunter with no JavaScript gets January on the
 * right lens with the right squares tinted, and a fully working lens switcher. What they lose is reaching
 * the other eleven months, which is navigation rather than information: the crests are `<button>`s, so
 * nothing is a broken link, and the board below them is complete and true for the month it shows.
 *
 * KNOWN GAP, recorded rather than half-built: the chosen month and lens are not in the URL, so neither
 * survives a reload or a shared link. `PlatPursuit.syncViewParam` is the helper for it, but writing a
 * param the server does not read produces "a URL that lies about what is on screen" (its own words), so
 * honouring it means the view reading `?month=`/`?view=` too. That is a deliberate follow-up, not an
 * oversight.
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

    if (PP.onPageReady) { PP.onPageReady(boot); } else { document.addEventListener('DOMContentLoaded', boot); }
}());
