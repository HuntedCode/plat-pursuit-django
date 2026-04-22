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
        single_concept_id = options.get('concept_id')

        qs = (
            IGDBMatch.objects
            .filter(status__in=('accepted', 'auto_accepted'))
            .exclude(raw_response={})
            .select_related('concept')
            .order_by('concept__concept_id')
        )
        if single_concept_id:
            qs = qs.filter(concept__concept_id=single_concept_id)

        total = qs.count()

        stats = {
            'processed': 0,
            'skipped_no_raw': 0,
            'errors': 0,
            'orphan_concepts_wiped': 0,
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
            'orphan_companies_wiped': 0,
            'orphan_genres_wiped': 0,
            'orphan_themes_wiped': 0,
            'orphan_engines_wiped': 0,
            'orphan_franchises_wiped': 0,
            'orphan_json_cleared': 0,
        }

        start = time.time()

        # Pass 1: wipe orphan enrichment on concepts that have through-rows
        # or JSON denorms but no accepted/auto_accepted match. These rows
        # are stale from a prior match that was rejected/deleted/rematched
        # and never re-enriched, so their data is pinned to an IGDB entry
        # the concept no longer points at.
        self._wipe_orphan_enrichment(
            stats, dry_run, verbose, single_concept_id,
        )

        if total == 0:
            self.stdout.write('No accepted IGDB matches with raw_response to rebuild.')
            self._print_summary(stats, time.time() - start, dry_run)
            return

        scope = f'{total} accepted match(es)'
        if limit:
            scope += f' (capped at {limit})'
        self.stdout.write(f'Rebuilding enrichment for {scope}.')
        if dry_run:
            self.stdout.write(self.style.WARNING('[DRY RUN] No writes will occur.'))

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

    def _wipe_orphan_enrichment(self, stats, dry_run, verbose, single_concept_id):
        """Clear enrichment on concepts that lack an accepted/auto_accepted match.

        Scope: any concept with at least one ConceptCompany/Genre/Theme/Engine/
        Franchise row OR a non-empty Concept.igdb_genres / Concept.igdb_themes,
        whose IGDBMatch (if any) is in a non-applied status (pending_review,
        no_match, rejected) — or who has no IGDBMatch at all.

        These rows are stale: they were created by a prior accepted match
        that's since been rejected/deleted/rematched and never re-enriched,
        so the data is pinned to an IGDB entry the concept no longer points
        at. Wiping here ensures the concept looks correctly "unenriched"
        until its next successful match lands.
        """
        accepted_concept_ids = set(
            IGDBMatch.objects
            .filter(status__in=('accepted', 'auto_accepted'))
            .values_list('concept_id', flat=True)
        )

        orphan_ids = set()
        for model in (
            ConceptCompany, ConceptGenre, ConceptTheme,
            ConceptEngine, ConceptFranchise,
        ):
            orphan_ids.update(
                model.objects
                .exclude(concept_id__in=accepted_concept_ids)
                .values_list('concept_id', flat=True)
                .distinct()
            )
        # Also concepts whose JSON denorm is populated but no accepted match.
        json_orphan_qs = (
            Concept.objects
            .exclude(id__in=accepted_concept_ids)
            .exclude(igdb_genres=[], igdb_themes=[])
            .values_list('id', flat=True)
        )
        orphan_ids.update(json_orphan_qs)

        if single_concept_id:
            # Restrict the scope when the user passed --concept-id so
            # we don't wipe the whole catalog on a spot check.
            target = Concept.objects.filter(concept_id=single_concept_id).first()
            if target is None or target.id not in orphan_ids:
                return
            orphan_ids = {target.id}

        if not orphan_ids:
            return

        label_prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(
            f'{label_prefix}Found {len(orphan_ids)} orphan concept(s) '
            f'with enrichment but no accepted match.'
        )

        for concept in Concept.objects.filter(id__in=orphan_ids).only(
            'id', 'concept_id', 'unified_title', 'igdb_genres', 'igdb_themes',
        ):
            counts = self._count_existing(concept)
            had_json = bool(concept.igdb_genres) or bool(concept.igdb_themes)

            if verbose:
                self.stdout.write(
                    f'  orphan {concept.concept_id} "{concept.unified_title}" — '
                    f'{counts["companies"]}c/{counts["genres"]}g/'
                    f'{counts["themes"]}t/{counts["engines"]}e/'
                    f'{counts["franchises"]}f'
                    f'{"  +json" if had_json else ""}'
                )

            stats['orphan_companies_wiped'] += counts['companies']
            stats['orphan_genres_wiped'] += counts['genres']
            stats['orphan_themes_wiped'] += counts['themes']
            stats['orphan_engines_wiped'] += counts['engines']
            stats['orphan_franchises_wiped'] += counts['franchises']
            if had_json:
                stats['orphan_json_cleared'] += 1

            if dry_run:
                continue

            # Wipe through-rows (mirrors IGDBService._wipe_enrichment_through_rows
            # but we avoid importing it here to keep the command self-contained
            # around the wipe semantics).
            ConceptCompany.objects.filter(concept=concept).delete()
            ConceptGenre.objects.filter(concept=concept).delete()
            ConceptTheme.objects.filter(concept=concept).delete()
            ConceptEngine.objects.filter(concept=concept).delete()
            ConceptFranchise.objects.filter(concept=concept).delete()

            update_fields = []
            if concept.igdb_genres:
                concept.igdb_genres = []
                update_fields.append('igdb_genres')
            if concept.igdb_themes:
                concept.igdb_themes = []
                update_fields.append('igdb_themes')
            if update_fields:
                concept.save(update_fields=update_fields)

            stats['orphan_concepts_wiped'] += 1

    def _print_summary(self, stats, elapsed, dry_run):
        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'{prefix}Enrichment Rebuild Complete'))
        self.stdout.write(f'  Elapsed:            {elapsed:.1f}s')

        # Orphan pass rollup
        orphan_rows_total = (
            stats['orphan_companies_wiped']
            + stats['orphan_genres_wiped']
            + stats['orphan_themes_wiped']
            + stats['orphan_engines_wiped']
            + stats['orphan_franchises_wiped']
        )
        if stats['orphan_concepts_wiped'] or orphan_rows_total or stats['orphan_json_cleared']:
            verb_orphan = 'Would wipe' if dry_run else 'Wiped'
            self.stdout.write('')
            self.stdout.write(f'  Orphan-enrichment pass:')
            self.stdout.write(f'    {verb_orphan} {stats["orphan_concepts_wiped"] if not dry_run else "?"} concept(s) with no accepted match')
            self.stdout.write(f'    ConceptCompany   rows: {stats["orphan_companies_wiped"]}')
            self.stdout.write(f'    ConceptGenre     rows: {stats["orphan_genres_wiped"]}')
            self.stdout.write(f'    ConceptTheme     rows: {stats["orphan_themes_wiped"]}')
            self.stdout.write(f'    ConceptEngine    rows: {stats["orphan_engines_wiped"]}')
            self.stdout.write(f'    ConceptFranchise rows: {stats["orphan_franchises_wiped"]}')
            self.stdout.write(f'    Concept JSON denorms cleared on: {stats["orphan_json_cleared"]} concept(s)')

        self.stdout.write('')
        self.stdout.write(f'  Rebuild pass:')
        self.stdout.write(f'    Concepts processed: {stats["processed"]}')
        if stats['skipped_no_raw']:
            self.stdout.write(
                f'    Skipped (no raw):   {stats["skipped_no_raw"]}'
            )
        if stats['errors']:
            self.stdout.write(self.style.ERROR(
                f'    Errors:             {stats["errors"]}'
            ))
        verb = 'Would delete' if dry_run else 'Rebuilt from raw_response'
        self.stdout.write(f'    {verb}:')
        self.stdout.write(f'      ConceptCompany   rows: {stats["deleted_companies" if dry_run else "rebuilt_companies"]}')
        self.stdout.write(f'      ConceptGenre     rows: {stats["deleted_genres" if dry_run else "rebuilt_genres"]}')
        self.stdout.write(f'      ConceptTheme     rows: {stats["deleted_themes" if dry_run else "rebuilt_themes"]}')
        self.stdout.write(f'      ConceptEngine    rows: {stats["deleted_engines" if dry_run else "rebuilt_engines"]}')
        self.stdout.write(f'      ConceptFranchise rows: {stats["deleted_franchises" if dry_run else "rebuilt_franchises"]}')
