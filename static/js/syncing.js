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

    // ── 3. Enter-moment state machine ───────────────────────────────────────
    function wireFinale() {
        var finale = document.querySelector('[data-sync-complete]');
        var live = document.querySelector('[data-sync-live]');
        var card = document.querySelector('[data-sync-card]');
        var completed = false;
        var latestStats = null;

        function fillStats(stats) {
            // Idempotent: runs on completion with the cached (one-poll-stale) figures, then
            // again from the trailing progress event with the final ones.
            if (!finale || !stats) { return; }
            ['plats', 'golds', 'silvers', 'bronzes'].forEach(function (key) {
                var slot = finale.querySelector('[data-complete-stat="' + key + '"]');
                if (slot && stats[key] != null) {
                    slot.textContent = Number(stats[key]).toLocaleString('en-US');
                }
            });
        }

        document.addEventListener('platpursuit:sync-progress', function (e) {
            var data = (e && e.detail) || {};
            if (data.stats) { latestStats = data.stats; }
            if (completed) { fillStats(latestStats); }
        });

        document.addEventListener('platpursuit:sync-status-changed', function (e) {
            if (!e.detail) { return; }
            if (e.detail.status === 'synced') {
                if (!finale || !live) {
                    // Quick refresh (no finale block rendered): the old behaviour -- straight in.
                    window.location.reload();
                    return;
                }
                completed = true;
                fillStats(latestStats);
                live.hidden = true;
                finale.hidden = false;
                if (card) { card.classList.add('is-emerged'); }
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
        if (syncedBtn) {
            syncedBtn.addEventListener('click', function () {
                // Real ordering: status-changed FIRST, trailing progress with final stats after.
                document.dispatchEvent(new CustomEvent('platpursuit:sync-status-changed', { detail: { status: 'synced' } }));
                emitProgress({ sync_percentage: 100, stats: { plats: 71, golds: 841, silvers: 2500, bronzes: 5000 } });
            });
        }
    }

    function boot() {
        wirePersonalization();
        wireFinale();
        wireDevPanel();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
