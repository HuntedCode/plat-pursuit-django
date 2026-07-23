"""Per-game leaderboard: ranking, keyset pagination, and rank lookup.

Ranking is `progress DESC, most_recent_trophy_date ASC (NULLS LAST), profile_id ASC`, backed by
`pg_game_leaderboard_idx`. Completers sort to the top ordered by WHEN they finished, then everyone else
by how close they are -- so a game's board reads as a race rather than a snapshot.

The third sort key is load-bearing, not decoration. Ties on the first two are the normal case (everyone
at 100% shares progress=100), and without a unique final key Postgres may order tied rows differently
between calls, which makes pagination skip or duplicate players and makes a displayed rank flicker.

KEYSET, not OFFSET. `OFFSET n` degrades linearly with depth and is a breaking change to swap later once
clients depend on the parameter shape; a cursor stays flat at any depth. Measured on beta: a top-20 page
is 0.6 ms and a rank lookup 1,400 deep is 1.4 ms, so this needs no cache today. If a game ever gets deep
enough to matter, `rank_for` is the one call to memoise.
"""
import logging

from django.db.models import Q, F

from trophies.models import ProfileGame

logger = logging.getLogger('psn_api')

PAGE_SIZE = 25

# Mirrors pg_game_leaderboard_idx field-for-field. Changing one without the other silently costs the
# index scan, so they are asserted equal in the tests.
ORDER_BY = ('-progress', F('most_recent_trophy_date').asc(nulls_last=True), 'profile_id')

_NULL = 'n'          # cursor marker for "no trophy date" (a 0% owner)
# NOT '.' -- the timestamp is a float, so a dot separator splits it in half. '~' is URL-unreserved and
# cannot appear in any of the three parts.
_SEP = '~'


def board_queryset(game):
    """Every player eligible for `game`'s board, in rank order.

    Scope is everyone who OWNS the game, 0% included, so every viewer can find themselves and
    "jump to my rank" always has somewhere to go. Hidden rows are excluded, which is why the header
    count comes from here rather than from the denormalized Game.played_count (that counts them).
    """
    return (
        ProfileGame.objects
        .filter(game=game, hidden_flag=False, user_hidden=False)
        .order_by(*ORDER_BY)
    )


def encode_cursor(row):
    """Opaque-ish cursor naming a row's exact position in the ordering."""
    stamp = _NULL if row.most_recent_trophy_date is None else f'{row.most_recent_trophy_date.timestamp():.6f}'
    return f'{row.progress}{_SEP}{stamp}{_SEP}{row.profile_id}'


def decode_cursor(raw):
    """Parse a cursor into (progress, timestamp_or_None, profile_id), or None if malformed.

    Cursors arrive from the query string, so anything unparseable is treated as "start from the top"
    rather than raising -- a mangled URL should show page one, not a 500.
    """
    if not raw:
        return None
    try:
        progress, stamp, profile_id = raw.split(_SEP)
        return (
            int(progress),
            None if stamp == _NULL else float(stamp),
            int(profile_id),
        )
    except (ValueError, AttributeError):
        logger.warning('Discarding malformed leaderboard cursor: %r', raw)
        return None


def _after(cursor):
    """Q matching rows strictly AFTER the cursor position, in ranking order.

    Mirrors the ORDER BY exactly, including NULLS LAST: a null date sorts after every real date, so a
    row with no trophies is only ever preceded by dated rows at the same progress.
    """
    progress, stamp, profile_id = cursor
    from datetime import datetime, timezone as dt_timezone

    if stamp is None:
        # Cursor sits in the null-date tail; only higher profile_ids in that same tail follow it.
        same = Q(progress=progress, most_recent_trophy_date__isnull=True, profile_id__gt=profile_id)
    else:
        moment = datetime.fromtimestamp(stamp, tz=dt_timezone.utc)
        same = Q(progress=progress) & (
            Q(most_recent_trophy_date__gt=moment)
            | Q(most_recent_trophy_date__isnull=True)          # nulls trail every real date
            | Q(most_recent_trophy_date=moment, profile_id__gt=profile_id)
        )
    return Q(progress__lt=progress) | same


def page(game, cursor=None, limit=PAGE_SIZE):
    """One page of the board plus the cursor for the next.

    Returns (rows, next_cursor). next_cursor is None on the last page. Fetches limit+1 rows so
    "is there more" costs nothing extra -- no COUNT, no OFFSET.
    """
    qs = board_queryset(game).select_related('profile')
    decoded = decode_cursor(cursor)
    if decoded:
        qs = qs.filter(_after(decoded))

    rows = list(qs[:limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    return rows, (encode_cursor(rows[-1]) if has_more and rows else None)


def page_around(game, profile, before=4, limit=PAGE_SIZE):
    """A window of the board centred on `profile`, for "jump to my rank".

    Returns (rows, next_cursor, start_rank), or None if they aren't on the board.

    Loading forward page by page until we reach someone ranked 900th would be absurd, so instead we
    step back a few places from them and start a normal keyset page there. Two cheap queries: find the
    row `before + 1` places ahead, then page from its cursor.
    """
    row = _board_row(game, profile)
    if row is None:
        return None
    ahead_q = _ahead_of(row)
    rank = board_queryset(game).filter(ahead_q).count() + 1

    # Walk backwards from the viewer: the board order reversed, restricted to those ahead of them.
    step_back = min(before, rank - 1)
    anchor_cursor, start_rank = None, rank - step_back
    if step_back:
        ahead = (
            board_queryset(game)
            .filter(ahead_q)
            .order_by('progress', F('most_recent_trophy_date').desc(nulls_first=True), '-profile_id')
        )
        anchor = list(ahead[:step_back + 1])
        # The (step_back + 1)-th row ahead is the one we page AFTER, so the window opens on the row
        # exactly `step_back` places above them. Fewer than that means we ran into the top of the board.
        if len(anchor) == step_back + 1:
            anchor_cursor = encode_cursor(anchor[-1])
        else:
            start_rank = 1

    rows, next_cursor = page(game, cursor=anchor_cursor, limit=limit)
    return rows, next_cursor, start_rank


def _board_row(game, profile):
    """The profile's own row on this board, or None if they don't own the game / are hidden."""
    if not profile:
        return None
    return (
        ProfileGame.objects
        .filter(game=game, profile=profile, hidden_flag=False, user_hidden=False)
        .only('progress', 'most_recent_trophy_date', 'profile_id')
        .first()
    )


def _ahead_of(row):
    """Q matching everyone ranked strictly above `row` on its board."""
    ahead = Q(progress__gt=row.progress)
    if row.most_recent_trophy_date is None:
        # Every dated row at this progress leads an undated one, then lower ids within the tail.
        ahead |= Q(progress=row.progress) & (
            Q(most_recent_trophy_date__isnull=False)
            | Q(most_recent_trophy_date__isnull=True, profile_id__lt=row.profile_id)
        )
    else:
        ahead |= Q(progress=row.progress) & (
            Q(most_recent_trophy_date__lt=row.most_recent_trophy_date)
            | Q(most_recent_trophy_date=row.most_recent_trophy_date, profile_id__lt=row.profile_id)
        )
    return ahead


def rank_for(game, profile):
    """1-indexed rank, or None if the profile isn't on this board.

    Counts everyone ahead, so it is O(rank). Bounded by the players of ONE game (biggest board on beta
    is ~1,400), which is why this is 1.4 ms rather than the tens of ms a global leaderboard would cost.
    """
    row = _board_row(game, profile)
    if row is None:
        return None
    return board_queryset(game).filter(_ahead_of(row)).count() + 1


def board_size(game):
    """Players actually on the board.

    NOT Game.played_count: that counts hidden rows too, so the header and the list would disagree.
    Cheap at this scale (the index makes it a counted range scan).
    """
    return board_queryset(game).count()
