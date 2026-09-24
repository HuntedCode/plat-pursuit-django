"""The navbar search's add-and-sync endpoints: the contract `navbar-search.js` polls against.

Both endpoints shipped without coverage, which is how the client half drifted away from them
unnoticed. The visible failure was that typing a new hunter into the navbar search and clicking
"Sync X" gave no confirmation and no link, ever: one `resetAddSync()` did double duty as "cancel the
sync" and "clear the UI", so the success handler's own `input.focus()` re-ran the typeahead, whose
render cancelled the poll ~300ms into its 2500ms start delay. The sync ran server-side and nobody
was told.

The state machine that replaced it lives in JS and this suite cannot reach it. What it CAN pin is
every server fact that machine now depends on, each of which was being read wrongly or not at all:

  * the refusal bodies (429 rate limit, 503 PSN outage) carry an `error` string. `API.request`
    throws on any non-ok response, so these used to be parsed by nobody and replaced with a generic
    "Failed to sync profile." The anon cap is 3/min, so the message a hunter hit most often was
    exactly the one always discarded.
  * `psn_username` comes back in the server's own spelling, which is what the client then polls with.
  * the status endpoint's three client-visible states: 'error' means gone-or-unfindable, a truthy
    `account_id` WITH a `slug` means there is a profile worth linking to, and 'syncing' is the
    intermediate the panel now narrates instead of sitting mute.

`POLL_CAP` and the cancel/repaint split are client-side and stay unverified here; they need a browser.
"""
import re
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from tests.factories import ProfileFactory
from trophies.models import Profile
from trophies.services.sync_service import SyncService, _cooldown_phrase

pytestmark = pytest.mark.django_db

SEARCH_URL = reverse('search_sync_profile')
STATUS_URL = reverse('add_sync_status')


@pytest.fixture(autouse=True)
def _quiet_externals():
    """Stub the PSN boundary only, and let the cooldown run for real.

    Patch targets are the JOB QUEUE (`initial_sync`, `profile_refresh`) and the outage flag, NOT
    `Profile.attempt_sync`. Patching `attempt_sync` is what the first version of this fixture did, and
    it silently disabled the very thing several tests believed they were exercising: `ProfileFactory`
    leaves `last_synced` at the model default (now), so a factory profile is ALWAYS inside its
    cooldown, and two tests asserting a successful refresh were green only because the mock stood in
    for the refusal. Stubbing one layer lower means `can_sync` is real.

    `redis_client` is still patched at the VIEW's module namespace (the view does
    `from ...cache import redis_client`, so patching the origin would leave it holding the real client)
    because `_get_queue_position` and `ProfileSyncStatusView` read it -- but NOT for the outage flag
    any more. Both the view and the service now go through `PSNManager.is_psn_outage_active`, which is
    the one door and the one thing to stub. A MagicMock's `.get()` returns a truthy Mock by default,
    hence the explicit `return_value=None`.
    """
    cache.clear()
    fake_redis = MagicMock()
    fake_redis.get.return_value = None
    with patch('trophies.views.sync_views.redis_client', fake_redis), \
         patch('trophies.psn_manager.PSNManager.initial_sync') as initial, \
         patch('trophies.psn_manager.PSNManager.profile_refresh') as refresh, \
         patch('trophies.psn_manager.PSNManager.is_psn_outage_active', return_value=False) as outage:
        yield {
            'redis': fake_redis,
            'initial_sync': initial,
            'profile_refresh': refresh,
            'outage': outage,
        }


def _signed_in(client, psn_username='searcher'):
    """Sign in, because refreshing a hunter we ALREADY TRACK now needs an account.

    Adding an untracked one does not, so the create-path tests below stay anonymous on purpose -- that
    asymmetry IS the rule, and a helper applied everywhere would hide it.
    """
    member = ProfileFactory(psn_username=psn_username)
    client.force_login(member.user)
    return member


def _refreshable(psn_username, **kwargs):
    """A tracked hunter whose cooldown has expired.

    `last_synced` must be backdated explicitly. The field defaults to `timezone.now` on the model, so
    a factory hunter is inside the standard one-hour cooldown from the instant it is created, and any
    test that wants the refresh path has to say so.
    """
    kwargs.setdefault('last_synced', timezone.now() - timedelta(hours=2))
    return ProfileFactory(psn_username=psn_username, **kwargs)


# --------------------------------------------------------------- SearchSyncProfileView ----

def test_new_name_creates_the_profile_and_queues_an_initial_sync(client, _quiet_externals):
    resp = client.post(SEARCH_URL, {'psn_username': 'BrandNewHunter'})

    assert resp.status_code == 200
    assert resp.json()['success'] is True
    profile = Profile.objects.get(psn_username='brandnewhunter')
    _quiet_externals['initial_sync'].assert_called_once_with(profile)
    _quiet_externals['profile_refresh'].assert_not_called()


def test_known_name_refreshes_instead_of_creating_a_second_profile(client, _quiet_externals):
    _signed_in(client)
    existing = _refreshable('knownhunter')

    resp = client.post(SEARCH_URL, {'psn_username': 'KNOWNHUNTER'})   # matched __iexact

    assert resp.status_code == 200
    assert Profile.objects.filter(psn_username__iexact='knownhunter').count() == 1
    assert resp.json()['psn_username'] == existing.psn_username
    _quiet_externals['profile_refresh'].assert_called_once()
    _quiet_externals['initial_sync'].assert_not_called()


def test_response_carries_the_spelling_the_client_must_poll_with(client):
    """A new profile is stored LOWERCASED (`psn_username.lower()` on create), so the name the client
    polls the status endpoint with has to be the server's, not the raw input. The client reads it off
    this key for exactly that reason; if it ever stopped being the canonical spelling, the poll would
    be asking about a name the DB does not hold under that key."""
    resp = client.post(SEARCH_URL, {'psn_username': 'MixedCaseName'})

    assert resp.json()['psn_username'] == 'mixedcasename'
    assert Profile.objects.filter(psn_username='mixedcasename').exists()


def test_surrounding_whitespace_does_not_create_a_second_profile(client):
    _signed_in(client)
    _refreshable('spacedout')

    resp = client.post(SEARCH_URL, {'psn_username': '  spacedout  '})

    assert resp.status_code == 200
    assert Profile.objects.filter(psn_username='spacedout').count() == 1


def test_blank_name_is_refused_with_a_message(client):
    resp = client.post(SEARCH_URL, {'psn_username': '   '})

    assert resp.status_code == 400
    assert resp.json()['error']
    assert not Profile.objects.exists()


def test_psn_outage_refusal_carries_the_line_the_panel_shows(client, _quiet_externals):
    """503 with an `error` body. The client surfaces this string verbatim, and can only do so because
    it now reads the body of a non-ok response; before that the hunter was told "Failed to sync
    profile. Please try again." while the server had written a sentence explaining that retrying was
    pointless."""
    _quiet_externals['outage'].return_value = True

    resp = client.post(SEARCH_URL, {'psn_username': 'duringoutage'})

    assert resp.status_code == 503
    assert 'unavailable' in resp.json()['error'].lower()
    assert not Profile.objects.filter(psn_username='duringoutage').exists()
    _quiet_externals['initial_sync'].assert_not_called()


def test_anon_rate_limit_refusal_carries_an_error_body(client):
    """The anon cap is 3/min by IP, low enough that a hunter correcting a typo twice can meet it.
    The 429 body's `error` is the only place the reason exists, so it is a hard part of the contract
    rather than a nicety."""
    for i in range(3):
        assert client.post(SEARCH_URL, {'psn_username': f'anonhunter{i}'}).status_code == 200

    resp = client.post(SEARCH_URL, {'psn_username': 'onetoomany'})

    assert resp.status_code == 429
    assert 'too many' in resp.json()['error'].lower()


def test_a_rate_limited_search_creates_nothing(client):
    """The refusal precedes the create, so a capped-out search must not leave a half-added hunter
    behind for the poll to then report on forever."""
    for i in range(3):
        client.post(SEARCH_URL, {'psn_username': f'anonhunter{i}'})

    client.post(SEARCH_URL, {'psn_username': 'neveradded'})

    assert not Profile.objects.filter(psn_username='neveradded').exists()


def test_a_refresh_inside_the_cooldown_is_refused_not_faked(client, _quiet_externals):
    """THE BUG. This view called `profile.attempt_sync()` and threw the return value away, so asking
    to refresh a hunter synced ten minutes ago reported `{'success': True, 'message': 'Syncing X'}`
    for a sync that was never queued. Its sibling `TriggerSyncView`, twelve lines above it in the same
    file, had always done this correctly.

    A factory hunter is inside the standard cooldown by construction, which is why no backdating is
    needed here and why `_refreshable()` exists for the tests that want the other branch.
    """
    _signed_in(client)
    ProfileFactory(psn_username='justsynced')

    resp = client.post(SEARCH_URL, {'psn_username': 'justsynced'})

    assert resp.status_code == 429
    body = resp.json()
    assert 'success' not in body, 'a refused refresh still claims to have started'
    assert body['reason'] == SyncService.REFUSED_COOLDOWN
    _quiet_externals['profile_refresh'].assert_not_called()   # nothing should have been queued


def test_the_refusal_says_when_it_can_be_asked_again(client):
    """A refusal that does not say when is barely better than the silent success it replaced. The
    machine-readable `seconds_to_next_sync` is what lets a client tick a countdown down rather than
    re-parse the sentence."""
    _signed_in(client)
    ProfileFactory(psn_username='soonhunter', last_synced=timezone.now() - timedelta(minutes=30))

    body = client.post(SEARCH_URL, {'psn_username': 'soonhunter'}).json()

    # ~30 minutes of a one-hour standard cooldown left. Loose bounds: the clock moves during the test.
    assert 29 * 60 <= body['seconds_to_next_sync'] <= 30 * 60
    assert 'refresh again in' in body['error']
    assert 'minutes' in body['error']


def test_a_premium_hunter_clears_the_cooldown_sooner(client, _quiet_externals):
    """The tier cooldown is 5 minutes for preferred against 1 hour for basic, and it is the ONLY thing
    `sync_tier` buys on a sync path -- it confers no queue priority whatsoever. Worth pinning, because
    it is the difference a paying hunter is actually promised."""
    _signed_in(client)
    ProfileFactory(psn_username='premiumhunter', sync_tier='preferred',
                   last_synced=timezone.now() - timedelta(minutes=10))
    ProfileFactory(psn_username='basichunter', sync_tier='basic',
                   last_synced=timezone.now() - timedelta(minutes=10))

    assert client.post(SEARCH_URL, {'psn_username': 'premiumhunter'}).status_code == 200
    assert client.post(SEARCH_URL, {'psn_username': 'basichunter'}).status_code == 429


def test_a_brand_new_profile_has_nothing_to_refuse(client, _quiet_externals):
    """`last_synced` defaults to `timezone.now`, so a just-created row looks freshly synced and would
    be refused by a naive shared cooldown check. The create path must not consult it at all."""
    resp = client.post(SEARCH_URL, {'psn_username': 'neverseenb4'})

    assert resp.status_code == 200
    assert resp.json()['success'] is True
    _quiet_externals['initial_sync'].assert_called_once()
    _quiet_externals['profile_refresh'].assert_not_called()


def test_both_refresh_surfaces_refuse_in_the_same_words(client, _quiet_externals):
    """The point of the shared path. `TriggerSyncView` said "Cooldown active: 2400 seconds left" and
    `SearchSyncProfileView` said nothing at all; one function now owns the sentence, so a hunter meets
    the same wording whichever surface they asked from."""
    owner = ProfileFactory(psn_username='ownerhunter')   # inside cooldown by construction
    client.force_login(owner.user)

    anon_body = client.post(SEARCH_URL, {'psn_username': 'ownerhunter'}).json()
    own_body = client.post(reverse('trigger_sync')).json()

    # The stable PREFIX, not the whole sentence: the trailing duration is two independent
    # ceil-to-minutes readings of two clock samples milliseconds apart, so a refusal whose remaining
    # seconds straddle a minute boundary between the two requests would differ by one minute. The
    # shared wording is what this pins; `_cooldown_phrase` is tested directly for the number.
    shared = 'Synced recently. You can refresh again in'
    assert anon_body['error'].startswith(shared)
    assert own_body['error'].startswith(shared)
    assert anon_body['reason'] == own_body['reason'] == SyncService.REFUSED_COOLDOWN


def test_all_three_refresh_surfaces_return_one_body_shape(client, _quiet_externals):
    """The consolidation, pinned at the wire rather than at the source.

    A shared `request_refresh` is only worth having if what comes OUT is uniform: a client that learns
    one body should not have to special-case which surface answered. Mutation testing found this gap --
    dropping the profile from `TriggerSyncView`'s response was caught by nothing, because every other
    test only ever looked at one surface at a time.

    `trigger_sync` does not strictly need `slug` (you are looking at your own account), and the argument
    for sending it anyway is exactly this: one shape, so the next consumer of any of these three gets
    the same keys.
    """
    owner = ProfileFactory(psn_username='shapeowner')       # inside cooldown by construction
    subject = ProfileFactory(psn_username='shapesubject')
    client.force_login(owner.user)

    own = client.post(reverse('trigger_sync')).json()
    searched = client.post(SEARCH_URL, {'psn_username': 'shapesubject'}).json()
    requested = client.post(
        reverse('request_profile_refresh', kwargs={'psn_username': 'shapesubject'})
    ).json()

    expected = {'error', 'reason', 'seconds_to_next_sync', 'psn_username', 'slug'}
    assert set(own) == expected, 'the own-profile trigger has drifted from the shared shape'
    assert set(searched) == expected, 'the search surface has drifted from the shared shape'
    assert set(requested) == expected, 'the profile-page endpoint has drifted from the shared shape'

    # And each names the hunter IT was asked about, not whoever happened to be logged in.
    assert own['psn_username'] == owner.psn_username
    assert searched['psn_username'] == requested['psn_username'] == subject.psn_username


def test_an_outage_is_reported_as_temporary_not_as_a_cooldown(client, _quiet_externals):
    """503 and the outage wording, distinguished from the 429 cooldown: one means "come back in
    40 minutes", the other means "nothing you do will help right now". They were the same
    undifferentiated failure to a client that only read the status line."""
    owner = ProfileFactory(psn_username='outagehunter')
    client.force_login(owner.user)
    _quiet_externals['outage'].return_value = True

    resp = client.post(reverse('trigger_sync'))

    assert resp.status_code == 503
    assert resp.json()['reason'] == SyncService.REFUSED_OUTAGE
    assert 'resume automatically' in resp.json()['error'], 'the wording that stops a hunter retrying'
    _quiet_externals['profile_refresh'].assert_not_called()


def test_a_cooldown_refusal_still_hands_over_the_hunter(client, _quiet_externals):
    """Making the refusal honest must not take away the profile. A cooldown means the hunter EXISTS and
    is viewable right now; only the refresh was declined. Before this endpoint told the truth, a tracked
    name returned 200 and the poll revealed the Visit link on its first tick -- so a 429 with no link
    would be a regression dressed as a fix, and worst of all on the anonymous landing hero, whose whole
    job is getting a stranger onto a profile page."""
    _signed_in(client)
    ProfileFactory(psn_username='trackedhunter')   # inside cooldown by construction

    body = client.post(SEARCH_URL, {'psn_username': 'trackedhunter'}).json()

    assert body['reason'] == SyncService.REFUSED_COOLDOWN
    assert body['psn_username'] == 'trackedhunter'
    assert body['slug'] == reverse('profile_detail', kwargs={'psn_username': 'trackedhunter'})


def test_a_profile_already_syncing_is_not_told_a_new_sync_started(client, _quiet_externals):
    """`PSNManager.profile_refresh` branches on 'error' and 'synced' only: on 'syncing' it falls off the
    end returning None, while `initiate_sync` returns True regardless. So this used to answer
    "Syncing X" for a job nobody queued -- the same bug this whole path exists to remove, one layer
    down.

    Reachable rather than theoretical: `last_synced` is stamped at the START of a sync (the orchestrator
    queues `sync_profile_data` first, and that is what bumps it), so a sync running longer than the
    cooldown leaves the cooldown open while the status is still 'syncing'.

    It answers ok, because what the asker wants IS happening -- but with a reason saying so, and
    without queueing anything.
    """
    _signed_in(client)
    ProfileFactory(psn_username='midflight', sync_status='syncing',
                   last_synced=timezone.now() - timedelta(hours=2))

    resp = client.post(SEARCH_URL, {'psn_username': 'midflight'})

    assert resp.status_code == 200
    assert resp.json()['reason'] == SyncService.ALREADY_SYNCING
    _quiet_externals['profile_refresh'].assert_not_called()   # nothing to queue; one is running


def test_every_refusal_from_the_search_surface_speaks_one_vocabulary(client, _quiet_externals):
    """`reason` exists so a client has ONE field to switch on. Two of this view's three refusals were
    shaped differently from the third: the rate-limit 429 and the outage 503 carried only `error`, so a
    client switching on `reason` got `undefined` for the two conditions it most needs to tell apart."""
    # The anon cap, at 3/m by IP.
    for i in range(3):
        client.post(SEARCH_URL, {'psn_username': f'capfill{i}'})
    capped = client.post(SEARCH_URL, {'psn_username': 'overthecap'})
    assert capped.status_code == 429
    assert capped.json()['reason'] == SyncService.REFUSED_RATE_LIMIT

    cache.clear()
    _quiet_externals['outage'].return_value = True
    outage = client.post(SEARCH_URL, {'psn_username': 'duringoutage'})
    assert outage.status_code == 503
    assert outage.json()['reason'] == SyncService.REFUSED_OUTAGE


def test_the_cooldown_phrase_reads_as_a_sentence_fragment():
    """The no-JS fallback wording. Every live surface renders `seconds_to_next_sync` as a ticking
    countdown instead, so this only has to be coarse and grammatical."""
    assert _cooldown_phrase(0) == 'a moment'      # unreachable from a refusal; '0 seconds' is nonsense
    assert _cooldown_phrase(1) == '1 second'
    assert _cooldown_phrase(35) == '35 seconds'
    assert _cooldown_phrase(60) == '1 minute'
    assert _cooldown_phrase(61) == '2 minutes'      # ceil: never understate the wait
    assert _cooldown_phrase(2400) == '40 minutes'


def test_an_anonymous_visitor_cannot_refresh_a_hunter_we_already_track(client, _quiet_externals):
    """The rule, and the half that is easy to forget. The profile-page control is members-only, so
    leaving this endpoint open to refresh the same profile would just move the door.

    403 rather than a redirect, because this is a JSON endpoint -- and WITH the profile, for the same
    reason the cooldown refusal carries it: the hunter exists and is viewable, so a visitor who typed a
    tracked name still gets handed the profile instead of being bounced. That is the anonymous hero's
    whole promise and it must survive the gate.
    """
    _refreshable('alreadyours')

    resp = client.post(SEARCH_URL, {'psn_username': 'alreadyours'})

    assert resp.status_code == 403
    body = resp.json()
    assert body['reason'] == SyncService.REFUSED_SIGN_IN
    assert body['slug'] == reverse('profile_detail', kwargs={'psn_username': 'alreadyours'})
    _quiet_externals['profile_refresh'].assert_not_called()


def test_an_anonymous_visitor_can_still_add_a_hunter_nobody_tracks(client, _quiet_externals):
    """The other half, and the one that must NOT change: typing your own name on the landing hero is the
    pitch to a stranger. Gating this would gate the front door."""
    resp = client.post(SEARCH_URL, {'psn_username': 'brandnewface'})

    assert resp.status_code == 200
    assert resp.json()['success'] is True
    _quiet_externals['initial_sync'].assert_called_once()


def test_a_signed_in_hunter_is_not_held_to_the_anon_cap(client):
    """15/min keyed by user, and the user bucket short-circuits the IP one, so a member behind a
    shared or NAT'd address is not punished for their neighbours."""
    member = ProfileFactory(psn_username='searchowner')
    client.force_login(member.user)

    for i in range(4):   # one past the anon ceiling
        assert client.post(SEARCH_URL, {'psn_username': f'authedlook{i}'}).status_code == 200


# ------------------------------------------------------------------ AddSyncStatusView ----

def test_unknown_name_reports_error_so_the_panel_can_stop(client):
    """'error' is the client's only terminal-failure signal: it stops the poll and prints the
    check-the-spelling line. A 404 or an empty 200 would leave it polling for five minutes about a
    hunter who does not exist."""
    resp = client.get(STATUS_URL, {'psn_username': 'nosuchhunter'})

    assert resp.status_code == 200
    assert resp.json()['sync_status'] == 'error'
    assert not resp.json()['account_id']


def test_status_lookup_is_case_insensitive(client):
    ProfileFactory(psn_username='casehunter', account_id='2000000000000000001')

    resp = client.get(STATUS_URL, {'psn_username': 'CaseHunter'})

    assert resp.json()['account_id'] == '2000000000000000001'


def test_ingested_profile_hands_over_both_keys_the_link_needs(client):
    """The client requires `account_id` AND `slug` before it reveals the Visit row. It used to gate on
    `account_id` alone and then assign `visitAnchor.href = data.slug` regardless, so a payload with
    one and not the other produced a live-looking link pointing at the empty string."""
    hunter = ProfileFactory(psn_username='ingested', account_id='2000000000000000002')

    data = client.get(STATUS_URL, {'psn_username': 'ingested'}).json()

    assert data['account_id'] == '2000000000000000002'
    assert data['slug'] == reverse('profile_detail', kwargs={'psn_username': hunter.psn_username})
    assert data['psn_username'] == 'ingested'


def test_freshly_created_profile_withholds_the_link(client):
    """The shape the client sees on its first tick after adding a brand-new hunter: the row exists,
    ingestion has not reached the account id yet. Neither terminal branch may fire, or the panel
    would announce a profile with nothing in it.

    `sync_status` is 'synced' here because that is the FIELD DEFAULT, not a finished sync -- which is
    exactly why `account_id` and not the status is what gates the link.
    """
    hunter = Profile.objects.create(psn_username='justadded')

    data = client.get(STATUS_URL, {'psn_username': 'justadded'}).json()

    assert not data['account_id']
    # The FIELD DEFAULT itself, not merely "not error": the whole reason `account_id` gates the link is
    # that a brand-new row already claims to be synced, and `docs/reference/api-endpoints.md` documents
    # that as a gotcha. `!= 'error'` alone was satisfied by any value.
    assert hunter.sync_status == 'synced'
    assert data['sync_status'] == 'synced'


def test_in_progress_sync_reports_syncing(client):
    """The intermediate the panel now narrates ("Syncing NAME. Trophies are arriving now."). It was
    always in the payload and the client simply ignored it, which is why a multi-minute first sync
    looked identical to a dead button."""
    ProfileFactory(psn_username='midsync', sync_status='syncing')

    assert client.get(STATUS_URL, {'psn_username': 'midsync'}).json()['sync_status'] == 'syncing'


def test_status_blank_name_is_refused(client):
    resp = client.get(STATUS_URL, {'psn_username': ''})

    assert resp.status_code == 400
    assert resp.json()['error']


def test_status_lookup_consumes_no_psn_tokens_for_anon(client):
    """Read-only by design, so it is open to anonymous callers to pair with the open search endpoint.
    A guard against someone "tidying up" by routing it through the sync machinery."""
    ProfileFactory(psn_username='tokenfree', account_id='2000000000000000003')

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        assert client.get(STATUS_URL, {'psn_username': 'tokenfree'}).status_code == 200
        assign.assert_not_called()


# ------------------------------------------------------------- the client-side contract ----

def _tag_around(markup, token):
    """The whole tag containing `token`, from its `<` to its `>`.

    Not `split(token)[1].split('>')[0]`: that only sees attributes written AFTER the token, so an
    `aria-live` or `role="status"` placed before it would slip past a test written to forbid it.
    """
    at = markup.index(token)
    return markup[markup.rindex('<', 0, at):markup.index('>', at) + 1]


def _navbar_search_form(client):
    """The navbar search form's markup as `(panel, form)`.

    `panel` is the results panel's own subtree, bounded by walking div depth from its opening tag to
    the matching close -- NOT "everything after the id", which is the same string as the rest of the
    form and would let anything in the form satisfy an assertion about being inside the panel. It did:
    the first version of the announcer test passed a broken slice and failed against correct markup.

    Scoped rather than asserting on bare ids across the whole page, so a same-named hook elsewhere in
    the chrome could not satisfy these on its own.
    """
    body = client.get('/support/').content.decode()
    form = body.split('id="navbar-sync-form"', 1)
    assert len(form) == 2, 'the navbar search form is not on the page'
    form = form[1].split('</form>', 1)[0]

    at = form.find('id="navbar-search-results"')
    assert at != -1, 'the navbar search results panel is not in the form'
    start = form.rindex('<div', 0, at)

    depth, i = 0, start
    while i < len(form):
        nxt_open, nxt_close = form.find('<div', i), form.find('</div>', i)
        if nxt_close == -1:
            break
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            i = nxt_open + 4
        else:
            depth -= 1
            i = nxt_close + 6
            if depth == 0:
                return form[start:i], form
    raise AssertionError('the results panel div is never closed inside the form')


def test_navbar_exposes_the_status_line_the_controller_writes_to(client):
    """`navbar-search.js` looks up `add-sync-status-text` by id. It was `add-sync-error-text` with the
    error modifier baked into its class list, which is a large part of why the successful path had
    nowhere to speak: the one element that could have carried "In the queue" was styled and named as
    a failure."""
    panel, _form = _navbar_search_form(client)

    assert 'id="add-sync-status-text"' in panel
    # The status line must not be born wearing the error colour; JS adds the modifier per phase.
    assert 'pp-navsearch__msg pp-navsearch__msg--err' not in panel
    assert 'add-sync-error-text' not in _form, 'the error-only hook survived the rename'


def test_the_announcer_sits_outside_the_listbox_it_reports_on(client):
    """The staged copy is announced from an sr-only sibling of the panel, not from the visible line
    inside it, for two reasons that each defeat the announcement on their own: a `<p>` is not a
    permitted child of `role="listbox"` so AT may prune it, and the panel carries `hidden` whenever
    it is closed -- which is precisely when a hunter who clicked away most needs telling.

    Mirrors the sync panel's own `data-nav-live` split. The pin is the ORDERING: the announcer must
    come after the panel's close, i.e. not be inside it.
    """
    panel, form = _navbar_search_form(client)

    assert 'data-search-live' in form, 'the live region is missing from the search form'
    assert 'aria-live="polite"' in form
    assert 'role="listbox"' in panel, 'the slice under test is not the listbox panel'
    assert 'data-search-live' not in panel,         'the announcer is inside role="listbox", where it can be pruned and is hidden half the time'
    # The visible line must NOT also be a live region, or every stage is announced twice.
    assert 'aria-live' not in _tag_around(form, 'id="add-sync-status-text"')


def test_the_field_spinner_does_not_double_announce_the_status(client):
    """It was `role="status" aria-label="Searching"`, which reads the same state a second time next to
    a live region that says it in words."""
    _panel, form = _navbar_search_form(client)

    spinner = _tag_around(form, 'add-sync-load')
    assert 'aria-hidden="true"' in spinner
    assert 'role="status"' not in spinner
    assert 'aria-label' not in spinner


# ------------------------------------------------- the controller's own load-bearing structure ----
#
# Source-text pins, the idiom this project already uses for JS it cannot execute (see
# `test_gamelists_actions.py`, which pins `quick-add.js` exactly this way). There is no JS test
# runner here -- `npm test` is a placeholder -- so the state machine's behaviour is unreachable, but
# the STRUCTURE that keeps it correct is not, and every pin below marks a bug that shipped.
#
# Comments are stripped first: a comment is a claim, only code is evidence, and this repo has shipped
# assertions satisfied by prose describing the very thing that had just been deleted.


def _js(relative):
    src = (Path(__file__).resolve().parents[2] / relative).read_text(encoding='utf-8')
    src = re.sub(r'/\*.*?\*/', '', src, flags=re.S)
    return re.sub(r'^\s*//.*$', '', src, flags=re.M)


def _body(js, signature):
    """A function's body, from its opening line to the first close at its own indentation."""
    assert signature in js, 'missing: %s' % signature
    return js.split(signature, 1)[1].split('\n    }', 1)[0]


def test_only_two_deliberate_acts_abandon_a_sync():
    """THE ORIGINAL BUG, pinned. One `resetAddSync()` both cancelled the in-flight sync and cleared
    the UI, so `closePanel` (any outside click) and `renderSuggestions` (any typeahead response,
    including the one the success handler's own `input.focus()` provoked) each silently destroyed the
    wait. Clicking "Sync X" posted the form and then restored the panel to its pre-click state.

    Exactly two callers may abandon: a submit superseding the last, and Clear. The count is 3 because
    the `function abandonAddSync()` declaration carries the same substring, so a bare `>= 2` would
    have been satisfied by the definition plus a single caller.
    """
    js = _js('static/js/navbar-search.js')

    assert js.count('abandonAddSync()') == 3, \
        'a third caller can abandon a sync; incidental cancellation is the bug this file was rewritten for'
    assert 'resetAddSync' not in js, 'the cancel-and-clear function is back'


def test_closing_the_panel_does_not_stop_the_poll():
    """Dismissing a dropdown is not a decision to abandon a multi-minute sync."""
    body = _body(_js('static/js/navbar-search.js'), 'function closePanel() {')

    assert 'abandonAddSync' not in body
    assert 'stopPolling' not in body
    # It must still drop the cursor class BEFORE emptying `items`, or the highlight strands on a row
    # that survives re-renders (the add row, the Visit row) and later shows up alongside a real cursor.
    assert "classList.remove('is-active')" in body
    assert body.index("classList.remove('is-active')") < body.index('items = []')


def test_async_continuations_guard_on_state_identity_not_nullness():
    """`if (!addSync)` still passes after a second submit REPLACED the object, so a superseded request
    wrote into the new wait: the panel read "Found B" with the link pointing at A's profile, and an
    overwritten timer handle became a 2.5s interval immune to every clear path for the tab's life."""
    js = _js('static/js/navbar-search.js')

    assert js.count('const mine = addSync') == 2, 'the poll and the POST each capture their own state'
    assert js.count('addSync !== mine') == 5, \
        'a continuation that writes addSync is unguarded (poll .then, POST .then, POST .catch x2, startTimer)'


def test_the_refusal_read_is_guarded_on_the_far_side_of_its_await():
    """COUNTING the guards is not enough, and the first version of the pin above proved it: the POST
    `.catch` had one, placed BEFORE the only `await` in the handler. Reading the response body yields,
    and `setAddSync` guards only `!addSync`, so a submit for a DIFFERENT name landing during that read
    had the previous request's refusal stamped into its wait -- wrong sentence, wrong phase, retry row
    re-shown. The count said 4 and the bug was live.

    So pin the ORDER: the await resolves into a local, and identity is re-checked after it.
    """
    js = _js('static/js/navbar-search.js')

    catch = js.split(".catch(async (error) => {", 1)[1].split('\n            });', 1)[0]
    read = 'const body = await refusalBody('
    assert read in catch, 'the body read is not resolved into a local first'

    guard_after = catch.split(read, 1)[1]
    assert 'if (addSync !== mine) return;' in guard_after, \
        'nothing re-checks identity after the await; a superseded refusal can land on the new wait'
    assert guard_after.index('if (addSync !== mine) return;') < guard_after.index('setAddSync('), \
        'the state is written before identity is re-checked'


def test_the_add_row_is_computed_in_exactly_one_place():
    """It was set in `renderSuggestions` and then one-way overridden in `paintAddSync`, so a failed
    sync left a live submit button labelled with the PREVIOUS query until the next debounced refetch
    caught up."""
    js = _js('static/js/navbar-search.js')

    assert js.count('addBtn.hidden =') == 1, 'the add row has two writers again'
    assert 'function paintAddRow()' in js


def test_a_finished_sync_can_be_acted_on_and_a_stalled_one_retried():
    """The submit short-circuit was wrong at both ends.

    `rest` (still syncing after five minutes) was caught by it, so that state could not be retried at
    all: Enter did nothing, refocus did nothing, the add row is hidden for `rest`, and the list was
    emptied on submit -- one sentence, forever, escapable only by Clear.

    `ready` fell THROUGH it, so an Enter that did not land on the Visit row (the cursor may be -1,
    since `focusVisitRow(true)` declines to steal a driven one) abandoned the resolved state and
    re-POSTed, replacing the link with "Looking up X..." and spending one of the three-a-minute anon
    budget re-syncing a hunter already found.
    """
    js = _js('static/js/navbar-search.js')
    # SCOPED to the submit handler. The in-flight phase pair also appears in `paintAddSync`'s
    # `busy` check, so an unscoped assertion matched THAT occurrence and stayed green while the
    # short-circuit it was meant to pin had been mutated back to trapping `rest`. Found by mutation
    # testing, not by reading it.
    submit = js.split("form.addEventListener('submit', (e) => {", 1)[1].split("\n    });", 1)[0]

    # Resolved: Enter navigates.
    assert "addSync.phase === 'ready' && addSync.href" in submit
    assert 'window.location.href = addSync.href;' in submit, \
        'Enter on a resolved sync no longer opens it'
    # In flight: short-circuit. NOT `addSyncOwnsRow`, which also covers `rest` and `error`.
    assert "(addSync.phase === 'waiting' || addSync.phase === 'syncing')" in submit, \
        'the short-circuit is back to a phase set that traps `rest`'
    assert 'addSyncOwnsRow' not in submit, \
        'the short-circuit uses the row gate, which traps `rest`'


def test_the_keyboard_cursor_attribute_follows_the_cursor():
    """`aria-activedescendant` was written only by `setActive` and cleared only by `closePanel`, so a row
    leaving `items` while the panel stayed OPEN left the combobox naming a hidden element -- reachable by
    arrowing onto the add row and pressing Enter, since the keydown handler only intercepts anchors and
    so falls through to a submit that hides that row."""
    body = _body(_js('static/js/navbar-search.js'), 'function refreshItems() {')

    assert "input.removeAttribute('aria-activedescendant')" in body, \
        'the attribute can outlive the element it names'


def test_a_failed_sync_hands_back_the_retry_row():
    """The copy tells a hunter to check the spelling and try again, so the control that would has to be
    on screen. Suppressing the row for every non-null phase hid it exactly when it was wanted."""
    body = _body(_js('static/js/navbar-search.js'), 'function addSyncOwnsRow(q) {')

    assert "addSync.phase !== 'error'" in body


def test_the_announcer_is_painted_from_state_not_fired_per_transition():
    """The sr-only line is the ONLY channel that reaches a screen reader: `ToastManager` appends a plain
    div and the toast container has no `aria-live`.

    Driving it from `paintAddSync` rather than `setAddSync` is what makes it consistent with the screen
    in every path: it clears itself on Clear (state gone), and it updates when a resolved-out-of-view
    sync is brought back into view by retyping the name -- which previously left the region still
    instructing the hunter to do the thing they had just done.
    """
    js = _js('static/js/navbar-search.js')

    assert 'announce(announcementFor())' in _body(js, 'function paintAddSync() {'), \
        'the announcer is no longer painted from state'
    # And it must no-op on an unchanged string, or an idempotent paint running every 2.5s re-announces
    # "Syncing X" for the whole five minutes.
    assert 'liveRegion.textContent !== next' in _body(js, 'function announce(message) {'), \
        'a repaint can re-announce the same sentence'


def test_the_announcement_gate_accounts_for_a_CLOSED_panel():
    """`statusVisible()` means "the field still holds this name", NOT "this is on screen". The two differ
    exactly when the panel is shut, which is the commonest way to wait out a first sync -- and gating on
    the wrong one meant a sync landing after an Escape announced the on-screen sentence while the Visit
    link it refers to was invisible, so the one sentence naming the way back reached nobody.

    The earlier version of this pin asserted only that the gate MENTIONED `statusVisible()`, which it
    did, while being wrong. Pin the panel check instead.
    """
    js = _js('static/js/navbar-search.js')

    assert '!panel.hidden' in _body(js, 'function inView() {'), \
        'inView() no longer distinguishes a closed panel'
    gate = _body(js, 'function announcementFor() {')
    assert 'inView()' in gate, 'the gate is back on statusVisible(), which ignores a closed panel'
    assert 'statusVisible()' not in gate
    assert "phase === 'ready'" in gate and "phase === 'error'" in gate


def test_a_failure_out_of_view_reaches_a_sighted_hunter_too():
    """Only `ready` toasted, so an `error` or a five-minute `rest` reached after the bar moved on was
    written to the sr-only region and nowhere else: screen-reader users were better served than sighted
    ones, the inverse of the usual gap."""
    js = _js('static/js/navbar-search.js')

    assert js.count('const seen = inView();') == 3, \
        'a terminal phase does not check visibility before deciding whether to toast'
    assert 'ToastManager.error(addSync.query' in js, 'an out-of-view failure is silent for sighted users'
    assert 'ToastManager.info(addSync.query' in js, 'an out-of-view poll cap is silent for sighted users'


def test_the_ready_sentence_has_one_source():
    """The toast and the announced line both promise "search the name again". Two literals would drift,
    and the announced half is the only one a screen reader can reach."""
    js = _js('static/js/navbar-search.js')

    assert js.count('readyAway:') == 1
    assert js.count('COPY.readyAway(addSync.query)') == 2, 'the toast and the announcer share one string'


def test_the_shared_refusal_reader_is_feature_tested_at_every_call_site():
    """A browser can hold a cached pre-change `utils.js` against a fresh controller. Every call site is
    inside a `.catch`, where a missing method throws from within a rejection handler: the navbar strands
    its spinner forever, navsync leaves the button disabled reading "Syncing...", quick-add loses the
    toast outright. `quick-add.js` is the pointed case -- its body was self-contained and could not fail
    at all until this branch extracted it, so the guard restores what the extraction took away."""
    nav = _js('static/js/navbar-search.js')
    assert 'api && api.failureBody' in nav
    # Falsifiable: BOTH occurrences of the name in this file are the guard and the call it guards. An
    # earlier form forbade the fully-qualified `PlatPursuit.API.failureBody(`, which appears in neither
    # the correct code nor any plausible careless rewrite (the file uses a local `api` alias), so it
    # could not fail.
    assert nav.count('api.failureBody') == 2, 'a call to failureBody is not paired with its guard'

    sync = _js('static/js/navsync.js')
    assert 'api && api.failureBody' in sync
    assert sync.count('api.failureBody') == 2

    assert 'PP.API.failureMessage ?' in _js('static/js/quick-add.js'), \
        'the delegation can throw from inside a catch'


def test_the_guards_do_not_hide_the_loss_of_the_helper_itself():
    """The necessary complement to feature-testing every call site: with all three guarded, deleting or
    renaming `API.failureOr` raises nothing and fails nothing, and every server refusal in the navbar
    and the sync panel silently reverts to its generic fallback forever. `failureMessage` is pinned by
    `test_gamelists_actions.py`; `failureOr` was pinned nowhere."""
    utils = _js('static/js/utils.js')

    assert 'async failureOr(err, fallback)' in utils, 'the shared fallback reader is gone'
    assert 'async failureMessage(err)' in utils
    assert 'async failureBody(err)' in utils, 'the parsed-body reader is gone'
    # The one thing a caller actually depends on: it reads the server's `error` key. A rewrite returning
    # `data.message` or `data.detail` would keep every other assertion green while reverting every
    # refusal in the app to its fallback.
    assert "data.error) || null" in utils, 'failureMessage no longer reads the server\'s `error` key'
    # And the two tolerances that make a `.catch` caller safe, which moved here out of quick-add.js:
    # a non-API error (no `.response`) and a non-JSON body (an HTML error page).
    body = _body(utils, 'async failureBody(err) {')
    assert "typeof err.response.json !== 'function'" in body, 'a non-API error now throws'
    assert 'catch' in body, 'an HTML error page now throws'


# ------------------------------------- a cooldown is not a failure, on any of three clients ----
#
# The regression these guard: making the cooldown refusal honest changed a 200 into a 429, and each
# client's generic error path then turned a hunter who EXISTS and is viewable right now into a dead end.
# Worst on the anonymous landing hero, whose entire job is getting a stranger onto a profile page, and
# which previously reached the profile via the fake success plus the poll's first tick.


def test_the_landing_hero_hands_over_the_profile_on_a_cooldown():
    """`landing.js` is the anonymous search-first hero. Its 429 branch painted red for "too many
    searches"; a cooldown arrives at the same status and means the opposite -- we have this hunter and
    they are current. The cooldown clause must come FIRST, or the generic branch swallows it."""
    js = _js('static/js/landing.js')

    assert "res.data.reason === 'cooldown'" in js, 'the hero cannot tell a cooldown from a search cap'
    assert js.index("res.data.reason === 'cooldown'") < js.index("'Too many searches."), \
        'the generic 429 branch runs first and swallows the cooldown'
    cooldown = js.split("res.data.reason === 'cooldown'", 1)[1].split('return;', 1)[0]
    assert 'visit.hidden = false' in cooldown, 'the profile link is not revealed'
    assert "setState('ready'" in cooldown, 'a viewable hunter is still being painted as an error'


def test_the_navbar_resolves_a_cooldown_to_the_visit_row():
    """Same regression in the navbar's submit `.catch`, and it must go through the SHARED resolved-state
    helper rather than a second copy: porting half of that landing (paint, but no toast when the panel
    was dismissed) is how the previous round's browser bugs happened."""
    js = _js('static/js/navbar-search.js')

    catch = js.split('.catch(async (error) => {', 1)[1].split('\n            });', 1)[0]
    assert "body.reason === 'cooldown'" in catch
    assert 'resolveReady(' in catch, 'the cooldown path does not reuse the resolved-state helper'
    assert js.count('resolveReady(') == 3, 'the poll and the cooldown must share one landing (+1 def)'


def test_the_sync_panel_does_not_paint_a_cooldown_as_an_error():
    """`navsync.js` turned the avatar ring red and the panel to "Sync error" underneath a message saying
    "you can refresh again in 40 minutes" -- two contradictory statements at once. A cooldown means the
    profile is fine; restore the synced state and put the countdown back on the button."""
    js = _js('static/js/navsync.js')

    body = js.split(".catch(async function (err) {", 1)[1].split('\n            });', 1)[0]
    assert "body.reason === 'cooldown'" in body
    cooldown = body.split("body.reason === 'cooldown'", 1)[1].split('return;', 1)[0]
    assert "setSync('error')" not in cooldown, 'a cooldown still reds the ring'
    assert 'countdown(' in cooldown, 'the button gets no countdown back'


# ----------------------------------------------- the exact hunter match in the typeahead ----

def test_an_exactly_named_hunter_is_never_crowded_out(client):
    """`istartswith` ranked by platinum count and capped at five could bury the hunter you actually
    typed: five `daniel*` accounts with more platinums and `dan` never appears. A search bug on its own,
    and the reason the add row could not tell "Refresh" from "Sync a new hunter"."""
    ProfileFactory(psn_username='dan', total_plats=0)
    for i in range(6):
        ProfileFactory(psn_username=f'daniel{i}', total_plats=500 + i)

    items = _groups_for(client, 'dan')['profile']['items']

    assert items[0]['label'] == 'dan', 'the exact match is not first'
    assert items[0]['exact'] is True


def test_a_prefix_match_is_not_flagged_exact(client):
    """The flag is what the client labels its add row from, so a false positive would offer "Refresh"
    for a hunter nobody tracks."""
    ProfileFactory(psn_username='daniel', total_plats=10)

    items = _groups_for(client, 'dan')['profile']['items']

    assert [i['exact'] for i in items] == [False]


def test_the_exact_match_ignores_case(client):
    """Profile URLs canonicalise to lowercase and display names do not, so the comparison has to be
    case-insensitive or a hunter typing their own name as they write it sees "a new hunter"."""
    ProfileFactory(psn_username='mixedcase')

    items = _groups_for(client, 'MixedCase')['profile']['items']

    assert items[0]['exact'] is True


def _groups_for(client, q):
    resp = client.get(reverse('site_suggest'), {'q': q})
    assert resp.status_code == 200
    return {g['type']: g for g in resp.json()['groups']}


# --------------------------------------------- the add row stops misdescribing itself ----

def test_the_add_row_carries_both_labels(client):
    """One button, two jobs. It both ADDS a hunter nobody tracks and REFRESHES one we already have, and
    for years it described itself as only the first -- "a new hunter" about accounts held since launch.
    The copy stays in the template and the controller picks."""
    _panel, form = _navbar_search_form(client)

    assert 'data-searchable' not in form   # sanity: we are looking at the right form
    assert 'a new hunter' in form
    assert 'data-search-addlabel="new"' in form
    assert 'data-search-addlabel="known"' in form
    assert 'Refresh' in form, 'the tracked-hunter label is missing'
    # Only ONE of them is visible at rest, and it is the additive one.
    known = _tag_around(form, 'data-search-addlabel="known"')
    assert 'hidden' in known, 'both labels render visible at once'


def test_the_tracked_flag_comes_from_the_server_not_a_string_compare():
    """A row's `label` is the DISPLAY name, which can differ from the `psn_username` the query matched --
    so comparing them in the client would say "a new hunter" for exactly the hunters whose display name
    is styled. The suggest endpoint flags the exact match; the controller only reads it."""
    js = _js('static/js/navbar-search.js')

    assert 'item.exact' in js, 'the controller no longer reads the server flag'
    assert "group.type === 'profile'" in js
    # And it must not have gone back to guessing from the label.
    assert 'label.toLowerCase()' not in js, 'the controller is comparing display names again'


def test_a_sign_in_refusal_hands_over_the_profile_in_both_clients():
    """403 `sign_in` means the same thing to a visitor as a cooldown does: this hunter is already
    tracked. Neither is a reason to withhold the profile, and on the ANONYMOUS hero `sign_in` is the case
    it meets most -- every name a visitor types that we already hold."""
    nav = _js('static/js/navbar-search.js')
    catch = nav.split('.catch(async (error) => {', 1)[1].split('\n            });', 1)[0]
    # The CONDITION, not the string. `'sign_in'` also appears in the ternary that picks the copy, so
    # `"'sign_in' in catch"` stayed green while the guard had been narrowed back to cooldown-only.
    # Presence of a name is not presence of a branch -- the fourth pin this session to learn it.
    guard = [ln for ln in catch.split('\n') if 'body.slug' in ln][0]
    assert "'cooldown'" in guard and "'sign_in'" in guard, \
        'the hand-over path no longer covers both already-tracked refusals'
    assert 'resolveReady(' in catch, 'the sign-in path does not reuse the resolved-state helper'

    landing = _js('static/js/landing.js')
    assert "res.data.reason === 'sign_in'" in landing
    hand_over = landing.split("res.data.reason === 'sign_in'", 1)[1].split('return;', 1)[0]
    assert 'visit.hidden = false' in hand_over, 'the anonymous hero withholds a viewable profile'
    assert "setState('error'" not in hand_over


def test_the_hero_does_not_call_a_stale_hunter_up_to_date():
    """`cooldown` and `sign_in` are NOT the same sentence. A cooldown means we synced them recently, so
    "already up to date" is true; `sign_in` means only that we track them and refused the refresh -- it
    says nothing whatever about freshness, and sharing the cooldown's wording told a visitor that a
    five-day-stale hunter was current. The navbar had two strings for this from the start; the hero,
    which meets `sign_in` most, had one."""
    landing = _js('static/js/landing.js')

    hand_over = landing.split("res.data.reason === 'sign_in'", 1)[1].split('return;', 1)[0]
    assert hand_over.count("'Found '") == 2, 'the two refusals still share one sentence'
    assert 'Already up to date' in hand_over
    assert 'Sign in to refresh' in hand_over


# ------------------------------------------------- the lookups that must stay indexed ----

def test_no_sync_endpoint_seq_scans_the_profile_table():
    """`psn_username__iexact` compiles to `UPPER("psn_username") = UPPER(%s)` on Postgres, which neither
    the unique constraint nor `psn_username_idx` can serve -- so it sequentially scans every Profile.

    This file holds the most-polled endpoint on the site: `navbar-search.js` and `landing.js` hit
    `add_sync_status` every 2.5s and `refresh-control.js` every 4s, so the scan ran 15-24 times a minute
    for every viewer with a page open.

    Pinned at the SOURCE because the failure is invisible from the outside: the wrong lookup returns
    exactly the right answer, just slowly, and no assertion on a response body or a test-sized fixture
    can tell the two apart. An `EXPLAIN` against a handful of rows would happily pick a seq scan for the
    indexed version too.

    `.lower()` + exact is correct as well as cheap, because `Profile.save()` lowercases this column
    unconditionally -- pinned by `test_the_stored_name_is_always_lowercased` below.
    """
    source = _js('trophies/views/sync_views.py')   # comment-stripping works on Python too

    assert 'psn_username__iexact' not in source, \
        'a sync endpoint is sequentially scanning the Profile table again'
    assert source.count('psn_username=psn_username.lower()') >= 2, \
        'the indexed form is gone from one of the lookups'


def test_the_stored_name_is_always_lowercased():
    """The premise the indexed lookup rests on. If `Profile.save()` ever stopped lowercasing, exact-match
    would start missing rows that `iexact` used to find -- silently, and only for mixed-case names."""
    profile = ProfileFactory(psn_username='MiXeDcAsE')

    assert profile.psn_username == 'mixedcase'
    profile.psn_username = 'ShOuTiNg'
    profile.save()
    profile.refresh_from_db()
    assert profile.psn_username == 'shouting'
