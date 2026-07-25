"""Per-game leaderboards: one windowing/rank/suggest engine, several boards.

A **Board** is a filtered population + a TOTAL ordering. The engine below (windowed reads, rank lookup,
suggest, size) is written ONCE against that interface; each board subclass supplies only its queryset and
its sort keys. Boards:

  - EverythingBoard   -- ProfileGame, progress across ALL of a game's trophies (the original overall board)
  - GroupProgressBoard-- ProfileTrophyGroup, completion WITHIN one trophy group (base game or a DLC)
  - GroupSpeedBoard   -- ProfileTrophyGroup, fastest first->last completion of a group (the "Fastest Platinum"
                         race for the default group). Only fully-completed, >=2-trophy groups qualify.
  - PlaytimeBoard     -- ProfileGame, most PSN-reported play time (whole game)

Every board's canonical order ends in `profile_id`, a UNIQUE final key that makes the order TOTAL. That is
load-bearing, not decoration: ties on the earlier keys are the normal case (everyone at 100% shares
progress; identical completion_seconds happen), and without a unique tail Postgres may order tied rows
differently between calls, so a row's rank would flicker and adjacent virtual windows would skip/duplicate.

Each board is backed by an index that serves its ORDER BY directly (pg_game_leaderboard_idx, ptg_progress_idx,
ptg_speed_idx, pg_playtime_idx), so windowed reads are single-digit ms. Plain OFFSET is fine at board scale;
the millions-of-players ceilings and their fixes are documented in docs/features/game-leaderboards.md.

VIEW OPTIONS (BoardOptions): invert (bottom-first, same index scanned backward), only_earners (drop 0%
rows -- progress boards only), registered_only (linked site accounts only). Filters change the POPULATION,
so rank / size / windows all apply them consistently.
"""
import logging
from dataclasses import dataclass

from django.db.models import Q, F, Count

from trophies.models import ProfileGame, ProfileTrophyGroup, Trophy, TrophyGroup

logger = logging.getLogger('psn_api')

PAGE_SIZE = 50          # rows per fetched window (the client asks for ranges this size)


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


@dataclass(frozen=True)
class SortKey:
    """One key in a board's total ordering. `nulls_last` marks a nullable key whose FORWARD order is
    ascending-nulls-last (our only nullable pattern: the tiebreak timestamps). Its inverted order is the
    exact reverse (descending-nulls-first)."""
    field: str
    desc: bool
    nulls_last: bool = False

    def order_expr(self, invert):
        if self.nulls_last:                                   # nullable tiebreak: asc-nulls-last forward
            f = F(self.field)
            return f.desc(nulls_first=True) if invert else f.asc(nulls_last=True)
        descending = self.desc ^ invert
        return ('-' if descending else '') + self.field       # plain string; the index serves it

    def better(self, value):
        """Q for 'this row's field ranks strictly ahead of `value` on this key alone' (canonical order)."""
        if self.nulls_last and value is None:
            return Q(**{f'{self.field}__isnull': False})      # any non-null beats a null (nulls sort last)
        if self.nulls_last:
            return Q(**{f'{self.field}__lt': value})          # nullable tiebreaks are ascending
        op = 'gt' if self.desc else 'lt'
        return Q(**{f'{self.field}__{op}': value})

    def tied(self, value):
        if value is None:
            return Q(**{f'{self.field}__isnull': True})
        return Q(**{self.field: value})


class Board:
    """A single leaderboard. Subclasses set KEYS (canonical order, unique final key) and _population()."""

    KEYS = ()
    kind = 'progress'          # how the row renders: 'progress' | 'speed' | 'playtime'

    def __init__(self, game, opts):
        self.game = game
        self.opts = opts

    # -- subclass hooks --------------------------------------------------

    def _population(self):
        """The filtered, UNORDERED queryset for this board (model + population filters)."""
        raise NotImplementedError

    # -- ordering --------------------------------------------------------

    def _order(self, invert):
        return tuple(k.order_expr(invert) for k in self.KEYS)

    def ordered(self):
        """The board in DISPLAY order (respects invert)."""
        return self._population().order_by(*self._order(self.opts.invert))

    # -- reads -----------------------------------------------------------

    def size(self):
        """Players on the currently-filtered board -- the client sizes the virtual spacer from this."""
        return self._population().count()

    def page_range(self, start, count=PAGE_SIZE):
        """Rows at 1-indexed display ranks [start, start+count). Bounded OFFSET slice; the index serves the
        ordering. Returns model instances (select_related profile) in display order; caller numbers them."""
        start = max(1, start)
        count = max(1, min(count, 500))
        return list(self.ordered().select_related('profile')[start - 1: start - 1 + count])

    def row_at_rank(self, rank):
        """The row at 1-indexed CANONICAL rank (from the best), or None past the board. Forward order, always
        (the number a viewer types is the rank shown beside a row, counted from the top regardless of invert).
        The rank is the fetch offset, so pass it straight through -- no COUNT, unlike the name suggest."""
        rank = max(1, rank)
        row = self._population().order_by(*self._order(invert=False)).select_related('profile')[rank - 1: rank].first()
        return self._suggestion(row, rank) if row else None

    def rank_for(self, profile):
        """1-indexed CANONICAL rank of `profile` on this board, or None if absent. O(rank), bounded by one
        game's players."""
        row = self._board_row(profile)
        return None if row is None else self._rank_of_row(row)

    def suggest(self, query, limit=8):
        """Board players whose PSN name matches `query`, each with its rank -- the search typeahead. Scoped
        to the filtered board, so a hidden/filtered-out player never appears. Returns [] below 2 chars."""
        q = (query or '').strip()
        if len(q) < 2:
            return []
        matches = list(
            self.ordered()
            .filter(Q(profile__psn_username__icontains=q) | Q(profile__display_psn_username__icontains=q))
            .select_related('profile')[:limit]
        )
        return [self._suggestion(row) for row in matches]

    # -- internals -------------------------------------------------------

    def _board_row(self, profile):
        if not profile:
            return None
        return (
            self._population()
            .filter(profile=profile)
            .only(*[k.field for k in self.KEYS])
            .first()
        )

    def _ahead_of(self, row):
        """Q matching everyone ranked strictly ahead of `row` in canonical order, built from KEYS: ahead at
        key i means tied on keys 0..i-1 and strictly better at key i. The unique final key closes it off."""
        result = Q(pk__in=[])                                 # matches nothing; OR terms accumulate
        tie = None
        for key in self.KEYS:
            value = getattr(row, key.field)
            clause = key.better(value) if tie is None else (tie & key.better(value))
            result = result | clause
            eq = key.tied(value)
            tie = eq if tie is None else (tie & eq)
        return result

    def _rank_of_row(self, row):
        return self._population().filter(self._ahead_of(row)).count() + 1

    def _suggestion(self, row, rank=None):
        """The dict shape the view serializes for the typeahead / rank preview. `rank` is passed when the
        caller already knows it (row_at_rank); the name suggest omits it, so it's counted."""
        return {
            'profile': row.profile,
            'progress': getattr(row, 'progress', None),
            'rank': rank if rank is not None else self._rank_of_row(row),
        }


# ── board types ──────────────────────────────────────────────────────────────

class EverythingBoard(Board):
    """Overall completion across ALL of a game's trophies (ProfileGame). The original board; for a
    single-group game this IS the default-group board."""
    KEYS = (
        SortKey('progress', desc=True),
        SortKey('most_recent_trophy_date', desc=False, nulls_last=True),
        SortKey('profile_id', desc=False),
    )

    def _population(self):
        qs = ProfileGame.objects.filter(game=self.game, hidden_flag=False, user_hidden=False)
        if self.opts.only_earners:
            qs = qs.filter(progress__gt=0)
        if self.opts.registered_only:
            qs = qs.filter(profile__user__isnull=False)
        return qs


class PlaytimeBoard(Board):
    """Most PSN-reported play time for the whole game (ProfileGame). Partial-index population: only rows with
    a reported duration. only_earners does not apply (it is playtime, not completion)."""
    kind = 'playtime'
    KEYS = (
        SortKey('play_duration', desc=True),
        SortKey('profile_id', desc=False),
    )

    def _population(self):
        qs = ProfileGame.objects.filter(
            game=self.game, hidden_flag=False, user_hidden=False, play_duration__isnull=False
        )
        if self.opts.registered_only:
            qs = qs.filter(profile__user__isnull=False)
        return qs


class _GroupBoard(Board):
    """Shared base for the per-trophy-group boards (ProfileTrophyGroup). Hidden games are filtered at read
    time against ProfileGame -- a rare, tiny anti-join -- rather than denormed onto the standings row."""

    def __init__(self, game, group, opts):
        super().__init__(game, opts)
        self.group = group

    def _group_qs(self):
        hidden = (
            ProfileGame.objects.filter(game=self.game)
            .filter(Q(hidden_flag=True) | Q(user_hidden=True))
            .values('profile_id')
        )
        return ProfileTrophyGroup.objects.filter(trophy_group=self.group).exclude(profile_id__in=hidden)


class GroupProgressBoard(_GroupBoard):
    """Completion within one trophy group. Ties broken by who reached their standing first."""
    KEYS = (
        SortKey('progress', desc=True),
        SortKey('last_trophy_at', desc=False, nulls_last=True),
        SortKey('profile_id', desc=False),
    )

    def _population(self):
        qs = self._group_qs()
        if self.opts.only_earners:
            qs = qs.filter(progress__gt=0)                    # hide the sub-1% who've barely started
        if self.opts.registered_only:
            qs = qs.filter(profile__user__isnull=False)
        return qs


class GroupSpeedBoard(_GroupBoard):
    """Fastest first->last completion of a group. Population is the partial speed index: only rows with a
    completion_seconds (fully earned, >=2-trophy groups). only_earners does not apply (all are complete)."""
    kind = 'speed'
    KEYS = (
        SortKey('completion_seconds', desc=False),
        SortKey('last_trophy_at', desc=False),
        SortKey('profile_id', desc=False),
    )

    def _population(self):
        qs = self._group_qs().filter(completion_seconds__isnull=False)
        if self.opts.registered_only:
            qs = qs.filter(profile__user__isnull=False)
        return qs


# ── board resolution ─────────────────────────────────────────────────────────

def group_for(game, group_id):
    """The game's TrophyGroup with this trophy_group_id ('default', '001', ...), or None."""
    return TrophyGroup.objects.filter(game=game, trophy_group_id=group_id).first()


def resolve_board(game, param, opts):
    """Map a `?board=` value to a Board. Recognized: 'progress:all' (Everything), 'progress:<gid>',
    'speed:<gid>', 'playtime'. Anything missing or unrecognized falls back to the Everything board, so a
    stale/hand-typed value degrades gracefully rather than erroring."""
    param = (param or '').strip()
    if param == 'playtime':
        return PlaytimeBoard(game, opts)
    if ':' in param:
        kind, gid = param.split(':', 1)
        if kind == 'progress' and gid == 'all':
            return EverythingBoard(game, opts)
        group = group_for(game, gid)
        if group is not None:
            if kind == 'speed':
                return GroupSpeedBoard(game, group, opts)
            if kind == 'progress':
                return GroupProgressBoard(game, group, opts)
    return EverythingBoard(game, opts)


def active_parts(param):
    """Split a board param into (mode, group_id) for marking the selector's active chips. The default /
    Everything board is ('progress', 'all'); playtime has no group."""
    p = (param or '').strip() or 'progress:all'
    if p == 'playtime':
        return 'playtime', None
    if ':' in p:
        mode, gid = p.split(':', 1)
        return mode, gid
    return 'progress', 'all'


def board_menu(game, active_param):
    """The boards available for `game`, for the selector. Cheap (the panel is lazy-loaded): a couple of
    small aggregates. Returns the mode chips (Standings / Fastest / Most Played -- each present only if it
    has a board), the trophy groups (with which qualify for a speed board), and the active mode/group.

    A group qualifies for a speed board only with >=2 trophies: a one-trophy group is completed instantly,
    so its speed board would just duplicate the progress board's first-earners race.
    """
    counts = {
        r['trophy_group_id']: r['n']
        for r in Trophy.objects.filter(game=game).values('trophy_group_id').annotate(n=Count('id'))
    }
    groups = []
    for i, tg in enumerate(game.trophy_groups.order_by('trophy_group_id')):
        gid = tg.trophy_group_id
        groups.append({
            'id': gid,
            'label': 'Base Game' if gid == 'default' else (tg.trophy_group_name or f'DLC {i}'),
            'speed': counts.get(gid, 0) >= 2,
        })

    # Standings lands on the base game for a game with DLC (the platinum race, and the tab default); the
    # overall board otherwise. Everything stays reachable from the group row.
    multi = len(groups) > 1
    has_default = any(g['id'] == 'default' for g in groups)
    standings_param = 'progress:default' if (multi and has_default) else 'progress:all'
    modes = [{'key': 'progress', 'label': 'Standings', 'param': standings_param}]
    first_speed = next((g['id'] for g in groups if g['speed']), None)
    if first_speed is not None:
        modes.append({'key': 'speed', 'label': 'Fastest', 'param': f'speed:{first_speed}'})
    has_playtime = ProfileGame.objects.filter(
        game=game, hidden_flag=False, user_hidden=False, play_duration__isnull=False
    ).exists()
    if has_playtime:
        modes.append({'key': 'playtime', 'label': 'Most Played', 'param': 'playtime'})

    active_mode, active_group = active_parts(active_param)

    # The group row: Base Game and Everything stay as pills, but the DLCs collapse into a dropdown (some
    # games have a lot of DLC). Filter to the active mode's eligible groups -- speed shows only >=2-trophy
    # groups, and has no Everything.
    row_groups = [g for g in groups if g['speed']] if active_mode == 'speed' else groups
    base = next((g for g in row_groups if g['id'] == 'default'), None)
    dlcs = [g for g in row_groups if g['id'] != 'default']
    active_dlc = next((g for g in dlcs if g['id'] == active_group), None)

    # A short title for the active board, for context (the header + the desktop minibar). The DLC dropdown
    # button stays a static "DLC", so this is where the specific DLC name surfaces.
    if active_mode == 'playtime':
        title = 'Most Played'
    else:
        if active_group == 'default':
            scope = 'Base Game'
        elif active_dlc:
            scope = active_dlc['label']
        else:
            scope = 'Overall'
        title = f'Fastest: {scope}' if active_mode == 'speed' else f'{scope} Standings'

    return {
        'modes': modes,
        'groups': groups,
        'base': base,
        'dlcs': dlcs,
        'active_dlc': active_dlc,
        'title': title,
        'multi': multi,
        'active_mode': active_mode,
        'active_group': active_group,
    }


def default_board_param(game):
    """The board a fresh Ranks tab lands on: base-game standings for a game with DLC (the platinum race),
    else the overall board (which, for a single-group game, IS the whole game)."""
    ids = list(game.trophy_groups.values_list('trophy_group_id', flat=True))
    if len(ids) > 1 and 'default' in ids:
        return 'progress:default'
    return 'progress:all'
