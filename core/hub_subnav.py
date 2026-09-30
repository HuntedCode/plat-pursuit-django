"""
Hub-of-Hubs IA: sub-navigation infrastructure.

PlatPursuit's IA is a personal My Pursuit hub (rooted at the logged-in Home /)
plus Browse, Leaderboards, Community and Support Us. The global navbar links to
each. A persistent sub-navigation strip below the main navbar surfaces each
hub's sub-pages on every URL in that hub's family, URL-prefix matched (the
personal strip is auth-gated).

This module defines:

1. ``HUB_SUBNAV_CONFIG`` — the five hub definitions, each with a list of
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
    membership_required: bool = False  # dropped unless the viewer holds a premium tier (the
                                       # cheap `user.premium_tier` truthiness the navbar's own
                                       # Membership link already gates on -- never is_premium(),
                                       # which costs provider queries on every request)
    group: str = ''  # the rail group this item belongs to (e.g. 'Catalog' / 'Curation'). Items are
                     # defined in group order so the template can {% regroup %} consecutive runs.
    #: How the tag is SPOKEN, when it is not simply the tag word. The pill's `aria-label` replaces
    #: its contents-derived name, so this is the whole of what a screen reader hears about the chip.
    #: It exists because that phrase was hardcoded to "coming soon" and fired on `{% if item.tag %}`
    #: rather than on the tag's VALUE -- so a `tag='New'` pill, the other value the field documents,
    #: would have announced "Game Lists, coming soon" while sighted readers saw NEW. Backwards, not
    #: merely wrong. Left empty, the tag word itself is spoken.
    tag_aria: str = ''
    tag: str = ''    # a short chip on the pill itself ('Soon', 'New'). SHORT, because a rail pill is
                     # `white-space: nowrap` and a long one pushes its neighbours into the overflow
                     # sheet -- the tag has to cost less room than the item it is labelling.


@dataclass(frozen=True)
class RenderedSubnavItem:
    """
    A sub-nav item with its URL already resolved.

    The template consumes these instead of HubSubnavItem so that dynamic
    items whose URL requires kwargs (e.g., the Profile tab, which takes the
    viewer's username) can coexist with static items that reverse from a
    url_name alone. The resolver lives in the context processor so NoReverseMatch
    failures degrade to "skip this item" rather than 500.

    ``icon`` is an optional Lucide-style icon name, and NOTHING RENDERS IT (verified 2026-09).
    The sub-nav template draws only the HUB's icon, through ``_hub_subnav_icon.html``, which
    handles four hub keys and has no else branch; ``item.icon`` appears in no template on the
    site. Every item renders as a label-only pill whatever this says. Kept because the values
    read as intent, but do not expect a glyph, and do not treat two items sharing one as a bug.

    ``group`` is the rail group label (e.g. 'Catalog'); the template groups
    consecutive same-group items under a quiet separator.

    ``tag`` is a short chip drawn on the pill itself ('Soon'), carried over from the
    HubSubnavItem. It has to be repeated here rather than read off the config: the
    template only ever sees these, which is exactly why the first cut rendered nothing
    -- the `{% if item.tag %}` was true of the config object the template never gets.

    ``tag_kind`` picks the chip's LOOK: '' is the default amber 'Soon' chip, 'count' the nav's
    primary number, 'xp' its accent lozenge. It exists because the two attention marks added in
    2026-09 -- a claimable-contract count on Career, unclaimed challenge XP on My Challenges --
    are not labels like 'Soon'. They are the same signals the parent My Pursuit nav item carries,
    and the nav tells its three markers apart by shape and colour; a sub-nav drawing them as amber
    'Soon' chips would contradict that one level down, and on this site amber means "not ready yet".
    """
    slug: str
    label: str
    url: str
    icon: str | None = None
    group: str = ''
    tag: str = ''
    tag_aria: str = ''
    tag_kind: str = ''


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
        # `/hunters/` and `/profiles/` moved to COMMUNITY_HUB in 2026-09.
    ),
    # Grouped rail (kept consistent with the other hubs' grouped rails -- Community's Explore/Create,
    # My Pursuit's Progress/Tools): Catalog = the core browse surfaces; Curation = the cross-cutting
    # groupings. Order = group order (regroup-ready).
    items=(
        HubSubnavItem('games', 'Games', 'games_list', 'gamepad-2', group='Catalog'),
        # SLUG deliberately not bare 'lists' (the gated GameList system's guard pins that);
        # display copy "Trophy Lists" per the IA doc's naming insurance.
        HubSubnavItem('trophy-lists', 'Trophy Lists', 'trophy_lists', 'list', group='Catalog'),
        HubSubnavItem('badges', 'Badges', 'badges_list', 'award', group='Catalog'),
        HubSubnavItem('jobs', 'Jobs', 'jobs_browse', 'briefcase', group='Catalog'),
        HubSubnavItem('recently-added', 'Recently Added', 'recently_added', 'clock', group='Catalog'),
        # Label is "Hunters" (2026-08); the SLUG stays `profiles`, matching the url names it maps to
        # below -- it is an internal key, and churning it would touch the overrides map and its tests to
        # no visible end.
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
        # `/my-lists/` (2026-09). The hub is resolved by PATH PREFIX, so a rail item whose URL sits
        # outside its own hub's prefixes drops you out of the hub the moment you click it -- the
        # rail vanishes, or worse, another hub's lights up. `test_nav_reachability` caught this the
        # moment My Lists joined Tools. The URL is deliberately NOT `/career/lists/` or similar: it
        # was frozen when the rebuilt pages shipped, and a hub is a nav grouping rather than a URL
        # namespace -- `/recap/` and `/collection/` are here on the same footing.
        '/my-lists/',
        # `/my-challenges/` (2026-09), on exactly the same footing and for exactly the same reason.
        '/my-challenges/',
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
        # MY LISTS IS PERSONAL, so it sits here rather than in Community with the public browse.
        # The private side of a public system is still personal and login-gated -- exactly as
        # *Collection* stays in My Pursuit while *Badges* sits in Browse. A hub is a mode, not a
        # feature's address. (The un-hide checklist originally put it in Community; the IA
        # decision that followed put it here, and that decision wins.)
        HubSubnavItem('my_lists', 'My Lists', 'my_lists', 'list', auth_required=True, group='Tools'),
        # MY CHALLENGES, beside My Lists and for the identical argument: the private side of a public
        # system is still personal. The public browse and Hall of Fame keep their Community slot.
        HubSubnavItem('my_challenges', 'My Challenges', 'my_challenges', 'flag',
                      auth_required=True, group='Tools'),
    ),
)


# The Leaderboards hub. NO items, which is where it started and where it has returned to: a rail would be
# a single pill naming the page you are already on.
#
# It briefly carried four. The original comment here invited items "the moment a second kind lands", and
# the rebuild landed three at once -- Game Boards, Badge Boards and Job Boards. They were removed in
# 2026-08 because each was a catalogue of entities that `/games/`, `/badges/` and `/jobs/` already
# catalogue, differing only by a sort those pages already had. Nothing linked to them except this rail,
# which existed because they did; the justification was circular, and it collapsed the moment either half
# was examined. Boards live on the thing they rank, so the full board is on game, badge and job detail.
#
# The Support hub ran in this same items=() shape until 2026-08 and then grew a rail -- the
# difference is not taste but destination count: Leaderboards collapsed to ONE page, Support grew
# to FOUR real ones. The removal reasoning above still holds here exactly because a single pill
# naming the page you are on is not navigation.
LEADERBOARDS_HUB = HubSubnavConfig(
    key='leaderboards',
    label='Leaderboards',
    icon='bar-chart',
    prefixes=('/leaderboards/',),
    items=(),
)


# The Support hub: four real destinations as of 2026-08 (storefront, roadmap, Badge Art, My
# Membership), which is what turned the rail on -- the reversal of the Leaderboards removal is
# principled, see the comment above. The /fundraiser/ prefix keeps the campaign slug pages in
# this hub; their active-item highlighting rides the overrides map below. My Membership is
# membership_required (premium_tier truthiness, the navbar link's own gate) in its 'Yours'
# group -- a non-member's door is the storefront -- except when it IS the active page, which
# always names itself.
# Relabelled "Support Us" in 2026-09: "Support" alone reads as a help desk on most of the web, and
# this is the one place a confused reader would look for one. Naming the ask is also the more earnest
# form, which is the site's voice. NOTE the rail still reads "Support Us -> Support" because the first
# ITEM keeps its name -- that one is an open naming question (Tiers? Ways to Help?) and is not being
# decided by a rename that was about the hub.
SUPPORT_HUB = HubSubnavConfig(
    key='support',
    label='Support Us',
    icon='heart',
    prefixes=('/support/', '/fundraiser/'),
    items=(
        HubSubnavItem('support', 'Support', 'support_hub', 'heart', group='The project'),
        HubSubnavItem('roadmap', 'Roadmap', 'support_roadmap', 'map', group='The project'),
        HubSubnavItem('fundraiser', 'Badge Art', 'support_fundraiser', 'palette', group='The project'),
        HubSubnavItem('membership', 'My Membership', 'subscription_management', 'star',
                      auth_required=True, membership_required=True, group='Yours'),
    ),
)


# The Community hub, returned 2026-09 after being retired in 2026-08. It exists because the four
# other hubs sort by the reader's INTENT (find / rank / mine / support) and miss a second axis: who
# AUTHORED the thing. Everything in Browse and Leaderboards is site-owned -- PSN data, IGDB metadata,
# PlatPursuit-authored badges and jobs -- and user-generated content is a different class, which the
# codebase already says by carrying an `all_ugc` restriction scope over comments, reviews, ratings and
# lists. See docs/architecture/ia-and-subnav.md.
#
# THE RAIL TURNED ON in 2026-09, when Game Lists came off its development gate and gave the hub a
# second destination. Until then it ran `items=()` on the reasoning that emptied the Leaderboards
# rail -- a single pill naming the page you are already on is not navigation -- and that reasoning
# expired the moment there were two places to go rather than one.
#
# My Lists is NOT here. It is personal and login-gated, so it sits in My Pursuit -> Tools; the public
# browse is what belongs to the community. Challenges and the Hall of Fame join this rail next.
#
# NO LANDING PAGE, and it does not need one. `/community/` 301s permanently to `/leaderboards/` (live
# since 2026-08 and therefore cached in browsers indefinitely, so it cannot be repointed) -- but a hub
# here is a nav grouping, not an address, and every page in this one is its own destination. The
# navbar button points at Hunters until there is a second item.
COMMUNITY_HUB = HubSubnavConfig(
    key='community',
    label='Community',
    icon='users',
    # Both spellings while the /profiles/ -> /hunters/ 301s stand: this is a PATH PREFIX match, so a
    # visitor landing on an old profile URL would otherwise lose the rail on the way through.
    # `/community/` is here for the Lists and Challenges pages served under it.
    prefixes=('/community/', '/hunters/', '/profiles/'),
    items=(
        HubSubnavItem('lists', 'Game Lists', 'lists_browse', 'list'),
        HubSubnavItem('profiles', 'Hunters', 'profiles_list', 'user'),
        # A COMING-SOON PAGE EARNS A RAIL ITEM, which is not obvious. It is here because the rail is
        # how somebody learns what this hub contains, and a hub of two while a third is weeks away
        # reads as the whole offering. The page it points at is real and says so plainly -- the rule
        # set when Challenges was parked was a page, never a redirect. It keeps its slug and url_name
        # when the real browse replaces it.
        #
        # LAST, AND TAGGED. Last because the two things you can actually use should not sit behind
        # the one you cannot; tagged because a pill that looks like its neighbours promises a
        # destination like its neighbours, and somebody clicking it deserves to know before they do.
        # Dropping the tag is what marks the feature as shipped.
        HubSubnavItem('challenges', 'Challenges', 'challenges', 'flag', tag='Soon',
                      tag_aria='coming soon'),
    ),
)


# Order matters for matching: hubs are checked in this order. Within each
# hub, prefixes are tried longest-first. Bare '/' is handled separately as
# an exact-equality check below.
HUB_SUBNAV_CONFIG: tuple[HubSubnavConfig, ...] = (
    MY_PURSUIT_HUB,
    BROWSE_HUB,
    LEADERBOARDS_HUB,
    COMMUNITY_HUB,
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
    # Browse. The Games/Trophy Lists IA split the highlighting with the pages: the CONCEPT Game
    # page lights Games, while a LIST detail page (and the list-scoped roadmap editor reached
    # from it) lights Trophy Lists -- each detail page points at the catalogue that browses it.
    'game_detail': ('browse', 'trophy-lists'),
    'game_detail_with_profile': ('browse', 'trophy-lists'),
    # The concept Game page + its unmatched-concept fallback (Games/Trophy Lists IA). Without
    # these lines the rail renders unlit on the new pages -- the documented job_detail failure.
    'game_page': ('browse', 'games'),
    'game_page_concept': ('browse', 'games'),
    'company_detail': ('browse', 'companies'),
    'franchise_detail': ('browse', 'franchises'),
    'badge_detail': ('browse', 'badges'),
    'badge_detail_with_profile': ('browse', 'badges'),
    'genre_detail': ('browse', 'genres'),
    'theme_detail': ('browse', 'genres'),
    # Added when Jobs joined the Catalog rail (2026-08) -- and missed at the time, so browsing a job left
    # the whole strip unhighlighted. A detail page's URL name never matches its sub-nav item's
    # (`job_detail` vs `jobs_browse`), so every one of them needs a line here; the item shipping without
    # one is silent, because the strip still renders.
    'job_detail': ('browse', 'jobs'),
    # The whole roadmap family is /games/<np>/-scoped (you reach every one FROM a list), so all
    # four light with the list family. The _ctg editor and BOTH public reader routes had no line
    # at all before -- the silent-unlit trap, on sitemap-indexed pages for the readers.
    'roadmap_edit': ('browse', 'trophy-lists'),
    'roadmap_edit_ctg': ('browse', 'trophy-lists'),
    'roadmap_detail': ('browse', 'trophy-lists'),
    'roadmap_detail_dlc': ('browse', 'trophy-lists'),
    # Community. A detail page's URL name never matches its rail item's (`list_detail` vs
    # `lists_browse`), and an item shipping without a line here is SILENT -- the strip still renders,
    # just with nothing lit. That is the `job_detail` failure documented above, and it is why these
    # three exist rather than being left to the prefix match.
    'list_detail': ('community', 'lists'),
    # My Challenges' write endpoints. They redirect to the page, so a reader rarely sees a rail
    # rendered under these names -- but an item shipping without a line here is SILENT, and a future
    # error path that re-renders rather than redirecting would inherit an unlit strip.
    # The run's own page sits under `/community/challenges/`, so the PREFIX match lands it in the
    # Community hub -- which is right: it is the public artefact, not the hunter's working page. It
    # needs a line anyway, because that hub's `challenges` item is the placeholder browse and the
    # prefix alone would leave the strip unlit.
    'challenge_detail': ('community', 'challenges'),
    # The picker's doors are fetch-only, so no strip ever renders for them -- but an item
    # without a line here is silently unhighlighted, and a future non-JSON fallback would
    # inherit the gap rather than announce it.
    'challenge_slot': ('my_pursuit', 'my_challenges'),
    'challenge_search': ('my_pursuit', 'my_challenges'),
    'challenge_assign': ('my_pursuit', 'my_challenges'),
    'challenge_clear': ('my_pursuit', 'my_challenges'),
    'challenge_start': ('my_pursuit', 'my_challenges'),
    'challenge_hide': ('my_pursuit', 'my_challenges'),
    # The reward doors. They serve the PUBLIC run page, but they live under `/my-challenges/` because only
    # an owner may call them -- and this map is keyed on the route, so they belong with their siblings here.
    'challenge_redeem': ('my_pursuit', 'my_challenges'),
    'challenge_redeem_all': ('my_pursuit', 'my_challenges'),
    'profile_detail': ('community', 'profiles'),
    'trophy_case': ('community', 'profiles'),
    # Reviews archived 2026-05. The notice page matches the COMMUNITY hub by prefix (2026-09) --
    # which is where it would have lived -- and still renders no sub-nav strip, because that hub is
    # empty today. If the rail is ever populated, the tombstone gains one; see
    # `test_the_retired_community_paths_render_no_strip`, which asserts the RENDERED page rather than
    # the config so it keeps meaning this after that happens.
    # (badge_detail now highlights the Browse > Badges tab -- see the Browse block above.)
    # My Pursuit: nested sub-pages of the moved items. Shareables is plat-cards-only as of 2026-08,
    # so its one nested child is the cards browse; profile_card + platinum_grid are retired and their
    # URLs bounce to the landing (no override needed for a redirect).
    'my_shareables_platinums': ('my_pursuit', 'shareables'),
    'recap_view': ('my_pursuit', 'recap'),
    'rate_my_games': ('my_pursuit', 'rate_my_games'),
    # Support: the slugged campaign pages carry a different URL name than the rail item
    # (`fundraiser` vs the landing's `support_fundraiser`), so without these lines the strip
    # renders with nothing lit -- the exact job_detail failure documented above.
    'fundraiser': ('support', 'fundraiser'),
    'fundraiser_success': ('support', 'fundraiser'),
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
    is_member: bool = False,
    active_slug: str | None = None,
    extras: tuple[RenderedSubnavItem, ...] = (),
    tags: dict[str, tuple[str, str, str]] | None = None,
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
    - ``tags`` OVERRIDES a config item's chip, as ``{slug: (text, kind, aria)}``. Attention
      marks are per-viewer and per-request (a claimable count, unclaimed XP), so they cannot
      live in ``HUB_SUBNAV_CONFIG`` the way 'Soon' does -- and they are an override rather
      than an ``extras`` entry because they attach to an item that already exists. An empty
      or missing text leaves the config's own chip ENTIRELY alone -- text, kind and aria together.
      That last part is a fix: `tag_aria` used to fall back independently of `tag`, so
      ``{'challenges': ('', '', 'now available')}`` left the chip reading 'Soon' while a screen
      reader heard "now available". The chip's three parts are one atomic override, which is the
      same backwards-announcement hazard ``HubSubnavItem.tag_aria``'s own comment records.
    """
    rendered: list[RenderedSubnavItem] = []
    for item in hub.items:
        # The page you are ON always names itself: a gated item never drops while active, or
        # the rail renders with no current pill and the mobile trigger reads bare (e.g. a
        # non-member landing on /support/membership/, which serves them a real state).
        is_active = active_slug is not None and item.slug == active_slug
        if item.auth_required and not is_authenticated and not is_active:
            continue
        if item.membership_required and not is_member and not is_active:
            continue
        try:
            url = reverse(item.url_name)
        except NoReverseMatch:
            continue
        tag, tag_kind, tag_aria = (tags or {}).get(item.slug) or ('', '', '')
        # ALL THREE OR NONE. An override with no TEXT is not an override -- taking its `aria` or its `kind`
        # anyway would let a chip read 'Soon' while announcing something else, or wear a mark's colour with a
        # label's word.
        override = bool(tag)
        rendered.append(RenderedSubnavItem(
            slug=item.slug, label=item.label, url=url, icon=item.icon, group=item.group,
            tag=tag if override else item.tag,
            tag_aria=tag_aria if override else item.tag_aria,
            tag_kind=tag_kind if override else ''))
    rendered.extend(extras)
    return tuple(rendered)
