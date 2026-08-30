"""Tests for the rebuilt Genre/Theme DETAIL page (GenreDetailView / ThemeDetailView, /genres|themes/<slug>/).

Covers the from-scratch rebuild: the hero header + stats, the shared card context (pursuer hooks) via
build_game_card_context, the deferred IGDB blob, the Browse filter/sort pipeline scoped to the tag, the
HtmxListMixin partial/XHR guard, and the materialized related-tags rail (co-occurrence in recompute_tag_covers).
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.test import RequestFactory
from django.urls import reverse

from tests.factories import (
    GenreFactory,
    ConceptGenreFactory,
    GameFactory,
    IGDBMatchFactory,
    BadgeSeriesFactory,
    GroupBadgeFactory,
    PlatformGroupFactory,
    StageFactory,
)

pytestmark = pytest.mark.django_db

GRID_PARTIAL = 'trophies/partials/tag_detail/browse_results.html'
FULL_PAGE = 'trophies/tag_detail.html'


def _genre(name, n=1, **game_kwargs):
    genre = GenreFactory(name=name, slug=name.lower().replace(' ', '-'))
    # The condensed card titles by CONCEPT (IA phase 3) -- keep the concept name in step with
    # the list name so title pins keep meaning what they say.
    if 'title_name' in game_kwargs:
        game_kwargs.setdefault('concept__unified_title', game_kwargs['title_name'])
    games = []
    for _ in range(n):
        game = GameFactory(**game_kwargs)
        ConceptGenreFactory(concept=game.concept, genre=genre)
        games.append(game)
    return genre, games


def _url(genre):
    return reverse('genre_detail', kwargs={'slug': genre.slug})


# ── Rendering ─────────────────────────────────────────────────────────────────────────────────────────────

def test_genre_detail_renders_hero_and_grid(client):
    genre, _ = _genre('Shooter', n=2, title_name='Bang Bang', title_platform=['PS5'])

    resp = client.get(_url(genre))
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'border-l-primary' in content      # rebuilt accented header
    assert 'Shooter' in content
    assert 'pp-gcard' in content              # shared game card grid
    assert 'Bang Bang' in content
    assert 'tagd-sentinel' in content         # infinite-scroll sentinel
    assert '{#' not in content and '{%' not in content   # no leaked template syntax


def test_theme_detail_renders(client):
    from trophies.models import Theme, ConceptTheme
    theme = Theme.objects.create(igdb_id=7001, name='Horror', slug='horror')
    game = GameFactory(title_name='Spooky', title_platform=['PS5'], concept__unified_title='Spooky')
    ConceptTheme.objects.create(concept=game.concept, theme=theme)

    content = client.get(reverse('theme_detail', kwargs={'slug': 'horror'})).content.decode()

    assert 'border-l-primary' in content
    assert 'Horror' in content
    assert 'Spooky' in content


def test_unknown_slug_404s(client):
    resp = client.get(reverse('genre_detail', kwargs={'slug': 'does-not-exist'}))
    assert resp.status_code == 404


def test_tag_grid_condenses_to_one_card_per_page_identity(client):
    """Tag pages run the identical condensed pipeline (IA phase 3, owner-approved): split
    concepts sharing a trusted igdb id -> one card, linking the concept Game page. The tag
    filter scopes the ELECTION POPULATION, same as every other pre-election filter."""
    from tests.factories import IGDBMatchFactory

    genre = GenreFactory(name='Racing', slug='racing')
    a = GameFactory(title_name='Drift A', title_platform=['PS5'], played_count=50,
                    concept__unified_title='Drift Work')
    b = GameFactory(title_name='Drift B', title_platform=['PS5'], played_count=5)
    shared = 81001
    IGDBMatchFactory(concept=a.concept, igdb_id=shared)
    IGDBMatchFactory(concept=b.concept, igdb_id=shared)
    ConceptGenreFactory(concept=a.concept, genre=genre)
    ConceptGenreFactory(concept=b.concept, genre=genre)

    resp = client.get(reverse('genre_detail', kwargs={'slug': 'racing'}), {'platform': 'PS5'})
    content = resp.content.decode()

    assert content.count('pp-gcard__title') == 1
    assert 'Drift Work' in content
    assert f'href="/games/{shared}/"' in content, 'the condensed tag card must link the Game page'
    assert '2 lists' in content
    # ISOLATION: the Trophy Lists page's list-identity mode never reaches the tag grids --
    # no observed-name titling, no region chips (the third-consumer ban).
    assert 'list_identity_cards' not in resp.context
    assert 'pp-gcard__region' not in content


def test_cards_get_pursuer_hooks(client):
    """The cards render the shared pursuer band (build_game_card_context sets show_game_hooks) -- the old
    hand-built rating/user maps omitted it."""
    genre = GenreFactory(name='RPG', slug='rpg')
    game = GameFactory(title_name='Questy', title_platform=['PS5'])
    ConceptGenreFactory(concept=game.concept, genre=genre)
    stage = StageFactory(series_slug='rpg-series')
    stage.concepts.add(game.concept)
    series = BadgeSeriesFactory(series_slug='rpg-series', name='RPG Legends', badge_type='franchise')
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)

    content = client.get(_url(genre)).content.decode()

    assert 'RPG Legends' in content        # badge series band on the card
    assert 'No contract' in content        # contract placeholder -> band is present


# ── Filters / sort ────────────────────────────────────────────────────────────────────────────────────────

def test_active_filter_chips_keep_tag_scope(client):
    """Removing a filter chip / Clear all must resolve against the tag detail path, not eject to Browse Games
    (the tag lives in the URL path, so a /games/?... link would silently drop the genre)."""
    genre, _ = _genre('Strategy', title_platform=['PS5'])

    content = client.get(_url(genre), {'letter': 'A'}).content.decode()

    assert 'pp-gbrowse__achip' in content                       # a filter chip rendered (Starts with A)
    assert 'href="' + _url(genre) + '?' in content              # chip/Clear-all resolve to the genre path
    assert 'href="' + reverse('games_list') + '?' not in content  # never back to Browse Games


def test_platform_filter_narrows(client):
    genre = GenreFactory(name='Puzzle', slug='puzzle')
    ps5 = GameFactory(title_name='PS5 Puzzle', title_platform=['PS5'], concept__unified_title='PS5 Puzzle')
    ps4 = GameFactory(title_name='PS4 Puzzle', title_platform=['PS4'], concept__unified_title='PS4 Puzzle')
    ConceptGenreFactory(concept=ps5.concept, genre=genre)
    ConceptGenreFactory(concept=ps4.concept, genre=genre)

    content = client.get(_url(genre), {'platform': 'PS4'}).content.decode()

    assert 'PS4 Puzzle' in content
    assert 'PS5 Puzzle' not in content


# ── HtmxListMixin partial / XHR ───────────────────────────────────────────────────────────────────────────

def test_xhr_returns_rows_partial(client):
    genre, _ = _genre('Racing', title_platform=['PS5'])

    resp = client.get(_url(genre), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    templates = {t.name for t in resp.templates if t.name}

    assert resp.status_code == 200
    assert GRID_PARTIAL in templates
    assert FULL_PAGE not in templates
    assert 'data-result-count' in resp.content.decode()


# ── Whale-safety ──────────────────────────────────────────────────────────────────────────────────────────

def test_raw_response_is_deferred():
    from trophies.views.genre_views import GenreDetailView

    genre = GenreFactory(name='Fighting', slug='fighting')
    game = GameFactory(title_platform=['PS5'])
    IGDBMatchFactory(concept=game.concept)
    ConceptGenreFactory(concept=game.concept, genre=genre)

    req = RequestFactory().get(_url(genre))
    req.user = AnonymousUser()
    view = GenreDetailView()
    view.request = req
    view.genre = genre
    view.kwargs = {'slug': genre.slug}
    first = view.get_queryset().first()

    assert 'raw_response' in first.concept.igdb_match.get_deferred_fields()


def test_query_count_is_bounded(client, django_assert_max_num_queries):
    genre = GenreFactory(name='Action', slug='action')
    for i in range(40):
        game = GameFactory(title_platform=['PS5'], played_count=50)
        ConceptGenreFactory(concept=game.concept, genre=genre)

    # 14 measured post-condensing (final-audit #9: 22 hid the phase's +1 sibling query in
    # slack). 15 = measured + one of headroom, tight enough that a stray per-card query trips it.
    with django_assert_max_num_queries(15):
        resp = client.get(_url(genre))
    assert resp.status_code == 200


# ── Header stats ──────────────────────────────────────────────────────────────────────────────────────────

def test_header_stats_aggregate(client):
    genre = GenreFactory(name='Sports', slug='sports')
    for pc, pe in ((100, 5), (60, 3)):
        game = GameFactory(title_platform=['PS5'], played_count=pc, plats_earned_count=pe)
        ConceptGenreFactory(concept=game.concept, genre=genre)

    content = client.get(_url(genre)).content.decode()

    # 2 games; owned = 160; platinums = 8 (aggregated over the denormed columns).
    assert 'data-countup="2"' in content     # games
    assert 'data-countup="160"' in content   # owned (sum played_count)
    assert 'data-countup="8"' in content      # platinums (sum plats_earned_count)


# ── Related-tags rail (materialized co-occurrence) ────────────────────────────────────────────────────────

def test_recompute_materializes_related_tags(client):
    a = GenreFactory(name='Shooter', slug='shooter')
    b = GenreFactory(name='Adventure', slug='adventure')
    for _ in range(2):   # two games in BOTH genres -> mutual overlap
        game = GameFactory(title_platform=['PS5'])
        ConceptGenreFactory(concept=game.concept, genre=a)
        ConceptGenreFactory(concept=game.concept, genre=b)

    call_command('recompute_tag_covers')
    a.refresh_from_db()
    b.refresh_from_db()

    assert 'adventure' in a.related_tags
    assert 'shooter' in b.related_tags


def test_related_rail_renders(client):
    a = GenreFactory(name='Shooter', slug='shooter')
    b = GenreFactory(name='Adventure', slug='adventure')
    for _ in range(2):
        game = GameFactory(title_platform=['PS5'])
        ConceptGenreFactory(concept=game.concept, genre=a)
        ConceptGenreFactory(concept=game.concept, genre=b)
    call_command('recompute_tag_covers')

    content = client.get(reverse('genre_detail', kwargs={'slug': 'shooter'})).content.decode()

    assert 'pp-related' in content           # the rail section
    assert 'Adventure' in content            # the related tag tile


def test_no_related_rail_before_recompute(client):
    """A tag with no materialized related_tags simply omits the rail (no error)."""
    genre, _ = _genre('Standalone', title_platform=['PS5'])

    content = client.get(_url(genre)).content.decode()

    assert 'pp-related' not in content
