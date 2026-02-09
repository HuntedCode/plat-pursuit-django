import logging

from django.utils import timezone
from django.core.management.base import BaseCommand
from django.core.cache import cache

from core.services.stats import compute_community_stats
from core.services.latest_badges import get_latest_badges
from core.services.whats_new import get_whats_new

logger = logging.getLogger(__name__)

HOURLY_JOBS = [
    {
        'name': 'Community Stats',
        'key': 'community_stats',
        'timeout': 3600,
        'func': compute_community_stats,
    },
    {
        'name': 'Latest Badges',
        'key': 'latest_badges',
        'timeout': 3600,
        'func': get_latest_badges,
    },
    {
        'name': 'What\'s New',
        'key': 'whats_new',
        'timeout': 3600,
        'func': get_whats_new,
    },
]


class Command(BaseCommand):
    help = 'Refresh all hourly homepage cache data (community stats, latest badges, what\'s new)'

    def handle(self, *args, **options):
        now_utc = timezone.now()
        today_utc = now_utc.date().isoformat()
        hour = now_utc.hour

        for job in HOURLY_JOBS:
            cache_key = f"{job['key']}_{today_utc}_{hour:02d}"
            try:
                data = job['func']()
                cache.set(cache_key, data, job['timeout'] * 2)
                self.stdout.write(self.style.SUCCESS(f"{job['name']} cached: {cache_key}"))
            except Exception as e:
                logger.exception(f"Failed to refresh {job['name']}")
                self.stdout.write(self.style.ERROR(f"{job['name']} failed: {e}"))
