/**
 * Challenge detail: the picker that fills a square.
 *
 * THREE MODES, ONE DIALOG. Pressing an empty or unfinished square opens it SLOT-FIRST (what fits here?);
 * typing in the search box switches it CONTRACT-FIRST (where does this game go?); and the history door
 * opens it on the first-run importer, spanning every open letter at once. They are one sheet because they
 * answer the same question from different directions, and because the catch-up warning, the confirmation
 * and the refusal handling would otherwise be written three times.
 *
 * NOTHING HERE DECIDES ANYTHING. The panels come from the server, which owns eligibility, the hatch
 * threshold, the importer's date rule and the import-vs-hatch label. The confirmation is the same: the
 * server answers 409 and this offers the dialog. A stale panel can therefore only ever produce a refusal,
 * never a bad write -- which is the property that lets this file be optimistic about nothing.
 *
 * THE CLOSE ROUTINE IS THE SECOND COPY OF THE MATURE VERSION, not the fourth, and the difference matters
 * because the miscount WAS the justification. The first version of this header said `gamelists.js`,
 * `game-flag.js` and `list-detail.js` "each carry it" and priced extraction as deleting three equal
 * implementations. The second version corrected that by enumerating what each sibling lacked -- and got
 * THAT wrong too, in the other direction.
 *
 * So, without a count: `list-detail.js` is the only file with all six of these guards (verified). The other
 * two carry some and not others. The conclusion that survives all three attempts is the one that never
 * depended on the tally -- extraction here is a forward port, not a demolition, and this project's
 * convention (`GameAdder`, `AnchoredMenu`) is to extract when a SECOND caller appears, which is now.
 *
 * It is still duplicated here, and that is a scope call rather than a principled one: extracting properly
 * means migrating `list-detail.js`'s shipped dialog in the same change, which is a `refactor/` branch and
 * not a picker. The six subtleties below are ported faithfully and each is named. What has changed is that
 * the reason given is true.
 */
(function () {
    'use strict';

    var PP = window.PlatPursuit || {};

    //: Set just before the run-finished reload and consumed by `intro`, so the entrance does not replay
    //: the celebration from zero. Per-tab by design: a different tab's visit should still animate.
    var INTRO_DONE = 'pp-challenge-intro-done';

    /** This run's Horizon bar, and the scope is the whole point.
     *
     *  `.pp-horizon` is a shared primitive (`components/horizon.html`) and this page renders two: the nav's
     *  hidden sync bar sits earlier in the document, so a bare `document.querySelector('.pp-horizon')` found
     *  that one. Every write then set `--horizon-progress` on a hidden element in the chrome while the run's
     *  bar sat still until a reload. The same shape of bug as binding the board's clicks to `.pp-csq-grid`
     *  and reaching only the first of five shelves.
     */
    function runHorizon() {
        return document.querySelector('[data-cpick-horizon] .pp-horizon');
    }

    /** The page's entrance: the tally counts up and the bar fills from nothing.
     *
     *  FOR EVERY VIEWER, which is why this runs before `boot`'s dialog guard and why the script is no longer
     *  gated on `can_edit`. Somebody reading another hunter's finished run should see it arrive.
     *
     *  BOTH REUSE WHAT THE SITE ALREADY HAS: `PlatPursuit.countUp` (the shared utility, which honours
     *  reduced-motion itself and reads its target from `data-countup`) and the fill-from-0 move that
     *  `franchise-detail.js` and `company-detail.js` make at load -- set the property to 0, wait for a paint,
     *  then set the served value and let the primitive's own `transition: width 0.35s` carry it.
     *
     *  DOUBLE rAF, NOT A FORCED REFLOW, and the difference is the whole reason the bar did not move. The
     *  first version used `void hz.offsetWidth`, which is the form `utils.js` uses -- but that one fires on a
     *  REVEAL, on an element that has already painted at its served value, so there is a committed "from"
     *  state to transition out of. At load there is none: setting 0 and the target inside one frame, before
     *  the element has ever painted, gives the browser a single computed value and nothing to animate.
     *  `franchise-detail.js` says so in its own comment ("double-rAF so the 0% width lands before the
     *  transition to the real value") and it is the load-time precedent this should have copied. Same idea,
     *  wrong context -- the tally ticked and the bar sat still, which is exactly what the owner saw.
     *
     *  UNDER REDUCED MOTION THE BAR IS LEFT ALONE ENTIRELY, and that is the half it would be easy to drop.
     *  `horizon.css` disables the fill's transition under `reduce`, so setting 0 and then the target would
     *  not animate -- but nor is it harmless to write 0 at all, because the served state IS the final state
     *  and there is nothing to restore. `utils.js`'s own reveal says the same thing in the same words.
     */
    function intro() {
        // SKIPPED ONCE, straight after a run finishes: `applySlot` reloads the page at that moment and the
        // entrance would otherwise reset the finished tally to 0 and count it up a second time. Consumed on
        // read, so the next ordinary visit animates normally. Every access is guarded -- storage throws in a
        // private window and can come back empty in previews.
        try {
            if (window.sessionStorage.getItem(INTRO_DONE)) {
                window.sessionStorage.removeItem(INTRO_DONE);
                return;
            }
        } catch (e) { /* no storage: fall through and animate, which is the harmless direction */ }

        var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        var tally = document.querySelector('[data-cpick-tally]');
        if (tally && PP.countUp) { PP.countUp(tally, 900); }
        if (reduce) { return; }
        var hz = runHorizon();
        if (!hz) { return; }
        var target = hz.style.getPropertyValue('--horizon-progress');
        if (!target) { return; }
        hz.style.setProperty('--horizon-progress', '0%');
        requestAnimationFrame(function () {
            requestAnimationFrame(function () { hz.style.setProperty('--horizon-progress', target); });
        });
    }

    function boot(first) {
        // EVERY VIEWER, before the guard below sends a visitor home -- but ONCE.
        //
        // `onPageReady` calls its function as `fn(true)` on load and `fn(false)` on every
        // `htmx:historyRestore`, and both precedents for this entrance gate it on that flag
        // (`franchise-detail.js`, `company-detail.js` are `function boot(first) { if (first) {...} }`).
        // Not reachable today -- `base.html` sets `htmx.config.historyCacheSize = 0` with
        // `refreshOnHistoryMiss`, so a back-nav is a full load and no restore fires with a live DOM -- but
        // the failure if that config ever changes is not a replayed animation, it is a PERMANENT one: a
        // restore replays `innerHTML` captured at push time, and a snapshot taken inside the two-frame
        // window below would bake `--horizon-progress: 0%` into the markup, making the served target `0%`
        // for good.
        if (first) { intro(); }

        var dialog = document.getElementById('cpick');
        // THE BOARD, not a grid. A jobs run draws one grid per discipline, so `querySelector` on
        // `.pp-csq-grid` would have bound the click delegation to the FIRST shelf and left the other
        // four dead -- and `labelFor`/`applySlot` would have searched only that shelf for a square.
        var grid = document.querySelector('.pp-csq-board');
        // NO DIALOG MEANS NO OWNER. The template renders it only for a hunter who can change this run, so a
        // visitor's page has no picker to wire and this exits before touching anything.
        if (!dialog || !grid || !dialog.showModal || !PP.API) { return; }

        var els = {
            title: dialog.querySelector('[data-cpick-title]'),
            sub: dialog.querySelector('[data-cpick-sub]'),
            q: dialog.querySelector('[data-cpick-q]'),
            status: dialog.querySelector('[data-cpick-status]'),
            rows: dialog.querySelector('[data-cpick-rows]'),
            catchup: dialog.querySelector('[data-cpick-catchup]'),
            catchupTitle: dialog.querySelector('[data-cpick-catchup-title]'),
            catchupNote: dialog.querySelector('[data-cpick-catchup-note]'),
            catchupRows: dialog.querySelector('[data-cpick-catchup-rows]'),
            current: dialog.querySelector('[data-cpick-current]'),
            clear: dialog.querySelector('[data-cpick-clear]'),
            foot: dialog.querySelector('[data-cpick-foot]'),
            ask: dialog.querySelector('[data-cpick-ask]'),
            askText: dialog.querySelector('[data-cpick-ask-text]'),
            askCost: dialog.querySelector('[data-cpick-ask-cost]'),
            askKeep: dialog.querySelector('[data-cpick-ask-keep]'),
            askGo: dialog.querySelector('[data-cpick-ask-go]'),
            note: dialog.querySelector('[data-cpick-note]'),
            noteLead: dialog.querySelector('[data-cpick-note-lead]'),
            noteFacts: dialog.querySelector('[data-cpick-note-facts]'),
            histSwitch: dialog.querySelector('[data-cpick-history-switch]'),
            // Outside the dialog: the page's own counters, which every write moves.
            tally: document.querySelector('[data-cpick-tally]'),
            horizon: runHorizon(),
        };

        var challengeId = grid.getAttribute('data-challenge-id');

        //: WHICH OF THE THREE PANELS IS ON SCREEN. `load()` used to encode this in its own argument -- a key
        //: meant the square's pool, a null key meant the search -- which worked while there were two. The
        //: history panel is reachable from two places and has to survive a keystroke in the search box, so the
        //: mode is a thing the sheet knows rather than a shape of the last call.
        //:
        //: SET WHEN A PANEL ARRIVES, never when one is requested, and the difference is a real bug rather than
        //: a preference. The loaders used to assign it before the fetch, so a request that FAILED left `mode`
        //: describing a panel that never rendered -- and the next keystroke then filtered a pool that was not
        //: on screen. Exactly the hazard the identity guard in each loader exists to prevent, reintroduced one
        //: level up. Each renderer owns it now, so it can only ever describe what a reader is looking at.
        var mode = 'slot';

        //: The card the foot is currently asking about, and what to run if the answer is yes.
        //: Held here rather than on the element because the callback is a closure over the row.
        var footAnchor = null;
        var footGo = null;

        //: How to dismiss the open INLINE prompt, published by `askInRow` so the `cancel` handler can answer
        //: Escape without knowing anything about that prompt's internals. Null when none is open.
        var openPromptClose = null;
        // THE OPEN SLOT, and the guard for every async reply. A reply is applied only if the panel is still
        // showing the slot it was asked about -- keyed on IDENTITY, not on nullness, because a hunter who
        // closes one square and opens another mid-request would otherwise see the first square's pool under
        // the second square's title.
        var openKey = null;
        var requestSeq = 0;
        // One write at a time. See `assign`.
        var writing = false;
        // Set the moment a swipe commits, because `dismissableSheet` signals a dismissal in no
        // other way the page can see. Cleared on the next open.
        var dismissed = false;

        // ── the choreographed close ───────────────────────────────────────────────────────────────
        // A QUEUE, not a single callback. The early return for "a close is already running" is the one exit
        // that does not reach the animation's own `done()`, so a callback handed to it would be dropped --
        // press an offer, then Escape while the request is in flight, and the toast telling you it worked
        // never fires. Queue first, return second.
        var pendingAfter = [];
        var closeTimer = null;

        function close(after) {
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
            if (dialog.classList.contains('is-closing')) { return; }
            dialog.classList.add('is-closing');
            var done = function () {
                // CLEARED, or a stale fallback from a previous close fires inside a later one -- cutting
                // that exit short and draining a queue that is no longer its own.
                if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
                dialog.classList.remove('is-closing');
                dialog.close();
                drain();
            };
            // TARGET-GUARDED AND `pseudoElement`-GUARDED. `animationend` bubbles, so any descendant
            // animation finishing mid-exit would end the close early -- and the `::backdrop`'s own
            // `cpickScrimOut` fires on THIS element with `pseudoElement` set, so checking the target alone
            // is not enough.
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

        // ── rendering ─────────────────────────────────────────────────────────────────────────────

        function say(message) {
            if (!els.status) { return; }
            els.status.textContent = message || '';
            els.status.classList.remove('pp-cpick__status--error');
        }

        /** A refusal, toned so it does not read as "Loading...", and routed somewhere visible. */
        function fail(message) {
            if (!stillOpen()) { reportAway(message); return; }
            if (!els.status) { return; }
            els.status.textContent = message;
            els.status.classList.add('pp-cpick__status--error');
        }

        function fail_from(err, fallback) {
            if (PP.API && PP.API.failureOr) {
                PP.API.failureOr(err, fallback).then(fail).catch(function () { fail(fallback); });
                return;
            }
            fail(fallback);
        }

        function art(row) {
            if (!row.cover) {
                var blank = document.createElement('span');
                blank.className = 'pp-cpick__row-art';
                blank.setAttribute('aria-hidden', 'true');
                return blank;
            }
            var img = document.createElement('img');
            img.className = 'pp-cpick__row-art';
            img.loading = 'lazy';
            img.decoding = 'async';
            img.alt = '';
            img.src = row.cover;
            return img;
        }

        function offerButton(row, label, onPick) {
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'pp-cpick__row';
            button.appendChild(art(row));
            var name = document.createElement('span');
            name.className = 'pp-cpick__row-name';
            // `textContent`, never `innerHTML`: a contract's name is catalogue data and passes through
            // staff hands, but an escaping bug here would be an XSS on every hunter who opened a picker.
            name.textContent = row.name;
            button.appendChild(name);
            if (label) {
                // A NODE OR A STRING. The catch-up rows pass a sentence; the history rows pass a built element,
                // because three facts with three different weights are not a sentence.
                if (label.nodeType) {
                    button.appendChild(label);
                } else {
                    var note = document.createElement('span');
                    note.className = 'pp-cpick__row-note';
                    note.textContent = label;
                    button.appendChild(note);
                }
            }
            button.addEventListener('click', function () { onPick(row, button); });
            // WRAPPED IN AN `<li>`, because both row containers are `<ul>`s and a `<button>` is not valid
            // as their direct child -- the parser tolerates it and assistive tech stops counting the list.
            var item = document.createElement('li');
            item.appendChild(button);
            return item;
        }

        function renderSlotPanel(panel) {
            // Any open prompt belonged to the panel being replaced.
            dropPrompts();
            mode = 'slot';
            leaveHistory();
            els.title.textContent = panel.label;
            els.sub.textContent = panel.total === panel.showing
                ? panel.total + (panel.total === 1 ? ' game fits' : ' games fit')
                : 'showing ' + panel.showing + ' of ' + panel.total;

            els.rows.textContent = '';
            els.rows.classList.remove('pp-cpick__rows--search');
            panel.rows.forEach(function (row) {
                els.rows.appendChild(offerButton(row, null, function (picked, button) {
                    assign(picked.slug, panel.key, false, button);
                }));
            });

            // THE COUNT GOES THROUGH THE LIVE REGION. It used to go only to `els.sub`, which is not one, so
            // "12 games fit" was never announced and a screen-reader user got silence on every successful
            // load. The subtitle keeps it visually; `say` is what makes it audible.
            if (!panel.rows.length) {
                say(panel.query ? 'Nothing matches that here.' : 'No games left for this square.');
            } else {
                // NAMED, not just counted. `aria-labelledby` points at the title, but nothing announces
                // that element changing -- so the dialog's announced name stayed "Choose a game" and a
                // screen-reader user never learned which square they had opened.
                say(panel.label + ': ' + els.sub.textContent);
            }

            renderCatchup(panel);

            els.current.textContent = panel.slot_is_filled
                ? 'Currently: ' + panel.current_name
                : '';
            els.clear.hidden = !panel.slot_is_filled;
        }

        function renderCatchup(panel) {
            els.catchupRows.textContent = '';
            if (!panel.catchup || !panel.catchup.length) {
                els.catchup.hidden = true;
                return;
            }
            els.catchup.hidden = false;
            // ONE WARNING ABOVE ALL OF THEM, rather than repeated per row. Every offer in this block lands
            // the square complete and locked, so the cost is a property of the block.
            els.catchupTitle.textContent = 'These finish the square straight away';
            els.catchupNote.textContent =
                'You have already completed these, so the square will be done and can never be changed. '
                + 'We will ask you to confirm.';
            panel.catchup.forEach(function (row) {
                // `absolute` with an explicit options bag -- `TimeFormatter` has `relative`, `absolute`
                // and `countdown`, and no `shortDate` (I invented that one). `relative` is wrong here
                // anyway: "2 years ago" is the wrong register for a date a hunter is being asked to
                // confirm, where the actual month is the thing that makes it recognisable.
                var when = row.completed_at && PP.TimeFormatter && PP.TimeFormatter.absolute
                    ? PP.TimeFormatter.absolute(row.completed_at,
                                                { year: 'numeric', month: 'short', day: 'numeric' })
                    : null;
                var label = row.via === 'import'
                    ? (when ? 'From your history, ' + when : 'From your history')
                    : 'Supply is thin here';
                els.catchupRows.appendChild(offerButton(row, label, function (picked, button) {
                    assign(picked.slug, panel.key, false, button);
                }));
            });
        }

        /** Undress the history mode. Both other renderers call it, so the note, the placeholder and the
         *  toggle's pressed state cannot survive into a panel they do not describe. */
        function leaveHistory() {
            showNote('');
            if (els.histSwitch) {
                els.histSwitch.setAttribute('aria-pressed', 'false');
                // Unhidden again: it is only withheld while history mode has nowhere to go back to.
                els.histSwitch.hidden = false;
            }
            if (els.q) { els.q.placeholder = 'Search for a game'; }
        }

        function renderSearchPanel(panel) {
            mode = 'search';
            dropPrompts();
            els.title.textContent = 'Search';
            els.sub.textContent = panel.too_short
                ? 'Type at least two letters'
                : (panel.total === panel.showing
                    ? panel.total + (panel.total === 1 ? ' match' : ' matches')
                    : 'showing ' + panel.showing + ' of ' + panel.total);
            els.catchup.hidden = true;
            els.current.textContent = '';
            els.clear.hidden = true;
            els.rows.textContent = '';
            els.rows.classList.add('pp-cpick__rows--search');
            leaveHistory();

            if (panel.too_short) { say('Type at least two letters.'); return; }
            if (!panel.rows.length) { say('No games match that.'); return; }
            say(els.sub.textContent);

            panel.rows.forEach(function (row) {
                var card = document.createElement('li');
                var block = document.createElement('div');
                // `--static`: this card is NOT the button. The squares it could fill are, inside it -- so
                // the card must not carry a pointer cursor or a hover lift promising a press that does
                // nothing.
                block.className = 'pp-cpick__row pp-cpick__row--static';
                block.appendChild(art(row));
                // A COLUMN BESIDE THE COVER, because a search result is a row rather than a card: it carries
                // a variable number of actions (one per square the game fits, up to six) and they need width
                // to sit at a real size.
                var main = document.createElement('div');
                main.className = 'pp-cpick__row-main';
                // THE TITLE AND ITS STATE SHARE A LINE, the chip at the end of it. Stacked underneath, the
                // chip read as a second fact about the row rather than as part of its heading, and cost a
                // line of height on every finished result.
                var head = document.createElement('span');
                head.className = 'pp-cpick__row-head';
                var name = document.createElement('span');
                name.className = 'pp-cpick__row-name';
                name.textContent = row.name;
                head.appendChild(name);
                // ALREADY FINISHED, marked. The server has been sending this all along -- one indexed query
                // over the page, via `completed_contract_ids` -- and only one narrow branch read it, so a
                // search result gave no hint that placing it would complete the square on the spot. The chip
                // is the house primitive, not DaisyUI's badge.
                if (row.is_completed_by_you) { head.appendChild(chip('Finished', 'success')); }
                main.appendChild(head);
                block.appendChild(main);

                if (row.already_in_run) {
                    main.appendChild(note('Already in this run'));
                } else if (!row.keys.length) {
                    // EXPLAINS ITSELF rather than rendering without a button. "No square open" and "this
                    // game fits nothing" look identical to a hunter unless one of them says so.
                    main.appendChild(note(row.is_completed_by_you
                        ? 'No open square for this'
                        : 'Nowhere to put this yet'));
                } else {
                    // ONE BUTTON PER SQUARE IT FITS. A jobs game routinely fits several, and choosing the
                    // game is not the same decision as choosing the slot.
                    //
                    // BUT A-Z ONLY EVER FITS ONE, and a lone pill reading "S" looked like a choice among
                    // options that do not exist. So a single square gets the sentence spelled out and the
                    // several-squares case gets a lead-in, which is also the honest difference between the
                    // two run types: in A-Z the letter is a fact about the game, and in Job Coverage it is a
                    // decision.
                    var single = row.keys.length === 1;
                    // A FINISHED GAME IS NOT PLACEABLE FROM HERE. The ordinary path refuses it outright, and
                    // the rules that DO lift it are offered inside the square's own panel, in the warning
                    // block that explains the square will lock: the hatch on either challenge type, and the
                    // first-run history importer on A-Z only. This said "the two rules" unconditionally,
                    // which is one rule too many on a Job Coverage run.
                    // So the squares are shown and disabled: the game is still worth finding, and the chip on
                    // the title says why nothing can be done with it.
                    //
                    // Deliberately WITHOUT copy promising the exception. Whether a rule lifts it is per-slot
                    // and per-hunter (`catchup_offers`), and answering that for every search result would be
                    // a query each -- so a note saying "open the square to use it" would be a promise this
                    // panel cannot keep.
                    var finished = !!row.is_completed_by_you;
                    if (!single) { main.appendChild(lead('Add this game to:')); }
                    var keys = document.createElement('div');
                    keys.className = 'pp-cpick__keys';
                    row.keys.forEach(function (key) {
                        // OFF THE PANEL, not the row: what a square is called and what it wears belong
                        // to the run, so they arrive once rather than repeated on every result.
                        var keyLabel = (panel.key_labels || {})[key] || key;
                        var occupant = (panel.filled || {})[key];
                        var pick = document.createElement('button');
                        pick.type = 'button';
                        pick.disabled = finished;
                        pick.className = 'pp-cpick__key' + (single ? ' pp-cpick__key--sentence' : '');
                        // THE JOB'S OWN GLYPH AND COLOUR, so the button offering the Slayer square looks
                        // like the Slayer square. A-Z keys have no atom and get neither -- a letter has no
                        // icon and no discipline.
                        var atom = (panel.key_atoms || {})[key];
                        if (atom) {
                            pick.classList.add('pp-cpick__key--job');
                            pick.style.setProperty(
                                '--disc', 'var(--disc-' + atom.disc_slug + ', var(--pp-primary))');
                        }
                        // THE SENTENCE EITHER WAY. A disabled button that describes the action it would
                        // perform is clearer than one showing a bare letter -- the DISABLED STATE is what
                        // says "not possible", so the words do not have to, and stripping them left a lone
                        // "S" that read like the pill this change existed to get rid of.
                        pick.textContent = single ? 'Add this game to ' + keyLabel : keyLabel;
                        // AFTER the text, never before: assigning `textContent` removes every child, so an
                        // icon appended above this line would be silently discarded.
                        if (atom && atom.icon) { pick.insertBefore(jobIcon(atom.icon), pick.firstChild); }
                        // AN OCCUPIED SQUARE SAYS SO BEFORE IT IS PRESSED. Picking a game for a square that
                        // already holds one silently replaced it -- easy to do by accident, since the search
                        // panel says nothing about the rest of the run.
                        if (occupant && !finished) {
                            pick.classList.add('pp-cpick__key--taken');
                            pick.appendChild(swap(' (replaces ' + occupant + ')'));
                        }
                        // THE NAME CARRIES THE GAME, because the pill alone names only the square and the
                        // game sits in a sibling associated with nothing. The visible text is contained in
                        // the accessible name, so the two do not disagree for voice control.
                        pick.setAttribute('aria-label', pick.textContent + ' \u2014 ' + row.name);
                        pick.addEventListener('click', function () {
                            if (!occupant) { assign(row.slug, key, false, pick); return; }
                            ask(pick,
                                'Put ' + row.name + ' in ' + keyLabel + '?',
                                keyLabel + ' already has ' + occupant + ', and it would be replaced.',
                                'Replace it',
                                'Keep ' + occupant,
                                function () { assign(row.slug, key, false, pick); });
                        });
                        keys.appendChild(pick);
                    });
                    main.appendChild(keys);
                }
                card.appendChild(block);
                els.rows.appendChild(card);
            });
        }

        /** Drop any open prompt, in BOTH surfaces. A re-render replaces the rows one was anchored to, and
         *  the foot's bar would otherwise survive into a panel about a different square -- still holding the
         *  callback for the old one, which is the worst version of this bug.
         *
         *  `li.` IS JUST PRECISION, not protection, and the comment here used to claim otherwise: that a bare
         *  `.pp-cpick__ask` sweep "would have torn the foot's markup out of the template". It would not. A
         *  class selector matches whole tokens, and the foot's elements are `pp-cpick__footask`,
         *  `pp-cpick__ask-text` and `pp-cpick__ask-row` -- none of which IS the token `pp-cpick__ask`. The
         *  qualifier says "the inline prompt is an `<li>`", which is true and worth saying; it was not
         *  rescuing anything. */
        function dropPrompts() {
            Array.prototype.forEach.call(
                dialog.querySelectorAll('li.pp-cpick__ask'),
                function (el) { if (el.parentNode) { el.parentNode.removeChild(el); } });
            // Or Escape would call a closer whose prompt is already detached, and `say('Nothing changed.')`
            // would answer a question nobody is being asked.
            openPromptClose = null;
            closeFootAsk(false);
        }

        function note(text) {
            var span = document.createElement('span');
            span.className = 'pp-cpick__row-note';
            span.textContent = text;
            return span;
        }

        /** A house chip (`components/chips.css`), never DaisyUI's `.badge` -- they tint from different tokens
         *  and read as two different greens side by side. */
        function chip(text, tone) {
            var span = document.createElement('span');
            span.className = 'bd-chip bd-chip--' + tone + ' pp-cpick__row-chip';
            span.textContent = text;
            return span;
        }

        /** A job's glyph, referenced out of the sprite this page already emitted.
         *
         *  `<use href="#jobicon-NAME">` is exactly what the server-side `job_icon_use` produces, so one
         *  sprite serves the grid and the picker alike and no path data is duplicated into JavaScript.
         *  `challenge_detail.html` emits `job_icon_sprite` for a jobs run, and an A-Z run never reaches here
         *  because its keys carry no atom. The name is validated server-side (`_key_look`), so this is never
         *  asked for a glyph the sprite lacks.
         *
         *  `createElementNS` rather than `innerHTML`: an SVG element built through the HTML parser lands in
         *  the wrong namespace and draws nothing.
         */
        function jobIcon(name) {
            var NS = 'http://www.w3.org/2000/svg';
            var svg = document.createElementNS(NS, 'svg');
            // The presentation attributes `job_icon_use` sets; the symbol carries geometry only and these
            // cascade into it.
            svg.setAttribute('viewBox', '0 0 24 24');
            svg.setAttribute('fill', 'none');
            svg.setAttribute('stroke', 'currentColor');
            svg.setAttribute('stroke-width', '2');
            svg.setAttribute('stroke-linecap', 'round');
            svg.setAttribute('stroke-linejoin', 'round');
            svg.setAttribute('aria-hidden', 'true');
            var use = document.createElementNS(NS, 'use');
            use.setAttribute('href', '#jobicon-' + name);
            svg.appendChild(use);
            return svg;
        }

        /** A history offer's label: three facts with three different weights, not one run-on line.
         *
         *  It was `'Goes in A \u00b7 finished Mar 3, 2024 \u00b7 replaces Alan Wake'` in a single dim
         *  `.pp-cpick__row-note`, which wrapped to three or four lines in a ~128px column, made every card a
         *  different height, and buried the destructive fact at the end in the quietest type on the card.
         *
         *  So: the destination is a badge (the thing being scanned for), the date is a quiet line, and the
         *  replacement is a warning chip -- because it is the only one of the three that costs anything.
         */
        function historyLabel(row, when) {
            var wrap = document.createElement('span');
            wrap.className = 'pp-cpick__dest';

            // `keyChip`, not `badge`, and the reason is narrower than the first version of this comment
            // claimed. It said "this file bans the word" -- it does not: a pin asserts `'badge' not in JS_CODE`,
            // and `JS_CODE` has the comments stripped, so the word is fine in prose (it appears in the
            // docstring seven lines up) and only a `badge` in CODE trips it. The pin exists because the house
            // status pill is `.bd-chip` and DaisyUI's `.badge` tints from different tokens; a local variable
            // must not be what weakens it. This element is a bespoke chip, not `.bd-chip`, so it is not what
            // the pin is about either way -- the rename is to keep the pin working, nothing more.
            var keyChip = document.createElement('span');
            keyChip.className = 'pp-cpick__dest-key';
            keyChip.textContent = row.key_label;
            wrap.appendChild(keyChip);

            if (when) {
                var date = document.createElement('span');
                date.className = 'pp-cpick__dest-when';
                date.textContent = when;
                wrap.appendChild(date);
            }
            if (row.occupant) {
                var swapChip = document.createElement('span');
                swapChip.className = 'pp-cpick__dest-swap';
                // `title` for a POINTER hover, and that is the whole claim. An earlier version added "and to
                // AT", which is wrong by mechanism: `title` on a non-focusable `<span>` is not announced by
                // NVDA or JAWS and is unreachable by touch. The full name does reach assistive tech, but
                // through `textContent` -- CSS truncation shortens the rendering, never the text -- so it is
                // already in the button's accessible name. On a touch device the full name is genuinely
                // unavailable until the confirmation names it, which it now does.
                swapChip.title = 'Replaces ' + row.occupant;
                swapChip.textContent = 'replaces ' + row.occupant;
                wrap.appendChild(swapChip);
            }
            return wrap;
        }

        /** The lead-in above several square buttons, so the pills read as answers to a question. */
        function lead(text) {
            var span = document.createElement('span');
            span.className = 'pp-cpick__lead';
            span.textContent = text;
            return span;
        }

        /** The "(replaces X)" half of an occupied square's button, quieter than the square's own name. */
        function swap(text) {
            var span = document.createElement('span');
            span.className = 'pp-cpick__key-swap';
            span.textContent = text;
            return span;
        }

        // ── fetching ──────────────────────────────────────────────────────────────────────────────

        function load(key, query) {
            var seq = ++requestSeq;
            var url = key
                ? '/my-challenges/' + challengeId + '/slot/' + encodeURIComponent(key) + '/'
                  + (query ? '?q=' + encodeURIComponent(query) : '')
                : '/my-challenges/' + challengeId + '/search/?q=' + encodeURIComponent(query || '');
            say('Loading...');
            PP.API.request(url).then(function (panel) {
                // STALE REPLY GUARD, on identity. Two panels can be in flight when somebody types quickly or
                // opens a second square, and applying the older one would show the wrong pool under the
                // right title.
                if (seq !== requestSeq) { return; }
                if (key) { renderSlotPanel(panel); } else { renderSearchPanel(panel); }
            }).catch(function (err) {
                if (seq !== requestSeq) { return; }
                fail_from(err, 'That did not load. Try again.');
            });
        }


        function loadHistory(query) {
            var seq = ++requestSeq;
            say('Loading...');
            PP.API.request('/my-challenges/' + challengeId + '/history/?q='
                           + encodeURIComponent(query || '')).then(function (panel) {
                // THE SAME IDENTITY GUARD the other loads use. Two panels can be in flight when somebody
                // types quickly or toggles the mode mid-request, and applying the older one shows the wrong
                // pool under the right title.
                if (seq !== requestSeq) { return; }
                renderHistoryPanel(panel);
            }).catch(function (err) {
                if (seq !== requestSeq) { return; }
                fail_from(err, 'That did not load. Try again.');
            });
        }

        /** What the closed importer says, in the hunter's terms rather than the flag's. */
        function historyClosedNote(reason) {
            if (reason === 'jobs') {
                return 'The history importer is for the A-Z Challenge. On a Job Coverage run, a game you have '
                    + 'already finished can only fill a square when very few games are left for that job.';
            }
            if (reason === 'spent') {
                return 'The importer is a one-time head start for your FIRST A-Z Challenge, and you have '
                    + 'already finished one -- so this run is played from here.';
            }
            // A REASON THIS BUILD DOES NOT KNOW. Naming a cause here would be inventing one -- the previous
            // text asserted "we could not work out when your account was created", which would be a
            // confidently wrong explanation for the first `closed_reason` added after it. Say the true,
            // smaller thing.
            if (reason === 'no_join_date') {
                return 'We could not work out when your account was created, so there is nothing to measure '
                    + 'against here.';
            }
            return 'The history importer is not available on this run.';
        }

        function renderHistoryPanel(panel) {
            // Any open prompt belonged to the panel being replaced.
            dropPrompts();
            mode = 'history';
            els.title.textContent = 'From your history';
            els.rows.textContent = '';
            els.rows.classList.remove('pp-cpick__rows--search');
            els.catchup.hidden = true;
            els.current.textContent = '';
            els.clear.hidden = true;
            if (els.histSwitch) {
                els.histSwitch.setAttribute('aria-pressed', 'true');
                // NO BOGUS "BACK". Arriving through the PAGE door means no square was ever opened, so
                // `load(openKey)` would be `load(null)` -- an empty catalogue search that answers "Type at
                // least two letters", a panel the hunter has never seen and did not ask for. That was a dead
                // end in the feature's primary flow, and both a comment and a test name claimed otherwise.
                // With nowhere to return to, the toggle is not offered; the sheet's own close button is the
                // way out.
                els.histSwitch.hidden = (openKey === null);
            }
            if (els.q) { els.q.placeholder = 'Search your history'; }

            if (!panel.open) {
                els.sub.textContent = 'Not available on this run';
                var why = historyClosedNote(panel.closed_reason);
                showNote(why);
                // THE REASON, NOT JUST THE HEADLINE. `showNote` writes into a plain `<p>`, so a screen reader
                // heard four words ("Not available on this run") and never learned why -- and the why is the
                // entire content of a closed panel.
                say(els.sub.textContent + '. ' + why);
                return;
            }

            // THE DATE IS NAMED, not implied. "Since you joined" is not something a hunter can check; a date
            // is. `TimeFormatter.absolute` is the same formatter the catch-up rows use, so the two blocks
            // cannot disagree about how a date reads.
            var joined = panel.joined_at && PP.TimeFormatter && PP.TimeFormatter.absolute
                ? PP.TimeFormatter.absolute(panel.joined_at,
                                            { year: 'numeric', month: 'short', day: 'numeric' })
                : null;
            showNoteBlock(
                joined ? 'Since you joined Platinum Pursuit on ' + joined
                       : 'Since you joined Platinum Pursuit',
                [
                    // ONE-TIME FIRST (owner, 2026-09-28). A head start somebody expects again on their second
                    // run is a disappointment we wrote ourselves.
                    'A one-time head start, for your first A-Z Challenge only.',
                    'Each game fills its letter immediately, and that square can never be changed.',
                    // THE RULE IS AN INSTANT, NOT A DAY. A game finished at breakfast on the day somebody
                    // signed up in the evening does not qualify, and a hunter hunting for it would otherwise
                    // think the list was broken.
                    'Anything finished earlier that same day, or before it, does not count.',
                    // THE ASYMMETRY, explained where it is met: a hunter running both types otherwise gets two
                    // answers about one game and no way to reconcile them.
                    'Job Coverage runs do not use the importer.',
                ]
            );

            if (!panel.rows.length) {
                // WHICH KIND OF EMPTY. With the window truncated, all the server knows is that nothing in the
                // first batch of candidates qualified -- so claiming the hunter has nothing importable would be
                // the same lie the window was added to stop, one layer up.
                els.sub.textContent = panel.query
                    ? 'Nothing here matches that'
                    : (panel.scan_truncated ? 'Nothing in the first batch' : 'Nothing here yet');
                if (panel.scan_truncated && !panel.query) {
                    // APPENDED AS A FACT, not concatenated onto a paragraph -- the block is a list now.
                    var li = document.createElement('li');
                    li.className = 'pp-cpick__note-fact--more';
                    li.textContent = 'We checked your earliest games by name and none qualified. Search for a '
                        + 'game to look further.';
                    if (els.noteFacts) { els.noteFacts.appendChild(li); }
                }
                say(els.sub.textContent);
                return;
            }
            // NO "+" HERE. There is no pagination on this panel, so a plus sign names rows the hunter cannot
            // reach -- and `more` can be true purely because the WINDOW filled, which says nothing about how
            // many more offers exist. Say what is on screen and how to look further.
            els.sub.textContent = panel.showing + ' ready to place';
            if (panel.more) { els.sub.textContent += ' \u00b7 search to look further'; }
            panel.rows.forEach(function (row) {
                // MONTH AND YEAR, no day, because the day was never the recognisable part: "Mar 2024" is what
                // places a game in a hunter's memory.
                //
                // IT IS NOT WHAT FIXED THE WRAPPING, which the first version of this comment claimed. At 375px
                // the label column is ~131px and the chip plus "Mar 3, 2024" comes to ~99px -- it fitted
                // already. What stopped the ragged heights was the ellipsis on the swap chip and the
                // equal-height rules; this is a legibility choice that happens to buy a little room.
                var when = row.completed_at && PP.TimeFormatter && PP.TimeFormatter.absolute
                    ? PP.TimeFormatter.absolute(row.completed_at, { year: 'numeric', month: 'short' })
                    : null;
                els.rows.appendChild(offerButton(row, historyLabel(row, when), function (picked, button) {
                    assign(picked.slug, row.key, false, button);
                }));
            });
            say('From your history: ' + els.sub.textContent);
        }

        /** One sentence in the note block, for the cases that only have to explain themselves. */
        function showNote(text) {
            if (!els.note) { return; }
            if (els.noteFacts) { els.noteFacts.textContent = ''; }
            if (els.noteLead) {
                els.noteLead.textContent = text || '';
                // PROSE, not the date's display styling. This element is specified as the anchor line for a
                // DATE; a two-sentence explanation set in it read as a shouted headline.
                els.noteLead.classList.add('pp-cpick__note-lead--prose');
            }
            els.note.hidden = !text;
        }

        /** THE OPEN CASE: an anchor line and a short list, rather than a paragraph.
         *
         *  Four facts decide whether a hunter presses anything here -- when the window starts, that it is
         *  one-time, that a press is permanent, and what does not count. As prose they were a block nobody
         *  would read at the moment they most need to; as a lead plus a list they are scannable, and the date
         *  (the only one they cannot infer) gets to be the thing their eye lands on.
         */
        function showNoteBlock(lead, facts) {
            if (!els.note || !els.noteLead || !els.noteFacts) { return; }
            els.noteLead.classList.remove('pp-cpick__note-lead--prose');
            els.noteLead.textContent = lead;
            els.noteFacts.textContent = '';
            facts.forEach(function (fact) {
                var li = document.createElement('li');
                li.textContent = fact;
                els.noteFacts.appendChild(li);
            });
            els.note.hidden = false;
        }

        /**
         * Is the sheet still the thing the hunter is looking at?
         *
         * THE GUARD `list-detail.js` HAS AND THIS DID NOT, and its absence was the worst defect in this
         * file. Press a catch-up offer, press Escape, let the 409 land: `offerConfirmation` fired a bare
         * `window.confirm` over a page with no sheet behind it, quoting a warning about a decision the
         * hunter had already walked away from -- and pressing OK stamped the square complete and LOCKED IT
         * FOREVER. The one action on a run that cannot be undone was reachable from a dismissed dialog.
         *
         * It also fixes the quieter half: a write that FAILED after dismissal wrote its reason into
         * `.pp-cpick__status`, inside a `display: none` dialog. Invisible, unannounced, no reload -- the
         * hunter was told nothing at all and had no reason to suspect anything.
         *
         * `dismissed` IS THE THIRD CONDITION, and without it this guard had a hole big enough to drive the
         * original bug back through. `dismissableSheet` does not touch `dialog.open` or `.is-closing`: it
         * drags the sheet with an inline transform, then translates it off-screen and waits 200ms before
         * calling `onClose` -- which is the first thing that closes the dialog. So for the whole drag, which
         * the hunter controls and can hold indefinitely, plus that 200ms, a sheet that has visibly LEFT THE
         * SCREEN still reported itself open. Swiping away and letting a 409 land put the permanent-lock
         * confirm back on a bare page. Touch is the platform `handle:` was added for, so this was the most
         * likely way to hit it, not the least.
         */
        function stillOpen() {
            return dialog.open && !dialog.classList.contains('is-closing') && !dismissed;
        }

        /** Report a failure where the hunter can actually see it: the sheet if it is up, a toast if not. */
        function reportAway(message) {
            if (PP.ToastManager && PP.ToastManager.error) { PP.ToastManager.error(message); }
            else if (PP.ToastManager && PP.ToastManager.show) { PP.ToastManager.show(message, 'error'); }
        }

        function assign(slug, key, confirmed, button) {
            // IN-FLIGHT LOCKOUT. Nothing disabled the offer, and `requestSeq` guarded only `load()`, so the
            // same offer could be sent twice (two writes, two reload timers, two success toasts) and two
            // DIFFERENT offers could toast two games for one square.
            //
            // The style for it did NOT already exist, whatever an earlier version of this comment said:
            // the same change that started setting `disabled` had deleted `.pp-cpick__row[disabled]` as
            // dead CSS. So a pressed offer looked identical to an unpressed one and the only feedback was
            // a 12px "Saving..." line. Both are back and both are pinned.
            // AND IT SAYS SO. Returning in silence is defensible for a stray double-press; it is not
            // defensible after the hunter has read a warning and pressed "Use it and lock the square", which
            // is reachable when another offer's write is still in flight. The prompt closes, and without this
            // nothing happens and nothing is said.
            if (writing) { say('Still saving the last one. Try again in a moment.'); return; }
            // AND NOT DURING THE EXIT. The dialog stays displayed and interactive for the 180ms of
            // `cpickOut`, so an offer pressed inside that window committed a write and delivered a toast
            // plus a reload after a dismissal the hunter believed had cancelled it. `list-detail.js` guards
            // its submit the same way.
            if (!stillOpen()) { return; }
            writing = true;
            if (button) { button.disabled = true; }
            var release = function () {
                writing = false;
                if (button) { button.disabled = false; }
            };
            var body = new FormData();
            body.append('contract', slug);
            if (confirmed) { body.append('confirm', '1'); }
            say('Saving...');
            PP.API.request(
                '/my-challenges/' + challengeId + '/slot/' + encodeURIComponent(key) + '/assign/',
                { method: 'POST', body: body }
            ).then(function (slot) {
                release();
                // AFTER THE CLOSE, not beside it. A modal `<dialog>` makes everything outside it inert and
                // takes it out of the accessibility tree, and the toast region lives outside -- so a toast
                // raised while this is open renders behind the top-layer backdrop and announces nothing.
                close(function () { applySlot(slot, slot.game_name + ' is in ' + labelFor(key) + '.'); });
            }).catch(function (err) {
                release();
                var response = err && err.response;
                if (response && response.status === 409) {
                    response.json().then(function (data) {
                        // A CONFIRMATION FOR A DISMISSED SHEET IS NOT A CONFIRMATION. If the hunter has
                        // walked away, the answer is "nothing happened", not a prompt over a bare page that
                        // can still lock a square permanently.
                        if (!stillOpen()) { return; }
                        offerConfirmation(data, slug, key, button);
                    }).catch(function () {
                        // NO INVENTED REASON. This used to assert "That square would be locked. Try again."
                        // -- stating the lock as fact when the cause is unknown (a proxy's 409, a non-JSON
                        // error document), and telling the hunter to retry something that would 409 forever
                        // because nothing on this path ever sends `confirm`.
                        fail('That did not go through. Reload and try again.');
                    });
                    return;
                }
                fail_from(err, 'That did not save.');
            });
        }

        /**
         * Ask, inside the sheet, right where the thing being confirmed is.
         *
         * NOT `window.confirm`. That was the first version and it was wrong in three ways beyond simply
         * looking foreign inside a designed sheet: it renders chrome we do not control, it cannot be styled
         * to say which of the two answers is the safe one, and -- the real defect -- a native dialog SURVIVES
         * its sheet. Dismiss the picker with a request in flight and the confirm sat there over a bare page,
         * still able to lock a square permanently. A guard was added for that; an inline prompt removes the
         * whole class, because a prompt that lives in the sheet leaves with it.
         *
         * ON THE HOUSE RECIPE (`.stg-confirm__row` in `components/settings-page.css`): two buttons, the SAFE
         * one first and focused, and the safe one named after what it preserves ("Keep Sly Cooper") rather
         * than "Cancel" -- so a reader who only reads the buttons still knows what each does. Account
         * deletion in Settings is built the same way.
         *
         * Spans the row grid, so on a multi-column catch-up list it is one wide prompt rather than a cell.
         */
        /** Ask before an irreversible placement, in whichever of the two surfaces suits the offers.
         *
         *  ONE QUESTION, TWO PRESENTATIONS, chosen by the shape of what is on screen:
         *
         *  - the SEARCH panel lists full-width rows, so a prompt inserted after the pressed row is just
         *    another row. It reads well and the owner said so; it stays.
         *  - the SQUARE panel and the catch-up block are multi-column GRIDS of cover cards. A full-width
         *    prompt there reflows the grid and pushes the card being decided about up far enough to clip its
         *    own art. Those go to the foot, which cannot reflow anything.
         *
         *  THE FOOT IS ALSO THE FALLBACK, and that closes a hole rather than just adding a case. This used to
         *  call `onGo()` outright when it could not find an `<li>` to anchor to -- so a DOM it did not expect
         *  meant an irreversible, permanently locking write happened with no confirmation at all. The foot
         *  needs no anchor, so there is nothing left to fall through to.
         */
        function ask(anchor, message, cost, goLabel, keepLabel, onGo) {
            var host = anchor && anchor.closest ? anchor.closest('li') : null;
            var list = host && host.parentNode;
            if (host && list && list.classList.contains('pp-cpick__rows--search')) {
                askInRow(anchor, host, list, message, cost, goLabel, keepLabel, onGo);
                return;
            }
            // THE WHOLE TRIO, because `askInFoot` dereferences all three and the button listeners are
            // wired behind `els.askKeep && els.askGo`. Gating on the container alone could raise a bar
            // with no working answers -- a question only Escape could dismiss.
            if (els.ask && els.askText && els.askKeep && els.askGo) {
                askInFoot(anchor, message, cost, goLabel, keepLabel, onGo);
                return;
            }
            // Neither surface exists, which means the sheet's markup is not what this script was written
            // against. Say nothing happened rather than doing the thing that cannot be undone.
            fail('Something went wrong. Reload the page and try again.');
        }

        /** Is the foot currently asking something? */
        function footAsking() {
            return !!(els.ask && !els.ask.hidden);
        }

        /** Put the foot back to its resting state: the square's name and its Clear button. */
        function closeFootAsk(restoreFocus) {
            if (!els.ask || els.ask.hidden) { return; }
            els.ask.hidden = true;
            els.foot.classList.remove('pp-cpick__foot--asking');
            if (els.current) { els.current.hidden = false; }
            // RESTORED FROM WHAT IT WAS, not to a guess. The button is hidden on an empty square and shown on
            // a filled one, so unhiding it unconditionally would offer "Clear this square" for a square with
            // nothing in it.
            if (els.clear) { els.clear.hidden = els.ask.getAttribute('data-clear-was') !== 'shown'; }
            var anchor = footAnchor;
            footAnchor = null;
            // CLEARED WITH THE PROMPT. Nothing can press the hidden button, so this is not a live path -- but
            // a callback closed over last panel's row, sitting in a variable after its prompt is gone, is the
            // shape of bug that only needs one future caller to become real.
            footGo = null;
            if (anchor) {
                anchor.classList.remove('pp-cpick__row--asking');
                anchor.removeAttribute('aria-describedby');
                if (restoreFocus && anchor.focus && document.contains(anchor)) { anchor.focus(); }
            }
        }

        /** The foot presentation: the grid holds still and the pressed card takes a ring. */
        function askInFoot(anchor, message, cost, goLabel, keepLabel, onGo) {
            closeFootAsk(false);
            footAnchor = anchor || null;
            els.askText.textContent = message;
            if (els.askCost) {
                els.askCost.textContent = cost || '';
                els.askCost.hidden = !cost;
            }
            els.askKeep.textContent = keepLabel;
            els.askGo.textContent = goLabel;
            els.ask.setAttribute('data-clear-was', els.clear && !els.clear.hidden ? 'shown' : 'hidden');
            if (els.current) { els.current.hidden = true; }
            if (els.clear) { els.clear.hidden = true; }
            els.ask.hidden = false;
            els.foot.classList.add('pp-cpick__foot--asking');
            // ONLY A CARD THAT IS STILL ON THE PAGE. A 409 can land after a re-render (press an offer, then
            // type in the search box), and the button it came from is detached by then -- ringing it dresses a
            // node nobody can see and points `aria-describedby` into nowhere. The foot still asks; there is
            // simply no card to mark.
            if (footAnchor && document.contains(footAnchor)) {
                footAnchor.classList.add('pp-cpick__row--asking');
                footAnchor.setAttribute('aria-describedby', 'cpick-ask-text');
            }
            footGo = onGo;
            // ANNOUNCED, because focus moves to a button whose label is "Keep Sly Cooper" and a screen reader
            // would otherwise be told nothing about what is being confirmed -- including that the square locks
            // for good. `aria-describedby` on the CARD was the first attempt and describes a path nobody
            // takes: the reader is on the button, and `closeFootAsk` strips the attribute before focus ever
            // returns to the card. The status region is already `role="status" aria-live="polite"` and already
            // announces the cancellation, so announcing the question is the smaller half of the same idea.
            say(message + (cost ? ' ' + cost : ''));
            // THE SAFE BUTTON, per the house convention: focusing the destructive one turns a stray Enter
            // into the thing the prompt exists to prevent.
            els.askKeep.focus();
        }

        function askInRow(anchor, host, list, message, cost, goLabel, keepLabel, onGo) {
            // ONE QUESTION AT A TIME, which `askInFoot` gets for free by owning a single bar and this had to
            // be told. A search result carries up to six key pills and none is disabled while a prompt is
            // open, so pressing a second occupied square inserted a SECOND prompt -- both with live Go
            // buttons, and `openPromptClose` overwritten so Escape answered the last one CREATED rather than
            // the one holding focus. Each prompt used to own its own key handler, so this was a regression
            // from centralising Escape, not a pre-existing gap.
            dropPrompts();

            var prompt = document.createElement('li');
            prompt.className = 'pp-cpick__ask';
            var text = document.createElement('p');
            text.className = 'pp-cpick__ask-text';
            text.textContent = message;
            prompt.appendChild(text);
            if (cost) {
                var costLine = document.createElement('p');
                costLine.className = 'pp-cpick__ask-cost';
                costLine.textContent = cost;
                prompt.appendChild(costLine);
            }

            var row = document.createElement('div');
            row.className = 'pp-cpick__ask-row';
            var keep = document.createElement('button');
            keep.type = 'button';
            keep.className = 'pp-cpick__ask-keep';
            keep.textContent = keepLabel;
            var go = document.createElement('button');
            go.type = 'button';
            go.className = 'pp-cpick__ask-go';
            go.textContent = goLabel;
            row.appendChild(keep);
            row.appendChild(go);
            prompt.appendChild(row);

            var close = function (restoreFocus) {
                openPromptClose = null;
                if (anchor && anchor.removeAttribute) { anchor.removeAttribute('aria-describedby'); }
                if (prompt.parentNode) { prompt.parentNode.removeChild(prompt); }
                // FOCUS GOES BACK to what was pressed, which a native confirm did for free and a built one
                // has to do on purpose -- otherwise a keyboard user lands at the top of the document.
                if (restoreFocus && anchor && anchor.focus && document.contains(anchor)) { anchor.focus(); }
            };
            keep.addEventListener('click', function () { close(true); say('Nothing changed.'); });
            go.addEventListener('click', function () { close(false); onGo(); });
            // ESCAPE IS ROUTED THROUGH THE `cancel` HANDLER, not handled here, and the reason is worth
            // recording because this used to be a `keydown` listener with `stopPropagation`.
            //
            // The sheet's Escape does not arrive as a keydown at all: a modal `<dialog>` turns Escape into a
            // `cancel` event dispatched ON THE DIALOG, which is what the close routine listens for. So
            // stopping a keydown from bubbling could never have stopped the sheet closing -- whether it
            // worked at all rested on the browser honouring `preventDefault()` on the keydown to suppress the
            // close request, which this listener never called. Publishing the closer and letting the `cancel`
            // handler decide needs no assumption about any of that.
            openPromptClose = function () { close(true); };

            // ANNOUNCED, AND DESCRIBED, matching the foot. This surface said nothing at all: a screen reader
            // heard only the focused button's label ("Keep Alan Wake, button") with no question and no cost.
            // The missing question was pre-existing; the cost line is new, so the pass had added an
            // unannounced consequence to one of its two surfaces.
            prompt.id = prompt.id || 'cpick-ask-row';
            if (anchor && anchor.setAttribute) { anchor.setAttribute('aria-describedby', prompt.id); }
            say(message + (cost ? ' ' + cost : ''));

            list.insertBefore(prompt, host.nextSibling);
            // THE SAFE BUTTON, per the house convention. Focusing the destructive one turns a stray Enter
            // into the thing the prompt exists to prevent.
            keep.focus();
        }

        /** What the square currently holds, read off the board rather than plumbed through every caller.
         *
         *  The board is the truth at the moment the question is asked, and it is the same value the square
         *  itself shows (the frozen snapshot, `_square_body.html`'s `.pp-csq__name`). Reading it here means all
         *  three panels get an accurate confirmation without `assign` having to carry an occupant argument
         *  through four call sites.
         */
        function occupantFor(key) {
            var square = grid.querySelector('[data-key="' + cssEscape(key) + '"]');
            var name = square && square.querySelector('.pp-csq__name');
            return name ? name.textContent.trim() : '';
        }

        function offerConfirmation(data, slug, key, button) {
            // THE SAFE ANSWER HAS TO BE TRUE. It was hardcoded to "Leave it empty", which is a promise about a
            // square that may not be empty: the history panel is the first surface to advertise "replaces X",
            // and pressing its offer went straight to `assign` -- so the only question a hunter saw said the
            // square would be left EMPTY while it was holding a game that the other answer would silently
            // destroy. Naming what is kept is the house convention and the whole point of it.
            var occupant = occupantFor(key);
            // THE QUESTION IS OURS, THE CONSEQUENCE IS THE SERVER'S. `data.error` is the sentence that explains
            // the lock, and it belongs on the cost line rather than as the headline -- a hunter scanning this
            // needs "what am I about to do" first and "what it costs" second.
            ask(button,
                'Put ' + data.contract_name + ' in ' + labelFor(key) + '?',
                data.error + (occupant ? ' ' + occupant + ' would be replaced.' : ''),
                'Use it and lock the square',
                occupant ? 'Keep ' + occupant : 'Leave it empty',
                function () { assign(slug, key, true, button); });
        }

        function labelFor(key) {
            var square = grid.querySelector('[data-key="' + cssEscape(key) + '"]');
            var job = square && square.querySelector('.pp-csq__job');
            return job ? job.textContent.trim() : key;
        }

        function cssEscape(value) {
            return window.CSS && window.CSS.escape ? window.CSS.escape(value) : value;
        }

        function toast(message) {
            // NO EXPLICIT DURATION any more. It existed only so a reload timer could wait for the toast, and
            // there is no reload -- so `ToastManager`'s own default is the right answer again.
            // GUARDED ON THE METHOD, not the namespace: a cached older `utils.js` against this fresh file
            // would pass an `if (PP.ToastManager)` check and then throw on a method it lacks.
            if (PP.ToastManager && PP.ToastManager.show) {
                PP.ToastManager.show(message, 'success');
            }
        }

        // ── applying a write to the page ──────────────────────────────────────────────────────────

        /**
         * Swap in the square the server just re-rendered, move the counters, and say what happened.
         *
         * NO RELOAD. There were two before this: a 900ms one whose toast never finished, then a 2.8s one
         * that did. Both were defensible for the same reason -- a filled square needs cover art the reply
         * did not carry, a completed one needs its check glyph, and a client composing those would be a
         * second renderer free to drift from the template. Both were also wrong about the cost: filling 26
         * squares meant 26 full navigations, each flashing the page, replaying the grid's 840ms entrance and
         * scrolling to the top. The owner filled a run and said so.
         *
         * The reply now carries `html` for the one square that changed, rendered by
         * `partials/_square_body.html` -- the same template the page used. One renderer, no navigation, and
         * the toast gets its whole life.
         */
        function applySlot(slot, message) {
            var square = grid.querySelector('[data-key="' + cssEscape(slot.key) + '"]');
            if (square) {
                // THE SERVER'S MARKUP, not markup built here. `slot.html` is the same partial the page
                // rendered, so a swapped square cannot look different from one that was there on load.
                if (slot.html) { square.innerHTML = slot.html; }
                square.classList.toggle('pp-csq--filled', slot.is_filled && !slot.is_completed);
                square.classList.toggle('pp-csq--empty', !slot.is_filled);
                square.classList.toggle('pp-csq--done', slot.is_completed);
                // A COMPLETED SQUARE STOPS BEING PRESSABLE. It stays a `<button>` rather than becoming the
                // `<div>` a fresh render would produce -- swapping the tag would mean rebuilding the element
                // and losing focus with it -- so it is disabled and loses its open hook instead. `:disabled`
                // carries the same cursor and kills the hover lift, so it reads identically.
                if (slot.is_completed) {
                    square.removeAttribute('data-cpick-open');
                    square.disabled = true;
                }
            }
            // THE SHELF'S OWN COUNTER, which nothing else moves. The header tally and the horizon both
            // update below, and the square gets its ring -- but "0 of 5 done" on the discipline that just
            // advanced kept saying 0 until a reload, and that counter is the entire justification for the
            // label area existing. Counted off the DOM rather than from the payload: the reply describes one
            // slot and says nothing about disciplines, and the shelf's own squares are already the truth.
            var shelf = square ? square.closest('.pp-csq-shelf') : null;
            var sub = shelf ? shelf.querySelector('.pp-csq-shelf__sub') : null;
            if (sub) {
                sub.textContent = shelf.querySelectorAll('.pp-csq--done').length
                    + ' of ' + shelf.querySelectorAll('.pp-csq').length + ' done';
            }
            if (els.tally) {
                // TICKS FROM THE OLD VALUE, not from zero: this is a change to a number already on screen, so
                // counting up from 0 would read as the page reloading. `countUp` needs the target on
                // `data-countup` and the start in `from` -- the shape `company-list.js` uses when its filtered
                // total changes. It honours reduced-motion itself, so there is no branch here.
                //
                // READ FROM `data-countup`, NOT FROM THE RENDERED TEXT, for two reasons that both bite the
                // same line. The text can be MID-ANIMATION (the entrance's own count-up is still running for
                // 900ms after load), so parsing it starts the new tick from a number that was never real. And
                // `countUp` formats with `toLocaleString()`, so a four-figure count renders `1.000` on a
                // de-DE browser and `1 000` on fr-FR, where stripping commas and parsing yields 1. Neither is
                // reachable at 25 or 26 squares; the attribute is the value either way.
                var before = parseInt(els.tally.dataset.countup || '', 10);
                els.tally.dataset.countup = String(slot.completed_count);
                if (PP.countUp) {
                    PP.countUp(els.tally, 600, { from: isNaN(before) ? 0 : before });
                    // AND REASSERT IT once every animation that could be in flight has ended. `countUp` has
                    // no cancellation: each call owns its own frame loop and writes ITS captured target when
                    // it finishes, so the entrance's 900ms loop starting at load can outlive a 600ms write
                    // loop and leave the OLD number on screen for good. Cheaper and more honest than
                    // reimplementing cancellation in a shared utility.
                    window.setTimeout(function () {
                        if (els.tally.dataset.countup === String(slot.completed_count)) {
                            els.tally.textContent = String(slot.completed_count);
                        }
                    }, 1000);
                } else {
                    els.tally.textContent = slot.completed_count;
                }
            }
            // NO EXPLICIT ANIMATION NEEDED. `.pp-horizon__fill` carries `transition: width 0.35s` in the
            // primitive itself, so setting the property animates.
            //
            // WHAT THE OLD BUG ACTUALLY WAS, corrected: writing to the wrong `.pp-horizon` was a COMPLETE
            // NO-OP, not a smooth animation of something hidden. The nav's sync bar sets
            // `--horizon-progress` inline on its `.pp-horizon__fill` (`navbar.html`, `data-nav-fill`), and
            // that declaration SHADOWS anything inherited from the root -- so setting the property on the
            // root changed nothing anywhere. The run's own bar sets it on the root, which is why inheriting
            // down to the fill works here. Diagnosis and fix were right; this comment's mechanism was not.
            if (els.horizon && slot.total_slots) {
                var pct = Math.round(slot.completed_count / slot.total_slots * 100);
                els.horizon.style.setProperty('--horizon-progress', pct + '%');
                els.horizon.setAttribute('aria-valuenow', String(pct));
            }
            if (message) { toast(message); }

            // THE RUN FINISHING IS A MODE CHANGE, not a square update, and it was the one thing the removed
            // reload had been quietly handling. When the last square completes, `can_edit` turns false: the
            // header gains its "Finished" chip, the read-only note appears, the picker stops shipping and
            // every square becomes a `<div>`. Patching all of that here would be the second renderer this
            // design exists to avoid -- so this is the one case that still reloads, once per run instead of
            // once per square, at the moment a hunter has most reason to expect the page to change.
            if (slot.is_complete) {
                // AND THE RELOAD MUST NOT REPLAY THE ENTRANCE. Without this the hunter watches the tally
                // reach 25, then the page reloads and the tally SNAPS BACK TO 0 and counts up again while the
                // bar refills -- the celebration played twice, the second time starting with a visible reset.
                // A marker rather than a querystring, so the finished run's URL stays clean and shareable.
                try { window.sessionStorage.setItem(INTRO_DONE, '1'); } catch (e) { /* private mode */ }
                window.setTimeout(function () { window.location.reload(); }, 1200);
            }
        }

        // ── wiring ────────────────────────────────────────────────────────────────────────────────

        /** Blank every part of the sheet, so nothing from the last square survives into this one. */
        function reset() {
            dismissed = false;
            els.q.value = '';
            els.rows.textContent = '';
            // The search layout is a CLASS on the rows container, so it has to come off too -- it was the
            // one piece of the last panel that `reset()` left behind.
            els.rows.classList.remove('pp-cpick__rows--search');
            // THE MODE IS PART OF THE SHEET'S STATE. A sheet reopened from a square must not still be in
            // history mode -- typing would filter history under a square's title, and the note would explain a
            // rule the panel is no longer applying. `leaveHistory` undresses the rest.
            //
            // This block first landed in `renderSlotPanel`, which already calls `leaveHistory()` and has its
            // mode set by `load()` -- so it was three duplicated lines there and nothing here, which is the
            // one place that has to forget.
            mode = 'slot';
            leaveHistory();
            els.catchupRows.textContent = '';
            els.catchupTitle.textContent = '';
            els.catchupNote.textContent = '';
            els.catchup.hidden = true;
            // `dropPrompts`, not `closeFootAsk`: this also has to null `openPromptClose`, or a prompt
            // whose `<li>` was just deleted by the line above leaves its closer behind for Escape to
            // call. `dropPrompts`' own comment names that hazard; only one of the two teardowns obeyed it.
            dropPrompts();
            els.current.hidden = false;
            els.current.textContent = '';
            els.clear.hidden = true;
            els.clear.disabled = false;
            // The static name until the panel lands: a dialog whose accessible name is a bare letter is not
            // usefully named, and the one it overwrites says what the sheet is for.
            els.title.textContent = 'Choose a game';
            els.sub.textContent = '';
            say('');
        }

        grid.addEventListener('click', function (e) {
            var square = e.target.closest ? e.target.closest('[data-cpick-open]') : null;
            if (!square || !grid.contains(square)) { return; }
            openKey = square.getAttribute('data-key');
            // EVERYTHING, not just the lists. This cleared `q`, `rows` and `catchup` and left the TITLE, the
            // subtitle, the "Currently: <game>" line and the Clear button holding the last square's values
            // -- so opening B after filling A showed "A / Currently: Elden Ring / [Clear this square]" for
            // the whole round trip, and longer if the load failed. `aria-labelledby` points at that title,
            // so a screen reader announced the dialog as "A" while B was loading. Worst of it: Clear was
            // VISIBLE and its handler posts to `openKey`, which had already advanced to B -- a destructive
            // control labelled with one square and acting on another.
            reset();
            // SHOWN BEFORE LOADED, and the order is the point. `load()` writes "Loading..." into the status
            // region, which lives INSIDE the dialog -- so while the dialog was still `display: none` that
            // mutation happened outside the rendered tree and no screen reader ever observed it. Opening the
            // picker was silent. `reset()` above means the sheet is never shown holding the last square's
            // state, so there is nothing to hide by loading first.
            dialog.showModal();
            // ONE FRAME LATER, because `showModal()` and a synchronous `load()` put the live region into the
            // tree ALREADY CONTAINING "Loading..." -- and initial content of a newly-rendered region is not
            // announced. Fixing the order was necessary and not sufficient. `ToastManager` defers its own
            // announcement for exactly this reason.
            window.setTimeout(function () { load(openKey, ''); }, 0);
            // THE DIALOG, not the search field. Focusing the field opens the soft keyboard over the panel
            // before a hunter has seen what is in it, and on a sheet whose first job is to SHOW options that
            // is the wrong first move. They can reach the field with one tap or one Tab.
            dialog.focus();
        });

        if (els.q) {
            var run = function () {
                var term = els.q.value.trim();
                // IN HISTORY MODE, TYPING FILTERS THE HISTORY. Falling through to the catalogue search would
                // answer a different question from the one the panel is asking, and an empty box here means
                // "all of my history" rather than "back to the square".
                if (mode === 'history') { loadHistory(term); return; }
                // AN EMPTY BOX RETURNS TO THE SQUARE'S OWN POOL rather than searching for nothing -- the
                // slot panel is the resting state of this sheet, not a search result with no term.
                if (!term) { load(openKey, ''); return; }
                // A TERM SEARCHES THE WHOLE CATALOGUE, not the square's pool: somebody typing a game's name
                // is asking the contract-first question even though they came in through a square.
                load(null, term);
            };
            els.q.addEventListener('input', PP.debounce ? PP.debounce(run, 250) : run);
        }

        // THE IN-SHEET TOGGLE. Pressing it in history mode goes BACK to whatever the sheet was showing, so a
        // hunter who arrived through a square is not stranded in a panel about the whole run.
        if (els.histSwitch) {
            els.histSwitch.addEventListener('click', function () {
                // THE OPEN QUESTION BELONGS TO THE PANEL BEING REPLACED. Nothing disables this while the foot
                // is asking, so without it a hunter could switch panels and then press "Use it and lock the
                // square" for an offer that is no longer on screen.
                dropPrompts();
                if (mode === 'history') {
                    if (els.q) { els.q.value = ''; }
                    load(openKey, '');
                    return;
                }
                if (els.q) { els.q.value = ''; }
                loadHistory('');
            });
        }

        // THE PAGE'S DOOR. Opens the sheet straight into history mode with no square in mind, which is the
        // flow it exists for: a hunter who knows their library covers half the alphabet should not have to
        // pick a letter first.
        Array.prototype.forEach.call(
            document.querySelectorAll('[data-cpick-history]'),
            function (button) {
                button.addEventListener('click', function () {
                    reset();
                    openKey = null;
                    if (!dialog.open) { dialog.showModal(); }
                    // FOCUS THE SHEET, not whatever `showModal` lands on -- which is the close button, the
                    // least useful control in it. The square door does this deliberately and explains why it
                    // does not jump to the search field; this door was simply missing both halves.
                    dialog.focus();
                    // AND A TICK LATER, because content already present in a live region when it enters the
                    // tree is not announced. The square door wraps its load for exactly this reason and the
                    // comment there spells it out; calling straight through meant "Loading..." was silent.
                    window.setTimeout(function () { loadHistory(''); }, 0);
                });
            });

        if (els.askKeep && els.askGo) {
            els.askKeep.addEventListener('click', function () {
                closeFootAsk(true);
                say('Nothing changed.');
            });
            els.askGo.addEventListener('click', function () {
                var go = footGo;
                footGo = null;
                // CLOSED BEFORE THE WRITE, and focus moved somewhere real rather than left to fall.
                //
                // The old reasoning here was that returning focus to the anchor would lose it anyway because
                // `assign` disables that button -- which justified a choice that did not achieve anything:
                // hiding the bar removes the focused Go button from the rendering tree, so focus fell to the
                // document either way. A SUCCESSFUL write closes the sheet and the browser restores focus to
                // whatever opened it, so that path was fine by luck; a FAILED one leaves the sheet open with
                // focus nowhere, which is the case this fixes.
                closeFootAsk(false);
                if (els.q && document.contains(els.q)) { els.q.focus(); }
                if (go) { go(); }
            });
        }

        if (els.clear) {
            els.clear.addEventListener('click', function () {
                if (!openKey) { return; }
                // THE SAME LOCK `assign` TAKES, which this was outside. Two quick presses meant two clear
                // POSTs, two `close()` drains, two toasts and two reload timers -- exactly what the flag was
                // added to prevent, in the one write it did not cover. Worse, Clear stays live during an
                // in-flight assign, so the two could race one slot and the hunter could be told both
                // "Elden Ring is in A." and "A is empty again."
                if (writing) { return; }
                writing = true;
                els.clear.disabled = true;
                var release = function () {
                    writing = false;
                    els.clear.disabled = false;
                };
                say('Clearing...');
                PP.API.request(
                    '/my-challenges/' + challengeId + '/slot/' + encodeURIComponent(openKey) + '/clear/',
                    { method: 'POST', body: new FormData() }
                ).then(function (slot) {
                    release();
                    close(function () { applySlot(slot, labelFor(slot.key) + ' is empty again.'); });
                }).catch(function (err) {
                    release();
                    fail_from(err, 'That did not clear.');
                });
            });
        }

        Array.prototype.forEach.call(
            dialog.querySelectorAll('[data-cpick-close]'),
            function (button) { button.addEventListener('click', function () { close(); }); });

        // Escape, routed through the choreographed close rather than the browser's instant one.
        // ESCAPE TRIES TO ANSWER THE NARROWEST THING THAT IS OPEN, and "tries" is the honest verb.
        //
        // A prompt open means a hunter is mid-question about an irreversible placement, so Escape should say
        // "no" to THAT rather than dismissing the question and the offers together.
        //
        // WHAT THIS CANNOT PROMISE, corrected: an earlier version of this comment said routing through
        // `cancel` needed "no assumption about whether cancelling a keydown suppresses a close request". It
        // traded that assumption for a weaker one. Under the close-watcher rules `<dialog>` follows, `cancel`
        // is only cancelable while the page holds TRANSIENT USER ACTIVATION, and it will not fire again for a
        // second close request until fresh activation arrives. Escape does not grant activation, and the
        // window expires in seconds -- so a hunter who READS this prompt (which is the entire point of it)
        // and then presses Escape may find the sheet closing instead.
        //
        // That is a UX imperfection, not a correctness one, and the difference is where the work went: the
        // `close` listener below tears both prompts down whatever route shut the sheet, so nothing is written,
        // no callback survives, and no ring or bar is left behind. The `keydown` handler below is a
        // best-effort attempt to keep the sheet open in that case; whether `preventDefault()` on the key
        // suppresses the close request is NOT something this file should assert, so it does not.
        // BEST EFFORT, FIRST. If cancelling the key does suppress the close request, a prompt answered here
        // never reaches `cancel` at all; if it does not, `cancel` (or the `close` listener) still cleans up.
        // No comment here claims which, because this file cannot verify it.
        dialog.addEventListener('keydown', function (e) {
            if (e.key !== 'Escape') { return; }
            if (!footAsking() && !openPromptClose) { return; }
            e.preventDefault();
            e.stopPropagation();
            if (footAsking()) { closeFootAsk(true); say('Nothing changed.'); return; }
            var closePrompt = openPromptClose;
            openPromptClose = null;
            closePrompt();
            say('Nothing changed.');
        }, true);

        dialog.addEventListener('cancel', function (e) {
            e.preventDefault();
            if (footAsking()) { closeFootAsk(true); say('Nothing changed.'); return; }
            if (openPromptClose) {
                var closePrompt = openPromptClose;
                openPromptClose = null;
                closePrompt();
                say('Nothing changed.');
                return;
            }
            close();
        });

        // Backdrop click. On a native `<dialog>` a click on the backdrop reports the dialog as the target.
        dialog.addEventListener('click', function (e) { if (e.target === dialog) { close(); } });

        // EVERY DISMISSAL TEARS THE QUESTION DOWN, and the native `close` event is the only place that can
        // promise it. Four routes shut this sheet -- the x button, the backdrop, a swipe (`dismissableSheet`
        // calls `dialog.close()` itself, bypassing our own `close()`), and Escape -- and only Escape used to
        // answer an open prompt. The others left the bar up through the 180ms exit, the ring on a card nobody
        // could see, and `openPromptClose` pointing at a detached node, which then swallowed the NEXT Escape
        // and announced "Nothing changed." about a question that was never on screen.
        //
        // ON `close` RATHER THAN IN `close()`: the event fires however the dialog shut, including the paths
        // that never call our function and including an Escape whose `cancel` we could not cancel (see the
        // handler above). One hook, no route to forget.
        dialog.addEventListener('close', function () { dropPrompts(); });

        // `handle:` IS THE DATA-LOSS GUARD, and this sheet needs it. `dismissableSheet`'s contract: "Omit on
        // a sheet you READ; pass one on a sheet you OPERATE." This one holds a search term and two
        // irreversible offers, so without a handle any downward flick -- on a row, on the warning block --
        // would dismiss it mid-decision.
        if (PP.dismissableSheet) {
            PP.dismissableSheet(dialog, {
                handle: '.pp-cpick__head',
                // A DIRECT close, not the choreographed one. The helper has already animated the sheet
                // off-screen by the time it calls this and clears the transform first, so handing it
                // `close` made a flicked sheet slide away, POP BACK into view, then play a second exit.
                onClose: function () {
                    // MARKED BEFORE CLOSED, so anything still in flight sees the dismissal even though the
                    // helper has been sliding the sheet away for 200ms already.
                    dismissed = true;
                    if (dialog.close && dialog.open) { dialog.close(); }
                },
            });
        }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); } else { document.addEventListener('DOMContentLoaded', boot); }
}());
