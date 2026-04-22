"""Wipe and re-apply IGDB enrichment for every accepted match.

Problem this solves: when a Concept's IGDBMatch moved from one IGDB entry to
another (via rematch sweep, --manual reassignment, or admin approval after
review), the enrichment helpers for that concept ADDED rows for the new
entry's companies / genres / themes / engines / franchises but never
DELETED the prior entry's rows. Each reassignment accumulated stale data.

This command walks every Concept with an accepted or auto-accepted
IGDBMatch, deletes the existing through-table enrichment rows
(ConceptCompany, ConceptGenre, ConceptTheme, ConceptEngine,
ConceptFranchise), and re-applies enrichment from the stored raw_response.

No IGDB API calls: we already have the match payload on disk. The underlying
Company / Genre / Theme / GameEngine / Franchise master records are
untouched — only the concept-scoped links are rebuilt.

Intended one-shot use: ship this after the `_apply_enrichment` live-path
wipe (which prevents future recurrence) and run once to scrub the backlog.
Idempotent — safe to re-run.
"""

import time

from django.core.management.base import BaseCommand

from trophies.models import (
    Concept, ConceptCompany, ConceptEngine, ConceptFranchise,
    ConceptGenre, ConceptTheme, IGDBMatch,
)
from trophies.services.igdb_service import IGDBService


class Command(BaseCommand):
    help = (
        "Wipe stale ConceptCompany/Genre/Theme/Engine/Franchise rows for "
        "every accepted IGDB match and re-apply enrichment from the stored "
        "raw_response. No IGDB API calls."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would be wiped/rebuilt without touching the DB.',
        )
        parser.add_argument(
            '--concept-id', type=str, metavar='ID',
            help='Limit to a single concept by concept_id (for spot checks).',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Process at most N matches (for incremental runs).',
        )
        parser.add_argument(
            '--verbose', action='store_true',
            help='Print per-row outcome; otherwise only per-bucket rollups.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbose = options['verbose']
        limit = options['limit']

        qs = (
            IGDBMatch.objects
            .filter(status__in=('accepted', 'auto_accepted'))
            .exclude(raw_response={})
            .select_related('concept')
            .order_by('concept__concept_id')
        )
        if options['concept_id']:
            qs = qs.filter(concept__concept_id=options['concept_id'])

        total = qs.count()
        if total == 0:
            self.stdout.write('No accepted IGDB matches with raw_response. Nothing to do.')
            return

        scope = f'{total} accepted match(es)'
        if limit:
            scope += f' (capped at {limit})'
        self.stdout.write(f'Rebuilding enrichment for {scope}.')
        if dry_run:
            self.stdout.write(self.style.WARNING('[DRY RUN] No writes will occur.'))

        stats = {
            'processed': 0,
            'skipped_no_raw': 0,
            'errors': 0,
            'deleted_companies': 0,
            'deleted_genres': 0,
            'deleted_themes': 0,
            'deleted_engines': 0,
            'deleted_franchises': 0,
            'rebuilt_companies': 0,
            'rebuilt_genres': 0,
            'rebuilt_themes': 0,
            'rebuilt_engines': 0,
            'rebuilt_franchises': 0,
        }

        start = time.time()

        for i, match in enumerate(qs.iterator(chunk_size=200)):
            if limit and stats['processed'] >= limit:
                break
            concept = match.concept

            raw = match.raw_response
            if not raw:
                stats['skipped_no_raw'] += 1
                continue

            stats['processed'] += 1
            label = f'[{i + 1}/{total}] {concept.concept_id} "{concept.unified_title}"'

            if dry_run:
                counts = self._count_existing(concept)
                for key, n in counts.items():
                    stats[f'deleted_{key}'] += n
                if verbose:
                    self.stdout.write(
                        f'{label} would wipe: '
                        f'{counts["companies"]}c/{counts["genres"]}g/'
                        f'{counts["themes"]}t/{counts["engines"]}e/'
                        f'{counts["franchises"]}f'
                    )
                continue

            try:
                IGDBService._apply_enrichment(match, raw)
            except Exception as exc:
                stats['errors'] += 1
                self.stdout.write(self.style.ERROR(
                    f'{label} ERROR during rebuild: {exc}'
                ))
                continue

            # Post-rebuild counts give us the rebuilt totals for reporting.
            post = self._count_existing(concept)
            for key, n in post.items():
                stats[f'rebuilt_{key}'] += n

            if verbose:
                self.stdout.write(self.style.SUCCESS(
                    f'{label} rebuilt: '
                    f'{post["companies"]}c/{post["genres"]}g/'
                    f'{post["themes"]}t/{post["engines"]}e/'
                    f'{post["franchises"]}f'
                ))

        elapsed = time.time() - start
        self._print_summary(stats, elapsed, dry_run)

    @staticmethod
    def _count_existing(concept):
        return {
            'companies': ConceptCompany.objects.filter(concept=concept).count(),
            'genres': ConceptGenre.objects.filter(concept=concept).count(),
            'themes': ConceptTheme.objects.filter(concept=concept).count(),
            'engines': ConceptEngine.objects.filter(concept=concept).count(),
            'franchises': ConceptFranchise.objects.filter(concept=concept).count(),
        }

    def _print_summary(self, stats, elapsed, dry_run):
        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'{prefix}Enrichment Rebuild Complete'))
        self.stdout.write(f'  Elapsed:            {elapsed:.1f}s')
        self.stdout.write(f'  Concepts processed: {stats["processed"]}')
        if stats['skipped_no_raw']:
            self.stdout.write(
                f'  Skipped (no raw):   {stats["skipped_no_raw"]}'
            )
        if stats['errors']:
            self.stdout.write(self.style.ERROR(
                f'  Errors:             {stats["errors"]}'
            ))
        verb = 'Would delete' if dry_run else 'Rebuilt from'
        self.stdout.write('')
        self.stdout.write(f'  {verb}:')
        self.stdout.write(f'    ConceptCompany   rows: {stats["deleted_companies" if dry_run else "rebuilt_companies"]}')
        self.stdout.write(f'    ConceptGenre     rows: {stats["deleted_genres" if dry_run else "rebuilt_genres"]}')
        self.stdout.write(f'    ConceptTheme     rows: {stats["deleted_themes" if dry_run else "rebuilt_themes"]}')
        self.stdout.write(f'    ConceptEngine    rows: {stats["deleted_engines" if dry_run else "rebuilt_engines"]}')
        self.stdout.write(f'    ConceptFranchise rows: {stats["deleted_franchises" if dry_run else "rebuilt_franchises"]}')
