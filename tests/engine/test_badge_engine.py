"""Unit tests for the PURE badge evaluation core (trophies/services/badge_engine).

No DB, no ORM — the engine core is a pure function of plain input dataclasses, so these tests build facts
directly and assert the earn rules exhaustively. The DB orchestrator gets its own integration tests later.

Completion is expressed as two booleans per game (supplied by the orchestrator, no platinum branching here):
  base_complete = default trophy group at 100% (the BASE bar)
  full_complete = whole game at 100% incl DLC (the HOLO bar)
"""
from datetime import date

from trophies.services.badge_engine import (
    GameState, StageInput, GroupInput, SeriesInput,
    evaluate_stage, evaluate_group_badge,
)

LEGACY = GroupInput(platforms=frozenset({'PS3', 'PSVITA'}), exclude_delisted=False)
ULTRA = GroupInput(platforms=frozenset({'PS4', 'PS5'}), exclude_delisted=True)
ALL = SeriesInput(completion_policy='all')


def _d(day):
    return date(2026, 1, day) if day else None


def game(gid=1, platforms=('PS5',), obtainable=True, delisted=False, base=False, full=False, day=None):
    # `full` implies `base` in reality (100% incl DLC requires the base list); tests set both explicitly.
    return GameState(gid, frozenset(platforms), obtainable, delisted, base, full, _d(day))


def stage(n, *games):
    return StageInput(n, tuple(games))


# ── platform routing ────────────────────────────────────────────────────────
def test_platform_routing():
    ps3 = game(platforms=('PS3',))
    ps5 = game(platforms=('PS5',))
    assert evaluate_stage(stage(1, ps5), ULTRA).gates is True
    assert evaluate_stage(stage(1, ps5), LEGACY).gates is False   # PS5 doesn't qualify for Legacy
    assert evaluate_stage(stage(1, ps3), LEGACY).gates is True
    assert evaluate_stage(stage(1, ps3), ULTRA).gates is False


def test_cross_boundary_game_qualifies_both():
    g = game(platforms=('PS3', 'PS4'))   # straddles the boundary
    assert evaluate_stage(stage(1, g), LEGACY).gates is True
    assert evaluate_stage(stage(1, g), ULTRA).gates is True


# ── base bar (default group at 100%) ─────────────────────────────────────────
def test_base_earned_when_base_complete():
    assert evaluate_group_badge(ALL, ULTRA, [stage(1, game(base=True, day=1))]).base_earned is True


def test_base_not_earned_when_incomplete():
    assert evaluate_group_badge(ALL, ULTRA, [stage(1, game(base=False))]).base_earned is False


def test_engine_treats_bars_independently():
    # The pure engine does NOT guard full => base; that invariant is enforced by the orchestrator at the data
    # boundary. So at the engine level base=False/full=True yields holo-satisfied-but-not-base (garbage in,
    # garbage out, by design). This documents WHY the guard lives upstream, not here.
    r = evaluate_stage(stage(1, game(base=False, full=True)), ULTRA)
    assert r.base_satisfied is False and r.holo_satisfied is True


# ── base vs holo ─────────────────────────────────────────────────────────────
def test_holo_requires_full_complete():
    # base but not full (DLC left): base earned, holo not. Then full -> holo.
    base_only = [stage(1, game(base=True, full=False, day=1))]
    r1 = evaluate_group_badge(ALL, ULTRA, base_only)
    assert r1.base_earned is True and r1.holo is False

    fully = [stage(1, game(base=True, full=True, day=1))]
    r2 = evaluate_group_badge(ALL, ULTRA, fully)
    assert r2.base_earned is True and r2.holo is True


def test_holo_satisfied_via_different_game_than_base():
    # Stage has A (base only) and B (base + full). Base via either, holo via B.
    a = game(gid=1, base=True, full=False, day=1)
    b = game(gid=2, base=True, full=True, day=2)
    r = evaluate_stage(stage(1, a, b), ULTRA)
    assert r.base_satisfied is True and r.holo_satisfied is True


# ── delisted policy (the crux) ───────────────────────────────────────────────
def test_delisted_gates_in_legacy_not_ultra():
    delisted = game(platforms=('PS3',), delisted=True)   # obtainable but delisted
    assert evaluate_stage(stage(1, delisted), LEGACY).gates is True    # Legacy counts delisted
    delisted_ultra = game(platforms=('PS5',), delisted=True)
    assert evaluate_stage(stage(1, delisted_ultra), ULTRA).gates is False   # Ultra excludes delisted


def test_delisted_never_blocks_ultra_but_still_counts():
    # Ultra series: stage1 normal game (done), stage2 ONLY a delisted game (undone).
    normal = game(gid=1, platforms=('PS5',), base=True, full=True, day=1)
    delisted_undone = game(gid=2, platforms=('PS5',), delisted=True, base=False)
    r = evaluate_group_badge(ALL, ULTRA, [stage(1, normal), stage(2, delisted_undone)])
    # stage2 doesn't gate -> only stage1 is required -> earned despite the untouched delisted game.
    assert r.gating_count == 1 and r.base_earned is True

    # ...but if the user DID complete a delisted game in a gating stage, it satisfies.
    gating = game(gid=3, platforms=('PS5',), base=False)                        # required, not done
    delisted_done = game(gid=4, platforms=('PS5',), delisted=True, base=True, day=1)
    sr = evaluate_stage(stage(1, gating, delisted_done), ULTRA)
    assert sr.gates is True and sr.base_satisfied is True   # satisfied via the delisted completion


# ── unobtainable ─────────────────────────────────────────────────────────────
def test_unobtainable_doesnt_gate_but_satisfies():
    gating = game(gid=1, obtainable=True, base=False)                # required, not done
    unobt_done = game(gid=2, obtainable=False, base=True, day=1)     # can't gate, but earned
    r = evaluate_stage(stage(1, gating, unobt_done), ULTRA)
    assert r.gates is True and r.base_satisfied is True


def test_unobtainable_only_stage_not_gating():
    r = evaluate_group_badge(ALL, ULTRA, [stage(1, game(obtainable=False))])
    assert r.gating_count == 0 and r.base_earned is False


def test_an_unobtainable_stage_still_pays_whoever_cleared_it():
    """Owner's call, 2026-09. The stage cannot be REQUIRED of anyone any more, but a hunter who cleared it
    while it was alive keeps the points. Losing XP because a storefront closed is a punishment for someone
    else's decision."""
    cleared_but_dead = game(obtainable=False, base=True, day=1)
    r = evaluate_group_badge(ALL, ULTRA, [stage(1, cleared_but_dead), stage(2, game(base=True, day=2))])

    assert r.gating_count == 1, 'the unobtainable stage must not be required'
    assert r.base_satisfied_count == 1, 'the progress numerator counts GATING stages only'
    assert r.xp_stage_count == 2, 'the cleared-but-dead stage stopped paying'


def test_a_badge_with_nothing_left_to_gate_is_revoked_but_still_pays():
    """The whole-badge version of the rule above, and the shape that follows from it: the engine reports
    base_earned=False (apply then deletes the hold), while xp_stage_count keeps the stages that were
    actually cleared. `_group_badge_xp` withholds only the COMPLETION BONUS, which is for finishing a badge
    that can no longer be finished."""
    r = evaluate_group_badge(ALL, ULTRA, [
        stage(1, game(obtainable=False, base=True, day=1)),
        stage(2, game(obtainable=False, base=True, day=2)),
    ])

    assert r.gating_count == 0
    assert r.base_earned is False, 'an unearnable badge must not be held'
    assert r.xp_stage_count == 2, 'the hunter lost points for work they had already done'


def test_stage_zero_never_pays_even_when_cleared():
    """Stage 0 is tangential by definition. Paying XP for it would make optional work feel mandatory, and
    it is the one exception the owner called out explicitly."""
    r = evaluate_group_badge(ALL, ULTRA, [
        stage(0, game(gid=1, base=True, day=1)),
        stage(1, game(gid=2, base=True, day=2)),
    ])

    assert r.xp_stage_count == 1, 'stage 0 was paid'


# ── cross-platform satisfaction ──────────────────────────────────────────────
def test_a_clear_on_any_platform_satisfies_the_stage():
    """One stage is one WORK. A hunter who platinumed the PS5 list has done the stage, and the PS3 badge
    must credit it rather than asking them to buy and replay the same game."""
    ps5_done = game(gid=1, platforms=('PS5',), base=True, day=1)
    ps3_untouched = game(gid=2, platforms=('PS3',), base=False)

    legacy = evaluate_group_badge(ALL, LEGACY, [stage(1, ps5_done, ps3_untouched)])

    assert legacy.gating_count == 1, 'the PS3 copy still gates Legacy HD'
    assert legacy.base_earned is True, 'the PS5 clear did not credit the PS3 badge'
    # The DATE has to travel with the credit. `base_date` was widened alongside satisfaction, and if it
    # ever narrows back, `base_satisfied` still reads the wider list -- so every assertion above keeps
    # passing while `earned_date` goes None, `apply_changes` stamps `earned_at=now()`, and the diff then
    # emits an `update` on every nightly forever, churning the earners board. Mutation-proven gap.
    assert legacy.stages[0].base_date == _d(1)
    assert legacy.earned_date == _d(1), 'the cross-platform earn lost its completion date'


def test_holo_also_crosses_platforms():
    """Holo follows base across platforms (owner's call, 2026-09).

    Untested until an audit mutation proved it: every other holo test puts both games on PS5 and evaluates
    against ULTRA, so `qualifying` and `stage.games` are the same list and narrowing holo back to
    platform-scoped was invisible to the whole suite.

    Worth knowing what this grants, because the two bars are not symmetric: base is the default trophy
    group, holo is the WHOLE game including DLC -- and DLC differs between editions. 100%-ing a remaster
    that folds its DLC into one list confers holo on an edition where the equivalent is several lists.
    Accepted deliberately: one work, one mastery.
    """
    ps5_mastered = game(gid=1, platforms=('PS5',), base=True, full=True, day=1)
    ps3_untouched = game(gid=2, platforms=('PS3',), base=False, full=False)

    legacy = evaluate_group_badge(ALL, LEGACY, [stage(1, ps5_mastered, ps3_untouched)])

    assert legacy.base_earned is True
    assert legacy.holo is True, 'the PS5 100% did not confer holo on the PS3 edition'
    assert legacy.holo_satisfied_count == 1


def test_holo_still_needs_the_whole_game_somewhere():
    """The limit: cross-platform widens WHICH game can supply the bar, never lowers the bar. A stage
    base-cleared everywhere but 100%'d nowhere is earned and not holo."""
    ps5_base_only = game(gid=1, platforms=('PS5',), base=True, full=False, day=1)
    ps3_base_only = game(gid=2, platforms=('PS3',), base=True, full=False, day=1)

    legacy = evaluate_group_badge(ALL, LEGACY, [stage(1, ps5_base_only, ps3_base_only)])

    assert legacy.base_earned is True
    assert legacy.holo is False


def test_gating_stays_platform_scoped():
    """The half that did NOT change. A badge must never require work that cannot be done on its own
    platforms, so an alive PS3 list cannot make a stage required of Ultra HD when the PS4 copy is dead.
    (Owner's decision, chosen over the alternative where any obtainable copy keeps the stage required.)"""
    ps4_dead = game(gid=1, platforms=('PS4',), obtainable=False)
    ps3_alive = game(gid=2, platforms=('PS3',), obtainable=True)

    ultra = evaluate_group_badge(ALL, ULTRA, [stage(1, ps4_dead, ps3_alive)])

    assert ultra.gating_count == 0, 'a PS3-only obtainable list made a stage required of Ultra HD'


def test_an_out_of_scope_stage_credits_nothing_anywhere():
    """The limit on cross-platform credit: a stage with no copy on the group's platforms is not that
    badge's business at all. Not gating, not satisfied, and -- the part a naive implementation gets wrong
    -- no XP either."""
    ps5_only_done = game(gid=1, platforms=('PS5',), base=True, day=1)

    legacy = evaluate_group_badge(ALL, LEGACY, [stage(1, ps5_only_done)])

    assert legacy.gating_count == 0
    assert legacy.base_satisfied_count == 0
    assert legacy.xp_stage_count == 0, 'a stage this badge cannot see paid it XP'
    assert legacy.stages[0].base_satisfied is False


# ── stage 0 + all-required ───────────────────────────────────────────────────
def test_stage_zero_is_skipped():
    done = game(gid=1, base=True, full=True, day=1)
    bonus_undone = game(gid=2, base=False)
    r = evaluate_group_badge(ALL, ULTRA, [stage(0, bonus_undone), stage(1, done)])
    assert r.gating_count == 1 and r.base_earned is True   # stage 0 ignored


def test_all_gating_stages_required():
    s1 = stage(1, game(gid=1, base=True, day=1))
    s2_undone = stage(2, game(gid=2, base=False))
    s2_done = stage(2, game(gid=2, base=True, day=2))
    assert evaluate_group_badge(ALL, ULTRA, [s1, s2_undone]).base_earned is False
    assert evaluate_group_badge(ALL, ULTRA, [s1, s2_done]).base_earned is True


def test_group_with_nothing_qualifying_not_earned():
    ps3only = game(platforms=('PS3',), base=True, day=1)
    r = evaluate_group_badge(ALL, ULTRA, [stage(1, ps3only)])
    assert r.gating_count == 0 and r.base_earned is False


# ── earn date (first-to-complete) ────────────────────────────────────────────
def test_earned_date_is_last_gating_stage():
    s1 = stage(1, game(gid=1, base=True, day=3))
    s2 = stage(2, game(gid=2, base=True, day=7))
    r = evaluate_group_badge(ALL, ULTRA, [s1, s2])
    assert r.base_earned is True and r.earned_date == date(2026, 1, 7)   # the LAST stage to fall


def test_earned_date_none_when_not_earned():
    s1 = stage(1, game(gid=1, base=True, day=3))
    s2 = stage(2, game(gid=2, base=False))
    assert evaluate_group_badge(ALL, ULTRA, [s1, s2]).earned_date is None


# ── megamix (min_count) ──────────────────────────────────────────────────────
def test_megamix_min_count():
    mm = SeriesInput(completion_policy='min_count', min_required=2)
    s1 = stage(1, game(gid=1, base=True, day=2))
    s2 = stage(2, game(gid=2, base=True, day=5))
    s3 = stage(3, game(gid=3, base=False))   # not done
    r = evaluate_group_badge(mm, ULTRA, [s1, s2, s3])
    assert r.gating_count == 3 and r.base_earned is True        # 2 of 3 satisfies min_required=2
    assert r.earned_date == date(2026, 1, 5)                    # date the 2nd stage fell


def test_megamix_below_min_count_not_earned():
    mm = SeriesInput(completion_policy='min_count', min_required=2)
    s1 = stage(1, game(gid=1, base=True, day=2))
    s2 = stage(2, game(gid=2, base=False))
    s3 = stage(3, game(gid=3, base=False))
    assert evaluate_group_badge(mm, ULTRA, [s1, s2, s3]).base_earned is False


def test_megamix_zero_min_required_defaults_to_all():
    mm = SeriesInput(completion_policy='min_count', min_required=0)
    s1 = stage(1, game(gid=1, base=True, day=2))
    s2 = stage(2, game(gid=2, base=False))
    assert evaluate_group_badge(mm, ULTRA, [s1, s2]).base_earned is False   # 0 -> all gating stages
