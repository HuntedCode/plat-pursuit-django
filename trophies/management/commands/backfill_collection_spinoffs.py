"""
Backfill ConceptFranchise.is_spinoff for collection (Series) links from IGDB.

Unlike backfill_franchise_main_flag (which re-derives from cached raw_response),
the spin-off signal was NEVER stored in raw_response -- it lives only on IGDB's
/collection_memberships endpoint. So this command HITS THE IGDB API: it gathers
every concept that has a collection-type franchise link + an IGDB game id, fetches
their collection memberships in batches, and stamps is_spinoff on each collection
link (type 2 = "Spin-off", everything else = normal member).

Idempotent and re-runnable. Only collection links are touched; franchise links keep
is_spinoff=False by definition.

Usage:
    python manage.py backfill_collection_spinoffs              # apply
    python manage.py backfill_collection_spinoffs --dry-run    # preview only
    python manage.py backfill_collection_spinoffs --limit 200  # cap concepts (testing)
    python manage.py backfill_collection_spinoffs --batch-size 500
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from trophies.models import ConceptFranchise
from trophies.services.igdb_service import IGDBService


class Command(BaseCommand):
    help = (
        "Backfill ConceptFranchise.is_spinoff for collection links by querying IGDB "
        "/collection_memberships (the type is not in cached raw_response)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without writing or skipping the IGDB fetch.',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Cap the number of concepts processed (for testing).',
        )
        parser.add_argument(
            '--batch-size', type=int, default=500,
            help='Rows per bulk_update batch (default: 500).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        batch_size = options['batch_size']

        # Every collection-type link, with the concept's IGDB game id (the membership
        # key) and the collection's IGDB id resolved up-front.
        cfs = (
            ConceptFranchise.objects
            .filter(franchise__source_type='collection')
            .select_related('franchise', 'concept__igdb_match')
            .order_by('concept_id')
        )
        if limit:
            # Limit by concept, not row, so a concept's links stay together.
            concept_ids = list(
                cfs.values_list('concept_id', flat=True).distinct()[:limit]
            )
            cfs = cfs.filter(concept_id__in=concept_ids)

        rows = list(cfs)
        if not rows:
            self.stdout.write(self.style.SUCCESS("No collection links to backfill."))
            return

        # Distinct game ids to query (a concept has one IGDBMatch; many share none).
        game_ids = sorted({
            cf.concept.igdb_match.igdb_id
            for cf in rows
            if getattr(cf.concept, 'igdb_match', None) and cf.concept.igdb_match.igdb_id
        })
        self.stdout.write(
            f"{len(rows)} collection link(s) across {len(game_ids)} game(s) with an IGDB id."
        )

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "[DRY RUN] Skipping the IGDB fetch; cannot preview exact changes "
                "without it. Re-run without --dry-run to apply."
            ))
            return

        self.stdout.write("Fetching collection memberships from IGDB...")
        membership = IGDBService.fetch_collection_memberships(game_ids)

        to_update = []
        stats = Counter()
        for cf in rows:
            match = getattr(cf.concept, 'igdb_match', None)
            game_id = match.igdb_id if match else None
            desired = bool(
                membership.get(game_id, {}).get(cf.franchise.igdb_id, False)
            )
            if cf.is_spinoff != desired:
                cf.is_spinoff = desired
                to_update.append(cf)
                stats['flagged_spinoff' if desired else 'cleared_spinoff'] += 1
            else:
                stats['unchanged'] += 1

        self.stdout.write(self.style.MIGRATE_HEADING("\nProposed changes:"))
        self.stdout.write(f"  Flag as spin-off:   {stats['flagged_spinoff']}")
        self.stdout.write(f"  Clear spin-off:     {stats['cleared_spinoff']}")
        self.stdout.write(f"  Unchanged:          {stats['unchanged']}")

        if not to_update:
            self.stdout.write(self.style.SUCCESS("\nAlready in sync. Nothing to write."))
            return

        # bulk_update is safe: no signals fire on ConceptFranchise writes anywhere.
        with transaction.atomic():
            for i in range(0, len(to_update), batch_size):
                chunk = to_update[i:i + batch_size]
                ConceptFranchise.objects.bulk_update(chunk, ['is_spinoff'])
                self.stdout.write(f"  Wrote batch {i // batch_size + 1} ({len(chunk)} rows)...")

        self.stdout.write(self.style.SUCCESS(
            f"\nBackfill complete. Updated {len(to_update)} collection link(s)."
        ))
