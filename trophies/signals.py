from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver
from trophies.models import UserBadge, Badge, Concept

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