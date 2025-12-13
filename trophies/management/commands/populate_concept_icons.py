from django.core.management.base import BaseCommand
from trophies.models import Concept

class Command(BaseCommand):
    def handle(self, *args, **options):
        concept_qs = Concept.objects.all()

        for concept in concept_qs:
            if concept.media:
                for img in concept.media:
                    if img.get('type') == 'MASTER':
                        concept.concept_icon_url = img.get('url')
                        concept.save(update_fields=['concept_icon_url'])