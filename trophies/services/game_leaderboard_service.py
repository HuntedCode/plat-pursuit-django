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
ptg_speed_idx, pg_playtime_idx), so windowed reads are single-digit ms. The `is_linked` gate below adds a
Profile join those indexes do not cover; it is a probe per returned row on a windowed read (bounded), and
a full pass on `countries()` (which is why that one is cached). Plain OFFSET is fine at board scale;
the millions-of-players ceilings and their fixes are documented in docs/features/game-leaderboards.md.

POPULATION: VERIFIED hunters only, always. Every other board on the site is gated on `is_linked`
(`badge_leaderboards._linked`) and this one was not -- it ranked every scraped PSN profile, so the same
site showed one board of ~300,000 profiles beside five of the ~50,000 people who actually claimed one.
It WAS an opt-in "registered only" toggle, which made the consistent behaviour the one you had to ask
for. Note the old toggle tested `profile__user__isnull=False`; the rule is `is_linked`, which is what the
other boards mean by a hunter.

VIEW OPTIONS (BoardOptions): only_earners (drop 0% rows -- progress boards only), country (one country's
slice, like every other board). Filters change the POPULATION, so rank / size / windows all apply them
consistently.

THE PROFILE JOIN is what `is_linked` costs, and country then rides it for free. The other five boards
denormalize both onto their standing rows so they never join Profile -- that is not available here:
ProfileGame is one row per (profile, game), so a mirror column would be a migration across the largest
table in the system to save a join on a set already bounded by one game's owners.

`invert` (bottom-first) and `registered_only` were options and are GONE. Invert existed only here, so it
was the last control that made this board behave unlike the other three, and a board that can be read
upside down needs two answers for every ordering question -- display order vs canonical rank, `from` vs
`start`, nulls-first vs nulls-last. One ordering, always forward.
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
    only_earners: bool = True
    country: str = ''

    @classmethod
    def from_request(cls, request, codes=(), unvalidated_country=None):
        """`codes` are the countries that actually have hunters on the board being built -- the PANEL
        path, where an option the picker offers must never empty the board.

        `unvalidated_country` is the WINDOW path, which cannot afford that check: resolving the codes is a
        DISTINCT over the whole population and the virtualizer asks for one window per screenful, so doing
        it there turned every scroll step into a full scan. The client only echoes back the slice the
        panel rendered, so it is validated by construction, and a crafted code selects nobody. See
        `board_helpers.slice_country`.
        """
        get = request.GET.get
        if unvalidated_country is None:
            raw = (get('country') or '').strip().upper()
            unvalidated_country = raw if raw in set(codes) else ''
        return cls(
            only_earners=get('earners', '1') != '0',   # default on; ?earners=0 shows all owners
            country=unvalidated_country,
        )

    def as_params(self):
        """The non-default flags, for building continuation/jump URLs that preserve the view."""
        params = {}
        if not self.only_earners:
            params['earners'] = '0'
        if self.country:
            params['country'] = self.country
        return params


@dataclass(frozen=True)
class SortKey:
    """One key in a board's total ordering. `nulls_last` marks a nullable key whose order is
    ascending-nulls-last (our only nullable pattern: the tiebreak timestamps)."""
    field: str
    desc: bool
    nulls_last: bool = False

    def order_expr(self):
        if self.nulls_last:                                   # nullable tiebreak: asc-nulls-last
            return F(self.field).asc(nulls_last=True)
        return ('-' if self.desc else '') + self.field        # plain string; the index serves it

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

    def _hunters(self, qs):
        """The population gate every board here shares: VERIFIED hunters, optionally one country's.

        One place, because the four boards each built their own filter stack and the `registered_only`
        clause was duplicated across all of them -- four chances for one to drift. `is_linked` is the same
        rule `badge_leaderboards._linked` applies, so "who is on a board" means one thing site-wide.
        """
        qs = qs.filter(profile__is_linked=True)
        return qs.filter(profile__country_code=self.opts.country) if self.opts.country else qs

    def countries(self):
        """Country codes with at least one hunter on THIS board, for its picker.

        Scoped to the board rather than to the site, like every other picker: an option that empties the
        thing it filters is a dead end, and scoping is what lets the panel skip an "emptied by the filter"
        state for country entirely.
        """
        from trophies.services.badge_leaderboards import _cached

        # CACHED, like every other picker on the site. This one is the most expensive of them: the
        # DISTINCT is on a JOINED column, so no board index can serve it and it scans the game's whole
        # population probing Profile per row. Keyed on the exact population it describes -- game, board
        # kind and the only filter that changes who is on it.
        key = f'lb:picker:cc:game:{self.game.pk}:{self.__class__.__name__}:{int(self.opts.only_earners)}'
        return _cached(key, lambda: sorted(
            self._population()
            .exclude(profile__country_code__isnull=True).exclude(profile__country_code='')
            .values_list('profile__country_code', flat=True).distinct()
        ))

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

    def _order(self):
        return tuple(k.order_expr() for k in self.KEYS)

    def ordered(self):
        """The board in order. Display order and canonical order are the same thing now that the board
        cannot be read bottom-first, which is what lets a display position BE a rank everywhere else."""
        return self._population().order_by(*self._order())

    # -- reads -----------------------------------------------------------

    def size(self):
        """Players on the currently-filtered board -- the client sizes the virtual spacer from this."""
        return self._population().count()

    def page_range(self, start, count=PAGE_SIZE):
        """Rows at 1-indexed display ranks [start, start+count). Bounded OFFSET slice; the index serves the
        ordering. Returns model instances (select_related profile) in display order; caller numbers them."""
        start = max(1, start)
        count = max(1, min(count, 500))
        # NO `select_related('profile')`. The rows path reads `r.profile_id` only -- `_entries` hands the
        # ids to `badge_leaderboards.page()`, which does its own one-query `hydrate()` -- so the join was
        # fetching 50 profiles per window that nothing then read. `row_at_rank`/`suggest` DO read
        # `row.profile` and keep theirs.
        return list(self.ordered()[start - 1: start - 1 + count])

    def row_at_rank(self, rank):
        """The row at 1-indexed rank (from the best), or None past the board. The rank is the fetch offset,
        so pass it straight through -- no COUNT, unlike the name suggests."""
        rank = max(1, rank)
        row = self.ordered().select_related('profile')[rank - 1: rank].first()
        return self._suggestion(row, rank) if row else None

    def rank_for(self, profile):
        """1-indexed CANONICAL rank of `profile` on this board, or None if absent. O(rank), bounded by one
        game's players."""
        row = self._board_row(profile)
        return None if row is None else self._rank_of_row(row)

    #: Longest accepted `?suggest=`. The patterns are `%q%` `icontains`, which no index serves, so a
    #: long query is a full scan of the game's population that returns nothing. PSN IDs are 16 characters.
    SUGGEST_MAX = 32

    def suggest(self, query, limit=8):
        """Board players whose PSN name matches `query`, each with its rank -- the search typeahead. Scoped
        to the filtered board, so a hidden/filtered-out player never appears. Returns [] below 2 chars.

        BOUNDED AT BOTH ENDS. The floor was always there; the ceiling was not, and this is a public
        endpoint whose patterns are `%q%` `icontains` -- no index serves those, so a long query that
        matches nothing is a full scan of the game's population, probing Profile per row for the
        `is_linked` gate, returning zero rows. Every other user-controlled param on this view is clamped
        (`at`, `range`, `count`); this was the one that was not.
        """
        q = (query or '').strip()[:self.SUGGEST_MAX]
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
        return self._hunters(qs)


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
        return self._hunters(qs)


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
        return self._hunters(qs)


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
        return self._hunters(self._group_qs().filter(completion_seconds__isnull=False))


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
    # Fastest defaults to the base game (the platinum race) when it qualifies. Groups sort by id, which puts
    # 'default' AFTER '001', so pick it explicitly before falling back to the first DLC.
    first_speed = ('default' if any(g['id'] == 'default' and g['speed'] for g in groups)
                   else next((g['id'] for g in groups if g['speed']), None))
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
