from django.core.management.base import BaseCommand
from trophies.models import Game, Concept


class Command(BaseCommand):
    help = "Create stub Concepts for games that don't have one"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Preview how many games would be affected without making changes')
        parser.add_argument('--batch-size', type=int, default=50, help='Number of games to update per batch (default: 50)')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']
        total = Game.objects.filter(concept__isnull=True).count()

        if total == 0:
            self.stdout.write("All games already have concepts. Nothing to do.")
            return

        if dry_run:
            self.stdout.write(f"[DRY RUN] {total} game(s) without concepts found. No changes made.")
            return

        created = 0
        batch = []

        for game in Game.objects.filter(concept__isnull=True).iterator():
            concept = Concept.create_default_concept(game)
            # Direct FK assignment — safe to skip add_concept() because:
            # 1. game.concept is None (our filter guarantees this)
            # 2. No old_concept to absorb, no cache keys to invalidate
            game.concept = concept
            game.concept_stale = False
            batch.append(game)
            created += 1
            self.stdout.write(f"  [{created}/{total}] Created concept {concept.concept_id} for {game.title_name}")

            if len(batch) >= batch_size:
                Game.objects.bulk_update(batch, ['concept', 'concept_stale'])
                batch = []

        if batch:
            Game.objects.bulk_update(batch, ['concept', 'concept_stale'])

        self.stdout.write(self.style.SUCCESS(f"Backfill complete. {created} stub concept(s) created."))
