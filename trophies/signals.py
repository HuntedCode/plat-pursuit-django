from datetime import datetime, timezone
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.cache import cache
from core.services.latest_platinums import OLDEST_TS_KEY
from .models import EarnedTrophy
import logging

logger = logging.getLogger(__name__)

@receiver(post_save, sender=EarnedTrophy, dispatch_uid="invalidate_latest_platinums")
def invalidate_latest_platinums(sender, instance, created, **kwargs):
    if instance.trophy.trophy_type.lower() != 'platinum' or not instance.earned:
        return
    
    oldest_ts_str = cache.get(OLDEST_TS_KEY)
    if not oldest_ts_str:
        invalidate_cache()
        return
    
    try:
        oldest_ts = datetime.fromisoformat(oldest_ts_str)
        if instance.earned_date_time > oldest_ts:
            invalidate_cache()
    except (ValueError, TypeError):
        logger.warning("Invalid oldest TS in cache. Skipping.")
        invalidate_cache()

def invalidate_cache():
    now = timezone.now()
    hour_key = f"{now.date().isoformat()}_{now.hour:02d}"
    cache_key = f"latest_platinums_{hour_key}"
    cache.delete(cache_key)
    logger.info("Invalidated latest platinums cache for new qualifying platinum")