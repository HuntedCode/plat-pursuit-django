"""Community Hub page-data assembler.

Builds the context dict for ``core/views.py:CommunityHubView``. The hub is
a fixed-layout **Feature Spotlight** page (NOT an aggregator) composed of:

- Hero (built_for_hunters site heartbeat ribbon)
- Conditional active fundraiser banner
- 2x2 Feature Grid (Reviews / Game Lists / Challenges / Leaderboards)
- Permanent Discord callout

Each Feature Grid card is split into two halves divided by a horizontal
rule:

- **Top half (community pulse)**: a small slice of fresh community
  signal — 5 most recently reviewed titles, 5 most recent public lists,
  5 most recently active challenges, top 5 badge XP. The lists are
  always padded to ``SPOTLIGHT_LIMIT`` rows so cards stay the same
  height regardless of how much data exists; missing rows render as
  greyed-out placeholders. This is mostly a dev-machine affordance —
  on prod the lists fill naturally — but it also keeps fresh installs
  and edge cases from looking broken.
- **Bottom half (personal hook)**: viewer-specific stats that connect
  the community pulse to "what does this mean for me." For
  authenticated viewers it's compact stat tiles or a rank-with-neighbors
  strip; for anonymous viewers it's a single sign-in CTA, and for
  authenticated viewers without a linked PSN it's a link-PSN CTA.

The hub is NOT a feed-of-feeds — the full data lives on each feature's
dedicated page; the hub just teases. The personal halves don't violate
this principle: they answer "how does the community pulse relate to me"
which is a *different question* from anything the dashboard surfaces in
isolation. See docs/features/community-hub.md for the design rationale.

Each card's data lives under its own context key so the template can
render or skip a card independently. The assembler swallows per-card
exceptions so a single broken card never breaks the whole page render —
the template falls back to the card's own empty state.
"""
import logging

logger = logging.getLogger(__name__)


# All four cards' top halves use the same slot count so the cards stay
# visually balanced. The personal half also uses 5 for the leaderboards
# card (rank + 2 above + 2 below) for the same reason. Each helper pads
# its return value to ``SPOTLIGHT_LIMIT`` rows by appending None entries
# via ``_pad_to_limit``, so the template can iterate the padded list and
# branch on ``{% if entry %}`` to render either a live row or a greyed-
# out placeholder. This pushes the visual rigor up to the data layer
# instead of asking the template to compute a missing-row count itself.
SPOTLIGHT_LIMIT = 5


def _pad_to_limit(items, limit=SPOTLIGHT_LIMIT):
    """Right-pad ``items`` with ``None`` entries up to ``limit`` rows.

    Used by every Community Hub spotlight helper so cards stay visually
    balanced regardless of how much real data exists. The template
    iterates the padded list and falls through to a placeholder row when
    it hits a None entry. On a populated production database the padding
    is a no-op (lists already meet the limit); on dev / fresh installs
    the padding gives the cards their full height so layout work isn't
    obscured by partial data.
    """
    items = list(items)
    if len(items) >= limit:
        return items[:limit]
    return items + [None] * (limit - len(items))


# ---------------------------------------------------------------------------
# Feature Grid card data — top halves (community pulse)
# ---------------------------------------------------------------------------

def _get_recently_reviewed_titles_spotlight(limit=SPOTLIGHT_LIMIT):
    """Most recently reviewed titles (deduped by concept) — Reviews card top.

    Surfaces the N concepts that received the most recent review activity,
    one row per concept, with the recommendation percentage as the at-a-
    glance score. Title-grouped output dedupes reviews by concept so three
    different people reviewing Elden Ring don't take all 5 slots.

    Returns a list of dicts: unified_title, slug, concept_icon_url,
    review_count, recommended_count, recommendation_pct, latest_review_at.
    """
    from django.db.models import Count, Max, Q
    from trophies.models import Concept

    concepts = list(
        Concept.objects
        .annotate(
            latest_review_at=Max(
                'reviews__created_at',
                filter=Q(reviews__is_deleted=False),
            ),
            review_count=Count(
                'reviews',
                filter=Q(reviews__is_deleted=False),
            ),
            recommended_count=Count(
                'reviews',
                filter=Q(
                    reviews__is_deleted=False,
                    reviews__recommended=True,
                ),
            ),
        )
        .filter(latest_review_at__isnull=False)
        .order_by('-latest_review_at')[:limit]
        .values(
            'unified_title', 'slug', 'concept_icon_url',
            'review_count', 'recommended_count', 'latest_review_at',
        )
    )

    # Reuse the same percentage math ReviewHubService.get_most_reviewed_games
    # uses so the score on the card matches the score on the Review Hub.
    result = []
    for c in concepts:
        total = c['review_count']
        pct = round(c['recommended_count'] / total * 100) if total else 0
        result.append({
            'unified_title': c['unified_title'],
            'slug': c['slug'],
            'concept_icon_url': c['concept_icon_url'] or '',
            'review_count': total,
            'recommendation_pct': pct,
            'latest_review_at': c['latest_review_at'],
        })
    return _pad_to_limit(result, limit)


def _get_active_challenges_spotlight(limit=SPOTLIGHT_LIMIT):
    """Recent active (incomplete) challenges across all types.

    Feeds the Challenges feature card top half. Filters out completed and
    soft-deleted challenges. Ordered by most recently created.

    Returns a list of Challenge instances ordered newest-first, padded
    to ``limit`` slots with None entries for placeholder rendering.
    """
    from trophies.models import Challenge
    rows = list(
        Challenge.objects
        .filter(is_deleted=False, is_complete=False)
        .select_related('profile')
        .order_by('-created_at')[:limit]
    )
    return _pad_to_limit(rows, limit)


def _get_recent_lists_spotlight(limit=SPOTLIGHT_LIMIT):
    """Most recent published public game lists — Game Lists card top half.

    Mirrors BrowseListsView's queryset shape: gated to public + non-deleted
    + premium-author lists (the same gating the public lists browse page
    uses). Ordered newest-first by creation time.

    Returns a list of GameList instances with profile preloaded, padded
    to ``limit`` slots with None entries for placeholder rendering.
    """
    from trophies.models import GameList
    rows = list(
        GameList.objects
        .filter(is_public=True, is_deleted=False, profile__user_is_premium=True)
        .select_related('profile')
        .order_by('-created_at')[:limit]
    )
    return _pad_to_limit(rows, limit)


def _get_xp_leaderboard_spotlight(viewer_profile=None, top_n=SPOTLIGHT_LIMIT):
    """Top N badge XP hunters — Leaderboards card top half.

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
        'entries': _pad_to_limit(entries, top_n),
        'viewer_rank': viewer_rank,
        'total_count': get_xp_count(),
    }


# ---------------------------------------------------------------------------
# Feature Grid card data — bottom halves (personal hook)
# ---------------------------------------------------------------------------
#
# Each card's bottom half answers a different question per card type:
#
#  Reviews    -> "how do MY reviews compare?" (count + helpful + recommend %)
#  Lists      -> "what's MY list footprint?" (lists + public + total games)
#  Challenges -> "where am I in each challenge type?" (per-type latest)
#  Leaderboards -> "where do I sit on the global ranks?" (rank + neighbors)
#
# All four return None for anonymous viewers OR viewers without a linked
# PSN profile — the template branches on the None to render the
# "sign in / link PSN" CTA. Returning None instead of an empty dict makes
# the template's branching unambiguous.

def _get_personal_review_stats(viewer_profile):
    """Personal review stats for the Reviews card bottom half.

    Returns a dict with: review_count, helpful_received, recommend_pct.
    Returns None for anonymous viewers / no-profile viewers.
    """
    if viewer_profile is None:
        return None

    from django.db.models import Count, Q, Sum
    from trophies.models import Review

    agg = Review.objects.filter(
        profile=viewer_profile,
        is_deleted=False,
    ).aggregate(
        review_count=Count('id'),
        helpful_received=Sum('helpful_count'),
        recommended_count=Count('id', filter=Q(recommended=True)),
    )

    review_count = agg['review_count'] or 0
    helpful_received = agg['helpful_received'] or 0
    recommended_count = agg['recommended_count'] or 0
    recommend_pct = (
        round(recommended_count / review_count * 100) if review_count else 0
    )

    return {
        'review_count': review_count,
        'helpful_received': helpful_received,
        'recommend_pct': recommend_pct,
    }


def _get_personal_list_stats(viewer_profile):
    """Personal game-list stats for the Game Lists card bottom half.

    Returns a dict with: total_lists, public_lists, total_games.
    Returns None for anonymous viewers / no-profile viewers. ``total_games``
    is the SUM of every list's denormalized game_count, not a distinct-game
    count, so a game appearing in two of the user's lists counts twice
    (matches how the user thinks about "how many games are across my
    lists").
    """
    if viewer_profile is None:
        return None

    from django.db.models import Count, Sum, Q
    from trophies.models import GameList

    agg = GameList.objects.filter(
        profile=viewer_profile,
        is_deleted=False,
    ).aggregate(
        total_lists=Count('id'),
        public_lists=Count('id', filter=Q(is_public=True)),
        total_games=Sum('game_count'),
    )

    return {
        'total_lists': agg['total_lists'] or 0,
        'public_lists': agg['public_lists'] or 0,
        'total_games': agg['total_games'] or 0,
    }


def _get_personal_challenge_slots(viewer_profile):
    """Per-type latest challenge rows for the Challenges card bottom half.

    Returns a list of 3 dicts, one per challenge type, in canonical
    display order (A-Z, Calendar, Genre). Each dict has:

        type_key:    'az' | 'calendar' | 'genre'
        type_label:  short uppercase label for the row badge
        challenge:   most-recent Challenge instance for that type, or None
                     if the viewer hasn't started one of that type
        detail_url_name: the URL name for the type's detail page (so the
                     template doesn't have to hardcode a mapping)

    Returning rows + ``challenge=None`` instead of just a dict keyed by
    type lets the template iterate one structure and pick a live or
    placeholder render per row, without doing per-type if/else branches.

    Returns None for anonymous viewers / no-profile viewers (which the
    template branches on to render the link-PSN CTA).
    """
    if viewer_profile is None:
        return None

    from trophies.models import Challenge

    # Canonical row layout. Row order is the type order users see on the
    # challenges browse page.
    type_rows = [
        {'type_key': 'az',       'type_label': 'A-Z',      'detail_url_name': 'az_challenge_detail',       'challenge': None},
        {'type_key': 'calendar', 'type_label': 'Calendar', 'detail_url_name': 'calendar_challenge_detail', 'challenge': None},
        {'type_key': 'genre',    'type_label': 'Genre',    'detail_url_name': 'genre_challenge_detail',    'challenge': None},
    ]
    type_index = {row['type_key']: row for row in type_rows}

    # One query gets every non-deleted challenge ordered newest-first; we
    # take the first hit per type and stop. Cheaper than three separate
    # queries on the (profile, is_deleted, challenge_type) index.
    remaining = set(type_index.keys())
    for ch in (
        Challenge.objects
        .filter(profile=viewer_profile, is_deleted=False)
        .order_by('-created_at')
    ):
        if ch.challenge_type in remaining:
            type_index[ch.challenge_type]['challenge'] = ch
            remaining.discard(ch.challenge_type)
            if not remaining:
                break

    return type_rows


def _get_personal_xp_neighborhood(viewer_profile):
    """Rank + 2 above + 2 below on the badge XP leaderboard.

    Returns a list of leaderboard entries (same shape as the top section)
    with the viewer's row marked ``is_self=True``. Returns None for
    anonymous viewers / no-profile viewers, OR an empty list when the
    viewer is on the leaderboard but Redis returned no neighbors (which
    shouldn't happen in practice — the viewer would always be in their
    own neighborhood — but the template still handles it).
    """
    if viewer_profile is None:
        return None

    from trophies.services.redis_leaderboard_service import get_xp_neighborhood

    entries = get_xp_neighborhood(viewer_profile.id, above=2, below=2)
    if not entries:
        return _pad_to_limit([], SPOTLIGHT_LIMIT)

    viewer_username = viewer_profile.psn_username
    for e in entries:
        e['total_xp_formatted'] = f"{e.get('total_xp', 0):,}"
        e['is_self'] = e.get('psn_username') == viewer_username
    # Always pad to SPOTLIGHT_LIMIT so the personal-half row count matches
    # the top half. Viewers near the top or bottom of the leaderboard get
    # asymmetric neighborhoods (e.g. rank 1 has no rows above), and the
    # placeholder padding keeps the card visually balanced.
    return _pad_to_limit(entries, SPOTLIGHT_LIMIT)


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
    anonymous visitors AND for authenticated users without a linked PSN
    profile. The personal-section helpers all branch on None to return
    None themselves so the template can render the sign-in / link-PSN CTA.

    Returns a dict with these keys (each card's top + personal data, plus
    the page-wide pieces):

        Top halves:
            - recently_reviewed_titles
            - recent_lists
            - active_challenges
            - xp_leaderboard
        Personal halves (None when viewer_profile is None):
            - personal_review_stats
            - personal_list_stats
            - personal_challenge_slots
            - personal_xp_neighborhood
        Page-wide:
            - discord_member_count
            - site_heartbeat
            - spotlight_limit (so the template can render placeholder loops)

    The standard ``active_fundraiser`` and ``hotbar`` context keys are
    added by their respective context processors / mixins, not here.
    """
    context = {'spotlight_limit': SPOTLIGHT_LIMIT}

    # ----- Top halves -----

    try:
        context['recently_reviewed_titles'] = _get_recently_reviewed_titles_spotlight()
    except Exception:
        logger.exception("Failed to load community hub recently_reviewed_titles")
        context['recently_reviewed_titles'] = []

    try:
        context['recent_lists'] = _get_recent_lists_spotlight()
    except Exception:
        logger.exception("Failed to load community hub recent_lists")
        context['recent_lists'] = []

    try:
        context['active_challenges'] = _get_active_challenges_spotlight()
    except Exception:
        logger.exception("Failed to load community hub active_challenges")
        context['active_challenges'] = []

    try:
        context['xp_leaderboard'] = _get_xp_leaderboard_spotlight(viewer_profile)
    except Exception:
        logger.exception("Failed to load community hub xp_leaderboard")
        context['xp_leaderboard'] = {'entries': [], 'viewer_rank': None, 'total_count': 0}

    # ----- Personal halves -----
    # Each helper returns None for anonymous / no-profile viewers; the
    # template branches on the None to render the sign-in / link-PSN CTA.

    try:
        context['personal_review_stats'] = _get_personal_review_stats(viewer_profile)
    except Exception:
        logger.exception("Failed to load community hub personal_review_stats")
        context['personal_review_stats'] = None

    try:
        context['personal_list_stats'] = _get_personal_list_stats(viewer_profile)
    except Exception:
        logger.exception("Failed to load community hub personal_list_stats")
        context['personal_list_stats'] = None

    try:
        context['personal_challenge_slots'] = _get_personal_challenge_slots(viewer_profile)
    except Exception:
        logger.exception("Failed to load community hub personal_challenge_slots")
        context['personal_challenge_slots'] = None

    try:
        context['personal_xp_neighborhood'] = _get_personal_xp_neighborhood(viewer_profile)
    except Exception:
        logger.exception("Failed to load community hub personal_xp_neighborhood")
        context['personal_xp_neighborhood'] = None

    # ----- Page-wide -----

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
