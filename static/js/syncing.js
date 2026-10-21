/**
 * The syncing hero of the gates surface (home/_hero_syncing.html on home/landing.html).
 *
 * Two jobs, both riding navsync.js's poll events (this file NEVER polls; navsync owns the
 * network and dispatches `platpursuit:sync-progress` on every poll and
 * `platpursuit:sync-status-changed` once per real transition):
 *
 *   1. Live personalization: the page may render before PSN's own totals land on the profile;
 *      when the poll payload carries `psn_found`, fill the numbers and swap the sentence.
 *   2. The enter-moment state machine: on a FIRST sync (the finale block only renders then),
 *      a `synced` transition swaps the status card into "Your Pursuer has emerged" with the
 *      final counts and an "Enter your Pursuit" CTA -- no auto-reload. Quick refreshes and
 *      errors keep the old reload behaviour. (The old five-panel walkthrough carousel is
 *      gone: the landing's real sections render below the hero and ARE the tour.)
 *
 * Ordering subtlety (load-bearing): navsync dispatches status-changed BEFORE the same poll's
 * sync-progress, so at finale time the cached stats are one poll stale. The stat fill is
 * idempotent and re-runs from the progress listener after completion; the trailing event
 * corrects the figures within the same tick.
 */
(function () {
    'use strict';

    // ── 2. Live personalization upgrade ─────────────────────────────────────
    function wirePersonalization() {
        var pending = document.querySelector('[data-psn-pending]');
        var line = document.querySelector('[data-psn-line]');
        if (!pending || !line || pending.hidden) { return; }   // already personalized server-side

        document.addEventListener('platpursuit:sync-progress', function upgrade(e) {
            var found = e && e.detail && e.detail.psn_found;
            if (!found || !found.total) { return; }
            document.removeEventListener('platpursuit:sync-progress', upgrade);
            var total = line.querySelector('[data-psn-found-total]');
            var plats = line.querySelector('[data-psn-found-plats]');
            if (total) { total.textContent = Number(found.total).toLocaleString('en-US'); }
            if (plats) { plats.textContent = Number(found.plats || 0).toLocaleString('en-US'); }
            pending.hidden = true;
            line.hidden = false;
        });
    }

    // ── Banked-so-far tally ─────────────────────────────────────────────────
    function wireTally() {
        var el = document.querySelector('[data-sync-tally]');
        if (!el) { return; }   // refreshes and non-initial states render no tally
        var prev = parseInt(el.dataset.countup, 10) || 0;

        document.addEventListener('platpursuit:sync-progress', function (e) {
            // The payload's stats ARE the tally: the per-type denorms climb live during the
            // walk (post_save signal), and summing them keeps this number definitionally
            // consistent with the finale's four figures.
            var stats = e && e.detail && e.detail.stats;
            if (!stats) { return; }
            var tally = (stats.plats || 0) + (stats.golds || 0)
                      + (stats.silvers || 0) + (stats.bronzes || 0);
            if (tally === prev) { return; }
            el.dataset.countup = tally;
            // Old-to-new ticking (countUp's `from`), reduced-motion handled inside it.
            if (window.PlatPursuit && PlatPursuit.countUp) {
                PlatPursuit.countUp(el, 600, { from: prev });
            } else {
                el.textContent = Number(tally).toLocaleString('en-US');
            }
            prev = tally;
        });
    }

    // ── 3. Enter-moment state machine ───────────────────────────────────────
    function wireFinale() {
        var finale = document.querySelector('[data-sync-complete]');
        var live = document.querySelector('[data-sync-live]');
        var card = document.querySelector('[data-sync-card]');
        var completed = false;
        var counted = false;
        var latestPursuit = null;

        function hook(name) { return finale ? finale.querySelector('[data-pursuit-' + name + ']') : null; }
        function setText(name, value) { var el = hook(name); if (el) { el.textContent = value; } }

        function fillPursuit(pursuit) {
            // What the hunter's history already earned (FV-03): the synced poll's `pursuit` block
            // (sync_views._pursuit_payload). Exactly one of the two lines shows; both render hidden so
            // the finale never flashes the wrong one. Idempotent, like the stats it replaced: it runs on
            // completion with whatever has arrived, then again from the trailing progress event.
            if (!finale) { return; }
            var count = (pursuit && pursuit.count) || 0;
            var yes = hook('yes'), none = hook('none'), claim = hook('claim');
            if (yes) { yes.hidden = !count; }
            if (none) { none.hidden = !!count; }
            if (claim) { claim.hidden = !count; }
            if (!count) { return; }
            var countEl = hook('count');
            if (countEl) {
                countEl.dataset.countup = count;
                if (counted) { countEl.textContent = Number(count).toLocaleString('en-US'); }
            }
            setText('noun', count === 1 ? 'contract is' : 'contracts are');
            setText('pronoun', count === 1 ? 'it' : 'them');
            // Said only when claiming moves the level, as the claim banner says it.
            var moves = !!pursuit.level && pursuit.level > (pursuit.from_level || 0);
            var reach = hook('reach');
            if (reach) { reach.hidden = !moves; }
            if (moves) {
                setText('level', Number(pursuit.level).toLocaleString('en-US'));
                setText('rank', pursuit.rank || '');
            }
            var top = hook('top');
            if (top) {
                top.textContent = '';
                (pursuit.top || []).forEach(function (job) {
                    // Text nodes, never markup: job names are data.
                    var li = document.createElement('li'), n = document.createElement('span');
                    li.appendChild(document.createTextNode(job.name + ' '));
                    n.className = 'pp-tally';
                    n.textContent = job.from + ' → ' + job.to;
                    li.appendChild(n);
                    top.appendChild(li);
                });
            }
        }

        function startCountUps() {
            // One frame after the reveal, ON PURPOSE: navsync dispatches the trailing
            // sync-progress synchronously right after status-changed, so by the time this
            // frame runs the data-countup target already holds the final figure -- the
            // count-up plays once, to the right number. countUp itself handles
            // prefers-reduced-motion (instant set) and reads the target off data-countup.
            counted = true;
            var countEl = hook('count');
            if (!countEl) { return; }
            if (window.PlatPursuit && PlatPursuit.countUp) { PlatPursuit.countUp(countEl, 900); }
            else { countEl.textContent = Number(countEl.dataset.countup || 0).toLocaleString('en-US'); }
        }

        document.addEventListener('platpursuit:sync-progress', function (e) {
            var data = (e && e.detail) || {};
            if (data.pursuit) { latestPursuit = data.pursuit; }
            if (completed) { fillPursuit(latestPursuit); }
        });

        document.addEventListener('platpursuit:sync-status-changed', function (e) {
            if (!e.detail) { return; }
            if (e.detail.status === 'synced') {
                if (completed) { return; }   // a re-sync from the finale must not replay it
                if (!finale || !live) {
                    // Quick refresh (no finale block rendered): the old behaviour -- straight in.
                    window.location.reload();
                    return;
                }
                completed = true;
                fillPursuit(latestPursuit);
                live.hidden = true;
                finale.hidden = false;
                if (card) { card.classList.add('is-emerged'); }
                requestAnimationFrame(startCountUps);
                return;
            }
            if (e.detail.status === 'error') {
                // HomeView re-renders the error styling and messaging server-side.
                window.location.reload();
            }
        });
    }

    // ── DEBUG dev panel (renders only under settings.DEBUG) ─────────────────
    function wireDevPanel() {
        var panel = document.querySelector('[data-sync-dev]');
        if (!panel) { return; }
        var step = 0;
        var progressBtn = panel.querySelector('[data-sync-dev-progress]');
        var syncedBtn = panel.querySelector('[data-sync-dev-synced]');

        function emitProgress(detail) {
            document.dispatchEvent(new CustomEvent('platpursuit:sync-progress', { detail: detail }));
        }

        if (progressBtn) {
            progressBtn.addEventListener('click', function () {
                step = Math.min(step + 1, 4);
                var canned = [
                    { sync_percentage: 15, sync_progress: 61, sync_target: 412, stats: { plats: 4, golds: 40, silvers: 199, bronzes: 820 }, psn_found: { total: 8412, plats: 71, level: 512 } },
                    { sync_percentage: 55, sync_progress: 227, sync_target: 412, stats: { plats: 31, golds: 301, silvers: 1400, bronzes: 4100 }, psn_found: { total: 8412, plats: 71, level: 512 } },
                    { sync_percentage: 100, sync_progress: 412, sync_target: 412, is_finalizing: true, finalize_phase: 'health_check', stats: { plats: 60, golds: 700, silvers: 2600, bronzes: 6900 } },
                    { sync_percentage: 100, sync_progress: 412, sync_target: 412, is_finalizing: true, finalize_phase: 'stats_badges', stats: { plats: 71, golds: 841, silvers: 2500, bronzes: 5000 } }
                ][step - 1];
                if (canned) { emitProgress(canned); }
            });
        }
        // "Simulate live sync": a paced run of poll payloads (one every 1.2s, ~24s total)
        // with the tally and bar climbing together -- the closest feel to a real first sync
        // without waiting on one. Re-click restarts. Does NOT auto-finish; hit Simulate
        // synced when you want the finale.
        var liveBtn = panel.querySelector('[data-sync-dev-live]');
        var liveTimer = null;
        if (liveBtn) {
            liveBtn.addEventListener('click', function () {
                if (liveTimer) { clearInterval(liveTimer); }
                var TICKS = 20, TARGET_TASKS = 412, TARGET_TALLY = 8412;
                var i = 0;
                liveTimer = setInterval(function () {
                    i += 1;
                    // Ease the climb so late-sync gains feel smaller, like real per-game jobs.
                    var p = 1 - Math.pow(1 - i / TICKS, 2);
                    // The stats ARE the tally: split the climb across the four metals so
                    // both the tally line and (post-synced) the finale read sensibly.
                    var t = Math.round(p * TARGET_TALLY);
                    emitProgress({
                        sync_percentage: Math.round(p * 100),
                        sync_progress: Math.round(p * TARGET_TASKS),
                        sync_target: TARGET_TASKS,
                        stats: { plats: Math.round(t * 0.008), golds: Math.round(t * 0.1),
                                 silvers: Math.round(t * 0.3), bronzes: t - Math.round(t * 0.008) - Math.round(t * 0.1) - Math.round(t * 0.3) },
                        psn_found: { total: 8412, plats: 71, level: 512 }
                    });
                    if (i >= TICKS) { clearInterval(liveTimer); liveTimer = null; }
                }, 1200);
            });
        }
        if (syncedBtn) {
            syncedBtn.addEventListener('click', function () {
                if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
                // Real ordering: status-changed FIRST, trailing progress with final stats after.
                document.dispatchEvent(new CustomEvent('platpursuit:sync-status-changed', { detail: { status: 'synced' } }));
                emitProgress({ sync_percentage: 100, stats: { plats: 71, golds: 841, silvers: 2500, bronzes: 5000 },
                               pursuit: { count: 60, from_level: 1, level: 103, rank: 'Hunter V', top: [{ name: 'Pathfinder', from: 1, to: 17 }, { name: 'Gunslinger', from: 1, to: 11 }, { name: 'Infiltrator', from: 1, to: 10 }] } });
            });
        }
    }

    function boot() {
        wirePersonalization();
        wireTally();
        wireFinale();
        wireDevPanel();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
