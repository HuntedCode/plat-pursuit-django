"""
Milestone service - Handles milestone checking, awarding, and progress tracking.

This service manages milestone-related business logic including:
- Checking if profiles have achieved milestones
- Awarding milestones and updating progress
- Discord role assignments for milestones
- Batch checking of multiple milestones
"""
import logging
from collections import defaultdict
from django.db import transaction
from django.db.models import F
from django.conf import settings
import requests

from trophies.models import UserTitle
from trophies.milestone_constants import ONE_OFF_TYPES

logger = logging.getLogger("psn_api")


def notify_bot_role_earned(profile, role_id):
    """
    Notify Discord bot to assign a role to a user.

    This function calls the Discord bot API to assign a role when a user
    earns a milestone with an associated Discord role. Called on every check
    (not just first award) so roles are re-assigned if a user leaves and
    rejoins the Discord server.

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
        logger.exception(
            f"Bot notification failed for role {role_id} "
            f"(user {profile.psn_username}): {e}"
        )


@transaction.atomic
def check_and_award_milestone(profile, milestone, _cache=None):
    """
    Check if a milestone is achieved for a profile and award if earned.

    This function evaluates milestone criteria using the appropriate handler,
    updates progress tracking, and awards the milestone if requirements are met.

    Args:
        profile: Profile instance to check
        milestone: Milestone instance to evaluate
        _cache: Optional dict for handler-level caching across tiers of the
                same criteria_type. Prevents redundant DB queries when checking
                multiple tiers in a single batch.

    Returns:
        dict: Dictionary with keys:
            - 'awarded': bool - True if milestone requirements are met
            - 'created': bool - True if milestone was newly awarded
    """
    from trophies.models import UserMilestoneProgress, UserMilestone
    from trophies.milestone_handlers import MILESTONE_HANDLERS

    # Skip premium-only milestones for non-premium users
    if milestone.premium_only and not profile.user_is_premium:
        return {'awarded': False, 'created': False}

    # Get the appropriate handler for this milestone type
    handler = MILESTONE_HANDLERS.get(milestone.criteria_type)
    if not handler:
        logger.warning(f"No handler for criteria_type: {milestone.criteria_type}")
        return {'awarded': False, 'created': False}

    # Execute handler to get current progress
    result = handler(profile, milestone, _cache=_cache)

    # Update progress tracking (only write when value actually changed)
    progress, created = UserMilestoneProgress.objects.get_or_create(
        profile=profile,
        milestone=milestone,
        defaults={'progress_value': result['progress']}
    )
    if progress.progress_value != result['progress']:
        progress.progress_value = result['progress']
        progress.save(update_fields=['progress_value'])

    # Award milestone if achieved
    if result['achieved']:
        user_milestone, created = UserMilestone.objects.get_or_create(
            profile=profile, milestone=milestone
        )
        if created:
            from trophies.models import Milestone as MilestoneModel
            MilestoneModel.objects.filter(pk=milestone.pk).update(earned_count=F('earned_count') + 1)
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

        # Assign Discord role if applicable (idempotent: safe to call every check
        # so roles are re-assigned if user leaves/rejoins the Discord server).
        # Deferred to after transaction commit to avoid holding DB connection
        # open during external HTTP calls.
        if milestone.discord_role_id and profile.is_discord_verified and profile.discord_id:
            transaction.on_commit(
                lambda p=profile, r=milestone.discord_role_id: notify_bot_role_earned(p, r)
            )

        return {'awarded': True, 'created': created, 'user_milestone': user_milestone}

    return {'awarded': False, 'created': False, 'user_milestone': None}


def check_all_milestones_for_user(profile, criteria_type=None, criteria_types=None,
                                  exclude_types=None, notify_webapp=True):
    """
    Batch check all relevant milestones for a profile.

    For tiered types: checks all milestones but only sends notifications
    for the highest newly earned milestone per criteria type, preventing
    spam when a user qualifies for multiple tiers at once.

    For one-off types: notifies individually since they have at most 1 tier.

    When no type filter is given, groups milestones by criteria_type and
    processes each group independently so every type gets its own notification.

    Uses a shared _cache dict so handlers only query expensive values once
    across all tiers of the same criteria_type.

    Args:
        profile: Profile instance to check milestones for
        criteria_type: Optional single criteria_type filter
        criteria_types: Optional iterable of criteria_types to check
        exclude_types: Optional set of criteria_types to skip (e.g., calendar
                       types when they're checked separately)
        notify_webapp: If True, send in-app notification for highest tier earned.

    Returns:
        list: List of newly awarded Milestone instances
    """
    from trophies.models import Milestone
    from notifications.signals import create_milestone_notification

    if criteria_type and criteria_types:
        raise ValueError("Pass criteria_type or criteria_types, not both")

    qs = Milestone.objects.all()

    if criteria_type:
        qs = qs.filter(criteria_type=criteria_type)
    elif criteria_types:
        qs = qs.filter(criteria_type__in=criteria_types)

    if exclude_types:
        qs = qs.exclude(criteria_type__in=exclude_types)

    # Shared cache for handler-level value reuse across tiers
    _cache = {}

    # Group milestones by criteria_type and process each independently
    milestones_by_type = defaultdict(list)
    for milestone in qs.order_by('required_value'):
        milestones_by_type[milestone.criteria_type].append(milestone)

    all_awarded = []

    for ctype, milestones in milestones_by_type.items():
        if ctype in ONE_OFF_TYPES:
            # One-off: notify individually (no spam risk, at most 1 tier)
            for milestone in milestones:
                status = check_and_award_milestone(
                    profile, milestone, _cache=_cache
                )
                if status['created']:
                    all_awarded.append(milestone)
                    if notify_webapp and status['user_milestone']:
                        create_milestone_notification(status['user_milestone'])
        else:
            # Tiered: check all, notify highest per type
            new_awards = []
            new_award_statuses = {}
            for milestone in milestones:
                status = check_and_award_milestone(
                    profile, milestone, _cache=_cache
                )
                if status['created']:
                    new_awards.append(milestone)
                    new_award_statuses[milestone.id] = status
            if new_awards:
                highest = max(new_awards, key=lambda m: m.required_value)
                if notify_webapp:
                    um = new_award_statuses[highest.id].get('user_milestone')
                    if um:
                        create_milestone_notification(um)
                all_awarded.extend(new_awards)

    return all_awarded
