from django.core.management.base import BaseCommand
from trophies.models import Concept

class Command(BaseCommand):
    def handle(self, *args, **options):
        concepts_qs = Concept.objects.all()
        for concept in concepts_qs:
            if concept.media:
                bg_url = ''
                for img in concept.media:
                    if img.get('type') == 'GAMEHUB_COVER_ART':
                        bg_url = img.get('url')
                    elif bg_url == '' and img.get('type') == 'BACKGROUND_LAYER_ART':
                        bg_url = img.get('url')
                concept.bg_url = bg_url
                concept.save(update_fields=['bg_url'])