"""The rarity ratchet: a grade may rise, but never fall.

Rarity is derived from a denominator that only grows, so without a floor a grade drifts DOWN as more
people earn the thing -- a hunter logs in and their Mythic has become Rare. That makes the grade a
weather report rather than a property of the item, and it can quietly take something away.

The floor stays COMMUNITY-LEVEL: it belongs to the badge or title and is identical for every hunter.
Freezing per-hunter at earn time would be worse, because two people would then see different grades on
the same item.
"""
import pytest

from tests.factories import (
    BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory,
)
from trophies.models import GroupBadge, SeriesBadgeStanding, Title, UserTitle
from trophies.services.rarity import effective_pct, rarity_for

pytestmark = pytest.mark.django_db


# ── The rule itself ───────────────────────────────────────────────────────────────────────────────

def test_the_floor_wins_when_the_live_number_has_risen():
    """The whole point: 20% today, but it was once 2%, so it is still graded from 2%."""
    assert effective_pct(20.0, floor_pct=2.0) == 2.0


def test_a_lower_live_number_beats_the_floor():
    """Rarity can still RISE -- the floor is a floor, not a freeze."""
    assert effective_pct(2.0, floor_pct=20.0) == 2.0


def test_no_floor_grades_live():
    """Null floor (nothing recorded yet) must read as 'no floor', not as zero."""
    assert effective_pct(37.0, floor_pct=None) == 37.0


def test_a_grade_does_not_fall_as_the_community_catches_up():
    """1 in 100 -> mythic. Later 40 in 100 would be common, but the floor holds the grade."""
    _, fresh = rarity_for(1, 100)
    _, later = rarity_for(40, 100, floor_pct=1.0)

    assert fresh == 'mythic' and later == 'mythic'


def test_a_grade_still_rises():
    """A thing that gets rarer (its base grew faster than its earners) may still be promoted."""
    _, better = rarity_for(1, 1000, floor_pct=40.0)

    assert better == 'mythic'


def test_the_percentage_shown_is_always_the_LIVE_one():
    """Only the CLASS honours the floor. Printing a ratcheted percentage would be a lie about the
    community -- the number says how many, the grade says how rare."""
    pct, cls = rarity_for(40, 100, floor_pct=1.0)

    assert pct == 40.0 and cls == 'mythic'


def test_an_unearned_thing_gets_no_grade_even_with_a_floor():
    """0 earners is unearned, not an achievement. A stale floor must not conjure a prestige grade."""
    pct, cls = rarity_for(0, 100, floor_pct=1.0)

    assert pct == 0.0 and cls == ''


# ── The maintenance command ───────────────────────────────────────────────────────────────────────

def _series_with_badge(slug, *, pursuers, earned, title_name=None):
    series = BadgeSeriesFactory(series_slug=slug)
    if title_name:
        series.title = Title.objects.create(name=title_name)
        series.save(update_fields=['title'])
    gb = GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)
    GroupBadge.objects.filter(id=gb.id).update(earned_count=earned)
    for _ in range(pursuers):
        SeriesBadgeStanding.objects.create(profile=ProfileFactory(), series_slug=slug,
                                           progress_bp=1000, stages_cleared=1, stages_total=5)
    return series, GroupBadge.objects.get(id=gb.id)


def test_the_command_records_the_floor():
    from django.core.management import call_command

    _, gb = _series_with_badge('ratchet-a', pursuers=100, earned=3)

    call_command('recalc_rarity_floors')

    assert GroupBadge.objects.get(id=gb.id).rarity_floor_pct == 3.0


def test_the_command_only_ever_moves_a_floor_down():
    """Safe to re-run: an extra pass must never inflate a grade."""
    from django.core.management import call_command

    _, gb = _series_with_badge('ratchet-b', pursuers=100, earned=2)
    call_command('recalc_rarity_floors')                       # floor 2.0
    GroupBadge.objects.filter(id=gb.id).update(earned_count=50)

    call_command('recalc_rarity_floors')                       # live is 50% now

    assert GroupBadge.objects.get(id=gb.id).rarity_floor_pct == 2.0


def test_the_command_lowers_a_floor_when_the_thing_gets_rarer():
    from django.core.management import call_command

    series, gb = _series_with_badge('ratchet-c', pursuers=100, earned=30)
    call_command('recalc_rarity_floors')                       # floor 30.0
    for _ in range(900):                                       # base grows, earners don't
        SeriesBadgeStanding.objects.create(profile=ProfileFactory(), series_slug=series.series_slug,
                                           progress_bp=1000, stages_cleared=1, stages_total=5)

    call_command('recalc_rarity_floors')

    assert GroupBadge.objects.get(id=gb.id).rarity_floor_pct == 3.0


def test_a_title_floor_counts_holders_not_badge_earners():
    """A title is granted by ANY live edition, so it is strictly easier than any single edition -- and
    the holder count is the number surfaces print beside the grade."""
    from django.core.management import call_command

    series, gb = _series_with_badge('ratchet-d', pursuers=100, earned=90, title_name='Crate Crusher')
    for _ in range(4):                                         # 4 holders, not 90 earners
        UserTitle.objects.create(profile=ProfileFactory(), title=series.title,
                                 source_type='badge_series', source_id=series.id)

    call_command('recalc_rarity_floors')

    assert Title.objects.get(id=series.title_id).rarity_floor_pct == 4.0


def test_dry_run_writes_nothing():
    from django.core.management import call_command

    _, gb = _series_with_badge('ratchet-e', pursuers=100, earned=3)

    call_command('recalc_rarity_floors', '--dry-run')

    assert GroupBadge.objects.get(id=gb.id).rarity_floor_pct is None
