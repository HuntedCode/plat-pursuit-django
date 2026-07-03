"""
Hub-of-Hubs IA: sub-navigation infrastructure.

PlatPursuit's IA is structured around the Home root plus three hubs (Browse,
Community, My Pursuit). The global navbar contains direct links to each. A
persistent sub-navigation strip below the main navbar surfaces each hub's
sub-pages on every URL in that hub's family, URL-prefix matched.

This module defines:

1. ``HUB_SUBNAV_CONFIG`` — the four hub definitions, each with a list of
   sub-nav items and the URL prefixes that activate them.
2. ``resolve_hub_subnav(request)`` — the matcher that inspects ``request.path``
   and returns the active hub + active sub-nav slug, or ``None`` for pages
   that don't belong to any hub.

Matching strategy: longest-prefix-wins. The matcher iterates the configured
prefixes in order of length descending and returns the first match. The bare
``/`` root is special-cased to the items-less Home hub: it only matches when
``request.path == '/'`` exactly, so paths like ``/community/...`` correctly
match their own hub instead of falling through to a root catchall.

Pages that don't match any hub (settings, auth flows, error pages, staff
admin pages) get ``None`` and the sub-nav strip is hidden via the template
``{% if hub_section %}`` guard.

See ``docs/architecture/ia-and-subnav.md`` for the full design rationale.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse


@dataclass(frozen=True)
class HubSubnavItem:
    """A single sub-nav item (one tab in the strip)."""
    slug: str
    label: str
    url_name: str
    icon: str | None = None
    auth_required: bool = False
    divider_before: bool = False  # render a group divider before this item (e.g. tools vs core)


@dataclass(frozen=True)
class RenderedSubnavItem:
    """
    A sub-nav item with its URL already resolved.

    The template consumes these instead of HubSubnavItem so that dynamic
    items whose URL requires kwargs (e.g., the Fundraiser tab, which takes
    a slug) can coexist with static items that reverse from a url_name
    alone. The resolver lives in the context processor so NoReverseMatch
    failures degrade to "skip this item" rather than 500.

    ``icon`` is an optional Lucide-style icon name. The template renders
    a matching SVG inline when set; items without an icon render as
    label-only pills.
    """
    slug: str
    label: str
    url: str
    icon: str | None = None
    divider_before: bool = False


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

# The Home hub was merged into MY_PURSUIT_HUB in the personal-hub unify: the logged-in Home (/)
# is now the personal hub's Overview tab (see MY_PURSUIT_HUB + the exact-'/' branch in
# resolve_hub_subnav). No standalone HOME_HUB remains.


BROWSE_HUB = HubSubnavConfig(
    key='browse',
    label='Browse',
    icon='compass',
    prefixes=(
        '/games/',
        '/trophies/',
        '/badges/',
        '/companies/',
        '/franchises/',
        '/genres/',
        '/themes/',
        '/engines/',
    ),
    items=(
        HubSubnavItem('games', 'Games', 'games_list', 'gamepad-2'),
        HubSubnavItem('trophies', 'Trophies', 'trophies_list', 'trophy'),
        HubSubnavItem('badges', 'Badges', 'badges_list', 'award'),
        HubSubnavItem('recently-added', 'Recently Added', 'recently_added', 'clock'),
        HubSubnavItem('flagged', 'Flagged Games', 'flagged_games', 'flag'),
        HubSubnavItem('franchises', 'Franchises', 'franchises_list', 'layers'),
        HubSubnavItem('genres', 'Genres & Themes', 'genres_list', 'tag'),
        HubSubnavItem('companies', 'Companies', 'companies_list', 'building'),
        HubSubnavItem('engines', 'Engines', 'engines_list', 'cpu'),
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
        HubSubnavItem('rate_my_games', 'Rate My Games', 'rate_my_games', 'star'),
        HubSubnavItem('challenges', 'Challenges', 'challenges_browse', 'target'),
        HubSubnavItem('lists', 'Lists', 'lists_browse', 'list'),
        HubSubnavItem('leaderboards', 'Leaderboards', 'overall_badge_leaderboards', 'bar-chart'),
    ),
)


# The personal hub is rooted at the logged-in Home (/): the Overview tab IS the Home, and the
# other personal surfaces now live at ROOT paths (moved from /my-pursuit/* and /dashboard/* in
# the unify). Profile is appended dynamically by the context processor (its URL needs the viewer's
# own username). The strip renders for AUTHENTICATED viewers only (the context processor gates it)
# -- anon sees a hero Home with no strip. Grouped: a gamification core (6) + personal tools.
MY_PURSUIT_HUB = HubSubnavConfig(
    key='my_pursuit',
    label='My Pursuit',
    icon='trophy',
    prefixes=(
        '/collection/', '/lab/', '/research-panel/', '/milestones/', '/titles/',
        '/profile-editor/', '/stats/', '/shareables/', '/recap/',
    ),
    items=(
        # Core: the gamification progression surfaces.
        HubSubnavItem('overview', 'Overview', 'home', 'home'),
        HubSubnavItem('collection', 'Collection', 'badge_collection', 'award', auth_required=True),
        HubSubnavItem('lab', 'The Lab', 'lab', 'flask', auth_required=True),
        HubSubnavItem('research-panel', 'Research Panel', 'research_panel', 'beaker'),
        HubSubnavItem('milestones', 'Milestones', 'milestones_list', 'flag'),
        HubSubnavItem('titles', 'Titles', 'my_titles', 'crown', auth_required=True),
        # Tools/outputs (divider before). Profile is appended after these as a dynamic extra.
        HubSubnavItem('stats', 'My Stats', 'my_stats', 'bar-chart-3', auth_required=True, divider_before=True),
        HubSubnavItem('shareables', 'My Shareables', 'my_shareables', 'image', auth_required=True),
        HubSubnavItem('recap', 'Recap', 'recap_index', 'calendar', auth_required=True),
    ),
)


# Order matters for matching: hubs are checked in this order. Within each
# hub, prefixes are tried longest-first. Bare '/' is handled separately as
# an exact-equality check below.
HUB_SUBNAV_CONFIG: tuple[HubSubnavConfig, ...] = (
    COMMUNITY_HUB,
    MY_PURSUIT_HUB,
    BROWSE_HUB,
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
    'badge_detail': ('browse', 'badges'),
    'badge_detail_with_profile': ('browse', 'badges'),
    'genre_detail': ('browse', 'genres'),
    'theme_detail': ('browse', 'genres'),
    'engine_detail': ('browse', 'engines'),
    'roadmap_edit': ('browse', 'games'),
    # Community
    'profile_detail': ('community', 'profiles'),
    'trophy_case': ('community', 'profiles'),
    # Reviews archived 2026-05; the notice page highlights the Community hub
    # root. Rate My Games is its own sub-nav item now that it lives at
    # /community/rate-my-games/.
    'reviews_landing': ('community', 'hub'),
    'review_hub': ('community', 'hub'),
    'rate_my_games': ('community', 'rate_my_games'),
    'list_detail': ('community', 'lists'),
    'list_create': ('community', 'lists'),
    'list_edit': ('community', 'lists'),
    'my_challenges': ('community', 'challenges'),
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
    # (badge_detail now highlights the Browse > Badges tab -- see the Browse block above.)
    # My Pursuit: nested sub-pages of the moved items. The shareables sub-pages
    # all live under /dashboard/shareables/* and should highlight the Shareables
    # sub-nav item; the platinum_grid wizard is one of those nested children.
    'my_shareables_platinums': ('my_pursuit', 'shareables'),
    'my_shareables_profile_card': ('my_pursuit', 'shareables'),
    'my_shareables_challenges': ('my_pursuit', 'shareables'),
    'platinum_grid': ('my_pursuit', 'shareables'),
    'recap_view': ('my_pursuit', 'recap'),
    # Fundraiser: lives at /fundraiser/<slug>/ but conceptually belongs to the
    # My Pursuit hub while a campaign is active. The context processor appends
    # the Fundraiser tab dynamically when one is live.
    'fundraiser': ('my_pursuit', 'fundraiser'),
    'fundraiser_success': ('my_pursuit', 'fundraiser'),
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

    # 2. Bare-root match: the logged-in Home (/) is the personal hub's Overview. Only the exact
    #    '/' path triggers this — '/community/...' etc. fall through. Anon gets a hero with no
    #    strip; the context processor gates the personal strip to authenticated viewers.
    if path == '/':
        return {'hub': MY_PURSUIT_HUB, 'active_slug': 'overview'}

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


def build_rendered_items(
    hub: HubSubnavConfig,
    *,
    is_authenticated: bool,
    extras: tuple[RenderedSubnavItem, ...] = (),
) -> tuple[RenderedSubnavItem, ...]:
    """
    Return the hub's sub-nav items resolved into ``RenderedSubnavItem``s
    for the current viewer, with any dynamic ``extras`` appended.

    - ``auth_required`` items are dropped for anonymous viewers.
    - URLs are resolved via ``reverse(item.url_name)``. If an item's URL
      name can't be reversed (stale config, URL rename), it's skipped
      rather than crashing the whole request.
    - ``extras`` are appended at the end of the strip and are passed
      through unchanged (caller is responsible for URL resolution since
      extras may need kwargs, e.g. the Fundraiser tab).
    """
    rendered: list[RenderedSubnavItem] = []
    for item in hub.items:
        if item.auth_required and not is_authenticated:
            continue
        try:
            url = reverse(item.url_name)
        except NoReverseMatch:
            continue
        rendered.append(RenderedSubnavItem(
            slug=item.slug, label=item.label, url=url, divider_before=item.divider_before))
    rendered.extend(extras)
    return tuple(rendered)
