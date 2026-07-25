"""Tests for the Leaderboard tab endpoint and its wiring into the game detail page.

The endpoint serves three shapes from one URL (panel / continuation rows / jump window), and the panel
is deliberately NOT server-rendered with the page -- that laziness is the whole performance argument, so
it gets an explicit assertion rather than being left to drift.
"""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from tests.factories import GameFactory, ProfileFactory, ProfileGameFactory
from trophies.models import ProfileTrophyGroup, TrophyGroup

pytestmark = pytest.mark.django_db


@pytest.fixture
def client(client):
    """Model traffic that arrived through Cloudflare.

    This endpoint's URL is `/games/<x>/<y>/`, which is the shape
    CloudflareOriginGuardMiddleware bounces when a request lacks a CF-Ray header -- it protects the
    profile-scoped detail pages from scrapers that cached the origin IP. Real browser fetches for this
    panel come from a page already served through the proxy, so they always carry the header. Setting it
    here keeps the guard live for every other path instead of switching it off for the suite.
    """
    client.defaults['HTTP_CF_RAY'] = 'test-ray'
    return client


def _url(game, **params):
    url = reverse('game_leaderboard', kwargs={'np_communication_id': game.np_communication_id})
    if params:
        url += '?' + '&'.join(f'{k}={v}' for k, v in params.items())
    return url


def _board(n, game=None):
    game = game or GameFactory()
    rows = [
        ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100 - i,
                           most_recent_trophy_date=timezone.now() - timedelta(minutes=i + 1))
        for i in range(n)
    ]
    return game, rows


# --- response shapes ---------------------------------------------------------


def _ranks(html):
    return [int(seg.split('"')[0]) for seg in html.split('data-lb-rank="')[1:]]


def test_panel_carries_the_header_the_spacer_total_and_a_first_window(client):
    game, _ = _board(3)

    body = client.get(_url(game)).content.decode()

    assert 'gd-lb__head' in body
    assert 'hunters on the board' in body
    assert 'data-lb-total="3"' in body            # the JS sizes the virtual spacer from this
    assert body.count('gd-lb__row') >= 3
    assert 'gd-lb__colhead--progress' in body     # standings column header
    assert 'Trophies' in body and 'Latest' in body


def test_medal_classes_key_off_rank_not_dom_position(client):
    """Virtualized rows mount in scroll order, so the top-3 medal colour must come from the rank itself
    (a class), not nth-child -- otherwise it colours whatever three rows are first in the DOM."""
    game, _ = _board(80)

    # A deep window (ranks 51..80) must carry NO medal classes...
    deep = client.get(_url(game, range=51, **{'from': 51})).content.decode()
    assert 'gd-lb__rank--1' not in deep and 'gd-lb__rank--2' not in deep and 'gd-lb__rank--3' not in deep

    # ...while the top window carries exactly ranks 1/2/3.
    top = client.get(_url(game, range=1, **{'from': 1})).content.decode()
    assert top.count('gd-lb__rank--1') == 1
    assert top.count('gd-lb__rank--2') == 1
    assert top.count('gd-lb__rank--3') == 1


def test_panel_stamps_the_page_size_for_the_client(client):
    game, _ = _board(3)

    assert 'data-lb-page-size="50"' in client.get(_url(game)).content.decode()


def test_range_returns_rows_only_numbered_from_the_given_rank(client):
    """A virtual-window fetch: just the rows, positioned by the client via their rank."""
    game, _ = _board(80)                          # more than one PAGE_SIZE (50) window

    body = client.get(_url(game, range=51, **{'from': 51})).content.decode()

    assert 'gd-lb__head' not in body and 'gd-lb__list' not in body   # rows only
    assert _ranks(body) == list(range(51, 81))    # ranks 51..80, contiguous


def test_adjacent_ranges_tile_without_gap_or_overlap(client):
    """The virtual-scroll guarantee, end to end through HTTP."""
    game, _ = _board(80)

    first = _ranks(client.get(_url(game, range=1, **{'from': 1})).content.decode())
    second = _ranks(client.get(_url(game, range=51, **{'from': 51})).content.decode())

    assert first == list(range(1, 51)) and second == list(range(51, 81))
    assert set(first).isdisjoint(second)


def test_range_numbering_counts_down_when_inverted(client):
    game, _ = _board(80)
    total = 80

    # Inverted window at display position 51 -> canonical ranks count DOWN from (total-51+1)=30.
    body = client.get(_url(game, invert=1, range=51, **{'from': total - 51 + 1})).content.decode()

    assert _ranks(body) == list(range(30, 0, -1))   # 30, 29, ..., 1


def test_empty_board_shows_the_empty_state(client):
    """A game nobody's played, with filters explicitly off, gets the 'no one at all' message."""
    body = client.get(_url(GameFactory(), earners=0)).content.decode()

    assert 'No hunters yet' in body
    assert 'gd-lb__row' not in body


def test_filtered_to_empty_shows_the_relax_your_filters_hint(client):
    """The default earners-only view of a game with only 0% owners should suggest relaxing, not dead-end."""
    game = GameFactory()
    ProfileGameFactory(game=game, profile=ProfileFactory(), progress=0, most_recent_trophy_date=None)

    body = client.get(_url(game)).content.decode()      # default: earners only -> board empty

    assert 'No hunters match' in body
    assert 'to see everyone' in body


def test_unknown_game_404s(client):
    game = GameFactory()
    game.np_communication_id = 'NPWR_NOPE_00'

    assert client.get(_url(game)).status_code == 404


def test_garbage_range_params_do_not_error(client):
    game, _ = _board(3)

    response = client.get(_url(game, range='nope', **{'from': 'nope', 'count': 'nope'}))

    assert response.status_code == 200


def test_board_is_public(client):
    """Anonymous visitors get the board -- it's the SEO-facing side of the page."""
    game, _ = _board(2)

    assert client.get(_url(game)).status_code == 200


# --- the viewer's own standing -----------------------------------------------


def test_linked_viewer_sees_their_rank_and_their_row_is_marked(client):
    game, rows = _board(5)
    me = rows[2]
    me.profile.is_linked = True
    me.profile.save(update_fields=['is_linked'])
    client.force_login(me.profile.user)

    body = client.get(_url(game)).content.decode()

    assert 'data-lb-jump' in body      # the "jump to my rank" control
    assert '#3' in body
    assert 'gd-lb__row--you' in body


def test_ranked_viewer_exposes_their_rank_for_the_minibar(client):
    """The rank rides on the .gd-lb root; the JS reads it to fill the minibar 'You #N' widget."""
    game, rows = _board(3)
    me = rows[0]
    me.profile.is_linked = True
    me.profile.save(update_fields=['is_linked'])
    client.force_login(me.profile.user)

    body = client.get(_url(game)).content.decode()

    assert 'data-lb-viewer-rank="1"' in body


def test_anonymous_viewer_exposes_no_rank(client):
    game, _ = _board(3)

    assert 'data-lb-viewer-rank=""' in client.get(_url(game)).content.decode()


def test_anonymous_viewer_gets_no_rank_control(client):
    game, _ = _board(3)

    body = client.get(_url(game)).content.decode()

    assert 'gd-lb__row--you' not in body
    assert 'data-lb-jump' not in body


def test_rows_show_the_players_trophy_haul(client):
    """Per-tier counts come free off the row's earned_trophies JSON -- no extra query."""
    game = GameFactory()
    ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100,
                       most_recent_trophy_date=timezone.now(),
                       earned_trophies={'bronze': 30, 'silver': 8, 'gold': 3, 'platinum': 1})

    body = client.get(_url(game)).content.decode()

    assert 'gd-lb__trophies' in body
    assert 'gd-gcount--platinum' in body
    assert 'Bronze: 30' in body
    assert 'Gold: 3' in body


def test_rows_omit_tiers_the_player_has_not_earned(client):
    """A player with no platinum shows no platinum dot -- so the plat dot appearing IS the finished signal."""
    game = GameFactory()
    ProfileGameFactory(game=game, profile=ProfileFactory(), progress=60,
                       most_recent_trophy_date=timezone.now(),
                       earned_trophies={'bronze': 12, 'silver': 2, 'gold': 0, 'platinum': 0})

    body = client.get(_url(game)).content.decode()

    assert 'gd-gcount--bronze' in body
    assert 'gd-gcount--platinum' not in body
    assert 'gd-gcount--gold' not in body


def test_rows_show_the_tiebreaker_time_under_the_date(client):
    """Two players at the same progress are ranked by exact time, so the time must be visible or the
    ordering looks arbitrary."""
    game = GameFactory()
    ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100,
                       most_recent_trophy_date=timezone.now().replace(hour=8, minute=15))
    ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100,
                       most_recent_trophy_date=timezone.now().replace(hour=20, minute=42))

    body = client.get(_url(game)).content.decode()

    assert body.count('gd-lb__time') == 2            # a time cell on each row (visible mobile + desktop)
    assert body.count('gd-lb__date--long') == 2      # spelled-out date for desktop
    assert body.count('gd-lb__date--short') == 2     # compact numeric date for the tight mobile row
    assert ':15' in body and ':42' in body           # the two distinct minutes both render


def test_zero_trophy_owner_renders_cleanly(client):
    """An owner synced with the empty-dict default and no trophy date must not crash or print zeros --
    no tier dots, and the date cell falls back to a dash."""
    game = GameFactory()
    ProfileGameFactory(game=game, profile=ProfileFactory(), progress=0,
                       most_recent_trophy_date=None, earned_trophies={})

    body = client.get(_url(game, earners=0)).content.decode()   # earners-only default would hide them

    assert body.count('gd-lb__row') == 1
    assert 'gd-gcount' not in body       # no tier dots for an empty haul
    assert 'gd-lb__time' not in body     # no time span when there's no date
    assert '&mdash;' in body or '—' in body


def test_hidden_players_are_absent_from_the_endpoint(client):
    game, _ = _board(2)
    ProfileGameFactory(game=game, profile=ProfileFactory(), progress=100, user_hidden=True)

    body = client.get(_url(game)).content.decode()

    assert body.count('gd-lb__row') == 2


# --- controls: filters, invert, jump-to-rank ---------------------------------


def test_panel_renders_the_controls_with_default_state(client):
    game, _ = _board(3)

    body = client.get(_url(game)).content.decode()

    assert 'data-lb-opt="earners"' in body
    assert 'data-lb-opt="registered"' in body
    assert 'data-lb-opt="invert"' in body
    assert 'data-lb-find' in body                            # the rank-or-hunter search field
    # Earners is on by default; the others off.
    assert 'data-lb-opt="earners" aria-pressed="true"' in body
    assert 'data-lb-opt="invert" aria-pressed="false"' in body


def test_earners_filter_default_hides_zero_trophy_owners(client):
    game = GameFactory()
    ProfileGameFactory(game=game, profile=ProfileFactory(), progress=80,
                       most_recent_trophy_date=timezone.now())
    ProfileGameFactory(game=game, profile=ProfileFactory(), progress=0, most_recent_trophy_date=None)

    default = client.get(_url(game)).content.decode()
    show_all = client.get(_url(game, earners=0)).content.decode()

    assert default.count('gd-lb__row') == 1                 # 0% owner hidden by default
    assert show_all.count('gd-lb__row') == 2


def test_registered_filter_hides_synced_only_profiles(client):
    game = GameFactory()
    ProfileGameFactory(game=game, profile=ProfileFactory(), progress=80,
                       most_recent_trophy_date=timezone.now())
    ProfileGameFactory(game=game, profile=ProfileFactory(user=None), progress=90,
                       most_recent_trophy_date=timezone.now())

    assert client.get(_url(game)).content.decode().count('gd-lb__row') == 2
    assert client.get(_url(game, registered=1)).content.decode().count('gd-lb__row') == 1


def test_invert_reverses_the_visible_order(client):
    game, rows = _board(4)                                   # progress 100, 99, 98, 97
    order = [reverse('profile_detail', args=[r.profile.psn_username]) for r in rows]

    forward = client.get(_url(game)).content.decode()
    inverted = client.get(_url(game, invert=1)).content.decode()

    assert forward.index(order[0]) < forward.index(order[-1])    # best first
    assert inverted.index(order[-1]) < inverted.index(order[0])  # worst first


def test_invert_keeps_ranks_canonical(client):
    """The top row inverted is the WORST player, but still labeled with its canonical (highest) rank."""
    game, _ = _board(5)

    body = client.get(_url(game, invert=1)).content.decode()
    first_rank = body.split('data-lb-rank="')[1].split('"')[0]

    assert first_rank == '5'                                 # counts down from the bottom


def test_inverted_panel_first_window_starts_at_the_highest_rank(client):
    """Inverted: the top of the display is the WORST player, labelled with the highest (canonical) rank,
    counting down. (Jumping itself is client-side now -- a scroll position, not a server round-trip.)"""
    game, _ = _board(30)

    body = client.get(_url(game, invert=1)).content.decode()

    assert _ranks(body)[0] == 30                             # worst player first
    assert _ranks(body) == list(range(30, 0, -1))            # counts down 30..1


# --- search typeahead --------------------------------------------------------


def test_suggest_returns_matching_hunters_with_rank(client):
    import json
    game = GameFactory()
    a = ProfileGameFactory(game=game, profile=ProfileFactory(psn_username='TrophyKing'), progress=100,
                           most_recent_trophy_date=timezone.now() - timedelta(minutes=5))
    ProfileGameFactory(game=game, profile=ProfileFactory(psn_username='SomeoneElse'), progress=90,
                       most_recent_trophy_date=timezone.now())

    data = json.loads(client.get(_url(game, suggest='trophy')).content)

    assert len(data['players']) == 1
    assert data['players'][0]['username'] == 'trophyking'   # stored lowercased
    assert data['players'][0]['rank'] == 1                   # 100% earlier -> rank 1
    assert 'url' in data['players'][0]


def test_suggest_respects_the_active_filters(client):
    import json
    game = GameFactory()
    ProfileGameFactory(game=game, profile=ProfileFactory(psn_username='HunterA', user=None), progress=95,
                       most_recent_trophy_date=timezone.now())
    ProfileGameFactory(game=game, profile=ProfileFactory(psn_username='HunterB'), progress=80,
                       most_recent_trophy_date=timezone.now())

    both = json.loads(client.get(_url(game, suggest='hunter')).content)
    members = json.loads(client.get(_url(game, suggest='hunter', registered=1)).content)

    assert {p['username'] for p in both['players']} == {'huntera', 'hunterb'}
    assert {p['username'] for p in members['players']} == {'hunterb'}   # unregistered filtered out


def test_suggest_below_two_chars_returns_nothing(client):
    import json
    game, _ = _board(3)

    data = json.loads(client.get(_url(game, suggest='a')).content)

    assert data['players'] == []


def test_at_previews_the_hunter_at_a_canonical_rank(client):
    import json
    game = GameFactory()
    ProfileGameFactory(game=game, profile=ProfileFactory(psn_username='TopDog'), progress=100,
                       most_recent_trophy_date=timezone.now() - timedelta(minutes=5))
    ProfileGameFactory(game=game, profile=ProfileFactory(psn_username='Runner'), progress=90,
                       most_recent_trophy_date=timezone.now())

    data = json.loads(client.get(_url(game, at=2)).content)

    assert len(data['players']) == 1
    assert data['players'][0]['username'] == 'runner'
    assert data['players'][0]['rank'] == 2


def test_at_ignores_invert_and_stays_canonical(client):
    """The number a viewer types is the rank shown on a row, which counts from the best down -- so ?at=1
    is the leader even while the board is displayed bottom-first."""
    import json
    game, _ = _board(4)

    data = json.loads(client.get(_url(game, at=1, invert=1)).content)

    assert data['players'][0]['rank'] == 1
    assert data['players'][0]['progress'] == 100    # the leader, not the inverted first row


def test_at_past_the_board_returns_nothing(client):
    import json
    game, _ = _board(3)

    data = json.loads(client.get(_url(game, at=99)).content)

    assert data['players'] == []


def test_at_respects_the_active_filters(client):
    import json
    game = GameFactory()
    ProfileGameFactory(game=game, profile=ProfileFactory(psn_username='Member'), progress=100,
                       most_recent_trophy_date=timezone.now() - timedelta(minutes=5))
    ProfileGameFactory(game=game, profile=ProfileFactory(psn_username='Guest', user=None), progress=90,
                       most_recent_trophy_date=timezone.now())

    everyone = json.loads(client.get(_url(game, at=2)).content)
    members = json.loads(client.get(_url(game, at=2, registered=1)).content)

    assert everyone['players'][0]['username'] == 'guest'
    assert members['players'] == []                 # only one member, so rank 2 is empty on that board


# --- wiring into the page ----------------------------------------------------


def _detail(client, game):
    return client.get(reverse('game_detail',
                              kwargs={'np_communication_id': game.np_communication_id})).content.decode()


def test_detail_page_offers_the_tab_but_does_not_render_the_board(client):
    """The laziness IS the performance argument: rendering it inline would undo the whole design."""
    game, _ = _board(3)

    body = _detail(client, game)

    assert 'gd-tab-leaderboard' in body            # the chip is there
    assert 'data-lb-src' in body                   # and the panel knows where to fetch
    assert 'gd-lb__row' not in body                # but no rows shipped with the page
    assert 'hunters on the board' not in body
    # The minibar carries the leaderboard search + filters reach + the "You #N" rank widget.
    assert 'data-lb-mb-find' in body
    assert 'data-lb-mb-filters' in body
    assert 'data-lb-mb-rank' in body
    assert 'data-lb-mb-title' in body             # the board-title slot (filled by JS on the Ranks tab, desktop)


def test_detail_page_renders_with_the_leaderboard_deep_link(client):
    """?view=leaderboard is the path that previously tripped the switcher's init order."""
    game, _ = _board(2)
    url = reverse('game_detail', kwargs={'np_communication_id': game.np_communication_id})

    response = client.get(url + '?view=leaderboard')

    assert response.status_code == 200
    assert 'gd-view-leaderboard' in response.content.decode()


# --- board selection (?board=) -----------------------------------------------


def _ptg_row(game, gid, progress, minutes_ago, completion_seconds=None, username=None):
    group, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id=gid, defaults={'defined_trophies': {}})
    profile = ProfileFactory(psn_username=username) if username else ProfileFactory()
    ProfileGameFactory(game=game, profile=profile, progress=progress)   # owns the game (not hidden)
    last = timezone.now() - timedelta(minutes=minutes_ago)
    return ProfileTrophyGroup.objects.create(
        profile=profile, trophy_group=group, progress=progress,
        first_trophy_at=last - timedelta(seconds=completion_seconds or 0),
        last_trophy_at=last,
        completion_seconds=completion_seconds,
    )


def test_board_param_routes_to_a_dlc_group_board(client):
    game, _ = _board(3)                                        # overall board has 3 ProfileGame rows
    _ptg_row(game, '001', progress=100, minutes_ago=5)
    _ptg_row(game, '001', progress=40, minutes_ago=2)         # only 2 players on the DLC group

    body = client.get(_url(game, board='progress:001')).content.decode()

    assert body.count('gd-lb__row') == 2                     # the DLC group's population, not the overall 3
    assert 'data-lb-total="2"' in body


def test_board_param_routes_to_the_speed_board(client):
    game = GameFactory()
    _ptg_row(game, 'default', progress=100, minutes_ago=10, completion_seconds=3600)
    _ptg_row(game, 'default', progress=100, minutes_ago=10, completion_seconds=7200)
    _ptg_row(game, 'default', progress=60, minutes_ago=5, completion_seconds=None)   # not complete -> off speed board

    body = client.get(_url(game, board='speed:default')).content.decode()

    assert body.count('data-lb-rank=') == 2                  # only the two completers (row--speed doubles a gd-lb__row count)
    assert 'gd-lb__metric--speed' in body
    assert 'gd-lb__col--start' in body and 'gd-lb__col--finish' in body   # started -> finished window
    assert 'gd-lb__coltime' in body                          # date AND time on the start/finish columns
    assert 'gd-lb__colhead--speed' in body                   # the column header labels the columns
    assert 'Started' in body and 'Elapsed' in body and 'Finished' in body
    assert 'data-lb-total="2"' in body


def test_board_param_routes_to_the_playtime_board(client):
    game = GameFactory()
    ProfileGameFactory(game=game, profile=ProfileFactory(), play_duration=timedelta(hours=50),
                       progress=87, last_played_date_time=timezone.now())
    ProfileGameFactory(game=game, profile=ProfileFactory(), play_duration=None)       # no reported time -> excluded

    body = client.get(_url(game, board='playtime')).content.decode()

    assert body.count('data-lb-rank=') == 1
    assert 'gd-lb__metric--playtime' in body
    assert '50h' in body                                     # the compact play-time label
    assert '87%' in body                                     # overall progress column
    assert 'gd-lb__col--prog' in body and 'gd-lb__col--last' in body   # progress + last-played context
    assert 'gd-lb__colhead--playtime' in body                # header labels the columns
    assert 'Play time' in body and 'Last played' in body
    assert 'data-lb-total="1"' in body


def test_unknown_board_param_falls_back_to_the_overall_board(client):
    game, _ = _board(3)

    body = client.get(_url(game, board='nonsense')).content.decode()

    assert body.count('data-lb-rank=') == 3                  # the Everything board, unchanged


def test_switcher_shows_modes_and_a_group_row_for_a_dlc_game(client):
    game, _ = _board(2)
    # a DLC group with >=2 trophies (qualifies for a speed board) + playtime data -> all three modes
    from tests.factories import TrophyFactory
    TrophyGroup.objects.create(game=game, trophy_group_id='default', defined_trophies={})
    dlc = TrophyGroup.objects.create(game=game, trophy_group_id='001', trophy_group_name='DLC One', defined_trophies={})
    TrophyFactory(game=game, trophy_group_id='001', trophy_id=1)
    TrophyFactory(game=game, trophy_group_id='001', trophy_id=2)
    ProfileGameFactory(game=game, profile=ProfileFactory(), play_duration=timedelta(hours=5))
    ProfileTrophyGroup.objects.create(profile=ProfileFactory(), trophy_group=dlc, progress=100,
                                      last_trophy_at=timezone.now())

    body = client.get(_url(game)).content.decode()

    assert 'data-lb-boards' in body                          # the switcher rendered
    assert 'data-lb-board="progress:all"' in body            # Standings (Everything)
    assert 'data-lb-board="speed:001"' in body               # Fastest, group row for the DLC
    assert 'data-lb-board="playtime"' in body                # Most Played
    assert 'DLC One' in body


def test_dlc_groups_collapse_into_a_dropdown(client):
    game, _ = _board(2)
    TrophyGroup.objects.create(game=game, trophy_group_id='default', defined_trophies={})
    TrophyGroup.objects.create(game=game, trophy_group_id='001', trophy_group_name='First DLC', defined_trophies={})
    TrophyGroup.objects.create(game=game, trophy_group_id='002', trophy_group_name='Second DLC', defined_trophies={})

    body = client.get(_url(game)).content.decode()

    assert 'data-lb-drop' in body and 'data-lb-dropmenu' in body       # the dropdown + its menu
    assert 'data-lb-board="progress:001"' in body and 'First DLC' in body      # both DLCs are menu items
    assert 'data-lb-board="progress:002"' in body and 'Second DLC' in body
    assert 'data-lb-board="progress:default"' in body                  # Base Game stays a pill
    assert 'data-lb-board="progress:all"' in body                      # Everything stays a pill
    assert '<span>DLC</span>' in body                                  # no DLC active -> button reads "DLC"


def test_dropdown_button_shows_and_marks_the_active_dlc(client):
    game, _ = _board(2)
    TrophyGroup.objects.create(game=game, trophy_group_id='default', defined_trophies={})
    TrophyGroup.objects.create(game=game, trophy_group_id='001', trophy_group_name='Blood and Wine', defined_trophies={})

    body = client.get(_url(game, board='progress:001')).content.decode()

    assert '<span>Blood and Wine</span>' in body                       # button carries the active DLC name
    assert 'gd-lb__dropbtn' in body and 'aria-pressed="true"' in body  # and reads as active


def test_started_toggle_hidden_on_non_progress_boards(client):
    game = GameFactory()
    _ptg_row(game, 'default', progress=100, minutes_ago=5, completion_seconds=3600)

    speed = client.get(_url(game, board='speed:default')).content.decode()
    assert 'data-lb-opt="earners"' not in speed                # no-op on speed -> hidden
    assert 'data-lb-opt="registered"' in speed and 'data-lb-opt="invert"' in speed   # these still apply

    standings = client.get(_url(game, board='progress:all')).content.decode()
    assert 'data-lb-opt="earners"' in standings                # kept on standings


def test_empty_copy_is_board_aware(client):
    game = GameFactory()
    TrophyGroup.objects.create(game=game, trophy_group_id='default', defined_trophies={})

    speed = client.get(_url(game, board='speed:default')).content.decode()   # no completers
    assert 'No finishers yet' in speed


def test_dlc_game_defaults_to_base_game_standings(client):
    game, _ = _board(2)
    base = TrophyGroup.objects.create(game=game, trophy_group_id='default', defined_trophies={})
    TrophyGroup.objects.create(game=game, trophy_group_id='001', trophy_group_name='DLC One', defined_trophies={})
    ProfileTrophyGroup.objects.create(profile=ProfileFactory(), trophy_group=base, progress=100,
                                      last_trophy_at=timezone.now())                          # so the board isn't empty

    body = client.get(_url(game)).content.decode()                                            # no ?board= -> the default

    assert 'data-lb-board="progress:default"' in body        # active board is base-game standings
    assert 'Base Game Standings' in body                     # the header title reflects it
    assert 'data-lb-board="progress:default" aria-pressed="true"' in body   # Base Game chip is the active one


def test_switcher_absent_when_only_one_board(client):
    """A single-group game with no speed (no >=2-trophy group here) and no playtime data has just the
    overall board -- nothing to switch, so no switcher chrome."""
    game, _ = _board(2)

    body = client.get(_url(game)).content.decode()

    assert 'data-lb-boards' not in body


def test_retired_players_modal_is_gone():
    """The orphaned modal + its 223 lines of dead JS were removed; nothing should reference them."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert not (root / 'static/js/game-players-modal.js').exists()
    assert not (root / 'templates/trophies/partials/game_detail/game_players_modal.html').exists()
    assert not (root / 'templates/trophies/partials/game_detail/game_detail_header.html').exists()
    assert 'game-players-modal' not in (root / 'templates/trophies/game_detail.html').read_text(encoding='utf-8')
