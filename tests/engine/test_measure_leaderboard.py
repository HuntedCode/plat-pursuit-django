"""Tests for the measure_leaderboard feasibility probe.

The command is a production diagnostic, so the bar is: it must run without error against real
Postgres (its SQL is raw), it must be read-only, and it must not fall over on an empty table.
"""
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command

from tests.factories import GameFactory, ProfileFactory, ProfileGameFactory, TrophyGroupFactory

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


def test_group_sizing_counts_dlc_and_projects_denorm_rows():
    """Sizes a future ProfileTrophyGroup table: a 3-group game with 10 players projects 30 rows."""
    dlc_game = GameFactory(played_count=10)
    for gid in ('default', '001', '002'):
        TrophyGroupFactory(game=dlc_game, trophy_group_id=gid)
    base_only = GameFactory(played_count=4)
    TrophyGroupFactory(game=base_only, trophy_group_id='default')

    output = _run()

    assert 'GROUP-SCOPED BOARDS' in output
    assert 'Games with DLC (>1 group)    : 1' in output
    assert 'Most groups on one game      : 3' in output
    assert '~34' in output                      # 10 players x 3 groups + 4 x 1


def test_group_sizing_handles_no_trophy_groups():
    """A database with no synced groups must not divide by zero or crash."""
    GameFactory(played_count=3)

    output = _run()

    assert 'Nothing to size' in output


def test_group_sizing_reports_time_field_coverage():
    """Time-based boards depend on these fields; a mostly-null field should be visible up front."""
    game = GameFactory(played_count=2)
    TrophyGroupFactory(game=game)
    ProfileGameFactory(profile=ProfileFactory(), game=game, play_duration=None)
    ProfileGameFactory(profile=ProfileFactory(), game=GameFactory(),
                       play_duration=timedelta(hours=5))

    output = _run()

    assert 'play_duration' in output
    assert 'first_played_date_time' in output
    assert '50.0%' in output      # 1 of 2 rows has play_duration


def test_is_read_only(django_assert_num_queries):
    """A prod diagnostic must not write. Guard by asserting the row counts are untouched."""
    from trophies.models import ProfileGame

    game = GameFactory(played_count=1)
    ProfileGameFactory(profile=ProfileFactory(), game=game, progress=10)
    before = ProfileGame.objects.count()

    _run(games=1)

    assert ProfileGame.objects.count() == before
