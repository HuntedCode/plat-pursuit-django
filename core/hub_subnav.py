"""
Hub-of-Hubs IA: sub-navigation infrastructure.

PlatPursuit's IA is structured as four hubs (Dashboard, Browse, Community,
My Pursuit). The global navbar contains direct links to each hub. A
persistent sub-navigation strip below the main navbar surfaces each hub's
sub-pages on every URL in that hub's family, URL-prefix matched.

This module defines:

1. ``HUB_SUBNAV_CONFIG`` — the four hub definitions, each with a list of
   sub-nav items and the URL prefixes that activate them.
2. ``resolve_hub_subnav(request)`` — the matcher that inspects ``request.path``
   and returns the active hub + active sub-nav slug, or ``None`` for pages
   that don't belong to any hub.

Matching strategy: longest-prefix-wins. The matcher iterates the configured
prefixes in order of length descending and returns the first match. The
Dashboard hub's bare ``/`` prefix is special-cased: it only matches when
``request.path == '/'`` exactly, so paths like ``/community/...`` correctly
match the Community hub instead of falling through to the Dashboard catchall.

Pages that don't match any hub (settings, auth flows, error pages, staff
admin pages) get ``None`` and the sub-nav strip is hidden via the template
``{% if hub_section %}`` guard.

See ``docs/architecture/ia-and-subnav.md`` for the full design rationale.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HubSubnavItem:
    """A single sub-nav item (one tab in the strip)."""
    slug: str
    label: str
    url_name: str
    icon: str | None = None
    auth_required: bool = False


@dataclass(frozen=True)
class HubSubnavConfig:
    """A hub definition: label, icon, URL prefixes, and sub-nav items."""
    key: str
    label: str
    icon: str | None
    prefixes: tuple[str, ...]
    items: tuple[HubSubnavItem, ...]


# ---------------------------------------------------------------------------
# Hub definitions
# ---------------------------------------------------------------------------
#
# Each hub's ``url_name`` references resolve to the new canonical paths
# established in the Phase 10a URL audit. Sub-nav items use URL names so
# they continue to resolve correctly across any future rename without
# touching this file.

DASHBOARD_HUB = HubSubnavConfig(
    key='dashboard',
    label='Dashboard',
    icon='layout-dashboard',
    # The bare '/' prefix is checked separately as an exact-equality match
    # in resolve_hub_subnav so it doesn't shadow other hubs. The /dashboard/
    # prefix here covers /dashboard/stats/, /dashboard/shareables/,
    # /dashboard/recap/, plus their nested children.
    prefixes=('/dashboard/',),
    items=(
        HubSubnavItem('home', 'Dashboard', 'home', 'home', auth_required=True),
        HubSubnavItem('stats', 'My Stats', 'my_stats', 'bar-chart-3', auth_required=True),
        HubSubnavItem('shareables', 'My Shareables', 'my_shareables', 'image', auth_required=True),
        HubSubnavItem('recap', 'Recap', 'recap_index', 'calendar', auth_required=True),
    ),
)


BROWSE_HUB = HubSubnavConfig(
    key='browse',
    label='Browse',
    icon='compass',
    prefixes=(
        '/games/',
        '/trophies/',
        '/companies/',
        '/franchises/',
        '/genres/',
        '/themes/',
    ),
    items=(
        HubSubnavItem('games', 'Games', 'games_list', 'gamepad-2'),
        HubSubnavItem('trophies', 'Trophies', 'trophies_list', 'trophy'),
        HubSubnavItem('recently-added', 'Recently Added', 'recently_added', 'clock'),
        HubSubnavItem('companies', 'Companies', 'companies_list', 'building'),
        HubSubnavItem('franchises', 'Franchises', 'franchises_list', 'layers'),
        HubSubnavItem('genres', 'Genres & Themes', 'genres_list', 'tag'),
        HubSubnavItem('flagged', 'Flagged Games', 'flagged_games', 'flag'),
    ),
)


COMMUNITY_HUB = HubSubnavConfig(
    key='community',
    label='Community',
    icon='users',
    prefixes=('/community/',),
    items=(
        HubSubnavItem('hub', 'Hub', 'community_hub', 'home'),
        HubSubnavItem('profiles', 'Profiles', 'profiles_list', 'user'),
        HubSubnavItem('reviews', 'Reviews', 'reviews_landing', 'message-square'),
        HubSubnavItem('challenges', 'Challenges', 'challenges_browse', 'target'),
        HubSubnavItem('lists', 'Lists', 'lists_browse', 'list'),
        HubSubnavItem('leaderboards', 'Leaderboards', 'overall_badge_leaderboards', 'bar-chart'),
    ),
)


MY_PURSUIT_HUB = HubSubnavConfig(
    key='my_pursuit',
    label='My Pursuit',
    icon='trophy',
    prefixes=('/my-pursuit/',),
    items=(
        HubSubnavItem('badges', 'Badges', 'badges_list', 'award'),
        HubSubnavItem('milestones', 'Milestones', 'milestones_list', 'flag'),
        HubSubnavItem('titles', 'Titles', 'my_titles', 'crown', auth_required=True),
    ),
)


# Order matters for matching: hubs are checked in this order. Within each
# hub, prefixes are tried longest-first. Bare '/' is handled separately as
# an exact-equality check below.
HUB_SUBNAV_CONFIG: tuple[HubSubnavConfig, ...] = (
    COMMUNITY_HUB,
    MY_PURSUIT_HUB,
    BROWSE_HUB,
    DASHBOARD_HUB,
)


# ---------------------------------------------------------------------------
# URL-name → sub-nav slug mapping
# ---------------------------------------------------------------------------
#
# When a sub-page has a different URL name than its sub-nav item (e.g. the
# badge detail page uses ``badge_detail`` but should highlight the
# ``badges`` sub-nav item), this map tells the resolver which sub-nav slug
# to mark active. Built lazily so it stays in sync with the configs above.

_URL_NAME_TO_SLUG_OVERRIDES: dict[str, tuple[str, str]] = {
    # url_name: (hub_key, item_slug)
    # Browse
    'game_detail': ('browse', 'games'),
    'game_detail_with_profile': ('browse', 'games'),
    'company_detail': ('browse', 'companies'),
    'franchise_detail': ('browse', 'franchises'),
    'genre_detail': ('browse', 'genres'),
    'theme_detail': ('browse', 'genres'),
    'roadmap_edit': ('browse', 'games'),
    # Community
    'profile_detail': ('community', 'profiles'),
    'trophy_case': ('community', 'profiles'),
    'review_hub': ('community', 'reviews'),
    'rate_my_games': ('community', 'reviews'),
    'list_detail': ('community', 'lists'),
    'list_create': ('community', 'lists'),
    'list_edit': ('community', 'lists'),
    'az_challenge_create': ('community', 'challenges'),
    'az_challenge_detail': ('community', 'challenges'),
    'az_challenge_setup': ('community', 'challenges'),
    'az_challenge_edit': ('community', 'challenges'),
    'calendar_challenge_create': ('community', 'challenges'),
    'calendar_challenge_detail': ('community', 'challenges'),
    'genre_challenge_create': ('community', 'challenges'),
    'genre_challenge_detail': ('community', 'challenges'),
    'genre_challenge_setup': ('community', 'challenges'),
    'genre_challenge_edit': ('community', 'challenges'),
    'badge_leaderboards': ('community', 'leaderboards'),
    # My Pursuit
    'badge_detail': ('my_pursuit', 'badges'),
    'badge_detail_with_profile': ('my_pursuit', 'badges'),
    # Dashboard: nested sub-pages. The shareables sub-pages all live under
    # /dashboard/shareables/* and should highlight the Shareables sub-nav
    # item; the platinum_grid wizard is one of those nested children.
    'my_shareables_platinums': ('dashboard', 'shareables'),
    'my_shareables_profile_card': ('dashboard', 'shareables'),
    'my_shareables_challenges': ('dashboard', 'shareables'),
    'platinum_grid': ('dashboard', 'shareables'),
    'recap_view': ('dashboard', 'recap'),
}


def _hub_by_key(key: str) -> HubSubnavConfig | None:
    for hub in HUB_SUBNAV_CONFIG:
        if hub.key == key:
            return hub
    return None


def resolve_hub_subnav(request) -> dict | None:
    """
    Inspect the request and return the active hub + active sub-nav slug, or
    ``None`` if the request doesn't belong to any hub.

    Returns a dict shaped::

        {
            'hub': HubSubnavConfig,
            'active_slug': 'badges',  # or None if no item is active
        }

    The matcher uses longest-prefix-wins ordering across all configured
    prefixes from all hubs. The bare ``/`` route is special-cased to match
    only when ``request.path == '/'`` exactly, so child paths under hubs
    don't fall through to the Dashboard catchall.
    """
    path = request.path

    # 1. Check for URL-name overrides first. If the resolver matched a URL
    #    name that we have an explicit override for (e.g. badge_detail), we
    #    can short-circuit the prefix walk and return immediately.
    resolver_match = getattr(request, 'resolver_match', None)
    if resolver_match is not None:
        url_name = resolver_match.url_name
        if url_name and url_name in _URL_NAME_TO_SLUG_OVERRIDES:
            hub_key, slug = _URL_NAME_TO_SLUG_OVERRIDES[url_name]
            hub = _hub_by_key(hub_key)
            if hub is not None:
                return {'hub': hub, 'active_slug': slug}

    # 2. Bare-root match for the Dashboard hub. Only the exact '/' path
    #    triggers this — '/community/...' etc. fall through.
    if path == '/':
        return {'hub': DASHBOARD_HUB, 'active_slug': 'home'}

    # 3. Longest-prefix-wins across all configured prefixes.
    best_match: tuple[HubSubnavConfig, str] | None = None
    best_length = 0
    for hub in HUB_SUBNAV_CONFIG:
        for prefix in hub.prefixes:
            if path.startswith(prefix) and len(prefix) > best_length:
                best_match = (hub, prefix)
                best_length = len(prefix)

    if best_match is None:
        return None

    hub, _ = best_match

    # 4. Determine the active sub-nav slug by matching the URL name against
    #    the hub's items. If no item matches, the strip still renders but
    #    nothing is highlighted (the page is in the hub's family but isn't
    #    one of the canonical sub-nav items).
    active_slug: str | None = None
    if resolver_match is not None and resolver_match.url_name:
        url_name = resolver_match.url_name
        for item in hub.items:
            if item.url_name == url_name:
                active_slug = item.slug
                break

    return {'hub': hub, 'active_slug': active_slug}


def visible_items(hub: HubSubnavConfig, *, is_authenticated: bool) -> tuple[HubSubnavItem, ...]:
    """
    Filter a hub's items to those visible for the current viewer.

    Items with ``auth_required=True`` are hidden from anonymous users so
    they don't see a tab that would 302-redirect them to login.
    """
    if is_authenticated:
        return hub.items
    return tuple(item for item in hub.items if not item.auth_required)
