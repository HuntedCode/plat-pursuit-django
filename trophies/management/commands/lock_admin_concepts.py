from django.core.management.base import BaseCommand
from trophies.models import Game


class Command(BaseCommand):
    help = "Lock concept_lock on games whose concept has an admin-duplicate suffix (-1, -2, etc) or PP_ stub prefix"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Preview changes without applying them')

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        games = Game.objects.filter(
            concept__isnull=False,
            concept_lock=False,
            concept__concept_id__regex=r'-\d+$',
        ).select_related('concept')

        count = games.count()
        if count == 0:
            self.stdout.write("No unlocked games with admin-duplicate concepts found.")
            return

        if dry_run:
            self.stdout.write(f"[DRY RUN] {count} game(s) would be locked:")
            for game in games:
                self.stdout.write(f"  {game.title_name} -> {game.concept.concept_id}")
            return

        for game in games:
            self.stdout.write(f"  Locking: {game.title_name} -> {game.concept.concept_id}")
        games.update(concept_lock=True)
        self.stdout.write(f"Locked concept on {count} game(s).")
