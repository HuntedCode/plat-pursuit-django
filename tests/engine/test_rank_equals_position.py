"""A hunter's rank must be the row they find when they scroll to it (2026-08 audit).

There are two independent definitions of "rank" in the leaderboard layer, and a reader meets both:

  `page()`      numbers rows by SLOT     -> offset + i + 1        (the number beside their name)
  `*_rank()`    counts everyone AHEAD    -> count(ahead) + 1      (the number in the header)

They agree only if no two rows can tie. Ties are not an edge case here: Badge Points is quantized to
`500a + 600b`, so hundreds of hunters land on exactly 1,600; `progress_bp` is `cleared / gating`, so a
3-stage series stacks every chaser onto 1/3 or 2/3. Counting only the visible sort keys returned the tie
group's FIRST slot to every member of it -- the twelfth hunter on 1,600 points was told "#7" in the header
and then found their own name at row 18.

The fix is the one `game_leaderboard_service` already made: end every board's canonical order in
`profile_id`, a unique final key that makes the order TOTAL, and express that same full key list in the
rank count. Its docstring calls the unique tail "load-bearing, not decoration". These tests hold that
property for the badge/career/job boards, which had the tail in their ORDER BY and not in their counts.

Written as "the two agree", never as "rank == 7": an assertion on a specific number would pin today's
fixture, and this is a property about two functions matching each other.
"""
import datetime as dt

import pytest

from trophies.models import (
    ProfileBadgeStanding, ProfileCareerStanding, ProfileEditionStanding, ProfileJobXP,
    SeriesBadgeStanding,
)
from trophies.services import badge_leaderboards as lb
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def _positions(rows):
    """{profile_id: 1-based slot} for a board read, i.e. exactly what `page()` would number them."""
    return {row[0]: i + 1 for i, row in enumerate(rows)}


def _assert_agrees(rows, rank_fn, label):
    """Every row's slot equals the rank function's answer for that profile."""
    positions = _positions(rows)
    assert positions, f'{label}: fixture produced no rows'
    mismatches = {
        pid: (slot, rank_fn(pid)) for pid, slot in positions.items() if rank_fn(pid) != slot
    }
    assert not mismatches, (
        f'{label}: the header rank disagrees with the row a reader would find. '
        f'{{profile: (slot_in_wall, rank_reported)}} = {mismatches}'
    )


# ------------------------------------------------------------------ Badge Points -------------------------

def test_badge_points_rank_equals_position_across_a_large_tie():
    """The realistic shape: XP is quantized, so a tie group is the norm. Twelve hunters on one value is
    what produced the original report."""
    tied = [ProfileFactory() for _ in range(12)]
    for p in tied:
        ProfileBadgeStanding.objects.create(profile=p, total_xp=1600, trophies_total=10)
    ProfileBadgeStanding.objects.create(
        profile=ProfileFactory(), total_xp=9000, trophies_total=99)   # clear leader above the tie
    ProfileBadgeStanding.objects.create(
        profile=ProfileFactory(), total_xp=100, trophies_total=1)     # and one below it

    _assert_agrees(lb.xp_rows(limit=100), lb.xp_rank, 'Badge Points')


def test_badge_trophies_rank_equals_position_when_both_figures_tie():
    """Two sort keys, so the tie has to be on BOTH before the tail decides. Stopping the count at
    `trophies_total` looks correct and still ties everyone matching on both."""
    for _ in range(8):
        ProfileBadgeStanding.objects.create(
            profile=ProfileFactory(), total_xp=500, trophies_platinum=5, trophies_total=120)
    ProfileBadgeStanding.objects.create(
        profile=ProfileFactory(), total_xp=500, trophies_platinum=5, trophies_total=400)   # same plats, more
    ProfileBadgeStanding.objects.create(
        profile=ProfileFactory(), total_xp=500, trophies_platinum=9, trophies_total=10)    # more plats

    _assert_agrees(lb.badge_trophy_rows(limit=100), lb.badge_trophy_rank, 'Badge Trophies')


def test_career_rank_equals_position_across_a_tie():
    for _ in range(6):
        ProfileCareerStanding.objects.create(profile=ProfileFactory(), total_xp=750, pursuer_level=4)
    ProfileCareerStanding.objects.create(profile=ProfileFactory(), total_xp=2000, pursuer_level=9)

    _assert_agrees(lb.career_xp_rows(limit=100), lb.career_xp_rank, 'Career XP')


def test_job_rank_equals_position_across_a_tie():
    from trophies.models import Job

    job = Job.objects.create(slug='ranker', name='Ranker', discipline='combat')
    for _ in range(5):
        ProfileJobXP.objects.create(profile=ProfileFactory(), job=job, total_xp=300, level=2)
    ProfileJobXP.objects.create(profile=ProfileFactory(), job=job, total_xp=1200, level=6)

    _assert_agrees(lb.job_rows('ranker', limit=100), lambda pid: lb.job_rank('ranker', pid), 'Job board')


# ------------------------------------------------------------------ the per-series board -----------------

def test_series_board_rank_equals_position_including_the_null_advance_date():
    """The hardest ordering: a discrete progress key, a nullable date tiebreak that sorts NULLS LAST, and
    then the unique tail. All three have to be in the count."""
    def standing(bp, on):
        return SeriesBadgeStanding.objects.create(
            profile=ProfileFactory(), series_slug='ranks', xp=1, progress_bp=bp, advanced_at=on)

    standing(10000, dt.date(2026, 1, 1))
    standing(10000, dt.date(2026, 5, 1))
    for _ in range(4):
        standing(6667, dt.date(2026, 2, 1))     # same rung, same date -> only the tail separates them
    standing(6667, None)                        # no advance date: sorts LAST within its rung
    standing(3333, dt.date(2026, 1, 15))

    rows = lb.series_board_rows('ranks', limit=100)
    _assert_agrees(rows, lambda pid: lb.series_board_rank('ranks', pid), 'Series board')

    # The null explicitly sorts below the dated rows on the same rung, not above them.
    ids = [r[0] for r in rows]
    null_row = SeriesBadgeStanding.objects.get(series_slug='ranks', advanced_at__isnull=True)
    dated_same_rung = SeriesBadgeStanding.objects.filter(
        series_slug='ranks', progress_bp=6667, advanced_at__isnull=False)
    for other in dated_same_rung:
        assert ids.index(null_row.profile_id) > ids.index(other.profile_id), (
            'a hunter with no advance date outranked one who has advanced, on the same rung'
        )


# ------------------------------------------------------------------ the filters compose ------------------

def test_rank_equals_position_under_a_country_slice():
    """A slice is a different board with a different population, so the property has to hold inside it --
    the rank must be counted against the same slice the rows came from."""
    for _ in range(5):
        ProfileBadgeStanding.objects.create(
            profile=ProfileFactory(country_code='CA'), total_xp=1600, trophies_total=10,
            country_code='CA')
    for _ in range(3):
        ProfileBadgeStanding.objects.create(
            profile=ProfileFactory(country_code='GB'), total_xp=1600, trophies_total=10,
            country_code='GB')

    _assert_agrees(lb.xp_rows(limit=100, country='CA'),
                   lambda pid: lb.xp_rank(pid, country='CA'), 'Badge Points (CA)')


def test_rank_equals_position_under_an_edition_slice():
    for _ in range(5):
        ProfileEditionStanding.objects.create(
            profile=ProfileFactory(), platform_group_key='ultra-hd', total_xp=1600, trophies_total=10)
    ProfileEditionStanding.objects.create(
        profile=ProfileFactory(), platform_group_key='legacy-hd', total_xp=9999, trophies_total=99)

    _assert_agrees(lb.xp_rows(limit=100, edition='ultra-hd'),
                   lambda pid: lb.xp_rank(pid, edition='ultra-hd'), 'Badge Points (Ultra HD)')


# ------------------------------------------------------------------ the key lists are honest -------------

@pytest.mark.parametrize('keys, label', [
    (lb.XP_KEYS, 'XP_KEYS'),
    (lb.TROPHY_KEYS, 'TROPHY_KEYS'),
    (lb.CAREER_KEYS, 'CAREER_KEYS'),
    (lb.SERIES_BOARD_KEYS, 'SERIES_BOARD_KEYS'),
    (lb.JOB_KEYS, 'JOB_KEYS'),
])
def test_every_board_order_ends_in_the_unique_key(keys, label):
    """The property the tests above depend on. Without a unique final key the order is not total, ties are
    resolved arbitrarily by Postgres, and rank/position can disagree run to run rather than consistently --
    which is far harder to notice than a stable wrong number."""
    assert keys[-1] == ('profile_id', lb._ASC), (
        f'{label} does not end in the unique profile_id key, so its ordering is not total'
    )
