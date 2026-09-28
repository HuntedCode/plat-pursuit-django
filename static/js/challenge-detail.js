/**
 * Challenge detail: the picker that fills a square.
 *
 * TWO MODES, ONE DIALOG. Pressing an empty or unfinished square opens it SLOT-FIRST (what fits here?);
 * typing in the search box switches it CONTRACT-FIRST (where does this game go?). They are one sheet
 * because they answer the same question from two directions, and because the catch-up warning, the
 * confirmation and the refusal handling would otherwise be written twice.
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

    function boot() {
        var dialog = document.getElementById('cpick');
        var grid = document.querySelector('.pp-csq-grid');
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
            // Outside the dialog: the page's own counters, which every write moves.
            tally: document.querySelector('[data-cpick-tally]'),
            horizon: document.querySelector('.pp-horizon'),
        };

        var challengeId = grid.getAttribute('data-challenge-id');
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
                var note = document.createElement('span');
                note.className = 'pp-cpick__row-note';
                note.textContent = label;
                button.appendChild(note);
            }
            button.addEventListener('click', function () { onPick(row, button); });
            // WRAPPED IN AN `<li>`, because both row containers are `<ul>`s and a `<button>` is not valid
            // as their direct child -- the parser tolerates it and assistive tech stops counting the list.
            var item = document.createElement('li');
            item.appendChild(button);
            return item;
        }

        function renderSlotPanel(panel) {
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

        function renderSearchPanel(panel) {
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
                var name = document.createElement('span');
                name.className = 'pp-cpick__row-name';
                name.textContent = row.name;
                main.appendChild(name);
                // ALREADY FINISHED, marked. The server has been sending this all along -- one indexed query
                // over the page, via `completed_contract_ids` -- and only one narrow branch read it, so a
                // search result gave no hint that placing it would complete the square on the spot. The chip
                // is the house primitive, not DaisyUI's badge.
                if (row.is_completed_by_you) { main.appendChild(chip('Finished', 'success')); }
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
                    if (!single) { main.appendChild(lead('Add this game to:')); }
                    var keys = document.createElement('div');
                    keys.className = 'pp-cpick__keys';
                    row.keys.forEach(function (key) {
                        var keyLabel = row.key_labels[key] || key;
                        var occupant = (panel.filled || {})[key];
                        var pick = document.createElement('button');
                        pick.type = 'button';
                        pick.className = 'pp-cpick__key' + (single ? ' pp-cpick__key--wide' : '');
                        pick.textContent = single ? 'Add this game to ' + keyLabel : keyLabel;
                        // AN OCCUPIED SQUARE SAYS SO BEFORE IT IS PRESSED. Picking a game for a square that
                        // already holds one silently replaced it -- easy to do by accident, since the search
                        // panel says nothing about the rest of the run.
                        if (occupant) {
                            pick.classList.add('pp-cpick__key--taken');
                            pick.appendChild(swap(' (replaces ' + occupant + ')'));
                        }
                        // THE NAME CARRIES THE GAME, because the pill alone names only the square and the
                        // game sits in a sibling associated with nothing. The visible text is contained in
                        // the accessible name, so the two do not disagree for voice control.
                        pick.setAttribute('aria-label', pick.textContent + ' \u2014 ' + row.name);
                        pick.addEventListener('click', function () {
                            if (occupant && !window.confirm(
                                    keyLabel + ' already has ' + occupant + '.\n\n'
                                    + 'Replace it with ' + row.name + '?')) {
                                return;
                            }
                            assign(row.slug, key, false, pick);
                        });
                        keys.appendChild(pick);
                    });
                    main.appendChild(keys);
                }
                card.appendChild(block);
                els.rows.appendChild(card);
            });
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
            if (writing) { return; }
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

        function offerConfirmation(data, slug, key, button) {
            // NATIVE `confirm()`, the same call `list-detail.js` uses for its own destructive action and for
            // the same reason: this is a modal `<dialog>` already, and a second layered dialog inside the
            // top layer is a fight with focus and with the backdrop that buys nothing. The text is the
            // server's, so the warning and the rule cannot drift apart.
            var proceed = window.confirm(
                data.error + '\n\n' + data.contract_name + ' -> ' + labelFor(key));
            if (!proceed) { say('Nothing changed.'); return; }
            assign(slug, key, true, button);
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
            if (els.tally) { els.tally.textContent = slot.completed_count; }
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
            els.catchupRows.textContent = '';
            els.catchupTitle.textContent = '';
            els.catchupNote.textContent = '';
            els.catchup.hidden = true;
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
                // AN EMPTY BOX RETURNS TO THE SQUARE'S OWN POOL rather than searching for nothing -- the
                // slot panel is the resting state of this sheet, not a search result with no term.
                if (!term) { load(openKey, ''); return; }
                // A TERM SEARCHES THE WHOLE CATALOGUE, not the square's pool: somebody typing a game's name
                // is asking the contract-first question even though they came in through a square.
                load(null, term);
            };
            els.q.addEventListener('input', PP.debounce ? PP.debounce(run, 250) : run);
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
        dialog.addEventListener('cancel', function (e) { e.preventDefault(); close(); });

        // Backdrop click. On a native `<dialog>` a click on the backdrop reports the dialog as the target.
        dialog.addEventListener('click', function (e) { if (e.target === dialog) { close(); } });

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
