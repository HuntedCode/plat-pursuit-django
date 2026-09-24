"""The refresh control in the profile hero.

The hero already rendered the one line on the page that changes hour to hour -- "Synced 5 days ago" --
and offered nothing to do about it. This is that line's missing half.

Every resting state is SERVER-RENDERED, which is what these tests are about: the control has to be
correct before any JS runs and correct with JS off entirely. `refresh-control.js` only adds pressing it,
ticking the countdown, and following a started sync; the structural pins at the bottom guard the parts
of that no Python test can reach.
"""
import re
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

#: The profile page sits behind `CloudflareOriginGuardMiddleware`, which 302s a direct-origin GET back
#: through platpursuit.com so the WAF can see it. Same header `test_profile_detail_queries.py` uses.
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


@pytest.fixture(autouse=True)
def _no_outage():
    """Off by default, so only the test that wants it sees one."""
    with patch('trophies.psn_manager.PSNManager.is_psn_outage_active', return_value=False) as outage:
        yield outage


def _hero(client, profile):
    """The hero's freshness line, as rendered."""
    resp = client.get(reverse('profile_detail', kwargs={'psn_username': profile.psn_username}), **CF)
    assert resp.status_code == 200
    body = resp.content.decode()
    at = body.find('data-refresh-line')
    assert at != -1, 'the hero freshness line is not on the page'
    return body[body.rindex('<p', 0, at):body.index('</p>', at)]


def _stale(psn_username='stalehunter', **kwargs):
    """Outside the cooldown. `last_synced` defaults to now on the model, so it must be said."""
    kwargs.setdefault('last_synced', timezone.now() - timedelta(days=5))
    return ProfileFactory(psn_username=psn_username, **kwargs)


def _member(client):
    member = ProfileFactory(psn_username='themember')
    client.force_login(member.user)
    return member


# ------------------------------------------------------------------------- the gate ----

def test_an_anonymous_visitor_sees_the_freshness_but_no_control(client):
    """The whole of the difference. Anonymous visitors still learn how stale a profile is -- that is
    public and useful -- they just cannot spend PSN tokens on it. Adding an UNTRACKED hunter stays open
    (it is the landing pitch); refreshing a tracked one does not."""
    profile = _stale()

    line = _hero(client, profile)

    assert 'Synced' in line, 'the freshness line itself is gone for anonymous visitors'
    assert 'data-refresh-btn' not in line
    assert 'data-refresh-url' not in line, 'the endpoint is exposed to anonymous visitors'


def test_a_signed_in_member_gets_the_action(client):
    profile = _stale()
    _member(client)

    line = _hero(client, profile)

    assert 'data-refresh-btn' in line
    assert '>Refresh</button>' in line
    assert reverse('request_profile_refresh', kwargs={'psn_username': 'stalehunter'}) in line


def test_the_endpoints_ride_the_line_not_the_button(client):
    """The button exists in only ONE of four resting states. The other three need the endpoints too: a
    profile already mid-sync has to poll, and a cooldown that ticks out has to build a button that was
    never rendered. Hanging the urls on the button left all three unable to act."""
    profile = _stale()
    _member(client)

    line = _hero(client, profile)

    opening = line[:line.index('>') + 1]
    assert 'data-refresh-url' in opening, 'the refresh endpoint is not on the line'
    assert 'data-refresh-status' in opening, 'the status endpoint is not on the line'
    assert 'data-refresh-name' in opening


# ------------------------------------------------------- the states, server-rendered ----

def test_a_hunter_inside_the_cooldown_is_told_when_not_offered_a_button(client):
    """A control that would refuse is worse than a sentence saying when. The seconds ride in a data
    attribute so JS can tick them down; the server renders the wording so it is right with JS off."""
    profile = ProfileFactory(psn_username='freshhunter')   # last_synced defaults to now
    _member(client)

    line = _hero(client, profile)

    assert 'data-refresh-btn' not in line, 'a button is offered that the cooldown would refuse'
    assert 'data-refresh-seconds=' in line
    assert 'Refreshable' in line


def test_a_hunter_mid_sync_is_not_offered_a_refresh(client):
    """It is already happening. The pulsing dot is the status, and a second control saying so would be
    the panel arguing with itself."""
    profile = _stale('midsync', sync_status='syncing')
    _member(client)

    line = _hero(client, profile)

    assert 'pp-phero__dot--live' in line, 'the live dot is missing mid-sync'
    assert 'Syncing now' in line
    assert 'data-refresh-btn' not in line


def test_a_psn_outage_replaces_the_control_with_the_reason(client, _no_outage):
    """Nothing the hunter does will help, so do not offer an action that cannot work."""
    profile = _stale()
    _member(client)
    _no_outage.return_value = True

    line = _hero(client, profile)

    assert 'PSN unavailable' in line
    assert 'data-refresh-btn' not in line


def test_there_is_no_such_thing_as_a_never_synced_profile(client):
    """`Profile.last_synced` is `DateTimeField(default=timezone.now)` with NO `null=True`, so it is
    never falsy and a brand-new row already claims to have synced this instant.

    Two pieces of code are written as though it could be null and are therefore DEAD: the hero's
    `{% else %}Never synced{% endelse %}` branch, and `SyncService.can_sync`'s
    `if not profile.last_synced: return True, None` short-circuit. Both are pre-existing and harmless
    (they would start working if the field ever gained `null=True`), but they mislead a reader into
    thinking the state is reachable -- it cost this suite a test that could not be written.

    The consequence that IS live: a freshly added hunter is inside its cooldown from the moment it
    exists, which is why the create path must never consult the cooldown and why every test wanting the
    refresh branch has to backdate.
    """
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ProfileFactory(psn_username='neversynced', last_synced=None)

    fresh = ProfileFactory(psn_username='brandnew')
    assert fresh.last_synced is not None
    _member(client)

    line = _hero(client, fresh)

    assert 'Never synced' not in line, 'the dead branch has somehow become reachable'
    assert 'data-refresh-seconds=' in line, 'a brand-new row is inside its cooldown, and must say so'


def test_a_premium_hunter_reaches_the_button_sooner(client):
    """`sync_tier` buys a 5-minute cooldown against an hour, and that is its ONLY effect on a sync path.
    It should be visible on the page, not just in the API."""
    premium = ProfileFactory(psn_username='premiumhunter', sync_tier='preferred',
                             last_synced=timezone.now() - timedelta(minutes=10))
    basic = ProfileFactory(psn_username='basichunter', sync_tier='basic',
                           last_synced=timezone.now() - timedelta(minutes=10))
    _member(client)

    assert 'data-refresh-btn' in _hero(client, premium)
    assert 'data-refresh-btn' not in _hero(client, basic)


# --------------------------------------------------------------------- accessibility ----

def test_the_countdown_is_not_itself_a_live_region(client):
    """The line holds a countdown that changes every second. Making the line a live region would have a
    screen reader read the whole sentence out every second; an sr-only sibling inside it carries the
    stage changes instead."""
    profile = ProfileFactory(psn_username='tickinghunter')
    _member(client)

    line = _hero(client, profile)

    opening = line[:line.index('>') + 1]
    assert 'aria-live' not in opening, 'the freshness line announces every tick of its own countdown'
    assert 'data-refresh-live' in line
    assert 'aria-live="polite"' in line


def test_the_control_script_is_loaded_on_the_page(client):
    """Markup with no controller is a dead button."""
    profile = _stale()
    _member(client)
    url = reverse('profile_detail', kwargs={'psn_username': profile.psn_username})

    body = client.get(url, **CF).content.decode()

    assert 'js/refresh-control' in body


# ------------------------------------------------- the controller's own structure ----
#
# Source-text pins, the idiom this project uses for JS it cannot execute (`npm test` is a placeholder).
# They guard the parts that are silently undoable and would be wrong in a way no page render shows.


def _js(relative):
    src = (Path(__file__).resolve().parents[2] / relative).read_text(encoding='utf-8')
    src = re.sub(r'/\*.*?\*/', '', src, flags=re.S)
    return re.sub(r'^\s*//.*$', '', src, flags=re.M)


def test_the_control_never_promises_a_duration():
    """The queue jump front-runs the ORCHESTRATOR job, which then fans per-game work out to a
    normal-priority queue -- so a requested refresh starts sooner and does not necessarily finish
    sooner. "Updating now" is true; any estimate would be a guess presented as a fact, and the whole
    control is worse than nothing if it lies about the wait."""
    js = _js('static/js/refresh-control.js')

    # Whole words, not substrings: a bare `'eta' not in js` passed by luck and would break on any
    # future `meta`, `beta`, `details` or `retained`.
    for promise in ['minutes remaining', 'Done in', 'estimated', 'finishes in']:
        assert promise not in js, 'the control promises a finish time it cannot know'
    assert not re.search(r'\bETA\b', js, re.I), 'the control promises a finish time it cannot know'


def test_the_control_stops_polling():
    """A page must never poll forever. The worker can be down, in which case a status never moves."""
    js = _js('static/js/refresh-control.js')

    assert 'POLL_CAP' in js
    assert 'polls > POLL_CAP' in js, 'the cap is declared but never enforced'
    assert 'stopPolling()' in js.split('polls > POLL_CAP', 1)[1][:200], \
        'the cap is checked but nothing stops the timer'


def test_a_cooldown_refusal_becomes_a_countdown_not_an_error():
    """The third client to learn this. A cooldown means the profile is CURRENT, which is the thing the
    hunter wanted; showing it as a failure would be telling them off for good news."""
    js = _js('static/js/refresh-control.js')

    assert "reason === 'cooldown'" in js
    cooldown = js.split("reason === 'cooldown'", 1)[1].split('return;', 1)[0]
    assert 'runCountdown(' in cooldown, 'a cooldown does not start the countdown to the next window'
    assert 'ToastManager.error' not in cooldown, 'good news is being toasted as an error'


def test_the_countdown_reads_as_a_sentence_not_a_clock():
    """`PlatPursuit.TimeFormatter.countdown` returns HH:MM:SS, which put "Refreshable in 00:47:13" in the
    middle of a sentence. The control says "48 minutes" -- the same shape the server's own
    `_cooldown_phrase` uses, so the no-JS wording and the ticking wording are one voice."""
    js = _js('static/js/refresh-control.js')

    assert 'TimeFormatter' not in js, 'the clock formatter is back in a sentence'
    assert 'function phrase(seconds)' in js
    assert "' minutes'" in js and "' seconds'" in js


def test_every_cross_file_call_is_guarded():
    """A browser can hold a cached pre-change `utils.js` against a fresh copy of this file. Two calls
    here were unguarded while every other touch in the same file was guarded -- and one of them sits
    inside a `setInterval`, so it throws on every tick for the life of the poll."""
    js = _js('static/js/refresh-control.js')

    assert 'function api()' in js
    assert 'PlatPursuit.API.' not in js, 'an unguarded cross-file call is back'


def test_the_poll_gives_up_on_a_dead_endpoint():
    """Its catch logged and kept ticking, so a dead status endpoint produced 90 silent throws over six
    minutes and then announced "Still updating" -- a claim about a sync whose state we had never
    successfully read."""
    js = _js('static/js/refresh-control.js')

    assert 'MAX_POLL_FAILURES' in js
    assert 'pollFailures >= MAX_POLL_FAILURES' in js, 'the failure cap is declared but never enforced'
    assert 'pollFailures = 0' in js, 'the counter never resets, so scattered failures accumulate'


def test_the_control_does_not_cost_the_back_forward_cache():
    """A `beforeunload` listener makes the page ineligible for the bfcache in Firefox and Safari, and the
    cleanup it did was a no-op: timers die with the document. A profile page is one people navigate back
    to, so that was a real cost for nothing."""
    js = _js('static/js/refresh-control.js')

    assert 'beforeunload' not in js


def _done_branch(js):
    """The poll's finished-successfully branch.

    SCOPED because an unscoped `'Reload' in js` also matched the poll-cap branch's "Reload in a few
    minutes", so it stayed green while the done state had been mutated to say "Done" -- found by mutation
    testing, not by reading it.
    """
    return js.split("stopPolling();", 1)[1].split("sync_status === 'error'", 1)[1].split('return;', 1)[1]


def test_the_done_state_admits_the_page_is_now_stale():
    """When the sync lands, every figure ALREADY rendered on the page is the old one. Saying "Updated"
    and leaving them there would make the control look broken; naming the reload is the honest move."""
    js = _js('static/js/refresh-control.js')
    done = _done_branch(js)

    assert 'Updated just now' in done, 'the done branch is not where it was'

    # The VISIBLE control, not merely "somewhere in the branch": the `announce()` beside it also says
    # "Reload", so a looser assertion stayed green while the on-screen half had been changed to "Done".
    # Both channels have to say it -- the sighted hunter reads the line, not the live region.
    offered = done.split('offerButton(', 1)[1].split(')', 1)[0]
    assert 'Reload' in offered, 'the visible control no longer says the figures on screen are stale'


def test_the_reload_prompt_is_pressable():
    """It told a hunter to reload and then did not help them do it.

    A real `<button>`, not a span with a click handler: a span is a control a keyboard and a screen
    reader cannot reach, and the inert `--off` note is what every state with nothing to press uses.
    """
    js = _js('static/js/refresh-control.js')
    done = _done_branch(js)

    assert 'offerButton(' in done, 'the done state is an inert note again'
    assert 'noteOnly(' not in done.split('announce(', 1)[0], 'the done state renders an unpressable note'
    assert 'reloadPage' in done
    assert 'function reloadPage()' in js
    assert 'window.location.reload()' in js


def test_a_relabelled_control_never_keeps_its_old_handler():
    """The subtle one. `offerButton` used to re-use an existing button and only change its text, which
    would leave a "Reload to see it" button still POSTing a refresh request. It builds a fresh element
    every time for that reason."""
    js = _js('static/js/refresh-control.js')
    body = js.split('function offerButton(label, handler) {', 1)[1].split('\n    }', 1)[0]

    assert 'createElement' in body
    assert 'btn.textContent =' not in body, 'a control is being relabelled instead of rebuilt'

    # BOTH PARAMETERS have to be read, and `textContent = text` alone does not prove it -- `text` could
    # be hardcoded and that assertion would still pass. Mutation found exactly that: hardcoding the
    # label, and separately hardcoding the handler, were both invisible. The handler one is the
    # dangerous half: "Reload to see it" would POST a refresh request instead of reloading.
    assert "label || 'Refresh'" in body, 'the label argument is ignored'
    assert 'handler || request' in body, 'the handler argument is ignored; every button would refresh'
    assert 'addEventListener(\'click\', onClick)' in body, 'the chosen handler is never wired up'


def test_both_reload_prompts_are_pressable():
    """Two states say "Reload" and mean it right now: the sync landing, and the status endpoint going
    dark. Both offer a button. (The third, at the poll cap, deliberately does not -- see below.)"""
    js = _js('static/js/refresh-control.js')

    for label in ['Reload to see it', 'Reload to check']:
        call = "offerButton('" + label + "', reloadPage)"
        assert call in js, label + ' is not pressable'


def test_the_still_updating_state_stays_an_inert_note():
    """The one "Reload" state that deliberately does NOT get a button: at the poll cap the sync is
    probably still running, so offering an immediate reload would contradict its own label and hand back
    the same stale figures. The other two are offering something that works right now."""
    js = _js('static/js/refresh-control.js')

    cap = js.split('polls > POLL_CAP', 1)[1].split('return;', 1)[0]
    assert 'Reload in a few minutes' in cap
    assert 'offerButton' not in cap, 'the cap state offers a reload that would show stale figures'
