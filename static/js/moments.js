/**
 * Celebration moments player (templates/partials/moments/_overlay.html, trophies/services/moment_service.py).
 *
 * One .pp-detail-modal (PlatPursuit.DetailModal owns open, close, Escape and the focus trap) and a <template>
 * per waiting moment. Moments play ONE AT A TIME with "1 of 3", Next and Skip all (owner, 2026-10-25):
 *
 *   auto  welcome + badges: auto-play on a My Pursuit page when the hunter has celebrations on, after any
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
    var countEl = root.querySelector('[data-moments-count]');
    var nextBtn = root.querySelector('[data-moments-next]');
    var skipBtn = root.querySelector('[data-moments-skip]');
    var linkEl = root.querySelector('[data-moments-link]');
    var playedUrl = root.getAttribute('data-played-url');
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
        if (!ids.length || !PP.API) { return; }
        PP.API.post(playedUrl, { ids: ids }).catch(function () { /* it replays once more; never an error to show */ });
    }

    // The Collection mints a badge the first time it sees it earned. This one was just celebrated, so it is
    // already "seen" there: one ceremony per badge, not two.
    function rememberMinted(badgeId) {
        if (!badgeId) { return; }
        try {
            var seen = JSON.parse(localStorage.getItem(MINT_KEY) || 'null');
            if (!Array.isArray(seen)) { return; }   // no set yet: the Collection seeds it silently on first visit
            if (seen.indexOf(badgeId) === -1) { seen.push(badgeId); localStorage.setItem(MINT_KEY, JSON.stringify(seen)); }
        } catch (e) { /* private mode */ }
    }

    function show(i) {
        idx = i;
        var t = queue[i];
        stage.innerHTML = '';
        stage.appendChild(t.content.cloneNode(true));
        countEl.textContent = queue.length > 1 ? (i + 1) + ' of ' + queue.length : '';
        var href = t.getAttribute('data-link-href');
        linkEl.hidden = !href;
        if (href) { linkEl.setAttribute('href', href); linkEl.textContent = t.getAttribute('data-link-label'); }
        nextBtn.textContent = i === queue.length - 1 ? 'Done' : 'Next';
        skipBtn.hidden = queue.length - i <= 1;

        var med = stage.querySelector('.pp-med');
        if (med && !reduced()) {
            // The medallion's own first-earn mint (badge-medallion.css), the site's earn vocabulary.
            window.requestAnimationFrame(function () { med.classList.add('is-minting'); });
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
