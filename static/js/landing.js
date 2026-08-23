/**
 * The anonymous landing (2026-08 rebuild).
 *
 * Three jobs, all degradable:
 *  1. The hero search: POST the PSN name to search_sync_profile, then stage the wait honestly --
 *     queue line with a live dot, poll add_sync_status, and surface the profile link the moment
 *     basic ingestion lands (`account_id` truthy). "Error" from the status endpoint covers both
 *     not-found and failed-sync, so the copy speaks to both. The poll stops on error, on the
 *     link, or after a hard cap -- a landing page must never poll forever.
 *  2. Fit the showcase Profile Card (real 1200x630 markup) into its frame by transform, scale
 *     clamped at 1, no floor (the share modal's rule).
 *  3. Count up the heartbeat numbers when their band scrolls in, via the canonical
 *     PlatPursuit.countUp (data-countup) -- not a fourth hand-rolled ticker.
 *
 * Section arrival is PlatPursuit.arriveOnScroll (html.pp-arm is armed in extra_head).
 */
(function () {
    'use strict';

    var POLL_MS = 2500;
    var POLL_CAP = 120;   // 5 minutes of polling, then rest with an honest line

    // --- 1. The hero search ---------------------------------------------------------------

    function wireSearch() {
        var form = document.querySelector('[data-land-search]');
        if (!form) { return; }
        var statusBox = document.querySelector('[data-land-status]');
        var msg = document.querySelector('[data-land-msg]');
        var visit = document.querySelector('[data-land-visit]');
        var hint = document.querySelector('[data-land-hint]');
        var input = form.querySelector('input[name="psn_username"]');
        var button = form.querySelector('button[type="submit"]');
        var timer = null, polls = 0;

        function setState(state, text) {
            statusBox.hidden = false;
            statusBox.classList.toggle('is-waiting', state === 'waiting');
            statusBox.classList.toggle('is-error', state === 'error');
            statusBox.classList.toggle('is-ready', state === 'ready');
            msg.textContent = text;
            if (hint) { hint.hidden = true; }
        }

        function stopPolling() {
            if (timer) { clearInterval(timer); timer = null; }
        }

        function poll(name) {
            polls += 1;
            if (polls > POLL_CAP) {
                stopPolling();
                setState('rest', 'Still syncing. Long histories take real time. Search the name again in a few minutes and we will take you straight there.');
                return;
            }
            fetch(form.dataset.urlStatus + '?psn_username=' + encodeURIComponent(name), {
                headers: { 'Accept': 'application/json' },
            }).then(function (r) { return r.json(); }).then(function (data) {
                if (data.sync_status === 'error') {
                    stopPolling();
                    setState('error', "We couldn't find that name on PSN. Check the spelling, and note that fully private profiles can't be tracked.");
                    return;
                }
                if (data.account_id && data.slug) {
                    stopPolling();
                    setState('ready', 'Found. Trophies are still arriving, so the numbers will keep climbing for a while.');
                    visit.href = data.slug;
                    visit.hidden = false;
                }
            }).catch(function () { /* transient; the next tick retries */ });
        }

        var PSN_RE = /^[a-zA-Z0-9_-]{3,16}$/;   // the navbar search's own gate

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var name = (input.value || '').trim();
            if (!name) { return; }
            if (!PSN_RE.test(name)) {
                setState('error', 'That does not look like a PSN name. Letters, numbers, dashes and underscores, 3 to 16 characters.');
                return;
            }
            stopPolling();
            polls = 0;
            visit.hidden = true;
            button.disabled = true;
            setState('waiting', 'Looking up ' + name + '...');

            fetch(form.action, {
                method: 'POST',
                body: new FormData(form),
                headers: { 'Accept': 'application/json' },
            }).then(function (r) {
                return r.json().then(function (data) { return { status: r.status, data: data }; });
            }).then(function (res) {
                button.disabled = false;
                if (res.status === 429) {
                    setState('error', res.data.error || 'Too many searches. Give it a minute and try again.');
                    return;
                }
                if (!res.data || !res.data.success) {
                    setState('error', (res.data && res.data.error) || 'Something went sideways. Try again in a moment.');
                    return;
                }
                setState('waiting', 'In the queue. First syncs pull an entire history, so give it a moment...');
                timer = setInterval(function () { poll(res.data.psn_username || name); }, POLL_MS);
            }).catch(function () {
                button.disabled = false;
                setState('error', 'Something went sideways. Try again in a moment.');
            });
        });
    }

    // --- 2. Fit the showcase card ---------------------------------------------------------

    function wireCardFit() {
        var frame = document.querySelector('[data-land-cardframe]');
        var scaler = document.querySelector('[data-land-cardscaler]');
        if (!frame || !scaler) { return; }
        var fit = function () {
            var scale = Math.min(1, frame.clientWidth / 1200);
            scaler.style.transform = 'scale(' + scale + ')';
        };
        fit();
        window.addEventListener('resize', fit);
    }

    // --- 3. Count-ups when the numbers scroll in -------------------------------------------

    function wireCountUps() {
        var els = Array.prototype.slice.call(document.querySelectorAll('[data-countup]'));
        if (!els.length || !window.PlatPursuit || !PlatPursuit.countUp) { return; }
        if (!('IntersectionObserver' in window)) { return; }   // numbers are already rendered
        var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) { return; }
                io.unobserve(entry.target);
                PlatPursuit.countUp(entry.target, 900);
            });
        }, { rootMargin: '0px 0px -10% 0px' });
        els.forEach(function (el) { io.observe(el); });
    }

    // --- 4. The badge inspect (the How It Works precedent, verbatim shape) -----------------

    function wireBadgePeek() {
        var modal = document.getElementById('badge-peek');
        if (!modal || !window.PlatPursuit || !PlatPursuit.Medallion || !PlatPursuit.Medallion.detailModal) { return; }
        var peek = PlatPursuit.Medallion.detailModal({
            modal: modal,
            bodySel: '[data-peek-body]',
            closeSel: '[data-peek-close]',
        });
        var tmpl = modal.dataset.peekUrl;

        function openPeek(cell) {
            var id = cell.dataset.badgeId;
            if (!id) { return; }
            peek.open(tmpl.replace(/0\/$/, id + '/'), cell);
        }

        document.querySelectorAll('.land-medals__slot[data-badge-id]').forEach(function (cell) {
            cell.addEventListener('click', function () { openPeek(cell); });
            cell.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    openPeek(cell);
                }
            });
        });
    }

    function boot() {
        wireSearch();
        wireCardFit();
        wireCountUps();
        wireBadgePeek();
        if (window.PlatPursuit && PlatPursuit.arriveOnScroll) {
            PlatPursuit.arriveOnScroll();
        } else {
            document.documentElement.classList.remove('pp-arm');
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
