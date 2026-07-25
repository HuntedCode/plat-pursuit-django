"""Tests for the per-group standings denorm (ProfileTrophyGroup) behind the group/speed leaderboards.

Covers PsnApiService.update_trophy_group_stats (the whale-safe aggregate that both sync and the backfill
run) and the backfill_profile_trophy_groups command: per-group progress/tiers/timestamps, the speed
completion gate (fully earned AND >=2 trophies), the single-trophy exclusion, DLC-group scoping, the
floored percentage, no-row-when-nothing-earned, and upsert idempotency.
"""
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from tests.factories import GameFactory, ProfileFactory, TrophyFactory, EarnedTrophyFactory
from trophies.models import ProfileTrophyGroup, TrophyGroup
from trophies.services.psn_api_service import PsnApiService

pytestmark = pytest.mark.django_db


def _group(game, gid, defined):
    return TrophyGroup.objects.create(game=game, trophy_group_id=gid, defined_trophies=defined)


def _trophy(game, gid, ttype, tid):
    return TrophyFactory(game=game, trophy_group_id=gid, trophy_type=ttype, trophy_id=tid)


def _earn(profile, trophy, when):
    return EarnedTrophyFactory(profile=profile, trophy=trophy, earned=True, earned_date_time=when)


def _run(profile, game):
    PsnApiService.update_trophy_group_stats([profile.id], [game.id])


# --- progress + tiers --------------------------------------------------------


def test_partial_group_gets_floored_progress_and_no_completion():
    game = GameFactory()
    _group(game, 'default', {'bronze': 2, 'silver': 1, 'platinum': 1})   # total 4
    profile = ProfileFactory()
    now = timezone.now()
    _earn(profile, _trophy(game, 'default', 'bronze', 1), now)
    _earn(profile, _trophy(game, 'default', 'silver', 2), now + timedelta(hours=1))
    # 2 of 4 earned

    _run(profile, game)

    ptg = ProfileTrophyGroup.objects.get(profile=profile, trophy_group__trophy_group_id='default')
    assert ptg.progress == 50                             # 2 of 4 earned
    assert ptg.completion_seconds is None                 # not fully earned -> not on the speed board
    assert ptg.earned_trophies == {'platinum': 0, 'gold': 0, 'silver': 1, 'bronze': 1}


def test_progress_floors_below_a_full_group():
    """3 of 4 must read 75, and 100 only when literally everything is earned (floor, not round)."""
    game = GameFactory()
    _group(game, 'default', {'bronze': 4})                # total 4
    profile = ProfileFactory()
    now = timezone.now()
    for i in range(3):
        _earn(profile, _trophy(game, 'default', 'bronze', i), now + timedelta(minutes=i))

    _run(profile, game)

    ptg = ProfileTrophyGroup.objects.get(profile=profile)
    assert ptg.progress == 75
    assert ptg.completion_seconds is None


# --- speed / completion gate -------------------------------------------------


def test_full_multi_trophy_group_records_completion_seconds():
    game = GameFactory()
    _group(game, 'default', {'bronze': 2, 'platinum': 1})   # total 3
    profile = ProfileFactory()
    start = timezone.now()
    _earn(profile, _trophy(game, 'default', 'bronze', 1), start)
    _earn(profile, _trophy(game, 'default', 'bronze', 2), start + timedelta(days=5, hours=6))
    _earn(profile, _trophy(game, 'default', 'platinum', 3), start + timedelta(days=5, hours=6))

    _run(profile, game)

    ptg = ProfileTrophyGroup.objects.get(profile=profile)
    assert ptg.progress == 100
    assert ptg.first_trophy_at == start
    assert ptg.completion_seconds == int(timedelta(days=5, hours=6).total_seconds())


def test_single_trophy_group_never_gets_a_speed_time():
    """A one-trophy group is fully earned instantly (first == last); its speed board would duplicate the
    progress board, so completion_seconds stays null even at 100%."""
    game = GameFactory()
    _group(game, 'default', {'bronze': 1})                 # total 1
    profile = ProfileFactory()
    _earn(profile, _trophy(game, 'default', 'bronze', 1), timezone.now())

    _run(profile, game)

    ptg = ProfileTrophyGroup.objects.get(profile=profile)
    assert ptg.progress == 100
    assert ptg.completion_seconds is None                 # total < 2 -> no speed board


def test_completion_seconds_zero_when_all_earned_in_one_batch():
    """Two trophies popped at the same timestamp is a legitimate <1m completion, not a data error."""
    game = GameFactory()
    _group(game, 'default', {'bronze': 2})
    profile = ProfileFactory()
    now = timezone.now()
    _earn(profile, _trophy(game, 'default', 'bronze', 1), now)
    _earn(profile, _trophy(game, 'default', 'bronze', 2), now)

    _run(profile, game)

    ptg = ProfileTrophyGroup.objects.get(profile=profile)
    assert ptg.progress == 100
    assert ptg.completion_seconds == 0


# --- group scoping -----------------------------------------------------------


def test_base_and_dlc_groups_are_scoped_independently():
    game = GameFactory()
    _group(game, 'default', {'bronze': 1, 'platinum': 1})   # total 2
    _group(game, '001', {'gold': 2})                        # total 2 (a DLC)
    profile = ProfileFactory()
    base_start = timezone.now()
    _earn(profile, _trophy(game, 'default', 'bronze', 1), base_start)
    _earn(profile, _trophy(game, 'default', 'platinum', 2), base_start + timedelta(hours=2))
    dlc_start = base_start + timedelta(days=10)
    _earn(profile, _trophy(game, '001', 'gold', 3), dlc_start)
    _earn(profile, _trophy(game, '001', 'gold', 4), dlc_start + timedelta(hours=1))

    _run(profile, game)

    base = ProfileTrophyGroup.objects.get(profile=profile, trophy_group__trophy_group_id='default')
    dlc = ProfileTrophyGroup.objects.get(profile=profile, trophy_group__trophy_group_id='001')
    assert base.progress == 100 and dlc.progress == 100
    assert base.completion_seconds == int(timedelta(hours=2).total_seconds())      # scoped to base timestamps
    assert dlc.completion_seconds == int(timedelta(hours=1).total_seconds())       # scoped to DLC timestamps, ignores the 10-day gap


def test_no_row_for_a_group_with_nothing_earned():
    game = GameFactory()
    _group(game, 'default', {'bronze': 1})
    _group(game, '001', {'gold': 2})                        # exists but the profile earns nothing in it
    profile = ProfileFactory()
    _earn(profile, _trophy(game, 'default', 'bronze', 1), timezone.now())
    # create the DLC trophies but leave them unearned
    _trophy(game, '001', 'gold', 3)
    _trophy(game, '001', 'gold', 4)

    _run(profile, game)

    assert ProfileTrophyGroup.objects.filter(profile=profile).count() == 1
    assert not ProfileTrophyGroup.objects.filter(profile=profile, trophy_group__trophy_group_id='001').exists()


def test_unearned_trophies_do_not_count_toward_progress_or_timestamps():
    game = GameFactory()
    _group(game, 'default', {'bronze': 4})
    profile = ProfileFactory()
    now = timezone.now()
    _earn(profile, _trophy(game, 'default', 'bronze', 1), now)
    _earn(profile, _trophy(game, 'default', 'bronze', 2), now + timedelta(hours=1))
    # an unearned trophy with a (spurious) later timestamp must be ignored
    EarnedTrophyFactory(profile=profile, trophy=_trophy(game, 'default', 'bronze', 3),
                        earned=False, earned_date_time=now + timedelta(days=99))

    _run(profile, game)

    ptg = ProfileTrophyGroup.objects.get(profile=profile)
    assert ptg.progress == 50                              # 2 of 4, unearned excluded
    assert ptg.last_trophy_at == now + timedelta(hours=1)  # not the unearned 99-day trophy


# --- idempotency + backfill --------------------------------------------------


def test_rerun_upserts_rather_than_duplicating():
    game = GameFactory()
    _group(game, 'default', {'bronze': 2})
    profile = ProfileFactory()
    now = timezone.now()
    _earn(profile, _trophy(game, 'default', 'bronze', 1), now)

    _run(profile, game)
    # earn the second trophy, re-run -> the same row updates, no duplicate
    _earn(profile, _trophy(game, 'default', 'bronze', 2), now + timedelta(hours=3))
    _run(profile, game)

    ptg = ProfileTrophyGroup.objects.get(profile=profile)      # .get asserts exactly one
    assert ptg.progress == 100
    assert ptg.completion_seconds == int(timedelta(hours=3).total_seconds())


def test_backfill_command_populates_all_profiles():
    game = GameFactory()
    _group(game, 'default', {'bronze': 1})
    from tests.factories import ProfileGameFactory
    p1, p2 = ProfileFactory(), ProfileFactory()
    for p in (p1, p2):
        ProfileGameFactory(profile=p, game=game, progress=100)
        _earn(p, _trophy(game, 'default', 'bronze', p.id), timezone.now())

    call_command('backfill_profile_trophy_groups')

    assert ProfileTrophyGroup.objects.filter(profile=p1).count() == 1
    assert ProfileTrophyGroup.objects.filter(profile=p2).count() == 1


def test_backfill_single_username():
    game = GameFactory()
    _group(game, 'default', {'bronze': 1})
    from tests.factories import ProfileGameFactory
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=game, progress=100)
    _earn(profile, _trophy(game, 'default', 'bronze', 1), timezone.now())

    call_command('backfill_profile_trophy_groups', username=profile.psn_username)

    assert ProfileTrophyGroup.objects.filter(profile=profile).count() == 1
