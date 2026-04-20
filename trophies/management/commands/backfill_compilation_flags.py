import time

from django.core.management.base import BaseCommand

from trophies.models import IGDBMatch
from trophies.services.igdb_service import IGDBService


class Command(BaseCommand):
    help = (
        "Backfill IGDBMatch.is_likely_compilation from each row's stored "
        "raw_response. Runs in-memory over the existing data, no IGDB API "
        "calls. Use after the v4 refresh pass to populate compilation flags "
        "on already-accepted matches."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report counts without writing. Safe to run any time.',
        )
        parser.add_argument(
            '--only-unflagged', action='store_true',
            help='Skip rows where is_likely_compilation is already True. '
                 'Useful for re-runs that should not re-examine already-flagged rows.',
        )
        parser.add_argument(
            '--batch-size', type=int, default=500,
            help='bulk_update batch size (default: 500).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        only_unflagged = options['only_unflagged']
        batch_size = options['batch_size']

        qs = IGDBMatch.objects.filter(status__in=['accepted', 'auto_accepted'])
        if only_unflagged:
            qs = qs.filter(is_likely_compilation=False)

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS('No IGDBMatch rows in scope.'))
            return

        self.stdout.write(
            f'Scanning {total} IGDBMatch row(s) '
            f'({"accepted + auto_accepted" if not only_unflagged else "accepted + auto_accepted, currently unflagged"}).'
        )

        start = time.time()
        flagged_now = 0
        unflagged_now = 0  # rows whose stored flag was True but shouldn't be
        skipped_no_game_type = 0
        to_update = []

        # chunk_size required by Django 5.x when prefetch_related is in play.
        # We only select basic fields here, but future-proof with chunked iter.
        for match in qs.iterator(chunk_size=batch_size):
            raw = match.raw_response or {}
            game_type = raw.get('game_type')

            if not isinstance(game_type, dict):
                # Pre-refresh row: cannot classify. Leave flag as-is.
                skipped_no_game_type += 1
                continue

            should_be_flagged = IGDBService._is_compilation_response(raw)

            if should_be_flagged != match.is_likely_compilation:
                match.is_likely_compilation = should_be_flagged
                to_update.append(match)
                if should_be_flagged:
                    flagged_now += 1
                else:
                    unflagged_now += 1

                if len(to_update) >= batch_size and not dry_run:
                    IGDBMatch.objects.bulk_update(to_update, ['is_likely_compilation'])
                    to_update = []

        # Flush any remaining updates.
        if to_update and not dry_run:
            IGDBMatch.objects.bulk_update(to_update, ['is_likely_compilation'])

        elapsed = time.time() - start
        self.stdout.write('')
        self.stdout.write(f'Scan complete in {elapsed:.1f}s.')
        self.stdout.write(f'  Flagged as compilation:          {flagged_now}')
        self.stdout.write(f'  Unflagged (was wrong, now False): {unflagged_now}')
        self.stdout.write(f'  Skipped (no game_type in raw):    {skipped_no_game_type}')

        if skipped_no_game_type:
            self.stdout.write(self.style.WARNING(
                f'  Note: {skipped_no_game_type} row(s) have not been refreshed against '
                f'the IGDB v4 fields yet. Run `enrich_from_igdb --refresh` to populate '
                f'game_type on those rows, then re-run this backfill.'
            ))

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\n[DRY RUN] No writes were made. Re-run without --dry-run to apply.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'\nWrote {flagged_now + unflagged_now} change(s) to IGDBMatch.is_likely_compilation.'
            ))
