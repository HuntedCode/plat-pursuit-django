"""Tests for the denormalized community-completion stats that `recalc_earn_rates` computes onto Game
(plats_earned_count / full_completion_count / avg_completion). The command's played_count / earn_rate
behavior is exercised elsewhere; these pin the new per-game stats + their population."""
import pytest
from django.core.management import call_command

from tests.factories import GameFactory, ProfileGameFactory

pytestmark = pytest.mark.django_db


def test_recalc_populates_community_stats():
    """plats_earned_count = owners with has_plat; full_completion_count = owners at progress 100;
    avg_completion = mean of all owners' progress (same ALL-rows population as played_count)."""
    game = GameFactory()
    ProfileGameFactory(game=game, has_plat=True, progress=100)
    ProfileGameFactory(game=game, has_plat=True, progress=100)
    ProfileGameFactory(game=game, has_plat=False, progress=50)
    ProfileGameFactory(game=game, has_plat=False, progress=0)

    call_command('recalc_earn_rates')

    game.refresh_from_db()
    assert game.played_count == 4
    assert game.plats_earned_count == 2          # two owners have the plat
    assert game.full_completion_count == 2       # two owners at progress=100
    assert game.avg_completion == 62.5           # (100 + 100 + 50 + 0) / 4


def test_recalc_rounds_avg_completion_to_one_decimal():
    """avg_completion stores the mean rounded to one decimal (e.g. (10+20+25)/3 = 18.33 -> 18.3)."""
    game = GameFactory()
    ProfileGameFactory(game=game, progress=10)
    ProfileGameFactory(game=game, progress=20)
    ProfileGameFactory(game=game, progress=25)

    call_command('recalc_earn_rates')

    game.refresh_from_db()
    assert game.avg_completion == 18.3


def test_recalc_zeroes_game_with_no_players():
    """A game nobody plays gets 0/0/0.0 -- no crash on the empty aggregate (avg is NULL -> 0.0)."""
    game = GameFactory()

    call_command('recalc_earn_rates')

    game.refresh_from_db()
    assert game.played_count == 0
    assert game.plats_earned_count == 0
    assert game.full_completion_count == 0
    assert game.avg_completion == 0.0
