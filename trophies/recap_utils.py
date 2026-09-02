"""
Shared utilities for Monthly Recap views (page views + API views).
"""
import pytz
from django.utils import timezone


#: Lower sanity bound for a recap URL, shared by the page view and the API. NOT a claim about any
#: hunter -- their real floor is their own first trophy, which enforces itself (a month with no activity
#: yields no recap). This only keeps nonsense out of date arithmetic: `datetime(year, month, 1)` raises
#: for year 0 and `datetime(year + 1, 1, 1)` overflows at the max int, both reachable from `<int:year>`.
#: PSN trophies date to 2008.
MIN_RECAP_YEAR = 2006


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
