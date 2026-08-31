"""The nightly contract-candidate pipeline: run the media-density rule (shared scanner in
trophies/services/contract_candidates.py) over the uncontracted catalogue and maintain the
ContractCandidate ledger.

Per uncontracted IGDB game (siblings vote once):
- Tier A -> a STAGED Contract is auto-created (is_live=False, jobs auto-suggested via the
  genre/theme detector, capped at MAX_CONTRACT_JOBS), in DEMAND ORDER under --max-stage per
  run so the staged queue grows at a publishable pace. Over-cap A games wait as `review`
  (tier A) and stage on later runs.
- Tier B -> `review` (with the blocked/rescued reason recorded for the queue).
- Tier C -> `snoozed`, re-evaluated every run: IGDB pages gain media late, franchise links
  land late -- snooze is a re-check, never a verdict.
- Status only moves FORWARD: `dismissed` is a sticky staff verdict the rule never overrides,
  and an id that gained a real contract is marked `done` (and leaves the population).

Run nightly after recompute_tag_covers (the rule reads franchise links + played_count-class
denorms). Idempotent; --dry-run reports without writing.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from trophies.models import Contract, ContractCandidate
from trophies.services.contract_candidates import (
    DEFAULT_MIN_EARNABLE, DEFAULT_MIN_SHOTS, TIER_AUTO, TIER_SNOOZE, CatalogScanner,
)
from trophies.services.job_detection import suggest_jobs_for_contract


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
        existing = {c.igdb_id: c for c in ContractCandidate.objects.all()}

        # Dedup siblings; keep the highest-signal row per IGDB game (tier A < B < C sorts
        # right lexically; more players wins within a tier).
        best = {}
        for row in scanner.iter_matches():
            if row['contracted']:
                continue
            cur = best.get(row['igdb_id'])
            if cur is None or (row['tier'], -row['players']) < (cur['tier'], -cur['players']):
                best[row['igdb_id']] = row

        creates, updates = [], []
        stage_pool = []          # candidates whose tier says A and whose status allows staging
        counts = {'new': 0, 'promoted': 0, 'done': 0, 'staged': 0}

        for igdb_id, row in best.items():
            cand = existing.get(igdb_id)
            if cand is None:
                cand = ContractCandidate(
                    igdb_id=igdb_id,
                    status=(ContractCandidate.STATUS_SNOOZED if row['tier'] == TIER_SNOOZE
                            else ContractCandidate.STATUS_REVIEW),
                )
                counts['new'] += 1
                creates.append(cand)
            else:
                if cand.status == ContractCandidate.STATUS_SNOOZED and row['tier'] != TIER_SNOOZE:
                    cand.status = ContractCandidate.STATUS_REVIEW   # the snooze re-check paying off
                    counts['promoted'] += 1
                updates.append(cand)
            cand.name = row['name']
            cand.tier = row['tier']
            cand.reason = row['reason']
            cand.players = row['players']
            cand.evaluated_at = now
            # Stage-eligible: the rule says auto AND staff have not dismissed AND it is not
            # already staged/done. (A 'blocked' game carries tier B, so it can never get here.)
            if (row['tier'] == TIER_AUTO
                    and cand.status in (ContractCandidate.STATUS_REVIEW,
                                        ContractCandidate.STATUS_SNOOZED)):
                stage_pool.append(cand)

        # Ids that gained a real contract since last run leave the queue as done.
        done_qs = ContractCandidate.objects.filter(
            igdb_id__in=scanner.contracted_igdb,
        ).exclude(status=ContractCandidate.STATUS_DONE)
        counts['done'] = done_qs.count()

        stage_pool.sort(key=lambda c: -c.players)
        to_stage = stage_pool[:max_stage]

        if dry:
            w(f"DRY RUN: {counts['new']} new, {counts['promoted']} snooze-promotions, "
              f"{counts['done']} done, would stage {len(to_stage)} (cap {max_stage}, "
              f"pool {len(stage_pool)})")
            for c in to_stage[:10]:
                w(f'  would stage: {c.name}  ({c.players} players)')
            return

        with transaction.atomic():
            for cand in creates:
                cand.save()
            staged_contracts = []
            for cand in to_stage:
                cand.contract = self._stage_contract(cand)
                cand.status = ContractCandidate.STATUS_STAGED
                staged_contracts.append(cand)
            counts['staged'] = len(staged_contracts)
            if updates:
                ContractCandidate.objects.bulk_update(
                    updates,
                    ['name', 'tier', 'reason', 'players', 'evaluated_at', 'status', 'contract'],
                    batch_size=500,
                )
            # Newly created rows that also staged this run were saved before staging; persist
            # their contract/status. (creates ∩ to_stage)
            for cand in creates:
                if cand.status == ContractCandidate.STATUS_STAGED:
                    cand.save(update_fields=['status', 'contract'])
            done_qs.update(status=ContractCandidate.STATUS_DONE, evaluated_at=now)

        w(self.style.SUCCESS(
            f"Evaluated {len(best)} uncontracted games: {counts['new']} new, "
            f"{counts['promoted']} snooze-promotions, {counts['staged']} staged "
            f"(cap {max_stage}, pool {len(stage_pool)}), {counts['done']} done."))

    def _stage_contract(self, cand):
        """Create the staged (is_live=False) Contract for a Tier A candidate: unique slug,
        auto-suggested jobs capped at MAX_CONTRACT_JOBS."""
        base = slugify(cand.name)[:200] or f'igdb-{cand.igdb_id}'
        slug = base
        if Contract.objects.filter(slug=slug).exists():
            slug = f'{base}-{cand.igdb_id}'
        contract = Contract.objects.create(
            name=cand.name[:255], slug=slug, igdb_id=cand.igdb_id, is_live=False,
            notes=f'Auto-staged by evaluate_contract_candidates (tier A, {cand.players} players).',
        )
        # suggest_jobs_for_contract already caps at MAX_CONTRACT_JOBS by signal strength.
        suggested = suggest_jobs_for_contract(contract)
        if suggested:
            from trophies.models import Job
            contract.jobs.set(Job.objects.filter(slug__in=suggested))
        return contract
