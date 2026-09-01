"""The nightly contract-candidate pipeline: run the media-density rule (shared scanner in
trophies/services/contract_candidates.py) over the uncontracted catalogue and maintain the
ContractCandidate ledger.

Per uncontracted IGDB game (siblings vote once; a flagged sibling blocks the whole group from
auto -- the shovelware override holds at GROUP level, not just per concept):
- Tier A -> a STAGED Contract is auto-created (is_live=False, jobs auto-suggested via the
  genre/theme detector), in DEMAND ORDER under --max-stage per run so the staged queue grows
  at a publishable pace. Over-cap A games wait as `review` (tier A) and stage on later runs.
- Tier B -> `review` (with the blocked/rescued reason recorded for the queue).
- Tier C -> `snoozed`, re-evaluated every run: IGDB pages gain media late, franchise links
  land late -- snooze is a re-check, never a verdict.

The status ledger:
- `staged` rows KEEP their status until their contract goes LIVE (-> `done`) or staff delete
  the staged contract (-> back to `review`). Ids with any contract (live or staged) are
  excluded from evaluation; only a LIVE contract marks `done`.
- `dismissed` is a sticky staff verdict the rule never overrides.
- Status writes are TARGETED (only rows whose status this run changes), never a blanket
  write-back, so a staff action landing mid-run isn't clobbered wholesale.

Run nightly at 04:45, AFTER update_shovelware (04:00) -- the shovelware override reads the
flags it writes. Idempotent; --dry-run reports without writing. One bad row cannot abort the
batch: each staging runs in its own savepoint and a failure leaves that candidate in review.
"""
import logging

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from trophies.models import Contract, ContractCandidate
from trophies.services.contract_candidates import (
    DEFAULT_MIN_EARNABLE, DEFAULT_MIN_SHOTS, REASON_BLOCKED, TIER_AUTO, TIER_REVIEW,
    TIER_SNOOZE, CatalogScanner,
)
from trophies.services.job_detection import suggest_jobs_for_contract

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Evaluate the catalogue against the media-density rule and maintain the ContractCandidate queue."

    def add_arguments(self, parser):
        parser.add_argument('--max-stage', type=int, default=150,
                            help='Max contracts auto-staged per run, demand order (default 150).')
        parser.add_argument('--min-shots', type=int, default=DEFAULT_MIN_SHOTS)
        parser.add_argument('--min-earnable', type=int, default=DEFAULT_MIN_EARNABLE)
        parser.add_argument('--dry-run', action='store_true', help='Report without writing.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        max_stage = opts['max_stage']
        now = timezone.now()
        w = self.stdout.write

        scanner = CatalogScanner(min_shots=opts['min_shots'], min_earnable=opts['min_earnable'])
        live_igdb = set(Contract.objects.filter(
            igdb_id__isnull=False, is_live=True).values_list('igdb_id', flat=True))
        existing = {c.igdb_id: c for c in ContractCandidate.objects.all()}

        # Dedup siblings to the best-signal row per IGDB game (tier A < B < C lexically; more
        # players wins within a tier) -- and hold the SHOVELWARE OVERRIDE at group level: a
        # clean sibling must not launder a flagged game into Tier A (the pre-prod audit's F2).
        best = {}
        group_flagged = set()
        for row in scanner.iter_matches():
            if row['contracted']:
                continue
            if row['is_shovelware']:
                group_flagged.add(row['igdb_id'])
            cur = best.get(row['igdb_id'])
            if cur is None or (row['tier'], -row['players']) < (cur['tier'], -cur['players']):
                best[row['igdb_id']] = row
        for igdb_id in group_flagged:
            row = best.get(igdb_id)
            if row is not None and row['tier'] == TIER_AUTO:
                row['tier'] = TIER_REVIEW
                row['reason'] = REASON_BLOCKED

        creates = []
        field_updates = []       # rows whose rule fields changed (targeted, no status writes)
        status_updates = []      # rows whose STATUS this run changes (targeted saves)
        stage_pool = []
        counts = {'new': 0, 'promoted': 0, 'recovered': 0, 'done': 0, 'staged': 0,
                  'stage_failed': 0}

        for igdb_id, row in best.items():
            cand = existing.get(igdb_id)
            if cand is None:
                cand = ContractCandidate(
                    igdb_id=igdb_id,
                    status=(ContractCandidate.STATUS_SNOOZED if row['tier'] == TIER_SNOOZE
                            else ContractCandidate.STATUS_REVIEW),
                    name=row['name'], tier=row['tier'], reason=row['reason'],
                    players=row['players'], evaluated_at=now,
                )
                counts['new'] += 1
                creates.append(cand)
            else:
                new_status = None
                if (cand.status == ContractCandidate.STATUS_SNOOZED
                        and row['tier'] != TIER_SNOOZE):
                    new_status = ContractCandidate.STATUS_REVIEW   # the re-check paying off
                    counts['promoted'] += 1
                elif (cand.status in (ContractCandidate.STATUS_STAGED,
                                      ContractCandidate.STATUS_DONE)
                        and cand.contract_id is None):
                    # Staff deleted the staged/held contract (SET_NULL): the id is uncontracted
                    # again, so the candidate re-enters the queue instead of sticking forever.
                    new_status = ContractCandidate.STATUS_REVIEW
                    counts['recovered'] += 1
                dirty = (cand.name != row['name'] or cand.tier != row['tier']
                         or cand.reason != row['reason'] or cand.players != row['players'])
                if dirty or new_status is not None:
                    # Unchanged rows skip the write entirely (no nightly churn over ~16k rows;
                    # evaluated_at therefore means "when the verdict last CHANGED").
                    cand.name = row['name']
                    cand.tier = row['tier']
                    cand.reason = row['reason']
                    cand.players = row['players']
                    cand.evaluated_at = now
                    if new_status is not None:
                        cand.status = new_status
                        status_updates.append(cand)
                    if dirty:
                        field_updates.append(cand)
            # Stage eligibility is checked for EVERY best row -- including unchanged ones, or
            # an over-cap review row (tier A, fields identical since yesterday) would never
            # stage on later runs.
            if (row['tier'] == TIER_AUTO
                    and cand.status in (ContractCandidate.STATUS_REVIEW,
                                        ContractCandidate.STATUS_SNOOZED)):
                stage_pool.append(cand)

        # Only a LIVE contract marks done -- a staged (is_live=False) contract keeps its
        # candidate in `staged` awaiting publish (the audit's F1: the full contracted set here
        # emptied the staged queue into done every night).
        done_qs = ContractCandidate.objects.filter(
            igdb_id__in=live_igdb,
        ).exclude(status=ContractCandidate.STATUS_DONE)
        counts['done'] = done_qs.count()

        stage_pool.sort(key=lambda c: -c.players)
        to_stage = stage_pool[:max_stage]

        if dry:
            w(f"DRY RUN: {counts['new']} new, {counts['promoted']} snooze-promotions, "
              f"{counts['recovered']} recovered, {counts['done']} done, would stage "
              f"{len(to_stage)} (cap {max_stage}, pool {len(stage_pool)})")
            for c in to_stage[:10]:
                w(f'  would stage: {c.name}  ({c.players} players)')
            return

        with transaction.atomic():
            for cand in creates:
                cand.save()
            for cand in to_stage:
                # Per-candidate savepoint: one bad row (an unforeseen collision, a racing
                # staff-created contract) must not abort the whole nightly batch.
                try:
                    with transaction.atomic():
                        contract = self._stage_contract(cand)
                except Exception:
                    logger.exception('Staging failed for igdb_id=%s (%s); left in review.',
                                     cand.igdb_id, cand.name)
                    counts['stage_failed'] += 1
                    continue
                if contract is None:
                    continue   # a contract appeared mid-run; done-marking resolves it later
                cand.contract = contract
                cand.status = ContractCandidate.STATUS_STAGED
                cand.save(update_fields=['contract', 'status'])
                counts['staged'] += 1
            if field_updates:
                ContractCandidate.objects.bulk_update(
                    field_updates,
                    ['name', 'tier', 'reason', 'players', 'evaluated_at'],
                    batch_size=500,
                )
            # Status transitions are TARGETED per-row writes, never a blanket write-back:
            # a dismissal landing mid-run on some other row is untouched.
            for cand in status_updates:
                if cand.status != ContractCandidate.STATUS_STAGED:   # staged saved above
                    cand.save(update_fields=['status', 'evaluated_at'])
            done_qs.update(status=ContractCandidate.STATUS_DONE, evaluated_at=now)

        w(self.style.SUCCESS(
            f"Evaluated {len(best)} uncontracted games: {counts['new']} new, "
            f"{counts['promoted']} snooze-promotions, {counts['recovered']} recovered, "
            f"{counts['staged']} staged (cap {max_stage}, pool {len(stage_pool)}, "
            f"{counts['stage_failed']} failed), {counts['done']} done (went live)."))

    def _stage_contract(self, cand):
        """Create the staged (is_live=False) Contract for a Tier A candidate: unique slug
        (uniquified with the igdb id, then a counter), jobs auto-suggested (the detector caps
        at MAX_CONTRACT_JOBS). Returns None if a contract for this igdb id appeared since the
        scan (a racing staff action) -- never raises IntegrityError for that case."""
        if Contract.objects.filter(igdb_id=cand.igdb_id).exists():
            return None
        base = slugify(cand.name)[:200] or f'igdb-{cand.igdb_id}'
        slug = base
        n = 0
        while Contract.objects.filter(slug=slug).exists():
            n += 1
            slug = f'{base}-{cand.igdb_id}' if n == 1 else f'{base}-{cand.igdb_id}-{n}'
        contract = Contract.objects.create(
            name=cand.name[:255], slug=slug, igdb_id=cand.igdb_id, is_live=False,
            notes=f'Auto-staged by evaluate_contract_candidates (tier A, {cand.players} players).',
        )
        suggested = suggest_jobs_for_contract(contract)
        if suggested:
            from trophies.models import Job
            contract.jobs.set(Job.objects.filter(slug__in=suggested))
        return contract
