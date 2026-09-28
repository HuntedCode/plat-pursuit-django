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
 * THE CLOSE ROUTINE IS A FOURTH COPY, and that is a deliberate choice rather than an oversight.
 * `gamelists.js`, `game-flag.js` and `list-detail.js` each carry it, and it is six separate bug fixes deep
 * (the queue, the reduced-motion path, the `pseudoElement` guard, the cleared fallback timer, the direct
 * close for swipes, and firing toasts only after the modal is gone). The rule in `utils.js`'s own history is
 * that extracting a helper and leaving duplicates behind is worse than the duplication -- "delegate and
 * delete" -- and deleting three shipped copies is not this chunk's business. So: a fourth copy, with every
 * subtlety carried across and named, and a `refactor/` branch owed that migrates all four together.
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
        };

        var challengeId = grid.getAttribute('data-challenge-id');
        // THE OPEN SLOT, and the guard for every async reply. A reply is applied only if the panel is still
        // showing the slot it was asked about -- keyed on IDENTITY, not on nullness, because a hunter who
        // closes one square and opens another mid-request would otherwise see the first square's pool under
        // the second square's title.
        var openKey = null;
        var requestSeq = 0;

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
            if (els.status) { els.status.textContent = message || ''; }
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
            button.addEventListener('click', function () { onPick(row); });
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
            panel.rows.forEach(function (row) {
                els.rows.appendChild(offerButton(row, null, function (picked) {
                    assign(picked.slug, panel.key, false);
                }));
            });

            if (!panel.rows.length) {
                say(panel.query
                    ? 'Nothing matches that here.'
                    : 'No games left for this square.');
            } else {
                say('');
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
                els.catchupRows.appendChild(offerButton(row, label, function (picked) {
                    assign(picked.slug, panel.key, false);
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

            if (panel.too_short) { say(''); return; }
            if (!panel.rows.length) { say('No games match that.'); return; }
            say('');

            panel.rows.forEach(function (row) {
                var card = document.createElement('li');
                var block = document.createElement('div');
                // `--static`: this card is NOT the button. The squares it could fill are, below
                // it -- so the card must not carry a pointer cursor or a hover lift promising a
                // press that does nothing.
                block.className = 'pp-cpick__row pp-cpick__row--static';
                block.appendChild(art(row));
                var name = document.createElement('span');
                name.className = 'pp-cpick__row-name';
                name.textContent = row.name;
                block.appendChild(name);

                if (row.already_in_run) {
                    block.appendChild(note('Already in this run'));
                } else if (!row.keys.length) {
                    // EXPLAINS ITSELF rather than rendering without a button. "No square open" and "this
                    // game fits nothing" look identical to a hunter unless one of them says so.
                    block.appendChild(note(row.is_completed_by_you
                        ? 'No open square for this'
                        : 'Nowhere to put this yet'));
                } else {
                    // ONE BUTTON PER SQUARE IT FITS. A jobs game routinely fits several, and choosing the
                    // game is not the same decision as choosing the slot.
                    var keys = document.createElement('div');
                    keys.className = 'pp-cpick__keys';
                    row.keys.forEach(function (key) {
                        var pick = document.createElement('button');
                        pick.type = 'button';
                        pick.className = 'pp-cpick__key';
                        pick.textContent = row.key_labels[key] || key;
                        pick.addEventListener('click', function () { assign(row.slug, key, false); });
                        keys.appendChild(pick);
                    });
                    block.appendChild(keys);
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
                say_failure(err, 'That did not load. Try again.');
            });
        }

        /**
         * Say why something failed, in the server's own words when it sent any.
         *
         * ASYNC, and that is not a detail. `API.failureOr` is `async` -- it awaits `failureBody`, which
         * reads the response stream -- so it returns a PROMISE. An earlier version of this function handed
         * that promise straight to `textContent`, which renders the string "[object Promise]" at a hunter
         * who is being told why their square did not save. Reading the signature would have caught it;
         * assuming it did not.
         *
         * GUARDED ON THE METHOD, not the namespace. Static files are hashed and served independently, so a
         * browser can hold a cached older `utils.js` against this fresh file -- and `if (!PP.API)` passes on
         * a stale bundle that simply lacks this one method. The exposure is worst exactly here, inside a
         * rejection handler, where failing to report a failure is invisible.
         */
        function say_failure(err, fallback) {
            if (PP.API && PP.API.failureOr) {
                PP.API.failureOr(err, fallback).then(say).catch(function () { say(fallback); });
                return;
            }
            say(fallback);
        }

        function assign(slug, key, confirmed) {
            var body = new FormData();
            body.append('contract', slug);
            if (confirmed) { body.append('confirm', '1'); }
            say('Saving...');
            PP.API.request(
                '/my-challenges/' + challengeId + '/slot/' + encodeURIComponent(key) + '/assign/',
                { method: 'POST', body: body }
            ).then(function (slot) {
                applySlot(slot);
                // AFTER THE CLOSE, not beside it. A modal `<dialog>` makes everything outside it inert and
                // takes it out of the accessibility tree, and the toast region lives outside -- so a toast
                // raised while this is open renders behind the top-layer backdrop and announces nothing.
                close(function () { toast(slot.game_name + ' is in ' + labelFor(key) + '.'); });
            }).catch(function (err) {
                var response = err && err.response;
                if (response && response.status === 409) {
                    response.json().then(function (data) {
                        offerConfirmation(data, slug, key);
                    }).catch(function () {
                        say('That square would be locked. Try again.');
                    });
                    return;
                }
                say_failure(err, 'That did not save.');
            });
        }

        function offerConfirmation(data, slug, key) {
            // NATIVE `confirm()`, the same call `list-detail.js` uses for its own destructive action and for
            // the same reason: this is a modal `<dialog>` already, and a second layered dialog inside the
            // top layer is a fight with focus and with the backdrop that buys nothing. The text is the
            // server's, so the warning and the rule cannot drift apart.
            var proceed = window.confirm(
                data.error + '\n\n' + data.contract_name + ' -> ' + labelFor(key));
            if (!proceed) { say('Nothing changed.'); return; }
            assign(slug, key, true);
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
            if (PP.ToastManager) { PP.ToastManager.show(message, 'success'); }
        }

        // ── applying a write to the page ──────────────────────────────────────────────────────────

        function applySlot(slot) {
            // A FULL RELOAD OF THE SQUARE would be simpler and is wrong: the reveal animation would replay
            // for one cell in a settled grid, which reads as the page glitching. So the square's state
            // classes and its text are updated in place, and the counters with them.
            var square = grid.querySelector('[data-key="' + cssEscape(slot.key) + '"]');
            if (square) {
                square.classList.toggle('pp-csq--filled', slot.is_filled && !slot.is_completed);
                square.classList.toggle('pp-csq--empty', !slot.is_filled);
                square.classList.toggle('pp-csq--done', slot.is_completed);
            }
            var tally = document.querySelector('[data-cpick-tally]');
            if (tally) { tally.textContent = slot.completed_count; }
            var horizon = document.querySelector('.pp-horizon');
            if (horizon && slot.total_slots) {
                var pct = Math.round(slot.completed_count / slot.total_slots * 100);
                horizon.style.setProperty('--horizon-progress', pct + '%');
                horizon.setAttribute('aria-valuenow', String(pct));
            }
            // THE SQUARE'S CONTENTS ARE NOT REBUILT HERE. A filled square needs cover art this reply does
            // not carry, and a completed one stops being a button entirely -- both are the server's render.
            // So the page is reloaded once the toast has been seen, which keeps one renderer for a square.
            window.setTimeout(function () { window.location.reload(); }, 900);
        }

        // ── wiring ────────────────────────────────────────────────────────────────────────────────

        grid.addEventListener('click', function (e) {
            var square = e.target.closest ? e.target.closest('[data-cpick-open]') : null;
            if (!square || !grid.contains(square)) { return; }
            openKey = square.getAttribute('data-key');
            els.q.value = '';
            els.rows.textContent = '';
            els.catchup.hidden = true;
            load(openKey, '');
            dialog.showModal();
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
                say('Clearing...');
                PP.API.request(
                    '/my-challenges/' + challengeId + '/slot/' + encodeURIComponent(openKey) + '/clear/',
                    { method: 'POST', body: new FormData() }
                ).then(function (slot) {
                    applySlot(slot);
                    close(function () { toast(labelFor(slot.key) + ' is empty again.'); });
                }).catch(function (err) {
                    say_failure(err, 'That did not clear.');
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
                onClose: function () { if (dialog.close && dialog.open) { dialog.close(); } },
            });
        }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); } else { document.addEventListener('DOMContentLoaded', boot); }
}());
