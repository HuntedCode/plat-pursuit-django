"""
Rebuild Franchise + ConceptFranchise rows from cached IGDBMatch.raw_response data.

Re-runs the enrichment-side franchise creation pass for every concept that
has an IGDBMatch, WITHOUT hitting the IGDB API. Useful when:

  - The enrichment logic changed (e.g. the (igdb_id, source_type) uniqueness
    fix) and we want to rebuild without paying the cost of a full
    `enrich_from_igdb --refresh` run.
  - Existing Franchise / ConceptFranchise rows are corrupted (e.g. cross-
    namespace ID collisions) and we want a clean rebuild.

Workflow this command supports:

    1. (Optional but recommended) Wipe the existing rows so we get a fully
       clean rebuild rather than partial updates over poisoned data:

           --wipe        delete ALL Franchise + ConceptFranchise rows first

    2. Iterate every IGDBMatch.raw_response and call the same enrichment
       helper that runs during normal IGDB sync.

    3. Report counts.

Usage:
    python manage.py rebuild_franchises_from_cache --dry-run
    python manage.py rebuild_franchises_from_cache --wipe
    python manage.py rebuild_franchises_from_cache --wipe --batch-size 200
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from trophies.models import ConceptFranchise, Franchise, IGDBMatch
from trophies.services.igdb_service import IGDBService


class Command(BaseCommand):
    help = (
        "Rebuild Franchise / ConceptFranchise rows from cached IGDBMatch.raw_response "
        "without hitting the IGDB API."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--wipe', action='store_true',
            help=(
                'Delete ALL existing Franchise + ConceptFranchise rows first. '
                'Recommended when recovering from data corruption.'
            ),
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would happen without writing any data.',
        )
        parser.add_argument(
            '--batch-size', type=int, default=100,
            help='Progress reporting interval (default 100).',
        )

    def handle(self, *args, **options):
        wipe = options['wipe']
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        # Only consider matches with usable raw_response data. The bare {}
        # default sentinel and missing dict both get filtered out.
        matches = (
            IGDBMatch.objects
            .exclude(raw_response={})
            .exclude(raw_response__isnull=True)
            .select_related('concept')
            .only('concept_id', 'concept', 'raw_response')
        )
        total_matches = matches.count()

        existing_franchises = Franchise.objects.count()
        existing_links = ConceptFranchise.objects.count()
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Current state ==="))
        self.stdout.write(f"  Franchise rows:        {existing_franchises}")
        self.stdout.write(f"  ConceptFranchise rows: {existing_links}")
        self.stdout.write(f"  IGDBMatch rows to scan: {total_matches}")

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"\n[DRY RUN] Would {'wipe and ' if wipe else ''}rebuild from "
                f"{total_matches} IGDBMatch row(s). No changes written."
            ))
            return

        if total_matches == 0:
            self.stdout.write(self.style.WARNING("\nNo IGDBMatch records to process."))
            return

        # Wipe + rebuild are wrapped in a single transaction. If anything
        # fails partway, the wipe rolls back too — better to leave the old
        # data in place than half-rebuild and leave the table in chaos.
        processed = 0
        errors = 0
        with transaction.atomic():
            if wipe:
                self.stdout.write(self.style.MIGRATE_HEADING("\n=== Wiping existing rows ==="))
                deleted_links = ConceptFranchise.objects.all().delete()[0]
                deleted_franchises = Franchise.objects.all().delete()[0]
                self.stdout.write(f"  Deleted {deleted_links} ConceptFranchise row(s)")
                self.stdout.write(f"  Deleted {deleted_franchises} Franchise row(s)")

            self.stdout.write(self.style.MIGRATE_HEADING("\n=== Rebuilding from cache ==="))
            for match in matches.iterator(chunk_size=200):
                try:
                    # The enrichment helper handles all the get_or_create + dedup
                    # logic, including the (igdb_id, source_type) composite key.
                    # Calling it directly skips the IGDB API entirely.
                    IGDBService._create_concept_franchises(
                        match.concept, match.raw_response,
                    )
                    processed += 1
                except Exception as exc:
                    errors += 1
                    self.stdout.write(self.style.ERROR(
                        f"  Error on concept_id={match.concept_id}: {exc}"
                    ))

                if processed % batch_size == 0:
                    self.stdout.write(f"  Processed {processed}/{total_matches}...")

        # Stats after rebuild
        new_franchises = Franchise.objects.count()
        new_links = ConceptFranchise.objects.count()
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== After rebuild ==="))
        self.stdout.write(f"  Franchise rows:        {new_franchises}")
        self.stdout.write(f"  ConceptFranchise rows: {new_links}")

        self.stdout.write(self.style.SUCCESS(
            f"\nProcessed {processed}/{total_matches} matches"
            + (f" with {errors} error(s)" if errors else " cleanly") + "."
        ))
