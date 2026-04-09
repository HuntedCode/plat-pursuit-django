"""Community Hub page-data assembler.

Builds the context dict for ``core/views.py:CommunityHubView``. The hub is
a fixed-layout **Feature Spotlight** page (NOT an aggregator) composed of:

- Hero (built_for_hunters site heartbeat ribbon)
- Conditional active fundraiser banner
- 2x2 Feature Grid (Reviews / Challenges / Lists / Leaderboards)
- Permanent Discord callout

Each card on the page is part-marketing (icon, tagline, CTA) and
part-preview (3-5 items of real signal). The hub is NOT a feed-of-feeds —
the full data lives on each feature's dedicated page; the hub just teases.
See docs/features/community-hub.md for the design rationale.

Each card's data lives under its own context key so the template can
render or skip a card independently. The assembler swallows per-card
exceptions so a single broken card never breaks the whole page render —
the template falls back to the card's own empty state.
"""
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature Grid card data — each helper returns a small slice for one card
# ---------------------------------------------------------------------------

def _get_top_reviewers_spotlight(limit=3):
    """Top N reviewers by helpful votes — feeds the Reviews feature card.

    Delegates to ReviewHubService.get_top_reviewers. The Feature Spotlight
    design uses a small N (3) instead of the original aggregator's 10 so
    the card stays a teaser pointing at the dedicated Review Hub page.
    """
    from trophies.services.review_hub_service import ReviewHubService
    return ReviewHubService.get_top_reviewers(limit=limit)


def _get_active_challenges_spotlight(limit=3):
    """Recent active (incomplete) challenges across all types.

    Feeds the Challenges feature card. Uses the Challenge model directly
    rather than the Event table because the spotlight cares about
    "challenges currently being worked on" not "challenge events from the
    last few hours". Filters out completed and soft-deleted challenges.
    Ordered by most recently created.

    Returns a list of Challenge instances ordered newest-first.
    """
    from trophies.models import Challenge
    return list(
        Challenge.objects
        .filter(is_deleted=False, is_complete=False)
        .select_related('profile')
        .order_by('-created_at')[:limit]
    )


def _get_recent_lists_spotlight(limit=3):
    """Most recent published public game lists — feeds the Game Lists card.

    Mirrors BrowseListsView's queryset shape: gated to public + non-deleted
    + premium-author lists (the same gating the public lists browse page
    uses). Ordered newest-first by creation time.

    Returns a list of GameList instances with profile preloaded.
    """
    from trophies.models import GameList
    return list(
        GameList.objects
        .filter(is_public=True, is_deleted=False, profile__user_is_premium=True)
        .select_related('profile')
        .order_by('-created_at')[:limit]
    )


def _get_xp_leaderboard_spotlight(viewer_profile=None, top_n=5):
    """Top N badge XP hunters — feeds the Leaderboards feature card.

    The Feature Spotlight design caps this at 5 instead of the original
    aggregator's 25 so the card stays a teaser pointing at the dedicated
    leaderboard page. The viewer's rank is still surfaced when logged in
    so the card has personal stakes.

    Returns a dict shaped::

        {
            'entries': [list of leaderboard rows],
            'viewer_rank': int or None,
            'total_count': int,
        }

    Each row already has psn_username, avatar_url, flag, is_premium,
    displayed_title, total_xp, total_badges, rank. Adds total_xp_formatted
    and is_self for template-side display consistency with the existing
    dashboard leaderboard module.
    """
    from trophies.services.redis_leaderboard_service import (
        get_xp_top, get_xp_rank, get_xp_count,
    )

    entries = get_xp_top(top_n)
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


# ---------------------------------------------------------------------------
# Discord callout (permanent fixture, doesn't depend on data)
# ---------------------------------------------------------------------------

def _get_discord_member_count():
    """Cached Discord member count for the callout, or None if unavailable.

    The callout itself ALWAYS renders — even when this returns None — so
    it can never silently disappear. The count is purely a nice-to-have
    affordance ("join 1,234 members") that gracefully degrades to a
    plain "Join the Discord" CTA when missing.

    Currently returns None as a placeholder. A future enhancement could
    pull the count from the Discord widget API and cache it for an hour
    via Django's cache. The Discord guild ID is not exposed by any
    settings constant today, so wiring this would require either an
    env var or a hard-coded guild ID. Deferred to the gamification
    initiative which has a tighter Discord integration story.
    """
    return None


# ---------------------------------------------------------------------------
# Top-level assembler
# ---------------------------------------------------------------------------

def build_community_hub_context(viewer_profile=None):
    """Build the full template context for the Community Hub page.

    Each card's data is computed in its own try/except so a single broken
    card never breaks the whole page. The template renders each card
    independently from its own context key, so missing data falls back to
    the card's empty state.

    ``viewer_profile`` is the Profile of the requesting user, or None for
    anonymous visitors. Used to mark the viewer's row in the leaderboard
    card.

    Returns a dict with these keys:
        - top_reviewers: list of profile dicts (~3) for the Reviews card
        - active_challenges: list of Challenge instances (~3) for the Challenges card
        - recent_lists: list of GameList instances (~3) for the Game Lists card
        - xp_leaderboard: dict with entries + viewer_rank + total_count for the Leaderboards card
        - discord_member_count: int or None for the Discord callout
        - site_heartbeat: cached community stats ribbon (None if cron broken)

    The standard ``active_fundraiser`` and ``hotbar`` context keys are
    added by their respective context processors / mixins, not here.
    """
    context = {}

    try:
        context['top_reviewers'] = _get_top_reviewers_spotlight(limit=3)
    except Exception:
        logger.exception("Failed to load community hub top_reviewers")
        context['top_reviewers'] = []

    try:
        context['active_challenges'] = _get_active_challenges_spotlight(limit=3)
    except Exception:
        logger.exception("Failed to load community hub active_challenges")
        context['active_challenges'] = []

    try:
        context['recent_lists'] = _get_recent_lists_spotlight(limit=3)
    except Exception:
        logger.exception("Failed to load community hub recent_lists")
        context['recent_lists'] = []

    try:
        context['xp_leaderboard'] = _get_xp_leaderboard_spotlight(viewer_profile, top_n=5)
    except Exception:
        logger.exception("Failed to load community hub xp_leaderboard")
        context['xp_leaderboard'] = {'entries': [], 'viewer_rank': None, 'total_count': 0}

    try:
        context['discord_member_count'] = _get_discord_member_count()
    except Exception:
        logger.exception("Failed to load community hub discord_member_count")
        context['discord_member_count'] = None

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
