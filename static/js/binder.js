/* Binder Surface controller.
 * Each SET (badge type) is its own binder view, picked from the set tabs. Within a set,
 * two view modes: Single (one .pp-binder__page at a time) and Spread/Flipbook (one
 * .pp-binder__spread of two facing pages at a time, desktop only). The grid steps 4 -> 2
 * -> 1 column responsively in single mode (CSS).
 * Flipbook navigation plays a 3D page-flip: the current spread's right page (forward) or
 * left page (backward) rotates around the spine, its back face pre-loaded with a clone of
 * the destination's facing page so the motion shows real content turning. Driven by the
 * buttons or by dragging near a page's outer edge. The perspective/preserve-3d scene is
 * enabled only while a flip animates (.pp-binder--flipping) so resting cards keep their
 * hover. Namespaced under window.PlatPursuit.Binder. */
(function () {
    'use strict';

    // A two-page spread of four fixed-128px compact frames needs ~1290px of content, so the
    // flipbook only turns on above that -- below it the spread would overflow. Single mode
    // covers narrower screens. The Flipbook chip + this threshold are kept in sync (the chip
    // is hidden under 1320 in binder.css).
    var FLIPBOOK_MIN = 1320;
    var FLIP_DUR = 880;                                  // full 0 -> 180deg page turn (ms)
    var FLIP_EASE = 'cubic-bezier(0.32, 0, 0.18, 1.0)';  // soft start, lands flat at 180 (no overshoot)
    var FLIP_THRESHOLD = 0.5;                            // release past 50% commits the flip, else springs back
    var EDGE_REGION = 120;                               // px from a spread's outer edge that grabs a page to drag

    function prefersReducedMotion() {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    }

    function groupBy(els, attr) {
        var map = {}, order = [];
        els.forEach(function (el) {
            var k = el.getAttribute(attr) || '__none__';
            if (!map[k]) { map[k] = []; order.push(k); }
            map[k].push(el);
        });
        return { map: map, order: order };
    }

    function initBinder(root) {
        var pages = Array.prototype.slice.call(root.querySelectorAll('.pp-binder__page[data-set]'));
        var spreads = Array.prototype.slice.call(root.querySelectorAll('.pp-binder__spread'));
        var setTabs = Array.prototype.slice.call(root.querySelectorAll('[data-set-tab]'));
        var viewChips = Array.prototype.slice.call(root.querySelectorAll('[data-binder-view]'));
        var prevBtn = root.querySelector('[data-binder-prev]');
        var nextBtn = root.querySelector('[data-binder-next]');
        var counter = root.querySelector('[data-binder-counter]');
        var pagesStage = root.querySelector('.pp-binder__pages-stage');

        var pg = groupBy(pages, 'data-set');
        var sp = groupBy(spreads, 'data-set');
        var pagesBySet = pg.map, spreadsBySet = sp.map;
        var setOrder = pg.order.length ? pg.order : sp.order;
        if (!setOrder.length) return;  // empty album

        var mode = 'single';                 // 'single' | 'spread' (the user's chosen view)
        var activeSet = setOrder[0];
        var pageIdx = {}, spreadIdx = {};    // remembered position per set, per mode
        setOrder.forEach(function (k) { pageIdx[k] = 0; spreadIdx[k] = 0; });

        function flipbookFits() { return window.innerWidth >= FLIPBOOK_MIN; }
        function spreadActive() { return mode === 'spread' && flipbookFits(); }

        // The Frame is a fixed width; a spread fits two pages only with the compact
        // variant (hidden plinth meta + smaller text). Toggle it only when the mode
        // actually flips, not on every set-switch render.
        var lastSpreadMode = null;
        function applyFrameVariant(spreadMode) {
            if (spreadMode === lastSpreadMode) return;
            lastSpreadMode = spreadMode;
            root.querySelectorAll('.pp-frame').forEach(function (f) {
                f.classList.toggle('pp-frame--compact', spreadMode);
                // Reset any flipped card to the front when shrinking to compact (the
                // back face isn't designed for the small size; flip is disabled there).
                if (spreadMode) f.classList.remove('is-flipped');
            });
            if (spreadMode) wireStageDrag();
            else cleanupFlip();  // leaving flipbook
        }

        // ---- 3D page-flip (flipbook mode, within the active set) ----
        var flipInProgress = false, pendingFlipTimer = null;

        // The perspective/preserve-3d scene is enabled ONLY while a flip animates -- a live
        // 3D context at rest breaks the cards' :hover hit-testing (no gleam/lift).
        function setFlipping(on) { root.classList.toggle('pp-binder--flipping', !!on); }

        function clearPendingFlipTimer() {
            if (pendingFlipTimer !== null) { window.clearTimeout(pendingFlipTimer); pendingFlipTimer = null; }
        }
        function curSpreads() { return spreadsBySet[activeSet] || []; }

        // The destination spread sits BEHIND the current one during a flip so its content
        // peeks through as the leaf lifts. Exactly one spread is ever is-next.
        function markIsNext(spreadEl) {
            spreads.forEach(function (s) { s.classList.toggle('is-next', s === spreadEl); });
        }

        // Clone the destination's facing page onto the leaf's back face (ids stripped so the
        // clone doesn't duplicate the binder<->list deep-link anchors).
        function makeBackFace(sourcePage) {
            var back = document.createElement('div');
            back.className = 'pp-binder__flip-leaf-back';
            var clone = sourcePage.cloneNode(true);
            clone.removeAttribute('id');
            clone.querySelectorAll('[id]').forEach(function (n) { n.removeAttribute('id'); });
            clone.style.position = 'static';
            clone.style.width = '100%';
            clone.style.transform = 'none';
            back.appendChild(clone);
            return back;
        }

        function teardownFlipLeaf(leaf) {
            if (!leaf) return;
            leaf.classList.remove('pp-binder__flip-leaf');
            leaf.style.transform = '';
            leaf.style.transition = '';
            var back = leaf.querySelector(':scope > .pp-binder__flip-leaf-back');
            if (back) back.remove();
        }

        // dir: +1 forward (right page rotates to -180), -1 backward (left page rotates to +180).
        // fromRotation lets a drag-commit start mid-rotation. A drag may have pre-wired the
        // leaf before calling this, so every bail past the re-entrancy guard funnels through
        // cleanupFlip() -- otherwise a stranded leaf would freeze the binder with the 3D scene on.
        function doFlip(dir, fromRotation) {
            if (flipInProgress) return;  // re-entrancy guard: never tear down an in-flight flip
            var list = curSpreads();
            var idx = spreadIdx[activeSet] || 0;
            var destIdx = idx + dir;
            if (destIdx < 0 || destIdx >= list.length) { cleanupFlip(); return; }
            var cur = list[idx], dest = list[destIdx];
            var leaf = cur.querySelector(dir > 0 ? '.pp-binder__page--right' : '.pp-binder__page--left');
            var destFacing = dest.querySelector(dir > 0 ? '.pp-binder__page--left' : '.pp-binder__page--right');
            if (!leaf || !destFacing) { cleanupFlip(); return; }

            // Reduced motion: jump straight to the destination, no rotation (drag is disabled
            // too, so no leaf is ever wired on this path).
            if (prefersReducedMotion()) {
                cleanupFlip();
                spreadIdx[activeSet] = destIdx;
                render();
                return;
            }

            flipInProgress = true;
            setFlipping(true);
            markIsNext(dest);
            if (!leaf.classList.contains('pp-binder__flip-leaf')) {
                leaf.appendChild(makeBackFace(destFacing));
                leaf.classList.add('pp-binder__flip-leaf');
            }

            var from = typeof fromRotation === 'number' ? fromRotation : 0;
            var target = dir > 0 ? -180 : 180;
            var dur = Math.max(200, FLIP_DUR * ((180 - Math.abs(from)) / 180));  // proportional to remaining angle

            // Pin the start rotation explicitly so the transition has two rotateY endpoints
            // to interpolate (a button flip otherwise snaps from the resting matrix).
            leaf.style.transition = 'none';
            leaf.style.transform = 'rotateY(' + from + 'deg)';
            void leaf.offsetWidth;
            leaf.style.transition = 'transform ' + dur + 'ms ' + FLIP_EASE;
            leaf.style.transform = 'rotateY(' + target + 'deg)';

            clearPendingFlipTimer();
            pendingFlipTimer = window.setTimeout(function () {
                // Swap + teardown in the SAME tick so the browser never paints a frame
                // where the leaf snapped back to 0 but the old spread is still current.
                spreadIdx[activeSet] = destIdx;
                render();                 // is-current -> dest, clears is-next
                teardownFlipLeaf(leaf);
                setFlipping(false);       // drop the 3D scene so rest-state hover works
                flipInProgress = false;
                pendingFlipTimer = null;
            }, dur + 30);
        }

        // Spring a partially-dragged leaf back to rest when the release was short of the
        // commit threshold. Duration scales with the travelled angle so a tiny drag snaps back.
        function springBackFlip(leaf, fromRotation) {
            var dur = Math.max(200, FLIP_DUR * (Math.abs(fromRotation || 0) / 180));
            void leaf.offsetWidth;
            leaf.style.transition = 'transform ' + dur + 'ms ' + FLIP_EASE;
            leaf.style.transform = 'rotateY(0deg)';
            clearPendingFlipTimer();
            pendingFlipTimer = window.setTimeout(function () {
                teardownFlipLeaf(leaf);
                markIsNext(null);
                setFlipping(false);
                flipInProgress = false;
                pendingFlipTimer = null;
            }, dur + 30);
        }

        function cleanupFlip() {
            clearPendingFlipTimer();
            flipInProgress = false;
            dragState = null;
            setFlipping(false);
            root.classList.remove('pp-binder__flip-dragging');
            if (pagesStage) pagesStage.style.cursor = '';
            spreads.forEach(function (s) {
                s.classList.remove('is-next');
                teardownFlipLeaf(s.querySelector('.pp-binder__flip-leaf'));
            });
        }

        // ---- Drag-to-flip: grab NEAR the outer page edge and turn it in real time ----
        // No covering element (that would intercept the cards' :hover). A pointerdown within
        // EDGE_REGION of the spread's outer edge starts a drag; everything else passes through
        // to the cards. Wired once to the stage; the handlers no-op outside flipbook mode.
        var dragState = null, stageWired = false;

        function wireStageDrag() {
            if (!pagesStage || stageWired) return;
            stageWired = true;
            pagesStage.addEventListener('pointerdown', onStageDown);
            pagesStage.addEventListener('pointermove', onStageMove);
            pagesStage.addEventListener('pointerup', onStageUp);
            pagesStage.addEventListener('pointercancel', onStageUp);
            // Reset a lingering grab cursor when the pointer leaves the stage at an edge.
            pagesStage.addEventListener('pointerleave', function () {
                if (!dragState) pagesStage.style.cursor = '';
            });
        }

        // Which flip a grab at clientX would start (0 = none): forward near the right edge,
        // backward near the left edge, and only if that destination spread exists.
        function edgeDirAt(clientX) {
            if (!spreadActive() || flipInProgress) return 0;
            var list = curSpreads(), idx = spreadIdx[activeSet] || 0, cur = list[idx];
            if (!cur) return 0;
            var r = cur.getBoundingClientRect();
            if (clientX >= r.right - EDGE_REGION && clientX <= r.right && idx < list.length - 1) return 1;
            if (clientX <= r.left + EDGE_REGION && clientX >= r.left && idx > 0) return -1;
            return 0;
        }

        function onStageDown(e) {
            if (dragState || prefersReducedMotion()) return;  // one drag at a time; no drag under reduced motion
            var dir = edgeDirAt(e.clientX);
            if (!dir) return;  // not an edge grab -> leave the cards (and their hover) alone
            var list = curSpreads(), idx = spreadIdx[activeSet] || 0;
            var cur = list[idx], dest = list[idx + dir];
            var half = cur.getBoundingClientRect().width / 2;
            if (half < 1) return;
            var leaf = cur.querySelector(dir > 0 ? '.pp-binder__page--right' : '.pp-binder__page--left');
            var destFacing = dest.querySelector(dir > 0 ? '.pp-binder__page--left' : '.pp-binder__page--right');
            if (!leaf || !destFacing) return;

            flipInProgress = true;
            setFlipping(true);
            markIsNext(dest);
            leaf.appendChild(makeBackFace(destFacing));
            leaf.classList.add('pp-binder__flip-leaf');
            leaf.style.transition = 'none';  // track the pointer with no easing lag
            root.classList.add('pp-binder__flip-dragging');
            dragState = { dir: dir, leaf: leaf, startX: e.clientX, half: half, rotation: 0, pointerId: e.pointerId };
            try { pagesStage.setPointerCapture(e.pointerId); } catch (_) {}
            e.preventDefault();
        }

        function onStageMove(e) {
            if (dragState) {
                if (e.pointerId !== dragState.pointerId) return;  // ignore a second, uncaptured pointer
                var frac = (e.clientX - dragState.startX) / dragState.half;
                // right edge: drag left (negative) -> [-180, 0]; left edge: drag right -> [0, 180].
                var rot = dragState.dir > 0 ? Math.max(-1, Math.min(0, frac)) * 180
                                            : Math.max(0, Math.min(1, frac)) * 180;
                dragState.rotation = rot;
                dragState.leaf.style.transform = 'rotateY(' + rot + 'deg)';
                return;
            }
            pagesStage.style.cursor = edgeDirAt(e.clientX) ? 'grab' : '';  // telegraph the grab
        }

        function onStageUp(e) {
            if (!dragState || (e && e.pointerId !== dragState.pointerId)) return;
            var st = dragState; dragState = null;
            root.classList.remove('pp-binder__flip-dragging');
            try { pagesStage.releasePointerCapture(st.pointerId); } catch (_) {}
            st.leaf.style.transition = '';
            if (Math.abs(st.rotation) >= 180 * FLIP_THRESHOLD) {
                flipInProgress = false;        // let doFlip re-claim it; leaf already wired
                doFlip(st.dir, st.rotation);   // finish the turn from the released angle
            } else {
                springBackFlip(st.leaf, st.rotation);
            }
        }

        function render() {
            var spreadMode = spreadActive();
            root.setAttribute('data-mode', spreadMode ? 'spread' : 'single');
            applyFrameVariant(spreadMode);

            pages.forEach(function (p) { p.classList.remove('is-current'); });
            spreads.forEach(function (s) { s.classList.remove('is-current', 'is-next'); });

            var list = (spreadMode ? spreadsBySet : pagesBySet)[activeSet] || [];
            var store = spreadMode ? spreadIdx : pageIdx;
            var idx = Math.min(Math.max(store[activeSet] || 0, 0), Math.max(list.length - 1, 0));
            store[activeSet] = idx;
            if (list[idx]) list[idx].classList.add('is-current');

            if (counter) {
                counter.textContent = (spreadMode ? 'Spread ' : 'Page ') +
                    (idx + 1) + ' / ' + Math.max(list.length, 1);
            }
            if (prevBtn) prevBtn.disabled = idx <= 0;
            if (nextBtn) nextBtn.disabled = idx >= list.length - 1;

            viewChips.forEach(function (c) {
                c.classList.toggle('pp-binder__view-chip--active',
                    c.getAttribute('data-binder-view') === (spreadMode ? 'spread' : 'single'));
            });
            setTabs.forEach(function (t) {
                var on = t.getAttribute('data-set-tab') === activeSet;
                t.classList.toggle('is-active', on);
                t.setAttribute('aria-selected', on ? 'true' : 'false');
            });
        }

        function go(delta) {
            if (spreadActive()) { doFlip(delta > 0 ? 1 : -1); return; }  // animated page turn
            var list = pagesBySet[activeSet] || [];
            var n = Math.min(Math.max((pageIdx[activeSet] || 0) + delta, 0), list.length - 1);
            if (n !== pageIdx[activeSet]) { pageIdx[activeSet] = n; render(); }
        }
        if (prevBtn) prevBtn.addEventListener('click', function () { go(-1); });
        if (nextBtn) nextBtn.addEventListener('click', function () { go(1); });

        viewChips.forEach(function (chip) {
            chip.addEventListener('click', function () {
                var v = chip.getAttribute('data-binder-view');
                if (v === 'spread' && !flipbookFits()) return;  // flipbook needs the desktop width
                mode = v;
                render();
            });
        });

        setTabs.forEach(function (tab) {
            tab.addEventListener('click', function () {
                var key = tab.getAttribute('data-set-tab');
                if (!key || key === activeSet || flipInProgress) return;  // ignore mid-flip
                cleanupFlip();          // drop any leaf/is-next from the old set
                activeSet = key;
                render();
            });
        });

        // Sorting re-paginates server-side: reload with the chosen ?sort=.
        var sortSelect = root.querySelector('[data-binder-sort]');
        if (sortSelect) {
            sortSelect.addEventListener('change', function () {
                var params = new URLSearchParams(window.location.search);
                params.set('sort', sortSelect.value);
                window.location.search = params.toString();
            });
        }

        // Keep the displayed mode honest as the viewport crosses the flipbook threshold
        // (a chosen flipbook downgrades to single below FLIPBOOK_MIN, and restores above it).
        // A render() mid-flip would strip is-next / snap is-current back to the source, so
        // cancel any in-flight flip first.
        var raf;
        window.addEventListener('resize', function () {
            if (raf) return;
            raf = window.requestAnimationFrame(function () {
                raf = null;
                if (flipInProgress) cleanupFlip();
                render();
            });
        });

        // Deep-link to a card: land on its set + the page (single) or spread (spread mode).
        function jumpToHash() {
            if (!window.location.hash) return;
            var target = root.querySelector(window.location.hash);
            if (!target) return;
            if (flipInProgress) cleanupFlip();  // a render() mid-flip would corrupt the turn
            var page = target.closest('.pp-binder__page');
            if (!page) return;
            var key = page.getAttribute('data-set');
            if (!key) return;
            activeSet = key;
            var pI = (pagesBySet[key] || []).indexOf(page);
            if (pI >= 0) pageIdx[key] = pI;
            var sI = (spreadsBySet[key] || []).indexOf(target.closest('.pp-binder__spread'));
            if (sI >= 0) spreadIdx[key] = sI;
            render();
        }

        render();
        jumpToHash();
        window.addEventListener('hashchange', jumpToHash);
    }

    function init() {
        document.querySelectorAll('.pp-binder').forEach(initBinder);
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.Binder = { init: init };
})();
