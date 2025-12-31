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

@receiver(m2m_changed, sender=Concept.games.through)
def update_badges_on_game_concept_change(sender, instance, action, reverse, model, pk_set, **kwargs):
    if action not in ['post_add', 'post_remove', 'post_clear']:
        return
    
    concepts_to_update = set()
    if reverse:
        if pk_set:
            concepts_to_update.update(Concept.objects.filter(pk__in=pk_set))
    else:
        concepts_to_update.add(instance)
    

    if not concepts_to_update:
        return
    
    badges_to_update = set()
    for concept in concepts_to_update:
        badges_to_update.update(concept.badges.all())
        base_badges_with_concept = Badge.objects.filter(concepts=concept)
        derived_badges = Badge.objects.filter(base_badge__in=base_badges_with_concept, concepts__isnull=True)
        badges_to_update.update(derived_badges)

    for badge in badges_to_update:
        if badge.badge_type == 'series':
            new_required = badge.compute_required()
            if new_required != badge.required_concepts:
                badge.required_concepts = new_required
                badge.save(update_fields=['required_concepts'])