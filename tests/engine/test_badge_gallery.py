"""Tests for the Browse badge Gallery -- the per-tier medallion wall at /badges/?view=gallery.

The catalog-discovery cousin of the Collection album: public, server-paginated, SHOWCASE-first
(every medallion in full earned colour), with a logged-in viewer's ownership shown as a card-corner
marker. These pin the view contract: DB-side tier/state/hide-owned filters, showcase rendering, the
public (non-modal) detail link, and whale-safe constant per-page query count.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from trophies.models import UserBadge
from tests.factories import (
    BadgeFactory, ProfileFactory, UserBadgeFactory, UserBadgeProgressFactory,
)

pytestmark = pytest.mark.django_db

GALLERY = reverse('badges_list')


def _series(slug, tiers=(1, 2, 3, 4)):
    return [
        BadgeFactory(series_slug=slug, tier=t, badge_type='series',
                     is_live=True, required_stages=5, display_series=slug)
        for t in tiers
    ]


def test_gallery_renders_showcase_wall_linking_to_detail(client):
    """Anonymous: the Gallery renders the medallion wall, every medallion in full 'earned' showcase colour,
    each card linking to the public badge_detail page (never the login-gated collection modal)."""
    _series('rs-gal')

    resp = client.get(GALLERY, {'view': 'gallery'})
    html = resp.content.decode()

    assert resp.status_code == 200
    assert 'pp-bgal' in html and 'pp-med' in html                 # the gallery island + medallions
    assert html.count('data-bgal-cell') == 4                      # one cell per tier
    assert 'data-state="earned"' in html                          # showcase-first: full colour for everyone
    assert '/badges/rs-gal/' in html                              # cards tap through to the series detail page
    assert 'collection_badge_modal' not in html and '/collection/badge/' not in html  # not the gated modal
    assert 'pp-bgal__owned' not in html                           # anonymous -> no ownership markers


def test_gallery_tier_filter_is_db_side(client):
    """?tier=bronze returns only that tier's badges (a real DB filter, not client-side)."""
    _series('rs-tier')

    html = client.get(GALLERY, {'view': 'gallery', 'tier': 'bronze'}).content.decode()

    assert html.count('data-bgal-cell') == 1
    assert 'data-tier="bronze"' in html
    assert 'data-tier="silver"' not in html


def test_gallery_search_matches_series(client):
    _series('elden-ring')
    _series('dark-souls')

    html = client.get(GALLERY, {'view': 'gallery', 'q': 'elden'}).content.decode()

    assert '/badges/elden-ring/' in html
    assert '/badges/dark-souls/' not in html


def test_gallery_anonymous_hides_personal_controls(client):
    """The state filter + hide-owned toggle are auth-only (anonymous browses the pure catalog)."""
    _series('rs-anon')

    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()

    assert 'name="state"' not in html
    assert 'name="hide_owned"' not in html


def test_gallery_owned_marker_for_earned_badge(client):
    """A logged-in viewer's earned badge gets a card-corner ownership marker; the medallion stays showcase."""
    profile = ProfileFactory()
    badges = _series('rs-own')
    UserBadgeFactory(profile=profile, badge=badges[0])  # earn bronze
    client.force_login(profile.user)

    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()

    assert 'pp-bgal__owned--earned' in html          # the earned cell shows the owned check
    assert html.count('data-state="earned"') >= 4    # ... but ALL medallions still render in showcase colour


def test_gallery_state_earned_filter_via_exists(client):
    """?state=earned returns only badges the viewer holds (DB EXISTS subquery)."""
    profile = ProfileFactory()
    badges = _series('rs-state')
    UserBadgeFactory(profile=profile, badge=badges[0])  # only bronze earned
    client.force_login(profile.user)

    html = client.get(GALLERY, {'view': 'gallery', 'state': 'earned'}).content.decode()

    assert html.count('data-bgal-cell') == 1
    assert 'pp-bgal__owned--earned' in html


def test_gallery_hide_owned_excludes_held(client):
    profile = ProfileFactory()
    badges = _series('rs-hide')
    UserBadgeFactory(profile=profile, badge=badges[0])  # bronze held
    client.force_login(profile.user)

    html = client.get(GALLERY, {'view': 'gallery', 'hide_owned': '1'}).content.decode()

    assert html.count('data-bgal-cell') == 3         # the 3 unheld tiers remain
    assert 'pp-bgal__owned--earned' not in html      # the held one is gone


def test_gallery_query_count_constant_regardless_of_catalog_size(client):
    """Whale-safety: rendering a page of the Gallery over a MUCH larger catalog issues the same number of
    queries (no per-badge N+1 -- the frames are batch-built with include_live_stats=False)."""
    _series('rs-base-a')
    _series('rs-base-b')
    client.get(GALLERY, {'view': 'gallery'})  # warm caches

    with CaptureQueriesContext(connection) as small:
        client.get(GALLERY, {'view': 'gallery'})

    for i in range(20):  # 20 more series x 4 tiers -> a page still shows GALLERY_PAGE_SIZE at most
        _series(f'rs-more-{i}')

    with CaptureQueriesContext(connection) as large:
        client.get(GALLERY, {'view': 'gallery'})

    assert len(large) == len(small)      # no growth with catalog size
    assert len(small) < 20               # absolute ceiling: a per-card N+1 (a page is 48 cards) can't hide


def test_gallery_view_defaults_to_series(client):
    """No ?view (or view != gallery) keeps the existing per-series Series view, not the medallion wall."""
    _series('rs-default')

    html = client.get(GALLERY).content.decode()

    assert 'pp-bgal' not in html  # series view, not the gallery island


def test_gallery_badge_type_filter(client):
    """?badge_type filters to that type (per-tier, across every series of the type)."""
    _series('rs-type')  # badge_type='series'
    BadgeFactory(series_slug='fr-type', tier=1, badge_type='franchise',
                 is_live=True, required_stages=5, display_series='fr-type')

    html = client.get(GALLERY, {'view': 'gallery', 'badge_type': 'franchise'}).content.decode()

    assert '/badges/fr-type/' in html
    assert '/badges/rs-type/' not in html


def test_gallery_card_markers_for_maintenance_and_in_progress(client):
    """The card-corner marker reads owned_state: a held (incl. lapsed/maintenance) badge shows the green
    check, an in-progress one shows the hollow ring. The medallions all stay showcase colour."""
    profile = ProfileFactory()
    badges = _series('rs-mark')
    ub = UserBadgeFactory(profile=profile, badge=badges[0])
    UserBadge.objects.filter(pk=ub.pk).update(status='maintenance')                   # bronze -> held/lapsed
    UserBadgeProgressFactory(profile=profile, badge=badges[1], completed_concepts=2)  # silver -> in progress
    client.force_login(profile.user)

    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()

    assert 'pp-bgal__owned--earned' in html      # maintenance is still held -> the owned check
    assert 'pp-bgal__owned--progress' in html    # in-progress -> the hollow ring


def test_gallery_full_page_carries_infinite_scroll_hooks(client):
    """The full gallery page emits the InfiniteScroller hooks: the grid + a sentinel + a loader, but NO
    page-number pager (infinite scroll owns pagination)."""
    _series('rs-inf')

    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()

    assert 'id="items-grid"' in html and 'id="bgal-sentinel"' in html and 'id="bgal-loading"' in html
    assert 'page-jump-form' not in html  # the page-number pager (pagination.html) is gone


def test_gallery_xhr_page_returns_bare_card_grid(client):
    """An InfiniteScroller ?page=N XHR fetch returns just the results partial (cards), not the full page
    chrome, so the scroller can extract + append the .pp-bgal__card nodes."""
    _series('rs-xhr')

    resp = client.get(GALLERY, {'view': 'gallery', 'page': 1}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    html = resp.content.decode()

    assert 'pp-bgal__card' in html          # the cards are there to append
    assert 'pp-bgal__toolbar' not in html   # ... but not the toolbar/full-page shell


def test_gallery_xhr_past_the_end_returns_no_cards(client):
    """Paginator.get_page CLAMPS an out-of-range page; the view must instead emit ZERO cards for an XHR
    fetch past the last page, so InfiniteScroller sees 0 and stops (rather than re-appending the last page
    forever)."""
    _series('rs-end')  # 4 badges -> a single page

    resp = client.get(GALLERY, {'view': 'gallery', 'page': 2}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    html = resp.content.decode()

    assert 'pp-bgal__card' not in html  # past the end -> nothing to append -> the scroller stops
