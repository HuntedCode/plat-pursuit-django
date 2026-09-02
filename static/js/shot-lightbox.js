/**
 * Hero screenshot lightbox (shared): FLIP grow-out/shrink-home open and close, directional
 * carousel push with a ghost of the outgoing image, filmstrip, keyboard arrows and touch swipe.
 * Extracted verbatim from game-detail.js when the concept Game page adopted the hero, so both
 * detail pages drive one implementation.
 *
 * Contract: markup comes from templates/trophies/partials/game_detail/shot_lightbox.html
 * (#gd-shot-modal dialog + #gd-shot-data json_script) plus opener thumbnails matching
 * `#gd-shots [data-shot]` in the hero. Binds nothing when the dialog is absent.
 * Plain DOMContentLoaded on purpose: neither host restores this markup via htmx history
 * (List detail never push-swaps; the Game page switcher runs hx-push-url="false"), so the
 * onPageReady(first) restore contract buys nothing here.
 */
document.addEventListener('DOMContentLoaded', () => {
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    (function () {
        const modal = document.getElementById('gd-shot-modal');
        const dataEl = document.getElementById('gd-shot-data');
        if (!modal || !dataEl) return;
        let shots = [];
        try { shots = JSON.parse(dataEl.textContent) || []; } catch (_) { shots = []; }
        if (!shots.length) return;

        const img = document.getElementById('gd-shot-img');
        const countEl = document.getElementById('gd-shot-count');
        const strip = document.getElementById('gd-shot-strip');
        const thumbs = strip ? Array.from(strip.querySelectorAll('[data-shot-to]')) : [];
        let idx = 0;
        let source = null;   // element the current view "belongs to" (for the grow/shrink FLIP)
        let pageToken = 0;   // latest paging slide, so overlapping navs clear .is-paging correctly
        let ghost = null;    // the outgoing image's clone, slid out during a push swap
        let openToken = 0;   // open generation: invalidates a deferred (img-load) FLIP if superseded by a later open/close

        function rectOf(el) { return el ? el.getBoundingClientRect() : null; }
        function clearGhost() {
            if (!ghost) return;
            if (ghost.getAnimations) ghost.getAnimations().forEach((a) => a.cancel());
            if (ghost.parentNode) ghost.remove();
            ghost = null;
        }

        // FLIP: animate the big image between its full box and a source thumbnail's box, so it
        // appears to grow out of (open) / shrink back into (close) the thumbnail it came from.
        function flip(srcRect, opening, done) {
            const last = img.getBoundingClientRect();
            if (!srcRect || !srcRect.width || !last.width) { if (done) done(); return; }
            const scale = srcRect.width / last.width;
            const dx = (srcRect.left + srcRect.width / 2) - (last.left + last.width / 2);
            const dy = (srcRect.top + srcRect.height / 2) - (last.top + last.height / 2);
            const thumbT = `translate(${dx}px, ${dy}px) scale(${scale})`;
            const frames = opening
                ? [{ transform: thumbT, opacity: 0.4 }, { transform: 'none', opacity: 1 }]
                : [{ transform: 'none', opacity: 1 }, { transform: thumbT, opacity: 0.4 }];
            const a = img.animate(frames, { duration: opening ? 340 : 260, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: opening ? 'none' : 'forwards' });
            if (done) {
                a.onfinish = done;
                // Opening: if a later action cancels this, still clear is-animating (don't strand the chrome/image).
                // Closing: a cancel means we're REOPENING -> must NOT run finalize (it would close the fresh modal).
                if (opening) a.oncancel = done;
            }
        }

        // Warm the neighbours so a push swap never flashes an undecoded image (invisible-until-missing polish).
        function preloadAround() {
            if (shots.length < 2) return;
            [(idx + 1) % shots.length, (idx - 1 + shots.length) % shots.length].forEach((i) => {
                const im = new Image(); im.decoding = 'async'; im.src = shots[i];
            });
        }
        function paint() {
            idx = (idx + shots.length) % shots.length;
            if (img) img.src = shots[idx];
            if (countEl) countEl.textContent = (idx + 1) + ' / ' + shots.length;
            thumbs.forEach((t, i) => {
                const on = i === idx;
                t.classList.toggle('is-active', on);
                if (on) t.scrollIntoView({ block: 'nearest', inline: 'center', behavior: reduce ? 'auto' : 'smooth' });
            });
            preloadAround();
        }
        // Directional PUSH swap (arrows / filmstrip / keyboard): the outgoing image is pushed out
        // one way while the incoming one slides in from the other, edge-to-edge (a carousel push).
        function go(delta) {
            // Ignore switches while the modal's OPEN or CLOSE FLIP is running (is-animating spans the whole
            // open window, incl. the img-load wait). A push mid-open collides with the deferred FLIP-open and
            // corrupts the carousel -- the exact "switch as the modal is opening" bug.
            if (modal.classList.contains('is-closing') || modal.classList.contains('is-animating') || !delta) return;
            const dir = delta > 0 ? 1 : -1;         // +1 next (new from right), -1 prev (new from left)
            const oldSrc = shots[idx];              // capture the outgoing image BEFORE paint swaps it
            idx += delta; paint();                  // paint swaps img.src to the new shot + updates chrome
            if (reduce) return;
            // Stop + clean any in-flight swap first: overlapping WAAPI animations on the same <img>
            // composite and fight each other, wedging the stage (the "frozen carousel" bug).
            if (img.getAnimations) img.getAnimations().forEach((a) => a.cancel());
            clearGhost();
            // The ghost (outgoing image) sits directly above the real <img> but below the nav chrome.
            ghost = document.createElement('img');
            ghost.src = oldSrc;
            ghost.className = img.className + ' gd-shotmodal__ghost';
            ghost.setAttribute('aria-hidden', 'true');
            img.insertAdjacentElement('afterend', ghost);

            modal.classList.add('is-paging');       // clip the stage so the sliding pair can't overflow
            const my = ++pageToken;
            const local = ghost;
            const opts = { duration: 300, easing: 'cubic-bezier(0.4, 0, 0.2, 1)' };
            img.animate([{ transform: `translateX(${dir * 100}%)` }, { transform: 'none' }], opts);
            const g = local.animate([{ transform: 'none' }, { transform: `translateX(${dir * -100}%)` }],
                Object.assign({ fill: 'forwards' }, opts));   // hold the exit frame until removed (no snap-back flash)
            const done = () => {
                if (local.parentNode) local.remove();
                if (ghost === local) ghost = null;
                if (my === pageToken) modal.classList.remove('is-paging');
            };
            g.onfinish = done; g.oncancel = done;
        }

        // The filmstrip scales + fades in/out concurrently with the main image's grow/shrink.
        // Scale-only (no translate) so it can't briefly overflow the viewport and flash a scrollbar.
        function stripAnim(opening) {
            if (!strip || reduce) return;
            const small = { opacity: 0, transform: 'scale(0.94)' };
            const full = { opacity: 1, transform: 'none' };
            strip.animate(opening ? [small, full] : [full, small],
                { duration: opening ? 320 : 240, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: opening ? 'none' : 'forwards' });
        }

        function openAt(i, sourceEl) {
            source = sourceEl || null;
            const srcRect = rectOf(sourceEl);
            // Clear any stale animation state from a prior (possibly interrupted) open/close cycle,
            // and drop held (fill:forwards) transforms so the fresh FLIP starts clean.
            modal.classList.remove('is-closing', 'is-paging');
            if (img.getAnimations) img.getAnimations().forEach((a) => a.cancel());
            if (strip && strip.getAnimations) strip.getAnimations().forEach((a) => a.cancel());
            clearGhost();
            idx = i; paint();
            modal.classList.add('is-animating');   // chrome hidden until the image lands
            if (modal.showModal && !modal.open) modal.showModal();
            if (reduce) { modal.classList.remove('is-animating'); return; }
            // A slow image defers the FLIP-open until load; stamp this open so a later open/close can
            // invalidate the pending run (else it fires a FLIP-open onto a swapped/closed modal).
            const myOpen = ++openToken;
            const run = () => { if (myOpen !== openToken) return; flip(srcRect, true, () => modal.classList.remove('is-animating')); stripAnim(true); };
            if (img.complete && img.naturalWidth) { run(); } else { img.addEventListener('load', run, { once: true }); }
        }
        function finalize() { clearGhost(); modal.classList.remove('is-closing', 'is-animating', 'is-paging'); if (modal.close && modal.open) modal.close(); }
        function close() {
            if (!modal.open) return;
            ++openToken;   // supersede any pending deferred FLIP-open so it can't fire during/after the close
            // Stop any in-flight slide before the shrink so it can't composite into a corrupt transform.
            if (img.getAnimations) img.getAnimations().forEach((a) => a.cancel());
            if (strip && strip.getAnimations) strip.getAnimations().forEach((a) => a.cancel());
            clearGhost();
            modal.classList.add('is-closing', 'is-animating');   // chrome + filmstrip leave with the image
            if (reduce) { finalize(); return; }
            stripAnim(false);
            // Shrink back to the matching hero thumbnail if one is shown, else the opener.
            const home = document.querySelector('#gd-shots [data-shot="' + idx + '"]') || source;
            const homeRect = rectOf(home);
            if (homeRect && homeRect.width) { flip(homeRect, false, finalize); }
            else { img.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'scale(0.94)' }], { duration: 200, easing: 'ease', fill: 'forwards' }).onfinish = finalize; }
        }

        document.querySelectorAll('#gd-shots [data-shot]').forEach((btn) => {
            btn.addEventListener('click', () => openAt(parseInt(btn.dataset.shot, 10) || 0, btn));
        });
        thumbs.forEach((t) => t.addEventListener('click', () => go((parseInt(t.dataset.shotTo, 10) || 0) - idx)));
        modal.querySelector('[data-shot-close]')?.addEventListener('click', close);
        modal.querySelector('[data-shot-prev]')?.addEventListener('click', () => go(-1));
        modal.querySelector('[data-shot-next]')?.addEventListener('click', () => go(1));
        modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
        // Esc: run our choreographed close (shrink home) instead of the instant native close.
        modal.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
        document.addEventListener('keydown', (e) => {
            if (!modal.open) return;
            if (e.key === 'ArrowLeft') go(-1);
            else if (e.key === 'ArrowRight') go(1);
        });
        // Touch: a horizontal swipe on the IMAGE navigates prev/next. No swipe-down-to-close here (it
        // hijacked the whole dialog and fought the filmstrip's horizontal scroll), and the strip keeps
        // its own native scroll (we only listen on the stage). Close via the button / backdrop / Esc.
        const stage = modal.querySelector('.gd-shotmodal__stage');
        if (stage && shots.length > 1) {
            let sx = 0, sy = 0, tracking = false;
            stage.addEventListener('touchstart', (e) => {
                if (e.touches.length !== 1) { tracking = false; return; }
                sx = e.touches[0].clientX; sy = e.touches[0].clientY; tracking = true;
            }, { passive: true });
            stage.addEventListener('touchend', (e) => {
                if (!tracking) return;
                tracking = false;
                const t = e.changedTouches[0];
                const dx = t.clientX - sx, dy = t.clientY - sy;
                // Clear horizontal intent only (so a vertical drag doesn't accidentally page). Left -> next.
                if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy) * 1.4) { go(dx < 0 ? 1 : -1); }
            }, { passive: true });
        }
    })();
});
