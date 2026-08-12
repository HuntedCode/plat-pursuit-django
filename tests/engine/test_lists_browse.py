"""Game Lists: the public browse grid (`/community/lists/`) and a list's detail page.

Both pages scaled their query count with their own content, in the two ways this codebase has a standing
rule about.

- **Browse** rendered `GameList.first_game_image` per card. That reads like a field and is a PROPERTY
  that runs its own query, so 24 tiles cost 24 extra round trips. Measured 8 queries for 5 lists and 23
  for 20.
- **Detail** rendered `game.display_image_url` per item with only `select_related('game')`. That chain is
  IGDB-first, so every item fetched its own concept + match, and `raw_response` -- the ~30 KB IGDB blob
  no cover template reads -- came with them. Measured 12 queries for 5 items and **82 for 40**.

Flat now, and these pin it. The rest cover the pieces that make a browse page a browse page: the HTMX
partial contract, the mosaic's adaptive composition, and the privacy rule the rebuild must not lose.
"""
import re
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory
from trophies.models import GameList, GameListItem

pytestmark = pytest.mark.django_db

BROWSE = '/community/lists/'
ROOT = Path(__file__).resolve().parents[2]


def _hunter(name='HuntedCode'):
    return ProfileFactory(is_linked=True, psn_username=name)


def _list(profile, n_items=0, *, name='A list', public=True, likes=0):
    """A list whose items each carry a DISTINCT concept + IGDB match, so an unguarded cover render
    genuinely N+1s rather than reusing one already-fetched join."""
    gl = GameList.objects.create(
        profile=profile, name=name, is_public=public, game_count=n_items, like_count=likes,
    )
    for i in range(n_items):
        concept = ConceptFactory()
        IGDBMatchFactory(concept=concept)
        GameListItem.objects.create(game_list=gl, game=GameFactory(concept=concept), position=i)
    return gl


def _queries(client, url):
    client.get(url)                                   # warm: content types, session, and friends
    with CaptureQueriesContext(connection) as ctx:
        client.get(url)
    return len(ctx)


# -- The headline: neither page scales with its own content ----------------------------------------

def test_the_browse_grid_costs_the_same_for_5_lists_as_for_20(client):
    owner = _hunter()
    for i in range(5):
        _list(owner, 3, name=f'List {i}')
    small = _queries(client, BROWSE)

    for i in range(5, 20):
        _list(owner, 3, name=f'List {i}')
    large = _queries(client, BROWSE)

    assert large == small, (
        f'the browse grid grew from {small} to {large} queries between 5 and 20 lists -- something '
        f'resolves per card again (first_game_image was the original)'
    )


def test_a_long_list_costs_the_same_to_open_as_a_short_one(client):
    owner = _hunter()
    short = _queries(client, reverse('list_detail', args=[_list(owner, 5, name='Short').id]))
    long_ = _queries(client, reverse('list_detail', args=[_list(owner, 40, name='Long').id]))

    assert long_ == short, (
        f'a 40-item list cost {long_} queries against {short} for a 5-item one -- the cover chain is '
        f'N+1ing again (it measured 82 before the select_related)'
    )


def test_the_cover_blob_is_deferred_on_both_pages():
    """`raw_response` is the IGDB API blob, never read by a cover template, and the documented trigger
    for the May 2026 web-server OOM once concurrent renders pile the join payload up. Pulling it back in
    would not change any query COUNT, so the count tests above cannot see it."""
    import inspect

    from trophies.views.list_views import BrowseListsView, GameListDetailView

    for view in (BrowseListsView, GameListDetailView):
        src = inspect.getsource(view)
        assert 'igdb_match' in src, f'{view.__name__} does not join the cover chain at all'
        assert 'raw_response' in src, f'{view.__name__} joins igdb_match without deferring raw_response'


# -- The browse page behaves like the other browse pages -------------------------------------------

def test_htmx_gets_the_grid_and_nothing_else(client):
    """The filter swap replaces `#browse-results` only. If the partial carried the toolbar or the scroll
    sentinel, filtering would tear out the controls doing the filtering."""
    _list(_hunter(), 2, name='Findable')

    full = client.get(BROWSE).content.decode()
    partial = client.get(BROWSE, HTTP_HX_REQUEST='true').content.decode()

    assert partial.strip().startswith('<div id="items-grid"'), 'the partial is not grid-only'
    assert 'gl-sentinel' not in partial, 'the scroll sentinel would be swapped away by a filter change'
    assert 'data-browse-form' not in partial, 'the toolbar is inside the swap target'
    assert 'data-browse-form' in full, 'the full page lost its toolbar'


def test_the_partial_bakes_in_the_reveal_class_and_the_full_page_does_not(client):
    """htmx's settle step restores server attributes on id'd swapped elements, so a class added by JS in
    afterSwap is wiped again and the cards unhide with a flash. It has to ship in the markup -- but only
    on the swap, or the first paint starts with every card hidden."""
    _list(_hunter(), 1)

    assert 'pp-reveal' in client.get(BROWSE, HTTP_HX_REQUEST='true').content.decode()
    assert 'pp-reveal' not in client.get(BROWSE).content.decode()


def test_the_infinite_scroller_page_fetch_also_gets_the_partial(client):
    """`InfiniteScroller` sends `X-Requested-With`, not an HTMX header. Without that branch it receives
    the whole page and appends a nav, a footer and a second toolbar to the grid."""
    _list(_hunter(), 1)

    body = client.get(BROWSE, HTTP_X_REQUESTED_WITH='XMLHttpRequest').content.decode()

    assert body.strip().startswith('<div id="items-grid"')


@pytest.mark.parametrize('n_items,expected', [(0, None), (1, 'is-1'), (2, 'is-2'), (3, 'is-3'),
                                              (4, 'is-4'), (9, 'is-4')])
def test_the_mosaic_composes_itself_around_however_many_covers_there_are(client, n_items, expected):
    """A list is a COLLECTION, so its tile shows several covers -- otherwise it is indistinguishable from
    a game tile in the same grid. A fixed 2x2 with holes reads as broken rather than sparse, so the
    layout adapts; and it never draws more than the four the queryset prefetched."""
    _list(_hunter(), n_items, name='Mosaic')

    html = client.get(BROWSE).content.decode()

    if expected is None:
        assert 'pp-gtile__art--empty' in html, 'an empty list gets no placeholder'
        assert 'pp-gtile__mosaic' not in html
    else:
        assert f'pp-gtile__mosaic {expected}' in html, f'{n_items} covers did not compose as {expected}'


def test_the_tile_uses_the_sites_own_cover_chain():
    """It read `game.title_image` directly, so lists were the one surface showing PSN art where the rest
    of the site shows the IGDB cover. `display_image_url` is the single source of truth for that chain,
    and CLAUDE.md is explicit that it is never reimplemented inline."""
    tile = (ROOT / 'templates' / 'trophies' / 'partials' / 'lists'
            / 'list_tile.html').read_text(encoding='utf-8')
    tile = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', tile, flags=re.S)

    assert 'display_image_url' in tile
    assert 'title_image' not in tile, 'the tile is back on the raw PSN image'


def test_the_header_count_is_the_one_the_grid_is_showing(client):
    """It ran a second, UNFILTERED `COUNT(*)` beside the paginator's, so the figure in the header
    disagreed with the grid under it the moment anyone searched."""
    owner = _hunter()
    _list(owner, 1, name='Bloodborne run')
    _list(owner, 1, name='Something else')

    resp = client.get(BROWSE, {'q': 'Bloodborne'})

    assert resp.context['total_lists'] == 1
    assert resp.context['paginator'].count == 1


# -- Rules the rebuild must not have dropped -------------------------------------------------------

def test_a_private_list_is_still_invisible_to_everyone_else(client):
    owner, stranger = _hunter('Owner'), _hunter('Stranger')
    private = _list(owner, 2, name='Secret', public=False)

    assert 'Secret' not in client.get(BROWSE).content.decode()

    client.force_login(stranger.user)
    assert client.get(reverse('list_detail', args=[private.id])).status_code == 404

    client.force_login(owner.user)
    assert client.get(reverse('list_detail', args=[private.id])).status_code == 200


def test_a_junk_sort_falls_back_rather_than_rendering_an_unselected_toolbar(client):
    """An unrecognised `?sort=` fell through to the default ordering, which was fine -- but the raw value
    was echoed back into the toolbar, so the select rendered with nothing selected."""
    _list(_hunter(), 1)

    resp = client.get(BROWSE, {'sort': 'nonsense'})

    assert resp.status_code == 200
    assert resp.context['current_sort'] == 'popular'


def test_the_detail_sort_only_offers_my_progress_when_there_is_a_me(client):
    """It sorts by the VIEWER's completion, which is meaningless logged out."""
    owner = _hunter()
    url = reverse('list_detail', args=[_list(owner, 2).id])

    assert 'completion' not in dict(client.get(url).context['sort_choices'])

    client.force_login(owner.user)
    assert 'completion' in dict(client.get(url).context['sort_choices'])
