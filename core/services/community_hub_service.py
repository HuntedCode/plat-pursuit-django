"""Community Hub page-data assembler.

Builds the context dict for `core/views.py:CommunityHubView` (and any future
read surfaces that want the same composition). The hub is a fixed-layout
page composed of curated modules that read from existing services and the
new Event system. See docs/features/community-hub.md for the page anatomy
and docs/architecture/event-system.md for the underlying Event taxonomy.

Each module's data lives under its own context key so the template can
render or skip a module independently. The assembler swallows per-module
exceptions so a single broken module never breaks the whole page render —
the template falls back to the module's own empty state.
"""
import logging

logger = logging.getLogger(__name__)


def _get_pursuit_feed_preview(limit=10):
    """Last N globally-visible Pursuit Feed events for the hub preview module.

    Returns a list of Event instances ordered newest-first. Filters via
    `feed_visible()` so soft-deleted-target events are excluded. The
    standalone /community/feed/ page (Phase 8) shares this same pattern but
    paginates instead of slicing.
    """
    from trophies.models import Event
    from trophies.services.event_service import PURSUIT_FEED_TYPES

    return list(
        Event.objects
        .feed_visible()
        .filter(event_type__in=PURSUIT_FEED_TYPES)
        .select_related('profile', 'target_content_type')
        .order_by('-occurred_at')[:limit]
    )


def _get_full_xp_leaderboard(viewer_profile=None, top_n=25):
    """Top N globally + viewer's rank if logged in.

    Returns a dict with: `entries` (list of leaderboard rows from
    redis_leaderboard_service), `viewer_rank` (int or None), `total_count`
    (int). Each row already has `psn_username`, `avatar_url`, `flag`,
    `is_premium`, `displayed_title`, `total_xp`, `total_badges`, `rank`.
    Adds `total_xp_formatted` for template-side display consistency with
    the existing dashboard leaderboard module.
    """
    from trophies.services.redis_leaderboard_service import (
        get_xp_top, get_xp_rank, get_xp_count,
    )

    entries = get_xp_top(top_n)
    # Match by psn_username because the Redis display dict does not include
    # profile_id (only psn_username, avatar_url, flag, etc.). psn_username
    # is unique on Profile, so this is safe.
    viewer_username = viewer_profile.psn_username if viewer_profile else None
    for e in entries:
        e['total_xp_formatted'] = f"{e.get('total_xp', 0):,}"
        e['is_self'] = (
            viewer_username is not None
            and e.get('psn_username') == viewer_username
        )

    viewer_rank = None
    if viewer_profile is not None:
        viewer_rank = get_xp_rank(viewer_profile.id)

    return {
        'entries': entries,
        'viewer_rank': viewer_rank,
        'total_count': get_xp_count(),
    }


def _get_full_country_leaderboard(viewer_profile, top_n=25):
    """Top N for the viewer's country. Returns None if viewer is anonymous or has no country.

    Same shape as _get_full_xp_leaderboard but scoped to a country code.
    """
    if viewer_profile is None or not viewer_profile.country_code:
        return None
    from trophies.services.redis_leaderboard_service import (
        get_country_xp_top, get_country_xp_rank, get_country_xp_count,
    )

    code = viewer_profile.country_code
    entries = get_country_xp_top(code, top_n)
    viewer_username = viewer_profile.psn_username
    for e in entries:
        e['total_xp_formatted'] = f"{e.get('total_xp', 0):,}"
        e['is_self'] = e.get('psn_username') == viewer_username

    return {
        'entries': entries,
        'viewer_rank': get_country_xp_rank(code, viewer_profile.id),
        'total_count': get_country_xp_count(code),
        'country_code': code,
        'country_name': viewer_profile.country or code,
    }


def _get_top_reviewers(limit=10):
    """Delegates to ReviewHubService.get_top_reviewers (added in Phase 7)."""
    from trophies.services.review_hub_service import ReviewHubService
    return ReviewHubService.get_top_reviewers(limit=limit)


def _get_active_challenge_events(limit=12):
    """Recent challenge_started + challenge_completed events for the activity module.

    Mixes both event types so the user sees both "X started a Genre
    Challenge" and "Y completed an A-Z Challenge" in the same feed.
    """
    from trophies.models import Event

    return list(
        Event.objects
        .feed_visible()
        .filter(event_type__in=('challenge_started', 'challenge_completed'))
        .select_related('profile')
        .order_by('-occurred_at')[:limit]
    )


def build_community_hub_context(viewer_profile=None):
    """Build the full template context for the Community Hub page.

    Each module's data is computed in its own try/except so a single
    broken module never breaks the whole page. The template renders
    each module independently from its own context key, so missing data
    falls back to the module's empty state.

    `viewer_profile` is the Profile of the requesting user, or None for
    anonymous visitors. Used to mark the viewer's row in leaderboards
    and to scope the country leaderboard.

    Returns a dict with these keys:
        - feed_preview: list of Event instances for the Pursuit Feed preview
        - xp_leaderboard: dict with entries + viewer_rank + total_count
        - country_leaderboard: dict (logged-in + has country) or None
        - top_reviewers: list of profile dicts
        - active_challenges: list of challenge_started/completed Events
        - site_heartbeat: cached community stats ribbon (None if cron broken)

    The standard `active_fundraiser` and `hotbar` context keys are added
    by their respective context processors / mixins, not here.
    """
    context = {}

    try:
        context['feed_preview'] = _get_pursuit_feed_preview(limit=10)
    except Exception:
        logger.exception("Failed to load community hub feed_preview")
        context['feed_preview'] = []

    try:
        context['xp_leaderboard'] = _get_full_xp_leaderboard(viewer_profile, top_n=25)
    except Exception:
        logger.exception("Failed to load community hub xp_leaderboard")
        context['xp_leaderboard'] = {'entries': [], 'viewer_rank': None, 'total_count': 0}

    try:
        context['country_leaderboard'] = _get_full_country_leaderboard(viewer_profile, top_n=25)
    except Exception:
        logger.exception("Failed to load community hub country_leaderboard")
        context['country_leaderboard'] = None

    try:
        context['top_reviewers'] = _get_top_reviewers(limit=10)
    except Exception:
        logger.exception("Failed to load community hub top_reviewers")
        context['top_reviewers'] = []

    try:
        context['active_challenges'] = _get_active_challenge_events(limit=12)
    except Exception:
        logger.exception("Failed to load community hub active_challenges")
        context['active_challenges'] = []

    # Site heartbeat: reuse the cached value the dashboard already pulls.
    # Returns None if the refresh_homepage_hourly cron is broken; the
    # built_for_hunters partial silently hides in that case.
    try:
        from trophies.views.dashboard_views import _get_site_heartbeat
        context['site_heartbeat'] = _get_site_heartbeat()
    except Exception:
        logger.exception("Failed to load site_heartbeat for community hub")
        context['site_heartbeat'] = None

    return context
