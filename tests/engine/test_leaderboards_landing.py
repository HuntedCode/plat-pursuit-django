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
        ProfileBadgeStanding.objects.create(profile=p, country_code=country, total_xp=points)
    if career:
        ProfileCareerStanding.objects.create(
            profile=p, country_code=country, total_xp=career, pursuer_level=level)
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


def test_the_viewer_standing_is_in_the_header_not_in_the_rows(client):
    """Shown once, in the header. A per-row personal rank would make every response per-user and forfeit
    caching for the entire section, which is its defining performance property."""
    profile = ProfileFactory(display_psn_username='Me', is_linked=True)
    ProfileBadgeStanding.objects.create(profile=profile, total_xp=100)
    client.force_login(profile.user)

    body = client.get(URL).content.decode()
    assert 'lb-mine' in body, 'the viewer standing strip is missing from the header'
    # The wall's rows must stay identical for everyone -- no per-viewer marker inside a row.
    assert 'lb-row--me' not in body and 'is-you' not in body


def test_an_anonymous_visitor_gets_the_boards_without_a_standing_strip(client):
    _ranked('Public', plats=1, trophies=10, points=50)
    body = client.get(URL).content.decode()
    assert 'Public' in body, 'the boards are not public'
    assert 'lb-mine' not in body, 'a standing strip rendered for an anonymous visitor'


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


def test_the_header_tally_counts_the_board_it_sits_above(client):
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
        assert f'>{expected}</div>' in resp.content.decode(), 'the figure did not reach the header'


def test_the_header_tally_follows_the_country_slice(client):
    """A slice changes the population, so it has to change the figure describing it."""
    _ranked('CanadaOne', country='CA', country_name='Canada', plats=1, trophies=10, points=50)
    _ranked('CanadaTwo', country='CA', country_name='Canada', plats=2, trophies=20, points=60)
    _ranked('Brit', country='GB', country_name='Britain', plats=9, trophies=90, points=900)

    everywhere = client.get(URL, {'tab': 'trophies'}).content.decode()
    assert '>3</div>' in everywhere

    sliced = client.get(URL, {'tab': 'trophies', 'country': 'CA'}).content.decode()
    assert '>2</div>' in sliced, 'the header kept the global figure over a sliced wall'


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
