from django.core.management.base import BaseCommand
from trophies.models import Game

class Command(BaseCommand):
    def handle(self, *args, **options):
        games = Game.objects.all()
        for game in games:
            plat = game.trophies.filter(trophy_type='platinum').first()
            print(plat)
            if plat:
                game.update_is_shovelware(plat.trophy_earn_rate)