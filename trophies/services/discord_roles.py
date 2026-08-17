"""Discord role assignment: the bot-facing side of who-gets-what-role.

Extracted from `badge_service` in the 2026-08 badge cutover. It lived there for historical reasons --
badges once granted Discord roles -- but migration 0251 removed `Badge.discord_role_id` and the badge
half of this was retired well before the rebuild. What remains has nothing to do with badges: roles come
from the milestones ladder and from premium tier.

The extraction is load-bearing, not tidying. Seven live consumers outside the badge system call these
helpers (milestones, subscriptions, the premium-downgrade hook on `Profile`), so leaving them in
`badge_service` would mean deleting the legacy badge engine takes subscriptions and milestone roles with
it.

`sync_discord_roles` keeps `badge_roles: 0` in its response because PlatBot reads that key. It is a
contract stub, not a live count.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger("psn_api")


def notify_bot_role_earned(profile, role_id):
    """Ask the bot to assign a role. The bot's /assign-role is idempotent, so re-assigning is harmless.

    Never raises: a role is cosmetic, and the callers are mid-transaction on things that matter more
    (a subscription webhook, a milestone award). A failed role push is logged and reconciled by the
    next /sync-roles.
    """
    if settings.DEBUG:
        return

    try:
        response = requests.post(
            settings.BOT_API_URL + "/assign-role",
            json={'user_id': profile.discord_id, 'role_id': role_id},
            headers={
                'Authorization': f"Bearer {settings.BOT_API_KEY}",
                'Content-Type': 'application/json',
            },
            timeout=10,
        )
        response.raise_for_status()
        logger.info(f"Bot notified: Assigned role {role_id} to {profile.discord_id}.")
    except requests.RequestException:
        logger.exception(
            f"Bot notification failed for role {role_id} (user {profile.psn_username})"
        )


def notify_bot_role_removed(profile, role_id):
    """Ask the bot to remove a role. Same never-raises contract as `notify_bot_role_earned`."""
    if settings.DEBUG:
        return

    try:
        response = requests.post(
            settings.BOT_API_URL + "/remove-role",
            json={'user_id': profile.discord_id, 'role_id': role_id},
            headers={
                'Authorization': f"Bearer {settings.BOT_API_KEY}",
                'Content-Type': 'application/json',
            },
            timeout=10,
        )
        response.raise_for_status()
        logger.info(f"Bot notified: Removed role {role_id} from {profile.discord_id}.")
    except requests.RequestException:
        logger.exception(
            f"Bot role removal failed for role {role_id} (user {profile.psn_username})"
        )


def sync_discord_roles(profile):
    """Reconcile every Discord role a verified profile has earned, from all sources.

    Called by the bot when a user first verifies (POST /api/v1/sync-roles/) and by the /sync-roles slash
    command. Idempotent by way of the bot's assign endpoint, so it doubles as the self-heal path when a
    role push was dropped.

    Returns per-source counts. `badge_roles` is always 0 (see module docstring).
    """
    if not profile.is_discord_verified or not profile.discord_id:
        return {'badge_roles': 0, 'milestone_roles': 0, 'premium_roles': 0}

    role_counts = {'badge_roles': 0, 'milestone_roles': 0, 'premium_roles': 0}

    # Milestone roles: the milestones app owns these (highest earned rung per ladder).
    from milestones.services import desired_milestone_roles
    milestone_role_ids = list(desired_milestone_roles(profile))
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
    logger.info(f"Synced {total} Discord role(s) for {profile.psn_username}: {role_counts}")

    return role_counts
