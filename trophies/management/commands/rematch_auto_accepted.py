"""Re-run the IGDB matching pipeline against existing auto_accepted matches.

Phase 3 of the compilation-aware IGDB matching plan. After Phase 2 reworked the
pipeline (new search-input selection, best-so-far accumulation, Strategy 7
/search, Strategy 6 localized-name, Strategy 9 romanization), existing
auto_accepted matches may have been made with inferior inputs. This command
re-runs match_concept for each one and either:

  - skips silently when the new match points at the same IGDB id,
  - applies the new match when it clears the auto-accept threshold AND
    beats the stored confidence (clear upgrade),
  - writes a RematchSuggestion row for admin review otherwise (different
    IGDB id but below threshold, or below-or-equal confidence).

Human-accepted matches (status='accepted') are intentionally left alone —
the admin already approved those outcomes.
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from trophies.models import IGDBMatch, RematchSuggestion
from trophies.services.igdb_service import IGDBService


class Command(BaseCommand):
    help = (
        'Re-run IGDB matching against auto_accepted matches. Apply clear '
        'upgrades; write RematchSuggestion rows for the rest.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Do not write IGDBMatch updates or RematchSuggestion rows; '
                 'report what each row would do.',
        )
        parser.add_argument(
            '--concept-id', type=str,
            help='Limit to a single concept by concept_id (for spot-checks).',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Stop after processing N matches (for incremental runs).',
        )
        parser.add_argument(
            '--verbose', action='store_true',
            help='Print per-row outcome; otherwise only per-bucket rollups.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbose = options['verbose']
        limit = options['limit']
        threshold = settings.IGDB_AUTO_ACCEPT_THRESHOLD

        qs = (
            IGDBMatch.objects
            .filter(status='auto_accepted')
            .select_related('concept')
            .order_by('concept__concept_id')
        )
        if options['concept_id']:
            qs = qs.filter(concept__concept_id=options['concept_id'])

        total = qs.count()
        if total == 0:
            self.stdout.write('No auto_accepted matches to re-match.')
            return

        scope = f'{total} auto_accepted match(es)'
        if limit:
            scope += f' (capped at {limit})'
        self.stdout.write(f'Re-matching {scope}. Auto-apply threshold: {threshold:.2f}')
        if dry_run:
            self.stdout.write(self.style.WARNING('[DRY RUN] No writes will occur.'))

        stats = {
            'processed': 0,
            'same_id': 0,
            'upgraded': 0,
            'suggested': 0,
            'suggested_already_pending': 0,
            'no_new_match': 0,
            'errors': 0,
        }

        for i, igdb_match in enumerate(qs.iterator()):
            if limit and stats['processed'] >= limit:
                break

            stats['processed'] += 1
            concept = igdb_match.concept
            label = f'[{i + 1}/{total}] {concept.concept_id} "{concept.unified_title}"'

            try:
                result = IGDBService.match_concept(concept)
            except Exception as exc:
                stats['errors'] += 1
                self.stdout.write(self.style.ERROR(f'{label} ERROR during match: {exc}'))
                continue

            if not result:
                stats['no_new_match'] += 1
                if verbose:
                    self.stdout.write(self.style.WARNING(
                        f'{label} -> no match returned; keeping existing #{igdb_match.igdb_id}'
                    ))
                continue

            new_data, new_confidence, new_method = result
            new_igdb_id = new_data.get('id')

            # Case 1: same IGDB id. No action.
            if new_igdb_id == igdb_match.igdb_id:
                stats['same_id'] += 1
                if verbose:
                    self.stdout.write(
                        f'{label} -> same id #{new_igdb_id}, no change'
                    )
                continue

            old_confidence = igdb_match.match_confidence or 0.0
            is_upgrade = (
                new_confidence >= threshold and new_confidence > old_confidence
            )

            # Case 2: clear upgrade. Apply new match via process_match.
            if is_upgrade:
                stats['upgraded'] += 1
                self.stdout.write(self.style.SUCCESS(
                    f'{label} -> UPGRADE #{igdb_match.igdb_id} ({old_confidence:.2f}) '
                    f'-> #{new_igdb_id} ({new_confidence:.2f})'
                ))
                if not dry_run:
                    try:
                        IGDBService.process_match(concept, new_data, new_confidence, new_method)
                    except Exception as exc:
                        stats['errors'] += 1
                        self.stdout.write(self.style.ERROR(
                            f'{label} ERROR applying upgrade: {exc}'
                        ))
                continue

            # Case 3: different id but not a clear upgrade -> suggestion.
            if not dry_run:
                created = self._record_suggestion(
                    igdb_match, new_data, new_confidence, new_method
                )
                if created:
                    stats['suggested'] += 1
                else:
                    stats['suggested_already_pending'] += 1
            else:
                stats['suggested'] += 1

            if verbose or not dry_run:
                self.stdout.write(
                    f'{label} -> SUGGEST #{igdb_match.igdb_id} ({old_confidence:.2f}) '
                    f'vs #{new_igdb_id} ({new_confidence:.2f}) via {new_method}'
                )

        self._print_summary(stats, dry_run)

    @staticmethod
    def _record_suggestion(igdb_match, new_data, new_confidence, new_method):
        """Create or refresh a pending RematchSuggestion for this concept+proposed id.

        Returns True if a new row was created, False if an existing pending row
        was refreshed (same concept + proposed IGDB id already queued).
        """
        new_igdb_id = new_data.get('id')
        new_name = new_data.get('name', '') or ''

        with transaction.atomic():
            existing = (
                RematchSuggestion.objects
                .select_for_update()
                .filter(
                    concept_id=igdb_match.concept_id,
                    proposed_igdb_id=new_igdb_id,
                    status='pending',
                )
                .first()
            )
            if existing:
                existing.old_igdb_id = igdb_match.igdb_id
                existing.old_igdb_name = igdb_match.igdb_name
                existing.old_confidence = igdb_match.match_confidence
                existing.old_match_method = igdb_match.match_method
                existing.proposed_igdb_name = new_name
                existing.proposed_confidence = new_confidence
                existing.proposed_match_method = new_method
                existing.proposed_raw_response = new_data
                existing.save(update_fields=[
                    'old_igdb_id', 'old_igdb_name', 'old_confidence',
                    'old_match_method', 'proposed_igdb_name',
                    'proposed_confidence', 'proposed_match_method',
                    'proposed_raw_response',
                ])
                return False

            RematchSuggestion.objects.create(
                concept_id=igdb_match.concept_id,
                old_igdb_id=igdb_match.igdb_id,
                old_igdb_name=igdb_match.igdb_name,
                old_confidence=igdb_match.match_confidence,
                old_match_method=igdb_match.match_method,
                proposed_igdb_id=new_igdb_id,
                proposed_igdb_name=new_name,
                proposed_confidence=new_confidence,
                proposed_match_method=new_method,
                proposed_raw_response=new_data,
                status='pending',
            )
            return True

    def _print_summary(self, stats, dry_run):
        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'{prefix}Rematch Complete'))
        self.stdout.write(f'  Processed:             {stats["processed"]}')
        self.stdout.write(f'  Same id (no change):   {stats["same_id"]}')
        self.stdout.write(self.style.SUCCESS(
            f'  Upgraded (applied):    {stats["upgraded"]}'
        ))
        self.stdout.write(self.style.WARNING(
            f'  New suggestions:       {stats["suggested"]}'
        ))
        if stats['suggested_already_pending']:
            self.stdout.write(
                f'  Refreshed existing:    {stats["suggested_already_pending"]}'
            )
        if stats['no_new_match']:
            self.stdout.write(self.style.WARNING(
                f'  No new match returned: {stats["no_new_match"]}'
            ))
        if stats['errors']:
            self.stdout.write(self.style.ERROR(
                f'  Errors:                {stats["errors"]}'
            ))
        self.stdout.write(f'  Finished at:           {timezone.now().isoformat()}')
