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

pytestmark = pytest.mark.django_db

URL = reverse('overall_badge_leaderboards')


def _ranked(name, *, country='', country_name='', plats=0, trophies=0, points=0, career=0, level=0):
    p = ProfileFactory(display_psn_username=name, country_code=country, country=country_name)
    if plats or trophies or points:
        ProfileBadgeStanding.objects.create(
            profile=p, country_code=country, total_xp=points,
            trophies_platinum=plats, trophies_total=trophies,
        )
    if career:
        ProfileCareerStanding.objects.create(
            profile=p, country_code=country, total_xp=career, pursuer_level=level)
    return p


def test_the_landing_offers_three_boards_and_defaults_to_progress(client):
    """Progress leads because it is the board with the most entrants -- the one a first-time visitor is
    most likely to appear on."""
    body = client.get(URL).content.decode()

    for key in ('progress', 'points', 'career'):
        assert f'data-board="{key}"' in body, f'the {key} board is missing from the tab strip'
    assert 'aria-selected="true"' in body

    import re
    active = re.search(r'data-board="(\w+)"[^>]*aria-selected="true"|aria-selected="true"[^>]*data-board="(\w+)"', body)
    assert active, 'no tab is marked active'
    assert 'progress' in active.group(0), 'the landing does not default to Progress'


def test_country_is_a_filter_not_a_tab(client):
    """The decision the section rests on. There must be no country TAB, and the filter must apply to
    whichever board is open -- a board per country is what made slicing unaffordable before."""
    _ranked('CanadaTop', country='CA', country_name='Canada', plats=9, trophies=90, points=500)
    _ranked('BritTop', country='GB', country_name='Britain', plats=99, trophies=999, points=5000)

    body = client.get(URL).content.decode()
    assert 'data-board="country"' not in body, 'country is back as a tab'
    assert 'name="country"' in body, 'the country filter is missing'

    sliced = client.get(URL, {'tab': 'progress', 'country': 'CA'}).content.decode()
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
    ProfileBadgeStanding.objects.create(profile=profile, total_xp=100, trophies_platinum=3, trophies_total=40)
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
    with CaptureQueriesContext(connection) as small:
        client.get(URL, {'tab': 'progress'})

    for i in range(20):
        _ranked(f'Many{i}', plats=i, trophies=i * 10, points=i * 100)
    with CaptureQueriesContext(connection) as large:
        client.get(URL, {'tab': 'progress'})

    assert len(large.captured_queries) == len(small.captured_queries), (
        f'{len(small.captured_queries)} queries for 3 rows but {len(large.captured_queries)} for 23'
    )


def test_the_empty_board_says_which_kind_of_empty_it_is(client):
    """"No hunters from this country" and "nobody has scored yet" are different problems with different
    next actions, and a single generic empty state would answer neither."""
    _ranked('Elsewhere', country='GB', country_name='Britain', plats=5, trophies=50)

    everywhere = client.get(URL, {'tab': 'career'}).content.decode()
    assert 'still empty' in everywhere

    sliced = client.get(URL, {'tab': 'progress', 'country': 'GB'}).content.decode()
    assert 'Elsewhere' in sliced   # sanity: GB has someone on the progress board


def test_the_series_directory_stays_reachable_but_out_of_the_tab_strip(client):
    """It is a DIRECTORY, not a board, and step 6 promotes it to `/leaderboards/badges/`. Keeping it in
    the strip was the incoherence this rebuild removes; dropping it entirely would leave a gap until
    step 6."""
    body = client.get(URL, {'tab': 'series'}).content.decode()
    assert 'Back to the boards' in body, 'the directory has no way back'

    strip = client.get(URL).content.decode()
    assert 'data-board="series"' not in strip, 'the directory is back in the board tab strip'


def test_a_career_only_hunter_makes_their_country_selectable(client):
    """The two economies are sealed apart, so a hunter can hold Career XP and no badge standing at all.
    Reading the picker from ProfileBadgeStanding alone left their country missing -- unselectable on the
    very board they appear on. Caught by a test whose real subject was something else entirely."""
    _ranked('CareerOnly', country='JP', country_name='Japan', career=1200, level=15)

    body = client.get(URL, {'tab': 'career'}).content.decode()
    assert 'value="JP"' in body, 'a career-only hunter\'s country is missing from the picker'

    sliced = client.get(URL, {'tab': 'career', 'country': 'JP'}).content.decode()
    assert 'CareerOnly' in sliced
