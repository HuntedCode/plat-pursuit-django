"""Tests for the measure_leaderboard feasibility probe.

The command is a production diagnostic, so the bar is: it must run without error against real
Postgres (its SQL is raw), it must be read-only, and it must not fall over on an empty table.
"""
from io import StringIO

import pytest
from django.core.management import call_command

from tests.factories import GameFactory, ProfileFactory, ProfileGameFactory

pytestmark = pytest.mark.django_db


def _run(**kwargs):
    out = StringIO()
    call_command('measure_leaderboard', stdout=out, **kwargs)
    return out.getvalue()


def test_runs_on_empty_database():
    """No games with players must not raise -- prod-safe means safe on a fresh DB too."""
    output = _run()

    assert 'SCALE' in output
    assert 'Nothing to measure' in output


def test_reports_scale_and_timings_for_the_biggest_game():
    game = GameFactory(played_count=3)
    for _ in range(3):
        ProfileGameFactory(profile=ProfileFactory(), game=game, progress=50)

    output = _run(games=1, depth=2)

    assert 'top-20 page' in output
    assert 'rank @' in output
    assert 'VERDICT' in output
    assert game.np_communication_id in output


def test_explain_flag_emits_a_query_plan():
    game = GameFactory(played_count=1)
    ProfileGameFactory(profile=ProfileFactory(), game=game, progress=10)

    output = _run(games=1, explain=True)

    assert 'EXPLAIN' in output
    # Postgres plans always name the scanned relation.
    assert 'profilegame' in output.lower()


def test_is_read_only(django_assert_num_queries):
    """A prod diagnostic must not write. Guard by asserting the row counts are untouched."""
    from trophies.models import ProfileGame

    game = GameFactory(played_count=1)
    ProfileGameFactory(profile=ProfileFactory(), game=game, progress=10)
    before = ProfileGame.objects.count()

    _run(games=1)

    assert ProfileGame.objects.count() == before
