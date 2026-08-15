"""Profile Activity: someone's trophy hunting as SESSIONS rather than as a list of trophies.

A session is one game on one day. That is what "what have they been playing" means, and it is the unit a
flat trophy log destroys: twelve rows from the same afternoon on the same game say twelve times less than
one card reading "God of War -- 12 trophies, including the platinum".

Whale safety is the whole design, not a caveat. Profiles here run to 250,000+ earned trophies, so:

  * the grouping is done by POSTGRES (`values().annotate()`), never by iterating rows in Python. The
    database returns tens of summary rows; the alternative materializes hundreds of MB of ORM objects.
  * pagination is KEYSET, not `Paginator`. An offset paginator runs `COUNT(*)` over the whole set on every
    page load and degrades as you page deeper; keyset reads an index range and stops.
  * the window is a page of DAYS, not of sessions. A day's sessions are indivisible -- they all share a
    date -- so cutting mid-day would either split a session or need the already-shown pairs carried in the
    cursor. Days are naturally bounded (you can only play so many games in one), so a fixed number of days
    is a bounded page.

Two things make that true rather than merely intended, and both are easy to undo by accident:

  * A day is filtered as a TIMESTAMP RANGE on the raw column, never as `TruncDate(...) == day`. A predicate
    on a function of a column cannot be an index range, so the trunc form quietly degrades
    `earnedtrophy_timeline_idx` to its `(profile, earned)` prefix and reads every entry that profile owns --
    250,000 of them for a heavy account, joined to Trophy. `TruncDate` belongs in `values()`, where it
    names the group, and nowhere else. (An expression index cannot rescue the trunc form either: the
    timezone conversion is STABLE, not IMMUTABLE.)
  * Days are computed in ONE fixed zone (`DAY_TZ`), not the viewer's. `TimezoneMiddleware` activates each
    signed-in user's own zone, and `TruncDate` follows the active one -- so day boundaries, totals and
    month headers would differ per viewer, and `profile_day` is a shared, crawlable URL. A day has to mean
    the same thing to a crawler on UTC and to a reader on UTC-8, or the link does not survive being shared.

It rides `earnedtrophy_timeline_idx`, the existing partial index on
`(profile, earned, earned_date_time) WHERE earned=True`. No new migration. The one query that cannot use
it for ordering is the distinct-day list, which is an index-ONLY scan of that profile's entries (no heap
access); everything downstream is a bounded range seek.
"""
import logging
from datetime import datetime, time, timedelta, timezone as dt_timezone

from django.db.models import Count, Q, Min, Max
from django.db.models.functions import TruncDate

logger = logging.getLogger("psn_api")

#: Days per page. Infinite scroll appends these, so it is a scroll depth rather than a "load more" bite.
#: 30 fills whole rows at the tile grid's 2, 3 and 5 column breakpoints. It leaves a half row at 4 columns
#: (1024-1535px) -- no page size is even across all four, since that would take 60.
DAYS_PER_PAGE = 30

#: Covers composed into a day tile. The mosaic primitive lays out 1-4 and no more, so asking for more
#: would fetch art nothing can draw.
MOSAIC_COVERS = 4

_TIERS = ('platinum', 'gold', 'silver', 'bronze')

#: The zone a DAY is measured in. Fixed on purpose -- see the module docstring. UTC because that is what
#: an anonymous visitor and a crawler already get, so the shared URL keeps meaning what it meant when it
#: was indexed.
DAY_TZ = dt_timezone.utc


def _day_bounds(day):
    """The half-open UTC range covering one day: `[start, next_start)`.

    Half-open, not `__date=day`, so it is a true index range on `earned_date_time` -- and so a trophy
    earned at 23:59:59.999 belongs to its day rather than to a rounding error.
    """
    start = datetime.combine(day, time.min, tzinfo=DAY_TZ)
    return start, start + timedelta(days=1)


def build_activity_page(profile, page=1, per_page=DAYS_PER_PAGE):
    """One page of DAY tiles, newest first.

    Paginated by OFFSET over distinct days, which the trophy Log deliberately is not. The difference is
    cardinality: offsetting trophies means walking a whale's 250,000 rows, while distinct days are bounded
    by how long they have been playing -- thousands at the very most. It also needs no `COUNT`, since the
    scroller stops when a page comes back short.

    Deliberately shallow. A tile needs its totals and up to four covers, so that is all this builds -- the
    sessions behind a day come from `day_sessions` when that day is opened. The flat version aggregated
    every session on the page whether or not anyone looked at one.

    Trophies with no `earned_date_time` are EXCLUDED and cannot be otherwise: a day is the unit, and an
    undated trophy has no day. They stay visible in the Log, which is the honest place for them.
    """
    rows = _day_game_rows(profile, page, per_page)
    if not rows:
        return {'activity_days': []}

    covers = _games_for(_mosaic_rows(rows))
    days = _by_day(rows, covers)
    _mark_month_starts(profile, days, page, per_page)
    return {'activity_days': days}


def _mark_month_starts(profile, days, page, per_page):
    """Flag the day that opens each month, so the wall can break itself up as you scroll.

    Decided HERE rather than in the template, because of the page boundary: a month runs across it, and an
    appended page knows nothing about what is already on screen. Marking the first day of every page would
    repeat "March" the moment a page break landed mid-month.

    So the first day of a page compares against the day BEFORE the window -- one indexed lookup, and only
    on pages after the first. The alternative, stripping duplicate headers in JS after each append, fixes
    it where it is visible rather than where it is decided, and leaves the partial wrong on its own.
    """
    previous = None
    if page > 1:
        offset = (page - 1) * per_page
        previous = (
            _dated(profile).values_list('day', flat=True)
            .distinct().order_by('-day')[offset - 1:offset].first()
        )

    for day in days:
        d = day['day']
        day['month_start'] = previous is None or (d.year, d.month) != (previous.year, previous.month)
        previous = d


def _mosaic_rows(rows):
    """The rows whose covers a mosaic will actually draw: the first few of EACH day.

    Per day, not a slice of the whole page. The rows arrive ordered by day then trophies desc, so a flat
    prefix looks equivalent -- but it only holds if every earlier day has at most MOSAIC_COVERS games. A
    hunter playing ten games a day exhausts the budget within the first third of the page and every day
    after it renders an empty mosaic.
    """
    seen, keep = {}, []
    for r in rows:
        n = seen.get(r['day'], 0)
        if n < MOSAIC_COVERS:
            seen[r['day']] = n + 1
            keep.append(r)
    return keep


def _day_game_rows(profile, page, per_page):
    """One row per (day, game) with just a count -- everything a tile and its mosaic need.

    Two queries rather than one, on purpose. Postgres has no cheap "the Nth page of distinct days" inside
    a group-by, so the first asks only for those days (a DISTINCT over an index range, no row payload) and
    the second bounds the aggregate to them. Bounding by a date RANGE keeps the second query on the same
    index rather than handing it a list of dates to match individually.
    """
    dated = _dated(profile)
    offset = max(page - 1, 0) * per_page

    window = list(
        dated.values_list('day', flat=True).distinct().order_by('-day')[offset:offset + per_page]
    )
    if not window:
        return []

    # A TIMESTAMP range spanning the window, not `day__gte/lte`. Filtering the annotation would put a
    # function of the column in the WHERE and cost the index range -- which is the whole cost model here.
    start, _ = _day_bounds(window[-1])
    _, end = _day_bounds(window[0])

    return list(
        dated.filter(earned_date_time__gte=start, earned_date_time__lt=end)
        .values('day', 'trophy__game_id')
        .annotate(trophies=Count('id'), platinum=Count('id', filter=Q(trophy__trophy_type='platinum')))
        # Trophies desc so a day's biggest games lead its mosaic -- the covers most worth showing.
        .order_by('-day', '-trophies')
    )


def _dated(profile):
    """Earned, dated trophies with their day. The one queryset every tier starts from; it rides
    `earnedtrophy_timeline_idx`."""
    return (
        profile.earned_trophy_entries
        .filter(earned=True, earned_date_time__isnull=False)
        # The annotation NAMES the group. It must not be filtered on -- see the module docstring.
        .annotate(day=TruncDate('earned_date_time', tzinfo=DAY_TZ))
    )


def _by_day(rows, covers):
    """Day tiles: totals, and up to four covers for the mosaic."""
    days = []
    for r in rows:
        if not days or days[-1]['day'] != r['day']:
            days.append({'day': r['day'], 'trophies': 0, 'platinums': 0, 'games': 0, 'covers': []})
        d = days[-1]
        d['trophies'] += r['trophies']
        d['platinums'] += r['platinum']
        d['games'] += 1
        game = covers.get(r['trophy__game_id'])
        if game is not None and len(d['covers']) < MOSAIC_COVERS:
            d['covers'].append(game)
    return days


def day_sessions(profile, day):
    """The sessions of ONE day -- what the day's modal shows.

    Bounded by construction: a day holds as many sessions as games played that day, whatever the size of
    the profile. This is where the per-session detail lives (tier breakdown, span, rarest), because it is
    only worth computing for a day someone actually opened.
    """
    start, end = _day_bounds(day)
    rows = list(
        _dated(profile).filter(earned_date_time__gte=start, earned_date_time__lt=end)
        .values('day', 'trophy__game_id')
        .annotate(
            trophies=Count('id'),
            first_at=Min('earned_date_time'),
            last_at=Max('earned_date_time'),
            # The rarest thing they took, by PSN's own earn rate. `Min` because LOWER is rarer.
            rarest=Min('trophy__trophy_earn_rate'),
            **{t: Count('id', filter=Q(trophy__trophy_type=t)) for t in _TIERS},
        )
        .order_by('-last_at')
    )
    if not rows:
        return []
    games = _games_for(rows)
    return [_session(r, games) for r in rows if r['trophy__game_id'] in games]


def _games_for(rows):
    """The page's games, in ONE query.

    `select_related` down the cover chain with `raw_response` deferred: `Game.display_image_url` resolves
    a trusted IGDB cover first, so without the join every card re-queries Concept and IGDBMatch, and
    without the defer each row hauls the ~30 KB IGDB blob that no cover template reads. The two must
    travel together -- that pairing is a standing rule, and dropping either half caused a real outage.
    """
    from trophies.models import Game

    ids = {r['trophy__game_id'] for r in rows}
    qs = (
        Game.objects.filter(id__in=ids)
        .select_related('concept', 'concept__igdb_match')
        .defer('concept__igdb_match__raw_response')
    )
    return {g.id: g for g in qs}


def _span_label(minutes):
    """First trophy to last, as a phrase. Empty below the threshold where a span means anything."""
    if minutes < 5:
        return ''
    if minutes < 60:
        return f'{minutes}m'
    hours = minutes / 60
    # One decimal below 10 hours ("2.5h" is a session), whole hours above it (nobody reads "13.4h").
    return f'{hours:.1f}h'.replace('.0h', 'h') if hours < 10 else f'{round(hours)}h'


def _session(row, games):
    """One aggregate row as the card's own vocabulary."""
    tiers = [{'tier': t, 'count': row[t]} for t in _TIERS if row[t]]
    span = row['last_at'] - row['first_at'] if row['first_at'] and row['last_at'] else timedelta(0)
    return {
        'day': row['day'],
        'game': games[row['trophy__game_id']],
        'trophies': row['trophies'],
        'tiers': tiers,
        'has_platinum': bool(row['platinum']),
        'first_at': row['first_at'],
        'last_at': row['last_at'],
        # Worded here rather than in the template: the rounding rule belongs with the number it describes,
        # and a "minutes -> 2h / 45m" template filter would be a new shared utility with one caller.
        # Empty under 5 minutes -- a single trophy spans zero, and a run of quick pops is a moment, not a
        # duration worth printing.
        'span_label': _span_label(int(span.total_seconds() // 60)),
        'rarest': row['rarest'],
    }


def attach_day_trophies(profile, day, sessions):
    """Fill each session's `trophies_list` for the STANDALONE day page.

    That page is what a crawler, a shared link and a no-JS visitor get, so its trophies cannot arrive by
    the fetch the modal uses -- they have to be in the HTML. One query for the whole day, grouped in
    Python by game, rather than one per session: a day has few games, but "few" is not a reason to write
    an N+1 into a page built for crawlers.
    """
    from collections import defaultdict

    start, end = _day_bounds(day)
    rows = (
        _dated(profile).filter(earned_date_time__gte=start, earned_date_time__lt=end)
        .select_related('trophy')
        .order_by('earned_date_time')
    )
    by_game = defaultdict(list)
    for et in rows:
        by_game[et.trophy.game_id].append(et)

    for s in sessions:
        s['trophies_list'] = by_game.get(s['game'].id, [])
    return sessions
