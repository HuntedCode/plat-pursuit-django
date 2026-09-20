"""`badge_xp.monthly_xp` -- badge XP bucketed by the month it was earned in.

There is no badge-XP ledger. The engine already carries the dates, so monthly XP is a re-bucketing of the
SAME two components `_group_badge_xp` sums: XP_PER_STAGE per cleared IN-SCOPE stage (at
`StageResult.base_date`) -- gating or not, since 2026-09 -- and XP_BADGE_COMPLETION_BONUS per earned badge
(at `GroupBadgeResult.earned_date`).

That makes drift the whole risk. If someone changes how XP is scored and only touches `_group_badge_xp`, the
recap starts quietly disagreeing with the profile's standing. The reconciliation test below is the guard:
the buckets must sum to exactly the standing total, minus only what has no date to be placed by.
"""
import pytest
from datetime import datetime, timezone as dt_timezone

import pytz

from trophies.services.badge_engine import GroupBadgeResult, StageResult
from trophies.services.badge_xp import (
    XP_BADGE_COMPLETION_BONUS, XP_PER_STAGE, _group_badge_xp, monthly_xp,
)

UTC = dt_timezone.utc


def _dt(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=UTC)


def _stage(n, *, gates=True, base=True, date=None):
    return StageResult(stage_number=n, gates=gates, base_satisfied=base,
                       holo_satisfied=False, base_date=date)


def _result(stages, *, earned=False, earned_date=None, gating_count=None):
    """Mirrors what `evaluate_group_badge` builds, including the two DIFFERENT stage counts:
    `base_satisfied_count` counts only GATING stages (the progress numerator) while `xp_stage_count`
    counts every in-scope stage cleared (the XP numerator). Deriving both here rather than passing them
    is what keeps these buckets reconcilable against the scored total."""
    gating = [s for s in stages if s.gates]
    return GroupBadgeResult(
        base_earned=earned,
        holo=False,
        gating_count=gating_count if gating_count is not None else len(gating),
        base_satisfied_count=sum(1 for s in gating if s.base_satisfied),
        holo_satisfied_count=0,
        earned_date=earned_date,
        stages=stages,
        xp_stage_count=sum(1 for s in stages if s.base_satisfied),
    )


def test_stage_clears_land_in_the_month_they_happened():
    res = _result([
        _stage(1, date=_dt(2026, 3, 4)),
        _stage(2, date=_dt(2026, 3, 27)),
        _stage(3, date=_dt(2026, 5, 1)),
    ])
    assert monthly_xp([res]) == {(2026, 3): 2 * XP_PER_STAGE, (2026, 5): XP_PER_STAGE}


def test_the_completion_bonus_lands_on_the_earn_date():
    res = _result(
        [_stage(1, date=_dt(2026, 3, 4)), _stage(2, date=_dt(2026, 4, 9))],
        earned=True, earned_date=_dt(2026, 4, 9),
    )
    assert monthly_xp([res]) == {
        (2026, 3): XP_PER_STAGE,
        (2026, 4): XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS,
    }


def test_a_stage_that_stopped_gating_still_pays():
    """XP is over every IN-SCOPE stage cleared, gating or not -- `xp_stage_count`, not
    `base_satisfied_count`. A stage stops gating when its qualifying games go unobtainable or delisted,
    which is a fact about the storefront, not about the hunter who already cleared it.

    (This docstring used to say the opposite -- "XP is over GATING stages only" -- left behind when the
    test was inverted. A reader repairing a future failure from it would have reintroduced the bug.)
    """
    res = _result([
        _stage(1, date=_dt(2026, 3, 4)),
        _stage(2, gates=False, date=_dt(2026, 3, 5)),
    ])
    # BOTH stages pay. Stage 2 is in scope and cleared; it simply stopped being REQUIRED.
    assert monthly_xp([res]) == {(2026, 3): 2 * XP_PER_STAGE}


def test_uncleared_stages_earn_nothing():
    res = _result([
        _stage(1, date=_dt(2026, 3, 4)),
        _stage(2, base=False, date=None),
    ])
    assert monthly_xp([res]) == {(2026, 3): XP_PER_STAGE}


def test_a_group_that_gates_nothing_still_pays_for_cleared_stages():
    """gating_count == 0 means the badge is no longer earnable in this platform group -- every stage's
    qualifying games went unobtainable or delisted. The hunter loses the HOLD (the engine returns
    base_earned=False, and apply revokes) and the completion bonus with it, but keeps the stage drip:
    the work was done, and a storefront closing later is not theirs to answer for.

    The two must agree, because the recap reads the buckets and the profile standing reads the score. If
    this one skipped the group the way it used to, the recap would silently shed XP the standing still
    shows. (Owner's call, 2026-09; before that both returned 0.)
    """
    res = _result([_stage(1, gates=False, date=_dt(2026, 3, 4))], gating_count=0)
    assert _group_badge_xp(res) == XP_PER_STAGE
    assert monthly_xp([res]) == {(2026, 3): XP_PER_STAGE}


def test_an_unearnable_group_is_paid_the_stages_but_never_the_bonus():
    """The bonus is for FINISHING the badge, and an unearnable badge cannot be finished. The engine never
    reports base_earned on a zero-gating group, so this pins the arithmetic that follows from it."""
    res = _result([_stage(1, gates=False, date=_dt(2026, 3, 4)),
                   _stage(2, gates=False, date=_dt(2026, 4, 4))], gating_count=0)

    assert _group_badge_xp(res) == 2 * XP_PER_STAGE
    # The WHOLE dict, not `BONUS not in values()`. That check cannot see a bonus folded into a bucket
    # that already holds a stage clear -- 600 + 500 = 1100, and `600 not in {1100}` is true. Mutation-
    # proven: paying the bonus to a zero-gating group left the old assertion green.
    assert monthly_xp([res]) == {(2026, 3): XP_PER_STAGE, (2026, 4): XP_PER_STAGE}


def test_dateless_clears_are_dropped_not_guessed():
    """A cleared stage with no completion date cannot be placed in a month. It must contribute nothing
    rather than fall into an arbitrary bucket."""
    res = _result([_stage(1, date=None), _stage(2, date=_dt(2026, 3, 4))])
    assert monthly_xp([res]) == {(2026, 3): XP_PER_STAGE}


def test_buckets_are_local_months_not_utc():
    """A clear at 2026-04-01 02:00 UTC is still MARCH for a hunter in Los Angeles, and their recap is
    built in their timezone."""
    res = _result([_stage(1, date=_dt(2026, 4, 1, h=2))])
    la = pytz.timezone('America/Los_Angeles')
    assert monthly_xp([res]) == {(2026, 4): XP_PER_STAGE}
    assert monthly_xp([res], la) == {(2026, 3): XP_PER_STAGE}


def test_empty_input():
    assert monthly_xp([]) == {}


# --- the guard that matters -----------------------------------------------------------------------

@pytest.mark.parametrize('tz', [None, pytz.timezone('America/Los_Angeles'), pytz.UTC])
def test_buckets_reconcile_against_the_scored_total(tz):
    """Every unit of XP the standings credit must appear in exactly one month -- no double-count, no loss.
    This is what catches a scoring change that only lands in one of the two functions."""
    results = [
        _result([_stage(1, date=_dt(2026, 1, 5)), _stage(2, date=_dt(2026, 3, 4))],
                earned=True, earned_date=_dt(2026, 3, 4)),
        _result([_stage(1, date=_dt(2025, 11, 30)), _stage(2, base=False),
                 _stage(3, gates=False, date=_dt(2026, 2, 2))]),
        _result([_stage(1, date=_dt(2026, 4, 1, h=2)), _stage(2, date=_dt(2026, 4, 20))],
                earned=True, earned_date=_dt(2026, 4, 20)),
    ]

    scored = sum(_group_badge_xp(r) for r in results)
    bucketed = sum(monthly_xp(results, tz).values())
    assert bucketed == scored, (
        f'monthly_xp bucketed {bucketed} but _group_badge_xp scored {scored} -- the recap and the '
        f'profile standing would show different numbers for the same work'
    )


def test_reconciliation_is_exact_only_when_every_clear_has_a_date():
    """The one legitimate shortfall, stated explicitly so it is never mistaken for drift: a clear with no
    completion date is scored by _group_badge_xp but cannot be placed in a month."""
    results = [_result([_stage(1, date=None), _stage(2, date=_dt(2026, 3, 4))])]

    scored = sum(_group_badge_xp(r) for r in results)
    bucketed = sum(monthly_xp(results).values())
    assert bucketed == scored - XP_PER_STAGE
    assert bucketed < scored
