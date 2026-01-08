from django.core.management.base import BaseCommand
from django.core.cache import cache
from trophies.models import Badge
from trophies.utils import compute_earners_leaderboard, compute_progress_leaderboard

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
                self.stdout.write(f"Updated earners leaderboard for series {slug}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed updating earners leaderboard for series {slug}: {e}"))
        
            try:
                data = compute_progress_leaderboard(slug)
                cache.set(f"lb_progress_{slug}", data, 3600 * 2)
                self.stdout.write(f"Updated progress leaderboard for series {slug}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed updating progress leaderboard for series {slug}: {e}"))
        
            processed_count += 1
        self.stdout.write(self.style.SUCCESS(f"Processed {processed_count} distinct series."))