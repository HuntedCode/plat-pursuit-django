"""Tests for research_panel_service.build_research_panel_context.

The Research Panel lists live Contracts as "Projects". Pins: the project shape (games as
the focal point + elements + fixed-T reward split), the per-viewer status machine
(available / pursuing / claimable / accepted) derived from EXISTING EarnedContract rows
(never written on this read path), jobless/non-live projects hidden, and claimable_count.
"""
import pytest

from trophies.models import Contract, ContractMembership, Job, Trophy
from trophies.services import contract_service
from trophies.services.research_panel_service import build_research_panel_context
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
    return next(p for p in ctx['projects'] if p['slug'] == slug)


def test_lists_live_project_with_games_elements_and_split():
    profile = ProfileFactory()
    _contract('p-list', ('gunslinger', 'mage'), title='Cool Game')

    ctx = build_research_panel_context(profile)

    p = _project(ctx, 'p-list')
    assert p['name'] == 'Cool Game'         # from the member concept, not the contract name
    assert p['game_count'] == 1
    assert len(p['elements']) == 2
    assert p['xp_total'] == CONTRACT_XP_TOTAL
    assert p['xp_each'] == CONTRACT_XP_TOTAL // 2
    assert len(p['vial_bands']) == 2        # one equal band per element (the even split)
    assert p['family_color'].startswith('var(--disc-')


def test_jobless_project_is_hidden():
    profile = ProfileFactory()
    _contract('p-jobless', job_slugs=())  # no elements -> awards nothing

    ctx = build_research_panel_context(profile)

    assert not any(p['slug'] == 'p-jobless' for p in ctx['projects'])


def test_non_live_project_is_excluded():
    profile = ProfileFactory()
    _contract('p-dormant', live=False)

    ctx = build_research_panel_context(profile)

    assert ctx['projects'] == []


def test_status_available_when_untouched():
    profile = ProfileFactory()
    _contract('p-avail')

    p = _project(build_research_panel_context(profile), 'p-avail')

    assert p['status'] == 'available'
    assert p['progress'] == 0


def test_status_pursuing_with_partial_progress():
    profile = ProfileFactory()
    _contract2 = _contract('p-pursue')
    _c, _concept, game = _contract2
    ProfileGameFactory(profile=profile, game=game, progress=60)

    p = _project(build_research_panel_context(profile), 'p-pursue')

    assert p['status'] == 'pursuing'
    assert p['progress'] == 60


def test_status_claimable_when_reached_not_accepted():
    profile = ProfileFactory()
    contract, concept, game = _contract('p-claim')
    plat = Trophy.objects.create(game=game, trophy_id=1, trophy_type='platinum', trophy_name='Plat')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)
    contract_service.mark_contract_reached(profile, contract)  # reached, not accepted

    ctx = build_research_panel_context(profile)
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

    ctx = build_research_panel_context(profile)
    p = _project(ctx, 'p-accepted')

    assert p['status'] == 'accepted'
    assert ctx['claimable_count'] == 0


def test_anonymous_viewer_sees_available_projects():
    _contract('p-anon')

    ctx = build_research_panel_context(None)

    assert _project(ctx, 'p-anon')['status'] == 'available'
