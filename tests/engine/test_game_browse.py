"""Tests for the rebuilt Browse Games page (GamesListView, /games/).

Covers the data/behavior contract that the from-scratch --pp-* rebuild had to
preserve: the .pp-gcard grid renders the card contract, the platform/sort/
platinum filters still narrow/order, the bare-/games/ dispatch redirect fires,
and infinite scroll works (the HtmxListMixin XHR guard returns the rows partial;
a past-end page 404s). Also pins whale-safety (bounded query count).
"""

import pytest
from django.urls import reverse

from tests.factories import (
    GameFactory,
    ProfileFactory,
    ProfileGameFactory,
    TrophyFactory,
)

pytestmark = pytest.mark.django_db

GRID_PARTIAL = 'trophies/partials/game_list/browse_results.html'
FULL_PAGE = 'trophies/game_list.html'


def _url(**params):
    # Always pass a param so dispatch() doesn't 302 to the defaults redirect.
    base = {'platform': 'PS5'}
    base.update(params)
    return reverse('games_list'), base


def test_grid_renders_card_contract(client):
    """The grid renders .pp-gcard cells with the game title + trophy-count labels,
    and the infinite-scroll sentinel is present."""
    GameFactory(title_name='Contract Quest', title_platform=['PS5'])
    url, params = _url()

    resp = client.get(url, params)
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'pp-gcard' in content
    assert 'Contract Quest' in content
    # Trophy-count labels (the 4-cell row) + the sentinel that drives infinite scroll.
    for label in ('BRZ', 'SLV', 'GLD', 'PLT'):
        assert label in content
    assert 'gbrowse-sentinel' in content
    # No raw Django comment markers leak (multi-line {# #} is NOT a comment in Django and ships as text).
    assert '{#' not in content
    assert 'browse results partial' not in content


def test_platform_filter_narrows(client):
    """?platform=PS5 shows only PS5 games; ?platform=PS3 shows only PS3 games."""
    GameFactory(title_name='Current Gen', title_platform=['PS5'])
    GameFactory(title_name='Retro Relic', title_platform=['PS3'])

    url = reverse('games_list')
    ps5 = client.get(url, {'platform': 'PS5'}).content.decode()
    assert 'Current Gen' in ps5
    assert 'Retro Relic' not in ps5

    ps3 = client.get(url, {'platform': 'PS3'}).content.decode()
    assert 'Retro Relic' in ps3
    assert 'Current Gen' not in ps3


def test_sort_alpha_orders(client):
    """The default alphabetical sort orders titles A->Z."""
    GameFactory(title_name='Zephyr Drift', title_platform=['PS5'])
    GameFactory(title_name='Alpha Ascent', title_platform=['PS5'])
    url, params = _url(sort='alpha')

    content = client.get(url, params).content.decode()

    assert content.index('Alpha Ascent') < content.index('Zephyr Drift')


def test_platinum_only_filter(client):
    """show_only_platinum=on keeps only games that define a platinum trophy."""
    plat_game = GameFactory(title_name='Platinum Path', title_platform=['PS5'])
    TrophyFactory(game=plat_game, trophy_type='platinum')
    GameFactory(title_name='No Platinum Here', title_platform=['PS5'])

    url, params = _url(show_only_platinum='on')
    content = client.get(url, params).content.decode()

    assert 'Platinum Path' in content
    assert 'No Platinum Here' not in content


def test_authenticated_progress_renders(client):
    """A signed-in user's per-game progress shows on the card."""
    profile = ProfileFactory()
    client.force_login(profile.user)
    game = GameFactory(title_name='In Progress Game', title_platform=['PS5'])
    ProfileGameFactory(profile=profile, game=game, progress=42, has_plat=False)

    url, params = _url()
    content = client.get(url, params).content.decode()

    assert 'In Progress Game' in content
    assert '42%' in content


def test_xhr_returns_rows_partial(client):
    """The InfiniteScroller's XHR (X-Requested-With) gets the rows-only partial,
    NOT the full page -- this is the HtmxListMixin guard added for infinite scroll."""
    GameFactory(title_name='Scroll Target', title_platform=['PS5'])
    url, params = _url()

    resp = client.get(url, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    templates = {t.name for t in resp.templates if t.name}

    assert resp.status_code == 200
    assert GRID_PARTIAL in templates
    assert FULL_PAGE not in templates
    assert 'pp-gcard' in resp.content.decode()


def test_xhr_past_end_page_404s(client):
    """A page past the last one 404s, which is how InfiniteScroller detects end-of-list."""
    GameFactory(title_platform=['PS5'])
    url, params = _url(page='999')

    resp = client.get(url, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    assert resp.status_code == 404


def test_bare_games_redirects_to_defaults(client):
    """A bare /games/ (no query) 302-redirects to the modern-platform defaults."""
    resp = client.get(reverse('games_list'))

    assert resp.status_code == 302
    assert 'platform=' in resp['Location']


def test_query_count_is_whale_safe(client, django_assert_max_num_queries):
    """Render cost stays bounded regardless of catalogue size (no per-card N+1):
    one page of 30 cards costs the same whether there are 10 or 60 games."""
    GameFactory.create_batch(60, title_platform=['PS5'])
    url, params = _url()

    # Page (count + 30 rows) + the two post-pagination maps + session/misc.
    with django_assert_max_num_queries(14):
        resp = client.get(url, params)
    assert resp.status_code == 200
