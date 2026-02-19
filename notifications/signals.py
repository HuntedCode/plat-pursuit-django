"""
Signal handlers for automatic notification creation.
Hooks into existing models using Django signals.
"""
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from datetime import timedelta
from trophies.models import EarnedTrophy, UserBadge, UserMilestone, Profile, ProfileGame
from notifications.services.notification_service import NotificationService
from notifications.services.shareable_data_service import ShareableDataService
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
    """
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


def _get_tier_name(tier):
    """Convert badge tier number to name."""
    tier_map = {
        1: 'Bronze',
        2: 'Silver',
        3: 'Gold',
        4: 'Platinum',
    }
    return tier_map.get(tier, 'Bronze')


def _get_badge_main_image(badge):
    """Get the main badge image URL."""
    try:
        layers = badge.get_badge_layers()
        return layers.get('main', '')
    except Exception:
        return ''


def _calculate_badge_xp(profile, badge):
    """
    Calculate Badge XP for a specific badge series and total user Badge XP.

    Uses the centralized xp_service for calculations. Always calculates fresh
    to ensure notifications show the correct XP including the newly earned badge.
    (The denormalized ProfileGamification table may not be updated yet when
    this is called from the notification signal.)

    Returns:
        tuple: (series_xp, total_xp)
    """
    from trophies.services.xp_service import calculate_series_xp, calculate_total_xp

    # Calculate series XP fresh
    series_xp = calculate_series_xp(profile, badge.series_slug) if badge.series_slug else 0

    # Calculate total XP fresh (not from denormalized table)
    # This ensures notifications show the correct total including the badge just earned
    total_xp, _, _ = calculate_total_xp(profile)

    return series_xp, total_xp


def _get_badge_notification_context(user_badge_instance):
    """
    Build comprehensive badge notification context including:
    - Badge layer URLs for composited display
    - Next tier progress information
    - Stage completion data for next tier
    - Badge XP (series and total)
    """
    from trophies.models import Badge, UserBadgeProgress, Stage

    badge = user_badge_instance.badge
    profile = user_badge_instance.profile

    # Get all badge layers for composited display
    layers = badge.get_badge_layers()

    # Calculate Badge XP
    series_xp, total_xp = _calculate_badge_xp(profile, badge)

    # Determine if this is max tier (tier 4 or no next tier exists)
    is_max_tier = badge.tier == 4
    next_tier_progress = None
    stage_completion = []

    # Only compute next tier progress if not max tier and has series_slug
    if not is_max_tier and badge.series_slug:
        # Find next tier badge in same series
        next_tier_badge = Badge.objects.filter(
            series_slug=badge.series_slug,
            tier=badge.tier + 1
        ).first()

        if next_tier_badge:
            # Get user's progress for next tier
            progress = UserBadgeProgress.objects.filter(
                profile=profile,
                badge=next_tier_badge
            ).first()

            completed = progress.completed_concepts if progress else 0
            required = next_tier_badge.required_stages or 1

            next_tier_progress = {
                'tier': next_tier_badge.tier,
                'tier_name': _get_tier_name(next_tier_badge.tier),
                'completed_stages': completed,
                'required_stages': required,
                'progress_percentage': min(100, int((completed / required) * 100)) if required > 0 else 0,
            }

            # Get stage completion data for next tier
            stage_completion_dict = next_tier_badge.get_stage_completion(profile, next_tier_badge.badge_type)

            # Fetch stage details (only required stages, stage_number > 0)
            stages = Stage.objects.filter(
                series_slug=badge.series_slug,
                stage_number__gt=0
            ).order_by('stage_number')

            for stage in stages:
                if stage.applies_to_tier(next_tier_badge.tier):
                    stage_completion.append({
                        'stage_number': stage.stage_number,
                        'stage_title': stage.title or f'Stage {stage.stage_number}',
                        'stage_icon': stage.stage_icon or '',
                        'is_complete': stage_completion_dict.get(stage.stage_number, False),
                    })
        else:
            # No next tier badge exists, treat as max tier
            is_max_tier = True

    return {
        # Basic badge info
        'username': profile.display_psn_username or profile.psn_username,
        'badge_name': badge.name,
        'badge_id': badge.id,
        'series_slug': badge.series_slug or '',
        'badge_tier': badge.tier,
        'badge_tier_name': _get_tier_name(badge.tier),
        'badge_description': badge.effective_description or '',
        'badge_series': badge.effective_display_series or '',

        # Badge layer URLs for composited image
        'badge_layers': {
            'backdrop': layers.get('backdrop', ''),
            'main': layers.get('main', ''),
            'foreground': layers.get('foreground'),  # May be None
        },
        'badge_image_url': layers.get('main', ''),  # Keep for backwards compatibility

        # Badge XP
        'series_xp': series_xp,
        'total_xp': total_xp,

        # Next tier progress (None if max tier or no series)
        'is_max_tier': is_max_tier,
        'next_tier_progress': next_tier_progress,

        # Stage list with completion status for next tier
        'stages': stage_completion,
    }


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

                # Count user's total platinums (including this one)
                total_plats = EarnedTrophy.objects.filter(
                    profile=profile,
                    earned=True,
                    trophy__trophy_type='platinum'
                ).count()

                # Get the earned date for filtering
                earned_date = instance.earned_date_time

                # Calculate yearly platinum count
                yearly_plats = 0

                if earned_date:
                    earned_year = earned_date.year

                    # Count platinums earned in the same year
                    yearly_plats = EarnedTrophy.objects.filter(
                        profile=profile,
                        earned=True,
                        trophy__trophy_type='platinum',
                        earned_date_time__year=earned_year
                    ).count()

                # Create notification from template with enhanced context
                # Note: badge_xp and tier1_badges are intentionally NOT stored here
                # Badge progress is fetched live when the share card is accessed because
                # UserBadgeProgress is calculated at the end of full sync, not per-game sync
                NotificationService.create_from_template(
                    recipient=profile.user,
                    template=template,
                    context={
                        'username': profile.display_psn_username or profile.psn_username,
                        'trophy_name': instance.trophy.trophy_name,
                        'game_name': instance.trophy.game.title_name,
                        'game_id': instance.trophy.game.id,
                        'np_communication_id': instance.trophy.game.np_communication_id,
                        'concept_id': instance.trophy.game.concept.id if instance.trophy.game.concept else None,
                        'trophy_detail': instance.trophy.trophy_detail or '',
                        'trophy_earn_rate': instance.trophy.trophy_earn_rate or 0,
                        'trophy_rarity': instance.trophy.trophy_rarity,
                        'trophy_icon_url': instance.trophy.trophy_icon_url or '',
                        'game_image': instance.trophy.game.title_image or instance.trophy.game.title_icon_url or '',
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
                        'user_total_platinums': total_plats,
                        'user_avatar_url': profile.avatar_url or '',
                        'earned_date_time': instance.earned_date_time.isoformat() if instance.earned_date_time else None,
                        'yearly_plats': yearly_plats,
                        'earned_year': earned_year if earned_date else None,
                    }
                )

            logger.info(
                f"[SIGNAL] Created platinum notification immediately for {profile.psn_username} - {instance.trophy.game.title_name}"
            )

        except NotificationTemplate.DoesNotExist:
            logger.error("[SIGNAL] Platinum earned template not found or not enabled")
        except Exception as e:
            logger.exception(f"[SIGNAL] Failed to create platinum notification: {e}")


@receiver(post_save, sender=UserBadge)
def notify_badge_awarded(sender, instance, created, **kwargs):
    """
    Triggered when a badge is awarded to a user.
    Always queues notification for time-window based consolidation.
    """
    if created:
        # Get user from profile
        if not instance.profile.user:
            return  # No user linked to profile

        profile = instance.profile

        # Build comprehensive notification context
        context = _get_badge_notification_context(instance)

        # Always queue badge notifications for consolidation
        # This handles both sync and manual operations (like refresh_badge_series)
        from notifications.services.deferred_notification_service import DeferredNotificationService
        try:
            DeferredNotificationService.queue_badge_notification(
                profile=profile,
                badge=instance.badge,
                context_data=context
            )
            logger.info(f"Queued badge notification for {profile.psn_username} - {instance.badge.name}")
        except Exception as e:
            logger.exception(f"Failed to queue badge notification: {e}")


def _build_milestone_context(user_milestone_instance):
    """
    Build rich context for milestone notification with next milestone progress.

    Gathers:
    - Current milestone details
    - Next milestone in same criteria_type (if exists)
    - Current progress value
    - Tier information
    """
    from trophies.models import Milestone, UserMilestone
    from trophies.milestone_handlers import MILESTONE_HANDLERS

    milestone = user_milestone_instance.milestone
    profile = user_milestone_instance.profile

    # Get current progress using the appropriate handler
    handler = MILESTONE_HANDLERS.get(milestone.criteria_type)
    current_progress = 0
    if handler:
        result = handler(profile, milestone)
        current_progress = result.get('progress', 0)

    # Determine if this is a "one-off" milestone type (no progression)
    from trophies.milestone_constants import ONE_OFF_TYPES, CALENDAR_MONTH_TYPES
    is_one_off = milestone.criteria_type in ONE_OFF_TYPES

    # Get all milestones of the same criteria type, ordered by required_value
    same_type_milestones = list(
        Milestone.objects.filter(criteria_type=milestone.criteria_type)
        .order_by('required_value')
    )

    # Calculate tier information
    total_tiers = len(same_type_milestones)
    current_tier = 1
    for idx, m in enumerate(same_type_milestones, start=1):
        if m.id == milestone.id:
            current_tier = idx
            break

    # Find next milestone (higher required_value in same criteria_type)
    earned_milestone_ids = set(
        UserMilestone.objects.filter(
            profile=profile, milestone__criteria_type=milestone.criteria_type
        ).values_list('milestone_id', flat=True)
    )

    next_milestone_data = None
    is_max_tier = True

    if not is_one_off:
        # Find next unearned milestone with higher required_value
        for m in same_type_milestones:
            if m.required_value > milestone.required_value and m.id not in earned_milestone_ids:
                # Calculate progress percentage for next milestone
                progress_pct = min(100, int((current_progress / m.required_value) * 100)) if m.required_value > 0 else 0

                next_milestone_data = {
                    'id': m.id,
                    'name': m.name,
                    'description': m.description or '',
                    'image': m.image.url if m.image else '',
                    'required_value': m.required_value,
                    'progress_value': current_progress,
                    'progress_percentage': progress_pct,
                }
                is_max_tier = False
                break

    # Build pre-formatted tier and next milestone text for the notification template
    if is_one_off:
        tier_text = ''
        next_milestone_text = ''
    else:
        tier_text = f" (Tier {current_tier}/{total_tiers})"
        if is_max_tier:
            next_milestone_text = " You've reached the highest tier!"
        elif next_milestone_data:
            next_milestone_text = (
                f" Next up: {next_milestone_data['name']}"
                f" ({next_milestone_data['progress_percentage']}% complete)."
            )
        else:
            next_milestone_text = ''

    # Map criteria_type to milestone page category tab slug
    from trophies.milestone_constants import MILESTONE_CATEGORIES
    category_slug = 'overview'
    for slug, cat_data in MILESTONE_CATEGORIES.items():
        if milestone.criteria_type in cat_data.get('criteria_types', []):
            category_slug = slug
            break

    return {
        # Basic milestone info (backward compatible)
        'username': profile.display_psn_username or profile.psn_username,
        'milestone_name': milestone.name,
        'milestone_id': milestone.id,
        'milestone_description': milestone.description or '',
        'milestone_image': milestone.image.url if milestone.image else '',
        'milestone_criteria': milestone.get_criteria_type_display(),
        'milestone_target': milestone.required_value,

        # Enhanced fields (calendar month types use 'calendar_months' anchor
        # to match the element ID in milestone_calendar_grid.html)
        'criteria_type': 'calendar_months' if milestone.criteria_type in CALENDAR_MONTH_TYPES else milestone.criteria_type,
        'milestone_category': category_slug,
        'current_progress': current_progress,
        'next_milestone': next_milestone_data,
        'current_tier': current_tier,
        'total_tiers': total_tiers,
        'is_max_tier': is_max_tier,
        'is_one_off': is_one_off,
        'tier_text': tier_text,
        'next_milestone_text': next_milestone_text,
    }


def create_milestone_notification(user_milestone_instance):
    """
    Create an in-app notification for a milestone achievement.

    Called from milestone_service.py instead of via post_save signal so that
    notifications can be consolidated (only the highest tier per criteria type
    in a batch).
    """
    try:
        template = NotificationTemplate.objects.get(
            name='milestone_achieved',
            auto_trigger_enabled=True
        )

        if not user_milestone_instance.profile.user:
            return

        context = _build_milestone_context(user_milestone_instance)

        NotificationService.create_from_template(
            recipient=user_milestone_instance.profile.user,
            template=template,
            context=context
        )

        logger.info(
            f"Created milestone notification for {user_milestone_instance.profile.psn_username}"
            f" - {user_milestone_instance.milestone.name}"
        )

    except NotificationTemplate.DoesNotExist:
        logger.warning("Milestone achieved template not found or not enabled")
    except Exception as e:
        logger.exception(f"Failed to create milestone notification: {e}")


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
