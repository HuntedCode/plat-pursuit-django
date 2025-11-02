from django.core.management.base import BaseCommand
from trophies.models import Game

class Command(BaseCommand):
    help = 'Delete any game with 0 played_count.'

    def handle(self, *args, **options):
        for game in Game.objects.all():
            if game.played_count == 0:
                game.delete()
        self.stdout.write(self.style.SUCCESS('Deleted all games with 0 played_count.'))