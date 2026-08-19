"""Tests for the Leaderboard tab endpoint and its wiring into the game detail page.

The endpoint serves three shapes from one URL (panel / continuation rows / jump window), and the panel
is deliberately NOT server-rendered with the page -- that laziness is the whole performance argument, so
it gets an explicit assertion rather than being left to drift.
"""
import re
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


def _linked(code=''):
    """A VERIFIED hunter, which is the only kind this board ranks.

    The gate is new: this board used to rank every scraped PSN profile and offered "members only" as an
    opt-in. `ProfileFactory` already defaults `is_linked=True` (see its own docstring), so this helper is
    about being EXPLICIT and about carrying a country -- a fixture that means "scraped" has to say
    `is_linked=False` out loud, because that is now the difference between being on the board and not.
    """
    return ProfileFactory(is_linked=True, country_code=code, country='Country' if code else '')


def _board(n, game=None):
    game = game or GameFactory()
    rows = [
        ProfileGameFactory(game=game, profile=_linked(), progress=100 - i,
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

    assert 'lb-boardcard' in body        # the shared board identity, on the shared control card
    assert 'lb-boardcard__tally' in body   # the count moved onto the shared board card
    assert 'data-lb-total="3"' in body            # the JS sizes the virtual spacer from this
    assert body.count('<li class="lb-row') >= 3
    assert 'lb-controls' in body                 # ...and the chrome is on a surface
    # The ROW LABELS, which replaced the deleted header strip -- asserted in their rendered shape so a
    # bare word appearing anywhere else on the panel cannot satisfy them.
    assert '<span class="lb-row__k">complete</span>' in body
    assert '<span class="lb-row__k">trophies</span>' in body


def test_medal_classes_key_off_rank_not_dom_position(client):
    """Virtualized rows mount in scroll order, so the top-3 medal colour must come from the rank itself
    (a class), not nth-child -- otherwise it colours whatever three rows are first in the DOM."""
    game, _ = _board(80)

    # A deep window (ranks 51..80) must carry NO medal classes...
    deep = client.get(_url(game, range=51, **{'from': 51})).content.decode()
    assert 'lb-row--podium' not in deep, 'a deep window rendered podium metal'

    # ...while the top window carries exactly ranks 1/2/3.
    top = client.get(_url(game, range=1, **{'from': 1})).content.decode()
    for place in (1, 2, 3):
        assert top.count(f'lb-row--p{place}') == 1, f'no podium marker for #{place}'


def test_panel_stamps_the_page_size_for_the_client(client):
    game, _ = _board(3)

    assert 'data-lb-page-size="50"' in client.get(_url(game)).content.decode()


def test_range_returns_rows_only_numbered_from_the_given_rank(client):
    """A virtual-window fetch: just the rows, positioned by the client via their rank."""
    game, _ = _board(80)                          # more than one PAGE_SIZE (50) window

    body = client.get(_url(game, range=51, **{'from': 51})).content.decode()

    assert 'lb-boardcard' not in body and '<ol' not in body   # rows only
    assert _ranks(body) == list(range(51, 81))    # ranks 51..80, contiguous


def test_adjacent_ranges_tile_without_gap_or_overlap(client):
    """The virtual-scroll guarantee, end to end through HTTP."""
    game, _ = _board(80)

    first = _ranks(client.get(_url(game, range=1, **{'from': 1})).content.decode())
    second = _ranks(client.get(_url(game, range=51, **{'from': 51})).content.decode())

    assert first == list(range(1, 51)) and second == list(range(51, 81))
    assert set(first).isdisjoint(second)


def test_a_window_numbers_from_where_it_starts(client):
    """A display position IS a rank. The `from` param and the +1/-1 step went with `invert`, which was the
    only reason two numberings ever existed on this board."""
    game, _ = _board(60)

    body = client.get(_url(game, range=51)).content.decode()

    assert _ranks(body) == list(range(51, 61))


def test_empty_board_shows_the_empty_state(client):
    """A game nobody's played, with filters explicitly off, gets the 'no one at all' message."""
    body = client.get(_url(GameFactory(), earners=0)).content.decode()

    assert 'No hunters yet' in body
    assert '<li class="lb-row' not in body


def test_filtered_to_empty_shows_the_relax_your_filters_hint(client):
    """The default earners-only view of a game with only 0% owners should suggest relaxing, not dead-end."""
    game = GameFactory()
    ProfileGameFactory(game=game, profile=_linked(), progress=0, most_recent_trophy_date=None)

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
    # MARKED IN THE BROWSER, not rendered in. The `--you` modifier and the "You" pill were server-side,
    # which made every row personal to the reader and therefore unshareable between readers; the shared
    # row is tagged from `data-lb-viewer-rank` by the engine instead. This is the same contract the other
    # three boards assert.
    assert 'data-lb-viewer-rank="3"' in body, 'the engine is not told which row is the viewer'
    assert 'is-you' not in body, 'the viewer marker was rendered into the rows, which un-caches them'


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


def test_a_linked_viewer_who_is_NOT_on_the_board_is_told_so(client):
    """The slot that answers "where am I" used to vanish for a linked hunter with no row here, which is
    indistinguishable from a feature that does not exist. It becomes a statement rather than the jump
    BUTTON, because there is no row to jump to.

    Deliberately says nothing about WHY -- no trophies yet, 0%, and hidden progress are all reachable, and
    naming the wrong reason is worse than naming none.
    """
    game, _ = _board(3)
    outsider = ProfileFactory(is_linked=True)          # owns nothing in this game
    client.force_login(outsider.user)

    body = client.get(_url(game)).content.decode()

    # No jump control, because there is no row to jump to. Game detail does not fill the board card's
    # `standing` slot the way badge and job detail do -- it is the one gap left between the four boards,
    # and it is recorded here rather than asserted as though it were covered.
    assert 'data-lb-jump' not in body, 'a jump control was offered with no row to jump to'
    assert 'data-lb-viewer-rank=""' in body, 'the engine was handed a rank for someone not on the board'


def test_an_anonymous_visitor_is_told_nothing_about_a_standing_they_cannot_have(client):
    """The distinction the branch exists for: silence, not "not on this board"."""
    game, _ = _board(3)

    body = client.get(_url(game)).content.decode()

    assert 'Not on this board yet' not in body and 'data-lb-jump' not in body


def test_anonymous_viewer_gets_no_rank_control(client):
    game, _ = _board(3)

    body = client.get(_url(game)).content.decode()

    assert 'gd-lb__row--you' not in body
    assert 'data-lb-jump' not in body


def test_progress_rows_carry_completion_and_the_trophy_count(client):
    """The reduced row: ONE headline figure, one supporting figure, one date.

    It used to carry per-tier trophy dots (plat/gold/silver/bronze) and a completion bar as well. Those
    are gone with the move onto the shared `.lb-row`, which has three figure slots and is the row every
    other board renders -- being the same board mattered more than the extra columns. The tier BREAKDOWN
    is the real loss and is stated here so it is a decision on the record rather than a silent trim; the
    total survives as the supporting figure.
    """
    game = GameFactory()
    ProfileGameFactory(game=game, profile=_linked(), progress=100,
                       most_recent_trophy_date=timezone.now(),
                       earned_trophies={'bronze': 30, 'silver': 8, 'gold': 3, 'platinum': 1})

    resp = client.get(_url(game))
    row = resp.context['rows'][0]
    body = resp.content.decode()

    assert row['primary'] == '100%' and row['primary_label'] == 'complete'
    assert row['secondary'] == 42 and row['secondary_label'] == 'trophies'   # 30 + 8 + 3 + 1
    assert row['when'] is not None
    # The dropped treatments must not come back piecemeal.
    assert 'gd-gcount' not in body, 'the per-tier dots are back on a row that has no slot for them'
    assert 'pp-horizon' not in body, 'the completion bar is back'


def test_a_row_with_no_trophies_and_no_date_renders_cleanly(client):
    """An owner synced with the empty-dict default and no trophy date must not crash or print junk."""
    game = GameFactory()
    ProfileGameFactory(game=game, profile=_linked(), progress=0,
                       most_recent_trophy_date=None, earned_trophies={})

    resp = client.get(_url(game, earners=0))          # the earners-only default would hide them
    row = resp.context['rows'][0]

    assert row['primary'] == '0%'
    assert row['secondary'] == 0
    assert row['when'] is None
    assert resp.content.decode().count('<li class="lb-row') == 1


def test_rows_show_the_tiebreak_date(client):
    """Two players at the same progress are ranked by exact time, so the date must be visible or the
    ordering looks arbitrary.

    It used to render the TIME under the date, in a long/short pair for desktop and mobile. The shared row
    carries one date in one format, which is the trade the reduction made -- so two hunters who finished
    on the same day now read as tied when the ordering knows they are not. Recorded here rather than left
    to be rediscovered from a support question."""
    game = GameFactory()
    when = timezone.now().replace(year=2025, month=1, day=9)
    ProfileGameFactory(game=game, profile=_linked(), progress=100,
                       most_recent_trophy_date=when.replace(hour=8, minute=15))
    ProfileGameFactory(game=game, profile=_linked(), progress=100,
                       most_recent_trophy_date=when.replace(hour=20, minute=42))

    resp = client.get(_url(game))
    body = resp.content.decode()

    assert all(r['when'] is not None for r in resp.context['rows'])
    assert body.count('lb-row__when') == 2, 'the tiebreak date is not rendered on both rows'
    assert 'gd-lb__time' not in body, 'the removed time cell is back'
    # THE REDUCTION IS LARGER THAN "the time". `leaderboard_row.html` renders `|date:"M Y"` -- month and
    # year -- so this board lost the DAY as well. Two hunters who finished in the same month now render
    # an identical string, and the ordering still knows they are not tied. Asserted so the loss is on the
    # record: the old row showed a long date, a short date and a time.
    assert 'Jan 2025' in body and body.count('Jan 2025') == 2


def test_hidden_players_are_absent_from_the_endpoint(client):
    game, _ = _board(2)
    ProfileGameFactory(game=game, profile=_linked(), progress=100, user_hidden=True)

    body = client.get(_url(game)).content.decode()

    assert body.count('<li class="lb-row') == 2


# --- controls: filters, invert, jump-to-rank ---------------------------------


def test_panel_renders_the_filters_as_selects(client):
    """The controls are `.lb-filters` selects now, like every other board -- they were `aria-pressed`
    toggle chips, which is a different control for the same job.

    `Invert` is gone (it existed only here) and so is `Accounts`: the board is `is_linked`-gated
    unconditionally now, so the one rule about who is on a board is not a setting.
    """
    game, _ = _board(2)
    body = client.get(_url(game)).content.decode()

    assert 'data-filter-form' in body
    assert 'id="gdlb-earners"' in body, 'the players filter is missing'
    assert 'data-lb-opt' not in body, 'the old toggle chips are back'
    assert 'gdlb-registered' not in body, 'the accounts filter is back'
    assert 'Invert' not in body, 'the invert control is back'


def test_earners_filter_default_hides_zero_trophy_owners(client):
    game = GameFactory()
    ProfileGameFactory(game=game, profile=_linked(), progress=80,
                       most_recent_trophy_date=timezone.now())
    ProfileGameFactory(game=game, profile=_linked(), progress=0, most_recent_trophy_date=None)

    default = client.get(_url(game)).content.decode()
    show_all = client.get(_url(game, earners=0)).content.decode()

    assert default.count('<li class="lb-row') == 1                 # 0% owner hidden by default
    assert show_all.count('<li class="lb-row') == 2


def test_the_board_is_linked_gated_without_being_asked(client):
    """Every other board is `is_linked`-gated (`badge_leaderboards._linked`); this one was not. It ranked
    every scraped PSN profile and offered "members only" as an OPT-IN -- so the consistent behaviour was
    the one you had to ask for, on the one board where the gate matters most (there are roughly six times
    as many scraped profiles as claimed ones).
    """
    game = GameFactory()
    mine = ProfileGameFactory(game=game, profile=_linked(), progress=90,
                              most_recent_trophy_date=timezone.now())
    scraped = ProfileFactory(is_linked=False)
    ProfileGameFactory(game=game, profile=scraped, progress=95,
                       most_recent_trophy_date=timezone.now())

    resp = client.get(_url(game))

    assert resp.context['total'] == 1, 'an unverified profile is on the board'
    assert [r['profile_id'] for r in resp.context['rows']] == [mine.profile_id]


def test_the_board_can_be_sliced_by_country(client):
    """The slice every other board offers. `ProfileGame` carries no `country_code` mirror -- the standing
    stores denormalize it so they never join Profile, which is not available for a per-(profile, game)
    table -- so it rides the `is_linked` join this board now makes anyway."""
    game = GameFactory()
    brit = ProfileGameFactory(game=game, profile=_linked(code='GB'), progress=80,
                              most_recent_trophy_date=timezone.now())
    ProfileGameFactory(game=game, profile=_linked(code='US'), progress=90,
                       most_recent_trophy_date=timezone.now())

    whole = client.get(_url(game))
    assert whole.context['total'] == 2
    assert {c['code'] for c in whole.context['countries']} == {'GB', 'US'}

    sliced = client.get(_url(game, country='GB'))
    assert sliced.context['total'] == 1
    assert [r['profile_id'] for r in sliced.context['rows']] == [brit.profile_id]
    # ...and the slice rides every later window, or row 51 answers a different question from row 50.
    assert 'country=GB' in sliced.content.decode()


def test_an_unknown_country_falls_back_to_the_whole_board(client):
    game = GameFactory()
    ProfileGameFactory(game=game, profile=_linked(code='GB'), progress=80,
                       most_recent_trophy_date=timezone.now())

    for raw in ('ZZ', 'nonsense', ''):
        resp = client.get(_url(game, country=raw))
        assert resp.status_code == 200, f'country={raw!r} was not handled'
        assert resp.context['total'] == 1, f'country={raw!r} emptied the board'


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
    # Both are verified hunters -- the board is gated on that now, so an unlinked profile would simply be
    # absent rather than filtered. What separates them here is COUNTRY, which is the slice under test.
    ProfileGameFactory(game=game, profile=ProfileFactory(psn_username='HunterA', is_linked=True),
                       progress=95, most_recent_trophy_date=timezone.now())
    ProfileGameFactory(game=game,
                       profile=ProfileFactory(psn_username='HunterB', is_linked=True,
                                              country_code='GB', country='United Kingdom'),
                       progress=80, most_recent_trophy_date=timezone.now())

    both = json.loads(client.get(_url(game, suggest='hunter')).content)
    sliced = json.loads(client.get(_url(game, suggest='hunter', country='GB')).content)

    assert {p['username'] for p in both['players']} == {'huntera', 'hunterb'}
    # The typeahead searches the BOARD, so it has to see the same population the rows do -- a suggestion
    # that jumps to a rank the board does not contain is worse than no suggestion.
    assert {p['username'] for p in sliced['players']} == {'hunterb'}


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


def test_at_past_the_board_returns_nothing(client):
    import json
    game, _ = _board(3)

    data = json.loads(client.get(_url(game, at=99)).content)

    assert data['players'] == []


def test_at_respects_the_active_filters(client):
    import json
    game = GameFactory()
    ProfileGameFactory(game=game,
                       profile=ProfileFactory(psn_username='Member', is_linked=True,
                                              country_code='GB', country='United Kingdom'),
                       progress=100, most_recent_trophy_date=timezone.now() - timedelta(minutes=5))
    ProfileGameFactory(game=game, profile=ProfileFactory(psn_username='Guest', is_linked=True),
                       progress=90, most_recent_trophy_date=timezone.now())

    everyone = json.loads(client.get(_url(game, at=2)).content)
    sliced = json.loads(client.get(_url(game, at=2, country='GB')).content)

    assert everyone['players'][0]['username'] == 'guest'
    # Rank 2 of the SLICED board, which has only one hunter on it -- the preview must count the same
    # population as the board, or it previews somebody the reader will never scroll past.
    assert sliced['players'] == []


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
    assert '<li class="lb-row' not in body                # but no rows shipped with the page
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


def _ptg_row(game, gid, progress, minutes_ago, completion_seconds=None, username=None, country=''):
    group, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id=gid, defaults={'defined_trophies': {}})
    kw = {'is_linked': True, 'country_code': country, 'country': 'Country' if country else ''}
    profile = ProfileFactory(psn_username=username, **kw) if username else ProfileFactory(**kw)
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

    assert body.count('<li class="lb-row') == 2                     # the DLC group's population, not the overall 3
    assert 'data-lb-total="2"' in body


def test_board_param_routes_to_the_speed_board(client):
    game = GameFactory()
    _ptg_row(game, 'default', progress=100, minutes_ago=10, completion_seconds=3600)
    _ptg_row(game, 'default', progress=100, minutes_ago=10, completion_seconds=7200)
    _ptg_row(game, 'default', progress=60, minutes_ago=5, completion_seconds=None)   # not complete -> off speed board

    body = client.get(_url(game, board='speed:default')).content.decode()

    assert body.count('data-lb-rank=') == 2                  # only the two completers (row--speed doubles a gd-lb__row count)
    # Asserted as ROW LABELS, not bare substrings: 'finished' also appears in the board card's meaning
    # line ("...for everyone who finished."), which renders whether or not a single row carries the label.
    assert '<span class="lb-row__k">elapsed</span>' in body
    assert '<span class="lb-row__k">finished</span>' in body
    # The started -> finished WINDOW is gone with the reduction: the shared row carries one date, so the
    # finish (which is also this board's tiebreak) is the one that stays. The elapsed figure is the
    # headline it always was.
    assert 'gd-lb__col--start' not in body and 'gd-lb__coltime' not in body
    assert 'data-lb-total="2"' in body


def test_board_param_routes_to_the_playtime_board(client):
    game = GameFactory()
    ProfileGameFactory(game=game, profile=_linked(), play_duration=timedelta(hours=50),
                       progress=87, last_played_date_time=timezone.now())
    ProfileGameFactory(game=game, profile=_linked(), play_duration=None)       # no reported time -> excluded

    body = client.get(_url(game, board='playtime')).content.decode()

    assert body.count('data-lb-rank=') == 1
    assert '<span class="lb-row__k">played</span>' in body
    assert '<span class="lb-row__k">last played</span>' in body
    assert '50h' in body                                     # the compact play-time label
    assert '87%' in body                                     # overall progress column
    # Play time leads, completion supports it, last-played is the date. The same three slots every other
    # board fills -- this one used its own column classes for them.
    assert 'gd-lb__col--prog' not in body and 'gd-lb__colhead' not in body
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
    ProfileGameFactory(game=game, profile=_linked(), play_duration=timedelta(hours=5))
    ProfileTrophyGroup.objects.create(profile=ProfileFactory(), trophy_group=dlc, progress=100,
                                      last_trophy_at=timezone.now())

    body = client.get(_url(game)).content.decode()

    assert 'data-lb-boards' in body                          # the switcher rendered
    assert 'data-lb-boardparam="progress:all"' in body            # Standings (Everything)
    assert 'data-lb-boardparam="speed:001"' in body               # Fastest, group row for the DLC
    assert 'data-lb-boardparam="playtime"' in body                # Most Played
    assert 'DLC One' in body


def test_dlc_groups_collapse_into_a_dropdown(client):
    game, _ = _board(2)
    TrophyGroup.objects.create(game=game, trophy_group_id='default', defined_trophies={})
    TrophyGroup.objects.create(game=game, trophy_group_id='001', trophy_group_name='First DLC', defined_trophies={})
    TrophyGroup.objects.create(game=game, trophy_group_id='002', trophy_group_name='Second DLC', defined_trophies={})

    body = client.get(_url(game)).content.decode()

    assert 'data-lb-drop' in body and 'data-lb-dropmenu' in body       # the dropdown + its menu
    assert 'data-lb-boardparam="progress:001"' in body and 'First DLC' in body      # both DLCs are menu items
    assert 'data-lb-boardparam="progress:002"' in body and 'Second DLC' in body
    assert 'data-lb-boardparam="progress:default"' in body                  # Base Game stays a pill
    assert 'data-lb-boardparam="progress:all"' in body                      # Everything stays a pill
    assert '<span>DLC</span>' in body                                  # no DLC active -> button reads "DLC"


def test_dropdown_button_shows_and_marks_the_active_dlc(client):
    game, _ = _board(2)
    TrophyGroup.objects.create(game=game, trophy_group_id='default', defined_trophies={})
    TrophyGroup.objects.create(game=game, trophy_group_id='001', trophy_group_name='Blood and Wine', defined_trophies={})

    body = client.get(_url(game, board='progress:001')).content.decode()

    assert '<span>Blood and Wine</span>' in body                       # button carries the active DLC name
    assert 'gd-lb__dropbtn' in body and 'aria-pressed="true"' in body  # and reads as active


def test_the_players_filter_is_hidden_on_non_progress_boards(client):
    """"Started" only means something on a progress board -- speed rows are all 100% complete by
    definition and play time is not a completion metric. It is the only filter left that is board-kind
    specific; country applies everywhere.
    """
    game = GameFactory()
    _ptg_row(game, 'default', progress=100, minutes_ago=10, completion_seconds=3600, country='GB')

    body = client.get(_url(game, board='speed:default')).content.decode()

    assert 'id="gdlb-earners"' not in body, 'the players filter is offered on a board it cannot affect'
    # The form must still carry a REAL control, not just the hidden `board` input -- the fixture gives a
    # hunter a country so the country select renders. Asserting the form alone passed on an empty shell.
    assert 'id="gdlb-country"' in body, 'the filter form survived with nothing in it'


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

    assert 'data-lb-boardparam="progress:default"' in body        # active board is base-game standings
    assert 'Base Game Standings' in body                     # the header title reflects it
    assert 'data-lb-boardparam="progress:default" aria-pressed="true"' in body   # Base Game chip is the active one


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


def test_the_viewer_rank_points_at_the_viewers_own_row(client):
    """The highlight is applied in the browser: the engine reads `data-lb-viewer-rank` and tags the row
    whose `data-lb-rank` matches. So the two numbers have to be produced by the same ordering, and they
    are produced by DIFFERENT code -- the rows are numbered by SLOT (`page()` counts `offset + i + 1`)
    and the viewer's rank is computed by COUNTING everyone ahead of them (`rank_for`).

    They agree only while the board's ordering is total and both reads share one population. This board
    just gained an `is_linked` gate and a country slice, either of which applied to one read and not the
    other would put the highlight on somebody else's row -- silently, because both numbers still look
    perfectly reasonable.
    """
    game, rows = _board(5)
    # An UNVERIFIED profile ranked AHEAD of the viewer. Without it this test could not fail: the gate and
    # the slice both applied to a population that had nothing for them to remove, so dropping either from
    # one read still left both numbers agreeing. It has to be able to shift them apart.
    ProfileGameFactory(game=game, profile=ProfileFactory(is_linked=False), progress=99,
                       most_recent_trophy_date=timezone.now() - timedelta(minutes=1))
    me = rows[2]
    client.force_login(me.profile.user)

    body = client.get(_url(game)).content.decode()

    viewer_rank = re.search(r'data-lb-viewer-rank="(\d+)"', body)
    assert viewer_rank, 'the engine is never told which row is the viewer'

    # The row that actually carries the viewer's name, and the rank the engine was handed, must be one.
    name = me.profile.display_psn_username or me.profile.psn_username
    li = body.rindex('<li class="lb-row', 0, body.index(name))
    row_rank = re.search(r'data-lb-rank="(\d+)"', body[li:li + 400])
    assert row_rank, 'the viewer row carries no rank for the engine to match'
    assert row_rank.group(1) == viewer_rank.group(1), (
        f'the highlight would land on rank {viewer_rank.group(1)} but the viewer is row '
        f'{row_rank.group(1)} -- the numbering and the rank read different populations'
    )


def test_only_the_board_root_claims_the_engines_marker_attribute(client):
    """REGRESSION: `data-lb-board` is the SHARED engine's marker -- `leaderboard_board.html` puts it on the
    board root and `wireBoard` finds the root by it. This page used the same name for its own "which board
    is selected" param, on the `.gd-lb` wrapper and on every switcher chip.

    The wrapper is the OUTERMOST of them, so `panel.querySelector('[data-lb-board]')` returned it instead
    of the board root. It carries no `data-lb-total`, so the engine read a board size of zero and declined
    to mount -- and every symptom of that is an ABSENCE: no viewer highlight, no jump, no infinite scroll.
    Nothing throws, and since the wall now ships as a flow list it does not even look broken. It took a
    report of "my row doesn't highlight" to find it.

    Asserted as a COUNT, because the failure is a second claimant rather than a missing one.
    """
    game, rows = _board(3)
    body = client.get(_url(game)).content.decode()

    # The marker is a BARE attribute on the board root and a VALUED one everywhere it collided, so the
    # two are distinguishable: no `data-lb-board=` may survive, and exactly one bare marker must.
    assert body.count('data-lb-board=') == 0, (
        'something other than the board root is using the engine marker as a valued attribute'
    )
    assert body.count('<div class="lb-board" data-lb-board') == 1, (
        'the board root is missing or duplicated'
    )
    # ...and this page's own param still ships, under its own name.
    assert 'data-lb-boardparam=' in body, 'the board switcher lost the param it selects with'


def test_the_hunter_search_ships_with_the_board(client):
    """The search is the one thing this board has that the others do not, and it reaches the page through
    a SLOT: `leaderboard_jumpbar.html` renders `{% include extra_partial only %}` guarded by
    `{% if extra_partial %}`, and the panel passes the path as a string.

    So dropping the kwarg, typoing the path, or losing the slot makes the search silently disappear -- no
    error, no failing test. The `?suggest=` and `?at=` ENDPOINTS are covered below, which means the suite
    would stay green with the only UI that calls them gone.
    """
    game, _ = _board(3)
    body = client.get(_url(game)).content.decode()

    assert 'data-lb-findform' in body, 'the hunter search did not reach the panel'
    assert 'data-lb-find' in body and 'data-lb-suggest' in body
    # ...and it is INSIDE the jump bar, between the two other ways in, rather than beside the cluster.
    bar = body[body.index('lb-jumpbar'):body.index('</div>', body.index('lb-goto'))]
    assert 'data-lb-find' in bar, 'the search is not in the jump bar slot'


def test_suggest_is_bounded_at_both_ends(client):
    """`?suggest=` is a public `%q%` icontains that no index serves, so a long query that matches nothing
    is a full scan of the game's population probing Profile per row for the `is_linked` gate. It had a
    2-char floor and no ceiling -- the only user-controlled param on this view that was unbounded."""
    import json

    from trophies.services.game_leaderboard_service import Board

    game, _ = _board(3)

    assert json.loads(client.get(_url(game, suggest='a')).content)['players'] == []
    huge = client.get(_url(game, suggest='x' * 5000))
    assert huge.status_code == 200
    assert json.loads(huge.content)['players'] == []
    assert Board.SUGGEST_MAX < 5000, 'the suggest ceiling no longer bounds what this asks for'


def test_an_unranked_viewer_is_told_where_they_stand(client):
    """The board card's `standing` slot. Badge and job detail fill it; game detail did not, so a linked
    hunter who owns the game but is off the filtered board saw the tally and nothing about themselves --
    and the jump bar is suppressed for them too, so the slot was the only place left to say it."""
    game, _ = _board(3)
    outsider = ProfileFactory(is_linked=True)          # owns nothing in this game
    client.force_login(outsider.user)

    body = client.get(_url(game)).content.decode()

    assert 'Not on this board yet' in body
    assert 'data-lb-jump' not in body, 'a jump control was offered with no row to jump to'


def test_the_gate_and_the_slice_apply_to_EVERY_board_kind(client):
    """`_hunters()` is shared by all four board classes, and only the standings board had a test. The
    group boards matter most: `_group_qs()` reads ProfileTrophyGroup, which carries no `is_linked` mirror,
    so the gate there is a live join a performance refactor would be tempted to drop."""
    game = GameFactory()
    _ptg_row(game, 'default', progress=100, minutes_ago=10, completion_seconds=3600)
    scraped = ProfileFactory(is_linked=False)
    ProfileTrophyGroup.objects.create(
        profile=scraped, trophy_group=TrophyGroup.objects.filter(game=game).first(),
        progress=100, completion_seconds=1, last_trophy_at=timezone.now())
    ProfileGameFactory(game=game, profile=scraped, progress=100,
                       most_recent_trophy_date=timezone.now())

    for board in ('progress:default', 'speed:default'):
        resp = client.get(_url(game, board=board))
        assert resp.context['total'] == 1, f'{board}: an unverified profile is on the board'
