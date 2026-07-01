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

    // Recent-shelf covers whose platinum we've never seen before -> genuinely new this sync.
    // localStorage 'pp_seen_plats' is the union of every plat np-id we've shown, so each plat
    // slots in exactly once. First-ever call (no record) just seeds the set, slotting nothing.
    function newPlatCovers(card) {
        var KEY = 'pp_seen_plats';
        var covers = card.querySelectorAll('.pursuer-card__shelf[data-shelf="recent"] .pursuer-card__cover[data-np-id]');
        var ids = [], byId = {};
        for (var i = 0; i < covers.length; i++) {
            var id = covers[i].dataset.npId;
            if (id) { ids.push(id); byId[id] = covers[i]; }
        }
        var seen = null;
        try { seen = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (_) {}
        var out = [];
        if (Array.isArray(seen)) {
            var set = {};
            seen.forEach(function (id) { set[id] = 1; });
            ids.forEach(function (id) { if (!set[id]) out.push(byId[id]); });
        }
        var union = {};
        (Array.isArray(seen) ? seen : []).forEach(function (id) { union[id] = 1; });
        ids.forEach(function (id) { union[id] = 1; });
        try { localStorage.setItem(KEY, JSON.stringify(Object.keys(union))); } catch (_) {}
        return out;
    }

    // The slot-in shift: the Recent strip holds one slot to the left (showing the previous top-5)
    // through the reveal, then slides right so the new platinum enters at the front and the oldest
    // (the extra cover the server rendered) slides off the end. Returns the hero cover (the new
    // one) so its flare class can be cleared on cleanup, or null when there's no beat to play.
    function runShift(card, previewSlot) {
        var shelf = card.querySelector('.pursuer-card__shelf[data-shelf="recent"]');
        var strip = shelf && shelf.querySelector('.pursuer-card__strip');
        if (!strip) return null;
        var covers = strip.querySelectorAll('.pursuer-card__cover');
        if (covers.length < 2) return null;                 // nothing to shift in/out
        var hero = covers[0];                               // Recent is newest-first -> new = front
        if (previewSlot) {
            newPlatCovers(card);                            // record seen, but force the beat
        } else if (newPlatCovers(card).indexOf(hero) === -1) {
            return null;                                    // the front cover isn't a new platinum
        }
        var slot = hero.offsetWidth + 8;                    // cover width + strip gap
        if (slot < 20) return null;                         // shelf hidden/unmeasurable -> skip
        strip.animate(
            [{ transform: 'translateX(-' + slot + 'px)' }, { transform: 'translateX(0)' }],
            { duration: 720, delay: 1500, easing: 'cubic-bezier(0.3,0.85,0.25,1)', fill: 'backwards' }
        );
        hero.classList.add('pursuer-card__cover--hero');    // CSS flares it as the shift lands
        return hero;
    }

    function forge(card, previewSlot) {
        if (!card || card.dataset.forging === '1' || reduce) return;
        card.dataset.forging = '1';
        var scan = document.createElement('div');
        scan.className = 'pursuer-card__forge-scan';
        card.appendChild(scan);
        // restart-safe: clear any prior state + force a reflow so the animation replays
        card.classList.remove('pursuer-card--forging');
        void card.offsetWidth;
        card.classList.add('pursuer-card--forging');
        var hero = runShift(card, previewSlot);
        setTimeout(function () { spawnSparks(card, 32); }, 340);
        setTimeout(function () { tickUp(card.querySelector('.pursuer-card__plat'), 1000); }, 700);
        setTimeout(function () { tickFamilies(card); }, 1150);
        setTimeout(function () {
            card.classList.remove('pursuer-card--forging');
            if (hero) hero.classList.remove('pursuer-card__cover--hero');
            scan.remove();
            card.dataset.forging = '';
        }, 2850);
    }

    function forgeVisibleCard() {
        var card = document.querySelector('.pursuer-card');
        if (card) forge(card);
    }

    document.addEventListener('DOMContentLoaded', function () {
        var SEEN_KEY = 'pp_forge_seen';
        function markSeen(v) { try { localStorage.setItem(SEEN_KEY, String(v)); } catch (_) {} }
        function getSeen() { try { return parseInt(localStorage.getItem(SEEN_KEY) || '0', 10) || 0; } catch (_) { return 0; } }

        // Live: a fresh sync completes while the card is on screen -> forge immediately. Stamp
        // "seen" with now so the catch-up below won't re-forge for this same sync on the next load.
        var wasSyncing = false;
        document.addEventListener('platpursuit:sync-status-changed', function (e) {
            var status = e && e.detail && e.detail.status;
            if (status === 'syncing') {
                wasSyncing = true;
            } else if (status === 'synced') {
                if (wasSyncing) { forgeVisibleCard(); markSeen(Math.floor(Date.now() / 1000)); }
                wasSyncing = false;
            }
        });

        // Catch-up: a sync that finished while you were away/elsewhere -> forge on this view (the
        // page rendered with the now-fresh data). Fire if the card's last-synced is newer than
        // the last sync we forged for.
        var card = document.querySelector('.pursuer-card');
        var syncedAt = card ? (parseInt(card.dataset.syncedAt || '0', 10) || 0) : 0;
        if (card && syncedAt > getSeen()) {
            forge(card);
            markSeen(syncedAt);
        }

        // Preview: ?forge=1 replays the forge; ?forge=slot also demos a platinum slotting in.
        var pv = /[?&]forge=(\w+)/.exec(window.location.search);
        if (pv) { var pc = document.querySelector('.pursuer-card'); if (pc) forge(pc, pv[1] === 'slot'); }
    });
})();
