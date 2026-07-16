/* Pursuer Card Forge -- the card is MADE, not faded in.
 *
 * Fires on a *fresh* sync completion (a syncing -> synced transition, so NOT on
 * page-load-already-synced) -- the "your data is fresh, you're up to date" payoff. Also fires
 * on a `?forge=1` preview so the effect can be watched without a real sync. One variant, quick
 * + rare enough to use everywhere.
 *
 * Choreography lives in pursuer-card-forge.css; this file adds the state class, inserts the
 * scan-beam + hot arcing sparks (drawn from the Frame's fabrication vocabulary, NOT confetti),
 * and ticks the platinum + family numbers up. Depends on navsync.js's
 * `platpursuit:sync-status-changed`. Reduced-motion: the whole forge is skipped.
 */
(function () {
    'use strict';
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var SPARK_COUNT = 32;   // hot sparks per forge
    var SEEN_CAP = 250;     // max platinum np-ids retained in the seen set (bounds localStorage)

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
        // Persist the seen set, bounded: current shelf ids first + a capped tail of older ones.
        // The current shelf is always retained (never false-"new"); older ids beyond the cap drop
        // off safely -- a platinum never re-enters the recent shelf once newer ones push it down.
        var merged = ids.slice();
        (Array.isArray(seen) ? seen : []).forEach(function (id) {
            if (merged.indexOf(id) === -1) merged.push(id);
        });
        if (merged.length > SEEN_CAP) merged.length = SEEN_CAP;
        try { localStorage.setItem(KEY, JSON.stringify(merged)); } catch (_) {}
        return out;
    }

    // Mark a Recent cover as newly earned: a persistent flowing ring that clears on first
    // hover/tap. EVERY genuinely-new platinum gets one, however many arrived (one sync or several
    // missed visits), so none goes silently unacknowledged.
    function markNew(cover) {
        if (!cover) return;
        cover.classList.add('pursuer-card__cover--new');
        cover.addEventListener('pointerenter', function () {
            cover.classList.remove('pursuer-card__cover--new');
        }, { once: true });
    }

    // Plan the slot-in: which VISIBLE Recent covers are newly earned, and whether to play the
    // single-platinum conveyor. The conveyor (slide the previous top-5 over, new one in at the
    // front) only reads right for exactly one new platinum entering at the front; 2+ (a batch, or
    // a long absence) just get marked in place. previewSlot forces the front for ?forge=slot.
    function planSlotIn(card, previewSlot) {
        var strip = card.querySelector('.pursuer-card__shelf[data-shelf="recent"] .pursuer-card__strip');
        var covers = strip ? strip.querySelectorAll('.pursuer-card__cover') : [];
        if (!covers.length) return { strip: null, covers: covers, news: [], shift: false };
        var news;
        if (previewSlot) {
            newPlatCovers(card);                            // record seen so the preview is idempotent
            news = [covers[0]];                             // force the newest for the demo
        } else {
            news = newPlatCovers(card);                     // genuinely-new covers (also records seen)
        }
        // Only the top-6 are visible; index 6 is the offscreen outgoing cover, never marked.
        // The 6 mirrors pursuer_card_service.showcase_limit + pursuer-card.css nth-child(7).
        var arr = Array.prototype.slice.call(covers);
        news = news.filter(function (c) { var i = arr.indexOf(c); return i > -1 && i < 6; });
        var shift = news.length === 1 && covers.length >= 2 && news[0] === covers[0];
        return { strip: strip, covers: covers, news: news, shift: shift };
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

        var plan = planSlotIn(card, previewSlot);
        if (plan.shift) {
            var slot = plan.covers[0].offsetWidth + 8;      // cover width + strip gap
            if (slot < 20) {                                // shelf hidden/unmeasurable
                plan.shift = false;
            } else {
                // Hold the previous top-5 a beat after they settle, then slide the new one in at
                // the front (the oldest slides off the end); mark it as it lands.
                var DELAY = 2300, DUR = 780;
                plan.strip.animate(
                    [{ transform: 'translateX(-' + slot + 'px)' }, { transform: 'translateX(0)' }],
                    { duration: DUR, delay: DELAY, easing: 'cubic-bezier(0.3,0.85,0.25,1)', fill: 'backwards' }
                );
                setTimeout(function () { markNew(plan.news[0]); }, DELAY + DUR - 40);
            }
        }
        if (!plan.shift) {
            // No conveyor -> mark every new cover once the showcase has revealed.
            setTimeout(function () { plan.news.forEach(markNew); }, 1500);
        }

        setTimeout(function () { spawnSparks(card, SPARK_COUNT); }, 340);
        setTimeout(function () { tickUp(card.querySelector('.pursuer-card__plat'), 1000); }, 700);
        setTimeout(function () { tickFamilies(card); }, 1150);
        // The conveyor runs to ~3.8s; otherwise the forge settles by 2.6s. (--new rings persist.)
        var endMs = plan.shift ? 3850 : 2600;
        setTimeout(function () {
            card.classList.remove('pursuer-card--forging');
            scan.remove();
            card.dataset.forging = '';
        }, endMs);
    }

    function forgeVisibleCard() {
        var card = document.querySelector('.pursuer-card');
        if (card) forge(card);
    }

    // Live sync completion: the on-page card is still pre-sync, so fetch a freshly-built one, swap
    // it in, then forge (detection runs on the fresh covers, so new platinums actually appear +
    // slot in). Falls back to forging the current card if the fetch fails. Assumes the sync's data
    // (counts, last_synced, new plats) is committed server-side by the time 'synced' fires; if the
    // fetch raced ahead and returned pre-sync data, detection finds nothing new and the forge just
    // plays without a slot-in -- a benign degradation.
    function refreshAndForge() {
        if (!document.querySelector('.pursuer-card')) return;
        fetch('/api/v1/pursuer-card/', { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
            .then(function (r) { return (r.ok && r.status !== 204) ? r.text() : null; })
            .then(function (html) {
                var target = document.querySelector('.pursuer-card');
                if (html && target) {
                    var tmp = document.createElement('div');
                    tmp.innerHTML = html.trim();
                    var fresh = tmp.querySelector('.pursuer-card');
                    if (fresh) { target.replaceWith(fresh); target = fresh; }
                }
                if (target) forge(target);
            })
            .catch(function () { forgeVisibleCard(); });
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
                if (wasSyncing) { refreshAndForge(); markSeen(Math.floor(Date.now() / 1000)); }
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
