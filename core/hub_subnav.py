"""
Hub-of-Hubs IA: sub-navigation infrastructure.

PlatPursuit's IA is a personal My Pursuit hub (rooted at the logged-in Home /)
plus Browse, Community, and Support. The global navbar links to each. A
persistent sub-navigation strip below the main navbar surfaces each hub's
sub-pages on every URL in that hub's family, URL-prefix matched (the personal
strip is auth-gated).

This module defines:

1. ``HUB_SUBNAV_CONFIG`` — the four hub definitions, each with a list of
   sub-nav items and the URL prefixes that activate them.
2. ``resolve_hub_subnav(request)`` — the matcher that inspects ``request.path``
   and returns the active hub + active sub-nav slug, or ``None`` for pages
   that don't belong to any hub.

Matching strategy: longest-prefix-wins. The matcher iterates the configured
prefixes in order of length descending and returns the first match. The bare
``/`` root is special-cased to NO hub -- it is the lobby, which sits above the
four hubs and carries no strip. It only matches when ``request.path == '/'``
exactly, so deeper paths still fall through to their own hub.

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
    group: str = ''  # the rail group this item belongs to (e.g. 'Catalog' / 'Curation'). Items are
                     # defined in group order so the template can {% regroup %} consecutive runs.


@dataclass(frozen=True)
class RenderedSubnavItem:
    """
    A sub-nav item with its URL already resolved.

    The template consumes these instead of HubSubnavItem so that dynamic
    items whose URL requires kwargs (e.g., the Profile tab, which takes the
    viewer's username) can coexist with static items that reverse from a
    url_name alone. The resolver lives in the context processor so NoReverseMatch
    failures degrade to "skip this item" rather than 500.

    ``icon`` is an optional Lucide-style icon name. The template renders
    a matching SVG inline when set; items without an icon render as
    label-only pills.

    ``group`` is the rail group label (e.g. 'Catalog'); the template groups
    consecutive same-group items under a quiet separator.
    """
    slug: str
    label: str
    url: str
    icon: str | None = None
    group: str = ''


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

# There is no Home hub. The logged-in Home (/) is the LOBBY: hub-less by design (see the exact-'/'
# branch in resolve_hub_subnav), reached by the navbar wordmark and by landing there after login.
# My Pursuit's landing is Career.


BROWSE_HUB = HubSubnavConfig(
    key='browse',
    label='Browse',
    icon='search',   # matches the navbar's Browse hub icon
    prefixes=(
        '/games/',
        '/trophies/',
        '/badges/',
        '/companies/',
        '/franchises/',
        '/genres/',
        '/themes/',
        '/engines/',
        # The public jobs catalogue. Browse rather than Leaderboards: a catalogue of jobs is a browse
        # surface, and its relationship to Career's Dossier is the Collection-vs-Browse-Badges split --
        # scope, not pagination.
        '/jobs/',
        # Both spellings while the /profiles/ -> /hunters/ 301s stand: this is a PATH PREFIX match, so a
        # visitor landing on an old profile URL would otherwise lose the Browse rail on the way through.
        '/hunters/',
        '/profiles/',
    ),
    # Grouped rail (kept consistent with the other hubs' grouped rails -- Community's Explore/Create,
    # My Pursuit's Progress/Tools): Catalog = the core browse surfaces; Curation = the cross-cutting
    # groupings. Order = group order (regroup-ready).
    items=(
        HubSubnavItem('games', 'Games', 'games_list', 'gamepad-2', group='Catalog'),
        HubSubnavItem('badges', 'Badges', 'badges_list', 'award', group='Catalog'),
        HubSubnavItem('jobs', 'Jobs', 'jobs_browse', 'briefcase', group='Catalog'),
        HubSubnavItem('recently-added', 'Recently Added', 'recently_added', 'clock', group='Catalog'),
        # Label is "Hunters" (2026-08); the SLUG stays `profiles`, matching the url names it maps to
        # below -- it is an internal key, and churning it would touch the overrides map and its tests to
        # no visible end.
        HubSubnavItem('profiles', 'Hunters', 'profiles_list', 'user', group='Catalog'),
        HubSubnavItem('franchises', 'Franchises', 'franchises_list', 'layers', group='Curation'),
        HubSubnavItem('companies', 'Companies', 'companies_list', 'building', group='Curation'),
        HubSubnavItem('genres', 'Genres & Themes', 'genres_list', 'tag', group='Curation'),
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
    icon='layers',   # matches the navbar's My Pursuit hub icon
    prefixes=(
        '/collection/', '/career/', '/milestones/', '/titles/',
        '/profile-editor/', '/shareables/', '/recap/', '/rate-my-games/',
    ),
    # Grouped rail: Progress = the gamification progression surfaces (Career merges the old Lab +
    # Research Panel); Tools = personal outputs. Profile is appended to Tools as a dynamic extra.
    items=(
        HubSubnavItem('career', 'Career', 'career', 'briefcase', auth_required=True, group='Progress'),
        HubSubnavItem('collection', 'Collection', 'badge_collection', 'award', auth_required=True, group='Progress'),
        HubSubnavItem('milestones', 'Milestones', 'milestones_list', 'flag', group='Progress'),
        HubSubnavItem('titles', 'Titles', 'my_titles', 'crown', auth_required=True, group='Progress'),
        HubSubnavItem('shareables', 'Plat Cards', 'my_shareables', 'image', auth_required=True, group='Tools'),
        HubSubnavItem('recap', 'Recap', 'recap_index', 'calendar', auth_required=True, group='Tools'),
        HubSubnavItem('rate_my_games', 'Rate My Games', 'rate_my_games', 'star', auth_required=True, group='Tools'),
    ),
)


# The Support hub: the always-on badge-art fundraiser + (coming) the membership store. It's
# landing-focused, so it carries NO sub-nav items -- the /support/ page features the fundraiser +
# store instead. The /fundraiser/ prefix maps the campaign page here too. ("Support" is a
# placeholder name, room for PlatPursuit charm.)
# The Leaderboards hub. It carried NO items until 2026-08, on the argument that a rail would be a single
# pill naming the page you were already on -- that comment invited items "the moment a second kind lands",
# and the rebuild landed three.
#
# Global Boards is the hub LANDING and its first pill, which is the standard shape: the strip's job is to
# say what else is here, and the page you are on being marked is how you know where you are.
LEADERBOARDS_HUB = HubSubnavConfig(
    key='leaderboards',
    label='Leaderboards',
    icon='bar-chart',
    prefixes=('/leaderboards/',),
    items=(
        HubSubnavItem('global', 'Global Boards', 'overall_badge_leaderboards', 'bar-chart'),
        HubSubnavItem('games', 'Game Boards', 'game_boards', 'gamepad-2'),
        HubSubnavItem('badges', 'Badge Boards', 'badge_boards', 'award'),
        HubSubnavItem('jobs', 'Job Boards', 'job_boards', 'briefcase'),
    ),
)


SUPPORT_HUB = HubSubnavConfig(
    key='support',
    label='Support',
    icon='heart',
    prefixes=('/support/', '/fundraiser/'),
    items=(),
)


# Order matters for matching: hubs are checked in this order. Within each
# hub, prefixes are tried longest-first. Bare '/' is handled separately as
# an exact-equality check below.
HUB_SUBNAV_CONFIG: tuple[HubSubnavConfig, ...] = (
    MY_PURSUIT_HUB,
    BROWSE_HUB,
    LEADERBOARDS_HUB,
    SUPPORT_HUB,
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
    'roadmap_edit': ('browse', 'games'),
    # Community
    'profile_detail': ('browse', 'profiles'),
    'trophy_case': ('browse', 'profiles'),
    # Reviews archived 2026-05 and the Community hub retired 2026-08, so the notice page has no hub
    # to sit in -- it renders without a sub-nav strip, which is right for a tombstone.
    # (badge_detail now highlights the Browse > Badges tab -- see the Browse block above.)
    # My Pursuit: nested sub-pages of the moved items. Shareables is plat-cards-only as of 2026-08,
    # so its one nested child is the cards browse; profile_card + platinum_grid are retired and their
    # URLs bounce to the landing (no override needed for a redirect).
    'my_shareables_platinums': ('my_pursuit', 'shareables'),
    'recap_view': ('my_pursuit', 'recap'),
    'rate_my_games': ('my_pursuit', 'rate_my_games'),
    # (The fundraiser + fundraiser_success pages resolve to the Support hub via its /fundraiser/
    # prefix -- no override needed. Support has no sub-nav items, so no active slug.)
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
    only when ``request.path == '/'`` exactly (the personal hub's Overview), so
    child paths under other hubs don't fall through to it.
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

    # 2. Bare root: the LOBBY. It belongs to no hub -- it sits ABOVE the four of them, which is why it
    #    carries no sub-nav strip: on a lobby the CTAs are the navigation, and a hub rail underneath them
    #    would be a second, competing set of directions. Returning None here (rather than a hub) is what
    #    makes the strip disappear, via the template's `{% if hub_section %}` guard. The navbar wordmark
    #    is its only chrome affordance, and it highlights off `hub_section is None` + the '/' path.
    if path == '/':
        return None

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
            slug=item.slug, label=item.label, url=url, icon=item.icon, group=item.group))
    rendered.extend(extras)
    return tuple(rendered)
