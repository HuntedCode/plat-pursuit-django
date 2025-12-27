from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from trophies.models import UserBadge
from trophies.utils import notify_new_badge

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