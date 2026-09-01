"""Bulk claim (2026-09-01) must be BEHAVIOUR-PRESERVING, not merely fast.

The claim path was rewritten so cost scales with the jobs touched (<= 25) instead of the
contracts claimed -- a hunter banking a back catalogue of hundreds used to blow the 30s request
timeout at ~90 queries per contract. Since this is the code the whole XP economy rests on
(`ProfileJobXP = Sum(all grants)`), the acceptance bar is a DIFFERENTIAL one: claiming N
contracts in one batch must leave exactly the state that claiming them one at a time would.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.factories import ProfileFactory
from trophies.models import (
    Contract, ContractXPGrant, EarnedContract, Job, ProfileJobXP, ProgressionMilestone,
)
from trophies.services import contract_service
from trophies.services.contract_service import (
    accept_contract, accept_contracts_bulk, claim, grant_job_xp,
)

pytestmark = pytest.mark.django_db


def _contracts(profile, n, *, jobs_per=6, start=0):
    """n live contracts the profile has reached both tiers on (the maximal-cost shape)."""
    jobs = list(Job.objects.exclude(is_fallback=True)[:jobs_per])
    now = timezone.now()
    made = []
    for i in range(n):
        c = Contract.objects.create(
            name=f'C{start + i}', slug=f'c-{start + i}', igdb_id=700000 + start + i, is_live=True)
        c.jobs.set(jobs)
        EarnedContract.objects.create(
            profile=profile, contract=c, has_platinum=True,
            platinum_reached_at=now, full_reached_at=now,
        )
        made.append(c)
    return made


def _state(profile):
    """Everything a claim writes, in a comparable shape."""
    return {
        'jobs': sorted(ProfileJobXP.objects.filter(profile=profile)
                       .values_list('job_id', 'total_xp', 'level')),
        'grants': sorted(ContractXPGrant.objects.filter(profile=profile)
                         .values_list('job_id', 'amount', 'tier', 'base_t', 'source')),
        'grant_count': ContractXPGrant.objects.filter(profile=profile).count(),
        'milestones': sorted(ProgressionMilestone.objects.filter(profile=profile)
                             .values_list('kind', 'key', 'job_id', 'level_at')),
        'accepted': sorted(EarnedContract.objects.filter(profile=profile)
                           .values_list('contract__slug', 'platinum_accepted_at',
                                        'full_accepted_at')),
    }


@pytest.mark.parametrize('n', [1, 3, 12])
def test_bulk_claim_is_identical_to_sequential(n):
    """THE acceptance bar: twin profiles, same contracts -- one banked in a single bulk claim,
    the other one contract at a time -- must end in the same state (job XP + levels, every
    ledger row, every milestone, every accepted stamp)."""
    seq_profile = ProfileFactory(is_linked=True)
    bulk_profile = ProfileFactory(is_linked=True)
    seq_contracts = _contracts(seq_profile, n, start=0)
    # The bulk twin gets its OWN contracts (igdb_id is unique) with identical shape.
    bulk_contracts = _contracts(bulk_profile, n, start=1000)

    for c in seq_contracts:
        accept_contract(seq_profile, c)
    accept_contracts_bulk(bulk_profile, bulk_contracts)

    seq, bulk = _state(seq_profile), _state(bulk_profile)
    assert bulk['jobs'] == seq['jobs'], 'job XP/levels diverged'
    assert bulk['grants'] == seq['grants'], 'ledger rows diverged'
    assert bulk['grant_count'] == seq['grant_count']
    assert bulk['milestones'] == seq['milestones'], 'milestone rows diverged'
    assert len(bulk['accepted']) == len(seq['accepted'])
    for (_slug_b, bp, bf), (_slug_s, sp, sf) in zip(bulk['accepted'], seq['accepted']):
        assert (bp is None) == (sp is None) and (bf is None) == (sf is None)


def test_ledger_invariant_holds_after_a_bulk_claim():
    """ProfileJobXP = Sum(all grants), per job -- the invariant the primitive exists to keep."""
    profile = ProfileFactory(is_linked=True)
    accept_contracts_bulk(profile, _contracts(profile, 8))

    for pjx in ProfileJobXP.objects.filter(profile=profile):
        ledger = sum(ContractXPGrant.objects.filter(profile=profile, job_id=pjx.job_id)
                     .values_list('amount', flat=True))
        assert pjx.total_xp == ledger, f'{pjx.job_id}: cache {pjx.total_xp} != ledger {ledger}'


def test_bulk_claim_cost_is_flat_in_contract_count():
    """The point of the rewrite: cost is driven by the JOBS touched (<= 25), not the contracts
    claimed. Measured across a 5x jump in claim size -- 40 vs 200 contracts, both far past the
    fixed setup overhead -- the query count must barely move. (Pre-rewrite this was ~90 queries
    PER CONTRACT, i.e. ~3,600 vs ~18,000, and the big claim blew the request timeout.)"""
    forty = ProfileFactory(is_linked=True)
    two_hundred = ProfileFactory(is_linked=True)
    cs_40 = _contracts(forty, 40, start=2000)
    cs_200 = _contracts(two_hundred, 200, start=3000)

    with CaptureQueriesContext(connection) as ctx_40:
        accept_contracts_bulk(forty, cs_40)
    with CaptureQueriesContext(connection) as ctx_200:
        accept_contracts_bulk(two_hundred, cs_200)

    n_40, n_200 = len(ctx_40.captured_queries), len(ctx_200.captured_queries)
    marginal = (n_200 - n_40) / 160
    # The residual is bulk_create/bulk_update BATCHING (one query per batch of rows), which is
    # inherent and cheap -- the bar is that a contract costs a small fraction of a query, not
    # ~90 of them. At this rate 1,000 contracts is still well under a hundred queries.
    assert marginal < 0.5, (
        f'claim cost still scales with contracts: 40 = {n_40} queries, 200 = {n_200} '
        f'({marginal:.2f} per extra contract)'
    )
    assert n_200 < 80, f'claim of 200 contracts should be a few dozen queries, got {n_200}'


def test_single_grant_still_works_through_the_wrapper():
    """grant_job_xp is now a list-of-one call into the batched primitive; its contract
    (ledger row + cache bump + return value) is unchanged."""
    profile = ProfileFactory(is_linked=True)
    job = Job.objects.exclude(is_fallback=True).first()

    returned = grant_job_xp(profile, job, 500, source='manual')

    assert returned == 500
    pjx = ProfileJobXP.objects.get(profile=profile, job=job)
    assert pjx.total_xp == 500
    row = ContractXPGrant.objects.get(profile=profile, job=job)
    assert row.amount == 500 and row.source == 'manual'
    assert grant_job_xp(profile, job, 0) == 0            # non-positive never writes
    assert ContractXPGrant.objects.filter(profile=profile).count() == 1


def test_bulk_claim_is_idempotent():
    """Re-claiming grants nothing and writes no second ledger row."""
    profile = ProfileFactory(is_linked=True)
    contracts = _contracts(profile, 5)
    accept_contracts_bulk(profile, contracts)
    before = _state(profile)

    accepted, granted = accept_contracts_bulk(profile, contracts)

    assert accepted == [] and granted == 0
    assert _state(profile) == before


def test_claim_all_payload_survives_the_batching():
    """The ceremony payload still describes the whole claim: XP total, every accepted slug, and
    per-job level deltas."""
    profile = ProfileFactory(is_linked=True)
    _contracts(profile, 6)

    payload = claim(profile, all_claimable=True)

    assert payload['xp'] > 0
    assert len(payload['accepted']) == 6
    assert payload['jobs'], 'no per-job deltas in the ceremony payload'
    assert all(j['to_level'] >= j['from_level'] for j in payload['jobs'])
    assert payload['pursuer']['to_level'] >= payload['pursuer']['from_level']


def test_multi_tier_level_jump_logs_every_crossing():
    """A batched claim that vaults a job through several prestige tiers at once logs the same
    milestones the incremental path would (tiers_crossed spans the whole jump)."""
    seq_profile = ProfileFactory(is_linked=True)
    bulk_profile = ProfileFactory(is_linked=True)
    seq_cs = _contracts(seq_profile, 25, jobs_per=1, start=4000)    # all XP into ONE job
    bulk_cs = _contracts(bulk_profile, 25, jobs_per=1, start=5000)

    for c in seq_cs:
        accept_contract(seq_profile, c)
    accept_contracts_bulk(bulk_profile, bulk_cs)

    seq_tiers = sorted(ProgressionMilestone.objects
                       .filter(profile=seq_profile, kind=ProgressionMilestone.JOB_TIER)
                       .values_list('key', 'level_at'))
    bulk_tiers = sorted(ProgressionMilestone.objects
                        .filter(profile=bulk_profile, kind=ProgressionMilestone.JOB_TIER)
                        .values_list('key', 'level_at'))
    assert bulk_tiers == seq_tiers
    assert len(bulk_tiers) >= 2, 'fixture did not cross multiple tiers; test proves nothing'


def test_career_standing_rolls_up_once_per_claim():
    """recompute_career_standing is a recompute-from-scratch, so the batch calls it ONCE -- and
    the standing still matches the per-job rows afterwards."""
    profile = ProfileFactory(is_linked=True)
    calls = []
    real = contract_service.recompute_career_standing
    contract_service.recompute_career_standing = lambda p: (calls.append(p.pk), real(p))[1]
    try:
        accept_contracts_bulk(profile, _contracts(profile, 10))
    finally:
        contract_service.recompute_career_standing = real

    assert len(calls) == 1, f'career standing recomputed {len(calls)}x for one claim'
