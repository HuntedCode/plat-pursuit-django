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
import math
from collections import defaultdict

from django.db.models import Count, OuterRef, Q, Subquery

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
        # Badges HELD, counted in the same pass. The Redis rows carried this denormalized into the
        # display hash; counting it per row instead would be a query each, and there is no badge count on
        # the standing to read.
        .annotate(badges_held=Count('group_badges', distinct=True))
        .values('id', 'display_psn_username', 'avatar_url', 'flag', 'user_is_premium',
                'country_code', 'display_title', 'badges_held')
    )
    return {r['id']: r for r in rows}


def country_options():
    """Countries with ranked hunters, as [{code, name, flag}], for the picker.

    Names and flags come from Profile rows rather than a hardcoded table, so a country appears exactly as
    the hunters from it are already labelled elsewhere on the site. Deduped by CODE, because the same
    country can carry slightly different `country` spellings across profiles and the picker must not show
    it twice.
    """
    from trophies.models import Profile

    codes = active_countries()
    if not codes:
        return []
    rows = (Profile.objects.filter(country_code__in=codes)
            .exclude(country__isnull=True).exclude(country='')
            .values_list('country_code', 'country', 'flag'))
    seen = {}
    for code, name, flag in rows:
        if code not in seen:
            seen[code] = {'code': code, 'name': name or code, 'flag': flag or ''}
    # Any code with ranked hunters but no labelled profile still gets an entry -- otherwise selecting it
    # from a URL would validate but show a blank picker.
    for code in codes:
        seen.setdefault(code, {'code': code, 'name': code, 'flag': ''})
    return sorted(seen.values(), key=lambda c: c['name'].lower())


def active_countries():
    """Country codes with at least one ranked profile on ANY board, for the country picker.

    Replaces the Redis `lb:xp:country:index` set, which had to be maintained alongside every per-country
    sorted set. Here it is a DISTINCT over indexed columns that already exist.

    The UNION matters: the two economies are sealed apart, so a hunter can have Career XP and no badge
    standing at all. Reading only ProfileBadgeStanding left their country missing from the picker, which
    made it unselectable on the Career board they DO appear on -- the filter would have been quietly
    incomplete for exactly the surface it was added to serve.
    """
    codes = set(
        ProfileBadgeStanding.objects.exclude(country_code='')
        .values_list('country_code', flat=True).distinct()
    )
    codes |= set(
        ProfileCareerStanding.objects.exclude(country_code='')
        .values_list('country_code', flat=True).distinct()
    )
    return sorted(codes)


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


def series_board_count(series_slug, country=None, earned=None):
    """How many profiles sit on a series board (optionally one bracket of it)."""
    return _series_board_qs(series_slug, country, earned).count()


def _series_board_qs(series_slug, country, earned):
    qs = _slice(SeriesBadgeStanding.objects.filter(series_slug=series_slug), country)
    if earned is True:
        return qs.filter(progress_bp__gte=10000)
    if earned is False:
        return qs.filter(progress_bp__lt=10000)
    return qs


def series_board_rows(series_slug, limit=50, offset=0, country=None, earned=None):
    """The per-series board -- earners AND chasers, one list:
    [(profile_id, progress_bp, stages_cleared, stages_total, advanced_at), ...].

    `(-progress_bp, advanced_at)` puts earners (10000 bp) on top by completion date, then each rung of
    chasers with whoever got there first ahead. `advanced_at` is not decoration: progress_bp is discrete
    (cleared / gating stages), so a 3-stage series stacks everyone on 1/3 or 2/3, and without the date
    those large ties would sort by profile id and read as unranked.

    `earned` brackets the board: True = those who finished (10000 bp) ordered by completion date,
    False = those still chasing, None = the whole board. The bracket exists because the surface being
    retired renders the two as separate tables; the panel that replaces it can take the whole board.
    """
    return list(
        _series_board_qs(series_slug, country, earned)
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


# ------------------------------------------------------------------ page assembly ------------------------

class BoardPaginator:
    """Template-compatible paginator over a COUNT, not a queryset -- boards are keyset pages, not slices of
    an evaluated list. Duck-types Django's Paginator for the shared pagination partial.

    Carried over from the Redis service (RedisPaginator) unchanged in behaviour, so the templates neither
    know nor care which backend produced the page."""

    def __init__(self, total_count, per_page):
        self.count = total_count
        self.per_page = per_page
        self.num_pages = max(1, math.ceil(total_count / per_page))


class BoardPage:
    """Template-compatible page object (see BoardPaginator)."""

    def __init__(self, object_list, number, paginator):
        self.object_list = object_list
        self.number = number
        self.paginator = paginator

    def __iter__(self):
        return iter(self.object_list)

    def __len__(self):
        return len(self.object_list)

    @property
    def has_previous(self):
        return self.number > 1

    @property
    def has_next(self):
        return self.number < self.paginator.num_pages

    @property
    def previous_page_number(self):
        return self.number - 1

    @property
    def next_page_number(self):
        return self.number + 1


def _entry(hydrated, profile_id, rank):
    """The row shape the leaderboard templates read: identity + rank. Board-specific figures are merged in
    by the caller.

    Note `displayed_title`: the templates use that key, while `hydrate` annotates `display_title` (the
    subquery). Mapping it here keeps the templates untouched during the backend swap -- renaming in the
    template is a step-4 concern, and doing both at once would make a rendering bug and a data bug look
    identical.
    """
    p = hydrated.get(profile_id) or {}
    return {
        'profile_id': profile_id,
        'rank': rank,
        'psn_username': p.get('display_psn_username', ''),
        'avatar_url': p.get('avatar_url') or '',
        'flag': p.get('flag') or '',
        'is_premium': p.get('user_is_premium', False),
        'displayed_title': p.get('display_title') or '',
        'total_badges': p.get('badges_held', 0),
    }


def page(rows, offset, extra=None):
    """Hydrate a page of board rows into template entries, numbering ranks from `offset`.

    `rows` is whatever the board function returned (profile_id first); `extra` maps a row tuple to the
    board-specific keys. One hydrate() for the whole page, so a page costs two queries total: the board
    read and the identity read.

    Rows whose profile vanished between the two reads still render (blank identity) rather than being
    dropped -- the Redis design silently skipped them, which left pages showing 47 of 50 rows with correct
    ranks and invisible holes.
    """
    rows = list(rows)
    hydrated = hydrate([r[0] for r in rows])
    out = []
    for i, row in enumerate(rows):
        entry = _entry(hydrated, row[0], offset + i + 1)
        if extra:
            entry.update(extra(row))
        out.append(entry)
    return out


# ------------------------------------------------------------------ directory previews -------------------

def _top_n_by_partition(qs, partition_field, order_by, n, value_fields):
    """Top `n` rows per partition, in ONE query, via a window function.

    The naive shape is a query per entity, which compounds under infinite scroll: a 24-card page becomes
    24 board reads, and a second scroll page 24 more. `ROW_NUMBER() OVER (PARTITION BY ...)` collapses
    that to one, and Django 4.2+ allows filtering directly on a window expression so it needs no raw SQL.

    Returns {partition_value: [row_tuple, ...]} preserving board order within each partition.
    """
    from django.db.models import F, Window
    from django.db.models.functions import RowNumber

    rows = (
        qs.annotate(_rn=Window(RowNumber(), partition_by=[F(partition_field)], order_by=order_by))
        .filter(_rn__lte=n)
        .values_list(partition_field, *value_fields)
    )
    out = defaultdict(list)
    for row in rows:
        out[row[0]].append(row[1:])
    return dict(out)


def series_board_previews(series_slugs, n=5):
    """Top `n` of each series' board: {series_slug: [(profile_id, progress_bp, stages_cleared,
    stages_total, advanced_at), ...]}.

    Same ordering as the full board, so a preview can never disagree with the board it previews.
    """
    from django.db.models import F

    if not series_slugs:
        return {}
    return _top_n_by_partition(
        SeriesBadgeStanding.objects.filter(series_slug__in=list(series_slugs)),
        'series_slug',
        [F('progress_bp').desc(), F('advanced_at').asc(), F('profile_id').asc()],
        n,
        ('profile_id', 'progress_bp', 'stages_cleared', 'stages_total', 'advanced_at'),
    )


def series_board_counts(series_slugs):
    """Entrants per series: {series_slug: count}. One grouped query.

    Feeds BOTH the "most entrants" sort and the minimum-participants gate, which is what makes that sort
    free -- the counts are needed either way.
    """
    from django.db.models import Count

    if not series_slugs:
        return {}
    return dict(
        SeriesBadgeStanding.objects.filter(series_slug__in=list(series_slugs))
        .values('series_slug').annotate(n=Count('id'))
        .values_list('series_slug', 'n')
    )


def game_board_previews(game_ids, n=5):
    """Top `n` of each game's board: {game_id: [(profile_id, progress, most_recent_trophy_date), ...]}.

    Ordered to match `pg_game_leaderboard_idx` (game, -progress, most_recent_trophy_date, profile) so the
    window rides the index the shipped game leaderboard already uses, rather than forcing its own sort.
    """
    from django.db.models import F
    from trophies.models import ProfileGame

    if not game_ids:
        return {}
    return _top_n_by_partition(
        ProfileGame.objects.filter(game_id__in=list(game_ids), progress__gt=0),
        'game_id',
        [F('progress').desc(), F('most_recent_trophy_date').asc(), F('profile_id').asc()],
        n,
        ('profile_id', 'progress', 'most_recent_trophy_date'),
    )


# ------------------------------------------------------------------ job boards ---------------------------

def job_rows(job_slug, limit=50, offset=0, country=None):
    """One job's board: [(profile_id, total_xp, level), ...].

    Served by `pjx_job_cc_xp_idx` when sliced and `profilejobxp_job_xp_idx` otherwise -- both already
    existed, the latter with a docstring calling ProfileJobXP "the read side for the Lab + leaderboards".
    `> 0` because a row is created for a job the moment any XP touches it, so unfiltered the board would
    open with a wall of zeroes.
    """
    from trophies.models import ProfileJobXP

    return list(
        _slice(ProfileJobXP.objects.filter(job_id=job_slug, total_xp__gt=0), country)
        .order_by('-total_xp', 'profile_id')
        .values_list('profile_id', 'total_xp', 'level')[offset:offset + limit]
    )


def job_rank(job_slug, profile_id, country=None):
    """A profile's position on one job's board, or None if they have no XP in it."""
    from trophies.models import ProfileJobXP

    mine = (ProfileJobXP.objects.filter(job_id=job_slug, profile_id=profile_id)
            .values_list('total_xp', flat=True).first())
    if not mine:
        return None
    return _slice(ProfileJobXP.objects.filter(job_id=job_slug), country).filter(total_xp__gt=mine).count() + 1


def job_board_counts(job_slugs):
    """Entrants per job: {job_slug: count}. Feeds the Job Boards directory's gate and its sort."""
    from django.db.models import Count
    from trophies.models import ProfileJobXP

    if not job_slugs:
        return {}
    return dict(
        ProfileJobXP.objects.filter(job_id__in=list(job_slugs), total_xp__gt=0)
        .values('job_id').annotate(n=Count('id'))
        .values_list('job_id', 'n')
    )


def job_board_previews(job_slugs, n=5):
    """Top `n` of each job's board, in ONE query: {job_slug: [(profile_id, total_xp, level), ...]}."""
    from django.db.models import F
    from trophies.models import ProfileJobXP

    if not job_slugs:
        return {}
    return _top_n_by_partition(
        ProfileJobXP.objects.filter(job_id__in=list(job_slugs), total_xp__gt=0),
        'job_id',
        [F('total_xp').desc(), F('profile_id').asc()],
        n,
        ('profile_id', 'total_xp', 'level'),
    )
