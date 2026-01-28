from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import F
from trophies.models import UserBadge, Comment

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


@receiver(post_save, sender=Comment, dispatch_uid="update_comment_count_on_save")
def update_comment_count_on_save(sender, instance, created, **kwargs):
    """Update denormalized comment_count on Concept when comment is created.

    Only counts concept-level comments (trophy_id and checklist_id are both null).
    Trophy-level and checklist-level comments are counted separately.
    """
    if created and not instance.is_deleted and instance.trophy_id is None and instance.checklist_id is None:
        concept = instance.concept
        if concept:
            concept.comment_count = F('comment_count') + 1
            concept.save(update_fields=['comment_count'])