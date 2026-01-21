from django.core.management.base import BaseCommand
import re
from trophies.models import Game, Concept, Trophy

class Command(BaseCommand):
    help = 'Remove TM/® symbols from existing titles'

    def handle(self, *args, **options):
        updated_items = []
        updated_count = 0
        for game in Game.objects.all().iterator(chunk_size=1000):
            original_title = game.title_name
            cleaned_title = re.sub(r'[™®]|(\bTM\b)|(\(R\))', '', original_title).strip()
            cleaned_title = re.sub('- trophy set', '', cleaned_title, flags=re.IGNORECASE).strip()
            cleaned_title = re.sub('trophy set', '', cleaned_title, flags=re.IGNORECASE).strip()
            cleaned_title = re.sub('- trophies', '', cleaned_title, flags=re.IGNORECASE).strip()
            cleaned_title = re.sub('trophies', '', cleaned_title, flags=re.IGNORECASE).strip()
            if cleaned_title != original_title:
                game.title_name = cleaned_title
                game.lock_title = True
                updated_items.append(game)
                updated_count += 1

                if len(updated_items) >= 1000:
                    Game.objects.bulk_update(updated_items, ['title_name', 'lock_title'])
                    updated_items = []
        if updated_items:
            Game.objects.bulk_update(updated_items, ['title_name', 'lock_title'])
            updated_items = []
        self.stdout.write(f"Cleaned {updated_count} games successfully.")

        updated_items = []
        updated_count = 0
        for concept in Concept.objects.all().iterator(chunk_size=1000):
            original_title = concept.unified_title
            cleaned_title = re.sub(r'[™®]|(\bTM\b)|(\(R\))', '', original_title).strip()
            if cleaned_title != original_title:
                concept.unified_title = cleaned_title
                updated_items.append(concept)
                updated_count += 1

                if len(updated_items) >= 1000:
                    Concept.objects.bulk_update(updated_items, ['unified_title'])
                    updated_items = []
        if updated_items:
            Concept.objects.bulk_update(updated_items, ['unified_title'])
        self.stdout.write(f"Cleaned {updated_count} concepts successfully.")

        updated_items = []
        updated_count = 0
        for trophy in Trophy.objects.all().iterator(chunk_size=1000):
            original_title = trophy.trophy_name
            cleaned_title = re.sub(r'[™®]|(\bTM\b)|(\(R\))', '', original_title).strip()
            if cleaned_title != original_title:
                trophy.trophy_name = cleaned_title
                updated_items.append(trophy)
                updated_count += 1

                if len(updated_items) >= 1000:
                    Trophy.objects.bulk_update(updated_items, ['trophy_name'])
                    updated_items = []
        if updated_items:
            Trophy.objects.bulk_update(updated_items, ['trophy_name'])
        self.stdout.write(f"Cleaned {updated_count} trophies successfully.")

        self.stdout.write(self.style.SUCCESS('All titles cleaned successfully!'))