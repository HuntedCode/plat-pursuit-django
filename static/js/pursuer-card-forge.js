/* Pursuer Card Forge -- the card is MADE, not faded in.
 *
 * Fires on a *fresh* sync completion (a syncing -> synced transition, so NOT on
 * page-load-already-synced) -- the "your data is fresh, you're up to date" payoff. Also fires
 * on a `?forge=1` preview so the effect can be watched without a real sync. One variant, quick
 * + rare enough to use everywhere.
 *
 * Choreography lives in pursuer-card-forge.css; this file adds the state class, inserts the
 * scan-beam + hot arcing sparks (drawn from the Frame's fabrication vocabulary, NOT confetti),
 * and ticks the platinum + family numbers up. Depends on hotbar.js's
 * `platpursuit:sync-status-changed`. Reduced-motion: the whole forge is skipped.
 */
(function () {
    'use strict';
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // Hot sparks along the full top edge: an upward-ish spread that arcs over and falls.
    function spawnSparks(card, n) {
        var w = card.offsetWidth, h = card.offsetHeight;
        for (var i = 0; i < n; i++) {
            var s = document.createElement('span');
            s.className = 'pursuer-card__forge-spark';
            s.style.left = (w * (0.04 + Math.random() * 0.92)) + 'px';
            s.style.top = '1px';
            card.appendChild(s);
            var ang = (-90 + (Math.random() * 150 - 75)) * Math.PI / 180;
            var sp = 40 + Math.random() * 95;
            var vx = Math.cos(ang) * sp, vy = Math.sin(ang) * sp;
            var drop = h * (0.5 + Math.random() * 0.6);
            var anim = s.animate([
                { transform: 'translate(0px,0px)', opacity: 1 },
                { transform: 'translate(' + vx + 'px,' + vy + 'px)', opacity: 1, offset: 0.35 },
                { transform: 'translate(' + (vx * 1.4) + 'px,' + drop + 'px)', opacity: 0 }
            ], { duration: 550 + Math.random() * 550, easing: 'cubic-bezier(0.3,0.55,0.7,1)' });
            anim.onfinish = (function (node) { return function () { node.remove(); }; })(s);
        }
    }

    // Roll a number element up to its rendered value (Tally-style tick-up).
    function tickUp(el, dur) {
        if (!el) return;
        var target = parseInt((el.textContent || '').replace(/\D/g, ''), 10);
        if (!(target > 1)) return;
        var t0 = null;
        function step(ts) {
            if (t0 === null) t0 = ts;
            var p = Math.min(1, (ts - t0) / dur), e = 1 - Math.pow(1 - p, 3);
            el.textContent = Math.round(target * e).toLocaleString();
            if (p < 1) requestAnimationFrame(step); else el.textContent = target.toLocaleString();
        }
        requestAnimationFrame(step);
    }

    function tickFamilies(card) {
        var fams = card.querySelectorAll('.pursuer-card__fam-n');
        for (var i = 0; i < fams.length; i++) tickUp(fams[i], 850);
    }

    function forge(card) {
        if (!card || card.dataset.forging === '1' || reduce) return;
        card.dataset.forging = '1';
        var scan = document.createElement('div');
        scan.className = 'pursuer-card__forge-scan';
        card.appendChild(scan);
        // restart-safe: clear any prior state + force a reflow so the animation replays
        card.classList.remove('pursuer-card--forging');
        void card.offsetWidth;
        card.classList.add('pursuer-card--forging');
        setTimeout(function () { spawnSparks(card, 32); }, 340);
        setTimeout(function () { tickUp(card.querySelector('.pursuer-card__plat'), 1000); }, 700);
        setTimeout(function () { tickFamilies(card); }, 1150);
        setTimeout(function () {
            card.classList.remove('pursuer-card--forging');
            scan.remove();
            card.dataset.forging = '';
        }, 2600);
    }

    function forgeVisibleCard() {
        var card = document.querySelector('.pursuer-card');
        if (card) forge(card);
    }

    document.addEventListener('DOMContentLoaded', function () {
        // Fresh sync completion -> re-forge (only on a real syncing -> synced transition).
        var wasSyncing = false;
        document.addEventListener('platpursuit:sync-status-changed', function (e) {
            var status = e && e.detail && e.detail.status;
            if (status === 'syncing') {
                wasSyncing = true;
            } else if (status === 'synced') {
                if (wasSyncing) forgeVisibleCard();
                wasSyncing = false;
            }
        });
        // Preview: any ?forge= value replays it.
        if (/[?&]forge=/.test(window.location.search)) forgeVisibleCard();
    });
})();
