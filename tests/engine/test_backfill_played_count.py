"""Tests for the backfill_played_count management command.

The command recomputes Game.played_count from the true number of ProfileGame
rows, correcting the inflation left by the historical sync double-increment.
"""

import pytest
from django.core.management import call_command

from trophies.models import Game, ProfileGame
from tests.factories import GameFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def test_backfill_corrects_inflated_count():
    game = GameFactory()
    ProfileGame.objects.create(profile=ProfileFactory(), game=game)
    ProfileGame.objects.create(profile=ProfileFactory(), game=game)
    # Simulate the historical inflation.
    Game.objects.filter(pk=game.pk).update(played_count=99)

    call_command("backfill_played_count")

    game.refresh_from_db()
    assert game.played_count == 2  # true ProfileGame count


def test_backfill_zeroes_games_with_no_players():
    game = GameFactory()
    Game.objects.filter(pk=game.pk).update(played_count=5)  # stale, no players

    call_command("backfill_played_count")

    game.refresh_from_db()
    assert game.played_count == 0


def test_backfill_dry_run_does_not_write():
    game = GameFactory()
    Game.objects.filter(pk=game.pk).update(played_count=42)

    call_command("backfill_played_count", "--dry-run")

    game.refresh_from_db()
    assert game.played_count == 42  # unchanged
