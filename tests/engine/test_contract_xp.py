"""Tests for the Contract (job/element) XP engine: the reached-vs-accepted gates, the
fixed-T even split across Platinum/100% tiers, no-platinum-pays-full, acceptance
idempotency + one-accept-banks-all-tiers, completion degradation (DLC) not re-paying,
the ledger->cache recompute, and the leveling curve.
"""
import pytest

from trophies.models import (
    Contract, ContractMembership, ContractXPGrant, EarnedContract, Job, ProfileGame, ProfileJobXP,
)
from trophies.services import contract_service
from trophies.util_modules import leveling
from trophies.util_modules.constants import CONTRACT_XP_TOTAL, JOB_LEVEL_CAP
from tests.factories import (
    ConceptFactory, EarnedTrophyFactory, GameFactory, ProfileFactory, ProfileGameFactory,
    TrophyFactory,
)

pytestmark = pytest.mark.django_db


def _contract(slug, job_slugs):
    c = Contract.objects.create(name=slug, slug=slug, is_live=True)
    c.jobs.set(Job.objects.filter(slug__in=job_slugs))
    return c


def _platinum_member(contract):
    """A member concept whose game has a platinum trophy."""
    concept = ConceptFactory()
    game = GameFactory(concept=concept)
    plat = TrophyFactory(game=game, trophy_type='platinum')
    ContractMembership.objects.create(contract=contract, concept=concept)
    return concept, game, plat


def _noplat_member(contract):
    concept = ConceptFactory()
    game = GameFactory(concept=concept)  # no platinum trophy
    ContractMembership.objects.create(contract=contract, concept=concept)
    return concept, game


def _earn_platinum(profile, game, plat):
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)


def test_reached_makes_claimable_but_grants_no_xp():
    profile = ProfileFactory()
    contract = _contract('c-claim', ['gunslinger'])
    _concept, game, plat = _platinum_member(contract)
    _earn_platinum(profile, game, plat)

    ec = contract_service.mark_contract_reached(profile, contract)
    assert ec.platinum_reached_at is not None
    assert ec.full_reached_at is not None
    assert ec.platinum_accepted_at is None and ec.full_accepted_at is None
    assert list(contract_service.claimable_contracts(profile)) == [ec]
    assert not ProfileJobXP.objects.filter(profile=profile).exists()  # no XP until accepted


def test_one_accept_banks_platinum_and_full_to_exactly_T():
    profile = ProfileFactory()
    contract = _contract('c-plat', ['gunslinger', 'slayer'])
    _concept, game, plat = _platinum_member(contract)
    _earn_platinum(profile, game, plat)
    contract_service.mark_contract_reached(profile, contract)

    granted = contract_service.accept_contract(profile, contract)

    assert granted == CONTRACT_XP_TOTAL  # both tiers banked in one accept == exactly T
    for slug in ('gunslinger', 'slayer'):
        assert ProfileJobXP.objects.get(profile=profile, job__slug=slug).total_xp == 2500  # 1750 plat + 750 full
    assert ContractXPGrant.objects.filter(profile=profile).count() == 4  # 2 jobs x 2 tiers
    ec = EarnedContract.objects.get(profile=profile, contract=contract)
    assert ec.platinum_accepted_at is not None and ec.full_accepted_at is not None
    assert not list(contract_service.claimable_contracts(profile))  # no longer claimable


def test_accept_is_idempotent():
    profile = ProfileFactory()
    contract = _contract('c-idem', ['gunslinger'])
    _concept, game, plat = _platinum_member(contract)
    _earn_platinum(profile, game, plat)
    contract_service.mark_contract_reached(profile, contract)
    contract_service.accept_contract(profile, contract)

    again = contract_service.accept_contract(profile, contract)

    assert again == 0
    assert ContractXPGrant.objects.filter(profile=profile).count() == 2  # 1 job x 2 tiers, unchanged


def test_no_platinum_game_pays_full_T_at_100():
    profile = ProfileFactory()
    contract = _contract('c-noplat', ['mage'])
    _concept, game = _noplat_member(contract)
    ProfileGameFactory(profile=profile, game=game, progress=100)  # 100% but no platinum

    contract_service.mark_contract_reached(profile, contract)
    ec = EarnedContract.objects.get(profile=profile, contract=contract)
    assert ec.has_platinum is False
    assert ec.platinum_reached_at is None and ec.full_reached_at is not None

    granted = contract_service.accept_contract(profile, contract)

    assert granted == CONTRACT_XP_TOTAL  # full T at 100% (no bonus split)
    assert ProfileJobXP.objects.get(profile=profile, job__slug='mage').total_xp == CONTRACT_XP_TOTAL


def test_even_split_distributes_remainder_and_sums_to_T():
    profile = ProfileFactory()
    contract = _contract('c-three', ['gunslinger', 'slayer', 'warrior'])
    _concept, game, plat = _platinum_member(contract)
    _earn_platinum(profile, game, plat)
    contract_service.mark_contract_reached(profile, contract)

    granted = contract_service.accept_contract(profile, contract)

    assert granted == CONTRACT_XP_TOTAL
    totals = list(ProfileJobXP.objects.filter(profile=profile).values_list('total_xp', flat=True))
    assert sum(totals) == CONTRACT_XP_TOTAL
    assert max(totals) - min(totals) <= 1  # remainder spread, near-even


def test_completion_degradation_after_accept_does_not_repay():
    """The DLC case: a 100%'d + accepted game drops below 100%. Banked XP is immutable
    and the reached ratchet never unsets, so re-detect + re-accept grants nothing more."""
    profile = ProfileFactory()
    contract = _contract('c-degrade', ['slayer'])
    _concept, game, plat = _platinum_member(contract)
    _earn_platinum(profile, game, plat)
    contract_service.mark_contract_reached(profile, contract)
    contract_service.accept_contract(profile, contract)
    xp_before = ProfileJobXP.objects.get(profile=profile, job__slug='slayer').total_xp

    ProfileGame.objects.filter(profile=profile, game=game).update(progress=85)  # DLC drops completion
    contract_service.mark_contract_reached(profile, contract)
    contract_service.accept_contract(profile, contract)

    assert ProfileJobXP.objects.get(profile=profile, job__slug='slayer').total_xp == xp_before
    assert ContractXPGrant.objects.filter(profile=profile).count() == 2  # unchanged


def test_recompute_rebuilds_cache_from_ledger():
    profile = ProfileFactory()
    contract = _contract('c-recompute', ['gunslinger', 'slayer'])
    _concept, game, plat = _platinum_member(contract)
    _earn_platinum(profile, game, plat)
    contract_service.mark_contract_reached(profile, contract)
    contract_service.accept_contract(profile, contract)

    ProfileJobXP.objects.filter(profile=profile).update(total_xp=0, level=0)  # corrupt the cache
    contract_service.recompute_profile_job_xp(profile)

    for slug in ('gunslinger', 'slayer'):
        pjx = ProfileJobXP.objects.get(profile=profile, job__slug=slug)
        assert pjx.total_xp == 2500
        assert pjx.level == leveling.level_for_xp(2500)


def test_check_profile_contracts_marks_reached_for_touched_concepts():
    """The sync seam passes the concept ids of touched games; their Contracts become
    claimable (reached) but earn no XP until accepted."""
    profile = ProfileFactory()
    contract = _contract('c-sync', ['gunslinger'])
    concept, game, plat = _platinum_member(contract)
    _earn_platinum(profile, game, plat)

    contract_service.check_profile_contracts(profile, [concept.id])

    ec = EarnedContract.objects.get(profile=profile, contract=contract)
    assert ec.platinum_reached_at is not None and ec.full_reached_at is not None
    assert not ProfileJobXP.objects.filter(profile=profile).exists()


def test_leveling_curve_roundtrips_and_caps():
    assert leveling.xp_for_level(0) == 0
    assert leveling.level_for_xp(0) == 0
    for level in (1, 2, 5, 10, JOB_LEVEL_CAP):
        xp = leveling.xp_for_level(level)
        assert leveling.level_for_xp(xp) == level          # exactly at threshold
        assert leveling.level_for_xp(xp - 1) == level - 1  # just below
    # Far above the cap must clamp (and not loop forever).
    assert leveling.level_for_xp(leveling.xp_for_level(JOB_LEVEL_CAP) * 100) == JOB_LEVEL_CAP
