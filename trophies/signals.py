from datetime import datetime, timedelta
from django.db import transaction
from django.db.models.signals import pre_save, post_save
from django.db.models import F
from django.dispatch import receiver
from django.core.cache import cache
from django.utils import timezone
from core.services.latest_platinums import OLDEST_TS_KEY
from core.services.latest_rares import PSN_THRESHOLD_KEY, PP_THRESHOLD_KEY
from .models import EarnedTrophy, ProfileGame
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

@receiver(pre_save, sender=EarnedTrophy)
def capture_old_earned(sender, instance, **kwargs):
    if instance.pk:
        old_instance = sender.objects.get(pk=instance.pk)
        instance._old_earned = old_instance.earned
    else:
        instance._old_earned = False

@receiver(post_save, sender=EarnedTrophy, dispatch_uid='update_trophy_earned_count')
def update_trophy_earned_count(sender, instance, created, **kwargs):
    if instance.earned and (created or (not instance._old_earned)):
        with transaction.atomic():
            trophy = instance.trophy
            trophy.earned_count = F('earned_count') + 1
            trophy.save(update_fields=['earned_count'])

            trophy.refresh_from_db(fields=['earned_count'])
            new_earn_rate = trophy.earned_count / trophy.game.played_count if trophy.game.played_count > 0 else 0.0
            trophy.earn_rate = new_earn_rate
            trophy.save(update_fields=['earn_rate'])
            

@receiver(post_save, sender=ProfileGame)
def update_game_played_count(sender, instance, created, **kwargs):
    if created:
        with transaction.atomic():
            instance.game.played_count = F('played_count') + 1
            instance.game.save(update_fields=['played_count'])

@receiver(post_save, sender=EarnedTrophy)
def invalidate_latest_rares(sender, instance, created, **kwargs):
    if instance.earned and (created or (not instance._old_earned)):
        week_ago = timezone.now() - timedelta(days=7)
        if instance.earned_date_time < week_ago:
            return
        
        psn_threshold = cache.get(PSN_THRESHOLD_KEY, 1.0)
        if instance.trophy.trophy_earn_rate < psn_threshold:
            _invalidate_rares_cache('psn')
        
        pp_threshold = cache.get(PP_THRESHOLD_KEY, 1.0)
        if instance.trophy.earn_rate < pp_threshold:
            _invalidate_rares_cache('pp')

def _invalidate_rares_cache(rarity_type):
    today_utc = timezone.now().date().isoformat()
    cache_key = f"latest_{rarity_type}_rares_{today_utc}"
    cache.delete(cache_key)
    logger.info(f"Invalidated latest {rarity_type.upper()} rares cache for qualifying trophy")

