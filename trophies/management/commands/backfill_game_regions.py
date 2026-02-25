from django.core.management.base import BaseCommand
from trophies.models import Game, TitleID


class Command(BaseCommand):
    help = "Backfill Game.region from TitleID regions (populated via populate_title_ids TSV)"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="Report changes without modifying the database")
        parser.add_argument('--verbose', action='store_true', help="Show per-game details")

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbose = options['verbose']

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: no changes will be saved"))

        games = Game.objects.filter(region=[], region_lock=False)
        total = games.count()
        self.stdout.write(f"Checking {total} games with empty region...")

        # Prefetch all TitleIDs with real regions into a lookup dict
        title_id_regions = dict(
            TitleID.objects.exclude(region='IP').exclude(region='')
            .values_list('title_id', 'region')
        )
        self.stdout.write(f"Loaded {len(title_id_regions)} TitleIDs with known regions")

        updated = 0
        skipped = 0
        for game in games.iterator():
            if not game.title_ids:
                skipped += 1
                continue

            region = None
            matched_tid = None
            for tid_str in game.title_ids:
                region = title_id_regions.get(tid_str)
                if region:
                    matched_tid = tid_str
                    break

            if region:
                if verbose:
                    self.stdout.write(f"  {game.title_name} -> region '{region}' (from TitleID {matched_tid})")
                if not dry_run:
                    game.add_region(region)
                updated += 1
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f"Backfill complete. {updated} games {'would be' if dry_run else ''} updated, {skipped} skipped"
        ))
