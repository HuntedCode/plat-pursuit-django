/* Badge Medallion physicality -- the shared interactive behaviour for a .pp-med rendered big (in a modal or
 * hero): click/tap FLIPS it to its engraved base; on a fine pointer you TILT the shown face toward the
 * cursor (a phone drives it from the gyroscope), with a tracking glare + foil sweep. Extracted from the
 * collection page so the Series/Gallery quick-peek -- and, later, badge detail -- share ONE implementation.
 * The CSS it drives (perspective, is-tilting/is-flipping transitions, .pp-med__glare, --tx/--ty vars) is
 * global, so this works anywhere a .pp-bdetail__stage medallion renders.
 *
 *   PlatPursuit.Medallion.wire(scope)   Wire the .pp-bdetail__stage medallion inside `scope` (a freshly
 *                                       injected modal body / hero container). No-op under reduced motion.
 *   PlatPursuit.Medallion.prefersReducedMotion() / .canTilt()   The shared motion gates, so a host page's
 *                                       own grow/wiggle checks can't drift out of sync with the tilt's.
 *
 * TEARDOWN: the gyro driver adds a WINDOW deviceorientation listener that outlives a wiped modal body, so it
 * stashes card._gyroCleanup -- a host closing the modal MUST call it before discarding the node. The flip/
 * tilt listeners live on the card/scene and die with the node. Affordance-hint timers (_hintTimer/_hintAnim)
 * are the host's to schedule; wire() only cancels them on interaction (generic property names).
 */
(function () {
    'use strict';
    window.PlatPursuit = window.PlatPursuit || {};

    var gyroGranted = false;   // iOS DeviceOrientation permission, once granted, persists for the session

    // Shared motion gates (kept in one place so the tilt/grow/wiggle checks can't drift out of sync).
    function prefersReducedMotion() {
        return !!window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }
    // The 3D tilt + its affordance wiggle are a fine-pointer hover affordance. any-hover/any-pointer (not
    // the plain forms): the plain ones check the PRIMARY device, `coarse` on touchscreen laptops with a mouse.
    function canTilt() {
        return !!window.matchMedia && !prefersReducedMotion()
            && window.matchMedia('(any-hover: hover) and (any-pointer: fine)').matches;
    }

    // A handled object: click/tap FLIPS it to its engraved base (all pointers, motion-OK), and on a fine
    // pointer you can TILT whichever face is up toward the cursor. Both drive ONE transform on the card via
    // render() -- flip = a 180deg base, tilt adds rotateX/Y on top -- so they compose instead of fighting.
    function wire(scope) {
        if (!scope || prefersReducedMotion()) return;
        var scene = scope.querySelector('.pp-bdetail__stage .pp-med__stage');
        var card = scene && scene.querySelector('.pp-med__art');
        if (!card) return;
        var back = card.querySelector('.pp-med__back');   // present only with_back (modal)
        var flipped = false, tiltX = 0, tiltY = 0, flipping = false;

        function render() {
            // At true rest hand the card back to CSS ('') rather than pinning an inline identity transform.
            if (!flipped && !tiltX && !tiltY) { card.style.transform = ''; return; }
            var ry = (flipped ? 180 : 0) + tiltY;
            card.style.transform = 'rotateX(' + tiltX.toFixed(2) + 'deg) rotateY(' + ry.toFixed(2) + 'deg)'
                + (tiltX || tiltY ? ' scale(1.05)' : '');
        }
        function killHint() {
            if (card._hintTimer) { clearTimeout(card._hintTimer); card._hintTimer = null; }
            if (card._hintAnim) { card._hintAnim.cancel(); card._hintAnim = null; }
        }

        // FLIP (all pointers): click/tap or Enter/Space turns the object over to its base.
        if (back) {
            scene.setAttribute('role', 'button');
            scene.setAttribute('tabindex', '0');
            scene.setAttribute('aria-pressed', 'false');
            scene.setAttribute('aria-label', 'Flip the badge to see its engraved base');
            var flip = function () {
                if (flipping) return;
                killHint();
                if (navigator.vibrate) { try { navigator.vibrate(8); } catch (e) { /* unsupported */ } }   // a tactile tick as it turns over
                flipping = true;
                flipped = !flipped;
                tiltX = tiltY = 0;   // flip cleanly (no residual tilt); the tilt resumes after
                card.style.setProperty('--tx', '0'); card.style.setProperty('--ty', '0');   // recenter the metal reflection
                card.classList.remove('is-tilting');
                card.classList.add('is-flipping');
                render();
                scene.setAttribute('aria-pressed', flipped ? 'true' : 'false');
                // Tracked (like the hint timer) so an interrupting close can tear it down.
                card._flipTimer = setTimeout(function () {
                    flipping = false; card.classList.remove('is-flipping'); card._flipTimer = null;
                }, 660);
            };
            scene.addEventListener('click', flip);
            scene.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); flip(); }
            });
        }

        // TILT: turn the shown face toward the light, with a tracking glare. A FINE pointer drives it from
        // the cursor; a COARSE one (a phone) drives it from the gyroscope -- both feed applyTilt(px, py).
        if (!canTilt() && !window.DeviceOrientationEvent) return;   // no tilt input available
        var glare = document.createElement('span');
        glare.className = 'pp-med__glare';
        glare.setAttribute('aria-hidden', 'true');
        card.appendChild(glare);   // the glare rides the rotating card
        var foil = card.querySelector('.pp-med__foil');   // present only on holographic (rare/top-tier) badges
        var MAX = 15;   // degrees of tilt at the edges

        // px,py are 0..1 across the object (0.5,0.5 = centred/flat). Shared by the pointer + gyro drivers.
        function applyTilt(px, py) {
            if (flipping) return;   // the flip owns the transform while it plays
            killHint();
            tiltX = -(py - 0.5) * 2 * MAX;
            tiltY = (px - 0.5) * 2 * MAX;
            card.classList.add('is-tilting');
            render();
            glare.style.setProperty('--gx', (px * 100).toFixed(1) + '%');
            glare.style.setProperty('--gy', (py * 100).toFixed(1) + '%');
            // Normalised tilt (-1..1) for the plate's environment reflection + rim (transform-driven overlays).
            card.style.setProperty('--tx', ((px - 0.5) * 2).toFixed(3));
            card.style.setProperty('--ty', ((py - 0.5) * 2).toFixed(3));
            if (foil) foil.style.backgroundPosition = (px * 100).toFixed(1) + '% ' + (py * 100).toFixed(1) + '%';
        }
        function releaseTilt() {
            if (flipping) return;
            tiltX = tiltY = 0;
            card.classList.remove('is-tilting');
            render();   // spring back to the resting face (front or flipped) via the CSS transition
            card.style.setProperty('--tx', '0'); card.style.setProperty('--ty', '0');
            if (foil) foil.style.backgroundPosition = '';
        }

        if (canTilt()) {
            // FINE pointer: track the cursor. Rect read off the (untransformed) scene so tilt doesn't feed back.
            scene.addEventListener('pointermove', function (e) {
                var r = scene.getBoundingClientRect();
                applyTilt((e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
            });
            scene.addEventListener('pointerleave', releaseTilt);
        } else {
            // COARSE pointer (phone): tilt the object by tilting the DEVICE. Calibrate to the orientation when
            // it starts, map +-GYRO_RANGE degrees of device tilt onto the full swing, rAF-coalesced to 1/frame.
            var GYRO_RANGE = 26, gbase = null, pend = null, raf = 0;
            var clampG = function (v) { return v < -GYRO_RANGE ? -GYRO_RANGE : v > GYRO_RANGE ? GYRO_RANGE : v; };
            var flushGyro = function () { raf = 0; if (pend) { applyTilt(pend[0], pend[1]); pend = null; } };
            var onOrient = function (e) {
                if (e.beta == null && e.gamma == null) return;
                if (!gbase) gbase = [e.beta || 0, e.gamma || 0];   // "rest" = however the phone is held on start
                var db = clampG((e.beta || 0) - gbase[0]);    // front-back -> up/down
                var dg = clampG((e.gamma || 0) - gbase[1]);   // left-right
                pend = [0.5 + dg / (2 * GYRO_RANGE), 0.5 + db / (2 * GYRO_RANGE)];
                if (!raf) raf = requestAnimationFrame(flushGyro);
            };
            var torn = false;
            var startGyro = function () { if (torn) return; gbase = null; window.addEventListener('deviceorientation', onOrient); };
            // The window listener outlives the wiped modal body, so expose a teardown for the host to call.
            // `torn` guards the async iOS grant: if the modal closed before requestPermission() resolves, the
            // later startGyro() must NOT attach a listener to a dead card.
            card._gyroCleanup = function () {
                torn = true;
                window.removeEventListener('deviceorientation', onOrient);
                if (raf) { cancelAnimationFrame(raf); raf = 0; }
            };
            if (typeof DeviceOrientationEvent.requestPermission === 'function' && !gyroGranted) {
                // iOS 13+ needs a user gesture -- the first tap on the medallion grants it, then gyro starts.
                scene.addEventListener('click', function () {
                    DeviceOrientationEvent.requestPermission().then(function (s) {
                        if (s === 'granted') { gyroGranted = true; startGyro(); }
                    }).catch(function () { /* denied */ });
                }, { once: true });
            } else {
                startGyro();   // Android / already-granted iOS: no gesture needed
            }
        }
    }

    window.PlatPursuit.Medallion = {
        wire: wire,
        prefersReducedMotion: prefersReducedMotion,
        canTilt: canTilt,
    };
})();
