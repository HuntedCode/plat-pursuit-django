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


def test_gallery_search_by_set_number(client):
    """A numeric query matches the edition/set number too -- plain, #-prefixed, and zero-padded all work."""
    BadgeFactory(series_slug='rs-sn42', tier=1, badge_type='series', is_live=True,
                 required_stages=5, display_series='rs-sn42', name='Set Search A', set_number=42)
    BadgeFactory(series_slug='rs-sn7', tier=1, badge_type='series', is_live=True,
                 required_stages=5, display_series='rs-sn7', name='Set Search B', set_number=7)

    for query in ('42', '#42', '#0042'):
        html = client.get(GALLERY, {'view': 'gallery', 'q': query}).content.decode()
        assert '/badges/rs-sn42/' in html, query
        assert '/badges/rs-sn7/' not in html, query


def test_gallery_anonymous_hides_personal_state_chips(client):
    """The State chips are auth-only (anonymous browses the pure catalog); tier + set chips still show."""
    _series('rs-anon')

    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()

    assert 'name="state"' not in html                 # no personal-state filter for anon
    assert 'name="tier"' in html                       # ... but the public tier/set chips remain


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


def test_gallery_multi_tier_filter_ORs_the_selections(client):
    """Multi-select: ?tier=silver&tier=gold returns silver AND gold (tier__in), not just one."""
    _series('rs-mt')

    html = client.get(GALLERY, {'view': 'gallery', 'tier': ['silver', 'gold']}).content.decode()

    assert html.count('data-bgal-cell') == 2
    assert 'data-tier="silver"' in html and 'data-tier="gold"' in html
    assert 'data-tier="bronze"' not in html and 'data-tier="platinum"' not in html


def test_gallery_multi_type_filter(client):
    """Multi-select: ?badge_type=series&badge_type=developer returns both types (badge_type__in)."""
    _series('rs-series')  # badge_type='series'
    BadgeFactory(series_slug='dv-one', tier=1, badge_type='developer',
                 is_live=True, required_stages=5, display_series='dv-one')
    BadgeFactory(series_slug='fr-skip', tier=1, badge_type='franchise',
                 is_live=True, required_stages=5, display_series='fr-skip')

    html = client.get(GALLERY, {'view': 'gallery', 'badge_type': ['series', 'developer']}).content.decode()

    assert '/badges/rs-series/' in html and '/badges/dv-one/' in html
    assert '/badges/fr-skip/' not in html  # a non-selected type is excluded


def test_gallery_multi_state_ORs_and_subsumes_hide(client):
    """Multi-select state: ?state=earned&state=unearned shows earned OR unearned, dropping the in-progress
    one (this is how the chase-carving that the old 'hide' toggles did now works -- just don't select it)."""
    profile = ProfileFactory()
    badges = _series('rs-ms')
    UserBadgeFactory(profile=profile, badge=badges[0])                                # bronze earned
    UserBadgeProgressFactory(profile=profile, badge=badges[1], completed_concepts=2)  # silver in progress
    # gold + platinum stay unearned
    client.force_login(profile.user)

    html = client.get(GALLERY, {'view': 'gallery', 'state': ['earned', 'unearned']}).content.decode()

    assert 'pp-bgal__owned--earned' in html          # earned shown
    assert 'pp-bgal__owned--progress' not in html    # in-progress dropped (not selected)
    assert html.count('data-bgal-cell') == 3         # earned bronze + unearned gold + platinum


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

    # Series view, not the medallion wall. (Both views now share the .pp-bgal__ TOOLBAR classes, so key on
    # the gallery-only grid/cells vs. the Series row.)
    assert 'data-bgal-cell' not in html and 'pp-bgal__grid' not in html
    assert 'pp-scard' in html


def test_permanent_chrome_shows_on_the_gallery(client):
    """The tier explainer + the viewer's badge stats are PERMANENT page chrome (in the header) -- they render
    on the Gallery view, not only the Series tab."""
    profile = ProfileFactory()
    badges = _series('rs-chrome')
    UserBadgeFactory(profile=profile, badge=badges[0])   # earn one -> creates the gamification row
    client.force_login(profile.user)

    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()

    assert 'How badges work' in html    # the tier explainer (static, both views)
    assert 'Total XP' in html           # the viewer's badge stats (auth)


def test_gallery_cell_wires_badge_peek(client):
    """Each Gallery cell carries its badge id (so a tap opens the public quick-peek modal) and keeps its
    detail href as the no-JS fallback; the shared modal container is on the page."""
    _series('rs-gpeek')

    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()

    assert 'data-badge-id=' in html       # cell carries the badge id for the peek
    assert 'id="badge-peek"' in html      # the shared modal container
    assert '/badges/rs-gpeek/' in html    # ... and still has the detail href fallback


def test_gallery_badge_type_filter(client):
    """?badge_type filters to that type (per-tier, across every series of the type)."""
    _series('rs-type')  # badge_type='series'
    BadgeFactory(series_slug='fr-type', tier=1, badge_type='franchise',
                 is_live=True, required_stages=5, display_series='fr-type')

    html = client.get(GALLERY, {'view': 'gallery', 'badge_type': 'franchise'}).content.decode()

    assert '/badges/fr-type/' in html
    assert '/badges/rs-type/' not in html


def test_gallery_card_markers_are_distinct_per_owned_state(client):
    """The card-corner marker reads owned_state, with THREE distinct treatments: earned = green check,
    maintenance = red ring (lapsed), in-progress = cyan ring. The medallions all stay showcase colour."""
    profile = ProfileFactory()
    badges = _series('rs-mark')
    UserBadgeFactory(profile=profile, badge=badges[0])                                # bronze -> earned
    ub = UserBadgeFactory(profile=profile, badge=badges[1])
    UserBadge.objects.filter(pk=ub.pk).update(status='maintenance')                   # silver -> held/lapsed
    UserBadgeProgressFactory(profile=profile, badge=badges[2], completed_concepts=2)  # gold -> in progress
    client.force_login(profile.user)

    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()

    assert 'pp-bgal__owned--earned' in html        # earned -> green check
    assert 'pp-bgal__owned--maintenance' in html   # maintenance -> red ring (distinct from both)
    assert 'pp-bgal__owned--progress' in html      # in-progress -> cyan ring


def test_gallery_card_stat_shows_stage_progress(client):
    """The card's second line reads as stage info -- how big the badge is ("N Stages"), and once you're
    working on it, how far you've gotten ("X/N Stages") -- not the medallion's showcase "N / N"."""
    profile = ProfileFactory()
    badges = _series('rs-stat', tiers=(1,))                                           # one badge, required_stages=5
    UserBadgeProgressFactory(profile=profile, badge=badges[0], completed_concepts=2)  # 2 of 5 in progress
    client.force_login(profile.user)

    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()

    assert 'Stages' in html                 # the stat is labelled in stages, not a bare ratio
    assert '2/5 Stages' in html             # in-progress shows the real completed count


def test_gallery_cell_caption_shows_tier_set_type_and_stages(client):
    """The caption carries the full catalog identity across two meta lines: tier + edition/set number on the
    first, badge type + stage count on the second."""
    BadgeFactory(series_slug='rs-setno', tier=1, badge_type='series', is_live=True,
                 required_stages=5, display_series='rs-setno', set_number=42)

    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()

    assert 'Bronze' in html         # tier, in text (the medallion colour alone isn't enough)
    assert '#0042' in html          # the edition/set number is on the card
    assert '5 Stages' in html       # badge type + stage count


def test_gallery_card_name_prefers_franchise(client):
    """The card name uses the broadest grouping: franchise when set, else the series name. A franchise-
    linked badge shows the franchise; an unlinked one falls back to its series_name."""
    from trophies.models import Franchise
    fr = Franchise.objects.create(igdb_id=4242, name='Resident Evil', slug='resident-evil-t',
                                  source_type='franchise')
    BadgeFactory(series_slug='rs-fr', tier=1, badge_type='series', is_live=True,
                 required_stages=5, display_series='RE Village Plat', franchise=fr)
    BadgeFactory(series_slug='rs-nofr', tier=1, badge_type='series', is_live=True,
                 required_stages=5, display_series='Solo Series Name')

    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()

    assert 'Resident Evil' in html          # franchise wins the name slot when present
    assert 'Solo Series Name' in html       # no franchise -> falls back to the series name


def test_gallery_sort_by_set_number(client):
    """?sort=set_number orders by the edition number (a DB sort); unnumbered badges sort last."""
    BadgeFactory(series_slug='rs-sn-hi', tier=1, badge_type='series', is_live=True,
                 required_stages=5, display_series='rs-sn-hi', set_number=9)
    BadgeFactory(series_slug='rs-sn-lo', tier=1, badge_type='series', is_live=True,
                 required_stages=5, display_series='rs-sn-lo', set_number=1)

    html = client.get(GALLERY, {'view': 'gallery', 'sort': 'set_number'}).content.decode()

    assert html.index('/badges/rs-sn-lo/') < html.index('/badges/rs-sn-hi/')  # #0001 before #0009


def test_gallery_set_number_sort_groups_by_type(client):
    """Set numbers restart per type, so the sort groups by TYPE (series before franchise) first -- a
    series #2 still sorts before a franchise #1."""
    BadgeFactory(series_slug='sr-2', tier=1, badge_type='series', is_live=True,
                 required_stages=5, display_series='sr-2', set_number=2)
    BadgeFactory(series_slug='fr-1', tier=1, badge_type='franchise', is_live=True,
                 required_stages=5, display_series='fr-1', set_number=1)

    html = client.get(GALLERY, {'view': 'gallery', 'sort': 'set_number'}).content.decode()

    assert html.index('/badges/sr-2/') < html.index('/badges/fr-1/')  # type group wins over the raw number


def test_gallery_defaults_to_set_order(client):
    """With no ?sort the Gallery defaults to SET ORDER (edition within each type), not name."""
    BadgeFactory(series_slug='dflt-a', tier=1, badge_type='series', is_live=True,
                 required_stages=5, display_series='dflt-a', name='AAA sorts first by name', set_number=9)
    BadgeFactory(series_slug='dflt-z', tier=1, badge_type='series', is_live=True,
                 required_stages=5, display_series='dflt-z', name='ZZZ sorts last by name', set_number=1)

    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()

    # Name order and set order DISAGREE (AAA=#9, ZZZ=#1); set #1 first proves the set-order default.
    assert html.index('/badges/dflt-z/') < html.index('/badges/dflt-a/')


def test_gallery_name_sort_breaks_ties_by_set_order(client):
    """Within another sort (A-Z), same-key cards fall back to SET ORDER, not an arbitrary order."""
    BadgeFactory(series_slug='tie-hi', tier=1, badge_type='series', is_live=True,
                 required_stages=5, display_series='tie-hi', name='Same Name', set_number=9)
    BadgeFactory(series_slug='tie-lo', tier=1, badge_type='series', is_live=True,
                 required_stages=5, display_series='tie-lo', name='Same Name', set_number=1)

    html = client.get(GALLERY, {'view': 'gallery', 'sort': 'name'}).content.decode()

    assert html.index('/badges/tie-lo/') < html.index('/badges/tie-hi/')  # #1 before #9 on the name tie


def test_gallery_sort_by_tier_is_platinum_first(client):
    """?sort=tier orders platinum -> bronze (matches the Collection Gallery)."""
    _series('rs-tsort')

    html = client.get(GALLERY, {'view': 'gallery', 'sort': 'tier'}).content.decode()

    assert html.index('data-tier="platinum"') < html.index('data-tier="bronze"')


def test_gallery_state_maintenance_and_in_progress_filters(client):
    """The maintenance and in-progress state chips filter via their own EXISTS paths (in-progress =
    started but NOT held, so an earned badge is excluded)."""
    profile = ProfileFactory()
    badges = _series('rs-mstate')
    UserBadgeFactory(profile=profile, badge=badges[0])                                # bronze earned
    ub = UserBadgeFactory(profile=profile, badge=badges[1])
    UserBadge.objects.filter(pk=ub.pk).update(status='maintenance')                   # silver maintenance
    UserBadgeProgressFactory(profile=profile, badge=badges[2], completed_concepts=2)  # gold in progress
    client.force_login(profile.user)

    m_html = client.get(GALLERY, {'view': 'gallery', 'state': 'maintenance'}).content.decode()
    assert m_html.count('data-bgal-cell') == 1 and 'pp-bgal__owned--maintenance' in m_html

    p_html = client.get(GALLERY, {'view': 'gallery', 'state': 'in_progress'}).content.decode()
    assert p_html.count('data-bgal-cell') == 1 and 'pp-bgal__owned--progress' in p_html
    assert 'pp-bgal__owned--earned' not in p_html  # the earned bronze is NOT in-progress


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
    assert 'id="filter-form"' not in html   # ... but not the toolbar form / full-page shell
    assert 'id="bgal-sentinel"' not in html  # ... nor the scroll sentinel


def test_gallery_xhr_past_the_end_returns_no_cards(client):
    """Paginator.get_page CLAMPS an out-of-range page; the view must instead emit ZERO cards for an XHR
    fetch past the last page, so InfiniteScroller sees 0 and stops (rather than re-appending the last page
    forever)."""
    _series('rs-end')  # 4 badges -> a single page

    resp = client.get(GALLERY, {'view': 'gallery', 'page': 2}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    html = resp.content.decode()

    assert 'pp-bgal__card' not in html  # past the end -> nothing to append -> the scroller stops
