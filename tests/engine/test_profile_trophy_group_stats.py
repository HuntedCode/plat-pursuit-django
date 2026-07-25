"""Tests for the per-group standings denorm (ProfileTrophyGroup) behind the group/speed leaderboards.

Covers PsnApiService.update_trophy_group_stats (the whale-safe aggregate that both sync and the backfill
run) and the backfill_profile_trophy_groups command: per-group progress/tiers/timestamps, the speed
completion gate (fully earned AND >=2 trophies), the single-trophy exclusion, DLC-group scoping, the
floored percentage, no-row-when-nothing-earned, and upsert idempotency.

The denominator is the real Trophy-row count in a group, so each test creates the FULL trophy set for a
group and earns a subset -- earning K of N trophies must read K/N.
"""
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from tests.factories import GameFactory, ProfileFactory, ProfileGameFactory, TrophyFactory, EarnedTrophyFactory
from trophies.models import ProfileTrophyGroup, TrophyGroup
from trophies.services.psn_api_service import PsnApiService

pytestmark = pytest.mark.django_db


def _group(game, gid):
    return TrophyGroup.objects.create(game=game, trophy_group_id=gid, defined_trophies={})


def _trophy(game, gid, ttype, tid):
    """A Trophy row in a group. The number of these in a group is the leaderboard denominator."""
    return TrophyFactory(game=game, trophy_group_id=gid, trophy_type=ttype, trophy_id=tid)


def _earn(profile, trophy, when):
    return EarnedTrophyFactory(profile=profile, trophy=trophy, earned=True, earned_date_time=when)


def _run(profile, game):
    PsnApiService.update_trophy_group_stats([profile.id], [game.id])


# --- progress + tiers --------------------------------------------------------


def test_partial_group_gets_floored_progress_and_no_completion():
    game = GameFactory()
    _group(game, 'default')
    # A four-trophy group: 2 bronze, 1 silver, 1 platinum.
    b1 = _trophy(game, 'default', 'bronze', 1)
    _trophy(game, 'default', 'bronze', 2)                  # exists, left unearned
    s1 = _trophy(game, 'default', 'silver', 3)
    _trophy(game, 'default', 'platinum', 4)               # exists, left unearned
    profile = ProfileFactory()
    now = timezone.now()
    _earn(profile, b1, now)
    _earn(profile, s1, now + timedelta(hours=1))          # 2 of 4 earned

    _run(profile, game)

    ptg = ProfileTrophyGroup.objects.get(profile=profile, trophy_group__trophy_group_id='default')
    assert ptg.progress == 50
    assert ptg.completion_seconds is None                 # not fully earned -> not on the speed board
    assert ptg.earned_trophies == {'platinum': 0, 'gold': 0, 'silver': 1, 'bronze': 1}


def test_progress_floors_below_a_full_group():
    """3 of 4 must read 75, and 100 only when literally everything is earned (floor, not round)."""
    game = GameFactory()
    _group(game, 'default')
    trophies = [_trophy(game, 'default', 'bronze', i) for i in range(4)]
    profile = ProfileFactory()
    now = timezone.now()
    for i in range(3):
        _earn(profile, trophies[i], now + timedelta(minutes=i))

    _run(profile, game)

    ptg = ProfileTrophyGroup.objects.get(profile=profile)
    assert ptg.progress == 75
    assert ptg.completion_seconds is None


# --- speed / completion gate -------------------------------------------------


def test_full_multi_trophy_group_records_completion_seconds():
    game = GameFactory()
    _group(game, 'default')
    b1 = _trophy(game, 'default', 'bronze', 1)
    b2 = _trophy(game, 'default', 'bronze', 2)
    plat = _trophy(game, 'default', 'platinum', 3)
    profile = ProfileFactory()
    start = timezone.now()
    _earn(profile, b1, start)
    _earn(profile, b2, start + timedelta(days=5, hours=6))
    _earn(profile, plat, start + timedelta(days=5, hours=6))

    _run(profile, game)

    ptg = ProfileTrophyGroup.objects.get(profile=profile)
    assert ptg.progress == 100
    assert ptg.first_trophy_at == start
    assert ptg.completion_seconds == int(timedelta(days=5, hours=6).total_seconds())


def test_single_trophy_group_never_gets_a_speed_time():
    """A one-trophy group is fully earned instantly (first == last); its speed board would duplicate the
    progress board, so completion_seconds stays null even at 100%."""
    game = GameFactory()
    _group(game, 'default')
    only = _trophy(game, 'default', 'bronze', 1)
    profile = ProfileFactory()
    _earn(profile, only, timezone.now())

    _run(profile, game)

    ptg = ProfileTrophyGroup.objects.get(profile=profile)
    assert ptg.progress == 100
    assert ptg.completion_seconds is None                 # only 1 trophy -> no speed board


def test_completion_seconds_zero_when_all_earned_in_one_batch():
    """Two trophies popped at the same timestamp is a legitimate <1m completion, not a data error."""
    game = GameFactory()
    _group(game, 'default')
    b1 = _trophy(game, 'default', 'bronze', 1)
    b2 = _trophy(game, 'default', 'bronze', 2)
    profile = ProfileFactory()
    now = timezone.now()
    _earn(profile, b1, now)
    _earn(profile, b2, now)

    _run(profile, game)

    ptg = ProfileTrophyGroup.objects.get(profile=profile)
    assert ptg.progress == 100
    assert ptg.completion_seconds == 0


# --- group scoping -----------------------------------------------------------


def test_base_and_dlc_groups_are_scoped_independently():
    game = GameFactory()
    _group(game, 'default')
    _group(game, '001')
    b1 = _trophy(game, 'default', 'bronze', 1)
    plat = _trophy(game, 'default', 'platinum', 2)
    g1 = _trophy(game, '001', 'gold', 3)
    g2 = _trophy(game, '001', 'gold', 4)
    profile = ProfileFactory()
    base_start = timezone.now()
    _earn(profile, b1, base_start)
    _earn(profile, plat, base_start + timedelta(hours=2))
    dlc_start = base_start + timedelta(days=10)
    _earn(profile, g1, dlc_start)
    _earn(profile, g2, dlc_start + timedelta(hours=1))

    _run(profile, game)

    base = ProfileTrophyGroup.objects.get(profile=profile, trophy_group__trophy_group_id='default')
    dlc = ProfileTrophyGroup.objects.get(profile=profile, trophy_group__trophy_group_id='001')
    assert base.progress == 100 and dlc.progress == 100
    assert base.completion_seconds == int(timedelta(hours=2).total_seconds())      # scoped to base timestamps
    assert dlc.completion_seconds == int(timedelta(hours=1).total_seconds())       # scoped to DLC, ignores the 10-day gap


def test_no_row_for_a_group_with_nothing_earned():
    game = GameFactory()
    _group(game, 'default')
    _group(game, '001')                                    # exists with trophies, but the profile earns none
    base = _trophy(game, 'default', 'bronze', 1)
    _trophy(game, '001', 'gold', 2)
    _trophy(game, '001', 'gold', 3)
    profile = ProfileFactory()
    _earn(profile, base, timezone.now())

    _run(profile, game)

    assert ProfileTrophyGroup.objects.filter(profile=profile).count() == 1
    assert not ProfileTrophyGroup.objects.filter(profile=profile, trophy_group__trophy_group_id='001').exists()


def test_unearned_trophies_do_not_count_toward_progress_or_timestamps():
    game = GameFactory()
    _group(game, 'default')
    b1 = _trophy(game, 'default', 'bronze', 1)
    b2 = _trophy(game, 'default', 'bronze', 2)
    b3 = _trophy(game, 'default', 'bronze', 3)
    _trophy(game, 'default', 'bronze', 4)                  # 4 trophies total
    profile = ProfileFactory()
    now = timezone.now()
    _earn(profile, b1, now)
    _earn(profile, b2, now + timedelta(hours=1))
    # an UNEARNED row with a spurious late timestamp must not move last_trophy_at
    EarnedTrophyFactory(profile=profile, trophy=b3, earned=False, earned_date_time=now + timedelta(days=99))

    _run(profile, game)

    ptg = ProfileTrophyGroup.objects.get(profile=profile)
    assert ptg.progress == 50                              # 2 of 4, unearned excluded from the numerator
    assert ptg.last_trophy_at == now + timedelta(hours=1)  # not the unearned 99-day trophy


# --- idempotency + backfill --------------------------------------------------


def test_rerun_upserts_rather_than_duplicating():
    game = GameFactory()
    _group(game, 'default')
    b1 = _trophy(game, 'default', 'bronze', 1)
    b2 = _trophy(game, 'default', 'bronze', 2)
    profile = ProfileFactory()
    now = timezone.now()
    _earn(profile, b1, now)

    _run(profile, game)                                    # 1 of 2 -> 50%
    _earn(profile, b2, now + timedelta(hours=3))
    _run(profile, game)                                    # 2 of 2 -> 100%, same row

    ptg = ProfileTrophyGroup.objects.get(profile=profile)      # .get asserts exactly one
    assert ptg.progress == 100
    assert ptg.completion_seconds == int(timedelta(hours=3).total_seconds())


def test_backfill_command_populates_all_profiles():
    game = GameFactory()
    _group(game, 'default')
    p1, p2 = ProfileFactory(), ProfileFactory()
    for p in (p1, p2):
        ProfileGameFactory(profile=p, game=game, progress=100)
        _earn(p, _trophy(game, 'default', 'bronze', p.id), timezone.now())

    call_command('backfill_profile_trophy_groups')

    assert ProfileTrophyGroup.objects.filter(profile=p1).count() == 1
    assert ProfileTrophyGroup.objects.filter(profile=p2).count() == 1


def test_backfill_single_username():
    game = GameFactory()
    _group(game, 'default')
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=game, progress=100)
    _earn(profile, _trophy(game, 'default', 'bronze', 1), timezone.now())

    call_command('backfill_profile_trophy_groups', username=profile.psn_username)

    assert ProfileTrophyGroup.objects.filter(profile=profile).count() == 1
