"""Mastery, Full Set and Series Master in the PURE engine (badge rewards phase 4, owner 2026-10-25).

Mastery: every stage the edition covers (stage 0 and stages that no longer gate included) at the full bar
(100% with DLC) on any one version, and it needs the badge itself. Full Set: every edition you can earn,
held. Series Master: every such edition mastered, offered only when no single edition covers every stage.
"""
from datetime import date

from trophies.services.badge_engine import (
    GameState, StageInput, GroupInput, SeriesInput, evaluate_group_badge, evaluate_series_rewards,
)

ULTRA = GroupInput(platforms=frozenset({'PS4', 'PS5'}), exclude_delisted=True)
LEGACY = GroupInput(platforms=frozenset({'PS3', 'PSVITA'}), exclude_delisted=False)
ALL = SeriesInput(completion_policy='all')


def game(platforms=('PS5',), obtainable=True, base=True, full=True, day=1, full_day=None):
    full_day = full_day if full_day is not None else (day if full else None)
    return GameState(1, frozenset(platforms), obtainable, False, base or full, full,
                     date(2026, 1, day) if day else None,
                     full_date=date(2026, 2, full_day) if full and full_day else None)


def stage(n, *games):
    return StageInput(n, tuple(games))


def ultra(*stages, series=ALL):
    return evaluate_group_badge(series, ULTRA, list(stages))


# -- mastery -----------------------------------------------------------------------------------------------

def test_every_stage_at_full_including_stage_zero_masters_the_badge():
    r = ultra(stage(0, game(full_day=9)), stage(1, game(full_day=3)), stage(2, game(full_day=5)))
    assert r.base_earned and r.holo and r.mastered
    assert (r.mastery_done, r.mastery_total) == (3, 3)
    assert r.mastered_date == date(2026, 2, 9), 'mastered when the LAST stage fell, stage 0 included'


def test_stage_zero_short_of_full_holds_holo_but_not_mastery():
    r = ultra(stage(0, game(full=False)), stage(1, game()), stage(2, game()))
    assert r.holo is True, 'holo never reads stage 0'
    assert r.mastered is False and (r.mastery_done, r.mastery_total) == (2, 3)


def test_a_stage_that_no_longer_gates_still_counts_for_mastery():
    dead = game(obtainable=False, base=False, full=False, day=None)
    r = ultra(stage(1, game()), stage(2, dead))
    assert r.base_earned and r.holo, 'a dead stage never gates the badge or its holo'
    assert r.mastered is False and r.mastery_total == 2

    cleared_while_alive = game(obtainable=False)
    assert ultra(stage(1, game()), stage(2, cleared_while_alive)).mastered is True


def test_a_stage_with_nothing_on_the_editions_platforms_is_not_its_business():
    r = ultra(stage(1, game()), stage(2, game(platforms=('PS3',), base=False, full=False, day=None)))
    assert r.mastered is True and r.mastery_total == 1
    assert r.scope == frozenset({1})


def test_mastery_is_satisfied_cross_platform_like_everything_else():
    r = ultra(stage(1, game(platforms=('PS4',), full=False), game(platforms=('PS3',))))
    assert r.mastered is True, 'the PS3 version at 100% masters the stage for Ultra HD'


def test_no_hold_no_mastery():
    # Every stage dead: nothing gates, the badge is revoked, so there is nothing to master.
    r = ultra(stage(1, game(obtainable=False)))
    assert r.gating_count == 0 and r.base_earned is False
    assert r.mastered is False and r.mastery_done == 1


def test_a_megamix_is_mastered_only_on_every_stage():
    mix = SeriesInput(completion_policy='min_count', min_required=1)
    r = ultra(stage(1, game()), stage(2, game(full=False)), series=mix)
    assert r.base_earned and r.holo
    assert r.mastered is False


def test_an_undated_stage_leaves_the_date_unknown_not_wrong():
    r = ultra(stage(1, game(full_day=3)), stage(2, game(day=None, full_day=0)))
    assert r.mastered is True and r.mastered_date is None


# -- series rewards ----------------------------------------------------------------------------------------

def both(ultra_stages, legacy_stages):
    return [evaluate_group_badge(ALL, ULTRA, ultra_stages), evaluate_group_badge(ALL, LEGACY, legacy_stages)]


def test_a_full_set_is_every_edition_held():
    held = stage(1, game(platforms=('PS3', 'PS5')))
    r = evaluate_series_rewards(both([held], [held]))
    assert r.full_set is True and r.full_set_date == date(2026, 1, 1)

    unheld = stage(1, game(platforms=('PS3', 'PS5'), base=False, full=False, day=None))
    assert evaluate_series_rewards(both([held], [unheld])).full_set is False


def test_an_edition_that_cannot_be_earned_never_blocks_a_full_set():
    # Three editions; the third has nothing left to gate, so a set of the other two is the whole set.
    held = stage(1, game(platforms=('PS3', 'PS5')))
    dead = evaluate_group_badge(ALL, GroupInput(frozenset({'PS5'}), True),
                                [stage(1, game(obtainable=False, base=False, full=False, day=None))])
    assert evaluate_series_rewards(both([held], [held]) + [dead]).full_set is True


def test_one_earnable_edition_is_not_a_set():
    held = stage(1, game(platforms=('PS3', 'PS5')))
    assert evaluate_series_rewards([evaluate_group_badge(ALL, ULTRA, [held])]).full_set is False


def test_series_master_is_offered_only_when_no_edition_covers_every_stage():
    shared = stage(1, game(platforms=('PS3', 'PS5')))
    # Ultra covers stages 1 and 2, Legacy covers 1 and 3: neither covers all three.
    split = both([shared, stage(2, game(platforms=('PS5',)))], [shared, stage(3, game(platforms=('PS3',)))])
    r = evaluate_series_rewards(split)
    assert r.master_offered is True and r.series_master is True

    # Ultra covers every stage: mastering it already masters Legacy, so Series Master would say nothing.
    covered = both([shared, stage(2, game(platforms=('PS3', 'PS5')))], [shared, stage(2, game(platforms=('PS3', 'PS5')))])
    r = evaluate_series_rewards(covered)
    assert r.master_offered is False and r.series_master is False


def test_series_master_needs_every_edition_mastered():
    shared = stage(1, game(platforms=('PS3', 'PS5')))
    split = both([shared, stage(2, game(platforms=('PS5',), full=False))], [shared, stage(3, game(platforms=('PS3',)))])
    r = evaluate_series_rewards(split)
    assert r.full_set is True and r.master_offered is True
    assert r.series_master is False and r.series_master_date is None
