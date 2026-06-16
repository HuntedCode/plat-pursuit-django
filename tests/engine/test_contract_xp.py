"""Tests for the Contract (job/element) XP engine: the reached-vs-accepted gates, the
fixed-T even split across Platinum/100% tiers, no-platinum-pays-full, acceptance
idempotency + one-accept-banks-all-tiers, completion degradation (DLC) not re-paying,
the ledger->cache recompute, and the leveling curve.
"""
import pytest

from trophies.models import (
    Contract, ContractBundle, ContractMembership, ContractXPGrant, EarnedContract, Job,
    ProfileGame, ProfileJobXP,
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


def test_recompute_floors_orphan_rows_to_level_one():
    """A ProfileJobXP row with no backing ledger grants (orphaned) is reset by recompute to
    the level-1 floor with 0 XP -- not level 0."""
    profile = ProfileFactory()
    job = Job.objects.get(slug='gunslinger')
    ProfileJobXP.objects.create(profile=profile, job=job, total_xp=5000, level=9)

    contract_service.recompute_profile_job_xp(profile)

    pjx = ProfileJobXP.objects.get(profile=profile, job=job)
    assert pjx.total_xp == 0
    assert pjx.level == 1


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


def test_odd_override_still_sums_to_exactly_T():
    """An xp_total_override where both tier fractions land on a .5 boundary. Rounding the
    100% tier independently would overshoot (0.70*1005=704 + 0.30*1005=302 = 1006); paying
    the 100% tier as (grand - platinum) keeps the two tiers summing to exactly the override."""
    profile = ProfileFactory()
    contract = _contract('c-odd', ['gunslinger'])
    contract.xp_total_override = 1005
    contract.save(update_fields=['xp_total_override'])
    _concept, game, plat = _platinum_member(contract)
    _earn_platinum(profile, game, plat)
    contract_service.mark_contract_reached(profile, contract)

    granted = contract_service.accept_contract(profile, contract)

    assert granted == 1005  # 704 platinum + 301 full, not 1006
    assert ProfileJobXP.objects.get(profile=profile, job__slug='gunslinger').total_xp == 1005


def test_split_accept_platinum_then_full_sums_to_T():
    """Platinum earned before 100% -> two separate reaches/accepts. The has_platinum
    snapshot frozen at first reach keeps the later 100% accept paying the remainder, so the
    two accepts together still sum to exactly T."""
    profile = ProfileFactory()
    contract = _contract('c-split', ['gunslinger'])
    _concept, game, plat = _platinum_member(contract)
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=95, has_plat=True)  # platinum, not yet 100%

    contract_service.mark_contract_reached(profile, contract)
    first = contract_service.accept_contract(profile, contract)
    assert first == 3500  # platinum tier only (0.70 * 5000)
    ec = EarnedContract.objects.get(profile=profile, contract=contract)
    assert ec.platinum_accepted_at is not None and ec.full_accepted_at is None

    ProfileGame.objects.filter(profile=profile, game=game).update(progress=100)  # later hits 100%
    contract_service.mark_contract_reached(profile, contract)
    second = contract_service.accept_contract(profile, contract)

    assert second == 1500  # remainder (5000 - 3500)
    assert ProfileJobXP.objects.get(profile=profile, job__slug='gunslinger').total_xp == CONTRACT_XP_TOTAL


def test_bundle_satisfier_completes_full_tier():
    """A Contract satisfied by a ContractBundle (no direct membership): all of the bundle's
    concepts at 100% reaches the 100% tier; a bundle has no platinum so it pays full T."""
    profile = ProfileFactory()
    contract = _contract('c-bundle', ['mage'])
    bundle = ContractBundle.objects.create(contract=contract, label='collection')
    c1, c2 = ConceptFactory(), ConceptFactory()
    g1, g2 = GameFactory(concept=c1), GameFactory(concept=c2)
    bundle.concepts.set([c1, c2])
    ProfileGameFactory(profile=profile, game=g1, progress=100)

    assert contract_service.mark_contract_reached(profile, contract) is None  # only 1 of 2 -> not reached

    ProfileGameFactory(profile=profile, game=g2, progress=100)
    ec = contract_service.mark_contract_reached(profile, contract)
    assert ec.full_reached_at is not None and ec.platinum_reached_at is None

    granted = contract_service.accept_contract(profile, contract)
    assert granted == CONTRACT_XP_TOTAL
    assert ProfileJobXP.objects.get(profile=profile, job__slug='mage').total_xp == CONTRACT_XP_TOTAL


def test_accept_contracts_bulk_accepts_all_claimable():
    profile = ProfileFactory()
    c1 = _contract('c-bulk1', ['gunslinger'])
    concept1, game1, plat1 = _platinum_member(c1)
    _earn_platinum(profile, game1, plat1)
    c2 = _contract('c-bulk2', ['mage'])
    concept2, game2 = _noplat_member(c2)
    ProfileGameFactory(profile=profile, game=game2, progress=100)
    contract_service.check_profile_contracts(profile, [concept1.id, concept2.id])
    assert len(list(contract_service.claimable_contracts(profile))) == 2

    total = contract_service.accept_contracts(profile)  # no list -> accept every claimable

    assert total == 2 * CONTRACT_XP_TOTAL
    assert not list(contract_service.claimable_contracts(profile))


def test_leveling_curve_is_one_based_and_caps():
    assert leveling.xp_for_level(1) == 0    # level 1 is the floor (0 XP)
    assert leveling.level_for_xp(0) == 1    # no XP -> level 1, not 0
    assert leveling.level_for_xp(-5) == 1   # floors at 1
    for level in (2, 5, 10, JOB_LEVEL_CAP):
        xp = leveling.xp_for_level(level)
        assert leveling.level_for_xp(xp) == level          # exactly at threshold
        assert leveling.level_for_xp(xp - 1) == level - 1  # just below
    # Far above the cap must clamp (and not loop forever).
    assert leveling.level_for_xp(leveling.xp_for_level(JOB_LEVEL_CAP) * 100) == JOB_LEVEL_CAP
