from django.core.management.base import BaseCommand
from trophies.models import Game, PublisherBlacklist

class Command(BaseCommand):
    def handle(self, *args, **options):
        publishers = Game.objects.filter(shovelware_status__in=['auto_flagged', 'manually_flagged']).values_list('concept__publisher_name', flat=True).exclude(concept__publisher_name='').exclude(concept__publisher_name__isnull=True).distinct()
        added = 0
        for pub in publishers:
            pb, created = PublisherBlacklist.objects.get_or_create(name=pub)
            added += 1 if created else 0
        self.stdout.write(self.style.SUCCESS(f"Added {added} publishers to blacklist!"))


