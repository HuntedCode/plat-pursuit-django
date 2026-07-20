"""Contracts History sub-view: the Board | History scope split + banked-XP read-outs.

Pins: `fully_banked` routes ONLY fully-banked contracts to History (a partially-banked one -- platinum
claimed, 100% still to earn -- stays on the Board); History cards carry the ACTUAL banked XP from the
ledger + the per-job split + a banked date + a boosted flag; the 'most XP' sort ranks by the per-job
contribution when a job filter is active; and job_render exposes the per-job contributing-contract
count/xp that feeds the job modal's "View in History" deep-link.
"""
import itertools

import pytest
from django.utils import timezone

from trophies.models import Contract, ContractXPGrant, Game, Job, ProfileGame, Trophy
from trophies.services import contract_service
from trophies.services.contracts_service import contracts_page
from trophies.services.job_render import build_profile_jobs
from trophies.util_modules.constants import CONTRACT_XP_TOTAL
from tests.factories import (
    ConceptFactory, EarnedTrophyFactory, GameFactory, IGDBMatchFactory, ProfileFactory, ProfileGameFactory,
)

pytestmark = pytest.mark.django_db

_igdb_seq = itertools.count(90001)   # fresh igdb-id range for this module (avoid unique collisions)


def _contract(slug, job_slugs=('gunslinger',)):
    """A live Contract + one anchored, trusted-matched PS5 member game (passes the current-gen default)."""
    igdb_id = next(_igdb_seq)
    contract = Contract.objects.create(name=slug, slug=slug, is_live=True, igdb_id=igdb_id)
    contract.jobs.set(Job.objects.filter(slug__in=job_slugs))
    concept = ConceptFactory(unified_title=slug, anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=igdb_id)
    game = GameFactory(concept=concept)
    return contract, game


def _bank(profile, contract, game, *, plat=True, progress=100):
    """Drive a contract to FULLY banked for `profile`: earn the platinum (optional) + set completion,
    then reach + accept so ContractXPGrant rows are written and the *_accepted_at stamps are set."""
    if plat:
        pt = Trophy.objects.create(game=game, trophy_id=1, trophy_type='platinum', trophy_name='Plat')
        EarnedTrophyFactory(profile=profile, trophy=pt, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=progress, has_plat=plat)
    contract_service.mark_contract_reached(profile, contract)
    contract_service.accept_contract(profile, contract)


def _find(page, slug):
    return next((p for p in page['contracts'] if p['slug'] == slug), None)


def test_scope_splits_board_and_history():
    profile = ProfileFactory()
    _contract('h-open')                         # untouched -> Board only
    c_done, g = _contract('h-done')
    _bank(profile, c_done, g)                   # fully banked -> History only

    board = {p['slug'] for p in contracts_page(profile, scope='board')['contracts']}
    history = {p['slug'] for p in contracts_page(profile, scope='history')['contracts']}

    assert 'h-open' in board and 'h-done' not in board
    assert 'h-done' in history and 'h-open' not in history


def test_partially_banked_stays_on_board():
    # Platinum banked but 100% NOT reached (post-plat DLC remains) -> XP still to earn -> Board, not History.
    # This is the case `status=='accepted'` would misroute; `fully_banked` keeps it on the Board.
    profile = ProfileFactory()
    c, g = _contract('h-partial')
    pt = Trophy.objects.create(game=g, trophy_id=1, trophy_type='platinum', trophy_name='Plat')
    EarnedTrophyFactory(profile=profile, trophy=pt, earned=True)
    ProfileGameFactory(profile=profile, game=g, progress=90, has_plat=True)   # plat earned, not 100%
    contract_service.mark_contract_reached(profile, c)
    contract_service.accept_contract(profile, c)                              # banks the platinum tier only

    assert _find(contracts_page(profile, scope='history'), 'h-partial') is None
    assert _find(contracts_page(profile, scope='board'), 'h-partial') is not None


def test_history_card_carries_banked_xp_and_per_job_split():
    profile = ProfileFactory()
    c, g = _contract('h-xp', ('gunslinger', 'mage'))   # 2 jobs -> even split
    _bank(profile, c, g)

    p = _find(contracts_page(profile, scope='history'), 'h-xp')
    assert p['is_history'] is True
    banked = sum(ContractXPGrant.objects.filter(profile=profile, earned_contract__contract=c)
                 .values_list('amount', flat=True))
    assert p['banked_xp'] == banked == CONTRACT_XP_TOTAL          # no multiplier -> banked == the contract T
    assert len(p['job_contribs']) == 2
    assert sum(jc['xp'] for jc in p['job_contribs']) == banked    # the split sums back to the total
    assert p['job_contribs'][0]['xp'] >= p['job_contribs'][1]['xp']   # sorted biggest-first
    assert p['boosted'] is False
    assert p['banked_at'] is not None


def test_history_card_flags_a_boosted_grant():
    profile = ProfileFactory()
    c, g = _contract('h-boost')
    _bank(profile, c, g)
    ContractXPGrant.objects.filter(profile=profile, earned_contract__contract=c).update(multiplier=2)

    assert _find(contracts_page(profile, scope='history'), 'h-boost')['boosted'] is True


def test_history_most_xp_sort_ranks_by_job_contribution():
    # Both contracts feed gunslinger, but the solo contract pays it the FULL T while the 2-job one pays
    # it half -> filtered to gunslinger + 'xp' sort, the solo contract ranks first.
    profile = ProfileFactory()
    c_solo, g1 = _contract('h-solo', ('gunslinger',))
    c_split, g2 = _contract('h-split', ('gunslinger', 'mage'))
    _bank(profile, c_solo, g1)
    _bank(profile, c_split, g2)

    order = [p['slug'] for p in
             contracts_page(profile, scope='history', jobs=['gunslinger'], sort='xp')['contracts']]

    assert order.index('h-solo') < order.index('h-split')


def test_job_render_exposes_contributing_contract_sources():
    profile = ProfileFactory()
    c, g = _contract('h-src', ('gunslinger', 'mage'))
    _bank(profile, c, g)

    tiles = {t['slug']: t for d in build_profile_jobs(profile)['disciplines'] for t in d['jobs']}
    assert tiles['gunslinger']['sources_count'] == 1
    assert tiles['gunslinger']['sources_xp'] == CONTRACT_XP_TOTAL // 2   # gunslinger's half of T
    # A job nothing has leveled -> zeros (drives the modal's find-on-the-Board empty nudge).
    assert tiles['driver']['sources_count'] == 0 and tiles['driver']['sources_xp'] == 0


def test_scope_history_renders_banked_contract_ssr(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    c, g = _contract('h-ssr')
    _bank(profile, c, g)

    resp = client.get('/career/', {'view': 'contracts', 'scope': 'history'})

    assert resp.status_code == 200
    assert b'h-ssr' in resp.content        # the banked contract renders in the History scope
    assert b'XP banked' in resp.content    # the History card's banked read-out
