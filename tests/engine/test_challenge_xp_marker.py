"""The "XP" pill on My Pursuit: Challenge job XP earned and not yet claimed.

WHAT IS WORTH PINNING. The marker renders on EVERY page of the site for every signed-in hunter, including
the Django admin, so half of this file is about cost. The other half is about never lying, in both
directions: a pill that lights for XP no button can pay sends somebody to a page with nothing to press, and
a pill that survives the claim contradicts the page they just used.

IT ASKED A DIFFERENT QUESTION FOR ABOUT AN HOUR -- "claimed but not yet SEEN", back when the XP ceremony was
going to be deferred to the next Career visit. That design is gone: the celebration fires on the run page in
the same response as the payout, so nothing is ever unseen, and the pill points at the claim instead. The
tests are written against the question it asks NOW.
"""
import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS
from challenges.services import challenge_service as svc
from challenges.services import rewards
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract, EarnedContract, Job
from trophies.services import career_attention

pytestmark = pytest.mark.django_db

_SEQ = {'n': 0}


@pytest.fixture(autouse=True)
def _clear_cache():
    """The answer is cached per profile, so a stale entry would make every assertion here a coin toss."""
    cache.clear()
    yield
    cache.clear()


def _hunter():
    return ProfileFactory(user=UserFactory(), user_is_premium=True, is_linked=True)


def _contract(name, *, jobs=()):
    _SEQ['n'] += 1
    c = Contract.objects.create(name=name, slug='%s-%d' % (name.lower().replace(' ', '-'), _SEQ['n']),
                                is_live=True, igdb_id=960_000 + _SEQ['n'])
    if jobs:
        c.jobs.set(jobs)
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=c.igdb_id, status='accepted')
    GameFactory(concept=concept)
    return c


def _completed_square(profile, challenge, job):
    """A finished square, through detection, so the fixture is a state a hunter can really be in."""
    contract = _contract('Game for %s' % job.slug, jobs=[job])
    svc.assign(challenge, profile, job.slug, contract)
    EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=True,
                                  platinum_reached_at=timezone.now())
    svc.detect_for_profile(profile)
    return contract


def _run_with_unclaimed_xp(profile, count=2):
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    jobs = list(Job.objects.order_by('slug')[:count])
    for job in jobs:
        _completed_square(profile, challenge, job)
    challenge.refresh_from_db()
    return challenge, jobs


# ── what lights it ───────────────────────────────────────────────────────────────────────────────

def test_a_finished_unclaimed_square_lights_it():
    profile = _hunter()
    assert career_attention.has_unclaimed_challenge_xp(profile) is False

    cache.clear()
    _run_with_unclaimed_xp(profile, count=1)

    assert career_attention.has_unclaimed_challenge_xp(profile) is True


def test_claiming_settles_it(django_capture_on_commit_callbacks):
    """THE CAPTURE FIXTURE IS LOAD-BEARING. The claim clears the cache from `transaction.on_commit`, and
    pytest's transaction never commits -- so without it the pill would still read lit and the test would be
    asserting the wrong mechanism. `on_commit` rather than inline is what stops a concurrent render
    re-caching the pre-claim answer with a fresh TTL."""
    profile = _hunter()
    challenge, jobs = _run_with_unclaimed_xp(profile, count=1)
    assert career_attention.has_unclaimed_challenge_xp(profile) is True

    with django_capture_on_commit_callbacks(execute=True):
        rewards.redeem_slot(challenge, profile, jobs[0].slug)

    assert career_attention.has_unclaimed_challenge_xp(profile) is False


def test_completing_a_square_arms_it_even_with_a_cached_no(django_capture_on_commit_callbacks):
    """THE HALF THAT IS EASY TO LEAVE OUT, and this module's own history says so: the claim count shipped
    invalidating only the SPENDING side, so every page render re-cached a zero with a fresh TTL moments
    before the reward landed -- the badge was dark for most of its TTL at exactly the moment it had
    something to say. Detection is the arming half here."""
    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    # Prime the cache with "nothing waiting", as any page render would.
    assert career_attention.has_unclaimed_challenge_xp(profile) is False

    with django_capture_on_commit_callbacks(execute=True):
        _completed_square(profile, challenge, job)

    assert career_attention.has_unclaimed_challenge_xp(profile) is True


def test_filling_a_square_does_not_spend_the_cached_answer(django_capture_on_commit_callbacks):
    """THE ARMING CLEAR IS FOR A COMPLETION, not for every recount, and this is the pin for the difference.

    `_recount` runs on every `assign` and every `clear`, so a clear gated only on the run TYPE fired 25 times
    while a hunter filled a board -- 25 Redis DELETEs, and 25 forced misses on a key that is read on every page
    of the site for every signed-in hunter. That is the cost this module's own bar is written about, paid by
    the marker itself.

    It is observable precisely because the cached value survives: assigning a contract that does not complete
    anything cannot change the answer, so the entry must still be there afterwards. (Correctness is unaffected
    either way -- arming is a DELETE, so an extra one only costs a re-query -- which is exactly why nothing
    would have noticed without this test.)
    """
    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    assert career_attention.has_unclaimed_challenge_xp(profile) is False, 'primes the cache, as a render would'

    with django_capture_on_commit_callbacks(execute=True):
        svc.assign(challenge, profile, job.slug, _contract('Not finished either', jobs=[job]))

    assert cache.get('career:chalxp:%s' % profile.pk) is False, 'the primed answer is still there'


def test_a_claimed_square_does_not_light_it():
    profile = _hunter()
    challenge, jobs = _run_with_unclaimed_xp(profile, count=1)
    rewards.redeem_slot(challenge, profile, jobs[0].slug)
    cache.clear()

    assert career_attention.has_unclaimed_challenge_xp(profile) is False


def test_a_filled_but_unfinished_square_does_not_light_it():
    """Earning the XP is what completing a square does; there is nothing to claim until then."""
    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    svc.assign(challenge, profile, job.slug, _contract('Not finished', jobs=[job]))

    assert career_attention.has_unclaimed_challenge_xp(profile) is False


def test_an_a_z_run_never_lights_it():
    """A-Z pays no job XP, ever, so a finished A-Z square owes nothing."""
    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    slot = challenge.slots.order_by('position').first()
    svc.assign(challenge, profile, slot.key, _contract('%sll Quiet' % slot.key))
    svc.mark_slot_completed(challenge.slots.get(key=slot.key))

    assert career_attention.has_unclaimed_challenge_xp(profile) is False


def test_a_hidden_run_still_lights_it():
    """Hiding is visibility, not a pause: the payout door still pays a hidden run's squares, so the XP is
    owed and the pill says so. A marker that went dark on hiding would hide a real reward."""
    profile = _hunter()
    challenge, _jobs = _run_with_unclaimed_xp(profile, count=1)
    svc.hide(challenge, profile)
    cache.clear()

    assert career_attention.has_unclaimed_challenge_xp(profile) is True


def test_a_square_whose_job_left_the_catalogue_does_not_light_it():
    """THE SAME PREDICATE AS THE WRITE, which is the point. `redeem_all` skips a square whose `Job` was
    deleted, so a pill that lit for one would send a hunter to a page with nothing to press."""
    profile = _hunter()
    challenge, jobs = _run_with_unclaimed_xp(profile, count=1)
    Job.objects.filter(slug=jobs[0].slug).delete()
    cache.clear()

    assert career_attention.has_unclaimed_challenge_xp(profile) is False
    assert rewards.pending_xp(challenge) == 0, 'and the panel agrees there is nothing to pay'


def test_another_hunters_unclaimed_xp_does_not_light_mine():
    mine, theirs = _hunter(), _hunter()
    _run_with_unclaimed_xp(theirs, count=1)

    assert career_attention.has_unclaimed_challenge_xp(mine) is False
    assert career_attention.has_unclaimed_challenge_xp(theirs) is True


# ── what it costs ────────────────────────────────────────────────────────────────────────────────

def test_a_profileless_viewer_pays_nothing():
    with CaptureQueriesContext(connection) as captured:
        assert career_attention.has_unclaimed_challenge_xp(None) is False

    assert captured.captured_queries == []


def test_it_costs_one_query_and_then_none():
    profile = _hunter()
    _run_with_unclaimed_xp(profile, count=2)
    cache.clear()

    with CaptureQueriesContext(connection) as first:
        career_attention.has_unclaimed_challenge_xp(profile)
    with CaptureQueriesContext(connection) as second:
        career_attention.has_unclaimed_challenge_xp(profile)

    assert len(first.captured_queries) == 1, 'one EXISTS, never a count or a fetch'
    assert second.captured_queries == []


def test_the_negative_answer_is_cached_too():
    """Otherwise every quiet account re-queries on every page of the site, which is most accounts on most
    pages.

    `is False`, NOT `== 0`. This shipped storing an int, justified by "`cache.get` cannot tell a stored
    `False` from a miss" -- which is not true of this cache: `False` round-trips and `is not None` separates it
    from a miss. Storing the bool made that justification unnecessary, and the assertion had to change with
    it, because `False == 0` is true in Python: written as `== 0` this test passes under either storage and so
    pins neither.
    """
    profile = _hunter()

    assert career_attention.has_unclaimed_challenge_xp(profile) is False
    assert cache.get('career:chalxp:%s' % profile.pk) is False, 'the answer itself, not a stand-in for it'


def test_it_does_not_scale_with_the_squares_a_hunter_has_claimed():
    """The partial index carries only the rows in flight, so a hunter with a long claimed history costs
    what a fresh one does."""
    profile = _hunter()
    challenge, jobs = _run_with_unclaimed_xp(profile, count=6)
    rewards.redeem_all(challenge, profile)
    _completed_square(profile, challenge, Job.objects.order_by('slug')[6])
    cache.clear()

    with CaptureQueriesContext(connection) as captured:
        assert career_attention.has_unclaimed_challenge_xp(profile) is True

    assert len(captured.captured_queries) == 1


def test_a_failure_in_challenges_does_not_take_the_other_markers_down(monkeypatch):
    """ITS OWN GUARD, because this is the only marker that reaches into another app's models. Without one,
    a DatabaseError in `challenges` reaches the context processor's blanket handler, which returns an empty
    dict -- so the hunter loses the claim COUNT and the New pill too, over a marker that is third in
    precedence."""
    from challenges.services import rewards as rewards_module

    def boom(_profile):
        raise RuntimeError('challenges is on fire')

    monkeypatch.setattr(rewards_module, 'has_unclaimed_xp', boom)
    profile = _hunter()

    assert career_attention.has_unclaimed_challenge_xp(profile) is False, 'it must fail closed, not raise'


# ── the seeder must not leave it lit ─────────────────────────────────────────────────────────────

def test_wiping_a_profiles_runs_clears_the_marker(django_capture_on_commit_callbacks):
    """Deleting runs takes their unclaimed squares with them, and neither of the two normal writers is
    involved -- so without an explicit clear the pill advertises XP on runs that no longer exist, on a dev
    box, at exactly the moment somebody is looking at the nav on purpose.

    THE CAPTURE FIXTURE IS LOAD-BEARING, for the reason `test_claiming_settles_it` records: the seeder now
    defers its clear to `transaction.on_commit`, so pytest's never-committing transaction would otherwise
    leave the cached answer standing and this test would be asserting the wrong mechanism. It was a bare
    delete first, INSIDE the command's own atomic block -- which cleared the key before the runs were
    actually gone, the exact window every other writer of this key uses `on_commit` to avoid."""
    from django.core.management import call_command
    from django.test import override_settings

    profile = _hunter()
    _run_with_unclaimed_xp(profile, count=1)
    assert career_attention.has_unclaimed_challenge_xp(profile) is True

    with django_capture_on_commit_callbacks(execute=True), override_settings(DEBUG=True):
        call_command('seed_challenge_demo', user=profile.psn_username, wipe=True, verbosity=0)
    # The seeder makes its own runs with unclaimed squares, so the pill may well light again -- what must
    # not happen is the CACHED answer surviving the delete.
    assert cache.get('career:chalxp:%s' % profile.pk) is None
