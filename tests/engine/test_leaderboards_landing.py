"""Global Boards -- the rebuilt `/leaderboards/` landing (step 4).

Three boards as tabs, country as a filter across all of them, and the viewer's own standing shown ONCE in
the header rather than per row. That last one is not a layout preference: a row identical for every
viewer is what makes the whole page cacheable, and a personal rank in the wall would forfeit it.

See docs/design/rebuild/leaderboards-rebuild.md.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from trophies.models import ProfileBadgeStanding, ProfileCareerStanding
from tests.factories import ProfileFactory
from tests.engine.test_leaderboards_overall_cost import active_board

pytestmark = pytest.mark.django_db

URL = reverse('overall_badge_leaderboards')


def _ranked(name, *, country='', country_name='', plats=0, trophies=0, points=0, career=0, level=0):
    """A hunter placed on whichever boards the caller gives them figures for.

    `plats`/`trophies` land on PROFILE's own counters, because the Trophies board reads those directly --
    it is not badge-scoped and has no standing row. `points` still needs a ProfileBadgeStanding.
    """
    p = ProfileFactory(
        display_psn_username=name, country_code=country, country=country_name,
        is_linked=True, total_plats=plats, total_trophies=trophies,
    )
    if points:
        ProfileBadgeStanding.objects.create(profile=p, country_code=country, total_xp=points, is_linked=True)
    if career:
        ProfileCareerStanding.objects.create(
            profile=p, country_code=country, total_xp=career, pursuer_level=level, is_linked=True)
    return p


def test_the_landing_offers_three_boards_and_defaults_to_trophies(client):
    """Trophies leads because it is the board with the most entrants -- every linked hunter with a trophy
    is on it, which is the one a first-time visitor is most likely to appear on.

    It has been renamed twice: "Progress" (which named the STORE rather than what it ranks), then "Badge
    Trophies" (badge-scoped, and the only thing in the subsystem needing a full-library aggregate). The
    label is asserted here as well as the key, because the two are separately changeable and a rename
    landing in only one of them is the likely half-done state.
    """
    body = client.get(URL).content.decode()

    for key in ('trophies', 'points', 'career'):
        assert f'data-board="{key}"' in body, f'the {key} board is missing from the tab strip'
    assert active_board(body) == 'trophies', 'the landing does not default to Trophies'
    assert '>Trophies</span>' in body, 'the board is still labelled something else in the strip'


def test_country_is_a_filter_not_a_tab(client):
    """The decision the section rests on. There must be no country TAB, and the filter must apply to
    whichever board is open -- a board per country is what made slicing unaffordable before."""
    _ranked('CanadaTop', country='CA', country_name='Canada', plats=9, trophies=90, points=500)
    _ranked('BritTop', country='GB', country_name='Britain', plats=99, trophies=999, points=5000)

    body = client.get(URL).content.decode()
    assert 'data-board="country"' not in body, 'country is back as a tab'
    assert 'name="country"' in body, 'the country filter is missing'

    sliced = client.get(URL, {'tab': 'trophies', 'country': 'CA'}).content.decode()
    assert 'CanadaTop' in sliced and 'BritTop' not in sliced


def test_switching_country_keeps_you_on_the_board_you_were_reading(client):
    """The filter form carries `tab` as a hidden field. Without it, changing country would silently throw
    the reader back to the default board -- a filter that moves you is worse than no filter."""
    _ranked('Someone', country='CA', country_name='Canada', career=900, level=12)
    body = client.get(URL, {'tab': 'career'}).content.decode()
    assert '<input type="hidden" name="tab" value="career">' in body


def test_the_viewer_standing_rides_the_tab_strip_not_the_rows(client):
    """Shown once, and now IN THE TAB STRIP. The pills and the strip were two controls stacked -- both
    navigated between boards, and one also carried your rank -- so the rank folded into the chip and your
    standing stopped being a block you scroll past.

    What has not changed is the rule underneath: a per-row personal rank would make every response
    per-user and forfeit caching for the wall, which is its defining performance property. The viewer's
    own row IS marked, but in the browser (see the client-side marker test below), never rendered in.
    """
    # `_ranked` puts them on the DEFAULT board too. Seeding only a badge standing left the Trophies tab
    # empty (it reads Profile's own counters, which were 0), so there was no wall to slice.
    profile = _ranked('Me', plats=3, trophies=30, points=100)
    client.force_login(profile.user)

    body = client.get(URL).content.decode()
    assert 'lb-chiprank' in body, 'the tab chips do not carry the viewer rank'
    # Sliced FROM the switch: the site navbar closes a `</nav>` earlier in the document, so searching
    # the whole body for the close finds that one and yields a backwards (empty) slice.
    start = body.index('<nav class="pp-switch"')
    strip = body[start:body.index('</nav>', start)]
    assert 'lb-chiprank' in strip, 'the rank is not in the tab strip'
    # The wall's rows must stay identical for everyone -- no per-viewer marker rendered into one.
    rows = body[body.index('<ol class="lb-wall'):]
    assert 'is-you' not in rows


def test_an_anonymous_visitor_gets_the_boards_without_a_standing_strip(client):
    _ranked('Public', plats=1, trophies=10, points=50)
    body = client.get(URL).content.decode()
    assert 'Public' in body, 'the boards are not public'
    assert 'lb-chiprank' not in body, 'a rank rendered on the tabs for an anonymous visitor'


def test_a_board_page_is_a_constant_number_of_queries(client):
    """Two reads per board (the board + one hydrate) plus fixed request overhead. The failure this guards
    is per-row hydration, which is invisible at test scale and quadratic in production."""
    for i in range(3):
        _ranked(f'Few{i}', plats=i, trophies=i * 10, points=i * 100)
    # Warm the picker caches first. They are viewer-independent and cached for an hour, so a COLD request
    # legitimately costs more than a warm one -- measuring one of each would compare cold-start against
    # steady state, when the property this guards is per-ROW cost.
    client.get(URL, {'tab': 'trophies'})
    with CaptureQueriesContext(connection) as small:
        client.get(URL, {'tab': 'trophies'})

    for i in range(20):
        _ranked(f'Many{i}', plats=i, trophies=i * 10, points=i * 100)
    client.get(URL, {'tab': 'trophies'})          # re-warm: the new rows may add a country key
    with CaptureQueriesContext(connection) as large:
        client.get(URL, {'tab': 'trophies'})

    assert len(large.captured_queries) == len(small.captured_queries), (
        f'{len(small.captured_queries)} queries for 3 rows but {len(large.captured_queries)} for 23'
    )


def test_the_board_card_tally_counts_the_board_it_sits_above(client):
    """The header figure and the pager's total are the same number, read once from `board_count`.

    It used to be `ProfileBadgeStanding.objects.count()` regardless of tab, country or the board's own
    `> 0` membership rule. On `?tab=career` that printed the badge population directly above the career
    wall -- two totals for one board, on one screen, differing by whatever the ratio happened to be.
    """
    # Two hunters on the badge boards, one on Career.
    _ranked('BadgeOne', plats=1, trophies=10, points=50)
    _ranked('BadgeTwo', plats=2, trophies=20, points=90)
    _ranked('CareerOnly', career=900, level=7)

    for tab, expected in (('career', 1), ('trophies', 2), ('points', 2)):
        resp = client.get(URL, {'tab': tab})
        assert resp.context['ranked_total'] == expected, (
            f'?tab={tab} header counted {resp.context["ranked_total"]}, board holds {expected}'
        )
        # The header figure and the pager's total are the SAME value, not two reads that happen to match.
        assert resp.context['ranked_total'] == resp.context['board'].paginator.count, (
            f'?tab={tab}: the header and the pager are counting different populations'
        )
        # The figure lives in the BOARD CARD now -- it counts this board, and the page header is
        # section identity that does not change between tabs.
        assert f'>{expected}</span>' in resp.content.decode(), 'the figure did not reach the board card'


def test_the_board_card_tally_follows_the_country_slice(client):
    """A slice changes the population, so it has to change the figure describing it."""
    _ranked('CanadaOne', country='CA', country_name='Canada', plats=1, trophies=10, points=50)
    _ranked('CanadaTwo', country='CA', country_name='Canada', plats=2, trophies=20, points=60)
    _ranked('Brit', country='GB', country_name='Britain', plats=9, trophies=90, points=900)

    everywhere = client.get(URL, {'tab': 'trophies'}).content.decode()
    assert '>3</span>' in everywhere

    sliced = client.get(URL, {'tab': 'trophies', 'country': 'CA'}).content.decode()
    assert '>2</span>' in sliced, 'the board card kept the global figure over a sliced wall'


def test_the_empty_board_says_which_kind_of_empty_it_is(client):
    """"No hunters from this country" and "nobody has scored yet" are different problems with different
    next actions, and a single generic empty state would answer neither."""
    _ranked('Elsewhere', country='GB', country_name='Britain', plats=5, trophies=50)

    everywhere = client.get(URL, {'tab': 'career'}).content.decode()
    assert 'still empty' in everywhere

    sliced = client.get(URL, {'tab': 'trophies', 'country': 'GB'}).content.decode()
    assert 'Elsewhere' in sliced   # sanity: GB has someone on the progress board


def test_the_retired_series_tab_lands_on_a_board(client):
    """`?tab=series` was a DIRECTORY, out of the tab strip, held open as a placeholder for
    `/leaderboards/badges/`. That page was built and then removed, and the placeholder outlived it while
    reading the RETIRED tier-era `Badge` model -- a frozen catalogue beside live counts.

    A stale bookmark maps to the default board rather than 404ing, the same courtesy the other retired tab
    keys (`xp`, `country`, `progress`) get.
    """
    resp = client.get(URL, {'tab': 'series'})

    assert resp.status_code == 200
    assert resp.context['active_tab'] == 'trophies'

def test_a_career_only_hunter_makes_their_country_selectable(client):
    """The two economies are sealed apart, so a hunter can hold Career XP and no badge standing at all.
    Reading the picker from ProfileBadgeStanding alone left their country missing -- unselectable on the
    very board they appear on. Caught by a test whose real subject was something else entirely."""
    _ranked('CareerOnly', country='JP', country_name='Japan', career=1200, level=15)

    body = client.get(URL, {'tab': 'career'}).content.decode()
    assert 'value="JP"' in body, 'a career-only hunter\'s country is missing from the picker'

    sliced = client.get(URL, {'tab': 'career', 'country': 'JP'}).content.decode()
    assert 'CareerOnly' in sliced


@pytest.mark.parametrize('page', ['abc', '', '-5', '99999', '1e5', '٣'])
def test_a_malformed_page_param_does_not_500(client, page):
    """This board's paginator is hand-rolled rather than Django's, so an unparseable `?page` raised
    ValueError straight out of the view -- a 500 for a typo'd URL, on a public page. The sibling series
    wall in the same file already guarded this; the boards did not.

    Clamped to page 1 rather than 404'd: the board is still there, and dropping a reader out of it over a
    malformed query param is hostile.
    """
    _ranked('Someone', plats=2, trophies=30, points=100)
    resp = client.get(URL, {'tab': 'trophies', 'page': page})
    assert resp.status_code == 200, f'?page={page!r} returned {resp.status_code}'
    assert 'Someone' in resp.content.decode(), f'?page={page!r} emptied the board'


# ------------------------------------------------------------------ the rows endpoint --------------------

def test_the_rows_endpoint_serves_a_window_of_bare_rows(client):
    """The server half of the virtualized wall. It returns `.lb-row` elements and NOTHING else -- the
    engine splices them into its own spacer, so any wall or chrome around them would be parsed and
    discarded."""
    for i in range(8):
        _ranked(f'H{i:02d}', plats=100 - i, trophies=500)

    body = client.get(reverse('leaderboard_rows'), {'tab': 'trophies', 'range': 3, 'count': 2}).content.decode()

    assert body.count('<li class="lb-row') == 2, 'the window is the wrong size'
    assert '<ol' not in body and 'lb-wall' not in body, 'the fragment carries chrome the engine would discard'
    # Numbered by SLOT from the requested start, so the rows the engine mounts at positions 3 and 4 are
    # labelled 3 and 4 -- the rank/position invariant, across the seam between windows.
    assert 'data-lb-rank="3"' in body and 'data-lb-rank="4"' in body
    assert 'data-lb-rank="1"' not in body


def test_a_window_reads_the_same_board_as_the_page(client):
    """`board_window` is shared with the page deliberately. A rows endpoint that re-derived the tab or the
    figure labels would be a second definition of the board, and the labels are the thing that must not
    differ between the screenful a reader arrives on and the rest of it."""
    for i in range(3):
        _ranked(f'P{i}', points=900 - i)

    first = client.get(URL, {'tab': 'points'}).content.decode()
    window = client.get(reverse('leaderboard_rows'), {'tab': 'points', 'range': 1, 'count': 3}).content.decode()

    assert 'points' in first and 'points' in window
    assert 'badges' in window, 'the supporting figure label differs from the page'


def test_the_window_honours_the_country_slice(client):
    """A window that ignored a filter the first window applied would return different hunters mid-scroll."""
    _ranked('Local', plats=50, trophies=100, country='GB')
    _ranked('Abroad', plats=90, trophies=200, country='US')

    body = client.get(reverse('leaderboard_rows'),
                      {'tab': 'trophies', 'country': 'GB', 'range': 1, 'count': 50}).content.decode()

    assert 'Local' in body and 'Abroad' not in body


def test_a_crafted_window_cannot_ask_for_the_whole_board(client):
    """`range` is an OFFSET straight into the board and `count` a LIMIT, so both are clamped: an unbounded
    range is a nine-figure OFFSET Postgres honours by walking every skipped row."""
    from trophies.views.badge_views import LeaderboardRowsView

    _ranked('Someone', plats=5, trophies=10)

    huge = client.get(reverse('leaderboard_rows'), {'tab': 'trophies', 'count': 100000})
    assert huge.status_code == 200
    assert huge.content.decode().count('<li class="lb-row') <= LeaderboardRowsView.MAX_COUNT

    for raw in ('abc', '-5', ''):
        resp = client.get(reverse('leaderboard_rows'), {'tab': 'trophies', 'range': raw, 'count': raw})
        assert resp.status_code == 200, f'range={raw!r} was not handled'


def test_the_endpoint_is_public(client):
    """The rows are identical for every viewer -- that is what makes them cacheable, and why the viewer's
    own rank lives in the page header rather than in a row."""
    _ranked('Public', plats=5, trophies=10)
    assert client.get(reverse('leaderboard_rows'), {'tab': 'trophies'}).status_code == 200


# ------------------------------------------------------------------ the virtualized wall -----------------

def test_the_wall_ships_its_first_window_and_the_engine_contract(client):
    """The board is there on arrival and on a no-JS read: the first window is server-rendered INSIDE the
    spacer, and the engine adopts those rows rather than re-fetching them.

    Everything the client needs rides on the root as data. A page size or rows URL hardcoded in the JS is
    the kind of thing that silently desyncs from the server that pages by it.
    """
    for i in range(4):
        _ranked(f'H{i}', plats=100 - i, trophies=500)

    resp = client.get(URL)
    body = resp.content.decode()

    assert 'lb-wall--virtual' in body, 'the wall is not virtualized'
    assert 'data-lb-total=' in body and 'data-lb-rows-url=' in body
    assert f'data-lb-page-size="{resp.context["page_size"]}"' in body
    assert '<li class="lb-row' in body, 'the first window was not server-rendered'
    assert 'data-lb-rank=' in body, 'rows carry no canonical rank for the engine to seed from'


def test_the_pager_is_gone(client):
    """Pagination and a spacer are two answers to the same question. Keeping both would leave a control
    that moves the reader somewhere the scrollbar says they already are."""
    _ranked('Someone', plats=5, trophies=10)
    body = client.get(URL).content.decode()

    assert 'leaderboard_pager' not in body
    assert 'rel="next"' not in body and 'rel="prev"' not in body


def test_the_column_header_labels_match_the_rows_beneath_it(client):
    """One definition of each board's figures (`FIGURES`), read by the header, the first window and every
    window the rows endpoint serves. A header naming one thing over rows naming another is the drift a
    separate rows endpoint invites."""
    from trophies.views.badge_views import OverallBadgeLeaderboardsView as V

    _ranked('P', points=900)
    resp = client.get(URL, {'tab': 'points'})

    primary, secondary = V.FIGURES['points']
    assert resp.context['primary_label'] == primary
    assert resp.context['secondary_label'] == secondary
    body = resp.content.decode()
    assert 'lb-colhead' in body and primary in body and secondary in body


def test_jump_to_my_rank_appears_only_for_a_ranked_viewer(client):
    """It reuses the rank the header already computed for the standing strip, so it costs nothing -- but
    an unranked viewer has nowhere to jump to, and a control that cannot act is worse than no control."""
    me = _ranked('Me', plats=9, trophies=90)
    _ranked('Other', plats=50, trophies=500)

    # Matched on the rendered BUTTON, not the bare attribute: the page's own JS selects `[data-lb-jump]`,
    # so an attribute search finds the script and passes on correct code.
    anon = client.get(URL).content.decode()
    assert 'class="lb-jump"' not in anon, 'a signed-out visitor was offered a jump to their own rank'

    client.force_login(me.user)
    body = client.get(URL).content.decode()
    assert 'class="lb-jump"' in body
    assert client.get(URL).context['my_rank'] == 2


def test_the_rank_box_is_bounded_by_the_board(client):
    """Typing a rank past the end should not be offered as a destination."""
    for i in range(3):
        _ranked(f'H{i}', plats=10 - i, trophies=50)

    resp = client.get(URL)
    body = resp.content.decode()

    assert 'class="lb-goto"' in body
    assert f'max="{resp.context["ranked_total"]}"' in body


def test_the_swap_region_wraps_everything_that_moves_with_the_slice(client):
    """A tab or filter change replaces one region rather than syncing pieces, because everything in it
    moves together: the tally, the standing chips and their labels, the lit tab, the selected filters,
    whether the edition control renders at all, and the board."""
    _ranked('Someone', plats=5, trophies=10, country='GB')
    body = client.get(URL).content.decode()

    region = body[body.index('<div data-lb-page>'):body.index('<!-- /lb-page -->')]
    assert 'data-lb-board' in region, 'the board is outside the swap region'
    assert 'pp-switch' in region, 'the tab strip is outside the swap region'
    assert 'data-filter-form' in region, 'the filters are outside the swap region'


def test_the_board_slides_on_a_tab_change(client):
    """Every other segmented switcher on the site uses the shared `slideViewIn` -- game detail, career,
    profile detail, badge list, titles and more. A tab that swaps instantly reads as a jump on a page
    where everything else glides.

    The BOARD slides, not the whole swap region: the tab strip and the header card are chrome and hold
    still, exactly as game detail's hero does while its panel moves.
    """
    _ranked('Someone', plats=5, trophies=10)
    body = client.get(URL).content.decode()

    assert 'PlatPursuit.slideViewIn' in body, 'the tab swap has no directional slide'
    assert 'tabOrder()' in body, 'the slide has no order, so it cannot pick a direction'


def test_the_virtual_wall_is_not_given_a_stagger_reveal(client):
    """The bug beta caught, pinned at the template level.

    `staggerReveal` puts `.pp-reveal` on a wall permanently, and `.pp-reveal .lb-row` is `opacity: 0`
    until a row earns `.is-revealed`. It reveals the batch present when it runs and then only rows handed
    to its observer -- and a virtualized wall mounts rows continuously, so every row past the first
    screenful arrived INVISIBLE. The board looked frozen on first load and fine after a tab swap, because
    the swap replaces the wall and the one-shot boot never ran on the new one.

    The engine now strips `.pp-reveal` defensively too, so re-adding this would be survivable -- but a
    reveal on a virtual wall is motion fighting motion either way, and it should not come back.
    """
    _ranked('Someone', plats=5, trophies=10)
    body = client.get(URL).content.decode()

    boot = body[body.index('<div data-lb-page>'):]
    assert 'PlatPursuit.staggerReveal' not in boot, (
        'the virtualized wall has a stagger reveal again -- rows mounted on scroll will be invisible'
    )


def test_the_viewer_row_is_marked_client_side_not_rendered_in(client):
    """You asked for your own row to be obvious while scrolling, and the constraint worth navigating is
    that the rows are byte-identical for every reader -- which is what keeps them cacheable, and why the
    design puts the standing in the header rather than in a row.

    Marking it in the BROWSER gives both: the engine knows the viewer's rank, so it tags that row on
    mount. Game detail renders its `--you` modifier server-side and pays the cost this avoids.
    """
    me = _ranked('Me', plats=9, trophies=90)
    _ranked('Other', plats=50, trophies=500)
    client.force_login(me.user)

    body = client.get(URL).content.decode()
    rows = body[body.index('<ol class="lb-wall'):]

    assert 'is-you' not in rows, 'the viewer marker was rendered into the rows, which un-caches them'
    assert 'youRank: viewerRank()' in body, 'the engine is not told which row is the viewer'
    # ...and the rows endpoint, which serves every window after the first, must stay impersonal too.
    window = client.get(reverse('leaderboard_rows'), {'tab': 'trophies', 'range': 1, 'count': 50})
    assert 'is-you' not in window.content.decode()


def test_the_board_card_says_what_the_board_ranks(client):
    """Which board you are on was signalled only by which chip was lit, and "Badge Points" tells a
    first-time visitor nothing about what a point is. The card is the one place on the page that says
    what is being ranked -- adjacent to the wall, so the answer sits beside the thing it describes."""
    from trophies.views.badge_views import OverallBadgeLeaderboardsView as V

    _ranked('Someone', plats=5, trophies=50, points=100)

    for tab in ('trophies', 'points', 'career'):
        resp = client.get(URL, {'tab': tab})
        assert resp.context['board_meaning'] == V.MEANINGS[tab]
        assert resp.context['board_label'] == dict(V.BOARDS)[tab]

    body = client.get(URL, {'tab': 'points'}).content.decode()
    assert 'lb-boardcard' in body
    assert V.MEANINGS['points'] in body, 'the board card does not say what the board ranks'


def test_a_board_the_viewer_is_not_on_shows_a_dash_not_a_gap(client):
    """A missing rank on a board you COULD be on is information. It also keeps the strip from jittering:
    a chip that changes width depending on whether you happen to be ranked makes the tabs move as you
    switch between them."""
    me = _ranked('Me', plats=3, trophies=30)      # trophies only -- not on Career
    client.force_login(me.user)

    resp = client.get(URL)
    ranks = {b['key']: b['rank'] for b in resp.context['boards']}

    assert ranks['trophies'] == 1
    assert ranks['career'] is None, 'the fixture no longer tests an unranked board'
    assert '&mdash;' in resp.content.decode(), 'an unranked board rendered no placeholder'
