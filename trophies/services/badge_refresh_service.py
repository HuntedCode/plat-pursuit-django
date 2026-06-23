"""Shared badge-series refresh.

Re-evaluates every earner's badges for a series and rebuilds the series leaderboards.
Used by the `refresh_badge_series` command and by DLC detection
(`detect_dlc_and_refresh`). It calls `handle_badge`, whose lapse behavior differs by
branch (hard-delete on `main`, `status='maintenance'` on `rebuild`); this module is
agnostic to that -- it just drives the per-earner re-evaluation + leaderboard rebuild,
so the same code does the right thing on either branch.
"""
import logging

from notifications.services.deferred_notification_service import DeferredNotificationService
from trophies.discord_utils.discord_notifications import send_badge_earned_notification
from trophies.models import Badge, Profile
from trophies.services.badge_service import handle_badge

logger = logging.getLogger('psn_api')


def refresh_badge_series_awards(series_slug, skip_notifications=False):
    """Re-evaluate all earners' badges for `series_slug` and rebuild its leaderboards.

    `skip_notifications=True` silences ALL earned-badge notifications for this run --
    Discord (the consolidated per-profile batch), on-site, and email -- so a bulk
    re-evaluation doesn't ping users about badges they effectively already held.

    Returns (processed_pairs, profiles_changed, earners_count, progress_count).
    `processed_pairs` is 0 when the series has no badges.
    """
    badges = list(Badge.objects.filter(series_slug=series_slug).order_by('tier'))
    if not badges:
        return (0, 0, 0, 0)

    profiles = Profile.objects.filter(
        played_games__game__concept__stages__series_slug=series_slug
    ).distinct()

    profiles_changed = set()
    created_by_profile = {}  # profile -> [newly-created badges] for the Discord batch
    processed = 0
    for profile in profiles:
        for badge in badges:
            try:
                if handle_badge(profile, badge):
                    profiles_changed.add(profile.id)
                    created_by_profile.setdefault(profile, []).append(badge)
                processed += 1
            except Exception:
                logger.exception(
                    "refresh_badge_series_awards: series=%s badge=%s profile=%s",
                    series_slug, badge.id, profile.id,
                )

    if not skip_notifications:
        # Discord: one consolidated batch per profile that newly earned something.
        for profile, created in created_by_profile.items():
            try:
                send_badge_earned_notification(profile, created)
            except Exception:
                logger.exception("refresh_badge_series_awards: discord notify for profile %s", profile.id)
        # On-site + email (deferred/consolidated).
        for profile_id in profiles_changed:
            try:
                DeferredNotificationService.create_badge_notifications(profile_id)
            except Exception:
                logger.exception("refresh_badge_series_awards: notifications for profile %s", profile_id)
    else:
        # Truly silence on-site + email: drop what the UserBadge award signal queued
        # this run, so a later sync doesn't flush it (the Discord batch was never sent).
        for profile_id in profiles_changed:
            try:
                DeferredNotificationService.discard_badge_notifications(profile_id)
            except Exception:
                logger.exception("refresh_badge_series_awards: discard notifications for profile %s", profile_id)

    earners_count = progress_count = 0
    try:
        from trophies.services.redis_leaderboard_service import rebuild_series_leaderboards
        earners_count, progress_count = rebuild_series_leaderboards(series_slug)
    except Exception:
        logger.exception("refresh_badge_series_awards: leaderboard rebuild for %s", series_slug)

    return (processed, len(profiles_changed), earners_count, progress_count)
