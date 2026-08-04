"""Badge LIST page (grouping-badge system): the batched list-service + the Gallery view.

The Gallery is the catalog-discovery wall: public, server-paginated, SHOWCASE-first (every medallion in full
earned colour), one cell PER GROUP BADGE (series x platform group), with a logged-in viewer's hold shown as a
card-corner marker. These pin the contract: DB-side platform/state/type filters, live rarity, the public
(non-modal) detail link, and a whale-safe constant per-page query count. (Replaces the retired per-tier suite.)
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from trophies.models import GroupBadge, SeriesBadgeStanding, UserGroupBadge
from trophies.services.badge_list_service import build_list_cards
from tests.factories import (
    ProfileFactory, PlatformGroupFactory, BadgeSeriesFactory, GroupBadgeFactory,
)

pytestmark = pytest.mark.django_db

GALLERY = reverse('badges_list')
# select_related the batched builder + Gallery rely on to stay query-flat.
_SR = ('series', 'series__franchise', 'series__collection', 'series__developer', 'platform_group')


def _group(series, key, name, **kw):
    pg = PlatformGroupFactory(key=key, name=name, platforms=['PS5'])
    return GroupBadgeFactory(series=series, platform_group=pg, is_live=True, **kw)


def _series_groups(slug, name, groups, **series_kw):
    """A series with a live GroupBadge per (key, name) platform-group. Returns the GroupBadge list."""
    series = BadgeSeriesFactory(series_slug=slug, name=name, **series_kw)
    return [_group(series, key, gname) for key, gname in groups]


def _pursuers(series_slug, n):
    for _ in range(n):
        SeriesBadgeStanding.objects.create(
            profile=ProfileFactory(), series_slug=series_slug, xp=100, progress_bp=1000,
            stages_cleared=1, stages_total=1,
        )


# ------------------------------------------------------------------ service ------------------------------

def test_build_list_cards_shape_and_live_rarity():
    series = BadgeSeriesFactory(series_slug='gow', name='God of War')
    ultra = _group(series, 'ultra-hd', 'Ultra HD')
    GroupBadge.objects.filter(id=ultra.id).update(earned_count=1)
    _pursuers('gow', 4)                              # 1 of 4 pursuers earned it -> 25% -> uncommon
    cards = build_list_cards(GroupBadge.objects.filter(id=ultra.id).select_related(*_SR), None)
    assert len(cards) == 1
    c = cards[0]
    assert c['earned'] is False and c['earned_count'] == 1
    assert c['rarity_pct'] == 25.0 and c['rarity_class'] == 'uncommon'
    assert c['frame']['state'] == 'earned' and c['frame']['art_layers']   # showcase frame is built


def test_build_list_cards_marks_the_viewers_holds():
    series = BadgeSeriesFactory(series_slug='h', name='Held')
    gb = _group(series, 'ultra-hd', 'Ultra HD')
    profile = ProfileFactory()
    UserGroupBadge.objects.create(profile=profile, group_badge=gb, is_holo=True)
    card = build_list_cards(GroupBadge.objects.filter(id=gb.id).select_related(*_SR), profile)[0]
    assert card['earned'] is True and card['is_holo'] is True
    assert card['frame']['is_holographic'] is True   # a mastered hold shimmers on the wall


def test_build_list_cards_query_count_is_flat():
    # The whole point: the query count does NOT grow with the number of cards (two bulk maps + the queryset).
    series = BadgeSeriesFactory(series_slug='s', name='S')
    gbs = [_group(series, f'g{i}', f'G{i}') for i in range(6)]
    profile = ProfileFactory()
    qs = GroupBadge.objects.filter(id__in=[g.id for g in gbs]).select_related(*_SR)
    with CaptureQueriesContext(connection) as ctx:
        cards = build_list_cards(qs, profile)
    assert len(cards) == 6
    assert len(ctx.captured_queries) <= 3   # queryset eval + pursuer counts + holds -- independent of card count


# ------------------------------------------------------------------ Gallery: render + links --------------

def test_gallery_showcase_wall_links_to_detail_anon(client):
    _series_groups('gow', 'God of War', [('legacy-hd', 'Legacy HD'), ('ultra-hd', 'Ultra HD')])
    resp = client.get(GALLERY, {'view': 'gallery'})
    html = resp.content.decode()
    assert resp.status_code == 200
    assert 'pp-bgal' in html and 'pp-med' in html                # the gallery island + medallions
    assert html.count('class="pp-bgal__card"') == 2             # one cell PER group badge, not per series
    assert 'Legacy HD' in html and 'Ultra HD' in html           # each carries its platform group
    assert 'data-state="earned"' in html                        # showcase-first: full colour for everyone
    assert '/badges/gow/' in html                               # cards tap through to the series detail page
    assert 'collection_badge_modal' not in html                 # not the login-gated collection modal
    assert 'pp-bgal__owned' not in html                         # anonymous -> no ownership markers


def test_gallery_defaults_to_series_view(client):
    _series_groups('d', 'Default', [('ultra-hd', 'Ultra HD')])
    html = client.get(GALLERY).content.decode()                 # no ?view
    assert 'pp-bgal__grid' not in html and 'pp-scard' in html   # the Series tile, not the medallion wall


def test_gallery_cell_wires_the_peek_and_detail_fallback(client):
    _series_groups('pk', 'Peek', [('ultra-hd', 'Ultra HD')])
    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()
    assert 'data-badge-id=' in html and 'id="badge-peek"' in html   # cell id + the shared modal container
    assert '/badges/pk/' in html                                    # detail href fallback


def test_gallery_card_name_resolution_chain(client):
    from trophies.models import Franchise, Company
    fr = Franchise.objects.create(igdb_id=4242, name='Resident Evil', slug='re-t', source_type='franchise')
    coll = Franchise.objects.create(igdb_id=5353, name='Ezio Trilogy', slug='ezio-t', source_type='collection')
    dev = Company.objects.create(igdb_id=7777, name='Naughty Dog', slug='nd-t')
    _series_groups('rs-fr', 'RE Village', [('ultra-hd', 'Ultra HD')], franchise=fr)
    _series_groups('rs-coll', 'Ezio Series', [('ultra-hd', 'Ultra HD')], collection=coll)
    _series_groups('rs-dev', 'Uncharted', [('ultra-hd', 'Ultra HD')], developer=dev)
    _series_groups('rs-plain', 'Solo Series Name', [('ultra-hd', 'Ultra HD')])
    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()
    assert 'pp-bgal__name">Resident Evil<' in html      # franchise wins
    assert 'pp-bgal__name">Ezio Trilogy<' in html       # collection wins over its series name
    assert 'pp-bgal__name">Naughty Dog<' in html        # developer wins over its series name
    assert 'pp-bgal__name">Solo Series Name<' in html   # series name is the final fallback


# ------------------------------------------------------------------ Gallery: filters ---------------------

def test_gallery_group_filter_is_db_side(client):
    _series_groups('gow', 'God of War', [('legacy-hd', 'Legacy HD'), ('ultra-hd', 'Ultra HD')])
    html = client.get(GALLERY, {'view': 'gallery', 'group': 'ultra-hd'}).content.decode()
    assert html.count('class="pp-bgal__card"') == 1    # only the Ultra HD group badge passes (chips still show both)


def test_gallery_search_matches_series(client):
    _series_groups('elden-ring', 'Elden Ring', [('ultra-hd', 'Ultra HD')])
    _series_groups('dark-souls', 'Dark Souls', [('ultra-hd', 'Ultra HD')])
    html = client.get(GALLERY, {'view': 'gallery', 'q': 'elden'}).content.decode()
    assert '/badges/elden-ring/' in html and '/badges/dark-souls/' not in html


def test_gallery_search_by_set_number(client):
    a = _series_groups('sn42', 'Set A', [('ultra-hd', 'Ultra HD')])[0]
    b = _series_groups('sn7', 'Set B', [('ultra-hd', 'Ultra HD')])[0]
    GroupBadge.objects.filter(id=a.id).update(set_number=42)
    GroupBadge.objects.filter(id=b.id).update(set_number=7)
    for query in ('42', '#42', '#0042'):
        html = client.get(GALLERY, {'view': 'gallery', 'q': query}).content.decode()
        assert '/badges/sn42/' in html and '/badges/sn7/' not in html, query


def test_gallery_type_filter(client):
    _series_groups('rs', 'RS', [('ultra-hd', 'Ultra HD')], badge_type='series')
    _series_groups('fr', 'FR', [('ultra-hd', 'Ultra HD')], badge_type='franchise')
    html = client.get(GALLERY, {'view': 'gallery', 'badge_type': 'franchise'}).content.decode()
    assert '/badges/fr/' in html and '/badges/rs/' not in html


def test_gallery_anonymous_hides_state_chips_keeps_platform(client):
    _series_groups('a', 'Anon', [('ultra-hd', 'Ultra HD')])
    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()
    assert 'name="state"' not in html and 'name="group"' in html   # state is auth-only; platform chips remain


def test_gallery_owned_marker_and_earned_filter(client):
    profile = ProfileFactory()
    legacy, ultra = _series_groups('own', 'Owned', [('legacy-hd', 'Legacy HD'), ('ultra-hd', 'Ultra HD')])
    UserGroupBadge.objects.create(profile=profile, group_badge=ultra)   # hold only Ultra HD
    client.force_login(profile.user)

    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()
    assert 'pp-bgal__owned--earned' in html            # the held cell shows the owned check
    assert html.count('data-state="earned"') >= 2      # ... but every medallion still renders showcase colour

    earned = client.get(GALLERY, {'view': 'gallery', 'state': 'earned'}).content.decode()
    assert earned.count('class="pp-bgal__card"') == 1  # earned filter -> only the held badge (DB EXISTS)
    unearned = client.get(GALLERY, {'view': 'gallery', 'state': 'unearned'}).content.decode()
    assert unearned.count('class="pp-bgal__card"') == 1   # not-earned -> only the other one


# ------------------------------------------------------------------ Gallery: sorts -----------------------

def test_gallery_defaults_to_set_order(client):
    a = _series_groups('dflt-a', 'AAA first by name', [('ultra-hd', 'Ultra HD')])[0]
    z = _series_groups('dflt-z', 'ZZZ last by name', [('ultra-hd', 'Ultra HD')])[0]
    GroupBadge.objects.filter(id=a.id).update(set_number=9)
    GroupBadge.objects.filter(id=z.id).update(set_number=1)
    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()   # no ?sort -> set order
    assert html.index('/badges/dflt-z/') < html.index('/badges/dflt-a/')   # #1 before #9 (name would disagree)


def test_gallery_name_sort_breaks_ties_by_set_order(client):
    hi = _series_groups('tie-hi', 'Same Name', [('ultra-hd', 'Ultra HD')])[0]
    lo = _series_groups('tie-lo', 'Same Name', [('ultra-hd', 'Ultra HD')])[0]
    GroupBadge.objects.filter(id=hi.id).update(set_number=9)
    GroupBadge.objects.filter(id=lo.id).update(set_number=1)
    html = client.get(GALLERY, {'view': 'gallery', 'sort': 'name'}).content.decode()
    assert html.index('/badges/tie-lo/') < html.index('/badges/tie-hi/')   # #1 before #9 on the name tie


@pytest.mark.parametrize('sort', ['rarity', 'popular', 'newest'])
def test_gallery_every_sort_breaks_ties_by_set_order(client, sort):
    from django.utils import timezone
    hi = _series_groups('rs-tie-hi', 'Tie Hi', [('ultra-hd', 'Ultra HD')])[0]
    lo = _series_groups('rs-tie-lo', 'Tie Lo', [('ultra-hd', 'Ultra HD')])[0]
    GroupBadge.objects.filter(id=hi.id).update(set_number=9, created_at=timezone.now())
    GroupBadge.objects.filter(id=lo.id).update(set_number=1, created_at=timezone.now())
    # both have 0 earners -> the primary key ties; only the set-number fallback can order them.
    html = client.get(GALLERY, {'view': 'gallery', 'sort': sort}).content.decode()
    assert html.index('/badges/rs-tie-lo/') < html.index('/badges/rs-tie-hi/')


# ------------------------------------------------------------------ Gallery: pagination / whale-safe -----

def test_gallery_full_page_carries_infinite_scroll_hooks(client):
    _series_groups('inf', 'Inf', [('ultra-hd', 'Ultra HD')])
    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()
    assert 'id="items-grid"' in html and 'id="bgal-sentinel"' in html and 'id="bgal-loading"' in html
    assert 'page-jump-form' not in html   # no page-number pager (infinite scroll owns pagination)


def test_gallery_xhr_page_returns_bare_card_grid(client):
    _series_groups('xhr', 'Xhr', [('ultra-hd', 'Ultra HD')])
    html = client.get(GALLERY, {'view': 'gallery', 'page': 1}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').content.decode()
    assert 'pp-bgal__card' in html          # the cards are there to append
    assert 'id="filter-form"' not in html   # ... but not the toolbar form / full-page shell
    assert 'id="bgal-sentinel"' not in html


def test_gallery_xhr_past_the_end_returns_no_cards(client):
    _series_groups('end', 'End', [('ultra-hd', 'Ultra HD')])   # one badge -> a single page
    html = client.get(GALLERY, {'view': 'gallery', 'page': 2}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').content.decode()
    assert 'pp-bgal__card' not in html   # past the end -> nothing to append -> the scroller stops


def test_gallery_query_count_constant_regardless_of_catalog_size(client):
    _series_groups('base-a', 'Base A', [('ultra-hd', 'Ultra HD')])
    _series_groups('base-b', 'Base B', [('ultra-hd', 'Ultra HD')])
    client.get(GALLERY, {'view': 'gallery'})   # warm caches
    with CaptureQueriesContext(connection) as small:
        client.get(GALLERY, {'view': 'gallery'})
    for i in range(20):
        _series_groups(f'more-{i}', f'More {i}', [('ultra-hd', 'Ultra HD')])
    with CaptureQueriesContext(connection) as large:
        client.get(GALLERY, {'view': 'gallery'})
    assert len(large) == len(small)   # no growth with catalog size (batched, no per-card N+1)
    assert len(small) < 20            # absolute ceiling: a per-card N+1 (a page is 48 cards) can't hide
