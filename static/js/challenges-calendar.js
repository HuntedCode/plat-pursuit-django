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

        // THE STRIP LOCAL IS BACK, AND NOW IT IS READ. It was removed once with a note saying nothing
        // needed it, which was true then: the only reader was a guard `tabs.length` already implied.
        // `centreTab` below is a real reader, twice over, and re-querying it per call would be the
        // version of this that looks tidy and is not.
        var strip = board.querySelector('.pp-cal__crests');
        var tabs = Array.prototype.slice.call(board.querySelectorAll('.pp-cal__crest'));
        var panels = board.querySelectorAll('.pp-cal__panel');
        if (!tabs.length || !panels.length) { return; }

        // THE SLUG ORDER, read off the TABS rather than hardcoded, so `slideViewIn` can tell a forward
        // month change from a backward one without this file carrying a second copy of the calendar.
        // (An earlier comment said "off the panels", which is not what the line below does.)
        var order = tabs.map(function (tab) { return tab.id.replace('cal-tab-', ''); });

        // THE LIVE CREST, by the mark the server rendered. Computed here because TWO things need it and
        // they must not disagree: the slide's starting point, and the strip's opening scroll position.
        var live = tabs.filter(function (tab) {
            return tab.getAttribute('aria-selected') === 'true';
        })[0];
        // THE SLIDE STARTS WHERE THE BOARD STARTS. This was `order[0]`, which is now the ALL tab and can
        // never be the live one -- so the first month switch always computed a FORWARD slide, whichever
        // direction it actually went. It was wrong eleven months in twelve when `order[0]` was January;
        // the overview made it wrong every time.
        var current = (live || tabs[0]).id.replace('cal-tab-', '');

        // BRING A CREST INTO VIEW BY SCROLLING THE STRIP, never by asking the element to scroll itself.
        // `scrollIntoView` walks EVERY scrollable ancestor, so `inline: 'center'` can pan the document
        // as well as the strip -- which this board has just finished proving is not hypothetical, since
        // a 620px strip in a 308px box had the page panning sideways on a phone. Writing `scrollLeft`
        // touches one box and cannot move anything else.
        // IT ALSO SIDESTEPS THE VERTICAL HAZARD the old call documented: `block: 'nearest'` was there to
        // stop the browser scrolling the document down to centre a 44px control. On BOOT that guard is
        // not enough -- a board below the fold is not "nearest", so the page would have jumped to it on
        // load, which is the one thing a page must not do while somebody is reading the top of it.
        // INSTANT, AND NOT BY ACCIDENT: `scroll-behavior: smooth` is declared on `html` and is not an
        // inherited property, so the strip's own scrolling is unanimated. The strip is where this writes.
        function centreTab(tab) {
            if (!strip || !tab) { return; }
            // NOTHING TO DO WHERE NOTHING SCROLLS. From `md:` the strip is `overflow-x: visible`, so this
            // is a no-op there rather than a silent write to a box with no overflow.
            if (strip.scrollWidth <= strip.clientWidth) { return; }
            // MEASURED OFF RECTS, NOT `offsetLeft`, which is relative to the nearest POSITIONED ancestor
            // -- a thing this strip does not have and could acquire from any rule above it.
            var box = strip.getBoundingClientRect();
            var coin = tab.getBoundingClientRect();
            strip.scrollLeft += (coin.left - box.left) - (box.width - coin.width) / 2;
        }

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

            // THE CREST ROW IS A HORIZONTAL SNAP STRIP AT MOBILE, so a switch to an off-screen month has
            // to bring its crest along. See `centreTab` for why that is a `scrollLeft` write rather than
            // the `scrollIntoView({ block: 'nearest', inline: 'center' })` this line used to be.
            centreTab(tab);
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

        // THE STRIP OPENS ON THE LIVE MONTH (owner, 2026-10-05: "the crest bar doesn't automatically
        // scroll to show the current month on the screen on load (say October, it is off-screen
        // initially)"). The board has opened on the current month since the server learned to render it,
        // but nothing ever moved the STRIP: `show` is the only thing that centres a crest and it runs on
        // interaction, so a hunter in October arrived at an October board above a row showing January.
        //
        // AFTER `wireTablist`, because the roving tabindex has to be settled before the strip moves --
        // but `live` itself is resolved once, up with `order`, so the slide's starting point and this
        // scroll cannot disagree about which crest is the live one.
        centreTab(live);

        // THE YEAR OVERVIEW'S ROWS JUMP TO THEIR MONTH (owner, 2026-10-05: "being able to click a box or
        // one of the rows to go to the proper tab would be really nice"). Delegated from the board, so one
        // listener serves twelve rows and the 365 cells inside them -- and because the cells are INSIDE
        // the rows, "click a box" and "click a row" are the same handler rather than two.
        //
        // RESOLVED BY ID, NOT BY INDEX. The row names its tab (`data-cal-jump="cal-tab-jul"`) and this
        // looks the element up, then asks the tab list where it sits. Reading the month number off the
        // row and using it as an index would work only while the overview is the first tab, which is
        // exactly the positional coupling that has already bitten this suite twice.
        //
        // IT GOES THROUGH `show` AND `syncTabindex`, the same pair `onSelect` uses, so a jump leaves the
        // board in precisely the state a crest click would: `aria-selected` rewritten, every panel's
        // `hidden` set from the resolved one, `slideViewIn` run, and the roving tabindex pointing at the
        // month that is now live. Calling `show` alone would switch the panel and leave the keyboard's
        // position on the overview.
        board.addEventListener('click', function (e) {
            if (!e.target.closest) { return; }
            var row = e.target.closest('[data-cal-jump]');
            if (!row) { return; }
            var tab = document.getElementById(row.getAttribute('data-cal-jump'));
            var index = tabs.indexOf(tab);
            if (index < 0) { return; }
            show(index);
            api.syncTabindex();
            // FOCUS FOLLOWS THE JUMP, so a hunter who clicks a row and then reaches for the arrow keys
            // is moving from the month they landed on rather than from wherever focus happened to be.
            // `wireTablist` has just made this the only crest with `tabIndex = 0`.
            //
            // `preventScroll` IS THE WHOLE POINT OF THIS LINE'S SECOND ARGUMENT. `focus()` scrolls the
            // element into view by default -- which is `scrollIntoView` by another name, the thing
            // `centreTab` above spends twenty lines refusing to use, and on this site it is worse than
            // the plain version: `html` carries BOTH `scroll-behavior: smooth` and a
            // `scroll-padding-top` for the sticky chrome, so it would be a smooth, chrome-offset
            // DOCUMENT scroll. Clicking a row at the bottom of the overview would switch the panel and
            // then slide the page up under the pointer. Keyboard users are unaffected either way: their
            // focus moves come from `wireTablist`'s own arrow handling, not from here.
            if (tab.focus) { tab.focus({ preventScroll: true }); }
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

        // THE MONTH TRAVELS TO THE SHELL, because the hue cannot travel the other way. `--cal-c` is set
        // by `data-month` on the fragment root, which sits INSIDE the dialog -- and custom properties
        // inherit downward only, so the dialog wrapping it can never read the fragment's hue. Copying
        // the attribute up is what lets the dialog itself wear the month's colour.
        function open(html, month) {
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
            if (month) { modal.setAttribute('data-month', month); }
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
                    open(html, cell.getAttribute('data-month'));
                })
                .catch(function () { /* offline or aborted: leave the board alone */ });
        });
    }

    // ── the side column's day peek ───────────────────────────────────────────────────────────────────
    //
    // Hovering a square that opens shows that day's figures where the month's normally sit. NO FETCH:
    // every figure is already on the square, which is why the per-day count is stored at all -- a hover
    // that asked the server would put a request behind every mouse movement across a 365-cell grid.
    //
    // SILENT TO SCREEN READERS, AND THEREFORE POINTER-ONLY. The peek is `aria-hidden` and nothing
    // announces it: a live region firing on every square a pointer crosses would be hostile, and the
    // modal is the accessible path to the same information.
    // IT MIRRORED FOCUS AT FIRST, on the reasoning this paragraph used to state as settled -- "a
    // keyboard reader has no hover, so the peek has to be reachable without a pointer". That was a net
    // loss, and the reasoning contained its own refutation: an `aria-hidden` subtree cannot be
    // "reachable" by a screen reader at all, so focusing a square announced nothing while removing the
    // month's figures from the accessibility tree for every one of a month's 28-31 day stops. The
    // listeners are gone and the keyboard path is the modal, which Enter opens.
    function wireDayPeek(first) {
        if (!first) { return; }

        var shown = null;      // the panel whose peek is currently up
        var settling = null;   // a restore waiting to happen, or null

        // HOW LONG THE MONTH'S FIGURES WAIT BEFORE COMING BACK. Long enough to cross a 3px gap at any
        // plausible pointer speed, short enough that leaving the grid feels immediate. It is a settle,
        // not a delay: nothing is waiting to APPEAR, only to return.
        var SETTLE_MS = 160;

        function faces(cell) {
            var panel = cell.closest('.pp-cal__panel');
            if (!panel) { return null; }
            var peek = panel.querySelector('[data-cal-peek]');
            var facts = panel.querySelector('[data-cal-facts]');
            return (peek && facts) ? { panel: panel, peek: peek, facts: facts } : null;
        }

        // A CLASS, NOT THE `hidden` ATTRIBUTE, and the reason is not a preference. The two faces share
        // one grid area so the column cannot resize under a moving cursor -- which requires the hidden
        // one to stay IN FLOW, and `hidden` is `display: none`. Overriding that from the stylesheet is
        // not possible: Tailwind's preflight ships
        // `[hidden]:where(:not([hidden=until-found])) { display: none !important }`, an important author
        // declaration, and no normal author rule beats one. The class has nothing to out-rank, and the
        // server renders the off state so the panel still opens on the month's figures with no script.
        var OFF = 'pp-cal__face--off';

        function restore() {
            // THE PENDING TIMER IS CANCELLED, NOT JUST FORGOTTEN. `restore` has direct callers now (the
            // month-switch tear-down below, and the cross-panel branch in `show`), and nulling `settling`
            // without clearing it left a live timer that `hold()` could no longer see -- so a crest click
            // inside the settle window, followed by a hover, would have the old timer tear down the new
            // peek. Cheap to clear, and the alternative is a race nobody would reproduce.
            if (settling !== null) { window.clearTimeout(settling); }
            settling = null;
            if (!shown) { return; }
            shown.peek.classList.add(OFF);
            shown.facts.classList.remove(OFF);
            shown = null;
        }

        // THE DEFERRED RESTORE, which is the whole fix for the flicker. Leaving a square schedules the
        // month's figures to come back; arriving at another square cancels that before it runs, so a
        // drag across a row is a sequence of day-to-day swaps rather than day, month, day, month.
        function settle() {
            if (settling !== null) { return; }
            settling = window.setTimeout(restore, SETTLE_MS);
        }

        function hold() {
            if (settling === null) { return; }
            window.clearTimeout(settling);
            settling = null;
        }

        function show(cell) {
            hold();
            var f = faces(cell);
            if (!f) { return; }
            // A SQUARE IN ANOTHER MONTH CANNOT LEAVE THE LAST PEEK UP. Switching months while a peek is
            // open would otherwise strand it on a panel the reader can no longer see.
            if (shown && shown.panel !== f.panel) { restore(); }

            var plats = parseInt(cell.getAttribute('data-peek-plats'), 10) || 0;
            var clean = cell.getAttribute('data-peek-clean') === '1';
            var on = cell.getAttribute('data-peek-on');

            // A NUMBER ONLY WHERE THIS BOARD HAS ONE. An openable square is not necessarily a FILLED
            // square -- `in_clean` implies `in_all` but not the reverse -- and an `in_all`-only square's
            // stored count is shovelware-inclusive, which this board excludes. The first version printed
            // it anyway with ", not counted" appended, so a hovered 3 March read "6 platinums, not
            // counted": a figure and its retraction on one line, attached to a square drawn empty.
            //
            // TWO QUESTIONS, NOT ONE, which the first attempt at the fix folded into a single flag. The
            // FIGURE needs both ("does this board recognise a count here" AND "is there one to print");
            // the NOTE needs only the first. Folded, a FILLED square whose count is stale at zero got the
            // shovelware note printed on it -- and `plat_count` is only written when a row is filled or
            // changes, so a stale zero on a drawn square is a state the data model permits. That is also
            // the second reason the figure is withheld rather than printed: "0 platinums" previews
            // nothing. An earlier version of this block stated that reason twice, in consecutive
            // paragraphs, and called the test `plats < 1` while the code read `plats > 0`.
            var counted = clean && plats > 0;
            f.peek.querySelector('[data-peek-head]').textContent = cell.getAttribute('data-peek-label');
            f.peek.querySelector('[data-peek-figure]').hidden = !counted;
            // CLEARED ON THE WAY OUT, NOT ONLY WRITTEN ON THE WAY IN. Skipping the writes left the
            // PREVIOUS square's figures in the DOM, which is invisible today only because the figure is
            // hidden with `display: none` -- and the stylesheet's own argument for the faces beside it is
            // that `display` is the wrong tool. If this line ever moves to `visibility`, hovering a
            // stacked 14 February and then an uncounted 3 March would print "4 platinums" above a note
            // saying nothing is counted: this fix's own bug, wearing another day's number.
            if (counted) {
                f.peek.querySelector('[data-peek-count]').textContent = String(plats);
                f.peek.querySelector('[data-peek-unit]').textContent =
                    plats === 1 ? 'platinum' : 'platinums';
            } else {
                f.peek.querySelector('[data-peek-count]').textContent = '';
                f.peek.querySelector('[data-peek-unit]').textContent = '';
            }
            // IT SAYS WHAT THE SQUARE IS, NOT WHY. An earlier version read "Shovelware only, so this
            // square stays open." and claimed to be exact, because `in_all` without `in_clean` does mean
            // every platinum that day was on a flagged game WHEN THE ROW WAS WRITTEN. Calendar fills are
            // deliberately monotone and `refresh_due_runs` documents the hole: `auto_flagged -> clean` is
            // a routine `update_shovelware` outcome, moves neither watermark, and so never makes a
            // dormant hunter due. That square's platinum can be clean now -- and the modal it opens
            // derives `clean` LIVE, so it would render the card with no shovelware flag while the peek
            // beside it insisted shovelware was the reason. Two surfaces on one square disagreeing is
            // the failure class `platinums_on_day` exists to prevent.
            // SO THE NOTE REPORTS THE STORED STATE, which is the thing the grid is drawn from and is
            // true whatever the catalogue has done since.
            f.peek.querySelector('[data-peek-note]').textContent = clean
                ? (on ? 'first on ' + on : '')
                : 'Nothing counted here yet.';

            f.facts.classList.add(OFF);
            f.peek.classList.remove(OFF);
            shown = f;
        }

        // DELEGATED AND CAPTURING, because `mouseenter`/`mouseleave` do not bubble. `mouseover` and
        // `mouseout` do, so one listener on the board serves all 365 squares rather than 730 listeners
        // -- and they fire on the way in and out of a cell's children too, which is why both handlers
        // resolve the square with `closest` and compare.
        document.body.addEventListener('mouseover', function (e) {
            if (!e.target.closest) { return; }
            var cell = e.target.closest('[data-peek-label]');
            if (cell) { show(cell); }
        });
        // A MONTH SWITCH TEARS THE PEEK DOWN, because not every switch comes from the pointer. Moving
        // months with the keyboard (or by a restored history state) while the pointer happens to be
        // parked on a day square fires no `mouseout`, so `shown` stayed on the departed panel with its
        // facts still hidden -- and switching back showed a stale day's figures. Pointer paths self-heal
        // within `SETTLE_MS`; this one had nothing to heal it.
        document.body.addEventListener('click', function (e) {
            if (!e.target.closest) { return; }
            // `[data-cal-jump]` TOO, which is the year overview's rows: clicking one switches the panel
            // out from under the pointer exactly as a crest does, so the same tear-down applies. Without
            // it the peek would stay up on the hidden overview and be waiting there on the way back.
            if (e.target.closest('.pp-cal__crest, [data-cal-jump]')) { restore(); }
        });
        document.body.addEventListener('keyup', function (e) {
            if (!e.target || !e.target.closest) { return; }
            if (e.target.closest('.pp-cal__crest')) { restore(); }
        });
        document.body.addEventListener('mouseout', function (e) {
            if (!e.target.closest) { return; }
            var cell = e.target.closest('[data-peek-label]');
            // LEAVING INTO A CHILD IS NOT LEAVING. `relatedTarget` is where the pointer went; if it is
            // still inside the same square, the peek stays.
            if (!cell) { return; }
            var to = e.relatedTarget;
            if (to && to.closest && to.closest('[data-peek-label]') === cell) { return; }
            settle();
        });

        // THE PEEK IS POINTER-ONLY, AND THAT IS THE FIX RATHER THAN THE SHORTCUT. It mirrored focus at
        // first, on the reasoning that a keyboard reader has no hover -- but the peek is
        // `aria-hidden="true"`, so focus-driven swapping announced NOTHING while removing the month's
        // figures from the accessibility tree, and a month has 28-31 consecutive day stops. Tabbing into
        // the grid emptied the one region that said how far the month had got, for the whole traversal,
        // in exchange for nothing a reader could hear.
        // THE KEYBOARD PATH IS THE MODAL, which Enter opens and which is a real dialog with real content.
        // A sighted keyboard user loses a preview; a screen-reader user keeps the figures. If the preview
        // is wanted on focus later it needs the peek to be announced rather than hidden, which is a
        // different design and not a listener.
    }

    if (PP.onPageReady) { PP.onPageReady(function (first) { boot(); wireDaySheet(first); wireDayPeek(first); }); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(); wireDaySheet(true); wireDayPeek(true); }); }
}());
