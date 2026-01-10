from django.core.management.base import BaseCommand
from django.core.cache import cache
from django.utils import timezone
from trophies.models import Badge
from trophies.utils import compute_earners_leaderboard, compute_progress_leaderboard, compute_total_progress_leaderboard, compute_badge_xp_leaderboard

class Command(BaseCommand):
    def handle(self, *args, **options):
        slugs_qs = Badge.objects.values_list('series_slug', flat=True).distinct().order_by('series_slug')
        unique_slugs = list(set(slugs_qs))
        if not unique_slugs:
            self.stdout.write(self.style.ERROR(f"No series found to update leaderboards."))
            return
        
        cache_timeout = 25200 # 7 Hours

        try:
            data = compute_total_progress_leaderboard()
            key = 'lb_total_progress'
            cache.set(key, data, cache_timeout)
            cache.set(f"{key}_refresh_time", timezone.now().isoformat(), cache_timeout)
            self.stdout.write('Updated total progress leaderboard.')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed updating total progress leaderboard: {e}"))
        
        try:
            data = compute_badge_xp_leaderboard()
            key = 'lb_total_xp'
            cache.set(key, data, cache_timeout)
            cache.set(f"{key}_refresh_time", timezone.now().isoformat(), cache_timeout)
            self.stdout.write('Updated total XP leaderboard.')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed updating total XP leaderboard: {e}"))

        processed_count = 0
        for slug in unique_slugs:
            try:
                data = compute_earners_leaderboard(slug)
                key = f"lb_earners_{slug}"
                cache.set(key, data, cache_timeout)
                cache.set(f"{key}_refresh_time", timezone.now().isoformat(), cache_timeout)
                self.stdout.write(f"Updated earners leaderboard for series {slug}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed updating earners leaderboard for series {slug}: {e}"))
        
            try:
                data = compute_progress_leaderboard(slug)
                key = f"lb_progress_{slug}"
                cache.set(key, data, cache_timeout)
                cache.set(f"{key}_refresh_time", timezone.now().isoformat(), cache_timeout)
                self.stdout.write(f"Updated progress leaderboard for series {slug}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed updating progress leaderboard for series {slug}: {e}"))
        
            processed_count += 1
        self.stdout.write(self.style.SUCCESS(f"Processed {processed_count} distinct series."))