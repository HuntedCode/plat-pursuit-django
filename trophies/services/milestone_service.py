"""
Milestone service - Handles milestone checking, awarding, and progress tracking.

This service manages milestone-related business logic including:
- Checking if profiles have achieved milestones
- Awarding milestones and updating progress
- Discord role assignments for milestones
- Batch checking of multiple milestones
"""
import logging
from django.db import transaction
from django.conf import settings
import requests

from trophies.models import UserTitle

logger = logging.getLogger("psn_api")


def notify_bot_role_earned(profile, role_id):
    """
    Notify Discord bot to assign a role to a user.

    This function calls the Discord bot API to assign a role when a user
    earns a milestone with an associated Discord role.

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
def check_and_award_milestone(profile, milestone, notify=True):
    """
    Check if a milestone is achieved for a profile and award if earned.

    This function evaluates milestone criteria using the appropriate handler,
    updates progress tracking, and awards the milestone if requirements are met.

    Args:
        profile: Profile instance to check
        milestone: Milestone instance to evaluate
        notify: If True, send Discord notification for new awards (default: True)

    Returns:
        dict: Dictionary with keys:
            - 'awarded': bool - True if milestone requirements are met
            - 'created': bool - True if milestone was newly awarded
    """
    from trophies.models import UserMilestoneProgress, UserMilestone
    from trophies.milestone_handlers import MILESTONE_HANDLERS
    from trophies.discord_utils.discord_notifications import notify_new_milestone

    # Skip premium-only milestones for non-premium users
    if milestone.premium_only and not profile.user_is_premium:
        return {'awarded': False, 'created': False}

    # Get the appropriate handler for this milestone type
    handler = MILESTONE_HANDLERS.get(milestone.criteria_type)
    if not handler:
        logger.warning(f"No handler for criteria_type: {milestone.criteria_type}")
        return {'awarded': False, 'created': False}

    # Execute handler to get current progress
    result = handler(profile, milestone)

    # Update progress tracking
    progress, created = UserMilestoneProgress.objects.get_or_create(
        profile=profile,
        milestone=milestone,
        defaults={'progress_value': result['progress']}
    )
    if result['updated'] or progress.progress_value != result['progress']:
        progress.progress_value = result['progress']
        progress.save(update_fields=['progress_value'])

    # Award milestone if achieved
    if result['achieved']:
        user_milestone, created = UserMilestone.objects.get_or_create(
            profile=profile, milestone=milestone
        )
        if created:
            milestone.earned_count += 1
            milestone.save(update_fields=['earned_count'])
            # Create UserTitle if milestone has an associated title
            if milestone.title:
                UserTitle.objects.get_or_create(
                    profile=profile,
                    title=milestone.title,
                    defaults={
                        'source_type': 'milestone',
                        'source_id': milestone.id
                    }
                )

        # Assign Discord role if applicable
        if milestone.discord_role_id and profile.is_discord_verified and profile.discord_id:
            notify_bot_role_earned(profile, milestone.discord_role_id)
            if notify:
                notify_new_milestone(profile, milestone)

        return {'awarded': True, 'created': created}

    return {'awarded': False, 'created': False}


def check_all_milestones_for_user(profile, criteria_type=None):
    """
    Batch check all relevant milestones for a profile.

    This function checks all milestones (or milestones of a specific type)
    for a profile. For platinum count milestones, it uses special logic to
    only notify about the highest newly earned milestone to avoid spam.

    Args:
        profile: Profile instance to check milestones for
        criteria_type: Optional filter to only check milestones of this type
                       (e.g., 'plat_count', 'manual')

    Returns:
        list: List of newly awarded Milestone instances
    """
    from trophies.models import Milestone
    from trophies.discord_utils.discord_notifications import notify_new_milestone

    qs = Milestone.objects.all()

    if criteria_type:
        qs = qs.filter(criteria_type=criteria_type)

    # Handle non-plat_count milestones normally
    if criteria_type != 'plat_count':
        awarded = []
        for milestone in qs:
            status = check_and_award_milestone(profile, milestone)
            if status['created']:
                awarded.append(milestone)
        return awarded

    # Special handling for plat_count milestones
    # Check all in order (ascending) but only notify for highest earned
    plat_qs = qs.order_by('required_value')
    new_awards = []

    for milestone in plat_qs:
        status = check_and_award_milestone(profile, milestone, notify=False)
        if status['created']:
            new_awards.append(milestone)

    # Send single notification for highest newly earned milestone
    if new_awards and profile.is_discord_verified and profile.discord_id:
        highest = max(new_awards, key=lambda m: m.required_value)
        logger.debug(f"Notifying for milestone {highest}")
        notify_new_milestone(profile, highest)

    return new_awards
