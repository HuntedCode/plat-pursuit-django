"""Tests for the per-game leaderboard service (keyset pagination + rank).

The keyset walk is the risky part: a wrong boundary silently skips or repeats players, and the failure
only shows up on boards with ties -- which is every board, since everyone at 100% shares progress=100.
The strongest guard here is `_walk`, which pages through an entire board and asserts the result equals
the full ordered list exactly. Any boundary bug shows up as a missing or duplicated row.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.factories import GameFactory, ProfileFactory, ProfileGameFactory
from trophies.services import game_leaderboard_service as svc

pytestmark = pytest.mark.django_db


def _ids(game):
    """The whole board in rank order, as profile ids."""
    return list(svc.board_queryset(game).values_list('profile_id', flat=True))


def _walk(game, limit):
    """Page through the entire board with the cursor, collecting profile ids."""
    seen, cursor, guard = [], None, 0
    while True:
        rows, cursor = svc.page(game, cursor=cursor, limit=limit)
        seen.extend(r.profile_id for r in rows)
        guard += 1
        assert guard < 100, 'cursor failed to terminate'
        if not cursor:
            return seen


def _player(game, progress, minutes_ago=None, **kw):
    date = None if minutes_ago is None else timezone.now() - timedelta(minutes=minutes_ago)
    return ProfileGameFactory(game=game, profile=ProfileFactory(), progress=progress,
                              most_recent_trophy_date=date, **kw)


# --- ordering ----------------------------------------------------------------


def test_completers_lead_ordered_by_who_finished_first():
    game = GameFactory()
    late = _player(game, 100, minutes_ago=10)
    early = _player(game, 100, minutes_ago=500)
    chaser = _player(game, 92, minutes_ago=1)

    assert _ids(game) == [early.profile_id, late.profile_id, chaser.profile_id]


def test_owners_with_no_trophies_sort_last_within_their_progress():
    game = GameFactory()
    dated = _player(game, 0, minutes_ago=5)
    undated = _player(game, 0, minutes_ago=None)

    assert _ids(game) == [dated.profile_id, undated.profile_id]


def test_hidden_players_are_off_the_board():
    game = GameFactory()
    shown = _player(game, 50, minutes_ago=5)
    _player(game, 90, minutes_ago=5, user_hidden=True)
    _player(game, 80, minutes_ago=5, hidden_flag=True)

    assert _ids(game) == [shown.profile_id]
    assert svc.board_size(game) == 1


# --- keyset pagination -------------------------------------------------------


@pytest.mark.parametrize('limit', [1, 2, 3, 7])
def test_paging_reproduces_the_board_exactly(limit):
    """Distinct progress values: the simple case, parameterised across page boundaries."""
    game = GameFactory()
    for pct in (100, 91, 74, 60, 45, 30, 12, 0):
        _player(game, pct, minutes_ago=pct)

    assert _walk(game, limit) == _ids(game)


@pytest.mark.parametrize('limit', [1, 2, 3, 5])
def test_paging_over_a_block_of_full_ties(limit):
    """Every player identical on BOTH sort keys -- only profile_id separates them."""
    game = GameFactory()
    stamp = timezone.now()
    for _ in range(9):
        ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100,
                           most_recent_trophy_date=stamp)

    walked = _walk(game, limit)
    assert walked == _ids(game)
    assert len(walked) == len(set(walked)) == 9      # nothing skipped, nothing repeated


@pytest.mark.parametrize('limit', [1, 2, 4])
def test_paging_across_the_null_date_boundary(limit):
    """The boundary most likely to be wrong: dated rows, then the undated tail, same progress."""
    game = GameFactory()
    for m in (30, 20, 10):
        _player(game, 0, minutes_ago=m)
    for _ in range(4):
        _player(game, 0, minutes_ago=None)

    walked = _walk(game, limit)
    assert walked == _ids(game)
    assert len(walked) == 7


@pytest.mark.parametrize('limit', [1, 3])
def test_paging_a_realistic_mixed_board(limit):
    """Completers, chasers, ties and undated owners together."""
    game = GameFactory()
    stamp = timezone.now()
    for _ in range(4):
        ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100,
                           most_recent_trophy_date=stamp)
    for pct in (98, 98, 71, 71, 71, 40):
        _player(game, pct, minutes_ago=pct)
    for _ in range(3):
        _player(game, 0, minutes_ago=None)

    walked = _walk(game, limit)
    assert walked == _ids(game)
    assert len(walked) == len(set(walked)) == 13


def test_last_page_reports_no_next_cursor():
    game = GameFactory()
    for pct in (100, 50):
        _player(game, pct, minutes_ago=pct)

    rows, cursor = svc.page(game, limit=25)
    assert len(rows) == 2
    assert cursor is None


def test_empty_board_is_not_an_error():
    rows, cursor = svc.page(GameFactory())

    assert rows == [] and cursor is None


def test_malformed_cursor_falls_back_to_the_first_page():
    """A mangled URL should show page one, not a 500."""
    game = GameFactory()
    for pct in (100, 50):
        _player(game, pct, minutes_ago=pct)

    # Includes '100.5~n~1': a dot-separated timestamp is exactly what broke the first cursor format.
    for junk in ('', 'garbage', '1~2', 'a~b~c', '100~notatime~5', '100.5~n~1', '~~'):
        rows, _ = svc.page(game, cursor=junk)
        assert [r.profile_id for r in rows] == _ids(game)


def test_cursor_round_trips_through_encode_decode():
    game = GameFactory()
    row = _player(game, 63, minutes_ago=42)

    progress, stamp, profile_id = svc.decode_cursor(svc.encode_cursor(row))

    assert (progress, profile_id) == (63, row.profile_id)
    assert stamp == pytest.approx(row.most_recent_trophy_date.timestamp(), abs=0.001)


# --- rank --------------------------------------------------------------------


def test_rank_matches_the_boards_own_ordering_for_every_player():
    """The definitive rank test: rank_for must agree with the list, for everyone, ties included."""
    game = GameFactory()
    stamp = timezone.now()
    for _ in range(3):
        ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100,
                           most_recent_trophy_date=stamp)
    for pct in (88, 88, 55, 20):
        _player(game, pct, minutes_ago=pct)
    for _ in range(2):
        _player(game, 0, minutes_ago=None)

    from trophies.models import Profile
    order = _ids(game)
    for position, profile_id in enumerate(order, start=1):
        profile = Profile.objects.get(pk=profile_id)
        assert svc.rank_for(game, profile) == position


def test_rank_is_none_for_someone_who_does_not_own_the_game():
    game = GameFactory()
    _player(game, 100, minutes_ago=5)

    assert svc.rank_for(game, ProfileFactory()) is None
    assert svc.rank_for(game, None) is None


def test_rank_is_none_for_a_hidden_player():
    """Hidden players are off the board, so they have no position on it."""
    game = GameFactory()
    hidden = _player(game, 90, minutes_ago=5, user_hidden=True)

    assert svc.rank_for(game, hidden.profile) is None


# --- jump to my rank ---------------------------------------------------------


def _board_with(n):
    """n players on distinct progress values, so rank == position is unambiguous."""
    game = GameFactory()
    rows = [_player(game, 100 - i, minutes_ago=i + 1) for i in range(n)]
    return game, rows


def test_jump_window_opens_a_few_places_above_the_viewer():
    """The point of the window: you see who you're chasing, not just yourself."""
    game, rows = _board_with(30)
    me = rows[19]                                   # rank 20

    window, _, start_rank = svc.page_around(game, me.profile, before=4, limit=10)

    assert start_rank == 16                          # 4 places above rank 20
    assert [r.profile_id for r in window] == _ids(game)[15:25]
    assert me.profile_id in [r.profile_id for r in window]


def test_jump_window_clamps_at_the_top_of_the_board():
    """Someone ranked 2nd can't have 4 rows above them; the window must not run off the top."""
    game, rows = _board_with(10)
    me = rows[1]                                     # rank 2

    window, _, start_rank = svc.page_around(game, me.profile, before=4, limit=5)

    assert start_rank == 1
    assert [r.profile_id for r in window] == _ids(game)[:5]


def test_jump_window_for_the_very_first_player():
    game, rows = _board_with(6)

    window, _, start_rank = svc.page_around(game, rows[0].profile, before=4, limit=3)

    assert start_rank == 1
    assert [r.profile_id for r in window] == _ids(game)[:3]


def test_jump_window_at_the_bottom_reports_no_further_pages():
    game, rows = _board_with(8)
    last = rows[-1]

    window, next_cursor, start_rank = svc.page_around(game, last.profile, before=3, limit=10)

    assert start_rank == 5
    assert [r.profile_id for r in window] == _ids(game)[4:]
    assert next_cursor is None


def test_jump_window_start_rank_is_consistent_with_rank_for():
    """start_rank must line up with the rank the header shows, or the numbering jumps."""
    game, rows = _board_with(25)
    me = rows[14]

    window, _, start_rank = svc.page_around(game, me.profile, before=4, limit=10)

    assert svc.rank_for(game, me.profile) == 15
    # The viewer sits exactly `before` places into the window.
    assert window[15 - start_rank].profile_id == me.profile_id


def test_jump_window_survives_a_block_of_full_ties():
    game = GameFactory()
    stamp = timezone.now()
    rows = [ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100,
                               most_recent_trophy_date=stamp) for _ in range(12)]
    order = _ids(game)
    me = next(r for r in rows if r.profile_id == order[8])

    window, _, start_rank = svc.page_around(game, me.profile, before=3, limit=6)

    assert start_rank == 6
    assert [r.profile_id for r in window] == order[5:11]


def test_jump_window_is_none_for_someone_not_on_the_board():
    game, _ = _board_with(5)

    assert svc.page_around(game, ProfileFactory()) is None
    assert svc.page_around(game, None) is None


def test_order_by_matches_the_index_it_relies_on():
    """If these drift apart the query silently stops using pg_game_leaderboard_idx."""
    from trophies.models import ProfileGame

    index = next(i for i in ProfileGame._meta.indexes if i.name == 'pg_game_leaderboard_idx')

    assert index.fields == ['game', '-progress', 'most_recent_trophy_date', 'profile']
    assert svc.ORDER_BY[0] == '-progress'
    assert svc.ORDER_BY[2] == 'profile_id'
