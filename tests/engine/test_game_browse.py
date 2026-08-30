"""Tests for the rebuilt Browse Games page (GamesListView, /games/).

Covers the data/behavior contract that the from-scratch --pp-* rebuild had to
preserve: the .pp-gcard grid renders the card contract, the platform/sort/
platinum filters still narrow/order, the bare-/games/ dispatch redirect fires,
and infinite scroll works (the HtmxListMixin XHR guard returns the rows partial;
a past-end page 404s). Also pins whale-safety (bounded query count).
"""

import itertools

import pytest
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    BadgeSeriesFactory,
    GroupBadgeFactory,
    PlatformGroupFactory,
    GameFactory,
    IGDBMatchFactory,
    ProfileFactory,
    ProfileGameFactory,
    StageFactory,
    TrophyFactory,
)

pytestmark = pytest.mark.django_db

GRID_PARTIAL = 'trophies/partials/game_list/browse_results.html'
FULL_PAGE = 'trophies/game_list.html'

_igdb_seq = itertools.count(10001)


def _live_badge_series(slug, name, badge_type='series'):
    """A badge SERIES with one live group badge (grouping-badge system) -- the shape the card badge-band + the
    ?in_badge filter now read."""
    series = BadgeSeriesFactory(series_slug=slug, name=name, badge_type=badge_type)
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)
    return series


def _make_member(concept, contract):
    """Attach `concept` to `contract` the igdb-derived way: anchor it + give it an IGDBMatch that
    shares the contract's igdb_id (assigning the contract a fresh id if it has none)."""
    if contract.igdb_id is None:
        contract.igdb_id = next(_igdb_seq)
        contract.save(update_fields=['igdb_id'])
    concept.anchor_migration_completed_at = timezone.now()
    concept.save(update_fields=['anchor_migration_completed_at'])
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id)


def _url(**params):
    # Always pass a param so dispatch() doesn't 302 to the defaults redirect.
    base = {'platform': 'PS5'}
    base.update(params)
    return reverse('games_list'), base


# ── the condensed catalogue (Games/Trophy Lists IA phase 3) ─────────────────────────────────────

def test_split_concepts_sharing_an_igdb_id_render_one_card(client):
    """THE condensing rule: two separate Concepts (a deliberate trophy-count split) whose trusted
    matches share one igdb id are ONE page -- so the catalogue shows ONE card, and the paginator
    counts identities, not lists. Both fixtures carry PS5 or _url()'s default platform filter
    would silently pre-elect for us (the recorded fixture wrinkle)."""
    a = GameFactory(title_name='Split A', title_platform=['PS5'], played_count=50)
    b = GameFactory(title_name='Split B', title_platform=['PS5'], played_count=5)
    shared = next(_igdb_seq)
    IGDBMatchFactory(concept=a.concept, igdb_id=shared)
    IGDBMatchFactory(concept=b.concept, igdb_id=shared)

    url, params = _url()
    content = client.get(url, params).content.decode()

    assert content.count('pp-gcard__title') == 1
    assert 'data-result-count="1"' in content
    assert 'Split A' in content, 'the most-played real row must be the elected card'


def test_sibling_lists_of_one_concept_render_one_card(client):
    """Regional/platform siblings of ONE concept condense the same way (the concept: partition)."""
    a = GameFactory(title_name='Sibling EU', title_platform=['PS5'], played_count=90)
    GameFactory(concept=a.concept, title_name='Sibling NA', title_platform=['PS5'], played_count=10)

    url, params = _url()
    content = client.get(url, params).content.decode()

    assert content.count('pp-gcard__title') == 1
    assert 'data-result-count="1"' in content


def test_platform_filter_promotes_the_matching_sibling(client):
    """Pre-election filtering IS the promotion rule: ?platform=PS3 removes the PS5 sibling from
    the election population, so the PS3 row wins its partition and the card shows the version
    you asked for. Passes its OWN platform param -- never _url()'s PS5 default."""
    ps5 = GameFactory(title_name='Promo PS5', title_platform=['PS5'], played_count=100)
    GameFactory(concept=ps5.concept, title_name='Promo PS3', title_platform=['PS3'], played_count=1)

    content = client.get(reverse('games_list'), {'platform': 'PS3'}).content.decode()

    assert 'Promo PS3' in content and 'Promo PS5' not in content
    assert 'data-result-count="1"' in content


def test_list_count_uses_the_destination_pages_ungated_membership(client):
    """The trust-divergence pin: the election partition is trust-GATED, but the card's "N lists"
    must use the DESTINATION page's UNGATED membership -- an untrusted-match sibling sharing the
    igdb id is invisible to the election yet IS a switcher entry on the Game page the card links,
    so it counts here. A blank-np sibling counts nowhere (GamePageView's floor)."""
    a = GameFactory(title_name='Gated A', title_platform=['PS5'], played_count=50)
    IGDBMatchFactory(concept=a.concept, igdb_id=78001)
    untrusted = GameFactory(title_platform=['PS4'])
    IGDBMatchFactory(concept=untrusted.concept, igdb_id=78001, status='pending_review')
    GameFactory(concept=a.concept, title_platform=['PS3'], np_communication_id='')  # np floor

    url, params = _url()
    resp = client.get(url, params)

    assert resp.context['condensed_cards'] is True
    assert resp.context['list_count_map'][a.id] == 2, (
        'trusted elected row + untrusted same-igdb sibling; the blank-np row never counts'
    )
    assert resp.context['platform_union_map'][a.id] == ['PS5', 'PS4']


def test_viewer_best_progress_folds_across_siblings(client):
    """A hunter whose progress lives on a NON-elected sibling still gets their fill on the
    elected card: partition-best progress + any-sibling has_plat, in the same value shape the
    card's five-state fill logic already reads (game_grouping_service precedent)."""
    elected = GameFactory(title_name='Fold Elected', title_platform=['PS5'], played_count=90)
    sibling = GameFactory(concept=elected.concept, title_name='Fold Sibling',
                          title_platform=['PS5'], played_count=1)
    viewer = ProfileFactory(is_linked=True)
    ProfileGameFactory(profile=viewer, game=sibling, progress=73, has_plat=True)
    client.force_login(viewer.user)

    url, params = _url()
    resp = client.get(url, params)

    row = resp.context['user_game_map'][elected.id]
    assert row['progress'] == 73 and row['has_plat'] is True
    assert '73%' in resp.content.decode()


def test_seo_item_list_claims_the_game_page_urls(client):
    """The ItemList schema must agree with what the grid links: the concept Game page for
    matched rows, the c/ page for unmatched concepts."""
    g = GameFactory(title_name='Schema Game', title_platform=['PS5'])
    IGDBMatchFactory(concept=g.concept, igdb_id=77001)
    stub = GameFactory(title_name='Schema Stub', title_platform=['PS5'])

    url, params = _url()
    resp = client.get(url, params)

    urls = {row['url'] for row in resp.context['seo_item_list']}
    assert '/games/77001/' in urls
    assert f'/games/c/{stub.concept.concept_id}/' in urls
    assert not any(f'/games/{g.np_communication_id}/' == u for u in urls), (
        'the ItemList must not claim list URLs for concept-bearing rows'
    )


def test_lucky_redirects_to_the_game_page(client):
    g = GameFactory(title_name='Lucky Game', title_platform=['PS5'])
    IGDBMatchFactory(concept=g.concept, igdb_id=77002)

    resp = client.get(reverse('random_game'), {'platform': 'PS5'})

    assert resp.status_code == 302 and resp.url == '/games/77002/'


def test_grid_renders_card_contract(client):
    """The grid renders .pp-gcard cells with the game title, the colored B/S/G/P trophy counts, the pursuer-
    hook placeholders (Browse Games sets show_game_hooks), and the infinite-scroll sentinel."""
    GameFactory(title_name='Render Check Game', title_platform=['PS5'], has_trophy_groups=True)
    url, params = _url()

    resp = client.get(url, params)
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'pp-gcard' in content
    assert 'Render Check Game' in content
    assert 'pp-gcard__tro' in content        # colored B/S/G/P trophy counts
    assert 'pp-gcard__dlc' in content         # DLC tag (game has trophy groups)
    assert 'No badges' in content            # badge-band placeholder (show_game_hooks on, game in none)
    assert 'No contract' in content          # contract placeholder
    assert 'gbrowse-sentinel' in content
    # No raw Django comment markers leak (multi-line {# #} is NOT a comment in Django and ships as text).
    assert '{#' not in content
    assert 'browse results partial' not in content


def test_card_shows_badges_and_contract(client):
    """A game in a badge series + a live contract shows the badge count/name + the contract on its card
    (the batched pursuer hooks)."""
    from trophies.models import Contract, Job

    game = GameFactory(title_name='Hooked Game', title_platform=['PS5'])
    stage = StageFactory(series_slug='hooked-series')
    stage.concepts.add(game.concept)
    _live_badge_series('hooked-series', 'Hooked Franchise', 'franchise')
    job = Job.objects.first() or Job.objects.create(slug='test-job', name='Test Job', discipline='combat')
    contract = Contract.objects.create(name='Hooked Contract', slug='hooked-contract', is_live=True)
    contract.jobs.add(job)
    _make_member(game.concept, contract)

    url, params = _url()
    content = client.get(url, params).content.decode()

    assert 'Hooked Franchise' in content     # the badge series name
    assert 'pp-gcard__badges-n' in content   # the count element (not the placeholder)
    assert 'Hooked Contract' in content      # the contract chip
    assert 'No contract' not in content      # placeholder replaced by the real chip


def test_card_dlc_tag_shows_pack_count(client):
    """A game with DLC trophy groups shows the count on the DLC tag (`DLC ×N`), counting only groups beyond
    the base 'default' group (4 groups incl. default -> ×3), batched whale-safely via dlc_map. A game with
    no trophy groups shows no DLC tag at all (no count element)."""
    from trophies.models import TrophyGroup

    game = GameFactory(title_name='DLC Count Game', title_platform=['PS5'], has_trophy_groups=True)
    TrophyGroup.objects.create(game=game, trophy_group_id='default')  # base game -- excluded from the count
    TrophyGroup.objects.create(game=game, trophy_group_id='001')
    TrophyGroup.objects.create(game=game, trophy_group_id='002')
    TrophyGroup.objects.create(game=game, trophy_group_id='003')
    GameFactory(title_name='No DLC Game', title_platform=['PS5'], has_trophy_groups=False)

    url, params = _url()
    content = client.get(url, params).content.decode()

    assert 'pp-gcard__dlc-n' in content       # the count element rendered for the DLC game
    assert '&times;3' in content or '×3' in content  # base 'default' excluded -> 3, not 4
    assert content.count('pp-gcard__dlc-n') == 1  # only the DLC game carries a count (no-groups game omits it)


def test_card_footer_shows_platform_and_players(client):
    """The card footer (.pp-gcard__foot) carries the platform chips on the left and, when the game has
    players, a compact player count on the right. Plain denormed Game columns -> no extra queries."""
    from trophies.models import Game

    game = GameFactory(title_name='Footer Game', title_platform=['PS5'])
    Game.objects.filter(pk=game.pk).update(played_count=128400)
    url, params = _url()
    content = client.get(url, params).content.decode()

    assert 'pp-gcard__foot' in content
    assert 'pp-gcard__plat' in content       # a platform chip rendered
    assert 'pp-gcard__players' in content
    assert '128.4k' in content                # compact_number(128400)


def test_card_footer_shows_platform_without_players(client):
    """A game nobody has played still shows the platform footer -- just no player-count span."""
    GameFactory(title_name='Unplayed Game', title_platform=['PS5'])  # played_count defaults to 0
    url, params = _url()
    content = client.get(url, params).content.decode()

    assert 'Unplayed Game' in content
    assert 'pp-gcard__foot' in content
    assert 'pp-gcard__plat' in content        # platform chip still shows
    assert 'pp-gcard__players' not in content  # but no player count


def test_active_filter_chips_render(client):
    """Applied content filters show as dismissable chips + a Clear all; a platform-only (scope) page shows
    none (search + platform/regions/sort are excluded from chips)."""
    GameFactory(title_name='Chip Game', title_platform=['PS5'])

    filtered = client.get(reverse('games_list'),
                          {'platform': 'PS5', 'show_only_platinum': 'on', 'in_badge': 'on'}).content.decode()
    assert 'pp-gbrowse__achips' in filtered      # the container
    assert 'Has platinum' in filtered
    assert 'In a badge' in filtered
    assert 'Clear all' in filtered

    scope_only = client.get(reverse('games_list'), {'platform': 'PS5'}).content.decode()
    assert 'Clear all' not in scope_only          # no content filters -> no chips
    assert 'Has platinum' not in scope_only


def test_active_filter_chip_remove_url_drops_only_that_filter():
    """Each chip's remove_url drops its own filter (+ resets page) but keeps the others; Clear all drops all
    content filters while keeping scope (platform)."""
    from django.test import RequestFactory
    from trophies.forms import GameSearchForm
    from trophies.views.browse_helpers import get_active_filter_chips

    req = RequestFactory().get('/games/', {'platform': 'PS5', 'show_only_platinum': 'on', 'in_badge': 'on'})
    result = get_active_filter_chips(req, GameSearchForm(req.GET))

    labels = {c['label']: c for c in result['filter_chips']}
    assert 'Has platinum' in labels and 'In a badge' in labels
    plat_remove = labels['Has platinum']['remove_url']
    assert 'show_only_platinum' not in plat_remove   # removing the platinum chip drops that param
    assert 'in_badge=on' in plat_remove              # but keeps the others
    assert 'page=1' in plat_remove                   # page reset
    assert 'show_only_platinum' not in result['filter_clear_url']   # Clear all drops content filters
    assert 'in_badge' not in result['filter_clear_url']
    assert 'platform=PS5' in result['filter_clear_url']             # ...but keeps scope


def test_genre_chip_shows_name_not_id(client):
    """A genre chip shows the genre NAME, not its numeric id (int-keyed choices vs str cleaned value)."""
    from trophies.models import Genre

    genre = Genre.objects.create(name='Roguelike', igdb_id=990001, slug='roguelike')
    GameFactory(title_name='Genre Chip Game', title_platform=['PS5'])
    content = client.get(reverse('games_list'), {'platform': 'PS5', 'genres': str(genre.id)}).content.decode()

    assert 'Roguelike' in content            # the name, not the raw id
    assert 'pp-gbrowse__achip"' in content   # a chip pill rendered


def test_active_filters_container_not_duplicated_on_full_page(client):
    """The #gbrowse-active-filters container renders exactly once on a full (non-HTMX) page load -- the OOB
    copy is HTMX-only, so a filtered bookmark URL doesn't show a duplicated chip row."""
    GameFactory(title_platform=['PS5'])
    content = client.get(reverse('games_list'), {'platform': 'PS5', 'in_badge': 'on'}).content.decode()

    assert content.count('id="gbrowse-active-filters"') == 1


def test_platform_filter_narrows(client):
    """?platform=PS5 shows only PS5 games; ?platform=PS3 shows only PS3 games."""
    GameFactory(title_name='Current Gen', title_platform=['PS5'])
    GameFactory(title_name='Retro Relic', title_platform=['PS3'])

    url = reverse('games_list')
    ps5 = client.get(url, {'platform': 'PS5'}).content.decode()
    assert 'Current Gen' in ps5
    assert 'Retro Relic' not in ps5

    ps3 = client.get(url, {'platform': 'PS3'}).content.decode()
    assert 'Retro Relic' in ps3
    assert 'Current Gen' not in ps3


def test_sort_alpha_orders(client):
    """The default alphabetical sort orders titles A->Z."""
    GameFactory(title_name='Zephyr Drift', title_platform=['PS5'])
    GameFactory(title_name='Alpha Ascent', title_platform=['PS5'])
    url, params = _url(sort='alpha')

    content = client.get(url, params).content.decode()

    assert content.index('Alpha Ascent') < content.index('Zephyr Drift')


def test_platinum_only_filter(client):
    """show_only_platinum=on keeps only games that define a platinum trophy."""
    plat_game = GameFactory(title_name='Platinum Path', title_platform=['PS5'])
    TrophyFactory(game=plat_game, trophy_type='platinum')
    GameFactory(title_name='No Platinum Here', title_platform=['PS5'])

    url, params = _url(show_only_platinum='on')
    content = client.get(url, params).content.decode()

    assert 'Platinum Path' in content
    assert 'No Platinum Here' not in content


def test_in_badge_filter(client):
    """?in_badge=on narrows to games whose concept is in a live badge series (the toggle that replaced the
    removed 'Pick a Badge' modal)."""
    with_b = GameFactory(title_name='Has Badge', title_platform=['PS5'])
    GameFactory(title_name='No Badge', title_platform=['PS5'])
    stage = StageFactory(series_slug='in-badge-series')
    stage.concepts.add(with_b.concept)
    _live_badge_series('in-badge-series', 'In Badge')

    content = client.get(reverse('games_list'), {'platform': 'PS5', 'in_badge': 'on'}).content.decode()

    assert 'Has Badge' in content
    assert 'No Badge' not in content


def test_in_contract_filter(client):
    """?in_contract=on narrows to games whose concept has a live contract."""
    from trophies.models import Contract

    with_c = GameFactory(title_name='Has Contract', title_platform=['PS5'])
    GameFactory(title_name='No Contract', title_platform=['PS5'])
    contract = Contract.objects.create(name='C1', slug='c1', is_live=True)
    _make_member(with_c.concept, contract)

    content = client.get(reverse('games_list'), {'platform': 'PS5', 'in_contract': 'on'}).content.decode()

    assert 'Has Contract' in content
    assert 'No Contract' not in content


def test_contract_jobs_filter(client):
    """?contract_jobs=<slug> narrows to games whose contract levels that job."""
    from trophies.models import Contract, Job

    jobs = list(Job.objects.exclude(is_fallback=True)[:2])
    if len(jobs) < 2:
        pytest.skip('needs >= 2 seeded non-fallback jobs')
    job_a, job_b = jobs[0], jobs[1]
    game_a = GameFactory(title_name='Job A Game', title_platform=['PS5'])
    game_b = GameFactory(title_name='Job B Game', title_platform=['PS5'])
    ca = Contract.objects.create(name='CA', slug='ca', is_live=True)
    ca.jobs.add(job_a)
    cb = Contract.objects.create(name='CB', slug='cb', is_live=True)
    cb.jobs.add(job_b)
    _make_member(game_a.concept, ca)
    _make_member(game_b.concept, cb)

    content = client.get(reverse('games_list'),
                         {'platform': 'PS5', 'contract_jobs': job_a.slug}).content.decode()

    assert 'Job A Game' in content
    assert 'Job B Game' not in content


def test_authenticated_progress_renders(client):
    """A signed-in user's per-game progress shows on the card."""
    profile = ProfileFactory()
    client.force_login(profile.user)
    game = GameFactory(title_name='In Progress Game', title_platform=['PS5'])
    ProfileGameFactory(profile=profile, game=game, progress=42, has_plat=False)

    url, params = _url()
    content = client.get(url, params).content.decode()

    assert 'In Progress Game' in content
    assert '42%' in content


def test_xhr_returns_rows_partial(client):
    """The InfiniteScroller's XHR (X-Requested-With) gets the rows-only partial,
    NOT the full page -- this is the HtmxListMixin guard added for infinite scroll."""
    GameFactory(title_name='Scroll Target', title_platform=['PS5'])
    url, params = _url()

    resp = client.get(url, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    templates = {t.name for t in resp.templates if t.name}

    assert resp.status_code == 200
    assert GRID_PARTIAL in templates
    assert FULL_PAGE not in templates
    body = resp.content.decode()
    assert 'pp-gcard' in body
    # The result count for this filter rides the grid (data-result-count), which the header count-up
    # reads on afterSwap to tick old -> new.
    assert 'data-result-count' in body


def test_xhr_past_end_page_404s(client):
    """A page past the last one 404s, which is how InfiniteScroller detects end-of-list."""
    GameFactory(title_platform=['PS5'])
    url, params = _url(page='999')

    resp = client.get(url, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    assert resp.status_code == 404


def test_bare_games_renders_defaults_in_place(client):
    """A bare /games/ renders the modern-platform default view as a 200 (SEO Lane 1: the old
    force-302 meant the hub's canonical URL never returned a page). The defaults still apply --
    the form binds them and the template surfaces them via history.replaceState. The signed-in
    saved-defaults redirect survives, pinned in test_seo_lane1."""
    resp = client.get(reverse('games_list'))

    assert resp.status_code == 200
    assert 'history.replaceState' in resp.content.decode()


def test_site_heartbeat_has_catalog_coverage():
    """compute_site_heartbeat runs the new catalogue-coverage queries (games in badge series / contracts,
    which feed the Browse Games header) and exposes them under `expanded`. Empty DB -> 0, no crash."""
    from core.services.site_heartbeat import compute_site_heartbeat

    expanded = compute_site_heartbeat().get('expanded', {})

    assert expanded.get('games_in_badges', {}).get('value') == 0
    assert expanded.get('games_in_contracts', {}).get('value') == 0


def test_sticky_minibar_and_sentinel_render(client):
    """The page renders the shared sticky mini-bar (identity + live count + Filters reach) and its
    StickyReveal sentinel, so the toolbar re-surfaces once you scroll past it on this long page."""
    GameFactory(title_name='Minibar Game', title_platform=['PS5'])
    url, params = _url()

    content = client.get(url, params).content.decode()

    assert 'pp-minibar' in content
    assert 'data-sticky-reveal' in content
    assert 'data-minibar-count' in content            # live result count in the bar
    assert 'data-minibar-filters' in content          # the Filters reach button
    assert 'data-minibar-search' in content           # proxied quick-search
    assert 'data-minibar-sort' in content             # proxied quick-sort (desktop)
    assert 'id="gbrowse-minibar-sentinel"' in content # the StickyReveal sentinel


def test_empty_state_shows_reset_cta_when_filtered(client):
    """A filtered search that returns nothing shows the 'Reset filters' recovery CTA; an unfiltered empty
    page does not (there'd be nothing to reset)."""
    GameFactory(title_name='Only Game', title_platform=['PS5'])

    # A query that matches nothing -> empty grid WITH an active filter -> reset CTA present.
    filtered = client.get(reverse('games_list'),
                          {'platform': 'PS5', 'query': 'zzz-no-such-game-zzz'}).content.decode()
    assert 'pp-gcard-empty' in filtered
    assert 'pp-gcard-empty__reset' in filtered
    assert 'Reset filters' in filtered


def test_header_scard_grid_renders_when_heartbeat_warm(client):
    """When the hourly site-heartbeat cache is warm, the Browse Games header renders the catalogue .scard
    grid (Total games / In badge series / In contracts / New this week) fed from those cached values -- zero
    request-path DB cost. Cold cache (no cron yet) simply omits the grid, so this pins the warm path."""
    from django.core.cache import cache
    from django.utils import timezone

    GameFactory(title_name='Header Grid Game', title_platform=['PS5'])
    now = timezone.now()
    key = f"site_heartbeat_{now.date().isoformat()}_{now.hour:02d}"
    cache.set(key, {
        'always': {'games_total': {'value': 12847, 'delta': 156}},
        'expanded': {
            'games_in_badges': {'value': 1204},
            'games_in_contracts': {'value': 312},
        },
    }, 120)
    try:
        url, params = _url()
        content = client.get(url, params).content.decode()
    finally:
        cache.delete(key)

    assert 'scard' in content                 # the Career-header stat-card treatment
    assert 'Total games' in content
    assert 'In badge series' in content
    assert 'In contracts' in content
    assert 'New this week' in content
    assert '12,847' in content                # catalogue total flows through from the cache
    assert '156' in content                   # games_total.delta -> catalog_games_new_this_week (the one non-obvious mapping)
    assert '{#' not in content                # multi-line comment leak guard (header block)


def test_query_count_is_whale_safe(client, django_assert_max_num_queries):
    """Render cost stays bounded regardless of catalogue size (no per-card N+1): one page of 30 cards costs
    the same whether there are 10 or 60 games, INCLUDING the batched badge + contract pursuer-hook maps
    (a fixed handful of queries over the page's concepts, never per-card)."""
    # played_count set so the community-stats footer actually RENDERS on every card (it's gated on
    # played_count) -- otherwise the footer's four denormed Game columns are never dereferenced and its
    # zero-extra-queries property goes unpinned.
    games = GameFactory.create_batch(60, title_platform=['PS5'], played_count=100)
    # Put a few games in badge series so the badge-map queries actually run (still bounded).
    stage = StageFactory(series_slug='whale-series')
    _live_badge_series('whale-series', 'Whale Badge')
    for g in games[:5]:
        stage.concepts.add(g.concept)
    url, params = _url()

    # Page (count + 30 rows) + rating/user maps + badge (2) + contract (1) batched maps + the contract
    # discipline roster (1, full page) + session/misc + the condensed grids' ONE sibling query
    # (list count / platform union / progress fold, phase 3). Bounded, not per-card.
    with django_assert_max_num_queries(21):
        resp = client.get(url, params)
    assert resp.status_code == 200
