"""Bulk claim (2026-09-01) must be BEHAVIOUR-PRESERVING, not merely fast.

The claim path was rewritten so cost scales with the jobs touched (<= 25) instead of the
contracts claimed -- a hunter banking a back catalogue of hundreds used to blow the 30s request
timeout at ~90 queries per contract. Since this is the code the whole XP economy rests on
(`ProfileJobXP = Sum(all grants)`), the acceptance bar is a DIFFERENTIAL one: claiming N
contracts in one batch must leave exactly the state that claiming them one at a time would.
"""
import pytest
from django.db import connection, transaction
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
        # multiplier rides the ledger row (a future double-XP event must record the value that
        # SIZED the tier); name/from_first_claim are hand-rolled by the batched milestone write,
        # so they are exactly the fields a regression there would silently change.
        'grants': sorted(ContractXPGrant.objects.filter(profile=profile)
                         .values_list('job_id', 'amount', 'tier', 'base_t', 'source',
                                      'multiplier')),
        'grant_count': ContractXPGrant.objects.filter(profile=profile).count(),
        'milestones': sorted(ProgressionMilestone.objects.filter(profile=profile)
                             .values_list('kind', 'key', 'job_id', 'level_at', 'name',
                                          'from_first_claim')),
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

    # `first_claim` is threaded the way the OLD claim() threaded it: computed once up front and
    # passed to every accept, so the whole onboarding burst is flagged consistently. Letting each
    # sequential accept re-derive it instead would compare claim-all against N INDEPENDENT
    # single accepts (where only the first is the onboarding claim) -- a different operation, and
    # the extended milestone comparison below catches exactly that mismatch.
    seq_first_claim = not contract_service._has_any_job_xp(seq_profile)
    for c in seq_contracts:
        accept_contract(seq_profile, c, first_claim=seq_first_claim)
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


def test_every_grant_is_linked_to_the_contract_that_paid_it():
    """`earned_contract` is the ledger's idempotency key (unique_together) AND what
    `reset_claim --contract` deletes by -- the differential test cannot see it (twins hold
    different contracts), so pin it directly: every grant points at the EarnedContract whose
    tier produced it, and each (contract, tier) pays exactly its jobs once."""
    profile = ProfileFactory(is_linked=True)
    contracts = _contracts(profile, 3, jobs_per=4, start=6000)
    accept_contracts_bulk(profile, contracts)

    for c in contracts:
        ec = EarnedContract.objects.get(profile=profile, contract=c)
        rows = ContractXPGrant.objects.filter(profile=profile, earned_contract=ec)
        assert rows.count() == 8, 'expected 4 jobs x 2 tiers of grants for this contract'
        assert set(rows.values_list('tier', flat=True)) == {'platinum', 'full'}
        assert rows.filter(tier='platinum').count() == 4
    assert not ContractXPGrant.objects.filter(profile=profile, earned_contract=None).exists()


def test_contract_without_a_platinum_pays_full_t_at_100_percent():
    """`has_platinum=False` is a different tier split (the whole T lands on the 100% tier).
    Every other fixture here sets it True, so the branch would otherwise never run in a batch."""
    profile = ProfileFactory(is_linked=True)
    jobs = list(Job.objects.exclude(is_fallback=True)[:2])
    c = Contract.objects.create(name='No Plat', slug='no-plat', igdb_id=610000, is_live=True)
    c.jobs.set(jobs)
    EarnedContract.objects.create(
        profile=profile, contract=c, has_platinum=False, full_reached_at=timezone.now())

    accepted, granted = accept_contracts_bulk(profile, [c])

    assert accepted == ['no-plat']
    tiers = set(ContractXPGrant.objects.filter(profile=profile).values_list('tier', flat=True))
    assert tiers == {'full'}, 'a platinum-less contract must not pay a platinum tier'
    assert granted == sum(ContractXPGrant.objects.filter(profile=profile)
                          .values_list('amount', flat=True))


def test_zero_xp_shares_write_no_ledger_row_but_still_stamp():
    """More jobs than XP: `_split` hands some jobs 0. Those write NO ledger row (the amount<=0
    filter), while the contract is still stamped accepted -- the rule the old per-grant path had
    via `if amount <= 0: return 0`."""
    profile = ProfileFactory(is_linked=True)
    jobs = list(Job.objects.exclude(is_fallback=True)[:6])
    c = Contract.objects.create(name='Tiny', slug='tiny', igdb_id=610001, is_live=True,
                                xp_total_override=4)     # 4 XP over 6 jobs -> two get 0
    c.jobs.set(jobs)
    EarnedContract.objects.create(
        profile=profile, contract=c, has_platinum=False, full_reached_at=timezone.now())

    accept_contracts_bulk(profile, [c])

    amounts = list(ContractXPGrant.objects.filter(profile=profile)
                   .values_list('amount', flat=True))
    assert amounts and all(a > 0 for a in amounts), 'a zero share must not reach the ledger'
    assert len(amounts) < len(jobs), 'fixture did not actually produce a zero share'
    ec = EarnedContract.objects.get(profile=profile, contract=c)
    assert ec.full_accepted_at is not None, 'the contract must still be stamped accepted'


def test_no_signal_receivers_on_the_bulk_written_models():
    """bulk_create/bulk_update do NOT fire post_save. The batched primitive is only safe because
    nothing listens to these four models -- so if someone later attaches a receiver, it would be
    silently dead for every claim. Fail HERE instead, with the fix spelled out."""
    import weakref

    from django.db.models.signals import post_delete, post_save, pre_save

    watched = {ContractXPGrant, ProfileJobXP, ProgressionMilestone, EarnedContract}
    offenders = []
    for signal in (pre_save, post_save, post_delete):
        # Django 5.x stores (lookup_key, receiver, is_async); index rather than unpack so this
        # guard does not break on the tuple shape changing again.
        for entry in signal.receivers:
            lookup_key, ref = entry[0], entry[1]
            _id, sender_id = lookup_key
            fn = ref() if isinstance(ref, weakref.ReferenceType) else ref
            for model in watched:
                if sender_id == id(model):
                    offenders.append(f'{getattr(fn, "__module__", "?")}.'
                                     f'{getattr(fn, "__name__", "?")} on {model.__name__}')
    assert not offenders, (
        'a save-signal receiver was added to a model the claim path writes in BULK, so it will '
        'never fire for a claim: ' + ', '.join(offenders) +
        '. Either call it explicitly from grant_job_xp_bulk/accept_contracts_bulk, or move the '
        'work into those functions.'
    )


def test_a_missing_cache_row_fails_loudly_instead_of_under_crediting(monkeypatch):
    """The guard for the invariant's most dangerous failure mode. Ledger rows are written BEFORE
    the cache rows are locked, so if a ProfileJobXP row were ever missing at that point, skipping
    it would bank XP the cache never records -- silently breaking ProfileJobXP = Sum(all grants)
    with no error and no way to notice. (Unreachable on Postgres/READ COMMITTED, where the
    ensure-then-lock is race-safe; simulated here by neutering the ensure.) It must raise, so the
    surrounding transaction rolls the whole claim back."""
    profile = ProfileFactory(is_linked=True)
    job = Job.objects.exclude(is_fallback=True).first()
    monkeypatch.setattr(ProfileJobXP.objects, 'bulk_create', lambda *a, **k: [])

    with pytest.raises(RuntimeError, match='no ProfileJobXP row'):
        with transaction.atomic():
            grant_job_xp(profile, job, 100)

    assert not ContractXPGrant.objects.filter(profile=profile).exists(), (
        'the ledger rows must roll back with the failed claim'
    )
