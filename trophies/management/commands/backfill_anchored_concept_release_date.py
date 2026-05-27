"""Populate Concept.release_date for IGDB-anchored Concepts missing it.

IGDB-anchored Concepts created via `anchor_concepts` start their lives at
`Concept.objects.create(concept_id=..., unified_title=...)` with no
`release_date` field set. Live PSN sync used to be the only thing
populating `release_date` (via `update_release_date`), but live sync no
longer touches IGDB-anchored concepts (see
`project_anchored_concepts_are_source_of_truth`). Result: anchored
concepts stay at `release_date=None` indefinitely.

The downstream impact is `Badge.update_most_recent_concept`, which
queries `Concept.release_date.max()` across Stage members. When all
qualifying concepts are anchored (release_date=None), max_date is None
and `most_recent_concept` becomes None — wiping the badge list's cover
art for fully-converted badges.

This command:
1. Backfills `Concept.release_date` from
   `IGDBMatch.igdb_first_release_date` for accepted/auto_accepted
   matches where the Concept has no release_date.
2. Recomputes `Badge.most_recent_concept` for every Badge so the badge
   list cover art picks up the freshly-populated dates.

Idempotent — safe to re-run. `--dry-run` reports without writing.
The runtime fix in `process_match` keeps this from drifting again.
"""
import time

from django.core.management.base import BaseCommand
from django.db.models import OuterRef, Subquery

from trophies.models import Badge, Concept, IGDBMatch


class Command(BaseCommand):
    help = (
        "Backfill Concept.release_date from IGDBMatch.igdb_first_release_date "
        "for IGDB-anchored Concepts missing it, then recompute Badge."
        "most_recent_concept so the badge list cover art picks up the change."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report counts without writing.',
        )
        parser.add_argument(
            '--skip-badge-refresh', action='store_true',
            help='Backfill release_date only; do NOT refresh Badge.most_recent_concept.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        skip_badge_refresh = options['skip_badge_refresh']

        start = time.time()

        # Step 1: count + report what we'll touch on the Concept side.
        match_date_subq = IGDBMatch.objects.filter(
            concept_id=OuterRef('pk'),
            status__in=['accepted', 'auto_accepted'],
            igdb_first_release_date__isnull=False,
        ).values('igdb_first_release_date')[:1]

        target_qs = Concept.objects.filter(
            release_date__isnull=True,
        ).annotate(
            _backfill_date=Subquery(match_date_subq),
        ).filter(_backfill_date__isnull=False)

        concept_count = target_qs.count()
        self.stdout.write(
            f'Concepts with no release_date but a populated IGDB '
            f'igdb_first_release_date: {concept_count}'
        )

        if dry_run:
            self.stdout.write(self.style.WARNING('--dry-run: skipping writes.'))
        elif concept_count:
            # Update via the subquery in a single SQL statement.
            from django.db.models import F
            updated = target_qs.update(release_date=F('_backfill_date'))
            self.stdout.write(self.style.SUCCESS(
                f'Backfilled release_date on {updated} Concept(s).'
            ))

        # Step 2: refresh Badge.most_recent_concept so badges whose stages
        # are populated entirely by previously-None-date concepts pick up
        # the new dates and re-select a non-None most_recent_concept.
        if skip_badge_refresh:
            self.stdout.write('Skipping Badge.most_recent_concept refresh.')
        else:
            badge_count = Badge.objects.count()
            self.stdout.write(
                f'Refreshing most_recent_concept on {badge_count} Badge(s)...'
            )
            refreshed = 0
            changed = 0
            for badge in Badge.objects.iterator(chunk_size=200):
                prior = badge.most_recent_concept_id
                if dry_run:
                    refreshed += 1
                    continue
                badge.update_most_recent_concept()
                refreshed += 1
                if badge.most_recent_concept_id != prior:
                    changed += 1
            if dry_run:
                self.stdout.write(self.style.SUCCESS(
                    f'Would refresh {refreshed} Badge(s). Re-run without '
                    f'--dry-run to compute the actual change count.'
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f'Refreshed {refreshed} Badge(s); '
                    f'most_recent_concept changed on {changed}.'
                ))

        elapsed = time.time() - start
        self.stdout.write(f'\nDone in {elapsed:.1f}s.')
