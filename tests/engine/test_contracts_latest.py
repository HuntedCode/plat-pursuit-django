"""The "Latest" contract window: one definition (NEW_CONTRACT_WINDOW_DAYS on `went_live_at`) read
by the board's Latest chip, its facet count, and the card's New marker.

Keyed on went_live_at rather than created_at on purpose: the candidate pipeline stages a contract
long before staff publish it, so created_at would call a weeks-old draft "new" the day it ships.
"""
import pytest
from django.utils import timezone

from tests.factories import ProfileFactory
from trophies.models import Contract, Job
from trophies.services import contracts_service
from trophies.util_modules.constants import NEW_CONTRACT_WINDOW_DAYS

pytestmark = pytest.mark.django_db


def _contract(name, slug, igdb_id, *, live_days_ago=None, jobs=None):
    c = Contract.objects.create(name=name, slug=slug, igdb_id=igdb_id, is_live=True)
    c.jobs.set(jobs or list(Job.objects.exclude(is_fallback=True)[:2]))
    # save() stamps went_live_at to now; move it to place the contract in or out of the window.
    stamp = (timezone.now() - timezone.timedelta(days=live_days_ago)) if live_days_ago is not None else None
    Contract.objects.filter(pk=c.pk).update(went_live_at=stamp)
    c.refresh_from_db()
    return c


def _page(profile, **kw):
    """platforms=[] disables the platform filter. contracts_page defaults it to MODERN_PLATFORMS,
    which requires a member GAME on that platform -- these fixtures are contracts without members,
    so the default would empty every result and the window assertions would pass vacuously."""
    kw.setdefault('platforms', [])
    return contracts_service.contracts_page(profile, **kw)


def test_new_only_keeps_recent_and_drops_old():
    profile = ProfileFactory(is_linked=True)
    _contract('Fresh', 'fresh', 870001, live_days_ago=1)
    _contract('Stale', 'stale', 870002, live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 5)

    names = {c['name'] for c in _page(profile, new_only=True)['contracts']}

    assert names == {'Fresh'}
    assert {c['name'] for c in _page(profile)['contracts']} == {'Fresh', 'Stale'}


def test_launch_contracts_with_no_stamp_are_not_new():
    """THE launch decision: the ~1,000 badge-derived contracts carry went_live_at = NULL, so the
    chip is empty on day one and fills as waves land, rather than calling the whole catalogue new."""
    profile = ProfileFactory(is_linked=True)
    _contract('Launch Set', 'launch-set', 870011, live_days_ago=None)

    assert _page(profile, new_only=True)['contracts'] == []
    card = _page(profile)['contracts'][0]
    assert card['is_new'] is False


def test_the_window_boundary():
    """Just inside the window is new; just outside is not."""
    profile = ProfileFactory(is_linked=True)
    _contract('Inside', 'inside', 870021, live_days_ago=NEW_CONTRACT_WINDOW_DAYS - 1)
    _contract('Outside', 'outside', 870022, live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 1)

    assert {c['name'] for c in _page(profile, new_only=True)['contracts']} == {'Inside'}


def test_card_marker_matches_the_filter():
    """The card's New marker and the chip must never disagree -- they read one cutoff."""
    profile = ProfileFactory(is_linked=True)
    _contract('Marked', 'marked', 870031, live_days_ago=2)
    _contract('Unmarked', 'unmarked', 870032, live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 2)

    by_name = {c['name']: c for c in _page(profile)['contracts']}

    assert by_name['Marked']['is_new'] is True
    assert by_name['Unmarked']['is_new'] is False
    filtered = {c['name'] for c in _page(profile, new_only=True)['contracts']}
    assert filtered == {n for n, c in by_name.items() if c['is_new']}


def test_facet_count_reflects_the_other_filters_not_itself():
    """The Latest chip's count rides the same filtered set as the status chips, so narrowing by
    platform narrows it too -- but turning Latest ON must not shrink its own count."""
    profile = ProfileFactory(is_linked=True)
    _contract('New One', 'new-one', 870041, live_days_ago=1)
    _contract('New Two', 'new-two', 870042, live_days_ago=3)
    _contract('Old One', 'old-one', 870043, live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 1)

    facets = contracts_service.board_facets(profile, platforms=[])

    assert facets['new'] == 2


def test_new_only_composes_with_other_filters():
    """It narrows rather than replacing: Latest + a job filter returns their intersection."""
    profile = ProfileFactory(is_linked=True)
    jobs = list(Job.objects.exclude(is_fallback=True)[:2])
    _contract('New Driver', 'new-driver', 870051, live_days_ago=1, jobs=[jobs[0]])
    _contract('New Other', 'new-other', 870052, live_days_ago=1, jobs=[jobs[1]])
    _contract('Old Driver', 'old-driver', 870053,
              live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 3, jobs=[jobs[0]])

    names = {c['name'] for c in
             _page(profile, new_only=True, jobs=[jobs[0].slug])['contracts']}

    assert names == {'New Driver'}
