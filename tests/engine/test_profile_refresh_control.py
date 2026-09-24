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

from tests.engine.test_navbar_add_sync import _tag_around
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


def _page(client, profile):
    """The whole rendered profile page.

    `_hero()` is deliberately narrower -- it returns only the freshness line -- so anything about the
    FIGURES has to look wider. The hooks asserted through this helper (`data-live-tier`,
    `data-live-total`, `data-sync-provisional`) appear nowhere else on the page, and the one that is not
    distinctive (`data-countup`) is read out of its own tag with `_tag_around`.
    """
    resp = client.get(reverse('profile_detail', kwargs={'psn_username': profile.psn_username}), **CF)
    assert resp.status_code == 200
    return resp.content.decode()


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


# ------------------------------------------- the figures that move while a sync runs ----
#
# Only FOUR do: the per-type trophy denorms, which climb through the walk via an EarnedTrophy post_save
# signal. `total_trophies`, Games, Completed and Avg. completion all wait for finalize -- which is the
# whole problem, because the tiers climbing past a frozen total beside them is a visible contradiction,
# not a subtle one.


def test_the_tier_figures_are_hooked_for_live_updates(client):
    """The four that move carry `data-live-tier`; nothing else does, because nothing else can."""
    profile = _stale('livehunter', sync_status='syncing')
    _member(client)

    body = _page(client, profile)

    for key in ['plats', 'golds', 'silvers', 'bronzes']:
        assert 'data-live-tier="%s"' % key in body, key + ' is not hooked'


def test_the_hook_names_match_the_payload_keys(client):
    """A CROSS-FILE CONTRACT that fails silently. The controller reads `stats[el.dataset.liveTier]`, so a
    hook named `platinums` against a payload key of `plats` updates nothing at all -- no error, no
    console warning, just four figures that never move. Pin both ends against each other."""
    subject = ProfileFactory(psn_username='payloadhunter', sync_status='syncing',
                             total_plats=1, total_golds=2, total_silvers=3, total_bronzes=4)
    _member(client)

    payload = client.get(reverse('add_sync_status'), {'psn_username': 'payloadhunter'}).json()
    body = _page(client, subject)

    assert set(payload['stats']) == {'plats', 'golds', 'silvers', 'bronzes'}
    for key in payload['stats']:
        assert 'data-live-tier="%s"' % key in body, \
            'the payload sends %r and the markup never asks for it' % key


def test_mid_sync_the_headline_total_is_derived_from_the_tiers(client):
    """THE inconsistency this exists to prevent. `total_trophies` waits for finalize, so mid-sync the
    hero's Trophies card showed a pre-sync figure while the four tiers climbed past it -- eventually
    out-summing the total printed in the same eyeline.

    Derived server-side too, not just by the poll, so the FIRST paint is already consistent rather than
    correcting itself a few seconds later.
    """
    profile = ProfileFactory(psn_username='derivehunter', sync_status='syncing',
                             total_trophies=100,
                             total_plats=1, total_golds=2, total_silvers=3, total_bronzes=4)
    _member(client)

    # SCOPED to the headline figure's own card: `data-countup` is on every figure in the hero, so an
    # unscoped assertion would happily match a different number that happened to agree.
    body = _page(client, profile)
    card = _trophies_card(body)

    assert 'data-countup="10"' in card, 'the total is not the sum of the tiers (1+2+3+4)'
    assert '100' not in card, 'the pre-sync total is still being printed'
    assert 'data-live-total' in body, 'the poll is not told to keep the derived figure in step'


def test_a_hunter_who_hides_games_keeps_the_static_total(client):
    """THE trap in deriving this figure, and the reason it is gated rather than always on.

    `total_trophies` is FILTER-RESPECTING (`hide_hiddens` + `hide_zeros`); the four tier counters are
    NOT -- the model calls them out as unfiltered and their sum is by definition `total_trophies_raw`.
    So for a hunter who hides games the derived figure is HIGHER than the one finalize will write, and it
    would climb all through the sync and then DROP on reload. A number going backwards reads as data
    loss, which is a worse failure than a figure that honestly does not move.

    The absent `data-live-total` hook is also what tells the controller to leave the figure alone, so
    this pins both halves at once.
    """
    profile = ProfileFactory(psn_username='hidinghunter', sync_status='syncing', hide_hiddens=True,
                             total_trophies=100,
                             total_plats=1, total_golds=2, total_silvers=3, total_bronzes=4)
    _member(client)

    body = _page(client, profile)

    assert 'data-live-total' not in body, \
        'the controller is told to derive a figure that will drop when the sync finishes'
    # SCOPED, and positive: asserting only an absence passes if the feature is deleted outright, and an
    # unscoped `data-countup="100"` would match any figure on the page that happened to be 100.
    assert 'data-countup="100"' in _trophies_card(body), \
        'the filtered total was replaced by an unfiltered sum'
    # And prove the profile really is mid-sync, or the absence above proves nothing at all.
    assert 'still arriving' in body, 'this profile is not syncing, so the gate was never exercised'


def test_hiding_zero_trophy_games_also_keeps_it_static(client):
    """Gated on BOTH filters rather than reasoning about which of them can actually shift an
    earned-trophy count. `update_profile_trophy_counts` honours both, and one condition is cheaper than
    an argument that has to stay true."""
    profile = ProfileFactory(psn_username='zerohunter', sync_status='syncing', hide_zeros=True,
                             total_trophies=100,
                             total_plats=1, total_golds=2, total_silvers=3, total_bronzes=4)
    _member(client)

    body = _page(client, profile)

    assert 'data-live-total' not in body
    assert 'data-countup="100"' in _trophies_card(body), 'the filtered total was replaced'
    assert 'still arriving' in body, 'this profile is not syncing, so the gate was never exercised'


def _trophies_card(body):
    """The hero's headline Trophies figure, scoped by its own label.

    Not via `data-live-total`: that hook is deliberately ABSENT on a settled profile and on any hunter
    with a display filter, so anchoring on it makes the tests that care about its absence unwritable.
    """
    at = body.index('>Trophies</div>')
    return body[at:body.index('</div>', body.index('scard__value', at))]


def test_a_failed_sync_shows_the_more_current_figure(client):
    """A failed sync leaves the tier denorms ALREADY ADVANCED by the partial walk while `total_trophies`
    was never rewritten -- `update_profile_trophy_counts` only runs on the finalize path. So an errored
    profile would otherwise print tiers that out-sum its own headline, permanently, until some later
    sync succeeds. The derived sum is the more current figure there, not the less."""
    # Tiers summing ABOVE the stale headline, which is the actual shape of the problem: a partial walk
    # advanced them past a `total_trophies` that was never rewritten. The first version of this fixture
    # had them 90 BELOW it, so it pinned the right branch while demonstrating the opposite of its own
    # argument.
    profile = _stale('erroredhunter', sync_status='error', total_trophies=8,
                     total_plats=1, total_golds=2, total_silvers=3, total_bronzes=4)
    _member(client)

    card = _trophies_card(_page(client, profile))

    assert 'data-countup="10"' in card, \
        'an errored profile still prints the pre-sync total its tiers have already passed'
    assert '"8"' not in card, 'the stale pre-sync figure is still being printed'


def test_a_settled_profile_shows_its_real_total_but_keeps_the_hook(client):
    """Two questions, and an earlier version answered both with one condition.

    The VALUE is the real `total_trophies` on a settled profile: it is the FILTER-RESPECTING figure, which
    is the whole reason a derived sum cannot stand in for it.

    The HOOK is present anyway, and that is the half that matters. A hunter loads a settled page and
    presses Refresh -- if the hook were gated on the rendered value there would be nothing to update,
    because the controller captures that element ONCE at DOMContentLoaded, so the headline would sit
    frozen at its pre-sync figure for the life of the page. Which is precisely the contradiction this
    feature exists to remove, on the only path anybody actually takes.
    """
    profile = _stale('settledhunter', total_trophies=100,
                     total_plats=1, total_golds=2, total_silvers=3, total_bronzes=4)
    _member(client)

    body = _page(client, profile)

    assert 'data-countup="100"' in _trophies_card(body), 'a settled profile is showing a derived total'
    assert 'data-live-total' in body, \
        'no hook on a settled profile, so a sync started from this page can never update the figure'


def test_the_status_poll_carries_the_live_tally(client):
    """The open endpoint the control polls. Zero extra queries -- the profile is already loaded for the
    lookup -- and nothing here is not already printed on the page."""
    ProfileFactory(psn_username='tallyhunter', sync_status='syncing',
                   total_plats=7, total_golds=70, total_silvers=700, total_bronzes=7000)

    stats = client.get(reverse('add_sync_status'), {'psn_username': 'tallyhunter'}).json()['stats']

    assert stats == {'plats': 7, 'golds': 70, 'silvers': 700, 'bronzes': 7000}


# ---------------------------------------------- saying so, for the figures that cannot ----

def test_a_syncing_profile_says_its_figures_are_still_arriving(client):
    """Games, Completed and Avg. completion cannot move during a sync, and a viewer has no way to know
    that from the numbers. One line for all of them rather than a marker on each."""
    profile = _stale('arrivinghunter', sync_status='syncing')
    _member(client)

    body = _page(client, profile)

    assert 'data-sync-provisional' in body
    assert 'still arriving' in body
    # Present and VISIBLE: rendered hidden would be the same as not saying it at all.
    assert 'hidden' not in _tag_around(body, 'These figures are still arriving')


def test_a_settled_profile_says_nothing_of_the_kind(client):
    """It is rendered hidden rather than omitted, because the controller reveals it the moment a hunter
    presses Refresh -- but it must not be visible on a profile that is not syncing."""
    profile = _stale('quiethunter')
    _member(client)

    body = _page(client, profile)

    assert 'data-sync-provisional' in body, 'the line is omitted, so JS cannot reveal it on demand'
    assert 'hidden' in _tag_around(body, 'These figures are still arriving')


def test_the_tab_walls_say_so_too_and_survive_a_tab_change(client):
    """The walls are the stalest thing on the page -- a mid-first-sync Games tab can be EMPTY and read as
    "this hunter owns nothing".

    The line has to sit OUTSIDE `#tab-content`, which htmx swaps wholesale on every tab click: inside it
    the line would vanish the first time somebody changed tab, and htmx settle would strip a class JS had
    added to it besides.
    """
    profile = _stale('wallhunter', sync_status='syncing')
    _member(client)
    body = _page(client, profile)

    assert 'still filling' in body, 'the tab walls say nothing while they fill'
    assert body.index('still filling') < body.index('id="tab-content"'), \
        'the notice is inside the htmx-swapped container and will vanish on the first tab change'
    # The index comparison only proves it is outside `#tab-content`. It has to be outside whatever htmx
    # ACTUALLY targets -- point `hx-target` at a wrapping element and the line gets swapped away with the
    # assertion above still green.
    assert 'hx-target="#tab-content"' in body, 'the tab swap targets something else now'


# ------------------------------------------------- the controller's half of the contract ----

def test_the_control_derives_the_total_and_never_trusts_the_stale_one():
    """It must SUM the tiers rather than read a `total` off the payload -- the endpoint does not send one,
    and `total_trophies` is precisely the figure that is wrong mid-sync."""
    js = _js('static/js/refresh-control.js')

    body = js.split('function applyTally(stats) {', 1)[1].split('\n    }', 1)[0]
    assert 'total += raw' in body, 'the headline figure is not derived from the tiers'
    assert 'stats.total' not in body, 'it is reading a total the endpoint does not send'
    # Writing is `tick`'s job now, so this function must not set text itself -- two writers would race
    # a half-finished animation against a hard swap.
    assert 'textContent' not in body, 'a figure is written directly, bypassing the tick'
    assert body.count('tick(') == 2, 'the tiers and the total do not both go through the tick'

    # A SHORT sum is the same contradiction inverted -- a headline less than the four figures beside it --
    # and one renamed payload key is all it takes. The total only lands when every tier resolved.
    assert 'resolved === tierEls.length' in body, 'a missing payload key writes a short total'
    assert "typeof raw !== 'number'" in body, \
        'a null-ish payload value zeroes a tier instead of being skipped'


def test_the_figures_tick_to_their_new_values():
    """They were a hard `textContent` swap, so a count went from 1,400 to 1,437 instantly every four
    seconds. The first-sync hero already ticks its tally through the shared `countUp` primitive; this is
    the same event on another surface, so it uses the same primitive at the same 600ms.

    Three substrings rather than the exact call: the earlier version pinned
    `'PlatPursuit.countUp(el, 600, { from: prev })'` verbatim, which any reformat would have broken while
    the behaviour stayed correct. The parity with `syncing.js`'s duration is the part Python cannot reach
    any other way, so it is what gets named.
    """
    js = _js('static/js/refresh-control.js')

    body = js.split('function tick(el, value) {', 1)[1].split('\n    }', 1)[0]
    assert 'PlatPursuit.countUp(' in body, 'the figures jump instead of ticking'
    assert '600' in body, "the duration drifted from syncing.js's tally"
    assert 'from: prev' in body, 'it animates from zero instead of from the previous value'
    # And a fallback, because a cached pre-change utils.js would otherwise throw mid-poll. `'en-US'` to
    # match the sibling and the server's `intcomma`; a bare `toLocaleString()` renders 1.437 on de-DE.
    assert "value.toLocaleString('en-US')" in body, 'no fallback, or it formats to the wrong locale'


def test_the_tick_sets_its_target_before_animating():
    """`countUp` reads its TARGET from `data-countup`. Set after the call, it animates to the PREVIOUS
    number -- which looks entirely convincing and is wrong by exactly one poll.

    Setting it also keeps the attribute honest: the page's count-up pass reads it, so leaving it stale
    would revert every figure to its page-load value if anything ever re-ran that pass.
    """
    js = _js('static/js/refresh-control.js')
    body = js.split('function tick(el, value) {', 1)[1].split('\n    }', 1)[0]

    assert 'el.dataset.countup = value' in body
    assert body.index('el.dataset.countup = value') < body.index('PlatPursuit.countUp('), \
        'the target is set after the animation starts, so it animates to the previous value'


def test_an_unchanged_figure_does_not_re_animate():
    """Most polls move some figures and not others: bronzes climb constantly while platinums barely
    move. Without the skip, three of the four would re-run a 600ms animation from 4 to 4 every four
    seconds -- four numbers pulsing while none of them changes reads as a page in distress."""
    js = _js('static/js/refresh-control.js')
    body = js.split('function tick(el, value) {', 1)[1].split('\n    }', 1)[0]

    assert 'if (value === prev) return;' in body, 'an unchanged figure still animates'
    assert body.index('if (value === prev) return;') < body.index('el.dataset.countup = value')


def test_a_response_that_outlived_its_request_is_discarded():
    """`stopPolling` clears the interval; it CANNOT cancel a fetch already in flight.

    So: one slow request, then the next poll returns `synced` and finishes the run -- and the slow one
    lands afterwards still carrying `sync_status: 'syncing'` and an OLDER tally. Without a liveness check
    it would tick all four tiers and the headline DOWNWARD, one line under "Updated just now", with
    nothing left polling to correct it. A number going backwards reads as data loss, which is the exact
    failure the derived total exists to avoid.

    Both handlers need it: a late REJECTION would otherwise overwrite the finished state with "Status
    unavailable" for a sync that completed.
    """
    js = _js('static/js/refresh-control.js')

    assert 'var seq = ++pollSeq' in js, 'requests are not tagged, so a stale response cannot be spotted'
    # In BOTH handlers, and before anything else in them -- a guard after the first write is no guard.
    assert js.count('if (!pollTimer || seq !== pollSeq) { return; }') == 2, \
        'only one of the two handlers checks whether its response is still wanted'

    then_body = js.split('.then(function (data) {', 1)[1].split('\n            })', 1)[0]
    assert then_body.index('seq !== pollSeq') < then_body.index('applyTally('), \
        'a stale response reaches the figures before it is checked'


def test_the_last_tally_lands_before_the_reload_prompt():
    """The tiers gain rows between the second-to-last poll and the one that sees `synced`. Without a final
    apply they freeze mid-climb at the exact moment the hunter looks at them."""
    js = _js('static/js/refresh-control.js')
    done = _done_branch(js)

    assert 'applyTally(data.stats)' in done, 'the tiers stop a poll short of the finish'
    assert done.index('applyTally(data.stats)') < done.index("say('Updated just now')")


def test_giving_up_on_the_poll_does_not_claim_the_sync_ended():
    """Losing the status teaches us nothing about the sync, which is very probably still running.

    Both give-up paths used to call `setLive(false)`, which hides the "still arriving" lines -- while the
    line right beside them said "Still updating". Self-contradictory in one eyeline, and the lines were
    TRUE: every non-tier figure on the page is still stale. Only the `synced` and `error` branches have
    actually learned the sync ended.
    """
    js = _js('static/js/refresh-control.js')

    cap = js.split('polls > POLL_CAP', 1)[1].split('return;', 1)[0]
    assert 'setLive(' not in cap, 'the poll cap claims the sync finished'

    failure = js.split('pollFailures >= MAX_POLL_FAILURES', 1)[1].split('}', 1)[0]
    assert 'setLive(' not in failure, 'a dead status endpoint claims the sync finished'

    # No count assertion here. The obvious one -- `js.count('setLive(false)') == 1` -- passed only
    # because `_js` strips LINE-LEADING comments and this file mentions the call in one; moving that
    # comment inline would have broken it while the code stayed correct. The two scoped checks above
    # carry the whole meaning.


def test_the_provisional_lines_follow_the_live_dot():
    """One owner for "is this profile syncing": the dot and the two lines are the same fact, so they are
    set together rather than from two places that can disagree."""
    js = _js('static/js/refresh-control.js')

    body = js.split('function setLive(on) {', 1)[1].split('\n    }', 1)[0]
    assert 'provisional' in body, 'the lines are not tied to the syncing state'
    assert 'hidden = !on' in body
