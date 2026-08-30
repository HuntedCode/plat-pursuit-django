"""Tests for the rebuilt Recently Added page (RecentlyAddedView, /games/recently-added/).

Covers the data/behavior contract the from-scratch rebuild preserved: the two-category switcher (New Games /
New DLC), the base-games grid rendering the shared `.pp-gcard` WITH the pursuer hooks (proving the extracted
build_game_card_context helper is wired in, at parity with Browse), the DLC grid rendering the `.pp-gcard--dlc`
sibling, the HtmxListMixin partial/XHR guard, the deferred IGDB raw_response blob, and whale-safe query bounds.
"""

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    BadgeSeriesFactory,
    GroupBadgeFactory,
    PlatformGroupFactory,
    GameFactory,
    IGDBMatchFactory,
    StageFactory,
)

pytestmark = pytest.mark.django_db

GRID_PARTIAL = 'trophies/partials/recently_added/browse_results.html'
FULL_PAGE = 'trophies/recently_added.html'


def _live_badge_series(slug, name, badge_type='series'):
    """A badge SERIES with one live group badge -- the shape the card badge-band reads."""
    series = BadgeSeriesFactory(series_slug=slug, name=name, badge_type=badge_type)
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)
    return series


def _dlc_group(game, group_id='001', name='Episode One', gold=1, silver=2, bronze=3):
    from trophies.models import TrophyGroup
    return TrophyGroup.objects.create(
        game=game, trophy_group_id=group_id, trophy_group_name=name,
        defined_trophies={'gold': gold, 'silver': silver, 'bronze': bronze, 'platinum': 0},
    )


def _view_queryset(category='base_games', **params):
    """Drive RecentlyAddedView.get_queryset directly (to inspect deferred fields / ordering)."""
    from trophies.views.game_views import RecentlyAddedView
    req = RequestFactory().get(reverse('recently_added'), {'category': category, **params})
    req.user = AnonymousUser()
    v = RecentlyAddedView()
    v.request = req
    v.kwargs = {}
    return v.get_queryset()


# ── Rendering + switcher ──────────────────────────────────────────────────────────────────────────────────

def test_defaults_to_base_games_and_renders_grid(client):
    """No category param defaults to New Games: the shared `.pp-gcard` grid + the segmented switcher render,
    and no raw Django comment markers leak (multi-line {# #} is NOT a comment and would ship as text)."""
    GameFactory(title_name='Fresh Arrival', title_platform=['PS5'])

    resp = client.get(reverse('recently_added'))
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'pp-gcard' in content
    assert 'Fresh Arrival' in content
    assert 'pp-switch' in content                       # the category switcher
    assert 'radded-sentinel' in content                 # infinite-scroll sentinel
    assert '{#' not in content and '{%' not in content   # no leaked template syntax


def test_switcher_marks_active_category(client):
    """The switcher chip for the active category carries is-active + aria-selected=true; the other does not."""
    GameFactory(title_platform=['PS5'])

    base = client.get(reverse('recently_added'), {'category': 'base_games'}).content.decode()
    assert 'data-category="base_games" aria-controls="ra-view" aria-selected="true"' in base
    assert 'data-category="dlc" aria-controls="ra-view" aria-selected="false"' in base

    dlc = client.get(reverse('recently_added'), {'category': 'dlc'}).content.decode()
    assert 'data-category="dlc" aria-controls="ra-view" aria-selected="true"' in dlc


def test_unknown_category_falls_back_to_base_games(client):
    """An unknown ?category value normalizes to New Games (not a broken empty page)."""
    GameFactory(title_name='Fallback Game', title_platform=['PS5'])

    resp = client.get(reverse('recently_added'), {'category': 'bogus'})
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'Fallback Game' in content
    assert 'data-category="base_games" aria-controls="ra-view" aria-selected="true"' in content


def test_recently_added_stays_per_list_and_uncondensed(client):
    """Browse Games condensed to one card per page identity (IA phase 3); Recently Added did NOT
    -- a new sibling list appearing is this page's whole point. Two sibling lists = two cards,
    each linking its OWN List detail, and none of the condensed maps reach the context."""
    a = GameFactory(title_name='Fresh EU', title_platform=['PS5'])
    b = GameFactory(concept=a.concept, title_name='Fresh NA', title_platform=['PS5'])

    resp = client.get(reverse('recently_added'))
    content = resp.content.decode()

    assert 'Fresh EU' in content and 'Fresh NA' in content
    assert f'href="/games/{a.np_communication_id}/"' in content
    assert f'href="/games/{b.np_communication_id}/"' in content
    assert 'condensed_cards' not in resp.context
    assert 'list_count_map' not in resp.context
    # Nor the Trophy Lists page's list-identity mode: RA titles by title_name, not observations,
    # and its cards carry no region chips.
    assert 'list_identity_cards' not in resp.context
    assert 'pp-gcard__region' not in content


def test_base_cards_get_pursuer_hooks(client):
    """Base-games cards render the shared card's pursuer band (badge series + contract placeholder), proving
    build_game_card_context is wired in -- the whole point of the reuse (legacy Recently Added lacked it)."""
    game = GameFactory(title_name='Hooked Arrival', title_platform=['PS5'])
    stage = StageFactory(series_slug='arr-series')
    stage.concepts.add(game.concept)
    _live_badge_series('arr-series', 'Arrival Franchise', 'franchise')

    content = client.get(reverse('recently_added')).content.decode()

    assert 'Arrival Franchise' in content        # badge series name on the card
    assert 'pp-gcard__badges-n' in content        # the real count element (not just the placeholder)
    assert 'No contract' in content               # contract placeholder (game has none) -> band is present


# ── DLC category ──────────────────────────────────────────────────────────────────────────────────────────

def test_dlc_category_renders_dlc_cards(client):
    """`?category=dlc` renders the `.pp-gcard--dlc` sibling: the DLC cover tag, the pack name, its parent
    game, and the summed trophy count. The 'default' base group is never shown as DLC."""
    game = GameFactory(title_name='Parent Game', title_platform=['PS5'])
    _dlc_group(game, group_id='001', name='Secret Ending', gold=1, silver=2, bronze=3)
    from trophies.models import TrophyGroup
    TrophyGroup.objects.create(game=game, trophy_group_id='default', trophy_group_name='Base Game')

    content = client.get(reverse('recently_added'), {'category': 'dlc'}).content.decode()

    assert 'pp-gcard--dlc' in content
    assert 'Secret Ending' in content            # the pack name
    assert 'Parent Game' in content              # its parent game (subtitle)
    assert '6 trophies' in content               # 1 + 2 + 3, summed safely in the template
    assert 'Base Game' not in content            # the 'default' base group is excluded from DLC


def test_dlc_empty_state(client):
    """With no DLC packs, the DLC category shows its own empty state (not the base-games one)."""
    GameFactory(title_platform=['PS5'])   # a base game exists, but no DLC groups

    content = client.get(reverse('recently_added'), {'category': 'dlc'}).content.decode()

    assert 'No new DLC found' in content


# ── 30-day window ─────────────────────────────────────────────────────────────────────────────────────────

def test_excludes_entries_older_than_window(client):
    """Only entries discovered within the 30-day window appear (base games AND DLC); older ones are excluded,
    so the grid matches the header's 'last 30 days' stat. (created_at is auto_now_add -> set via .update.)"""
    from trophies.models import Game, TrophyGroup

    fresh = GameFactory(title_name='Just Landed', title_platform=['PS5'])
    old = GameFactory(title_name='Ancient Add', title_platform=['PS5'])
    Game.objects.filter(id=old.id).update(created_at=timezone.now() - timedelta(days=45))

    base = client.get(reverse('recently_added')).content.decode()
    assert 'Just Landed' in base
    assert 'Ancient Add' not in base

    _dlc_group(fresh, group_id='001', name='Fresh Pack')
    stale = _dlc_group(old, group_id='002', name='Stale Pack')
    TrophyGroup.objects.filter(id=stale.id).update(created_at=timezone.now() - timedelta(days=45))

    dlc = client.get(reverse('recently_added'), {'category': 'dlc'}).content.decode()
    assert 'Fresh Pack' in dlc
    assert 'Stale Pack' not in dlc


# ── Filters + sort ────────────────────────────────────────────────────────────────────────────────────────

def test_platform_filter_narrows(client):
    GameFactory(title_name='PS5 Only', title_platform=['PS5'])
    GameFactory(title_name='PS4 Only', title_platform=['PS4'])

    content = client.get(reverse('recently_added'), {'platform': 'PS4'}).content.decode()

    assert 'PS4 Only' in content
    assert 'PS5 Only' not in content


# ── HtmxListMixin partial / XHR guard ─────────────────────────────────────────────────────────────────────

def test_xhr_returns_rows_partial(client):
    """The InfiniteScroller's XHR (X-Requested-With) gets the rows-only partial, not the full page."""
    GameFactory(title_name='Scroll Target', title_platform=['PS5'])

    resp = client.get(reverse('recently_added'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    templates = {t.name for t in resp.templates if t.name}

    assert resp.status_code == 200
    assert GRID_PARTIAL in templates
    assert FULL_PAGE not in templates
    assert 'data-result-count' in resp.content.decode()


def test_category_switch_returns_view_island(client):
    """The New Games/New DLC switcher HTMX-swaps the #ra-view island (toolbar + grid), not the full page --
    dynamic switch, no reload. The category-scoped toolbar re-renders inside it."""
    GameFactory(title_name='Island Game', title_platform=['PS5'])

    resp = client.get(
        reverse('recently_added'), {'category': 'base_games'},
        HTTP_HX_REQUEST='true', HTTP_HX_TARGET='ra-view',
    )
    templates = {t.name for t in resp.templates if t.name}
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'trophies/partials/recently_added/view.html' in templates
    assert FULL_PAGE not in templates
    assert 'radded-form' in content        # toolbar re-rendered inside the island
    assert 'radded-sentinel' in content    # infinite-scroll sentinel rides the island


def test_filter_swap_returns_grid_only(client):
    """A filter/sort change HTMX-swaps only the inner #browse-results grid, not the toolbar island."""
    GameFactory(title_platform=['PS5'])

    resp = client.get(
        reverse('recently_added'), {'sort': 'alpha'},
        HTTP_HX_REQUEST='true', HTTP_HX_TARGET='browse-results',
    )
    templates = {t.name for t in resp.templates if t.name}

    assert resp.status_code == 200
    assert GRID_PARTIAL in templates
    assert 'trophies/partials/recently_added/view.html' not in templates
    assert FULL_PAGE not in templates


# ── Whale-safety: deferred blob + bounded queries ─────────────────────────────────────────────────────────

def test_raw_response_is_deferred_both_categories():
    """The ~30 KB IGDB raw_response blob is deferred off both category querysets (never read by the cards)."""
    game = GameFactory(title_platform=['PS5'])
    IGDBMatchFactory(concept=game.concept)
    _dlc_group(game)

    base_first = _view_queryset('base_games').first()
    assert 'raw_response' in base_first.concept.igdb_match.get_deferred_fields()

    dlc_first = _view_queryset('dlc').first()
    assert 'raw_response' in dlc_first.game.concept.igdb_match.get_deferred_fields()


def test_query_count_is_whale_safe(client, django_assert_max_num_queries):
    """One page of base-games cards costs a bounded number of queries regardless of catalogue size, INCLUDING
    the batched badge + contract pursuer-hook maps (never per-card)."""
    games = GameFactory.create_batch(60, title_platform=['PS5'], played_count=100)
    stage = StageFactory(series_slug='whale-arr')
    _live_badge_series('whale-arr', 'Whale Arrival')
    for g in games[:5]:
        stage.concepts.add(g.concept)

    with django_assert_max_num_queries(20):
        resp = client.get(reverse('recently_added'))
    assert resp.status_code == 200
