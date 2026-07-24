"""Per-game leaderboard: ranking, keyset pagination, rank lookup, and view options.

Canonical ranking is `progress DESC, most_recent_trophy_date ASC (NULLS LAST), profile_id ASC`, backed by
`pg_game_leaderboard_idx`. Completers sort to the top ordered by WHEN they finished, then everyone else by
how close they are -- so a game's board reads as a race rather than a snapshot.

The third sort key is load-bearing, not decoration. Ties on the first two are the normal case (everyone at
100% shares progress=100), and without a unique final key Postgres may order tied rows differently between
calls, which makes pagination skip or duplicate players and makes a displayed rank flicker.

KEYSET, not OFFSET, for the main scroll. `OFFSET n` degrades linearly with depth; a cursor stays flat. Jump
targets (to a typed rank, or to the viewer) DO use a bounded offset -- fine because a single game's board is
small (biggest on beta ~1,400), so a deep offset is still single-digit ms.

VIEW OPTIONS (BoardOptions):
  - invert: show the board bottom-first. Served by scanning the SAME index BACKWARD -- no extra cost. Rank
    NUMBERS stay canonical (from the top / best); inverting only reverses the display, so ranks count down.
  - only_earners (default ON): drop 0%/zero-trophy owners. They sit at the bottom of the index, so excluding
    them just ends the scan earlier -- free, often faster.
  - registered_only: only profiles with a linked site account (Profile.user is set). A post-join filter, not
    index-served, but negligible at board scale.

Filters change the POPULATION, so rank / board_size / paging all apply them consistently: a rank is always
"position within the currently-viewed board", which is what a viewer toggling a filter expects.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone

from django.db.models import Q, F

from trophies.models import ProfileGame

logger = logging.getLogger('psn_api')

PAGE_SIZE = 25

# Mirrors pg_game_leaderboard_idx field-for-field; asserted equal in the tests. INVERTED is the exact
# reverse (Postgres serves it by scanning the same index backward, nulls flipping LAST<->FIRST).
ORDER_BY = ('-progress', F('most_recent_trophy_date').asc(nulls_last=True), 'profile_id')
INVERTED_ORDER = ('progress', F('most_recent_trophy_date').desc(nulls_first=True), '-profile_id')

_NULL = 'n'          # cursor marker for "no trophy date" (a 0% owner)
# NOT '.' -- the timestamp is a float, so a dot separator splits it in half. '~' is URL-unreserved and
# cannot appear in any of the three parts.
_SEP = '~'


@dataclass(frozen=True)
class BoardOptions:
    """The viewer's board controls. `only_earners` defaults ON -- the common board is people who've
    actually started, not every owner."""
    invert: bool = False
    only_earners: bool = True
    registered_only: bool = False

    @classmethod
    def from_request(cls, request):
        get = request.GET.get
        return cls(
            invert=get('invert') == '1',
            only_earners=get('earners', '1') != '0',   # default on; ?earners=0 shows all owners
            registered_only=get('registered') == '1',
        )

    def as_params(self):
        """The non-default flags, for building continuation/jump URLs that preserve the view."""
        params = {}
        if self.invert:
            params['invert'] = '1'
        if not self.only_earners:
            params['earners'] = '0'
        if self.registered_only:
            params['registered'] = '1'
        return params


def _base_qs(game, opts):
    """The filtered population for `game`'s board, WITHOUT ordering (rank/count use forward order; paging
    uses display order). Scope is everyone who owns the game minus hidden rows, then the opt filters."""
    qs = ProfileGame.objects.filter(game=game, hidden_flag=False, user_hidden=False)
    if opts.only_earners:
        qs = qs.filter(progress__gt=0)
    if opts.registered_only:
        qs = qs.filter(profile__user__isnull=False)
    return qs


def board_queryset(game, opts):
    """The board in DISPLAY order (respects invert). Used for paging and jump windows."""
    return _base_qs(game, opts).order_by(*(INVERTED_ORDER if opts.invert else ORDER_BY))


def board_size(game, opts):
    """Players on the currently-filtered board. NOT Game.played_count (that counts hidden rows AND
    ignores the filters, so it would disagree with the list)."""
    return _base_qs(game, opts).count()


# ── cursors ──────────────────────────────────────────────────────────────────

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


def _after(cursor, opts):
    """Q matching rows strictly AFTER the cursor in the current DISPLAY order (forward or inverted).

    Mirrors the ORDER BY exactly, including the null placement, which flips under inversion: NULLS LAST
    forward (0% no-date owners trail) becomes NULLS FIRST inverted (they lead). Only reachable with
    only_earners OFF, since earners always have a date.
    """
    progress, stamp, profile_id = cursor
    moment = None if stamp is None else datetime.fromtimestamp(stamp, tz=dt_timezone.utc)

    if not opts.invert:
        if moment is None:
            same = Q(progress=progress, most_recent_trophy_date__isnull=True, profile_id__gt=profile_id)
        else:
            same = Q(progress=progress) & (
                Q(most_recent_trophy_date__gt=moment)
                | Q(most_recent_trophy_date__isnull=True)          # nulls trail every real date
                | Q(most_recent_trophy_date=moment, profile_id__gt=profile_id)
            )
        return Q(progress__lt=progress) | same

    # Inverted: progress ascending, dates descending with nulls first, ids descending.
    if moment is None:
        same = Q(progress=progress) & (
            Q(most_recent_trophy_date__isnull=False)               # non-nulls follow the null head
            | Q(most_recent_trophy_date__isnull=True, profile_id__lt=profile_id)
        )
    else:
        same = Q(progress=progress) & (
            Q(most_recent_trophy_date__lt=moment)
            | Q(most_recent_trophy_date=moment, profile_id__lt=profile_id)
        )
    return Q(progress__gt=progress) | same


# ── pages ────────────────────────────────────────────────────────────────────

def page(game, opts, cursor=None, limit=PAGE_SIZE):
    """One page of the board plus the cursor for the next.

    Returns (rows, next_cursor). next_cursor is None on the last page. Fetches limit+1 rows so
    "is there more" costs nothing extra -- no COUNT, no OFFSET.
    """
    qs = board_queryset(game, opts).select_related('profile')
    decoded = decode_cursor(cursor)
    if decoded:
        qs = qs.filter(_after(decoded, opts))

    rows = list(qs[:limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    return rows, (encode_cursor(rows[-1]) if has_more and rows else None)


def page_at_rank(game, opts, rank, before=4, limit=PAGE_SIZE):
    """A window of the board centred on a canonical `rank` (for "jump to my rank" and typed jumps).

    Returns (rows, next_cursor, start_rank, total), or None if the board is empty. The window opens a few
    places above the target so you see who you're chasing. `rank` is canonical (from the top); under
    invert it's mapped to the matching display position. Bounded OFFSET -- fine at board scale.

    `start_rank` is the canonical rank of the FIRST returned row; the caller numbers rows from it with a
    step of +1 (forward) or -1 (inverted). next_cursor continues the scroll via keyset from the last row.
    """
    total = board_size(game, opts)
    if total == 0:
        return None
    rank = max(1, min(rank, total))

    # Canonical rank -> position in the current display order.
    display_pos = rank if not opts.invert else (total - rank + 1)
    start_display = max(1, display_pos - before)

    qs = board_queryset(game, opts).select_related('profile')
    rows = list(qs[start_display - 1: start_display - 1 + limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1]) if has_more and rows else None

    start_rank = start_display if not opts.invert else (total - start_display + 1)
    return rows, next_cursor, start_rank, total


# ── rank ─────────────────────────────────────────────────────────────────────

def _board_row(game, profile, opts):
    """The profile's own row on the FILTERED board, or None if absent (doesn't own it, hidden, or
    filtered out -- e.g. a 0-trophy viewer when only_earners is on)."""
    if not profile:
        return None
    return (
        _base_qs(game, opts)
        .filter(profile=profile)
        .only('progress', 'most_recent_trophy_date', 'profile_id')
        .first()
    )


def _ahead_of(row):
    """Q matching everyone ranked strictly above `row` in canonical (forward) order."""
    ahead = Q(progress__gt=row.progress)
    if row.most_recent_trophy_date is None:
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


def rank_for(game, profile, opts):
    """1-indexed CANONICAL rank (from the top / best), or None if the profile isn't on this board.

    Canonical regardless of invert -- "You're #42" means 42nd best. Respects the filters, so it's the
    rank within the currently-viewed population. O(rank), bounded by one game's players.
    """
    row = _board_row(game, profile, opts)
    if row is None:
        return None
    return _base_qs(game, opts).filter(_ahead_of(row)).count() + 1
