from django.core.management.base import BaseCommand
from trophies.models import Game, TitleID
from trophies.util_modules.region import CONTENT_RATING_AUTHORITY_TO_REGION


class Command(BaseCommand):
    help = "Backfill Game.region from TitleID regions and Concept content_rating authority"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="Report changes without modifying the database")
        parser.add_argument('--verbose', action='store_true', help="Show per-game details")

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbose = options['verbose']

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: no changes will be saved"))

        # Phase A: Propagate regions from TitleID model to Game.region
        phase_a_count = self._phase_a_title_id_regions(dry_run, verbose)

        # Phase B: Infer region from Concept.content_rating authority
        # Re-queries region=[] so games updated by Phase A are excluded
        phase_b_count = self._phase_b_content_rating(dry_run, verbose)

        total = phase_a_count + phase_b_count
        self.stdout.write(self.style.SUCCESS(
            f"Backfill complete. {total} games updated "
            f"(Phase A: {phase_a_count} from TitleIDs, Phase B: {phase_b_count} from content ratings)"
        ))

    def _phase_a_title_id_regions(self, dry_run, verbose):
        """Propagate correct regions from TitleID objects to their associated Games."""
        self.stdout.write("\n--- Phase A: TitleID region propagation ---")

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
        for game in games.iterator():
            if not game.title_ids:
                continue

            for tid_str in game.title_ids:
                region = title_id_regions.get(tid_str)
                if region:
                    if verbose:
                        self.stdout.write(f"  {game.title_name} -> region '{region}' (from TitleID {tid_str})")
                    if not dry_run:
                        game.add_region(region)
                    updated += 1
                    break

        self.stdout.write(f"Phase A: {updated} games {'would be' if dry_run else ''} updated")
        return updated

    def _phase_b_content_rating(self, dry_run, verbose):
        """Infer region from Concept.content_rating authority for remaining empty-region games."""
        self.stdout.write("\n--- Phase B: Content rating authority inference ---")

        games = Game.objects.filter(
            region=[], region_lock=False, concept__isnull=False
        ).select_related('concept')
        total = games.count()
        self.stdout.write(f"Checking {total} remaining games with concepts...")

        updated = 0
        skipped = 0
        for game in games.iterator():
            content_rating = game.concept.content_rating
            if not content_rating:
                skipped += 1
                continue

            authority = content_rating.get('authority', '').upper()
            region_code = CONTENT_RATING_AUTHORITY_TO_REGION.get(authority)

            if region_code:
                if verbose:
                    self.stdout.write(f"  {game.title_name} -> region '{region_code}' (from {authority})")
                if not dry_run:
                    game.add_region(region_code)
                updated += 1
            else:
                skipped += 1
                if verbose and authority:
                    self.stdout.write(f"  {game.title_name} -> unknown authority '{authority}', skipped")

        self.stdout.write(f"Phase B: {updated} games {'would be' if dry_run else ''} updated, {skipped} skipped")
        return updated
