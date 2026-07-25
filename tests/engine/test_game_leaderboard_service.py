"""Tests for the per-game leaderboard service: the shared Board engine and each board type.

The board is rendered virtualized on the client, so the service reads rows by rank RANGE. The ordering is
the load-bearing part: ties on the earlier keys are the normal case, and `page_range` must return them in a
stable, TOTAL order (the unique profile_id tail) so a virtual window never shows a duplicate or a gap.

The EverythingBoard (ProfileGame, overall completion) carries the bulk of the engine tests; the group,
speed, and playtime boards get focused coverage of their ordering + population.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.factories import GameFactory, ProfileFactory, ProfileGameFactory
from trophies.models import ProfileTrophyGroup, TrophyGroup
from trophies.services import game_leaderboard_service as svc
from trophies.services.game_leaderboard_service import BoardOptions

pytestmark = pytest.mark.django_db

DEFAULT = BoardOptions()                              # earners only (the default view)
ALL = BoardOptions(only_earners=False)               # every owner, 0% included
INVERTED = BoardOptions(only_earners=False, invert=True)


def _ever(game, opts):
    return svc.EverythingBoard(game, opts)


def _ids(game, opts):
    return list(_ever(game, opts).ordered().values_list('profile_id', flat=True))


def _player(game, progress, minutes_ago=None, registered=True, **kw):
    date = None if minutes_ago is None else timezone.now() - timedelta(minutes=minutes_ago)
    profile = ProfileFactory() if registered else ProfileFactory(user=None)
    return ProfileGameFactory(game=game, profile=profile, progress=progress,
                              most_recent_trophy_date=date, **kw)


# --- EverythingBoard: ordering -----------------------------------------------


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


# --- EverythingBoard: filters ------------------------------------------------


def test_only_earners_drops_zero_trophy_owners():
    game = GameFactory()
    earner = _player(game, 40, minutes_ago=5)
    _player(game, 0, minutes_ago=None)

    assert _ids(game, DEFAULT) == [earner.profile_id]
    assert _ever(game, DEFAULT).size() == 1
    assert _ever(game, ALL).size() == 2


def test_registered_only_drops_profiles_without_a_site_account():
    game = GameFactory()
    member = _player(game, 80, minutes_ago=5, registered=True)
    _player(game, 90, minutes_ago=5, registered=False)

    reg = BoardOptions(registered_only=True)
    assert _ids(game, reg) == [member.profile_id]
    assert _ever(game, reg).size() == 1
    assert _ever(game, DEFAULT).size() == 2


def test_hidden_players_are_off_every_board():
    game = GameFactory()
    shown = _player(game, 50, minutes_ago=5)
    _player(game, 90, minutes_ago=5, user_hidden=True)
    _player(game, 80, minutes_ago=5, hidden_flag=True)

    assert _ids(game, ALL) == [shown.profile_id]


# --- EverythingBoard: windowed reads -----------------------------------------


@pytest.mark.parametrize('opts', [DEFAULT, ALL, INVERTED, BoardOptions(registered_only=True)])
def test_ranges_tile_the_board_without_gap_or_overlap(opts):
    """Adjacent windows must exactly reconstruct the board -- the virtual-scroll guarantee."""
    game = GameFactory()
    for pct in (100, 91, 74, 60, 45, 30, 12, 3):
        _player(game, pct, minutes_ago=pct)
    order = _ids(game, opts)
    board = _ever(game, opts)

    a = [r.profile_id for r in board.page_range(1, 3)]
    b = [r.profile_id for r in board.page_range(4, 3)]
    c = [r.profile_id for r in board.page_range(7, 3)]

    assert a + b + c == order
    assert len({*a, *b, *c}) == len(order)              # no overlap


@pytest.mark.parametrize('opts', [ALL, INVERTED])
def test_ranges_are_stable_over_a_tie_cluster(opts):
    """Every player identical on both sort keys -- only profile_id separates them, so tiling must be
    deterministic and gapless forward and inverted."""
    game = GameFactory()
    stamp = timezone.now()
    for _ in range(9):
        ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100, most_recent_trophy_date=stamp)
    order = _ids(game, opts)
    board = _ever(game, opts)

    tiled = []
    for start in (1, 4, 7):
        tiled += [r.profile_id for r in board.page_range(start, 3)]

    assert tiled == order
    assert len(set(tiled)) == 9


def test_range_start_is_clamped_and_past_the_end_is_empty():
    game = GameFactory()
    for pct in (100, 60, 20):
        _player(game, pct, minutes_ago=pct)
    order = _ids(game, DEFAULT)
    board = _ever(game, DEFAULT)

    assert [r.profile_id for r in board.page_range(0, 2)] == order[:2]   # clamps to 1
    assert board.page_range(99, 10) == []                               # past the end


def test_empty_board_range_is_empty():
    assert _ever(GameFactory(), DEFAULT).page_range(1) == []


# --- EverythingBoard: rank ---------------------------------------------------


def test_rank_matches_the_board_order_for_every_player():
    game = GameFactory()
    stamp = timezone.now()
    for _ in range(3):
        ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100, most_recent_trophy_date=stamp)
    for pct in (88, 88, 55, 20):
        _player(game, pct, minutes_ago=pct)

    from trophies.models import Profile
    board = _ever(game, DEFAULT)
    for position, pid in enumerate(_ids(game, DEFAULT), start=1):
        assert board.rank_for(Profile.objects.get(pk=pid)) == position


def test_rank_is_canonical_regardless_of_invert():
    game = GameFactory()
    rows = [_player(game, 100 - i, minutes_ago=i + 1) for i in range(5)]
    third = rows[2].profile

    assert _ever(game, DEFAULT).rank_for(third) == 3
    assert _ever(game, BoardOptions(invert=True)).rank_for(third) == 3


def test_rank_reflects_the_active_filters():
    game = GameFactory()
    _player(game, 95, minutes_ago=5, registered=False)   # ahead, but unregistered
    me = _player(game, 80, minutes_ago=5, registered=True)

    assert _ever(game, DEFAULT).rank_for(me.profile) == 2
    assert _ever(game, BoardOptions(registered_only=True)).rank_for(me.profile) == 1


def test_rank_is_none_when_the_viewer_is_filtered_out():
    game = GameFactory()
    zero = _player(game, 0, minutes_ago=None)

    assert _ever(game, DEFAULT).rank_for(zero.profile) is None
    assert _ever(game, ALL).rank_for(zero.profile) == 1


def test_rank_is_none_for_a_non_owner_or_anonymous():
    game = GameFactory()
    _player(game, 100, minutes_ago=5)

    assert _ever(game, DEFAULT).rank_for(ProfileFactory()) is None
    assert _ever(game, DEFAULT).rank_for(None) is None


# --- EverythingBoard: search suggest -----------------------------------------


def test_suggest_matches_by_name_and_carries_rank():
    game = GameFactory()
    for i, name in enumerate(['AceHunter', 'AceRunner', 'Nobody']):
        ProfileGameFactory(game=game, profile=ProfileFactory(psn_username=name), progress=100 - i,
                           most_recent_trophy_date=timezone.now() - timedelta(minutes=i + 1))

    results = _ever(game, DEFAULT).suggest('ace')

    assert {r['profile'].psn_username for r in results} == {'acehunter', 'acerunner'}  # stored lowercased
    ranks = {r['profile'].psn_username: r['rank'] for r in results}
    assert ranks['acehunter'] == 1 and ranks['acerunner'] == 2


def test_suggest_is_scoped_to_the_filtered_board():
    game = GameFactory()
    ProfileGameFactory(game=game, profile=ProfileFactory(psn_username='ZedEarner'), progress=50,
                       most_recent_trophy_date=timezone.now())
    ProfileGameFactory(game=game, profile=ProfileFactory(psn_username='ZedZero'), progress=0,
                       most_recent_trophy_date=None)

    assert {r['profile'].psn_username for r in _ever(game, DEFAULT).suggest('zed')} == {'zedearner'}
    assert {r['profile'].psn_username for r in _ever(game, ALL).suggest('zed')} == {'zedearner', 'zedzero'}


def test_suggest_short_query_returns_empty():
    game = GameFactory()
    _player(game, 100, minutes_ago=5)
    assert _ever(game, DEFAULT).suggest('a') == []
    assert _ever(game, DEFAULT).suggest('') == []


# --- EverythingBoard: row_at_rank (the number typeahead) ---------------------


def test_row_at_rank_returns_the_hunter_at_that_canonical_rank():
    game = GameFactory()
    players = [_player(game, 100 - i, minutes_ago=i + 1) for i in range(5)]
    board = _ever(game, DEFAULT)

    for n, p in enumerate(players, start=1):
        row = board.row_at_rank(n)
        assert row['profile'].id == p.profile_id
        assert row['rank'] == n


def test_row_at_rank_is_canonical_even_when_inverted():
    game = GameFactory()
    leader = _player(game, 100, minutes_ago=5)
    _player(game, 60, minutes_ago=1)

    assert _ever(game, INVERTED).row_at_rank(1)['profile'].id == leader.profile_id


def test_row_at_rank_past_the_board_is_none():
    game = GameFactory()
    _player(game, 100, minutes_ago=5)
    assert _ever(game, DEFAULT).row_at_rank(2) is None


def test_row_at_rank_is_a_single_query(django_assert_num_queries):
    """The rank is the fetch offset, so row_at_rank must NOT re-count it -- one indexed slice, no COUNT."""
    game = GameFactory()
    for i in range(5):
        _player(game, 100 - i, minutes_ago=i + 1)
    board = _ever(game, DEFAULT)

    with django_assert_num_queries(1):
        row = board.row_at_rank(3)
    assert row['rank'] == 3


def test_row_at_rank_respects_the_filters():
    game = GameFactory()
    _player(game, 100, minutes_ago=5, registered=False)     # rank 1 overall, filtered out for members
    member = _player(game, 90, minutes_ago=1, registered=True)

    assert _ever(game, DEFAULT).row_at_rank(1)['progress'] == 100
    assert _ever(game, BoardOptions(registered_only=True)).row_at_rank(1)['profile'].id == member.profile_id


# --- group / speed / playtime boards -----------------------------------------


def _group(game, gid='default'):
    return TrophyGroup.objects.create(game=game, trophy_group_id=gid, defined_trophies={})


def _ptg(group, profile, progress, last_minutes_ago=None, completion_seconds=None):
    last = None if last_minutes_ago is None else timezone.now() - timedelta(minutes=last_minutes_ago)
    return ProfileTrophyGroup.objects.create(
        profile=profile, trophy_group=group, progress=progress,
        last_trophy_at=last, completion_seconds=completion_seconds,
    )


def test_group_progress_board_ranks_by_completion_then_who_finished_first():
    game = GameFactory()
    group = _group(game)
    late = _ptg(group, ProfileFactory(), 100, last_minutes_ago=10)
    early = _ptg(group, ProfileFactory(), 100, last_minutes_ago=500)
    chaser = _ptg(group, ProfileFactory(), 60, last_minutes_ago=1)

    board = svc.GroupProgressBoard(game, group, DEFAULT)
    order = [r.profile_id for r in board.ordered()]
    assert order == [early.profile_id, late.profile_id, chaser.profile_id]
    assert board.rank_for(early.profile) == 1


def test_group_board_excludes_hidden_games():
    game = GameFactory()
    group = _group(game)
    shown = ProfileFactory()
    hidden = ProfileFactory()
    _ptg(group, shown, 80, last_minutes_ago=5)
    _ptg(group, hidden, 90, last_minutes_ago=5)
    # the hidden player's ProfileGame for this game is user-hidden -> off the board
    ProfileGameFactory(game=game, profile=hidden, progress=90, user_hidden=True)

    board = svc.GroupProgressBoard(game, group, DEFAULT)
    assert [r.profile_id for r in board.ordered()] == [shown.id]


def test_group_progress_only_earners_hides_sub_one_percent():
    game = GameFactory()
    group = _group(game)
    started = _ptg(group, ProfileFactory(), 5, last_minutes_ago=5)
    _ptg(group, ProfileFactory(), 0, last_minutes_ago=5)          # earned <1% -> progress floored to 0

    assert [r.profile_id for r in svc.GroupProgressBoard(game, group, DEFAULT).ordered()] == [started.profile_id]
    assert svc.GroupProgressBoard(game, group, ALL).size() == 2


def test_speed_board_ranks_fastest_first_and_only_completers():
    game = GameFactory()
    group = _group(game)
    fast = _ptg(group, ProfileFactory(), 100, last_minutes_ago=10, completion_seconds=3600)
    slow = _ptg(group, ProfileFactory(), 100, last_minutes_ago=10, completion_seconds=7200)
    _ptg(group, ProfileFactory(), 60, last_minutes_ago=5, completion_seconds=None)   # not complete -> excluded

    board = svc.GroupSpeedBoard(game, group, DEFAULT)
    assert [r.profile_id for r in board.ordered()] == [fast.profile_id, slow.profile_id]
    assert board.size() == 2
    assert board.rank_for(fast.profile) == 1


def test_playtime_board_ranks_most_played_first_and_excludes_no_data():
    game = GameFactory()
    most = ProfileGameFactory(game=game, profile=ProfileFactory(), play_duration=timedelta(hours=100))
    some = ProfileGameFactory(game=game, profile=ProfileFactory(), play_duration=timedelta(hours=10))
    ProfileGameFactory(game=game, profile=ProfileFactory(), play_duration=None)       # no reported time -> excluded

    board = svc.PlaytimeBoard(game, DEFAULT)
    assert [r.profile_id for r in board.ordered()] == [most.profile_id, some.profile_id]
    assert board.size() == 2


# --- board resolution --------------------------------------------------------


def test_resolve_board_maps_params_to_board_types():
    game = GameFactory()
    _group(game, 'default')
    _group(game, '001')

    assert isinstance(svc.resolve_board(game, '', DEFAULT), svc.EverythingBoard)          # default
    assert isinstance(svc.resolve_board(game, 'progress:all', DEFAULT), svc.EverythingBoard)
    assert isinstance(svc.resolve_board(game, 'playtime', DEFAULT), svc.PlaytimeBoard)
    assert isinstance(svc.resolve_board(game, 'progress:001', DEFAULT), svc.GroupProgressBoard)
    assert isinstance(svc.resolve_board(game, 'speed:default', DEFAULT), svc.GroupSpeedBoard)


def test_resolve_board_falls_back_to_everything_for_unknown_or_missing_group():
    game = GameFactory()
    assert isinstance(svc.resolve_board(game, 'garbage', DEFAULT), svc.EverythingBoard)
    assert isinstance(svc.resolve_board(game, 'progress:999', DEFAULT), svc.EverythingBoard)  # no such group


# --- index contract ----------------------------------------------------------


def test_each_board_order_matches_the_index_it_relies_on():
    from trophies.models import ProfileGame, ProfileTrophyGroup

    def order_fields(board_cls):
        # forward order as a comparable list of field strings ('-progress', 'profile_id', ...)
        out = []
        for k in board_cls.KEYS:
            out.append(('-' if k.desc else '') + k.field)
        return out

    pg_idx = {i.name: i for i in ProfileGame._meta.indexes}
    ptg_idx = {i.name: i for i in ProfileTrophyGroup._meta.indexes}

    # EverythingBoard <-> pg_game_leaderboard_idx (nullable date sits between, matched loosely by position)
    assert pg_idx['pg_game_leaderboard_idx'].fields == ['game', '-progress', 'most_recent_trophy_date', 'profile']
    assert order_fields(svc.EverythingBoard)[0] == '-progress'
    assert order_fields(svc.EverythingBoard)[-1] == 'profile_id'

    # PlaytimeBoard <-> pg_playtime_idx
    assert pg_idx['pg_playtime_idx'].fields == ['game', '-play_duration', 'profile']
    assert order_fields(svc.PlaytimeBoard) == ['-play_duration', 'profile_id']

    # Group boards <-> ptg indexes (trophy_group leads the index; the board scopes to it via filter)
    assert ptg_idx['ptg_progress_idx'].fields == ['trophy_group', '-progress', 'last_trophy_at', 'profile']
    assert order_fields(svc.GroupProgressBoard)[0] == '-progress'
    assert ptg_idx['ptg_speed_idx'].fields == ['trophy_group', 'completion_seconds', 'last_trophy_at', 'profile']
    assert order_fields(svc.GroupSpeedBoard)[0] == 'completion_seconds'
