from django.core.management.base import BaseCommand
from trophies.models import Badge, Concept

class Command(BaseCommand):
    help = 'Seeds sample badges for testing'

    def handle(self, *args, **options):
        sample_concepts = Concept.objects.filter(unified_title__icontains='ratchet')

        for tier in [1, 2, 3, 4]:
            badge = Badge.objects.create(
                name=f"Platinum Bolt Tier {tier}",
                series_slug='ratchet-and-clank',
                description=f"Earn plats/100%s in the Ratchet & Clank series (Tier {tier})",
                tier=tier,
                badge_type='series',
                requires_all=True,
            )
            badge.concepts.set(sample_concepts)
            print(f"Created {badge}")