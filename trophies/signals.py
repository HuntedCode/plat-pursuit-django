from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
from trophies.models import EarnedTrophy, ProfileGame, Badge, Trophy, UserBadge
from trophies.utils import process_badge, notify_new_badge

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

@receiver(post_save, sender=UserBadge, dispatch_uid='notification_on_new_badge')
def notify_on_new_badge(sender, instance, created, **kwargs):
    if not created:
        return
    profile = instance.profile
    badge = instance.badge
    if not profile or not badge or not profile.discord_id:
        return
    notify_new_badge(profile, badge)

@receiver(post_save, sender=UserBadge, dispatch_uid="update_badge_earned_count")
def update_badge_earned_count_on_save(sender, instance, created, **kwargs):
    if created:
        badge = instance.badge
        badge.earned_count += 1
        badge.save(update_fields=['earned_count'])

@receiver(post_delete, sender=UserBadge, dispatch_uid='decrement_badge_earned_count')
def decrement_badge_earned_count_on_delete(sender, instance, **kwargs):
    badge = instance.badge
    if badge.earned_count > 0:
        badge.earned_count -= 1
        badge.save(update_fields=['earned_count'])