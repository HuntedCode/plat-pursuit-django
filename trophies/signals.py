from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver
from trophies.models import UserBadge, Badge

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

@receiver(m2m_changed, sender=Badge.concepts.through)
def update_badges_on_concepts_change(sender, instance, action, pk_set, **kwargs):
    if action in ['post_add', 'post_remove', 'post_clear']:
        if instance.badge_type == 'series':
            instance.required_concepts = instance.compute_required()
            instance.save(update_fields=['required_concepts'])
        
        if instance.derived_badges.exists():
            for derived in instance.derived_badges.filter(concepts__isnull=True, badge_type='series'):
                derived.required_concepts = derived.compute_required()
                derived.save(update_fields=['required_concepts'])
                