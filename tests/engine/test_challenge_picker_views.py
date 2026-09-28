"""The picker's four doors: the gate, the two-step confirmation, and the JSON contract.

WHAT THESE PIN that the service tests cannot:

**No door redirects.** Every one of these is called by `fetch`, and a redirect to the login page comes back
as a 200 HTML body that `PlatPursuit.API` reports as SUCCESS. That bug shipped once on `challenge_hide`,
so every new door is pinned against it rather than assumed safe.

**The 409 is not a 400.** A completing placement is refused until confirmed, and the same request repeated
with `confirm` succeeds -- so the status has to say "not yet", not "you're wrong". A client that treats it
as a plain failure would never offer the confirmation.

**An untrusted key is a 404.** The slot key comes from the URL. "Not a slot of this run" and "not your run"
must be indistinguishable from outside.
"""
import json

import pytest
from django.urls import reverse
from django.utils import timezone

from challenges.models import (
    CHALLENGE_TYPE_AZ,
    CHALLENGE_TYPE_JOBS,
    COMPLETED_VIA_HATCH,
    COMPLETED_VIA_IMPORT,
    Challenge,
)
from challenges.services import challenge_service as svc
from tests.factories import (
    ConceptFactory,
    EarnedTrophyFactory,
    GameFactory,
    IGDBMatchFactory,
    ProfileFactory,
    TrophyFactory,
    UserFactory,
)
from trophies.models import Contract, EarnedContract, Job

pytestmark = pytest.mark.django_db

_SEQ = {'n': 0}


def _hunter(client=None, *, linked=True):
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=True, is_linked=linked)
    if client is not None:
        client.force_login(user)
    return profile


def _contract(name, *, jobs=(), live=True, with_game=True):
    _SEQ['n'] += 1
    contract = Contract.objects.create(name=name, slug='v-%d' % _SEQ['n'], is_live=live,
                                       igdb_id=830_000 + _SEQ['n'])
    if jobs:
        contract.jobs.set(jobs)
    if with_game:
        concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
        IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
        GameFactory(concept=concept)
    return contract


def _platted_at(profile, contract, when):
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    game = GameFactory(concept=concept)
    plat = TrophyFactory(game=game, trophy_type='platinum')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned_date_time=when)
    EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=True,
                                  platinum_reached_at=timezone.now())


def _joined(profile, when):
    profile.user.date_joined = when
    profile.user.save(update_fields=['date_joined'])
    return profile


def _slot_url(challenge, key):
    return reverse('challenge_slot', args=[challenge.pk, key])


def _assign_url(challenge, key):
    return reverse('challenge_assign', args=[challenge.pk, key])


def _clear_url(challenge, key):
    return reverse('challenge_clear', args=[challenge.pk, key])


def _search_url(challenge):
    return reverse('challenge_search', args=[challenge.pk])


def _body(resp):
    return json.loads(resp.content)


# ── every door refuses without a session, in JSON ─────────────────────────────────────────────────

@pytest.mark.parametrize('door', ['slot', 'search', 'assign', 'clear'])
def test_no_door_redirects_an_anonymous_caller(client, door):
    """A redirect here comes back to `fetch` as a 200 HTML login page, which `PlatPursuit.API` reports as
    success -- the failure that shipped on `challenge_hide`. Every door is pinned, not just the ones that
    write."""
    challenge = svc.start(_hunter(), CHALLENGE_TYPE_AZ)
    urls = {
        'slot': (_slot_url(challenge, 'B'), 'get'),
        'search': (_search_url(challenge), 'get'),
        'assign': (_assign_url(challenge, 'B'), 'post'),
        'clear': (_clear_url(challenge, 'B'), 'post'),
    }
    url, method = urls[door]

    resp = getattr(client, method)(url)

    assert resp.status_code == 401
    assert resp['Content-Type'].startswith('application/json')
    assert resp.get('Location') is None


def test_an_unlinked_account_is_refused_in_json_too(client):
    challenge = svc.start(_hunter(), CHALLENGE_TYPE_AZ)
    _hunter(client, linked=False)

    resp = client.get(_slot_url(challenge, 'B'))

    assert resp.status_code == 403
    assert resp.get('Location') is None


def test_somebody_elses_run_is_a_404(client):
    """Resolving through `profile=` means an id alone cannot confirm whose run it is."""
    challenge = svc.start(_hunter(), CHALLENGE_TYPE_AZ)
    _hunter(client)

    assert client.get(_slot_url(challenge, 'B')).status_code == 404
    assert client.post(_assign_url(challenge, 'B')).status_code == 404


def test_a_key_this_run_has_no_slot_for_is_a_404(client):
    """The key is a URL segment, so it is untrusted. A jobs slug on an A-Z run is not a validation error to
    explain -- it is indistinguishable from asking about somebody else's run."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    assert client.get(_slot_url(challenge, 'card-shark')).status_code == 404
    assert client.post(_assign_url(challenge, 'card-shark')).status_code == 404


# ── the slot panel ────────────────────────────────────────────────────────────────────────────────

def test_the_slot_door_returns_the_pool_with_cover_urls(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Bloodborne')

    body = _body(client.get(_slot_url(challenge, 'B')))

    assert body['key'] == 'B'
    assert [r['name'] for r in body['rows']] == ['Bloodborne']
    # A URL or None -- never a `Game`, which would not serialise.
    assert body['rows'][0]['cover'] is None or isinstance(body['rows'][0]['cover'], str)


def test_the_slot_door_passes_a_search_term_through(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Bloodborne')
    _contract('Brothers')

    body = _body(client.get(_slot_url(challenge, 'B'), {'q': 'broth'}))

    assert [r['name'] for r in body['rows']] == ['Brothers']


def test_a_catchup_date_is_serialised_as_iso(client):
    """ISO 8601 so the client formats it with `TimeFormatter`, rather than being handed a string in the
    server's idea of a locale."""
    profile = _hunter(client)
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Brothers', with_game=False)
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=30))

    body = _body(client.get(_slot_url(challenge, 'B')))

    row = body['catchup'][0]
    assert row['via'] == COMPLETED_VIA_IMPORT
    assert row['completed_at'] is not None and 'T' in row['completed_at']


# ── the search panel ──────────────────────────────────────────────────────────────────────────────

def test_the_search_door_says_which_squares_a_game_fits(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    jobs = list(Job.objects.order_by('slug')[:2])
    _contract('Astro Bot', jobs=jobs)

    body = _body(client.get(_search_url(challenge), {'q': 'astro'}))

    assert set(body['rows'][0]['keys']) == {j.slug for j in jobs}
    assert body['rows'][0]['key_labels'][jobs[0].slug] == jobs[0].name


def test_a_short_search_term_says_so_rather_than_returning_nothing(client):
    """`too_short` lets the panel explain itself. An empty list with no reason reads as "no results",
    which is a different and wrong message."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Bloodborne')

    body = _body(client.get(_search_url(challenge), {'q': 'b'}))

    assert body['too_short'] is True
    assert body['rows'] == []


# ── assign ────────────────────────────────────────────────────────────────────────────────────────

def test_assigning_returns_the_square_and_the_runs_counters(client):
    """Every write moves the counters, and the page shows them in two places. Returning them means the
    client never guesses or re-fetches."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Bloodborne')

    body = _body(client.post(_assign_url(challenge, 'B'), {'contract': contract.slug}))

    assert body['key'] == 'B'
    assert body['is_filled'] is True
    assert body['is_completed'] is False
    assert body['game_name'] == 'Bloodborne'
    assert body['filled_count'] == 1
    assert body['completed_count'] == 0
    assert body['total_slots'] == 26


def test_an_unknown_game_is_refused_without_claiming_the_run_is_gone(client):
    """A 404 would tell the client the RUN is unavailable, and its error handling would say so. The run is
    fine; the game is the problem."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    resp = client.post(_assign_url(challenge, 'B'), {'contract': 'no-such-game'})

    assert resp.status_code == 400
    assert 'Job Board' in _body(resp)['error']


def test_a_missing_contract_field_is_refused_the_same_way(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    assert client.post(_assign_url(challenge, 'B')).status_code == 400


def test_a_completing_placement_answers_409_and_writes_nothing(client):
    """409, NOT 400. Nothing is wrong with the request -- the same request with `confirm` succeeds -- so a
    client that treated this as a plain failure would never offer the confirmation."""
    profile = _hunter(client)
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Brothers', with_game=False)
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=30))

    resp = client.post(_assign_url(challenge, 'B'), {'contract': done.slug})
    body = _body(resp)

    assert resp.status_code == 409
    assert body['needs_confirmation'] is True
    assert body['via'] == COMPLETED_VIA_IMPORT
    assert body['contract_name'] == 'Brothers'
    assert challenge.slots.get(key='B').is_filled is False, 'the 409 must not have written'


def test_the_same_request_with_confirm_goes_through(client):
    profile = _hunter(client)
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Brothers', with_game=False)
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=30))

    body = _body(client.post(_assign_url(challenge, 'B'),
                             {'contract': done.slug, 'confirm': '1'}))

    assert body['is_completed'] is True
    assert body['completed_via'] == COMPLETED_VIA_IMPORT
    assert body['completed_count'] == 1


def test_the_hatch_route_also_asks_first(client):
    profile = _hunter(client)
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    Challenge.objects.create(profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Old',
                             total_slots=26, is_complete=True, completed_at=timezone.now())
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Brothers')
    EarnedContract.objects.create(profile=profile, contract=done, has_platinum=True,
                                  platinum_reached_at=timezone.now())

    resp = client.post(_assign_url(challenge, 'B'), {'contract': done.slug})

    assert resp.status_code == 409
    assert _body(resp)['via'] == COMPLETED_VIA_HATCH


@pytest.mark.parametrize('sent', ['0', 'false', '', 'maybe'])
def test_only_an_affirmative_confirm_counts(client, sent):
    """A falsy string must not read as consent. `'false'` is the one that would, if the view tested
    truthiness of the raw value instead of matching it."""
    profile = _hunter(client)
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Brothers', with_game=False)
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=30))

    resp = client.post(_assign_url(challenge, 'B'), {'contract': done.slug, 'confirm': sent})

    assert resp.status_code == 409


def test_a_finished_square_refuses_reassignment(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    slot = svc.assign(challenge, profile, 'B', _contract('Bloodborne'))
    svc.mark_slot_completed(slot)
    other = _contract('Brothers')

    resp = client.post(_assign_url(challenge, 'B'), {'contract': other.slug})

    assert resp.status_code == 400
    assert 'finished' in _body(resp)['error'].lower()


# ── clear ─────────────────────────────────────────────────────────────────────────────────────────

def test_clearing_empties_an_unfinished_square(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.assign(challenge, profile, 'B', _contract('Bloodborne'))

    body = _body(client.post(_clear_url(challenge, 'B')))

    assert body['is_filled'] is False
    assert body['game_name'] == ''
    assert body['filled_count'] == 0


def test_clearing_a_finished_square_is_refused(client):
    """Load-bearing for the XP economy rather than tidiness: the job-XP guard is keyed on the slot, so
    clearing a paid square and refilling it is how one job gets paid twice in one run."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    slot = svc.assign(challenge, profile, 'B', _contract('Bloodborne'))
    svc.mark_slot_completed(slot)

    resp = client.post(_clear_url(challenge, 'B'))

    assert resp.status_code == 400
    assert challenge.slots.get(key='B').is_filled is True


# ── method discipline ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('url_name', ['challenge_assign', 'challenge_clear'])
def test_a_write_door_refuses_get(client, url_name):
    """A write reachable by GET is a write a link can perform, and a crawler or a prefetch would."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    resp = client.get(reverse(url_name, args=[challenge.pk, 'B']))

    assert resp.status_code == 405


@pytest.mark.parametrize('url_name, args', [('challenge_slot', ('B',)), ('challenge_search', ())])
def test_a_read_door_refuses_post(client, url_name, args):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    resp = client.post(reverse(url_name, args=(challenge.pk,) + args))

    assert resp.status_code == 405


# ── the audit's findings, pinned ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('door', ['slot', 'search'])
def test_a_nul_byte_in_the_search_term_is_not_a_500(client, door):
    """`?q=%00%00` WAS AN UNHANDLED 500. `MIN_QUERY` is a length floor and says nothing about content, so two
    NUL bytes cleared it and reached `name__icontains`; psycopg refuses NUL in a text parameter
    ("PostgreSQL text fields cannot contain NUL (0x00) bytes") and the `DataError` was caught by nothing.
    Repeatable at the read limit by any linked hunter on their own run.

    The term is now scrubbed BEFORE it is measured, so this is the empty string -- no term at all."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Bloodborne')
    url = _slot_url(challenge, 'B') if door == 'slot' else _search_url(challenge)

    resp = client.get(url, {'q': '\x00\x00'})

    assert resp.status_code == 200


def test_a_control_character_term_is_treated_as_no_term(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Bloodborne')
    _contract('Brothers')

    body = _body(client.get(_slot_url(challenge, 'B'), {'q': '\x00\x01\x02'}))

    assert body['total'] == 2, 'a term of control bytes should filter nothing, not error'
    assert body['query'] == ''


def test_an_absurdly_long_term_is_truncated_rather_than_refused(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    resp = client.get(_slot_url(challenge, 'B'), {'q': 'x' * 5000})

    assert resp.status_code == 200
    assert len(_body(resp)['query']) <= 120


@pytest.mark.parametrize('state', ['finished', 'hidden'])
def test_the_picker_doors_refuse_a_run_that_cannot_be_changed(client, state):
    """They answered 200 with a full offer list for a run where `_lock_challenge` refuses every write -- after
    doing the whole pool scan and, on a thin slot, the five-query trophy read. Expensive work to build a list
    of buttons that could only 400."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    if state == 'finished':
        Challenge.objects.filter(pk=challenge.pk).update(is_complete=True, completed_at=timezone.now())
    else:
        svc.hide(challenge, profile)

    assert client.get(_slot_url(challenge, 'B')).status_code == 404
    assert client.get(_search_url(challenge), {'q': 'blood'}).status_code == 404
    assert client.post(_assign_url(challenge, 'B'), {'contract': 'x'}).status_code == 404
    assert client.post(_clear_url(challenge, 'B')).status_code == 404


def test_a_locked_square_says_so_instead_of_building_offers(client):
    """A completed square can never be reassigned, so every query spent building its pool is spent building
    a refusal. The panel answers in one query and tells the client it is locked."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    slot = svc.assign(challenge, profile, 'B', _contract('Bloodborne'))
    svc.mark_slot_completed(slot)
    _contract('Brothers')

    body = _body(client.get(_slot_url(challenge, 'B')))

    assert body['locked'] is True
    assert body['rows'] == []
    assert body['total'] == 0
    assert body['current_name'] == 'Bloodborne'


def test_the_catchup_block_says_when_it_is_showing_everything(client):
    """A truncated list that looks complete is a lie, so the panel says which it is.

    BOTH BRANCHES, and the first version of this test only exercised one. It used a single candidate against
    a limit of 24, so it never reached the full-slice path -- which was the branch that was WRONG: it
    reported `pool.count()`, every completed game in the slot's pool with no lifting filter, so a hunter
    could see an empty catch-up block beside a count of forty. The test would have passed with the number
    hard-coded.
    """
    profile = _hunter(client)
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Brothers', with_game=False)
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=30))

    body = _body(client.get(_slot_url(challenge, 'B')))

    assert len(body['catchup']) == 1
    assert body['catchup_more'] is False, 'one candidate is not a truncated list'


def test_the_catchup_block_says_when_it_is_cut_short(client):
    """THE BRANCH THAT WAS WRONG. The slice fills, so there may be more -- and the old code answered that
    with a count of the whole completed pool, including everything no rule lifts."""
    profile = _hunter(client)
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    for i in range(3):
        done = _contract('Brothers %d' % i, with_game=False)
        _platted_at(profile, done, timezone.now() - timezone.timedelta(days=30))

    # `limit` is the panel's page size; the door does not expose it, so drive the service directly.
    from challenges.services import picker
    panel = picker.slot_panel(profile, challenge, 'B', limit=2)

    assert len(panel['catchup']) == 2
    assert panel['catchup_more'] is True


@pytest.mark.parametrize('door', ['slot', 'assign', 'clear'])
def test_a_nul_byte_in_the_url_key_is_not_a_500(client, door):
    """THE SIBLING OF THE SEARCH-TERM BUG, in the function whose docstring claimed immunity to it.

    `<str:key>` matched `[^/]+`, so `%00` decoded to a NUL, reached `slots.filter(key=key)` and psycopg
    refused it at parameter-dump time -- an unhandled `DataError` and a 500 on all three slot doors,
    repeatable by any linked hunter on their own run. `<slug:key>` rejects it at the router, before there is
    a query to poison.

    Asserted as "not a 500" rather than "is a 404", because the meaningful property is that an untrusted URL
    segment cannot reach the database, and a router that declines to match is one correct way to get there.
    """
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    suffix = {'slot': '', 'assign': 'assign/', 'clear': 'clear/'}[door]
    path = '/my-challenges/%d/slot/%%00/%s' % (challenge.pk, suffix)

    resp = client.get(path) if door == 'slot' else client.post(path)

    # 404 for the GET door, 405 for the POST ones -- and the 405 is this project's own documented quirk, not
    # a surprise: `handler404` is GET-only, so a 404 reached by a POST comes back listing GET/HEAD/OPTIONS.
    # `_ChallengeActionView`'s docstring records the same thing for `Http404` raised inside a view.
    #
    # The property under test is that an untrusted URL segment never reaches the database. Either status is a
    # correct way to get there; a 500 is not. My first version of this test asserted `== 404` while its own
    # docstring said "asserted as not a 500", and the POST doors failed it for the right reason.
    assert resp.status_code in (404, 405), 'expected a refusal, got %s' % resp.status_code
    assert resp.status_code != 500


def test_a_real_key_still_routes_for_both_challenge_types(client):
    """The guard above must not have narrowed the door: `slug` has to accept an uppercase letter and a
    hyphenated job slug, which are the only two shapes a key ever takes."""
    profile = _hunter(client)
    az = svc.start(profile, CHALLENGE_TYPE_AZ)
    jobs = svc.start(profile, CHALLENGE_TYPE_JOBS)

    assert client.get(_slot_url(az, 'B')).status_code == 200
    assert client.get(_slot_url(jobs, 'card-shark')).status_code == 200
