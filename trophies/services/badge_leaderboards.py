"""Badge leaderboards: the sealed READ layer over the standing stores (Phase 4, Lane B).

All boards are live DB reads over denormalized, indexed stores -- no Redis, no rebuild cron. They stay in the
sealed subsystem and are written by the recompute the sync/apply path already runs (see badge_xp.py).

  Badge Points     -> ProfileBadgeStanding.total_xp                  [db_index]
  Global Progress  -> ProfileBadgeStanding (-platinum, -total)        [pbs_progress_idx]
  Career XP        -> ProfileCareerStanding.total_xp                  [db_index]
  Per-series XP    -> SeriesBadgeStanding (series_slug, xp)           [sbs_series_xp_idx]
  Per-series board -> SeriesBadgeStanding (-progress_bp, advanced_at) [sbs_series_board_idx]  earners+chasers
  Per-badge earners-> UserGroupBadge (group_badge, earned_at)         [ugb_badge_earned_idx]  (rank == earned order)

Every board takes an optional `country` and every one of those slices is served by a
(..., country_code, ...board order) composite -- a country view is a range scan, not a filter over a scan.
That is why country is a FILTER here and never a board of its own.

rank_of / earners_rank return a profile's LIVE position (the value shown on the medallion back); they're single
indexed reads, whale-safe. rows(...) returns a page of (profile_id, value) for rendering; `hydrate()` turns a
page of ids into display rows in ONE query.
"""
from django.db.models import OuterRef, Q, Subquery

from trophies.models import (
    ProfileBadgeStanding, ProfileCareerStanding, SeriesBadgeStanding, UserGroupBadge, UserTitle,
)


def _slice(qs, country):
    """Apply the country filter, or not. Empty/None means the global board."""
    return qs.filter(country_code=country.upper()) if country else qs


def hydrate(profile_ids):
    """Display rows for a page of profile ids: {profile_id: {...}}, in ONE query.

    The boards store ids and values, never names -- the Redis design denormalized display data into a hash
    and then had to keep it fresh, which is why a renamed hunter showed a stale name until the next
    rebuild, and why a missing hash entry silently dropped a row from a page. Reading it live costs one
    query and cannot go stale.

    `displayed_title` is folded in as a subquery rather than called: it is a METHOD that runs
    `user_titles.filter(...).first()` and then hops the `title` FK -- two queries per row, so ~100 on a
    50-row page purely to print a word under each name. Served by `usertitle_display_idx`.
    """
    from trophies.models import Profile

    ids = list(profile_ids)
    if not ids:
        return {}
    rows = (
        Profile.objects.filter(pk__in=ids)
        .annotate(display_title=Subquery(
            UserTitle.objects.filter(profile_id=OuterRef('pk'), is_displayed=True)
            .values('title__name')[:1]
        ))
        .values('id', 'display_psn_username', 'avatar_url', 'flag', 'user_is_premium',
                'country_code', 'display_title')
    )
    return {r['id']: r for r in rows}


# ------------------------------------------------------------------ global XP ----------------------------

def xp_rows(limit=50, offset=0, country=None):
    """Top profiles by Badge Points: [(profile_id, total_xp), ...]."""
    return list(
        _slice(ProfileBadgeStanding.objects, country).order_by('-total_xp', 'profile_id')
        .values_list('profile_id', 'total_xp')[offset:offset + limit]
    )


def xp_rank(profile_id, country=None):
    """A profile's 1-based position on the Badge Points board, or None if they have no standing.

    A COUNT of everyone above rather than a window function: it is one indexed aggregate, it does not
    materialize the board, and it stays O(index) as the population grows.
    """
    mine = ProfileBadgeStanding.objects.filter(profile_id=profile_id).values_list('total_xp', flat=True).first()
    if mine is None:
        return None
    return _slice(ProfileBadgeStanding.objects, country).filter(total_xp__gt=mine).count() + 1


def progress_rows(limit=50, offset=0, country=None):
    """Global Progress: trophies across badge games, PLATINUMS first, total as the tiebreak.
    [(profile_id, platinum, gold, silver, bronze, total), ...]."""
    return list(
        _slice(ProfileBadgeStanding.objects, country)
        .order_by('-trophies_platinum', '-trophies_total', 'profile_id')
        .values_list('profile_id', 'trophies_platinum', 'trophies_gold',
                     'trophies_silver', 'trophies_bronze', 'trophies_total')[offset:offset + limit]
    )


def progress_rank(profile_id, country=None):
    """Position on the Global Progress board. Two-key ordering, so the COUNT has to express the same
    tiebreak the ORDER BY does -- ahead means MORE platinums, or equal platinums and more trophies.
    Counting only `trophies_platinum__gt` would report every hunter on a platinum rung as joint-first."""
    mine = (ProfileBadgeStanding.objects.filter(profile_id=profile_id)
            .values('trophies_platinum', 'trophies_total').first())
    if mine is None:
        return None
    plat, total = mine['trophies_platinum'], mine['trophies_total']
    ahead = _slice(ProfileBadgeStanding.objects, country).filter(
        Q(trophies_platinum__gt=plat)
        | Q(trophies_platinum=plat, trophies_total__gt=total)
    ).count()
    return ahead + 1


def career_xp_rows(limit=50, offset=0, country=None):
    """Career XP (the jobs economy): [(profile_id, total_xp, pursuer_level), ...]."""
    return list(
        _slice(ProfileCareerStanding.objects, country).order_by('-total_xp', 'profile_id')
        .values_list('profile_id', 'total_xp', 'pursuer_level')[offset:offset + limit]
    )


def career_xp_rank(profile_id, country=None):
    """Position on the Career XP board, or None without a standing."""
    mine = ProfileCareerStanding.objects.filter(profile_id=profile_id).values_list('total_xp', flat=True).first()
    if mine is None:
        return None
    return _slice(ProfileCareerStanding.objects, country).filter(total_xp__gt=mine).count() + 1


# ------------------------------------------------------------------ per-series XP / progress -------------

def series_xp_rows(series_slug, limit=50, offset=0):
    """Top profiles by XP in one series: [(profile_id, xp), ...]."""
    return list(
        SeriesBadgeStanding.objects.filter(series_slug=series_slug).order_by('-xp', 'profile_id')
        .values_list('profile_id', 'xp')[offset:offset + limit]
    )


def series_board_rows(series_slug, limit=50, offset=0, country=None):
    """The per-series board -- earners AND chasers, one list:
    [(profile_id, progress_bp, stages_cleared, stages_total, advanced_at), ...].

    `(-progress_bp, advanced_at)` puts earners (10000 bp) on top by completion date, then each rung of
    chasers with whoever got there first ahead. `advanced_at` is not decoration: progress_bp is discrete
    (cleared / gating stages), so a 3-stage series stacks everyone on 1/3 or 2/3, and without the date
    those large ties would sort by profile id and read as unranked.
    """
    return list(
        _slice(SeriesBadgeStanding.objects.filter(series_slug=series_slug), country)
        .order_by('-progress_bp', 'advanced_at', 'profile_id')
        .values_list('profile_id', 'progress_bp', 'stages_cleared', 'stages_total',
                     'advanced_at')[offset:offset + limit]
    )


def series_board_rank(series_slug, profile_id, country=None):
    """Position on the merged per-series board. Mirrors the two-key ORDER BY: ahead means further along,
    or equally far along and there sooner. A null `advanced_at` sorts last within its rung, matching the
    query's NULLS LAST."""
    mine = (SeriesBadgeStanding.objects.filter(series_slug=series_slug, profile_id=profile_id)
            .values('progress_bp', 'advanced_at').first())
    if mine is None:
        return None
    bp, at = mine['progress_bp'], mine['advanced_at']
    qs = _slice(SeriesBadgeStanding.objects.filter(series_slug=series_slug), country)
    ahead = qs.filter(progress_bp__gt=bp).count()
    if at is not None:
        ahead += qs.filter(progress_bp=bp, advanced_at__lt=at).count()
    return ahead + 1


def series_rank(series_slug, profile_id):
    """A profile's 1-based XP position within a series, or None if they have no standing there."""
    mine = (
        SeriesBadgeStanding.objects.filter(series_slug=series_slug, profile_id=profile_id)
        .values_list('xp', flat=True).first()
    )
    if mine is None:
        return None
    return SeriesBadgeStanding.objects.filter(series_slug=series_slug, xp__gt=mine).count() + 1


# ------------------------------------------------------------------ per-badge earners --------------------

def earners_rows(group_badge_id, limit=50, offset=0):
    """First-to-complete order for one group badge: [(profile_id, earned_at), ...] earliest first."""
    return list(
        UserGroupBadge.objects.filter(group_badge_id=group_badge_id).order_by('earned_at', 'profile_id')
        .values_list('profile_id', 'earned_at')[offset:offset + limit]
    )


def earners_rank(profile_id, group_badge_id):
    """A profile's LIVE earners position for a group badge (1 = first to complete the current iteration), or
    None if they don't currently hold it. This is the value shown on the medallion back."""
    mine = (
        UserGroupBadge.objects.filter(group_badge_id=group_badge_id, profile_id=profile_id)
        .values_list('earned_at', flat=True).first()
    )
    if mine is None:
        return None
    return UserGroupBadge.objects.filter(group_badge_id=group_badge_id, earned_at__lt=mine).count() + 1


def earners_ranks(profile_id, group_badge_ids):
    """Batched earners_rank for a profile's set of badges (e.g. a medallion grid): {group_badge_id: rank}. Held
    badges only; each is one bounded indexed COUNT, so this is len(held) queries -- fine for a page's medallions."""
    held = dict(
        UserGroupBadge.objects.filter(profile_id=profile_id, group_badge_id__in=group_badge_ids)
        .values_list('group_badge_id', 'earned_at')
    )
    return {
        gb_id: UserGroupBadge.objects.filter(group_badge_id=gb_id, earned_at__lt=at).count() + 1
        for gb_id, at in held.items()
    }
