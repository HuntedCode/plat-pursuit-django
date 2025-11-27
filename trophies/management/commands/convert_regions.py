from django.core.management.base import BaseCommand
from trophies.models import Game

class Command(BaseCommand):
    def handle(self, *args, **options):
        games = Game.objects.all()
        for game in games:
            if game.region:
                distinct_region = set()
                for r in game.region:
                    distinct_region.add(r)
                game.region = list(distinct_region)
                game.save(update_fields=['region'])