from django.core.management.base import BaseCommand
from trophies.models import Game
from trophies.utils import NA_REGION_CODES, EU_REGION_CODES, JP_REGION_CODES, AS_REGION_CODES

class Command(BaseCommand):
    def handle(self, *args, **options):
        games = Game.objects.all()
        for game in games:
            if game.region:
                old_regions = game.region
                game.region = []
                game.save(update_fields=['region'])

                for region in old_regions:
                    game.add_region(region)