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

    // Detail modal ("pick it up" / "put it down"): grow the modal's medallion disc from a tapped source
    // medallion into the modal on open, shrink it back into the grid on close. The chrome
    // (.pp-detail-modal + is-growing/is-opening/is-closing) and body structure (.pp-bdetail__stage
    // .pp-med__stage) are shared, so this one controller drives the Collection modal AND the badge
    // quick-peek identically. The host wires its own triggers and calls open(url, sourceEl); the close
    // affordances (buttons, Esc, focus trap) are wired here.
    //   config.modal     the .pp-detail-modal element (required)
    //   config.bodySel   body container selector (default '[data-detail-body]')
    //   config.dialogSel dialog selector (default '.pp-detail-modal__dialog')
    //   config.closeSel  close-trigger selector (default '[data-detail-close]')
    function detailModal(config) {
        config = config || {};
        var modal = config.modal;
        var noop = { open: function () {}, close: function () {} };
        if (!modal) return noop;
        var body = modal.querySelector(config.bodySel || '[data-detail-body]');
        var dialog = modal.querySelector(config.dialogSel || '.pp-detail-modal__dialog');
        if (!body) return noop;
        var lastFocus = null, busy = false, growingSrc = null, closing = false;
        // The active grow/shrink "settle" handler + its safety timer, tracked so an interrupting close can
        // tear them down (else a close mid-open leaves grow's `done` armed to fire over the shrink).
        var settleEl = null, settleFn = null, settleTimer = null;
        // The modal disc's stable REST rect (centered, full size), captured in growInto BEFORE any transform;
        // shrinkOut reuses it so a close mid-grow computes the put-down from the true rest box.
        var discRestRect = null;
        function clearSettle() {
            if (settleEl && settleFn) settleEl.removeEventListener('transitionend', settleFn);
            if (settleTimer) clearTimeout(settleTimer);
            settleEl = settleFn = settleTimer = null;
        }
        function cancelHint() {
            var c = body.querySelector('.pp-bdetail__stage .pp-med__art');
            if (!c) return;
            if (c._gyroCleanup) { c._gyroCleanup(); c._gyroCleanup = null; }
            if (c._flipTimer) { clearTimeout(c._flipTimer); c._flipTimer = null; }
            if (c._hintTimer) { clearTimeout(c._hintTimer); c._hintTimer = null; }
            if (c._hintAnim) {
                c._hintAnim.cancel(); c._hintAnim = null;
                c.style.transition = 'none'; c.style.transform = 'none';
            }
        }
        // If the tapped disc sits partly behind the sticky chrome (top stack via --sticky-top, or the
        // bottom .mobile-tabbar), nudge it fully into the clear BEFORE the pick-up. Instant scroll -> the
        // rect is re-measured by open() right after. No-op under reduced motion / nothing obscured.
        function revealFromChrome(sourceEl) {
            if (prefersReducedMotion() || !sourceEl) return;
            var disc = sourceEl.querySelector('.pp-med__stage');
            if (!disc) return;
            var r = disc.getBoundingClientRect();
            var vh = window.innerHeight, margin = 16;
            var top = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--sticky-top')) || 0;
            var bottom = 0;
            var tabbar = document.querySelector('.mobile-tabbar');
            if (tabbar) { var tr = tabbar.getBoundingClientRect(); if (tr.height > 0 && tr.top < vh) bottom = vh - tr.top; }
            var safeTop = top + margin, safeBottom = vh - bottom - margin;
            var dy = 0;
            if (r.top < safeTop) dy = r.top - safeTop;
            else if (r.bottom > safeBottom) dy = r.bottom - safeBottom;
            if (dy) window.scrollBy(0, dy);
        }
        function open(url, sourceEl) {
            if (busy || !modal.hidden) return;   // don't open over an already-open (or closing) modal
            busy = true;
            lastFocus = document.activeElement;   // capture the trigger before async work moves focus
            revealFromChrome(sourceEl);           // free the badge from behind sticky chrome before the pick-up
            // Measure the tapped medallion's ART (carries the hover lift/press; falls back to the disc) so the
            // grow launches from where it VISUALLY is. Match disc-to-disc; skip under reduced motion.
            var srcMed = sourceEl && sourceEl.querySelector('.pp-med');
            var srcDisc = srcMed && srcMed.querySelector('.pp-med__stage');
            var srcArt = srcMed && srcMed.querySelector('.pp-med__art');
            var canGrow = srcDisc && !prefersReducedMotion();
            var srcRect = canGrow ? (srcArt || srcDisc).getBoundingClientRect() : null;
            fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.text() : null; })
                .then(function (html) {
                    busy = false;
                    if (html == null) return;
                    body.innerHTML = html;
                    wire(body);   // shared flip/tilt/gyro physicality
                    var grow = canGrow && srcRect.width;
                    if (grow) modal.classList.add('is-growing');   // fade (not spring) from frame 1
                    modal.hidden = false;
                    document.body.style.overflow = 'hidden';
                    if (grow) growInto(srcMed, srcRect);
                    else if (dialog) dialog.focus();
                })
                .catch(function () { busy = false; });
        }
        // Grow ONLY the disc from the tapped spot to its final spot -- same element the whole way (no clone);
        // the meter/label stay put on both grid and hero, so the progress bar never travels.
        function growInto(srcMed, sr) {
            var modalDisc = body.querySelector('.pp-bdetail__stage .pp-med__stage');
            if (!modalDisc) { if (dialog) dialog.focus(); return; }
            var mr = modalDisc.getBoundingClientRect();
            discRestRect = mr;
            var scale = sr.width / mr.width;
            var dx = (sr.left + sr.width / 2) - (mr.left + mr.width / 2);
            var dy = (sr.top + sr.height / 2) - (mr.top + mr.height / 2);
            growingSrc = srcMed || null;
            if (growingSrc) { var gs = growingSrc.querySelector('.pp-med__stage'); if (gs) gs.style.visibility = 'hidden'; }
            modal.classList.add('is-opening');
            modalDisc.style.transformOrigin = 'center center';
            modalDisc.style.transition = 'none';
            modalDisc.style.transform = 'translate(' + dx + 'px, ' + dy + 'px) scale(' + scale + ')';
            void modalDisc.offsetWidth;   // flush the start state (shelf look, at the tapped spot)
            modal.classList.remove('is-opening');
            var settled = false;
            function done(e) {
                if (settled || (e && e.propertyName !== 'transform')) return;
                settled = true;
                clearSettle();
                modalDisc.style.transition = ''; modalDisc.style.transform = ''; modalDisc.style.transformOrigin = '';
                // Leave the tapped grid badge HIDDEN while open -- "picked up". close() respawns it.
                if (dialog) dialog.focus();
                var hintCard = body.querySelector('.pp-bdetail__stage .pp-med__art');
                if (hintCard) hintCard._hintTimer = setTimeout(hintWiggle, 500);
            }
            settleEl = modalDisc; settleFn = done;
            modalDisc.addEventListener('transitionend', done);
            modalDisc.style.transition = 'transform 0.46s cubic-bezier(0.2, 0.82, 0.25, 1)';
            modalDisc.style.transform = 'none';   // grow, fully opaque (mirror of the put-down)
            settleTimer = setTimeout(done, 640);   // safety: never leave the disc mid-transform
        }
        // Affordance hint: a beat after pick-up, tilt toward a random rim point and back, decaying, to
        // signal it can be turned. Fine-pointer + motion-OK only; a real pointermove cancels it.
        function hintWiggle() {
            if (closing || modal.hidden || !canTilt()) return;
            var card = body.querySelector('.pp-bdetail__stage .pp-med__art');
            if (!card || !card.animate) return;
            card._hintTimer = null;
            if (card.matches && card.matches(':hover')) return;
            var A = 11, theta = Math.random() * Math.PI * 2;
            var rx = -Math.sin(theta) * A, ry = Math.cos(theta) * A;
            function f(m, s) {
                return 'rotateX(' + (rx * m).toFixed(2) + 'deg) rotateY(' + (ry * m).toFixed(2) + 'deg) scale(' + s + ')';
            }
            var anim = card.animate(
                [
                    { transform: f(0, 1) },
                    { transform: f(1, 1.03), offset: 0.18 },
                    { transform: f(-0.66, 1.024), offset: 0.42 },
                    { transform: f(0.36, 1.014), offset: 0.64 },
                    { transform: f(-0.15, 1.005), offset: 0.84 },
                    { transform: f(0, 1) }
                ],
                { duration: 1150, easing: 'cubic-bezier(0.4, 0, 0.3, 1)' }
            );
            card._hintAnim = anim;
            anim.onfinish = anim.oncancel = function () { if (card._hintAnim === anim) card._hintAnim = null; };
        }
        function close() {
            if (closing) return;
            clearSettle();   // cancel any in-flight grow settle before it can fire over the close
            cancelHint();
            if (growingSrc && !prefersReducedMotion()) { closing = true; shrinkOut(); }
            else finishClose();
        }
        function finishClose() {
            closing = false;
            discRestRect = null;
            clearSettle();
            cancelHint();   // must run BEFORE body is wiped (it looks the card up in the DOM)
            modal.hidden = true;
            modal.classList.remove('is-growing', 'is-closing');
            body.innerHTML = '';
            document.body.style.overflow = '';
            if (growingSrc) {
                var gs = growingSrc.querySelector('.pp-med__stage');
                if (gs) gs.style.visibility = '';   // the disc reappears in the grid
                growingSrc = null;
            }
            if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) { /* gone */ } }
        }
        // Reverse of growInto: shrink the medallion back into the grid spot while the chrome fades -- "put
        // it back down". The disc stays opaque the whole way; the grid badge swaps in the instant it lands.
        function shrinkOut() {
            var modalDisc = body.querySelector('.pp-bdetail__stage .pp-med__stage');
            var srcDisc = growingSrc && growingSrc.querySelector('.pp-med__stage');
            if (!modalDisc || !srcDisc) { finishClose(); return; }
            var mr = discRestRect || modalDisc.getBoundingClientRect();   // stable rest box (not a mid-grow measure)
            var sr = srcDisc.getBoundingClientRect();
            var scale = sr.width / mr.width;
            var dx = (sr.left + sr.width / 2) - (mr.left + mr.width / 2);
            var dy = (sr.top + sr.height / 2) - (mr.top + mr.height / 2);
            modal.classList.remove('is-growing');
            modal.classList.add('is-closing');   // fades scrim + dialog chrome (NOT the disc)
            modalDisc.style.transformOrigin = 'center center';
            modalDisc.style.transition = 'transform 0.4s cubic-bezier(0.4, 0.05, 0.55, 0.95)';
            modalDisc.style.transform = 'translate(' + dx + 'px, ' + dy + 'px) scale(' + scale + ')';
            var settled = false;
            function fin(e) {
                if (settled || (e && e.propertyName !== 'transform')) return;
                settled = true;
                clearSettle();
                finishClose();
            }
            settleEl = modalDisc; settleFn = fin;
            modalDisc.addEventListener('transitionend', fin);
            settleTimer = setTimeout(fin, 520);   // safety
        }
        modal.querySelectorAll(config.closeSel || '[data-detail-close]').forEach(function (b) { b.addEventListener('click', close); });
        document.addEventListener('keydown', function (e) {
            if (modal.hidden) return;
            if (e.key === 'Escape') { close(); return; }
            if (e.key === 'Tab' && dialog) {   // trap focus within the dialog
                var f = Array.prototype.slice.call(dialog.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'))
                    .filter(function (el) { return el.offsetParent !== null; });
                if (!f.length) { e.preventDefault(); dialog.focus(); return; }
                var first = f[0], last = f[f.length - 1];
                if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
            }
        });
        return { open: open, close: close };
    }

    window.PlatPursuit.Medallion = {
        wire: wire,
        detailModal: detailModal,
        prefersReducedMotion: prefersReducedMotion,
        canTilt: canTilt,
    };
})();
