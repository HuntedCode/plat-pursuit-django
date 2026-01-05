from django.utils import timezone
from django.core.management.base import BaseCommand
from django.core.cache import cache
from core.services.featured import get_featured_games

FEATURED_GAMES_KEY = 'featured_games'
FEATURED_GAMES_TIMEOUT = 86400

class Command(BaseCommand):
    def handle(self, *args, **options):
        today_utc = timezone.now().date().isoformat()
        key = f"{FEATURED_GAMES_KEY}_{today_utc}"
        try:
            featured = get_featured_games()
            cache.set(key, featured, FEATURED_GAMES_TIMEOUT * 2)
            self.stdout.write(self.style.SUCCESS(f"Featured games cached for {key}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to get featured games: {e}"))
