"""Global Boards -- the rebuilt `/leaderboards/` landing (step 4).

FOUR boards as tabs since 2026-09 (Shovelware Free leads and is the default), country as a filter across
all of them, and the viewer's own standing shown ONCE in the header rather than per row. That last one is
not a layout preference: a row identical for every viewer is what makes the whole page cacheable, and a
personal rank in the wall would forfeit it.

See docs/design/rebuild/leaderboards-rebuild.md.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from trophies.models import ProfileBadgeStanding, ProfileCareerStanding, ProfileTrophyStanding
from tests.factories import ProfileFactory
from tests.engine.test_leaderboards_overall_cost import active_board

pytestmark = pytest.mark.django_db

URL = reverse('overall_badge_leaderboards')


def _ranked(name, *, country='', country_name='', plats=0, trophies=0, points=0, career=0, level=0,
            clean_plats=None, clean_trophies=None):
    """A hunter placed on whichever boards the caller gives them figures for.

    `plats`/`trophies` land on PROFILE's own counters, because the Trophies board reads those directly --
    it is not badge-scoped and has no standing row. `points` still needs a ProfileBadgeStanding.

    The Shovelware Free board reads its OWN store, so a hunter given trophy figures gets a matching
    `ProfileTrophyStanding` -- i.e. a library with no flagged games in it, which is the sensible default
    for a fixture that is not about shovelware. Pass `clean_plats` / `clean_trophies` to seed a hunter
    whose clean figures differ from their raw ones. Without this every fixture in this file would leave
    the DEFAULT board empty, which is what makes it worth stating: the board that leads the strip is the
    one a test forgets to populate.
    """
    p = ProfileFactory(
        display_psn_username=name, country_code=country, country=country_name,
        is_linked=True, total_plats=plats, total_trophies=trophies,
    )
    cp = plats if clean_plats is None else clean_plats
    ct = trophies if clean_trophies is None else clean_trophies
    if ct:
        ProfileTrophyStanding.objects.create(
            profile=p, clean_plats=cp, clean_trophies=ct, country_code=country, is_linked=True)
    if points:
        ProfileBadgeStanding.objects.create(profile=p, country_code=country, total_xp=points, is_linked=True)
    if career:
        ProfileCareerStanding.objects.create(
            profile=p, country_code=country, total_xp=career, pursuer_level=level, is_linked=True)
    return p


def test_the_landing_offers_four_boards_and_defaults_to_shovelware_free(client):
    """FOUR boards, and the landing opens on Shovelware Free (2026-09).

    Trophies led before it, on the grounds that it has the most entrants -- still true, since this board
    drops anyone whose whole library is flagged. Entrant count stopped being the tie-breaker: the two rank
    the same hunters by the same rule over different populations, and this one is the more honest answer
    to "who has done the most".

    The Trophies board has been renamed twice ("Progress", then "Badge Trophies"), which is why labels are
    asserted alongside keys here: the two are separately changeable and a rename landing in only one of
    them is the likely half-done state.
    """
    _ranked('Somebody', plats=3, trophies=30)
    body = client.get(URL).content.decode()

    for key in ('clean', 'trophies', 'points', 'career'):
        assert f'data-board="{key}"' in body, f'the {key} board is missing from the tab strip'
    assert active_board(body) == 'clean', 'the landing does not default to Shovelware Free'
    assert '>Shovelware Free</span>' in body, 'the board is labelled something else in the strip'
    assert '>Trophies</span>' in body, 'the Trophies board lost its tab'


def test_the_default_is_derived_from_the_strip_order_not_repeated(client):
    """Two places could name the default -- the first tab, and the fallback `?tab=` resolves to -- and
    they must not be able to disagree. `DEFAULT_BOARD` is derived from `BOARDS[0]`, so reordering the
    strip moves both at once.

    Worth pinning because the failure is quiet: an unknown `?tab=` would land on a board that is no longer
    first, so the page would render correctly with the WRONG tab lit and nothing would error.
    """
    from trophies.views.badge_views import OverallBadgeLeaderboardsView as V

    assert V.DEFAULT_BOARD == V.BOARDS[0][0], 'the default no longer follows the tab strip'
    assert V.DEFAULT_BOARD in V.BOARD_KEYS

    _ranked('Somebody', plats=3, trophies=30)
    unknown = client.get(URL, {'tab': 'not-a-board'}).content.decode()
    assert active_board(unknown) == V.DEFAULT_BOARD, 'an unknown tab does not fall back to the default'


def test_retired_tabs_land_on_the_board_they_MEANT_not_on_the_default(client):
    """`progress`, `xp`, `country` and `series` are old bookmarks. Each names a board that still exists,
    so each resolves to THAT board rather than to whatever leads the strip today.

    This became a real distinction in 2026-09, when Shovelware Free took the first slot: mapping these to
    "the default" would have silently redirected every old Trophies bookmark onto a different board with
    different numbers.
    """
    _ranked('Somebody', plats=3, trophies=30, points=100)

    for legacy, expected in (('progress', 'trophies'), ('series', 'trophies'),
                             ('xp', 'points'), ('country', 'points')):
        resp = client.get(URL, {'tab': legacy})
        # 200, not a 404: a stale bookmark should land on a board rather than an error. `series` is the
        # odd one -- it was a DIRECTORY placeholder for a page that was built and then removed, so it
        # names no board of its own and rides with the rest.
        assert resp.status_code == 200, f'?tab={legacy} 404s instead of landing somewhere'
        body = resp.content.decode()
        assert active_board(body) == expected, (
            f'?tab={legacy} landed on {active_board(body)!r}, not the {expected!r} board it named'
        )


def test_the_landing_survives_an_empty_default_board(client):
    """THE POST-DEPLOY STATE, and the reason deploy-checklist #10 exists.

    `ProfileTrophyStanding` ships empty: between migration 0332 and the backfill, the board the landing
    opens on has no rows at all. That is not a degraded corner of a page, it is the section's front door,
    so "renders an empty wall" and "500s" are very different outcomes and only one of them is survivable.

    Hunters are seeded on the OTHER boards, so this is specifically the default board being empty rather
    than an empty site -- which is exactly the shape of the deploy window.
    """
    _ranked('Elsewhere', points=500, career=900, level=9, clean_trophies=0)
    assert not ProfileTrophyStanding.objects.exists(), 'the fixture put someone on the default board'

    resp = client.get(URL)

    assert resp.status_code == 200, 'the landing 500s when its default board has no rows'
    assert resp.context['active_tab'] == 'clean'
    assert resp.context['ranked_total'] == 0
    body = resp.content.decode()
    assert 'data-board="clean"' in body, 'the tab strip did not survive the empty board'
    assert 'data-board="trophies"' in body, 'the other boards became unreachable'


def _order(body, *names):
    """The order the given hunters appear in a rendered wall."""
    return sorted(names, key=body.index)


def test_the_default_board_serves_ITS_OWN_rows_not_the_trophies_boards(client):
    """THE headline behaviour, and it had no test that could fail.

    Every fixture in this file used to give a hunter the SAME figures on both trophy boards (`_ranked`
    mirrors `plats`/`trophies` into the clean columns, and `ProfileFactory` mirrors `total_trophies` into
    `total_trophies_raw`), so no assertion could tell the two boards apart. Rewiring the `clean` tab to
    serve `lb.trophy_rows` left 129 tests green -- including the two written specifically to cover that
    path, whose docstrings say they exist because the clean board hydrates through a different one.

    So this fixture INVERTS them: a hunter who is enormous on Trophies and nearly absent from Shovelware
    Free, and one who is the reverse. The two boards must then disagree about the order, which is only
    possible if each is reading its own store.
    """
    _ranked('JunkHunter', plats=99, trophies=999, clean_plats=0, clean_trophies=1)
    _ranked('RealHunter', plats=1, trophies=10, clean_plats=50, clean_trophies=500)

    clean = client.get(URL, {'tab': 'clean'}).content.decode()
    trophies = client.get(URL, {'tab': 'trophies'}).content.decode()

    assert _order(clean, 'JunkHunter', 'RealHunter') == ['RealHunter', 'JunkHunter'], (
        'the Shovelware Free board is not ordering by its own store'
    )
    assert _order(trophies, 'JunkHunter', 'RealHunter') == ['JunkHunter', 'RealHunter'], (
        'the fixture does not actually invert the two boards, so the assertion above proves nothing'
    )
    # ...and the FIGURE each row shows comes from the same store it was ordered by, or the board would
    # sort on one number and display another.
    assert '999' not in clean.split('lb-wall')[1], 'the clean wall is showing raw trophy totals'


def test_the_rows_endpoint_serves_the_default_board(client):
    """Every rows-endpoint test in this file pinned `tab=trophies`, so the board that now serves every
    bare visit had no window coverage -- and it hydrates through a DIFFERENT path (`_store_for` returns
    `profile_id` / `profile__`, a join, where Trophies' store IS Profile)."""
    for i in range(4):
        _ranked(f'Clean{i}', plats=10 - i, trophies=100 - i)

    resp = client.get(reverse('leaderboard_rows'), {'tab': 'clean', 'range': 2})
    assert resp.status_code == 200
    assert 'lb-row' in resp.content.decode(), 'the clean board served no rows'

    # ...and they are the CLEAN board's rows. Without an inverting fixture this endpoint test passed with
    # the tab wired to `trophy_rows`, because every hunter had identical figures on both boards.
    _ranked('OnlyClean', plats=0, trophies=1, clean_plats=900, clean_trophies=9000)
    top = client.get(reverse('leaderboard_rows'), {'tab': 'clean', 'range': 1}).content.decode()
    assert 'OnlyClean' in top, 'the rows endpoint served a board this hunter does not lead'

    suggest = client.get(reverse('leaderboard_rows'), {'tab': 'clean', 'suggest': 'Clean'})
    assert suggest.status_code == 200
    players = suggest.json()['players']
    assert players and all(p['rank'] >= 1 for p in players), 'the clean board typeahead is not ranked'


def test_the_default_board_is_a_constant_number_of_queries(client):
    """Per-row hydration is invisible at test scale and quadratic in production. The existing guard covers
    the Trophies board; this one covers the board that serves every bare visit, which reads a standing
    store and therefore JOINS to Profile to hydrate names -- a different path, and the more likely one to
    grow a per-row read."""
    for i in range(3):
        _ranked(f'Few{i}', plats=i, trophies=i * 10)
    client.get(URL, {'tab': 'clean'})
    with CaptureQueriesContext(connection) as small:
        client.get(URL, {'tab': 'clean'})

    for i in range(20):
        _ranked(f'Many{i}', plats=i, trophies=i * 10)
    client.get(URL, {'tab': 'clean'})
    with CaptureQueriesContext(connection) as large:
        client.get(URL, {'tab': 'clean'})

    assert len(large.captured_queries) == len(small.captured_queries), (
        f'{len(small.captured_queries)} queries for 3 rows but {len(large.captured_queries)} for 23'
    )


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
        # The visible figure and the SPACER are the same value, not two reads that happen to match. This
        # used to compare against the paginator's count; the paginator went with the pager, and the
        # spacer inherited its job -- `data-lb-total` is what sizes the scrollbar for the whole board, so
        # a tally that disagrees with it is a board claiming one size and scrolling another.
        assert f'data-lb-total="{expected}"' in resp.content.decode(), (
            f'?tab={tab}: the tally and the virtual spacer are counting different populations'
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
    from trophies.views.board_helpers import MAX_COUNT, MAX_START

    # MORE than the ceiling, or `count=100000` returns everything there is and `<= MAX_COUNT` holds with
    # the clamp deleted. This test was vacuous on one row for as long as it existed.
    for i in range(MAX_COUNT + 20):
        _ranked(f'H{i:03d}', plats=500 - i, trophies=5000 - i)

    huge = client.get(reverse('leaderboard_rows'), {'tab': 'trophies', 'count': 100000})
    assert huge.status_code == 200
    assert huge.content.decode().count('<li class="lb-row') == MAX_COUNT

    far = client.get(reverse('leaderboard_rows'), {'tab': 'trophies', 'range': 10 ** 12})
    assert far.status_code == 200
    assert far.context['entries'] == []
    assert MAX_START < 10 ** 12, 'the start clamp no longer bounds what this asks for' 

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
    """The board is there on arrival and on a no-JS read: the first window is server-rendered inside the
    wall, and the engine adopts those rows rather than re-fetching them.

    The wall ships as FLOW, not as a spacer. `lb-wall--virtual` absolutely positions every row and the
    height reserving their space is set by the engine at mount, so shipping the class meant a board that
    never mounted rendered a zero-height pile with the page drawn through it -- which is the opposite of
    the no-JS read this test is named for. `virtualBoard` promotes the wall when it takes over.

    Everything the client needs rides on the root as data. A page size or rows URL hardcoded in the JS is
    the kind of thing that silently desyncs from the server that pages by it.
    """
    for i in range(4):
        _ranked(f'H{i}', plats=100 - i, trophies=500)

    resp = client.get(URL)
    body = resp.content.decode()

    assert '<ol class="lb-wall" data-lb-wall>' in body, 'the wall is not a plain flow list on arrival'
    assert 'lb-wall--virtual' not in body, 'the wall ships pre-virtualized and will collapse if unmounted'
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
    # Asserted on the ROWS, which is where the labels live. There was a column-header strip too, naming
    # the same figures a second time on desktop only; it is gone, and the labels it duplicated are the
    # ones that survive -- they sit in the same column as the value they describe, at every width.
    body = resp.content.decode()
    assert 'lb-colhead' not in body, 'the duplicated column header is back'
    assert body.count(f'<span class="lb-row__k">{primary}</span>') >= 1
    assert body.count(f'<span class="lb-row__k">{secondary}</span>') >= 1


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

    resp = client.get(URL)
    body = resp.content.decode()
    rows = body[body.index('<ol class="lb-wall'):]

    assert 'is-you' not in rows, 'the viewer marker was rendered into the rows, which un-caches them'
    # The MARKUP contract, not the JS. The wiring moved into `PlatPursuit.wireBoard` (utils.js), which
    # this page does not inline -- so asserting on a line of script here passed only while the script
    # happened to live in this template, and would go vacuously green if the attribute were dropped.
    #
    # Read off the context rather than hardcoded: a literal 2 would keep passing if the attribute
    # started rendering someone else's rank.
    assert resp.context['my_rank'] == 2, 'the fixture no longer puts the viewer second'
    assert 'data-lb-viewer-rank="2"' in body, 'the engine is not told which row is the viewer'
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


def test_the_chrome_is_carded_and_the_wall_is_not(client):
    """The site-wide rule: STACKED CHROME CARDS + FREE CONTENT. Chrome is carded (page header, then
    toolbar / stat cards); the content -- a grid or a list -- flows free below, never inside an outer
    card. An infinite-scroll wall is the exact case that rule protects, because a card around it is a
    border that grows forever.

    The filters, the board identity and the jump bar all sat bare on the page background, which is the
    half of the rule that was being missed.
    """
    _ranked('Someone', plats=5, trophies=50)
    body = client.get(URL).content.decode()

    assert 'lb-controls' in body, 'the chrome is not on a surface'
    controls = body[body.index('lb-controls'):body.index('<div class="lb-board"')]
    # The filters are conditional (a board with nobody ranked in any country has none to offer), so the
    # unconditional chrome is what this asserts.
    for part in ('lb-boardcard', 'lb-jumpbar'):
        assert part in controls, f'{part} is outside the control card'

    # ...and the wall is NOT inside it.
    wall_at = body.index('<ol class="lb-wall')
    assert wall_at > body.index('</section>', body.index('lb-controls')), (
        'the wall is inside the control card -- an outer card around an infinite list is the thing the '
        'rule forbids'
    )


def test_the_tab_strip_is_not_on_the_control_card(client):
    """`.pp-switch` is itself a bordered container of transparent chips, so a card under it is a bordered
    surface inside a bordered surface. Every other switcher on the site sits bare on the page for that
    reason, and this one briefly did not -- it got swept onto the card along with the filters when the
    chrome was given a surface.

    Asserted as an ORDERING (the strip closes before the card opens) rather than a substring, because
    'pp-switch' also appears in the page's script block and would pass a naive containment check.
    """
    _ranked('Someone', plats=5, trophies=50)
    body = client.get(URL).content.decode()

    strip_close = body.index('</nav>', body.index('<nav class="pp-switch"'))
    card_open = body.rindex('<section class="card', 0, body.index('lb-controls'))

    assert strip_close < card_open, (
        'the tab strip is inside the control card -- a bordered switcher on a bordered surface'
    )


# ---------------------------------------------------------------------------------------------------
# BOARD SEARCH (2026-08). Game detail had a hunter typeahead and the other three did not; it now runs on
# all four, off one service function and one JSON shape.
#
# PREFIX matching, which is what makes it affordable everywhere: `Profile.psn_username` carries a plain
# btree that serves `istartswith`, and nothing serves `icontains` on it. It is also the rule the universal
# nav search already documents, so search behaves the same wherever a reader meets it.
# ---------------------------------------------------------------------------------------------------

def test_the_board_search_finds_a_hunter_and_names_their_rank(client):
    """The rank is the whole point: a suggestion you cannot jump to is a search result on the wrong
    board. It is counted on the board being read, so it agrees with the row numbering."""
    _ranked('Aardvark', plats=10, trophies=100)
    _ranked('Zebra', plats=90, trophies=900)

    data = client.get(reverse('leaderboard_rows'), {'tab': 'trophies', 'suggest': 'aard'}).json()

    assert [p['username'] for p in data['players']] == ['Aardvark']
    assert data['players'][0]['rank'] == 2, 'the rank is not the position on this board'
    assert data['players'][0]['url'].endswith('/Aardvark/')


def test_the_board_search_is_scoped_to_the_board_being_read(client):
    """A global hunter search already lives in the navbar and answers a different question. This one has
    to be scoped, or selecting a suggestion jumps to a rank the board does not contain."""
    _ranked('Trophyist', plats=10, trophies=100)                    # trophies board only
    _ranked('Trophyless', points=500)                               # badge points board only

    trophies = client.get(reverse('leaderboard_rows'), {'tab': 'trophies', 'suggest': 'trophy'}).json()
    points = client.get(reverse('leaderboard_rows'), {'tab': 'points', 'suggest': 'trophy'}).json()

    assert {p['username'] for p in trophies['players']} == {'Trophyist'}
    assert {p['username'] for p in points['players']} == {'Trophyless'}


def test_the_board_search_respects_the_active_slice(client):
    """The country filter narrows the board, so it has to narrow the search -- and the RANK returned has
    to be the rank on the sliced board, not the whole one."""
    _ranked('Britone', country='GB', country_name='United Kingdom', plats=50, trophies=500)
    _ranked('Yankone', country='US', country_name='United States', plats=90, trophies=900)

    whole = client.get(reverse('leaderboard_rows'), {'tab': 'trophies', 'suggest': 'brit'}).json()
    sliced = client.get(reverse('leaderboard_rows'),
                        {'tab': 'trophies', 'suggest': 'brit', 'country': 'GB'}).json()

    assert whole['players'][0]['rank'] == 2
    assert sliced['players'][0]['rank'] == 1, 'the search ranked against the unsliced board'


def test_the_board_search_matches_a_PREFIX_not_a_substring(client):
    """Deliberate, and the reason this is cheap enough to put on every board: `psn_username_idx` serves
    `istartswith` and nothing serves `icontains` on Profile, so a substring search is a scan of the
    board's population per keystroke. The navbar's hunter search made the same call."""
    _ranked('Nightwing', plats=10, trophies=100)

    assert client.get(reverse('leaderboard_rows'),
                      {'tab': 'trophies', 'suggest': 'night'}).json()['players']
    assert client.get(reverse('leaderboard_rows'),
                      {'tab': 'trophies', 'suggest': 'wing'}).json()['players'] == []


def test_the_board_search_is_bounded_at_both_ends(client):
    """A public endpoint. The floor stops one letter matching most of the site; the ceiling stops a long
    query becoming a large indexed comparison."""
    from trophies.services.badge_leaderboards import SUGGEST_MAX

    _ranked('Someone', plats=10, trophies=100)

    assert client.get(reverse('leaderboard_rows'),
                      {'tab': 'trophies', 'suggest': 's'}).json()['players'] == []
    huge = client.get(reverse('leaderboard_rows'), {'tab': 'trophies', 'suggest': 'x' * 5000})
    assert huge.status_code == 200 and huge.json()['players'] == []
    assert SUGGEST_MAX < 5000, 'the ceiling no longer bounds what this asks for'


def test_the_board_search_returns_matches_in_BOARD_order(client):
    """The reader is picking a place to jump to, so the list should read in board order rather than in
    whatever order the index handed the matches back."""
    _ranked('Samone', plats=10, trophies=100)
    _ranked('Samtwo', plats=90, trophies=900)
    _ranked('Samthree', plats=50, trophies=500)

    ranks = [p['rank'] for p in client.get(reverse('leaderboard_rows'),
                                           {'tab': 'trophies', 'suggest': 'sam'}).json()['players']]
    assert ranks == sorted(ranks) == [1, 2, 3]


def test_the_board_carries_a_sticky_minibar(client):
    """The board IS the page here, so a reader ends up thousands of rows past the tab strip, the board
    card, their rank and both ways in -- with nothing above the fold but rows. The bar brings those back.

    It is a PROXY, not a second set of controls: its rank chip and its search drive the same `wireBoard`
    handle the originals do. Its attributes are `data-lb-mb-*` rather than the originals', because
    `wireBoard` finds the search field with `querySelector` (FIRST match) and a duplicate inside the same
    scope would wire one field and leave the other silently dead -- the collision class that cost a day on
    game detail.
    """
    for i in range(4):
        _ranked(f'H{i}', plats=100 - i, trophies=500)

    body = client.get(URL).content.decode()

    assert 'data-lb-minibar' in body, 'the board has no minibar'
    assert 'data-sticky-sentinel="#lb-minibar-sentinel"' in body
    assert 'id="lb-minibar-sentinel"' in body, 'the bar has nothing to reveal against'
    # Proxy controls, under their own attributes.
    assert 'data-lb-mb-rank' in body and 'data-lb-mb-find' in body
    # ...and NOT under the originals', or one of the two search fields would never be wired.
    # ...and the bar's own controls reuse NONE of the originals' attributes. Asserted over the bar's
    # markup rather than by counting the page: the in-page jump chip is conditional on being ranked, so a
    # count would pass for the wrong reason on a signed-out read.
    bar = body[body.index('data-lb-minibar'):body.index('</div>', body.index('data-lb-mb-findform'))]
    for attr in ('data-lb-find ', 'data-lb-findform', 'data-lb-suggest', 'data-lb-jump',
                 'data-lb-gotoform'):
        assert attr not in bar, f'the minibar reuses {attr}, so one of the two will never be wired'


def test_the_minibar_count_reads_the_tally_source_value_not_its_text(client):
    """REGRESSION, reported from the browser as "the minibar says 0 on every board".

    The bar's count proxies the board card's Tally, and it was copied off that element's rendered TEXT.
    `mount()` calls `boardEntrance` -- which STARTS the Tally's count-up -- and then `syncMinibar`, and
    `countUp`'s first write is the FROM value. So the text at the instant the bar reads it is "0", and
    the bar keeps it: it is synced per mount and never again, so the figure stayed 0 on every board while
    the card beside it ticked up to the real one. The header was right the whole time, which is what made
    it read as a wiring fault rather than a counting one.

    The fix reads `data-countup`, the figure the server sent, which no animation frame can be mistaken
    for. Pinned over the page source because the fault is in this page's own script -- there is no JS
    harness -- so the guard is that the count branch reaches for the ATTRIBUTE and not the text.
    """
    import re

    for i in range(4):
        _ranked(f'H{i}', plats=100 - i, trophies=500)

    body = client.get(URL).content.decode()

    # The server's own render carries the real figure, so a reader with no JS sees it too.
    span = body[body.index('data-lb-mb-count'):]
    assert span[span.index('>') + 1:span.index('</span>')].strip() == '4', (
        'the minibar does not render the board population on the server'
    )

    # The count block ONLY. The slice used to run to the end of `syncMinibar`, which swept in the rank
    # chip's block too -- so the negative assertion below could have fired on unrelated code, and passed
    # while the count block was wrong. Guarded rather than left to raise a bare ValueError: reformatting
    # the inline script's indentation should say what broke, not hand over a traceback into slicing.
    sync = body[body.index('function syncMinibar()'):]
    assert '\n    }' in sync, "syncMinibar's close moved; this test's slicing needs rewriting"
    sync = sync[:sync.index('\n    }')]                       # the function's own close, at 4 spaces
    assert 'if (card && count) {' in sync, 'the count block is gone or renamed'
    branch = sync[sync.index('if (card && count) {'):]
    assert '\n        }' in branch, "the count block's close moved; this test's slicing needs rewriting"
    branch = branch[:branch.index('\n        }')]             # that block's close, at 8 spaces

    # The `.dataset.countup` READ is the property. NOT pinned to `parseFloat` or to a variable name --
    # `Number(...)` is the same fix and a rename is a harmless refactor; neither must fail this.
    assert re.search(r'\w+\.dataset\.countup', branch), (
        'the minibar count no longer reads the tally SOURCE value; a mid-animation read prints 0'
    )
    # ...and NO rendered-text read of any spelling. Deliberately not anchored on `\w+\.`: the original
    # bug's one-line form is `card.querySelector('.pp-tally').textContent`, where the preceding character
    # is `)`. The lookahead excludes the WRITE (`count.textContent = ...`) while still counting a
    # comparison read (`== '0'`), whose second `=` fails the inner match.
    reads = re.findall(r'\.(?:textContent|innerText|innerHTML)(?!\s*=[^=])', branch)
    assert not reads, (
        f'the minibar reads rendered text ({reads}), which is a frame of the count-up animation'
    )
    # ...and the value it reads has to be there to read. Scoped to the board card's OWN tally: the page
    # carries a dozen other `.pp-tally` elements and `data-countup` is the house idiom, so an unscoped
    # substring would stay green on a page where the card had lost its attribute.
    tallyblock = body[body.index('lb-boardcard__tally'):]
    tallyblock = tallyblock[:tallyblock.index('</div>')]
    assert 'data-countup=' in tallyblock, 'the board card tally carries no source value to read'


def test_the_minibar_and_boardentrance_look_for_the_same_tally(client):
    """The selector the minibar uses to find the card's Tally is written in THREE places -- the class on
    the card partial, `boardEntrance` in `utils.js`, and this page's own script -- and nothing made them
    agree. They diverged once already (`.pp-tally[data-countup]` here vs `.lb-boardcard__tally
    [data-countup]` there), which is survivable only while both happen to match the same node.

    Rename the class in the partial and in `utils.js` but miss this template and the card still ticks
    while `querySelector` here returns null -- the count silently stops updating, which is exactly the
    reported symptom, with a green suite. So the literal is EXTRACTED from the page's script and checked
    against the other two rather than typed a fourth time here.
    """
    import re
    from pathlib import Path

    _ranked('Someone', plats=5, trophies=50)
    body = client.get(URL).content.decode()

    branch = body[body.index('if (card && count) {'):]
    branch = branch[:branch.index('\n        }')]

    found = re.search(r"querySelector\('\.([\w-]+)\s*\[data-countup\]'\)", branch)
    assert found, 'the minibar no longer finds the tally by class + [data-countup]'
    selector = found.group(1)

    assert f'class="{selector}"' in body, (
        f'the minibar looks for .{selector}, which the board card partial does not render'
    )
    utils = (Path(__file__).resolve().parents[2] / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')
    entrance = utils[utils.index('function boardEntrance('):]
    entrance = entrance[:entrance.index('\n}')]            # the function's own close, at column 0
    assert f'.{selector} [data-countup]' in entrance, (
        f'boardEntrance and the minibar have diverged again: the bar looks for .{selector}'
    )


def test_every_board_renders_its_own_population_into_the_bar_server_side(client):
    """The SERVER's half, per board: `{{ ranked_total }}` in the bar equals `{{ total }}` on the card.

    Deliberately NOT the regression pin for the reported bug. That fault was entirely client-side, and the
    Django test client runs no JS -- reintroducing the exact pre-fix script leaves this test green (I
    checked). What it does pin is that a reader with JS off, or one reading before `mount()` runs, gets
    the right figure on EVERY board rather than only the default one, and that the two template variables
    behind those figures never drift apart. The JS half is pinned by shape, above.

    The seeds give the three boards DIFFERENT populations on purpose: with all three equal, a bar
    hardcoded to the default board's total would pass on every tab and the per-board claim would be
    hollow.
    """
    _ranked('Trophied', plats=7, trophies=70, points=400, career=900, level=8)
    _ranked('Second', plats=2, trophies=20, points=100, career=50, level=2)
    _ranked('CareerOnly', career=10, level=1)

    seen = {}
    for tab in ('trophies', 'points', 'career'):
        body = client.get(URL, {'tab': tab}).content.decode()

        bar = body[body.index('data-lb-mb-count'):]
        bar = bar[bar.index('>') + 1:bar.index('</span>')].strip()

        card = body[body.index('lb-boardcard__tally'):]
        card = card[card.index('data-countup="') + len('data-countup="'):]
        card = card[:card.index('"')]

        # `intcomma` on one side, the raw int on the other: they agree in VALUE, and only look alike
        # while every seeded population stays under four figures.
        seen[tab] = bar.replace(',', '')
        assert bar.replace(',', '') == card, (
            f'{tab}: the minibar says {bar!r} and the board card it proxies says {card!r}'
        )

    # The populations really are distinct, so the loop above could not have passed on one figure repeated.
    assert len(set(seen.values())) > 1, (
        f'every board seeded to the same population {seen}, so the per-board claim is untested'
    )


def test_the_minibar_lives_outside_the_swapped_wrapper(client):
    """A tab or filter change replaces `[data-lb-page]`'s innerHTML. A minibar inside it would be torn out
    and rebuilt under a reader mid-scroll -- and its listeners, which are wired once, would die with it."""
    _ranked('Someone', plats=5, trophies=50)
    body = client.get(URL).content.decode()

    page_start = body.index('<div data-lb-page>')
    page_end = body.index('</div><!-- /lb-page -->')
    assert not (page_start < body.index('data-lb-minibar') < page_end), (
        'the minibar is inside the swapped wrapper, so a tab change destroys it'
    )


def test_the_minibar_sentinel_is_inside_the_swapped_wrapper(client):
    """REGRESSION, reported from the browser as "the bar sometimes never appears, and sometimes never
    goes away". One stale observer, two symptoms.

    The bar is deliberately OUTSIDE `[data-lb-page]` so a swap cannot tear it out mid-scroll. The
    SENTINEL cannot be -- it marks where the chrome ends, so it sits inside and every swap replaces it.
    `StickyReveal.init()` dropped entries whose TARGET had left the DOM and skipped any target already
    wired, so this pairing survived the cleanup and kept observing a detached node: `.is-pinned` froze
    wherever it was, hidden or showing.

    This pins the ARRANGEMENT that makes the re-init necessary, so the two halves cannot drift apart --
    the fix itself lives in `StickyReveal` and is asserted below.
    """
    _ranked('Someone', plats=5, trophies=50)
    body = client.get(URL).content.decode()

    bar = body.index('data-lb-minibar')
    sentinel = body.index('id="lb-minibar-sentinel"')
    page_start = body.index('<div data-lb-page>')
    page_end = body.index('</div><!-- /lb-page -->')

    assert bar < page_start, 'the bar is inside the swapped wrapper and will be destroyed by a swap'
    assert page_start < sentinel < page_end, (
        'the sentinel is outside the wrapper, so it no longer marks where the chrome ends'
    )


def test_stickyreveal_rewires_when_only_the_sentinel_is_replaced(client):
    """The fix for the above, asserted on the helper because there is no JS runner here.

    `init()` has to drop an entry whose SENTINEL left the DOM, not only one whose target did -- and clear
    the target's wired flag, or the re-wire loop skips it and the entry never comes back.
    """
    import pathlib as _p

    js = _p.Path('static/js/utils.js').read_text(encoding='utf-8')

    assert 'document.contains(e.sentinel)' in js, (
        'init() no longer checks the sentinel, so a swapped sentinel leaves a stale observer'
    )
    assert 'e.target._stickyReveal = false' in js, (
        'the wired flag is not cleared, so the dropped entry can never be re-wired'
    )
