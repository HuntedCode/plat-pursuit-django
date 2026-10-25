/**
 * Celebration moments player (templates/partials/moments/_overlay.html, trophies/services/moment_service.py).
 *
 * One .pp-detail-modal (PlatPursuit.DetailModal owns open, close, Escape and the focus trap) and a <template>
 * per waiting moment. Moments play ONE AT A TIME with "1 of 3", Next and Skip all (owner, 2026-10-25):
 *
 *   auto  welcome, badges, masteries and series rewards: auto-play on a My Pursuit page when the hunter has celebrations on, after any
 *         other modal on the page has had its turn (they open at 450ms and win; this waits for a clear stage)
 *   reel  platinums, then a milestone: NEVER auto-play. Only the strip's pill opens them.
 *
 * A moment is marked played the moment it is ON SCREEN (one small POST), so closing mid-queue cannot replay
 * something already seen, on this device or another. Skip all marks the rest of the open queue played.
 * Reduced motion gets the end state: the medallion's mint and the count-ups are both motion-gated already.
 */
(function () {
    'use strict';
    var PP = window.PlatPursuit || {};
    var root = document.getElementById('pp-moments');
    if (!root || !PP.DetailModal) { return; }

    var AUTO_DELAY = 1100;                 // after the page's own first-visit modals (450ms) have opened
    var MINT_KEY = 'pp-badges-minted';     // the Collection's per-device "already minted" set (collection.js)
    var stage = root.querySelector('[data-moments-stage]');
    var sparks = root.querySelector('[data-moments-sparks]');
    var countEl = root.querySelector('[data-moments-count]');
    var nextBtn = root.querySelector('[data-moments-next]');
    var skipBtn = root.querySelector('[data-moments-skip]');
    var linkEl = root.querySelector('[data-moments-link]');
    var playedUrl = root.getAttribute('data-played-url');
    // `?preview=moments` (team only): sample moments with no rows behind them. Nothing is posted and the
    // Collection's minted set is left alone, so the preview replays on every reload.
    var preview = root.hasAttribute('data-preview');
    var modal = PP.DetailModal(root, { closeSelector: '[data-moments-close]' });
    var queue = [], idx = 0;

    function reduced() {
        return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }
    function waiting(which) {
        var sel = which === 'all' ? 'template[data-moment]' : 'template[data-moment][data-queue="' + which + '"]';
        return Array.prototype.slice.call(document.querySelectorAll(sel));
    }

    function markPlayed(templates) {
        var ids = templates.map(function (t) { return parseInt(t.getAttribute('data-moment'), 10); });
        templates.forEach(function (t) { if (t.parentNode) { t.parentNode.removeChild(t); } });
        refreshPills();
        if (preview || !ids.length || !PP.API) { return; }
        PP.API.post(playedUrl, { ids: ids }).catch(function () { /* it replays once more; never an error to show */ });
    }

    // The Collection mints a badge the first time it sees it earned. This one was just celebrated, so it is
    // already "seen" there: one ceremony per badge, not two.
    function rememberMinted(badgeId) {
        if (!badgeId || preview) { return; }
        try {
            var seen = JSON.parse(localStorage.getItem(MINT_KEY) || 'null');
            if (!Array.isArray(seen)) { return; }   // no set yet: the Collection seeds it silently on first visit
            if (seen.indexOf(badgeId) === -1) { seen.push(badgeId); localStorage.setItem(MINT_KEY, JSON.stringify(seen)); }
        } catch (e) { /* private mode */ }
    }

    // THE FLOURISH, one beat per moment and in the earn vocabulary (sparks, the medallion's own mint, a light
    // sweep), never confetti. The CSS carries the entrances (moments.css); this lands the sparks on the beat
    // the object arrives, from wherever it actually is on screen. Reduced motion: none of it (sparkBurst and
    // the CSS are both gated), the end state is what you see.
    // EXPLODE (owner, 2026-10-25): the sparks are thrown across much of the screen and fall slowly off the
    // bottom of it, which is why the spark layer is the whole viewport, not the dialog.
    var BEATS = {
        badge: { sel: '.pp-moment__med .pp-med', at: 380, color: 'var(--pp-primary)', n: 70 },
        // Phase 4: a mastery throws gold, and a set bursts from between its editions.
        mastered: { sel: '.pp-moment__med .pp-med', at: 380, color: 'var(--med-tier-gold)', n: 80 },
        full_set: { sel: '.pp-moment__set', at: 420, color: 'var(--pp-primary)', n: 80 },
        series_master: { sel: '.pp-moment__set', at: 420, color: 'var(--med-tier-gold)', n: 100 },
        platinum: { sel: '.pp-moment__cover', at: 560, color: 'var(--color-trophy-platinum)', n: 60 },
        milestone: { sel: '.pp-moment__number', at: 520, color: 'var(--color-trophy-platinum)', n: 90 },
    };
    function flourish(kind) {
        var beat = BEATS[kind];
        if (!beat || reduced() || !PP.sparkBurst) { return; }
        window.setTimeout(function () {
            var el = stage.querySelector(beat.sel);
            if (!el || root.hidden) { return; }
            var r = el.getBoundingClientRect();
            PP.sparkBurst(sparks, r.left + r.width / 2, r.top + r.height / 2, beat.color, beat.n, { explode: true });
        }, beat.at);
    }

    function show(i) {
        idx = i;
        var t = queue[i];
        stage.innerHTML = '';
        stage.appendChild(t.content.cloneNode(true));
        flourish(t.getAttribute('data-kind'));
        countEl.textContent = queue.length > 1 ? (i + 1) + ' of ' + queue.length : '';
        var href = t.getAttribute('data-link-href');
        linkEl.hidden = !href;
        if (href) { linkEl.setAttribute('href', href); linkEl.textContent = t.getAttribute('data-link-label'); }
        nextBtn.textContent = i === queue.length - 1 ? 'Done' : 'Next';
        skipBtn.hidden = queue.length - i <= 1;

        if (!reduced()) {
            // Each medallion's own first-earn mint (badge-medallion.css), struck as it lands and the sparks fly;
            // a set's editions one after another. A mastered one has its crown set on in the same beat.
            Array.prototype.forEach.call(stage.querySelectorAll('.pp-med'), function (med, n) {
                window.setTimeout(function () { med.classList.add('is-minting'); }, 360 + n * 180);
            });
        }
        if (PP.countUp) {
            Array.prototype.forEach.call(stage.querySelectorAll('[data-countup]'), function (el) { PP.countUp(el, 900); });
        }
        rememberMinted(t.getAttribute('data-badge-id'));
        markPlayed([t]);
    }

    function play(which, trigger) {
        if (!root.hidden) { return; }      // already playing: never swap a queue out from under the hunter
        queue = which === 'all' ? waiting('auto').concat(waiting('reel')) : waiting(which);
        if (!queue.length) { return; }
        show(0);
        modal.open(trigger || null);
    }

    nextBtn.addEventListener('click', function () {
        if (idx < queue.length - 1) { show(idx + 1); } else { modal.close(); }
    });
    skipBtn.addEventListener('click', function () {
        markPlayed(queue.slice(idx + 1).filter(function (t) { return t.parentNode; }));
        modal.close();
    });

    // The strip's pill says what is left to open, by the same rule the server used to write it.
    function refreshPills() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-moments-open]'), function (pill) {
            var which = pill.getAttribute('data-moments-open');
            var left = waiting(which);
            var label = pill.querySelector('span');
            if (!left.length) { pill.hidden = true; return; }
            if (which === 'all') { label.textContent = left.length + ' new'; return; }
            var plats = left.filter(function (t) { return t.getAttribute('data-kind') === 'platinum'; }).length;
            label.textContent = plats ? plats + ' new platinum' + (plats === 1 ? '' : 's') : 'New milestone';
        });
    }
    document.addEventListener('click', function (e) {
        var pill = e.target.closest && e.target.closest('[data-moments-open]');
        if (pill) { e.preventDefault(); play(pill.getAttribute('data-moments-open'), pill); }
    });

    // PICK IT UP (owner, 2026-10-25): the badge's medallion lifts into the site's own inspector, the same
    // pick-up / put-down the Collection and Browse Badges use (PlatPursuit.Medallion.detailModal), showing the
    // hunter's REAL state of that badge. It opens OVER the moment and puts the medallion back on close.
    var peekEl = document.getElementById('pp-moments-peek');
    var peek = (peekEl && PP.Medallion && PP.Medallion.detailModal)
        ? PP.Medallion.detailModal({ modal: peekEl, bodySel: '[data-peek-body]', closeSel: '[data-peek-close]' })
        : null;
    function pickUp(wrap) {
        var id = wrap.getAttribute('data-badge-id');
        var tmpl = peekEl.getAttribute('data-peek-url') || '';
        var url = tmpl.replace(/0\/$/, id + '/');
        if (!peek || !id || url === tmpl) { return; }
        peek.open(url, wrap.querySelector('.pp-med') || wrap);
    }
    stage.addEventListener('click', function (e) {
        var wrap = e.target.closest && e.target.closest('[data-moment-pick]');
        if (wrap && peek) { e.preventDefault(); pickUp(wrap); }
    });
    stage.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' && e.key !== ' ') { return; }
        var wrap = e.target.closest && e.target.closest('[data-moment-pick]');
        if (wrap && peek) { e.preventDefault(); pickUp(wrap); }
    });
    // Escape while the inspector is up puts the medallion DOWN and leaves the moment open: both dialogs listen
    // on the document, so this capture-phase listener answers first and stops the moment closing under it.
    window.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || !peekEl || peekEl.hidden) { return; }
        e.stopImmediatePropagation();
        if (peek) { peek.close(); }
    }, true);

    // Auto-play waits for a clear stage: no other modal open and no claim ceremony running. Whatever the page
    // opened first has its turn; this never stacks on top of it.
    function stageClear() {
        return !document.querySelector('.pp-detail-modal:not([hidden]):not(#pp-moments)')
            && !document.body.classList.contains('ccx-open');
    }
    function whenStageClear(fn) {
        if (stageClear()) { fn(); return; }
        var mo = new MutationObserver(function () {
            if (!stageClear()) { return; }
            mo.disconnect();
            window.setTimeout(function () { whenStageClear(fn); }, 300);   // still clear after the beat?
        });
        mo.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['hidden', 'class'] });
    }
    // Warm every waiting moment's art now: a <template>'s images only start loading when it is cloned in, which
    // left the medallion dark through its own strike.
    Array.prototype.forEach.call(document.querySelectorAll('template[data-moment]'), function (t) {
        Array.prototype.forEach.call(t.content.querySelectorAll('img[src]'), function (img) {
            new Image().src = img.getAttribute('src');
        });
    });

    // A badge waiting here is celebrated by this player, so the Collection must not mint it as well. This
    // (deferred) script runs before DOMContentLoaded, when collection.js decides what to mint.
    // Only when it will auto-play: with celebrations off the hunter may never open it, and the Collection's
    // mint is then the only one they get.
    if (root.getAttribute('data-autoplay')) {
        waiting('auto').forEach(function (t) { rememberMinted(t.getAttribute('data-badge-id')); });
    }

    if (root.getAttribute('data-autoplay') && waiting('auto').length) {
        window.setTimeout(function () { whenStageClear(function () { play('auto', null); }); }, AUTO_DELAY);
    }
})();
