"""Badge evaluation engine (rebuild) — the PURE core.

This module is deliberately free of Django/ORM imports: it takes plain input dataclasses and returns a
result. That keeps the earn rules a single, exhaustively-testable, side-effect-free function of the facts —
which is also what makes the whole rebuild whale-safe (the caller feeds bounded, pre-aggregated facts) and
reconcilable (run it twice, get the same answer; run it for old vs new, diff).

The DB-reading orchestrator (`evaluate_profile`, which prefetches Stages/Games and the profile's completion
facts and maps them into these inputs) and `ConceptBundle` handling are built on top of this core, separately.

Rules encoded here (see docs/design/rebuild/badge-backend-rebuild.md §3.3):
  - A game QUALIFIES for a group if its platforms intersect the group's platforms.
  - A stage is IN SCOPE for a group if it holds at least one qualifying game. A stage with nothing on the
    group's platforms is not that badge's business at all: it cannot gate it, satisfy it, or pay XP for it.
  - Gating is PLATFORM-SCOPED. An in-scope stage GATES (becomes required) if one of its QUALIFYING games is
    obtainable AND (the group counts delisted games, or the game isn't delisted). -> Legacy HD counts
    delisted; Ultra HD doesn't. So a stage whose only PS4 list is unobtainable stops gating Ultra HD even
    when its PS3 list is still alive.
  - Satisfaction is CROSS-PLATFORM. An in-scope stage is SATISFIED by ANY of its games, on any platform --
    clear the PS4 list and the stage counts for Legacy HD too, and vice versa. One work, one clear.
    (Owner's call, 2026-09. Before this, satisfaction was scoped to qualifying games the way gating still
    is, so a cross-gen hunter had to re-clear the same game once per edition.)
  - The two together mean delisted and unobtainable games never BLOCK, but still COUNT -- and a stage that
    stopped gating still pays XP to anyone who cleared it. See `xp_stage_count`.
  - Two completion bars, supplied per game by the orchestrator (no platinum-specific branching in here):
      base_complete = the game's DEFAULT trophy group at 100% (the platinum for plat games; the main list for
                      no-plat games; DLC-independent).
      full_complete = the WHOLE game at 100% incl DLC.
    base = every gating stage has a qualifying game at base_complete; holo = ...at full_complete (live, cosmetic).
  - A BONUS stage (`StageInput.is_bonus`, which replaced stage 0) is skipped for the badge, its holo and
    its XP.
  - MASTERY (owner, 2026-10-25) is the badge's top finish: every stage IN SCOPE for the edition, bonus stages
    and stages that no longer gate included, at full_complete on any one version. It is never required, so it
    can read what gating cannot. It needs the badge itself, and it implies holo (a superset of its stages).
    It is OFFERED only when it asks more than holo (owner, 2026-10-25): a bonus stage (usually a region-locked
    release), a stage that no longer gates, or a megamix's stages past its threshold. Elsewhere it would be
    holo under another name, so holo is that badge's top finish. Either way the edition is FINISHED when every
    in-scope stage is at the full bar, which is what Series Master reads.
  - A series' FULL SET and SERIES MASTER (`evaluate_series_rewards`) read the editions' results together.
  - completion_policy 'all' -> every gating stage; 'min_count' (megamix) -> >= min_required gating stages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


# ------------------------------------------------------------------ inputs (pure) ------------------------

@dataclass(frozen=True)
class GameState:
    """One qualifying game's static facts + this profile's completion state on it."""
    game_id: int                  # identity/debug only; the engine never looks it up (so a bundle's synthetic negative id is safe)
    platforms: frozenset          # title_platform, e.g. frozenset({'PS4', 'PS5'})
    is_obtainable: bool
    is_delisted: bool
    base_complete: bool           # default trophy group at 100% (ProfileTrophyGroup) -> the BASE bar
    full_complete: bool           # whole game at 100% incl DLC (ProfileGame.progress == 100) -> the HOLO bar
    completion_date: Optional[date] = None   # when the profile reached BASE on this game (default group's last trophy)
    full_date: Optional[date] = None         # when it reached the FULL bar (its last trophy), for mastery's date


@dataclass(frozen=True)
class StageInput:
    stage_number: int
    games: tuple                  # tuple[GameState, ...]
    is_bonus: bool = False        # counts toward Mastery only (Stage.is_bonus)


@dataclass(frozen=True)
class GroupInput:
    platforms: frozenset
    exclude_delisted: bool


@dataclass(frozen=True)
class SeriesInput:
    completion_policy: str = 'all'    # 'all' | 'min_count'
    min_required: int = 0             # min_count only


# ------------------------------------------------------------------ outputs ------------------------------

@dataclass
class StageResult:
    stage_number: int
    gates: bool
    base_satisfied: bool
    holo_satisfied: bool
    base_date: Optional[date]         # earliest date the stage's base bar was met (for earn_rank)
    # Appended with defaults so the positional constructions in the test suite keep working.
    in_scope: bool = True             # the stage holds a game on this edition's platforms (mastery's scope)
    full_date: Optional[date] = None  # earliest date the stage's full bar was met (for mastered_date)
    is_bonus: bool = False            # the stage counts toward Mastery only


@dataclass
class GroupBadgeResult:
    base_earned: bool
    holo: bool
    gating_count: int
    base_satisfied_count: int         # GATING stages cleared -- the progress numerator ("3 of 5")
    holo_satisfied_count: int
    earned_date: Optional[date]       # completion-ordered earn moment (see _earned_date)
    stages: list = field(default_factory=list)   # list[StageResult]
    #: Every IN-SCOPE stage cleared, gating or not -- the XP numerator, and deliberately NOT
    #: `base_satisfied_count`. The two diverge on a stage that is in scope but no longer gates (its
    #: qualifying games went unobtainable): it pays XP to whoever cleared it while staying out of the
    #: progress fraction, which must never exceed its own denominator. Appended with a default so the
    #: positional constructions in the test suite keep working.
    xp_stage_count: int = 0
    #: FINISHED: every in-scope stage, bonus stages included, at the full bar, with the badge held. `mastery_done`
    #: of `mastery_total` is its progress. `finished_date` is when the last of them fell (None when any is
    #: undated). MASTERED is finished on an edition that offers Mastery (see the module docstring).
    mastered: bool = False
    mastery_done: int = 0
    mastery_total: int = 0
    mastered_date: Optional[date] = None
    mastery_offered: bool = False
    finished: bool = False
    finished_date: Optional[date] = None
    #: The in-scope stage numbers, bonus stages included: which edition "covers" what, for Series Master.
    scope: frozenset = frozenset()
    #: The BONUS stages' results (list[StageResult]), apart from `stages`: everything that reads `stages`
    #: (XP buckets, the challenge planner) must never see them. The badge page reads both.
    bonus_stages: list = field(default_factory=list)


# ------------------------------------------------------------------ per-game predicates ------------------

def _qualifies(g: GameState, group: GroupInput) -> bool:
    return bool(g.platforms & group.platforms)


def _gates(g: GameState, group: GroupInput) -> bool:
    if not g.is_obtainable:
        return False
    if g.is_delisted and group.exclude_delisted:
        return False
    return True


# ------------------------------------------------------------------ evaluation ---------------------------

def evaluate_stage(stage: StageInput, group: GroupInput) -> StageResult:
    """Evaluate one stage for one group.

    Two different scopes, on purpose:

    - GATING reads only the QUALIFYING games (platform overlap). A badge must not require work that cannot
      be done on its own platforms, so an Ultra HD badge never becomes gated by a PS3-only list.
    - SATISFACTION reads EVERY game in the stage. The stage is one work; clearing it on any platform clears
      it. This is what lets a cross-gen hunter finish both editions from one platinum instead of buying and
      replaying the same game per edition.

    A stage with NO qualifying game is out of scope entirely and returns all-False -- not gating, not
    satisfied, no date -- so it contributes nothing to this badge anywhere downstream, XP included.
    """
    qualifying = [g for g in stage.games if _qualifies(g, group)]
    if not qualifying:
        return StageResult(stage.stage_number, False, False, False, None, in_scope=False, is_bonus=stage.is_bonus)

    gates = any(_gates(g, group) for g in qualifying)
    base_satisfied = any(g.base_complete for g in stage.games)
    holo_satisfied = any(g.full_complete for g in stage.games)
    # The stage becomes base-satisfied at the EARLIEST date ANY of its games met the base bar -- the same
    # set satisfaction reads, so the date can never name a game that did not satisfy it.
    base_dates = [g.completion_date for g in stage.games if g.base_complete and g.completion_date is not None]
    base_date = min(base_dates) if base_dates else None
    full_dates = [g.full_date for g in stage.games if g.full_complete and g.full_date is not None]
    return StageResult(stage.stage_number, gates, base_satisfied, holo_satisfied, base_date,
                       full_date=min(full_dates) if full_dates else None, is_bonus=stage.is_bonus)


def _earned_date(gating: list, policy: str, need: int, base_earned: bool) -> Optional[date]:
    """Completion-ordered earn moment: for 'all', the date the LAST required stage fell; for 'min_count',
    the date the need-th stage fell. None if base isn't earned or a required completion date is missing."""
    if not base_earned:
        return None
    dates = sorted(r.base_date for r in gating if r.base_satisfied and r.base_date is not None)
    threshold = need if policy == 'min_count' else len([r for r in gating if r.base_satisfied])
    if threshold == 0 or len(dates) < threshold:
        return None
    return dates[threshold - 1]


def _mastery(scoped: list, base_earned: bool, extra_stages: bool, earnable: bool = True) -> dict:
    """Mastery over the in-scope stages (bonus stages included). It needs the badge itself: a badge with nothing
    left to gate (`earnable` False) is revoked, so there is no hold to master and nothing is offered.
    `extra_stages`: a megamix asks for fewer stages than it gates."""
    done = sum(1 for r in scoped if r.holo_satisfied)
    finished = base_earned and bool(scoped) and done == len(scoped)
    dates = [r.full_date for r in scoped]
    when = max(dates) if finished and None not in dates else None
    offered = earnable and (extra_stages or any(r.is_bonus or not r.gates for r in scoped))
    mastered = offered and finished
    return {'mastered': mastered, 'mastery_done': done, 'mastery_total': len(scoped),
            'mastered_date': when if mastered else None, 'scope': frozenset(r.stage_number for r in scoped),
            'mastery_offered': offered, 'finished': finished, 'finished_date': when}


def evaluate_group_badge(series: SeriesInput, group: GroupInput, stages: list) -> GroupBadgeResult:
    """Evaluate one group badge (a BadgeSeries x PlatformGroup) for one profile. Pure."""
    results = [evaluate_stage(s, group) for s in stages if not s.is_bonus]   # a bonus stage is tangential
    # ...to everything but mastery, which is every stage the edition covers.
    bonus = [evaluate_stage(s, group) for s in stages if s.is_bonus]
    scoped = [r for r in results + bonus if r.in_scope]
    gating = [r for r in results if r.gates]
    gating_count = len(gating)
    base_ok = sum(1 for r in gating if r.base_satisfied)
    holo_ok = sum(1 for r in gating if r.holo_satisfied)
    # Every in-scope stage cleared, gating or not. Out-of-scope stages report base_satisfied=False, so this
    # needs no scope test of its own. Bonus stages are already excluded above and must stay excluded: they are
    # tangential by definition, and paying XP for them would make optional work feel mandatory.
    xp_stage_count = sum(1 for r in results if r.base_satisfied)

    if gating_count == 0:
        # Nothing gates this group (e.g. every stage's only qualifying games are unobtainable, or delisted
        # in an exclude-delisted group): the badge is not earnable here, and a hunter holding it is revoked.
        # The XP still travels. Those stages were genuinely cleared, and clawing points back because a
        # storefront closed would punish the hunter for someone else's decision.
        return GroupBadgeResult(False, False, 0, base_ok, holo_ok, None, results, xp_stage_count,
                                bonus_stages=bonus, **_mastery(scoped, False, False, earnable=False))

    if series.completion_policy == 'min_count':
        # Megamix. min_required applies to THIS group's gating stages; 0 means "all". (How min_required maps
        # under the platform split is a product detail flagged in the design doc; this is the simple rule.)
        need = min(series.min_required or gating_count, gating_count)
        base_earned = base_ok >= need
        holo = holo_ok >= need
    else:   # 'all'
        need = gating_count
        base_earned = base_ok == gating_count
        holo = holo_ok == gating_count

    earned_date = _earned_date(gating, series.completion_policy, need, base_earned)
    return GroupBadgeResult(base_earned, holo, gating_count, base_ok, holo_ok, earned_date, results,
                            xp_stage_count, bonus_stages=bonus, **_mastery(scoped, base_earned, need < gating_count))


@dataclass(frozen=True)
class SeriesRewards:
    full_set: bool
    full_set_date: Optional[date]
    master_offered: bool              # no single edition covers every stage, so finishing one is not enough
    series_master: bool
    series_master_date: Optional[date]


def _latest(dates):
    return None if not dates or None in dates else max(dates)


def evaluate_series_rewards(editions: list) -> SeriesRewards:
    """A series' rewards from EVERY live edition's result (a partial list gives a wrong answer). Pure.

    Only editions that can be earned count (owner, 2026-10-25): an edition with nothing left to gate never
    makes a Full Set harder, as an unobtainable game never makes a badge harder. Both need 2+ of them.

    - FULL SET: every one of them held.
    - SERIES MASTER: every one of them FINISHED (mastered, or holo where the edition offers no Mastery), offered
      only when no single edition covers every stage. When one does, finishing it finishes the others too
      (satisfaction is cross-platform), so it would say nothing.
    """
    earnable = [r for r in editions if r.gating_count > 0]
    if len(earnable) < 2:
        return SeriesRewards(False, None, False, False, None)
    full_set = all(r.base_earned for r in earnable)
    every_stage = frozenset().union(*(r.scope for r in earnable))
    offered = not any(r.scope == every_stage for r in earnable)
    master = offered and all(r.finished for r in earnable)
    return SeriesRewards(
        full_set, _latest([r.earned_date for r in earnable]) if full_set else None,
        offered, master, _latest([r.finished_date for r in earnable]) if master else None,
    )
