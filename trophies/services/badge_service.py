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

from trophies.models import UserTitle

logger = logging.getLogger("psn_api")


def _build_badge_context(profile, badges):
    """
    Pre-fetch data needed by handle_badge to avoid N+1 queries per badge.

    Returns a dict with:
        - earned_badge_ids: set of Badge IDs the profile has earned
        - badges_by_key: dict of (series_slug, tier) -> Badge for prerequisite lookups
        - stage_data: series_slug -> [(stage_number, required_tiers, game_ids)]
        - plat_game_ids: set of game IDs where profile has platinum
        - complete_game_ids: set of game IDs where profile has 100% progress
    """
    from trophies.models import UserBadge, Stage, ProfileGame

    earned_badge_ids = set(
        UserBadge.objects.filter(
            profile=profile, badge__in=badges
        ).values_list('badge_id', flat=True)
    )
    badges_by_key = {(b.series_slug, b.tier): b for b in badges}

    # Pre-fetch ALL stage completion data for all relevant series in one pass
    series_slugs = {b.series_slug for b in badges if b.series_slug}

    all_stages = (
        Stage.objects
        .filter(series_slug__in=series_slugs)
        .prefetch_related('concepts__games')
    )

    # Build mapping: series_slug -> [(stage_number, required_tiers, game_ids)]
    stage_data = {}
    all_game_ids = set()

    for stage in all_stages:
        slug = stage.series_slug
        if slug not in stage_data:
            stage_data[slug] = []
        game_ids = set()
        for concept in stage.concepts.all():
            for game in concept.games.all():
                game_ids.add(game.id)
        stage_data[slug].append((stage.stage_number, stage.required_tiers, game_ids))
        all_game_ids.update(game_ids)

    # Two queries: fetch all plat'd and 100%'d game IDs for this profile
    plat_game_ids = set(
        ProfileGame.objects.filter(
            profile=profile, game_id__in=all_game_ids, has_plat=True
        ).values_list('game_id', flat=True)
    ) if all_game_ids else set()

    complete_game_ids = set(
        ProfileGame.objects.filter(
            profile=profile, game_id__in=all_game_ids, progress=100
        ).values_list('game_id', flat=True)
    ) if all_game_ids else set()

    return {
        'earned_badge_ids': earned_badge_ids,
        'badges_by_key': badges_by_key,
        'stage_data': stage_data,
        'plat_game_ids': plat_game_ids,
        'complete_game_ids': complete_game_ids,
    }


def _get_stage_completion_from_cache(badge, _context):
    """
    Compute stage completion from pre-fetched cache instead of per-badge DB queries.
    Mirrors the logic in Badge.get_stage_completion() but uses cached data from
    _build_badge_context(), reducing O(2B) queries to O(0) for badge evaluation.
    """
    stage_entries = _context.get('stage_data', {}).get(badge.series_slug, [])

    is_plat_check = False
    is_progress_check = False

    if badge.badge_type in ['series', 'collection']:
        is_plat_check = badge.tier in [1, 3]
        is_progress_check = badge.tier in [2, 4]
    elif badge.badge_type == 'megamix':
        is_plat_check = True
    else:
        return {}

    if is_plat_check:
        completed_ids = _context['plat_game_ids']
    elif is_progress_check:
        completed_ids = _context['complete_game_ids']
    else:
        return {}

    completion = {}
    for stage_number, required_tiers, game_ids in stage_entries:
        # Same logic as Stage.applies_to_tier: empty required_tiers means all tiers
        if required_tiers and badge.tier not in required_tiers:
            continue
        if not game_ids:
            completion[stage_number] = False
            continue
        completion[stage_number] = bool(completed_ids & game_ids)

    return completion


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
    )

    if not pg_qs.exists():
        logger.info(f"No ProfileGames found for profile {profile.psn_username}")
        return 0

    concept_ids = pg_qs.values_list(
        'game__concept_id', flat=True
    ).filter(game__concept__isnull=False).distinct()

    stages = Stage.objects.filter(concepts__id__in=concept_ids).distinct()
    series_slugs = stages.values_list('series_slug', flat=True).distinct()
    badges = list(Badge.objects.filter(series_slug__in=series_slugs, is_live=True).distinct().order_by('tier'))

    # Pre-fetch context to avoid N+1 queries per badge
    badge_ctx = _build_badge_context(profile, badges)

    # Use bulk context manager to defer gamification updates until all badges are processed
    # This prevents N separate ProfileGamification recalculations during sync
    from trophies.services.xp_service import bulk_gamification_update

    checked_count = 0
    with bulk_gamification_update():
        for badge in badges:
            try:
                handle_badge(profile, badge, add_role_only=skip_notis, _context=badge_ctx)
                checked_count += 1
            except Exception:
                logger.exception(
                    f"Error checking badge {badge.id} for profile {profile.psn_username}"
                )

    duration = time.time() - start_time
    logger.info(
        f"Checked {checked_count} unique badges for profile "
        f"{profile.psn_username} in {duration:.2f}s"
    )
    return checked_count


def _check_prerequisite_tier(profile, badge, _context=None):
    """
    Check if previous tier badge has been earned (prerequisite check).

    Args:
        profile: Profile instance
        badge: Badge instance to check prerequisites for
        _context: Optional pre-fetched context from _build_badge_context

    Returns:
        bool: True if prerequisite is met or no prerequisite exists, False otherwise
    """
    from trophies.models import UserBadge, Badge

    if badge.tier <= 1:
        return True

    prev_tier = badge.tier - 1

    # Use pre-fetched context to avoid per-badge queries
    if _context:
        prev_badge = _context['badges_by_key'].get((badge.series_slug, prev_tier))
        if prev_badge:
            return prev_badge.id in _context['earned_badge_ids']
        # prev tier not in current batch; fall through to DB query

    # Fallback for standalone calls or when prev tier not in batch
    prev_badge = Badge.objects.filter(
        series_slug=badge.series_slug, tier=prev_tier, is_live=True
    ).first()

    return prev_badge and UserBadge.objects.filter(
        profile=profile, badge=prev_badge
    ).exists()


def _update_badge_progress(profile, badge, completed_count):
    """
    Update or create UserBadgeProgress for a badge.

    Args:
        profile: Profile instance
        badge: Badge instance
        completed_count: Number of completed stages/concepts

    Returns:
        UserBadgeProgress: The updated or created progress instance
    """
    from trophies.models import UserBadgeProgress

    progress, created = UserBadgeProgress.objects.get_or_create(
        profile=profile,
        badge=badge,
        defaults={'completed_concepts': completed_count}
    )
    if not created:
        progress.completed_concepts = completed_count
        progress.last_checked = timezone.now()
        progress.save(update_fields=['completed_concepts', 'last_checked'])

    return progress


def _award_badge(profile, badge, _already_checked_exists=None):
    """
    Award a badge to a profile and create associated title if applicable.

    Args:
        profile: Profile instance
        badge: Badge instance to award
        _already_checked_exists: If provided (True/False), skip the .exists() check

    Returns:
        bool: True if badge was newly created, False if already exists
    """
    from trophies.models import UserBadge

    if _already_checked_exists is None:
        user_badge_exists = UserBadge.objects.filter(
            profile=profile, badge=badge
        ).exists()
    else:
        user_badge_exists = _already_checked_exists

    if user_badge_exists:
        return False

    _, created = UserBadge.objects.get_or_create(profile=profile, badge=badge)
    if not created:
        return False
    logger.info(
        f"Awarded badge {badge.effective_display_title} (tier: {badge.tier}) "
        f"to {profile.display_psn_username}"
    )

    # Create UserTitle if badge has an associated title
    if badge.title:
        UserTitle.objects.get_or_create(
            profile=profile,
            title=badge.title,
            defaults={
                'source_type': 'badge',
                'source_id': badge.id
            }
        )

    return True


def _revoke_badge(profile, badge):
    """
    Revoke a badge from a profile and remove associated title if applicable.

    Args:
        profile: Profile instance
        badge: Badge instance to revoke
    """
    from trophies.models import UserBadge

    UserBadge.objects.filter(profile=profile, badge=badge).delete()
    logger.info(
        f"Revoked badge {badge.effective_display_title} (tier: {badge.tier}) "
        f"from {profile.display_psn_username}"
    )

    # Remove UserTitle if badge had an associated title
    if badge.title:
        UserTitle.objects.filter(
            profile=profile,
            title=badge.title,
            source_type='badge',
            source_id=badge.id
        ).delete()


def _process_badge_award_revoke(profile, badge, badge_earned, prev_badge_earned, _context=None):
    """
    Process badge awarding or revoking based on completion status.

    Args:
        profile: Profile instance
        badge: Badge instance
        badge_earned: Whether the badge requirements are met
        prev_badge_earned: Whether prerequisite tier is met
        _context: Optional pre-fetched context to update after award/revoke

    Returns:
        bool: True if badge was newly created, False otherwise
    """
    from trophies.models import UserBadge

    # Use context to check existence if available, otherwise query
    if _context:
        user_badge_exists = badge.id in _context['earned_badge_ids']
    else:
        user_badge_exists = UserBadge.objects.filter(
            profile=profile, badge=badge
        ).exists()
    badge_created = False

    if prev_badge_earned and badge_earned and not user_badge_exists:
        badge_created = _award_badge(profile, badge, _already_checked_exists=False)
        # Update context so subsequent tier checks see this badge as earned
        if badge_created and _context:
            _context['earned_badge_ids'].add(badge.id)
    elif (not badge_earned or not prev_badge_earned) and user_badge_exists:
        _revoke_badge(profile, badge)
        # Remove Discord role after transaction commits (avoid holding DB txn open during HTTP)
        if badge.discord_role_id and profile.is_discord_verified and profile.discord_id:
            role_id = badge.discord_role_id
            transaction.on_commit(lambda rid=role_id: notify_bot_role_removed(profile, rid))
        # Update context so subsequent tier checks see this badge as revoked
        if _context:
            _context['earned_badge_ids'].discard(badge.id)

    return badge_created


def _handle_discord_notifications(profile, badge, badge_created, add_role_only):
    """
    Handle Discord role assignment and notifications for badge earning.

    Args:
        profile: Profile instance
        badge: Badge instance
        badge_created: Whether the badge was newly created
        add_role_only: If True, only assign roles without sending notifications
    """
    from trophies.discord_utils.discord_notifications import notify_new_badge

    if not badge_created or not badge.discord_role_id:
        return

    if not profile.is_discord_verified or not profile.discord_id:
        return

    # Assign Discord role after transaction commits (avoid holding DB txn open during HTTP)
    role_id = badge.discord_role_id
    transaction.on_commit(lambda rid=role_id: notify_bot_role_earned(profile, rid))

    # Send Discord notification for newly earned badge
    if not add_role_only:
        transaction.on_commit(lambda b=badge: notify_new_badge(profile, b))


@transaction.atomic
def handle_badge(profile, badge, add_role_only=False, _context=None):
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
        _context: Optional pre-fetched context from _build_badge_context
                  to avoid N+1 queries during batch processing

    Returns:
        bool: True if badge was newly created, False otherwise
    """
    if not profile or not badge:
        return False

    # Check prerequisite: Previous tier must be earned first
    prev_badge_earned = _check_prerequisite_tier(profile, badge, _context=_context)

    # Handle series and collection badges (concept-based)
    if badge.badge_type in ['series', 'collection']:
        # Use pre-fetched cache when available (batch path), fall back to per-badge query (standalone)
        if _context and 'stage_data' in _context:
            stage_completion_dict = _get_stage_completion_from_cache(badge, _context)
        else:
            stage_completion_dict = badge.get_stage_completion(profile, badge.badge_type)
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
        _update_badge_progress(profile, badge, completed_count)

        # Award or revoke badge based on completion
        badge_created = _process_badge_award_revoke(profile, badge, badge_earned, prev_badge_earned, _context=_context)

        # Handle Discord notifications
        if prev_badge_earned and badge_earned:
            _handle_discord_notifications(profile, badge, badge_created, add_role_only)

        return badge_created

    # Handle megamix badges (flexible completion requirements)
    elif badge.badge_type == 'megamix':
        if _context and 'stage_data' in _context:
            stage_completion_dict = _get_stage_completion_from_cache(badge, _context)
        else:
            stage_completion_dict = badge.get_stage_completion(profile, badge.badge_type)
        completed_count = 0

        for stage, is_complete in stage_completion_dict.items():
            if stage == 0:  # Stage 0 is optional/tangential
                continue
            if is_complete:
                completed_count += 1

        # Determine if badge is earned based on requires_all flag
        if badge.requires_all:
            # All non-zero stages must be completed
            required_stages = sum(1 for stage in stage_completion_dict if stage != 0)
            badge_earned = completed_count >= required_stages
        else:
            # At least min_required stages must be completed
            badge_earned = completed_count >= badge.min_required

        # Update progress tracking
        _update_badge_progress(profile, badge, completed_count)

        # Award or revoke badge based on completion
        badge_created = _process_badge_award_revoke(profile, badge, badge_earned, prev_badge_earned, _context=_context)

        # Handle Discord notifications
        if prev_badge_earned and badge_earned:
            _handle_discord_notifications(profile, badge, badge_created, add_role_only)

        return badge_created

    # 'misc' badges are admin-awarded only and not evaluated automatically.
    return False


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
        response = requests.post(url, json=data, headers=headers, timeout=10)
        response.raise_for_status()
        logger.info(
            f"Bot notified: Assigned role {role_id} to {profile.discord_id}."
        )
    except requests.RequestException:
        logger.exception(
            f"Bot notification failed for role {role_id} "
            f"(user {profile.psn_username})"
        )


def notify_bot_role_removed(profile, role_id):
    """
    Notify Discord bot to remove a role from a user.

    This function calls the Discord bot API to remove a role when a user
    loses a badge, milestone, or subscription with an associated Discord role.

    Args:
        profile: Profile instance with discord_id set
        role_id: Discord role ID to remove
    """
    if settings.DEBUG:
        return

    try:
        url = settings.BOT_API_URL + "/remove-role"
        headers = {
            'Authorization': f"Bearer {settings.BOT_API_KEY}",
            'Content-Type': 'application/json'
        }
        data = {
            'user_id': profile.discord_id,
            'role_id': role_id,
        }
        response = requests.post(url, json=data, headers=headers, timeout=10)
        response.raise_for_status()
        logger.info(
            f"Bot notified: Removed role {role_id} from {profile.discord_id}."
        )
    except requests.RequestException:
        logger.exception(
            f"Bot role removal failed for role {role_id} "
            f"(user {profile.psn_username})"
        )


def sync_discord_roles(profile):
    """
    Sync ALL Discord roles for a verified profile.

    Collects every role the user has earned (badges, milestones, premium) and
    calls notify_bot_role_earned for each. The bot's /assign-role endpoint is
    idempotent, so re-assigning an existing role is harmless.

    Intended to be called:
    - By the bot when a user first verifies (POST /api/v1/sync-roles/)
    - By a /sync-roles slash command the user can trigger manually

    Args:
        profile: Profile instance (must have discord_id and is_discord_verified)

    Returns:
        dict with counts of roles synced by source
    """
    from trophies.models import UserBadge, UserMilestone

    if not profile.is_discord_verified or not profile.discord_id:
        return {'badge_roles': 0, 'milestone_roles': 0, 'premium_roles': 0}

    role_counts = {'badge_roles': 0, 'milestone_roles': 0, 'premium_roles': 0}

    # Badge roles
    badge_role_ids = list(
        UserBadge.objects.filter(
            profile=profile,
            badge__discord_role_id__isnull=False,
        ).exclude(
            badge__discord_role_id=0
        ).values_list('badge__discord_role_id', flat=True)
    )
    for role_id in badge_role_ids:
        notify_bot_role_earned(profile, role_id)
    role_counts['badge_roles'] = len(badge_role_ids)

    # Milestone roles
    milestone_role_ids = list(
        UserMilestone.objects.filter(
            profile=profile,
            milestone__discord_role_id__isnull=False,
        ).exclude(
            milestone__discord_role_id=0
        ).values_list('milestone__discord_role_id', flat=True)
    )
    for role_id in milestone_role_ids:
        notify_bot_role_earned(profile, role_id)
    role_counts['milestone_roles'] = len(milestone_role_ids)

    # Premium roles
    if profile.user_is_premium and profile.user:
        from users.constants import PREMIUM_DISCORD_ROLE_TIERS, SUPPORTER_DISCORD_ROLE_TIERS
        tier = profile.user.premium_tier
        if tier in PREMIUM_DISCORD_ROLE_TIERS and settings.DISCORD_PREMIUM_ROLE:
            notify_bot_role_earned(profile, settings.DISCORD_PREMIUM_ROLE)
            role_counts['premium_roles'] += 1
        elif tier in SUPPORTER_DISCORD_ROLE_TIERS and settings.DISCORD_PREMIUM_PLUS_ROLE:
            notify_bot_role_earned(profile, settings.DISCORD_PREMIUM_PLUS_ROLE)
            role_counts['premium_roles'] += 1

    total = sum(role_counts.values())
    logger.info(
        f"Synced {total} Discord role(s) for {profile.psn_username}: {role_counts}"
    )

    return role_counts


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
    )

    if not pg_qs.exists():
        logger.info(f"No ProfileGames found for profile {profile.psn_username}")
        return 0

    concept_ids = pg_qs.values_list(
        'game__concept_id', flat=True
    ).filter(game__concept__isnull=False).distinct()

    stages = Stage.objects.filter(concepts__id__in=concept_ids).distinct()
    series_slugs = stages.values_list('series_slug', flat=True).distinct()
    badges = list(Badge.objects.filter(series_slug__in=series_slugs, is_live=True).distinct().order_by('tier'))

    # Pre-fetch context to avoid N+1 queries per badge
    badge_ctx = _build_badge_context(profile, badges)

    role_granting_badges = []
    checked_count = 0

    for badge in badges:
        try:
            created = handle_badge(profile, badge, add_role_only=True, _context=badge_ctx)
            checked_count += 1
            if created and badge.discord_role_id:
                role_granting_badges.append(badge)
        except Exception:
            logger.exception(
                f"Error checking badge {badge.id} for profile {profile.psn_username}"
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
