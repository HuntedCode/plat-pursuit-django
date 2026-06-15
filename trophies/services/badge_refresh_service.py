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
from trophies.models import Badge, Profile
from trophies.services.badge_service import handle_badge

logger = logging.getLogger('psn_api')


def refresh_badge_series_awards(series_slug):
    """Re-evaluate all earners' badges for `series_slug` and rebuild its leaderboards.

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
    processed = 0
    for profile in profiles:
        for badge in badges:
            try:
                if handle_badge(profile, badge):
                    profiles_changed.add(profile.id)
                processed += 1
            except Exception:
                logger.exception(
                    "refresh_badge_series_awards: series=%s badge=%s profile=%s",
                    series_slug, badge.id, profile.id,
                )

    for profile_id in profiles_changed:
        try:
            DeferredNotificationService.create_badge_notifications(profile_id)
        except Exception:
            logger.exception("refresh_badge_series_awards: notifications for profile %s", profile_id)

    earners_count = progress_count = 0
    try:
        from trophies.services.redis_leaderboard_service import rebuild_series_leaderboards
        earners_count, progress_count = rebuild_series_leaderboards(series_slug)
    except Exception:
        logger.exception("refresh_badge_series_awards: leaderboard rebuild for %s", series_slug)

    return (processed, len(profiles_changed), earners_count, progress_count)
