"""One live-only claimable source, and every claim entry point landing where its count was measured.

`contract_service.claimable_contracts` is the source for every claimable count (banner, tab, nav badge,
the accept API's reply) AND for Claim all: live contracts with jobs, under the engine's pending rule. Claim
all used to pay pending rows on contracts that had gone non-live, which no count or board showed.
`claimable_summary` aggregates the pending XP in SQL (`_pending_xp_expr`), held here to the engine's
Python `_pending_tiers` so the two forms cannot drift.
"""
import itertools
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from trophies.models import Contract, EarnedContract, Job
from trophies.services import career_attention, contract_service
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

_seq = itertools.count(93001)


def _contract(*, live=True, jobs=('gunslinger',), override=None):
    n = next(_seq)
    c = Contract.objects.create(name=f'live-{n}', slug=f'live-{n}', igdb_id=n, is_live=live,
                                xp_total_override=override)
    c.jobs.set(Job.objects.filter(slug__in=jobs))
    return c


def _pending(profile, contract, *, has_platinum=False, plat=False, full=True, plat_banked=False):
    now = timezone.now()
    return EarnedContract.objects.create(
        profile=profile, contract=contract, has_platinum=has_platinum,
        platinum_reached_at=now if plat else None, platinum_accepted_at=now if plat_banked else None,
        full_reached_at=now if full else None)


def test_a_non_live_or_jobless_reward_is_not_claimable_anywhere():
    """Every count and Claim all agree: a pending reward on an unpublished contract, or one that levels
    no job, is not claimable. The rows are kept, so a republished contract is claimable again."""
    profile = ProfileFactory()
    live = _contract()
    _pending(profile, live)
    withdrawn = _contract(live=False)
    _pending(profile, withdrawn)
    _pending(profile, _contract(jobs=()))

    cache.clear()
    assert contract_service.claimable_contracts(profile).count() == 1
    assert contract_service.claimable_summary(profile)['count'] == 1
    assert career_attention.claimable_count(profile) == 1

    result = contract_service.claim(profile, all_claimable=True)
    assert result['accepted'] == [live.slug], 'Claim all paid a contract no count showed'
    assert EarnedContract.objects.get(profile=profile, contract=withdrawn).full_accepted_at is None

    withdrawn.is_live = True
    withdrawn.save(update_fields=['is_live'])
    assert contract_service.claimable_contracts(profile).count() == 1, 'republished, it is claimable again'


@pytest.mark.parametrize('override', [None, 6000, 5005, 1, 0])
@pytest.mark.parametrize('shape', ['full-only, no plat', 'both pending', 'plat pending', 'plat banked, full pending'])
def test_the_summary_banks_exactly_what_accepting_would(override, shape):
    """The SQL total is the engine's: for each row, the sum `_pending_tiers` would bank. 5005 x 0.70 = 3503.5
    is the half-up boundary (Python's ROUND_HALF_UP gives 3504; a float or a truncating SQL form would not),
    and an override of 0 falls back to T, as `or` does in Python."""
    has_platinum, plat, full, plat_banked = {
        'full-only, no plat': (False, False, True, False),
        'both pending': (True, True, True, False),
        'plat pending': (True, True, False, False),
        'plat banked, full pending': (True, True, True, True),
    }[shape]
    profile = ProfileFactory()
    contract = _contract(override=override)
    ec = _pending(profile, contract, has_platinum=has_platinum, plat=plat, full=full, plat_banked=plat_banked)

    tiers, _t, _m = contract_service._pending_tiers(ec, contract)
    engine = sum(amount for _tier, amount in tiers)
    summary = contract_service.claimable_summary(profile)
    assert summary['total_xp'] == engine, f'{shape}, override {override}: SQL {summary["total_xp"]}, engine {engine}'
    # A row that would bank nothing (override 1 with the platinum banked: 1 - round(0.7) = 0) is left out,
    # as the Python summary always did.
    assert summary['items'] == ([{'name': contract.name, 'xp': engine}] if engine > 0 else [])
    # ...but it still COUNTS, as it does for the nav badge and the API, so no two surfaces disagree.
    assert summary['count'] == contract_service.claimable_contracts(profile).count() == 1


def test_the_banner_total_leaves_out_a_tier_already_banked():
    """The banner used to sum each claimable contract's whole T (`xp_eff`). With its platinum tier banked,
    only the 100% tier's 30% is still waiting."""
    profile = ProfileFactory()
    _pending(profile, _contract(), has_platinum=True, plat=True, full=True, plat_banked=True)
    assert contract_service.claimable_summary(profile)['total_xp'] == 1800   # 6000 - round(6000 x 0.70)


def test_the_summary_peeks_highest_first_and_counts_the_rest():
    profile = ProfileFactory()
    for override in (1000, 3000, 2000, 500):
        _pending(profile, _contract(override=override))
    summary = contract_service.claimable_summary(profile, peek=2)
    assert [i['xp'] for i in summary['items']] == [3000, 2000]
    assert summary['count'] == 4 and summary['more'] == 2 and summary['total_xp'] == 6500


def test_job_detail_links_a_claimable_contract_to_the_claim_board(client):
    """Job detail has no claim controller, so its cards' Claim button did nothing. A claimable card links
    to the board that can claim it, the same board the Career banner opens."""
    from trophies.views.career_views import claim_board_url
    from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory

    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    contract = _contract()
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id)
    GameFactory(concept=concept)
    _pending(profile, contract)

    body = client.get(reverse('job_detail', args=['gunslinger'])).content.decode()
    card = body.split(f'data-slug="{contract.slug}"', 1)[1].split('class="rp-row ', 1)[0]
    assert 'rp-accept' not in card, 'a Claim button with nothing listening for it'
    assert f'href="{claim_board_url().replace("&", "&amp;")}"' in card


def test_the_contracts_tab_lands_on_ready_to_claim_once():
    """The tab's badge counts claimables on every platform; the default board shows current-gen with no
    status. The first tab tap onto that untouched default board lands on Ready to Claim, every platform.
    Source text (no JS runner): the landing is recorded only when the URL carries no board filter, fires
    only while the board is still that default, and is spent after one use."""
    import re

    from tests.career_sources import career_script

    src = career_script()
    seed = src.split('boardQuery = buildParams(1);', 1)[1].split("document.addEventListener('pp:contracts-shown'", 1)[0]
    assert re.search(r'var landingQuery = BOARD_KEYS\.some\(.*?location\.search.*?\)\s*\?\s*null\s*:\s*boardQuery;',
                     seed, re.S), 'the landing is armed even when the URL carries a board filter'
    shown = src.split("document.addEventListener('pp:contracts-shown'", 1)[1].split('});', 1)[0]
    spent = re.search(r'var landing = landingQuery;\s*landingQuery = null;', shown)
    assert spent, 'the landing is not spent on the first entry, so a later return can still swap the board'
    guard = re.search(r'if \(landing && boardQuery === landing && tab && parseInt\(tab\.dataset\.claimable', shown)
    assert guard and spent.start() < guard.start(), 'the tab lands even after the board was changed (or never checks the badge)'
    assert "{ detail: { status: 'claimable', allPlatforms: true } }" in shown[guard.end():]

    # Arriving ON Contracts (a plain ?view=contracts link) means the hunter has already seen the board:
    # the landing is spent at load, or their next return would swap it out.
    on_load = src.split("var panel = list.closest('.lab-view');", 1)[1].split("document.addEventListener", 1)[0]
    assert re.search(r'if \(panel && !panel\.hidden\) \{[^}]*landingQuery = null;', on_load), (
        'a load straight onto Contracts leaves the landing armed for a later return')
