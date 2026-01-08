from django.core.management.base import BaseCommand
from django.core.cache import cache
from trophies.models import Badge
from trophies.utils import compute_earners_leaderboard

class Command(BaseCommand):
    def handle(self, *args, **options):
        series_slugs = Badge.objects.values_list('series_slug', flat=True).distinct()
        if not series_slugs:
            self.stdout.write(self.style.ERROR(f"No series found to update leaderboards."))
            return
        
        processed_count = 0
        for slug in series_slugs:
            try:
                data = compute_earners_leaderboard(slug)
                cache.set(f"lb_earners_date_{slug}", data, 3600 * 2)
                self.stdout.write(f"Updated leaderboard for series {slug}")
                processed_count += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed updating leaderboard for series {slug}: {e}"))
        self.stdout.write(self.style.SUCCESS(f"Processed {processed_count} distinct series."))