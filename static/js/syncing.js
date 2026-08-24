/**
 * The first-sync waiting room (templates/home/syncing.html).
 *
 * Three jobs, all riding navsync.js's poll events (this file NEVER polls; navsync owns the
 * network and dispatches `platpursuit:sync-progress` on every poll and
 * `platpursuit:sync-status-changed` once per real transition):
 *
 *   1. The "What's Waiting for You" walkthrough carousel. The pattern is copied from
 *      landing.js's ratings carousel (wireRatingsCarousel) rather than extracted into utils:
 *      two consumers with different DOM contracts don't earn a shared primitive yet -- a
 *      third one does.
 *   2. Live personalization: the page may render before PSN's own totals land on the profile;
 *      when the poll payload carries `psn_found`, fill the numbers and swap the sentence.
 *   3. The enter-moment state machine: on a FIRST sync (the finale block only renders then),
 *      a `synced` transition swaps the status card into "Your Pursuer has emerged" with the
 *      final counts and an "Enter your Pursuit" CTA -- no auto-reload. Quick refreshes and
 *      errors keep the old reload behaviour.
 *
 * Ordering subtlety (load-bearing): navsync dispatches status-changed BEFORE the same poll's
 * sync-progress, so at finale time the cached stats are one poll stale. The stat fill is
 * idempotent and re-runs from the progress listener after completion; the trailing event
 * corrects the figures within the same tick.
 */
(function () {
    'use strict';

    // ── 1. Walkthrough carousel ─────────────────────────────────────────────
    function wireWalkthrough() {
        var host = document.querySelector('[data-sync-walkthrough]');
        if (!host) { return; }
        var slides = Array.prototype.slice.call(host.querySelectorAll('.sw__slide'));
        var dots = Array.prototype.slice.call(host.querySelectorAll('[data-sw-dot]'));
        if (slides.length < 2) { return; }
        var index = 0, timer = null;
        var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        function show(next) {
            index = (next + slides.length) % slides.length;
            slides.forEach(function (slide, i) {
                slide.classList.toggle('is-active', i === index);
                slide.setAttribute('aria-hidden', i === index ? 'false' : 'true');
            });
            dots.forEach(function (dot, i) {
                dot.classList.toggle('is-active', i === index);
                if (i === index) { dot.setAttribute('aria-current', 'true'); }
                else { dot.removeAttribute('aria-current'); }
            });
        }

        // Advance only while actually on screen; the reader should always arrive on slide one.
        var inView = false;

        function start() {
            // No auto-advance under reduced motion: the dots remain the manual path.
            if (reduce || timer || !inView) { return; }
            timer = setInterval(function () { show(index + 1); }, 7000);
        }
        function stop() {
            if (timer) { clearInterval(timer); timer = null; }
        }

        dots.forEach(function (dot) {
            dot.addEventListener('click', function () {
                show(parseInt(dot.dataset.swDot, 10) || 0);
                stop();   // a chosen slide stays chosen; auto-advance resumes on mouse-out
            });
        });
        host.addEventListener('mouseenter', stop);
        host.addEventListener('mouseleave', start);
        host.addEventListener('focusin', stop);
        host.addEventListener('focusout', start);

        // Swipe: touch/pen only (a mouse drag is text selection), horizontal only
        // (touch-action: pan-y on the host leaves vertical scrolling to the browser),
        // pointerId-tracked so a wandering drag can never page against a stale origin.
        var swipeX = null, swipeY = null, swipeId = null;
        host.addEventListener('pointerdown', function (e) {
            if (e.pointerType === 'mouse') { return; }
            swipeId = e.pointerId; swipeX = e.clientX; swipeY = e.clientY;
        });
        host.addEventListener('pointerup', function (e) {
            if (swipeId === null || e.pointerId !== swipeId) { return; }
            var dx = e.clientX - swipeX, dy = e.clientY - swipeY;
            swipeId = swipeX = swipeY = null;
            if (Math.abs(dx) < 44 || Math.abs(dx) < Math.abs(dy) * 1.5) { return; }
            stop();
            show(index + (dx < 0 ? 1 : -1));
        });
        host.addEventListener('pointercancel', function () { swipeId = swipeX = swipeY = null; });

        if ('IntersectionObserver' in window) {
            var io = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    inView = entry.isIntersecting;
                    if (inView) { start(); } else { stop(); }
                });
            }, { threshold: 0.35 });
            io.observe(host);
        } else {
            inView = true;
            start();
        }
    }

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
        wireWalkthrough();
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
