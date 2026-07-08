/* Collection page controller (baseline).
 * Two concerns: the Surface-level view toggle (Binder vs List, the binder's own
 * Binder/Gallery presentation toggle is separate and owned by binder.js), and the
 * list view's client-side sort / filter / search. The list's "View ->" links set a
 * #card-<id> hash; binder.js flips to that page on hashchange, and this controller just
 * makes the binder visible first. (The binder links back via per-series detail-page links,
 * which are plain server-rendered anchors.) Namespaced under window.PlatPursuit.Collection. */
(function () {
    'use strict';

    var STORAGE_KEY = 'pp-collection-view';

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

    function initViewToggle(root) {
        var views = Array.prototype.slice.call(root.querySelectorAll('.pp-collection__view'));
        var chips = Array.prototype.slice.call(root.querySelectorAll('.pp-collection__view-chip'));
        if (!views.length || !chips.length) return;

        function setView(name) {
            views.forEach(function (v) {
                v.hidden = v.getAttribute('data-collection-view') !== name;
            });
            chips.forEach(function (c) {
                var on = c.getAttribute('data-collection-view') === name;
                c.classList.toggle('is-active', on);
                c.setAttribute('aria-selected', on ? 'true' : 'false');
                c.tabIndex = on ? 0 : -1;   // roving tabindex: only the active view chip is in the tab order
            });
            try { localStorage.setItem(STORAGE_KEY, name); } catch (e) { /* private mode */ }
        }

        chips.forEach(function (c, i) {
            c.addEventListener('click', function () {
                setView(c.getAttribute('data-collection-view'));
            });
            // WAI-ARIA tabs keyboard model: arrows/Home/End move focus AND switch the view.
            c.addEventListener('keydown', function (e) {
                var next = -1;
                if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (i + 1) % chips.length;
                else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (i - 1 + chips.length) % chips.length;
                else if (e.key === 'Home') next = 0;
                else if (e.key === 'End') next = chips.length - 1;
                else return;
                e.preventDefault();
                setView(chips[next].getAttribute('data-collection-view'));
                chips[next].focus();
            });
        });

        // Initial view: a #card-<id> deep-link lands in the Case, otherwise the stored preference
        // (legacy 'binder' maps to 'case'), else the Case.
        var initial = 'case';
        if (window.location.hash.indexOf('#card-') !== 0) {
            try { initial = localStorage.getItem(STORAGE_KEY) || 'case'; } catch (e) { /* noop */ }
        }
        if (initial === 'binder') initial = 'case';
        setView(initial);

        // Row "View ->" cross-link: show the Case first; initCase's hashchange handler then activates
        // the badge's set + scrolls to it. If the hash is ALREADY this card, nudge a hashchange manually.
        root.querySelectorAll('[data-binder-link]').forEach(function (a) {
            a.addEventListener('click', function () {
                setView('case');
                if (window.location.hash === a.getAttribute('href')) {
                    window.dispatchEvent(new Event('hashchange'));
                }
            });
        });
    }

    // The Case: set tabs switch shelves; a #card-<id> deep-link (or list "View ->") lands on the
    // badge's set and scrolls to it.
    function initCase(root) {
        var caseEl = root.querySelector('.pp-case');
        if (!caseEl) return;
        var tabs = Array.prototype.slice.call(caseEl.querySelectorAll('[data-set-tab]'));
        var shelves = Array.prototype.slice.call(caseEl.querySelectorAll('.pp-case__shelf[data-set]'));
        if (!shelves.length) return;

        function activateSet(key) {
            shelves.forEach(function (s) { s.hidden = s.getAttribute('data-set') !== key; });
            tabs.forEach(function (t) {
                var on = t.getAttribute('data-set-tab') === key;
                t.classList.toggle('is-active', on);
                t.setAttribute('aria-selected', on ? 'true' : 'false');
                t.tabIndex = on ? 0 : -1;   // roving tabindex: only the active tab is in the tab order
            });
        }
        tabs.forEach(function (tab, i) {
            tab.addEventListener('click', function () { activateSet(tab.getAttribute('data-set-tab')); });
            // WAI-ARIA tabs keyboard model: arrows/Home/End move focus AND activate the tab.
            tab.addEventListener('keydown', function (e) {
                var next = -1;
                if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (i + 1) % tabs.length;
                else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (i - 1 + tabs.length) % tabs.length;
                else if (e.key === 'Home') next = 0;
                else if (e.key === 'End') next = tabs.length - 1;
                else return;
                e.preventDefault();
                activateSet(tabs[next].getAttribute('data-set-tab'));
                tabs[next].focus();
            });
        });
        tabs.forEach(function (t) { t.tabIndex = t.classList.contains('is-active') ? 0 : -1; });

        function jumpToCard() {
            if (window.location.hash.indexOf('#card-') !== 0) return;
            var target = caseEl.querySelector(window.location.hash);
            if (!target) return;
            var shelf = target.closest('.pp-case__shelf[data-set]');
            if (shelf) activateSet(shelf.getAttribute('data-set'));
            (target.closest('.pp-case__slot') || target).scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        jumpToCard();
        window.addEventListener('hashchange', jumpToCard);
    }

    // "Turn it in your hand": tilt the big modal medallion disc in 3D toward the cursor, with a glare
    // that tracks the light, springing back on leave. A hover affordance -> fine-pointer + motion-OK only.
    // The rect is read off the (untransformed) .pp-med so the tilt doesn't feed back into its own bbox.
    function initTilt(scope) {
        if (!canTilt()) return;
        // Perspective is on the SCENE (.pp-med__stage); we rotate the CARD (.pp-med__art) -- the layers'
        // direct parent -- so the preserve-3d parallax reaches them. The rect is read off the (untransformed)
        // scene so the tilt doesn't feed back into its own bbox. Rotation is set as an INLINE transform so it
        // beats the base :hover lift's specificity.
        var scene = scope.querySelector('.pp-bdetail__stage .pp-med__stage');
        var card = scene && scene.querySelector('.pp-med__art');
        if (!card) return;

        var glare = document.createElement('span');
        glare.className = 'pp-med__glare';
        glare.setAttribute('aria-hidden', 'true');
        card.appendChild(glare);   // the glare rides the rotating card
        var foil = card.querySelector('.pp-med__foil');   // present only on holographic (rare/top-tier) badges

        var MAX = 15;   // degrees of tilt at the edges
        scene.addEventListener('pointermove', function (e) {
            if (card._hintTimer) { clearTimeout(card._hintTimer); card._hintTimer = null; }   // engaged during the beat -> skip the auto-hint
            if (card._hintAnim) { card._hintAnim.cancel(); card._hintAnim = null; }   // user's own turn wins over the hint
            var r = scene.getBoundingClientRect();
            var px = (e.clientX - r.left) / r.width;
            var py = (e.clientY - r.top) / r.height;
            card.style.transform = 'rotateX(' + (-(py - 0.5) * 2 * MAX).toFixed(2) + 'deg) '
                + 'rotateY(' + ((px - 0.5) * 2 * MAX).toFixed(2) + 'deg) scale(1.05)';
            glare.style.setProperty('--gx', (px * 100).toFixed(1) + '%');
            glare.style.setProperty('--gy', (py * 100).toFixed(1) + '%');
            // Holographic shimmer tracks the cursor (inline beats the CSS :hover shift).
            if (foil) foil.style.backgroundPosition = (px * 100).toFixed(1) + '% ' + (py * 100).toFixed(1) + '%';
            card.classList.add('is-tilting');
        });
        scene.addEventListener('pointerleave', function () {
            card.classList.remove('is-tilting');
            card.style.transform = '';   // spring back to rest via the CSS transition
            if (foil) foil.style.backgroundPosition = '';
        });
    }

    // Badge detail ("pick it up"): tap a medallion -> fetch its detail partial into the modal. The slot
    // keeps its href to the badge page as a no-JS fallback.
    function initDetail(root) {
        var modal = document.getElementById('collection-detail');
        if (!modal) return;
        var body = modal.querySelector('[data-detail-body]');
        var dialog = modal.querySelector('.pp-detail-modal__dialog');
        var lastFocus = null, busy = false, growingSrc = null, closing = false;
        // The active grow/shrink "settle" handler + its safety timer, tracked so an interrupting close can
        // tear them down. Without this, a close mid-open leaves grow's `done` armed: it later fires over the
        // shrink (snapping the disc back to center) and steals focus onto the hidden dialog.
        var settleEl = null, settleFn = null, settleTimer = null;
        // The modal disc's stable REST rect (centered, full size), captured in growInto BEFORE any transform.
        // shrinkOut reuses it so a close mid-grow computes the put-down from the true rest box, not the
        // half-grown (transformed) rect -- measuring mid-animation sent the disc to the wrong place.
        var discRestRect = null;
        function clearSettle() {
            if (settleEl && settleFn) settleEl.removeEventListener('transitionend', settleFn);
            if (settleTimer) clearTimeout(settleTimer);
            settleEl = settleFn = settleTimer = null;
        }
        // Cancel a pending/running affordance hint (its timer + WAAPI anim live on the modal card node).
        function cancelHint() {
            var c = body.querySelector('.pp-bdetail__stage .pp-med__art');
            if (!c) return;
            if (c._hintTimer) { clearTimeout(c._hintTimer); c._hintTimer = null; }
            if (c._hintAnim) {
                c._hintAnim.cancel(); c._hintAnim = null;
                // Snap the card flat instantly so a half-played wiggle can't carry its rotation into the put-down.
                c.style.transition = 'none'; c.style.transform = 'none';
            }
        }

        function open(url, sourceEl) {
            if (busy || !modal.hidden) return;   // don't open over an already-open (or closing) modal
            busy = true;
            lastFocus = document.activeElement;   // capture the trigger before async work moves focus
            // Container transform: capture the tapped medallion's DISC rect BEFORE the fetch so we can grow
            // it into the modal's big medallion (spatial continuity). We match disc-to-disc (not the whole
            // medallion) because the text below has a fixed font size that mustn't scale. Skipped under
            // reduced-motion.
            var srcMed = sourceEl && sourceEl.querySelector('.pp-med');
            var srcDisc = srcMed && srcMed.querySelector('.pp-med__stage');
            // Measure the ART (not the stage) so the grow launches from where the badge VISUALLY is: the art
            // carries the hover lift + press transform, the stage's box does not (it's immune to child
            // transforms). The art fills the stage 1:1, so it's otherwise equivalent. Falls back to the stage.
            var srcArt = srcMed && srcMed.querySelector('.pp-med__art');
            var canGrow = srcDisc && !prefersReducedMotion();
            var srcRect = canGrow ? (srcArt || srcDisc).getBoundingClientRect() : null;
            fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.text() : null; })
                .then(function (html) {
                    busy = false;
                    if (html == null) return;
                    body.innerHTML = html;
                    initTilt(body);
                    var grow = canGrow && srcRect.width;
                    // Add is-growing BEFORE showing so the dialog uses the fade (not the spring) from frame 1.
                    if (grow) modal.classList.add('is-growing');
                    modal.hidden = false;
                    document.body.style.overflow = 'hidden';
                    if (grow) growInto(srcMed, srcRect);
                    else if (dialog) dialog.focus();
                })
                .catch(function () { busy = false; });
        }

        // Grow ONLY the disc (.pp-med__stage) from the tapped disc's spot to its final spot -- the same
        // element the whole way (no clone), and the meter/label (siblings of the disc) stay put on BOTH the
        // shelf and the hero, so the progress bar never travels. Matches disc-to-disc; the layers morph
        // between the shelf and modal renderings (foreground, parallax, brightness) via is-opening.
        function growInto(srcMed, sr) {
            var modalDisc = body.querySelector('.pp-bdetail__stage .pp-med__stage');
            if (!modalDisc) { if (dialog) dialog.focus(); return; }
            var mr = modalDisc.getBoundingClientRect();
            discRestRect = mr;   // stable rest box -- shrinkOut reuses this so a mid-grow close measures right
            var scale = sr.width / mr.width;                    // start shrunk to the tapped disc's size
            var dx = (sr.left + sr.width / 2) - (mr.left + mr.width / 2);
            var dy = (sr.top + sr.height / 2) - (mr.top + mr.height / 2);
            growingSrc = srcMed || null;
            // Hide only the DISC on the shelf (leave the label/meter in place) -- the badge lifts off, its
            // text/meter stays put. Nothing to fade or reconcile since they never move.
            if (growingSrc) { var gs = growingSrc.querySelector('.pp-med__stage'); if (gs) gs.style.visibility = 'hidden'; }
            // Mirror of the put-down: start in the SHELF look (is-opening, instant) at the tapped spot, then
            // remove it so the layers settle to the modal look + the chrome fades in AS the disc grows.
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
                // Leave the tapped grid badge HIDDEN while the modal is open -- it reads as the user having
                // "picked it up". close() restores it (respawns on the shelf) when they put it back.
                // NB: do NOT remove is-growing here -- that re-triggers the base ppDetailIn spring (the
                // "flash"). It's cleared in close(), when the modal is hidden and can't re-animate.
                if (dialog) dialog.focus();
                var hintCard = body.querySelector('.pp-bdetail__stage .pp-med__art');   // wait a beat, then hint the tilt
                if (hintCard) hintCard._hintTimer = setTimeout(hintWiggle, 500);
            }
            settleEl = modalDisc; settleFn = done;
            modalDisc.addEventListener('transitionend', done);
            modalDisc.style.transition = 'transform 0.46s cubic-bezier(0.2, 0.82, 0.25, 1)';
            modalDisc.style.transform = 'none';   // grow, fully opaque (no fade -- mirror of the put-down)
            settleTimer = setTimeout(done, 640);   // safety (0.46s transition + buffer): never leave the disc mid-transform
        }

        // Affordance hint: a beat after the pick-up settles, tilt the object toward a RANDOM point on its
        // rim and back, decaying over a couple of swings, to signal it can be turned in the hand. Same
        // fine-pointer + motion-OK context as the tilt (a touch device can't tilt, so no hint). A real
        // pointermove cancels it (see initTilt) so the user's own turn always wins.
        function hintWiggle() {
            if (closing || modal.hidden || !canTilt()) return;   // never fire a hint into a close (the timer may already be queued)
            var card = body.querySelector('.pp-bdetail__stage .pp-med__art');
            if (!card || !card.animate) return;
            card._hintTimer = null;
            // Already engaging (cursor resting over it -> :hover lift active, or mid-tilt)? Skip -- starting
            // the wiggle from identity would snap off that transform. The user will discover the tilt anyway.
            if (card.matches && card.matches(':hover')) return;
            var A = 11, theta = Math.random() * Math.PI * 2;   // peak tilt (deg) toward a random rim point
            var rx = -Math.sin(theta) * A, ry = Math.cos(theta) * A;
            function f(m, s) {
                return 'rotateX(' + (rx * m).toFixed(2) + 'deg) rotateY(' + (ry * m).toFixed(2) + 'deg) scale(' + s + ')';
            }
            var anim = card.animate(
                [
                    { transform: f(0, 1) },
                    { transform: f(1, 1.03), offset: 0.18 },      // swing toward the point
                    { transform: f(-0.66, 1.024), offset: 0.42 }, // back past the opposite point
                    { transform: f(0.36, 1.014), offset: 0.64 },  // decaying return swing
                    { transform: f(-0.15, 1.005), offset: 0.84 },
                    { transform: f(0, 1) }                        // settle to rest
                ],
                { duration: 1150, easing: 'cubic-bezier(0.4, 0, 0.3, 1)' }
            );
            card._hintAnim = anim;
            anim.onfinish = anim.oncancel = function () { if (card._hintAnim === anim) card._hintAnim = null; };
        }

        function close() {
            if (closing) return;
            clearSettle();   // cancel any in-flight grow settle (its handler + safety timer) before it can fire over the close
            cancelHint();    // and any pending/running affordance wiggle
            if (growingSrc && !prefersReducedMotion()) { closing = true; shrinkOut(); }
            else finishClose();
        }
        function finishClose() {
            closing = false;
            discRestRect = null;
            clearSettle();   // idempotent: covers finishClose reached directly (e.g. shrinkOut's missing-disc bail)
            cancelHint();    // must run BEFORE body is wiped (it looks the card up in the DOM)
            modal.hidden = true;
            modal.classList.remove('is-growing', 'is-closing');
            body.innerHTML = '';
            document.body.style.overflow = '';
            if (growingSrc) {
                var gs = growingSrc.querySelector('.pp-med__stage');
                if (gs) gs.style.visibility = '';   // the disc reappears on the shelf (its shadow already faded in during the shrink)
                growingSrc = null;
            }
            if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) { /* gone */ } }
        }
        // Reverse of growInto: shrink the medallion back into the shelf spot while the chrome fades, then
        // respawn the shelf badge -- "putting it back down". The medallion stays visible during the shrink
        // (chrome fades via CSS, dialog opacity untouched) and fades out only as it lands, so no swap flash.
        function shrinkOut() {
            var modalDisc = body.querySelector('.pp-bdetail__stage .pp-med__stage');
            var srcDisc = growingSrc && growingSrc.querySelector('.pp-med__stage');
            if (!modalDisc || !srcDisc) { finishClose(); return; }
            // Use the REST rect (not a fresh measure): if we're closing mid-grow the disc is still transformed,
            // and measuring it now would compute the shrink from the half-grown box -> the disc lurches to the
            // wrong place. The rest box is stable; the disc transitions to it from wherever the grow left it.
            var mr = discRestRect || modalDisc.getBoundingClientRect();
            var sr = srcDisc.getBoundingClientRect();   // still valid: body scroll is locked while the modal is open
            var scale = sr.width / mr.width;
            var dx = (sr.left + sr.width / 2) - (mr.left + mr.width / 2);
            var dy = (sr.top + sr.height / 2) - (mr.top + mr.height / 2);
            modal.classList.remove('is-growing');
            modal.classList.add('is-closing');   // fades scrim + dialog chrome + hero meter out (NOT the disc)
            modalDisc.style.transformOrigin = 'center center';
            modalDisc.style.transition = 'transform 0.4s cubic-bezier(0.4, 0.05, 0.55, 0.95)';
            modalDisc.style.transform = 'translate(' + dx + 'px, ' + dy + 'px) scale(' + scale + ')';
            // Disc stays fully OPAQUE the whole way down (only the chrome dissolves); the shelf disc swaps in
            // the instant it lands -- the mirror of the pick-up, no dissolve. The meter never travels.
            var settled = false;
            function fin(e) {
                if (settled || (e && e.propertyName !== 'transform')) return;
                settled = true;
                clearSettle();
                finishClose();
            }
            settleEl = modalDisc; settleFn = fin;
            modalDisc.addEventListener('transitionend', fin);
            settleTimer = setTimeout(fin, 520);   // safety (0.4s transition + buffer)
        }

        // If the tapped disc sits partly behind the sticky chrome (top nav/sub-nav/hotbar stack via
        // --sticky-top, or the bottom .mobile-tabbar), scroll it fully into the clear BEFORE the pick-up
        // so the growing disc doesn't overlap the chrome oddly. Instant scroll -> the rect is re-measured
        // by open() right after. No-op when the pick-up won't run (reduced motion) or nothing's obscured.
        function revealFromChrome(slot) {
            if (prefersReducedMotion()) return;
            var disc = slot.querySelector('.pp-med__stage');
            if (!disc) return;
            var r = disc.getBoundingClientRect();
            var vh = window.innerHeight, margin = 16;
            var top = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--sticky-top')) || 0;
            var bottom = 0;
            var tabbar = document.querySelector('.mobile-tabbar');
            // NB: on desktop the tabbar is display:none (lg:hidden); its rect is all-zeros, so guard on
            // height > 0, else bottom would balloon to the full viewport and scroll every badge off-screen.
            if (tabbar) { var tr = tabbar.getBoundingClientRect(); if (tr.height > 0 && tr.top < vh) bottom = vh - tr.top; }
            var safeTop = top + margin, safeBottom = vh - bottom - margin;
            var dy = 0;
            if (r.top < safeTop) dy = r.top - safeTop;                     // behind top chrome -> nudge the badge down
            else if (r.bottom > safeBottom) dy = r.bottom - safeBottom;    // behind bottom chrome -> nudge it up
            if (dy) window.scrollBy(0, dy);
        }

        // Delegate across the whole page: any medallion with a data-modal-url (Case slots, Showcase,
        // Chase, Gallery cells) opens the detail modal. The List table's "View ->" uses a #card hash
        // instead, so it doesn't match here.
        root.addEventListener('click', function (e) {
            var slot = e.target.closest('[data-modal-url]');
            if (!slot) return;
            e.preventDefault();
            revealFromChrome(slot);   // free the badge from behind sticky chrome before the pick-up
            open(slot.getAttribute('data-modal-url'), slot);
        });
        modal.querySelectorAll('[data-detail-close]').forEach(function (b) { b.addEventListener('click', close); });
        document.addEventListener('keydown', function (e) {
            if (modal.hidden) return;
            if (e.key === 'Escape') { close(); return; }
            if (e.key === 'Tab') {   // trap focus within the dialog
                var f = Array.prototype.slice.call(dialog.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'))
                    .filter(function (el) { return el.offsetParent !== null; });
                if (!f.length) { e.preventDefault(); dialog.focus(); return; }
                var first = f[0], last = f[f.length - 1];
                if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
            }
        });
    }

    // Showcase mode toggle (Rarest / Newest / Top tier): swap the visible row, persisted. A lightweight
    // form of curation until per-badge picking ships with the customization update.
    function initShowcase(root) {
        var sc = root.querySelector('.pp-showcase');
        if (!sc) return;
        var modes = Array.prototype.slice.call(sc.querySelectorAll('[data-showcase-mode]'));
        var rows = Array.prototype.slice.call(sc.querySelectorAll('[data-showcase-row]'));
        if (modes.length < 2) return;   // one mode -> nothing to toggle
        var KEY = 'pp-collection-showcase';
        function setMode(name) {
            if (!rows.some(function (r) { return r.getAttribute('data-showcase-row') === name; })) return;
            rows.forEach(function (r) { r.hidden = r.getAttribute('data-showcase-row') !== name; });
            modes.forEach(function (m) { var on = m.getAttribute('data-showcase-mode') === name; m.classList.toggle('is-active', on); m.setAttribute('aria-pressed', on ? 'true' : 'false'); });
            try { localStorage.setItem(KEY, name); } catch (e) { /* private mode */ }
        }
        modes.forEach(function (m) { m.addEventListener('click', function () { setMode(m.getAttribute('data-showcase-mode')); }); });
        var stored = null;
        try { stored = localStorage.getItem(KEY); } catch (e) { /* noop */ }
        if (stored) setMode(stored);   // else leave the server default (first mode active)
    }

    // --- Shared filter/sort primitives (the List table + the Gallery wall filter the SAME flat badge
    // set on the SAME data-* attributes; only the presentation and the sort UI differ). ---
    var TIER_ORDER = ['bronze', 'silver', 'gold', 'platinum'];
    var STATE_ORDER = ['earned', 'maintenance', 'in_progress', 'unearned'];

    function stateMatches(elState, want) {
        if (want === 'all' || elState === want) return true;
        // A "maintenance" badge is still held -> it counts as earned for filtering
        // (it has no dedicated chip; the lapse only matters on the shelf).
        return want === 'earned' && elState === 'maintenance';
    }

    function elMatches(el, filters, term) {
        return (filters.tier === 'all' || el.getAttribute('data-tier') === filters.tier)
            && stateMatches(el.getAttribute('data-state'), filters.state)
            && (filters.theme === 'all' || el.getAttribute('data-theme') === filters.theme)
            && (term === ''
                || el.getAttribute('data-series').indexOf(term) !== -1
                || el.getAttribute('data-badge').indexOf(term) !== -1);
    }

    function sortValue(el, key) {
        switch (key) {
            case 'set_number': return parseInt(el.getAttribute('data-set-number'), 10) || 0;
            case 'series':     return el.getAttribute('data-series');
            case 'tier':       return TIER_ORDER.indexOf(el.getAttribute('data-tier'));
            case 'state':      return STATE_ORDER.indexOf(el.getAttribute('data-state'));
            case 'progress':   return parseFloat(el.getAttribute('data-progress')) || 0;
            case 'rarity':     return parseFloat(el.getAttribute('data-rarity-pct')) || 0;
            case 'rank':       return parseInt(el.getAttribute('data-rank'), 10) || 0;
            case 'theme':      return el.getAttribute('data-theme');
            default:           return 0;
        }
    }

    function compareBy(key, dir) {
        return function (a, b) {
            var av = sortValue(a, key), bv = sortValue(b, key);
            if (av < bv) return dir === 'asc' ? -1 : 1;
            if (av > bv) return dir === 'asc' ? 1 : -1;
            return 0;
        };
    }

    // Wire the tier/state/set filter chips within `scope`: click sets the dimension + repaints the group's
    // active state, then re-applies. Shared by the List and the Gallery (identical chip markup).
    function wireFilterChips(scope, filters, applyFilters) {
        scope.querySelectorAll('[data-filter-tier], [data-filter-state], [data-filter-theme]').forEach(function (chip) {
            chip.addEventListener('click', function () {
                var dim = chip.hasAttribute('data-filter-tier') ? 'tier'
                    : chip.hasAttribute('data-filter-state') ? 'state' : 'theme';
                filters[dim] = chip.getAttribute('data-filter-' + dim);
                scope.querySelectorAll('[data-filter-' + dim + ']').forEach(function (c) {
                    c.classList.toggle('is-active', c === chip);
                });
                applyFilters();
            });
        });
    }

    // The List: the data table. Column-header sort (asc/desc toggle + aria-sort) over the shared engine.
    function initList(root) {
        var listRoot = root.querySelector('.pp-list');
        if (!listRoot) return;
        var tbody = listRoot.querySelector('.pp-list__table tbody');
        if (!tbody) return;

        var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        var stats = listRoot.querySelector('[data-visible-count]');
        var emptyMsg = listRoot.querySelector('[data-empty-message]');
        var totalRows = rows.length;
        var filters = { tier: 'all', state: 'all', theme: 'all' };
        var searchTerm = '';
        var sortKey = 'series';
        var sortDir = 'asc';

        function applyFilters() {
            var visible = 0;
            rows.forEach(function (row) {
                var ok = elMatches(row, filters, searchTerm);
                row.style.display = ok ? '' : 'none';
                if (ok) visible++;
            });
            if (stats) stats.textContent = visible + ' of ' + totalRows;
            if (emptyMsg) emptyMsg.hidden = visible !== 0;
        }

        function applySort() {
            rows.slice().sort(compareBy(sortKey, sortDir)).forEach(function (row) { tbody.appendChild(row); });
            listRoot.querySelectorAll('th[data-sort]').forEach(function (th) {
                th.classList.remove('is-sorted-asc', 'is-sorted-desc');
                if (th.getAttribute('data-sort') === sortKey) {
                    th.classList.add(sortDir === 'asc' ? 'is-sorted-asc' : 'is-sorted-desc');
                    th.setAttribute('aria-sort', sortDir === 'asc' ? 'ascending' : 'descending');
                } else {
                    th.setAttribute('aria-sort', 'none');
                }
            });
        }

        wireFilterChips(listRoot, filters, applyFilters);

        var search = listRoot.querySelector('[data-search]');
        if (search) {
            search.addEventListener('input', function (e) {
                searchTerm = e.target.value.toLowerCase().trim();
                applyFilters();
            });
        }

        listRoot.querySelectorAll('th[data-sort]').forEach(function (th) {
            function toggleSort() {
                var key = th.getAttribute('data-sort');
                if (sortKey === key) {
                    sortDir = sortDir === 'asc' ? 'desc' : 'asc';
                } else {
                    sortKey = key;
                    sortDir = 'asc';
                }
                applySort();
            }
            th.addEventListener('click', toggleSort);
            th.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleSort(); }
            });
        });

        applySort();  // default: series ascending (set numbers are sparse pre-launch)
    }

    // The Gallery: the visual filter wall. Same filters as the List, but a flat medallion grid and a
    // sort <select> (value = "key:dir") instead of column headers.
    function initGallery(root) {
        var gal = root.querySelector('.pp-gallery');
        if (!gal) return;
        var grid = gal.querySelector('[data-gallery-grid]');
        var cells = Array.prototype.slice.call(gal.querySelectorAll('[data-gallery-cell]'));
        if (!grid || !cells.length) return;

        var stats = gal.querySelector('[data-visible-count]');
        var emptyMsg = gal.querySelector('[data-empty-message]');
        var total = cells.length;
        var filters = { tier: 'all', state: 'all', theme: 'all' };
        var searchTerm = '';

        // Any user filter/sort switches the wall to instant (the entrance stagger is a one-time arrival, not
        // a working-tool behaviour; without this, re-showing a cell via display would replay its animation).
        // applyFilters is only ever called by user actions (chips + search), never on init.
        function applyFilters() {
            gal.classList.add('is-touched');
            var visible = 0;
            cells.forEach(function (cell) {
                var ok = elMatches(cell, filters, searchTerm);
                cell.style.display = ok ? '' : 'none';
                if (ok) visible++;
            });
            if (stats) stats.textContent = visible + ' of ' + total;
            if (emptyMsg) emptyMsg.hidden = visible !== 0;
        }

        wireFilterChips(gal, filters, applyFilters);

        var search = gal.querySelector('[data-search]');
        if (search) {
            search.addEventListener('input', function (e) {
                searchTerm = e.target.value.toLowerCase().trim();
                applyFilters();
            });
        }

        var sortSel = gal.querySelector('[data-gallery-sort]');
        function applySort() {
            var spec = ((sortSel && sortSel.value) || 'series:asc').split(':');
            cells.slice().sort(compareBy(spec[0], spec[1] || 'asc')).forEach(function (c) { grid.appendChild(c); });
        }
        if (sortSel) sortSel.addEventListener('change', function () { gal.classList.add('is-touched'); applySort(); });
        applySort();  // default matches the select's first option (series A-Z)
    }

    function init() {
        var root = document.querySelector('.pp-collection');
        if (!root) return;
        initViewToggle(root);
        initCase(root);
        initShowcase(root);
        initDetail(root);
        initGallery(root);
        initList(root);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.Collection = { init: init };
})();
