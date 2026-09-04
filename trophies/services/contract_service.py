"""Contract (job/element) XP engine.

Two gates per Contract (see docs/design/rebuild/job-board-contracts.md + the plan
cheerful-snacking-wozniak.md):

  1. REACHED  -- detected automatically on sync (`mark_contract_reached` /
     `check_profile_contracts`). Stamps EarnedContract.*_reached_at. Grants NO XP;
     it only makes the reward claimable.
  2. ACCEPTED -- the user banks the reward (`accept_contracts_bulk`, via `claim` /
     `accept_contract` / `accept_contracts`).
     ONE accept per Contract grants ALL of its claimable tiers at once (Platinum +
     100% together = full XP, one click), writing the immutable ContractXPGrant ledger
     and bumping the ProfileJobXP cache.

A third path, RECONCILIATION (`credit_is_orphaned` / `revoke_contract`), is the engine's only
subtractive one. Membership is derived live from the anchored IGDB id and can change under a
hunter's feet (a concept split, a re-anchor, a lost trusted match), which the forward-only gates
above cannot see. Staff-triggered per Contract via `manage.py reconcile_contracts`; never on cron.

Every Contract pays the same global total T (override via Contract.xp_total_override),
split evenly among its jobs, across the Platinum (bulk) and 100% (bonus) tiers. Games
with no platinum pay the FULL T at 100%. The recorded grant amount is permanent (never
recomputed from current config). Per-job totals always aggregate in the DB (whale-OOM rule).
"""
import logging
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Count, Q, Sum, prefetch_related_objects
from django.utils import timezone

from trophies.models import (
    Concept, Contract, ContractXPGrant, EarnedContract, EarnedTrophy, IGDBMatch, Job, ProfileGame,
    ProfileJobXP, ProgressionMilestone, Trophy,
)
from trophies.util_modules.constants import CONTRACT_PLATINUM_FRAC, CONTRACT_XP_TOTAL
from trophies.util_modules.leveling import (
    frac_into_level, level_for_xp, next_rank_floor, pursuer_rank_for_level, pursuer_rank_ladder,
    ranks_crossed, tier_for_level, tier_rank, tiers_crossed,
)


logger = logging.getLogger(__name__)


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


def _has_any_job_xp(profile):
    """Has this profile ever banked job XP? The first accept from 0 XP is the onboarding claim, so
    its milestones get `from_first_claim` (the catch-up burst we can group/celebrate later)."""
    return ProfileJobXP.objects.filter(profile=profile, total_xp__gt=0).exists()


def catalogue_job_count():
    """How many jobs a Pursuer Level spans.

    Scoped to the five real disciplines, matching `job_render.DISCIPLINE_LABELS`, which is what the
    DISPLAY iterates: `build_profile_jobs` walks the discipline buckets, so a Job whose discipline
    is not one of the five is invisible there. `Job.discipline` has `choices` but no DB constraint,
    so a badly-seeded row is possible -- and a bare `Job.objects.count()` here would then re-split
    the definition (display counting 25, the stored figure counting 26) with nothing raising. Read
    off `Job.DISCIPLINES` rather than importing job_render's dict, so the model stays the source.
    """
    return Job.objects.filter(discipline__in=[key for key, _label in Job.DISCIPLINES]).count()


def pursuer_level_from(level_sum, row_count, n_jobs):
    """Pursuer Level from a (Sum(level), Count(rows)) pair over a profile's ProfileJobXP.

    THE FLOOR IS THE WHOLE POINT, and it is why this is a shared function rather than an inlined
    `Sum('level')`. A hunter has a level in every one of the ~25 jobs from the moment they exist --
    an untouched job sits at level 1, not level 0 -- but ProfileJobXP only materializes a row once
    a job is actually paid. So `Sum(level)` alone counts only the jobs they have touched, and the
    missing rows have to be added back at their level-1 floor.

    This is not cosmetic. `PURSUER_RANKS` is explicitly calibrated against the floored scale
    ("Pursuer Level ~= 25 floor + 2 per game"), so an unfloored figure lands a hunter one or two
    ranks below where they actually are. It drifted exactly once: `recompute_career_standing`
    summed the rows it had while `build_profile_jobs` floored across the catalogue, so the Career
    XP leaderboard's "level" column and the hunter's own Career page showed different numbers for
    the same hunter. Both now come through here.

    `max(..., 0)` guards nothing reachable today (Job is CASCADE, so a deleted Job takes its rows
    with it) but keeps a catalogue edit mid-flight from producing a negative level.
    """
    return (level_sum or 0) + max(n_jobs - (row_count or 0), 0)


def _pursuer_level(profile):
    """Pursuer Level for a profile, read live from ProfileJobXP (2 queries).

    The same figure `ProfileCareerStanding.pursuer_level` materializes and `build_profile_jobs`
    computes for display -- see `pursuer_level_from` for the floor rule they all share."""
    agg = ProfileJobXP.objects.filter(profile=profile).aggregate(s=Sum('level'), c=Count('id'))
    return pursuer_level_from(agg['s'], agg['c'], catalogue_job_count())


def _log_rank_milestones(profile, old_level, new_level, first_claim):
    """Log a ProgressionMilestone for each Pursuer rank crossed old -> new (idempotent; no divisions)."""
    for min_lvl, key, name, _has_div in ranks_crossed(old_level, new_level):
        ProgressionMilestone.objects.get_or_create(
            profile=profile, kind=ProgressionMilestone.PURSUER_RANK, key=key, job=None,
            defaults={'name': name, 'level_at': min_lvl, 'from_first_claim': first_claim},
        )


def grant_job_xp(profile, job, amount, *, source='contract', source_id=None,
                 tier=None, base_t=None, multiplier=None, earned_contract=None, first_claim=False):
    """Grant XP for ONE job: a thin wrapper over `grant_job_xp_bulk` called with a list of one.

    The BATCHED version is the primitive, so there is exactly one implementation of "how XP is
    granted" regardless of how many grants a caller has -- the property the ledger invariant
    (ProfileJobXP = Sum(all grants)) depends on. Caller owns idempotency + the surrounding
    transaction. Returns the amount (0 if <= 0).
    """
    return grant_job_xp_bulk(profile, [{
        'job': job, 'amount': amount, 'source': source, 'source_id': source_id,
        'tier': tier, 'base_t': base_t, 'multiplier': multiplier,
        'earned_contract': earned_contract,
    }], first_claim=first_claim)


def grant_job_xp_bulk(profile, grants, *, first_claim=False):
    """The SINGLE job-XP grant primitive, batched: write the immutable ledger rows + bump each
    touched ProfileJobXP cache row ONCE (re-leveling under the flat curve). Used by contract
    accepts AND any future source (quests, double-XP events, manual). Keeping all XP flowing
    through here is what makes ProfileJobXP = Sum(all grants) hold for every source -- and the
    single place every job prestige-tier crossing is logged.

    `grants` is an iterable of dicts: {job, amount, source, source_id, tier, base_t, multiplier,
    earned_contract}. Non-positive amounts are dropped and never reach the ledger.

    WHY BATCHED (2026-09-01): granting one-at-a-time cost ~90 queries per contract, so once the
    contract catalogue grew past a few hundred, a hunter banking their back catalogue blew the
    30s request timeout. Nothing about the model had to change -- the real work is per JOB
    (<= 25 rows), not per grant:
      - ledger rows go out in one bulk_create instead of N inserts;
      - each ProfileJobXP row is locked, summed and written ONCE, not once per grant;
      - `recompute_career_standing` is a recompute-from-scratch, so running it once at the end
        is exactly equivalent to running it N times (it was the single biggest cost);
      - level milestones stay correct because `tiers_crossed(old, new)` returns EVERY tier in
        the span, so a job jumping 1 -> 40 in one claim logs the same rows it would have logged
        crossing them one grant at a time.
    Caller owns idempotency + the surrounding transaction. Returns the total XP granted.
    """
    rows, totals, job_by_id = [], {}, {}
    for g in grants:
        amount = g.get('amount') or 0
        if amount <= 0:
            continue
        job = g['job']
        multiplier = g.get('multiplier')
        if multiplier is None:
            multiplier = Decimal('1.00')
        rows.append(ContractXPGrant(
            profile=profile, job=job, amount=amount, multiplier=multiplier,
            source=g.get('source', 'contract'), source_id=g.get('source_id'),
            earned_contract=g.get('earned_contract'), tier=g.get('tier'),
            base_t=g.get('base_t'),
        ))
        totals[job.pk] = totals.get(job.pk, 0) + amount
        job_by_id[job.pk] = job
    if not rows:
        return 0

    ContractXPGrant.objects.bulk_create(rows, batch_size=1000)

    # Stamped at BIRTH. Both mirrors default empty/False, and the propagation signal fires only when the
    # Profile value CHANGES -- so a row created after a hunter's last country move (or after they linked)
    # would keep the defaults forever. For `country_code` that quietly dropped them from the
    # country-sliced job board; for `is_linked` it would drop them from the job board entirely.
    have = set(ProfileJobXP.objects.filter(
        profile=profile, job_id__in=totals.keys()).values_list('job_id', flat=True))
    missing = [
        ProfileJobXP(
            profile=profile, job=job_by_id[jid],
            country_code=getattr(profile, 'country_code', '') or '',
            is_linked=bool(getattr(profile, 'is_linked', False)),
        )
        for jid in totals if jid not in have
    ]
    if missing:
        ProfileJobXP.objects.bulk_create(missing, ignore_conflicts=True)   # race-safe create

    now = timezone.now()
    # ORDER BY is load-bearing, not tidiness: locking the profile's job rows in a deterministic
    # order is what makes two concurrent claims for the same hunter (a double-clicked Claim All,
    # or a sync granting XP alongside) queue instead of deadlock. The per-grant version got this
    # for free by locking one row at a time in Job.Meta.ordering; a batched `IN` lock can
    # otherwise be acquired in whatever order the planner picks.
    pjx_rows = list(ProfileJobXP.objects.select_for_update()
                    .filter(profile=profile, job_id__in=totals.keys())
                    .order_by('job_id'))
    if len(pjx_rows) != len(totals):
        # Unreachable on Postgres/READ COMMITTED (the ensure above is race-safe), but if it ever
        # happens the ledger rows are already written, so a missing cache row would silently
        # under-credit the hunter and break ProfileJobXP = Sum(all grants) with no signal. Fail
        # loudly instead: the surrounding atomic block rolls the whole claim back.
        missing_ids = set(totals) - {p.job_id for p in pjx_rows}
        raise RuntimeError(
            f'grant_job_xp_bulk: no ProfileJobXP row for {sorted(missing_ids)} '
            f'(profile={profile.pk}); refusing to bank XP the cache cannot record.'
        )
    crossings = []
    for pjx in pjx_rows:
        old_level = level_for_xp(pjx.total_xp)   # logical level before this claim (floor 1; never Initiate)
        pjx.total_xp += totals[pjx.job_id]
        pjx.level = level_for_xp(pjx.total_xp)
        pjx.updated_at = now                     # bulk_update does not fire auto_now
        if pjx.level > old_level:
            crossings.append((job_by_id[pjx.job_id], old_level, pjx.level))
    ProfileJobXP.objects.bulk_update(
        pjx_rows, ['total_xp', 'level', 'updated_at'], batch_size=100)
    # Milestones in ONE write. `uniq_milestone_job` (profile, kind, key, job) makes
    # ignore_conflicts exactly the get_or_create semantics `_log_job_tier_milestones` uses --
    # existing rows are left untouched, so a re-claim adds nothing. Doing it per tier was the
    # last thing that grew with the size of the claim (more contracts -> more levels -> more
    # crossings), though it was always bounded by tiers x jobs rather than by contracts.
    milestone_rows = [
        ProgressionMilestone(
            profile=profile, kind=ProgressionMilestone.JOB_TIER, key=key, job=job,
            name=name, level_at=min_lvl, from_first_claim=first_claim,
        )
        for job, old_level, new_level in crossings
        for min_lvl, key, name in tiers_crossed(old_level, new_level)
    ]
    if milestone_rows:
        ProgressionMilestone.objects.bulk_create(milestone_rows, ignore_conflicts=True)

    # The Career XP board's roll-up rides THIS primitive, not the ledger-rebuild function, because this
    # is the seam every grant actually passes through -- contract accepts, quests, events, manual awards.
    # Hooking it to `recompute_profile_job_xp` instead (as it first was) meant the board only moved when a
    # management command was run by hand: a live accept updated ProfileJobXP and left the standing frozen,
    # so the leaderboard silently stopped at whatever the last backfill produced.
    #
    # Recomputed from scratch rather than incremented: it is a Sum over a profile's ~24 job rows, an
    # accept is a rare user-initiated action rather than a hot path, and a re-derived total cannot drift
    # the way a running one can.
    recompute_career_standing(profile)
    return sum(totals.values())


def contract_by_concept_map(concept_ids, *, live_only=True, prefetch_jobs=True):
    """Batch resolve concept_id -> Contract via the igdb key (ANCHORED + TRUSTED concepts whose
    raw igdb_id keys a Contract). Two bounded queries instead of a per-concept lookup -- the
    replacement for the old `ContractMembership.objects.filter(concept_id__in=...)` pattern in
    the board/views. Concepts with no matching contract are simply absent from the map."""
    rows = (
        Concept.objects
        .filter(id__in=list(concept_ids), anchor_migration_completed_at__isnull=False,
                igdb_match__status__in=IGDBMatch.TRUSTED_STATUSES)
        .values_list('id', 'igdb_match__igdb_id')
    )
    concept_igdb = {cid: gid for cid, gid in rows if gid is not None}
    if not concept_igdb:
        return {}
    cq = Contract.objects.filter(igdb_id__in=set(concept_igdb.values()))
    if live_only:
        cq = cq.filter(is_live=True)
    if prefetch_jobs:
        cq = cq.prefetch_related('jobs')
    by_igdb = {c.igdb_id: c for c in cq}
    return {cid: by_igdb[gid] for cid, gid in concept_igdb.items() if gid in by_igdb}


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
        # SMALL SIDE FIRST, and this is whale-safety not micro-optimisation. Joining
        # EarnedTrophy -> Trophy -> Game inside the EXISTS lets the planner start from
        # `(profile, earned)` and apply the join as a filter -- which on a 250,000-trophy hunter with
        # NO match scans every one of those rows, once per contract, and `LIMIT 1` actively biases it
        # toward that fast-start shape. Resolving the platinum trophy ids first is catalogue-bounded
        # (a handful of rows) and turns the check into an unambiguous seek on the
        # (profile_id, trophy_id) index. Same pattern build_catalog uses for default_tg_ids.
        platinum_ids = list(
            Trophy.objects.filter(game__concept_id__in=member_ids, trophy_type='platinum')
            .values_list('id', flat=True)
        )
        platinum_reached = bool(platinum_ids) and EarnedTrophy.objects.filter(
            profile=profile, earned=True, trophy_id__in=platinum_ids,
        ).exists()
        full_reached = ProfileGame.objects.filter(
            profile=profile, game__concept_id__in=member_ids, progress=100,
        ).exists()
    if not (platinum_reached and full_reached):
        for bundle in contract.bundles.all():
            if platinum_reached and full_reached:
                break
            # `bundle.concepts.all()`, not values_list: callers prefetch `bundles__concepts`,
            # and values_list on a related manager issues a fresh query that silently bypasses
            # it. This runs once per CANDIDATE per contract, so it is the hot instance of the
            # same bug process_contracts._candidate_profiles already fixed on the cold one.
            bundle_ids = {c.id for c in bundle.concepts.all()}
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
        # Contracts keyed on the completed ANCHORED + TRUSTED-matched concepts' raw IGDB ids,
        # plus any episodic bundle they satisfy. Only LIVE contracts are reached on sync (a
        # draft contract must not become claimable/accepted before curation is finished).
        igdb_ids = list(
            Concept.objects.filter(
                id__in=concept_list,
                anchor_migration_completed_at__isnull=False,
                igdb_match__status__in=IGDBMatch.TRUSTED_STATUSES,
            ).values_list('igdb_match__igdb_id', flat=True)
        )
        igdb_ids = [i for i in igdb_ids if i is not None]
        contracts = set(Contract.objects.filter(igdb_id__in=igdb_ids, is_live=True)) if igdb_ids else set()
        contracts.update(Contract.objects.filter(bundles__concepts__in=concept_list, is_live=True).distinct())
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


def accept_contract(profile, contract, *, first_claim=None):
    """User action: bank ALL of this Contract's claimable tiers at once (Platinum + 100%
    together when both are reached). Writes the ledger + bumps the cache. Idempotent --
    already-accepted tiers are skipped. Returns total XP granted.

    A thin wrapper over `accept_contracts_bulk` with one contract, so single and bulk accepts
    share one implementation of the accept rules."""
    _accepted, granted = accept_contracts_bulk(profile, [contract], first_claim=first_claim)
    return granted


@transaction.atomic
def accept_contracts_bulk(profile, contracts, *, first_claim=None):
    """Bank every claimable tier across MANY contracts in ONE pass. Returns
    (accepted_slugs, total_xp).

    The claim-all path: a hunter whose back catalogue just became claimable can hold hundreds
    of contracts, and accepting them one at a time meant N transactions x ~90 queries each.
    Here the whole batch is planned in Python (`_pending_tiers` is pure -- no queries, no side
    effects), then handed to `grant_job_xp_bulk` as a single set of grants, so the cost is
    driven by the number of JOBS touched (<= 25) rather than the number of contracts.

    Semantics preserved exactly from the per-contract accept:
      - already-accepted tiers are skipped (idempotent); a contract with nothing pending is
        simply absent from the result;
      - a contract is only reported accepted when it actually granted XP (> 0), though its
        stamps are still written -- matching the old behaviour where a zero-XP tier stamped
        the EarnedContract but did not count as accepted;
      - `first_claim` is derived ONCE for the batch, so an onboarding claim-all flags every
        milestone it creates consistently;
      - Pursuer-rank crossings are logged once around the whole batch: `ranks_crossed` returns
        every rank in the span, so a multi-rank jump logs the same rows as the incremental path.
    One transaction for the batch, as before: `claim` was ALREADY atomic, so the old
    per-contract accepts were nested savepoints inside one transaction rather than N
    transactions -- what changes is the query cost, not the all-or-nothing semantics."""
    contracts = list(contracts)
    if not contracts:
        return [], 0
    # Fill the jobs cache for the whole batch in ONE query. Without this the planning loop's
    # `contract.jobs.all()` is a query per contract -- the last thing that still scaled with
    # contract count. Populates the objects the caller handed us (already-prefetched batches,
    # e.g. from claimable_contracts, are left alone).
    prefetch_related_objects(
        [c for c in contracts if 'jobs' not in getattr(c, '_prefetched_objects_cache', {})],
        'jobs',
    )
    # Ordered for the same reason as the job rows below: one statement now takes every
    # EarnedContract lock, so without an explicit order two overlapping claims could acquire
    # them in different orders. (`claim()` still pk-sorts its input, but that no longer governs
    # lock order on its own.)
    ecs = {
        ec.contract_id: ec
        for ec in EarnedContract.objects.select_for_update()
        .filter(profile=profile, contract__in=contracts)
        .order_by('contract_id')
    }
    if not ecs:
        return [], 0

    if first_claim is None:
        first_claim = not _has_any_job_xp(profile)
    old_pursuer_level = _pursuer_level(profile)
    now = timezone.now()

    grants, accepted, touched = [], [], []
    for contract in contracts:
        ec = ecs.get(contract.pk)
        if ec is None:
            continue
        jobs = list(contract.jobs.all())
        if not jobs:
            continue
        tiers, t, multiplier = _pending_tiers(ec, contract)   # reached-but-unaccepted + T/mult used
        if not tiers:
            continue
        contract_total = 0
        for tier, tier_total in tiers:
            for job, amount in zip(jobs, _split(tier_total, len(jobs))):
                grants.append({
                    'job': job, 'amount': amount, 'source': 'contract', 'tier': tier,
                    'base_t': t, 'multiplier': multiplier, 'earned_contract': ec,
                })
                if amount > 0:
                    contract_total += amount
            setattr(ec, _ACCEPTED_FIELD[tier], now)
        touched.append(ec)
        if contract_total > 0:
            accepted.append(contract.slug)

    if not grants:
        return [], 0

    # One shared primitive for the whole batch (ledger rows + per-job cache bump + job-tier
    # milestones), so contracts/quests/events stay consistent and ProfileJobXP = Sum(all grants).
    granted = grant_job_xp_bulk(profile, grants, first_claim=first_claim)
    EarnedContract.objects.bulk_update(
        touched, ['platinum_accepted_at', 'full_accepted_at'], batch_size=500)
    _log_rank_milestones(profile, old_pursuer_level, _pursuer_level(profile), first_claim)
    return accepted, granted


def claimable_contracts(profile):
    """EarnedContracts with a reached-but-unaccepted tier (the pending rewards).

    Jobs are prefetched because every consumer walks them (the claim payload's per-job deltas,
    and the accept's XP split); without it a claim-all paid one query PER contract just to read
    the job list."""
    return (
        EarnedContract.objects.filter(profile=profile)
        .filter(
            Q(platinum_reached_at__isnull=False, platinum_accepted_at__isnull=True)
            | Q(full_reached_at__isnull=False, full_accepted_at__isnull=True)
        )
        .select_related('contract')
        .prefetch_related('contract__jobs')
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
    transaction (all-or-nothing). Returns total XP.

    Delegates to `accept_contracts_bulk`, which is the batched implementation -- looping
    `accept_contract` here would have opened a savepoint AND paid the full per-contract query
    cost for every contract, which is exactly what the batching exists to avoid. Deterministic
    lock order lives there too (contract_id, then job_id)."""
    if contracts is None:
        contracts = [ec.contract for ec in claimable_contracts(profile)]
    # first_claim is derived ONCE inside the bulk accept (prior XP == 0), so every contract in
    # an onboarding claim-all flags its milestones the same way.
    _accepted, granted = accept_contracts_bulk(profile, contracts)
    return granted


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
         jobs:[{slug, name, disc, icon, xp, from_level, to_level, from_frac, to_frac, tier,
                tiers:[{key,name,level,rank}]}],   # every job the claim gave XP to (bar fills; may or may not level)
         pursuer:{from_level, to_level, from_label, to_label, from_key, to_key, rank_up, div_up,
                  ranks:[{key,name}], ladder, ladder_pre}}
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

    # ONE batched accept for the whole claim (see accept_contracts_bulk): the cost scales with
    # the jobs touched, not the contracts claimed, so a hunter banking a back catalogue of
    # hundreds is the same handful of queries as banking one.
    accepted, total = accept_contracts_bulk(profile, contracts, first_claim=first_claim)
    if not accepted:
        return _empty_claim()

    post = _levels_snapshot(profile, job_by_id.keys())
    post_pursuer = _pursuer_level(profile)
    pre_rank = pursuer_rank_for_level(pre_pursuer)
    post_rank = pursuer_rank_for_level(post_pursuer)
    # On a rank-up, the footer first fills the OLD rank (from_rank) to 100% -- completed to its top --
    # BEFORE the hand-off, then swaps to the new rank on return. Basing it on the FROM rank (not the rank
    # just below the new one) keeps it consistent with the hand-off's from->to on multi-rank skips.
    # None for a division step.
    ladder_pre = None
    if post_rank['key'] != pre_rank['key']:
        floor = next_rank_floor(pre_rank['key'])
        if floor is not None:
            ladder_pre = pursuer_rank_ladder(floor - 1)
            ladder_pre['fill'] = 100

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
            'tier': tier_for_level(to_lvl)['name'],    # the job's RESTING tier (the at-rest subtitle)
            # `level` = the tier's min_level (blooms exactly when the bar ticks past it); `rank` =
            # its ladder position (Apprentice 1 .. Legend 7), so the bloom escalates toward Legend.
            'tiers': [{'key': k, 'name': n, 'level': lvl, 'rank': tier_rank(k)}
                      for lvl, k, n in tiers_crossed(frm_lvl, to_lvl)],
        })
    jobs.sort(key=lambda j: (j['to_level'] - j['from_level'], j['xp']), reverse=True)   # biggest promotions, then XP
    return {
        'xp': total,
        'accepted': accepted,
        'first_claim': first_claim,
        'rank_now': post_rank['label'],
        'jobs': jobs,
        'pursuer': {
            'from_level': pre_pursuer, 'to_level': post_pursuer,
            'from_label': pre_rank['label'], 'to_label': post_rank['label'],
            'from_key': pre_rank['key'], 'to_key': post_rank['key'],   # for the hand-off screen's per-rank colours
            # Two finale intensities: rank_up = crossed into a new NAMED rank (heavy); div_up = climbed
            # a division within the same rank (lighter). Both false = no rank movement, no finale.
            'rank_up': post_rank['key'] != pre_rank['key'],
            'div_up': post_rank['key'] == pre_rank['key'] and post_rank['division'] != pre_rank['division'],
            'ranks': [{'key': k, 'name': n} for _lvl, k, n, _hd in ranks_crossed(pre_pursuer, post_pursuer)],
            'ladder': pursuer_rank_ladder(post_pursuer),   # the new-rank ladder (footer after a rank-up / all else)
            'ladder_pre': ladder_pre,                      # rank-up only: old rank filled 100% (footer pre hand-off)
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
        pjx = existing.get(job_id) or ProfileJobXP(
            profile=profile, job_id=job_id,
            country_code=getattr(profile, 'country_code', '') or '',
            is_linked=bool(getattr(profile, 'is_linked', False)),
        )
        pjx.total_xp = total or 0
        pjx.level = level_for_xp(pjx.total_xp)
        pjx.save()

    floor_level = level_for_xp(0)  # 1 (the level-1 floor)
    for job_id, pjx in existing.items():
        if job_id not in sums and (pjx.total_xp or pjx.level != floor_level):
            pjx.total_xp = 0
            pjx.level = floor_level
            pjx.save(update_fields=['total_xp', 'level', 'updated_at'])

    recompute_career_standing(profile)
    return sums


def recompute_career_standing(profile):
    """Roll the per-job cache up into ProfileCareerStanding -- the Career XP board's sort key and Pursuer
    Level.

    Rides this seam rather than getting its own trigger because the rows it sums are written immediately
    above: anything that changes a profile's job XP comes through here, so the roll-up cannot drift from
    its source. Both figures are recompute-from-scratch (Sum over the profile's ~24 rows), so a re-run is
    idempotent and a missed one is self-healing on the next grant.

    Without it, a global Career XP board would aggregate ~24 rows per user across the whole population on
    every read; with it, the board is an indexed ORDER BY.

    `pursuer_level` is the FLOORED figure (`pursuer_level_from`), the same one the Career page and the
    share cards show. Storing a bare `Sum('level')` here was a real bug: the board labels that column
    "level", so a hunter read one number on their own page and a smaller one next to their name on the
    leaderboard. Changing this REQUIRES a backfill (`recompute_job_xp --all`) -- every row written
    before it carries the old definition.
    """
    from django.db.models import Count, Sum
    from trophies.models import ProfileCareerStanding, ProfileJobXP

    # `rows` rides the aggregate that was already being issued, so the floor costs no extra query
    # beyond the catalogue count. It used to store a bare `Sum('level')`, which is the SAME figure
    # under a different definition of Pursuer Level than every other surface uses -- see
    # `pursuer_level_from`.
    totals = ProfileJobXP.objects.filter(profile=profile).aggregate(
        xp=Sum('total_xp'), lvl=Sum('level'), rows=Count('id'),
    )
    country = getattr(profile, 'country_code', '') or ''
    is_linked = bool(getattr(profile, 'is_linked', False))
    ProfileCareerStanding.objects.update_or_create(
        profile=profile,
        defaults={
            'total_xp': totals['xp'] or 0,
            'pursuer_level': pursuer_level_from(totals['lvl'], totals['rows'], catalogue_job_count()),
            'country_code': country,
            'is_linked': is_linked,
        },
    )
    # The job rows carry the same mirror and are NOT written by this function, so they are refreshed here
    # rather than left to the propagation signal -- which only fires on CHANGE, and so would never reach a
    # row created after the last time either value moved. See the birth-stamped bulk_create in
    # `grant_job_xp_bulk`.
    ProfileJobXP.objects.filter(profile=profile).exclude(
        country_code=country, is_linked=is_linked,
    ).update(country_code=country, is_linked=is_linked)


# --- reconciliation: membership drift --------------------------------------

def credit_is_orphaned(ec, platinum_reached, full_reached):
    """True when `ec`'s stamped credit is not supported by the detection result passed in.

    A PURE PREDICATE over tiers the caller has already detected -- it issues no queries and does
    not re-run `_detect_tiers`. That shape is the point: both callers necessarily compute the
    tiers themselves (the command to find candidates, `verify_profile_sync` to check the other
    drift direction), so a version that re-detected internally duplicated their work AND left the
    rule half-stated at the call site. The rule lives here, once.

    Contract membership is DERIVED: `Contract.member_concept_ids` resolves it live from the
    anchored + trusted IGDB id, so a concept SPLIT, a re-anchor, an IGDBMatch falling out of
    TRUSTED_STATUSES, or a staff edit to `Contract.igdb_id` all silently change who qualifies.
    Detection (gate 1) is forward-only and never notices, so credit -- and, once accepted, banked
    XP -- survives for a game the hunter does not own.

    DELIBERATELY ALL-OR-NOTHING: orphaned only when NEITHER tier detects. A per-tier version would
    strip fairly-earned 100% XP the moment `detect_dlc_and_refresh` adds a DLC and knocks a
    hunter's ProfileGame off 100 -- a legitimate progress regression with nothing to do with
    membership drift. If the platinum still detects, the hunter plainly owns the game and the row
    stays. Forgiving in the one direction that cannot destroy earned XP.
    """
    if ec.platinum_reached_at is None and ec.full_reached_at is None:
        return False        # nothing stamped, so there is no credit to orphan
    return not (platinum_reached or full_reached)


@transaction.atomic
def revoke_contract(profile, contract):
    """Remove a profile's ENTIRE credit for one Contract and rebuild their job XP from what is
    left. Returns (revoked, xp_removed) -- `revoked` False means it declined and wrote nothing.

    The inverse of accept, and the ONLY subtractive path in the engine. The ContractXPGrant ledger
    is immutable in the sense that a row is never RECOMPUTED -- config changes must not rewrite
    history -- but a grant for a game the hunter never completed was never history to begin with,
    and deleting it is the only honest reversal. `recompute_profile_job_xp` then rebuilds
    ProfileJobXP and ProfileCareerStanding from the surviving ledger, so per-job levels, the
    Pursuer Level, and every board that sorts on them follow automatically.

    RE-CHECKS ORPHAN-NESS UNDER THE LOCK rather than trusting the caller. (The igdb-derived member
    set is re-resolved; a caller-prefetched `contract.bundles` cache is NOT, so for an episodic
    contract the bundle half of qualification is as fresh as the caller made it. Bundle membership
    is staff-edited and does not move mid-sweep, unlike a hunter's completion.) A sweep finds its
    candidates in one pass and revokes in a second, and on a popular Contract those passes are
    minutes apart -- long enough for a hunter to legitimately re-qualify (a sync lands a platinum
    on a concept that IS still a member). `mark_contract_reached` would leave their existing stamp
    untouched, so nothing else would notice, and an unconditional revoke would delete credit the
    hunter had just earned for real. This also makes the function safe to call directly from a
    shell, which it otherwise would not be.

    LOCKS IN THE SAME ORDER AS `accept_contracts_bulk`: the EarnedContract row first, then the
    profile's ProfileJobXP rows by job_id. Both are load-bearing. Without the ProfileJobXP lock,
    `recompute_profile_job_xp` is a read-then-write -- it snapshots Sum(ledger), and a claim that
    commits in the gap is then overwritten by the stale total, silently breaking the
    `ProfileJobXP = Sum(all grants)` invariant the whole economy rests on (and it cannot
    self-heal, because `grant_job_xp_bulk` increments from the cached value). Locking in the
    OPPOSITE order to the accept path would trade that race for a deadlock, so the order is copied
    from it deliberately -- keep the two in step.

    DELETES THE ROW rather than nulling the stamps, because `has_platinum` was frozen at first
    reach against the membership that has since changed. A nulled row would keep that stale flag
    and mis-size the tiers if the hunter later completes the game for real; a deleted one is
    re-created by `mark_contract_reached` with the flag frozen correctly.

    ProgressionMilestone rows are LEFT ALONE by design. They record a moment that genuinely
    happened, their unique constraints mean a deleted rung can never be re-logged, and a journey
    entry sitting slightly ahead of current XP is a far smaller wrong than un-earning a rung the
    hunter watched themselves cross.
    """
    ec = (EarnedContract.objects.select_for_update()
          .filter(profile=profile, contract=contract).first())
    if ec is None:
        return False, 0
    member_ids = contract.member_concept_ids()
    platinum_reached, full_reached = _detect_tiers(profile, contract, member_ids)
    if not credit_is_orphaned(ec, platinum_reached, full_reached):
        return False, 0        # re-qualified since the caller looked, or never orphaned

    # MATERIALIZE THE WHOLE CATALOGUE BEFORE LOCKING. `SELECT ... FOR UPDATE` is not a predicate
    # lock, and `grant_job_xp_bulk` bulk_creates a ProfileJobXP row for any job the hunter has never
    # been paid -- an INSERT that passes straight through a lock on the rows that already exist,
    # which is most of the catalogue for most hunters (one row out of ~25 after a first claim).
    # Without this, a claim paying a NEW job could commit between `recompute_profile_job_xp`'s two
    # reads, and its floor loop would then zero the XP that claim had just banked. Creating the rows
    # first means the concurrent claim finds them present and blocks on our lock instead.
    #
    # This invents nothing: every job is level 1 from birth, a row at (0 XP, level 1) is that exact
    # state, and `pursuer_level_from` counts a missing row and a floored row identically. (A Job
    # added to the catalogue between this INSERT and the concurrent claim would still escape. The
    # catalogue is seeded and static, so that is a migration-shaped event, not a request-shaped one.)
    ProfileJobXP.objects.bulk_create(
        [ProfileJobXP(profile=profile, job_id=job_id,
                      country_code=getattr(profile, 'country_code', '') or '',
                      is_linked=bool(getattr(profile, 'is_linked', False)))
         for job_id in Job.objects.values_list('pk', flat=True)],
        ignore_conflicts=True,
    )
    # Ordered by job_id, matching grant_job_xp_bulk's lock order. Taken BEFORE the ledger is read
    # below, which is what closes the lost-update window.
    list(ProfileJobXP.objects.select_for_update()
         .filter(profile=profile).order_by('job_id'))

    # Read while the rows still exist: after the delete, this ledger IS the only record that the
    # hunter was ever paid. Per-job, not just the total -- grants are per-job and permanent, so a
    # bare total identifies who was affected but cannot restore them.
    grants = list(ContractXPGrant.objects.filter(earned_contract=ec)
                  .values_list('job_id', 'tier', 'amount'))
    removed = sum(amount for _job, _tier, amount in grants)
    ec.delete()             # ContractXPGrant.earned_contract is CASCADE; the grants go with it
    recompute_profile_job_xp(profile)
    # LOGGED LAST, after every write this function makes has succeeded. Logging is not
    # transactional: emitted before the writes, a line claiming XP was taken would survive the
    # rollback of a revoke that actually failed, and the audit trail would disagree with the
    # command's own FAILED count. Only the COMMIT can still fail from here.
    logger.info(
        "revoke_contract: profile=%s (%s) contract=%s xp_removed=%s grants=%s",
        profile.pk, profile.psn_username, contract.slug, removed,
        ';'.join(f'{job}:{tier}:{amount}' for job, tier, amount in grants) or 'none',
    )
    return True, removed
