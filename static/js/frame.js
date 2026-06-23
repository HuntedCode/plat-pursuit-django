/* ============================================================
 * The Frame — PlatPursuit Badge Chrome (JS controller)
 *
 * Public API: window.PlatPursuit.Frame
 *
 *   .init(root = document)                  auto-wire every .pp-frame
 *                                            found inside `root`
 *   .flip(target)                           toggle .is-flipped on a
 *                                            frame element
 *   .triggerEarnMoment(target, opts)        play the choreographed
 *                                            earn moment (returns a
 *                                            Promise that resolves
 *                                            when the settle finishes
 *                                            — or immediately under
 *                                            prefers-reduced-motion)
 *   .cancelEarnMoment(target)               stop + reset to pre-play
 *   .triggerMaintenanceMoment(target, opts) play the choreographed
 *                                            active→maintenance
 *                                            transition (~2.55s,
 *                                            same Promise contract)
 *   .cancelMaintenanceMoment(target)        stop + reset to staged
 *                                            active appearance
 *   .refreshTitleScroll(target)             re-measure title overflow
 *                                            after a dynamic text change
 *   .destroy(target)                        tear down observers + timers
 *
 * Auto-init runs on DOMContentLoaded against the whole document.
 * Pages that inject Frames after page load (HTMX swaps, etc.)
 * must call .init(swappedRoot) explicitly.
 *
 * Reduced motion is honored: the earn moment skips choreography
 * and applies the end-state in a single cross-fade. Hover gleam
 * and title marquee are suppressed via CSS @media in frame.css.
 * ============================================================ */

(function () {
    'use strict';

    var TIER_EARN_SCALE = {
        bronze:   { duration: 0.75, sparkCount: 1 },
        silver:   { duration: 0.9,  sparkCount: 1 },
        gold:     { duration: 1.0,  sparkCount: 2 },
        platinum: { duration: 1.25, sparkCount: 3 }
    };

    // Frames we've already wired (avoids double-binding when
    // .init() is called multiple times against overlapping roots).
    var WIRED = new WeakSet();

    // Per-frame state (observers, in-flight earn timers, etc.)
    // so we can clean up in .destroy() and reset in .cancelEarnMoment().
    var FRAME_STATE = new WeakMap();

    // Cached prefers-reduced-motion query result. Refreshed on
    // change so toggling OS / browser settings mid-session works.
    var reducedMotion = false;
    if (typeof window.matchMedia === 'function') {
        var mq = window.matchMedia('(prefers-reduced-motion: reduce)');
        reducedMotion = !!mq.matches;
        if (typeof mq.addEventListener === 'function') {
            mq.addEventListener('change', function (e) { reducedMotion = !!e.matches; });
        } else if (typeof mq.addListener === 'function') {
            // Safari < 14
            mq.addListener(function (e) { reducedMotion = !!e.matches; });
        }
    }

    function getState(target) {
        var s = FRAME_STATE.get(target);
        if (!s) {
            s = {
                resizeObserver: null,
                earnTimers: [],
                earnIntervals: [],
                earnRaf: null,
                earnResolve: null,
                initialSnapshot: null,
                maintTimers: [],
                maintResolve: null,
                maintSnapshot: null,
                gleam: null
            };
            FRAME_STATE.set(target, s);
        }
        return s;
    }

    function tierOf(target) {
        if (target.classList.contains('pp-frame--bronze'))   return 'bronze';
        if (target.classList.contains('pp-frame--silver'))   return 'silver';
        if (target.classList.contains('pp-frame--platinum')) return 'platinum';
        return 'gold';
    }

    // Locate the in-progress text span in the plinth meta line
    // (e.g. "9 of 10 stages"). May return null on earned cards
    // (where the line shows "Earned ..." instead).
    function progressSpan(target) {
        return target.querySelector('.pp-frame__plinth-meta > span:first-child');
    }
    var STAGES_RE = /^(\d+)\s+of\s+(\d+)\s+stages$/;

    // Snapshot the static classes / inline styles / dynamic text
    // the card has BEFORE its first earn moment plays. cancel
    // restores from this snapshot so a Reset always returns the
    // card to its initial render-time appearance, even if the
    // animation made it through PHASE 6 (which strips
    // pp-frame--blueprint + pp-frame--unearned). Without this,
    // a cancel after PHASE 6 would leave the card in its
    // earned-looking end state with no way to replay cleanly.
    function captureInitialState(target) {
        var snapshot = {
            wasBlueprint:  target.classList.contains('pp-frame--blueprint'),
            wasUnearned:   target.classList.contains('pp-frame--unearned'),
            wasGhost:      target.classList.contains('pp-frame--ghost'),
            wasBackStaged: target.classList.contains('pp-earn-back-staged'),
            wasFlipped:    target.classList.contains('is-flipped'),
            ppBuild:       target.style.getPropertyValue('--pp-build')
        };
        var engraving = target.querySelector('.pp-frame__engraving');
        if (engraving) {
            snapshot.engravingPlaceholder = engraving.classList.contains('pp-frame__engraving--placeholder');
        }
        var meta = progressSpan(target);
        if (meta) {
            snapshot.metaText = meta.textContent;
        }
        return snapshot;
    }

    function restoreInitialState(target, snapshot) {
        if (!snapshot) return;
        target.classList.toggle('pp-frame--blueprint',  snapshot.wasBlueprint);
        target.classList.toggle('pp-frame--unearned',   snapshot.wasUnearned);
        target.classList.toggle('pp-frame--ghost',      snapshot.wasGhost);
        target.classList.toggle('pp-earn-back-staged',  snapshot.wasBackStaged);
        target.classList.toggle('is-flipped',           snapshot.wasFlipped);
        if (snapshot.ppBuild) {
            target.style.setProperty('--pp-build', snapshot.ppBuild);
        } else {
            target.style.removeProperty('--pp-build');
        }
        var engraving = target.querySelector('.pp-frame__engraving');
        if (engraving && typeof snapshot.engravingPlaceholder === 'boolean') {
            engraving.classList.toggle('pp-frame__engraving--placeholder', snapshot.engravingPlaceholder);
        }
        var meta = progressSpan(target);
        if (meta && typeof snapshot.metaText === 'string') {
            meta.textContent = snapshot.metaText;
        }
    }


    /* --------------------------------------------------------
     * Flip
     * -------------------------------------------------------- */
    function wireFlip(target) {
        if (!target.classList.contains('pp-frame--flippable')) return;

        var downX = 0;
        var downY = 0;
        var downTime = 0;
        var DRAG_THRESHOLD_PX = 8;
        var TAP_TIMEOUT_MS = 500;

        target.addEventListener('pointerdown', function (e) {
            downX = e.clientX;
            downY = e.clientY;
            downTime = Date.now();
        });
        target.addEventListener('pointerup', function (e) {
            // Suppress on interactive children — let the link /
            // button handle the event natively.
            if (e.target.closest('a, button, input, select, textarea, [role="button"]')) return;
            // Blueprint cards aren't flippable (the badge isn't earned yet).
            if (target.classList.contains('pp-frame--blueprint')) return;
            // Compact cards (the binder's flipbook spread) don't flip -- the back
            // face isn't designed for the small size. View full backs in Single mode.
            if (target.classList.contains('pp-frame--compact')) return;
            // Distinguish tap from scroll-drag.
            var dx = Math.abs(e.clientX - downX);
            var dy = Math.abs(e.clientY - downY);
            if (dx > DRAG_THRESHOLD_PX || dy > DRAG_THRESHOLD_PX) return;
            if (Date.now() - downTime > TAP_TIMEOUT_MS) return;
            target.classList.toggle('is-flipped');
        });
    }

    function flip(target) {
        if (!target || !target.classList.contains('pp-frame--flippable')) return;
        if (target.classList.contains('pp-frame--compact')) return;  // compact cards don't flip
        target.classList.toggle('is-flipped');
    }


    /* --------------------------------------------------------
     * Hover sheen loop
     *
     * The front-face gleam sweeps once on a hover, then repeats
     * every GLEAM_INTERVAL (start-to-start) for as long as the
     * card stays hovered. Driven entirely from JS so we can:
     *   - loop while hovered (CSS :hover fires only once)
     *   - reset on mouse-out (next hover sweeps from the top
     *     immediately, not from wherever the loop left off)
     *   - guarantee a sweep ALWAYS finishes: a fresh sweep only
     *     starts when none is in flight, so a quick hover/un-hover
     *     can't restart it mid-animation or stack two sweeps.
     *
     * GLEAM_DURATION must match the pp-gleam-hover-sweep keyframe
     * length in frame.css.
     * -------------------------------------------------------- */
    var GLEAM_DURATION = 900;    // one sweep (ms) — keep in sync with frame.css
    var GLEAM_INTERVAL = 5000;   // start-to-start cadence while hovered (ms)

    // States that never show a hover sheen (CSS suppresses it too).
    // NOTE: flipped is deliberately NOT gated here — the loop tracks
    // HOVER, not facing. A flip is a click with the mouse still on the
    // card, so the loop keeps running: an in-flight sweep finishes
    // (invisibly, behind the rotated-away front face) and the cadence
    // ticks on, so flipping back rejoins the loop already in progress.
    function canGleam(target) {
        if (reducedMotion) return false;
        if (target.classList.contains('pp-frame--blueprint')) return false;
        if (target.classList.contains('pp-frame--unearned')) return false;
        if (target.classList.contains('pp-frame--maintenance')) return false;
        return true;
    }

    function wireGleam(target) {
        if (!target.classList.contains('pp-hover-a')) return;

        var g = {
            hovering: false,
            sweeping: false,
            loopTimer: null,   // pending NEXT sweep (cancelled on mouse-out)
            endTimer: null,    // clears `sweeping` when the current sweep ends
            sweepOnce: null     // exposed one-off sweep (arrival flourish, etc.)
        };
        getState(target).gleam = g;

        // One guarded sweep. The finish-guard (g.sweeping) means it always runs
        // to completion and never stacks; canGleam keeps it off suppressed /
        // reduced-motion states. onEnd fires when the sweep finishes (the hover
        // loop uses it to reschedule).
        function beginSweep(onEnd) {
            if (g.sweeping || !canGleam(target)) return;
            g.sweeping = true;
            // Re-arm the keyframe from 0% (reflow between remove/add).
            target.classList.remove('pp-gleam-sweeping');
            void target.offsetWidth;
            target.classList.add('pp-gleam-sweeping');
            g.endTimer = window.setTimeout(function () {
                g.endTimer = null;
                g.sweeping = false;
                target.classList.remove('pp-gleam-sweeping');
                if (onEnd) onEnd();
            }, GLEAM_DURATION);
        }

        function startSweep() {
            if (!g.hovering) return;
            beginSweep(function () {
                // Loop only while still hovered; schedule start-to-start.
                if (g.hovering && canGleam(target)) {
                    g.loopTimer = window.setTimeout(startSweep, GLEAM_INTERVAL - GLEAM_DURATION);
                }
            });
        }

        // A single sweep with no hover dependency and no loop — for callers like
        // the badge-detail hero that want one flourish as the Frame arrives.
        g.sweepOnce = function () { beginSweep(null); };

        target.addEventListener('mouseenter', function () {
            g.hovering = true;
            startSweep();  // immediate from the top (guard no-ops if mid-sweep)
        });
        target.addEventListener('mouseleave', function () {
            g.hovering = false;
            // Cancel a pending NEXT sweep so the loop stops. An in-flight
            // sweep is left alone — it finishes cleanly and won't reschedule
            // (hovering is now false), so the next hover starts fresh.
            if (g.loopTimer) { window.clearTimeout(g.loopTimer); g.loopTimer = null; }
        });
    }

    // Fire a single arrival-flourish sweep on a wired frame. No-ops cleanly if
    // the frame isn't wired, isn't gleam-capable, or a sweep is already running.
    function triggerGleam(target) {
        if (!target) return;
        var state = FRAME_STATE.get(target);
        if (state && state.gleam && state.gleam.sweepOnce) state.gleam.sweepOnce();
    }

    function teardownGleam(target) {
        var state = FRAME_STATE.get(target);
        var g = state && state.gleam;
        if (!g) return;
        if (g.loopTimer) { window.clearTimeout(g.loopTimer); g.loopTimer = null; }
        if (g.endTimer) { window.clearTimeout(g.endTimer); g.endTimer = null; }
        g.sweeping = false;
        g.hovering = false;
        target.classList.remove('pp-gleam-sweeping');
    }


    /* --------------------------------------------------------
     * Title overflow detection + marquee
     * -------------------------------------------------------- */
    function refreshTitleScroll(target) {
        var seriesEls = target.querySelectorAll('.pp-frame__series');
        for (var i = 0; i < seriesEls.length; i++) {
            var el = seriesEls[i];
            var hasOverflow = el.scrollWidth > el.clientWidth + 1;
            el.classList.toggle('is-scrolling', hasOverflow);
        }
    }

    function wireTitleScroll(target) {
        // Measure once after mount, then re-measure on resize.
        refreshTitleScroll(target);

        if (typeof window.ResizeObserver === 'function') {
            var state = getState(target);
            var raf = null;
            state.resizeObserver = new ResizeObserver(function () {
                if (raf) return;
                raf = window.requestAnimationFrame(function () {
                    raf = null;
                    refreshTitleScroll(target);
                });
            });
            state.resizeObserver.observe(target);
        }
    }


    /* --------------------------------------------------------
     * Earn moment
     *
     * Twelve-phase choreography. The base Gold animation lives in
     * frame.css; per-tier overrides scale intensity. Durations are
     * driven from --earn-scale (set here), so JS timings and CSS
     * animations stay in lock-step.
     *
     * Phases:
     *  T=0       Build pulse 90% → 100% with weld sparks
     *  T=1800    .pp-earn-cooling          (lift + atmosphere stays)
     *  T=2900    Twin-welder border seal   (1400ms)
     *  T=4300    .pp-earn-sealed           (1400ms self-contained pulse)
     *  T=5800    .pp-earn-uncloak          (2200ms bottom→top scan)
     *  T=8200    .pp-earn-searing          (1800ms badge sears in)
     *  T=10000   strip blueprint/unearned
     *  T=10200   .pp-earn-gleaming         (700ms sheen sweep)
     *  T=11100   front engraving etch      (3000ms)
     *  T=14300   .is-flipped               (flip to back)
     *  T=15150   .pp-earn-back-scan        (2000ms back-face scan)
     *  T=18000   flip BACK to front
     *  T=18900   .pp-earn-settling         (descend + tier-tinted flourish)
     * -------------------------------------------------------- */
    function maybeTintSpark(target, spark) {
        if (Math.random() < 0.45) {
            spark.classList.add('pp-spark--tier-' + tierOf(target));
        }
    }

    function spawnBorderSpark(target, leftPct, topPct, edge) {
        var spark = document.createElement('div');
        spark.className = 'pp-spark';
        maybeTintSpark(target, spark);
        spark.style.left = leftPct + '%';
        spark.style.top = topPct + '%';
        spark.style.bottom = 'auto';
        var xMid, xEnd, yMid, yEnd;
        if (edge === 'top') {
            xMid = (Math.random() - 0.5) * 14;
            xEnd = (Math.random() - 0.5) * 36;
            yMid = -(6 + Math.random() * 8);
            yEnd = -(18 + Math.random() * 22);
        } else if (edge === 'right') {
            xMid = (5 + Math.random() * 8);
            xEnd = (18 + Math.random() * 22);
            yMid = (Math.random() - 0.5) * 14;
            yEnd = (Math.random() - 0.5) * 36;
        } else if (edge === 'bottom') {
            xMid = (Math.random() - 0.5) * 14;
            xEnd = (Math.random() - 0.5) * 36;
            yMid = (6 + Math.random() * 8);
            yEnd = (18 + Math.random() * 22);
        } else {
            // left
            xMid = -(5 + Math.random() * 8);
            xEnd = -(18 + Math.random() * 22);
            yMid = (Math.random() - 0.5) * 14;
            yEnd = (Math.random() - 0.5) * 36;
        }
        var dur = 450 + Math.random() * 300;
        spark.style.setProperty('--spark-x-mid', xMid + 'px');
        spark.style.setProperty('--spark-y-mid', yMid + 'px');
        spark.style.setProperty('--spark-x-end', xEnd + 'px');
        spark.style.setProperty('--spark-y-end', yEnd + 'px');
        spark.style.setProperty('--spark-dur', dur + 'ms');
        target.appendChild(spark);
        window.setTimeout(function () { spark.remove(); }, dur + 50);
    }

    function spawnBuildSpark(target) {
        var art = target.querySelector('.pp-frame__art');
        if (!art) return;
        var buildPct = parseFloat(getComputedStyle(target).getPropertyValue('--pp-build')) || 0;
        var spark = document.createElement('div');
        spark.className = 'pp-spark';
        maybeTintSpark(target, spark);
        spark.style.left = (15 + Math.random() * 70) + '%';
        spark.style.bottom = buildPct + '%';
        var xMid = (Math.random() - 0.5) * 18;
        var xEnd = (Math.random() - 0.5) * 90;
        var yMid = -(8 + Math.random() * 14);
        var yEnd = 22 + Math.random() * 34;
        var dur = 550 + Math.random() * 350;
        spark.style.setProperty('--spark-x-mid', xMid + 'px');
        spark.style.setProperty('--spark-y-mid', yMid + 'px');
        spark.style.setProperty('--spark-x-end', xEnd + 'px');
        spark.style.setProperty('--spark-y-end', yEnd + 'px');
        spark.style.setProperty('--spark-dur', dur + 'ms');
        art.appendChild(spark);
        window.setTimeout(function () { spark.remove(); }, dur + 50);
    }

    function cancelEarnMoment(target) {
        if (!target) return;
        var state = getState(target);
        state.earnTimers.forEach(function (id) { window.clearTimeout(id); });
        state.earnIntervals.forEach(function (id) { window.clearInterval(id); });
        state.earnTimers = [];
        state.earnIntervals = [];
        if (state.earnRaf) {
            window.cancelAnimationFrame(state.earnRaf);
            state.earnRaf = null;
        }

        // Strip earn-moment phase classes + injected spark / beam
        // / weld-head children. The static staging classes
        // (pp-frame--blueprint, pp-frame--unearned,
        // pp-earn-back-staged, is-flipped, engraving placeholder)
        // are handled below by the snapshot restore.
        target.querySelectorAll('.pp-spark, .pp-frame__etch-head, .pp-frame__border-weld-head, .pp-frame__scan-beam-h')
            .forEach(function (el) { el.remove(); });
        target.classList.remove(
            'pp-earn-cooling',
            'pp-earn-uncloak',
            'pp-earn-searing',
            'pp-earn-gleaming',
            'pp-earn-engraving',
            'pp-earn-settling',
            'pp-earn-sealed',
            'pp-earn-back-scan',
            'pp-earn-weld-top',
            'pp-earn-weld-right',
            'pp-earn-weld-bottom',
            'pp-earn-weld-left',
            'is-animating'
        );
        target.style.removeProperty('--scan-fwd');
        target.style.removeProperty('--scan-back');
        target.style.removeProperty('--art-bottom');
        target.style.removeProperty('--art-range');
        var engraving = target.querySelector('.pp-frame__engraving');
        if (engraving) {
            engraving.style.removeProperty('clip-path');
            engraving.style.removeProperty('-webkit-clip-path');
        }

        // Restore everything the partial set at render time so the
        // card is identical to its initial state after a Reset.
        // pp-anim-resetting suppresses the .pp-anim-weld --pp-build
        // transition during the snap-back so the Reset is instant —
        // without it the badge would fade down from 100% to 90% over
        // 1.5s in reverse. Remove the class after a forced reflow.
        if (state.initialSnapshot) {
            target.classList.add('pp-anim-resetting');
            restoreInitialState(target, state.initialSnapshot);
            void target.offsetWidth;
            target.classList.remove('pp-anim-resetting');
        }

        if (state.earnResolve) {
            state.earnResolve({ cancelled: true });
            state.earnResolve = null;
        }
    }

    function triggerEarnMoment(target, opts) {
        opts = opts || {};
        if (!target) return Promise.resolve({ cancelled: true });

        return new Promise(function (resolve) {
            var state = getState(target);
            // Snapshot the partial-rendered state on first play so
            // every subsequent cancel / replay restores cleanly.
            if (!state.initialSnapshot) {
                state.initialSnapshot = captureInitialState(target);
            }
            cancelEarnMoment(target);
            state.earnResolve = resolve;

            var engraving = target.querySelector('.pp-frame__engraving');

            // Reduced-motion fast path: skip choreography, apply
            // end-state classes in a single tick.
            if (reducedMotion) {
                target.classList.remove('pp-frame--blueprint', 'pp-frame--unearned', 'pp-frame--ghost');
                target.style.setProperty('--pp-build', '100%');
                if (engraving && engraving.classList.contains('pp-frame__engraving--placeholder')) {
                    engraving.classList.remove('pp-frame__engraving--placeholder');
                }
                if (typeof opts.onComplete === 'function') opts.onComplete();
                state.earnResolve = null;
                resolve({ cancelled: false, reduced: true });
                return;
            }

            // Tier-scale.
            var tier = tierOf(target);
            var scale = TIER_EARN_SCALE[tier] || TIER_EARN_SCALE.gold;
            if (typeof opts.scale === 'number' && opts.scale > 0) {
                scale = { duration: opts.scale, sparkCount: scale.sparkCount };
            }
            var dScale = scale.duration;
            var sparkCount = scale.sparkCount;
            var t = function (ms) { return Math.round(ms * dScale); };
            target.style.setProperty('--earn-scale', dScale);

            // Stage the card in pre-play state (blueprint, build at 90%). A ghost
            // (not-yet-started) card sheds its ghost treatment as it enters the weld.
            target.classList.add('pp-frame--blueprint', 'pp-frame--unearned', 'pp-earn-back-staged');
            target.classList.remove('pp-frame--ghost');
            target.style.setProperty('--pp-build', '90%');

            // Force reflow so the build transition fires from 90%, not snaps.
            void target.offsetWidth;

            var emit = function (phase) {
                if (typeof opts.onPhase === 'function') opts.onPhase(phase);
            };

            // PHASE 1: Weld pulse 90% → 100% (1800ms feel).
            state.earnRaf = window.requestAnimationFrame(function () {
                state.earnRaf = null;
                target.classList.add('is-animating');
                target.style.setProperty('--pp-build', '100%');
                // Bump the plinth progress text from "X of Y stages"
                // to "Y of Y stages" so the final-stage clear shows.
                var metaEl = progressSpan(target);
                if (metaEl) {
                    var match = metaEl.textContent.match(STAGES_RE);
                    if (match) {
                        metaEl.textContent = match[2] + ' of ' + match[2] + ' stages';
                    }
                }
                emit('build');
                for (var i = 0; i < 6; i++) {
                    state.earnTimers.push(window.setTimeout(function () { spawnBuildSpark(target); }, i * 25));
                }
                var count = 0;
                var buildTimer = window.setInterval(function () {
                    spawnBuildSpark(target);
                    count++;
                    if (count > 65) {
                        window.clearInterval(buildTimer);
                    }
                }, 28);
                state.earnIntervals.push(buildTimer);
            });

            // PHASE 2: Cooling — card lifts, banner / lock / line fade.
            state.earnTimers.push(window.setTimeout(function () {
                target.classList.add('pp-earn-cooling');
                emit('cooling');
            }, t(1800)));

            // PHASE 3: Twin-welder border seal.
            state.earnTimers.push(window.setTimeout(function () {
                target.classList.remove('is-animating');
                target.querySelectorAll('.pp-spark:not(.pp-spark--etch)').forEach(function (s) { s.remove(); });

                var dur = t(1400);
                var headR = document.createElement('span');
                headR.className = 'pp-frame__border-weld-head';
                headR.style.animation = 'pp-border-weld-trace-right ' + dur + 'ms linear forwards';
                target.appendChild(headR);
                var headL = document.createElement('span');
                headL.className = 'pp-frame__border-weld-head';
                headL.style.animation = 'pp-border-weld-trace-left ' + dur + 'ms linear forwards';
                target.appendChild(headL);
                state.earnTimers.push(window.setTimeout(function () { headR.remove(); headL.remove(); }, dur + 50));

                state.earnTimers.push(window.setTimeout(function () { target.classList.add('pp-earn-weld-top');    }, Math.round(dur * 0.33)));
                state.earnTimers.push(window.setTimeout(function () { target.classList.add('pp-earn-weld-right');  }, Math.round(dur * 0.67)));
                state.earnTimers.push(window.setTimeout(function () { target.classList.add('pp-earn-weld-left');   }, Math.round(dur * 0.67)));
                state.earnTimers.push(window.setTimeout(function () { target.classList.add('pp-earn-weld-bottom'); }, dur));

                var startTime = Date.now();
                var borderTimer = window.setInterval(function () {
                    var elapsed = Date.now() - startTime;
                    var progress = Math.min(elapsed / dur, 1);
                    if (progress >= 1) { window.clearInterval(borderTimer); return; }

                    var leftR, topR, edgeR;
                    if (progress < 0.33) {
                        leftR = 50 + (progress / 0.33) * 50;
                        topR = 0;
                        edgeR = 'top';
                    } else if (progress < 0.67) {
                        leftR = 100;
                        topR = ((progress - 0.33) / 0.34) * 100;
                        edgeR = 'right';
                    } else {
                        leftR = 100 - ((progress - 0.67) / 0.33) * 50;
                        topR = 100;
                        edgeR = 'bottom';
                    }
                    spawnBorderSpark(target, leftR, topR, edgeR);

                    var leftL, topL, edgeL;
                    if (progress < 0.33) {
                        leftL = 50 - (progress / 0.33) * 50;
                        topL = 0;
                        edgeL = 'top';
                    } else if (progress < 0.67) {
                        leftL = 0;
                        topL = ((progress - 0.33) / 0.34) * 100;
                        edgeL = 'left';
                    } else {
                        leftL = ((progress - 0.67) / 0.33) * 50;
                        topL = 100;
                        edgeL = 'bottom';
                    }
                    spawnBorderSpark(target, leftL, topL, edgeL);
                }, 36);
                state.earnIntervals.push(borderTimer);
                emit('border');
            }, t(2900)));

            // PHASE 4: Sealed glow pulse.
            state.earnTimers.push(window.setTimeout(function () {
                target.classList.add('pp-earn-sealed');
                emit('sealed');
            }, t(4300)));

            // PHASE 5: Uncloak SCAN — bottom-to-top beam over the
            // WHOLE front face (plinth → art → title).
            state.earnTimers.push(window.setTimeout(function () {
                target.classList.remove('pp-earn-sealed');
                target.querySelectorAll('.pp-spark').forEach(function (s) { s.remove(); });

                var face = target.querySelector('.pp-frame__face--front') || target;
                var artEl = target.querySelector('.pp-frame__art');
                if (face && artEl) {
                    var faceRect = face.getBoundingClientRect();
                    var artRect = artEl.getBoundingClientRect();
                    var artBottom = (faceRect.bottom - artRect.bottom) / faceRect.height;
                    var artTop = (faceRect.bottom - artRect.top) / faceRect.height;
                    target.style.setProperty('--art-bottom', artBottom);
                    target.style.setProperty('--art-range', artTop - artBottom);
                }

                if (face) {
                    var beam = document.createElement('div');
                    beam.className = 'pp-frame__scan-beam-h';
                    face.appendChild(beam);
                    state.earnTimers.push(window.setTimeout(function () { beam.remove(); }, 2300));
                }

                target.classList.add('pp-earn-uncloak');
                state.earnRaf = window.requestAnimationFrame(function () {
                    state.earnRaf = null;
                    target.style.setProperty('--scan-fwd', '100%');
                });
                emit('uncloak');

                var scanStart = Date.now();
                var scanDur = t(2200);
                var scanSparkTimer = window.setInterval(function () {
                    var elapsed = Date.now() - scanStart;
                    var progress = Math.min(elapsed / scanDur, 1);
                    if (progress >= 1) { window.clearInterval(scanSparkTimer); return; }
                    var eased = progress < 0.5
                        ? 2 * progress * progress
                        : 1 - Math.pow(-2 * progress + 2, 2) / 2;
                    var beamPct = eased * 100;
                    var faceEl = target.querySelector('.pp-frame__face--front') || target;
                    if (!faceEl) return;
                    var spark = document.createElement('div');
                    spark.className = 'pp-spark';
                    maybeTintSpark(target, spark);
                    spark.style.left = (10 + Math.random() * 80) + '%';
                    spark.style.bottom = beamPct + '%';
                    var xMid = (Math.random() - 0.5) * 14;
                    var xEnd = (Math.random() - 0.5) * 60;
                    var yMid = -(4 + Math.random() * 8);
                    var yEnd = -(10 + Math.random() * 18);
                    var sDur = 480 + Math.random() * 280;
                    spark.style.setProperty('--spark-x-mid', xMid + 'px');
                    spark.style.setProperty('--spark-y-mid', yMid + 'px');
                    spark.style.setProperty('--spark-x-end', xEnd + 'px');
                    spark.style.setProperty('--spark-y-end', yEnd + 'px');
                    spark.style.setProperty('--spark-dur', sDur + 'ms');
                    faceEl.appendChild(spark);
                    window.setTimeout(function () { spark.remove(); }, sDur + 50);
                }, 42);
                state.earnIntervals.push(scanSparkTimer);
                state.earnTimers.push(window.setTimeout(function () { window.clearInterval(scanSparkTimer); }, scanDur + 50));
            }, t(5800)));

            // PHASE 6: Badge sears onto the freshly revealed background.
            state.earnTimers.push(window.setTimeout(function () {
                target.classList.add('pp-earn-searing');
                emit('searing');

                var art = target.querySelector('.pp-frame__art');
                if (!art) return;

                var sparkDur = t(1170);
                var sparkStart = Date.now();
                var tierClass = 'pp-spark--tier-' + tierOf(target);
                var layers = art.querySelectorAll('.pp-frame__layer');
                var insertTarget = layers[1] || layers[0] || null;

                var spawnOne = function () {
                    var spark = document.createElement('div');
                    spark.className = 'pp-spark pp-spark--behind';
                    spark.classList.add(tierClass);
                    // Umbrella spread: upper semicircle only.
                    var angle = -Math.PI + Math.random() * Math.PI;
                    var distance = 90 + Math.random() * 130;
                    var peakX = Math.cos(angle) * distance;
                    var peakY = Math.sin(angle) * distance;
                    spark.style.left = '50%';
                    spark.style.top = '50%';
                    spark.style.bottom = 'auto';
                    spark.style.setProperty('--spark-x-mid', (peakX * 0.55) + 'px');
                    spark.style.setProperty('--spark-y-mid', peakY + 'px');
                    spark.style.setProperty('--spark-x-end', peakX + 'px');
                    spark.style.setProperty('--spark-y-end', (peakY * 0.7) + 'px');
                    var sDur = 600 + Math.random() * 400;
                    spark.style.setProperty('--spark-dur', sDur + 'ms');
                    if (insertTarget) art.insertBefore(spark, insertTarget);
                    else art.appendChild(spark);
                    window.setTimeout(function () { spark.remove(); }, sDur + 50);
                };

                var sparkTimer = window.setInterval(function () {
                    var elapsed = Date.now() - sparkStart;
                    if (elapsed >= sparkDur) { window.clearInterval(sparkTimer); return; }
                    for (var i = 0; i < sparkCount; i++) spawnOne();
                }, 28);
                state.earnIntervals.push(sparkTimer);
                state.earnTimers.push(window.setTimeout(function () { window.clearInterval(sparkTimer); }, sparkDur + 50));
            }, t(8200)));

            // Strip blueprint + searing at sear end.
            state.earnTimers.push(window.setTimeout(function () {
                target.classList.remove('pp-earn-searing');
                target.classList.remove('pp-frame--blueprint', 'pp-frame--unearned', 'pp-frame--ghost');
            }, t(10000)));

            // PHASE 7: Completion sheen sweep.
            state.earnTimers.push(window.setTimeout(function () {
                target.classList.add('pp-earn-gleaming');
                emit('gleaming');
            }, t(10200)));
            state.earnTimers.push(window.setTimeout(function () {
                target.classList.remove('pp-earn-gleaming');
            }, t(10900)));

            // PHASE 8: Engraving etch — 3000ms slow burn.
            state.earnTimers.push(window.setTimeout(function () {
                if (!engraving) return;
                var plinth = engraving.parentElement;
                if (!plinth) return;

                if (engraving.classList.contains('pp-frame__engraving--placeholder')) {
                    engraving.classList.remove('pp-frame__engraving--placeholder');
                }

                var plinthRect = plinth.getBoundingClientRect();
                var engRect = engraving.getBoundingClientRect();
                var spans = engraving.querySelectorAll(':scope > span');
                var textLeftPx = engRect.left;
                var textRightPx = engRect.right;
                if (spans.length >= 1) {
                    textLeftPx = spans[0].getBoundingClientRect().left;
                    textRightPx = spans[spans.length - 1].getBoundingClientRect().right;
                }

                var headStartPct = ((textLeftPx - plinthRect.left) / plinthRect.width) * 100;
                var headEndPct = ((textRightPx - plinthRect.left) / plinthRect.width) * 100;
                var headTopPct = ((engRect.top + engRect.height / 2 - plinthRect.top) / plinthRect.height) * 100;

                var engLeftPct = Math.max(0, ((textLeftPx - engRect.left) / engRect.width) * 100);
                var engRightPct = Math.min(100, ((textRightPx - engRect.left) / engRect.width) * 100);

                engraving.style.clipPath = 'inset(0 ' + (100 - engLeftPct) + '% 0 0)';
                target.classList.add('pp-earn-engraving');
                emit('engraving');

                var head = document.createElement('span');
                head.className = 'pp-frame__etch-head';
                head.style.left = headStartPct + '%';
                head.style.top = headTopPct + '%';
                head.style.opacity = '1';
                var etchTotalMs = t(3000);
                var etchFadeDelayMs = Math.round(etchTotalMs * 0.9);
                head.style.transition = 'left ' + etchTotalMs + 'ms linear, opacity 300ms ease-out ' + etchFadeDelayMs + 'ms';
                plinth.appendChild(head);
                state.earnTimers.push(window.setTimeout(function () { head.remove(); }, etchTotalMs + 200));

                window.requestAnimationFrame(function () {
                    engraving.style.clipPath = 'inset(0 ' + (100 - engRightPct) + '% 0 0)';
                    head.style.left = headEndPct + '%';
                    head.style.opacity = '0';
                });

                var etchStart = Date.now();
                var etchSparkTimer = window.setInterval(function () {
                    var elapsed = Date.now() - etchStart;
                    var progress = Math.min(elapsed / etchTotalMs, 1);
                    if (progress >= 1) { window.clearInterval(etchSparkTimer); return; }
                    var sparkLeftPct = headStartPct + progress * (headEndPct - headStartPct);
                    var spark = document.createElement('div');
                    spark.className = 'pp-spark pp-spark--etch';
                    spark.style.left = sparkLeftPct + '%';
                    spark.style.top = headTopPct + '%';
                    spark.style.bottom = 'auto';
                    var xMid = (Math.random() - 0.5) * 10;
                    var xEnd = (Math.random() - 0.5) * 32;
                    var yMid = -(2 + Math.random() * 5);
                    var yEnd = 8 + Math.random() * 14;
                    var sDur = 400 + Math.random() * 280;
                    spark.style.setProperty('--spark-x-mid', xMid + 'px');
                    spark.style.setProperty('--spark-y-mid', yMid + 'px');
                    spark.style.setProperty('--spark-x-end', xEnd + 'px');
                    spark.style.setProperty('--spark-y-end', yEnd + 'px');
                    spark.style.setProperty('--spark-dur', sDur + 'ms');
                    plinth.appendChild(spark);
                    window.setTimeout(function () { spark.remove(); }, sDur + 50);
                }, 65);
                state.earnIntervals.push(etchSparkTimer);
            }, t(11100)));

            // PHASE 9: Flip to back face.
            state.earnTimers.push(window.setTimeout(function () {
                target.classList.add('is-flipped');
                emit('flip-back');
            }, t(14300)));

            // PHASE 10: Back face SCAN.
            state.earnTimers.push(window.setTimeout(function () {
                target.classList.remove('pp-earn-back-staged');
                target.classList.add('pp-earn-back-scan');
                state.earnRaf = window.requestAnimationFrame(function () {
                    state.earnRaf = null;
                    target.style.setProperty('--scan-back', '100%');
                });
                emit('back-scan');
            }, t(15150)));

            // PHASE 11: Flip BACK to front.
            state.earnTimers.push(window.setTimeout(function () {
                target.classList.remove('is-flipped');
                emit('flip-front');
            }, t(18000)));

            // PHASE 12: Settle.
            state.earnTimers.push(window.setTimeout(function () {
                target.classList.add('pp-earn-settling');
                emit('settling');
            }, t(18900)));

            // Total runtime ~ 21s at gold; settle animation is 2000ms.
            var totalMs = t(18900) + Math.round(2000 * dScale);
            state.earnTimers.push(window.setTimeout(function () {
                if (typeof opts.onComplete === 'function') opts.onComplete();
                if (state.earnResolve === resolve) {
                    state.earnResolve = null;
                    resolve({ cancelled: false, reduced: false });
                }
            }, totalMs));
        });
    }


    /* --------------------------------------------------------
     * Maintenance moment
     *
     * The choreographed transition from active to maintenance.
     * Fired when a user is first being told their badge has
     * been put into a maintenance cycle (e.g. on notification
     * click). NOT on every render of a maintenance card.
     *
     * Phases (start: card in staged active appearance; end: card
     * settled in maintenance state). Three macro-phases:
     *
     *   PHASE 1 — Diagnostic (T=0-1350)
     *     Card visually active. Two quick cyan scan lines
     *     "examine" the card: vertical pass top→bottom, then
     *     horizontal pass left→right.
     *     T=100   Vertical scan (550ms)
     *     T=800   Horizontal scan (550ms)
     *
     *   PHASE 2 — Print pass + stripes ascent (T=1450-3250)
     *     Heavy printing-pass beam (cyan) sweeps bottom→top
     *     across the whole face over 1800ms with an ease-curve
     *     (slow start, fast middle, slow end). The badge layer
     *     cools (filter transition) during the sweep. In
     *     parallel, a dedicated face-level damage overlay (sized
     *     to the WHOLE face, sat below the title bar + plinth
     *     chrome z-index-wise) reveals, and its --maint-ascent
     *     transitions 0% → 100% over 1800ms with the SAME easing
     *     as the beam. Both move in face-relative coordinates so
     *     the stripes' top edge tracks the beam's bottom edge
     *     exactly — the chrome overlays the parts outside the
     *     art region so the user only sees stripes growing
     *     within the art, in perfect lockstep with the beam.
     *     The art-level overlay stays hidden during this window.
     *     T=1450  Staging removed, beam injected, ascent armed
     *     T=3250  Beam exits, ascent overlay at full coverage;
     *             ascending class lifts: face overlay fades out,
     *             art-level overlay fades in at --pp-build:-3%
     *             (full art coverage). Visually seamless.
     *
     *   PHASE 2c — Stripes retract (T=3550-4350)
     *     300ms hold at full coverage (the "system has examined
     *     everything" beat), then --pp-build -3% → final value
     *     (e.g. 80%) over 800ms with ease-in-out so it
     *     accelerates and decelerates naturally instead of
     *     reading as a linear slide.
     *
     *   PHASE 3 — Plinth swap (T=4950-6600)
     *     600ms breath after stripes settle, then a deliberate
     *     swap so the user can clearly see the bottom replace.
     *       T=4950  Plinth slides off the bottom (650ms)
     *       T=5600  Plinth invisible below clipwrap; meta
     *               text swaps; plinth-extras-hidden lifts
     *               (pips + REACTIVATE label fade in 300ms)
     *       T=5950  350ms empty-plinth hold ends; plinth
     *               slides back up with new content (650ms)
     *       T=6600  Plinth at rest with new content
     *
     *   PHASE 4 — Tagging (T=6950-10000)
     *     Banner falls in, MAINTENANCE band drops dramatically,
     *     impact punctuates, ambient sparks begin. Each beat
     *     deliberately weighty to drive the maintenance call home.
     *     T=6950  RESTORING banner slides down (950ms)
     *     T=8300  MAINTENANCE band drops with bounce (1300ms)
     *     T=9145  Impact shake at the band's clack moment (65%)
     *     T=10000 Settle complete; ambient sparks begin
     * -------------------------------------------------------- */
    function captureMaintInitialState(target) {
        var meta = progressSpan(target);
        return {
            wasMaintStaged:         target.classList.contains('pp-frame--maint-staged'),
            wasStripesHidden:       target.classList.contains('pp-maint-stripes-hidden'),
            wasStripesAscending:    target.classList.contains('pp-maint-stripes-ascending'),
            wasPlinthExtrasHidden:  target.classList.contains('pp-maint-plinth-extras-hidden'),
            wasBannerShown:         target.classList.contains('pp-maint-banner-shown'),
            wasBandShown:           target.classList.contains('pp-maint-band-shown'),
            ppBuild:                target.style.getPropertyValue('--pp-build') || '',
            maintAscent:            target.style.getPropertyValue('--maint-ascent') || '',
            activeMetaText:         meta ? meta.textContent : null,
            maintenanceMetaText:    meta ? meta.getAttribute('data-meta-maintenance') : null
        };
    }

    function restoreMaintInitialState(target, snapshot) {
        if (!snapshot) return;
        target.classList.toggle('pp-frame--maint-staged',        snapshot.wasMaintStaged);
        target.classList.toggle('pp-maint-stripes-hidden',       snapshot.wasStripesHidden);
        target.classList.toggle('pp-maint-stripes-ascending',    snapshot.wasStripesAscending);
        target.classList.toggle('pp-maint-plinth-extras-hidden', snapshot.wasPlinthExtrasHidden);
        target.classList.toggle('pp-maint-banner-shown',         snapshot.wasBannerShown);
        target.classList.toggle('pp-maint-band-shown',           snapshot.wasBandShown);
        target.classList.remove('pp-maint-band-drop', 'pp-maint-impact-shake');
        // Restore inline --pp-build to its render-time value so
        // the staged state's class rule (which uses !important
        // to enforce 110%) resumes control of the resolved value.
        if (snapshot.ppBuild) {
            target.style.setProperty('--pp-build', snapshot.ppBuild);
        } else {
            target.style.removeProperty('--pp-build');
        }
        if (snapshot.maintAscent) {
            target.style.setProperty('--maint-ascent', snapshot.maintAscent);
        } else {
            target.style.removeProperty('--maint-ascent');
        }
        // Clear inline plinth styles set by the swap animation.
        var plinth = target.querySelector('.pp-frame__plinth');
        if (plinth) {
            plinth.style.removeProperty('transition');
            plinth.style.removeProperty('transform');
        }
        var meta = progressSpan(target);
        if (meta && snapshot.activeMetaText !== null) {
            meta.textContent = snapshot.activeMetaText;
        }
    }

    function cancelMaintenanceMoment(target) {
        if (!target) return;
        var state = getState(target);
        state.maintTimers.forEach(function (id) { window.clearTimeout(id); });
        state.maintTimers = [];

        // Strip phase classes injected by the choreography + any
        // ephemeral elements (diagnostic scans, scan beam, sparks).
        target.classList.remove('pp-maint-band-drop', 'pp-maint-impact-shake');
        target.querySelectorAll('.pp-frame__diag-scan, .pp-frame__diag-beam').forEach(function (el) { el.remove(); });

        // Suppress CSS transitions during the snap-back so the card
        // returns to staged-active appearance instantly, without
        // running the active→maintenance fades in reverse.
        target.classList.add('pp-anim-resetting');

        if (state.maintSnapshot) {
            restoreMaintInitialState(target, state.maintSnapshot);
        }

        void target.offsetWidth;
        target.classList.remove('pp-anim-resetting');

        if (state.maintResolve) {
            state.maintResolve({ cancelled: true });
            state.maintResolve = null;
        }
    }

    function triggerMaintenanceMoment(target, opts) {
        opts = opts || {};
        if (!target) return Promise.resolve({ cancelled: true });

        return new Promise(function (resolve) {
            var state = getState(target);
            if (!state.maintSnapshot) {
                state.maintSnapshot = captureMaintInitialState(target);
            }
            cancelMaintenanceMoment(target);
            state.maintResolve = resolve;

            var emit = function (phase) {
                if (typeof opts.onPhase === 'function') opts.onPhase(phase);
            };

            // Reduced-motion fast path: drop the staging class, swap
            // the meta text, settle in one tick.
            if (reducedMotion) {
                target.classList.remove('pp-frame--maint-staged');
                var metaR = progressSpan(target);
                if (metaR && state.maintSnapshot.maintenanceMetaText) {
                    metaR.textContent = state.maintSnapshot.maintenanceMetaText;
                }
                if (typeof opts.onComplete === 'function') opts.onComplete();
                state.maintResolve = null;
                resolve({ cancelled: false, reduced: true });
                return;
            }

            var injectScan = function (modClass) {
                var art = target.querySelector('.pp-frame__art');
                if (!art) return null;
                var scan = document.createElement('div');
                scan.className = 'pp-frame__diag-scan ' + modClass;
                art.appendChild(scan);
                state.maintTimers.push(window.setTimeout(function () {
                    scan.remove();
                }, 650));
                return scan;
            };

            // PHASE 1a (T=100): vertical diagnostic scan over the
            // badge — top → bottom, 550ms.
            state.maintTimers.push(window.setTimeout(function () {
                injectScan('pp-frame__diag-scan--v');
                emit('diag-scan-v');
            }, 100));

            // PHASE 1b (T=800): horizontal diagnostic scan over
            // the badge — left → right, 550ms.
            state.maintTimers.push(window.setTimeout(function () {
                injectScan('pp-frame__diag-scan--h');
                emit('diag-scan-h');
            }, 800));

            // PHASE 2 (T=1450): Print pass + stripes ASCENT. Beam
            // sweeps bottom→top of the FACE over 1800ms. The
            // face-level ascent overlay is revealed (the
            // art-level overlay is hidden by the same class) and
            // its --maint-ascent transitions 0%→100% over the
            // same 1800ms with the same easing as the beam. Both
            // operate in face-relative coordinates, so the mask
            // boundary tracks the beam exactly; chrome (title +
            // plinth-clipwrap, z-index 5) covers the parts
            // outside the art so only the art region shows the
            // growing stripes.
            state.maintTimers.push(window.setTimeout(function () {
                target.classList.remove('pp-frame--maint-staged');

                var face = target.querySelector('.pp-frame__face--front') || target;

                target.style.setProperty('--pp-build', '-3%');
                target.style.setProperty('--maint-ascent', '0%');
                target.classList.add('pp-maint-stripes-ascending');
                void target.offsetWidth;
                target.classList.remove('pp-maint-stripes-hidden');
                target.style.setProperty('--maint-ascent', '100%');
                emit('print-pass');

                var beam = document.createElement('div');
                beam.className = 'pp-frame__diag-beam';
                face.appendChild(beam);
                state.maintTimers.push(window.setTimeout(function () {
                    beam.remove();
                }, 1900));
            }, 1450));

            // PHASE 2b (T=3250): Beam exits, stripes at full
            // coverage via the ascent formula. Swap the mask back
            // to default by removing the ascending class. The
            // default formula uses --pp-build (currently -3%),
            // which renders the SAME full-coverage state — so the
            // class swap is visually seamless. Amber repair line
            // becomes visible again (tied to --pp-build).
            state.maintTimers.push(window.setTimeout(function () {
                target.classList.remove('pp-maint-stripes-ascending');
                emit('stripes-settled');
            }, 3250));

            // PHASE 2c (T=3550): Stripes RETRACT. 300ms hold at
            // full coverage, then --pp-build animates -3% → final
            // value (e.g. 80%) over the .pp-frame--maintenance
            // --pp-build transition (800ms ease-in-out). Mask
            // settles at T=4350 with natural acceleration and
            // deceleration instead of the previous linear-feel.
            state.maintTimers.push(window.setTimeout(function () {
                var finalBuild = state.maintSnapshot.ppBuild || '80%';
                target.style.setProperty('--pp-build', finalBuild);
                emit('stripes-retract');
            }, 3550));

            // PHASE 3 (T=4950): Plinth slide-out (650ms).
            // 600ms breath after retract settles so the new line
            // gets a beat to read before the bottom drops away.
            state.maintTimers.push(window.setTimeout(function () {
                var plinth = target.querySelector('.pp-frame__plinth');
                if (plinth) plinth.style.transform = 'translateY(100%)';
                emit('plinth-slide-out');
            }, 4950));

            // PHASE 3b (T=5600): plinth fully below the clipwrap.
            // Swap meta text + lift plinth-extras-hidden so the
            // pips + REACTIVATE label are ready on the new plinth.
            state.maintTimers.push(window.setTimeout(function () {
                var meta = progressSpan(target);
                if (meta && state.maintSnapshot.maintenanceMetaText) {
                    meta.textContent = state.maintSnapshot.maintenanceMetaText;
                }
                target.classList.remove('pp-maint-plinth-extras-hidden');
                emit('meta-swap');
            }, 5600));

            // PHASE 3c (T=5950): 350ms empty-plinth hold ends;
            // plinth slides back up with new content (650ms).
            // The empty hold makes the "bottom swap" reading clear.
            state.maintTimers.push(window.setTimeout(function () {
                var plinth = target.querySelector('.pp-frame__plinth');
                if (plinth) {
                    plinth.style.transition = 'none';
                    plinth.style.transform = 'translateY(100%)';
                    void plinth.offsetWidth;
                    plinth.style.transition = '';
                    plinth.style.transform = 'translateY(0)';
                }
                emit('plinth-slide-in');
            }, 5950));

            // PHASE 4a (T=6950): RESTORING banner slides down
            // (950ms). Plinth lands at T=6600; 350ms breath.
            state.maintTimers.push(window.setTimeout(function () {
                target.classList.add('pp-maint-banner-shown');
                emit('banner-show');
            }, 6950));

            // PHASE 4b (T=8300): MAINTENANCE band drop keyframe
            // (1300ms). 400ms breath after the banner settles.
            state.maintTimers.push(window.setTimeout(function () {
                target.classList.add('pp-maint-band-drop');
                emit('band-drop');
            }, 8300));

            // PHASE 4c (T=9145): impact shake at the band's clack.
            // 845ms = 65% of the 1300ms band drop animation, the
            // overshoot-and-snap-back beat in the keyframe.
            state.maintTimers.push(window.setTimeout(function () {
                target.classList.add('pp-maint-impact-shake');
                window.setTimeout(function () {
                    target.classList.remove('pp-maint-impact-shake');
                }, 240);
                emit('impact');
            }, 9145));

            // PHASE 4d (T=10000): settle complete. The maintenance
            // band carries its own ambient sheen sweep via CSS, so
            // there's no JS-driven ambient effect to start here.
            state.maintTimers.push(window.setTimeout(function () {
                emit('complete');
                if (typeof opts.onComplete === 'function') opts.onComplete();
                if (state.maintResolve === resolve) {
                    state.maintResolve = null;
                    resolve({ cancelled: false, reduced: false });
                }
            }, 10000));
        });
    }


    /* --------------------------------------------------------
     * Lifecycle
     * -------------------------------------------------------- */
    function destroy(target) {
        if (!target) return;
        cancelEarnMoment(target);
        cancelMaintenanceMoment(target);
        teardownGleam(target);
        var state = FRAME_STATE.get(target);
        if (state) {
            if (state.resizeObserver) {
                state.resizeObserver.disconnect();
                state.resizeObserver = null;
            }
            FRAME_STATE.delete(target);
        }
        WIRED.delete(target);
    }

    function wireOne(target) {
        if (WIRED.has(target)) return;
        WIRED.add(target);
        wireFlip(target);
        wireGleam(target);
        wireTitleScroll(target);
        // No per-card JS init for the maintenance state -- the ambient
        // sheen sweep on the maintenance band is pure CSS, scoped to
        // .pp-frame--maintenance .pp-frame__maint-stamp.
    }

    function init(root) {
        root = root || document;
        var frames = root.querySelectorAll('.pp-frame');
        frames.forEach(wireOne);
    }


    /* --------------------------------------------------------
     * Public API
     * -------------------------------------------------------- */
    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.Frame = {
        init: init,
        flip: flip,
        triggerEarnMoment: triggerEarnMoment,
        cancelEarnMoment: cancelEarnMoment,
        triggerMaintenanceMoment: triggerMaintenanceMoment,
        cancelMaintenanceMoment: cancelMaintenanceMoment,
        triggerGleam: triggerGleam,
        refreshTitleScroll: refreshTitleScroll,
        destroy: destroy
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { init(document); });
    } else {
        init(document);
    }
})();
