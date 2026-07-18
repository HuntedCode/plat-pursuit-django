"""Tests for the rebuilt Browse Games page (GamesListView, /games/).

Covers the data/behavior contract that the from-scratch --pp-* rebuild had to
preserve: the .pp-gcard grid renders the card contract, the platform/sort/
platinum filters still narrow/order, the bare-/games/ dispatch redirect fires,
and infinite scroll works (the HtmxListMixin XHR guard returns the rows partial;
a past-end page 404s). Also pins whale-safety (bounded query count).
"""

import pytest
from django.urls import reverse

from tests.factories import (
    BadgeFactory,
    GameFactory,
    ProfileFactory,
    ProfileGameFactory,
    StageFactory,
    TrophyFactory,
)

pytestmark = pytest.mark.django_db

GRID_PARTIAL = 'trophies/partials/game_list/browse_results.html'
FULL_PAGE = 'trophies/game_list.html'


def _url(**params):
    # Always pass a param so dispatch() doesn't 302 to the defaults redirect.
    base = {'platform': 'PS5'}
    base.update(params)
    return reverse('games_list'), base


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
    from trophies.models import Contract, ContractMembership, Job

    game = GameFactory(title_name='Hooked Game', title_platform=['PS5'])
    stage = StageFactory(series_slug='hooked-series')
    stage.concepts.add(game.concept)
    BadgeFactory(name='Hooked Franchise', series_slug='hooked-series', tier=1,
                 is_live=True, badge_type='franchise')
    job = Job.objects.first() or Job.objects.create(slug='test-job', name='Test Job', discipline='combat')
    contract = Contract.objects.create(name='Hooked Contract', slug='hooked-contract', is_live=True)
    contract.jobs.add(job)
    ContractMembership.objects.create(concept=game.concept, contract=contract)

    url, params = _url()
    content = client.get(url, params).content.decode()

    assert 'Hooked Franchise' in content     # the badge series name
    assert 'pp-gcard__badges-n' in content   # the count element (not the placeholder)
    assert 'Hooked Contract' in content      # the contract chip
    assert 'No contract' not in content      # placeholder replaced by the real chip


def test_card_dlc_tag_shows_pack_count(client):
    """A game with DLC trophy groups (beyond the base 'default' group) shows the count on the DLC tag
    (`DLC ×N`), batched whale-safely via dlc_map. A game with no extra groups shows no tag."""
    from trophies.models import TrophyGroup

    game = GameFactory(title_name='DLC Count Game', title_platform=['PS5'], has_trophy_groups=True)
    TrophyGroup.objects.create(game=game, trophy_group_id='default')  # base game -- excluded
    TrophyGroup.objects.create(game=game, trophy_group_id='001')
    TrophyGroup.objects.create(game=game, trophy_group_id='002')
    TrophyGroup.objects.create(game=game, trophy_group_id='003')

    url, params = _url()
    content = client.get(url, params).content.decode()

    assert 'pp-gcard__dlc-n' in content       # the count element rendered
    assert '&times;3' in content or '×3' in content  # 3 DLC packs (base 'default' excluded)


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


def test_in_contract_filter(client):
    """?in_contract=on narrows to games whose concept has a live contract."""
    from trophies.models import Contract, ContractMembership

    with_c = GameFactory(title_name='Has Contract', title_platform=['PS5'])
    GameFactory(title_name='No Contract', title_platform=['PS5'])
    contract = Contract.objects.create(name='C1', slug='c1', is_live=True)
    ContractMembership.objects.create(concept=with_c.concept, contract=contract)

    content = client.get(reverse('games_list'), {'platform': 'PS5', 'in_contract': 'on'}).content.decode()

    assert 'Has Contract' in content
    assert 'No Contract' not in content


def test_contract_jobs_filter(client):
    """?contract_jobs=<slug> narrows to games whose contract levels that job."""
    from trophies.models import Contract, ContractMembership, Job

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
    ContractMembership.objects.create(concept=game_a.concept, contract=ca)
    ContractMembership.objects.create(concept=game_b.concept, contract=cb)

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
    assert 'pp-gcard' in resp.content.decode()


def test_xhr_past_end_page_404s(client):
    """A page past the last one 404s, which is how InfiniteScroller detects end-of-list."""
    GameFactory(title_platform=['PS5'])
    url, params = _url(page='999')

    resp = client.get(url, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    assert resp.status_code == 404


def test_bare_games_redirects_to_defaults(client):
    """A bare /games/ (no query) 302-redirects to the modern-platform defaults."""
    resp = client.get(reverse('games_list'))

    assert resp.status_code == 302
    assert 'platform=' in resp['Location']


def test_site_heartbeat_has_catalog_coverage():
    """compute_site_heartbeat runs the new catalogue-coverage queries (games in badge series / contracts,
    which feed the Browse Games header) and exposes them under `expanded`. Empty DB -> 0, no crash."""
    from core.services.site_heartbeat import compute_site_heartbeat

    expanded = compute_site_heartbeat().get('expanded', {})

    assert expanded.get('games_in_badges', {}).get('value') == 0
    assert expanded.get('games_in_contracts', {}).get('value') == 0


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
    assert '{#' not in content                # multi-line comment leak guard (header block)


def test_game_card_workshop_renders(client):
    """The /design/game-card/ workshop renders a card without crashing. A bare game (no badges/contract)
    hits the 'plain' branch, so this exercises the card partial + its empty-slot placeholders -- and guards
    against multi-line {# #} comment leaks (which only surface when a card actually renders)."""
    from django.urls import reverse

    GameFactory(title_name='Workshop Plain Game', title_platform=['PS5'])

    resp = client.get(reverse('design_game_card'))
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'Game card' in content
    assert 'Workshop Plain Game' in content       # a real card rendered (the plain branch)
    assert 'No badges' in content                 # empty-slot placeholder present
    assert '{#' not in content                    # no raw Django comment markers leaked


def test_query_count_is_whale_safe(client, django_assert_max_num_queries):
    """Render cost stays bounded regardless of catalogue size (no per-card N+1): one page of 30 cards costs
    the same whether there are 10 or 60 games, INCLUDING the batched badge + contract pursuer-hook maps
    (a fixed handful of queries over the page's concepts, never per-card)."""
    games = GameFactory.create_batch(60, title_platform=['PS5'])
    # Put a few games in badge series so the badge-map queries actually run (still bounded).
    stage = StageFactory(series_slug='whale-series')
    BadgeFactory(name='Whale Badge', series_slug='whale-series', tier=1, is_live=True)
    for g in games[:5]:
        stage.concepts.add(g.concept)
    url, params = _url()

    # Page (count + 30 rows) + rating/user maps + badge (2) + contract (1) batched maps + the contract
    # discipline roster (1, full page) + session/misc. Bounded, not per-card.
    with django_assert_max_num_queries(20):
        resp = client.get(url, params)
    assert resp.status_code == 200
