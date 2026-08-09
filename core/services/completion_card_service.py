"""Data layer for plat cards -- the share card a hunter gets for finishing a game.

**What earns a card:** the game's DEFAULT trophy group at 100%. Not "has a platinum" -- that was the
old rule, and it silently excluded every 100%-with-no-platinum game, a whole class of real achievement.
The default-group rule is a strict superset (a platinum lives in the default group, so plat implies the
group is done), which is why ONE query yields both card variants:

    platinum -- the game defines a platinum, so completing the group means they popped it
    full     -- the game defines no platinum; the group is simply finished

**Why ProfileTrophyGroup:** it already stores exactly this, per (profile, trophy group), refreshed on
every sync by `PSNApiService.update_profilegame_stats` and seeded by `backfill_profile_trophy_groups`.
`progress` is floored, so it reads 100 iff every trophy in the group is earned. The new badge system
uses the same read for its BASE bar (`badge_orchestrator.evaluate_with_catalog`). So eligibility here is
a read, never a live aggregate over EarnedTrophy -- which is what keeps it whale-safe.

**The staleness guard** mirrors the badge engine's invariant: whole-game 100% implies the base list is
done, so a PTG row that hasn't caught up must not hide a completion the hunter can plainly see. See
`eligible_completions`.

Cards are keyed on the game's default `TrophyGroup`, not on the ProfileTrophyGroup row: TrophyGroup ids
are stable, the PTG row is a denorm that a backfill may legitimately delete and rewrite, and keying on
the group means the "is this yours" question is answered by the eligibility predicate rather than by
which row happened to exist when the link was made.
"""
import logging

from django.db.models import Q

from trophies.models import (
    BadgeSeries, EarnedTrophy, ProfileGame, ProfileTrophyGroup, SeriesBadgeStanding, Stage,
    TrophyGroup, UserConceptRating, UserTitle,
)

logger = logging.getLogger(__name__)

PLATINUM = 'platinum'
FULL = 'full'

VARIANT_LABELS = {
    PLATINUM: 'Platinum Card',
    FULL: '100% Card',
}

#: How many badge series a card will ever name. The card renders at embed size (~450px wide in a
#: Discord preview), so this is a layout constant, not a data limit.
BADGE_LINE_CAP = 2


# ── Eligibility ───────────────────────────────────────────────────────────────────────────────────

def eligible_completions(profile):
    """Every completion this profile can make a card from, as a real queryset (so the browse page can
    filter, sort and paginate it in the DB).

    The base read is the profile's own default-group standings -- an index seek on the
    (profile, trophy_group) unique index, bounded by games played, never by trophies earned.

    The OR arm is the staleness guard. `ProfileGame.progress` is the whole-game percentage, so 100 there
    means every trophy including DLC, which necessarily includes the base list. If a PTG row lags behind
    a sync, the hunter would otherwise look at a 100% game on their profile and find no card for it. The
    subquery is over the same profile's ProfileGames, so it stays bounded too.

    (A *missing* PTG row is not covered and does not need to be: rows are written for every group a
    profile has trophy data for, so no row at all means no trophy data for that group.)
    """
    completed_games = ProfileGame.objects.filter(profile=profile, progress=100).values('game_id')
    return (
        ProfileTrophyGroup.objects
        .filter(profile=profile, trophy_group__trophy_group_id='default')
        .filter(Q(progress=100) | Q(trophy_group__game_id__in=completed_games))
        .select_related(
            'trophy_group',
            'trophy_group__game',
            'trophy_group__game__concept',
            'trophy_group__game__concept__igdb_match',
        )
        # raw_response is the ~30 KB IGDB blob no card or row ever reads; joining it in was the
        # trigger for the May 2026 web-server OOM.
        .defer('trophy_group__game__concept__igdb_match__raw_response')
    )


def resolve_variant(game):
    """`platinum` when the game defines a platinum, else `full`.

    Reads the GAME's defined_trophies rather than the group's: the platinum always lives in the default
    group so the two agree, but the game-level field is the one reliably populated across the catalogue
    (it is what the profile game filters key on too).
    """
    defined = game.defined_trophies or {}
    try:
        return PLATINUM if int(defined.get('platinum') or 0) > 0 else FULL
    except (TypeError, ValueError):
        return FULL


def variant_filter(qs, variant):
    """Narrow an `eligible_completions` queryset to one variant, in the DB."""
    has_plat = Q(trophy_group__game__defined_trophies__platinum__gt=0)
    if variant == PLATINUM:
        return qs.filter(has_plat)
    if variant == FULL:
        return qs.exclude(has_plat)
    return qs


def get_completion(profile, trophy_group_id):
    """The profile's standing on `trophy_group_id`, or None if they haven't earned a card for it.

    This is the ownership check every card endpoint runs: it answers "is this completion yours" with
    the same predicate that built the list, so a deep link can never show a card the browse page won't.
    """
    return eligible_completions(profile).filter(trophy_group_id=trophy_group_id).first()


def completion_for_earned_trophy(profile, earned_trophy_id):
    """Resolve a platinum EarnedTrophy id to its completion.

    Kept because platinum-earned notifications already in the wild deep-link by EarnedTrophy id, and
    because the pre-2026-08 share endpoints were keyed that way for external/mobile consumers.
    """
    game_id = (
        EarnedTrophy.objects
        .filter(id=earned_trophy_id, profile=profile, earned=True, trophy__trophy_type='platinum')
        .values_list('trophy__game_id', flat=True)
        .first()
    )
    if not game_id:
        return None
    group_id = (
        TrophyGroup.objects
        .filter(game_id=game_id, trophy_group_id='default')
        .values_list('id', flat=True)
        .first()
    )
    return get_completion(profile, group_id) if group_id else None


# ── Ordinals ──────────────────────────────────────────────────────────────────────────────────────

def _platinum_ordinal(profile, earned_trophy):
    """"Platinum #N" at the time of earning.

    Deliberately unchanged from the original implementation: the ordinal is a coupled pair with the
    listing sort (`earned_date_time DESC NULLS LAST, -id`), and the two silently desync if either side
    is rewritten. NULL-date platinums (PSN occasionally returns no timestamp) sort to the END of the
    timeline and therefore take the LOWEST ordinals.
    """
    plats = EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='platinum')
    earned_date = earned_trophy.earned_date_time
    if not earned_date:
        return plats.filter(earned_date_time__isnull=True, id__lte=earned_trophy.id).count()
    return plats.filter(
        Q(earned_date_time__isnull=True)
        | Q(earned_date_time__lt=earned_date)
        | Q(earned_date_time=earned_date, id__lte=earned_trophy.id)
    ).count()


def _full_ordinal(profile, standing):
    """"100% #N" -- the full-completion variant counts its OWN ladder.

    A single shared ladder across both variants would have renumbered every platinum card already
    shared, so the two counts stay independent. Ties break by trophy_group_id for a total order, the
    same reason the leaderboard indexes carry a unique final key.
    """
    completed_at = standing.last_trophy_at
    qs = variant_filter(eligible_completions(profile), FULL)
    if not completed_at:
        return qs.filter(last_trophy_at__isnull=True, trophy_group_id__lte=standing.trophy_group_id).count()
    return qs.filter(
        Q(last_trophy_at__isnull=True)
        | Q(last_trophy_at__lt=completed_at)
        | Q(last_trophy_at=completed_at, trophy_group_id__lte=standing.trophy_group_id)
    ).count()


# ── Badge series + title ──────────────────────────────────────────────────────────────────────────

def _badge_lines(profile, concept):
    """The live badge series this game belongs to, and the title each one grants.

    Reads the NEW grouping-badge system (BadgeSeries / SeriesBadgeStanding / UserTitle
    `source_type='badge_series'`) -- the same read-models Collection and the Titles page use. The card
    used to read the legacy Badge/UserBadgeProgress tier-1 rows, which retire at the badge cutover.

    Ordered so the strongest claim leads: a title the hunter actually HOLDS first, then whichever
    series they are furthest along in.
    """
    if not concept:
        return []

    live_slugs = BadgeSeries.objects.filter(group_badges__is_live=True).values_list('series_slug', flat=True)
    slugs = set(
        Stage.objects
        .filter(concepts=concept, series_slug__in=live_slugs)
        .exclude(series_slug__isnull=True).exclude(series_slug='')
        # .order_by() strips Stage.Meta.ordering, which would otherwise ride the SELECT and defeat
        # .distinct() (a concept in two same-series stages -> duplicate rows).
        .values_list('series_slug', flat=True).order_by().distinct()
    )
    if not slugs:
        return []

    series_list = list(BadgeSeries.objects.filter(series_slug__in=slugs).select_related('title'))
    standings = {
        s.series_slug: s
        for s in SeriesBadgeStanding.objects.filter(profile=profile, series_slug__in=slugs)
    }
    title_ids = [s.title_id for s in series_list if s.title_id]
    held_titles = set(
        UserTitle.objects
        .filter(profile=profile, title_id__in=title_ids, source_type='badge_series')
        .values_list('title_id', flat=True)
    ) if title_ids else set()

    lines = []
    for series in series_list:
        standing = standings.get(series.series_slug)
        progress_bp = standing.progress_bp if standing else 0
        lines.append({
            'series_name': series.display_series or series.name,
            'series_slug': series.series_slug,
            'title': series.title.name if series.title_id else '',
            'title_held': series.title_id in held_titles,
            'progress_pct': round(progress_bp / 100),
            'stages_cleared': standing.stages_cleared if standing else 0,
            'stages_total': standing.stages_total if standing else 0,
        })

    lines.sort(key=lambda l: (not l['title_held'], -l['progress_pct'], l['series_name'].lower()))
    return lines[:BADGE_LINE_CAP]


def _displayed_title(profile):
    """The title the hunter is currently wearing, or ''."""
    return (
        UserTitle.objects
        .filter(profile=profile, is_displayed=True)
        .values_list('title__name', flat=True)
        .first()
    ) or ''


def _user_rating(profile, concept):
    """The hunter's own base-game rating, or None. `concept_trophy_group=NULL` is the base-game
    convention shared with RatingService."""
    if not concept:
        return None
    rating = UserConceptRating.objects.filter(
        profile=profile, concept=concept, concept_trophy_group__isnull=True,
    ).first()
    if not rating:
        return None
    return {
        'overall_rating': rating.overall_rating,
        'difficulty': rating.difficulty,
        'grindiness': rating.grindiness,
        'fun_ranking': rating.fun_ranking,
        'hours_to_platinum': rating.hours_to_platinum,
    }


# ── The card payload ──────────────────────────────────────────────────────────────────────────────

RARITY_LABELS = {0: 'Ultra Rare', 1: 'Very Rare', 2: 'Rare', 3: 'Common'}


def get_card_data(profile, standing):
    """Everything the card template needs, for either variant.

    `standing` is a row from `eligible_completions` (so ownership is already established).
    """
    group = standing.trophy_group
    game = group.game
    concept = game.concept
    variant = resolve_variant(game)

    profile_game = ProfileGame.objects.filter(profile=profile, game=game).first()

    # The platinum variant leads on the trophy itself; the full variant has no anchor trophy, so its
    # hero is the completion.
    platinum = None
    if variant == PLATINUM:
        platinum = (
            EarnedTrophy.objects
            .filter(profile=profile, trophy__game=game, trophy__trophy_type='platinum', earned=True)
            .select_related('trophy')
            .first()
        )

    if platinum:
        ordinal, ordinal_label = _platinum_ordinal(profile, platinum), 'Platinum'
        completed_at = platinum.earned_date_time
    else:
        # A game that defines a platinum but has no earned row is a data anomaly, not a variant: fall
        # back to the full presentation rather than rendering a platinum card with an empty hero.
        variant = FULL
        ordinal, ordinal_label = _full_ordinal(profile, standing), '100%'
        completed_at = standing.last_trophy_at

    earned_counts = standing.earned_trophies or {}
    group_defined = group.defined_trophies or {}

    return {
        'variant': variant,
        'variant_label': VARIANT_LABELS[variant],
        'ordinal': ordinal,
        'ordinal_label': ordinal_label,

        'username': profile.display_psn_username or profile.psn_username,
        'user_avatar_url': profile.avatar_url or '',
        'is_plus': getattr(profile, 'is_plus', False),
        # The title they're WEARING, not one this game granted -- the card's identity strip is the
        # hunter, and the worn title is how they present themselves everywhere else on the site.
        'display_title': _displayed_title(profile),

        'game_name': game.title_name,
        'game_id': game.id,
        'np_communication_id': game.np_communication_id,
        'concept_id': concept.id if concept else None,
        'trophy_group_id': group.id,
        'game_image': game.display_image_url_large,
        'landscape_url': (concept.get_landscape_url() or '') if concept else '',
        'title_platform': game.title_platform,
        'region': game.region,
        'is_regional': game.is_regional,

        'trophy_name': platinum.trophy.trophy_name if platinum else '',
        'trophy_icon_url': platinum.trophy.trophy_icon_url or '' if platinum else '',
        'trophy_earn_rate': round(float(platinum.trophy.trophy_earn_rate or 0), 2) if platinum else None,
        'rarity_label': RARITY_LABELS.get(platinum.trophy.trophy_rarity, '') if platinum else '',

        'completed_at': completed_at,
        'started_at': profile_game.first_played_date_time if profile_game else None,
        'play_duration_seconds': (
            profile_game.play_duration.total_seconds()
            if profile_game and profile_game.play_duration else None
        ),
        # Group-scoped counts: the card is about finishing THIS list, so a game whose DLC is still
        # outstanding must not read as partially done.
        'group_earned': sum(int(v or 0) for v in earned_counts.values()),
        'group_defined': sum(int(v or 0) for v in group_defined.values()),
        'group_tiers': earned_counts,

        'badge_lines': _badge_lines(profile, concept),
        'user_rating': _user_rating(profile, concept),
    }
