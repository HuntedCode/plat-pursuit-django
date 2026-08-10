"""The game-detail community stats row must be pure denorm reads.

`_build_game_stats_context` used to run five live per-game aggregates behind an hourly
cache. `total_earns` counted EarnedTrophy across every trophy in the game (players x
trophies rows), which on a cold cache could outlast the gunicorn worker timeout on a
popular title -- and the hourly cache key meant the whole catalogue went cold on the
hour, so a crawler walking distinct games missed by construction.

The query-count test is the load-bearing one: it fails the moment somebody reintroduces
a live aggregate here, which is exactly how this regressed the first time.
"""
import pytest
from django.core.management import call_command
from django.utils import timezone
from datetime import timedelta

from tests.factories import (
    EarnedTrophyFactory, GameFactory, ProfileGameFactory, TrophyFactory,
)
from trophies.views.game_views import GameDetailView

pytestmark = pytest.mark.django_db


def test_game_stats_context_issues_no_queries(django_assert_num_queries):
    """Every value reads off the already-loaded Game instance."""
    game = GameFactory(
        played_count=1200,
        monthly_players_count=90,
        plats_earned_count=300,
        total_earns_count=45000,
        full_completion_count=250,
        avg_completion=61.4,
    )

    with django_assert_num_queries(0):
        stats = GameDetailView()._build_game_stats_context(game)

    assert stats == {
        'total_players': 1200,
        'monthly_players': 90,
        'plats_earned': 300,
        'total_earns': 45000,
        'completes': 250,
        'avg_progress': 61.4,
    }


def test_recalc_populates_total_earns_across_all_trophies():
    """total_earns_count sums earned rows across every trophy in the game, and counts only
    rows with earned=True."""
    game = GameFactory()
    bronze = TrophyFactory(game=game, trophy_type='bronze')
    gold = TrophyFactory(game=game, trophy_type='gold')

    EarnedTrophyFactory(trophy=bronze, earned=True)
    EarnedTrophyFactory(trophy=bronze, earned=True)
    EarnedTrophyFactory(trophy=gold, earned=True)
    EarnedTrophyFactory(trophy=gold, earned=False)   # unearned must not count

    call_command('recalc_earn_rates')

    game.refresh_from_db()
    assert game.total_earns_count == 3


def test_recalc_populates_monthly_players_on_the_30_day_window():
    """monthly_players_count counts owners whose last_played falls inside 30 days."""
    game = GameFactory()
    now = timezone.now()
    ProfileGameFactory(game=game, last_played_date_time=now - timedelta(days=1))
    ProfileGameFactory(game=game, last_played_date_time=now - timedelta(days=29))
    ProfileGameFactory(game=game, last_played_date_time=now - timedelta(days=31))   # outside
    ProfileGameFactory(game=game, last_played_date_time=None)                       # never played

    call_command('recalc_earn_rates')

    game.refresh_from_db()
    assert game.monthly_players_count == 2
    assert game.played_count == 4      # the window does not change the owner denominator


def test_recalc_zeroes_new_stats_for_untouched_game():
    """A game nobody plays gets 0/0 rather than a crash on the empty aggregate."""
    game = GameFactory()

    call_command('recalc_earn_rates')

    game.refresh_from_db()
    assert game.total_earns_count == 0
    assert game.monthly_players_count == 0
