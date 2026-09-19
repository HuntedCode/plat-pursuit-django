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
  - Stage 0 is skipped (tangential/bonus).
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


@dataclass(frozen=True)
class StageInput:
    stage_number: int
    games: tuple                  # tuple[GameState, ...]


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
        return StageResult(stage.stage_number, False, False, False, None)

    gates = any(_gates(g, group) for g in qualifying)
    base_satisfied = any(g.base_complete for g in stage.games)
    holo_satisfied = any(g.full_complete for g in stage.games)
    # The stage becomes base-satisfied at the EARLIEST date ANY of its games met the base bar -- the same
    # set satisfaction reads, so the date can never name a game that did not satisfy it.
    base_dates = [g.completion_date for g in stage.games if g.base_complete and g.completion_date is not None]
    base_date = min(base_dates) if base_dates else None
    return StageResult(stage.stage_number, gates, base_satisfied, holo_satisfied, base_date)


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


def evaluate_group_badge(series: SeriesInput, group: GroupInput, stages: list) -> GroupBadgeResult:
    """Evaluate one group badge (a BadgeSeries x PlatformGroup) for one profile. Pure."""
    results = [evaluate_stage(s, group) for s in stages if s.stage_number > 0]   # stage 0 is tangential
    gating = [r for r in results if r.gates]
    gating_count = len(gating)
    base_ok = sum(1 for r in gating if r.base_satisfied)
    holo_ok = sum(1 for r in gating if r.holo_satisfied)
    # Every in-scope stage cleared, gating or not. Out-of-scope stages report base_satisfied=False, so this
    # needs no scope test of its own. Stage 0 is already excluded above and must stay excluded: it is
    # tangential by definition, and paying XP for it would make optional work feel mandatory.
    xp_stage_count = sum(1 for r in results if r.base_satisfied)

    if gating_count == 0:
        # Nothing gates this group (e.g. every stage's only qualifying games are unobtainable, or delisted
        # in an exclude-delisted group): the badge is not earnable here, and a hunter holding it is revoked.
        # The XP still travels. Those stages were genuinely cleared, and clawing points back because a
        # storefront closed would punish the hunter for someone else's decision.
        return GroupBadgeResult(False, False, 0, base_ok, holo_ok, None, results, xp_stage_count)

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
                            xp_stage_count)
