"""Tests for the /badges/ Series view -- the per-series TILE grid (the default, non-gallery mode).

Each tile (.pp-scard) is one badge SERIES rendered as its whole tier LADDER: four nodes (earned / active /
locked) that double as a face selector, the selected tier's medallion + stat pane, and community meta.
These pin the rebuilt contract: the tile markup + ladder, the server-chosen default face (the tier you're
working on -> Bronze if unstarted -> top tier if finished), the multi-select badge_type/completion_status
filters (OR'd), the infinite-scroll partial for XHR page fetches (+ a past-end fetch returning nothing).
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tests.factories import BadgeFactory, ProfileFactory, UserBadgeFactory

pytestmark = pytest.mark.django_db

SERIES = reverse('badges_list')


def _series(slug, badge_type='series', tiers=(1, 2, 3, 4)):
    return [
        BadgeFactory(series_slug=slug, tier=t, badge_type=badge_type,
                     is_live=True, required_stages=5, display_series=slug)
        for t in tiers
    ]


def test_series_renders_tile_with_tier_ladder(client):
    """The default view renders the tile grid: each tile is a .pp-scard with a four-node tier ladder and a
    detail link."""
    _series('rs-series')

    resp = client.get(SERIES)
    html = resp.content.decode()

    assert resp.status_code == 200
    assert 'pp-scard' in html                       # the tile (not the old row / DaisyUI card)
    assert 'pp-scard__ladder' in html               # the tier ladder centrepiece
    assert html.count('pp-scard__dot') == 4         # one node per tier (4-tier series); dot isn't referenced in JS
    assert 'pp-scard__arrow' in html                # prev/next face-swap arrows
    assert 'pp-scard__trophies' in html             # per-tier trophy spread in the face
    assert 'pp-med' in html                         # uses the shared medallion object (not the flat badge)
    assert html.count('pp-med__stage') == 1         # ONE medallion per tile (retinted on swap), not one per tier
    assert 'earned' in html                         # type . N earned line
    assert '/badges/rs-series/' in html
    assert 'pp-vtoggle' in html                     # shared Series|Gallery toggle present


def test_series_tile_emits_tier_name_for_css_accent(client):
    """The tile's data-tier must be a tier NAME (bronze/silver/gold/platinum) so series-list.css's
    [data-tier="..."] --tier-c accents match -- not a DaisyUI semantic (warning/secondary/...). An
    unstarted 4-tier series defaults to the Bronze face."""
    _series('rs-tiername')

    html = client.get(SERIES).content.decode()

    assert 'data-tier="bronze"' in html
    for semantic in ('data-tier="warning"', 'data-tier="secondary"', 'data-tier="error"', 'data-tier="primary"'):
        assert semantic not in html


def test_series_default_face_is_the_working_tier(client):
    """Resting face = the tier you're working on (the lowest unearned). Earn Bronze -> the tile defaults to
    the Silver face (data-tier='silver')."""
    profile = ProfileFactory()
    badges = _series('rs-working')
    UserBadgeFactory(profile=profile, badge=badges[0])   # Bronze earned -> working on Silver
    client.force_login(profile.user)

    html = client.get(SERIES).content.decode()

    assert 'data-tier="silver"' in html
    assert 'pp-scard__node is-earned' in html            # the Bronze node reads earned
    assert 'pp-scard__seal' in html                      # ... and the earned face carries the seal


def test_series_default_face_finished_is_top_tier(client):
    """A finished series (all tiers earned) defaults to the top tier's face (data-tier='platinum')."""
    profile = ProfileFactory()
    for b in _series('rs-fin'):
        UserBadgeFactory(profile=profile, badge=b)
    client.force_login(profile.user)

    html = client.get(SERIES).content.decode()

    assert 'data-tier="platinum"' in html


def test_series_anon_sees_neutral_catalog_ladder(client):
    """Anonymous (and logged-in-without-profile) users get NEUTRAL catalog nodes -- no earned/active/locked
    emphasis and no 'Working'/'Locked' progress text, since there's no personal progress to imply."""
    _series('rs-anon')

    html = client.get(SERIES).content.decode()

    assert 'is-catalog' in html
    assert 'pp-scard__node is-active' not in html and 'pp-scard__node is-locked' not in html
    assert 'Working' not in html            # no personal-progress state chips for anon


def test_series_multi_badge_type_filter_ORs(client):
    """?badge_type=series&badge_type=developer returns BOTH (badge_type__in), not just one."""
    _series('rs-a', badge_type='series')
    _series('dev-b', badge_type='developer')
    _series('fr-c', badge_type='franchise')

    html = client.get(SERIES, {'badge_type': ['series', 'developer']}).content.decode()

    assert '/badges/rs-a/' in html
    assert '/badges/dev-b/' in html
    assert '/badges/fr-c/' not in html             # franchise excluded


def test_series_completion_status_multi_filter(client):
    """?completion_status=completed (auth) keeps only fully-earned series; not-started is excluded."""
    profile = ProfileFactory()
    done = _series('rs-done')
    _series('rs-fresh')
    for b in done:
        UserBadgeFactory(profile=profile, badge=b)   # earn every tier -> completed
    client.force_login(profile.user)

    html = client.get(SERIES, {'completion_status': ['completed']}).content.decode()

    assert '/badges/rs-done/' in html
    assert '/badges/rs-fresh/' not in html


def test_series_xhr_returns_bare_tiles_partial(client):
    """An InfiniteScroller page fetch (XHR) returns just the tiles partial -- no page chrome/toolbar."""
    _series('rs-xhr')

    resp = client.get(SERIES, {'page': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    html = resp.content.decode()

    assert 'pp-scard' in html
    assert 'pp-vtoggle' not in html                 # bare partial, no shared chrome
    assert 'id="filter-form"' not in html           # ... and no toolbar form


def test_series_xhr_past_end_returns_no_tiles(client):
    """A page fetch past the last page emits NO tiles so the scroller sees zero and stops (get_page would
    otherwise clamp to the last page and loop it forever)."""
    _series('rs-end')   # one series, far under a full page

    resp = client.get(SERIES, {'page': '9'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    html = resp.content.decode()

    assert '/badges/rs-end/' not in html            # no tile for this series on the past-end page


def test_series_big_tier_falls_back_to_smooth_bar(client):
    """Coherence guard: a tier needing more stages than the segment cap renders the smooth Horizon bar, not
    a row of indistinct slivers; a small tier still segments (one cell per stage)."""
    profile = ProfileFactory()
    BadgeFactory(series_slug='rs-big', tier=1, badge_type='series', is_live=True,
                 required_stages=20, display_series='rs-big')     # > cap -> smooth bar
    BadgeFactory(series_slug='rs-small', tier=1, badge_type='series', is_live=True,
                 required_stages=4, display_series='rs-small')    # <= cap -> 4 segments
    client.force_login(profile.user)

    html = client.get(SERIES).content.decode()

    assert 'pp-horizon__track' in html               # the big tier's smooth fallback bar
    assert html.count('pp-horizon__seg') == 4        # only the small tier segments; the big one does NOT sliver


def test_series_query_count_is_flat(client):
    """Whale-safety: the tile grid builds 4 medallion frames per tile, so it MUST NOT N+1 per series/tier.
    Query count stays flat as the catalog grows (frames read only select_related FKs; per-tier trophies +
    stats are single bulk queries)."""
    for i in range(3):
        _series('rs-qs-%d' % i)
    with CaptureQueriesContext(connection) as small:
        client.get(SERIES)

    for i in range(12):
        _series('rs-qb-%d' % i)
    with CaptureQueriesContext(connection) as big:
        client.get(SERIES)

    # 4x more series (and 4x more per-tier frame builds) must not grow the query count meaningfully.
    assert len(big.captured_queries) <= len(small.captured_queries) + 2
    assert len(small.captured_queries) < 25


def test_series_search_filters_by_slug(client):
    _series('elden-ring-series')
    _series('dark-souls-series')

    html = client.get(SERIES, {'series_slug': 'elden'}).content.decode()

    assert '/badges/elden-ring-series/' in html
    assert '/badges/dark-souls-series/' not in html
