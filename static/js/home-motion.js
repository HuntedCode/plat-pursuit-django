/* Home (the lobby) premium motion.
 *
 * The entrance stagger itself is pure CSS (.home-reveal / .pp-head-cascade in home.css + motion.css);
 * this file handles the beats that need JS:
 *
 *   1. Count-ups that LAND. The figures roll up in a left-to-right cascade rather than all at once, and
 *      each one gets a small scale spring as it settles. The VALUE easing stays monotonic on purpose --
 *      an overshoot on a number means rendering a figure that is briefly wrong, which reads as a glitch
 *      rather than as physics. The overshoot belongs on the transform, not on the truth.
 *   2. The Horizon bar fills from 0 instead of rendering pre-filled, so the meter is something that
 *      happened rather than something that was already there.
 *   3. "Sync now" in the header delegates to the avatar panel's real control (navsync.js owns that state
 *      machine -- disabled while syncing, progress, queue position -- and a second copy would drift).
 *   4. The Career ring picks up pace while the CTA is hovered, via playback RATE rather than duration --
 *      the only way to speed a running animation without it jumping to a different frame.
 *
 * Reduced motion: 1 and 2 are skipped entirely (server-rendered values stand); 3 still works, since it is
 * an affordance rather than a flourish.
 */
(function () {
    'use strict';

    var STILL = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // --- 3. Sync now -> the navbar's real control -------------------------------------------------
    function wireSyncNow() {
        document.addEventListener('click', function (e) {
            if (!e.target.closest('[data-home-syncnow]')) { return; }
            e.preventDefault();
            // Open the avatar panel, then press ITS button -- so the disabled/progress states that panel
            // manages stay the single source of truth.
            //
            // FOCUS, not click. The panel is a DaisyUI dropdown and its open rule is
            // `.dropdown:focus-within .dropdown-content`; `element.click()` dispatches a click event but
            // does NOT move focus, so the panel never opened. The sync itself still fired, which is why
            // this read as "the button does nothing": the request went out and the only visible feedback
            // was the avatar ring changing colour in the far corner of the screen.
            var avatar = document.querySelector('[data-nav-avatar]');
            var btn = document.querySelector('[data-nav-syncnow]');
            if (avatar && avatar.focus) { avatar.focus(); }
            if (btn) { btn.click(); }
        });
    }

    // --- 4. The freshness line reflects the sync it just started ----------------------------------
    // The panel is the authoritative progress surface, but it is in the corner and the user is looking
    // at the line they just clicked. This mirrors the state there and nowhere else -- navsync.js stays
    // the only thing that talks to the server or owns the button's disabled state.
    function wireFreshnessLine() {
        var label = document.querySelector('[data-home-sync-label]');
        var dot = document.querySelector('.home-hi__dot');
        if (!label) { return; }
        var original = label.textContent;

        // Field names and the finalize phrasing are taken from navsync.js's own `update()`, so the line
        // and the panel cannot describe the same moment differently.
        var PHASE = { health_check: 'Verifying', stats_badges: 'Badges', finishing: 'Wrapping up' };

        document.addEventListener('platpursuit:sync-progress', function (e) {
            var d = (e && e.detail) || {};
            if (d.sync_status !== 'syncing') { return; }
            if (d.is_finalizing) {
                label.textContent = (PHASE[d.finalize_phase] || 'Finalizing') + ' your sync';
            } else {
                var pct = parseInt(d.sync_percentage, 10);
                label.textContent = isNaN(pct) ? 'Syncing your trophies'
                                               : 'Syncing your trophies, ' + pct + '%';
            }
            if (dot) { dot.classList.remove('is-ready'); }
        });

        document.addEventListener('platpursuit:sync-status-changed', function (e) {
            var status = e && e.detail && e.detail.status;
            if (status === 'synced') {
                // Reload rather than patch the numbers in place: a finished sync changes the trophy
                // card, the glances and the activity list, and this page has no machinery to refresh
                // them piecemeal. The user asked for fresh data by pressing the button.
                label.textContent = 'Sync complete, refreshing';
                window.setTimeout(function () { window.location.reload(); }, 600);
                return;
            }
            if (status === 'error') {
                label.textContent = original;
                if (dot) { dot.classList.remove('is-ready'); }
            }
        });
    }

    // --- 1. Count-ups -----------------------------------------------------------------------------
    function tickUp(el, dur, delay) {
        var target = parseInt((el.textContent || '').replace(/[^\d]/g, ''), 10);
        if (!(target > 1)) { return; }
        el.textContent = '0';
        var t0 = null;
        function step(ts) {
            if (t0 === null) { t0 = ts; }
            var p = Math.min(1, (ts - t0) / dur), e = 1 - Math.pow(1 - p, 3);
            el.textContent = Math.round(target * e).toLocaleString();
            if (p < 1) { requestAnimationFrame(step); return; }
            el.textContent = target.toLocaleString();
            // The landing: a small damped pop once the value is final. Transform only, so it composites
            // on the GPU and cannot reflow the stat grid it sits in.
            if (el.animate) {
                el.animate(
                    [{ transform: 'scale(1)' }, { transform: 'scale(1.08)' }, { transform: 'scale(1)' }],
                    { duration: 340, easing: 'cubic-bezier(0.2, 0.9, 0.3, 1.4)' }
                );
            }
        }
        setTimeout(function () { requestAnimationFrame(step); }, delay);
    }

    // --- 2. Horizon fill --------------------------------------------------------------------------
    // Two halves, and the split matters. The RESET runs immediately; only the RESTORE waits for the
    // count-ups. Doing both late meant the bar sat at its full server-rendered width through the whole
    // cascade and then snapped to zero before filling -- a visible glitch, and worse than not animating.
    //
    // `--horizon-progress` lives on the .pp-horizon ROOT (that is where components/horizon.html sets it
    // and what Horizon.update() writes); the fill reads it through the cascade. Reading it off the FILL
    // returns '', which is how the first cut of this did nothing at all while the bar still ended up
    // correct -- the server value was simply never disturbed.
    function primeHorizons() {
        var primed = [];
        document.querySelectorAll('main .pp-horizon').forEach(function (root) {
            var target = root.style.getPropertyValue('--horizon-progress');
            if (!target || target.trim() === '0%') { return; }
            // Kill the transition for the reset, or the bar SLIDES BACKWARDS from its server-rendered
            // width to zero before filling -- which looks like a bug, not a beat. Reset, force a reflow
            // to flush it, then hand the transition back so only the restore animates.
            var fill = root.querySelector('.pp-horizon__fill');
            if (fill) { fill.style.transition = 'none'; }
            root.style.setProperty('--horizon-progress', '0%');
            if (fill) { void fill.offsetWidth; fill.style.transition = ''; }
            primed.push([root, target]);
        });
        return primed;
    }

    function releaseHorizons(primed, delay) {
        setTimeout(function () {
            primed.forEach(function (pair) {
                // The primitive already transitions the fill's width, so restoring the value plays it.
                pair[0].style.setProperty('--horizon-progress', pair[1]);
            });
        }, delay);
    }

    // --- 4. The Career ring picks up pace on card hover -------------------------------------------
    // CSS cannot do this without a jump: changing `animation-duration` re-maps elapsed time onto the new
    // duration, so labDnaSpin (0 -> 360 -> 720deg) snaps to a different angle the moment you hover.
    // updatePlaybackRate() is the tool built for it -- it changes SPEED while preserving the current
    // time, so the ring accelerates from wherever it happens to be.
    //
    // The primitive's own `.lab-dna:hover -> animation-play-state: paused` is left alone and still wins
    // when you point at the ring itself: play state and playback rate are independent, so "faster over
    // the card, stopped over the ring" composes rather than conflicts.
    var RING_HOVER_RATE = 2.4;

    function wireRingPace() {
        var card = document.querySelector('.home-moat--career');
        var arcs = card && card.querySelector('.lab-dna__arcs');
        if (!card || !arcs || !arcs.getAnimations) { return; }

        function setRate(rate) {
            arcs.getAnimations().forEach(function (a) {
                // updatePlaybackRate is the seamless form; playbackRate is the instant fallback.
                if (a.updatePlaybackRate) { a.updatePlaybackRate(rate); } else { a.playbackRate = rate; }
            });
        }
        card.addEventListener('mouseenter', function () { setRate(RING_HOVER_RATE); });
        card.addEventListener('mouseleave', function () { setRate(1); });
    }

    function run() {
        wireSyncNow();        // an affordance, never gated
        wireFreshnessLine();  // mirrors navsync's state onto the line the user clicked
        if (STILL) { return; }

        // Gated on the 1.0 launch greeting: on the one visit that modal auto-opens, the whole
        // motion pass would play out behind its scrim. Behind the gate the server-rendered
        // values stand (correct for no-JS and reduced motion too); on "Look around" the pass
        // plays as the payoff. Fail-open: no gate published = run immediately.
        (window.ppAfterLaunchWelcome || function (f) { f(); })(function () {
            // Left-to-right, top-to-bottom: DOM order IS reading order here, so a flat per-index step reads
            // as a cascade without measuring anything. Capped so a long stat row never trickles.
            wireRingPace();
            var primed = primeHorizons();          // zero them NOW, at settle, before the pass paints
            var nums = document.querySelectorAll('[data-countup]');
            for (var i = 0; i < nums.length; i++) { tickUp(nums[i], 900, Math.min(i * 55, 500)); }
            releaseHorizons(primed, Math.min(nums.length * 55, 500) + 120);
        });
    }

    if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', run); }
    else { run(); }
})();
