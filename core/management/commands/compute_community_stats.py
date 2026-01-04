from django.utils import timezone
from django.core.management.base import BaseCommand
from django.conf import settings
from django.core.cache import cache
from core.services.stats import compute_community_stats
import logging

logger = logging.getLogger(__name__)

STATS_CACHE_KEY = 'community_stats'
STATS_CACHE_TIMEOUT = 3600

class Command(BaseCommand):
    def handle(self, *args, **options):
        today_utc = timezone.now().date().isoformat()
        now_utc = timezone.now()
        key = f"{STATS_CACHE_KEY}_{today_utc}_{now_utc.hour:02d}"
        try:
            stats = compute_community_stats()
            cache.set(key, stats, STATS_CACHE_TIMEOUT * 2)
            logger.info(f"Community stats cached for {key}")
        except Exception as e:
            logger.error(f"Failed to computer community stats: {e}")