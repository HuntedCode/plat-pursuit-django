"""Tests for the rebuilt Browse Games page (GamesListView, /games/).

Covers the data/behavior contract that the from-scratch --pp-* rebuild had to
preserve: the .pp-gcard grid renders the card contract, the platform/sort/
platinum filters still narrow/order, the bare-/games/ dispatch redirect fires,
and infinite scroll works (the HtmxListMixin XHR guard returns the rows partial;
a past-end page 404s). Also pins whale-safety (bounded query count).
"""

import itertools
import pathlib
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    BadgeSeriesFactory,
    ConceptFactory,
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
    a = GameFactory(title_name='Split A', title_platform=['PS5'], played_count=50,
                    concept__unified_title='Split A')
    b = GameFactory(title_name='Split B', title_platform=['PS5'], played_count=5,
                    concept__unified_title='Split B')
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
    ps3 = GameFactory(concept=ps5.concept, title_name='Promo PS3', title_platform=['PS3'],
                      played_count=1)

    resp = client.get(reverse('games_list'), {'platform': 'PS3'})

    # Context-level on purpose: both siblings share the concept, so the card's displayed title
    # (the concept's) and its platform union (the WORK's) cannot discriminate the elected row.
    assert [g.pk for g in resp.context['object_list']] == [ps3.pk]
    assert 'data-result-count="1"' in resp.content.decode()


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


def test_a_blank_np_work_never_renders_a_card_and_never_breaks_the_fold(client):
    """Final-audit #1/#2: the election population carries the DESTINATION's np floor, so a work
    whose only row has no np id -- unrenderable on every page it could link -- never becomes a
    card that 404s on click. And a blank-np SIBLING neither elects nor damages the viewer's
    progress fold: the elected row's own entry survives (the fold seeds with it)."""
    GameFactory(title_name='Ghost Work', title_platform=['PS5'], np_communication_id=None,
                concept__unified_title='Ghost Work')
    elected = GameFactory(title_platform=['PS5'], played_count=50,
                          concept__unified_title='Solid Work')
    GameFactory(concept=elected.concept, title_platform=['PS5'], np_communication_id='',
                played_count=999)   # blank-np sibling: floored out despite the higher played
    viewer = ProfileFactory(is_linked=True)
    ProfileGameFactory(profile=viewer, game=elected, progress=60)
    client.force_login(viewer.user)

    url, params = _url()
    resp = client.get(url, params)
    content = resp.content.decode()

    assert 'Ghost Work' not in content, 'an unrenderable work must not mint a card'
    assert 'Solid Work' in content
    assert resp.context['user_game_map'][elected.id]['progress'] == 60, (
        'the fold must never delete the elected row\'s own progress'
    )


def test_trust_split_renders_the_accepted_two_destination_cards(client):
    """Final-audit #3, pinning the PLAN's recorded accepted risk: an untrusted-match concept
    sharing a trusted concept's igdb id elects its OWN card (its page is the c/ destination)
    while the trusted card's UNGATED count includes the untrusted lists (its switcher shows
    them). Two cards because there ARE two live destination pages; self-heals on graduation."""
    trusted = GameFactory(title_platform=['PS5'], played_count=50,
                          concept__unified_title='Trusted Work')
    IGDBMatchFactory(concept=trusted.concept, igdb_id=82001)
    shadow = GameFactory(title_platform=['PS5'], played_count=5,
                         concept__unified_title='Shadow Work')
    IGDBMatchFactory(concept=shadow.concept, igdb_id=82001, status='pending_review')

    url, params = _url()
    resp = client.get(url, params)
    content = resp.content.decode()

    assert content.count('pp-gcard__title') == 2
    assert 'href="/games/82001/"' in content
    assert f'href="/games/c/{shadow.concept.concept_id}/"' in content
    assert resp.context['list_count_map'][trusted.id] == 2, 'the trusted switcher lists both'
    assert resp.context['list_count_map'][shadow.id] == 1, 'the c/ page lists only its own'


def test_condensed_card_links_titles_and_chips(client):
    """The card tweaks (owner: 'not a ton of tweaks to the actual cards'): the condensed card
    links the concept GAME page, titles by the concept, shows the partition's platform UNION in
    display order plus the lists chip -- and region chips are gone (the card describes the WORK;
    region stays a filter)."""
    a = GameFactory(title_name='Chip List EU', title_platform=['PS5'], played_count=90,
                    is_regional=True, region=['EU'])
    a.concept.unified_title = 'Chip Work'
    a.concept.save()
    IGDBMatchFactory(concept=a.concept, igdb_id=79001)
    GameFactory(concept=a.concept, title_name='Chip List JP', title_platform=['PS4', 'PSVR2'],
                played_count=5, is_regional=True, region=['JP'])

    url, params = _url()
    content = client.get(url, params).content.decode()

    assert 'href="/games/79001/"' in content, 'the condensed card must link the Game page'
    assert 'Chip Work' in content and 'Chip List EU' not in content
    # The Trophy Lists page's list-identity mode must never leak here: a planted PSN observation
    # does not retitle a condensed card (isolation between the card's three modes).
    from trophies.models import PSNTitleObservation
    PSNTitleObservation.objects.create(
        np_communication_id=a.np_communication_id, game=a, source='trophy_titles',
        title_name_raw='Observed Leak Name', content_hash=f'{a.np_communication_id}:leak:tt',
    )
    content = client.get(url, params).content.decode()
    assert 'Observed Leak Name' not in content and 'Chip Work' in content
    # Union in display order: PS5, PS4, PSVR2 (PSVR2 slots after PS4, before PS3).
    plats = content.split('pp-gcard__plats')[1].split('</span></span>')[0]
    assert plats.index('PS5') < plats.index('PS4') < plats.index('PSVR2')
    assert '2 lists' in content
    assert 'pp-gcard__plat--region' not in content and '>EU<' not in content
    assert 'pp-gcard__region' not in content   # the list-identity mode's chips stay on ITS page

    # A single-list work shows no chip.
    lone = GameFactory(title_name='Lone Work', title_platform=['PS5'])
    content = client.get(url, params).content.decode()
    assert '1 lists' not in content


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
    """The grid renders .pp-gcard cells with the game title, the hooks row and the footer, and the
    infinite-scroll sentinel. A game card stands for a whole game page, so it carries no one-list trophy
    breakdown, and nothing on it says what the game lacks (DS-06): no "No badges", no "No contract", no
    star for an unrated game."""
    GameFactory(title_name='Render Check Game', title_platform=['PS5'], has_trophy_groups=True,
                concept__unified_title='Render Check Game')
    url, params = _url()

    resp = client.get(url, params)
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'pp-gcard' in content
    assert 'Render Check Game' in content
    assert 'class="pp-gcard__hooks"' in content and 'class="pp-gcard__foot"' in content
    assert 'pp-gcard__tro' not in content and 'pp-gcard__dlc' not in content   # one-list cards only
    assert 'No badges' not in content and 'No contract' not in content
    assert 'pp-gcard__fact' not in content   # unrated: no rating at all, not a star and a dash
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

    assert 'In 1 badge series: Hooked Franchise' in content   # the series name rides the chip's title
    assert 'pp-gcard__badges-n' in content   # the count element
    assert 'Hooked Contract' in content      # the contract chip
    assert 'No contract' not in content      # placeholder replaced by the real chip
    assert '6,000 XP' in content and '6000 XP' not in content   # thousands separator, as Career writes it (DS-14)


def test_card_dlc_tag_shows_pack_count(client):
    """A game with DLC trophy groups shows the count on the DLC tag (`DLC ×N`), counting only groups beyond
    the base 'default' group (4 groups incl. default -> ×3), batched whale-safely via dlc_map. A game with
    no trophy groups shows no DLC tag at all (no count element). The tag rides the one-list breakdown, so
    this reads Trophy Lists browse: a game card (Browse Games) carries neither."""
    from trophies.models import TrophyGroup

    game = GameFactory(title_name='DLC Count Game', title_platform=['PS5'], has_trophy_groups=True)
    TrophyGroup.objects.create(game=game, trophy_group_id='default')  # base game -- excluded from the count
    TrophyGroup.objects.create(game=game, trophy_group_id='001')
    TrophyGroup.objects.create(game=game, trophy_group_id='002')
    TrophyGroup.objects.create(game=game, trophy_group_id='003')
    GameFactory(title_name='No DLC Game', title_platform=['PS5'], has_trophy_groups=False)

    content = client.get(reverse('trophy_lists')).content.decode()

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
    GameFactory(title_name='Unplayed Game', title_platform=['PS5'],  # played_count defaults to 0
                concept__unified_title='Unplayed Game')
    url, params = _url()
    content = client.get(url, params).content.decode()

    assert 'Unplayed Game' in content
    assert 'pp-gcard__foot' in content
    assert 'pp-gcard__plat' in content        # platform chip still shows
    assert 'pp-gcard__players' not in content  # but no player count


def test_active_filter_chips_render(client):
    """Applied filters show as dismissable chips + a Clear all, platform included since it stopped being an
    invisible default (DS-01): a platform-only page shows that platform's chip and nothing else."""
    GameFactory(title_name='Chip Game', title_platform=['PS5'])

    filtered = client.get(reverse('games_list'),
                          {'platform': 'PS5', 'show_only_platinum': 'on', 'in_badge': 'on'}).content.decode()
    assert 'pp-gbrowse__achips' in filtered      # the container
    assert 'Has platinum' in filtered
    assert 'In a badge' in filtered
    assert 'Clear all' in filtered

    platform_only = client.get(reverse('games_list'), {'platform': 'PS5'}).content.decode()
    chips = platform_only.split('id="gbrowse-active-filters"')[1].split('</div>')[0]
    assert 'PS5' in chips, 'the chosen platform is not visible beside the results'
    assert 'Has platinum' not in platform_only


def test_active_filter_chip_remove_url_drops_only_that_filter():
    """Each chip's remove_url drops its own filter (+ resets page) but keeps the others; Clear all drops every
    filter, platform included (a chip since DS-01), and keeps the search query."""
    from django.test import RequestFactory
    from trophies.forms import GameSearchForm
    from trophies.views.browse_helpers import get_active_filter_chips

    req = RequestFactory().get('/games/', {'platform': 'PS5', 'show_only_platinum': 'on', 'in_badge': 'on',
                                           'query': 'jak'})
    result = get_active_filter_chips(req, GameSearchForm(req.GET))

    labels = {c['label']: c for c in result['filter_chips']}
    assert 'Has platinum' in labels and 'In a badge' in labels
    plat_remove = labels['Has platinum']['remove_url']
    assert 'show_only_platinum' not in plat_remove   # removing the platinum chip drops that param
    assert 'in_badge=on' in plat_remove              # but keeps the others
    assert 'page=1' in plat_remove                   # page reset
    assert 'show_only_platinum' not in result['filter_clear_url']   # Clear all drops content filters
    assert 'in_badge' not in result['filter_clear_url']
    assert 'platform' not in result['filter_clear_url']             # ...and the platform
    assert 'query=jak' in result['filter_clear_url']                # but keeps the search
    assert 'PS5' in labels and 'platform=PS5' not in labels['PS5']['remove_url']


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
    GameFactory(title_name='Current Gen', title_platform=['PS5'], concept__unified_title='Current Gen')
    GameFactory(title_name='Retro Relic', title_platform=['PS3'], concept__unified_title='Retro Relic')

    url = reverse('games_list')
    ps5 = client.get(url, {'platform': 'PS5'}).content.decode()
    assert 'Current Gen' in ps5
    assert 'Retro Relic' not in ps5

    ps3 = client.get(url, {'platform': 'PS3'}).content.decode()
    assert 'Retro Relic' in ps3
    assert 'Current Gen' not in ps3


def test_sort_alpha_orders(client):
    """The default alphabetical sort orders titles A->Z."""
    GameFactory(title_name='Zephyr Drift', title_platform=['PS5'], concept__unified_title='Zephyr Drift')
    GameFactory(title_name='Alpha Ascent', title_platform=['PS5'], concept__unified_title='Alpha Ascent')
    url, params = _url(sort='alpha')

    content = client.get(url, params).content.decode()

    assert content.index('Alpha Ascent') < content.index('Zephyr Drift')


def test_platinum_only_filter(client):
    """show_only_platinum=on keeps only games that define a platinum trophy."""
    plat_game = GameFactory(title_name='Platinum Path', title_platform=['PS5'],
                            concept__unified_title='Platinum Path')
    TrophyFactory(game=plat_game, trophy_type='platinum')
    GameFactory(title_name='No Platinum Here', title_platform=['PS5'],
                concept__unified_title='No Platinum Here')

    url, params = _url(show_only_platinum='on')
    content = client.get(url, params).content.decode()

    assert 'Platinum Path' in content
    assert 'No Platinum Here' not in content


def test_in_badge_filter(client):
    """?in_badge=on narrows to games whose concept is in a live badge series (the toggle that replaced the
    removed 'Pick a Badge' modal)."""
    with_b = GameFactory(title_name='Has Badge', title_platform=['PS5'],
                         concept__unified_title='Has Badge')
    GameFactory(title_name='No Badge', title_platform=['PS5'], concept__unified_title='No Badge')
    stage = StageFactory(series_slug='in-badge-series')
    stage.concepts.add(with_b.concept)
    _live_badge_series('in-badge-series', 'In Badge')

    content = client.get(reverse('games_list'), {'platform': 'PS5', 'in_badge': 'on'}).content.decode()

    assert 'Has Badge' in content
    assert 'No Badge' not in content


def test_in_contract_filter(client):
    """?in_contract=on narrows to games whose concept has a live contract."""
    from trophies.models import Contract

    with_c = GameFactory(title_name='Has Contract', title_platform=['PS5'],
                         concept__unified_title='Has Contract')
    GameFactory(title_name='No Contract', title_platform=['PS5'], concept__unified_title='No Contract')
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
    # unified_title too: the condensed card titles by CONCEPT (phase 3).
    game_a = GameFactory(title_name='Job A Game', title_platform=['PS5'],
                         concept__unified_title='Job A Game')
    game_b = GameFactory(title_name='Job B Game', title_platform=['PS5'],
                         concept__unified_title='Job B Game')
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
    game = GameFactory(title_name='In Progress Game', title_platform=['PS5'],
                       concept__unified_title='In Progress Game')
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


# ── Countless scroll pagination (the beta slow-scroll fix) ────────────────────────────────────────

def test_scroll_fetches_never_run_the_count(client):
    """The beta slowness: every scroll fetch paid a COUNT(*) over the WINDOWED election
    subquery -- the whole election executed a second time per page, for a number the scroller
    never reads. A page>=2 XHR fetch must run ZERO count queries and exactly ONE window
    (election) query; the filter swap (htmx, page 1) keeps the real count for its header tick."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for i in range(35):
        GameFactory(title_platform=['PS5'], played_count=i)
    url, params = _url(page='2')

    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(url, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    assert resp.status_code == 200
    counts = [q['sql'] for q in ctx.captured_queries if q['sql'].startswith('SELECT COUNT')]
    assert not counts, f'scroll fetch ran a COUNT: {counts[0][:120]}'
    windows = [q['sql'] for q in ctx.captured_queries if 'ROW_NUMBER' in q['sql']]
    assert len(windows) == 1, f'expected ONE election execution, saw {len(windows)}'

    # The filter swap keeps the REAL population count (its header tick reads it).
    url, params = _url()
    swap = client.get(url, params, HTTP_HX_REQUEST='true')
    assert 'data-result-count="35"' in swap.content.decode()


def test_scroll_fetches_carry_x_has_next(client):
    """The stop-one-fetch-early header (the career scroller's protocol, now on the browse
    family): '1' while more rows exist, '0' on the exact last page -- each saved fetch is a
    whole election execution."""
    for i in range(35):
        GameFactory(title_platform=['PS5'])   # 35 rows, 30/page -> page 2 is the last

    url, params = _url(page='2')
    last = client.get(url, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    assert last.status_code == 200
    assert last['X-Has-Next'] == '0'
    assert last.content.decode().count('pp-gcard__title') == 5

    for i in range(30):
        GameFactory(title_platform=['PS5'])   # 65 rows -> page 2 now has a page 3 behind it
    more = client.get(url, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    assert more['X-Has-Next'] == '1'
    assert more.content.decode().count('pp-gcard__title') == 30


def test_exact_multiple_last_page_says_no_next(client):
    """The probe-row boundary (the audit's surviving mutant): with rows an exact multiple of
    the page size, the last page's slice comes back FULL -- has_next must still be '0' (strict
    `>` on the probe), or every exact-multiple wall costs the extra fetch this exists to kill."""
    for i in range(60):
        GameFactory(title_platform=['PS5'])   # exactly 2 full pages

    url, params = _url(page='2')
    resp = client.get(url, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    assert resp.status_code == 200
    assert resp.content.decode().count('pp-gcard__title') == 30
    assert resp['X-Has-Next'] == '0'


def test_bare_games_renders_everything_in_place(client):
    """A bare /games/ is a 200 (SEO Lane 1: the old force-302 meant the hub's canonical URL never
    returned a page) and filters NOTHING (DS-01): a PS3 game is on it. It used to bind PS4 + PS5
    invisibly, so the same search found 11 games from a link and 3 from the page's own box. The
    signed-in saved-defaults redirect survives, pinned in test_seo_lane1."""
    GameFactory(concept=ConceptFactory(unified_title='Old Gen Game'), title_name='Old Gen Game', title_platform=['PS3'])
    resp = client.get(reverse('games_list'))

    assert resp.status_code == 200
    assert 'Old Gen Game' in resp.content.decode()


def test_site_heartbeat_computes_no_catalog_coverage():
    """Both catalogue-coverage counts went with the header tiles that were their only readers: the
    badge-series one with Browse Games' (DS-04), the contract one with the Jobs header's (PR-02). An
    empty DB still computes the rest without a crash."""
    from core.services.site_heartbeat import compute_site_heartbeat

    expanded = compute_site_heartbeat().get('expanded', {})

    assert 'games_in_badges' not in expanded
    assert 'games_in_contracts' not in expanded
    assert expanded['ratings_total']['value'] == 0


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


def test_header_has_no_tiles_even_when_the_heartbeat_is_warm(client):
    """DS-04: the header's only number is the games the grid shows. The four catalogue tiles counted trophy
    LISTS beside a grid of games ("15,824 games shown" under "Total games 40,125"), so they are gone, warm
    cache or not."""
    from django.core.cache import cache
    from django.utils import timezone

    GameFactory(title_name='Header Grid Game', title_platform=['PS5'], concept__unified_title='Header Grid Game')
    now = timezone.now()
    key = f"site_heartbeat_{now.date().isoformat()}_{now.hour:02d}"
    cache.set(key, {
        'always': {'games_total': {'value': 12847, 'delta': 156}},
        'expanded': {'ratings_total': {'value': 312}},
    }, 120)
    try:
        url, params = _url()
        content = client.get(url, params).content.decode()
    finally:
        cache.delete(key)

    assert 'class="scard' not in content     # bare 'scard' also matches Discard in the base modal
    assert 'Total games' not in content and '12,847' not in content
    assert 'id="gbrowse-count"' in content and 'game shown' in content   # the one count: 1 game, as the grid
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


def _letter_row_rules():
    """Every `.pp-gbrowse__fchips--letters` rule in game-browse.css, comment-stripped and flattened, each
    with the at-rule prelude it sits in (None at top level). Comments are stripped because the rule's own
    comment names the declarations this test asserts on."""
    root = pathlib.Path(__file__).resolve().parents[2]
    css = (root / 'static' / 'css' / 'components' / 'game-browse.css').read_text(encoding='utf-8')
    flat = ' '.join(re.sub(r'/\*.*?\*/', '', css, flags=re.S).split())
    sel = re.escape('.pp-gbrowse__fchips--letters')
    rules = [(m.group(1), m.group(2)) for m in
             re.finditer(r'(@media [^{]*\{ )?' + sel + r' \{([^}]*)\}', flat)]
    shrink = re.findall(sel + r' > \.pp-bgal__chip \{([^}]*)\}', flat)
    chip = re.findall(r'(?<![\w-])\.pp-bgal__chip \{([^}]*)\}', ' '.join(re.sub(
        r'/\*.*?\*/', '', (root / 'static' / 'css' / 'components' / 'browse-gallery.css')
        .read_text(encoding='utf-8'), flags=re.S).split()))
    return rules, shrink, chip


def test_the_letter_row_cannot_widen_the_page():
    """iPhone REPORT, 2026-10-11: opening the filter drawer zoomed iOS Safari out to a third of its scale.
    The phone-width letter row is a nowrap `overflow-x: auto` strip of ~1170px in a ~330px box, and a scroll
    strip in the page column leaks its intrinsic width upward: a mobile browser answers by widening the
    layout viewport (emulated iPhone 12: `innerWidth` 1175 against a 390 layout viewport). The same row
    ships on Browse Games, the genre/theme pages, and Trophy Lists.

    Source text because the suite has no browser, so this pins the MECHANISM, verified in Chromium under
    real device emulation (iPhone SE/12/14 Pro Max, Pixel 7, iPad Mini) on all three pages.
    `contain: layout size` is the measured fix (the Plat Calendar crest row, same defect). Both keywords are
    load-bearing, and size containment sizes the box as if empty, so the intrinsic height must equal one
    chip. It is switched off at md:, where the row wraps and its height comes from its chips."""
    rules, shrink, chip = _letter_row_rules()

    base = [body for at, body in rules if at is None]
    assert len(base) == 1, 'the phone letter-row rule moved or split: %d matches' % len(base)
    assert 'overflow-x: auto' in base[0], 'the phone row is no longer a scroll strip; revisit this pin'
    assert 'contain: layout size' in base[0], 'the strip can widen the layout viewport again'
    assert 'contain-intrinsic-height: 30px' in base[0], 'size containment with no matching height clips the chips'

    # The 30px IS one chip: 5px + 5px padding, 12px text on an 18px line, 1px + 1px border. A chip restyle
    # must revisit the contained height, so the chip's box is pinned alongside it.
    assert len(chip) == 1, 'the base chip rule moved or split: %d matches' % len(chip)
    assert 'padding: 5px 12px' in chip[0] and 'font-size: 12px' in chip[0], (
        'the chip box changed; re-measure it and update contain-intrinsic-height on the letter row'
    )

    # A shrinking chip breaks "0-9" onto two lines, which overflows the contained height.
    assert len(shrink) == 1 and 'flex-shrink: 0' in shrink[0], 'letter chips can shrink and wrap their label'

    md = [body for at, body in rules if at and 'min-width: 768px' in at]
    assert len(md) == 1 and 'contain: none' in md[0], (
        'size containment survives into the band where the row wraps, which collapses its height'
    )


# ── flag badges and the filters that must match them (owner, 2026-10-24) ─────────────────────────────────────

def _work(title, *lists):
    """One game page: a trusted match over a concept with one list per kwargs dict."""
    from tests.factories import ConceptFactory, IGDBMatchFactory
    concept = ConceptFactory(unified_title=title)
    IGDBMatchFactory(concept=concept, status='accepted')
    return [GameFactory(concept=concept, title_name=f'{title} {i}', title_platform=['PS5'], **kw)
            for i, kw in enumerate(lists)]


def test_a_card_wears_a_flag_only_when_every_list_on_its_page_has_it(client):
    _work('Pulled Everywhere', {'is_delisted': True}, {'is_delisted': True})
    _work('Pulled On One', {'is_delisted': True}, {'is_delisted': False})

    content = client.get(reverse('games_list')).content.decode()

    assert content.count('<use href="#pp-gflag-delisted"/>') == 1


def test_the_delisted_filter_matches_the_badge_on_browse_games_and_the_list_on_trophy_lists(client):
    """Browse Games is one card per Game page, so "Delisted" there means every list on the page; Trophy Lists
    is one card per list, so it still means that list. A filtered card must always wear the badge it was
    filtered for."""
    _work('Pulled Everywhere', {'is_delisted': True}, {'is_delisted': True})
    _work('Pulled On One', {'is_delisted': True}, {'is_delisted': False})

    games = client.get(reverse('games_list'), {'show_delisted': 'on'}).content.decode()
    lists = client.get(reverse('trophy_lists'), {'show_delisted': 'on'}).content.decode()

    assert 'Pulled Everywhere' in games and 'Pulled On One' not in games
    assert 'Pulled On One 0' in lists, "the delisted list is still that list's own result"


def test_no_page_version_follows_the_game_pages_membership():
    """The database twin of `page_version_rows`: trusted matches group by IGDB id across concepts, anything else
    by concept, and a list with no np id is not on the page."""
    from django.db.models import Q
    from tests.factories import ConceptFactory, IGDBMatchFactory
    from trophies.models import Game
    from trophies.services.game_grouping_service import no_page_version

    def matches():
        return set(Game.objects.filter(is_delisted=True).filter(no_page_version(Q(is_delisted=False)))
                   .values_list('title_name', flat=True))

    trusted_a = ConceptFactory()
    match = IGDBMatchFactory(concept=trusted_a, status='accepted')
    trusted_b = ConceptFactory()
    IGDBMatchFactory(concept=trusted_b, igdb_id=match.igdb_id, status='auto_accepted')
    GameFactory(concept=trusted_a, title_name='T delisted', is_delisted=True)
    GameFactory(concept=trusted_b, title_name='T sibling listed')            # another concept, same page
    untrusted = ConceptFactory()
    IGDBMatchFactory(concept=untrusted, status='pending_review')
    GameFactory(concept=untrusted, title_name='U delisted', is_delisted=True)
    lone = ConceptFactory()
    GameFactory(concept=lone, title_name='L delisted', is_delisted=True)
    GameFactory(concept=lone, title_name='L unlisted np', np_communication_id='')  # not a list on the page
    GameFactory(concept=None, title_name='N delisted', is_delisted=True)

    assert matches() == {'U delisted', 'L delisted', 'N delisted'}
