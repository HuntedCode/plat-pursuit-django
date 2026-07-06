/**
 * ClaimCeremony -- the "what just happened" payoff when a Pursuer banks Contract XP.
 *
 * A rare, deliberate moment (you claim seldom, especially the forced onboarding "claim all"),
 * so it earns its own full-screen overlay: the award arrives, the XP flows into the jobs it
 * leveled, their bars fill and their levels tick up. Driven ENTIRELY by the server's `claim`
 * payload (see contract_service.claim) -- no DB, no reads -- so the same code powers the real
 * claim flow AND the DEBUG replay harness.
 *
 * Usage:  PlatPursuit.ClaimCeremony.play(payload)  -> Promise (resolves when dismissed)
 *
 * Payload: {xp, accepted:[slug], first_claim, rank_now,
 *           jobs:[{slug,name,disc,icon,xp,from_level,to_level,from_frac,to_frac,tiers:[{key,name}]}],
 *           pursuer:{from_level,to_level,ranks:[{key,name}]}}
 *
 * Composes the kit's signature primitives rather than redrawing them: the award + per-job numbers
 * are Tally (.pp-tally), the job bars are Horizon (.pp-horizon, themed to each job's discipline
 * colour) -- the same treatments the Career page's Job rows use. Icons are referenced from the
 * page's job-icon sprite (`{% job_icon_sprite %}`), so this only runs on surfaces that emit it
 * (today: the Career page). No confetti: particles are reserved for the weld-spark / scan-beam earn
 * vocabulary (visual-identity.md §3), which the Phase 3 promotion peaks will use.
 */
(function () {
    'use strict';
    var PP = (window.PlatPursuit = window.PlatPursuit || {});

    function reduced() {
        return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }
    var easeOut = function (t) { return 1 - Math.pow(1 - t, 3); };

    // rAF number count-up (self-contained; career.html's ppCountUp isn't in this scope). `alive`
    // (optional) lets the caller stop the loop early if the ceremony was dismissed mid-count.
    function countUp(el, to, dur, alive) {
        return new Promise(function (resolve) {
            var start = null;
            function step(ts) {
                if (alive && !alive()) return resolve();
                if (start === null) start = ts;
                var p = Math.min(1, (ts - start) / dur);
                el.textContent = Math.round(to * easeOut(p)).toLocaleString();
                if (p < 1) requestAnimationFrame(step); else resolve();
            }
            requestAnimationFrame(step);
        });
    }

    // Interpolate el's integer from -> to over dur (used for the footer "X to next" counting down as
    // the rank bar fills). Bails to `to` if the ceremony is dismissed.
    function countTo(el, from, to, dur, alive) {
        var start = null, span = to - from;
        function step(ts) {
            if (alive && !alive()) { el.textContent = to; return; }
            if (start === null) start = ts;
            var p = Math.min(1, (ts - start) / dur);
            el.textContent = Math.round(from + span * easeOut(p)).toLocaleString();
            if (p < 1) requestAnimationFrame(step); else el.textContent = to;
        }
        requestAnimationFrame(step);
    }

    // Drive a Horizon bar (the kit's progress primitive) to `pct` by setting --horizon-progress on
    // the .pp-horizon element; the .pp-horizon__fill child carries the width transition. `animate=false`
    // snaps instantly (used to reset to 0 between level bands). Resolves on the fill's transitionend,
    // with a safety timeout for the no-change case.
    function fillTo(barEl, pct, animate) {
        var fill = barEl.querySelector('.pp-horizon__fill');
        return new Promise(function (resolve) {
            if (!animate) {
                if (fill) fill.style.transition = 'none';
                barEl.style.setProperty('--horizon-progress', pct + '%');
                requestAnimationFrame(function () { if (fill) fill.style.transition = ''; resolve(); });
                return;
            }
            var settled = false;
            function done(e) {
                if (e && e.propertyName !== 'width') return;
                if (settled) return;
                settled = true;
                if (fill) fill.removeEventListener('transitionend', done);
                resolve();
            }
            if (fill) fill.addEventListener('transitionend', done);
            requestAnimationFrame(function () { barEl.style.setProperty('--horizon-progress', pct + '%'); });
            setTimeout(done, 700);   // width may not change (already there) -> transitionend never fires
        });
    }

    function pop(el, cls) {
        el.classList.remove(cls);
        void el.offsetWidth;   // reflow so the animation replays every level
        el.classList.add(cls);
    }

    var wait = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };

    // Hot arcing sparks (the kit's fabrication vocabulary -- NOT confetti). Spawned into the overlay's
    // fixed spark layer at a viewport point; each arcs up-and-out then falls under "gravity" and fades.
    // `color` is any CSS colour string (a discipline var for tier blooms, the primary accent for ranks).
    function sparkBurst(layer, cx, cy, color, n) {
        if (!layer) return;
        for (var i = 0; i < n; i++) {
            var s = document.createElement('span');
            s.className = 'ccx__spark';
            s.style.left = cx + 'px'; s.style.top = cy + 'px';
            s.style.setProperty('--sc', color);
            layer.appendChild(s);
            var ang = (-90 + (Math.random() * 120 - 60)) * Math.PI / 180;   // upward spread
            var dist = 26 + Math.random() * 46;
            var dx = Math.cos(ang) * dist, dy = Math.sin(ang) * dist;
            var fall = Math.abs(dy) + 34 + Math.random() * 54;             // arc back down under gravity
            var anim = s.animate([
                { transform: 'translate(0,0) scale(1)', opacity: 1 },
                { transform: 'translate(' + dx + 'px,' + dy + 'px) scale(0.9)', opacity: 1, offset: 0.35 },
                { transform: 'translate(' + (dx * 1.35) + 'px,' + fall + 'px) scale(0.25)', opacity: 0 },
            ], { duration: 560 + Math.random() * 320, easing: 'cubic-bezier(0.2, 0.55, 0.35, 1)' });
            anim.onfinish = (function (node) { return function () { node.remove(); }; })(s);
        }
    }

    // A tier promotion, fired while the level counter is PAUSED on the threshold level: hot sparks off
    // the (frozen, flaring) level chip + a callout that names the exact level -> "Lv 10  APPRENTICE" --
    // so the crossing reads as caused by hitting that level, not a random pop.
    function bloomTier(tile, tierName, level, rank, discKey) {
        var lv = tile.querySelector('.ccx__lv');
        var root = tile.closest('.ccx');
        var layer = root && root.querySelector('.ccx__sparks');
        var r = lv.getBoundingClientRect();
        // Escalate toward Legend: Apprentice (rank 1) ~11 sparks .. Legend (rank 7) ~29. The deeper the
        // tier, the bigger the burst -- these crossings are rarer and harder-won.
        var n = 8 + (rank || 1) * 3;
        sparkBurst(layer, r.left + r.width / 2, r.top + r.height / 2, 'var(--disc-' + discKey + ', var(--pp-primary))', n);
        var stamp = document.createElement('div');
        stamp.className = 'ccx__tierstamp';
        var lvTag = document.createElement('span');
        lvTag.className = 'ccx__ts-lv';
        lvTag.textContent = 'Lv ' + Number(level);       // states the cause explicitly
        var tierTag = document.createElement('span');
        tierTag.className = 'ccx__ts-name';
        tierTag.textContent = tierName;                  // textContent = XSS-safe
        stamp.appendChild(lvTag);
        stamp.appendChild(tierTag);
        tile.appendChild(stamp);
        requestAnimationFrame(function () { stamp.classList.add('is-in'); });
        setTimeout(function () {
            stamp.classList.add('is-out');
            setTimeout(function () { if (stamp.parentNode) stamp.remove(); }, 380);
        }, 1150);
    }

    // Tick the level number from -> to over `dur` (a quick climb when several levels pass between the
    // meaningful stops). Resolves when it lands on `to`.
    function spinNumber(lvn, from, to, dur) {
        return new Promise(function (resolve) {
            var steps = to - from;
            if (steps <= 0) { lvn.textContent = to; resolve(); return; }
            var i = 0, per = Math.max(28, dur / steps);
            var id = setInterval(function () {
                i += 1;
                lvn.textContent = from + i;
                if (i >= steps) { clearInterval(id); resolve(); }
            }, per);
        });
    }

    // Run a job tile: the bar fills honestly while the level number climbs, and it PAUSES on each
    // tier-boundary level to bloom the promotion -- so a tier-up reads as caused by hitting that exact
    // level, never a random pop. Tier boundaries + the final level are the "stops"; when many levels
    // pass between stops the number spins quickly through them (they're not the meaningful beats).
    function runJob(tile, job, isReduced) {
        var fill = tile.querySelector('.pp-horizon');   // the Horizon bar (fillTo drives --horizon-progress)
        var lvn = tile.querySelector('.ccx__lvn');
        var lv = tile.querySelector('.ccx__lv');
        var gained = job.to_level - job.from_level;
        lvn.textContent = job.from_level;

        if (isReduced || gained <= 0) {
            lvn.textContent = job.to_level;
            // Seed the starting band first so a within-level nudge sweeps from_frac -> to_frac
            // ("start where it was"), not 0 -> to_frac. Reduced motion just lands on to_frac.
            return fillTo(fill, job.from_frac * 100, false)
                .then(function () { return fillTo(fill, job.to_frac * 100, !isReduced); });
        }

        // The stops: each tier boundary crossed (a deliberate beat) then the final level.
        var stops = [];
        (job.tiers || []).forEach(function (t) {
            if (t.level > job.from_level && t.level <= job.to_level) stops.push({ level: t.level, name: t.name, rank: t.rank });
        });
        if (!stops.length || stops[stops.length - 1].level !== job.to_level) stops.push({ level: job.to_level, name: null });

        var chain = fillTo(fill, job.from_frac * 100, false);   // seed the starting band, no animation
        var prev = job.from_level;
        stops.forEach(function (stop, si) {
            var isLast = si === stops.length - 1;
            var from = prev;
            chain = chain
                // climb to this stop: fill the bar to full while the number spins up to `stop.level`
                .then(function () { return Promise.all([fillTo(fill, 100, true), spinNumber(lvn, from, stop.level, 440)]); })
                .then(function () {
                    lvn.textContent = stop.level;
                    if (stop.name) {                       // a tier boundary -> stop, flare, name it, hold
                        pop(lv, 'is-threshold');
                        if (!isReduced) bloomTier(tile, stop.name, stop.level, stop.rank, job.disc);
                        return wait(620);
                    }
                    pop(lv, 'is-levelup');                 // an ordinary final level -> a light pop
                })
                .then(function () {
                    return isLast ? fillTo(fill, job.to_frac * 100, true)   // settle into the final band
                                  : fillTo(fill, 0, false);                 // reset to climb toward the next stop
                });
            prev = stop.level;
        });
        return chain;
    }

    function eyebrow(p) {
        if (p.first_claim) return 'Your Pursuit begins';
        var n = (p.accepted || []).length;
        return n > 1 ? (n + ' Contracts claimed') : 'Contract claimed';
    }

    function buildTile(job) {
        var disc = 'var(--disc-' + job.disc + ', var(--pp-primary))';
        // Sprite ids are a fixed [a-z0-9-] enum (Job.icon); sanitize before it reaches innerHTML so
        // the <use> reference can never carry markup even if the field is ever mis-sourced.
        var icon = String(job.icon || '').replace(/[^a-z0-9-]/g, '');
        var li = document.createElement('li');
        li.className = 'ccx__job';
        li.style.setProperty('--disc', disc);
        // Compose the kit primitives (visual-identity.md §3): the bar is a Horizon (themed tone,
        // accent = the job's discipline family colour, exactly as the Job rows do); the numbers are
        // Tally. The ceremony arranges primitives, it never redraws them.
        li.innerHTML =
            '<span class="ccx__ji"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
                'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
                '<use href="#jobicon-' + icon + '"/></svg></span>' +
            '<div class="ccx__jbody">' +
                '<div class="ccx__jrow">' +
                    '<span class="ccx__jname"></span>' +
                    '<span class="ccx__jgain pp-tally">+' + Number(job.xp).toLocaleString() + '</span>' +
                '</div>' +
                '<div class="ccx__jtier"></div>' +   // resting tier subtitle (revealed at settle)
                '<div class="ccx__bar pp-horizon pp-horizon__track" style="--horizon-accent: var(--disc); --horizon-progress: 0%;">' +
                    '<span class="pp-horizon__fill"></span>' +
                '</div>' +
            '</div>' +
            '<span class="ccx__lv"><span class="ccx__lvn pp-tally">' + Number(job.from_level) + '</span></span>';
        li.querySelector('.ccx__jname').textContent = job.name;   // textContent = XSS-safe
        // Resting-tier subtitle. If the job IMPROVED its tier this claim, mark it (up-chevron + flare)
        // so the eye lands on which tiers actually moved -- not just what tier each job is.
        var tierEl = li.querySelector('.ccx__jtier');
        if (job.tiers && job.tiers.length) { tierEl.classList.add('is-up'); tierEl.innerHTML = CHEV_UP; }
        var tierName = document.createElement('span');
        tierName.className = 'ccx__jtier-name';
        tierName.textContent = job.tier || '';    // textContent = XSS-safe
        tierEl.appendChild(tierName);
        return li;
    }

    var PAGE_SIZE = 5;   // jobs per page -- a page of 5 keeps the roomy list; more jobs paginate
    var CHEVRON = {
        prev: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>',
        next: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>',
    };
    // Up-chevron marking a tier that IMPROVED this claim (on the resting-tier subtitle).
    var CHEV_UP = '<svg class="ccx__jtier-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 15 6-6 6 6"/></svg>';

    function chunk(arr, size) {
        var out = [];
        for (var i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
        return out;
    }

    // Snap a tile straight to its finished state (manual page flips + reduced motion): no bar sweep,
    // no level ticks -- just the final band + final level.
    function settleJob(tile, job) {
        var bar = tile.querySelector('.pp-horizon');
        var fillEl = bar.querySelector('.pp-horizon__fill');
        tile.querySelector('.ccx__lvn').textContent = job.to_level;
        if (fillEl) fillEl.style.transition = 'none';
        bar.style.setProperty('--horizon-progress', (job.to_frac * 100) + '%');
        if (fillEl) requestAnimationFrame(function () { fillEl.style.transition = ''; });
    }

    // Build a page's tiles into the container (opacity 0, not yet revealed). Building page 1 up front
    // is what lets the box open at its final height instead of popping when tiles arrive.
    function buildPage(container, pageJobs) {
        container.innerHTML = '';
        return pageJobs.map(function (job, i) {
            var t = buildTile(job);
            t.style.setProperty('--i', i);
            container.appendChild(t);
            return t;
        });
    }
    // Play the cascade on already-built tiles (reveal + honest bar run). Returns a promise.
    function revealPage(tiles, pageJobs) {
        return Promise.all(tiles.map(function (t, i) {
            return wait(i * 90).then(function () {
                t.classList.add('is-in');
                return wait(150).then(function () { return runJob(t, pageJobs[i], false); });
            });
        }));
    }
    // Snap already-built tiles straight to their finished state (manual flips + reduced motion). Tiles
    // appear instantly (transition off) so the page-flip motion is carried by the container as one
    // cohesive block (the shared-axis slide), not by each tile animating on its own.
    function settlePage(tiles, pageJobs) {
        tiles.forEach(function (t, i) {
            t.style.transition = 'none';
            t.classList.add('is-in');
            settleJob(t, pageJobs[i]);
            var tr = t.querySelector('.ccx__jtier');   // already-settled page: tier shows instantly
            if (tr) { tr.style.transition = 'none'; tr.style.opacity = '1'; tr.style.transform = 'none'; }
        });
    }

    // Build the Pursuer rank ladder (.pgl--rank) from the payload -- same markup the hero uses, so its
    // built-in pglDraw/pglBloom/pglTicks reveal auto-plays on mount (the "fills to your rank" animation).
    function buildLadder(ladder) {
        var wrap = document.createElement('div');
        wrap.className = 'pgl pgl--rank';
        if (ladder.current && ladder.current.key) wrap.style.setProperty('--pgl-cur-c', 'var(--rank-' + ladder.current.key + ')');
        var track = document.createElement('div');
        track.className = 'pgl__track';
        (ladder.rungs || []).forEach(function (r, i) {
            var rung = document.createElement('span');
            rung.className = 'pgl__rung' + (r.current ? ' is-current' : '') + (r.reached ? ' is-on' : '');
            rung.style.setProperty('--f', (r.current ? ladder.fill : (r.reached ? 100 : 0)) + '%');
            rung.style.setProperty('--i', i);
            // Only the CURRENT rung takes its rank colour; reached rungs stay the cyan default (a clean
            // brand trail + a coloured "you are here"). --pgl-cur-c below colours the label to match.
            if (r.current && r.key) rung.style.setProperty('--rung-c', 'var(--rank-' + r.key + ')');
            if (r.current && ladder.current && ladder.current.division) {
                var divs = document.createElement('span');
                divs.className = 'pgl__divs';
                rung.appendChild(divs);
            }
            track.appendChild(rung);
        });
        wrap.appendChild(track);
        var foot = document.createElement('div');
        foot.className = 'pgl__foot';
        var cur = document.createElement('span');
        cur.className = 'pgl__cur';
        cur.textContent = (ladder.current && ladder.current.label) || '';
        var next = document.createElement('span');
        next.className = 'pgl__next';
        var c = ladder.current || {};
        if (c.next_label) {
            var nnum = document.createElement('b'); nnum.className = 'pgl__next-n'; nnum.textContent = c.levels_to_next;
            next.appendChild(nnum);
            next.appendChild(document.createTextNode(' to ' + c.next_label));
        } else {
            next.textContent = 'Apex reached';
        }
        foot.appendChild(cur); foot.appendChild(next);
        wrap.appendChild(foot);
        return wrap;
    }

    function build(payload) {
        var root = document.createElement('div');
        root.className = 'ccx';
        root.setAttribute('role', 'dialog');
        root.setAttribute('aria-modal', 'true');
        root.setAttribute('aria-label', 'Reward claimed');

        root.innerHTML =
            '<div class="ccx__scrim"></div>' +
            '<div class="ccx__stage" role="document">' +
                '<div class="ccx__glow" aria-hidden="true"></div>' +
                '<div class="ccx__eyebrow"></div>' +
                '<div class="ccx__award">' +
                    '<span class="ccx__plus pp-tally">+</span>' +
                    '<span class="ccx__xp pp-tally pp-tally--hero pp-tally--glow">0</span>' +
                    '<span class="ccx__unit pp-tally">XP</span>' +
                '</div>' +
                '<ul class="ccx__jobs"></ul>' +
                '<div class="ccx__pager" hidden>' +
                    '<button type="button" class="ccx__pg ccx__pg--prev" aria-label="Previous page" disabled>' + CHEVRON.prev + '</button>' +
                    '<div class="ccx__dots"></div>' +
                    '<button type="button" class="ccx__pg ccx__pg--next" aria-label="Next page" disabled>' + CHEVRON.next + '</button>' +
                '</div>' +
                '<div class="ccx__summary" hidden></div>' +
                '<div class="ccx__rankbar"></div>' +   // resting Pursuer rank ladder (built at settle)
                '<div class="ccx__foot">' +
                    '<button type="button" class="ccx__done btn btn-primary btn-sm">Continue</button>' +
                '</div>' +
                '<div class="ccx__finale" hidden aria-hidden="true">' +   // Pursuer screen: the rank hand-off
                    '<div class="ccx__finale-flood ccx__finale-flood--from" aria-hidden="true"></div>' +   // old rank colour floods up
                    '<div class="ccx__finale-flood ccx__finale-flood--to" aria-hidden="true"></div>' +     // ...swaps to new at the hand-off
                    '<div class="ccx__finale-scan" aria-hidden="true"></div>' +
                    '<div class="ccx__finale-body">' +
                        '<div class="ccx__finale-eyebrow"></div>' +
                        '<div class="ccx__handoff">' +
                            '<div class="ccx__ho-from pp-tally"></div>' +
                            '<div class="ccx__ho-arrow" aria-hidden="true">' + CHEV_UP + CHEV_UP + CHEV_UP + '</div>' +
                            '<div class="ccx__ho-to pp-tally pp-tally--xl"></div>' +
                        '</div>' +
                        '<button type="button" class="ccx__finale-done btn btn-primary btn-sm">Continue</button>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="ccx__sparks" aria-hidden="true"></div>';   // fixed spark layer (over everything)
        root.querySelector('.ccx__eyebrow').textContent = eyebrow(payload);
        buildSummary(root.querySelector('.ccx__summary'), payload);
        // Build the resting rank ladder NOW (invisible) so the box reserves its exact height up front
        // -- no downward shift when it appears. A rank-up shows the OLD rank (filled) first; else the new.
        var pr0 = payload.pursuer;
        var lad0 = pr0 && ((pr0.rank_up && pr0.ladder_pre) ? pr0.ladder_pre : pr0.ladder);
        if (lad0) root.querySelector('.ccx__rankbar').appendChild(buildLadder(lad0));
        return root;
    }

    // End-of-claim recap: "Tiers earned  Apprentice x2 . Adept x1" (aggregated across all jobs). Kept
    // in the DOM from the start (space reserved) but revealed only at settle.
    function buildSummary(el, payload) {
        var tally = {};   // name -> count, preserving first-seen (ascending tier) order
        (payload.jobs || []).forEach(function (j) {
            (j.tiers || []).forEach(function (t) { tally[t.name] = (tally[t.name] || 0) + 1; });
        });
        var names = Object.keys(tally);
        if (!names.length) return;   // stays hidden -- no tiers crossed this claim
        var label = document.createElement('span');
        label.className = 'ccx__summary-label';
        label.textContent = 'Tiers earned';
        el.appendChild(label);
        names.forEach(function (name) {
            var chip = document.createElement('span');
            chip.className = 'ccx__summary-chip';
            chip.textContent = tally[name] > 1 ? (name + ' ×' + tally[name]) : name;
            el.appendChild(chip);
        });
        el.hidden = false;
    }

    function play(payload) {
        if (!payload || !payload.jobs || !payload.jobs.length) return Promise.resolve();
        var isReduced = reduced();
        var root = build(payload);
        document.body.appendChild(root);
        document.body.classList.add('ccx-open');
        var zoom = document.getElementById('zoom-container');   // the page steps back behind the ceremony
        if (zoom && !isReduced) zoom.classList.add('pp-receded');   // same depth cue the modals use (content only)

        var xpEl = root.querySelector('.ccx__xp');
        var jobsEl = root.querySelector('.ccx__jobs');
        var pagerEl = root.querySelector('.ccx__pager');
        var dotsEl = root.querySelector('.ccx__dots');
        var prevBtn = root.querySelector('.ccx__pg--prev');
        var nextBtn = root.querySelector('.ccx__pg--next');
        var doneBtn = root.querySelector('.ccx__done');
        var lastFocus = document.activeElement;

        var pages = chunk(payload.jobs, PAGE_SIZE);
        var pageIdx = 0;
        var interactive = false;   // the arrows go live only once every page has auto-played
        var flipping = false;      // one shared-axis flip at a time
        var torn = false;
        var resolveDismiss;
        var dismissed = new Promise(function (r) { resolveDismiss = r; });

        // ---- pager (only when the claim spans more than one page) ----
        if (pages.length > 1) {
            pages.forEach(function (_ignored, i) {
                var d = document.createElement('button');
                d.type = 'button';
                d.className = 'ccx__dot';
                d.tabIndex = -1;   // a mouse convenience; keyboard users page with the arrows
                d.setAttribute('aria-label', 'Page ' + (i + 1));
                d.addEventListener('click', function () { flipTo(i); });   // flipTo hoisted below
                dotsEl.appendChild(d);
            });
            pagerEl.hidden = false;   // reserves its space now (revealed at settle); no layout shift later
        }

        // Build page 1 immediately (tiles invisible) and LOCK the container to that height, so the box
        // opens at its final size -- no "pop" when tiles arrive -- and shorter pages don't shrink it.
        var firstTiles = buildPage(jobsEl, pages[0]);
        jobsEl.style.minHeight = jobsEl.offsetHeight + 'px';
        function updatePager() {
            var dots = dotsEl.children;
            for (var i = 0; i < dots.length; i++) dots[i].classList.toggle('is-on', i === pageIdx);
            prevBtn.disabled = !interactive || pageIdx === 0;
            nextBtn.disabled = !interactive || pageIdx === pages.length - 1;
        }
        function fadeOut() {
            if (torn) return Promise.resolve();
            jobsEl.classList.add('is-out');
            return wait(150).then(function () { jobsEl.classList.remove('is-out'); });
        }
        // Manual review flip: a Material "shared axis" (X) transition -- the outgoing page accelerates
        // off toward the pressed direction, the incoming page decelerates in from the opposite side, as
        // one block. Quick exit (accelerate) + soft arrival (emphasized decelerate) = the premium read.
        function flipTo(p) {
            if (torn || !interactive || flipping || p < 0 || p >= pages.length || p === pageIdx) return;
            var dir = p > pageIdx ? 1 : -1;   // next -> content moves left; prev -> right
            if (isReduced) { pageIdx = p; updatePager(); settlePage(buildPage(jobsEl, pages[p]), pages[p]); return; }
            flipping = true;
            jobsEl.style.animation = (dir > 0 ? 'ccxPageOutL' : 'ccxPageOutR') + ' 90ms cubic-bezier(0.3, 0, 0.8, 0.15) both';
            wait(90).then(function () {
                if (torn) { flipping = false; return; }
                pageIdx = p; updatePager();
                settlePage(buildPage(jobsEl, pages[p]), pages[p]);   // build the new 5 already at rest
                jobsEl.style.animation = (dir > 0 ? 'ccxPageInR' : 'ccxPageInL') + ' 260ms cubic-bezier(0.05, 0.7, 0.1, 1) both';
                return wait(260);
            }).then(function () {
                flipping = false;              // always release, even if dismissed mid-flip
                if (torn) return;
                jobsEl.style.animation = '';   // release; rest at natural state
            });
        }
        prevBtn.addEventListener('click', function () { flipTo(pageIdx - 1); });
        nextBtn.addEventListener('click', function () { flipTo(pageIdx + 1); });

        // ---- teardown + focus trap ----
        function teardown() {
            if (torn) return;                 // Continue + scrim + Escape all route here; run once
            torn = true;
            root.classList.remove('is-in');
            document.body.classList.remove('ccx-open');
            if (zoom) zoom.classList.remove('pp-receded');
            document.removeEventListener('keydown', onKey);
            var finish = function () {
                if (root.parentNode) root.parentNode.removeChild(root);
                if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) { /* gone */ } }
                resolveDismiss();
            };
            if (isReduced) finish(); else setTimeout(finish, 240);
        }
        // Escape dismisses; arrows flip pages; Tab is trapped inside the dialog (aria-modal alone
        // doesn't stop the browser walking Tab onto the board still behind the scrim).
        function onKey(e) {
            if (e.key === 'Escape') { teardown(); return; }
            if (e.key === 'ArrowLeft' && interactive) { flipTo(pageIdx - 1); return; }
            if (e.key === 'ArrowRight' && interactive) { flipTo(pageIdx + 1); return; }
            if (e.key === 'Tab') {
                // While the finale takeover is up, trap to ITS control only (the footer/pager sit behind it).
                var fin = root.querySelector('.ccx__finale');
                var sel = (fin && !fin.hidden) ? '.ccx__finale-done' : '.ccx__done, .ccx__pg';
                var f = Array.prototype.filter.call(
                    root.querySelectorAll(sel),
                    function (el) { return !el.disabled && el.offsetParent !== null; });
                if (!f.length) return;
                e.preventDefault();
                var i = f.indexOf(document.activeElement);
                var to = e.shiftKey ? (i <= 0 ? f.length - 1 : i - 1) : (i >= f.length - 1 ? 0 : i + 1);
                f[to].focus();
            }
        }
        doneBtn.addEventListener('click', function () { if (interactive) teardown(); });   // inert until controls are live
        root.querySelector('.ccx__scrim').addEventListener('click', teardown);
        document.addEventListener('keydown', onKey);
        doneBtn.focus();   // focus lands in the dialog immediately (not on the background board)

        // (Re-)mount a ladder into the footer, re-triggering its fill-from-empty reveal (pglDraw). The
        // "X to next" counts DOWN from the from-zero distance (the boundary level -- thousands, high up)
        // to the real remaining, landing as the bar finishes filling.
        function mountLadder(lad, charging) {
            var rb = root.querySelector('.ccx__rankbar');
            if (!rb || !lad) return;
            rb.innerHTML = '';
            rb.appendChild(buildLadder(lad));
            // `charging` = a rank-up's OLD rank filling to 100%: its bar shakes as it tops out (tension).
            if (charging && !isReduced) {
                var curRung = rb.querySelector('.pgl__rung.is-current');
                if (curRung) curRung.classList.add('is-charging');
            }
            var numEl = rb.querySelector('.pgl__next-n');
            var cur = lad.current || {};
            if (numEl && !isReduced && cur.next_level != null) {
                countTo(numEl, cur.next_level, cur.levels_to_next, 2000, function () { return !torn; });
            }
        }
        // Continue + the page arrows go live here -- but when a rank/division hand-off is queued we hold
        // them back (hidden + inert) until the user dismisses that screen, so they can't accidentally skip
        // past their own rank-up (the job-screen Continue tears the whole ceremony down).
        function revealControls() {
            root.classList.add('is-live');
            interactive = true; updatePager();
        }
        function finishAll() {
            if (torn) return;
            // Re-mount now so the fill-from-empty reveal plays HERE, visibly (the up-front build drew
            // invisibly, just to reserve height). Pre hand-off a rank-up shows the OLD rank filled 100%.
            var pr = payload.pursuer;
            // `charging` mounts the OLD rank filled to 100% (the finale's dismiss then swaps in the new one).
            // Reduced motion has no finale to do that swap, so it must mount the final (new) ladder directly.
            var charging = !isReduced && !!(pr && pr.rank_up && pr.ladder_pre);
            mountLadder(charging ? pr.ladder_pre : (pr && pr.ladder), charging);
            root.classList.add('is-settled');    // ladder + summary reveal (the footer fill is part of the show)
            var finalePending = !isReduced && !!(pr && (pr.rank_up || pr.div_up));
            if (!finalePending) revealControls();   // no hand-off coming -> controls now; else after it's dismissed
        }

        // The Pursuer screen: the rank HAND-OFF, played after a beat, only when the rank advanced a step.
        // No bar (the footer already filled) -- a focused "changing of the guard": the old rank lifts
        // away and the new rank rises up and lands, each in its own colour. Two intensities fall out of
        // that: a division step is a same-hue climb; a named rank-up is a full colour change + scan-beam
        // + a bigger spark burst + a heavier land.
        var sparkLayer = root.querySelector('.ccx__sparks');
        function playFinale() {
            var pr = payload.pursuer;
            if (torn || !pr || !(pr.rank_up || pr.div_up)) return Promise.resolve();
            var heavy = !!pr.rank_up;
            var finaleEl = root.querySelector('.ccx__finale');
            var fromEl = finaleEl.querySelector('.ccx__ho-from');
            var toEl = finaleEl.querySelector('.ccx__ho-to');
            finaleEl.querySelector('.ccx__finale-eyebrow').textContent = heavy ? 'Rank Up' : 'Division Up';
            fromEl.textContent = pr.from_label || '';
            toEl.textContent = pr.to_label || '';
            if (pr.from_key) fromEl.style.setProperty('--rc', 'var(--rank-' + pr.from_key + ')');
            if (pr.to_key) toEl.style.setProperty('--rc', 'var(--rank-' + pr.to_key + ')');
            // Bottom flood: old rank colour, swapping to the new one at the hand-off.
            if (pr.from_key) finaleEl.querySelector('.ccx__finale-flood--from').style.setProperty('--flood-c', 'var(--rank-' + pr.from_key + ')');
            if (pr.to_key) finaleEl.querySelector('.ccx__finale-flood--to').style.setProperty('--flood-c', 'var(--rank-' + pr.to_key + ')');
            finaleEl.classList.toggle('is-heavy', heavy);
            finaleEl.hidden = false;
            finaleEl.classList.add('is-in');       // screen + OLD rank fade in

            return wait(700)                        // let it rest a moment on the old rank
                .then(function () {
                    if (torn) return;
                    finaleEl.classList.add('is-glow');   // the old rank glows (wind-up: something's coming)
                    return wait(720);
                })
                .then(function () {
                    if (torn) return;
                    finaleEl.classList.add('is-hand');   // old lifts away, chevrons stream up, new rises in
                    return wait(heavy ? 780 : 700);      // ...until the new rank lands
                })
                .then(function () {
                    if (torn) return;
                    var r = toEl.getBoundingClientRect();
                    var color = pr.to_key ? 'var(--rank-' + pr.to_key + ')' : 'var(--pp-primary)';
                    sparkBurst(sparkLayer, r.left + r.width / 2, r.top + r.height / 2, color, heavy ? 28 : 14);
                    if (heavy) finaleEl.classList.add('is-scan');   // scan-beam sweep on a full rank-up
                    return wait(360);
                })
                .then(function () {
                    if (torn) return;
                    // HOLD here -- no auto return. Reveal Continue; the user closes it themselves (screenshot /
                    // admire), which transitions BACK to the settled job screen (not end the ceremony).
                    finaleEl.classList.add('is-held');
                    var btn = finaleEl.querySelector('.ccx__finale-done');
                    if (btn) {
                        btn.addEventListener('click', function () {
                            if (torn) return;
                            if (pr.rank_up) mountLadder(pr.ladder);   // the NEW rank's bar is waiting on return
                            revealControls();                          // NOW Continue + the arrows appear on the job screen
                            finaleEl.classList.remove('is-in', 'is-glow', 'is-hand', 'is-scan', 'is-held');
                            setTimeout(function () { finaleEl.hidden = true; }, 360);   // fade out -> the job screen
                            if (doneBtn && doneBtn.focus) doneBtn.focus();              // hand focus to the footer Continue
                        });
                        btn.focus();
                    }
                });
        }

        // ---- reduced motion: show page 1 settled, hand over the arrows, no autoplay ----
        if (isReduced) {
            root.classList.add('is-in', 'is-static');
            xpEl.textContent = Number(payload.xp).toLocaleString();
            settlePage(firstTiles, pages[0]);
            finishAll();   // builds the resting footer ladder; no takeover in reduced motion
            return dismissed;
        }

        // ---- full motion: bloom -> award -> auto-play each page of 5 -> hand over the arrows ----
        // Page 0 reuses the tiles already built for the height lock; later pages fade the old page
        // out, build the next 5, and reveal.
        function autoplay(p, prebuilt) {
            if (torn) return Promise.resolve();
            var pre = prebuilt ? Promise.resolve(prebuilt) : fadeOut().then(function () { return buildPage(jobsEl, pages[p]); });
            return pre.then(function (tiles) {
                if (torn || !tiles) return;
                jobsEl.classList.remove('show-tiers');   // this page's tiers stay hidden until its bars finish
                pageIdx = p; updatePager();
                return revealPage(tiles, pages[p]);
            }).then(function () {
                if (torn) return;
                jobsEl.classList.add('show-tiers');      // reveal THIS page's tier subtitles (staggered)
                // Hold so the tiers are actually read before the page turns; the last page just stays put.
                return (p + 1 >= pages.length) ? null : wait(1050);
            }).then(function () {
                if (torn || p + 1 >= pages.length) return;
                return autoplay(p + 1, null);
            });
        }

        var alive = function () { return !torn; };
        requestAnimationFrame(function () { requestAnimationFrame(function () { if (!torn) root.classList.add('is-in'); }); });
        wait(260)                                                 // stage bloom
            .then(function () { if (torn) return; pop(root.querySelector('.ccx__award'), 'is-hit'); return countUp(xpEl, payload.xp, 900, alive); })
            .then(function () { return autoplay(0, firstTiles); })
            .then(function () { return wait(420); })       // a beat after the jobs settle
            .then(finishAll)                               // footer bar fills to its new spot
            .then(function () {                            // then, only if the rank moved, the hand-off screen
                var pr = payload.pursuer;
                if (torn || !pr || !(pr.rank_up || pr.div_up)) return;
                // The current rung is the ladder's SLOWEST beat (it creeps + blooms last). Wait for that
                // bloom to actually finish -- so the bar is fully filled -- then rest, then the hand-off.
                var curRung = root.querySelector('.ccx__rankbar .pgl__rung.is-current');
                return new Promise(function (res) {
                    if (!curRung) { res(); return; }
                    var t = setTimeout(res, 3600);   // safety net
                    curRung.addEventListener('animationend', function h(e) {
                        if (e.animationName !== 'pglBloom') return;   // the current-rung bloom = ladder done
                        clearTimeout(t); curRung.removeEventListener('animationend', h); res();
                    });
                })
                    .then(function () { if (!torn) return wait(650); })   // let the fully-filled bar rest a beat
                    .then(function () { return playFinale(); });
            });

        return dismissed;
    }

    PP.ClaimCeremony = { play: play };
})();
