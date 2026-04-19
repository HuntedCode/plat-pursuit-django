"""
Shared logic for grouping Games by IGDB ID on detail pages (Franchise, Company,
and any future page that presents a concept's outputs grouped by game rather
than listed per PSN entry).

The detail-page UX anchors around "the game", not "the PSN trophy list". Resident
Evil 4 Remake on PS4 and PS5 appear as two Game rows (two PSN trophy lists) but
represent the same game; users want to see them stacked as versions of one
entry. IGDBMatch.igdb_id is the key: games whose concepts share an IGDB id are
the same game. Games without an IGDB match each become their own single-entry
group so they're never dropped from the list.

The returned group dicts are shaped for the shared
``templates/trophies/partials/franchise_detail/game_groups_list.html`` partial,
which handles version-row rendering (progress rings, trophy counts, flag
badges, platform/region chips, etc.).
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Iterable, Mapping

from django.db.models import F, OuterRef, Q, Subquery

from trophies.models import Game, ProfileGame


def fetch_user_progress_map(profile, games):
    """Return ``{game_id: ProfileGame}`` for the viewer's progress on the given
    games, or an empty dict when ``profile`` is falsy or ``games`` is empty.

    Callers should pass ``profile = request.user.profile if authenticated else None``.
    """
    if not profile or not games:
        return {}
    game_ids = [g.id for g in games]
    return {
        pg.game_id: pg
        for pg in ProfileGame.objects.filter(profile=profile, game_id__in=game_ids)
    }




def build_igdb_groups(
    games: Iterable,
    *,
    user_progress_map: Mapping | None = None,
    extra_per_group: Mapping | None = None,
):
    """Group games by IGDBMatch.igdb_id and compute per-group stats.

    Args:
        games: An iterable of Game objects. Must be materialised (not a
            queryset) because this function iterates multiple times when
            attaching ``user_pg`` and later computing per-group stats.
        user_progress_map: Optional ``{game_id: ProfileGame}`` mapping. When
            provided, each Game gets a ``game.user_pg`` attribute set, and
            each group's ``user_*`` stat fields reflect the viewer's progress.
            When omitted or empty, user stat fields are zeroed / False.
        extra_per_group: Optional ``{concept_id: dict}`` mapping; each dict is
            merged into the group the concept lands in. Used by FranchiseDetailView
            to attach the concept's ``is_main`` flag so the view can partition
            groups into "main" vs "also featured" after grouping runs. Other
            callers (Company) can omit it.

    Returns:
        list[dict]: One group dict per IGDB id (order of first encounter),
        followed by single-entry groups for games without an IGDB match.
        Each dict has:
            igdb_id:              int | None
            display_name:         str   (IGDB name preferred over PSN title)
            cover_url:            str
            release_date:         datetime | None
            games:                list[Game]
            total_trophies:       int
            has_platinum:         bool
            user_any_progress:    bool
            user_earned_trophies: int
            user_plat_earned:     bool
            user_best_progress:   int   (highest progress % across versions)
        Plus any keys from ``extra_per_group[concept_id]``.
    """
    user_progress_map = user_progress_map or {}
    extra_per_group = extra_per_group or {}

    igdb_groups: OrderedDict = OrderedDict()
    ungrouped: list[dict] = []

    for game in games:
        # Attach user progress so templates can access it via game.user_pg
        # without a per-row dict lookup.
        game.user_pg = user_progress_map.get(game.id)

        igdb_match = getattr(game.concept, 'igdb_match', None) if game.concept else None
        extras = extra_per_group.get(game.concept_id, {}) if game.concept_id else {}

        if igdb_match and igdb_match.igdb_id:
            igdb_id = igdb_match.igdb_id
            if igdb_id not in igdb_groups:
                igdb_groups[igdb_id] = {
                    'igdb_id': igdb_id,
                    'display_name': igdb_match.igdb_name or game.title_name,
                    'cover_url': game.display_image_url,
                    'release_date': igdb_match.igdb_first_release_date,
                    'games': [],
                    **extras,
                }
            igdb_groups[igdb_id]['games'].append(game)
        else:
            ungrouped.append({
                'igdb_id': None,
                'display_name': game.title_name,
                'cover_url': game.display_image_url,
                'release_date': None,
                'games': [game],
                **extras,
            })

    all_groups = list(igdb_groups.values()) + ungrouped

    # Per-group rollup stats. Single pass over each group's games.
    for group in all_groups:
        group_trophies = 0
        group_has_platinum = False
        group_user_earned = 0
        group_user_plat = False
        group_user_any = False
        group_best_pct = 0
        for game in group['games']:
            dt = game.defined_trophies or {}
            group_trophies += sum(dt.get(k, 0) for k in ('bronze', 'silver', 'gold', 'platinum'))
            if dt.get('platinum', 0) > 0:
                group_has_platinum = True
            pg = getattr(game, 'user_pg', None)
            if pg:
                group_user_any = True
                group_user_earned += pg.earned_trophies_count or 0
                if pg.has_plat:
                    group_user_plat = True
                if pg.progress and pg.progress > group_best_pct:
                    group_best_pct = pg.progress
        group['total_trophies'] = group_trophies
        group['has_platinum'] = group_has_platinum
        group['user_any_progress'] = group_user_any
        group['user_earned_trophies'] = group_user_earned
        group['user_plat_earned'] = group_user_plat
        group['user_best_progress'] = group_best_pct

    return all_groups


# Sort keys shared between Franchise and Company detail pages so both views
# offer identical sort options with identical ordering semantics.
SORT_CHOICES = [
    ('release', 'Release Date (Oldest First)'),
    ('release_desc', 'Release Date (Newest First)'),
    ('alpha', 'Alphabetical (A-Z)'),
    ('alpha_desc', 'Alphabetical (Z-A)'),
    ('versions', 'Most Versions'),
    ('trophies', 'Most Trophies'),
]


def sort_groups(groups, sort_val):
    """Sort a list of group dicts (from ``build_igdb_groups``) by the given key.

    Groups with no release_date sort to the end on ascending sorts and to the
    start on descending, using signed infinity sentinels.
    """
    no_date_asc = float('inf')
    no_date_desc = float('-inf')

    def release_key_asc(g):
        return g['release_date'].timestamp() if g['release_date'] else no_date_asc

    def release_key_desc(g):
        return g['release_date'].timestamp() if g['release_date'] else no_date_desc

    if sort_val == 'release_desc':
        return sorted(
            groups,
            key=lambda g: (release_key_desc(g), g['display_name'].lower()),
            reverse=True,
        )
    if sort_val == 'alpha':
        return sorted(groups, key=lambda g: g['display_name'].lower())
    if sort_val == 'alpha_desc':
        return sorted(groups, key=lambda g: g['display_name'].lower(), reverse=True)
    if sort_val == 'versions':
        return sorted(groups, key=lambda g: (-len(g['games']), g['display_name'].lower()))
    if sort_val == 'trophies':
        return sorted(groups, key=lambda g: (-g['total_trophies'], g['display_name'].lower()))
    # Default: release (oldest first)
    return sorted(groups, key=lambda g: (release_key_asc(g), g['display_name'].lower()))


def pick_hero_cover(groups):
    """Return the cover URL of the most-recently-released group with art,
    or empty string if none of the groups have any cover.
    """
    for group in sorted(
        groups,
        key=lambda g: (
            g['release_date'].timestamp() if g['release_date'] else float('-inf')
        ),
        reverse=True,
    ):
        if group['cover_url']:
            return group['cover_url']
    return ''


# ---------------------------------------------------------------------------
# Representative cover-art subquery factories
# ---------------------------------------------------------------------------
#
# Browse cards on /franchises/ and /companies/ show cover art of the featured
# entity's most-recently-released game. We can't just pick a random concept —
# we want the newest release, and we want three fallback tiers so the card is
# never empty when data is incomplete:
#   1. Game.title_image           (PSN store art — the cleanest image)
#   2. IGDBMatch.igdb_cover_image_id  (built into a URL in the template)
#   3. Game.title_icon_url        (generic PS icon — always present for most games)
#
# These factories return Subquery objects suitable for .annotate() on any
# model that has a path to Game via a through-table (ConceptFranchise,
# ConceptCompany, etc.). The caller specifies the path as a kwarg so one
# implementation serves both pages.

_MOST_RECENT_RELEASE_ORDER = [
    F('concept__igdb_match__igdb_first_release_date').desc(nulls_last=True),
    'title_name',
]


def _base_game_qs(*, outer_ref_pk: str, through_path: str, extra_filter: Q | None = None):
    """Base Game queryset for cover-art subqueries.

    Args:
        outer_ref_pk: Name of the primary-key field on the outer model.
            Typically ``'pk'``.
        through_path: Dotted path from Game to the outer model's row, ending
            at the FK field that matches OuterRef. Examples:
              - ``'concept__concept_franchises__franchise'``  (Franchise page)
              - ``'concept__concept_companies__company'``     (Company page)
        extra_filter: Optional extra Q() to narrow the games further. Used by
            FranchiseListView to restrict to is_main=True links for franchise
            rows while allowing any link for collections.
    """
    qs = Game.objects.filter(**{through_path: OuterRef(outer_ref_pk)})
    if extra_filter is not None:
        qs = qs.filter(extra_filter)
    return qs


def representative_title_image_subquery(
    *, outer_ref_pk: str = 'pk', through_path: str, extra_filter: Q | None = None,
):
    """Tier 1: most-recent game's PSN ``title_image`` (store art)."""
    return Subquery(
        _base_game_qs(
            outer_ref_pk=outer_ref_pk,
            through_path=through_path,
            extra_filter=extra_filter,
        )
        .exclude(title_image__isnull=True)
        .exclude(title_image='')
        .order_by(*_MOST_RECENT_RELEASE_ORDER)
        .values('title_image')[:1]
    )


def representative_concept_icon_subquery(
    *, outer_ref_pk: str = 'pk', through_path: str, extra_filter: Q | None = None,
):
    """Tier 2: most-recent game's ``concept.concept_icon_url`` (PSN MASTER
    portrait cover art). Populated at sync time for modern titles."""
    return Subquery(
        _base_game_qs(
            outer_ref_pk=outer_ref_pk,
            through_path=through_path,
            extra_filter=extra_filter,
        )
        .filter(concept__isnull=False)
        .exclude(concept__concept_icon_url__isnull=True)
        .exclude(concept__concept_icon_url='')
        .order_by(*_MOST_RECENT_RELEASE_ORDER)
        .values('concept__concept_icon_url')[:1]
    )


def representative_igdb_cover_id_subquery(
    *, outer_ref_pk: str = 'pk', through_path: str, extra_filter: Q | None = None,
):
    """Tier 3: most-recent game's ``igdb_cover_image_id`` (converted to a URL
    in the template)."""
    return Subquery(
        _base_game_qs(
            outer_ref_pk=outer_ref_pk,
            through_path=through_path,
            extra_filter=extra_filter,
        )
        .filter(concept__igdb_match__isnull=False)
        .exclude(concept__igdb_match__igdb_cover_image_id='')
        .order_by(*_MOST_RECENT_RELEASE_ORDER)
        .values('concept__igdb_match__igdb_cover_image_id')[:1]
    )


def representative_title_icon_subquery(
    *, outer_ref_pk: str = 'pk', through_path: str, extra_filter: Q | None = None,
):
    """Tier 4: most-recent game's ``title_icon_url`` (generic PS icon)."""
    return Subquery(
        _base_game_qs(
            outer_ref_pk=outer_ref_pk,
            through_path=through_path,
            extra_filter=extra_filter,
        )
        .exclude(title_icon_url__isnull=True)
        .exclude(title_icon_url='')
        .order_by(*_MOST_RECENT_RELEASE_ORDER)
        .values('title_icon_url')[:1]
    )


def compute_user_progress_stats(
    groups,
    total_trophies: int,
    user_progress_map: Mapping,
    *,
    profile,
):
    """Derive the "Your Progress" stat dict used in the detail page hero.

    Args:
        groups: The groups that should be counted toward these totals (callers
            decide whether to pass all groups or a filtered subset — e.g. the
            FranchiseDetailView passes only main_groups).
        total_trophies: Pre-computed total across ``groups``; used as the
            completion-percentage denominator.
        user_progress_map: The ``{game_id: ProfileGame}`` map; trophies_earned
            is summed from it, scoped to games that appear in ``groups``.
        profile: The viewer's Profile (truthy gate — returns None when falsy).

    Returns:
        dict | None: The stat dict, or None when ``profile`` is falsy.
    """
    if not profile:
        return None

    games_played = sum(1 for g in groups if g['user_any_progress'])
    games_platinumed = sum(1 for g in groups if g['user_plat_earned'])

    # Scope user progress to games that are actually in the provided groups.
    in_scope_ids = {game.id for group in groups for game in group['games']}
    scoped_progress = {
        gid: pg for gid, pg in user_progress_map.items() if gid in in_scope_ids
    }
    trophies_earned = sum(pg.earned_trophies_count or 0 for pg in scoped_progress.values())
    completion_pct = round((trophies_earned / total_trophies) * 100) if total_trophies else 0

    return {
        'games_played': games_played,
        'games_platinumed': games_platinumed,
        'versions_played': len(scoped_progress),
        'trophies_earned': trophies_earned,
        'completion_pct': completion_pct,
    }
