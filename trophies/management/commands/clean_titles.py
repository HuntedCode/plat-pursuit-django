from django.core.management.base import BaseCommand
import re
from trophies.models import Game, Concept, Trophy

class Command(BaseCommand):
    help = 'Remove TM/® symbols from existing titles'

    def handle(self, *args, **options):
        games = Game.objects.all()
        updated_count = 0
        for game in games:
            original_title = game.title_name
            cleaned_title = re.sub(r'[™®]|(\bTM\b)|(\(R\))', '', original_title).strip()
            if cleaned_title != original_title:
                game.title_name = cleaned_title
                game.save(update_fields=['title_name'])
                updated_count += 1
        self.stdout.write(f"Cleaned {updated_count} games successfully.")

        concepts = Concept.objects.all()
        updated_count = 0
        for concept in concepts:
            original_title = concept.unified_title
            cleaned_title = re.sub(r'[™®]|(\bTM\b)|(\(R\))', '', original_title).strip()
            if cleaned_title != original_title:
                concept.unified_title = cleaned_title
                concept.save(update_fields=['unified_title'])
                updated_count += 1
        self.stdout.write(f"Cleaned {updated_count} concepts successfully.")

        trophies = Trophy.objects.all()
        updated_count = 0
        for trophy in trophies:
            original_title = trophy.trophy_name
            cleaned_title = re.sub(r'[™®]|(\bTM\b)|(\(R\))', '', original_title).strip()
            if cleaned_title != original_title:
                trophy.trophy_name = cleaned_title
                trophy.save(update_fields=['trophy_name'])
                updated_count += 1
        self.stdout.write(f"Cleaned {updated_count} trophies successfully.")

        self.stdout.write(self.style.SUCCESS('All titles cleaned successfully!'))