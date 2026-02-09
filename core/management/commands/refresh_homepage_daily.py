import logging

from django.utils import timezone
from django.core.management.base import BaseCommand
from django.core.cache import cache

from core.services.featured import get_featured_games
from core.services.featured_badges import get_featured_badges
from core.services.featured_checklists import get_featured_checklists

logger = logging.getLogger(__name__)

DAILY_JOBS = [
    {
        'name': 'Featured Games',
        'key': 'featured_games',
        'timeout': 86400,
        'func': get_featured_games,
    },
    {
        'name': 'Featured Badges',
        'key': 'featured_badges',
        'timeout': 86400,
        'func': get_featured_badges,
    },
    {
        'name': 'Featured Checklists',
        'key': 'featured_checklists',
        'timeout': 86400,
        'func': get_featured_checklists,
    },
]


class Command(BaseCommand):
    help = 'Refresh all daily homepage cache data (featured games, badges, checklists)'

    def handle(self, *args, **options):
        today_utc = timezone.now().date().isoformat()

        for job in DAILY_JOBS:
            cache_key = f"{job['key']}_{today_utc}"
            try:
                data = job['func']()
                cache.set(cache_key, data, job['timeout'] * 2)
                self.stdout.write(self.style.SUCCESS(f"{job['name']} cached: {cache_key}"))
            except Exception as e:
                logger.exception(f"Failed to refresh {job['name']}")
                self.stdout.write(self.style.ERROR(f"{job['name']} failed: {e}"))
