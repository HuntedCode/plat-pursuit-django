"""Tests for the per-game leaderboard service (options, keyset pagination, rank, jump).

The keyset walk is the risky part: a wrong boundary silently skips or repeats players, and the failure
only shows on boards with ties -- which is every board, since everyone at 100% shares progress=100. The
strongest guard is `_walk`, which pages an entire board and asserts the result equals the full ordered
list exactly, run across forward/inverted and every filter combination.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.factories import GameFactory, ProfileFactory, ProfileGameFactory
from trophies.services import game_leaderboard_service as svc
from trophies.services.game_leaderboard_service import BoardOptions

pytestmark = pytest.mark.django_db

DEFAULT = BoardOptions()                              # earners only (the default view)
ALL = BoardOptions(only_earners=False)               # every owner, 0% included
INVERTED = BoardOptions(only_earners=False, invert=True)


def _ids(game, opts):
    return list(svc.board_queryset(game, opts).values_list('profile_id', flat=True))


def _walk(game, opts, limit):
    """Page through the whole board with the cursor, collecting profile ids."""
    seen, cursor, guard = [], None, 0
    while True:
        rows, cursor = svc.page(game, opts, cursor=cursor, limit=limit)
        seen.extend(r.profile_id for r in rows)
        guard += 1
        assert guard < 200, 'cursor failed to terminate'
        if not cursor:
            return seen


def _player(game, progress, minutes_ago=None, registered=True, **kw):
    date = None if minutes_ago is None else timezone.now() - timedelta(minutes=minutes_ago)
    profile = ProfileFactory() if registered else ProfileFactory(user=None)
    return ProfileGameFactory(game=game, profile=profile, progress=progress,
                              most_recent_trophy_date=date, **kw)


# --- ordering ----------------------------------------------------------------


def test_completers_lead_ordered_by_who_finished_first():
    game = GameFactory()
    late = _player(game, 100, minutes_ago=10)
    early = _player(game, 100, minutes_ago=500)
    chaser = _player(game, 92, minutes_ago=1)

    assert _ids(game, DEFAULT) == [early.profile_id, late.profile_id, chaser.profile_id]


def test_invert_is_the_exact_reverse():
    game = GameFactory()
    for pct in (100, 80, 60, 40, 20):
        _player(game, pct, minutes_ago=pct)

    assert _ids(game, INVERTED) == list(reversed(_ids(game, ALL)))


# --- filters -----------------------------------------------------------------


def test_only_earners_drops_zero_trophy_owners():
    game = GameFactory()
    earner = _player(game, 40, minutes_ago=5)
    _player(game, 0, minutes_ago=None)          # 0%, no trophies

    assert _ids(game, DEFAULT) == [earner.profile_id]
    assert svc.board_size(game, DEFAULT) == 1
    assert svc.board_size(game, ALL) == 2


def test_registered_only_drops_profiles_without_a_site_account():
    game = GameFactory()
    member = _player(game, 80, minutes_ago=5, registered=True)
    _player(game, 90, minutes_ago=5, registered=False)     # synced but not registered

    assert _ids(game, BoardOptions(registered_only=True)) == [member.profile_id]
    assert svc.board_size(game, BoardOptions(registered_only=True)) == 1
    assert svc.board_size(game, DEFAULT) == 2


def test_hidden_players_are_off_every_board():
    game = GameFactory()
    shown = _player(game, 50, minutes_ago=5)
    _player(game, 90, minutes_ago=5, user_hidden=True)
    _player(game, 80, minutes_ago=5, hidden_flag=True)

    assert _ids(game, ALL) == [shown.profile_id]


# --- keyset pagination (the exhaustive guard) --------------------------------


@pytest.mark.parametrize('opts', [DEFAULT, ALL, INVERTED, BoardOptions(registered_only=True)])
@pytest.mark.parametrize('limit', [1, 2, 3, 7])
def test_paging_reproduces_the_board_exactly(opts, limit):
    game = GameFactory()
    for pct in (100, 91, 74, 60, 45, 30, 12, 3):
        _player(game, pct, minutes_ago=pct)

    assert _walk(game, opts, limit) == _ids(game, opts)


@pytest.mark.parametrize('opts', [ALL, INVERTED])
@pytest.mark.parametrize('limit', [1, 2, 3, 5])
def test_paging_over_full_ties(opts, limit):
    """Every player identical on both sort keys -- only profile_id separates them, forward and inverted."""
    game = GameFactory()
    stamp = timezone.now()
    for _ in range(9):
        ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100, most_recent_trophy_date=stamp)

    walked = _walk(game, opts, limit)
    assert walked == _ids(game, opts)
    assert len(walked) == len(set(walked)) == 9


@pytest.mark.parametrize('opts', [ALL, INVERTED])
@pytest.mark.parametrize('limit', [1, 2, 4])
def test_paging_across_the_null_date_boundary(opts, limit):
    """The boundary most likely to be wrong: dated rows then the undated tail, forward and inverted."""
    game = GameFactory()
    for m in (30, 20, 10):
        _player(game, 0, minutes_ago=m)
    for _ in range(4):
        _player(game, 0, minutes_ago=None)

    walked = _walk(game, opts, limit)
    assert walked == _ids(game, opts)
    assert len(walked) == 7


def test_last_page_has_no_next_cursor():
    game = GameFactory()
    for pct in (100, 50):
        _player(game, pct, minutes_ago=pct)

    rows, cursor = svc.page(game, DEFAULT, limit=25)
    assert len(rows) == 2 and cursor is None


def test_empty_board_is_not_an_error():
    rows, cursor = svc.page(GameFactory(), DEFAULT)
    assert rows == [] and cursor is None


def test_malformed_cursor_falls_back_to_the_first_page():
    game = GameFactory()
    for pct in (100, 50):
        _player(game, pct, minutes_ago=pct)

    for junk in ('', 'garbage', '1~2', 'a~b~c', '100~notatime~5', '100.5~n~1', '~~'):
        rows, _ = svc.page(game, DEFAULT, cursor=junk)
        assert [r.profile_id for r in rows] == _ids(game, DEFAULT)


# --- rank --------------------------------------------------------------------


def test_rank_matches_the_board_order_for_every_player():
    game = GameFactory()
    stamp = timezone.now()
    for _ in range(3):
        ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100, most_recent_trophy_date=stamp)
    for pct in (88, 88, 55, 20):
        _player(game, pct, minutes_ago=pct)

    from trophies.models import Profile
    for position, pid in enumerate(_ids(game, DEFAULT), start=1):
        assert svc.rank_for(game, Profile.objects.get(pk=pid), DEFAULT) == position


def test_rank_is_canonical_regardless_of_invert():
    """Inverting the display doesn't change 'You're #N' -- it's still Nth best."""
    game = GameFactory()
    rows = [_player(game, 100 - i, minutes_ago=i + 1) for i in range(5)]
    third = rows[2].profile

    assert svc.rank_for(game, third, DEFAULT) == 3
    assert svc.rank_for(game, third, BoardOptions(invert=True)) == 3


def test_rank_reflects_the_active_filters():
    """With unregistered players hidden, your rank is among the registered."""
    game = GameFactory()
    _player(game, 95, minutes_ago=5, registered=False)   # ahead, but unregistered
    me = _player(game, 80, minutes_ago=5, registered=True)

    assert svc.rank_for(game, me.profile, DEFAULT) == 2                      # 2nd overall
    assert svc.rank_for(game, me.profile, BoardOptions(registered_only=True)) == 1   # 1st among members


def test_rank_is_none_when_the_viewer_is_filtered_out():
    game = GameFactory()
    zero = _player(game, 0, minutes_ago=None)            # 0 trophies

    assert svc.rank_for(game, zero.profile, DEFAULT) is None    # earners-only default hides them
    assert svc.rank_for(game, zero.profile, ALL) == 1


def test_rank_is_none_for_a_non_owner_or_anonymous():
    game = GameFactory()
    _player(game, 100, minutes_ago=5)

    assert svc.rank_for(game, ProfileFactory(), DEFAULT) is None
    assert svc.rank_for(game, None, DEFAULT) is None


# --- jump to a rank ----------------------------------------------------------


def _board(n, opts=DEFAULT):
    game = GameFactory()
    rows = [_player(game, 100 - i, minutes_ago=i + 1) for i in range(n)]
    return game, rows


def test_jump_opens_a_few_places_above_the_target():
    game, _ = _board(30)

    rows, _cur, _prev, start_rank, total = svc.page_at_rank(game, DEFAULT, 20, before=4, limit=10)

    assert total == 30
    assert start_rank == 16                                  # 4 above rank 20
    assert [r.profile_id for r in rows] == _ids(game, DEFAULT)[15:25]


def test_jump_clamps_at_the_top():
    game, _ = _board(10)

    rows, _cur, _prev, start_rank, _total = svc.page_at_rank(game, DEFAULT, 2, before=4, limit=5)

    assert start_rank == 1
    assert [r.profile_id for r in rows] == _ids(game, DEFAULT)[:5]


def test_jump_clamps_an_out_of_range_rank():
    game, _ = _board(8)

    rows, _cur, _prev, start_rank, total = svc.page_at_rank(game, DEFAULT, 999, before=3, limit=10)

    assert total == 8
    assert start_rank == 5                                   # clamped to rank 8, 3 above
    assert [r.profile_id for r in rows] == _ids(game, DEFAULT)[4:]


def test_jump_numbers_the_target_row_correctly_under_invert():
    """Under invert the window still lands on the target with its canonical rank."""
    game, _ = _board(30)
    order = _ids(game, DEFAULT)                              # canonical (forward) order
    target_pid = order[19]                                   # canonical rank 20

    rows, _cur, _prev, start_rank, total = svc.page_at_rank(
        game, BoardOptions(invert=True), 20, before=4, limit=10)

    # start_rank counts DOWN by one per row; find where the target lands and check its number.
    idx = [r.profile_id for r in rows].index(target_pid)
    assert start_rank - idx == 20
    assert total == 30


def test_jump_returns_none_on_an_empty_board():
    assert svc.page_at_rank(GameFactory(), DEFAULT, 1) is None


# --- scroll up from a jump (page_before) -------------------------------------


@pytest.mark.parametrize('opts', [DEFAULT, BoardOptions(invert=True)])
def test_jump_then_walk_up_reproduces_everything_above(opts):
    """The bidirectional guard: land mid-board, page UP repeatedly, and the collected rows must equal the
    slice of the board above the landing window exactly -- no skip, no repeat, forward and inverted."""
    game = GameFactory()
    for pct in range(60, 0, -1):                          # 60 players, distinct progress
        _player(game, pct, minutes_ago=pct)
    order = _ids(game, opts)                              # full board in display order

    rows, _next, prev_cursor, _start, _total = svc.page_at_rank(game, opts, 30, before=4, limit=8)
    window_ids = [r.profile_id for r in rows]
    landing_top = order.index(window_ids[0])             # display index of the window's first row

    collected, cursor, guard = [], prev_cursor, 0
    while cursor:
        prev_rows, cursor = svc.page_before(game, opts, cursor, limit=8)
        collected = [r.profile_id for r in prev_rows] + collected   # prepend (display order)
        guard += 1
        assert guard < 50

    assert collected == order[:landing_top]              # exactly the rows above the window
    assert len(collected) == len(set(collected))         # nothing repeated


@pytest.mark.parametrize('opts', [ALL, INVERTED])
def test_page_before_over_ties_and_the_null_tail(opts):
    """page_before's riskiest case (per the module's own note): a cursor inside a tie cluster with a
    null-date tail below. Paging up from just below the tail must reproduce everything above it exactly."""
    game = GameFactory()
    stamp = timezone.now()
    for _ in range(3):                                   # progress-50 tie cluster, same timestamp
        ProfileGameFactory(game=game, profile=ProfileFactory(), progress=50, most_recent_trophy_date=stamp)
    _player(game, 80, minutes_ago=5)                     # someone above the cluster
    for _ in range(2):
        _player(game, 0, minutes_ago=None)               # null-date tail (only present with only_earners off)
    order = _ids(game, opts)

    # Start from the last row and walk up; the collected rows must equal everything above it.
    last = svc.board_queryset(game, opts).select_related('profile')[len(order) - 1]
    collected, cursor, guard = [], svc.encode_cursor(last), 0
    while cursor:
        rows, cursor = svc.page_before(game, opts, cursor, limit=2)
        collected = [r.profile_id for r in rows] + collected
        guard += 1
        assert guard < 50

    assert collected == order[:-1]
    assert len(collected) == len(set(collected))


def test_page_before_at_the_top_has_no_further_cursor():
    game = GameFactory()
    for pct in (100, 80, 60, 40):
        _player(game, pct, minutes_ago=pct)
    order = _ids(game, DEFAULT)

    # cursor = the 3rd row; page_before it returns the 2 above, and no more.
    third = svc.board_queryset(game, DEFAULT)[2]
    rows, prev_cursor = svc.page_before(game, DEFAULT, svc.encode_cursor(third), limit=25)

    assert [r.profile_id for r in rows] == order[:2]
    assert prev_cursor is None


def test_jump_window_has_no_prev_cursor_when_it_opens_at_the_top():
    game, _ = _board(10)

    _rows, _next, prev_cursor, start_rank, _total = svc.page_at_rank(game, DEFAULT, 2, before=4, limit=5)

    assert start_rank == 1                                # clamped to the top
    assert prev_cursor is None                            # nothing above to load


# --- search suggest ----------------------------------------------------------


def test_suggest_matches_by_name_and_carries_rank():
    game = GameFactory()
    for i, name in enumerate(['AceHunter', 'AceRunner', 'Nobody']):
        ProfileGameFactory(game=game, profile=ProfileFactory(psn_username=name), progress=100 - i,
                           most_recent_trophy_date=timezone.now() - timedelta(minutes=i + 1))

    results = svc.suggest(game, DEFAULT, 'ace')

    assert {r['profile'].psn_username for r in results} == {'acehunter', 'acerunner'}  # stored lowercased
    ranks = {r['profile'].psn_username: r['rank'] for r in results}
    assert ranks['acehunter'] == 1 and ranks['acerunner'] == 2


def test_suggest_is_scoped_to_the_filtered_board():
    game = GameFactory()
    ProfileGameFactory(game=game, profile=ProfileFactory(psn_username='ZedEarner'), progress=50,
                       most_recent_trophy_date=timezone.now())
    ProfileGameFactory(game=game, profile=ProfileFactory(psn_username='ZedZero'), progress=0,
                       most_recent_trophy_date=None)                          # filtered by earners-default

    assert {r['profile'].psn_username for r in svc.suggest(game, DEFAULT, 'zed')} == {'zedearner'}
    assert {r['profile'].psn_username for r in svc.suggest(game, ALL, 'zed')} == {'zedearner', 'zedzero'}


def test_suggest_short_query_returns_empty():
    game, _ = _board(3)
    assert svc.suggest(game, DEFAULT, 'a') == []
    assert svc.suggest(game, DEFAULT, '') == []


# --- index contract ----------------------------------------------------------


def test_order_by_matches_the_index_it_relies_on():
    from trophies.models import ProfileGame

    index = next(i for i in ProfileGame._meta.indexes if i.name == 'pg_game_leaderboard_idx')
    assert index.fields == ['game', '-progress', 'most_recent_trophy_date', 'profile']
    assert svc.ORDER_BY[0] == '-progress' and svc.ORDER_BY[2] == 'profile_id'
