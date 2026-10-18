/**
 * Challenge Optimizer (/my-challenges/optimizer/): a five-step wizard, then a plan the hunter adjusts and applies.
 *
 * THE SERVER DECIDES EVERYTHING. This file collects answers and tweaks, posts them, and draws what comes back.
 * Every re-solve is a POST to the plan door with the whole state (the SUBMITTED answers + tweaks), so there is no
 * client copy of the plan's rules to drift: a stale tab can only produce a refused line at Apply, never a bad write.
 *
 * TWEAKS, all by contract slug:
 *   pins        "Any games you want in?" (step 5)
 *   squarePins  "type|key" -> slug: a locked line, an Other options pick, or an accepted swap
 *   locks       "type|key": the square pins that lock the hunter's own pick there (sent as `lock: true`)
 *   excluded    "Not this one"
 *   penalized   "Different games" (accumulates, so each press reaches further)
 *   leftOpen    "type|key": an open square the hunter will fill themselves
 * A change that needs a re-solve goes through `change()`, which snapshots the tweaks first and rolls them back
 * if the request fails, so the tweaks never disagree with the plan on screen.
 *
 * Every DOM string is set with `textContent`. Contract names are catalogue data that passes through staff
 * hands; an escaping bug here would be an XSS on every hunter who opened the page. THE ONE EXCEPTION is
 * `serverFragment`: markup the SERVER rendered from a fixed template (auto-escaped, server-owned values),
 * inserted as a fragment. Never route anything else through it: unlike `textContent`, a fragment would run a
 * `<script>` it contained.
 */
(function () {
    'use strict';

    var PP = window.PlatPursuit || {};
    var STEPS = ['runs', 'platforms', 'source', 'badges', 'pins'];
    var TYPE_LABELS = { az: 'A-Z', jobs: 'Job Coverage' };
    var SOURCE_LABELS = { both: 'Started games first', backlog: 'Backlog only', 'new': 'New games only' };
    var UNPLACED_COPY = {
        finished: "You've already finished it.",
        gone: "It's no longer on the Job Board.",
        no_square: "It doesn't fit an open square.",
        in_run: 'It is already in the run it fits.',
        conflict: 'Other picks took the squares it fits.',
        square_closed: "That square isn't open any more.",
        left_open: "You're leaving the square it fits open."
    };
    //: The collapsed summary's word on the picks, when they are planned (step 1, owner 2026-10-18).
    var PICKS_TAGS = { replan: 'Planning around your picks', fresh: 'Starting fresh on your picks' };
    var AVAILABILITY_LABELS = { delisted: 'Delisted', unobtainable: 'Unobtainable' };
    //: Mirrors the server's ceiling on "Different games" (`optimizer_views.MAX_EXCLUDED`).
    var MAX_PENALIZED = 200;

    function reducedMotion() {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    }

    function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

    /** The showing dock's height as `--pp-cplan-dock-h`, which lifts the site's back-to-top button above it (see
     *  the docks in challenge-optimizer.css). A hidden dock measures 0, and the observer reports every change: a
     *  step swap, the wizard folding into the review, the bar's status line wrapping. No observer, no lift. */
    function watchDocks(docks) {
        if (!docks.length || !window.ResizeObserver) { return; }
        var sync = function () {
            var tallest = 0;
            docks.forEach(function (dock) { tallest = Math.max(tallest, dock.offsetHeight); });
            document.documentElement.style.setProperty('--pp-cplan-dock-h', tallest + 'px');
        };
        var observer = new ResizeObserver(sync);
        docks.forEach(function (dock) { observer.observe(dock); });
    }

    function boot() {
        var root = document.querySelector('[data-cplan]');
        if (!root || root.dataset.cplanWired || !PP.API) { return; }
        root.dataset.cplanWired = '1';

        var urls = {
            plan: root.dataset.planUrl, alt: root.dataset.altUrl,
            search: root.dataset.searchUrl, apply: root.dataset.applyUrl
        };
        var maxPins = parseInt(root.dataset.maxPins, 10) || 20;
        var maxBadges = parseInt(root.dataset.maxBadges, 10) || 60;
        var badgesUrl = root.dataset.badgesUrl;

        var els = {
            wizard: root.querySelector('[data-cplan-wizard]'),
            steps: {},
            rail: root.querySelectorAll('[data-step-jump]'),
            back: root.querySelector('[data-cplan-back]'),
            next: root.querySelector('[data-cplan-next]'),
            summary: root.querySelector('[data-cplan-summary]'),
            summaryText: root.querySelector('[data-cplan-summary-text]'),
            edit: root.querySelector('[data-cplan-edit]'),
            status: root.querySelector('[data-cplan-status]'),
            review: root.querySelector('[data-cplan-review]'),
            reviewHead: root.querySelector('[data-cplan-review-head]'),
            stats: root.querySelector('[data-cplan-stats]'),
            sections: root.querySelector('[data-cplan-sections]'),
            reroll: root.querySelector('[data-cplan-reroll]'),
            apply: root.querySelector('[data-cplan-apply]'),
            again: root.querySelector('[data-cplan-again]'),
            openTotal: root.querySelector('[data-cplan-open-total]'),
            picksQ: root.querySelector('[data-cplan-picks-q]'),
            picksHint: root.querySelector('[data-cplan-picks-hint]'),
            badgeStarted: root.querySelector('[data-cplan-badge-started]'),
            badgeAll: root.querySelector('[data-cplan-badge-all]'),
            badgeNone: root.querySelector('[data-cplan-badge-none]'),
            badgeExtras: root.querySelector('[data-cplan-badge-extras]'),
            weightHint: root.querySelector('[data-cplan-weight-hint]'),
            pickToggle: root.querySelector('[data-cplan-pick-toggle]'),
            pick: root.querySelector('[data-cplan-pick]'),
            badgeQ: root.querySelector('[data-cplan-badge-q]'),
            badgeGrid: root.querySelector('[data-cplan-badge-grid]'),
            badgeMore: root.querySelector('[data-cplan-badge-more]'),
            badgeStatus: root.querySelector('[data-cplan-badge-status]'),
            counting: root.querySelector('[data-cplan-counting]'),
            picked: root.querySelector('[data-cplan-picked]'),
            pinQ: root.querySelector('[data-cplan-pin-q]'),
            pinStatus: root.querySelector('[data-cplan-pin-status]'),
            pinned: root.querySelector('[data-cplan-pinned]'),
            pinResults: root.querySelector('[data-cplan-pin-results]')
        };
        if (!els.wizard) { return; }
        STEPS.forEach(function (key) { els.steps[key] = root.querySelector('[data-step="' + key + '"]'); });
        els.barStatus = root.querySelector('[data-cplan-bar-status]');
        watchDocks(root.querySelectorAll('[data-cplan-dock]'));

        // ── motion (the Google/Apple pass, owner 2026-10-17) ─────────────────────────────────────
        //
        // THE HOUSE VOCABULARY, nothing new: the settle-in arrival (`ppRevealIn`'s shape, a fade on an ease-out
        // under a rise on the spring flipGrid settles with, split so the spring never spikes the opacity), the
        // shared `flipGrid` glide, `countUp`, `igniteTab`, `animatePanel`, `staggerReveal`, `.pp-draw-in`. Every
        // one is a no-op under reduced motion, and an arriving element is inert (`pp-arriving`) until it lands.

        var SPRING = 'cubic-bezier(0.22, 1, 0.36, 1)';
        var FADE = 'cubic-bezier(0.2, 0.8, 0.2, 1)';

        function settleIn(el, delay) {
            if (!el || !el.animate || reducedMotion()) { return; }
            el.classList.add('pp-arriving');
            el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 360, delay: delay || 0, easing: FADE, fill: 'backwards' });
            var rise = el.animate([{ transform: 'translateY(12px) scale(0.955)' }, { transform: 'none' }],
                                  { duration: 480, delay: delay || 0, easing: SPRING, fill: 'backwards' });
            var landed = function () { el.classList.remove('pp-arriving'); };
            rise.finished.then(landed, landed);
        }

        /** A button that says what it is doing while it works ("Planning…"); returns the undo. */
        function busyLabel(btn, text) {
            if (!btn || btn.hidden) { return function () {}; }
            var was = btn.textContent;
            btn.textContent = text;
            return function () { if (btn.textContent === text) { btn.textContent = was; } };
        }

        /** The sticky bar's own line (the review's buttons live there, so its state is always in view). Visual
         *  only: `say` stays the one announcer. */
        function barSay(text) { if (els.barStatus) { els.barStatus.textContent = text || ''; } }

        /** A failure where the hunter is looking: on the review that is a toast (it announces itself), above the
         *  wizard the page's status line. */
        function sayFailure(text) {
            if (reviewing() && PP.ToastManager) { say(''); PP.ToastManager.error(text); return; }
            say(text, true);
        }

        var stepIndex = 0;
        var furthest = 0;
        var pins = [];                    // [{slug, name}]
        var tweaks = freshTweaks();
        var submitted = null;             // the answers the current plan was built from
        var plan = null;
        var seq = 0;
        var busy = false;
        // A CHANGE MADE WHILE A REQUEST IS IN FLIGHT IS QUEUED, never dropped: the reply would otherwise draw a
        // plan that disagrees with the pin list or the tweaks the hunter just set.
        var queued = null;                // {rollback, focus} for the re-plan to run when this one lands

        /** Two page-only fields, never sent: `declined`, swap halves the hunter left out with "Only C", and
         *  `choices`, {radio group name: option} for the swap cards' Both / Only switches. A REDRAW that is not a
         *  re-plan (the Letter/Job sort) rebuilds every card and must not quietly reset an "Only A" to Both; a
         *  fresh plan starts every card on Both again. */
        function freshTweaks() {
            return { squarePins: {}, locks: new Set(), excluded: new Set(), penalized: new Set(), leftOpen: new Set(),
                     declined: new Set(), choices: {} };
        }

        // ── answers ──────────────────────────────────────────────────────────────────────────────

        function checked(selector) {
            return Array.prototype.filter.call(root.querySelectorAll(selector), function (i) { return i.checked; });
        }

        function radioValue(selector, fallback) {
            var on = checked(selector)[0];
            return on ? on.value : fallback;
        }

        function answers() {
            // NONE TURNS EVERY SOURCE OFF, whatever ticks sit greyed out under it, so the request asks for no badges.
            var none = !!(els.badgeNone && els.badgeNone.checked);
            var all = !none && !!(els.badgeAll && els.badgeAll.checked);
            var typeBoxes = none ? [] : checked('[data-cplan-badge-type]');
            return {
                types: checked('[data-cplan-type]').map(function (i) { return i.value; }),
                platforms: checked('[data-cplan-platform]').map(function (i) { return i.value; }),
                platformLabels: checked('[data-cplan-platform]').map(function (i) { return i.dataset.label || i.value; }),
                source: radioValue('[data-cplan-source]', 'both'),
                badge_started: !none && !!(els.badgeStarted && els.badgeStarted.checked),
                badge_all: all,
                badge_types: all ? [] : typeBoxes.map(function (i) { return i.value; }),
                badgeTypeLabels: all ? [] : typeBoxes.map(function (i) { return i.dataset.label; }),
                badge_picks: none ? [] : Array.from(badgePicks.keys()),
                badge_weight: radioValue('[data-cplan-weight]', 'bonus'),
                // Asked only while an included run holds a pick; with none, Replan would plan the same squares.
                picks: picksAsked() ? radioValue('[data-cplan-picks]', 'keep') : 'keep'
            };
        }

        // ── games already picked (owner, 2026-10-18) ─────────────────────────────────────────────
        //
        // A square holding a game the hunter has not finished is planned only if they say so: step 1 asks while an
        // included run has one, counting from each card's `data-picked` (the server's own run counts).

        /** Unfinished squares in the runs step 1 is planning. */
        function pickedTotal() {
            var total = 0;
            root.querySelectorAll('[data-cplan-picked-for]').forEach(function (el) {
                var card = el.closest('[data-cplan-run]');
                var box = card && card.querySelector('[data-cplan-type]');
                if (box && box.checked) { total += parseInt(el.dataset.picked, 10) || 0; }
            });
            return total;
        }

        function picksAsked() { return !!els.picksQ && !els.picksQ.hidden; }

        function syncPicks() {
            if (!els.picksQ) { return; }
            var total = pickedTotal();
            els.picksQ.hidden = !total;
            if (els.picksHint) {
                els.picksHint.textContent = plural(total, 'square has', 'squares have') + " a game you haven't finished.";
            }
        }

        /** Are the picks in the plan on screen ("Plan around them" or "Start fresh")? */
        function replanning() { return !!submitted && !!PICKS_TAGS[submitted.picks]; }

        function payload() {
            var a = submitted;
            return {
                types: a.types, platforms: a.platforms, source: a.source,
                badge_started: a.badge_started, badge_all: a.badge_all, badge_types: a.badge_types,
                badge_picks: a.badge_picks, badge_weight: a.badge_weight, picks: a.picks,
                tweaks: {
                    pins: pins.map(function (p) { return p.slug; }),
                    square_pins: Object.keys(tweaks.squarePins).map(function (sq) {
                        var parts = sq.split('|');
                        var entry = { type: parts[0], key: parts[1], contract: tweaks.squarePins[sq] };
                        if (tweaks.locks.has(sq)) { entry.lock = true; }
                        return entry;
                    }),
                    excluded: Array.from(tweaks.excluded),
                    penalized: Array.from(tweaks.penalized),
                    left_open: Array.from(tweaks.leftOpen).map(function (sq) {
                        var parts = sq.split('|');
                        return { type: parts[0], key: parts[1] };
                    })
                }
            };
        }

        function summaryLine(a) {
            var parts = [a.types.map(function (t) { return TYPE_LABELS[t] || t; }).join(' + ')];
            parts.push(a.platformLabels.length ? a.platformLabels.join(', ') : 'Any platform');
            parts.push(SOURCE_LABELS[a.source]);
            parts.push(badgeLine(a));
            if (PICKS_TAGS[a.picks]) { parts.push(PICKS_TAGS[a.picks]); }
            if (pins.length) { parts.push(plural(pins.length, 'must-have', 'must-haves')); }
            return parts.join(' · ');
        }

        // ── wizard ───────────────────────────────────────────────────────────────────────────────

        function goTo(index) {
            var from = STEPS[stepIndex];
            // A STEP CHANGE SETTLES THE BADGE PANEL: leaving mid-tween hides the step, the panel's transition never
            // ends, and it would keep an inline height (measured before its badges loaded) and clip from then on.
            if (els.pick && PP.animatePanel) { PP.animatePanel(els.pick, !els.pick.hidden, false); }
            stepIndex = Math.max(0, Math.min(index, STEPS.length - 1));
            furthest = Math.max(furthest, stepIndex);
            var to = STEPS[stepIndex];
            STEPS.forEach(function (key) { if (els.steps[key]) { els.steps[key].hidden = key !== to; } });
            if (PP.slideViewIn && from !== to) { PP.slideViewIn(els.steps[to], from, to, STEPS); }
            syncRail();
            els.back.hidden = stepIndex === 0;
            els.next.textContent = stepIndex === STEPS.length - 1 ? 'Plan it' : 'Next';
            var heading = els.steps[to] && els.steps[to].querySelector('.pp-cplan__q');
            if (heading && from !== to) { heading.setAttribute('tabindex', '-1'); heading.focus({ preventScroll: true }); }
        }

        function syncRail() {
            Array.prototype.forEach.call(els.rail, function (btn, i) {
                btn.disabled = busy || i > furthest;
                btn.classList.toggle('is-current', i === stepIndex);
                btn.classList.toggle('is-done', i < furthest && i !== stepIndex);
                if (i === stepIndex) { btn.setAttribute('aria-current', 'step'); } else { btn.removeAttribute('aria-current'); }
            });
        }

        function typesChosen() {
            if (answers().types.length) { return true; }
            var anyRun = root.querySelector('[data-cplan-type]');
            var anyDoor = root.querySelector('.pp-cplan__opt-form');
            say(anyRun ? 'Pick at least one challenge to plan.'
                : anyDoor ? 'Start a challenge above to plan it.' : 'There is nothing to plan yet.', true);
            return false;
        }

        els.next.addEventListener('click', function () {
            if (busy) { return; }
            if (STEPS[stepIndex] === 'runs' && !typesChosen()) { return; }
            say('');
            if (stepIndex === STEPS.length - 1) {
                // The rail can reach this step without passing step 1's check, so it is asked again here.
                if (!typesChosen()) { goTo(0); return; }
                startPlanning();
                return;
            }
            goTo(stepIndex + 1);
        });
        els.back.addEventListener('click', function () { if (!busy) { goTo(stepIndex - 1); } });
        Array.prototype.forEach.call(els.rail, function (btn) {
            btn.addEventListener('click', function () { if (!busy) { goTo(STEPS.indexOf(btn.dataset.stepJump)); } });
        });

        if (els.edit) {
            els.edit.addEventListener('click', function () {
                if (busy) { return; }
                // THE OLD PLAN GOES AWAY WHILE ITS ANSWERS ARE BEING CHANGED, so nothing on screen can be
                // re-planned or applied against answers the hunter has not submitted.
                els.review.hidden = true;
                showResume(!!saved);
                els.summary.hidden = true;
                els.wizard.hidden = false;
                goTo(0);
                els.wizard.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' });
            });
        }

        // ── badges step ──────────────────────────────────────────────────────────────────────────
        //
        // THE SOURCES ARE A UNION: started editions, whole types, everything, and hand picks, in any mix. The
        // server resolves them; this only collects them and says back what counts.

        var badgePicks = new Map();      // GroupBadge id -> "Series (Edition)"
        var WEIGHT_HINTS = {
            bonus: 'Badges break ties. Your challenges come first.',
            priority: 'Badge games fill as many squares as they can, even if that takes an extra game. Filling squares still comes first.',
            only: "Only games that clear a badge stage. A square no badge game fits stays open for you to fill."
        };
        var WEIGHT_TAGS = { priority: ' (priority)', only: ' (badges only)' };

        /** One line for what counts: the step's own read-back and the collapsed summary's badge part. */
        function badgeLine(a) {
            var bits = [];
            if (a.badge_all) { bits.push('All badges'); }
            else {
                if (a.badge_started) { bits.push("Badges you've started"); }
                bits = bits.concat(a.badgeTypeLabels || []);
            }
            if (a.badge_picks.length) { bits.push(plural(a.badge_picks.length, 'picked badge', 'picked badges')); }
            if (!bits.length) { return 'No badges'; }
            return bits.join(', ') + (WEIGHT_TAGS[a.badge_weight] || '');
        }

        function syncBadgeStep() {
            var none = !!(els.badgeNone && els.badgeNone.checked);
            var all = !none && !!(els.badgeAll && els.badgeAll.checked);
            // "All badges" covers every type AND the started ones, so those chips step back rather than pretend to
            // choose anything; None covers everything, "All" included. Their ticks are KEPT (disabled, not cleared),
            // so turning "All" or None off restores them.
            var cover = function (selector, on) {
                root.querySelectorAll(selector).forEach(function (chip) {
                    chip.classList.toggle('is-covered', on);
                    chip.querySelector('input').disabled = on;
                });
            };
            cover('[data-cplan-type-chip], [data-cplan-started-chip]', none || all);
            cover('[data-cplan-all-chip]', none);
            // The weight, the picker and the picked chips mean nothing under None: greyed AND inert, so nothing in
            // them can be operated (or reached by Tab) while it does not count.
            [els.badgeExtras, els.picked].forEach(function (el) {
                if (!el) { return; }
                el.classList.toggle('is-covered', none);
                if (none) { el.setAttribute('inert', ''); } else { el.removeAttribute('inert'); }
            });
            if (els.weightHint) { els.weightHint.textContent = WEIGHT_HINTS[radioValue('[data-cplan-weight]', 'bonus')]; }
            if (els.counting) {
                var line = badgeLine(answers());
                els.counting.textContent = line === 'No badges' ? "Badges won't count this time." : 'Counting: ' + line + '.';
            }
            renderPicked();
        }

        /** Neighbours glide when a chip is added or removed (`flipGrid`); a new chip settles in. */
        function chipFlip(list) {
            return PP.flipGrid && list ? PP.flipGrid({
                container: list, itemSelector: '.pp-cplan__pin', enter: true, mark: false, duration: 320,
                key: function (el) { return el.dataset.key || null; }
            }) : null;
        }
        var pickFlip = chipFlip(els.picked);

        function renderPicked() {
            if (!els.picked) { return; }
            if (pickFlip) { pickFlip.measure(); }
            els.picked.textContent = '';
            badgePicks.forEach(function (name, id) {
                var li = document.createElement('li');
                li.className = 'pp-cplan__pin';
                li.dataset.key = String(id);
                var label = document.createElement('span');
                label.textContent = name;
                li.appendChild(label);
                var remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'pp-cplan__pin-x';
                remove.setAttribute('aria-label', 'Remove ' + name);
                remove.textContent = '×';
                remove.addEventListener('click', function () {
                    togglePick(id, name, false);
                    // The list was just rebuilt: focus the next chip's remove button, or the counting line.
                    var next = els.picked.querySelectorAll('.pp-cplan__pin-x')[0];
                    if (next) { next.focus(); }
                    else if (els.counting) { els.counting.setAttribute('tabindex', '-1'); els.counting.focus(); }
                });
                li.appendChild(remove);
                els.picked.appendChild(li);
            });
            if (pickFlip) { pickFlip.play(); }
        }

        /** Add or drop one hand pick, keeping every card for that badge in step (a card may be on screen twice). */
        function togglePick(id, name, on) {
            if (on && !badgePicks.has(id) && badgePicks.size >= maxBadges) {
                badgeSay('That is the most badges one plan can pick by hand. "All badges" or a whole type covers more.', true);
                return;
            }
            if (on) { badgePicks.set(id, name); } else { badgePicks.delete(id); }
            root.querySelectorAll('[data-cplan-badge="' + id + '"]').forEach(function (btn) {
                btn.setAttribute('aria-pressed', on ? 'true' : 'false');
                // The check TRACES itself in on a pick (`.pp-draw-in`, restarted each time).
                var check = on && btn.closest('.pp-cplan__badge') && btn.closest('.pp-cplan__badge').querySelector('.pp-cplan__badge-check');
                if (check && !reducedMotion()) { check.classList.remove('pp-draw-in'); void check.offsetWidth; check.classList.add('pp-draw-in'); }
            });
            badgeSay('');
            syncBadgeStep();
        }

        function badgeSay(text, isError) {
            if (!els.badgeStatus) { return; }
            els.badgeStatus.textContent = text || '';
            els.badgeStatus.classList.toggle('pp-cplan__status--error', !!isError);
        }

        root.querySelectorAll('[data-cplan-badge-none], [data-cplan-badge-started], [data-cplan-badge-all], [data-cplan-badge-type], [data-cplan-weight]')
            .forEach(function (input) { input.addEventListener('change', syncBadgeStep); });

        // ── the badge gallery ────────────────────────────────────────────────────────────────────

        var badgeSeq = 0;
        var badgePage = 0;
        var galleryOpened = false;
        var badgeReveal = null;

        /** The gallery's cards arrive on the house browse-grid grammar (`staggerReveal`, as Browse Challenges). */
        function revealBadges() {
            if (!els.badgeGrid) { return; }
            if (badgeReveal) { badgeReveal.disconnect(); badgeReveal = null; }
            if (PP.staggerReveal) {
                badgeReveal = PP.staggerReveal({
                    grid: els.badgeGrid, cardSelector: '.pp-cplan__badge', step: 22,
                    reveal: function (el, delayMs) {
                        if (!el.animate) { return; }
                        el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 420, delay: delayMs, easing: FADE, fill: 'backwards' });
                        el.animate([{ transform: 'translateY(14px) scale(0.965)' }, { transform: 'none' }],
                                   { duration: 500, delay: delayMs, easing: SPRING, fill: 'backwards' });
                    }
                });
            }
            // NO OBSERVER, NO HIDE: a grid left wearing `pp-reveal` with nothing to reveal it stays blank.
            if (!badgeReveal) { els.badgeGrid.classList.remove('pp-reveal'); }
        }

        /** First open only: tick the editions on step 2's platforms (owner, 2026-10-16), so a PS5 hunter starts on
         *  Ultra HD. Later changes are the hunter's own and are left alone. No platforms means every edition. */
        function seedEditionFilter() {
            var plats = answers().platforms;
            root.querySelectorAll('[data-cplan-badge-group]').forEach(function (box) {
                var groupPlats = (box.dataset.platforms || '').split(',');
                box.checked = !plats.length || groupPlats.some(function (p) { return plats.indexOf(p) !== -1; });
            });
        }

        function loadBadges(reset) {
            if (!badgesUrl || !els.badgeGrid) { return; }
            var mine = ++badgeSeq;
            var page = reset ? 1 : badgePage + 1;
            var params = new URLSearchParams({ q: els.badgeQ ? els.badgeQ.value.trim() : '', page: String(page) });
            checked('[data-cplan-badge-group]').forEach(function (i) { params.append('group', i.value); });
            checked('[data-cplan-badge-filter]').forEach(function (i) { params.append('type', i.value); });
            badgeSay('Loading badges…');
            PP.API.get(badgesUrl + '?' + params.toString()).then(function (data) {
                if (mine !== badgeSeq) { return; }
                if (reset) { els.badgeGrid.textContent = ''; }
                badgePage = data.page;
                els.badgeGrid.appendChild(serverFragment(data.html));
                els.badgeGrid.querySelectorAll('[data-cplan-badge]').forEach(function (btn) {
                    btn.setAttribute('aria-pressed', badgePicks.has(parseInt(btn.dataset.cplanBadge, 10)) ? 'true' : 'false');
                });
                if (els.badgeMore) { els.badgeMore.hidden = !data.more; }
                revealBadges();
                badgeSay(data.total ? plural(data.total, 'badge', 'badges') + ' match.' : 'No badges match those filters.');
            }).catch(function (err) {
                if (mine !== badgeSeq) { return; }
                failureText(err, 'Badges did not load.').then(function (msg) { badgeSay(msg, true); });
            });
        }

        if (els.pickToggle && els.pick) {
            els.pickToggle.addEventListener('click', function () {
                var open = els.pick.hidden;
                if (PP.animatePanel) { PP.animatePanel(els.pick, open); } else { els.pick.hidden = !open; }
                els.pickToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
                els.pickToggle.textContent = open ? 'Hide badges' : 'Browse badges';
                if (open && !galleryOpened) {
                    galleryOpened = true;
                    seedEditionFilter();
                    loadBadges(true);
                }
            });
        }
        if (els.badgeQ) {
            var badgeSearch = PP.debounce ? PP.debounce(function () { loadBadges(true); }, 250) : function () { loadBadges(true); };
            els.badgeQ.addEventListener('input', badgeSearch);
        }
        root.querySelectorAll('[data-cplan-badge-group], [data-cplan-badge-filter]').forEach(function (box) {
            box.addEventListener('change', function () { loadBadges(true); });
        });
        if (els.badgeMore) {
            els.badgeMore.querySelector('button').addEventListener('click', function () { loadBadges(false); });
        }
        if (els.badgeGrid) {
            // DELEGATED: every page of cards arrives later, from the server.
            els.badgeGrid.addEventListener('click', function (e) {
                var btn = e.target.closest ? e.target.closest('[data-cplan-badge]') : null;
                if (!btn) { return; }
                togglePick(parseInt(btn.dataset.cplanBadge, 10), btn.dataset.name, btn.getAttribute('aria-pressed') !== 'true');
            });
        }
        syncBadgeStep();
        syncPicks();

        // ── must-have search ─────────────────────────────────────────────────────────────────────

        var searchSeq = 0;
        var typedToken = 0;

        var pinFlip = chipFlip(els.pinned);

        function renderPinned() {
            if (pinFlip) { pinFlip.measure(); }
            els.pinned.textContent = '';
            pins.forEach(function (pin) {
                var li = document.createElement('li');
                li.className = 'pp-cplan__pin';
                li.dataset.key = pin.slug;
                var name = document.createElement('span');
                name.textContent = pin.name;
                li.appendChild(name);
                var remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'pp-cplan__pin-x';
                remove.setAttribute('aria-label', 'Remove ' + pin.name);
                remove.textContent = '×';
                remove.addEventListener('click', function () {
                    if (busy) { pinSay('One moment, the plan is still loading.'); return; }
                    if (reviewing()) {
                        change(function () { pins = pins.filter(function (p) { return p.slug !== pin.slug; }); });
                    } else {
                        pins = pins.filter(function (p) { return p.slug !== pin.slug; });
                    }
                    renderPinned();
                });
                li.appendChild(remove);
                els.pinned.appendChild(li);
            });
            if (pinFlip) { pinFlip.play(); }
        }

        function fitsLine(row) {
            var bits = [];
            Object.keys(row.fits || {}).forEach(function (type) {
                // A run that already holds the game offers no square there ("already in the run" otherwise).
                if ((row.in_run || {})[type]) { return; }
                (row.fits[type] || []).forEach(function (f) { bits.push(f.label); });
            });
            return bits;
        }

        /** Is a plan on screen to re-solve? NOT just `plan`: during Edit answers the old plan still exists but
         *  its answers are being changed, and re-solving it would plan against the answers being replaced. */
        function reviewing() { return !!plan && !els.review.hidden; }

        function addPin(row) {
            if (busy) { pinSay('One moment, the plan is still loading.'); return; }
            if (pins.some(function (p) { return p.slug === row.slug; })) { pinSay(row.name + ' is already on your list.'); return; }
            if (pins.length >= maxPins) { pinSay('That is the most must-haves one plan can take.', true); return; }
            var add = function () {
                pins.push({ slug: row.slug, name: row.name });
                tweaks.excluded.delete(row.slug);
            };
            if (reviewing()) { change(add); } else { add(); }
            renderPinned();
            pinSay(row.name + ' is in.');
        }

        function pinSay(text, isError) {
            if (!els.pinStatus) { return; }
            els.pinStatus.textContent = text || '';
            els.pinStatus.classList.toggle('pp-cplan__status--error', !!isError);
        }

        function clearSearch() {
            searchSeq++;
            typedToken++;
            els.pinResults.textContent = '';
        }

        function search(term) {
            var types = answers().types;
            if (!types.length) { pinSay('Pick a challenge on step 1 first.'); return; }
            var mine = ++searchSeq;
            var params = new URLSearchParams({ q: term });
            types.forEach(function (t) { params.append('type', t); });
            pinSay('Searching…');
            PP.API.get(urls.search + '?' + params.toString()).then(function (data) {
                if (mine !== searchSeq) { return; }
                els.pinResults.textContent = '';
                if (data.no_runs) { pinSay('Pick a challenge on step 1 first.'); return; }
                if (data.too_short) { pinSay('Type at least two letters.'); return; }
                if (!data.rows.length) { pinSay('Nothing on the Job Board matches that.'); return; }
                pinSay('');
                data.rows.forEach(function (row) {
                    var fits = fitsLine(row);
                    var blocked = row.is_completed_by_you || !fits.length;
                    var note = row.is_completed_by_you ? "You've finished this one"
                        : fits.length ? 'Fits ' + fits.join(', ') : 'Fits no open square';
                    els.pinResults.appendChild(offerRow(row, note, blocked ? null : function () { addPin(row); }));
                });
            }).catch(function (err) {
                if (mine !== searchSeq) { return; }
                failureText(err, 'Search did not load.').then(function (msg) { pinSay(msg, true); });
            });
        }

        if (els.pinQ) {
            var runSearch = function (token) {
                // A KEYSTROKE FROM BEFORE THE BOX WAS CLEARED IS STALE (the picker's own guard).
                if (token !== typedToken) { return; }
                search(els.pinQ.value.trim());
            };
            var debounced = PP.debounce ? PP.debounce(runSearch, 250) : runSearch;
            els.pinQ.addEventListener('input', function () {
                if (!els.pinQ.value.trim()) { clearSearch(); pinSay(''); return; }
                debounced(typedToken);
            });
        }
        // The results say which squares a game fits, so they go stale when step 1's runs change.
        // DELEGATED, so a run card swapped in by Start (below) is wired without a second binding.
        if (els.steps.runs) {
            els.steps.runs.addEventListener('change', function (e) {
                if (e.target.matches && e.target.matches('[data-cplan-type]')) { clearSearch(); syncPicks(); }
            });
        }

        // ── starting a run in place ──────────────────────────────────────────────────────────────

        /** Start (or Resume) from step 1 without a reload. The form still posts to `challenge_start` without
         *  JavaScript; here it posts to its `data-start-url` and the SERVER-RENDERED card in the reply (the same
         *  partial the page drew) replaces the Start card, through `serverFragment`, like the badge cards. */
        function startInPlace(form) {
            var button = form.querySelector('button[type="submit"]');
            if (!button || button.disabled) { return; }
            var label = button.textContent;
            button.disabled = true;
            button.textContent = 'Starting…';
            PP.API.post(form.dataset.startUrl, {}).then(function (data) {
                var fresh = swapCard(data.type, data.html);
                // FOCUS FOLLOWS THE CARD: its toggle, else its heading, so it never falls to the page body.
                var target = fresh && (fresh.querySelector('[data-cplan-type]') || fresh.querySelector('h3'));
                if (target) {
                    if (target.tagName === 'H3') { target.setAttribute('tabindex', '-1'); }
                    target.focus();
                }
                // A new run changes which squares a must-have search result fits; its box arrives checked
                // without a `change` event, so the delegated listener would not clear them.
                clearSearch();
                syncRunCopy();
                syncPicks();
                recountOpen(true);
                settleIn(fresh);
                say('');
                if (data.message && PP.ToastManager) { PP.ToastManager.success(data.message); }
            }).catch(function (err) {
                button.disabled = false;
                button.textContent = label;
                failureText(err, "That didn't start. Try again in a moment.").then(function (msg) { sayFailure(msg); });
            });
        }

        /** Replace one step-1 card with the server's re-render of it; returns the new card. */
        function swapCard(type, html) {
            var card = root.querySelector('[data-cplan-run="' + type + '"]');
            if (!card || !html) { return card; }
            card.replaceWith(serverFragment(html));
            return root.querySelector('[data-cplan-run="' + type + '"]');
        }

        /** THE ONLY PLACE this file turns HTML into nodes, and it only ever receives markup the SERVER rendered from
         *  a fixed template (run cards, badge cards). See the file header for why nothing else may route through it. */
        function serverFragment(html) {
            return document.createRange().createContextualFragment(html || '');
        }

        /** The tagline and step 1's hint, by how many runs there are and whether another can be started. The
         *  same three cases the template draws on first paint. */
        function syncRunCopy() {
            var runs = root.querySelectorAll('[data-cplan-type]').length;
            if (!runs) { return; }
            var door = root.querySelector('[data-cplan-start]');
            var hint = root.querySelector('[data-cplan-runs-hint]');
            if (hint) {
                hint.textContent = runs > 1 ? 'Plan both and the optimizer hunts for games that count on each board.'
                    : door ? 'Start the other and the optimizer hunts for games that count on both boards.'
                    : 'Plan it and the optimizer lines up a game for every open square.';
            }
            var tagline = root.querySelector('[data-cplan-tagline]');
            if (tagline) {
                tagline.textContent = runs > 1 ? 'Make every game count twice: one optimized plan for both challenges.'
                                               : 'The best game for every open square, on your terms.';
            }
        }

        if (els.steps.runs) {
            els.steps.runs.addEventListener('submit', function (e) {
                var form = e.target.closest ? e.target.closest('[data-cplan-start]') : null;
                if (!form || !form.dataset.startUrl) { return; }
                e.preventDefault();
                startInPlace(form);
            });
        }

        // ── planning ─────────────────────────────────────────────────────────────────────────────

        function snapshot() {
            return { pins: pins.slice(), squarePins: Object.assign({}, tweaks.squarePins), locks: new Set(tweaks.locks),
                     excluded: new Set(tweaks.excluded), penalized: new Set(tweaks.penalized),
                     leftOpen: new Set(tweaks.leftOpen), declined: new Set(tweaks.declined) };
        }

        function restore(s) {
            pins = s.pins;
            tweaks.squarePins = s.squarePins;
            tweaks.locks = s.locks;
            tweaks.excluded = s.excluded;
            tweaks.penalized = s.penalized;
            tweaks.leftOpen = s.leftOpen;
            tweaks.declined = s.declined;
            renderPinned();
        }

        /** Pin one game to these squares, first clearing any pin it already holds elsewhere in THEIR runs: the
         *  solver keeps one square pin per game per run, so a second would leave a square empty and report the
         *  game as unplaced while it sits on the plan. Picking a game that is locked on another line moves it. */
        function pinGame(slug, squares) {
            var runs = squares.map(function (s) { return s.type; });
            Object.keys(tweaks.squarePins).forEach(function (k) {
                if (tweaks.squarePins[k] === slug && runs.indexOf(k.split('|')[0]) >= 0) {
                    delete tweaks.squarePins[k];
                    tweaks.locks.delete(k);
                }
            });
            squares.forEach(function (s) { tweaks.squarePins[sqKey(s)] = slug; tweaks.locks.delete(sqKey(s)); });
        }

        /** LEAVE A SQUARE OPEN (owner, 2026-10-17): the hunter will fill it themselves, so the plan stops filling it
         *  and Apply never touches it. Beats a lock on that square, which is dropped with it. */
        function leaveOpen(sq) {
            tweaks.leftOpen.add(sqKey(sq));
            delete tweaks.squarePins[sqKey(sq)];
            tweaks.locks.delete(sqKey(sq));
        }

        /** KEEP A PICK (picks planned, owner 2026-10-18): its own game locked back onto its square, which the server
         *  holds through re-solves. `r` is a line's `replaces` entry, carrying the pick's live slug. The latest word
         *  wins, so it also undoes an earlier "Not this one" on that game. */
        function keepPick(r) { tweaks.excluded.delete(r.slug); pinGame(r.slug, [r]); tweaks.locks.add(sqKey(r)); }

        /** Apply a tweak and re-solve; the tweak is rolled back if the re-solve fails. */
        function change(mutate, focus) {
            var before = snapshot();
            mutate();
            replan({ rollback: function () { restore(before); }, focus: focus });
        }

        function startPlanning(restored) {
            // NEW ANSWERS START A FRESH SET OF TWEAKS. Locks and exclusions were made against the last plan's
            // games; must-haves are the hunter's own list and carry over. Open my plan passes the saved plan's own
            // (`restored`, a function, run only once the lock check has passed), so it comes back as it was left.
            var next = answers();
            // THE WEEKLY ALLOWANCE (free hunters): say plainly when the answers are not the ones this week's plan
            // was spent on, BEFORE touching anything (the plan on screen and its tweaks stay as they were), and ask
            // before the week's plan is spent. The server enforces both regardless.
            if (allowanceState === 'in_use' && saved && answersKey(next) !== answersKey(saved)) {
                say(lockedLine(), true);
                return;
            }
            submitted = next;
            tweaks = (restored && restored()) || freshTweaks();
            if (allowanceState === 'available') { openSpend(); return; }
            replan({ fromWizard: true });
        }

        // ── the weekly allowance ─────────────────────────────────────────────────────────────────

        var allowanceState = root.dataset.allowance || 'unlimited';   // unlimited | available | in_use
        // Each date is its server text AND its moment, so it can be shown on the hunter's own clock.
        var allowanceNext = { at: root.dataset.allowanceNextAt || '', label: root.dataset.allowanceNext || '' };
        var allowanceIfNow = { at: root.dataset.allowanceIfNowAt || '', label: root.dataset.allowanceIfNow || '' };

        // ── dates ───────────────────────────────────────────────────────────────────────────────
        //
        // ON THE HUNTER'S CLOCK (owner, 2026-10-17): their saved zone when they ever chose one (`data-when-tz`),
        // else the device's own; the server's text is the fallback. The page's server-rendered dates are
        // re-rendered once here; the ones this script writes go through `whenNode` / `whenText`.

        if (PP.TimeFormatter && PP.TimeFormatter.localizeWhen) { PP.TimeFormatter.localizeWhen(document); }

        function whenText(w) {
            var text = w && w.at && PP.TimeFormatter && PP.TimeFormatter.when
                ? PP.TimeFormatter.when(w.at, { timeZone: root.dataset.whenTz || null, hour24: root.dataset.whenClock === '24' })
                : '';
            return text || (w && w.label) || '';
        }

        /** A highlighted date, like `partials/_when.html` renders one. */
        function whenNode(w) {
            var el = document.createElement('time');
            el.className = 'pp-when';
            if (w && w.at) { el.setAttribute('datetime', w.at); }
            el.textContent = whenText(w);
            return el;
        }

        /** The next-free date a reply carries, or null. */
        function nextFrom(reply) {
            return reply && reply.next_free_label ? { at: reply.next_free_at || '', label: reply.next_free_label } : null;
        }
        // WHAT OPEN MY PLAN REOPENS (owner, 2026-10-16): the autosaved plan's answers and tweaks, for everyone. For
        // a free hunter whose week is in use, `saved` is also the answers the week was spent on (the lock check).
        var savedPlan = readSaved();
        var saved = savedPlan ? savedPlan.answers : null;
        var savedTweaks = savedPlan ? savedPlan.tweaks : null;
        var spendDialog = document.getElementById('cplan-spend');
        var resumeCard = root.querySelector('[data-cplan-resume]');

        function readSaved() {
            var el = document.getElementById('cplan-saved');
            if (!el) { return null; }
            try {
                var data = JSON.parse(el.textContent);
                return data && data.answers ? data : null;
            } catch (e) { return null; }
        }

        /** Open my plan is offered while the wizard is up, never beside the plan it would reopen: the members' card
         *  and the free banner's button alike. */
        function showResume(on) {
            if (resumeCard) { resumeCard.hidden = !on; }
            var bannerOpen = root.querySelector('[data-cplan-allowance] [data-cplan-open-saved]');
            if (bannerOpen) { bannerOpen.hidden = !(on && saved); }
        }

        /** The page's copy of the server's autosave, kept in step after every plan this tab builds. */
        function rememberPlan(a, sentTweaks) {
            saved = withPickNames(a);
            savedTweaks = sentTweaks;
            var text = root.querySelector('[data-cplan-resume-text]');
            // A team preview saves nothing, so it never claims to have.
            if (text && !root.dataset.preview) {
                text.textContent = 'Your last plan is saved with its locks, swaps and must-haves. Saved ';
                var now = document.createElement('span');
                now.className = 'pp-when';
                now.textContent = 'just now';
                text.appendChild(now);
                text.appendChild(document.createTextNode('.'));
            }
        }

        /** Applied games are on the board now: they leave the saved must-haves and locks, as on the server. */
        function forgetApplied(results) {
            if (!savedTweaks) { return; }
            var done = results.filter(function (r) { return r.status === 'applied'; });
            var placed = new Set(done.map(function (r) { return r.contract; }));
            var written = new Set(done.map(function (r) { return r.type + '|' + r.key; }));
            savedTweaks = Object.assign({}, savedTweaks, {
                pins: (savedTweaks.pins || []).filter(function (p) { return !placed.has(p.slug); }),
                // Only the squares written: the same game locked as a pick elsewhere keeps its lock.
                square_pins: (savedTweaks.square_pins || []).filter(function (e) { return !written.has(e.type + '|' + e.key); })
            });
        }

        /** Saved tweaks back into the page: must-haves straight into the list, the rest returned for
         *  `startPlanning`. Null when there are none to restore. */
        function restoreTweaks(t) {
            if (!t) { return null; }
            pins = (t.pins || []).slice(0, maxPins).map(function (p) { return { slug: p.slug, name: p.name }; });
            renderPinned();
            var squarePins = {};
            var locks = new Set();
            (t.square_pins || []).forEach(function (e) {
                squarePins[e.type + '|' + e.key] = e.contract;
                if (e.lock === true) { locks.add(e.type + '|' + e.key); }
            });
            var leftOpen = new Set((t.left_open || []).map(function (e) { return e.type + '|' + e.key; }));
            return { squarePins: squarePins, locks: locks, excluded: new Set(t.excluded || []), penalized: new Set(t.penalized || []),
                     leftOpen: leftOpen, declined: new Set(), choices: {} };
        }

        function sortedStrings(list) { return (list || []).map(String).sort(); }

        /** The values a set of inputs on this page can hold right now (the challenges with a running run, the
         *  badge types with live badges). */
        function present(selector) {
            return Array.prototype.map.call(root.querySelectorAll(selector), function (i) { return i.value; });
        }

        /** "Is this the same plan?" in the server's own terms (`allowance.same_plan`): the weight and the picks
         *  choice are not part of it,
         *  "All badges" covers started and the types, and both sides are judged against what EXISTS now: a run that
         *  finished mid-week, or a badge type gone dormant, must not lock a hunter out of the plan they spent. */
        function answersKey(a) {
            var runs = present('[data-cplan-type]');
            var types = present('[data-cplan-badge-type]');
            var inside = function (list, allowed) { return sortedStrings(list).filter(function (v) { return allowed.indexOf(v) !== -1; }); };
            return JSON.stringify([inside(a.types, runs), sortedStrings(a.platforms), a.source,
                                   a.badge_all ? false : !!a.badge_started, !!a.badge_all,
                                   a.badge_all ? [] : inside(a.badge_types, types), sortedStrings(a.badge_picks)]);
        }

        function lockedLine() {
            return "This week's free plan uses different answers. Press Open my plan to go back to it"
                + (allowanceNext.label ? ', or change answers when your next free plan is ready ' + whenText(allowanceNext) + '.' : '.');
        }

        function openSpend(next) {
            if (!spendDialog || !spendDialog.showModal) {
                // A member whose membership lapsed while this page was open: the page has no dialog to ask with.
                say('This would use your free optimized plan for the week. Reload the page to choose.', true);
                return;
            }
            say('');
            if (next) {
                var until = spendDialog.querySelector('[data-cplan-spend-until]');
                if (until) { until.textContent = ''; until.appendChild(whenNode(next)); }
            }
            spendDialog.showModal();
            // "NOT YET" FIRST: the safe choice takes the focus, and the dialog's description (its date and facts)
            // is read before either button.
            var cancel = spendDialog.querySelector('.gl-dialog__foot [data-cplan-spend-cancel]');
            if (cancel) { cancel.focus(); }
        }

        if (spendDialog) {
            // EVERY CLOSE PLAYS THE EXIT the dialog's CSS already has (`.gl-dialog.is-closing`), Escape included.
            wireDialogClose(spendDialog);
            spendDialog.querySelectorAll('[data-cplan-spend-cancel]').forEach(function (btn) {
                btn.addEventListener('click', function () { closeDialog(spendDialog); });
            });
            spendDialog.addEventListener('cancel', function (e) { e.preventDefault(); closeDialog(spendDialog); });
            // A press on the backdrop lands on the dialog element itself: the same "no" as Not yet.
            spendDialog.addEventListener('click', function (e) { if (e.target === spendDialog) { closeDialog(spendDialog); } });
            spendDialog.querySelector('[data-cplan-spend-confirm]').addEventListener('click', function () {
                // AFTER the exit: the page is inert while the modal is up, so the plan's status would be lost.
                closeDialog(spendDialog, function () { replan({ fromWizard: true, useFreePlan: true }); });
            });
        }

        /** The free plan is now this week's: remember its answers (with picked badges' names, for the chips) and
         *  turn the banner into "your plan this week". */
        function markSpent(a, next) {
            showInUse(withPickNames(a), next || allowanceIfNow);
        }

        function withPickNames(a) {
            var names = {};
            badgePicks.forEach(function (name, id) { names[String(id)] = name; });
            return Object.assign({}, a, { badge_pick_names: names });
        }

        /** This week's plan is `answers`, with the next free one at `next` ({at, label}): remember it and say so in
         *  the banner. Called after this tab's own spend, and when the server says another tab already made one. */
        function showInUse(answersSaved, next) {
            allowanceState = 'in_use';
            if (answersSaved) {
                // Another tab's plan: the tweaks kept here belong to a different one.
                if (!saved || answersKey(answersSaved) !== answersKey(saved)) { savedTweaks = null; }
                saved = answersSaved;
            }
            if (next && next.label) { allowanceNext = next; }
            var title = root.querySelector('[data-cplan-allowance-title]');
            var text = root.querySelector('[data-cplan-allowance-text]');
            var open = root.querySelector('[data-cplan-open-saved]');
            if (title) { title.textContent = 'Your optimized plan this week'; }
            if (text) {
                text.textContent = 'Tweak, reroll and apply it as much as you like.';
                if (allowanceNext.label) {
                    text.appendChild(document.createTextNode(' Your next free plan is ready '));
                    text.appendChild(whenNode(allowanceNext));
                    text.appendChild(document.createTextNode('.'));
                }
            }
            if (open) { open.hidden = !saved; }   // the banner's own button (members have no banner)
        }

        /** Put saved answers back into the wizard, so "Open my plan" re-plans exactly this week's plan. */
        function applyAnswers(a) {
            var setChecked = function (selector, values) {
                var want = sortedStrings(values);
                root.querySelectorAll(selector).forEach(function (input) { input.checked = want.indexOf(input.value) !== -1; });
            };
            setChecked('[data-cplan-type]', a.types);
            setChecked('[data-cplan-platform]', a.platforms);
            setChecked('[data-cplan-source]', [a.source]);
            setChecked('[data-cplan-weight]', [a.badge_weight || 'bonus']);
            setChecked('[data-cplan-picks]', [a.picks || 'keep']);
            // The runs were re-ticked without a `change` event: show the question for them before `answers()` reads
            // it, or a saved Plan around would be sent as Keep (and its locks would mean nothing).
            syncPicks();
            setChecked('[data-cplan-badge-type]', a.badge_types);
            if (els.badgeStarted) { els.badgeStarted.checked = !!a.badge_started; }
            if (els.badgeAll) { els.badgeAll.checked = !!a.badge_all; }
            // Saved answers with no source at all ARE "None": say so, rather than leave an empty step.
            if (els.badgeNone) {
                els.badgeNone.checked = !a.badge_started && !a.badge_all && !(a.badge_types || []).length
                    && !(a.badge_picks || []).length;
            }
            badgePicks.clear();
            var names = a.badge_pick_names || {};
            (a.badge_picks || []).forEach(function (id) { badgePicks.set(id, names[String(id)] || 'A picked badge'); });
            root.querySelectorAll('[data-cplan-badge]').forEach(function (btn) {
                btn.setAttribute('aria-pressed', badgePicks.has(parseInt(btn.dataset.cplanBadge, 10)) ? 'true' : 'false');
            });
            syncBadgeStep();
        }

        root.querySelectorAll('[data-cplan-open-saved]').forEach(function (openSaved) {
            openSaved.addEventListener('click', function () {
                if (busy || !saved) { return; }
                applyAnswers(saved);
                startPlanning(function () { return restoreTweaks(savedTweaks); });
            });
        });

        function replan(opts) {
            opts = opts || {};
            if (busy) {
                // The EARLIEST rollback wins: it restores the state the plan on screen was built from. The rest of
                // the request's options ride along, so a confirmed spend queued behind a tweak still carries it.
                queued = Object.assign({}, queued || {}, opts, { rollback: (queued && queued.rollback) || opts.rollback });
                return;
            }
            var mine = ++seq;
            var onReview = reviewing() && !opts.fromWizard;
            // THE PRESSED BUTTON SAYS IT IS WORKING (Plan it, or Plan again after an Apply), like Start's "Starting…".
            var unlabel = opts.fromWizard
                ? busyLabel(!els.wizard.hidden ? els.next : els.again, 'Planning…') : function () {};
            setBusy(true);
            say(opts.fromWizard ? 'Lining up your squares…' : 'Re-planning…');
            barSay(onReview ? 'Re-planning…' : '');
            var body = payload();
            // THE ANSWERS THIS REQUEST CARRIES: a spend recorded from its reply names them, never whatever
            // `submitted` holds by the time a reply that waited in line lands.
            var sentAnswers = submitted;
            var sentTweaks = { pins: pins.slice(), square_pins: body.tweaks.square_pins,
                               excluded: body.tweaks.excluded, penalized: body.tweaks.penalized,
                               left_open: body.tweaks.left_open };
            // ONLY the request the hunter just confirmed carries the spend; nothing else can spend their plan.
            if (opts.useFreePlan) { body.use_free_plan = true; }
            // Said so the team's line preview (`?preview=optimizer-line`) queues a Plan it, never a tweak.
            if (opts.fromWizard) { body.from_wizard = true; }
            // IN LINE, NOT REFUSED (owner, 2026-10-16): while every whole-catalogue slot is taken the server answers
            // 202 with a ticket and a place in line. Wait, re-ask with the ticket, and carry on until it builds. The
            // page stays busy the whole time; "Stop waiting" (or the cap) ends the wait by moving `seq` on.
            var waitStarted = Date.now();
            var lastLine = '';
            // STOPPING abandons this change like a failure does: its rollback restores the state the plan on screen
            // was built from (which also covers anything queued behind it), and the queued change is dropped.
            stopLine = function (message) {
                seq++;
                stopLine = null;
                if (opts.rollback) { opts.rollback(); }
                queued = null;
                if (plan) { render(); }
                showLineCancel(false);
                unlabel();
                barSay('');
                setBusy(false);
                say(message || 'Stopped waiting. Press Plan it whenever you want to try again.');
            };
            var attempt = function (ticket) {
                if (ticket) { body.line_ticket = ticket; }
                return PP.API.post(urls.plan, body).then(function (data) {
                    // A SPEND IS NEVER MISSED: a confirmed request already on its way when the hunter stopped
                    // waiting can still be built, and the server then spent the week on it.
                    if (mine !== seq && data && data.allowance && data.allowance.spent) {
                        markSpent(sentAnswers, nextFrom(data.allowance));
                    }
                    if (mine !== seq || !data || !data.queued) { return data; }
                    if (Date.now() - waitStarted > LINE_MAX_WAIT_MS) {
                        stopLine("The line isn't moving right now. Try again in a few minutes.");
                        return null;
                    }
                    showLineCancel(true);
                    // Said only when it CHANGES: a polite live region re-read every 2s is noise to a screen reader.
                    var line = lineLine(data);
                    if (line !== lastLine) { say(line); lastLine = line; }
                    return new Promise(function (resolve) { window.setTimeout(resolve, LINE_POLL_MS); }).then(function () {
                        return mine === seq ? attempt(data.ticket) : null;
                    });
                });
            };
            attempt(null).then(function (data) {
                if (mine !== seq || !data) { return; }
                // THE SERVER SAYS WHEN THE WEEK WAS SPENT, and only on a plan that was actually built; the date is
                // its own, not this page's load time.
                if (data.allowance && data.allowance.spent) { markSpent(sentAnswers, nextFrom(data.allowance)); }
                // THE SERVER AUTOSAVED IT: so does this page, for Open my plan after Edit answers.
                // The server's word on what is left open, not what was sent (a square filled by hand drops out).
                sentTweaks.left_open = (data.left_open || []).map(function (sq) { return { type: sq.type, key: sq.key }; });
                rememberPlan(sentAnswers, sentTweaks);
                // A RE-SOLVE KEEPS ITS PLACE: lines that stay glide to where the new plan puts them, new ones settle
                // in, so the hunter sees what their tweak changed instead of a hard cut of the whole list.
                var glide = onReview && lineFlip;
                if (glide) { lineFlip.measure(); }
                plan = data;
                // THE SERVER'S WORD on what is left open: a square the hunter has since filled by hand drops out.
                tweaks.leftOpen = new Set((plan.left_open || []).map(sqKey));
                // RETURNED, so the page stays BUSY through the wizard's fold: a second Plan it (or Open my plan)
                // pressed in those 180ms would otherwise start a second solve and replay the whole reveal.
                return new Promise(function (resolve) {
                    showReview(!!opts.fromWizard, function () {
                        if (glide) { lineFlip.play(); }
                        say(readyLine());
                        restoreFocus(opts.fromWizard ? null : opts.focus, !!opts.fromWizard);
                        resolve();
                    });
                });
            }).catch(function (err) {
                if (mine !== seq) { return; }
                if (opts.rollback) { opts.rollback(); }
                // A change queued behind this one was built on the state just rolled back; its own rollback would
                // re-apply the failed change.
                if (queued) { queued.rollback = null; }
                if (plan) { render(); }
                // THE BODY IS READ ONCE (a response stream can be), then branched: the allowance's two refusals
                // are answers to act on, not errors to print.
                var fallback = 'The plan did not load. Try again in a moment.';
                barSay('');
                var handle = function (reply) {
                    if (reply && reply.needs_confirmation) {
                        allowanceState = 'available';
                        say('');
                        openSpend(nextFrom(reply));
                        return;
                    }
                    if (reply && reply.plan_locked) {
                        showInUse(reply.saved_answers || null, nextFrom(reply));
                        // The page's own line, so its date is on the same clock as the banner's just above it
                        // (the server's text is in the saved zone, UTC for a hunter who never chose one). With no
                        // date (the week ran out as the reply was built) the server's own words are the true ones.
                        say(reply.next_free_label ? lockedLine() : (reply.error || fallback), true);
                        return;
                    }
                    sayFailure((reply && (reply.error || reply.detail)) || fallback);
                };
                (PP.API.failureBody ? PP.API.failureBody(err) : Promise.resolve(null))
                    .then(handle).catch(function () { handle(null); });
            }).then(function () {
                if (mine !== seq) { return; }
                // EVERY ENDING hides Stop waiting, an error after a wait included: left showing on an idle page, a
                // click would silently throw away the next ordinary re-plan.
                stopLine = null;
                showLineCancel(false);
                unlabel();
                barSay('');
                setBusy(false);
                if (queued) {
                    var next = queued;
                    queued = null;
                    replan(next);
                }
            });
        }

        //: How often a hunter waiting in line re-asks. Under the solve limit (40 a minute) for any wait.
        var LINE_POLL_MS = 2000;
        //: The longest the page waits in line before saying the line is not moving (a build is ~2.5s).
        var LINE_MAX_WAIT_MS = 3 * 60 * 1000;
        var lineCancel = root.querySelector('[data-cplan-line-cancel]');

        //: Ends the current wait in line (set by `replan` while it runs). The loop sees `seq` move on and stops; the
        //: ticket lapses on the server by itself.
        var stopLine = null;

        function showLineCancel(on) { if (lineCancel) { lineCancel.hidden = !on; } }

        if (lineCancel) { lineCancel.addEventListener('click', function () { if (stopLine) { stopLine(); } }); }

        function ordinal(n) {
            var tail = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th');
            return n + tail;
        }

        /** "You're 3rd in line. Your plan should be ready in about 8 seconds." */
        function lineLine(wait) {
            var secs = Math.max(1, wait.eta_seconds || 1);
            var when = secs < 60 ? 'about ' + plural(secs, 'second', 'seconds')
                                 : 'about ' + plural(Math.ceil(secs / 60), 'minute', 'minutes');
            return "Lots of hunters are optimizing right now. You're " + ordinal(wait.position || 1)
                + ' in line. Your plan should be ready in ' + when + '.';
        }

        function readyLine() {
            var squares = squareCount();
            // Games with a square to write: a pick that only stays is part of the plan, not news.
            var games = plan.lines.filter(function (line) { return line.squares.some(changes); }).length;
            if (!games) { return 'Plan ready.'; }
            return 'Plan ready: ' + plural(games, 'game', 'games') + ' for ' + plural(squares, 'square', 'squares') + '.';
        }

        function setBusy(on) {
            busy = on;
            root.classList.toggle('is-busy', on);
            // ON THE REVIEW, NOT THE ROOT: the root holds the status region, and a busy region may stay silent.
            if (els.review) { els.review.setAttribute('aria-busy', on ? 'true' : 'false'); }
            els.next.disabled = on;
            els.back.disabled = on;
            syncRail();
            syncBar();
        }

        function syncBar() {
            var applied = !plan && els.again && !els.again.hidden;
            if (els.reroll) { els.reroll.disabled = busy || !plan || applied; }
            if (els.apply) { els.apply.disabled = busy || !squareCount(); }
        }

        /** Show the plan. A FRESH one (from the wizard) is a scene change, choreographed: the wizard folds away
         *  (anticipation, a short ease-in), then the summary bar and the plan's groups settle in on a capped cascade
         *  and the stats count up. A re-solve is not a scene change and swaps at once. `after` runs once the plan
         *  is on screen (focus, the ready line, a re-solve's glide). */
        function showReview(fresh, after) {
            var reveal = function () {
                els.wizard.hidden = true;
                showResume(false);
                els.summary.hidden = false;
                els.summaryText.textContent = summaryLine(submitted);
                els.review.hidden = false;
                els.again.hidden = true;
                els.reroll.hidden = false;
                els.apply.hidden = false;
                if (fresh) { lastStats = null; }
                render();
                if (fresh) {
                    settleIn(els.summary);
                    settleIn(els.stats, 60);
                    // GROUPS, NOT ROWS, and capped: a long plan must not trickle in for seconds.
                    Array.prototype.slice.call(els.sections.children, 0, 5).forEach(function (sec, i) { settleIn(sec, 120 + i * 70); });
                }
                if (after) { after(); }
            };
            if (fresh && !els.wizard.hidden && els.wizard.animate && !reducedMotion()) {
                var fold = els.wizard.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(-8px) scale(0.99)' }],
                                              { duration: 180, easing: 'cubic-bezier(0.4, 0, 1, 1)', fill: 'forwards' });
                // HIDDEN BEFORE the fill is cancelled, so the folded wizard never flashes back for a frame.
                fold.finished.then(function () { reveal(); fold.cancel(); }, reveal);
                return;
            }
            reveal();
        }

        /** Squares Apply would write: every planned square but a pick that stays (it is already there). */
        function squareCount() {
            if (!plan) { return 0; }
            return plan.lines.reduce(function (n, line) { return n + line.squares.filter(changes).length; }, 0);
        }

        function changes(sq) { return !sq.stays; }

        /** Open squares the plan fills: every square it writes except the unfinished ones it replaces. */
        function openSquaresFilled() {
            if (!plan) { return 0; }
            return squareCount() - plan.lines.reduce(function (n, line) { return n + (line.replaces || []).length; }, 0);
        }

        // ── focus ────────────────────────────────────────────────────────────────────────────────

        /** After a redraw, focus the control the hunter just used (by its key), else the plan's heading. A
         *  redraw replaces every row, so without this focus falls to the page body. Only a fresh plan scrolls;
         *  a tweak on a long plan must not jump the page to its top. */
        function restoreFocus(key, scroll) {
            // A SHEET STILL PLAYING ITS EXIT keeps the page inert: focus lands once it is really closed.
            if (dialog && dialog.open) {
                dialog.addEventListener('close', function () { restoreFocus(key, scroll); }, { once: true });
                return;
            }
            // 'square:type|key' LANDS ON THAT SQUARE wherever the re-plan put it: the chip of the line that now holds
            // it, or its Plan it button while it is left open.
            var target = key && window.CSS && CSS.escape && (key.indexOf('square:') === 0
                ? els.sections.querySelector('[data-square="' + CSS.escape(key.slice(7)) + '"]')
                : els.sections.querySelector('[data-focus="' + CSS.escape(key) + '"]'));
            if (!target && els.reviewHead) { target = els.reviewHead; }
            if (!target) { return; }
            target.focus({ preventScroll: true });
            // The same smooth arrival Edit answers makes in the other direction, never a jump.
            if (scroll && els.summary) {
                els.summary.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' });
            }
        }

        // ── rendering the plan ───────────────────────────────────────────────────────────────────

        // ── the view: by letter or by job ───────────────────────────────────────────────────────
        //
        // By letter (the default; owner, 2026-10-16) or by job, which regroups the whole plan into the run page's
        // discipline shelves (owner, 2026-10-17). The switch sits above the plan when both boards are planned and
        // there is more than one game, games glide to their new places (`flipGrid`), and the choice is remembered
        // per browser.

        var SORT_KEY = 'pp-cplan-sort';
        var sortBar = root.querySelector('[data-cplan-sortbar]');
        var sortMode = 'az';
        try { if (window.localStorage.getItem(SORT_KEY) === 'jobs') { sortMode = 'jobs'; } } catch (e) { /* storage blocked */ }
        root.querySelectorAll('[data-cplan-sort]').forEach(function (input) {
            input.checked = input.value === sortMode;
            input.addEventListener('change', function () {
                if (!input.checked) { return; }
                sortMode = input.value;
                try { window.localStorage.setItem(SORT_KEY, sortMode); } catch (e) { /* storage blocked */ }
                if (!plan) { return; }
                if (lineFlip) { lineFlip.measure(); }
                render();
                if (lineFlip) { lineFlip.play(); }
                // Kept on the switch the hunter just used, whatever the re-render did around it.
                input.focus({ preventScroll: true });
            });
        });

        // ONE GLIDE for a re-sort and a re-solve: survivors glide, arrivals (a re-solve's new games) settle in.
        var lineFlip = PP.flipGrid ? PP.flipGrid({
            container: function () { return els.sections; },
            itemSelector: '.pp-cplan__line',
            // render() REBUILDS the rows, so identity is the game's own focus landing, not the node.
            key: function (el) { var f = el.querySelector('[data-focus]'); return f ? f.getAttribute('data-focus') : null; },
            mark: false, enter: true, enterScale: 0.96
        }) : null;

        function sortRank(line, type) {
            var at = (line.sort || {})[type];
            return typeof at === 'number' ? at : Number.MAX_SAFE_INTEGER;
        }

        function byChosenOrder(a, b) {
            var other = sortMode === 'az' ? 'jobs' : 'az';
            return (sortRank(a, sortMode) - sortRank(b, sortMode)) || (sortRank(a, other) - sortRank(b, other));
        }

        /** THE VIEW SWITCH (Letter / Job) sits above the whole plan, because Job regroups all of it (owner,
         *  2026-10-17). Shown only when both boards are planned and there is more than one game to arrange. */
        function placeSortBar() {
            if (sortBar) { sortBar.hidden = !canSwitchView(); }
        }

        function canSwitchView() { return !!(plan.runs.az && plan.runs.jobs && plan.lines.length > 1); }

        /** THE JOB VIEW: chosen with the switch (only while the switch is there to switch back: a remembered "Job"
         *  never strands a hunter whose plan has no switch), and always when Job Coverage is the one board planned. */
        function jobView() {
            return (plan.job_groups || []).length > 0 && (!plan.runs.az || (canSwitchView() && sortMode === 'jobs'));
        }

        function render() {
            renderStats();
            els.sections.textContent = '';
            placeSortBar();
            if (jobView()) { renderByJob(); } else { renderByLetter(); }
            section('Swaps worth a look', "Replace a game you haven't finished with one that covers more.",
                    plan.swaps.map(offeredSwap).filter(Boolean), swapRow);
            section("Couldn't place", null, plan.unplaced, unplacedRow);
            // The Job view draws its job squares in their disciplines, so only the letters are left for this.
            emptySection(jobView() ? 'az' : null);
            if (!plan.lines.length && !plan.swaps.length && !plan.empty.length && !(plan.left_open || []).length) {
                nothingToPlan();
            }
            notesSection();
            var n = squareCount();
            els.apply.textContent = n ? 'Apply ' + plural(n, 'square', 'squares') : 'Nothing to apply';
            syncBar();
        }

        function renderByLetter() {
            var both = [], az = [], jobs = [];
            plan.lines.forEach(function (line) {
                var types = new Set(line.squares.map(function (s) { return s.type; }));
                (types.size > 1 ? both : types.has('az') ? az : jobs).push(line);
            });
            [both, az, jobs].forEach(function (group) { group.sort(byChosenOrder); });
            section('Covers both boards', 'One game, two squares.', both, lineRow);
            section('A-Z only', null, az, lineRow);
            section('Job Coverage only', null, jobs, lineRow);
        }

        /** BY JOB (owner, 2026-10-17): the run page's own discipline groups, in its own order (`job_groups`, from
         *  `slot_render.discipline_sequence`), each job in board order with the game that fills it, its job chip
         *  first. A job the plan could not fill, or that the hunter is leaving open, keeps its place as a slim row,
         *  so each discipline reads like its shelf on the run page. Games with no job follow as "A-Z only". */
        function renderByJob() {
            var byJob = {}, azOnly = [];
            plan.lines.forEach(function (line) {
                var job = line.squares.filter(function (s) { return s.type === 'jobs'; })[0];
                if (job) { byJob[job.key] = line; } else { azOnly.push(line); }
            });
            var empty = squaresByKey(plan.empty, 'jobs');
            var blocked = squaresByKey(plan.notes.badge_blocked, 'jobs');
            var left = squaresByKey(plan.left_open, 'jobs');
            plan.job_groups.forEach(function (group) {
                var rows = [];
                group.keys.forEach(function (key) {
                    if (byJob[key]) { rows.push(lineRow(byJob[key], 'jobs')); }
                    // WHY IT IS EMPTY, said on the row (Still open says it for the letters): only "Badges only"
                    // kept a game out, or nothing on the hunter's terms fits at all.
                    else if (empty[key]) {
                        rows.push(slotRow(empty[key], blocked[key] ? 'No badge game fits' : 'Nothing on your terms fits'));
                    }
                    else if (left[key]) {
                        rows.push(slotRow(left[key], 'Leaving open', true));
                    }
                });
                if (!rows.length) { return; }
                var filled = group.keys.filter(function (key) { return byJob[key]; }).length;
                var wrap = sectionShell(group.label || 'Other jobs', filled);
                wrap.classList.add('pp-cplan__section--disc');
                if (/^[a-z-]+$/.test(group.slug || '')) {
                    wrap.style.setProperty('--disc', 'var(--disc-' + group.slug + ', var(--pp-primary))');
                }
                if (group.icon) {
                    wrap.querySelector('.pp-cplan__section-head').prepend(jobIcon(group.icon));
                }
                var list = document.createElement('ul');
                list.className = 'pp-cplan__lines';
                list.setAttribute('role', 'list');
                rows.forEach(function (row) { list.appendChild(row); });
                wrap.appendChild(list);
                els.sections.appendChild(wrap);
            });
            azOnly.sort(byChosenOrder);
            section('A-Z only', null, azOnly, lineRow);
        }

        function squaresByKey(squares, type) {
            var out = {};
            (squares || []).forEach(function (sq) { if (sq.type === type) { out[sq.key] = sq; } });
            return out;
        }

        /** A job's place when no game fills it: its chip, what is going on, and (left open) the way back. */
        function slotRow(sq, text, isLeftOpen) {
            var li = document.createElement('li');
            li.className = 'pp-cplan__line pp-cplan__line--slot';
            li.appendChild(squareChip(sq, false));
            var note = document.createElement('span');
            note.className = 'pp-cplan__slot-note';
            note.textContent = text;
            // The flip's identity for this row (it has no game).
            note.setAttribute('data-focus', 'slot:' + sqKey(sq));
            li.appendChild(note);
            if (isLeftOpen) { li.appendChild(planItAction(sq)); }
            return li;
        }

        function planItAction(sq) {
            var btn = action('Plan it', function () {
                change(function () { tweaks.leftOpen.delete(sqKey(sq)); }, 'square:' + sqKey(sq));
            }, { aria: 'Plan it: ' + sq.label });
            btn.setAttribute('data-square', sqKey(sq));
            return btn;
        }

        //: The stats as last shown, so a re-solve ticks each one from its old value. Null: count up from zero.
        var lastStats = null;

        function renderStats() {
            var openTotal = Object.keys(plan.runs).reduce(function (n, t) { return n + plan.runs[t].open; }, 0);
            var doubles = plan.lines.filter(function (l) { return l.squares.length > 1; }).length;
            var stages = new Set();
            plan.lines.forEach(function (l) { (l.stages || []).forEach(function (s) { stages.add(s.series + '#' + s.stage); }); });
            els.stats.textContent = '';
            // [key, value, suffix, label]: the number counts, the suffix ("/12") rides `data-countup-suffix`.
            var cells = [['filled', openSquaresFilled(), '/' + openTotal, 'open squares filled'],
                         ['games', plan.lines.length, '', plan.lines.length === 1 ? 'game' : 'games']];
            // "Cover both" only means something when two boards are planned.
            if (Object.keys(plan.runs).length > 1) { cells.push(['both', doubles, '', 'cover both']); }
            // When the picks are planned, how many of them this plan would change (an accepted swap counts too).
            var changed = plan.lines.reduce(function (n, l) { return n + (l.replaces || []).length; }, 0);
            if (replanning() || changed) { cells.push(['changed', changed, '', changed === 1 ? 'pick changed' : 'picks changed']); }
            cells.push(['stages', stages.size, '', stages.size === 1 ? 'badge stage' : 'badge stages']);
            var before = lastStats;
            lastStats = {};
            cells.forEach(function (cell) {
                var div = document.createElement('div');
                div.className = 'pp-cplan__stat';
                var num = document.createElement('span');
                num.className = 'pp-cplan__stat-num pp-tally';
                num.dataset.countup = String(cell[1]);
                if (cell[2]) { num.dataset.countupSuffix = cell[2]; }
                num.textContent = cell[1] + cell[2];
                lastStats[cell[0]] = cell[1];
                // Fresh nodes every render, so a loop still running from the last one writes to a detached cell.
                if (PP.countUp) {
                    var from = before && before[cell[0]] != null ? before[cell[0]] : 0;
                    PP.countUp(num, before ? 600 : 900, { from: from });
                }
                var lbl = document.createElement('span');
                lbl.className = 'pp-cplan__stat-lbl';
                lbl.textContent = cell[3];
                div.appendChild(num);
                div.appendChild(lbl);
                els.stats.appendChild(div);
            });
        }

        function sectionShell(title, count) {
            var wrap = document.createElement('section');
            wrap.className = 'pp-cplan__section';
            var head = document.createElement('h2');
            head.className = 'pp-cplan__section-head';
            head.appendChild(document.createTextNode(title));
            if (count !== undefined) {
                // The visible pill is decoration; the heading's spoken text carries the count once.
                var pill = document.createElement('span');
                pill.className = 'pp-cplan__section-count';
                pill.setAttribute('aria-hidden', 'true');
                pill.textContent = count;
                head.appendChild(pill);
                var spoken = document.createElement('span');
                spoken.className = 'sr-only';
                spoken.textContent = ', ' + count;
                head.appendChild(spoken);
            }
            wrap.appendChild(head);
            return wrap;
        }

        function hint(text) {
            var p = document.createElement('p');
            p.className = 'pp-cplan__hint';
            p.textContent = text;
            return p;
        }

        function section(title, sub, items, build) {
            if (!items || !items.length) { return; }
            var wrap = sectionShell(title, items.length);
            if (sub) { wrap.appendChild(hint(sub)); }
            var list = document.createElement('ul');
            list.className = 'pp-cplan__lines';
            list.setAttribute('role', 'list');
            items.forEach(function (item) { list.appendChild(build(item)); });
            wrap.appendChild(list);
            els.sections.appendChild(wrap);
            return wrap;
        }

        function sqKey(sq) { return sq.type + '|' + sq.key; }

        function isLocked(line) {
            return line.squares.every(function (sq) { return tweaks.squarePins[sqKey(sq)] === line.slug; });
        }

        /** `lead`: the board whose chip comes first (the Job view leads with the job). */
        function lineRow(line, lead) {
            var li = document.createElement('li');
            li.className = 'pp-cplan__line';
            // UNDER REPLAN a changed pick is the plan's own line, not an accepted swap: it locks like any other, and
            // Keep (in place of Undo swap) puts the old game back.
            var swapped = !replanning() && (line.replaces || []).length > 0;
            var locked = !swapped && isLocked(line);
            li.classList.toggle('is-locked', locked || swapped);
            li.appendChild(art(line));
            var main = document.createElement('div');
            main.className = 'pp-cplan__line-main';
            var name = document.createElement('p');
            name.className = 'pp-cplan__line-name';
            name.textContent = line.name;
            // A FOCUS LANDING for re-solves that put this game here (a swap, an Other options pick): whichever
            // buttons the line ends up with, its name is always there.
            name.setAttribute('tabindex', '-1');
            name.setAttribute('data-focus', 'line:' + line.slug);
            main.appendChild(name);

            var squares = document.createElement('div');
            squares.className = 'pp-cplan__squares';
            var ordered = lead === 'jobs' ? line.squares.slice().sort(function (a, b) {
                return (b.type === lead) - (a.type === lead);
            }) : line.squares;
            ordered.forEach(function (sq) {
                var btn = squareChip(sq, true);
                btn.setAttribute('data-square', sqKey(sq));
                btn.setAttribute('aria-label', 'Other options for ' + sq.label);
                btn.addEventListener('click', function () { openAlternatives(sq, line); });
                squares.appendChild(btn);
            });
            main.appendChild(squares);

            (line.replaces || []).forEach(function (r) {
                main.appendChild(lineNote('Replaces ' + (r.name || 'the game') + ' in ' + r.label + '.'));
            });
            // A PICK THAT STAYS is on the plan so the hunter sees all of it, said so it never reads as a change.
            line.squares.forEach(function (sq) {
                if (sq.stays) { main.appendChild(lineNote('Already your pick in ' + sq.label + '.')); }
            });
            var stages = stageList(line);
            if (stages) { main.appendChild(stages); }
            var meta = metaList(line);
            if (meta) { main.appendChild(meta); }
            main.appendChild(lineActions(line, locked, swapped));
            li.appendChild(main);
            return li;
        }

        function metaList(item) {
            var wrap = document.createElement('div');
            wrap.className = 'pp-cplan__meta';
            if (item.progress > 0) { wrap.appendChild(metaNote("You're " + item.progress + '% in', 'progress')); }
            if (item.pinned) { wrap.appendChild(metaNote('Your pick', 'pin')); }
            if (item.outside_filters) { wrap.appendChild(metaNote('Outside your filters', 'warn')); }
            (item.availability || []).forEach(function (flag) {
                var alertChip = availabilityChip(flag);
                if (alertChip) { wrap.appendChild(alertChip); }
            });
            (item.platforms || []).forEach(function (p) { wrap.appendChild(platformTag(p)); });
            return wrap.childNodes.length ? wrap : null;
        }

        /** A platform as the site's flat platform tag. The TONE comes from the server (`platform_color_str`), and
         *  it is checked as a bare word before it becomes a class, because a class name is markup. */
        function platformTag(p) {
            var tag = document.createElement('span');
            tag.className = 'bd-meta-tag' + (/^[a-z]+$/.test(p.tone || '') ? ' bd-meta-tag--' + p.tone : '');
            tag.textContent = p.name;
            return tag;
        }

        /** DELISTED / UNOBTAINABLE as the house alert chip, the picker's own treatment for the same facts; null for
         *  a flag this page has no words for. */
        function availabilityChip(flag) {
            if (!AVAILABILITY_LABELS[flag]) { return null; }
            var alertChip = document.createElement('span');
            alertChip.className = 'bd-chip bd-chip--error';
            alertChip.textContent = AVAILABILITY_LABELS[flag];
            return alertChip;
        }

        /** The badge stages a game advances, ONE ROW PER BADGE (owner, 2026-10-17): the badge and its editions once,
         *  then its stage numbers, each stage's title on hover (and read out after its number). A game in a long
         *  series used to repeat "Counts toward Stage N (title) of Series, Edition" once per stage. A stage reached
         *  only through a bundle keeps its own row, "Part of stage N": the game alone does not clear it. */
        function stageList(item) {
            var groups = [];
            var byKey = {};
            (item.stages || []).forEach(function (s) {
                var editions = (s.editions || []).join(' / ');
                var key = [s.series, editions, s.part ? 1 : 0].join('|');
                if (!byKey[key]) {
                    byKey[key] = { name: s.series_name, editions: editions, part: !!s.part, stages: [] };
                    groups.push(byKey[key]);
                }
                byKey[key].stages.push(s);
            });
            if (!groups.length) { return null; }
            var list = document.createElement('ul');
            list.className = 'pp-cplan__stages';
            list.setAttribute('role', 'list');
            groups.forEach(function (g) { list.appendChild(stageRow(g)); });
            return list;
        }

        function stageRow(group) {
            var row = document.createElement('li');
            row.className = 'pp-cplan__stage';
            row.appendChild(badgeGlyph());
            // The name and numbers wrap INSIDE their own box, so a wrapped "Stages ..." lines up under the name.
            var body = document.createElement('span');
            body.className = 'pp-cplan__stage-body';
            row.appendChild(body);
            var name = document.createElement('span');
            name.className = 'pp-cplan__stage-name';
            name.textContent = group.name;
            if (group.editions) {
                var ed = document.createElement('span');
                ed.className = 'pp-cplan__stage-ed';
                ed.textContent = ' (' + group.editions + ')';
                name.appendChild(ed);
            }
            body.appendChild(name);

            var nums = document.createElement('span');
            nums.className = 'pp-cplan__stage-nums';
            var word = group.stages.length > 1 ? 'Stages ' : 'Stage ';
            nums.appendChild(document.createTextNode(group.part ? 'Part of ' + word.toLowerCase() : word));
            group.stages.forEach(function (s, i) {
                if (i) { nums.appendChild(document.createTextNode(', ')); }
                var n = document.createElement('span');
                n.className = 'pp-cplan__stage-n';
                n.textContent = String(s.stage);
                if (s.title) {
                    n.title = s.title;
                    var spoken = document.createElement('span');
                    spoken.className = 'sr-only';
                    spoken.textContent = ' (' + s.title + ')';
                    n.appendChild(spoken);
                }
                nums.appendChild(n);
            });
            body.appendChild(nums);
            return row;
        }

        /** Lucide's award glyph, the badge mark beside each stage row. */
        function badgeGlyph() {
            var NS = 'http://www.w3.org/2000/svg';
            var svg = document.createElementNS(NS, 'svg');
            svg.setAttribute('viewBox', '0 0 24 24');
            svg.setAttribute('fill', 'none');
            svg.setAttribute('stroke', 'currentColor');
            svg.setAttribute('stroke-width', '2');
            svg.setAttribute('stroke-linecap', 'round');
            svg.setAttribute('stroke-linejoin', 'round');
            svg.setAttribute('aria-hidden', 'true');
            var ring = document.createElementNS(NS, 'circle');
            ring.setAttribute('cx', '12');
            ring.setAttribute('cy', '8');
            ring.setAttribute('r', '6');
            var ribbon = document.createElementNS(NS, 'path');
            ribbon.setAttribute('d', 'M15.477 12.89 17 22l-5-3-5 3 1.523-9.11');
            svg.appendChild(ring);
            svg.appendChild(ribbon);
            return svg;
        }

        /** One plain sentence under a game's name: what it replaces, covers, keeps, or why it did not fit. */
        function lineNote(text) {
            var p = document.createElement('p');
            p.className = 'pp-cplan__swap-why';
            p.textContent = text;
            return p;
        }

        function metaNote(text, kind) {
            var span = document.createElement('span');
            span.className = 'pp-cplan__note pp-cplan__note--' + kind;
            span.textContent = text;
            return span;
        }

        function lineActions(line, locked, swapped) {
            var row = document.createElement('div');
            row.className = 'pp-cplan__actions';
            if (swapped) {
                row.appendChild(action('Undo swap', function () {
                    change(function () {
                        line.replaces.forEach(function (r) { delete tweaks.squarePins[sqKey(r)]; });
                        // An undone swap is undone whole: a half left out with "Only C" may be offered again, and its
                        // card starts on Both.
                        tweaks.declined.forEach(function (k) {
                            if (k.indexOf(line.slug + '@') === 0) { tweaks.declined.delete(k); }
                        });
                        Object.keys(tweaks.choices).forEach(function (k) {
                            if (k.indexOf('cplan_swap_' + line.slug + '@') === 0) { delete tweaks.choices[k]; }
                        });
                    });
                }, { aria: 'Undo swap: ' + line.name }));
            } else {
                // A LOCK CHANGES NOTHING NOW, only what later re-solves may touch, so it toggles IN PLACE: no request,
                // no redraw, so the line's own border transition plays and the button blooms once on lock.
                row.appendChild(action('Lock', function (btn) {
                    var on = !isLocked(line);
                    line.squares.forEach(function (sq) {
                        if (on) { tweaks.squarePins[sqKey(sq)] = line.slug; } else { delete tweaks.squarePins[sqKey(sq)]; }
                        // A pick that stays is LOCKED there, so a later hand edit to that square drops it.
                        if (on && sq.stays) { tweaks.locks.add(sqKey(sq)); } else { tweaks.locks.delete(sqKey(sq)); }
                    });
                    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
                    var li = btn.closest('.pp-cplan__line');
                    if (li) { li.classList.toggle('is-locked', on); }
                    if (on && PP.igniteTab) { PP.igniteTab(btn); }
                }, { pressed: locked, aria: 'Lock ' + line.name, focus: 'lock:' + line.slug }));
            }
            // KEEP (picks planned): put the pick back, LOCKED, so its line comes back as "Already your pick".
            var kept = replanning() ? (line.replaces || []).filter(function (r) { return r.slug; }) : [];
            if (kept.length) {
                var keepLabel = kept.length === 1 ? 'Keep ' + (kept[0].name || 'your pick') : 'Keep your picks';
                row.appendChild(action(keepLabel, function () {
                    change(function () { kept.forEach(keepPick); }, 'square:' + sqKey(kept[0]));
                }, { aria: keepLabel + ': ' + kept.map(function (r) { return (r.name || 'your pick') + ' in ' + r.label; }).join(' and ') }));
            }
            // USE IT FOR ONE SQUARE (owner, 2026-10-18): a line replacing a pick in BOTH its squares offers "Only F"
            // and "Only Survivalist". "Only F" pins the game to F and locks the other pick back; the re-solve then
            // draws two lines. Plain buttons, so nothing re-plans until one is pressed.
            if (kept.length === 2 && line.squares.length === 2) {
                kept.forEach(function (r) {
                    var other = kept.filter(function (o) { return sqKey(o) !== sqKey(r); })[0];
                    row.appendChild(action('Only ' + r.label, function () {
                        change(function () { pinGame(line.slug, [r]); keepPick(other); }, 'square:' + sqKey(r));
                    }, { aria: 'Only ' + r.label + ': use ' + line.name + ' there and keep ' + (other.name || 'your pick')
                                + ' in ' + other.label }));
                });
            }
            row.appendChild(action('Not this one', function () {
                change(function () {
                    tweaks.excluded.add(line.slug);
                    Object.keys(tweaks.squarePins).forEach(function (k) {
                        if (tweaks.squarePins[k] === line.slug) { delete tweaks.squarePins[k]; tweaks.locks.delete(k); }
                    });
                    pins = pins.filter(function (p) { return p.slug !== line.slug; });
                    renderPinned();
                });
            }, { aria: 'Not this one: ' + line.name }));
            row.appendChild(action('Other options', function () { openAlternatives(line.squares[0], line); },
                                   { aria: 'Other options for ' + line.squares.map(function (s) { return s.label; }).join(' and ') }));
            return row;
        }

        /** A plan button. Only a TOGGLE (Lock) carries `aria-pressed`; the rest are plain actions. */
        function action(label, onClick, opts) {
            opts = opts || {};
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'pp-cplan__act';
            btn.textContent = label;
            if (opts.pressed !== undefined) { btn.setAttribute('aria-pressed', opts.pressed ? 'true' : 'false'); }
            if (opts.aria) { btn.setAttribute('aria-label', opts.aria); }
            if (opts.focus) { btn.setAttribute('data-focus', opts.focus); }
            btn.addEventListener('click', function () { if (!busy) { onClick(btn); } });
            return btn;
        }

        function squareChip(sq, asButton) {
            var el = document.createElement(asButton ? 'button' : 'span');
            if (asButton) { el.type = 'button'; }
            el.className = 'pp-cplan__sq';
            if (sq.look) {
                el.classList.add('pp-cplan__sq--job');
                if (/^[a-z-]+$/.test(sq.look.disc_slug || '')) {
                    el.style.setProperty('--disc', 'var(--disc-' + sq.look.disc_slug + ', var(--pp-primary))');
                }
                if (sq.look.icon) { el.appendChild(jobIcon(sq.look.icon)); }
            }
            var label = document.createElement('span');
            label.textContent = sq.label;
            el.appendChild(label);
            return el;
        }

        function swapRow(swap) {
            var li = document.createElement('li');
            li.className = 'pp-cplan__line pp-cplan__line--swap';
            li.appendChild(art(swap.suggested));
            var main = document.createElement('div');
            main.className = 'pp-cplan__line-main';
            var name = document.createElement('p');
            name.className = 'pp-cplan__line-name';
            name.textContent = swap.suggested.name;
            main.appendChild(name);
            // EVERY GAME IT REPLACES, one line each (owner, 2026-10-17): a game that would replace unfinished games
            // in both challenges says so on one card, instead of hiding the second as "also covers".
            swap.replaces.forEach(function (r) {
                main.appendChild(lineNote('Replaces ' + (r.current.name || 'the game') + ' in ' + r.square.label + '.'));
            });
            var also = (swap.also || []).map(function (s) { return s.label; }).join(', ');
            if (also) { main.appendChild(lineNote('Also covers ' + also + '.')); }
            var stages = stageList(swap.suggested);
            if (stages) { main.appendChild(stages); }
            var meta = metaList(swap.suggested);
            if (meta) { main.appendChild(meta); }
            var choice = swap.replaces.length > 1 ? swapChoice(swap) : null;
            if (choice) { main.appendChild(choice.el); }
            var row = document.createElement('div');
            row.className = 'pp-cplan__actions';
            row.appendChild(action('Use this swap', function () {
                var chosen = choice ? choice.squares() : swap.replaces.map(function (r) { return r.square; });
                // "JUST THIS ONE" STICKS: the halves left out are declined for this plan, or the re-solve would
                // offer the same game for them again at once (it still saves a game there).
                // RE-SOLVED, ALWAYS: an accepted swap moves games, and the plan above it must be redrawn around
                // it before anything is applied. The swapped-in game then leads its line with an Undo.
                change(function () {
                    swap.replaces.forEach(function (r) {
                        if (chosen.indexOf(r.square) < 0) { tweaks.declined.add(swapKey(swap.suggested.slug, r.square)); }
                    });
                    pinGame(swap.suggested.slug, chosen);
                }, 'line:' + swap.suggested.slug);
            }, { aria: 'Use this swap: ' + swap.suggested.name }));
            main.appendChild(row);
            li.appendChild(main);
            return li;
        }

        /** BOTH OR ONE, for a game that would replace a game in each challenge: the house segmented switch, radio
         *  backed (no JS state to drift), Both preselected. `squares()` reads which squares "Use this swap" takes. */
        function swapChoice(swap) {
            var group = document.createElement('div');
            group.className = 'pp-switch pp-cplan__swap-choice';
            group.setAttribute('role', 'radiogroup');
            group.setAttribute('aria-label', 'Which games to replace');
            var name = 'cplan_swap_' + swapKey(swap.suggested.slug, swap.replaces[0].square);
            var options = [{ value: 'both', label: 'Both', squares: swap.replaces.map(function (r) { return r.square; }) }]
                .concat(swap.replaces.map(function (r) {
                    return { value: sqKey(r.square), label: 'Only ' + r.square.label, squares: [r.square] };
                }));
            var kept = options.some(function (opt) { return opt.value === tweaks.choices[name]; }) ? tweaks.choices[name] : 'both';
            var inputs = options.map(function (opt) {
                var chip = document.createElement('label');
                chip.className = 'pp-switch__chip';
                var input = document.createElement('input');
                input.type = 'radio';
                input.name = name;
                input.className = 'sr-only';
                input.value = opt.value;
                input.checked = opt.value === kept;
                input.addEventListener('change', function () { if (input.checked) { tweaks.choices[name] = opt.value; } });
                var text = document.createElement('span');
                text.className = 'pp-switch__lbl';
                text.textContent = opt.label;
                chip.appendChild(input);
                chip.appendChild(text);
                group.appendChild(chip);
                return input;
            });
            return {
                el: group,
                squares: function () {
                    for (var i = 0; i < inputs.length; i++) { if (inputs[i].checked) { return options[i].squares; } }
                    return options[0].squares;
                }
            };
        }

        function swapKey(slug, square) { return slug + '@' + sqKey(square); }

        /** A suggested swap minus the halves the hunter declined ("Only C" declines the other). Null when nothing
         *  is left to offer. */
        function offeredSwap(swap) {
            var left = swap.replaces.filter(function (r) { return !tweaks.declined.has(swapKey(swap.suggested.slug, r.square)); });
            if (!left.length) { return null; }
            return left.length === swap.replaces.length ? swap : Object.assign({}, swap, { replaces: left });
        }

        function unplacedRow(item) {
            var li = document.createElement('li');
            li.className = 'pp-cplan__line pp-cplan__line--muted';
            var main = document.createElement('div');
            main.className = 'pp-cplan__line-main';
            var name = document.createElement('p');
            name.className = 'pp-cplan__line-name';
            name.textContent = item.name || 'A game you picked';
            main.appendChild(name);
            main.appendChild(lineNote(UNPLACED_COPY[item.reason] || "It didn't fit this plan."));
            li.appendChild(main);
            return li;
        }

        /** `onlyType`: just that board's squares (the Job view draws the job ones in their disciplines). */
        function emptySection(onlyType) {
            var mine = function (sq) { return !onlyType || sq.type === onlyType; };
            var empty = plan.empty.filter(mine);
            var left = (plan.left_open || []).filter(mine);
            if (!empty.length && !left.length) { return; }
            var wrap = sectionShell('Still open', empty.length + left.length);
            if (empty.length) {
                var blocked = (plan.notes.badge_blocked || []).filter(mine).length;
                wrap.appendChild(hint(blocked === empty.length
                    ? 'No badge game fits these. Switch badges to Bonus or Priority to fill them with any game, or pick by hand from the run.'
                    : blocked
                        ? 'Nothing on your terms is left for these. ' + plural(blocked, 'square', 'squares') + ' would fill with Bonus or Priority instead of Badges only.'
                        : 'Nothing on your terms is left for these. Widen the platforms, or pick by hand from the run.'));
                var row = document.createElement('div');
                row.className = 'pp-cplan__squares';
                empty.forEach(function (sq) { row.appendChild(squareChip(sq, false)); });
                wrap.appendChild(row);
            }
            if (left.length) {
                wrap.appendChild(hint("You're leaving these open to fill yourself."));
                var list = document.createElement('div');
                list.className = 'pp-cplan__left-open';
                left.forEach(function (sq) {
                    var item = document.createElement('div');
                    item.className = 'pp-cplan__left-open-item';
                    item.appendChild(squareChip(sq, false));
                    item.appendChild(planItAction(sq));
                    list.appendChild(item);
                });
                wrap.appendChild(list);
            }
            els.sections.appendChild(wrap);
        }

        function nothingToPlan() {
            var wrap = sectionShell('Nothing to plan');
            wrap.appendChild(hint('Every open square already has a game. Finish a few and come back.'));
            wrap.appendChild(runLinks(plan.runs, function (t, r) { return 'Open ' + TYPE_LABELS[t]; }));
            els.sections.appendChild(wrap);
        }

        function runLinks(runs, label) {
            var links = document.createElement('div');
            links.className = 'pp-cplan__actions';
            Object.keys(runs).forEach(function (t) {
                var a = document.createElement('a');
                a.className = 'pp-cta pp-cta--ghost';
                a.href = runs[t].url;
                a.textContent = label(t, runs[t]);
                links.appendChild(a);
            });
            return links;
        }

        function notesSection() {
            var hatch = plan.notes.hatch || {};
            var types = Object.keys(hatch);
            if (!types.length && !plan.notes.importer) { return; }
            var wrap = sectionShell('Worth knowing');
            wrap.classList.add('pp-cplan__notes');
            var list = document.createElement('ul');
            list.setAttribute('role', 'list');
            if (plan.notes.importer && plan.runs.az) {
                list.appendChild(noteItem('Some letters may fill from games you finished since joining. ',
                                          'Open your A-Z run', plan.runs.az.url));
            }
            types.forEach(function (t) {
                var labels = hatch[t].map(function (s) { return s.label; }).join(', ');
                list.appendChild(noteItem('Some squares are short on games (' + labels + '). They can take one you already finished, from the square itself. ',
                                          'Open your ' + TYPE_LABELS[t] + ' run', plan.runs[t] && plan.runs[t].url));
            });
            wrap.appendChild(list);
            els.sections.appendChild(wrap);
        }

        function noteItem(text, linkText, href) {
            var li = document.createElement('li');
            li.className = 'pp-cplan__note-item';
            li.appendChild(document.createTextNode(text));
            if (href) {
                var a = document.createElement('a');
                a.className = 'link link-primary';
                a.href = href;
                a.textContent = linkText;
                li.appendChild(a);
            }
            return li;
        }

        // ── shared row pieces (the picker's own, ported) ─────────────────────────────────────────

        function art(row) {
            if (!row.cover) {
                var blank = document.createElement('span');
                blank.className = 'pp-cpick__row-art';
                blank.setAttribute('aria-hidden', 'true');
                return blank;
            }
            var img = document.createElement('img');
            img.className = 'pp-cpick__row-art' + (row.cover_is_art === false ? ' pp-cpick__row-art--icon' : '');
            img.loading = 'lazy';
            img.decoding = 'async';
            img.alt = '';
            img.src = row.cover;
            return img;
        }

        function offerRow(row, noteText, onPick) {
            var item = document.createElement('li');
            var el = document.createElement(onPick ? 'button' : 'div');
            if (onPick) { el.type = 'button'; }
            el.className = 'pp-cpick__row' + (onPick ? '' : ' pp-cpick__row--static');
            el.appendChild(art(row));
            // THE TEXT STACKS in the picker's own column (`.pp-cpick__row-main`): the must-have results lay a card
            // out as a ROW, where name, platforms and note side by side squeezed each other on a phone.
            var main = document.createElement('span');
            main.className = 'pp-cpick__row-main';
            var name = document.createElement('span');
            name.className = 'pp-cpick__row-name';
            name.textContent = row.name;
            main.appendChild(name);
            var tags = rowTags(row);
            if (tags) { main.appendChild(tags); }
            if (noteText) {
                var note = document.createElement('span');
                note.className = 'pp-cpick__row-note';
                note.textContent = noteText;
                main.appendChild(note);
            }
            el.appendChild(main);
            if (onPick) { el.addEventListener('click', function () { onPick(row); }); }
            item.appendChild(el);
            return item;
        }

        /** A card's platforms, then its availability flags, in the run page picker's own `.pp-cpick__row-plats` row
         *  (`challenge-detail.js`'s `rowTags`), so the two sheets' cards read alike; null for a game with neither. */
        function rowTags(row) {
            var plats = row.platforms || [];
            var flags = row.availability || [];
            if (!plats.length && !flags.length) { return null; }
            var wrap = document.createElement('span');
            wrap.className = 'pp-cpick__row-plats';
            plats.forEach(function (p) { wrap.appendChild(platformTag(p)); });
            flags.forEach(function (flag) {
                var alertChip = availabilityChip(flag);
                if (alertChip) { wrap.appendChild(alertChip); }
            });
            return wrap;
        }

        /** A job glyph out of the page's sprite, as `challenge-detail.js` builds it (`createElementNS`: an SVG
         *  built through the HTML parser lands in the wrong namespace and draws nothing). */
        function jobIcon(name) {
            var NS = 'http://www.w3.org/2000/svg';
            var svg = document.createElementNS(NS, 'svg');
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

        // ── Other options sheet ──────────────────────────────────────────────────────────────────

        var dialog = document.getElementById('cplan-alt');
        var alt = dialog ? {
            title: dialog.querySelector('[data-cplan-alt-title]'),
            sub: dialog.querySelector('[data-cplan-alt-sub]'),
            status: dialog.querySelector('[data-cplan-alt-status]'),
            rows: dialog.querySelector('[data-cplan-alt-rows]'),
            tabs: dialog.querySelector('[data-cplan-alt-tabs]'),
            filters: dialog.querySelector('[data-cplan-alt-filters]'),
            badge: dialog.querySelector('[data-cplan-alt-badge]'),
            more: dialog.querySelector('[data-cplan-alt-more]'),
            moreBtn: dialog.querySelector('[data-cplan-alt-more-btn]'),
            body: dialog.querySelector('.pp-cpick__body'),
            foot: dialog.querySelector('[data-cplan-alt-foot]'),
            closeBtn: dialog.querySelector('[data-cplan-alt-close]')
        } : null;
        var altSeq = 0;

        function closeSheet() { closeDialog(dialog); }

        /** Close a dialog on its own exit (`.is-closing`, which both this page's dialogs style), then run `after`.
         *  Callbacks QUEUE and run on the dialog's real `close` event, however it closed (the exit, a swipe, the
         *  fallback timer), so none is ever dropped by a close that was already running. */
        function closeDialog(d, after) {
            if (!d) { return; }
            if (!d.open) { if (after) { after(); } return; }
            // ONCE THE EXIT HAS STARTED it is the hunter's answer: a second tap (a double-click on Confirm, Confirm
            // during Not yet's exit) adds nothing. The CSS also takes the dialog's pointer events away meanwhile.
            if (d.classList.contains('is-closing')) { return; }
            if (after) { (d._afterClose = d._afterClose || []).push(after); }
            var done = function () {
                if (d._closeTimer) { clearTimeout(d._closeTimer); d._closeTimer = null; }
                d.classList.remove('is-closing');
                if (d.open) { d.close(); }
            };
            if (reducedMotion()) { done(); return; }
            d.classList.add('is-closing');
            // TARGET- AND pseudoElement-GUARDED, as in the picker: `animationend` bubbles, and the
            // `::backdrop`'s own exit fires on the dialog with `pseudoElement` set.
            var onEnd = function (e) {
                if (e.target !== d || e.pseudoElement) { return; }
                d.removeEventListener('animationend', onEnd);
                done();
            };
            d.addEventListener('animationend', onEnd);
            d._closeTimer = setTimeout(function () {
                d._closeTimer = null;
                if (d.classList.contains('is-closing')) { d.removeEventListener('animationend', onEnd); done(); }
            }, 400);
        }

        function wireDialogClose(d) {
            d.addEventListener('close', function () {
                var queue = d._afterClose || [];
                d._afterClose = [];
                queue.forEach(function (fn) { fn(); });
            });
        }

        function altSay(text, isError) {
            alt.status.textContent = text || '';
            alt.status.classList.toggle('pp-cpick__status--error', !!isError);
        }

        function openAlternatives(square, line) {
            if (!dialog || busy) { return; }
            // THE LINE'S OTHER SQUARES (owner, 2026-10-17): the sheet is about the whole line, so its title names
            // every square it holds and its tabs say which square each game takes ("C + Gunslinger", "C only",
            // "Gunslinger only"). `current` is the line's own game, which fits both by definition: left out
            // server-side. EVERY game is reachable: each tab pages in as the hunter scrolls.
            var partners = line.squares.filter(function (s) { return s.type !== square.type || s.key !== square.key; });
            alt.title.textContent = 'Other options for '
                + [square].concat(partners).map(function (s) { return s.label; }).join(' and ');
            alt.sub.textContent = 'Pick one and the plan rebuilds around it.';
            alt.rows.textContent = '';
            if (alt.tabs) { alt.tabs.hidden = true; alt.tabs.textContent = ''; }
            // "COUNTS TOWARD YOUR BADGES" resets to off on every open, like the run picker's filters, and is offered
            // only when the plan counts badges at all.
            if (alt.badge) { alt.badge.checked = false; }
            // Under "Badges only" every game already counts, so the chip would do nothing there.
            if (alt.filters) { alt.filters.hidden = !badgesCounted(submitted) || submitted.badge_weight === 'only'; }
            dialog.showModal();
            dialog.focus();
            renderAltFoot(square, partners, line);
            altView = { square: square, line: line, partners: partners, group: null, wanted: null, next: 0,
                        more: false, loading: false, list: null, tabs: null, badgeOnly: false };
            loadAlternatives(altView, null, false);
        }

        //: The sheet as it stands: which square and tab, and where the next page starts. Replaced on every open.
        var altView = null;

        /** Whether these answers count badges at all (any source on). */
        function badgesCounted(a) {
            return !!(a && (a.badge_started || a.badge_all || (a.badge_types || []).length || (a.badge_picks || []).length));
        }

        /** The sheet's tabs, in the server's order: [group, label, what an empty tab says]. */
        function altTabs(view) {
            var sq = view.square.label;
            var other = view.partners.map(function (s) { return s.label; }).join(' and ');
            return [['both', sq + ' + ' + other, 'Nothing else covers ' + sq + ' and ' + other + '.'],
                    ['rest', sq + ' only', 'Nothing else fits only ' + sq + '.'],
                    ['partner', other + ' only', 'Nothing else fits only ' + other + '.']];
        }
        //: How close (px) the Show more control may come to the sheet's bottom before the next page loads itself.
        var ALT_MORE_MARGIN = 240;

        /** One page of one tab. `group` null asks the server for its default tab; `append` adds to the list.
         *  `opts.badgeOnly` asks with the badge filter changed, `opts.fallback` lets the server move to the first
         *  tab with games when the asked one is empty. NOTHING ABOUT THE VIEW CHANGES UNTIL THE REPLY LANDS (the
         *  tab, the filter): a failed reload leaves the sheet agreeing with the list on screen, so the next page
         *  is asked for under the same tab and filter as the pages already drawn. */
        function loadAlternatives(view, group, append, opts) {
            opts = opts || {};
            // A TAB OR FILTER LOAD asks with the chip as it reads NOW (a tab picked while a toggle is still loading
            // must not drop it); a next PAGE asks with the filter already drawn, so it matches the pages above it.
            var badgeOnly = opts.badgeOnly !== undefined ? opts.badgeOnly
                : (!append && alt.badge ? alt.badge.checked : view.badgeOnly);
            var mine = ++altSeq;
            view.loading = true;
            // The tab the hunter last ASKED for, so a filter toggled while a tab switch loads keeps that switch.
            if (!append) { view.wanted = group; }
            syncAltMore();
            if (!append) { window.setTimeout(function () { if (mine === altSeq) { altSay('Loading…'); } }, 0); }
            var body = payload();
            body.type = view.square.type;
            body.key = view.square.key;
            body.partners = view.partners.map(function (s) { return { type: s.type, key: s.key }; });
            body.current = view.line.slug;
            if (group) { body.group = group; }
            if (opts.fallback) { body.fallback = true; }
            body.offset = append ? view.next : 0;
            if (badgeOnly) { body.badge_only = true; }
            PP.API.post(urls.alt, body).then(function (data) {
                if (mine !== altSeq || altView !== view) { return; }
                view.loading = false;
                view.badgeOnly = badgeOnly;
                if (alt.badge) { alt.badge.checked = badgeOnly; }
                view.wanted = null;
                view.group = data.group;
                view.next = data.offset + data.showing;
                view.more = !!data.more;
                if (!append) {
                    if (!data.counts.both && !data.counts.rest && !data.counts.partner) {
                        alt.rows.textContent = '';
                        if (alt.tabs) { alt.tabs.hidden = true; }
                        // The badge chip stays, so a filtered empty sheet can be widened again.
                        altSay(view.badgeOnly ? 'No game here counts toward your badges.'
                            : 'Nothing else on your terms fits '
                              + [view.square].concat(view.partners).map(function (s) { return s.label; }).join(' or ') + '.');
                        syncAltMore();
                        return;
                    }
                    altSay('');
                    renderAltTabs(view, data.counts);
                    alt.rows.textContent = '';
                    view.list = altList([], function () {}, false);
                    alt.rows.appendChild(view.list);
                    if (!data.rows.length) {
                        var none = document.createElement('p');
                        none.className = 'pp-cplan__alt-none';
                        var tab = altTabs(view).filter(function (t) { return t[0] === view.group; })[0];
                        none.textContent = tab ? tab[2] : 'Nothing else fits ' + view.square.label + '.';
                        alt.rows.appendChild(none);
                    }
                }
                appendAlternatives(view, data.rows.filter(function (r) { return r.slug !== view.line.slug; }));
                syncAltMore();
                keepAltFilling();
            }).catch(function (err) {
                if (mine !== altSeq || altView !== view) { return; }
                view.loading = false;
                view.wanted = null;
                // A FAILED TAB SWITCH OR FILTER TOGGLE puts the tabs and the chip back on what is still showing, so
                // Show more pages what is drawn.
                syncAltTabs(view);
                if (alt.badge) { alt.badge.checked = view.badgeOnly; }
                syncAltMore();
                failureText(err, 'Other options did not load.').then(function (msg) { altSay(msg, true); });
            });
        }

        function appendAlternatives(view, rows) {
            var both = view.group === 'both';
            // A "C + Gunslinger" card drops "Also covers": its tab already says which squares it takes.
            var withAlso = !both;
            // WHERE A PICK GOES, by tab: the whole line (pinned to every square of it, or a locked line or an
            // accepted swap would keep its old game on the other square), the opened square, or the partner alone.
            var squares = both ? [view.square].concat(view.partners)
                : view.group === 'partner' ? view.partners : [view.square];
            var pick = function (row) {
                // THE SHEET PLAYS ITS EXIT while the re-plan starts at once; `restoreFocus` waits for the dialog to be
                // really closed (the page is inert until then), so focus still lands.
                closeSheet();
                change(function () { pinGame(row.slug, squares); }, 'line:' + row.slug);
            };
            rows.forEach(function (row) {
                var also = withAlso ? (row.also || []).map(function (s) { return s.label; }) : [];
                view.list.appendChild(offerRow(row, also.length ? 'Also covers ' + also.join(', ') : '', pick));
            });
        }

        /** THE TABS (the house segmented switch, radio backed), only when the line holds two squares: "C +
         *  Gunslinger", "C only" and "Gunslinger only", each with its full count. Switching loads that tab's first
         *  page.
         *
         *  BUILT ONCE PER OPEN, then only re-checked and re-counted: rebuilding on every reply removed the radio a
         *  keyboard hunter had just moved to (an arrow key changes the radio, and fires `change`), so focus fell to
         *  the page and the group could not be walked. */
        function renderAltTabs(view, counts) {
            if (!alt.tabs) { return; }
            alt.tabs.hidden = !view.partners.length;
            if (!view.partners.length) { return; }
            if (view.tabs) {
                Object.keys(view.tabs).forEach(function (g) { view.tabs[g].count.textContent = String(counts[g]); });
                syncAltTabs(view);
                return;
            }
            alt.tabs.textContent = '';
            view.tabs = {};
            altTabs(view).forEach(function (tab) {
                var chip = document.createElement('label');
                chip.className = 'pp-switch__chip';
                var input = document.createElement('input');
                input.type = 'radio';
                input.name = 'cplan_alt_tab';
                input.value = tab[0];
                input.className = 'sr-only';
                input.checked = view.group === tab[0];
                input.addEventListener('change', function () {
                    if (input.checked && altView === view) { loadAlternatives(view, tab[0], false); }
                });
                var text = document.createElement('span');
                text.className = 'pp-switch__lbl';
                text.textContent = tab[1];
                var n = document.createElement('span');
                n.className = 'pp-cplan__chip-n';
                n.textContent = String(counts[tab[0]]);
                chip.appendChild(input);
                chip.appendChild(text);
                chip.appendChild(n);
                alt.tabs.appendChild(chip);
                view.tabs[tab[0]] = { input: input, count: n };
            });
        }

        /** The tabs agree with the tab on screen (`view.group`). */
        function syncAltTabs(view) {
            if (!view.tabs) { return; }
            Object.keys(view.tabs).forEach(function (g) { view.tabs[g].input.checked = g === view.group; });
        }

        /** The Show more control matches the tab: shown while there is more, saying so while a page loads. */
        function syncAltMore() {
            if (!alt.more) { return; }
            alt.more.hidden = !(altView && altView.more);
            if (alt.moreBtn) {
                var loading = !!(altView && altView.loading);
                alt.moreBtn.setAttribute('aria-disabled', loading ? 'true' : 'false');
                alt.moreBtn.textContent = loading ? 'Loading…' : 'Show more';
            }
        }

        function loadMoreAlternatives() {
            var view = altView;
            if (!view || !view.more || view.loading || !dialog.open) { return; }
            loadAlternatives(view, view.group, true);
        }

        /** THE OBSERVER'S BLIND SPOT (the run picker's own fix): it reports a CHANGE in visibility, so a Show more
         *  that never left the view (a short page) would never ask again. Checked after every page. */
        function keepAltFilling() {
            if (!altView || !altView.more || altView.loading || !alt.more || alt.more.hidden || !alt.body) { return; }
            var view = alt.body.getBoundingClientRect();
            var spot = alt.more.getBoundingClientRect();
            if (spot.top - view.bottom < ALT_MORE_MARGIN) { window.requestAnimationFrame(loadMoreAlternatives); }
        }

        /** LEAVE IT OPEN, from the sheet (owner, 2026-10-17): one button per OPEN square of the line (both on a
         *  C + Gunslinger line), for "none of these, I'll fill it myself". An unfinished square is left out: its
         *  game is already there, and Undo swap is how it goes back. When the picks are planned a changed pick offers
         *  Keep instead, and a pick that stays offers nothing (its line has Lock). */
        function renderAltFoot(square, partners, line) {
            if (!alt.foot) { return; }
            alt.foot.textContent = '';
            var replaced = {};
            (line.replaces || []).forEach(function (r) { replaced[sqKey(r)] = r; });
            [square].concat(partners).forEach(function (sq) {
                var old = replaced[sqKey(sq)];
                if (sq.stays || (old && !(replanning() && old.slug))) { return; }
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'pp-cta pp-cta--ghost';
                btn.textContent = old ? 'Keep ' + (old.name || 'your pick') : 'Leave ' + sq.label + ' open';
                if (old) { btn.setAttribute('aria-label', btn.textContent + ' in ' + sq.label); }
                btn.addEventListener('click', function () {
                    if (busy) { return; }
                    closeSheet();
                    change(function () { if (old) { keepPick(old); } else { leaveOpen(sq); } }, 'square:' + sqKey(sq));
                });
                alt.foot.appendChild(btn);
            });
            alt.foot.hidden = !alt.foot.childNodes.length;
        }

        function altList(rows, pick, withAlso) {
            var list = document.createElement('ul');
            list.className = 'pp-cpick__rows';
            list.setAttribute('role', 'list');
            rows.forEach(function (row) {
                var also = withAlso ? (row.also || []).map(function (s) { return s.label; }) : [];
                list.appendChild(offerRow(row, also.length ? 'Also covers ' + also.join(', ') : '', pick));
            });
            return list;
        }

        if (dialog) {
            wireDialogClose(dialog);
            if (alt.badge) {
                alt.badge.addEventListener('change', function () {
                    if (!altView) { return; }
                    // The tab asked for (a switch may still be loading), and if the filter empties it, the first tab
                    // that still has games rather than an empty one.
                    loadAlternatives(altView, altView.wanted || altView.group, false,
                                     { badgeOnly: alt.badge.checked, fallback: true });
                });
            }
            // SHOW MORE, pressed or reached: the button is there for a keyboard and for a reader who would rather ask;
            // scrolling near it loads the next page by itself.
            if (alt.moreBtn) { alt.moreBtn.addEventListener('click', loadMoreAlternatives); }
            if (alt.more && 'IntersectionObserver' in window) {
                new IntersectionObserver(function (entries) {
                    entries.forEach(function (entry) { if (entry.isIntersecting) { loadMoreAlternatives(); } });
                }, { root: alt.body || null, rootMargin: '0px 0px ' + ALT_MORE_MARGIN + 'px 0px' }).observe(alt.more);
            }
            alt.closeBtn.addEventListener('click', closeSheet);
            // ESCAPE GETS THE SAME EXIT AS THE CLOSE BUTTON, not the browser's instant one.
            dialog.addEventListener('cancel', function (e) { e.preventDefault(); closeSheet(); });
            // A press on the backdrop lands on the dialog element itself.
            dialog.addEventListener('click', function (e) { if (e.target === dialog) { closeSheet(); } });
            dialog.addEventListener('close', function () { altSeq++; });
            if (PP.dismissableSheet) {
                PP.dismissableSheet(dialog, {
                    handle: '.pp-cpick__head',
                    // DIRECT, as the picker learned: the helper has already animated the sheet away.
                    onClose: function () { if (dialog.open) { dialog.close(); } }
                });
            }
        }

        // ── different games, apply ───────────────────────────────────────────────────────────────

        if (els.reroll) {
            els.reroll.addEventListener('click', function () {
                if (!plan || busy) { return; }
                if (tweaks.penalized.size >= MAX_PENALIZED) {
                    sayFailure("That's every alternative this plan can reach. Change your answers for a wider pool.");
                    return;
                }
                change(function () {
                    plan.lines.forEach(function (line) {
                        // A pick that stays is the hunter's own game, never a suggestion to reroll.
                        var theirs = line.squares.some(function (sq) { return sq.stays; });
                        if (!theirs && !isLocked(line) && !line.pinned && tweaks.penalized.size < MAX_PENALIZED) {
                            tweaks.penalized.add(line.slug);
                        }
                    });
                });
            });
        }

        if (els.apply) {
            els.apply.addEventListener('click', function () {
                if (!plan || busy || !squareCount()) { return; }
                var lines = [];
                plan.lines.forEach(function (line) {
                    line.squares.filter(changes).forEach(function (sq) {
                        lines.push({ type: sq.type, key: sq.key, contract: line.slug,
                                     replacing: (line.replacing || {})[sq.type] || '' });
                    });
                });
                var mine = ++seq;
                var unlabel = busyLabel(els.apply, 'Filling…');
                setBusy(true);
                say('Filling your squares…');
                barSay('Filling your squares…');
                PP.API.post(urls.apply, { lines: lines }).then(function (data) {
                    if (mine !== seq) { return; }
                    showApplied(data);
                }).catch(function (err) {
                    if (mine !== seq) { return; }
                    failureText(err, 'Nothing was applied. Try again in a moment.').then(function (msg) { sayFailure(msg); });
                }).then(function () {
                    if (mine !== seq) { return; }
                    unlabel();
                    barSay('');
                    setBusy(false);
                });
            });
        }

        if (els.again) {
            els.again.addEventListener('click', function () {
                if (busy || !submitted) { return; }
                tweaks = freshTweaks();
                replan({ fromWizard: true });
            });
        }

        function refreshOpenCounts(runs) {
            Object.keys(runs).forEach(function (t) {
                var open = runs[t].total_slots - runs[t].filled_count;
                var sub = root.querySelector('[data-cplan-open-for="' + t + '"]');
                if (sub) { sub.textContent = plural(open, 'open square', 'open squares'); }
            });
            recountOpen(true);
        }

        /** The header tally: the sum of every run card's open count, shown once any run exists. `tick`: count from
         *  the old figure to the new one (an Apply fills squares, a Start adds them), the run page's own beat. */
        function recountOpen(tick) {
            var total = 0;
            root.querySelectorAll('[data-cplan-open-for]').forEach(function (sub) { total += parseInt(sub.textContent, 10) || 0; });
            if (els.openTotal) {
                // FROM THE ATTRIBUTE, never the text: the text may be mid-count (see `challenge-detail.js`).
                var was = parseInt(els.openTotal.dataset.countup || '', 10);
                els.openTotal.dataset.countup = String(total);
                if (tick && PP.countUp && !isNaN(was)) {
                    PP.countUp(els.openTotal, 700, { from: was });
                    // AND REASSERT IT once any loop that could be running has ended (the boot's 900ms count, a second
                    // Start a moment later): `countUp` cannot cancel, and the last loop to finish wins otherwise.
                    // The run page's own fix (`challenge-detail.js`).
                    window.setTimeout(function () {
                        if (els.openTotal.dataset.countup === String(total)) { els.openTotal.textContent = String(total); }
                    }, 1000);
                } else { els.openTotal.textContent = total; }
            }
            var label = root.querySelector('[data-cplan-open-label]');
            if (label) { label.textContent = total === 1 ? 'open square' : 'open squares'; }
            var tally = root.querySelector('[data-cplan-tally]');
            if (tally) { tally.hidden = !root.querySelector('[data-cplan-type]'); }
        }

        function showApplied(data) {
            var skipped = data.results.filter(function (r) { return r.status !== 'applied'; });
            forgetApplied(data.results);
            plan = null;
            if (sortBar) { sortBar.hidden = true; }
            tweaks = freshTweaks();
            // The just-applied must-haves are on the board now; carrying them would report them as unplaced.
            pins = [];
            renderPinned();
            els.summaryText.textContent = summaryLine(submitted);
            Object.keys(data.cards || {}).forEach(function (t) { swapCard(t, data.cards[t]); });
            syncPicks();
            refreshOpenCounts(data.runs);
            els.stats.textContent = '';
            els.sections.textContent = '';
            els.reroll.hidden = true;
            els.apply.hidden = true;
            els.again.hidden = false;
            var wrap = sectionShell(plural(data.applied, 'square', 'squares') + ' filled');
            wrap.classList.add('pp-cplan__done');
            // THE PAGE'S ONE SIGNATURE BEAT, and deliberately not a ceremony: a check that traces itself in beside
            // the heading while the result settles and the header's open count ticks down.
            if (data.applied) { wrap.querySelector('.pp-cplan__section-head').prepend(doneCheck()); }
            wrap.appendChild(runLinks(data.runs, function (t, r) {
                return 'Open ' + (TYPE_LABELS[t] || t) + ' (' + r.filled_count + '/' + r.total_slots + ')';
            }));
            if (skipped.length) {
                wrap.appendChild(hint(plural(skipped.length, 'line', 'lines') + ' skipped:'));
                var list = document.createElement('ul');
                list.setAttribute('role', 'list');
                skipped.forEach(function (r) {
                    var li = document.createElement('li');
                    li.className = 'pp-cplan__note-item';
                    li.textContent = r.label + (r.name ? ' (' + r.name + ')' : '') + ': ' + r.reason;
                    list.appendChild(li);
                });
                wrap.appendChild(list);
            }
            els.sections.appendChild(wrap);
            settleIn(wrap);
            say(skipped.length ? plural(data.applied, 'square', 'squares') + ' filled, ' + skipped.length + ' skipped.'
                               : plural(data.applied, 'square', 'squares') + ' filled.', !data.applied);
            syncBar();
            if (els.reviewHead) { els.reviewHead.focus({ preventScroll: true }); }
        }

        /** The Apply result's check, traced in (`.pp-draw-in`; `pathLength` makes the dash math uniform). */
        function doneCheck() {
            var NS = 'http://www.w3.org/2000/svg';
            var svg = document.createElementNS(NS, 'svg');
            svg.setAttribute('viewBox', '0 0 24 24');
            svg.setAttribute('fill', 'none');
            svg.setAttribute('stroke', 'currentColor');
            svg.setAttribute('stroke-width', '2.5');
            svg.setAttribute('stroke-linecap', 'round');
            svg.setAttribute('stroke-linejoin', 'round');
            svg.setAttribute('aria-hidden', 'true');
            svg.setAttribute('class', 'pp-cplan__done-check pp-draw-in');
            var circle = document.createElementNS(NS, 'circle');
            circle.setAttribute('cx', '12'); circle.setAttribute('cy', '12'); circle.setAttribute('r', '10');
            circle.setAttribute('pathLength', '1');
            var tick = document.createElementNS(NS, 'path');
            tick.setAttribute('d', 'm8 12.5 2.5 2.5 5.5-6');
            tick.setAttribute('pathLength', '1');
            svg.appendChild(circle);
            svg.appendChild(tick);
            return svg;
        }

        // ── status ───────────────────────────────────────────────────────────────────────────────

        function say(text, isError) {
            if (!els.status) { return; }
            els.status.textContent = text || '';
            els.status.classList.toggle('pp-cplan__status--error', !!isError);
        }

        function failureText(err, fallback) {
            if (PP.API.failureOr) { return PP.API.failureOr(err, fallback).catch(function () { return fallback; }); }
            return Promise.resolve(fallback);
        }

        // THE HEADER TALLY COUNTS UP ON ARRIVAL, the run page's own opening beat.
        if (els.openTotal && PP.countUp && parseInt(els.openTotal.dataset.countup || '0', 10) > 0) { PP.countUp(els.openTotal, 900); }

        goTo(0);
    }

    if (PP.onPageReady) { PP.onPageReady(boot); } else { document.addEventListener('DOMContentLoaded', boot); }
})();
