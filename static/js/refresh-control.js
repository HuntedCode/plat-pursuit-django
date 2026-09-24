/**
 * The profile hero's refresh control.
 *
 * Asks for the hunter you are looking at to be re-synced from PSN, and narrates the wait. It lives
 * inline in the freshness line ("Synced 5 days ago · Refresh") because that line is what creates the
 * want: before this, a hunter could read how stale a profile was and had nowhere to go with it.
 *
 * WHY IT IS NOT JUST IMPATIENCE. `refresh_profiles` only picks up an unlinked, non-Discord-verified
 * profile once it is SEVEN DAYS stale, and that is most of the hunters anyone browses. For those,
 * asking is the only thing that makes them current inside a week.
 *
 * EVERY RESTING STATE IS SERVER-RENDERED. The button, the countdown and the two "nothing to press"
 * notes all arrive in the markup, so the control is correct before this file runs and correct with JS
 * off entirely. All this adds is: pressing it, ticking the countdown down rather than leaving it at
 * "soon", and polling a started sync to completion.
 *
 * WHAT IT REFUSES TO PROMISE: a duration. The queue jump front-runs the orchestrator job, which then
 * fans per-game work out to a normal-priority queue, so a requested refresh starts sooner and does not
 * necessarily finish sooner. "Updating now" is the truth; "done in 30s" would not be.
 */
document.addEventListener('DOMContentLoaded', function () {
    var line = document.querySelector('[data-refresh-line]');
    if (!line) return;   // anonymous, or a hero variant without the line

    var textEl = line.querySelector('[data-refresh-text]');
    var dot = line.querySelector('[data-refresh-dot]');
    var liveRegion = line.querySelector('[data-refresh-live]');
    var btn = line.querySelector('[data-refresh-btn]');
    var note = line.querySelector('[data-refresh-note]');

    var POLL_MS = 4000;
    var POLL_CAP = 90;   // 6 minutes. A page must never poll forever.

    // The endpoints live on the LINE, not on the button: the button exists in only one of four
    // resting states, and the other three need them too -- a profile already mid-sync has to poll, and
    // a cooldown that ticks out has to rebuild a button that was never rendered.
    var REFRESH_URL = line.dataset.refreshUrl || '';
    var STATUS_URL = line.dataset.refreshStatus || '';
    var NAME = line.dataset.refreshName || '';

    var pollTimer = null;
    var countdownTimer = null;
    var polls = 0;
    var pollFailures = 0;
    var MAX_POLL_FAILURES = 3;   // a dead endpoint should be said out loud, not retried 90 times

    // Guarded like every other cross-file touch in this project: a browser can hold a cached
    // pre-change `utils.js` against a fresh copy of this file, and an unguarded call inside a
    // setInterval throws on every tick.
    function api() {
        return (window.PlatPursuit && PlatPursuit.API) || null;
    }

    // ---- the line ----------------------------------------------------------------------------

    function say(text) {
        if (textEl) { textEl.textContent = text; }
    }

    // Announced out of band, because the line holds a countdown that changes every second and a live
    // region would read all of it out, every second.
    function announce(text) {
        if (liveRegion && liveRegion.textContent !== text) { liveRegion.textContent = text; }
    }

    function setLive(on) {
        if (dot) { dot.classList.toggle('pp-phero__dot--live', !!on); }
    }

    /** Add `el` at the end of the line, but before the announcer, which stays last. */
    function appendToLine(el) {
        if (liveRegion) { line.insertBefore(el, liveRegion); }
        else { line.appendChild(el); }
    }

    /**
     * Replace whichever control is present with an inert note.
     *
     * A `<span>`, not a disabled button, and that is the whole point of the `--off` variant: these are
     * states where there is nothing to press ("Queued", "PSN unavailable", "Refreshable in 12 minutes"),
     * so they are text in a sentence rather than a control nobody can use. Anything a hunter can ACT on
     * goes through `offerButton` instead.
     */
    function noteOnly(text) {
        if (btn) { btn.remove(); btn = null; }
        if (!note) {
            note = document.createElement('span');
            note.className = 'pp-phero__refresh pp-phero__refresh--off';
            note.setAttribute('data-refresh-note', '');
            appendToLine(note);
        }
        note.textContent = text;
    }

    // ---- the cooldown countdown --------------------------------------------------------------

    function stopCountdown() {
        if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
    }

    /**
     * "48 minutes" / "35 seconds" -- the same shape the server's own `_cooldown_phrase` uses, so the
     * no-JS wording and the ticking wording are one voice.
     *
     * Deliberately NOT `PlatPursuit.TimeFormatter.countdown`, which returns HH:MM:SS: that put
     * "Refreshable in 00:47:13" in the middle of a sentence. (An earlier comment here claimed the nav
     * sync panel used that helper for its own cooldown. It does not -- it has a local one.)
     */
    function phrase(seconds) {
        if (seconds >= 120) { return Math.ceil(seconds / 60) + ' minutes'; }
        if (seconds > 60) { return '2 minutes'; }
        if (seconds === 60) { return '1 minute'; }
        return seconds === 1 ? '1 second' : seconds + ' seconds';
    }

    /**
     * Tick a cooldown down to zero, then hand the button back.
     *
     * The server renders "Refreshable soon" with the seconds in a data attribute rather than a
     * formatted time, so this owns the wording and the ticking together.
     */
    function runCountdown(seconds) {
        stopCountdown();
        var left = parseInt(seconds, 10) || 0;
        if (left <= 0) { offerButton(); return; }

        noteOnly('Refreshable in ' + phrase(left));
        countdownTimer = setInterval(function () {
            left -= 1;
            if (left <= 0) { stopCountdown(); offerButton(); return; }
            noteOnly('Refreshable in ' + phrase(left));
        }, 1000);
    }

    /**
     * Put a pressable control in the line, replacing whatever is there.
     *
     * Two states use it: the Refresh action, and "Reload to see it" once a sync lands. Always a REAL
     * `<button>` -- a span with a click handler is a control a keyboard and a screen reader cannot
     * reach, and these are the only two things on this line a hunter can actually do.
     *
     * Always a FRESH element, never a relabelled one: reusing the node would carry its old click
     * handler, so a Refresh button relabelled "Reload to see it" would still ask for a sync.
     */
    function offerButton(label, handler) {
        var text = label || 'Refresh';
        var onClick = handler || request;

        // Only the Refresh action needs somewhere to POST to. Reload needs nothing, so the guard is
        // scoped to the default handler rather than applied to every caller.
        if (onClick === request && !REFRESH_URL) {
            if (note) { note.textContent = 'Refreshable now'; }
            return;
        }

        var fresh = document.createElement('button');
        fresh.type = 'button';
        fresh.className = 'pp-phero__refresh';
        fresh.setAttribute('data-refresh-btn', '');
        fresh.textContent = text;
        fresh.addEventListener('click', onClick);

        if (btn) { line.replaceChild(fresh, btn); }
        else if (note) { line.replaceChild(fresh, note); note = null; }
        else { appendToLine(fresh); }
        btn = fresh;
    }

    /**
     * A plain reload.
     *
     * Not a partial re-render: a finished sync rewrites the level, the ring, the trophy tiers and every
     * tab's wall, so there is no subset of the page to swap. `location.reload()` keeps the query string,
     * so whichever tab the hunter was on survives.
     */
    function reloadPage() {
        window.location.reload();
    }

    // ---- polling a started sync --------------------------------------------------------------

    function stopPolling() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    }

    function poll() {
        polls += 1;
        if (polls > POLL_CAP) {
            stopPolling();
            // Honest rest rather than a spinner forever: a long history genuinely takes this long, and
            // the page will be correct on its next load.
            //
            // The one "Reload" state that stays an inert NOTE, deliberately: the sync is probably still
            // running, so a button offering an immediate reload would contradict its own label and hand
            // back the same stale figures. The other two ("Reload to see it", "Reload to check") are
            // offering something that works right now.
            setLive(false);
            say('Still updating');
            noteOnly('Reload in a few minutes');
            announce('Still updating ' + NAME + '. Reload in a few minutes.');
            return;
        }
        var client = api();
        if (!client) { stopPolling(); return; }

        client.get(STATUS_URL + '?psn_username=' + encodeURIComponent(NAME))
            .then(function (data) {
                pollFailures = 0;
                if (data.sync_status === 'syncing') { return; }   // still going; keep ticking
                stopPolling();
                setLive(false);
                if (data.sync_status === 'error') {
                    say('Sync failed');
                    noteOnly('Try again shortly');
                    announce('Could not update ' + NAME + '.');
                    return;
                }
                // Done. The figures on the page are now the OLD ones, so say so rather than quietly
                // implying the page reflects the new sync -- and make the thing we are asking for one
                // click away, since a note telling a hunter to reload without helping them is just a
                // chore written on the page.
                say('Updated just now');
                offerButton('Reload to see it', reloadPage);
                announce(NAME + ' is updated. Reload the page to see the new figures.');
            })
            .catch(function (err) {
                console.error('refresh poll error:', err);
                // A single failure is transient and the next tick retries. A run of them is not, and
                // grinding on for six minutes to then announce "Still updating" would be a claim about
                // a sync we have no idea the state of.
                pollFailures += 1;
                if (pollFailures >= MAX_POLL_FAILURES) {
                    stopPolling();
                    setLive(false);
                    say('Status unavailable');
                    offerButton('Reload to check', reloadPage);
                    announce('Could not read the status for ' + NAME + '. Reload to check.');
                }
            });
    }

    function startPolling() {
        if (!STATUS_URL || !NAME) return;
        stopPolling();
        polls = 0;
        pollFailures = 0;
        pollTimer = setInterval(poll, POLL_MS);
    }

    // ---- the ask -----------------------------------------------------------------------------

    function request() {
        if (!btn || btn.disabled || !REFRESH_URL) return;
        var client = api();
        if (!client) return;   // a cached pre-change utils.js: leave the control alone rather than throw

        btn.disabled = true;
        btn.textContent = 'Asking…';

        client.post(REFRESH_URL, {})
            .then(function (data) {
                setLive(true);
                say('Updating now');
                noteOnly(data && data.reason === 'already_syncing' ? 'Already in progress' : 'Queued');
                announce('Updating ' + NAME + ' now.');
                startPolling();
            })
            .catch(async function (err) {
                console.error('refresh request error:', err);
                var body = client.failureBody ? await client.failureBody(err) : null;
                var reason = body && body.reason;

                // A COOLDOWN is not a failure: the profile is current, which is the thing the hunter
                // wanted. Show the countdown to the next window instead of an error.
                if (reason === 'cooldown') {
                    runCountdown(body.seconds_to_next_sync);
                    announce('Already up to date. ' + (body.error || ''));
                    return;
                }
                if (reason === 'outage') {
                    noteOnly('PSN unavailable');
                    announce(body.error || 'PlayStation Network is unavailable.');
                    return;
                }
                // The session expired between page load and this click. It used to be unreachable,
                // because the endpoint redirected and the redirect read as a success -- which is the
                // bug that made this branch necessary.
                if (reason === 'sign_in') {
                    noteOnly('Sign in to refresh');
                    announce(body.error || 'Sign in to refresh this hunter.');
                    return;
                }
                // Rate limited, or anything else. The server's sentence when it wrote one.
                var msg = (body && body.error) || 'Could not ask for an update. Try again in a moment.';
                noteOnly('Try again shortly');
                announce(msg);
                if (window.PlatPursuit && PlatPursuit.ToastManager) { PlatPursuit.ToastManager.error(msg); }
            });
    }

    // ---- wiring ------------------------------------------------------------------------------

    if (btn) { btn.addEventListener('click', request); }

    // A server-rendered cooldown starts ticking immediately, so "Refreshable soon" becomes a real
    // number and turns back into a button on its own.
    if (note && note.dataset.refreshSeconds) { runCountdown(note.dataset.refreshSeconds); }

    // A profile already mid-sync when the page loaded is worth following: it is the one case where the
    // page can go from stale to fresh without the hunter doing anything.
    if (dot && dot.classList.contains('pp-phero__dot--live')) { startPolling(); }

    //: No `beforeunload` cleanup. Timers die with the document, so it bought nothing -- and merely
    //: registering that listener makes the page ineligible for the back/forward cache in Firefox and
    //: Safari, which is a real cost for a profile page people navigate back to.
});
