"""The two attention markers on the My Pursuit nav item: a claim COUNT and a new-contracts DOT.

They render in the site-wide navbar and mobile tab bar, so the cost rules matter as much as the
behaviour: this runs on every page of the site for every signed-in hunter.
"""
import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from tests.factories import ProfileFactory
from trophies.models import Contract, EarnedContract, Job
from trophies.services import career_attention, contracts_service

pytestmark = pytest.mark.django_db

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}

_NEXT_IGDB = 7_000_000


@pytest.fixture(autouse=True)
def _clear_cache():
    """Both answers are cached, one of them SITE-WIDE, so a stale entry would leak between tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def hunter(client):
    profile = ProfileFactory(is_linked=True, sync_status='synced', total_trophies=10)
    profile.user.ui_flags = {'career_explainer': True}
    profile.user.save(update_fields=['ui_flags'])
    client.force_login(profile.user)
    client.profile = profile
    return client


def _contract(name, *, live=True, jobs=None, announced_days_ago=None):
    global _NEXT_IGDB
    _NEXT_IGDB += 1
    c = Contract.objects.create(name=name, slug=name.lower().replace(' ', '-'),
                                igdb_id=_NEXT_IGDB, is_live=live)
    c.jobs.set(jobs if jobs is not None else list(Job.objects.exclude(is_fallback=True)[:1]))
    if announced_days_ago is not None:
        when = timezone.now() - timezone.timedelta(days=announced_days_ago)
        Contract.objects.filter(pk=c.pk).update(
            went_live_at=when - timezone.timedelta(hours=6),
            announced_at=when, announcement_posted=True)
    return c


def _claimable(profile, contract):
    """A reward reached and not yet accepted -- the board's own definition of claimable."""
    return EarnedContract.objects.create(
        profile=profile, contract=contract, full_reached_at=timezone.now())


# -- the claim count --------------------------------------------------------------------------

def test_the_count_matches_the_board(hunter):
    """The badge and the board must agree or the badge is lying. This count is deliberately NOT
    computed the board's way -- the board annotates the whole catalogue with four correlated
    subqueries, which is the wrong shape for something on every page -- so the two definitions are
    pinned together here."""
    for i in range(3):
        _claimable(hunter.profile, _contract('Claim Me %d' % i))
    _contract('Untouched')

    assert career_attention.claimable_count(hunter.profile) == 3
    assert contracts_service.claimable_summary(hunter.profile)['count'] == 3


def test_an_accepted_reward_stops_counting(hunter):
    ec = _claimable(hunter.profile, _contract('Already Taken'))
    ec.full_accepted_at = timezone.now()
    ec.save(update_fields=['full_accepted_at'])
    cache.clear()

    assert career_attention.claimable_count(hunter.profile) == 0
    assert contracts_service.claimable_summary(hunter.profile)['count'] == 0


def test_an_unpublished_or_jobless_contract_never_counts(hunter):
    """Both are the board's gates (`is_live`, `has_jobs`). A claim badge for something the board
    will not show leads somewhere empty."""
    _claimable(hunter.profile, _contract('Withdrawn', live=False))
    _claimable(hunter.profile, _contract('No Jobs Yet', jobs=[]))

    assert career_attention.claimable_count(hunter.profile) == 0
    assert contracts_service.claimable_summary(hunter.profile)['count'] == 0


def test_the_count_is_cached_and_the_claim_path_clears_it(hunter):
    """Cached because it renders on every page; cleared on a claim because a badge still standing
    after the claim would look broken in the one moment the hunter is looking straight at it."""
    _claimable(hunter.profile, _contract('Claim Me'))
    assert career_attention.claimable_count(hunter.profile) == 1

    EarnedContract.objects.update(full_accepted_at=timezone.now())
    assert career_attention.claimable_count(hunter.profile) == 1, 'not cached at all'

    career_attention.forget_claimable(hunter.profile)
    assert career_attention.claimable_count(hunter.profile) == 0


def test_the_count_reads_the_hunters_own_rows_not_the_catalogue(hunter):
    """The shape that makes it affordable. A catalogue-sized scan per page render is the thing this
    exists to avoid, so the cost must not grow with the number of contracts on the board."""
    _claimable(hunter.profile, _contract('Mine'))

    for i in range(25):
        _contract('Someone Elses %02d' % i)
    cache.clear()

    with CaptureQueriesContext(connection) as ctx:
        career_attention.claimable_count(hunter.profile)
    assert len(ctx.captured_queries) == 1


# -- the new-contracts dot --------------------------------------------------------------------

def test_the_dot_lights_for_an_announcement_newer_than_the_marker(hunter):
    _contract('Fresh', announced_days_ago=0)

    assert career_attention.has_new_contracts(hunter.profile.user) is True


def test_the_dot_agrees_with_the_modal(hunter):
    """A dot that leads to no modal is a dot that trained the reader to ignore dots. Both read the
    same marker and the same 14-day first-visit floor."""
    from trophies.util_modules.constants import NEW_CONTRACT_WINDOW_DAYS
    from trophies.services import new_contracts_modal

    _contract('Ancient', announced_days_ago=NEW_CONTRACT_WINDOW_DAYS + 10)

    user = hunter.profile.user
    assert career_attention.has_new_contracts(user) is False
    assert new_contracts_modal.new_for(hunter.profile, user)['rows'] == []

    _contract('Fresh', announced_days_ago=1)
    cache.clear()
    assert career_attention.has_new_contracts(user) is True
    assert new_contracts_modal.new_for(hunter.profile, user)['rows'] != []


def test_dismissing_the_modal_puts_the_dot_out(hunter):
    contract = _contract('Read It', announced_days_ago=0)
    user = hunter.profile.user
    assert career_attention.has_new_contracts(user) is True

    contract.refresh_from_db()
    hunter.post(reverse('api:user-quick-settings'),
                data={'setting': 'contracts_seen', 'value': contract.announced_at.isoformat()},
                content_type='application/json')

    user.refresh_from_db()
    assert career_attention.has_new_contracts(user) is False


def test_a_contract_that_was_never_posted_lights_nothing(hunter):
    """Same gate as the modal: `--baseline` records a backlog as known WITHOUT posting it, and the
    ~1,000 launch contracts are settled that way. A dot for them would be the whole catalogue
    announcing itself on the first render after deploy."""
    c = _contract('Baselined')
    Contract.objects.filter(pk=c.pk).update(announced_at=timezone.now(), announcement_posted=False)

    assert career_attention.has_new_contracts(hunter.profile.user) is False


def test_the_dot_costs_no_per_user_query(hunter):
    """THE TRICK THAT MAKES IT FREE. "Is anything new to this hunter" is a comparison between a
    per-user marker -- already on the user object, loaded by authentication -- and a global maximum.
    So the only fetch is one value shared by every visitor on the site."""
    _contract('Fresh', announced_days_ago=0)
    career_attention.latest_announced_at()          # warm the shared value

    with CaptureQueriesContext(connection) as ctx:
        career_attention.has_new_contracts(hunter.profile.user)
    assert len(ctx.captured_queries) == 0


def test_the_empty_board_is_cached_too(hunter):
    """`cache.get` cannot tell a stored None from a miss, so an empty board would re-query on every
    render of every page -- the one case where the site-wide trick would cost the most."""
    career_attention.latest_announced_at()

    with CaptureQueriesContext(connection) as ctx:
        assert career_attention.latest_announced_at() is None
    assert len(ctx.captured_queries) == 0


def test_announcing_relights_the_dot_immediately(hunter):
    """The shared value is cached for fifteen minutes. Without an explicit clear, the one event the
    whole feature is built around would take up to that long to show."""
    from core.services.contract_announcer import mark_announced

    _mark_seen(hunter)
    pending = _contract('Waiting')
    career_attention.latest_announced_at()          # cache the pre-announcement state

    mark_announced([pending])

    assert career_attention.has_new_contracts(hunter.profile.user) is True


def _mark_seen(hunter):
    user = hunter.profile.user
    user.ui_flags = dict(user.ui_flags, contracts_seen=timezone.now().isoformat())
    user.save(update_fields=['ui_flags'])


def test_claiming_clears_the_badge_for_real(hunter):
    """Through the real claim path, not the helper. The badge is cached, so nothing about the claim
    itself would drop it -- the hunter would take their reward and watch the count stay put."""
    from trophies.services import contract_service

    contract = _contract('Claim Me')
    _claimable(hunter.profile, contract)
    assert career_attention.claimable_count(hunter.profile) == 1

    contract_service.claim(hunter.profile, contract=contract)

    assert career_attention.claimable_count(hunter.profile) == 0


# -- what actually renders --------------------------------------------------------------------

def test_the_nav_carries_both_markers(hunter):
    for i in range(2):
        _claimable(hunter.profile, _contract('Claim Me %d' % i))
    _contract('Fresh', announced_days_ago=0)

    body = hunter.get('/career/', **CF).content.decode()
    nav = body.split('My Pursuit', 1)[0].rsplit('<a href', 1)[1] + body.split('My Pursuit', 1)[1][:400]

    assert 'pp-navhub__n' in nav and '>2<' in nav, 'the claim count is missing'
    assert 'pp-navhub__dot' in nav, 'the new-contracts dot is missing'
    # The markers are aria-hidden, so the LABEL has to carry the same thing in words.
    assert 'ready to claim' in nav and 'new contracts on the board' in nav


def test_a_quiet_account_gets_no_markers(hunter):
    body = hunter.get('/career/', **CF).content.decode()

    assert 'pp-navhub__n' not in body
    assert 'pp-navhub__dot' not in body


def test_the_count_is_capped_in_the_markup(hunter):
    """A three-digit badge would widen the nav item enough to move the row."""
    for i in range(12):
        _claimable(hunter.profile, _contract('Claim Me %02d' % i))

    body = hunter.get('/career/', **CF).content.decode()

    badge = body.split('class="pp-navhub__n"', 1)[1].split('</span>', 1)[0]
    assert '9+' in badge and '12' not in badge


def test_an_anonymous_visitor_pays_nothing(monkeypatch):
    """The processor returns before it does ANY work for a viewer with no profile -- the
    whole cost for them, and it runs on the Django admin and every anonymous page too.

    Asserting the empty dict cannot see this: without the guard the try/except returns exactly
    the same empty dict, having reached it by raising. What separates the two is the LOG -- so
    every anonymous page render on the site would write a debug traceback, which is control
    flow by exception and a noisy way to do nothing."""
    from plat_pursuit import context_processors as cp

    logged = []
    monkeypatch.setattr(cp.logger, 'debug', lambda *a, **kw: logged.append(a))

    class _Req:
        user = None

    assert cp.career_attention(_Req()) == {}
    assert not logged, 'an anonymous request fell through to the exception handler'


def test_the_markers_never_break_a_page(hunter, monkeypatch):
    """Fails closed, like the What's New and moderation processors: a hunter loses a marker for one
    render and nobody gains one. A nav that 500s because a badge could not be counted would be a
    poor trade for a dot."""
    def _boom(*a, **kw):
        raise RuntimeError('cache is down')

    monkeypatch.setattr(career_attention, 'claimable_count', _boom)

    assert hunter.get('/career/', **CF).status_code == 200
