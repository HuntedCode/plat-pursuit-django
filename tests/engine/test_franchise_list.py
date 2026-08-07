"""Tests for the rebuilt Franchise/Series list page (FranchiseListView, /franchises/).

Covers the from-scratch rebuild: the .pp-gtile grid + type corner badge + version count, the Franchise/Series/
All type filter, search/sort/show_solo, the materialized representative_game cover (recompute_tag_covers now
handling franchises, honoring is_excluded/is_spinoff), the HtmxListMixin partial/XHR guard, and bounded queries.
"""

import itertools

import pytest
from django.core.management import call_command
from django.urls import reverse

from tests.factories import GameFactory, IGDBMatchFactory

pytestmark = pytest.mark.django_db

GRID_PARTIAL = 'trophies/partials/franchise_list/browse_results.html'
FULL_PAGE = 'trophies/franchise_list.html'

_fr_seq = itertools.count(40001)
_ig_seq = itertools.count(880001)


def _franchise(name, source_type='franchise', n_games=2, excluded=False, **game_kwargs):
    """A Franchise/Series with `n_games` distinct-IGDB-id member games (so game_count == n_games)."""
    from trophies.models import Franchise, ConceptFranchise
    fr = Franchise.objects.create(
        igdb_id=next(_fr_seq), name=name, slug=name.lower().replace(' ', '-'), source_type=source_type,
    )
    for _ in range(n_games):
        game = GameFactory(**game_kwargs)
        IGDBMatchFactory(concept=game.concept, igdb_id=next(_ig_seq))
        ConceptFranchise.objects.create(concept=game.concept, franchise=fr, is_excluded=excluded)
    return fr


# ── Rendering + type badge ────────────────────────────────────────────────────────────────────────────────

def test_franchise_list_renders(client):
    _franchise('Resident Evil', n_games=3, title_platform=['PS5'])

    resp = client.get(reverse('franchises_list'))
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'pp-gtile' in content            # the shared grouping tile
    assert 'Resident Evil' in content
    assert 'pp-switch' in content           # the Franchise/Series/All toggle
    assert 'fr-sentinel' in content         # infinite-scroll sentinel
    assert '3 games' in content
    assert '{#' not in content and '{%' not in content


def test_type_badge_franchise_vs_series(client):
    _franchise('Final Fantasy', source_type='franchise', title_platform=['PS5'])

    franchise_view = client.get(reverse('franchises_list'), {'type': 'franchise'}).content.decode()
    assert 'pp-gtile__tag' in franchise_view
    assert '>Franchise<' in franchise_view   # the corner badge text

    _franchise('FF VII Series', source_type='collection', title_platform=['PS5'])
    series_view = client.get(reverse('franchises_list'), {'type': 'series'}).content.decode()
    assert '>Series<' in series_view


# ── Type filter ───────────────────────────────────────────────────────────────────────────────────────────

def test_type_filter_narrows(client):
    _franchise('Umbrella IP', source_type='franchise', title_platform=['PS5'])
    _franchise('Sub Series', source_type='collection', title_platform=['PS5'])

    franchise_only = client.get(reverse('franchises_list'), {'type': 'franchise'}).content.decode()
    assert 'Umbrella IP' in franchise_only
    assert 'Sub Series' not in franchise_only

    series_only = client.get(reverse('franchises_list'), {'type': 'series'}).content.decode()
    assert 'Sub Series' in series_only
    assert 'Umbrella IP' not in series_only

    both = client.get(reverse('franchises_list'), {'type': 'all'}).content.decode()
    assert 'Umbrella IP' in both and 'Sub Series' in both


def test_show_solo_toggle(client):
    _franchise('Solo Franchise', n_games=1, title_platform=['PS5'])   # 1 game -> hidden by default

    default = client.get(reverse('franchises_list')).content.decode()
    assert 'Solo Franchise' not in default

    with_solo = client.get(reverse('franchises_list'), {'show_solo': '1'}).content.decode()
    assert 'Solo Franchise' in with_solo


# ── Search / sort ─────────────────────────────────────────────────────────────────────────────────────────

def test_search_narrows(client):
    _franchise('Halo', title_platform=['PS5'])
    _franchise('Gears', title_platform=['PS5'])

    content = client.get(reverse('franchises_list'), {'query': 'Hal'}).content.decode()

    assert 'Halo' in content
    assert 'Gears' not in content


def test_sort_by_games_desc(client):
    _franchise('Small IP', n_games=2, title_platform=['PS5'])
    _franchise('Big IP', n_games=4, title_platform=['PS5'])

    content = client.get(reverse('franchises_list'), {'sort': 'games'}).content.decode()

    assert content.index('Big IP') < content.index('Small IP')


# ── HtmxListMixin partial / XHR ───────────────────────────────────────────────────────────────────────────

def test_xhr_returns_rows_partial(client):
    _franchise('Scroll IP', title_platform=['PS5'])

    resp = client.get(reverse('franchises_list'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    templates = {t.name for t in resp.templates if t.name}

    assert resp.status_code == 200
    assert GRID_PARTIAL in templates
    assert FULL_PAGE not in templates
    assert 'data-result-count' in resp.content.decode()


# ── Materialized cover ────────────────────────────────────────────────────────────────────────────────────

def test_recompute_materializes_franchise_cover(client):
    fr = _franchise('Coverable', n_games=2, title_platform=['PS5'], title_image='https://example.com/f.jpg')

    call_command('recompute_tag_covers')
    fr.refresh_from_db()

    assert fr.representative_game_id is not None
    # And it renders on the tile.
    content = client.get(reverse('franchises_list')).content.decode()
    assert 'https://example.com/f.jpg' in content
    assert 'pp-gtile__art--empty' not in content


def test_recompute_ignores_excluded_links_for_cover(client):
    """The franchise cover pick honors is_excluded -- an excluded link's game must not provide the cover."""
    from trophies.models import Franchise, ConceptFranchise
    fr = Franchise.objects.create(igdb_id=next(_fr_seq), name='Curated', slug='curated', source_type='franchise')
    # One EXCLUDED game (with art) + two normal games (with art).
    bad = GameFactory(title_platform=['PS5'], title_image='https://example.com/EXCLUDED.jpg')
    IGDBMatchFactory(concept=bad.concept, igdb_id=next(_ig_seq))
    ConceptFranchise.objects.create(concept=bad.concept, franchise=fr, is_excluded=True)
    for _ in range(2):
        g = GameFactory(title_platform=['PS5'], title_image='https://example.com/ok.jpg')
        IGDBMatchFactory(concept=g.concept, igdb_id=next(_ig_seq))
        ConceptFranchise.objects.create(concept=g.concept, franchise=fr, is_excluded=False)

    call_command('recompute_tag_covers')
    fr.refresh_from_db()

    assert fr.representative_game_id is not None
    assert fr.representative_game_id != bad.id   # the excluded game never provides the cover


def test_query_count_is_bounded(client, django_assert_max_num_queries):
    for i in range(20):
        _franchise(f'Franchise {i}', n_games=2, title_platform=['PS5'])

    with django_assert_max_num_queries(14):
        resp = client.get(reverse('franchises_list'))
    assert resp.status_code == 200
