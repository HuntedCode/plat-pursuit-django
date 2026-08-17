"""
Signal handlers for automatic notification creation.
Hooks into existing models using Django signals.
"""
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from datetime import timedelta
from trophies.models import EarnedTrophy, Profile, ProfileGame
from notifications.services.notification_service import NotificationService
from core.services.shareable_data_service import ShareableDataService
from notifications.models import NotificationTemplate
import logging

logger = logging.getLogger(__name__)

# Log when signals module is loaded
logger.info("[SIGNAL] Notification signals module loaded successfully")


@receiver(pre_save, sender=EarnedTrophy)
def capture_earned_trophy_previous_state(sender, instance, **kwargs):
    """
    Capture the previous 'earned' value before saving.
    This allows post_save to detect when earned flips from False to True.
    Uses .only('earned') to minimize query overhead during bulk sync operations.

    Suppressed during sync via sync_signal_suppressor() context manager:
    during sync, platinum notifications are handled by DeferredNotificationService
    and earned-flip detection is done directly in create_or_update_earned_trophy_from_trophy_data().
    """
    from trophies.sync_utils import is_sync_signal_suppressed
    if is_sync_signal_suppressed():
        instance._previous_earned = None
        return

    if instance.pk and not instance._state.adding:
        try:
            instance._previous_earned = (
                EarnedTrophy.objects.only('earned')
                .values_list('earned', flat=True)
                .get(pk=instance.pk)
            )
        except EarnedTrophy.DoesNotExist:
            instance._previous_earned = None
    else:
        instance._previous_earned = None


@receiver(pre_save, sender=Profile)
def capture_profile_previous_state(sender, instance, **kwargs):
    """
    Capture the previous 'is_discord_verified' value before saving.
    This allows post_save to detect when it flips from False to True.
    """
    if instance.pk and not instance._state.adding:
        try:
            instance._previous_is_discord_verified = (
                Profile.objects.only('is_discord_verified')
                .values_list('is_discord_verified', flat=True)
                .get(pk=instance.pk)
            )
        except Profile.DoesNotExist:
            instance._previous_is_discord_verified = None
    else:
        instance._previous_is_discord_verified = None


@receiver(post_save, sender=EarnedTrophy)
def notify_platinum_earned(sender, instance, created, **kwargs):
    """
    Triggered when a platinum trophy is earned.

    Notification rules:
    - Only notify for platinum trophies
    - Only notify when earned=True AND this is a NEW earn (not a re-save)
    - A "new earn" is either:
      1. created=True with earned=True (new record that's already earned)
      2. created=False with earned=True AND previous earned was False (flipped to earned)
    - Never notify if earned=False or if earned was already True before this save
    """
    previous_earned = getattr(instance, '_previous_earned', None)
    logger.info(f"[SIGNAL] Platinum signal triggered for {instance.trophy.trophy_name} (created={created}, earned={instance.earned}, previous_earned={previous_earned})")

    # Skip if not a platinum trophy
    if instance.trophy.trophy_type != 'platinum':
        logger.debug(f"[SIGNAL] Skipping - not platinum trophy: {instance.trophy.trophy_type}")
        return

    # Skip if not earned
    if not instance.earned:
        logger.debug(f"[SIGNAL] Skipping - trophy not earned")
        return

    # Determine if this is a NEW earn (the key logic fix)
    # Case 1: New record created with earned=True
    # Case 2: Existing record where earned flipped from False to True
    is_new_earn = created or (previous_earned is False and instance.earned is True)

    if not is_new_earn:
        logger.debug(f"[SIGNAL] Skipping - not a new earn (previous_earned={previous_earned}, earned={instance.earned})")
        return

    # Skip if shovelware game
    if instance.trophy.game.is_shovelware:
        logger.debug(f"[SIGNAL] Skipping - shovelware game: {instance.trophy.game.title_name}")
        return

    # Get user from profile
    if not instance.profile.user:
        logger.debug(f"[SIGNAL] Skipping - no user linked to profile")
        return  # No user linked to profile

    # Skip if no earned date
    if not instance.earned_date_time:
        logger.debug(f"[SIGNAL] Skipping - no earned date")
        return

    # Apply 2-day threshold to prevent spam on initial sync (matches Discord notification logic)
    threshold = timezone.now() - timedelta(days=2)
    if instance.earned_date_time < threshold:
        logger.info(f"[SIGNAL] Skipping - earned more than 2 days ago (initial sync spam prevention)")
        return

    logger.info(f"[SIGNAL] Passed initial checks for {instance.profile.psn_username} - {instance.trophy.game.title_name}")

    from notifications.models import Notification

    # Check if notification already exists for this platinum
    existing_notification = Notification.objects.filter(
        recipient=instance.profile.user,
        notification_type='platinum_earned',
        metadata__game_id=instance.trophy.game.id
    ).exists()

    if existing_notification:
        logger.info(f"[SIGNAL] Notification already exists for game_id={instance.trophy.game.id}, skipping")
        return  # Already sent notification for this platinum

    logger.info(f"[SIGNAL] No existing notification found, checking sync status")

    # Check if profile is currently syncing
    profile = instance.profile
    if profile.sync_status == 'syncing':
        # Queue notification for later creation (after game sync completes)
        from notifications.services.deferred_notification_service import DeferredNotificationService
        try:
            DeferredNotificationService.queue_platinum_notification(
                profile=profile,
                game=instance.trophy.game,
                trophy=instance.trophy,
                earned_date=instance.earned_date_time
            )
            logger.info(f"[SIGNAL] Queued platinum notification for {profile.psn_username} - {instance.trophy.game.title_name}")
        except Exception as e:
            logger.exception(f"[SIGNAL] Failed to queue platinum notification: {e}")
    else:
        # Create notification immediately (manual update outside sync)
        # Wrap in transaction to prevent duplicate creation from concurrent signals
        try:
            with transaction.atomic():
                # Re-check inside transaction to close TOCTOU window
                if Notification.objects.filter(
                    recipient=instance.profile.user,
                    notification_type='platinum_earned',
                    metadata__game_id=instance.trophy.game.id
                ).exists():
                    logger.info(f"[SIGNAL] Notification created by concurrent signal, skipping")
                    return

                template = NotificationTemplate.objects.get(
                    name='platinum_earned',
                    auto_trigger_enabled=True
                )

                # Fetch ProfileGame data for enriched metadata
                profile_game = ProfileGame.objects.filter(
                    profile=profile,
                    game=instance.trophy.game
                ).first()

                # Per-platinum totals (user_total_platinums, yearly_plats) used to
                # be stored here, but the count was racy when multiple plats from
                # one sync were processed in non-chronological order. The inbox
                # links to the Plat Cards page where the count is computed
                # live, so it isn't needed in the frozen metadata anymore.
                NotificationService.create_from_template(
                    recipient=profile.user,
                    template=template,
                    context={
                        'username': profile.display_psn_username or profile.psn_username,
                        'trophy_name': instance.trophy.trophy_name,
                        'game_name': instance.trophy.game.title_name,
                        'game_id': instance.trophy.game.id,
                        'earned_trophy_id': instance.id,
                        'np_communication_id': instance.trophy.game.np_communication_id,
                        'concept_id': instance.trophy.game.concept.id if instance.trophy.game.concept else None,
                        'trophy_detail': instance.trophy.trophy_detail or '',
                        'trophy_earn_rate': instance.trophy.trophy_earn_rate or 0,
                        'trophy_rarity': instance.trophy.trophy_rarity,
                        'trophy_icon_url': instance.trophy.trophy_icon_url or '',
                        'game_image': instance.trophy.game.display_image_url_large,
                        'rarity_label': ShareableDataService.get_rarity_label(instance.trophy.trophy_rarity),
                        'title_platform': instance.trophy.game.title_platform,
                        'region': instance.trophy.game.region,
                        'is_regional': instance.trophy.game.is_regional,
                        'first_played_date_time': profile_game.first_played_date_time.isoformat() if profile_game and profile_game.first_played_date_time else None,
                        'last_played_date_time': profile_game.last_played_date_time.isoformat() if profile_game and profile_game.last_played_date_time else None,
                        'play_duration_seconds': profile_game.play_duration.total_seconds() if profile_game and profile_game.play_duration else None,
                        'earned_trophies_count': profile_game.earned_trophies_count if profile_game else 0,
                        'total_trophies_count': profile_game.total_trophies if profile_game else 0,
                        'progress_percentage': profile_game.progress if profile_game else 0,
                        'user_avatar_url': profile.avatar_url or '',
                        'earned_date_time': instance.earned_date_time.isoformat() if instance.earned_date_time else None,
                    }
                )

            logger.info(
                f"[SIGNAL] Created platinum notification immediately for {profile.psn_username} - {instance.trophy.game.title_name}"
            )

        except NotificationTemplate.DoesNotExist:
            logger.error("[SIGNAL] Platinum earned template not found or not enabled")
        except Exception as e:
            logger.exception(f"[SIGNAL] Failed to create platinum notification: {e}")


@receiver(post_save, sender=Profile)
def notify_discord_linked(sender, instance, created, **kwargs):
    """
    Triggered when Discord is verified for a profile.
    Only notifies when is_discord_verified flips from False to True.
    """
    previous_verified = getattr(instance, '_previous_is_discord_verified', None)

    # Only notify if is_discord_verified flipped from False to True
    is_newly_verified = (
        not created
        and instance.is_discord_verified is True
        and previous_verified is False
    )

    if not is_newly_verified:
        return

    if not instance.user:
        return  # No user linked to profile

    try:
        template = NotificationTemplate.objects.get(
            name='discord_verified',
            auto_trigger_enabled=True
        )

        # Create notification from template
        NotificationService.create_from_template(
            recipient=instance.user,
            template=template,
            context={
                'username': instance.display_psn_username or instance.psn_username,
            }
        )

        logger.info(
            f"Created Discord verification notification for {instance.psn_username}"
        )

    except NotificationTemplate.DoesNotExist:
        logger.warning("Discord verified template not found or not enabled")
    except Exception as e:
        logger.error(f"Failed to create Discord verification notification: {e}")
