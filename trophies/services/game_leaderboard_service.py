"""Per-game leaderboard: ranking, windowed reads, rank lookup, and view options.

Canonical ranking is `progress DESC, most_recent_trophy_date ASC (NULLS LAST), profile_id ASC`, backed by
`pg_game_leaderboard_idx`. Completers sort to the top ordered by WHEN they finished, then everyone else by
how close they are -- so a game's board reads as a race rather than a snapshot.

The third sort key is load-bearing, not decoration. Ties on the first two are the normal case (everyone at
100% shares progress=100), and without a unique final key Postgres may order tied rows differently between
calls, so a row's rank -- its position in this order -- would flicker between reads.

The client renders the board VIRTUALIZED (a full-height spacer, only the visible ~30 rows in the DOM), so
it reads rows by rank RANGE (`page_range`) rather than by cursor. Plain OFFSET is fine here: a single game's
board is small (biggest on beta ~1,400), so even the deepest window is single-digit ms on the index.

VIEW OPTIONS (BoardOptions):
  - invert: show the board bottom-first. Served by scanning the SAME index BACKWARD -- no extra cost.
  - only_earners (default ON): drop 0%/zero-trophy owners. They sit at the bottom of the index, so excluding
    them just ends the scan earlier -- free, often faster.
  - registered_only: only profiles with a linked site account (Profile.user is set). A post-join filter,
    not index-served, but negligible at board scale.

Filters change the POPULATION, so rank / board_size / windows all apply them consistently: a rank is always
"position within the currently-viewed board", which is what a viewer toggling a filter expects.
"""
import logging
from dataclasses import dataclass

from django.db.models import Q, F

from trophies.models import ProfileGame

logger = logging.getLogger('psn_api')

PAGE_SIZE = 50          # rows per fetched window (the client asks for ranges this size)

# Mirrors pg_game_leaderboard_idx field-for-field; asserted equal in the tests. INVERTED is the exact
# reverse (Postgres serves it by scanning the same index backward, nulls flipping LAST<->FIRST).
ORDER_BY = ('-progress', F('most_recent_trophy_date').asc(nulls_last=True), 'profile_id')
INVERTED_ORDER = ('progress', F('most_recent_trophy_date').desc(nulls_first=True), '-profile_id')


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
    """The filtered population for `game`'s board, WITHOUT ordering. Scope is everyone who owns the game
    minus hidden rows, then the opt filters."""
    qs = ProfileGame.objects.filter(game=game, hidden_flag=False, user_hidden=False)
    if opts.only_earners:
        qs = qs.filter(progress__gt=0)
    if opts.registered_only:
        qs = qs.filter(profile__user__isnull=False)
    return qs


def board_queryset(game, opts):
    """The board in DISPLAY order (respects invert)."""
    return _base_qs(game, opts).order_by(*(INVERTED_ORDER if opts.invert else ORDER_BY))


def board_size(game, opts):
    """Players on the currently-filtered board -- the client sizes the virtual spacer from this. NOT
    Game.played_count (that counts hidden rows AND ignores the filters, so it would disagree with the list)."""
    return _base_qs(game, opts).count()


def page_range(game, opts, start, count=PAGE_SIZE):
    """Rows at 1-indexed display ranks [start, start+count), for the virtualized list.

    Bounded OFFSET slice -- fine at board scale, and the index serves the ordering directly. Returns model
    instances (select_related profile) in display order; the caller numbers them start, start+1, ...
    """
    start = max(1, start)
    count = max(1, min(count, 500))
    return list(board_queryset(game, opts).select_related('profile')[start - 1: start - 1 + count])


# ── rank ─────────────────────────────────────────────────────────────────────

def _board_row(game, profile, opts):
    """The profile's own row on the FILTERED board, or None if absent (doesn't own it, hidden, or filtered
    out -- e.g. a 0-trophy viewer when only_earners is on)."""
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


def _rank_of_row(game, opts, row):
    """Canonical rank of a row we already hold (no re-fetch) -- count everyone ahead of it, +1."""
    return _base_qs(game, opts).filter(_ahead_of(row)).count() + 1


def rank_for(game, profile, opts):
    """1-indexed CANONICAL rank (from the top / best), or None if the profile isn't on this board.

    Canonical regardless of invert -- "You're #42" means 42nd best. Respects the filters, so it's the rank
    within the currently-viewed population. The client converts it to a display position (total - rank + 1
    when inverted) to place the row in the virtual list. O(rank), bounded by one game's players.
    """
    row = _board_row(game, profile, opts)
    if row is None:
        return None
    return _rank_of_row(game, opts, row)


def suggest(game, opts, query, limit=8):
    """Board players whose PSN name matches `query`, each with its rank -- for the search typeahead.

    Scoped to the filtered board, so a hidden/filtered-out player never appears. Ranks are computed per
    match (a bounded count each), fine at typeahead limits. Returns [] below 2 chars.
    """
    q = (query or '').strip()
    if len(q) < 2:
        return []
    matches = list(
        board_queryset(game, opts)
        .filter(Q(profile__psn_username__icontains=q) | Q(profile__display_psn_username__icontains=q))
        .select_related('profile')[:limit]
    )
    return [{'profile': row.profile, 'progress': row.progress, 'rank': _rank_of_row(game, opts, row)}
            for row in matches]
