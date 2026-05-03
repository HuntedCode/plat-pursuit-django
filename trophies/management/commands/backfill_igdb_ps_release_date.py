"""Recompute IGDBMatch.igdb_first_release_date from raw_response.

Historical behavior: enrichment stamped this field with IGDB's global
`first_release_date`, the earliest worldwide release across any
platform. For PC-first / multi-platform games (Hollow Knight, Cuphead,
Hades, Stardew Valley, etc.) that's a PC release months or years
before the PSN launch — the date didn't reflect the PlayStation
release at all.

Fix forward: enrichment now derives the value from the per-platform
`release_dates` array, filtered to PS_PLATFORM_IDS (with fallback to
the global `first_release_date` when no PS-specific entries exist).

This backfill applies the same logic to existing rows, reading the
already-persisted `raw_response` payload — no IGDB API calls needed.
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


class Command(BaseCommand):
    help = (
        "Recompute IGDBMatch.igdb_first_release_date as the earliest "
        "PlayStation-platform release date from raw_response. Fixes rows "
        "stamped with IGDB's global first_release_date, which gave "
        "incorrect dates for PC-first / multi-platform games."
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
        skipped_unchanged = 0
        skipped_parse_error = 0
        to_update = []

        for match in qs.iterator(chunk_size=batch_size):
            raw = match.raw_response or {}
            ts = IGDBService._earliest_ps_release_timestamp(raw)

            new_value = None
            if ts:
                try:
                    new_value = datetime.fromtimestamp(ts, tz=dt_timezone.utc)
                except (ValueError, OSError):
                    skipped_parse_error += 1
                    continue

            current = match.igdb_first_release_date
            if current == new_value:
                skipped_unchanged += 1
                continue

            if new_value is None:
                cleared += 1
            elif current is None or new_value > current:
                # PC-first port pattern: PS release later than worldwide first.
                moved_forward += 1
            else:
                # PS-first / earliest PS release predates the previously stored
                # date. Rare but possible if IGDB's global first_release_date
                # was wrong or post-dated for some reason.
                moved_backward += 1

            match.igdb_first_release_date = new_value
            to_update.append(match)

            if len(to_update) >= batch_size and not dry_run:
                IGDBMatch.objects.bulk_update(to_update, ['igdb_first_release_date'])
                to_update = []

        if to_update and not dry_run:
            IGDBMatch.objects.bulk_update(to_update, ['igdb_first_release_date'])

        elapsed = time.time() - start
        total_changed = moved_forward + moved_backward + cleared
        self.stdout.write('')
        self.stdout.write(f'Scan complete in {elapsed:.1f}s.')
        self.stdout.write(f'  Moved forward (PS later than global): {moved_forward}')
        self.stdout.write(f'  Moved backward (PS earlier):          {moved_backward}')
        self.stdout.write(f'  Cleared (no PS or global data):       {cleared}')
        self.stdout.write(f'  Already correct:                      {skipped_unchanged}')
        if skipped_parse_error:
            self.stdout.write(self.style.WARNING(
                f'  Parse errors:                         {skipped_parse_error}'
            ))

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\n[DRY RUN] No writes were made. Re-run without --dry-run to apply.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'\nApplied {total_changed} field update(s).'
            ))
