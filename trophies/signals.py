import logging
from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver
from django.db.models import F
from trophies.models import UserBadge, UserBadgeProgress, Comment, Stage

logger = logging.getLogger(__name__)

@receiver(post_save, sender=UserBadge, dispatch_uid="update_badge_earned_count")
def update_badge_earned_count_on_save(sender, instance, created, **kwargs):
    if created:
        from trophies.models import Badge
        Badge.objects.filter(pk=instance.badge_id).update(earned_count=F('earned_count') + 1)

@receiver(post_delete, sender=UserBadge, dispatch_uid='decrement_badge_earned_count')
def decrement_badge_earned_count_on_delete(sender, instance, **kwargs):
    from trophies.models import Badge
    Badge.objects.filter(pk=instance.badge_id, earned_count__gt=0).update(
        earned_count=F('earned_count') - 1
    )


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


# --- Gamification Signal Handlers ---

@receiver(post_save, sender=UserBadgeProgress, dispatch_uid="update_gamification_on_progress")
def update_gamification_on_progress(sender, instance, created, **kwargs):
    """
    Update ProfileGamification when badge progress changes.

    Triggered on:
    - New progress record created
    - Existing progress updated (completed_concepts changed)
    """
    from trophies.services.xp_service import (
        update_profile_gamification,
        is_bulk_update_active,
        defer_profile_update
    )

    # Defer update if bulk operation is active
    if is_bulk_update_active():
        defer_profile_update(instance.profile)
        return

    try:
        update_profile_gamification(instance.profile)
        logger.debug(
            f"Updated gamification for {instance.profile.psn_username} "
            f"after progress update on {instance.badge.name}"
        )
    except Exception as e:
        logger.exception(f"Failed to update gamification after progress change: {e}")


@receiver(post_save, sender=UserBadge, dispatch_uid="update_gamification_on_badge_earned")
def update_gamification_on_badge_earned(sender, instance, created, **kwargs):
    """
    Update ProfileGamification when a badge is earned.

    Adds the 3000 XP badge completion bonus.
    Only triggers on new badge creation, not updates.
    """
    if not created:
        return

    from trophies.services.xp_service import (
        update_profile_gamification,
        is_bulk_update_active,
        defer_profile_update
    )

    # Defer update if bulk operation is active
    if is_bulk_update_active():
        defer_profile_update(instance.profile)
        return

    try:
        update_profile_gamification(instance.profile)
        logger.info(
            f"Updated gamification for {instance.profile.psn_username} "
            f"after earning {instance.badge.name}"
        )
    except Exception as e:
        logger.exception(f"Failed to update gamification after badge earned: {e}")


@receiver(post_delete, sender=UserBadge, dispatch_uid="update_gamification_on_badge_revoked")
def update_gamification_on_badge_revoked(sender, instance, **kwargs):
    """
    Update ProfileGamification when a badge is revoked.

    Removes the 3000 XP badge completion bonus.
    """
    from trophies.services.xp_service import (
        update_profile_gamification,
        is_bulk_update_active,
        defer_profile_update
    )

    # Defer update if bulk operation is active
    if is_bulk_update_active():
        defer_profile_update(instance.profile)
        return

    try:
        update_profile_gamification(instance.profile)
        logger.info(
            f"Updated gamification for {instance.profile.psn_username} "
            f"after revoking {instance.badge.name}"
        )
    except Exception as e:
        logger.error(
            f"Failed to update gamification after badge revoked: {e}",
            exc_info=True
        )


# --- Stage Icon Auto-Population ---

@receiver(m2m_changed, sender=Stage.concepts.through, dispatch_uid="auto_populate_stage_icon")
def auto_populate_stage_icon(sender, instance, action, **kwargs):
    """
    Auto-populate Stage.stage_icon from first Concept.concept_icon_url.

    Triggers when:
    - Concepts are added to a Stage (post_add) - syncs icon to first concept
    - All concepts are cleared from a Stage (post_clear) - clears icon

    Always updates stage_icon to match the first concept, overwriting any manual changes.
    """
    try:
        if action == 'post_add':
            # Always sync to first concept's icon
            first_concept = instance.concepts.first()
            if first_concept and first_concept.concept_icon_url:
                instance.stage_icon = first_concept.concept_icon_url
                instance.save(update_fields=['stage_icon'])
                logger.debug(
                    f"Auto-populated stage_icon for {instance} from {first_concept}"
                )
            elif first_concept and not first_concept.concept_icon_url:
                # First concept exists but has no icon - clear stage icon
                instance.stage_icon = None
                instance.save(update_fields=['stage_icon'])
                logger.debug(
                    f"Cleared stage_icon for {instance} (first concept has no icon)"
                )

        elif action == 'post_clear':
            # Clear icon when all concepts removed
            if instance.stage_icon:
                instance.stage_icon = None
                instance.save(update_fields=['stage_icon'])
                logger.debug(f"Cleared stage_icon for {instance} (all concepts removed)")

    except Exception as e:
        logger.exception(
            f"Failed to auto-populate stage_icon for {instance}: {e}"
        )