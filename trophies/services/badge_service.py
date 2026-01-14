"""
Badge service - Handles badge checking, awarding, and progress tracking.

This service consolidates all badge-related business logic including:
- Checking if profiles have earned badges
- Awarding and revoking badges
- Tracking badge progress
- Discord role assignments for badges
"""
import logging
import time
from django.db import transaction
from django.db.models.query import QuerySet
from django.utils import timezone
from django.conf import settings
import requests

logger = logging.getLogger("psn_api")


def check_profile_badges(profile, profilegame_ids, skip_notis: bool = False):
    """
    Check and award badges for a profile based on recently updated games.

    This function examines ProfileGames that have been updated and checks if the
    profile has earned any related badges. It's optimized to only check badges
    that could potentially be affected by the updated games.

    Args:
        profile: Profile instance to check badges for
        profilegame_ids: List of ProfileGame IDs that were recently updated
        skip_notis: If True, skip Discord notifications (default: False)

    Returns:
        int: Number of badges checked
    """
    from trophies.models import ProfileGame, Badge, Stage

    start_time = time.time()

    pg_qs: QuerySet[ProfileGame] = ProfileGame.objects.filter(
        id__in=profilegame_ids, profile=profile
    ).select_related('game__concept').prefetch_related('game__concept__badges')

    if not pg_qs.exists():
        logger.info(f"No ProfileGames found for profile {profile.psn_username}")
        return 0

    concept_ids = pg_qs.values_list(
        'game__concept_id', flat=True
    ).filter(game__concept__isnull=False).distinct()

    stages = Stage.objects.filter(concepts__id__in=concept_ids).distinct()
    series_slugs = stages.values_list('series_slug', flat=True).distinct()
    badges = Badge.objects.filter(series_slug__in=series_slugs).distinct().order_by('tier')

    checked_count = 0
    for badge in badges:
        try:
            handle_badge(profile, badge, add_role_only=skip_notis)
            checked_count += 1
        except Exception as e:
            logger.error(
                f"Error checking badge {badge.id} for profile {profile.psn_username}: {e}"
            )

    duration = time.time() - start_time
    logger.info(
        f"Checked {checked_count} unique badges for profile "
        f"{profile.psn_username} in {duration:.2f}s"
    )
    return checked_count


@transaction.atomic
def handle_badge(profile, badge, add_role_only=False):
    """
    Handle badge logic for a single badge and profile.

    This function:
    1. Checks if prerequisites (previous tiers) are met
    2. Calculates badge completion status
    3. Awards or revokes the badge as needed
    4. Updates progress tracking
    5. Assigns Discord roles if applicable
    6. Sends notifications if badge was newly earned

    Args:
        profile: Profile instance to check
        badge: Badge instance to evaluate
        add_role_only: If True, only assign Discord roles without sending
                       notification messages (used for initial/bulk checks)

    Returns:
        bool: True if badge was newly created, False otherwise
    """
    from trophies.models import UserBadge, UserBadgeProgress, Badge
    from trophies.discord_utils.discord_notifications import notify_new_badge

    if not profile or not badge:
        return

    # Check prerequisite: Previous tier must be earned first
    if badge.tier > 1:
        prev_tier = badge.tier - 1
        prev_badge = Badge.objects.filter(
            series_slug=badge.series_slug, tier=prev_tier
        ).first()
        if prev_badge and not UserBadge.objects.filter(
            profile=profile, badge=prev_badge
        ).exists():
            logger.info(
                f"Skipped {badge.name} for {profile.psn_username} - "
                f"previous tier {prev_tier} not earned."
            )
            return

    # Handle series and collection badges (concept-based)
    if badge.badge_type in ['series', 'collection']:
        stage_completion_dict = badge.get_stage_completion(profile)
        badge_earned = True
        completed_count = 0

        for stage, is_complete in stage_completion_dict.items():
            if stage == 0:  # Stage 0 is optional/tangential
                continue
            elif not is_complete:
                badge_earned = False
                continue
            completed_count += 1

        # Update progress tracking
        progress, created = UserBadgeProgress.objects.get_or_create(
            profile=profile,
            badge=badge,
            defaults={'completed_concepts': completed_count}
        )
        if not created:
            progress.completed_concepts = completed_count
            progress.last_checked = timezone.now()
            progress.save(update_fields=['completed_concepts', 'last_checked'])

        # Award or revoke badge based on completion
        user_badge_exists = UserBadge.objects.filter(
            profile=profile, badge=badge
        ).exists()
        badge_created = False

        if badge_earned and not user_badge_exists:
            UserBadge.objects.create(profile=profile, badge=badge)
            badge_created = True
            logger.info(
                f"Awarded badge {badge.effective_display_title} (tier: {badge.tier}) "
                f"to {profile.display_psn_username}"
            )
        elif not badge_earned and user_badge_exists:
            UserBadge.objects.filter(profile=profile, badge=badge).delete()
            logger.info(
                f"Revoked badge {badge.effective_display_title} (tier: {badge.tier}) "
                f"from {profile.display_psn_username}"
            )

        # Handle Discord role assignment
        if badge_earned and badge.discord_role_id:
            if profile.is_discord_verified and profile.discord_id:
                notify_bot_role_earned(profile, badge.discord_role_id)

        # Send Discord notification for newly earned badge
        if not add_role_only and badge_created and badge.discord_role_id:
            if profile.is_discord_verified and profile.discord_id:
                notify_new_badge(profile, badge)

        return badge_created


def notify_bot_role_earned(profile, role_id):
    """
    Notify Discord bot to assign a role to a user.

    This function calls the Discord bot API to assign a role when a user
    earns a badge or milestone with an associated Discord role.

    Args:
        profile: Profile instance with discord_id set
        role_id: Discord role ID to assign
    """
    if settings.DEBUG:
        return

    try:
        url = settings.BOT_API_URL + "/assign-role"
        headers = {
            'Authorization': f"Bearer {settings.BOT_API_KEY}",
            'Content-Type': 'application/json'
        }
        data = {
            'user_id': profile.discord_id,
            'role_id': role_id,
        }
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status()
        logger.info(
            f"Bot notified: Assigned role {role_id} to {profile.discord_id}."
        )
    except requests.RequestException as e:
        logger.error(
            f"Bot notification failed for role {role_id} "
            f"(user {profile.psn_username}): {e}"
        )


@transaction.atomic
def initial_badge_check(profile, discord_notify: bool = True):
    """
    Perform initial badge check for a profile (typically after first sync).

    This function checks all possible badges for a profile and awards any that
    have been earned. It's designed for initial profile syncs or full recalculations.
    Unlike check_profile_badges, this examines ALL games, not just recently updated ones.

    The function batches Discord role notifications to send a single consolidated
    message instead of multiple individual notifications.

    Args:
        profile: Profile instance to check all badges for
        discord_notify: If True, send batch notification for earned badges with roles

    Returns:
        int: Number of badges checked
    """
    from trophies.models import ProfileGame, Badge, Stage
    from trophies.discord_utils.discord_notifications import send_batch_role_notification

    start_time = time.time()

    pg_qs: QuerySet[ProfileGame] = ProfileGame.objects.filter(
        profile=profile
    ).select_related('game__concept').prefetch_related('game__concept__badges')

    if not pg_qs.exists():
        logger.info(f"No ProfileGames found for profile {profile.psn_username}")
        return 0

    concept_ids = pg_qs.values_list(
        'game__concept_id', flat=True
    ).filter(game__concept__isnull=False).distinct()

    stages = Stage.objects.filter(concepts__id__in=concept_ids).distinct()
    series_slugs = stages.values_list('series_slug', flat=True).distinct()
    badges = Badge.objects.filter(series_slug__in=series_slugs).distinct().order_by('tier')

    role_granting_badges = []
    checked_count = 0

    for badge in badges:
        try:
            created = handle_badge(profile, badge, add_role_only=True)
            checked_count += 1
            if created and badge.discord_role_id:
                role_granting_badges.append(badge)
        except Exception as e:
            logger.error(
                f"Error checking badge {badge.id} for profile {profile.psn_username}: {e}"
            )

    logger.info(f"Found {len(role_granting_badges)} qualifying role-granting badges")

    # Send batch notification for all earned badges with roles
    if discord_notify:
        if role_granting_badges and profile.is_discord_verified and profile.discord_id:
            send_batch_role_notification(profile, role_granting_badges)
        else:
            logger.info(
                "No notification sent: missing verification, discord_id, "
                "or qualifying badges"
            )

    duration = time.time() - start_time
    logger.info(
        f"Checked {checked_count} unique badges for profile "
        f"{profile.psn_username} in {duration:.2f}s"
    )
    return checked_count
