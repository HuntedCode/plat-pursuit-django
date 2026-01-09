from django.core.management.base import BaseCommand
from django.core.cache import cache
from django.utils import timezone
from trophies.models import Badge
from trophies.utils import compute_earners_leaderboard, compute_progress_leaderboard

class Command(BaseCommand):
    def handle(self, *args, **options):
        slugs_qs = Badge.objects.values_list('series_slug', flat=True).distinct().order_by('series_slug')
        unique_slugs = list(set(slugs_qs))
        if not unique_slugs:
            self.stdout.write(self.style.ERROR(f"No series found to update leaderboards."))
            return
        
        processed_count = 0
        for slug in unique_slugs:
            try:
                data = compute_earners_leaderboard(slug)
                key = f"lb_earners_date_{slug}"
                cache.set(key, data, 3600 * 2)
                cache.set(f"{key}_refresh_time", timezone.now().isoformat(), 3600 * 2)
                self.stdout.write(f"Updated earners leaderboard for series {slug}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed updating earners leaderboard for series {slug}: {e}"))
        
            try:
                data = compute_progress_leaderboard(slug)
                key = f"lb_progress_{slug}"
                cache.set(key, data, 3600 * 2)
                cache.set(f"{key}_refresh_time", timezone.now().isoformat(), 3600 * 2)
                self.stdout.write(f"Updated progress leaderboard for series {slug}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed updating progress leaderboard for series {slug}: {e}"))
        
            processed_count += 1
        self.stdout.write(self.style.SUCCESS(f"Processed {processed_count} distinct series."))