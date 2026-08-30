"""Trophy Lists browse (/games/lists/, TrophyListsBrowseView) -- the Games/Trophy Lists IA's
LAST canonical page: the LIST-level catalogue, one card per trophy list, deliberately
UN-condensed (the anti-Browse-Games). NOT named test_lists_browse.py -- that file belongs to the
hidden GameList collections system and is module-skipped with it.
"""

import pytest
from django.urls import reverse

from tests.factories import GameFactory, ProfileFactory, ProfileGameFactory

pytestmark = pytest.mark.django_db

GRID_PARTIAL = 'trophies/partials/trophy_lists/browse_results.html'
FULL_PAGE = 'trophies/trophy_lists.html'
URL = '/games/lists/'


# ── The page's identity: per-LIST, never condensed ────────────────────────────────────────────────

def test_sibling_lists_render_one_card_each(client):
    """THE anti-condensing pin: two sibling lists of one concept are TWO cards here -- exactly
    what Browse Games' election dedupes away -- each linking its OWN List detail."""
    a = GameFactory(title_name='Stack EU', title_platform=['PS5'])
    b = GameFactory(concept=a.concept, title_name='Stack NA', title_platform=['PS5'])

    resp = client.get(URL)
    content = resp.content.decode()

    assert content.count('pp-gcard__title') == 2
    assert 'data-result-count="2"' in content
    assert f'href="/games/{a.np_communication_id}/"' in content
    assert f'href="/games/{b.np_communication_id}/"' in content
    assert 'condensed_cards' not in resp.context
    assert 'list_count_map' not in resp.context


def test_blank_np_rows_never_render_a_card(client):
    """The destination np floor without the election: an un-linkable row is not a card."""
    GameFactory(title_name='Linkable List', title_platform=['PS5'])
    GameFactory(title_name='Ghost List', title_platform=['PS5'], np_communication_id=None)

    content = client.get(URL).content.decode()

    assert 'Linkable List' in content
    assert 'Ghost List' not in content


def test_bare_url_returns_200_even_with_saved_browse_defaults(client):
    """The no-dispatch decision: this page is static-sitemap-advertised, so the bare URL must
    200 without a redirect -- INCLUDING for a signed-in hunter whose saved Browse Games defaults
    would 302 them on /games/ (the browse_defaults key does not apply here)."""
    GameFactory(title_platform=['PS5'])
    viewer = ProfileFactory(is_linked=True)
    viewer.user.browse_defaults = {'games': {'platform': ['PS5'], 'sort': 'played'}}
    viewer.user.save(update_fields=['browse_defaults'])
    client.force_login(viewer.user)

    resp = client.get(URL)

    assert resp.status_code == 200
    # The full attribute: bare 'index, follow' is a SUBSTRING of 'noindex, follow' and
    # would pass either way (the audit's vacuous-assertion catch).
    assert 'content="index, follow"' in resp.content.decode()


# ── List identity: observed names ─────────────────────────────────────────────────────────────────

def _observe(game, raw_name):
    """A PSN trophy_titles observation -- what this page's cards are titled by."""
    from trophies.models import PSNTitleObservation
    return PSNTitleObservation.objects.create(
        np_communication_id=game.np_communication_id, game=game, source='trophy_titles',
        title_name_raw=raw_name, content_hash=f'{game.np_communication_id}:{raw_name}:tt',
    )


def test_cards_title_by_the_observed_list_name(client):
    """The list-identity mode: the card's title (and alt) is what PSN actually calls the list
    (display_list_names), falling back to title_name -- NOT the concept's unified_title (the
    condensed grids' source; the fixture wrinkle this suite exists to respect)."""
    observed = GameFactory(title_name='Cleaned Title', title_platform=['PS5'],
                           concept__unified_title='Concept Title')
    _observe(observed, 'Observed PSN Name')
    bare = GameFactory(title_name='Fallback Title', title_platform=['PS5'],
                       concept__unified_title='Another Concept')

    resp = client.get(URL)
    content = resp.content.decode()

    assert resp.context['list_identity_cards'] is True
    assert 'Observed PSN Name' in content
    assert 'Fallback Title' in content
    assert 'Concept Title' not in content and 'Another Concept' not in content
    # The ItemList schema claims the same names the grid shows.
    names = {row['name'] for row in resp.context['seo_item_list']}
    assert 'Observed PSN Name' in names and 'Fallback Title' in names


def test_the_name_batch_runs_exactly_once_per_render(client):
    """display_list_names is THE grid read: one observation query per page, never per card."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for i in range(8):
        g = GameFactory(title_platform=['PS5'])
        _observe(g, f'Observed {i}')

    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(URL)

    assert resp.status_code == 200
    observation_queries = [q for q in ctx.captured_queries
                           if 'psntitleobservation' in q['sql'].lower()]
    assert len(observation_queries) == 1, (
        f'expected ONE batched observation query, saw {len(observation_queries)}'
    )


def test_region_chips_render_on_these_cards_only(client):
    """The list-identity mode's region chips: a regional list shows its region codes, a
    non-regional one shows the muted Global chip -- and the class is this page's OWN
    (.pp-gcard__plat--region stays retired; the work-describing cards remain region-free,
    banned in their suites)."""
    GameFactory(title_name='JP Copy', title_platform=['PS5'], is_regional=True, region=['JP'])
    GameFactory(title_name='World Copy', title_platform=['PS5'])

    content = client.get(URL).content.decode()

    assert 'pp-gcard__region' in content
    assert '>JP<' in content
    assert 'pp-gcard__region--global' in content and '>Global<' in content
    assert 'pp-gcard__plat--region' not in content   # the retired class stays retired


# ── Header: the browse-family standard ────────────────────────────────────────────────────────────

def test_header_carries_the_family_tally_and_scard_grid(client):
    """Jeffrey's beta catch: the header must look/function like the other browse headers --
    the right-aligned headline Tally with its shown-sublabel plus the .scard substance grid,
    LIST-scoped (the games heartbeat scards stay off this page; lists are its honest unit).
    Values checked through the context so the hourly cache serves what this fixture built."""
    from django.core.cache import cache

    from tests.factories import TrophyFactory

    cache.delete('trophy_lists:header_stats')
    plat = GameFactory(title_name='Plat List', title_platform=['PS5'])
    TrophyFactory(game=plat, trophy_type='platinum')
    GameFactory(title_name='JP List', title_platform=['PS5'], is_regional=True, region=['JP'])

    resp = client.get(URL)
    content = resp.content.decode()

    assert 'id="tlb-count"' in content and 'lists shown' in content
    for label in ('Trophy lists', 'Regional', 'With a platinum', 'New this week'):
        assert label in content, label
    assert resp.context['tlb_stats'] == {
        'total': 2, 'regional': 1, 'with_plat': 1, 'new_this_week': 2,
    }


def test_header_stats_never_run_on_grid_swaps(client):
    """The gating that keeps the swap path cheap: the XHR/HTMX branches re-render only the grid,
    so the header stats must not be computed (or fetched) for them."""
    from django.core.cache import cache

    cache.delete('trophy_lists:header_stats')
    GameFactory(title_platform=['PS5'])

    xhr = client.get(URL, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    htmx = client.get(URL, HTTP_HX_REQUEST='true')

    assert 'tlb_stats' not in xhr.context
    assert 'tlb_stats' not in htmx.context
    assert cache.get('trophy_lists:header_stats') is None, 'a swap warmed the header cache'


# ── Filters + sort ────────────────────────────────────────────────────────────────────────────────

def test_platform_and_region_filters_narrow(client):
    """Regions are first-class here (the page's point): ?regions=NA keeps the NA list and drops
    the EU-only one; ?platform narrows as everywhere."""
    na = GameFactory(title_name='NA Copy', title_platform=['PS5'], is_regional=True, region=['NA'])
    GameFactory(concept=na.concept, title_name='EU Copy', title_platform=['PS5'],
                is_regional=True, region=['EU'])
    GameFactory(title_name='Old Gen List', title_platform=['PS3'])

    by_region = client.get(URL, {'regions': 'NA'}).content.decode()
    assert 'NA Copy' in by_region and 'EU Copy' not in by_region

    by_platform = client.get(URL, {'platform': 'PS3'}).content.decode()
    assert 'Old Gen List' in by_platform and 'NA Copy' not in by_platform


def test_every_sort_ends_on_the_pk_tiebreaker():
    """This is the ONE grid where title ties are the NORM (sibling stacks share a cleaned
    title_name), and the InfiniteScroller fetches each page as its own LIMIT/OFFSET query --
    without a unique trailing key Postgres may reorder a tie block between pages, duplicating
    or dropping cards at a page boundary. Pin the deterministic tail on default AND param sorts."""
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    from trophies.views.game_views import TrophyListsBrowseView

    for params in ({}, {'sort': 'played'}):
        req = RequestFactory().get(URL, params)
        req.user = AnonymousUser()
        v = TrophyListsBrowseView()
        v.request = req
        v.kwargs = {}
        assert v.get_queryset().query.order_by[-1] == 'pk', params


def test_alpha_default_and_sort_param(client):
    GameFactory(title_name='Zulu List', title_platform=['PS5'], played_count=999)
    GameFactory(title_name='Alpha List', title_platform=['PS5'], played_count=1)

    default = client.get(URL).content.decode()
    assert default.index('Alpha List') < default.index('Zulu List'), 'alpha is the default sort'

    played = client.get(URL, {'sort': 'played'}).content.decode()
    assert played.index('Zulu List') < played.index('Alpha List')


def test_platinum_only_filter(client):
    from tests.factories import TrophyFactory
    plat = GameFactory(title_name='Plat List', title_platform=['PS5'])
    TrophyFactory(game=plat, trophy_type='platinum')
    GameFactory(title_name='Platless List', title_platform=['PS5'])

    content = client.get(URL, {'show_only_platinum': 'on'}).content.decode()

    assert 'Plat List' in content and 'Platless List' not in content


def test_region_chip_and_reset_resolve_against_this_page(client):
    """The chip_base_url / empty_reset_url overrides: removing the region chip and the
    empty-state reset must land back on /games/lists/, never eject to Browse Games."""
    GameFactory(title_name='Regional List', title_platform=['PS5'], is_regional=True, region=['JP'])

    content = client.get(URL, {'regions': 'NA', 'letter': 'Q'}).content.decode()

    # The chip ELEMENT, not the always-present __achips container (substring trap).
    assert 'class="pp-gbrowse__achip"' in content             # active-filter chips rendered
    assert 'href="/games/lists/?' in content                  # chip remove-URLs on this page
    assert 'href="/games/?' not in content                    # never Browse Games
    assert 'pp-gcard-empty__reset' in content                 # nothing matches Q + NA -> reset CTA
    assert 'href="/games/lists/"' in content


# ── Nav + sitemap wiring ──────────────────────────────────────────────────────────────────────────

def test_the_browse_rail_lights_and_the_sitemap_advertises(client):
    """The page is a first-class Browse destination: its rail pill is active on its own page
    (exact url_name match -- no override needed since the item IS the page), and the static
    sitemap advertises it (the no-dispatch decision is what lets seo_closing's 200-no-redirect
    contract hold)."""
    from core.sitemaps import StaticViewSitemap

    GameFactory(title_platform=['PS5'])
    content = client.get(URL).content.decode()

    # The active pill: href + is-active + aria-current on one anchor (the subnav's contract).
    pill = content.split('href="/games/lists/"', 1)[1].split('>')[0]
    assert 'is-active' in pill and 'aria-current="page"' in pill, (
        'the Trophy Lists rail pill must light on its own page'
    )
    assert 'trophy_lists' in StaticViewSitemap().items()


def test_list_detail_lights_trophy_lists_not_games(client):
    """A LIST detail page belongs to the list-level catalogue (Jeffrey's browser-pass catch):
    /games/<np>/ lights the Trophy Lists pill, while the CONCEPT Game page keeps lighting
    Games -- each detail page points at the catalogue that browses it. Resolved through the
    real request so the override map's url_name spelling is exercised, not just its shape."""
    from django.test import RequestFactory

    from core.hub_subnav import resolve_hub_subnav

    game = GameFactory(title_platform=['PS5'])
    resp = client.get(f'/games/{game.np_communication_id}/', HTTP_CF_RAY='test')
    assert resp.status_code == 200
    req = RequestFactory().get(f'/games/{game.np_communication_id}/')
    req.resolver_match = resp.wsgi_request.resolver_match
    match = resolve_hub_subnav(req)
    assert match['hub'].key == 'browse' and match['active_slug'] == 'trophy-lists'

    # Assert over EVERY lit rail pill (desktop + mobile sheet render the strip twice; the
    # jobs-surface pattern): the lit set must be exactly Trophy Lists -- which also proves the
    # Games pill is dark without a fragile positional split. The navbar's Browse hub button is
    # not a pp-subpill, so it can't leak in.
    import re
    lit = re.findall(r'href="([^"]*)" class="pp-subpill is-active"', resp.content.decode())
    assert set(lit) == {'/games/lists/'}, lit


def test_the_whole_list_scoped_family_lights_trophy_lists():
    """Every /games/<np>/-scoped url_name lights the list family -- INCLUDING the two public
    roadmap reader routes, which the audit caught with NO override line at all (rail rendered
    unlit on sitemap-indexed pages). The concept pages stay on Games (the IA split)."""
    from core.hub_subnav import _URL_NAME_TO_SLUG_OVERRIDES as overrides

    for name in ('game_detail', 'game_detail_with_profile',
                 'roadmap_edit', 'roadmap_edit_ctg', 'roadmap_detail', 'roadmap_detail_dlc'):
        assert overrides[name] == ('browse', 'trophy-lists'), name
    for name in ('game_page', 'game_page_concept'):
        assert overrides[name] == ('browse', 'games'), name


# ── Chassis: XHR partial, pagination, minibar ─────────────────────────────────────────────────────

def test_xhr_returns_rows_partial(client):
    GameFactory(title_platform=['PS5'])

    resp = client.get(URL, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    templates = {t.name for t in resp.templates if t.name}

    assert resp.status_code == 200
    assert GRID_PARTIAL in templates
    assert FULL_PAGE not in templates
    assert 'data-result-count' in resp.content.decode()


def test_xhr_past_end_page_404s(client):
    GameFactory(title_platform=['PS5'])
    resp = client.get(URL, {'page': '999'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    assert resp.status_code == 404


def test_htmx_filter_swap_returns_grid_with_oob_chips(client):
    """A panel filter change HTMX-swaps only the grid partial -- WITH pp-reveal baked in (the
    settle-strips-classes fix) and the OOB active_filters copy whose chips resolve against
    /games/lists/ (the line that keeps a reader's SECOND filter change from ejecting them to
    Browse Games)."""
    GameFactory(title_name='Regional List', title_platform=['PS5'], is_regional=True, region=['NA'])

    resp = client.get(URL, {'regions': 'NA'}, HTTP_HX_REQUEST='true')
    templates = {t.name for t in resp.templates if t.name}
    content = resp.content.decode()

    assert resp.status_code == 200
    assert GRID_PARTIAL in templates
    assert FULL_PAGE not in templates
    assert 'pp-reveal' in content
    assert 'hx-swap-oob="true"' in content
    assert 'href="/games/lists/?' in content
    assert 'href="/games/?' not in content


def test_grid_contract_minibar_and_sentinel(client):
    """The card contract (pursuer band via show_game_hooks) + the page chrome: sticky minibar,
    its sentinel, the infinite-scroll sentinel, no Lucky button (deliberate v1 omission), and
    no leaked template syntax."""
    viewer = ProfileFactory(is_linked=True)
    game = GameFactory(title_name='Contract Check', title_platform=['PS5'])
    ProfileGameFactory(profile=viewer, game=game, progress=42)
    client.force_login(viewer.user)

    content = client.get(URL).content.decode()

    assert 'pp-gcard' in content and 'Contract Check' in content
    assert 'No badges' in content and 'No contract' in content   # the pursuer band placeholders
    assert '42%' in content                                       # viewer progress fill
    assert 'data-sticky-sentinel="#tlb-minibar-sentinel"' in content
    assert 'id="tlb-sentinel"' in content and 'id="tlb-count"' in content
    assert 'data-lucky-btn' not in content
    assert 'js/trophy-lists.js' in content and 'js/browse-filters.js' in content
    assert '{#' not in content and '{%' not in content


# ── Whale safety ──────────────────────────────────────────────────────────────────────────────────

def test_query_count_is_whale_safe(client, django_assert_max_num_queries):
    """One page of cards costs a bounded number of queries regardless of catalogue size --
    the house ceiling every browse grid pins (the name-batch-once pin above guards only the
    observation table; THIS one catches a dropped select_related going 30-wide)."""
    for i in range(60):
        g = GameFactory(title_platform=['PS5'])
        _observe(g, f'Whale List {i}')

    with django_assert_max_num_queries(20):
        resp = client.get(URL)
    assert resp.status_code == 200


def test_raw_response_is_deferred():
    """The ~30 KB IGDB blob is deferred off the queryset (never read by the cards; the
    May-2026 OOM rule)."""
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    from tests.factories import IGDBMatchFactory
    from trophies.views.game_views import TrophyListsBrowseView

    game = GameFactory(title_platform=['PS5'])
    IGDBMatchFactory(concept=game.concept)

    req = RequestFactory().get(URL)
    req.user = AnonymousUser()
    v = TrophyListsBrowseView()
    v.request = req
    v.kwargs = {}
    first = v.get_queryset().first()
    assert 'raw_response' in first.concept.igdb_match.get_deferred_fields()
