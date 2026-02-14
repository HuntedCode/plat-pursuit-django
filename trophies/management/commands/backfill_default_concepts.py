from django.core.management.base import BaseCommand
from trophies.models import Game, Concept


class Command(BaseCommand):
    help = "Create stub Concepts for games that don't have one"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Preview how many games would be affected without making changes')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        games = Game.objects.filter(concept__isnull=True)
        count = games.count()

        if count == 0:
            self.stdout.write("All games already have concepts. Nothing to do.")
            return

        if dry_run:
            self.stdout.write(f"[DRY RUN] {count} game(s) without concepts found. No changes made.")
            return

        created = 0
        for game in games:
            concept = Concept.create_default_concept(game)
            game.add_concept(concept)
            created += 1
            self.stdout.write(f"  Created concept {concept.concept_id} for {game.title_name}")

        self.stdout.write(f"Backfill complete. {created} stub concept(s) created.")
