"""Tests for the rebuilt Company (Developers & Publishers) list page (CompanyListView, /companies/).

Covers the from-scratch rebuild: the .pp-gtile grid with the company logo chip + country meta, the Role
quick-filter + country/sort filters, search, the HtmxListMixin partial/XHR guard, the materialized
representative_game cover (recompute_tag_covers now handling companies), and bounded queries.
"""

import itertools

import pytest
from django.core.management import call_command
from django.urls import reverse

from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory

pytestmark = pytest.mark.django_db

GRID_PARTIAL = 'trophies/partials/company_list/browse_results.html'
FULL_PAGE = 'trophies/company_list.html'

_co_seq = itertools.count(60001)
_ig_seq = itertools.count(770001)

_ROLE_FLAG = {
    'developer': 'is_developer', 'publisher': 'is_publisher',
    'porting': 'is_porting', 'supporting': 'is_supporting',
}


def _company(name, slug, country=None, logo=None):
    from trophies.models import Company
    return Company.objects.create(
        igdb_id=next(_co_seq), name=name, slug=slug, country=country, logo_image_id=logo or '',
    )


def _link(company, title, role='developer', platforms=None, with_art=True):
    from trophies.models import ConceptCompany
    concept = ConceptFactory(unified_title=title)
    IGDBMatchFactory(concept=concept, igdb_id=next(_ig_seq))
    GameFactory(
        concept=concept, title_name=title, title_platform=platforms or ['PS5'],
        title_image='https://example.com/c.jpg' if with_art else '',
    )
    ConceptCompany.objects.create(concept=concept, company=company, **{_ROLE_FLAG[role]: True})
    return concept


# ── Rendering ─────────────────────────────────────────────────────────────────────────────────────────────

def test_company_list_renders(client):
    co = _company('Naughty Dog', 'naughty-dog')
    _link(co, 'The Last of Us')
    _link(co, 'Uncharted 4')

    resp = client.get(reverse('companies_list'))
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'pp-gtile' in content
    assert 'Naughty Dog' in content
    assert '2 games' in content
    assert 'co-sentinel' in content            # infinite-scroll sentinel
    assert '{#' not in content and '{%' not in content


def test_tile_shows_logo_chip_and_country(client):
    co = _company('Insomniac', 'insomniac', country=840, logo='logo123')
    _link(co, 'Spider-Man')

    content = client.get(reverse('companies_list')).content.decode()

    assert 'pp-gtile__logo' in content         # studio logo chip
    assert 'logo123' in content                # the logo image id in the URL
    assert 'pp-gtile__country' in content      # country meta line


# ── Filters ───────────────────────────────────────────────────────────────────────────────────────────────

def test_role_filter_narrows(client):
    dev = _company('Dev Studio', 'dev-studio')
    _link(dev, 'Dev Game', role='developer')
    pub = _company('Pub House', 'pub-house')
    _link(pub, 'Pub Game', role='publisher')

    dev_only = client.get(reverse('companies_list'), {'role': 'developer'}).content.decode()
    assert 'Dev Studio' in dev_only
    assert 'Pub House' not in dev_only

    pub_only = client.get(reverse('companies_list'), {'role': 'publisher'}).content.decode()
    assert 'Pub House' in pub_only
    assert 'Dev Studio' not in pub_only


def test_country_filter_narrows(client):
    us = _company('US Studio', 'us-studio', country=840)
    _link(us, 'US Game')
    jp = _company('JP Studio', 'jp-studio', country=392)
    _link(jp, 'JP Game')

    content = client.get(reverse('companies_list'), {'country': '840'}).content.decode()

    assert 'US Studio' in content
    assert 'JP Studio' not in content


def test_search_narrows(client):
    a = _company('Square Enix', 'square-enix')
    _link(a, 'FF')
    b = _company('Capcom', 'capcom')
    _link(b, 'RE')

    content = client.get(reverse('companies_list'), {'query': 'Square'}).content.decode()

    assert 'Square Enix' in content
    assert 'Capcom' not in content


def test_sort_by_games_desc(client):
    small = _company('Small Studio', 'small-studio')
    _link(small, 'S1')
    _link(small, 'S2')
    big = _company('Big Studio', 'big-studio')
    for i in range(4):
        _link(big, f'B{i}')

    content = client.get(reverse('companies_list'), {'sort': 'games'}).content.decode()

    assert content.index('Big Studio') < content.index('Small Studio')


# ── HtmxListMixin partial / XHR ───────────────────────────────────────────────────────────────────────────

def test_xhr_returns_grid_partial(client):
    co = _company('Scroll Studio', 'scroll-studio')
    _link(co, 'Scroll Game')

    resp = client.get(reverse('companies_list'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    templates = {t.name for t in resp.templates if t.name}

    assert resp.status_code == 200
    assert GRID_PARTIAL in templates
    assert FULL_PAGE not in templates
    assert 'data-result-count' in resp.content.decode()


# ── Materialized cover ────────────────────────────────────────────────────────────────────────────────────

def test_recompute_materializes_company_cover(client):
    co = _company('Coverable Co', 'coverable-co')
    _link(co, 'Coverable Game')

    call_command('recompute_tag_covers')
    co.refresh_from_db()

    assert co.representative_game_id is not None
    content = client.get(reverse('companies_list')).content.decode()
    assert 'https://example.com/c.jpg' in content
    assert 'pp-gtile__art--empty' not in content


def test_query_count_is_bounded(client, django_assert_max_num_queries):
    for i in range(20):
        co = _company(f'Studio {i}', f'studio-{i}', country=840, logo=f'lg{i}')
        _link(co, f'Game {i}')

    with django_assert_max_num_queries(14):
        resp = client.get(reverse('companies_list'))
    assert resp.status_code == 200
