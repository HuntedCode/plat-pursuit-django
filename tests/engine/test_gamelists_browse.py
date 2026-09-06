"""The rebuilt Game Lists browse page.

Carries forward the assertions `test_lists_browse.py` already wrote for the 2026-08 rebuild, since
those encode work that was done once and should not be re-derived: the query flatness, the HTMX
partial contract, the infinite-scroll branch, the mosaic composition, the privacy rule and the sort
clamp. What changed underneath is the KEYING -- items point at Concepts now, so the covers come from
a batched service rather than a prefetch, and the flatness tests are the ones that notice if that
regresses.

The page is staff-gated while this branch is open (lists ship with the Challenges beta, in one
update), so every request here logs in as staff. That gate is a `_DevelopmentGate` mixin and its
removal is the whole of "turn it on".
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from gamelists.services import game_list_service as svc
from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db

BROWSE = '/community/lists/'


@pytest.fixture
def staff_client(client):
    """Every request in this file. The gate is temporary; the page under it is not."""
    staff = UserFactory()
    staff.role = 'admin'
    staff.save()
    client.force_login(staff)
    return client


def _hunter(psn='curator', premium=True):
    """Premium by default: several tests build more than the free cap of three lists, and the cap
    is the service's own guard rather than something to work around."""
    profile = ProfileFactory(is_linked=True, psn_username=psn)
    if premium:
        profile.user_is_premium = True
        profile.save(update_fields=['user_is_premium'])
    return profile


def _list(owner, games, *, name, public=True):
    game_list = svc.create_list(owner, name=name, is_public=public)
    for _ in range(games):
        concept = ConceptFactory()
        GameFactory(concept=concept, title_platform='PS5')
        svc.add_concept(game_list, owner, concept)
    return game_list


#: Tables the page under test is allowed to touch. Counting the WHOLE page instead makes this
#: measure the site chrome too -- and the chrome caches, so an unrelated `art_reveal` lookup fires on
#: the first render and not the second, which showed up as the grid getting FASTER as it grew. A
#: flatness test that moves for reasons outside the page cannot say anything about the page.
_LIST_TABLES = ('gamelists_', 'trophies_game"', 'trophies_concept')


def _queries(client, url):
    with CaptureQueriesContext(connection) as ctx:
        client.get(url)
    return len([
        q for q in ctx.captured_queries
        if any(table in q['sql'] for table in _LIST_TABLES)
    ])


# ── the headline: the page does not scale with its own content ───────────────────────────────────

def test_the_browse_grid_costs_the_same_for_4_lists_as_for_10(staff_client):
    """The original measured 8 queries for 5 lists and 23 for 20, because the tile read a
    `first_game_image` PROPERTY that ran its own query per card while reading like a field. The
    concept re-key could reintroduce exactly that shape -- a cover needs a Game and an item now
    holds a Concept -- so this is the test that guards the re-key, not just the old rebuild."""
    owner = _hunter()
    for i in range(4):
        _list(owner, 3, name=f'List {i}')
    small = _queries(staff_client, BROWSE)

    for i in range(4, 10):
        _list(owner, 3, name=f'List {i}')
    large = _queries(staff_client, BROWSE)

    assert large == small, (
        f'the grid grew from {small} to {large} queries between 4 and 10 lists -- something '
        f'resolves per card again'
    )


def test_a_bigger_list_does_not_cost_the_grid_more(staff_client):
    """The mosaic is bounded to four covers in the SERVICE. Bounding it in the template instead
    would still fetch every item on every list -- a 200-game list loading 200 rows to draw four."""
    owner = _hunter()
    _list(owner, 2, name='Small')
    small = _queries(staff_client, BROWSE)

    _list(owner, 40, name='Huge')
    large = _queries(staff_client, BROWSE)

    assert large == small, f'{small} queries with a 2-game list, {large} once a 40-game one appeared'


def test_the_cover_join_does_not_drag_the_igdb_blob_along(staff_client):
    """`raw_response` is the ~30 KB IGDB blob no cover template reads and the documented trigger for
    the May 2026 OOM. Pulling it back in would not change any query COUNT, so the tests above cannot
    see it -- this reads the SQL."""
    owner = _hunter()
    _list(owner, 3, name='Covered')

    with CaptureQueriesContext(connection) as ctx:
        staff_client.get(BROWSE)

    joined = [q['sql'] for q in ctx.captured_queries if 'igdb' in q['sql'].lower()]
    assert joined, 'the browse page no longer joins the cover chain at all'
    for sql in joined:
        assert 'raw_response' not in sql.lower(), 'the IGDB blob is being fetched for a cover'


# ── it behaves like the other browse pages ───────────────────────────────────────────────────────

def test_htmx_gets_the_grid_and_nothing_else(staff_client):
    owner = _hunter()
    _list(owner, 2, name='Swapped')

    body = staff_client.get(BROWSE, HTTP_HX_REQUEST='true').content.decode()

    assert 'items-grid' in body
    assert '<!doctype html' not in body.lower(), 'the filter swap returned the whole page'
    assert '<nav' not in body.lower()


def test_the_partial_bakes_in_the_reveal_class_and_the_full_page_does_not(staff_client):
    """htmx's settle step restores server attributes on id'd swapped elements, so a class added by
    JS in afterSwap is wiped again and the cards unhide with a flash. It has to be in the markup the
    swap returns, and only there."""
    owner = _hunter()
    _list(owner, 2, name='Revealed')

    full = staff_client.get(BROWSE).content.decode()
    swapped = staff_client.get(BROWSE, HTTP_HX_REQUEST='true').content.decode()

    assert 'pp-reveal' in swapped
    assert 'pp-reveal' not in full


def test_the_infinite_scroller_page_fetch_also_gets_the_partial(staff_client):
    """`InfiniteScroller` sends `X-Requested-With`, NOT an htmx header. A guard that checks only
    `request.htmx` returns a whole document to be appended into the grid."""
    owner = _hunter()
    _list(owner, 2, name='Scrolled')

    body = staff_client.get(
        BROWSE, HTTP_X_REQUESTED_WITH='XMLHttpRequest').content.decode()

    assert 'items-grid' in body
    assert '<!doctype html' not in body.lower()


def test_the_header_count_is_the_one_the_grid_is_showing(staff_client):
    """It used to run a second, unfiltered `COUNT(*)`, so the header disagreed with the grid the
    moment anybody searched."""
    owner = _hunter()
    _list(owner, 1, name='Alpha')
    _list(owner, 1, name='Beta')

    resp = staff_client.get(BROWSE, {'q': 'Alpha'})

    assert resp.context['total_lists'] == 1
    assert resp.context['paginator'].count == 1


# ── the rules that are not about speed ───────────────────────────────────────────────────────────

def test_a_private_list_is_invisible_to_everyone_else(staff_client):
    """Even to staff on this page: the browse grid is the PUBLIC catalogue, and a staff account
    browsing it is still browsing it."""
    owner = _hunter()
    _list(owner, 1, name='Kept back', public=False)
    _list(owner, 1, name='Shared')

    body = staff_client.get(BROWSE).content.decode()

    assert 'Shared' in body
    assert 'Kept back' not in body


def test_a_junk_sort_falls_back_rather_than_rendering_an_unselected_toolbar(staff_client):
    """Dropping to no ordering is how a browse grid ends up in whatever order the database felt
    like, which looks like a bug and cannot be reproduced."""
    owner = _hunter()
    _list(owner, 1, name='Sorted')

    resp = staff_client.get(BROWSE, {'sort': 'nonsense'})

    assert resp.context['current_sort'] == 'popular'


@pytest.mark.parametrize('sort', ['popular', 'recent', 'updated', 'most_games', 'alpha'])
def test_every_offered_sort_actually_sorts(staff_client, sort):
    """The toolbar reads `SORT_CHOICES`, and a sort that appears in the dropdown and does nothing is
    the failure that made those choices DATA in the first place."""
    owner = _hunter()
    _list(owner, 2, name='Zeta')
    _list(owner, 1, name='Alpha')

    resp = staff_client.get(BROWSE, {'sort': sort})

    assert resp.status_code == 200
    assert resp.context['current_sort'] == sort
    assert len(resp.context['game_lists']) == 2


def test_alphabetical_sorting_ignores_case(staff_client):
    """Postgres files uppercase before lowercase, so a plain `name` sort puts "apex" after
    "Zenith" -- the house rule is `Lower()` on every front-facing name column."""
    owner = _hunter()
    _list(owner, 1, name='apex predator')
    _list(owner, 1, name='Zenith')

    names = [gl.name for gl in staff_client.get(BROWSE, {'sort': 'alpha'}).context['game_lists']]

    assert names == ['apex predator', 'Zenith']


def test_search_matches_a_list_name_a_description_and_a_creator(staff_client):
    owner = _hunter(psn='searchme')
    svc.create_list(owner, name='Findable', description='nothing special', is_public=True)
    svc.create_list(owner, name='Other', description='a haystack needle here', is_public=True)

    for term, expected in (('Findable', 1), ('needle', 1), ('searchme', 2)):
        found = staff_client.get(BROWSE, {'q': term}).context['game_lists']
        assert len(found) == expected, f'searching {term!r} found {len(found)}'


def test_the_game_count_range_filters_both_ends(staff_client):
    owner = _hunter()
    _list(owner, 1, name='Tiny')
    _list(owner, 5, name='Middling')
    _list(owner, 9, name='Big')

    found = staff_client.get(BROWSE, {'min_games': '3', 'max_games': '7'}).context['game_lists']

    assert [gl.name for gl in found] == ['Middling']


def test_a_junk_range_is_ignored_rather_than_emptying_the_grid(staff_client):
    owner = _hunter()
    _list(owner, 2, name='Still here')

    found = staff_client.get(BROWSE, {'min_games': 'lots', 'max_games': '-'}).context['game_lists']

    assert len(found) == 1


# ── the mosaic ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('games, expected', [(1, 1), (2, 2), (3, 3), (5, 4)])
def test_the_mosaic_composes_around_however_many_covers_there_are(staff_client, games, expected):
    """`is-N` picks the composition, so a two-game list reads as composed rather than as a four-slot
    grid with holes."""
    owner = _hunter()
    _list(owner, games, name=f'{games} games')

    resp = staff_client.get(BROWSE)

    assert len(resp.context['game_lists'][0].cover_items) == expected
    assert f'pp-gtile__mosaic is-{expected}' in resp.content.decode()


def test_an_empty_list_draws_the_placeholder_rather_than_a_broken_mosaic(staff_client):
    owner = _hunter()
    svc.create_list(owner, name='Nothing yet', is_public=True)

    body = staff_client.get(BROWSE).content.decode()

    assert 'pp-gtile__art--empty' in body
    assert 'pp-gtile__mosaic' not in body


def test_the_tile_uses_the_sites_own_cover_chain(staff_client):
    """The pre-rebuild tile read `game.title_image` directly, which is why lists were the one
    surface showing PSN art where the rest of the site shows the IGDB cover."""
    from pathlib import Path

    tile = (Path(__file__).resolve().parents[2]
            / 'templates' / 'gamelists' / 'partials' / 'list_tile.html').read_text(encoding='utf-8')

    assert 'display_image_url' in tile
    assert 'item.title_image' not in tile
