from django.core.management.base import BaseCommand
from trophies.models import Game
from trophies.util_modules.language import detect_asian_language

class Command(BaseCommand):
    def handle(self, *args, **options):
        games = Game.objects.filter(concept=None)
        self.stdout.write(f"Checking {games.count()} games...")
        updated = 0
        for game in games:
            region_code = detect_asian_language(game.title_name)
            if not region_code == 'Unknown':
                game.add_region(region_code)
                game.is_regional = True
                game.save(update_fields=['is_regional'])
                updated += 1
            else:
                trophy_group = game.trophy_groups.first()
                if trophy_group is not None:
                    region_code = detect_asian_language(trophy_group.trophy_group_name)
                    if not region_code == 'Unknown':
                        game.add_region(region_code)
                        game.is_regional = True
                        game.save(update_fields=['is_regional'])
                        updated += 1
        self.stdout.write(self.style.SUCCESS(f"{updated} games successfully updated!"))