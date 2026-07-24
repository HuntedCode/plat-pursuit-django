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


def test_panel_carries_the_header_and_the_first_page(client):
    game, _ = _board(3)

    body = client.get(_url(game)).content.decode()

    assert 'gd-lb__head' in body
    assert 'hunters on the board' in body
    assert body.count('gd-lb__row') >= 3


def test_continuation_returns_rows_only_so_the_scroller_can_append(client):
    """Chrome in an appended page would duplicate the header partway down the list."""
    game, _ = _board(30)          # must exceed PAGE_SIZE or there is no second page
    first = client.get(_url(game)).content.decode()
    cursor = first.split('data-lb-next="')[1].split('"')[0]

    body = client.get(_url(game, after=cursor)).content.decode()

    assert 'gd-lb__row' in body
    assert 'gd-lb__head' not in body
    assert 'gd-lb__list' not in body


def test_pages_do_not_overlap(client):
    """The cursor boundary, end to end through HTTP rather than the service."""
    game, _ = _board(30)
    first = client.get(_url(game)).content.decode()
    cursor = first.split('data-lb-next="')[1].split('"')[0]
    second = client.get(_url(game, after=cursor)).content.decode()

    # Compare the row CURSORS, which identify players. Rank numbers would be the wrong key: they come
    # from ?from=, so a continuation fetched without it legitimately restarts numbering at 1.
    def cursors(html):
        return [seg.split('"')[0] for seg in html.split('data-lb-cursor="')[1:]]

    assert len(cursors(first)) == 25 and len(cursors(second)) == 5
    assert set(cursors(first)) & set(cursors(second)) == set()


def test_rank_numbering_continues_across_pages(client):
    game, _ = _board(30)
    first = client.get(_url(game)).content.decode()
    marker = first.split('data-lb-from="')[1].split('"')[0]
    cursor = first.split('data-lb-next="')[1].split('"')[0]

    second = client.get(_url(game, after=cursor, **{'from': marker})).content.decode()

    assert f'data-lb-rank="{marker}"' in second


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


def test_malformed_cursor_serves_the_first_page_rather_than_erroring(client):
    game, _ = _board(3)

    response = client.get(_url(game, after='garbage~~'))

    assert response.status_code == 200
    assert 'gd-lb__row' in response.content.decode()


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


def test_self_row_renders_whenever_the_viewer_is_ranked(client):
    """Rendered (hidden) even when the viewer is on the first page, so the JS can surface it once they
    scroll their own row away."""
    game, rows = _board(3)                                   # viewer will be on the first page
    me = rows[0]
    me.profile.is_linked = True
    me.profile.save(update_fields=['is_linked'])
    client.force_login(me.profile.user)

    body = client.get(_url(game)).content.decode()

    assert 'data-lb-self' in body
    assert 'gd-lb__self--bottom' in body                     # default edge before JS flips it


def test_anonymous_viewer_gets_no_self_row(client):
    game, _ = _board(3)

    assert 'data-lb-self' not in client.get(_url(game)).content.decode()


def test_anonymous_viewer_gets_no_rank_control(client):
    game, _ = _board(3)

    body = client.get(_url(game)).content.decode()

    assert 'gd-lb__row--you' not in body
    assert 'data-lb-jump' not in body


def test_jump_window_centres_on_the_viewer(client):
    """?around=me exists so a deep viewer doesn't have to page forward to reach themselves."""
    game, rows = _board(30)
    me = rows[19]
    me.profile.is_linked = True
    me.profile.save(update_fields=['is_linked'])
    client.force_login(me.profile.user)

    body = client.get(_url(game, around='me')).content.decode()

    assert 'gd-lb__row--you' in body
    assert 'gd-lb__head' not in body          # rows only; it replaces the list
    assert 'data-lb-rank="16"' in body        # opens a few places above rank 20


def test_jump_window_is_harmless_for_someone_not_on_the_board(client):
    game, _ = _board(3)
    outsider = ProfileFactory(is_linked=True)
    client.force_login(outsider.user)

    response = client.get(_url(game, around='me'))

    assert response.status_code == 200
    assert 'gd-lb__row' not in response.content.decode()


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

    assert body.count('gd-lb__time') == 2       # a time cell on each row
    assert body.count('gd-lb__date') == 2
    assert ':15' in body and ':42' in body      # the two distinct minutes both render


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


def test_jump_to_typed_rank_windows_on_that_position(client):
    game, _ = _board(60)                                     # big enough that rank 20 is mid-board

    body = client.get(_url(game, rank=20)).content.decode()

    assert 'gd-lb__head' not in body                         # rows only
    assert 'data-lb-rank="16"' in body                       # opens a few places above 20
    assert 'data-lb-rank="20"' in body
    assert 'data-lb-prev' in body                            # can scroll UP from the jump
    assert 'data-lb-next' in body                            # and DOWN


def test_jump_near_the_top_has_no_prev_marker(client):
    game, _ = _board(30)

    body = client.get(_url(game, rank=2)).content.decode()   # window clamps to the top

    assert 'data-lb-rank="1"' in body
    assert 'data-lb-prev' not in body                        # nothing above to load


def test_scroll_up_prepends_the_page_above_the_jump(client):
    """?before= returns the rows above, numbered contiguously, with no next marker (that end is loaded)."""
    game, _ = _board(30)
    window = client.get(_url(game, rank=20)).content.decode()
    prev_cursor = window.split('data-lb-prev="')[1].split('"')[0]
    fromtop = window.split('data-lb-fromtop="')[1].split('"')[0]     # rank of the window's top row (16)

    body = client.get(_url(game, before=prev_cursor, fromtop=fromtop)).content.decode()

    assert 'gd-lb__head' not in body and 'data-lb-next' not in body  # rows only, upward
    assert 'data-lb-rank="15"' in body                       # immediately above the window's top (16)
    assert 'data-lb-rank="1"' in body                        # up to the top of the board


def test_scroll_up_prepends_correctly_when_inverted(client):
    """Inverted upward-scroll numbering counts the other way -- the one combination hand-verified but
    previously untested at the HTTP layer."""
    game, _ = _board(60)
    window = client.get(_url(game, invert=1, rank=20)).content.decode()
    prev_cursor = window.split('data-lb-prev="')[1].split('"')[0]
    fromtop = window.split('data-lb-fromtop="')[1].split('"')[0]     # inverted window top = a HIGH rank

    body = client.get(_url(game, invert=1, before=prev_cursor, fromtop=fromtop)).content.decode()

    # Inverted: ranks above the window are HIGHER, so the prepend counts UP from fromtop+1.
    expected = str(int(fromtop) + 1)
    assert f'data-lb-rank="{expected}"' in body
    assert 'data-lb-next' not in body                        # upward only


def test_inverted_numbering_continues_correctly_across_a_page(client):
    """Inverted page 1 starts at the highest rank and counts DOWN; the next page must continue the count."""
    game, _ = _board(30)                                    # PAGE_SIZE is 25

    first = client.get(_url(game, invert=1)).content.decode()
    assert first.split('data-lb-rank="')[1].split('"')[0] == '30'   # worst player first
    marker_from = first.split('data-lb-from="')[1].split('"')[0]
    cursor = first.split('data-lb-next="')[1].split('"')[0]
    assert marker_from == '5'                               # 30 - 25 rows

    second = client.get(_url(game, invert=1, after=cursor, **{'from': marker_from})).content.decode()
    assert second.split('data-lb-rank="')[1].split('"')[0] == '5'   # continues down
    assert 'data-lb-rank="1"' in second                    # to the top of the board


def test_jump_to_out_of_range_rank_clamps(client):
    game, _ = _board(8)

    body = client.get(_url(game, rank=9999)).content.decode()

    assert 'data-lb-rank="8"' in body                        # clamped to the last rank
    assert 'gd-lb__row' in body


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
    # The minibar carries the leaderboard search + filters reach (shown while the Ranks tab is active).
    assert 'data-lb-mb-find' in body
    assert 'data-lb-mb-filters' in body


def test_detail_page_renders_with_the_leaderboard_deep_link(client):
    """?view=leaderboard is the path that previously tripped the switcher's init order."""
    game, _ = _board(2)
    url = reverse('game_detail', kwargs={'np_communication_id': game.np_communication_id})

    response = client.get(url + '?view=leaderboard')

    assert response.status_code == 200
    assert 'gd-view-leaderboard' in response.content.decode()


def test_retired_players_modal_is_gone():
    """The orphaned modal + its 223 lines of dead JS were removed; nothing should reference them."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert not (root / 'static/js/game-players-modal.js').exists()
    assert not (root / 'templates/trophies/partials/game_detail/game_players_modal.html').exists()
    assert not (root / 'templates/trophies/partials/game_detail/game_detail_header.html').exists()
    assert 'game-players-modal' not in (root / 'templates/trophies/game_detail.html').read_text(encoding='utf-8')
