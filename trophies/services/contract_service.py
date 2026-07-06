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
from django.db.models import Count, Q, Sum
from django.utils import timezone

from trophies.models import (
    Contract, ContractMembership, ContractXPGrant, EarnedContract, EarnedTrophy, Job, ProfileGame,
    ProfileJobXP, ProgressionMilestone, Trophy,
)
from trophies.util_modules.constants import CONTRACT_PLATINUM_FRAC, CONTRACT_XP_TOTAL
from trophies.util_modules.leveling import (
    frac_into_level, level_for_xp, pursuer_rank_for_level, ranks_crossed, tiers_crossed,
)


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


def _has_platinum(member_ids):
    """Do this Contract's member concept(s) define a platinum at all? Drives the tier
    fractions: games without a platinum pay the FULL T at 100% rather than the bonus
    fraction. Takes the member concept ids (loaded once by the caller)."""
    if not member_ids:
        return False
    return Trophy.objects.filter(game__concept_id__in=member_ids, trophy_type='platinum').exists()


def _has_any_job_xp(profile):
    """Has this profile ever banked job XP? The first accept from 0 XP is the onboarding claim, so
    its milestones get `from_first_claim` (the catch-up burst we can group/celebrate later)."""
    return ProfileJobXP.objects.filter(profile=profile, total_xp__gt=0).exists()


def _pursuer_level(profile):
    """Pursuer Level = sum of every job's level (level-1 floor for untouched jobs), matching
    job_render.build_profile_jobs' total_level so a logged rank crossing lines up with the display."""
    n_jobs = Job.objects.count()
    agg = ProfileJobXP.objects.filter(profile=profile).aggregate(s=Sum('level'), c=Count('id'))
    return (agg['s'] or 0) + (n_jobs - (agg['c'] or 0))


def _log_job_tier_milestones(profile, job, old_level, new_level, first_claim):
    """Log a ProgressionMilestone for each job prestige tier crossed old -> new (idempotent)."""
    for min_lvl, key, name in tiers_crossed(old_level, new_level):
        ProgressionMilestone.objects.get_or_create(
            profile=profile, kind=ProgressionMilestone.JOB_TIER, key=key, job=job,
            defaults={'name': name, 'level_at': min_lvl, 'from_first_claim': first_claim},
        )


def _log_rank_milestones(profile, old_level, new_level, first_claim):
    """Log a ProgressionMilestone for each Pursuer rank crossed old -> new (idempotent; no divisions)."""
    for min_lvl, key, name, _has_div in ranks_crossed(old_level, new_level):
        ProgressionMilestone.objects.get_or_create(
            profile=profile, kind=ProgressionMilestone.PURSUER_RANK, key=key, job=None,
            defaults={'name': name, 'level_at': min_lvl, 'from_first_claim': first_claim},
        )


def grant_job_xp(profile, job, amount, *, source='contract', source_id=None,
                 tier=None, base_t=None, multiplier=None, earned_contract=None, first_claim=False):
    """The SINGLE job-XP grant primitive: write one immutable ledger row + bump the
    ProfileJobXP cache (re-leveling under the flat curve). Used by contract accepts AND any
    future source (quests, double-XP events, manual). Keeping all XP flowing through here
    is what makes ProfileJobXP = Sum(all grants) hold for every source -- and the single place
    every job prestige-tier crossing is logged.

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
    old_level = level_for_xp(pjx.total_xp)   # logical level before this grant (floor 1; never logs Initiate)
    pjx.total_xp += amount
    pjx.level = level_for_xp(pjx.total_xp)
    pjx.save(update_fields=['total_xp', 'level', 'updated_at'])
    if pjx.level > old_level:
        _log_job_tier_milestones(profile, job, old_level, pjx.level, first_claim)
    return amount


def home_contract_for_concept(concept):
    """Resolve a Concept's home Contract (membership, then bundle satisfier)."""
    m = ContractMembership.objects.filter(concept=concept).select_related('contract').first()
    if m:
        return m.contract
    return Contract.objects.filter(bundles__concepts=concept).first()


def _detect_tiers(profile, contract, member_ids):
    """(platinum_reached, full_reached) for this profile on this Contract.

    Platinum = the user earned the platinum on any member concept. 100% = any member
    concept (or a fully-cleared bundle) at progress 100. Completing any one version
    variant counts. Collapsed to two bounded `.exists()` queries over the member set
    (+ one per bundle) so the sync hot path doesn't fan out a query per concept.
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
    if not full_reached:
        for bundle in contract.bundles.all():
            bundle_ids = set(bundle.concepts.values_list('id', flat=True))
            if not bundle_ids:
                continue
            completed = set(
                ProfileGame.objects
                .filter(profile=profile, game__concept_id__in=bundle_ids, progress=100)
                .values_list('game__concept_id', flat=True)
            )
            if bundle_ids <= completed:
                full_reached = True
                break
    return platinum_reached, full_reached


# --- gate 1: detection (sync) ---------------------------------------------

def mark_contract_reached(profile, contract):
    """Detection only: stamp newly-reached tiers so the reward becomes claimable.
    Grants NO XP. Returns the EarnedContract if anything changed, else None."""
    member_ids = list(contract.memberships.values_list('concept_id', flat=True))
    platinum_reached, full_reached = _detect_tiers(profile, contract, member_ids)
    if not (platinum_reached or full_reached):
        return None

    ec, _created = EarnedContract.objects.get_or_create(
        profile=profile, contract=contract,
        defaults={'has_platinum': _has_platinum(member_ids)},
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
        contracts = set(
            m.contract for m in
            ContractMembership.objects.filter(concept__in=concept_list).select_related('contract')
        )
        contracts.update(Contract.objects.filter(bundles__concepts__in=concept_list).distinct())
    else:
        contracts = Contract.objects.filter(is_live=True)
    for contract in contracts:
        mark_contract_reached(profile, contract)


# --- gate 2: acceptance (user action) -------------------------------------

_ACCEPTED_FIELD = {'platinum': 'platinum_accepted_at', 'full': 'full_accepted_at'}


def _pending_tiers(ec, contract):
    """(tiers, t, multiplier) for an EarnedContract, where tiers is the reached-but-unaccepted
    [(tier, tier_total_xp), ...].

    The pure XP computation shared by accept_contract (which then grants + stamps the accepted
    fields) and claimable_summary (which totals + previews) -- no side effects, no queries. The
    100% tier is (grand total - platinum tier), NOT its fraction rounded independently, so
    platinum + full always sum to exactly the grand total even at a .5 rounding boundary. Games
    with no platinum pay the FULL T at 100%. has_platinum is the value frozen on the
    EarnedContract at first reach (see model + audit B1). Returns the `t` + `multiplier` it used
    so the caller stamps the grant ledger with the SAME multiplier that sized the tiers -- the two
    must not diverge (a future dynamic `_active_multiplier` could otherwise size a tier under one
    value and record another across an event boundary)."""
    t = contract.xp_total_override or CONTRACT_XP_TOTAL
    multiplier = _active_multiplier()
    grand_total = _tier_total(1.0, t, multiplier)
    platinum_total = _tier_total(CONTRACT_PLATINUM_FRAC, t, multiplier) if ec.has_platinum else 0
    tiers = []
    if ec.has_platinum and ec.platinum_reached_at and ec.platinum_accepted_at is None:
        tiers.append(('platinum', platinum_total))
    if ec.full_reached_at and ec.full_accepted_at is None:
        tiers.append(('full', (grand_total - platinum_total) if ec.has_platinum else grand_total))
    return tiers, t, multiplier


@transaction.atomic
def accept_contract(profile, contract, *, first_claim=None):
    """User action: bank ALL of this Contract's claimable tiers at once (Platinum + 100%
    together when both are reached). Writes the ledger + bumps the cache. Idempotent --
    already-accepted tiers are skipped. Returns total XP granted.

    Logs any Pursuer-rank crossing this accept causes (job-tier crossings are logged inside
    grant_job_xp). `first_claim` is passed by a bulk accept so the whole onboarding claim-all is
    flagged consistently; call-alone leaves it None and it's derived (prior XP == 0)."""
    ec = EarnedContract.objects.select_for_update().filter(profile=profile, contract=contract).first()
    if ec is None:
        return 0
    jobs = list(contract.jobs.all())
    if not jobs:
        return 0

    tiers, t, multiplier = _pending_tiers(ec, contract)   # reached-but-unaccepted + the T/mult used
    if not tiers:
        return 0

    now = timezone.now()
    if first_claim is None:
        first_claim = not _has_any_job_xp(profile)
    old_pursuer_level = _pursuer_level(profile)

    # All grants go through the shared primitive (ledger row + row-locked cache bump + job-tier
    # milestone logging), so contracts/quests/events stay consistent and ProfileJobXP = Sum(all grants).
    granted = 0
    for tier, tier_total in tiers:
        for job, amount in zip(jobs, _split(tier_total, len(jobs))):
            granted += grant_job_xp(
                profile, job, amount, source='contract', tier=tier,
                base_t=t, multiplier=multiplier, earned_contract=ec, first_claim=first_claim,
            )
        setattr(ec, _ACCEPTED_FIELD[tier], now)

    ec.save(update_fields=['platinum_accepted_at', 'full_accepted_at'])
    _log_rank_milestones(profile, old_pursuer_level, _pursuer_level(profile), first_claim)
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


def claimable_summary(profile, peek=3):
    """Cheap Home-glance summary of a profile's pending Contract rewards: the count, the total XP
    waiting, a highest-XP-first peek, and how many more sit beyond the peek. Bounded by the curated
    Contract catalog (dozens at most, never trophy-scale), so the per-row loop is whale-safe -- it
    costs one select_related query + Decimal arithmetic, no per-row queries (_pending_tiers is pure
    and `contract` is already joined)."""
    rows = []
    total = 0
    for ec in claimable_contracts(profile):
        tiers, _t, _mult = _pending_tiers(ec, ec.contract)
        xp = sum(amount for _tier, amount in tiers)
        if xp <= 0:
            continue
        total += xp
        rows.append({'name': ec.contract.name, 'xp': xp})
    rows.sort(key=lambda r: -r['xp'])
    return {
        'count': len(rows),
        'total_xp': total,
        'items': rows[:peek],
        'more': max(0, len(rows) - peek),
    }


@transaction.atomic
def accept_contracts(profile, contracts=None):
    """Bulk accept (QoL): accept every claimable Contract (or a given list) in ONE
    transaction (all-or-nothing). Contracts are locked in pk order to avoid deadlocks
    across overlapping bulk accepts. Returns total XP."""
    if contracts is None:
        contracts = [ec.contract for ec in claimable_contracts(profile)]
    contracts = sorted(contracts, key=lambda c: c.pk)
    # Decide first-claim ONCE for the whole bulk (prior XP == 0), so every contract in the onboarding
    # claim-all flags its milestones the same way -- not just the first before XP starts landing.
    first_claim = not _has_any_job_xp(profile)
    return sum(accept_contract(profile, c, first_claim=first_claim) for c in contracts)


# --- claim (the ceremony-facing accept) ------------------------------------

def _levels_snapshot(profile, job_ids):
    """{job_id: (logical level, total_xp)} for the given (bounded) jobs; (1, 0) floor for untouched."""
    snap = {jid: (1, 0) for jid in job_ids}
    for pjx in ProfileJobXP.objects.filter(profile=profile, job_id__in=job_ids).only('job_id', 'total_xp'):
        snap[pjx.job_id] = (level_for_xp(pjx.total_xp), pjx.total_xp)
    return snap


def _empty_claim():
    """A fresh, full-shape empty payload (same keys as the success path so callers see one shape).
    A function, not a module constant, so each caller gets its own `accepted`/`jobs` lists."""
    return {'xp': 0, 'accepted': [], 'first_claim': False, 'rank_now': '', 'jobs': [], 'pursuer': None}


@transaction.atomic
def claim(profile, *, contract=None, all_claimable=False):
    """The ceremony-facing accept: bank the claimable XP (one Contract or every claimable one) and
    return the full 'what just happened' payload that drives the claim animation -- per-job level
    deltas + the tier/rank crossings, derived from before/after snapshots. Wraps the existing
    accept_contract (idempotent, ledger-backed, milestone-logging); adds no new writes of its own.

    The payload:
        {xp, accepted:[slug], first_claim, rank_now,
         jobs:[{slug, name, disc, icon, xp, from_level, to_level, from_frac, to_frac,
                tiers:[{key,name}]}],   # every job the claim gave XP to (bar fills; may or may not level)
         pursuer:{from_level, to_level, ranks:[{key,name}]}}
    All work is bounded to the claimed Contracts' jobs (never the user's library)."""
    if all_claimable:
        contracts = [ec.contract for ec in claimable_contracts(profile)]
    elif contract is not None:
        contracts = [contract]
    else:
        contracts = []
    contracts = sorted(contracts, key=lambda c: c.pk)
    if not contracts:
        return _empty_claim()

    job_by_id = {j.pk: j for c in contracts for j in c.jobs.all()}   # Job PK is its slug
    first_claim = not _has_any_job_xp(profile)
    pre = _levels_snapshot(profile, job_by_id.keys())
    pre_pursuer = _pursuer_level(profile)

    accepted, total = [], 0
    for c in contracts:
        granted = accept_contract(profile, c, first_claim=first_claim)
        if granted > 0:
            accepted.append(c.slug)
            total += granted
    if not accepted:
        return _empty_claim()

    post = _levels_snapshot(profile, job_by_id.keys())
    post_pursuer = _pursuer_level(profile)

    jobs = []
    for jid, job in job_by_id.items():
        (frm_lvl, frm_xp), (to_lvl, to_xp) = pre[jid], post[jid]
        if to_xp <= frm_xp:
            continue   # this job received no XP from the claimed Contracts
        jobs.append({
            'slug': job.slug, 'name': job.name, 'disc': job.discipline, 'icon': job.icon,
            'xp': to_xp - frm_xp,                      # XP this claim gave the job (the "+N" on its tile)
            'from_level': frm_lvl, 'to_level': to_lvl,
            'from_frac': frac_into_level(frm_xp),      # where the bar starts / lands within each level band
            'to_frac': frac_into_level(to_xp),
            'tiers': [{'key': k, 'name': n} for _lvl, k, n in tiers_crossed(frm_lvl, to_lvl)],
        })
    jobs.sort(key=lambda j: (j['to_level'] - j['from_level'], j['xp']), reverse=True)   # biggest promotions, then XP
    return {
        'xp': total,
        'accepted': accepted,
        'first_claim': first_claim,
        'rank_now': pursuer_rank_for_level(post_pursuer)['label'],
        'jobs': jobs,
        'pursuer': {
            'from_level': pre_pursuer, 'to_level': post_pursuer,
            'ranks': [{'key': k, 'name': n} for _lvl, k, n, _hd in ranks_crossed(pre_pursuer, post_pursuer)],
        },
    }


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
