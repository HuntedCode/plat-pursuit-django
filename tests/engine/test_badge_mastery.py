"""Mastery, Full Set and Series Master in the PURE engine (badge rewards phase 4, owner 2026-10-25).

Mastery: every stage the edition covers (bonus stages and stages that no longer gate included) at the full bar
(100% with DLC) on any one version, and it needs the badge itself. It is offered only when it asks more than
holo: a bonus stage, a stage that no longer gates, or a megamix's stages past its threshold. Full Set: every
edition you can earn, held. Series Master: every such edition finished (mastered, or holo where no Mastery is
offered), offered only when no single edition covers every stage.
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


def bonus(*games, n=9):
    """A bonus stage (Stage.is_bonus): toward Mastery only, usually a region-locked release."""
    return StageInput(n, tuple(games), is_bonus=True)


def ultra(*stages, series=ALL):
    return evaluate_group_badge(series, ULTRA, list(stages))


# -- mastery -----------------------------------------------------------------------------------------------

def test_every_stage_at_full_including_the_bonus_masters_the_badge():
    r = ultra(bonus(game(full_day=9)), stage(1, game(full_day=3)), stage(2, game(full_day=5)))
    assert r.base_earned and r.holo and r.mastered
    assert (r.mastery_done, r.mastery_total) == (3, 3)
    assert r.mastered_date == date(2026, 2, 9), 'mastered when the LAST stage fell, the bonus included'


def test_a_bonus_short_of_full_holds_holo_but_not_mastery():
    r = ultra(bonus(game(full=False)), stage(1, game()), stage(2, game()))
    assert r.holo is True, 'holo never reads a bonus stage'
    assert r.mastered is False and (r.mastery_done, r.mastery_total) == (2, 3)


def test_a_stage_that_no_longer_gates_still_counts_for_mastery():
    dead = game(obtainable=False, base=False, full=False, day=None)
    r = ultra(stage(1, game()), stage(2, dead))
    assert r.base_earned and r.holo, 'a dead stage never gates the badge or its holo'
    assert r.mastered is False and r.mastery_total == 2

    cleared_while_alive = game(obtainable=False)
    assert ultra(stage(1, game()), stage(2, cleared_while_alive)).mastered is True


def test_a_stage_with_nothing_on_the_editions_platforms_is_not_its_business():
    r = ultra(bonus(game()), stage(1, game()), stage(2, game(platforms=('PS3',), base=False, full=False, day=None)))
    assert r.mastered is True and r.mastery_total == 2
    assert r.scope == frozenset({1, 9})


def test_mastery_is_satisfied_cross_platform_like_everything_else():
    r = ultra(bonus(game()), stage(1, game(platforms=('PS4',), full=False), game(platforms=('PS3',))))
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
    assert r.mastery_offered and not r.finished and r.mastered is False
    assert ultra(stage(1, game()), stage(2, game()), series=mix).mastered is True


def test_an_undated_stage_leaves_the_date_unknown_not_wrong():
    r = ultra(bonus(game(full_day=3)), stage(1, game(full_day=3)), stage(2, game(day=None, full_day=0)))
    assert r.mastered is True and r.mastered_date is None


# -- when mastery is offered ---------------------------------------------------------------------------------

def test_a_badge_that_asks_nothing_past_holo_offers_no_mastery():
    """Owner, 2026-10-25: Mastery is above and beyond. Every stage gating, no bonus stage, no megamix: Mastery would
    be holo under another name, so the edition is FINISHED at holo and never mastered."""
    r = ultra(stage(1, game(full_day=3)), stage(2, game(full_day=5)))
    assert r.holo and r.finished and r.finished_date == date(2026, 2, 5)
    assert r.mastery_offered is False and r.mastered is False and r.mastered_date is None


def test_each_extra_offers_mastery():
    dead = game(obtainable=False)
    delisted = GameState(1, frozenset({'PS5'}), True, True, True, True, date(2026, 1, 1), full_date=date(2026, 2, 1))
    region_locked = bonus(game())
    for why, stages in (('a bonus stage (region-locked)', [stage(1, game()), region_locked]),
                        ('a stage that is unobtainable', [stage(1, game()), stage(2, dead)]),
                        ('a stage delisted on Ultra HD', [stage(1, game()), stage(2, delisted)])):
        r = ultra(*stages)
        assert r.mastery_offered and r.mastered, why

    legacy = evaluate_group_badge(ALL, LEGACY, [stage(1, game(platforms=('PS3',))),
                                                stage(2, GameState(1, frozenset({'PS3'}), True, True, True, True))])
    assert legacy.mastery_offered is False, 'Legacy HD counts delisted games, so a delisted stage still gates it'


def test_a_megamix_offers_mastery_only_past_its_threshold():
    two = [stage(1, game()), stage(2, game())]
    assert ultra(*two, series=SeriesInput(completion_policy='min_count', min_required=1)).mastery_offered is True
    assert ultra(*two, series=SeriesInput(completion_policy='min_count', min_required=2)).mastery_offered is False
    assert ultra(*two, series=SeriesInput(completion_policy='min_count', min_required=0)).mastery_offered is False
    assert ultra(*two, series=SeriesInput(completion_policy='min_count', min_required=5)).mastery_offered is False, \
        'a threshold above the gating count is clamped to all of them'


def test_an_edition_with_nothing_left_to_gate_offers_nothing():
    r = ultra(stage(1, game(obtainable=False)), bonus(game()))
    assert r.gating_count == 0 and r.mastery_offered is False


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


def test_series_master_reads_each_editions_own_finish():
    shared = stage(1, game(platforms=('PS3', 'PS5')))
    # Ultra HD has a region-locked bonus stage short of 100%: it is holo but not finished, so no Series Master.
    split = both([shared, bonus(game(full=False)), stage(2, game(platforms=('PS5',)))],
                 [shared, stage(3, game(platforms=('PS3',)))])
    assert split[0].holo and not split[0].finished and split[1].finished and not split[1].mastery_offered
    r = evaluate_series_rewards(split)
    assert r.master_offered is True and r.series_master is False

    split[0] = evaluate_group_badge(ALL, ULTRA, [shared, bonus(game()), stage(2, game(platforms=('PS5',)))])
    assert split[0].mastered and evaluate_series_rewards(split).series_master is True


def test_series_master_needs_every_edition_finished():
    shared = stage(1, game(platforms=('PS3', 'PS5')))
    split = both([shared, stage(2, game(platforms=('PS5',), full=False))], [shared, stage(3, game(platforms=('PS3',)))])
    r = evaluate_series_rewards(split)
    assert r.full_set is True and r.master_offered is True
    assert r.series_master is False and r.series_master_date is None
