"""Pins the per-game leaderboard ordering contract that pg_game_leaderboard_idx backs.

Ordering: progress DESC, then earliest most_recent_trophy_date (who got there first), then profile_id.

The third key is the point of these tests. Ties on the first two are the NORMAL case, not an edge case
-- everyone who 100%s a game shares progress=100 -- and without a unique final key Postgres may return
tied rows in a different order between calls, which makes pagination skip or duplicate players and makes
a displayed rank flicker between refreshes. If someone later "simplifies" the sort by dropping
profile_id, these fail.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.factories import GameFactory, ProfileFactory, ProfileGameFactory

pytestmark = pytest.mark.django_db

# The ordering under test. Mirrors pg_game_leaderboard_idx field-for-field.
LEADERBOARD_ORDER = ('-progress', 'most_recent_trophy_date', 'profile_id')


def _board(game):
    from trophies.models import ProfileGame
    return list(
        ProfileGame.objects
        .filter(game=game, hidden_flag=False, user_hidden=False)
        .order_by(*LEADERBOARD_ORDER)
        .values_list('profile_id', flat=True)
    )


def test_higher_progress_outranks_lower():
    game = GameFactory()
    low = ProfileGameFactory(game=game, profile=ProfileFactory(), progress=40)
    high = ProfileGameFactory(game=game, profile=ProfileFactory(), progress=95)

    assert _board(game) == [high.profile_id, low.profile_id]


def test_equal_progress_is_broken_by_who_got_there_first():
    """The whole point of the leaderboard: at 100% it becomes a race."""
    game = GameFactory()
    now = timezone.now()
    later = ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100,
                               most_recent_trophy_date=now)
    earlier = ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100,
                                 most_recent_trophy_date=now - timedelta(days=30))

    assert _board(game) == [earlier.profile_id, later.profile_id]


def test_full_ties_are_deterministic_across_repeated_queries():
    """Identical progress AND identical timestamp must still produce one stable order."""
    game = GameFactory()
    stamp = timezone.now()
    rows = [ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100,
                               most_recent_trophy_date=stamp) for _ in range(6)]

    expected = sorted(r.profile_id for r in rows)
    assert _board(game) == expected
    assert _board(game) == expected     # stable, not incidental


def test_pagination_over_full_ties_neither_skips_nor_duplicates():
    """The failure this ordering prevents: tied rows straddling a page boundary."""
    from trophies.models import ProfileGame

    game = GameFactory()
    stamp = timezone.now()
    for _ in range(10):
        ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100,
                           most_recent_trophy_date=stamp)

    qs = ProfileGame.objects.filter(game=game).order_by(*LEADERBOARD_ORDER)
    page1 = list(qs[:4].values_list('profile_id', flat=True))
    page2 = list(qs[4:8].values_list('profile_id', flat=True))
    page3 = list(qs[8:12].values_list('profile_id', flat=True))
    seen = page1 + page2 + page3

    assert len(seen) == 10
    assert len(set(seen)) == 10          # nothing duplicated across pages
    assert seen == sorted(seen)          # and nothing skipped


def test_players_with_no_trophy_date_sort_last_within_their_progress():
    """0% players have a null date; ASC NULLS LAST (the Postgres default) keeps them at the bottom."""
    game = GameFactory()
    dated = ProfileGameFactory(game=game, profile=ProfileFactory(), progress=0,
                               most_recent_trophy_date=timezone.now())
    undated = ProfileGameFactory(game=game, profile=ProfileFactory(), progress=0,
                                 most_recent_trophy_date=None)

    assert _board(game) == [dated.profile_id, undated.profile_id]


def test_hidden_rows_are_excluded_from_the_board():
    """played_count counts these, the board must not -- the two numbers are reconciled in the UI."""
    game = GameFactory()
    shown = ProfileGameFactory(game=game, profile=ProfileFactory(), progress=50)
    ProfileGameFactory(game=game, profile=ProfileFactory(), progress=90, user_hidden=True)
    ProfileGameFactory(game=game, profile=ProfileFactory(), progress=80, hidden_flag=True)

    assert _board(game) == [shown.profile_id]


def test_index_matches_the_ordering_it_backs():
    """Guards the index and the query drifting apart -- a mismatch silently costs the index scan."""
    from trophies.models import ProfileGame

    index = next(i for i in ProfileGame._meta.indexes if i.name == 'pg_game_leaderboard_idx')

    assert index.fields == ['game', '-progress', 'most_recent_trophy_date', 'profile']
