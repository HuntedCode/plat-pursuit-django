"""Contract (job/element) XP engine.

Two gates per Contract (see docs/design/rebuild/job-board-contracts.md + the plan
cheerful-snacking-wozniak.md):

  1. REACHED  -- detected automatically on sync (`mark_contract_reached` /
     `check_profile_contracts`). Stamps EarnedContract.*_reached_at. Grants NO XP;
     it only makes the reward claimable.
  2. ACCEPTED -- the user banks the reward (`accept_contract` / `accept_contracts`).
     ONE accept per Contract grants ALL of its claimable tiers at once (Platinum +
     100% together = full XP, one click), writing the immutable ContractXPGrant ledger
     and bumping the ProfileJobXP cache.

Every Contract pays the same global total T (override via Contract.xp_total_override),
split evenly among its jobs, across the Platinum (bulk) and 100% (bonus) tiers. Games
with no platinum pay the FULL T at 100%. The recorded grant amount is permanent (never
recomputed from current config). Per-job totals always aggregate in the DB (whale-OOM rule).
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from trophies.models import (
    Concept, Contract, ContractXPGrant, EarnedContract, EarnedTrophy, IGDBMatch, ProfileGame,
    ProfileJobXP, Trophy,
)
from trophies.util_modules.constants import CONTRACT_PLATINUM_FRAC, CONTRACT_XP_TOTAL
from trophies.util_modules.leveling import level_for_xp


# --- helpers ---------------------------------------------------------------

def _split(total, n):
    """Split `total` XP into `n` even integer shares; remainder to the first jobs."""
    if n <= 0:
        return []
    base = total // n
    rem = total - base * n
    return [base + (1 if i < rem else 0) for i in range(n)]


def _tier_total(frac, t, multiplier):
    """XP for one tier, computed in Decimal end-to-end (ROUND_HALF_UP) so grants are
    deterministic and audit-reproducible. Relies on the invariant PLATINUM_FRAC +
    FULL_FRAC == 1.0 so the two tiers together pay T."""
    return int((Decimal(str(frac)) * t * multiplier).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _active_multiplier():
    """Active XP multiplier (the hook for double-XP events). Default 1.00."""
    return Decimal('1.00')


def _has_platinum(contract, member_ids):
    """Does this Contract define a platinum at all -- across its member concepts OR its
    satisfier bundles? Drives the tier fractions: contracts with no platinum anywhere pay
    the FULL T at 100% rather than the bonus fraction.

    Bundle concepts MUST count here: _detect_tiers can set platinum_reached purely via a
    fully-platted satisfier bundle (a multi-game collection), so freezing has_platinum
    from members alone would strand such a contract as permanently claimable -- the accept
    gate only banks the platinum tier when has_platinum is True, so a reached-but-not-
    has_platinum contract shows claimable forever and re-accepting grants nothing."""
    concept_ids = set(member_ids)
    concept_ids.update(contract.bundles.values_list('concepts__id', flat=True))
    concept_ids.discard(None)
    if not concept_ids:
        return False
    return Trophy.objects.filter(game__concept_id__in=concept_ids, trophy_type='platinum').exists()


def grant_job_xp(profile, job, amount, *, source='contract', source_id=None,
                 tier=None, base_t=None, multiplier=None, earned_contract=None):
    """The SINGLE job-XP grant primitive: write one immutable ledger row + bump the
    ProfileJobXP cache (re-leveling under the flat curve). Used by contract accepts AND any
    future source (quests, double-XP events, manual). Keeping all XP flowing through here
    is what makes ProfileJobXP = Sum(all grants) hold for every source.

    Caller owns idempotency + the surrounding transaction. ProfileJobXP is row-locked here
    because it's shared across all of a profile's grants. Returns the amount (0 if <= 0).
    """
    if amount <= 0:
        return 0
    if multiplier is None:
        multiplier = Decimal('1.00')
    ContractXPGrant.objects.create(
        profile=profile, job=job, amount=amount, multiplier=multiplier,
        source=source, source_id=source_id,
        earned_contract=earned_contract, tier=tier, base_t=base_t,
    )
    ProfileJobXP.objects.get_or_create(profile=profile, job=job)  # race-safe create
    pjx = ProfileJobXP.objects.select_for_update().get(profile=profile, job=job)
    pjx.total_xp += amount
    pjx.level = level_for_xp(pjx.total_xp)
    pjx.save(update_fields=['total_xp', 'level', 'updated_at'])
    return amount


def home_contract_for_concept(concept):
    """Resolve a Concept's Contract: the Contract keyed on its raw IGDB id (only for ANCHORED
    concepts), then a bundle satisfier (episodic) as a fallback."""
    if concept.anchor_migration_completed_at is not None:
        try:
            igdb_id = concept.igdb_match.igdb_id
        except IGDBMatch.DoesNotExist:
            igdb_id = None
        if igdb_id is not None:
            hit = Contract.objects.filter(igdb_id=igdb_id).first()
            if hit:
                return hit
    return Contract.objects.filter(bundles__concepts=concept).first()


def _detect_tiers(profile, contract, member_ids):
    """(platinum_reached, full_reached) for this profile on this Contract.

    Platinum = the user earned the platinum on any member concept, OR fully platinum'd
    a satisfier bundle (every concept in the bundle platted -- e.g. a multi-game
    collection whose one platinum stands in for the games it covers). 100% = any member
    concept, OR a fully-cleared bundle, at progress 100. Completing any one version
    variant counts. Bounded to two `.exists()` queries over the member set plus up to
    two set-membership queries per bundle, short-circuiting once both tiers are reached,
    so the sync hot path doesn't fan out a query per concept.
    """
    platinum_reached = full_reached = False
    if member_ids:
        platinum_reached = EarnedTrophy.objects.filter(
            profile=profile, earned=True,
            trophy__trophy_type='platinum', trophy__game__concept_id__in=member_ids,
        ).exists()
        full_reached = ProfileGame.objects.filter(
            profile=profile, game__concept_id__in=member_ids, progress=100,
        ).exists()
    if not (platinum_reached and full_reached):
        for bundle in contract.bundles.all():
            if platinum_reached and full_reached:
                break
            bundle_ids = set(bundle.concepts.values_list('id', flat=True))
            if not bundle_ids:
                continue
            if not full_reached:
                completed = set(
                    ProfileGame.objects
                    .filter(profile=profile, game__concept_id__in=bundle_ids, progress=100)
                    .values_list('game__concept_id', flat=True)
                )
                if bundle_ids <= completed:
                    full_reached = True
            if not platinum_reached:
                platted = set(
                    EarnedTrophy.objects
                    .filter(profile=profile, earned=True, trophy__trophy_type='platinum',
                            trophy__game__concept_id__in=bundle_ids)
                    .values_list('trophy__game__concept_id', flat=True)
                )
                if bundle_ids <= platted:
                    platinum_reached = True
    return platinum_reached, full_reached


# --- gate 1: detection (sync) ---------------------------------------------

def mark_contract_reached(profile, contract):
    """Detection only: stamp newly-reached tiers so the reward becomes claimable.
    Grants NO XP. Returns the EarnedContract if anything changed, else None."""
    member_ids = contract.member_concept_ids()
    platinum_reached, full_reached = _detect_tiers(profile, contract, member_ids)
    if not (platinum_reached or full_reached):
        return None

    ec, _created = EarnedContract.objects.get_or_create(
        profile=profile, contract=contract,
        defaults={'has_platinum': _has_platinum(contract, member_ids)},
    )
    changed = []
    now = timezone.now()
    if platinum_reached and ec.platinum_reached_at is None:
        ec.platinum_reached_at = now
        changed.append('platinum_reached_at')
    if full_reached and ec.full_reached_at is None:
        ec.full_reached_at = now
        changed.append('full_reached_at')
    if changed:
        ec.save(update_fields=changed)
    return ec


def check_profile_contracts(profile, concepts=None):
    """Sync hook: mark reached tiers for the Contracts of the given completed concepts
    (or all live Contracts if none given). Detection only -- never grants."""
    if concepts is not None:
        concept_list = list(concepts)
        if not concept_list:
            return
        # Contracts keyed on the completed ANCHORED concepts' raw IGDB ids, plus any episodic
        # bundle they satisfy.
        igdb_ids = list(
            Concept.objects.filter(id__in=concept_list, anchor_migration_completed_at__isnull=False)
            .values_list('igdb_match__igdb_id', flat=True)
        )
        igdb_ids = [i for i in igdb_ids if i is not None]
        contracts = set(Contract.objects.filter(igdb_id__in=igdb_ids)) if igdb_ids else set()
        contracts.update(Contract.objects.filter(bundles__concepts__in=concept_list).distinct())
    else:
        contracts = Contract.objects.filter(is_live=True)
    for contract in contracts:
        mark_contract_reached(profile, contract)


# --- gate 2: acceptance (user action) -------------------------------------

@transaction.atomic
def accept_contract(profile, contract):
    """User action: bank ALL of this Contract's claimable tiers at once (Platinum + 100%
    together when both are reached). Writes the ledger + bumps the cache. Idempotent --
    already-accepted tiers are skipped. Returns total XP granted."""
    ec = EarnedContract.objects.select_for_update().filter(profile=profile, contract=contract).first()
    if ec is None:
        return 0
    jobs = list(contract.jobs.all())
    if not jobs:
        return 0

    has_platinum = ec.has_platinum  # frozen at first reach (see model + audit B1)
    t = contract.xp_total_override or CONTRACT_XP_TOTAL
    multiplier = _active_multiplier()
    now = timezone.now()

    # Compute the 100% tier as (grand total - platinum tier) rather than rounding its
    # fraction independently, so platinum + full always sum to exactly the grand total
    # even when xp_total_override or the multiplier produce a .5 rounding boundary.
    grand_total = _tier_total(1.0, t, multiplier)
    platinum_total = _tier_total(CONTRACT_PLATINUM_FRAC, t, multiplier) if has_platinum else 0

    tiers = []  # (tier, tier_total, accepted_field)
    if has_platinum and ec.platinum_reached_at and ec.platinum_accepted_at is None:
        tiers.append(('platinum', platinum_total, 'platinum_accepted_at'))
    if ec.full_reached_at and ec.full_accepted_at is None:
        # No-platinum games pay the full T at 100%; otherwise the remainder bonus.
        full_total = (grand_total - platinum_total) if has_platinum else grand_total
        tiers.append(('full', full_total, 'full_accepted_at'))
    if not tiers:
        return 0

    # All grants go through the shared primitive (ledger row + row-locked cache bump),
    # so contracts/quests/events stay consistent and ProfileJobXP = Sum(all grants).
    granted = 0
    for tier, tier_total, field in tiers:
        for job, amount in zip(jobs, _split(tier_total, len(jobs))):
            granted += grant_job_xp(
                profile, job, amount, source='contract', tier=tier,
                base_t=t, multiplier=multiplier, earned_contract=ec,
            )
        setattr(ec, field, now)

    ec.save(update_fields=['platinum_accepted_at', 'full_accepted_at'])
    return granted


def claimable_contracts(profile):
    """EarnedContracts with a reached-but-unaccepted tier (the pending rewards)."""
    return (
        EarnedContract.objects.filter(profile=profile)
        .filter(
            Q(platinum_reached_at__isnull=False, platinum_accepted_at__isnull=True)
            | Q(full_reached_at__isnull=False, full_accepted_at__isnull=True)
        )
        .select_related('contract')
    )


@transaction.atomic
def accept_contracts(profile, contracts=None):
    """Bulk accept (QoL): accept every claimable Contract (or a given list) in ONE
    transaction (all-or-nothing). Contracts are locked in pk order to avoid deadlocks
    across overlapping bulk accepts. Returns total XP."""
    if contracts is None:
        contracts = [ec.contract for ec in claimable_contracts(profile)]
    contracts = sorted(contracts, key=lambda c: c.pk)
    return sum(accept_contract(profile, c) for c in contracts)


# --- cache repair ----------------------------------------------------------

def recompute_profile_job_xp(profile):
    """Rebuild the ProfileJobXP cache from the ContractXPGrant ledger (DB aggregation,
    never Python iteration). Returns the {job_id: total_xp} it wrote."""
    sums = dict(
        ContractXPGrant.objects.filter(profile=profile)
        .values('job').annotate(total=Sum('amount')).values_list('job', 'total')
    )
    existing = {pjx.job_id: pjx for pjx in ProfileJobXP.objects.filter(profile=profile)}

    for job_id, total in sums.items():
        pjx = existing.get(job_id) or ProfileJobXP(profile=profile, job_id=job_id)
        pjx.total_xp = total or 0
        pjx.level = level_for_xp(pjx.total_xp)
        pjx.save()

    floor_level = level_for_xp(0)  # 1 (the level-1 floor)
    for job_id, pjx in existing.items():
        if job_id not in sums and (pjx.total_xp or pjx.level != floor_level):
            pjx.total_xp = 0
            pjx.level = floor_level
            pjx.save(update_fields=['total_xp', 'level', 'updated_at'])

    return sums
