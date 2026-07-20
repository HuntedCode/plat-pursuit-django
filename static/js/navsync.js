/**
 * Nav sync — the profile sync controller for the navbar's status-aware avatar + panel.
 *
 * Replaces the old hotbar's JS: reuses its proven half (adaptive polling, manual trigger,
 * cooldown countdown, and the `platpursuit:sync-status-changed` / `platpursuit:sync-progress`
 * CustomEvents that home/syncing.html depends on), but drives the house-style panel via data-*
 * hooks instead of DaisyUI badge classes + a <progress> element. The avatar RING carries the
 * live state (green synced / cyan syncing / red error) even while the panel is closed.
 */
document.addEventListener('DOMContentLoaded', function () {
    var root = document.getElementById('nav-sync');
    if (!root) return;   // anon / profile-less: no sync surface

    var syncUrl = root.dataset.urlSync;
    var trigUrl = root.dataset.urlTrigger;
    var initialStatus = root.dataset.status;
    var initialSeconds = parseInt(root.dataset.seconds, 10) || 0;

    var wrap = document.getElementById('nav-account');
    var avatar = wrap ? wrap.querySelector('[data-nav-avatar]') : null;
    var rings = wrap ? wrap.querySelectorAll('.pp-av') : [];   // the trigger + the small panel-head avatar
    var statusEl = root.querySelector('[data-nav-status]');
    var prog = root.querySelector('[data-nav-prog]');
    var fill = root.querySelector('[data-nav-fill]');
    var pct = root.querySelector('[data-nav-pct]');
    var queue = root.querySelector('[data-nav-queue]');
    var syncBtn = root.querySelector('[data-nav-syncnow]');
    var live = root.querySelector('[data-nav-live]');
    var whenEl = root.querySelector('[data-nav-when]');
    var loot = {
        plats: root.querySelector('[data-nav-loot="platinum"]'), golds: root.querySelector('[data-nav-loot="gold"]'),
        silvers: root.querySelector('[data-nav-loot="silver"]'), bronzes: root.querySelector('[data-nav-loot="bronze"]')
    };

    // Finalize sub-phase labels (keys match _job_sync_complete() in token_keeper.py).
    var PHASE = { health_check: 'Verifying', stats_badges: 'Badges', milestones: 'Milestones', challenges: 'Challenges', finishing: 'Wrapping up' };

    var lastStatus = initialStatus, pollTimer = null, pollStart = 0, activeInterval = 2000;
    var INIT = 2000, EXT = 10000, THRESH = 60000;

    function setSync(s) {
        for (var i = 0; i < rings.length; i++) { rings[i].setAttribute('data-sync', s); }
        if (avatar) { avatar.setAttribute('data-sync', s); }
        if (statusEl) { statusEl.setAttribute('data-sync', s); }
    }
    // One-shot green pulse on the ring when a sync completes (the satisfying "done" beat). Remove-reflow-add
    // retriggers it; cleared after the animation so it can fire again next sync.
    function flashRings() {
        for (var i = 0; i < rings.length; i++) {
            var r = rings[i];
            r.classList.remove('is-synced-flash'); void r.offsetWidth; r.classList.add('is-synced-flash');
        }
        setTimeout(function () { for (var j = 0; j < rings.length; j++) { rings[j].classList.remove('is-synced-flash'); } }, 900);
    }
    function txt(el, t) { if (el) { el.textContent = t; } }
    function show(el) { if (el) { el.hidden = false; } }
    function hide(el) { if (el) { el.hidden = true; } }

    // Refresh the panel's server-rendered stats (trophy loot + last-synced) from the poll response, so they
    // don't go stale after a sync finishes without a page reload.
    function applyStats(data) {
        if (data.stats) {
            if (loot.plats) { loot.plats.textContent = (data.stats.plats || 0).toLocaleString(); }
            if (loot.golds) { loot.golds.textContent = (data.stats.golds || 0).toLocaleString(); }
            if (loot.silvers) { loot.silvers.textContent = (data.stats.silvers || 0).toLocaleString(); }
            if (loot.bronzes) { loot.bronzes.textContent = (data.stats.bronzes || 0).toLocaleString(); }
        }
        if (data.last_synced && whenEl) { whenEl.textContent = 'Synced ' + data.last_synced; }
    }

    function update(data) {
        var s = data.sync_status;
        if (data.psn_outage && s !== 'synced') {
            setSync('error'); txt(statusEl, 'PSN down'); hide(prog);
            if (syncBtn) { syncBtn.textContent = 'PSN down'; syncBtn.disabled = true; }
            txt(live, 'PlayStation Network is currently unavailable'); stopPoll(); dispatch(s); return;
        }
        if (s === 'synced') {
            setSync('synced'); txt(statusEl, 'Synced'); hide(prog);
            txt(live, 'Profile sync complete'); stopPoll(); countdown(data.seconds_to_next_sync);
            if (lastStatus === 'syncing') {
                flashRings();   // the satisfying "done" pulse on the avatar ring
                if (window.PlatPursuit && PlatPursuit.ToastManager) { PlatPursuit.ToastManager.success('Profile sync complete!'); }
            }
        } else if (s === 'syncing') {
            setSync('syncing');
            var tag = data.is_finalizing ? (PHASE[data.finalize_phase] || 'Finalizing') : 'Syncing';
            txt(statusEl, tag + '…'); show(prog);
            if (fill) { fill.style.setProperty('--horizon-progress', parseInt(data.sync_percentage, 10) + '%'); }
            txt(pct, data.is_finalizing ? tag + '…' : parseInt(data.sync_percentage, 10) + '%');
            if (queue) {
                if (!data.is_finalizing && data.queue_position != null && data.queue_position > 0) { queue.textContent = '~' + data.queue_position + ' ahead'; queue.hidden = false; }
                else { queue.hidden = true; }
            }
            if (syncBtn) { syncBtn.textContent = 'Syncing…'; syncBtn.disabled = true; }
            txt(live, data.is_finalizing ? 'Finalizing sync' : ('Syncing ' + parseInt(data.sync_percentage, 10) + '% complete'));
        } else {
            setSync('error'); txt(statusEl, 'Sync error'); hide(prog);
            txt(live, 'Profile sync encountered an error'); stopPoll(); countdown(data.seconds_to_next_sync);
        }
        applyStats(data);   // keep the loot + last-synced fresh (no reload needed)
        dispatch(s);
        document.dispatchEvent(new CustomEvent('platpursuit:sync-progress', { detail: data }));
    }
    function dispatch(s) {
        if (s && s !== lastStatus) { lastStatus = s; document.dispatchEvent(new CustomEvent('platpursuit:sync-status-changed', { detail: { status: s } })); }
    }

    function setBtn(sec) {
        if (!syncBtn) return;
        if (sec <= 0) { syncBtn.textContent = 'Sync Now'; syncBtn.disabled = false; syncBtn.setAttribute('aria-label', 'Sync profile now'); }
        else { var t = PlatPursuit.TimeFormatter.countdown(sec); syncBtn.textContent = t; syncBtn.disabled = true; syncBtn.setAttribute('aria-label', 'Sync available in ' + t); }
    }
    function countdown(sec) {
        if (!syncBtn) return;
        setBtn(sec);
        var iv = setInterval(function () { sec--; setBtn(sec); if (sec <= 0) { clearInterval(iv); } }, 1000);
    }

    function poll() { if (!syncUrl) return; PlatPursuit.API.get(syncUrl).then(update).catch(function (e) { console.error('sync poll error:', e); }); }
    function startPoll() {
        pollStart = Date.now(); activeInterval = INIT;
        (function next() { pollTimer = setTimeout(function () { poll(); if (Date.now() - pollStart > THRESH) { activeInterval = EXT; } next(); }, activeInterval); })();
        poll();
    }
    function stopPoll() { if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; } }

    function trigger() {
        if (!trigUrl || !syncBtn || syncBtn.disabled) return;
        // Move focus INTO the panel before disabling the button. Otherwise disabling the just-clicked
        // button blurs it, and the focus-based dropdown loses focus-within and force-closes -- jarring,
        // since the user wants to watch the sync they just started. root (#nav-sync) is tabindex=0 and
        // inside the dropdown, so focusing it keeps the menu open.
        if (root && root.focus) { root.focus({ preventScroll: true }); }
        setSync('syncing'); txt(statusEl, 'Syncing…'); show(prog);
        syncBtn.textContent = 'Syncing…'; syncBtn.disabled = true;
        PlatPursuit.API.post(trigUrl, {})
            .then(function (data) { if (data.error) { console.error(data.error); stopPoll(); } else { startPoll(); } })
            .catch(async function (err) {
                console.error('sync trigger error:', err); stopPoll();
                var msg = 'Failed to start sync. Please try again.';
                try { var e = await err.response?.json().catch(function () { return null; }); if (e && e.error) { msg = e.error; } } catch (_) { /* ignore */ }
                if (window.PlatPursuit && PlatPursuit.ToastManager) { PlatPursuit.ToastManager.error(msg); }
                setSync('error'); txt(statusEl, 'Sync error');
            });
    }
    if (syncBtn) { syncBtn.addEventListener('click', trigger); }

    if (initialStatus === 'synced') { countdown(initialSeconds); }
    else if (initialStatus === 'syncing') { startPoll(); }

    window.addEventListener('beforeunload', stopPoll);
});
