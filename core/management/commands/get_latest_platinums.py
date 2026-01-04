from django.utils import timezone
from django.core.management.base import BaseCommand
from django.conf import settings
from django.core.cache import cache
from core.services.latest_platinums import get_latest_platinums
import logging

logger = logging.getLogger(__name__)

LATEST_PLATINUMS_KEY = 'latest_platinums'
LATEST_PLATINUMS_TIMEOUT = 3600

class Command(BaseCommand):
    def handle(self, *args, **options):
        today_utc = timezone.now().date().isoformat()
        now_utc = timezone.now()
        key = f"{LATEST_PLATINUMS_KEY}_{today_utc}_{now_utc.hour:02d}"
        try:
            platinums = get_latest_platinums()
            cache.set(key, platinums, LATEST_PLATINUMS_TIMEOUT * 2)
            logger.info(f"Latest platinums cached for {key}")
        except Exception as e:
            logger.error(f"Failed to get latest platinums: {e}")
