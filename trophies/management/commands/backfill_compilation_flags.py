import time

from django.core.management.base import BaseCommand

from trophies.models import IGDBMatch
from trophies.services.igdb_service import IGDBService


class Command(BaseCommand):
    help = (
        "Backfill IGDBMatch fields derived from raw_response.game_type: "
        "is_likely_compilation (Bundle/Pack detection) and game_category "
        "(the numeric category ID). Runs in-memory over existing data, no "
        "IGDB API calls. Use after the v4 refresh pass to populate these "
        "fields on already-accepted matches whose old match logic read from "
        "the deprecated `category` field and got None."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report counts without writing. Safe to run any time.',
        )
        parser.add_argument(
            '--only-missing', action='store_true',
            help='Skip rows where both is_likely_compilation and game_category '
                 'already agree with the raw_response. Useful for fast re-runs '
                 'after the first pass has populated most rows.',
        )
        parser.add_argument(
            '--batch-size', type=int, default=500,
            help='bulk_update batch size (default: 500).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        only_missing = options['only_missing']
        batch_size = options['batch_size']

        qs = IGDBMatch.objects.filter(status__in=['accepted', 'auto_accepted'])
        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS('No IGDBMatch rows in scope.'))
            return

        self.stdout.write(f'Scanning {total} IGDBMatch row(s) (accepted + auto_accepted).')

        start = time.time()
        compilation_flag_changes = 0
        category_changes = 0
        skipped_no_game_type = 0
        skipped_unchanged = 0
        to_update = []

        for match in qs.iterator(chunk_size=batch_size):
            raw = match.raw_response or {}
            game_type = raw.get('game_type')

            if not isinstance(game_type, dict):
                skipped_no_game_type += 1
                continue

            correct_compilation = IGDBService._is_compilation_response(raw)
            correct_category = IGDBService._extract_game_category(raw)

            compilation_wrong = correct_compilation != match.is_likely_compilation
            category_wrong = correct_category != match.game_category

            if not compilation_wrong and not category_wrong:
                skipped_unchanged += 1
                continue

            if only_missing and match.game_category is not None and not compilation_wrong:
                # Fast-path: --only-missing skips rows where category is already
                # populated and the compilation flag matches. Leaves existing
                # non-null values alone.
                skipped_unchanged += 1
                continue

            if compilation_wrong:
                match.is_likely_compilation = correct_compilation
                compilation_flag_changes += 1
            if category_wrong:
                match.game_category = correct_category
                category_changes += 1
            to_update.append(match)

            if len(to_update) >= batch_size and not dry_run:
                IGDBMatch.objects.bulk_update(
                    to_update, ['is_likely_compilation', 'game_category'],
                )
                to_update = []

        if to_update and not dry_run:
            IGDBMatch.objects.bulk_update(
                to_update, ['is_likely_compilation', 'game_category'],
            )

        elapsed = time.time() - start
        self.stdout.write('')
        self.stdout.write(f'Scan complete in {elapsed:.1f}s.')
        self.stdout.write(f'  is_likely_compilation changes:   {compilation_flag_changes}')
        self.stdout.write(f'  game_category changes:           {category_changes}')
        self.stdout.write(f'  Skipped (already correct):       {skipped_unchanged}')
        self.stdout.write(f'  Skipped (no game_type in raw):   {skipped_no_game_type}')

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
                f'\nApplied {compilation_flag_changes + category_changes} field update(s).'
            ))
