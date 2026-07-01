/* Pursuer Card Forge -- the card is MADE, not faded in.
 *
 * Fires on a *fresh* sync completion (a syncing -> synced transition, so NOT on
 * page-load-already-synced) as a snappy re-forge -- the "your data is fresh, you're up to
 * date" payoff. Also fires on a `?forge=1` (full) / `?forge=quick` preview so the effect can
 * be watched without a real sync. The full "birth" variant on a user's first-ever sync is a
 * follow-on (needs a server first-sync flag).
 *
 * Choreography lives in pursuer-card-forge.css; this file adds the state class, inserts the
 * scan-beam + hot arcing sparks (drawn from the Frame's fabrication vocabulary, NOT confetti),
 * and ticks the platinum count up. Depends on hotbar.js's `platpursuit:sync-status-changed`.
 * Reduced-motion: the whole forge is skipped -- the card simply stays as it is.
 */
(function () {
    'use strict';
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // Hot sparks off the top edge: an upward-ish spread that arcs over and falls under gravity.
    function spawnSparks(card, n) {
        var w = card.offsetWidth, h = card.offsetHeight;
        for (var i = 0; i < n; i++) {
            var s = document.createElement('span');
            s.className = 'pursuer-card__forge-spark';
            s.style.left = (w * (0.34 + Math.random() * 0.32)) + 'px';
            s.style.top = '1px';
            card.appendChild(s);
            var ang = (-90 + (Math.random() * 110 - 55)) * Math.PI / 180;
            var sp = 45 + Math.random() * 75;
            var vx = Math.cos(ang) * sp, vy = Math.sin(ang) * sp;
            var drop = h * (0.45 + Math.random() * 0.5);
            var anim = s.animate([
                { transform: 'translate(0px,0px)', opacity: 1 },
                { transform: 'translate(' + vx + 'px,' + vy + 'px)', opacity: 1, offset: 0.35 },
                { transform: 'translate(' + (vx * 1.5) + 'px,' + drop + 'px)', opacity: 0 }
            ], { duration: 520 + Math.random() * 520, easing: 'cubic-bezier(0.3,0.55,0.7,1)' });
            anim.onfinish = (function (node) { return function () { node.remove(); }; })(s);
        }
    }

    // Roll the platinum count up to its rendered value (Tally-style tick-up).
    function tickPlat(card) {
        var el = card.querySelector('.pursuer-card__plat');
        if (!el) return;
        var target = parseInt((el.textContent || '').replace(/\D/g, ''), 10);
        if (!(target > 1)) return;
        var dur = 1000, t0 = null;
        function step(ts) {
            if (t0 === null) t0 = ts;
            var p = Math.min(1, (ts - t0) / dur), e = 1 - Math.pow(1 - p, 3);
            el.textContent = Math.round(target * e).toLocaleString();
            if (p < 1) requestAnimationFrame(step); else el.textContent = target.toLocaleString();
        }
        requestAnimationFrame(step);
    }

    function forge(card, quick) {
        if (!card || card.dataset.forging === '1' || reduce) return;
        card.dataset.forging = '1';
        var cls = quick ? 'pursuer-card--forging-quick' : 'pursuer-card--forging';
        var scan = document.createElement('div');
        scan.className = 'pursuer-card__forge-scan';
        card.appendChild(scan);
        // restart-safe: clear any prior state + force a reflow so the animation replays
        card.classList.remove('pursuer-card--forging', 'pursuer-card--forging-quick');
        void card.offsetWidth;
        card.classList.add(cls);
        setTimeout(function () { spawnSparks(card, quick ? 10 : 18); }, quick ? 50 : 360);
        setTimeout(function () { tickPlat(card); }, quick ? 230 : 680);
        setTimeout(function () {
            card.classList.remove(cls);
            scan.remove();
            card.dataset.forging = '';
        }, quick ? 1400 : 2600);
    }

    function forgeVisibleCard(quick) {
        var card = document.querySelector('.pursuer-card');
        if (card) forge(card, quick);
    }

    document.addEventListener('DOMContentLoaded', function () {
        // Fresh sync completion -> snappy re-forge (only on a real syncing -> synced transition).
        var wasSyncing = false;
        document.addEventListener('platpursuit:sync-status-changed', function (e) {
            var status = e && e.detail && e.detail.status;
            if (status === 'syncing') {
                wasSyncing = true;
            } else if (status === 'synced') {
                if (wasSyncing) forgeVisibleCard(true);
                wasSyncing = false;
            }
        });
        // Preview: ?forge=1 (full birth) or ?forge=quick (re-forge).
        var m = /[?&]forge=(\w+)/.exec(window.location.search);
        if (m) forgeVisibleCard(m[1] === 'quick');
    });
})();
