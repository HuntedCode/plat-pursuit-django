"""Badge leaderboards: the sealed READ layer over the standing stores (Phase 4, Lane B).

All boards are live DB reads over denormalized, indexed stores -- no Redis, no rebuild cron. They stay in the
sealed subsystem and are written by the recompute the sync/apply path already runs (see badge_xp.py).

  Badge Points     -> ProfileBadgeStanding.total_xp                  [db_index]
  Badge Trophies   -> ProfileBadgeStanding (-platinum, -total)        [pbs_progress_idx]
  Career XP        -> ProfileCareerStanding.total_xp                  [db_index]
  Per-series XP    -> SeriesBadgeStanding (series_slug, xp)           [sbs_series_xp_idx]
  Per-series board -> SeriesBadgeStanding (-progress_bp, advanced_at) [sbs_series_board_idx]  earners+chasers
  Per-badge earners-> UserGroupBadge (group_badge, earned_at)         [ugb_badge_earned_idx]  (rank == earned order)

Every board takes an optional `country` and every one of those slices is served by a
(..., country_code, ...board order) composite -- a country view is a range scan, not a filter over a scan.
That is why country is a FILTER here and never a board of its own.

The two BADGE boards additionally take an `edition` (a PlatformGroup key). That swaps the STORE rather
than adding a WHERE: ProfileEditionStanding carries the same columns under the same names, pre-sliced and
indexed edition-first, so `_store` picks a manager and every query body below stays as it was. The two
filters compose, which is why the edition indexes carry country in the middle.

rank_of / earners_rank return a profile's LIVE position (the value shown on the medallion back); they're single
indexed reads, whale-safe. rows(...) returns a page of (profile_id, value) for rendering; `hydrate()` turns a
page of ids into display rows in ONE query.
"""
import math
from collections import defaultdict

from django.db.models import Count, OuterRef, Q, Subquery

from trophies.models import (
    ProfileBadgeStanding, ProfileCareerStanding, ProfileEditionStanding, SeriesBadgeStanding,
    UserGroupBadge, UserTitle,
)


def _slice(qs, country):
    """Apply the country filter, or not. Empty/None means the global board."""
    return qs.filter(country_code=country.upper()) if country else qs


# ------------------------------------------------------------------ rank == position ---------------------
# Every board's canonical order ENDS IN `profile_id`, a unique final key that makes the order TOTAL, and the
# rank count expresses that same full key list. This is the rule `game_leaderboard_service` already
# established ("load-bearing, not decoration"), and it is what keeps the two ways a reader meets their rank
# from disagreeing:
#
#   page()      numbers rows by SLOT      -> offset + i + 1
#   *_rank()    counts everyone AHEAD     -> count(ahead) + 1
#
# Those are the same number only when no two rows can tie. They tie constantly here: Badge Points is
# quantized to 500a + 600b, so hundreds of hunters land on exactly 1,600. Counting only the visible sort
# keys returned the tie group's FIRST slot to every member of it -- the twelfth hunter on 1,600 was told
# "#7" in the header and then found their own name at row 18.

_ASC, _DESC, _ASC_NULLS_LAST = 'asc', 'desc', 'asc_nulls_last'

# (field, direction) in canonical order, unique key last. One definition per board, shared by the rank
# count and asserted against the ORDER BY by test_rank_equals_position.
XP_KEYS = (('total_xp', _DESC), ('profile_id', _ASC))
TROPHY_KEYS = (('trophies_platinum', _DESC), ('trophies_total', _DESC), ('profile_id', _ASC))
CAREER_KEYS = (('total_xp', _DESC), ('profile_id', _ASC))
# Postgres orders ASC NULLS LAST by default, which is what `.order_by('advanced_at')` gets and what this
# mirrors: a hunter who has not advanced sorts below one who has, within the same rung.
SERIES_BOARD_KEYS = (('progress_bp', _DESC), ('advanced_at', _ASC_NULLS_LAST), ('profile_id', _ASC))
JOB_KEYS = (('total_xp', _DESC), ('profile_id', _ASC))


def _better(field, direction, value):
    """Q for 'strictly ahead of `value` on this key alone', in canonical order."""
    if direction == _ASC_NULLS_LAST:
        if value is None:
            return Q(**{f'{field}__isnull': False})   # any non-null beats a null, since nulls sort last
        return Q(**{f'{field}__lt': value})
    return Q(**{f'{field}__{"gt" if direction == _DESC else "lt"}': value})


def _ahead_q(keys, row):
    """Q matching everyone ranked strictly ahead of `row`: tied on keys 0..i-1 and strictly better at key i.

    Same construction as `game_leaderboard_service.Board._ahead_of`. `row` is a dict of the key fields'
    values for the profile being ranked.
    """
    result = Q(pk__in=[])                     # matches nothing; the OR terms accumulate onto it
    tie = None
    for field, direction in keys:
        value = row[field]
        clause = _better(field, direction, value)
        result = result | (clause if tie is None else (tie & clause))
        eq = Q(**{f'{field}__isnull': True}) if value is None else Q(**{field: value})
        tie = eq if tie is None else (tie & eq)
    return result


def badge_store(edition=None):
    """The queryset the two badge boards read: all editions, or one edition's pre-sliced store.

    ProfileEditionStanding names its columns identically to ProfileBadgeStanding on purpose, so this is a
    store swap rather than a query rewrite -- every caller below orders and filters the same way whichever
    comes back.

    An unknown edition key returns an EMPTY store rather than falling back to all editions. Silently
    widening would show a reader the global board under an edition heading, which is worse than showing
    nothing; the VIEW validates the key against live editions before it ever gets here, so an empty board
    from this path means a bug, not a typo'd URL.
    """
    if not edition:
        return ProfileBadgeStanding.objects.all()
    return ProfileEditionStanding.objects.filter(platform_group_key=edition)


def active_editions():
    """Platform editions the picker may offer, as PlatformGroup rows.

    BOTH gates, deliberately, because the reader and the writer disagree about which one matters and an
    edition needs to satisfy each:

      is_active=True         -- what `badge_xp.edition_platforms()` uses to decide which standings to WRITE.
      group_badges__is_live  -- what the Browse Badges gallery chips use; an unlaunched group would offer a
                                board that could only ever be empty.

    Gating on `is_live` alone was the bug: deactivating a group whose badges are still live left it in this
    dropdown while the write seam stopped maintaining its rows and then deleted them on each re-sync, so
    the board drained over a few days and finally claimed "nobody is on Legacy HD yet" -- a sentence about
    a config flag, presented as a fact about hunters.
    """
    from trophies.models import PlatformGroup
    return list(
        PlatformGroup.objects.filter(is_active=True, group_badges__is_live=True)
        .distinct().order_by('sort_order', 'name')
    )


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
        # NO badge count here. `badges_held=Count('group_badges', distinct=True)` used to ride along,
        # carried over from the Redis display hash -- a LEFT JOIN + GROUP BY + COUNT(DISTINCT) on every
        # board page, every directory card page and every job board, for a figure no template renders. The
        # only three that ever read it are orphaned pre-rebuild partials with no includers. If a board ever
        # wants to show badges held, add it back deliberately and render it.
        .values('id', 'display_psn_username', 'avatar_url', 'flag', 'user_is_premium',
                'country_code', 'display_title')
    )
    return {r['id']: r for r in rows}


def country_options(codes=None):
    """Countries with ranked hunters, as [{code, name, flag}], for the picker.

    Names and flags come from Profile rows rather than a hardcoded table, so a country appears exactly as
    the hunters from it are already labelled elsewhere on the site.

    ONE ROW PER COUNTRY, via `DISTINCT ON (country_code)`. It used to select one row per PROFILE and dedupe
    them in a Python loop -- on a public, uncached, anonymous-reachable page, that pulled the entire linked
    population across the wire on every request to build a ~60-entry dropdown, which is CLAUDE.md's
    forbidden shape at population scale rather than per-user scale. `trophies/forms.py` already had the
    right pattern.

    `order_by(country_code, ...)` is required by DISTINCT ON and also decides WHICH profile's spelling wins
    when a country carries several: the alphabetically-first non-empty label, deterministically, rather
    than whichever row the database happened to return first.

    `codes` may be passed by a caller that already has them, so the page does not resolve the same two
    DISTINCT aggregates twice.
    """
    from trophies.models import Profile

    codes = active_countries() if codes is None else codes
    if not codes:
        return []
    rows = (Profile.objects.filter(country_code__in=codes)
            .exclude(country__isnull=True).exclude(country='')
            .order_by('country_code', 'country', 'flag')
            .distinct('country_code')
            .values_list('country_code', 'country', 'flag'))
    seen = {code: {'code': code, 'name': name or code, 'flag': flag or ''}
            for code, name, flag in rows}
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

def board_count(tab, country=None, edition=None):
    """How many hunters are ON one of the three Global Boards -- the population its rows come from.

    ONE definition of each board's membership, read by the paginator AND by the header tally. The view used
    to rebuild these querysets by hand, beside the service that owned the rows, and both of this page's
    count bugs came out of that duplication: the Career board grew its `> 0` rule in the service and not in
    the view's copy (so the last page ran past the total), and the header counted a third, looser
    expression again (so `?tab=career` printed the badge population above the career wall).
    """
    if tab == 'career':
        return _slice(ProfileCareerStanding.objects.filter(total_xp__gt=0), country).count()
    field = 'total_xp' if tab == 'points' else 'trophies_total'
    return _slice(badge_store(edition).filter(**{f'{field}__gt': 0}), country).count()


def xp_rows(limit=50, offset=0, country=None, edition=None):
    """Top profiles by Badge Points: [(profile_id, total_xp, badges_held), ...].

    `> 0` is the board's MEMBERSHIP rule, applied here and not only where the page is counted. A hunter can
    hold trophies in an edition without clearing a gating stage in it, which keeps their edition standing
    alive on zero points -- so an unfiltered read would hand the last page rows the count never promised.

    `badges_held` rides along as the board's supporting figure. It is a COLUMN on the same row, so it costs
    nothing beyond the read already happening -- and under an edition slice it is that edition's badge
    count, because a global figure beside a sliced points total would be describing two different things.
    """
    return list(
        _slice(badge_store(edition), country).filter(total_xp__gt=0)
        .order_by('-total_xp', 'profile_id')
        .values_list('profile_id', 'total_xp', 'badges_held')[offset:offset + limit]
    )


def xp_rank(profile_id, country=None, edition=None):
    """A profile's 1-based position on the Badge Points board, or None if they have no standing.

    A COUNT of everyone above rather than a window function: it is one indexed aggregate, it does not
    materialize the board, and it stays O(index) as the population grows.

    The viewer's own figure is read from the SAME store the board reads. Taking it from the all-editions
    row and counting against an edition's would rank them by a number nobody else on that board is being
    measured by -- and it would look plausible.
    """
    store = badge_store(edition)
    mine = store.filter(profile_id=profile_id).values_list('total_xp', flat=True).first()
    if not mine:
        return None      # no standing, or a standing of zero -- which is not ON the board (see xp_rows)
    ahead = _slice(store, country).filter(total_xp__gt=0).filter(
        _ahead_q(XP_KEYS, {'total_xp': mine, 'profile_id': profile_id})).count()
    return ahead + 1


def badge_trophy_rows(limit=50, offset=0, country=None, edition=None):
    """Badge Trophies: trophies across badge games, PLATINUMS first, total as the tiebreak.
    [(profile_id, platinum, gold, silver, bronze, total), ...].

    `> 0` is the board's membership rule -- see xp_rows for why it belongs here rather than only on the
    count that pages it."""
    return list(
        _slice(badge_store(edition), country).filter(trophies_total__gt=0)
        .order_by('-trophies_platinum', '-trophies_total', 'profile_id')
        .values_list('profile_id', 'trophies_platinum', 'trophies_gold',
                     'trophies_silver', 'trophies_bronze', 'trophies_total')[offset:offset + limit]
    )


def badge_trophy_rank(profile_id, country=None, edition=None):
    """Position on the Badge Trophies board. The COUNT expresses the board's FULL key list, tail included
    -- ahead means more platinums, or equal platinums and more trophies, or tied on both and a lower
    profile id. Counting only `trophies_platinum__gt` would report every hunter on a platinum rung as
    joint-first; stopping at `trophies_total` still ties everyone who matches on both."""
    store = badge_store(edition)
    mine = store.filter(profile_id=profile_id).values('trophies_platinum', 'trophies_total').first()
    if mine is None or not mine['trophies_total']:
        return None      # not on this board -- see badge_trophy_rows
    ahead = _slice(store, country).filter(trophies_total__gt=0).filter(
        _ahead_q(TROPHY_KEYS, {**mine, 'profile_id': profile_id})).count()
    return ahead + 1


def career_xp_rows(limit=50, offset=0, country=None):
    """Career XP (the jobs economy): [(profile_id, total_xp, pursuer_level), ...].

    `> 0` is this board's membership rule, the same one the two badge boards apply. Zero-XP career
    standings are a real state, not a hypothetical: `recompute_career_standing` upserts unconditionally, so
    `reset_claim --all` and a `recompute_job_xp` for a hunter with no grants both leave a row at zero.
    Without this filter the paginator counted the filtered set while the rows came from the unfiltered one,
    so the last page ran past the total the footer promised.
    """
    return list(
        _slice(ProfileCareerStanding.objects, country).filter(total_xp__gt=0)
        .order_by('-total_xp', 'profile_id')
        .values_list('profile_id', 'total_xp', 'pursuer_level')[offset:offset + limit]
    )


def career_xp_rank(profile_id, country=None):
    """Position on the Career XP board, or None if they are not on it.

    `if not mine` rather than `is None`: a zero-XP standing exists but is not ON the board (see
    career_xp_rows). Guarding on None alone handed every zeroed hunter `count(everyone) + 1` -- one rank,
    shared by all of them, pointing at a board none of them appear on.
    """
    mine = ProfileCareerStanding.objects.filter(profile_id=profile_id).values_list('total_xp', flat=True).first()
    if not mine:
        return None
    ahead = _slice(ProfileCareerStanding.objects, country).filter(total_xp__gt=0).filter(
        _ahead_q(CAREER_KEYS, {'total_xp': mine, 'profile_id': profile_id})).count()
    return ahead + 1


# ------------------------------------------------------------------ per-series XP / progress -------------

def series_board_count(series_slug, country=None):
    """How many profiles sit on a series board."""
    return _series_board_qs(series_slug, country).count()


def _series_board_qs(series_slug, country):
    return _slice(SeriesBadgeStanding.objects.filter(series_slug=series_slug), country)


def series_board_rows(series_slug, limit=50, offset=0, country=None):
    """The per-series board -- earners AND chasers, one list:
    [(profile_id, progress_bp, stages_cleared, stages_total, advanced_at), ...].

    `(-progress_bp, advanced_at)` puts earners (10000 bp) on top by completion date, then each rung of
    chasers with whoever got there first ahead. `advanced_at` is not decoration: progress_bp is discrete
    (cleared / gating stages), so a 3-stage series stacks everyone on 1/3 or 2/3, and without the date
    those large ties would sort by profile id and read as unranked.

    """
    return list(
        _series_board_qs(series_slug, country)
        .order_by('-progress_bp', 'advanced_at', 'profile_id')
        .values_list('profile_id', 'progress_bp', 'stages_cleared', 'stages_total',
                     'advanced_at')[offset:offset + limit]
    )


def series_board_rank(series_slug, profile_id, country=None):
    """Position on the merged per-series board. Mirrors the FULL ORDER BY: ahead means further along, or
    equally far along and there sooner, or tied on both and a lower profile id. A null `advanced_at` sorts
    last within its rung, matching the query's NULLS LAST.

    The tail matters most here of all the boards: progress_bp is discrete (cleared / gating stages), so a
    3-stage series stacks every chaser onto 1/3 or 2/3 and the date breaks only some of those ties.
    """
    mine = (SeriesBadgeStanding.objects.filter(series_slug=series_slug, profile_id=profile_id)
            .values('progress_bp', 'advanced_at').first())
    if mine is None:
        return None
    qs = _slice(SeriesBadgeStanding.objects.filter(series_slug=series_slug), country)
    return qs.filter(_ahead_q(SERIES_BOARD_KEYS, {**mine, 'profile_id': profile_id})).count() + 1


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


def entry(hydrated, profile_id, rank):
    """The row shape the leaderboard templates read: identity + rank. Board-specific figures are merged in
    by the caller.

    PUBLIC: the directory views build their preview rows with this directly (they page differently from
    `page()`, which numbers a single board's slice). It carried a leading underscore and three
    cross-module callers, which is a contradiction -- either it is internal and they should not reach for
    it, or it is API. It is API.

    Note `displayed_title`: the templates use that key, while `hydrate` annotates `display_title` (the
    subquery). Mapping it here keeps the templates untouched during the backend swap -- renaming in the
    template is a step-4 concern, and doing both at once would make a rendering bug and a data bug look
    identical.
    """
    p = hydrated.get(profile_id) or {}
    return {
        'profile_id': profile_id,
        'rank': rank,
        # `or ''` rather than a default, because the column is nullable: a row whose profile exists but has
        # no display name would otherwise return None, and the template links to `/hunters/None/`. The row
        # template gates the LINK on this being truthy -- an empty name cannot be reversed against
        # `<str:psn_username>` and raises NoReverseMatch, which is a 500 for the whole page rather than the
        # blank row `page()` promises below.
        'psn_username': p.get('display_psn_username') or '',
        'avatar_url': p.get('avatar_url') or '',
        'flag': p.get('flag') or '',
        'is_premium': p.get('user_is_premium', False),
        'displayed_title': p.get('display_title') or '',
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
        row_entry = entry(hydrated, row[0], offset + i + 1)
        if extra:
            row_entry.update(extra(row))
        out.append(row_entry)
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
    ahead = (_slice(ProfileJobXP.objects.filter(job_id=job_slug, total_xp__gt=0), country)
             .filter(_ahead_q(JOB_KEYS, {'total_xp': mine, 'profile_id': profile_id})).count())
    return ahead + 1


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
