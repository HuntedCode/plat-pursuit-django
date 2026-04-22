"""Wipe and re-apply IGDB enrichment for every accepted match.

Problem this solves: when a Concept's IGDBMatch moved from one IGDB entry
to another (via rematch sweep, --manual reassignment, admin approval after
review, or manual deletion), the enrichment helpers for that concept ADDED
rows for the new entry's companies / genres / themes / engines / franchises
but never DELETED the prior entry's rows. Concepts whose match was
reassigned or removed accumulated stale data pinned to IGDB entries they
no longer point at.

Strategy: catalog-wide bulk wipe first, then iterate the accepted matches
to rebuild. Dramatically faster than per-concept wipe + rebuild because it
avoids ~5 redundant DELETE statements per concept — a single bulk
`DELETE FROM conceptcompany` covers every concept in the DB for the cost
of one round trip, versus 17k-plus individual scoped deletes.

No IGDB API calls: we rebuild from the stored raw_response on each match.
Master records (Company / Genre / Theme / GameEngine / Franchise) are
untouched — only the concept-scoped through-rows and JSON denorms are
blown away and reconstructed.

Intended one-shot use after the `_apply_enrichment` live-path wipe
(which prevents future recurrence). Idempotent — safe to re-run.
"""

import time

from django.core.management.base import BaseCommand
from django.db import transaction

from trophies.models import (
    Concept, ConceptCompany, ConceptEngine, ConceptFranchise,
    ConceptGenre, ConceptTheme, IGDBMatch,
)
from trophies.services.igdb_service import IGDBService


class Command(BaseCommand):
    help = (
        "Bulk-wipe ConceptCompany/Genre/Theme/Engine/Franchise through-rows "
        "and Concept.igdb_genres/igdb_themes JSON denorms for every Concept, "
        "then re-apply enrichment from stored raw_response on concepts with "
        "an accepted match. Concepts without an accepted match end up "
        "cleanly unenriched. No IGDB API calls."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would be wiped/rebuilt without touching the DB.',
        )
        parser.add_argument(
            '--concept-id', type=str, metavar='ID',
            help='Limit to a single concept by concept_id (for spot checks). '
                 'Uses per-concept wipe instead of the bulk path.',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Stop after rebuilding N accepted matches. Ignored for the '
                 'bulk wipe phase — a partial wipe would leave orphans.',
        )
        parser.add_argument(
            '--verbose', action='store_true',
            help='Print per-row outcome; otherwise only per-phase rollups.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbose = options['verbose']
        limit = options['limit']
        single_concept_id = options.get('concept_id')

        start = time.time()
        stats = self._empty_stats()

        if single_concept_id:
            # Spot-check path: scope everything to a single concept, reuse
            # the per-concept wipe+rebuild flow. Bulk semantics don't
            # meaningfully help here and would wipe the whole DB.
            self._run_single_concept(single_concept_id, stats, dry_run, verbose)
            self._print_summary(stats, time.time() - start, dry_run)
            return

        # Bulk path for the full catalog.
        qs = (
            IGDBMatch.objects
            .filter(status__in=('accepted', 'auto_accepted'))
            .exclude(raw_response={})
            .select_related('concept')
            .order_by('concept__concept_id')
        )
        total = qs.count()

        self.stdout.write(
            f'Bulk-wiping enrichment across the catalog, then rebuilding '
            f'{total} accepted match(es).'
        )
        if dry_run:
            self.stdout.write(self.style.WARNING('[DRY RUN] No writes will occur.'))

        # Phase 1: bulk-wipe everything in a single transaction.
        self._bulk_wipe_everything(stats, dry_run)

        # Phase 2: rebuild accepted concepts from stored raw_response.
        # skip_wipe=True because phase 1 already cleared everything.
        if total > 0:
            self._rebuild_accepted_concepts(qs, total, stats, dry_run, verbose, limit)

        self._print_summary(stats, time.time() - start, dry_run)

    # -------------------------------------------------------------------
    # Bulk path
    # -------------------------------------------------------------------

    def _bulk_wipe_everything(self, stats, dry_run):
        """Single-transaction catalog-wide wipe of enrichment through-rows + JSON denorms.

        One DELETE per through-table (5 total) + one UPDATE for JSON denorms.
        The alternative — per-concept DELETEs scoped by WHERE concept_id = X —
        issues ~5 queries per concept; this approach is ~5 queries total.
        """
        through_models = [
            (ConceptCompany, 'companies'),
            (ConceptGenre, 'genres'),
            (ConceptTheme, 'themes'),
            (ConceptEngine, 'engines'),
            (ConceptFranchise, 'franchises'),
        ]

        if dry_run:
            for model, key in through_models:
                stats[f'wiped_{key}'] = model.objects.count()
            stats['json_cleared'] = (
                Concept.objects
                .exclude(igdb_genres=[], igdb_themes=[])
                .count()
            )
            return

        with transaction.atomic():
            for model, key in through_models:
                count, _ = model.objects.all().delete()
                stats[f'wiped_{key}'] = count

            # Reset Concept.igdb_genres / igdb_themes in a single UPDATE.
            # exclude() narrows to rows that actually have something to clear,
            # avoiding a full-table rewrite on Concepts that never populated.
            stats['json_cleared'] = (
                Concept.objects
                .exclude(igdb_genres=[], igdb_themes=[])
                .update(igdb_genres=[], igdb_themes=[])
            )

    def _rebuild_accepted_concepts(self, qs, total, stats, dry_run, verbose, limit):
        """Iterate accepted matches and re-apply enrichment from stored raw_response.

        skip_wipe=True on the _apply_enrichment call — phase 1 already wiped
        everything, and the per-concept wipe inside _apply_enrichment would
        just issue 5 no-op DELETEs against already-empty state.
        """
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
                if verbose:
                    self.stdout.write(f'{label} would rebuild from raw_response')
                continue

            try:
                IGDBService._apply_enrichment(match, raw, skip_wipe=True)
            except Exception as exc:
                stats['errors'] += 1
                self.stdout.write(self.style.ERROR(
                    f'{label} ERROR during rebuild: {exc}'
                ))
                continue

            if verbose:
                post = self._count_existing(concept)
                self.stdout.write(self.style.SUCCESS(
                    f'{label} rebuilt: '
                    f'{post["companies"]}c/{post["genres"]}g/'
                    f'{post["themes"]}t/{post["engines"]}e/'
                    f'{post["franchises"]}f'
                ))
                for key, n in post.items():
                    stats[f'rebuilt_{key}'] += n
            else:
                # Count in bulk at the end when not verbose, to avoid
                # N extra SELECT COUNT queries during the hot loop.
                pass

        if not dry_run and not verbose and stats['processed']:
            # One final aggregate count, cheap vs. N per-concept counts.
            stats['rebuilt_companies'] = ConceptCompany.objects.count()
            stats['rebuilt_genres'] = ConceptGenre.objects.count()
            stats['rebuilt_themes'] = ConceptTheme.objects.count()
            stats['rebuilt_engines'] = ConceptEngine.objects.count()
            stats['rebuilt_franchises'] = ConceptFranchise.objects.count()

    # -------------------------------------------------------------------
    # Single-concept path (spot checks)
    # -------------------------------------------------------------------

    def _run_single_concept(self, concept_id, stats, dry_run, verbose):
        """Per-concept wipe + rebuild for --concept-id spot checks.

        Bulk semantics don't apply to a single row — and using them would
        wipe the entire catalog when the user only wanted to validate one
        concept. Falls back to IGDBService._apply_enrichment which handles
        the wipe per-concept.
        """
        concept = Concept.objects.filter(concept_id=concept_id).first()
        if not concept:
            self.stdout.write(self.style.ERROR(f'No concept with concept_id={concept_id}'))
            return

        match = IGDBMatch.objects.filter(
            concept=concept,
            status__in=('accepted', 'auto_accepted'),
        ).first()

        if not match or not match.raw_response:
            # Orphan case: wipe without rebuild.
            pre = self._count_existing(concept)
            had_json = bool(concept.igdb_genres) or bool(concept.igdb_themes)
            self.stdout.write(
                f'Concept {concept.concept_id} has no accepted match — '
                f'will wipe enrichment only (no rebuild).'
            )
            if verbose:
                self.stdout.write(
                    f'  pre: {pre["companies"]}c/{pre["genres"]}g/'
                    f'{pre["themes"]}t/{pre["engines"]}e/'
                    f'{pre["franchises"]}f{"  +json" if had_json else ""}'
                )
            for key, n in pre.items():
                stats[f'wiped_{key}'] = n
            if had_json:
                stats['json_cleared'] = 1

            if not dry_run:
                IGDBService._wipe_enrichment_through_rows(concept)
                update_fields = []
                if concept.igdb_genres:
                    concept.igdb_genres = []
                    update_fields.append('igdb_genres')
                if concept.igdb_themes:
                    concept.igdb_themes = []
                    update_fields.append('igdb_themes')
                if update_fields:
                    concept.save(update_fields=update_fields)
            return

        pre = self._count_existing(concept)
        for key, n in pre.items():
            stats[f'wiped_{key}'] = n

        if dry_run:
            if verbose:
                self.stdout.write(
                    f'{concept.concept_id} would wipe+rebuild: '
                    f'{pre["companies"]}c/{pre["genres"]}g/'
                    f'{pre["themes"]}t/{pre["engines"]}e/'
                    f'{pre["franchises"]}f'
                )
            return

        try:
            IGDBService._apply_enrichment(match, match.raw_response)
        except Exception as exc:
            stats['errors'] += 1
            self.stdout.write(self.style.ERROR(
                f'{concept.concept_id} ERROR during rebuild: {exc}'
            ))
            return

        stats['processed'] = 1
        post = self._count_existing(concept)
        for key, n in post.items():
            stats[f'rebuilt_{key}'] = n
        if verbose:
            self.stdout.write(self.style.SUCCESS(
                f'{concept.concept_id} rebuilt: '
                f'{post["companies"]}c/{post["genres"]}g/'
                f'{post["themes"]}t/{post["engines"]}e/'
                f'{post["franchises"]}f'
            ))

    # -------------------------------------------------------------------
    # Shared helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _empty_stats():
        return {
            'processed': 0,
            'skipped_no_raw': 0,
            'errors': 0,
            'wiped_companies': 0,
            'wiped_genres': 0,
            'wiped_themes': 0,
            'wiped_engines': 0,
            'wiped_franchises': 0,
            'json_cleared': 0,
            'rebuilt_companies': 0,
            'rebuilt_genres': 0,
            'rebuilt_themes': 0,
            'rebuilt_engines': 0,
            'rebuilt_franchises': 0,
        }

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

        wipe_verb = 'Would wipe' if dry_run else 'Wiped'
        self.stdout.write('')
        self.stdout.write(f'  Bulk wipe phase:')
        self.stdout.write(f'    {wipe_verb} ConceptCompany   rows: {stats["wiped_companies"]}')
        self.stdout.write(f'    {wipe_verb} ConceptGenre     rows: {stats["wiped_genres"]}')
        self.stdout.write(f'    {wipe_verb} ConceptTheme     rows: {stats["wiped_themes"]}')
        self.stdout.write(f'    {wipe_verb} ConceptEngine    rows: {stats["wiped_engines"]}')
        self.stdout.write(f'    {wipe_verb} ConceptFranchise rows: {stats["wiped_franchises"]}')
        self.stdout.write(f'    Concept JSON denorms cleared on: {stats["json_cleared"]} concept(s)')

        self.stdout.write('')
        self.stdout.write(f'  Rebuild phase:')
        self.stdout.write(f'    Concepts processed: {stats["processed"]}')
        if stats['skipped_no_raw']:
            self.stdout.write(f'    Skipped (no raw):   {stats["skipped_no_raw"]}')
        if stats['errors']:
            self.stdout.write(self.style.ERROR(f'    Errors:             {stats["errors"]}'))
        rebuild_verb = 'Would rebuild' if dry_run else 'Rebuilt from raw_response'
        self.stdout.write(f'    {rebuild_verb}:')
        self.stdout.write(f'      ConceptCompany   rows: {stats["rebuilt_companies"]}')
        self.stdout.write(f'      ConceptGenre     rows: {stats["rebuilt_genres"]}')
        self.stdout.write(f'      ConceptTheme     rows: {stats["rebuilt_themes"]}')
        self.stdout.write(f'      ConceptEngine    rows: {stats["rebuilt_engines"]}')
        self.stdout.write(f'      ConceptFranchise rows: {stats["rebuilt_franchises"]}')
