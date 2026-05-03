"""Recompute PlayStation release date fields on IGDBMatch from raw_response.

Backfills two columns:

  * `igdb_first_release_date` — the earliest PS-platform release date.
    Historically stamped with IGDB's global `first_release_date` (the
    earliest worldwide release on any platform), which gave wrong dates
    for PC-first / multi-platform games (Hollow Knight, Cuphead, Hades,
    Stardew Valley, etc.) where PC predates PSN by months/years.

  * `igdb_ps_release_dates` — the new per-platform JSON list. Newly
    introduced in migration 0221; needs initial population on every
    pre-existing row.

Both values are derived from the per-platform `release_dates` array
already persisted in `raw_response` — no IGDB API calls needed.
"""

import time
from datetime import datetime, timezone as dt_timezone

from django.core.management.base import BaseCommand

from trophies.models import IGDBMatch
from trophies.services.igdb_service import IGDBService


# Heavy IGDBMatch fields the backfill never reads. raw_response IS read
# (we extract release_dates from it), so it can't be deferred here.
_DEFERRED_FIELDS = (
    'igdb_summary',
    'igdb_storyline',
    'franchise_names',
    'similar_game_igdb_ids',
    'external_urls',
)

_UPDATE_FIELDS = ('igdb_first_release_date', 'igdb_ps_release_dates')


class Command(BaseCommand):
    help = (
        "Recompute IGDBMatch.igdb_first_release_date (earliest PS launch) "
        "and populate IGDBMatch.igdb_ps_release_dates (per-platform list) "
        "from raw_response.release_dates. Fixes rows previously stamped "
        "with IGDB's global first_release_date and seeds the new "
        "per-platform column added in migration 0221."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report counts without writing. Safe to run any time.',
        )
        parser.add_argument(
            '--batch-size', type=int, default=500,
            help='bulk_update batch size (default: 500).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        # All non-no_match rows. The date bug applied uniformly during
        # enrichment regardless of acceptance status, so pending and
        # rejected rows have wrong dates too. Updating the date column
        # has no side effects on those rows beyond data quality —
        # rejected matches stay rejected, pending stay pending.
        qs = (
            IGDBMatch.objects
            .filter(status__in=['accepted', 'auto_accepted', 'pending', 'rejected'])
            .defer(*_DEFERRED_FIELDS)
        )
        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS('No IGDBMatch rows in scope.'))
            return

        self.stdout.write(f'Scanning {total} IGDBMatch row(s) (accepted + auto_accepted + pending + rejected).')

        start = time.time()
        moved_forward = 0
        moved_backward = 0
        cleared = 0
        ps_dates_seeded = 0
        ps_dates_updated = 0
        skipped_unchanged = 0
        skipped_parse_error = 0
        to_update = []

        for match in qs.iterator(chunk_size=batch_size):
            raw = match.raw_response or {}

            # Earliest PS release date.
            ts = IGDBService._earliest_ps_release_timestamp(raw)
            new_first = None
            if ts:
                try:
                    new_first = datetime.fromtimestamp(ts, tz=dt_timezone.utc)
                except (ValueError, OSError):
                    skipped_parse_error += 1
                    continue

            # Per-platform PS release dates list.
            new_ps_list = IGDBService._ps_release_dates_for_storage(raw)

            current_first = match.igdb_first_release_date
            current_ps_list = match.igdb_ps_release_dates or []

            first_changed = current_first != new_first
            ps_list_changed = current_ps_list != new_ps_list

            if not first_changed and not ps_list_changed:
                skipped_unchanged += 1
                continue

            # Track which transition happened for first-release-date.
            if first_changed:
                if new_first is None:
                    cleared += 1
                elif current_first is None or new_first > current_first:
                    # PC-first port pattern: PS release later than what was stored.
                    moved_forward += 1
                else:
                    # Earliest PS release predates the previously stored date.
                    # Rare but possible if global first_release_date was wrong.
                    moved_backward += 1

            if ps_list_changed:
                if not current_ps_list:
                    ps_dates_seeded += 1
                else:
                    ps_dates_updated += 1

            match.igdb_first_release_date = new_first
            match.igdb_ps_release_dates = new_ps_list
            to_update.append(match)

            if len(to_update) >= batch_size and not dry_run:
                IGDBMatch.objects.bulk_update(to_update, list(_UPDATE_FIELDS))
                to_update = []

        if to_update and not dry_run:
            IGDBMatch.objects.bulk_update(to_update, list(_UPDATE_FIELDS))

        elapsed = time.time() - start
        first_changed_total = moved_forward + moved_backward + cleared
        ps_changed_total = ps_dates_seeded + ps_dates_updated
        self.stdout.write('')
        self.stdout.write(f'Scan complete in {elapsed:.1f}s.')
        self.stdout.write('  igdb_first_release_date:')
        self.stdout.write(f'    Moved forward (PS later than global): {moved_forward}')
        self.stdout.write(f'    Moved backward (PS earlier):          {moved_backward}')
        self.stdout.write(f'    Cleared (no PS or global data):       {cleared}')
        self.stdout.write('  igdb_ps_release_dates:')
        self.stdout.write(f'    Seeded (column was empty):            {ps_dates_seeded}')
        self.stdout.write(f'    Updated (column had different data):  {ps_dates_updated}')
        self.stdout.write(f'  Already correct (both fields):          {skipped_unchanged}')
        if skipped_parse_error:
            self.stdout.write(self.style.WARNING(
                f'  Parse errors:                           {skipped_parse_error}'
            ))

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\n[DRY RUN] No writes were made. Re-run without --dry-run to apply.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'\nApplied {first_changed_total} first-release-date update(s) '
                f'and {ps_changed_total} per-platform list update(s).'
            ))
