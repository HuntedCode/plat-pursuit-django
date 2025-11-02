from django.core.management.base import BaseCommand
from trophies.models import Game

class Command(BaseCommand):
    help = 'Populate played_count on Game from played_by relations.'

    def handle(self, *args, **options):
        for game in Game.objects.all():
            game.played_count = game.played_by.count()
            game.save(update_fields=['played_count'])
        self.stdout.write(self.style.SUCCESS('Game played_counts populated.'))