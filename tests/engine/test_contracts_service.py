"""Tests for contracts_service.build_contracts_context.

The Contracts board lists live Contracts to pursue. Pins: the contract shape (games as
the focal point + jobs + fixed-T reward split), the per-viewer status machine
(available / pursuing / claimable / accepted) derived from EXISTING EarnedContract rows
(never written on this read path), jobless/non-live contracts hidden, and claimable_count.
"""
import pytest

from trophies.models import Contract, ContractMembership, Job, Trophy
from trophies.services import contract_service
from trophies.services.contracts_service import build_contracts_context
from trophies.util_modules.constants import CONTRACT_XP_TOTAL
from tests.factories import (
    ConceptFactory, EarnedTrophyFactory, GameFactory, ProfileFactory, ProfileGameFactory,
)

pytestmark = pytest.mark.django_db


def _contract(slug, job_slugs=('gunslinger',), live=True, title='My Game'):
    contract = Contract.objects.create(name=slug, slug=slug, is_live=live)
    contract.jobs.set(Job.objects.filter(slug__in=job_slugs))
    concept = ConceptFactory(unified_title=title)
    game = GameFactory(concept=concept)
    ContractMembership.objects.create(contract=contract, concept=concept)
    return contract, concept, game


def _project(ctx, slug):
    return next(p for p in ctx['contracts'] if p['slug'] == slug)


def test_lists_live_project_with_games_elements_and_split():
    profile = ProfileFactory()
    _contract('p-list', ('gunslinger', 'mage'), title='Cool Game')

    ctx = build_contracts_context(profile)

    p = _project(ctx, 'p-list')
    assert p['name'] == 'p-list'            # the contract name wins over the member concept title
    assert p['game_count'] == 1
    assert len(p['elements']) == 2
    assert p['xp_total'] == CONTRACT_XP_TOTAL
    assert p['xp_each'] == CONTRACT_XP_TOTAL // 2
    assert len(p['ring_segments']) == 2                       # one SVG arc per element (the even split)
    assert {s['slug'] for s in p['ring_segments']} == {e['slug'] for e in p['elements']}  # tagged per job
    assert all('dash' in s and 'offset' in s and 'disc_slug' in s for s in p['ring_segments'])
    assert p['family_color'].startswith('var(--disc-')
    # gunslinger + mage are different families -> the accent bar is a multi-family gradient.
    assert p['family_gradient'].startswith('linear-gradient')


def test_jobless_project_is_hidden():
    profile = ProfileFactory()
    _contract('p-jobless', job_slugs=())  # no elements -> awards nothing
    _contract('p-keep')                   # a control that MUST still appear

    slugs = {p['slug'] for p in build_contracts_context(profile)['contracts']}

    assert 'p-keep' in slugs        # distinguishes "hidden" from "everything is gone"
    assert 'p-jobless' not in slugs


def test_non_live_project_is_excluded():
    profile = ProfileFactory()
    _contract('p-dormant', live=False)
    _contract('p-live')             # a control that MUST still appear

    slugs = {p['slug'] for p in build_contracts_context(profile)['contracts']}

    assert 'p-live' in slugs
    assert 'p-dormant' not in slugs


def test_status_available_when_untouched():
    profile = ProfileFactory()
    _contract('p-avail')

    p = _project(build_contracts_context(profile), 'p-avail')

    assert p['status'] == 'available'
    assert p['progress'] == 0


def test_status_pursuing_with_partial_progress():
    profile = ProfileFactory()
    _contract2 = _contract('p-pursue')
    _c, _concept, game = _contract2
    ProfileGameFactory(profile=profile, game=game, progress=60)

    p = _project(build_contracts_context(profile), 'p-pursue')

    assert p['status'] == 'pursuing'
    assert p['progress'] == 60


def test_status_pursuing_when_complete_but_reach_not_yet_stamped():
    """The documented seam: a member game is 100% but the sync hasn't run
    mark_contract_reached yet (no EarnedContract). The read-only render shows 'pursuing'
    at 100%, not 'claimable' -- the next sync flips it."""
    profile = ProfileFactory()
    _c, _concept, game = _contract('p-precommit')
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)

    p = _project(build_contracts_context(profile), 'p-precommit')

    assert p['status'] == 'pursuing'
    assert p['progress'] == 100
    assert p['completed'] is True


def test_xp_total_override_drives_reward_split():
    profile = ProfileFactory()
    contract, _concept, _game = _contract('p-override', ('gunslinger', 'mage'))
    contract.xp_total_override = 1000
    contract.save(update_fields=['xp_total_override'])

    p = _project(build_contracts_context(profile), 'p-override')

    assert p['xp_total'] == 1000           # the override, not CONTRACT_XP_TOTAL
    assert p['xp_each'] == 500             # 1000 // 2 elements


def test_status_claimable_when_reached_not_accepted():
    profile = ProfileFactory()
    contract, concept, game = _contract('p-claim')
    plat = Trophy.objects.create(game=game, trophy_id=1, trophy_type='platinum', trophy_name='Plat')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)
    contract_service.mark_contract_reached(profile, contract)  # reached, not accepted

    ctx = build_contracts_context(profile)
    p = _project(ctx, 'p-claim')

    assert p['status'] == 'claimable'
    assert ctx['claimable_count'] == 1


def test_status_accepted_after_accept():
    profile = ProfileFactory()
    contract, concept, game = _contract('p-accepted')
    plat = Trophy.objects.create(game=game, trophy_id=1, trophy_type='platinum', trophy_name='Plat')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)
    contract_service.mark_contract_reached(profile, contract)
    contract_service.accept_contract(profile, contract)

    ctx = build_contracts_context(profile)
    p = _project(ctx, 'p-accepted')

    assert p['status'] == 'accepted'
    assert ctx['claimable_count'] == 0


def test_anonymous_viewer_sees_available_projects():
    _contract('p-anon')

    ctx = build_contracts_context(None)

    assert _project(ctx, 'p-anon')['status'] == 'available'
