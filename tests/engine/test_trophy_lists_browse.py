"""Trophy Lists browse (/games/lists/, TrophyListsBrowseView) -- the Games/Trophy Lists IA's
LAST canonical page: the LIST-level catalogue, one card per trophy list, deliberately
UN-condensed (the anti-Browse-Games). NOT named test_lists_browse.py -- that file belongs to the
hidden GameList collections system and is module-skipped with it.
"""

import pytest
from django.urls import reverse

from tests.factories import GameFactory, ProfileFactory, ProfileGameFactory

pytestmark = pytest.mark.django_db

GRID_PARTIAL = 'trophies/partials/trophy_lists/browse_results.html'
FULL_PAGE = 'trophies/trophy_lists.html'
URL = '/games/lists/'


# ── The page's identity: per-LIST, never condensed ────────────────────────────────────────────────

def test_sibling_lists_render_one_card_each(client):
    """THE anti-condensing pin: two sibling lists of one concept are TWO cards here -- exactly
    what Browse Games' election dedupes away -- each linking its OWN List detail."""
    a = GameFactory(title_name='Stack EU', title_platform=['PS5'])
    b = GameFactory(concept=a.concept, title_name='Stack NA', title_platform=['PS5'])

    resp = client.get(URL)
    content = resp.content.decode()

    assert content.count('pp-gcard__title') == 2
    assert 'data-result-count="2"' in content
    assert f'href="/games/{a.np_communication_id}/"' in content
    assert f'href="/games/{b.np_communication_id}/"' in content
    assert 'condensed_cards' not in resp.context
    assert 'list_count_map' not in resp.context


def test_blank_np_rows_never_render_a_card(client):
    """The destination np floor without the election: an un-linkable row is not a card."""
    GameFactory(title_name='Linkable List', title_platform=['PS5'])
    GameFactory(title_name='Ghost List', title_platform=['PS5'], np_communication_id=None)

    content = client.get(URL).content.decode()

    assert 'Linkable List' in content
    assert 'Ghost List' not in content


def test_bare_url_returns_200_even_with_saved_browse_defaults(client):
    """The no-dispatch decision: this page is static-sitemap-advertised, so the bare URL must
    200 without a redirect -- INCLUDING for a signed-in hunter whose saved Browse Games defaults
    would 302 them on /games/ (the browse_defaults key does not apply here)."""
    GameFactory(title_platform=['PS5'])
    viewer = ProfileFactory(is_linked=True)
    viewer.user.browse_defaults = {'games': {'platform': ['PS5'], 'sort': 'played'}}
    viewer.user.save(update_fields=['browse_defaults'])
    client.force_login(viewer.user)

    resp = client.get(URL)

    assert resp.status_code == 200
    assert 'index, follow' in resp.content.decode()


# ── Filters + sort ────────────────────────────────────────────────────────────────────────────────

def test_platform_and_region_filters_narrow(client):
    """Regions are first-class here (the page's point): ?regions=NA keeps the NA list and drops
    the EU-only one; ?platform narrows as everywhere."""
    na = GameFactory(title_name='NA Copy', title_platform=['PS5'], is_regional=True, region=['NA'])
    GameFactory(concept=na.concept, title_name='EU Copy', title_platform=['PS5'],
                is_regional=True, region=['EU'])
    GameFactory(title_name='Old Gen List', title_platform=['PS3'])

    by_region = client.get(URL, {'regions': 'NA'}).content.decode()
    assert 'NA Copy' in by_region and 'EU Copy' not in by_region

    by_platform = client.get(URL, {'platform': 'PS3'}).content.decode()
    assert 'Old Gen List' in by_platform and 'NA Copy' not in by_platform


def test_alpha_default_and_sort_param(client):
    GameFactory(title_name='Zulu List', title_platform=['PS5'], played_count=999)
    GameFactory(title_name='Alpha List', title_platform=['PS5'], played_count=1)

    default = client.get(URL).content.decode()
    assert default.index('Alpha List') < default.index('Zulu List'), 'alpha is the default sort'

    played = client.get(URL, {'sort': 'played'}).content.decode()
    assert played.index('Zulu List') < played.index('Alpha List')


def test_platinum_only_filter(client):
    from tests.factories import TrophyFactory
    plat = GameFactory(title_name='Plat List', title_platform=['PS5'])
    TrophyFactory(game=plat, trophy_type='platinum')
    GameFactory(title_name='Platless List', title_platform=['PS5'])

    content = client.get(URL, {'show_only_platinum': 'on'}).content.decode()

    assert 'Plat List' in content and 'Platless List' not in content


def test_region_chip_and_reset_resolve_against_this_page(client):
    """The chip_base_url / empty_reset_url overrides: removing the region chip and the
    empty-state reset must land back on /games/lists/, never eject to Browse Games."""
    GameFactory(title_name='Regional List', title_platform=['PS5'], is_regional=True, region=['JP'])

    content = client.get(URL, {'regions': 'NA', 'letter': 'Q'}).content.decode()

    assert 'pp-gbrowse__achip' in content                     # active-filter chips rendered
    assert 'href="/games/lists/?' in content                  # chip remove-URLs on this page
    assert 'href="/games/?' not in content                    # never Browse Games
    assert 'pp-gcard-empty__reset' in content                 # nothing matches Q + NA -> reset CTA
    assert 'href="/games/lists/"' in content


# ── Chassis: XHR partial, pagination, minibar ─────────────────────────────────────────────────────

def test_xhr_returns_rows_partial(client):
    GameFactory(title_platform=['PS5'])

    resp = client.get(URL, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    templates = {t.name for t in resp.templates if t.name}

    assert resp.status_code == 200
    assert GRID_PARTIAL in templates
    assert FULL_PAGE not in templates
    assert 'data-result-count' in resp.content.decode()


def test_xhr_past_end_page_404s(client):
    GameFactory(title_platform=['PS5'])
    resp = client.get(URL, {'page': '999'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    assert resp.status_code == 404


def test_grid_contract_minibar_and_sentinel(client):
    """The card contract (pursuer band via show_game_hooks) + the page chrome: sticky minibar,
    its sentinel, the infinite-scroll sentinel, no Lucky button (deliberate v1 omission), and
    no leaked template syntax."""
    viewer = ProfileFactory(is_linked=True)
    game = GameFactory(title_name='Contract Check', title_platform=['PS5'])
    ProfileGameFactory(profile=viewer, game=game, progress=42)
    client.force_login(viewer.user)

    content = client.get(URL).content.decode()

    assert 'pp-gcard' in content and 'Contract Check' in content
    assert 'No badges' in content and 'No contract' in content   # the pursuer band placeholders
    assert '42%' in content                                       # viewer progress fill
    assert 'data-sticky-sentinel="#tlb-minibar-sentinel"' in content
    assert 'id="tlb-sentinel"' in content and 'id="tlb-count"' in content
    assert 'data-lucky-btn' not in content
    assert 'js/trophy-lists.js' in content and 'js/browse-filters.js' in content
    assert '{#' not in content and '{%' not in content
