"""One "claimable" rule, read the same way by the engine and every surface.

A tier is pending when it is reached and not accepted, and the PLATINUM tier only exists for a row whose
frozen `has_platinum` is True, because that flag is what `_pending_tiers` pays by. Readers that each
re-derived the rule without the flag counted a platinum stamp the engine would never pay: the banner, the
nav badge and Claim all promised a reward, and claiming it granted nothing ("claimable forever").

Every reader now goes through `contract_service`'s predicates (`has_pending_tier` / `pending_tier_q`),
and this table holds all of them to the engine across every combination of the five fields.
"""
import itertools

import pytest
from django.core.cache import cache
from django.utils import timezone

from trophies.models import Contract, EarnedContract, Job
from trophies.services import career_attention, contract_service
from trophies.services.contracts_service import annotated_contracts
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

_igdb_seq = itertools.count(91001)
_FIELDS = ('has_platinum', 'platinum_reached_at', 'platinum_accepted_at', 'full_reached_at', 'full_accepted_at')


def _live_contract():
    n = next(_igdb_seq)
    c = Contract.objects.create(name=f'tier-{n}', slug=f'tier-{n}', igdb_id=n, is_live=True)
    c.jobs.set(Job.objects.filter(slug='gunslinger'))
    return c


@pytest.mark.parametrize('bits', list(itertools.product((False, True), repeat=5)),
                         ids=lambda bits: ''.join('1' if b else '0' for b in bits))
def test_every_claimable_reader_agrees_with_the_engine(bits):
    """All 32 combinations of has_platinum and the four stamps (including ones no real path writes, so a
    reader cannot agree only on the realistic half). The engine's answer is `_pending_tiers`: claimable
    exactly when accepting would bank a tier."""
    now = timezone.now()
    values = dict(zip(_FIELDS, bits))
    profile = ProfileFactory()
    contract = _live_contract()
    ec = EarnedContract.objects.create(
        profile=profile, contract=contract, has_platinum=values['has_platinum'],
        **{f: (now if values[f] else None) for f in _FIELDS[1:]})

    # The engine's answer, as XP rather than as the predicate it shares with the readers: a tier that would
    # bank NOTHING is exactly the "claimable but pays nothing" shape, so it must not count as claimable.
    engine = any(amount > 0 for _tier, amount in contract_service._pending_tiers(ec, contract)[0])

    cache.clear()   # career_attention caches its count per profile
    readers = {
        'has_pending_tier': contract_service.has_pending_tier(ec),
        'pending_tier_q': EarnedContract.objects.filter(pk=ec.pk).filter(contract_service.pending_tier_q()).exists(),
        'claimable_contracts': contract_service.claimable_contracts(profile).filter(pk=ec.pk).exists(),
        'board status': annotated_contracts(profile).get(pk=contract.pk).status == 'claimable',
        'nav badge': career_attention.claimable_count(profile) == 1,
        'home summary': contract_service.claimable_summary(profile)['count'] == 1,
    }
    disagree = {name: got for name, got in readers.items() if got != engine}
    assert not disagree, f'{values}: the engine says claimable={engine}, but {disagree}'


def _stuck_row(*, full_accepted):
    """The real-world shape: a contract reached and paid at 100% while it had no platinum (so the row froze
    has_platinum=False), which then gained a platinum-bearing member, so sync detects a platinum."""
    from tests.engine.test_contract_xp import _earn_platinum, _platinum_member

    profile = ProfileFactory()
    contract = _live_contract()
    _concept, game, plat = _platinum_member(contract)
    now = timezone.now()
    ec = EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=False,
                                       full_reached_at=now, full_accepted_at=now if full_accepted else None)
    _earn_platinum(profile, game, plat)
    return profile, contract, ec


def test_a_platinum_the_frozen_flag_rules_out_is_never_stamped_or_offered():
    """Sync detects the platinum, but the row cannot take the stamp, so nothing reads it as claimable and
    Claim all banks nothing."""
    profile, contract, ec = _stuck_row(full_accepted=True)

    contract_service.mark_contract_reached(profile, contract)
    ec.refresh_from_db()
    assert ec.platinum_reached_at is None, 'a platinum the engine never pays was stamped'
    assert annotated_contracts(profile).get(pk=contract.pk).status == 'accepted'
    assert not contract_service.claimable_contracts(profile).exists()
    assert contract_service.claim(profile, all_claimable=True)['accepted'] == []


def test_an_existing_stuck_row_is_repaired_by_the_readers_alone():
    """Rows stamped BEFORE the writer was fixed need no migration: every reader ignores a platinum stamp the
    frozen flag rules out, so the contract reads banked, not claimable."""
    profile, contract, ec = _stuck_row(full_accepted=True)
    EarnedContract.objects.filter(pk=ec.pk).update(platinum_reached_at=timezone.now())   # the old writer's stamp

    cache.clear()
    assert annotated_contracts(profile).get(pk=contract.pk).status == 'accepted'
    assert career_attention.claimable_count(profile) == 0
    assert contract_service.claimable_summary(profile)['count'] == 0
    assert contract_service.claim(profile, all_claimable=True)['accepted'] == []


def test_the_card_splits_tiers_by_the_hunters_frozen_flag():
    """A row frozen without a platinum is paid the whole T at 100%, so its card shows one bar and never
    offers a platinum claim, even though the contract's members now define a platinum."""
    from trophies.services.contracts_service import contracts_page

    profile, contract, _ec = _stuck_row(full_accepted=False)
    card = next(c for c in contracts_page(profile, platforms=[])['contracts'] if c['slug'] == contract.slug)
    assert card['has_plat'] is False and card['plat_xp'] == 0
    assert card['claim_plat'] is False and card['claim_full'] is True
    assert card['bonus_xp'] == card['xp_total'], 'the 100% tier must carry the whole T, as accepting pays'


def test_a_bundle_contracts_card_shows_the_platinum_tier_its_row_pays():
    """The flip side of the frozen-flag split. A bundle-only contract (no igdb id) has no igdb-derived
    members, so the member-only `defines_plat` is always False, and its card showed ONE bar even for a
    hunter whose row (frozen from the bundle-aware `_has_platinum`) pays a platinum tier. The card now
    follows the row: the split, and the platinum claim."""
    from trophies.services.contracts_service import contracts_page

    profile = ProfileFactory()
    contract = Contract.objects.create(name='tier-bundle', slug='tier-bundle', igdb_id=None, is_live=True)
    contract.jobs.set(Job.objects.filter(slug='gunslinger'))
    EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=True,
                                  platinum_reached_at=timezone.now())
    card = next(c for c in contracts_page(profile, platforms=[])['contracts'] if c['slug'] == 'tier-bundle')
    assert card['has_plat'] is True and card['plat_xp'] > 0
    assert card['claim_plat'] is True, 'the card hides a platinum tier that accepting would pay'
