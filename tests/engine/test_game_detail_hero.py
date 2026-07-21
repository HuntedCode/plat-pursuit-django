"""Tests for the rebuilt game-detail hero backend logic.

Pins:
  - `_build_outlook_context` (the anonymous "Platinum Outlook"): PSN-GLOBAL platinum rarity ->
    difficulty (4 - trophy_rarity), guarded when there's no platinum or the rarity tier isn't synced,
    and never per-user work.
  - `_build_timeline_events`: the platinum floats to WHERE it was earned in the sequence (with DLC a
    base-game plat precedes the 75%/100% overall milestones); "Started Playing" is pinned first and
    "100%" is pinned last.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from trophies.views.game_views import GameDetailView
from tests.factories import (
    EarnedTrophyFactory, GameFactory, ProfileFactory, ProfileGameFactory, TrophyFactory,
)

pytestmark = pytest.mark.django_db


# ── _build_outlook_context ────────────────────────────────────────────────

def _outlook(game):
    return GameDetailView()._build_outlook_context(game)['outlook']


def test_outlook_ultra_rare_platinum():
    game = GameFactory()
    TrophyFactory(game=game, trophy_type='platinum', trophy_earn_rate=2.1, trophy_rarity=0)  # Ultra Rare
    out = _outlook(game)
    assert out['has_platinum'] is True
    assert out['plat_rate'] == pytest.approx(2.1)
    assert out['plat_rarity_label'] == 'Ultra Rare'
    assert out['difficulty_level'] == 4          # 4 - 0


def test_outlook_common_platinum():
    game = GameFactory()
    TrophyFactory(game=game, trophy_type='platinum', trophy_earn_rate=48.0, trophy_rarity=3)  # Common
    out = _outlook(game)
    assert out['plat_rarity_label'] == 'Common'
    assert out['difficulty_level'] == 1          # 4 - 3


def test_outlook_no_platinum_degrades():
    game = GameFactory()
    TrophyFactory(game=game, trophy_type='gold', trophy_earn_rate=10.0, trophy_rarity=2)
    out = _outlook(game)
    assert out['has_platinum'] is False
    assert out['plat_rate'] is None
    assert out['plat_rarity_label'] is None
    assert out['difficulty_level'] is None


def test_outlook_platinum_missing_rarity_is_guarded():
    # PSN rate present but the tier isn't synced -> no label/difficulty, but still a platinum + rate.
    game = GameFactory()
    TrophyFactory(game=game, trophy_type='platinum', trophy_earn_rate=5.0, trophy_rarity=None)
    out = _outlook(game)
    assert out['has_platinum'] is True
    assert out['plat_rate'] == pytest.approx(5.0)
    assert out['plat_rarity_label'] is None
    assert out['difficulty_level'] is None


# ── _build_timeline_events (dynamic platinum ordering) ─────────────────────

def _timeline_labels(game, profile):
    return [e['label'] for e in GameDetailView()._build_profile_context(game, profile)['timeline_events']]


def test_timeline_platinum_floats_before_unreached_milestones():
    """DLC case: a base-game platinum earned early (low overall-completion index) sorts BEFORE the
    50%/75% milestones it precedes; Started is first and 100% is last."""
    game = GameFactory()
    profile = ProfileFactory()
    now = timezone.now()
    ProfileGameFactory(profile=profile, game=game, progress=40,
                       first_played_date_time=now - timedelta(days=10))
    tro = [TrophyFactory(game=game, trophy_type='bronze') for _ in range(8)]  # 8 total -> 75% at index 6
    tro[0].trophy_type = 'platinum'
    tro[0].save()
    # 4 earned (the platinum earned 2nd -> index 1), 4 unearned -> total_trophies stays 8.
    dates = [now - timedelta(days=d) for d in (9, 8, 7, 6)]
    EarnedTrophyFactory(profile=profile, trophy=tro[1], earned=True, earned_date_time=dates[0])  # 1st
    EarnedTrophyFactory(profile=profile, trophy=tro[0], earned=True, earned_date_time=dates[1])  # 2nd = plat
    EarnedTrophyFactory(profile=profile, trophy=tro[2], earned=True, earned_date_time=dates[2])
    EarnedTrophyFactory(profile=profile, trophy=tro[3], earned=True, earned_date_time=dates[3])
    for i in range(4, 8):
        EarnedTrophyFactory(profile=profile, trophy=tro[i], earned=False, earned_date_time=None)

    labels = _timeline_labels(game, profile)
    plat_i = labels.index('Platinum Trophy')
    assert labels[0] == 'Started Playing'
    assert labels[-1] == '100% Trophy'
    assert plat_i < labels.index('50% Trophy')
    assert plat_i < labels.index('75% Trophy')


def test_timeline_platinum_stays_late_without_dlc():
    """No DLC: the platinum is the last trophy earned, so it stays after 75% and before 100%."""
    game = GameFactory()
    profile = ProfileFactory()
    now = timezone.now()
    ProfileGameFactory(profile=profile, game=game, progress=100,
                       first_played_date_time=now - timedelta(days=5))
    tro = [TrophyFactory(game=game, trophy_type='bronze') for _ in range(4)]
    tro[3].trophy_type = 'platinum'
    tro[3].save()
    for i in range(4):
        EarnedTrophyFactory(profile=profile, trophy=tro[i], earned=True,
                            earned_date_time=now - timedelta(days=4 - i))  # plat (tro[3]) earned last

    labels = _timeline_labels(game, profile)
    assert labels.index('75% Trophy') < labels.index('Platinum Trophy') < labels.index('100% Trophy')
