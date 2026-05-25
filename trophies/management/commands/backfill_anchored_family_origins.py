"""Populate origin_* fields on GameFamily for anchored-concept families.

GameFamily gained four denormalized "origin" fields (first_release_date,
name, cover_image_id, summary) snapshotted from the topmost canonical
IGDB entry. The live enrichment path in `_link_concept_to_family`
populates them automatically going forward, but families that were last
linked before that code existed have empty origin_* columns.

Scope: families containing at least one anchored concept (i.e. concepts
where `anchor_migration_completed_at IS NOT NULL`). Other families
will populate naturally as their concepts get re-enriched; running this
command for them isn't necessary and would burn IGDB rate-limit budget.

Each family already knows its canonical IGDB id (`family.igdb_id` was
set by the recursive resolver). We fetch that entry's IGDB data once
and snapshot the origin_* fields. No re-resolution happens; this is
purely a metadata snapshot pass.

Defaults skip families that already have origin data populated.
Use --force to re-snapshot anyway. --dry-run reports without writing.
"""
import time

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from trophies.models import GameFamily
from trophies.services.igdb_service import IGDBService


class Command(BaseCommand):
    help = (
        "Snapshot origin_* fields onto GameFamily rows that hold at least "
        "one anchored concept. Idempotent — safe to re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report which families would be updated without writing.',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Re-snapshot even families whose origin_first_release_date '
                 'is already populated.',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Process at most N families this run.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']
        limit = options['limit']

        # Families containing at least one anchored concept, with a known
        # canonical IGDB id.
        qs = GameFamily.objects.filter(
            igdb_id__isnull=False,
            concepts__anchor_migration_completed_at__isnull=False,
        ).distinct()
        if not force:
            qs = qs.filter(origin_first_release_date__isnull=True)
        qs = qs.annotate(
            anchored_count=Count(
                'concepts',
                filter=Q(concepts__anchor_migration_completed_at__isnull=False),
            ),
        ).order_by('canonical_name')

        total = qs.count()
        if total == 0:
            msg = (
                'No anchored-concept families need origin backfill.'
                if not force else 'No anchored-concept families found.'
            )
            self.stdout.write(self.style.SUCCESS(msg))
            return

        if limit:
            self.stdout.write(
                f'Found {total} target families; processing up to {limit} '
                f'this run.'
            )
            qs = qs[:limit]
        else:
            self.stdout.write(f'Found {total} target families.')

        start = time.time()
        updated = 0
        skipped_no_data = 0
        skipped_no_change = 0
        failed = 0

        for family in qs.iterator():
            label = f'GameFamily #{family.pk} "{family.canonical_name}" (igdb_id={family.igdb_id})'

            canonical_data = IGDBService.fetch_full_game_data(family.igdb_id)
            if not canonical_data:
                self.stdout.write(self.style.WARNING(
                    f'  SKIP {label}: IGDB fetch returned nothing.'
                ))
                skipped_no_data += 1
                continue

            origin_fields = IGDBService._origin_fields_from_canonical(canonical_data)
            if not origin_fields:
                # _origin_fields_from_canonical returns {} only when given
                # None, so this branch is defensive; we already know
                # canonical_data is truthy.
                skipped_no_data += 1
                continue

            update_fields = []
            for field_name, value in origin_fields.items():
                if getattr(family, field_name) != value:
                    setattr(family, field_name, value)
                    update_fields.append(field_name)

            # Also fix canonical_name if admin hasn't edited it AND the
            # origin name differs (catches the Tomb Raider: Anniversary
            # misnaming case from pre-fix data).
            new_canonical_name = origin_fields.get('origin_name') or ''
            if (
                new_canonical_name
                and not family.admin_notes
                and family.canonical_name != new_canonical_name
            ):
                family.canonical_name = new_canonical_name
                update_fields.append('canonical_name')

            if not update_fields:
                skipped_no_change += 1
                self.stdout.write(f'  OK   {label}: already up to date.')
                continue

            if dry_run:
                updated += 1
                self.stdout.write(
                    f'  WOULD UPDATE {label}: {", ".join(update_fields)}'
                )
                continue

            try:
                family.save(update_fields=update_fields)
                updated += 1
                self.stdout.write(
                    f'  UPDATED {label}: {", ".join(update_fields)}'
                )
            except Exception as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(
                    f'  FAIL {label}: {exc}'
                ))

        elapsed = time.time() - start
        verb = 'Would update' if dry_run else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'\nDone in {elapsed:.1f}s. {verb}: {updated}. '
            f'No change: {skipped_no_change}. '
            f'IGDB fetch failed: {skipped_no_data}. '
            f'Save failed: {failed}.'
        ))
