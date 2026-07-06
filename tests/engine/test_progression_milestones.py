"""Progression milestones: tier/rank crossings logged when XP is banked, plus the pure ladder
builders that drive the Career hero + job-detail ladders."""
import pytest

from trophies.models import (
    Contract, ContractMembership, Job, ProgressionMilestone, Trophy,
)
from trophies.services import contract_service
from trophies.util_modules.constants import JOB_XP_PER_LEVEL
from trophies.util_modules.leveling import (
    frac_into_level, job_tier_ladder, pursuer_rank_ladder, ranks_crossed, tiers_crossed,
)
from tests.factories import (
    ConceptFactory, EarnedTrophyFactory, GameFactory, ProfileFactory, ProfileGameFactory,
)

pytestmark = pytest.mark.django_db


# --- pure crossing / ladder helpers ---------------------------------------------

def test_tiers_crossed_spans_a_multi_tier_jump():
    keys = [k for _lvl, k, _n in tiers_crossed(1, 60)]
    assert keys == ['apprentice', 'adept', 'expert']   # Initiate (the floor) is never crossed
    assert tiers_crossed(30, 30) == []                  # no movement
    assert [k for _l, k, _n in tiers_crossed(24, 25)] == ['adept']   # exact boundary


def test_frac_into_level_is_the_within_band_fraction():
    assert frac_into_level(0) == 0.0                                   # floor
    assert frac_into_level(JOB_XP_PER_LEVEL) == 0.0                    # exactly on a boundary -> 0
    assert frac_into_level(JOB_XP_PER_LEVEL // 2) == pytest.approx(0.5)
    assert 0.0 <= frac_into_level(JOB_XP_PER_LEVEL * 3 + 900) < 1.0


def test_ranks_crossed_excludes_the_floor():
    keys = [k for _lvl, k, _n, _d in ranks_crossed(25, 200)]
    assert 'newbie' not in keys and 'recruit' in keys and 'ranger' in keys


def test_job_tier_ladder_marks_current_reached_and_fill():
    ladder = job_tier_ladder(30)   # Adept (25..50)
    cur = [r for r in ladder['rungs'] if r['current']]
    assert len(cur) == 1 and cur[0]['key'] == 'adept'
    assert ladder['current_name'] == 'Adept' and ladder['next_name'] == 'Expert'
    assert 0 < ladder['fill'] < 100
    assert all(r['reached'] for r in ladder['rungs'] if r['min_level'] <= 30)


def test_pursuer_rank_ladder_has_all_rungs():
    ladder = pursuer_rank_ladder(120)   # Hunter
    assert len(ladder['rungs']) == 11
    assert ladder['current']['name'] == 'Hunter'


# --- detection on accept --------------------------------------------------------

def _plat_contract(profile, slug, job_slugs, *, xp_override=None):
    concept = ConceptFactory()
    game = GameFactory(concept=concept)
    plat = Trophy.objects.create(game=game, trophy_id=1, trophy_type='platinum', trophy_name='Plat')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)
    c = Contract.objects.create(name=slug, slug=slug, is_live=True, xp_total_override=xp_override)
    c.jobs.set(Job.objects.filter(slug__in=job_slugs))
    ContractMembership.objects.create(contract=c, concept=concept)
    contract_service.mark_contract_reached(profile, c)
    return c


def test_accept_logs_job_and_rank_milestones_flagged_first_claim():
    profile = ProfileFactory()
    c = _plat_contract(profile, 'ms-big', ['gunslinger'], xp_override=90000)   # -> gunslinger level 31 (Adept)
    contract_service.accept_contract(profile, c)                              # first accept, from 0 XP
    job_ms = ProgressionMilestone.objects.filter(profile=profile, kind=ProgressionMilestone.JOB_TIER)
    keys = set(job_ms.values_list('key', flat=True))
    assert {'apprentice', 'adept'} <= keys and 'initiate' not in keys        # crossed both, floor never logged
    assert all(m.from_first_claim for m in job_ms)                           # the onboarding burst is flagged
    assert ProgressionMilestone.objects.filter(profile=profile, kind=ProgressionMilestone.PURSUER_RANK).exists()


def test_second_claim_is_not_flagged_first_claim():
    profile = ProfileFactory()
    contract_service.accept_contract(profile, _plat_contract(profile, 'ms-a', ['gunslinger'], xp_override=90000))
    contract_service.accept_contract(profile, _plat_contract(profile, 'ms-b', ['mage'], xp_override=90000))
    mage = ProgressionMilestone.objects.filter(profile=profile, job__slug='mage')
    assert mage.exists() and not any(m.from_first_claim for m in mage)


def test_milestones_are_idempotent_on_re_accept():
    profile = ProfileFactory()
    c = _plat_contract(profile, 'ms-idem', ['gunslinger'], xp_override=90000)
    contract_service.accept_contract(profile, c)
    before = ProgressionMilestone.objects.filter(profile=profile).count()
    contract_service.accept_contract(profile, c)   # already accepted -> no new grants, no new milestones
    assert ProgressionMilestone.objects.filter(profile=profile).count() == before


# --- claim() payload (drives the ceremony) --------------------------------------

def test_claim_returns_what_happened_payload():
    profile = ProfileFactory()
    c = _plat_contract(profile, 'clm', ['gunslinger'], xp_override=90000)   # gunslinger 1 -> 31 (Adept)
    result = contract_service.claim(profile, contract=c)
    assert result['xp'] == 90000 and result['accepted'] == ['clm'] and result['first_claim'] is True
    g = {j['slug']: j for j in result['jobs']}['gunslinger']
    assert g['from_level'] == 1 and g['to_level'] == 31 and g['xp'] == 90000
    assert 'icon' in g and g['disc'] == 'combat'                       # icon + colour for the ceremony tile
    assert 0.0 <= g['from_frac'] < 1.0 and 0.0 <= g['to_frac'] < 1.0   # within-level bar fractions
    assert {t['key'] for t in g['tiers']} == {'apprentice', 'adept'}   # crossed both, Initiate excluded
    assert result['pursuer']['to_level'] > result['pursuer']['from_level']
    assert any(r['key'] == 'recruit' for r in result['pursuer']['ranks'])
    assert result['rank_now']   # a display label


def test_claim_all_aggregates_across_contracts():
    profile = ProfileFactory()
    _plat_contract(profile, 'ca-1', ['gunslinger'], xp_override=30000)
    _plat_contract(profile, 'ca-2', ['mage'], xp_override=30000)
    result = contract_service.claim(profile, all_claimable=True)
    assert set(result['accepted']) == {'ca-1', 'ca-2'}
    assert {'gunslinger', 'mage'} <= {j['slug'] for j in result['jobs']}   # both leveled jobs in one payload


def test_claim_returns_empty_when_nothing_claimable():
    profile = ProfileFactory()
    r = contract_service.claim(profile, all_claimable=True)
    assert r['xp'] == 0 and r['accepted'] == [] and r['jobs'] == []
