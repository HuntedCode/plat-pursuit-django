from django.core.management.base import BaseCommand
from trophies.models import Game, PublisherBlacklist

class Command(BaseCommand):
    def handle(self, *args, **options):
        publishers = Game.objects.filter(is_shovelware=True).values_list('concept__publisher_name', flat=True).exclude(concept__publisher_name='').exclude(concept__publisher_name__isnull=True).distinct()
        added = 0
        for pub in publishers:
            pb, created = PublisherBlacklist.objects.get_or_create(name=pub)
            added += 1 if created else 0
        self.stdout.write(self.style.SUCCESS(f"Added {added} publishers to blacklist!"))
        
        total = 0
        updated = 0
        publishers = PublisherBlacklist.objects.all()
        for pub in publishers:
            pub_games = Game.objects.filter(concept__publisher_name=pub.name)
            for game in pub_games:
                if game.is_shovelware == False:
                    game.is_shovelware = True
                    game.save(update_fields=['is_shovelware'])
                    updated += 1
            self.stdout.write(f"Marked {updated} games from {pub.name}")
            total += updated
        self.stdout.write(self.style.SUCCESS(f"Successfully marked {total} games from {len(publishers)} publishers!"))