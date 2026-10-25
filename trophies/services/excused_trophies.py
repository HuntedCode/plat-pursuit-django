"""Excused trophies: finishing a list that can no longer be finished.

Staff flag a trophy that can no longer be earned (`Trophy.is_unobtainable`: servers closed, content removed).
For BADGES ONLY (owner, 2026-10-25), a hunter who has earned every trophy that is NOT excused has finished the
list: it clears a stage, counts toward holo and pays stage XP, exactly as PSN's 100% would. Contracts,
challenges, leaderboards and profile stats keep reading PSN's own percentages.

What is excused, per list version:
  * every flagged trophy
  * the PLATINUM, when any trophy in the base ('default') group is flagged: it needs every base trophy, so it
    is unobtainable too, and staff should not have to remember to flag it

The badge bars, measured over the trophies still REQUIRED:
  base  every required trophy in the 'default' group earned
  full  every required trophy in every group earned
  group every required trophy in that one group earned (a trophy-group stage member)

A bar with NOTHING left to earn (every trophy in it excused) is never met by excusal, because nobody did
anything to meet it, and an undated clear would re-stamp the badge on every run. Such a stage member is instead
UNOBTAINABLE for gating (`ExcusedList.dead`), so it never makes a badge harder either: the rule game-level
`Game.is_obtainable` already follows. A hunter who earned those trophies before they died still clears it on
PSN's own 100%.

Two reads, both small:
  `excused_lists(game_ids)`  catalogue side, once per badge catalogue. A seek on a partial index of the few
                             flagged rows, then the trophies of just those lists.
  `ExcusedProgress.read()`   per hunter, ONE aggregate over EarnedTrophy for the required trophies of the
                             lists `candidates()` picks: started, short of 100%, and holding at least as many
                             earned trophies as the smallest bar needs. Nothing is sent otherwise, which is
                             every hunter until staff flag a list they are close to finishing.
"""
from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Count, Max

from trophies.models import EarnedTrophy, Trophy

BASE_GROUP = 'default'


@dataclass(frozen=True)
class ExcusedList:
    """One list version with at least one excused trophy. `required` / `excused` are keyed by trophy group id
    ('default', '001', ...) and hold the trophy ids still required, and how many are excused. `plat_excused`:
    the list's platinum is one of them."""
    required: dict
    excused: dict
    plat_excused: bool

    def excused_count(self, group_id=None):
        """Excused trophies in one group, or in the whole list when `group_id` is None."""
        if group_id is None:
            return sum(self.excused.values())
        return self.excused.get(group_id, 0)

    def split(self):
        """Where the excused trophies sit, for saying it in words: (base-game trophies, DLC trophies, platinum).
        The base count leaves the platinum out; it is reported on its own."""
        base = self.excused.get(BASE_GROUP, 0) - (1 if self.plat_excused else 0)
        dlc = sum(n for gid, n in self.excused.items() if gid != BASE_GROUP)
        return base, dlc, self.plat_excused

    def dead(self, group_id):
        """Every trophy in this bar's group is excused, so nothing is left to earn there."""
        return group_id in self.required and not self.required[group_id]

    @property
    def min_required(self):
        """The fewest trophies any bar on this list needs: a hunter with fewer earned cannot meet one."""
        return min((len(ids) for ids in self.required.values() if ids), default=None)


def excused_trophy_ids(trophies) -> set:
    """THE rule for what is excused on ONE list, from `(id, trophy_group_id, trophy_type, is_unobtainable)`
    rows: every flagged trophy, and the platinum when any base-group trophy is flagged. Shared by the badge
    engine and the Game page's trophy list, so the two cannot disagree about which trophies are excused."""
    rows = list(trophies)
    base_flagged = any(flagged for _, gid, _, flagged in rows if gid == BASE_GROUP)
    return {tid for tid, _, ttype, flagged in rows if flagged or (base_flagged and ttype == 'platinum')}


def excused_lists(game_ids) -> dict:
    """{game_id: ExcusedList} for every list among `game_ids` with at least one flagged trophy. Lists with
    nothing flagged are absent, which is how every caller knows there is nothing to do."""
    flagged_games = set(
        Trophy.objects.filter(game_id__in=game_ids, is_unobtainable=True).values_list('game_id', flat=True)
    )
    if not flagged_games:
        return {}
    rows = Trophy.objects.filter(game_id__in=sorted(flagged_games)).values_list(
        'id', 'game_id', 'trophy_group_id', 'trophy_type', 'is_unobtainable',
    )
    by_game = defaultdict(list)
    for trophy_id, game_id, gid, ttype, flagged in rows:
        by_game[game_id].append((trophy_id, gid, ttype, flagged))

    lists = {}
    for game_id, trophies in by_game.items():
        excused_ids = excused_trophy_ids(trophies)
        required, excused = defaultdict(set), defaultdict(int)
        for trophy_id, gid, _, _ in trophies:
            if trophy_id in excused_ids:
                excused[gid] += 1
                required.setdefault(gid, set())   # kept even when empty: that is what makes the bar dead
            else:
                required[gid].add(trophy_id)
        lists[game_id] = ExcusedList(
            required={gid: frozenset(ids) for gid, ids in required.items()}, excused=dict(excused),
            plat_excused=any(ttype == 'platinum' and tid in excused_ids for tid, _, ttype, _ in trophies),
        )
    return lists


def candidates(lists, standing) -> list:
    """The excused lists worth reading for one hunter. `standing` is {game_id: (PSN progress, earned trophies
    count)} from their ProfileGame rows. A list they finished on PSN needs nothing more, and one where they have
    earned fewer trophies than the smallest bar needs cannot be finished this way yet."""
    picked = []
    for game_id, xl in lists.items():
        progress, earned = standing.get(game_id, (100, 0))
        if progress < 100 and xl.min_required is not None and earned >= xl.min_required:
            picked.append(game_id)
    return picked


class ExcusedProgress:
    """One hunter's standing on the excused lists read for them. `base`, `full` and `group` answer
    `(done, date)`: done when every required trophy of that bar is earned and the bar requires at least one,
    dated by the last of them (None only if PSN gave none of them a date). A list that was not read has no
    earned rows, so it is never done: a bar needs at least one earned trophy. `excused_count`, `plat_excused`
    and `dead` read the whole catalogue's lists, read or not: they are facts about the list."""

    def __init__(self, lists, earned):
        self._lists = lists
        self._earned = earned          # {(game_id, group_id): (count, last)}

    @classmethod
    def read(cls, profile, lists, game_ids):
        """The ONE per-hunter read, over the required trophies of `game_ids` (from `candidates`). Empty
        `game_ids` (or no profile) sends nothing."""
        started = {gid for gid in game_ids if gid in lists} if profile is not None else set()
        trophy_ids = sorted(t for gid in started for ids in lists[gid].required.values() for t in ids)
        earned = {}
        if trophy_ids:
            earned = {
                (row['trophy__game_id'], row['trophy__trophy_group_id']): (row['n'], row['last'])
                for row in EarnedTrophy.objects.filter(
                    profile=profile, earned=True, trophy_id__in=trophy_ids,
                ).values('trophy__game_id', 'trophy__trophy_group_id').annotate(
                    n=Count('id'), last=Max('earned_date_time'),
                )
            }
        return cls(lists, earned)

    def excused_count(self, game_id, group_id=None):
        """Excused trophies on a list (or one group of it), 0 for a list with none."""
        xl = self._lists.get(game_id)
        return xl.excused_count(group_id) if xl else 0

    def plat_excused(self, game_id):
        xl = self._lists.get(game_id)
        return bool(xl and xl.plat_excused)

    def split(self, game_id):
        xl = self._lists.get(game_id)
        return xl.split() if xl else (0, 0, False)

    def dead(self, game_id, group_id):
        xl = self._lists.get(game_id)
        return bool(xl and xl.dead(group_id))

    def _groups(self, game_id, group_ids):
        xl = self._lists.get(game_id)
        if xl is None:
            return False, None
        dates, needed = [], 0
        for gid in group_ids:
            required = xl.required.get(gid)
            if required is None:
                return False, None      # the list has no such group: nothing to finish here
            n, last = self._earned.get((game_id, gid), (0, None))
            if n < len(required):
                return False, None
            needed += len(required)
            if last is not None:
                dates.append(last)
        if not needed:
            return False, None          # nothing left to earn is not finishing it (see the module docstring)
        return True, (max(dates) if dates else None)

    def base(self, game_id):
        return self._groups(game_id, (BASE_GROUP,))

    def full(self, game_id):
        xl = self._lists.get(game_id)
        return self._groups(game_id, tuple(xl.required) if xl else ())

    def group(self, game_id, group_id):
        return self._groups(game_id, (group_id,))
