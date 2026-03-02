"""
Shared utilities for Monthly Recap views (page views + API views).
"""
import pytz
from django.utils import timezone


def get_user_local_now(request):
    """Get current time in the authenticated user's timezone."""
    now = timezone.now()
    if request.user.is_authenticated:
        try:
            return now.astimezone(pytz.timezone(request.user.user_timezone or 'UTC'))
        except pytz.exceptions.UnknownTimeZoneError:
            pass
    return now


def get_most_recent_completed_month(now_local):
    """
    Get the (year, month) tuple for the most recent completed month.

    The previous calendar month is always considered the "featured" recap
    for non-premium users.
    """
    if now_local.month == 1:
        return (now_local.year - 1, 12)
    else:
        return (now_local.year, now_local.month - 1)


def is_most_recent_completed_month(year, month, now_local):
    """Check if the given year/month is the most recent completed month."""
    recent_year, recent_month = get_most_recent_completed_month(now_local)
    return year == recent_year and month == recent_month


def check_sync_freshness(profile, now_local):
    """
    Check whether the profile has synced within the current calendar month
    (in the user's local timezone).

    Returns True if fresh, False if stale or last_synced is None.
    """
    if not profile.last_synced:
        return False
    user_tz = now_local.tzinfo
    last_synced_local = profile.last_synced.astimezone(user_tz)
    return (
        last_synced_local.year == now_local.year
        and last_synced_local.month == now_local.month
    )
