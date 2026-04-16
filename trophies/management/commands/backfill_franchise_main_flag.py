"""
Backfill ConceptFranchise.is_main from existing IGDBMatch.raw_response data.

Re-derives the "main franchise" flag for every concept that has an IGDBMatch,
without re-querying IGDB. Uses the exact same precedence rules as the live
enrichment code in IGDBService._create_concept_franchises:

    1. raw_response['franchises'][0]['id']  (first plural entry — preferred,
                                             modern IGDB shape)
    2. raw_response['franchise']['id']      (singular — fallback for older
                                             entries with no plural array)
    3. None → no main franchise for this concept

Usage:
    python manage.py backfill_franchise_main_flag           # apply updates
    python manage.py backfill_franchise_main_flag --dry-run # preview only
    python manage.py backfill_franchise_main_flag --batch-size 500
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from trophies.models import ConceptFranchise, IGDBMatch


def _derive_main_igdb_id(raw_response):
    """Return the IGDB ID that should be flagged is_main for this concept.

    Mirrors IGDBService._create_concept_franchises so the enrichment pipeline
    and this backfill stay in lockstep. Any future change to that precedence
    needs to land in both places.
    """
    if not raw_response:
        return None

    plural = [
        f for f in raw_response.get('franchises', [])
        if f.get('id') and f.get('name')
    ]
    if plural:
        return plural[0]['id']

    singular = raw_response.get('franchise') or {}
    if singular.get('id'):
        return singular['id']

    return None


class Command(BaseCommand):
    help = (
        "Backfill ConceptFranchise.is_main from existing IGDBMatch.raw_response. "
        "Avoids re-hitting the IGDB API."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without writing to the database.',
        )
        parser.add_argument(
            '--batch-size', type=int, default=500,
            help='Number of ConceptFranchise rows to update per bulk_update batch (default: 500).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        # Pull all IGDBMatch rows that have a raw_response. We only care about
        # concepts that actually have franchise links — but checking that on
        # the SQL side requires a join, and it's cheaper to filter in Python
        # since we're already iterating raw_response.
        matches = IGDBMatch.objects.exclude(raw_response={}).only(
            'concept_id', 'raw_response',
        )
        total_matches = matches.count()
        self.stdout.write(f"Scanning {total_matches} IGDBMatch record(s)...")

        # Build the desired-state map: {concept_id: should_be_main_igdb_id_or_None}
        desired_main_by_concept = {}
        no_franchise_data = 0
        for match in matches.iterator(chunk_size=1000):
            desired = _derive_main_igdb_id(match.raw_response)
            desired_main_by_concept[match.concept_id] = desired
            if desired is None:
                no_franchise_data += 1

        self.stdout.write(
            f"  {len(desired_main_by_concept) - no_franchise_data} concept(s) have a derivable main franchise."
        )
        self.stdout.write(
            f"  {no_franchise_data} concept(s) have no franchise data in raw_response (will be left alone)."
        )

        # Walk every ConceptFranchise row for the affected concepts and decide
        # the desired is_main value. The link is main when its franchise.igdb_id
        # matches the concept's derived main_igdb_id AND the franchise is of
        # source_type='franchise' (collections are never main).
        affected_concept_ids = [
            cid for cid, mid in desired_main_by_concept.items() if mid is not None
        ]
        if not affected_concept_ids:
            self.stdout.write(self.style.SUCCESS("Nothing to backfill."))
            return

        cfs = (
            ConceptFranchise.objects
            .filter(concept_id__in=affected_concept_ids)
            .select_related('franchise')
        )
        total_cfs = cfs.count()
        self.stdout.write(f"Evaluating {total_cfs} ConceptFranchise row(s)...")

        to_update = []
        stats = Counter()
        for cf in cfs.iterator(chunk_size=1000):
            desired_main_id = desired_main_by_concept.get(cf.concept_id)
            should_be_main = (
                cf.franchise.source_type == 'franchise'
                and cf.franchise.igdb_id == desired_main_id
            )
            if cf.is_main != should_be_main:
                cf.is_main = should_be_main
                to_update.append(cf)
                if should_be_main:
                    stats['promoted_to_main'] += 1
                else:
                    stats['demoted_from_main'] += 1
            else:
                stats['unchanged'] += 1

        self.stdout.write(self.style.MIGRATE_HEADING("\nProposed changes:"))
        self.stdout.write(f"  Promote to main:    {stats['promoted_to_main']}")
        self.stdout.write(f"  Demote from main:   {stats['demoted_from_main']}")
        self.stdout.write(f"  Unchanged:          {stats['unchanged']}")

        if dry_run:
            self.stdout.write(self.style.WARNING("\n[DRY RUN] No changes written."))
            return

        if not to_update:
            self.stdout.write(self.style.SUCCESS("\nAlready in sync. Nothing to write."))
            return

        # Apply in batches inside a single transaction so a partial failure
        # rolls back cleanly. bulk_update is the right tool here — no signals
        # fire on ConceptFranchise updates anywhere in the codebase.
        with transaction.atomic():
            for i in range(0, len(to_update), batch_size):
                chunk = to_update[i:i + batch_size]
                ConceptFranchise.objects.bulk_update(chunk, ['is_main'])
                self.stdout.write(
                    f"  Wrote batch {i // batch_size + 1} ({len(chunk)} rows)..."
                )

        self.stdout.write(self.style.SUCCESS(
            f"\nBackfill complete. Updated {len(to_update)} ConceptFranchise row(s)."
        ))
