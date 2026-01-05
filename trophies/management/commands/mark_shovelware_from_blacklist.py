from django.core.management.base import BaseCommand
from trophies.models import Game, PublisherBlacklist

class Command(BaseCommand):
    def handle(self, *args, **options):
        publishers = PublisherBlacklist.objects.all()
        for pub in publishers:
            pub_games = Game.objects.filter(concept__publisher_name=pub.name)
            for game in pub_games:
                game.is_shovelware = True
                game.save(update_fields=['is_shovelware'])
            self.stdout.write(f"Marked {pub_games.count()} games from {pub.name}")
        self.stdout.write(self.style.SUCCESS(f"Successfully marked games from {publishers.count()} publishers!"))