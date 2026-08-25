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

from trophies.models import BadgeSeries, GroupBadge, SeriesBadgeStanding, UserGroupBadge
from trophies.services.badge_list_service import build_list_cards, build_series_items
from tests.factories import (
    ProfileFactory, PlatformGroupFactory, BadgeSeriesFactory, GroupBadgeFactory,
)

pytestmark = pytest.mark.django_db

GALLERY = reverse('badges_list')
SERIES = reverse('badges_list')   # the default (no ?view) view is the per-series tile grid
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
    """n LINKED profiles with a standing -- the rarity denominator is the whole community now, and
    a pursuer is by definition part of it."""
    for _ in range(n):
        SeriesBadgeStanding.objects.create(
            profile=ProfileFactory(is_linked=True), series_slug=series_slug, xp=100, progress_bp=1000,
            stages_cleared=1, stages_total=1,
        )


# ------------------------------------------------------------------ service ------------------------------

def test_build_list_cards_shape_and_live_rarity():
    series = BadgeSeriesFactory(series_slug='gow', name='God of War')
    ultra = _group(series, 'ultra-hd', 'Ultra HD')
    GroupBadge.objects.filter(id=ultra.id).update(earned_count=1)
    _pursuers('gow', 4)                              # 1 of 4 in the community -> 25% -> common
    cards = build_list_cards(GroupBadge.objects.filter(id=ultra.id).select_related(*_SR), None)
    assert len(cards) == 1
    c = cards[0]
    assert c['earned'] is False and c['earned_count'] == 1
    assert c['rarity_pct'] == 25.0 and c['rarity_class'] == 'common'
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
    """The whole point: the query count does NOT grow with the number of cards.

    Measured as flatness rather than against a magic number. This asserted `<= 3` and so failed when
    a fourth bulk map (the viewer's stage standings) was legitimately added -- a passing test breaking
    on a correct change, which teaches people to bump the number rather than check the property.
    """
    series = BadgeSeriesFactory(series_slug='s', name='S')
    profile = ProfileFactory()

    def count_for(n):
        gbs = [_group(series, f'g{n}_{i}', f'G{n}_{i}') for i in range(n)]
        qs = GroupBadge.objects.filter(id__in=[g.id for g in gbs]).select_related(*_SR)
        with CaptureQueriesContext(connection) as ctx:
            cards = build_list_cards(qs, profile)
        assert len(cards) == n
        return len(ctx.captured_queries)

    # Warm the cached community-size scalar first. Without this the FIRST measurement pays for it and
    # the second does not, so the counts differ by one for a reason that has nothing to do with size.
    count_for(2)
    few, many = count_for(3), count_for(30)
    assert few == many, f'query count grew with card count: {few} -> {many}'
    # Measured cost is 3 (queryset eval + holds + standings; community_size is cached).
    assert many <= 4, f'flat, but {many} bulk queries per page is more than this page needs'


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


def test_gallery_cell_links_to_the_group_tab(client):
    _series_groups('pk', 'Linky', [('legacy-hd', 'Legacy HD'), ('ultra-hd', 'Ultra HD')])
    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()
    # the whole cell links straight to the badge detail page with THIS edition's tab open (no inspect modal)
    assert '/badges/pk/?group=legacy-hd' in html and '/badges/pk/?group=ultra-hd' in html


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

def test_gallery_defaults_to_name_order(client):
    """set_number (and its 'Set order' sort) was removed 2026-08-23: the new system never
    assigned the numbers, so the sort was name-order wearing a different label. Name is the
    honest default now. Three series arranged so every OTHER sort key produces a different
    leader (rarity leads with B, popular and newest lead with C) -- only a genuine name
    default yields A, B, C. The first cut passed under all four keys, which pins nothing
    (audit-caught)."""
    from django.utils import timezone
    a = _series_groups('dflt-a', 'AAA by name', [('ultra-hd', 'Ultra HD')])[0]
    b = _series_groups('dflt-b', 'BBB by name', [('ultra-hd', 'Ultra HD')])[0]
    c = _series_groups('dflt-c', 'CCC by name', [('ultra-hd', 'Ultra HD')])[0]
    GroupBadge.objects.filter(id=a.id).update(earned_count=5)
    GroupBadge.objects.filter(id=b.id).update(earned_count=0)   # rarity would lead with B
    GroupBadge.objects.filter(id=c.id).update(earned_count=9, created_at=timezone.now())
    # popular and newest would both lead with C
    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()   # no ?sort -> name
    assert html.index('/badges/dflt-a/') < html.index('/badges/dflt-b/') < html.index('/badges/dflt-c/')


def test_gallery_rejects_the_retired_set_order_sort_key(client):
    _series_groups('rk-z', 'ZZZ', [('ultra-hd', 'Ultra HD')])
    _series_groups('rk-a', 'AAA', [('ultra-hd', 'Ultra HD')])
    html = client.get(GALLERY, {'view': 'gallery', 'sort': 'set_number'}).content.decode()
    assert html.index('/badges/rk-a/') < html.index('/badges/rk-z/'), 'unknown key falls back to name'
    assert 'Set order' not in html, 'the sort dropdown must not offer the retired key'


@pytest.mark.parametrize('sort', ['rarity', 'popular', 'newest'])
def test_gallery_every_sort_breaks_ties_by_name(client, sort):
    from django.utils import timezone
    _series_groups('rs-tie-z', 'ZZZ Tie', [('ultra-hd', 'Ultra HD')])
    _series_groups('rs-tie-a', 'AAA Tie', [('ultra-hd', 'Ultra HD')])
    GroupBadge.objects.update(created_at=timezone.now())
    # both have 0 earners -> the primary key ties; only the name fallback can order them.
    html = client.get(GALLERY, {'view': 'gallery', 'sort': sort}).content.decode()
    assert html.index('/badges/rs-tie-a/') < html.index('/badges/rs-tie-z/')


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


# ================================================================= SERIES VIEW ===========================
# The default view: one TILE per badge series, carrying a row of its live GROUP medallions (one per platform
# group). Reuses build_list_cards via build_series_items, so it inherits the same batched/whale-safe path.

# ------------------------------------------------------------------ service ------------------------------

def test_build_series_items_groups_cards_by_series():
    _series_groups('gow', 'God of War', [('legacy-hd', 'Legacy HD'), ('ultra-hd', 'Ultra HD')])
    _series_groups('tlou', 'The Last of Us', [('ultra-hd', 'Ultra HD')])
    items = build_series_items(list(BadgeSeries.objects.order_by('name')), None)
    by_slug = {it['series'].series_slug: it for it in items}
    assert len(by_slug['gow']['cards']) == 2       # both platform groups become cells
    assert len(by_slug['tlou']['cards']) == 1
    assert by_slug['gow']['card_name'] == 'God of War' and by_slug['gow']['badge_type'] == 'series'


def test_build_series_items_total_earned_sums_its_groups():
    legacy, ultra = _series_groups('e', 'Earned', [('legacy-hd', 'Legacy HD'), ('ultra-hd', 'Ultra HD')])
    GroupBadge.objects.filter(id=legacy.id).update(earned_count=3)
    GroupBadge.objects.filter(id=ultra.id).update(earned_count=5)
    item = build_series_items(list(BadgeSeries.objects.filter(series_slug='e')), None)[0]
    assert item['total_earned'] == 8


def test_build_series_items_query_count_is_flat():
    """One group-badge fetch for the whole page plus build_list_cards' bulk maps -- independent of
    series count. Flatness, not a magic number: see the note on the sibling test above."""
    profile = ProfileFactory()

    def count_for(n, tag):
        for i in range(n):
            _series_groups(f'{tag}{i}', f'S{tag}{i}', [('ultra-hd', 'Ultra HD')])
        series = list(BadgeSeries.objects.filter(series_slug__startswith=tag))
        with CaptureQueriesContext(connection) as ctx:
            items = build_series_items(series, profile)
        assert len(items) == n
        return len(ctx.captured_queries)

    # Warm the cached community-size scalar first. Without this the FIRST measurement pays for it and
    # the second does not, so the counts differ by one for a reason that has nothing to do with size.
    count_for(2, 'warm')
    few, many = count_for(3, 'few'), count_for(20, 'many')
    assert few == many, f'query count grew with series count: {few} -> {many}'
    assert many <= 4


# ------------------------------------------------------------------ render + links -----------------------

def test_series_view_renders_group_medallions(client):
    _series_groups('gow', 'God of War', [('legacy-hd', 'Legacy HD'), ('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES).content.decode()                 # no ?view -> the Series tiles
    assert 'pp-med' in html
    assert html.count('class="pp-scard"') == 1                 # one tile per series
    assert html.count('class="pp-scard__group"') == 2          # one medallion cell per platform group
    assert 'Legacy HD' in html and 'Ultra HD' in html          # each cell names its platform group
    assert '/badges/gow/' in html                              # heading + cells link to the detail page


def test_series_tile_shows_cta_instead_of_a_lonely_zero(client):
    # A live badge no one has earned yet -> a "Be the first" nudge in the rarity slot, not a bare "0".
    _series_groups('new', 'Brand New', [('ultra-hd', 'Ultra HD')])   # earned_count defaults to 0
    html = client.get(SERIES).content.decode()
    assert 'Be the first' in html and 'pp-scard__grade--cta' in html


def test_series_tile_shows_the_count_once_earned(client):
    gb = _series_groups('pop', 'Popular', [('ultra-hd', 'Ultra HD')])[0]
    GroupBadge.objects.filter(id=gb.id).update(earned_count=1200)
    html = client.get(SERIES).content.decode()
    # earners -> the count (or rarity); no CTA in the tile's grade slot ("Be the first" is also a filter chip
    # label now, so assert on the tile's cta class, not the raw text).
    assert '1,200' in html and 'pp-scard__grade--cta' not in html


def test_default_art_resolves_to_a_static_url(client):
    # A badge with no custom art falls back to default.png -- which must render as a resolved STATIC url, not a
    # bare relative path (the broken-default-art bug).
    from django.templatetags.static import static
    _series_groups('nomed', 'No Art', [('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES).content.decode()
    assert static('images/badges/default.png') in html
    assert 'src="images/badges/default.png"' not in html


def test_user_badge_avatar_subject_is_flagged_for_the_circle_mask(client):
    # A user badge with no custom art uses the submitter's avatar (often square / 4:3 from PSN); the medallion
    # marks it .pp-med--avatar so the CSS circle-masks + shrinks it onto the plate.
    author = ProfileFactory(avatar_url='https://example.test/av.png')
    series = BadgeSeriesFactory(series_slug='ub', name='User Badge', badge_type='user', submitted_by=author)
    pg = PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS5'])
    GroupBadgeFactory(series=series, platform_group=pg, is_live=True)
    html = client.get(SERIES).content.decode()
    assert 'pp-med--avatar' in html                     # flagged for the circle treatment
    assert 'https://example.test/av.png' in html        # ... showing the avatar art


def test_non_avatar_badge_is_not_flagged_for_the_circle_mask(client):
    _series_groups('reg', 'Regular', [('ultra-hd', 'Ultra HD')], badge_image='badges/series/reg.png')
    html = client.get(SERIES).content.decode()
    assert 'pp-med--avatar' not in html                 # a normal subject keeps the full-plate art


def test_series_view_hides_series_without_a_live_group(client):
    # The _live_groups > 0 gate: a series whose only group badges are dormant (is_live=False) is excluded.
    _series_groups('live', 'Live One', [('ultra-hd', 'Ultra HD')])           # a live group -> shows
    dormant = BadgeSeriesFactory(series_slug='dorm', name='Dormant Only')
    pg = PlatformGroupFactory(key='legacy-hd', name='Legacy HD', platforms=['PS3'])
    GroupBadgeFactory(series=dormant, platform_group=pg, is_live=False)      # only dormant -> hidden
    html = client.get(SERIES).content.decode()
    assert '/badges/live/' in html and '/badges/dorm/' not in html
    # And the service agrees when handed both series directly.
    items = build_series_items(list(BadgeSeries.objects.order_by('name')), None)
    assert {it['series'].series_slug for it in items if it['cards']} == {'live'}


def test_series_view_shows_empty_state_when_no_match(client):
    _series_groups('exists', 'Exists', [('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES, {'series_slug': 'zzz-nothing-here'}).content.decode()
    assert 'pp-slist__empty' in html and 'No badges found' in html
    assert 'class="pp-scard"' not in html   # no tiles rendered


def test_series_medallion_links_to_the_group_tab(client):
    _series_groups('pk', 'Linky', [('legacy-hd', 'Legacy HD'), ('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES).content.decode()
    # each medallion links straight to the badge detail page with ITS edition tab open (no inspect modal)
    assert '/badges/pk/?group=legacy-hd' in html and '/badges/pk/?group=ultra-hd' in html


def test_series_tile_shows_owned_seal_and_holo(client):
    profile = ProfileFactory()
    ultra = _series_groups('own', 'Owned', [('ultra-hd', 'Ultra HD')])[0]
    UserGroupBadge.objects.create(profile=profile, group_badge=ultra, is_holo=True)
    client.force_login(profile.user)
    html = client.get(SERIES).content.decode()
    assert 'pp-scard__seal' in html            # the held cell shows the earned seal
    assert 'pp-scard__seal--holo' in html      # ... mastered -> the holo variant


def test_series_card_name_resolution_chain(client):
    from trophies.models import Franchise, Company
    fr = Franchise.objects.create(igdb_id=414, name='Resident Evil', slug='re-s', source_type='franchise')
    dev = Company.objects.create(igdb_id=717, name='Naughty Dog', slug='nd-s')
    _series_groups('rs-fr', 'RE Village', [('ultra-hd', 'Ultra HD')], franchise=fr)
    _series_groups('rs-dev', 'Uncharted', [('ultra-hd', 'Ultra HD')], developer=dev)
    _series_groups('rs-plain', 'Solo Series Name', [('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES).content.decode()
    assert 'pp-scard__name">Resident Evil<' in html      # franchise wins
    assert 'pp-scard__name">Naughty Dog<' in html        # developer wins over its series name
    assert 'pp-scard__name">Solo Series Name<' in html   # series name is the final fallback


# ------------------------------------------------------------------ filters ------------------------------

def test_series_search_matches_name(client):
    _series_groups('elden-ring', 'Elden Ring', [('ultra-hd', 'Ultra HD')])
    _series_groups('dark-souls', 'Dark Souls', [('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES, {'series_slug': 'elden'}).content.decode()
    assert '/badges/elden-ring/' in html and '/badges/dark-souls/' not in html


def test_series_type_filter_is_db_side(client):
    _series_groups('rs', 'RS', [('ultra-hd', 'Ultra HD')], badge_type='series')
    _series_groups('fr', 'FR', [('ultra-hd', 'Ultra HD')], badge_type='franchise')
    html = client.get(SERIES, {'badge_type': 'franchise'}).content.decode()
    assert '/badges/fr/' in html and '/badges/rs/' not in html


def test_series_badge_type_filter_ORs(client):
    _series_groups('rs-a', 'RS A', [('ultra-hd', 'Ultra HD')], badge_type='series')
    _series_groups('dev-b', 'Dev B', [('ultra-hd', 'Ultra HD')], badge_type='developer')
    _series_groups('fr-c', 'FR C', [('ultra-hd', 'Ultra HD')], badge_type='franchise')
    html = client.get(SERIES, {'badge_type': ['series', 'developer']}).content.decode()
    assert '/badges/rs-a/' in html and '/badges/dev-b/' in html   # both selected types (badge_type__in)
    assert '/badges/fr-c/' not in html                            # franchise excluded


# ------------------------------------------------------------------ shared chrome ------------------------

def test_series_page_renders_sticky_mini_bar(client):
    _series_groups('mb', 'Mini', [('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES).content.decode()
    assert 'class="pp-minibar"' in html                # identity + Series/Gallery switch, persistent
    assert 'id="badges-minibar-sentinel"' in html
    assert 'data-minibar-badge-filters' in html        # the Filters reach button


def test_series_toolbar_is_the_shared_collapsible(client):
    _series_groups('tb', 'Toolbar', [('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES).content.decode()
    assert 'pp-bgal__bar' in html and 'id="bgal-filters-toggle"' in html   # the shared Gallery toolbar
    assert 'id="bgal-advanced"' in html and 'id="bgal-filter-count"' in html
    assert 'name="badge_type"' in html                 # the Type chips live in the advanced panel
    assert 'pp-sbar' not in html                        # the old inline toolbar is gone


# ------------------------------------------------------------------ rarity filter ------------------------
# Rarity is live-derived (pct of the whole COMMUNITY who earned the badge). The filter reproduces that in
# the DB so it stays whale-safe + paginated.
#
# The community is ONE number shared by both series, so the pair has to be calibrated against the total,
# not per-series. And with a 1% mythic ceiling a mythic badge needs >100 accounts to exist at all -- hence
# the deliberately large seed. See rarity.RARITY_THRESHOLDS.

def _rarity_pair():
    myth = _series_groups('myth', 'Myth', [('ultra-hd', 'Ultra HD')])[0]
    comm = _series_groups('comm', 'Comm', [('ultra-hd', 'Ultra HD')])[0]
    _pursuers('myth', 60)
    _pursuers('comm', 60)                                           # community = 120
    GroupBadge.objects.filter(id=myth.id).update(earned_count=1)    # 0.8% -> mythic
    GroupBadge.objects.filter(id=comm.id).update(earned_count=30)   # 25%  -> common


def test_gallery_rarity_filter_is_db_side(client):
    _rarity_pair()
    myth = client.get(GALLERY, {'view': 'gallery', 'rarity': 'mythic'}).content.decode()
    assert '/badges/myth/' in myth and '/badges/comm/' not in myth
    comm = client.get(GALLERY, {'view': 'gallery', 'rarity': 'common'}).content.decode()
    assert '/badges/comm/' in comm and '/badges/myth/' not in comm


def test_series_rarity_filter_keeps_series_with_a_group_of_that_class(client):
    _rarity_pair()
    html = client.get(SERIES, {'rarity': 'mythic'}).content.decode()
    assert '/badges/myth/' in html and '/badges/comm/' not in html


def test_rarity_filter_excludes_badges_with_no_rarity(client):
    # No pursuers + no earners -> no rarity class -> excluded from the earned-tier filters (not silently "common").
    _series_groups('none', 'None', [('ultra-hd', 'Ultra HD')])
    html = client.get(GALLERY, {'view': 'gallery', 'rarity': 'common'}).content.decode()
    assert '/badges/none/' not in html


def test_be_the_first_chip_selects_only_unearned(client):
    # "Be the first" (rarity=unearned) surfaces the not-yet-earned badges the earned tiers exclude.
    _rarity_pair()                                             # myth (earned) + comm (earned)
    _series_groups('fresh', 'Fresh', [('ultra-hd', 'Ultra HD')])   # earned_count 0 -> not yet earned
    for view in ((GALLERY, {'view': 'gallery', 'rarity': 'unearned'}), (SERIES, {'rarity': 'unearned'})):
        html = client.get(view[0], view[1]).content.decode()
        assert '/badges/fresh/' in html                       # the un-earned badge shows
        assert '/badges/myth/' not in html and '/badges/comm/' not in html   # earned ones don't


def test_be_the_first_combines_with_a_rarity_tier(client):
    # Selecting a tier AND "Be the first" unions them (earned mythic + not-yet-earned).
    _rarity_pair()
    _series_groups('fresh', 'Fresh', [('ultra-hd', 'Ultra HD')])
    html = client.get(GALLERY, {'view': 'gallery', 'rarity': ['mythic', 'unearned']}).content.decode()
    assert '/badges/myth/' in html and '/badges/fresh/' in html   # both
    assert '/badges/comm/' not in html                            # common excluded


def test_rarity_chips_render_on_both_views(client):
    _series_groups('x', 'X', [('ultra-hd', 'Ultra HD')])
    for html in (client.get(SERIES).content.decode(),
                 client.get(GALLERY, {'view': 'gallery'}).content.decode()):
        assert 'name="rarity"' in html and 'pp-bgal__chip--rarity' in html
        assert 'value="mythic"' in html and 'value="common"' in html
        assert 'value="unearned"' in html and 'Be the first' in html   # the not-yet-earned chip


def test_series_state_chips_are_auth_only(client):
    _series_groups('a', 'Anon', [('ultra-hd', 'Ultra HD')])
    assert 'name="state"' not in client.get(SERIES).content.decode()   # anon -> no owned chips
    p = ProfileFactory()
    client.force_login(p.user)
    assert 'name="state"' in client.get(SERIES).content.decode()       # authed -> Earned / Not earned


def test_series_owned_filter_is_db_side(client):
    profile = ProfileFactory()
    has = _series_groups('has', 'Has', [('ultra-hd', 'Ultra HD')])[0]
    _series_groups('none', 'None', [('ultra-hd', 'Ultra HD')])
    UserGroupBadge.objects.create(profile=profile, group_badge=has)   # hold a group in 'has' only
    client.force_login(profile.user)
    earned = client.get(SERIES, {'state': 'earned'}).content.decode()
    assert '/badges/has/' in earned and '/badges/none/' not in earned      # holds >=1 group
    unearned = client.get(SERIES, {'state': 'unearned'}).content.decode()
    assert '/badges/none/' in unearned and '/badges/has/' not in unearned  # holds none


# ------------------------------------------------------------------ sorts --------------------------------

def test_series_default_sort_is_name(client):
    _series_groups('z', 'Zed', [('ultra-hd', 'Ultra HD')])
    _series_groups('a', 'Alpha', [('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES).content.decode()
    assert html.index('/badges/a/') < html.index('/badges/z/')


def test_series_popular_and_rarity_sort_by_total_earners(client):
    hi = _series_groups('hi', 'Hi', [('ultra-hd', 'Ultra HD')])[0]
    lo = _series_groups('lo', 'Lo', [('ultra-hd', 'Ultra HD')])[0]
    GroupBadge.objects.filter(id=hi.id).update(earned_count=100)
    GroupBadge.objects.filter(id=lo.id).update(earned_count=1)
    popular = client.get(SERIES, {'sort': 'popular'}).content.decode()
    assert popular.index('/badges/hi/') < popular.index('/badges/lo/')   # most earned first
    rare = client.get(SERIES, {'sort': 'rarity'}).content.decode()
    assert rare.index('/badges/lo/') < rare.index('/badges/hi/')         # fewest earners = rarest first


def test_series_newest_sort_by_created_at(client):
    from datetime import timedelta
    from django.utils import timezone
    _series_groups('old', 'Old', [('ultra-hd', 'Ultra HD')])
    _series_groups('new', 'New', [('ultra-hd', 'Ultra HD')])
    now = timezone.now()
    BadgeSeries.objects.filter(series_slug='old').update(created_at=now - timedelta(days=5))
    BadgeSeries.objects.filter(series_slug='new').update(created_at=now)
    html = client.get(SERIES, {'sort': 'newest'}).content.decode()
    assert html.index('/badges/new/') < html.index('/badges/old/')


# ------------------------------------------------------------------ pagination / whale-safe --------------

def test_series_full_page_carries_infinite_scroll_hooks(client):
    _series_groups('inf', 'Inf', [('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES).content.decode()
    assert 'id="items-grid"' in html and 'id="bgal-sentinel"' in html and 'id="bgal-loading"' in html


def test_series_xhr_page_returns_bare_tile_grid(client):
    _series_groups('xhr', 'Xhr', [('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES, {'page': 1}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').content.decode()
    assert 'class="pp-scard"' in html          # the tiles are there to append
    assert 'id="filter-form"' not in html      # ... but not the toolbar / full-page shell


def test_series_xhr_past_the_end_returns_no_tiles(client):
    _series_groups('end', 'End', [('ultra-hd', 'Ultra HD')])   # one series -> a single page
    html = client.get(SERIES, {'page': 2}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').content.decode()
    assert 'class="pp-scard"' not in html   # past the end -> nothing to append -> the scroller stops


def test_series_query_count_constant_regardless_of_catalog_size(client):
    _series_groups('base-a', 'Base A', [('ultra-hd', 'Ultra HD')])
    _series_groups('base-b', 'Base B', [('ultra-hd', 'Ultra HD')])
    client.get(SERIES)   # warm caches
    with CaptureQueriesContext(connection) as small:
        client.get(SERIES)
    for i in range(20):
        _series_groups(f'more-{i}', f'More {i}', [('ultra-hd', 'Ultra HD')])
    with CaptureQueriesContext(connection) as large:
        client.get(SERIES)
    assert len(large) == len(small)   # no growth with catalog size (batched, no per-series N+1)
    assert len(small) < 20


# ------------------------------------------------------------------ header: forge explainer --------------
# "How badges work" is staged as the forge journey: 3 beats + the two-editions legend, with real .pp-med
# medallions (forge_meds) as the payoff. It lives in the shared header, so it teaches on both views.

def test_forge_explainer_renders_the_journey(client):
    _series_groups('x', 'X', [('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES).content.decode()
    assert 'pp-forge' in html
    assert html.count('data-beat=') == 4                # four beats: set -> platinum -> claim -> master
    assert 'data-forge-mint' in html                    # the claim medallion (beat 3) is struck/minted
    assert 'pp-med--holographic' in html                # beat 4: the mastered medallion is holographic
    assert 'pp-forge__editions' in html                 # the two-editions reward legend
    assert 'Legacy HD' in html                          # the legend names both editions (only Ultra HD tiles exist)
    assert 'PS3 &amp; PS Vita' in html                  # the Legacy HD platform scope


def test_forge_explainer_states_the_mastery_mechanic(client):
    _series_groups('x', 'X', [('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES).content.decode()
    assert 'Master it' in html                          # the mastery beat
    # Mastery = 100% (incl. DLC) on every stage -- NOT the inverse; the copy must say so.
    assert '100% every stage, DLC and all' in html and 'holographic' in html


def test_forge_uses_real_badge_art_and_wires_the_peek(client):
    # A live badge WITH custom subject art -> the forge composes that real art onto the metal plates AND the
    # illustrations become interactive: tapping any opens that real badge's quick-peek (like the page's tiles).
    series = BadgeSeriesFactory(series_slug='art', name='Arty', badge_image='badges/series/arty.png')
    pg = PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS5'])
    gb = GroupBadgeFactory(series=series, platform_group=pg, is_live=True, earned_count=999)
    html = client.get(SERIES).content.decode()
    assert 'badges/series/arty.png' in html          # the real subject rides the medallion plates, not a plate
    assert 'pp-forge-peek"' in html                  # the illustrations carry the interactive class (attr, not JS)
    assert 'data-badge-id="%d"' % gb.id in html      # ... wired to that real badge's quick-peek


def test_forge_medallions_not_interactive_without_a_source_badge(client):
    # No badge has custom art -> no example to inspect, so the illustrations render but stay non-interactive
    # (no false click affordance). Match the class ATTRIBUTE (double-quote) so the JS selector doesn't count.
    _series_groups('x', 'X', [('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES).content.decode()
    assert 'pp-forge' in html and 'pp-forge-peek"' not in html


def test_forge_explainer_renders_on_gallery_too(client):
    _series_groups('x', 'X', [('ultra-hd', 'Ultra HD')])
    html = client.get(GALLERY, {'view': 'gallery'}).content.decode()
    assert 'pp-forge' in html and 'data-forge-mint' in html   # the shared modal teaches on both views


def test_howto_modal_is_first_run_only(client):
    """Inverted 2026-08. This used to assert TWO recall buttons (header + mini-bar) that re-opened the
    modal on demand.

    The teaching moved to `/badges/how-it-works/` -- a real URL, because support links it, search indexes
    it, and three surfaces render these edition names without being able to explain them: the badge detail
    group tabs, this page's gallery filter chips, and the Collection's edition stat labels. With a fuller
    home in place, a button re-opening a SHORTER copy is how the two drift apart, so the modal keeps only
    its onboarding job: greet a first visit, then hand off.

    The modal itself stays. What went is the permanent chrome around it.
    """
    _series_groups('x', 'X', [('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES).content.decode()

    assert 'id="badge-howto" hidden' in html      # the modal, hidden until the JS opens it on a first visit
    assert 'pp-howto__got' in html                # the "Got it" dismiss inside it
    assert '/badges/how-it-works/' in html        # the hand-off to the permanent home

    assert 'class="pp-howto-btn' not in html, 'the header recall button is back'
    assert 'pp-minibar__howto' not in html, 'the mini-bar recall button is back'


def test_the_header_reflows_the_lede_across_both_columns_on_a_phone(client):
    """The phone fit, ported from the hunter-profile header rather than re-invented.

    The lede is nested under the title, so at 375px it renders in a ~233px column beside the tally and
    wraps to two lines -- while the corner under the tally sits empty. Below 768px the row becomes a grid
    with the lede spanning BOTH columns, where its ~263px fits on one line in the 303px available.

    What this pins is the pairing, which is the part that breaks silently: the CSS places three NAMED
    areas, and if the template stops emitting any one of those hooks the rule still parses, still
    compiles, and simply stops applying. Nothing looks broken in review; the header just quietly grows a
    line again on the size where it can least afford one.
    """
    from pathlib import Path

    _series_groups('y', 'Y', [('ultra-hd', 'Ultra HD')])
    html = client.get(SERIES).content.decode()

    for hook in ('pp-bhead', 'pp-bhead__id', 'pp-bhead__title', 'pp-bhead__sub', 'pp-bhead__tally'):
        assert hook in html, f'the header lost its {hook!r} hook, so the phone reflow stops applying'

    # p-3 on a phone, matching the profile header it copies. p-4 is 8px this header does not have.
    assert 'card-body p-3 md:p-5' in html, 'the header card is back on phone padding it cannot afford'

    css = (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components'
           / 'series-list.css').read_text(encoding='utf-8')
    block = css[css.index('.pp-bhead {'):]
    block = block[:block.index('\n}\n', block.index('.pp-bhead__tally')) + 1]

    assert 'grid-template-areas' in block, 'the header row is no longer a named grid on a phone'
    assert 'display: contents' in block, (
        'the id block has a box again, so the title and lede cannot be placed as separate grid items'
    )
    # Named elements, not structural selectors -- the exact lesson profile-hero.css records: a
    # `> :first-child` matches whichever child happens to be first and re-breaks when one is added.
    assert ':first-child' not in block and ':nth-child' not in block, (
        'the header grid places children structurally; use the named .pp-bhead__* hooks'
    )
    for area in ('title', 'sub', 'tally'):
        assert f'grid-area: {area}' in block, f'the {area} area is declared but never assigned'
