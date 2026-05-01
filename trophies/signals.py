import logging
from django.db.models.signals import post_save, post_delete, m2m_changed, pre_save
from django.dispatch import receiver
from django.db.models import F
from trophies.models import UserBadge, UserBadgeProgress, Stage, Profile, EarnedTrophy, ProfileGame

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Incremental counter maintenance for Trophy.earned_count and
# Game.played_count. The daily `recalc_earn_rates` cron is the source of
# truth and reconciles drift; these signals keep the counters live in
# steady state so users see accurate values between cron runs.
#
# Notes:
# - Trophy.earn_rate (= earned_count / played_count) is NOT updated here.
#   It's a derived value, refreshed by the daily cron. Up-to-24h-stale
#   percentages are acceptable; updating it incrementally would require
#   touching every trophy in a game when its played_count moves.
# - Sync paths stamp `_previous_earned` on the EarnedTrophy instance
#   before calling .save() so the post_save handler can detect transitions
#   without a SELECT (the existing pre_save handler that tracks this is
#   suppressed during sync for performance — see trophies/sync_utils.py).
# - bulk_create / bulk_update do not fire these signals (Django default).
#   The cron compensates for any rows touched that way.
# ──────────────────────────────────────────────────────────────────────


@receiver(post_save, sender=EarnedTrophy, dispatch_uid="update_trophy_earned_count_on_save")
def update_trophy_earned_count_on_save(sender, instance, created, **kwargs):
    """Increment/decrement Trophy.earned_count when an EarnedTrophy flips."""
    from trophies.models import Trophy

    if created:
        if instance.earned:
            Trophy.objects.filter(pk=instance.trophy_id).update(
                earned_count=F('earned_count') + 1
            )
        return

    prev = getattr(instance, '_previous_earned', None)
    # No previous-state hint means we cannot tell what changed. Daily cron
    # reconciliation will fix any drift; skipping here is safer than guessing.
    if prev is None:
        return

    if prev is False and instance.earned is True:
        Trophy.objects.filter(pk=instance.trophy_id).update(
            earned_count=F('earned_count') + 1
        )
    elif prev is True and instance.earned is False:
        Trophy.objects.filter(pk=instance.trophy_id, earned_count__gt=0).update(
            earned_count=F('earned_count') - 1
        )


@receiver(post_delete, sender=EarnedTrophy, dispatch_uid="update_trophy_earned_count_on_delete")
def update_trophy_earned_count_on_delete(sender, instance, **kwargs):
    """Decrement Trophy.earned_count when an earned row is removed."""
    if instance.earned:
        from trophies.models import Trophy
        Trophy.objects.filter(pk=instance.trophy_id, earned_count__gt=0).update(
            earned_count=F('earned_count') - 1
        )


@receiver(post_save, sender=ProfileGame, dispatch_uid="update_game_played_count_on_save")
def update_game_played_count_on_save(sender, instance, created, **kwargs):
    """Increment Game.played_count when a profile picks up a new game."""
    if created:
        from trophies.models import Game
        Game.objects.filter(pk=instance.game_id).update(
            played_count=F('played_count') + 1
        )


@receiver(post_delete, sender=ProfileGame, dispatch_uid="update_game_played_count_on_delete")
def update_game_played_count_on_delete(sender, instance, **kwargs):
    """Decrement Game.played_count when a profile-game link is removed."""
    from trophies.models import Game
    Game.objects.filter(pk=instance.game_id, played_count__gt=0).update(
        played_count=F('played_count') - 1
    )


# ──────────────────────────────────────────────────────────────────────
# Profile premium transitions: keep profile showcases in sync with premium
# tier. Runs for every path that changes user_is_premium (subscription
# webhooks, admin toggles, management commands, shell edits).
# ──────────────────────────────────────────────────────────────────────
@receiver(pre_save, sender=Profile, dispatch_uid="track_profile_premium_transition")
def _track_profile_premium_transition(sender, instance, **kwargs):
    """Snapshot the old premium value so the post_save handler can detect the edge."""
    if not instance.pk:
        instance._old_user_is_premium = None
        return
    try:
        old = Profile.objects.only('user_is_premium').get(pk=instance.pk)
        instance._old_user_is_premium = old.user_is_premium
    except Profile.DoesNotExist:
        instance._old_user_is_premium = None


@receiver(post_save, sender=Profile, dispatch_uid="handle_profile_premium_downgrade")
def _handle_profile_premium_downgrade(sender, instance, created, **kwargs):
    """Deactivate premium-only showcases when user_is_premium goes True -> False."""
    if created:
        return
    old = getattr(instance, '_old_user_is_premium', None)
    if old is True and instance.user_is_premium is False:
        from trophies.services.showcase_service import ProfileShowcaseService
        try:
            ProfileShowcaseService.handle_premium_downgrade(instance)
        except Exception:
            logger.exception(
                f"Failed to deactivate showcases on premium downgrade for profile {instance.pk}"
            )

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
    Update ProfileGamification and earners leaderboard when a badge is earned.

    Adds the 3000 XP badge completion bonus and updates sorted set leaderboard.
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

    # Update earners leaderboard sorted set
    _update_earner_leaderboard_on_badge_change(instance.profile, instance.badge.series_slug)


@receiver(post_delete, sender=UserBadge, dispatch_uid="update_gamification_on_badge_revoked")
def update_gamification_on_badge_revoked(sender, instance, **kwargs):
    """
    Update ProfileGamification and earners leaderboard when a badge is revoked.

    Removes the 3000 XP badge completion bonus and updates sorted set leaderboard.
    """
    from trophies.models import ProfileGamification
    from trophies.services.xp_service import (
        update_profile_gamification,
        is_bulk_update_active,
        defer_profile_update
    )

    # Skip if profile's gamification record was already cascade-deleted
    # (happens when the Profile itself is being deleted)
    if not ProfileGamification.objects.filter(profile_id=instance.profile_id).exists():
        return

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

    # Update earners leaderboard sorted set
    _update_earner_leaderboard_on_badge_change(instance.profile, instance.badge.series_slug)


def _update_earner_leaderboard_on_badge_change(profile, series_slug):
    """
    Update the earners sorted set leaderboard after a badge is earned or revoked.

    Finds the user's highest remaining tier in the series and updates their
    sorted set entry accordingly, or removes them if no badges remain.
    """
    if not profile.is_linked:
        return

    try:
        from trophies.services.redis_leaderboard_service import (
            update_earner_entry, remove_earner_entry,
        )

        # Find the user's current highest tier badge in this series
        highest = UserBadge.objects.filter(
            profile=profile, badge__series_slug=series_slug
        ).select_related('badge').order_by('-badge__tier', 'earned_at').first()

        if highest:
            update_earner_entry(series_slug, profile, highest.badge.tier, highest.earned_at)
        else:
            remove_earner_entry(series_slug, profile.id)
    except Exception as e:
        logger.error(f"Failed to update earner leaderboard for {profile.psn_username}: {e}")


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