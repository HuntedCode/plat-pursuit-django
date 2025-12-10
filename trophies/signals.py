from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.cache import cache
from trophies.models import EarnedTrophy, ProfileGame, Badge, Trophy
from trophies.utils import process_badge

@receiver(post_save, sender=Trophy, dispatch_uid='invalidate_cache_on_trophy_save')
def invalidate_trophy_cache(sender, instance, created, **kwargs):
    if created:
        game_id = instance.game.np_communication_id
        cache.delete(f"game:trophies:{game_id}")

@receiver(post_save, sender=EarnedTrophy, dispatch_uid='badge_check_on_trophy_save')
def check_badges_on_trophy_save(sender, instance, created, **kwargs):
    if not instance.earned:
        return
    profile = instance.profile
    concept = instance.trophy.game.concept
    if not concept:
        return
    
    relevant_badges = Badge.objects.filter(badge_type='series', concepts=concept).distinct()
    for badge in relevant_badges:
        process_badge(profile, badge)

@receiver(post_save, sender=ProfileGame, dispatch_uid='badge_check_on_game_save')
def check_badges_on_game_save(sender, instance, created, **kwargs):
    if instance.progress < 100:
        return
    profile = instance.profile
    concept = instance.game.concept
    if not concept:
        return
    
    relevant_badges = Badge.objects.filter(badge_type='series', concepts=concept, tier__in=[2, 4]).distinct()
    for badge in relevant_badges:
        process_badge(profile, badge)