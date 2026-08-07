"""Tests for the rebuilt Genres & Themes list page (GenreThemeListView, /genres/).

Covers the data/behavior contract the from-scratch rebuild preserved and added: the combined page's
Genres/Themes switcher (`?tab=`), the category-tile grid rendering name + game count + the materialized
representative-cover FK (recompute_tag_covers), the game_count>0 gate, search + sort, the tab-whitelist
fallback, the HtmxListMixin partial/XHR guard, and a bounded query count.
"""

import itertools

import pytest
from django.core.management import call_command
from django.urls import reverse

from tests.factories import (
    GenreFactory,
    ConceptGenreFactory,
    GameFactory,
    IGDBMatchFactory,
    ProfileFactory,
    ProfileGameFactory,
)

pytestmark = pytest.mark.django_db

GRID_PARTIAL = 'trophies/partials/genre_theme_list/browse_results.html'
FULL_PAGE = 'trophies/genre_theme_list.html'

_theme_seq = itertools.count(9000)


def _genre_with_games(name, n=1, **game_kwargs):
    genre = GenreFactory(name=name, slug=name.lower().replace(' ', '-'))
    for _ in range(n):
        game = GameFactory(**game_kwargs)
        ConceptGenreFactory(concept=game.concept, genre=genre)
    return genre


def _theme_with_games(name, n=1, **game_kwargs):
    from trophies.models import Theme, ConceptTheme
    theme = Theme.objects.create(
        igdb_id=next(_theme_seq), name=name, slug=name.lower().replace(' ', '-'),
    )
    for _ in range(n):
        game = GameFactory(**game_kwargs)
        ConceptTheme.objects.create(concept=game.concept, theme=theme)
    return theme


# ── Rendering + switcher ──────────────────────────────────────────────────────────────────────────────────

def test_genres_tab_renders_tiles(client):
    """The default (Genres) tab renders `.pp-gtile` cards with the genre name + game count + the switcher,
    and no raw template syntax leaks."""
    _genre_with_games('Shooter', n=2)

    resp = client.get(reverse('genres_list'))
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'pp-gtile' in content
    assert 'Shooter' in content
    assert '2 games' in content
    assert 'pp-switch' in content
    assert '{#' not in content and '{%' not in content


def test_themes_tab_renders_tiles(client):
    """`?tab=themes` renders theme tiles from the Theme/ConceptTheme path."""
    _theme_with_games('Horror', n=1)

    content = client.get(reverse('genres_list'), {'tab': 'themes'}).content.decode()

    assert 'pp-gtile' in content
    assert 'Horror' in content
    assert '1 game' in content


def test_switcher_marks_active_tab(client):
    _genre_with_games('Puzzle')

    genres = client.get(reverse('genres_list'), {'tab': 'genres'}).content.decode()
    assert 'data-tab="genres" aria-selected="true"' in genres
    assert 'data-tab="themes" aria-selected="false"' in genres

    themes = client.get(reverse('genres_list'), {'tab': 'themes'}).content.decode()
    assert 'data-tab="themes" aria-selected="true"' in themes


def test_unknown_tab_falls_back_to_genres(client):
    _genre_with_games('Platformer')

    content = client.get(reverse('genres_list'), {'tab': 'bogus'}).content.decode()

    assert 'Platformer' in content
    assert 'data-tab="genres" aria-selected="true"' in content


# ── game_count gate + cover ───────────────────────────────────────────────────────────────────────────────

def test_only_tags_with_games_are_shown(client):
    """A genre with zero games is filtered out (game_count > 0)."""
    _genre_with_games('Has Games')
    GenreFactory(name='Empty Genre', slug='empty-genre')   # no ConceptGenre -> no games

    content = client.get(reverse('genres_list')).content.decode()

    assert 'Has Games' in content
    assert 'Empty Genre' not in content


def test_tile_renders_materialized_cover(client):
    """After recompute_tag_covers materializes a tag's representative_game, its tile shows that game's
    display_image_url (here the title_image), not the empty placeholder."""
    _genre_with_games('Racing', title_image='https://example.com/cover.jpg')
    call_command('recompute_tag_covers')

    content = client.get(reverse('genres_list')).content.decode()

    assert 'pp-gtile__art' in content
    assert 'https://example.com/cover.jpg' in content
    assert 'pp-gtile__art--empty' not in content


def test_tile_shows_placeholder_before_recompute(client):
    """Before a tag has been recomputed (representative_game null), the tile shows the placeholder rather
    than erroring -- the graceful pre-materialization / brand-new-tag state."""
    _genre_with_games('Fresh', title_image='https://example.com/x.jpg')

    content = client.get(reverse('genres_list')).content.decode()

    assert 'pp-gtile__art--empty' in content


# ── recompute_tag_covers: contract preference / fallback / stability ──────────────────────────────────────

def _contract_game_in(genre, igdb_id, **game_kwargs):
    """A genre member game whose concept keys a live Contract (so it's a 'contract game')."""
    from trophies.models import Contract
    game = GameFactory(**game_kwargs)
    IGDBMatchFactory(concept=game.concept, igdb_id=igdb_id)
    Contract.objects.create(name=f'Contract {igdb_id}', slug=f'contract-{igdb_id}', igdb_id=igdb_id, is_live=True)
    ConceptGenreFactory(concept=game.concept, genre=genre)
    return game


def test_recompute_prefers_contract_game(client):
    """A contract game wins the cover even when a non-contract member exists."""
    genre = GenreFactory(name='Shooter', slug='shooter')
    plain = GameFactory(title_image='https://example.com/plain.jpg')
    ConceptGenreFactory(concept=plain.concept, genre=genre)
    contract_game = _contract_game_in(genre, igdb_id=55501, title_image='https://example.com/contract.jpg')

    call_command('recompute_tag_covers')
    genre.refresh_from_db()

    assert genre.representative_game_id == contract_game.id


def test_recompute_falls_back_to_member_without_contract(client):
    """A tag with no contract games still gets a cover (the most-recent member with art)."""
    genre = _genre_with_games('Puzzle', title_image='https://example.com/p.jpg')

    call_command('recompute_tag_covers')
    genre.refresh_from_db()

    assert genre.representative_game_id is not None


def test_recompute_is_stable_across_runs(client):
    """Re-running the recompute does not reshuffle a tag's cover (stable per-tag pick = premium, not random)."""
    genre = GenreFactory(name='Action', slug='action')
    _contract_game_in(genre, igdb_id=55601, title_image='https://example.com/a.jpg')
    _contract_game_in(genre, igdb_id=55602, title_image='https://example.com/b.jpg')

    call_command('recompute_tag_covers')
    genre.refresh_from_db()
    first = genre.representative_game_id

    call_command('recompute_tag_covers')
    genre.refresh_from_db()

    assert genre.representative_game_id == first


# ── Search + sort ─────────────────────────────────────────────────────────────────────────────────────────

def test_search_narrows(client):
    _genre_with_games('Adventure')
    _genre_with_games('Strategy')

    content = client.get(reverse('genres_list'), {'query': 'Adv'}).content.decode()

    assert 'Adventure' in content
    assert 'Strategy' not in content


def test_sort_by_games_orders_desc(client):
    _genre_with_games('Small', n=1)
    _genre_with_games('Big', n=3)

    content = client.get(reverse('genres_list'), {'sort': 'games'}).content.decode()

    assert content.index('Big') < content.index('Small')


def test_stat_players_counts_unique_profiles(client):
    """The 'Most Players' stat counts distinct PROFILES, not ProfileGame rows -- one hunter owning two games
    in the genre is one player, not two."""
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory
    from trophies.views.genre_views import GenreThemeListView

    genre = GenreFactory(name='Co-op', slug='co-op')
    g1, g2 = GameFactory(), GameFactory()
    ConceptGenreFactory(concept=g1.concept, genre=genre)
    ConceptGenreFactory(concept=g2.concept, genre=genre)
    player = ProfileFactory()
    ProfileGameFactory(profile=player, game=g1)
    ProfileGameFactory(profile=player, game=g2)   # same player owns BOTH games in the genre

    req = RequestFactory().get(reverse('genres_list'), {'tab': 'genres', 'sort': 'players'})
    req.user = AnonymousUser()
    view = GenreThemeListView()
    view.request = req
    view.kwargs = {}
    item = view.get_queryset().get(pk=genre.pk)

    assert item.stat_players == 1


# ── HtmxListMixin partial / XHR guard ─────────────────────────────────────────────────────────────────────

def test_xhr_returns_rows_partial(client):
    _genre_with_games('Fighting')

    resp = client.get(reverse('genres_list'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    templates = {t.name for t in resp.templates if t.name}

    assert resp.status_code == 200
    assert GRID_PARTIAL in templates
    assert FULL_PAGE not in templates
    assert 'data-result-count' in resp.content.decode()


# ── Bounded query count ───────────────────────────────────────────────────────────────────────────────────

def test_query_count_is_bounded(client, django_assert_max_num_queries):
    """Rendering the whole tab (with per-tag cover + count subqueries) stays a small fixed number of queries
    regardless of how many genres there are -- the subqueries ride the single list query, not per-card."""
    for i in range(15):
        _genre_with_games(f'Genre {i}')

    with django_assert_max_num_queries(12):
        resp = client.get(reverse('genres_list'))
    assert resp.status_code == 200
