"""
XP Service - Centralized logic for Badge XP calculations and updates.

This service consolidates all XP-related logic that was previously scattered across:
- leaderboard_service.py (compute_badge_xp_leaderboard)
- notifications/signals.py (_calculate_badge_xp)
- notifications/services/shareable_data_service.py (get_badge_xp_for_game)

All XP calculation should go through this service to ensure consistency.
"""
import logging
import threading
from contextlib import contextmanager
from django.db import transaction

from trophies.util_modules.constants import (
    BRONZE_STAGE_XP, SILVER_STAGE_XP, GOLD_STAGE_XP, PLAT_STAGE_XP, BADGE_TIER_XP
)

logger = logging.getLogger(__name__)

# Tier XP mapping - single source of truth
TIER_XP_MAP = {
    1: BRONZE_STAGE_XP,   # 250
    2: SILVER_STAGE_XP,   # 75
    3: GOLD_STAGE_XP,     # 250
    4: PLAT_STAGE_XP,     # 75
}

# Thread-local storage for bulk update context
_bulk_update_context = threading.local()


def get_tier_xp(tier: int) -> int:
    """
    Get XP value for a specific badge tier.

    Args:
        tier: Badge tier (1=Bronze, 2=Silver, 3=Gold, 4=Platinum)

    Returns:
        int: XP multiplier for the tier
    """
    return TIER_XP_MAP.get(tier, 0)


def calculate_progress_xp_for_badge(badge, completed_concepts: int) -> int:
    """
    Calculate progress XP for a single badge based on completed concepts.

    Args:
        badge: Badge instance
        completed_concepts: Number of completed concepts/stages

    Returns:
        int: XP value for this badge's progress
    """
    tier_xp = get_tier_xp(badge.tier)
    return completed_concepts * tier_xp


def calculate_series_xp(profile, series_slug: str) -> int:
    """
    Calculate total XP for a specific badge series.

    Includes:
    - Progress XP from all tiers in the series
    - Badge completion bonuses (3000 XP per earned badge)

    Args:
        profile: Profile instance
        series_slug: Badge series identifier

    Returns:
        int: Total XP for this series
    """
    from trophies.models import UserBadgeProgress, UserBadge

    if not series_slug:
        return 0

    # Calculate progress XP for this series
    progress_records = UserBadgeProgress.objects.filter(
        profile=profile,
        badge__series_slug=series_slug
    ).select_related('badge')

    progress_xp = sum(
        calculate_progress_xp_for_badge(prog.badge, prog.completed_concepts)
        for prog in progress_records
    )

    # Add badge completion bonuses
    badges_earned = UserBadge.objects.filter(
        profile=profile,
        badge__series_slug=series_slug
    ).count()

    return progress_xp + (badges_earned * BADGE_TIER_XP)


def calculate_total_xp(profile) -> tuple[int, dict, int]:
    """
    Calculate total badge XP for a profile.

    Returns:
        tuple: (total_xp, series_breakdown, total_badges_earned)
            - total_xp: Combined progress XP + badge completion bonuses
            - series_breakdown: Dict mapping series_slug to XP
            - total_badges_earned: Count of fully earned badges
    """
    from trophies.models import UserBadgeProgress, UserBadge

    # Get all progress records
    progress_records = UserBadgeProgress.objects.filter(
        profile=profile
    ).select_related('badge')

    # Calculate per-series XP
    series_xp = {}
    total_progress_xp = 0

    for prog in progress_records:
        series_slug = prog.badge.series_slug
        if not series_slug:
            continue

        xp = calculate_progress_xp_for_badge(prog.badge, prog.completed_concepts)

        if series_slug not in series_xp:
            series_xp[series_slug] = 0
        series_xp[series_slug] += xp
        total_progress_xp += xp

    # Add badge completion bonuses to series totals
    earned_badges = UserBadge.objects.filter(
        profile=profile
    ).select_related('badge')

    total_badges = 0
    for user_badge in earned_badges:
        series_slug = user_badge.badge.series_slug
        if series_slug:
            if series_slug not in series_xp:
                series_xp[series_slug] = 0
            series_xp[series_slug] += BADGE_TIER_XP
        total_badges += 1

    total_xp = total_progress_xp + (total_badges * BADGE_TIER_XP)

    return total_xp, series_xp, total_badges


@transaction.atomic
def update_profile_gamification(profile) -> 'ProfileGamification':
    """
    Update or create ProfileGamification with recalculated XP values.

    This is the primary method for updating denormalized XP data.
    Called by signal handlers when UserBadgeProgress or UserBadge changes.

    Args:
        profile: Profile instance

    Returns:
        ProfileGamification: Updated gamification record
    """
    from trophies.models import ProfileGamification

    total_xp, series_xp, total_badges = calculate_total_xp(profile)

    gamification, created = ProfileGamification.objects.update_or_create(
        profile=profile,
        defaults={
            'total_badge_xp': total_xp,
            'series_badge_xp': series_xp,
            'total_badges_earned': total_badges,
        }
    )

    if created:
        logger.info(
            f"Created gamification for {profile.psn_username}: "
            f"total_xp={total_xp}, badges={total_badges}"
        )
    else:
        logger.debug(
            f"Updated gamification for {profile.psn_username}: "
            f"total_xp={total_xp}, badges={total_badges}"
        )

    return gamification


def get_profile_gamification(profile) -> 'ProfileGamification':
    """
    Get or create ProfileGamification for a profile.

    If the record doesn't exist, creates it with calculated values.

    Args:
        profile: Profile instance

    Returns:
        ProfileGamification: The gamification record
    """
    from trophies.models import ProfileGamification

    try:
        return ProfileGamification.objects.get(profile=profile)
    except ProfileGamification.DoesNotExist:
        return update_profile_gamification(profile)


def recalculate_all_gamification() -> int:
    """
    Recalculate gamification stats for ALL profiles with badge progress.

    Used for:
    - Initial data migration
    - Periodic reconciliation
    - Admin bulk operations

    Returns:
        int: Number of profiles updated
    """
    from trophies.models import Profile

    # Get all profiles with any badge progress
    profiles_with_progress = Profile.objects.filter(
        badge_progress__isnull=False
    ).distinct()

    updated_count = 0
    for profile in profiles_with_progress.iterator(chunk_size=100):
        try:
            update_profile_gamification(profile)
            updated_count += 1
        except Exception as e:
            logger.error(f"Failed to update gamification for {profile.psn_username}: {e}")

    logger.info(f"Recalculated gamification for {updated_count} profiles")
    return updated_count


def get_badge_xp_for_game(profile, game) -> int:
    """
    Calculate badge XP earned from completing a specific game/platinum.

    This replaces ShareableDataService.get_badge_xp_for_game() with
    a centralized implementation that uses the same XP constants.

    Args:
        profile: Profile instance
        game: Game instance

    Returns:
        int: XP earned from this game's badge contributions
    """
    from trophies.models import Stage, Badge

    if not game.concept:
        return 0

    total_xp = 0

    # Find stages that include this game's concept
    stages = Stage.objects.filter(
        concepts=game.concept,
        stage_number__gt=0
    )

    for stage in stages:
        # Verify badge series exists
        if not Badge.objects.filter(series_slug=stage.series_slug).exists():
            continue

        # Determine applicable tiers (empty = all tiers)
        applicable_tiers = stage.required_tiers if stage.required_tiers else [1, 2, 3, 4]

        # Sum XP for each applicable tier
        stage_xp = sum(get_tier_xp(tier) for tier in applicable_tiers)
        total_xp += stage_xp

    return total_xp


# --- Bulk Update Context Manager ---

@contextmanager
def bulk_gamification_update():
    """
    Context manager to defer gamification updates during bulk operations.

    When multiple badge updates occur in quick succession (e.g., during sync),
    this prevents N separate gamification recalculations. Instead, affected
    profiles are collected and updated once when the context exits.

    Usage:
        with bulk_gamification_update():
            # Multiple badge updates happen here
            for badge in badges:
                handle_badge(profile, badge)
        # Single gamification update happens after context exits
    """
    _bulk_update_context.active = True
    _bulk_update_context.profiles = set()

    try:
        yield
    finally:
        _bulk_update_context.active = False

        # Update all affected profiles once
        profiles_to_update = _bulk_update_context.profiles
        _bulk_update_context.profiles = set()

        for profile in profiles_to_update:
            try:
                update_profile_gamification(profile)
            except Exception as e:
                logger.error(f"Failed to update gamification for {profile.psn_username}: {e}")


def is_bulk_update_active() -> bool:
    """Check if bulk update context is currently active."""
    return getattr(_bulk_update_context, 'active', False)


def defer_profile_update(profile):
    """
    Mark profile for deferred update during bulk operation.

    Called by signal handlers when bulk context is active.
    The profile will be updated once the bulk context exits.
    """
    if hasattr(_bulk_update_context, 'profiles'):
        _bulk_update_context.profiles.add(profile)
