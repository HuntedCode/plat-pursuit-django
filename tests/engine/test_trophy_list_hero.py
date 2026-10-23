"""The trophy list pass (2026-10-22; docs/design/reviews/game-and-list-lower.md, GL-01..GL-11).

- The Trophy List page's hero is the Game page's, asked of ONE list: its status card (with the bar by trophy group,
  the grade counts and My stats), its platinum stat, its actions; the invitation when there is no hunter.
- One control row on both pages (All / Unearned, Sort, More filters), filtered by ONE function.
- PSN's own weighting for the group headers and the hero's bar, so a list shows one percentage (GL-11).
- Earned rows say "Earned <date>" with the exact time on hover; the community numbers sit behind Ranks.
"""
import datetime
from unittest import mock

import pytest
from django.utils import timezone

from tests.factories import (
    CompanyFactory, ConceptCompanyFactory, ConceptFactory, EarnedTrophyFactory, GameFactory, ProfileFactory,
    ProfileGameFactory, TrophyFactory, TrophyGroupFactory,
)
from trophies.forms import GameDetailForm
from trophies.models import IGDBMatch
from trophies.util_modules.trophy_progress import psn_progress
from trophies.views.game_views import filter_and_sort_rows
from trophies.views.hero_context import list_status, plat_rarity

pytestmark = pytest.mark.django_db

DEFINED = {'bronze': 4, 'silver': 1, 'gold': 1, 'platinum': 1}


# ── psn_progress: PSN's percentage from counts (GL-11) ─────────────────────────────────────────────

def test_psn_progress_weighs_grades_and_leaves_the_platinum_out():
    # Jak 3 on dev: 18/34 bronze, 4/9 silver, 1/3 gold, 0/1 platinum. PSN says 45%; a count says 49%.
    assert psn_progress({'bronze': 34, 'silver': 9, 'gold': 3, 'platinum': 1},
                        {'bronze': 18, 'silver': 4, 'gold': 1}) == 45
    # Golf on dev: 22/24, 19/24, 7/7, 1/1 -> PSN's 89%.
    assert psn_progress({'bronze': 24, 'silver': 24, 'gold': 7, 'platinum': 1},
                        {'bronze': 22, 'silver': 19, 'gold': 7, 'platinum': 1}) == 89


def test_psn_progress_floors_but_reads_one_once_anything_is_earned():
    assert psn_progress({'bronze': 300}, {'bronze': 1}) == 1     # 0.33% -> PSN shows 1, not 0
    assert psn_progress({'bronze': 300}, {}) == 0
    assert psn_progress({'bronze': 3}, {'bronze': 2}) == 66       # never rounds up into "done"


def test_psn_progress_edge_shapes():
    assert psn_progress({'platinum': 1}, {'platinum': 1}) == 100   # nothing to weigh but the platinum
    assert psn_progress({'platinum': 1}, {}) == 0
    assert psn_progress({'bronze': 2}, {'bronze': 9}) == 100       # an earned count past the defined one is capped
    assert psn_progress(None, None) == 0
    assert psn_progress({'bronze': 'x', 'silver': '2'}, {'silver': 2}) == 100


# ── hero_context: the platinum stat and the status card, per list ──────────────────────────────────

def test_plat_rarity_reads_the_lists_platinum():
    game = GameFactory(defined_trophies=DEFINED)
    TrophyFactory(game=game, trophy_type='platinum', trophy_earn_rate=0.4, trophy_rarity=0)
    out = plat_rarity(game)
    assert out == {'rate': pytest.approx(0.4), 'label': 'Ultra Rare', 'grade': 'mythic', 'list_label': ''}


def test_plat_rarity_without_a_platinum_counts_the_trophies():
    game = GameFactory(defined_trophies={'bronze': 10, 'silver': 2, 'gold': 0, 'platinum': 0})
    assert plat_rarity(game, 'PS4') == {'none': True, 'total': 12, 'list_label': 'PS4'}


@pytest.mark.parametrize('rate, tier', [(0.0, 1), (5.0, None), (5.0, 7)])
def test_plat_rarity_is_left_out_until_psn_reports_it(rate, tier):
    game = GameFactory(defined_trophies=DEFINED)
    TrophyFactory(game=game, trophy_type='platinum', trophy_earn_rate=rate, trophy_rarity=tier)
    assert plat_rarity(game) is None


def test_plat_rarity_of_an_unsynced_list_is_left_out():
    """An unsynced list is not a list without a platinum: no "No platinum" on a list we know nothing about."""
    assert plat_rarity(GameFactory(defined_trophies={})) is None


@pytest.mark.parametrize('progress, earned, platted, state', [
    (89, 49, True, 'plat'), (100, 7, False, 'complete'), (45, 23, False, 'started'),
    (0, 1, False, 'started'),   # PSN floors a sliver to 0% while a trophy is earned
    (0, 0, False, 'owned'),
])
def test_list_status_states(progress, earned, platted, state):
    game = GameFactory(defined_trophies=DEFINED)
    out = list_status(game, progress, earned, platted=platted)
    assert out['state'] == state and out['total'] == 7 and out['to_go'] == max(7 - earned, 0)


# ── filter_and_sort_rows: the one filter both pages run (GL-10) ────────────────────────────────────

def _rows():
    return [
        {'trophy_id': 1, 'trophy_type': 'platinum', 'trophy_name': 'Plat', 'trophy_group_id': 'default',
         'trophy_earn_rate': 0.5, 'earn_rate': 0.01, 'earned_count': 1},
        {'trophy_id': 2, 'trophy_type': 'gold', 'trophy_name': 'beta', 'trophy_group_id': 'default',
         'trophy_earn_rate': 4.0, 'earn_rate': 0.2, 'earned_count': 5},
        {'trophy_id': 3, 'trophy_type': 'bronze', 'trophy_name': 'Alpha', 'trophy_group_id': '001',
         'trophy_earn_rate': 60.0, 'earn_rate': 0.9, 'earned_count': 9},
    ]


def _ids(params, earned=None):
    return [t['trophy_id'] for t in filter_and_sort_rows(_rows(), GameDetailForm(params), earned or {})]


def test_filter_unearned_and_earned():
    earned = {2: {'earned': True, 'earned_date_time': timezone.now()}}
    assert _ids({'earned': 'unearned'}, earned) == [1, 3]
    assert _ids({'earned': 'earned'}, earned) == [2]
    # Signed out there is nothing earned to filter by: the rows pass through.
    assert _ids({'earned': 'unearned'}) == [1, 2, 3]


def test_filter_type_rarity_and_dlc():
    assert _ids({'trophy_type': ['gold', 'bronze']}) == [2, 3]
    assert _ids({'rarity_bracket': ['ultra_rare']}) == [1]          # <= 1%
    assert _ids({'rarity_bracket': ['very_rare', 'common']}) == [2, 3]
    assert _ids({'dlc_filter': 'base'}) == [1, 2]
    assert _ids({'dlc_filter': 'dlc'}) == [3]


def test_sorts():
    assert _ids({'sort': 'alpha'}) == [3, 2, 1]
    assert _ids({'sort': 'type'}) == [1, 2, 3]
    assert _ids({'sort': 'psn_rarity'}) == [1, 2, 3]
    assert _ids({'sort': 'earned_count'}) == [3, 2, 1]
    when = timezone.now()
    earned = {3: {'earned': True, 'earned_date_time': when - datetime.timedelta(days=2)},
              1: {'earned': True, 'earned_date_time': when}}
    assert _ids({'sort': 'earned_date'}, earned) == [3, 1, 2]       # unearned last


def test_an_invalid_form_leaves_the_rows_alone():
    assert _ids({'sort': 'nonsense', 'earned': 'unearned'}, {1: {'earned': True}}) == [1, 2, 3]


# ── The Trophy List page's hero ────────────────────────────────────────────────────────────────────

def _list(client, game, **extra):
    return client.get(f'/games/{game.np_communication_id}/', **extra).content.decode()


def _hero(html):
    start = html.index('class="gd-hero gp-hero')
    return html[start:html.index('</section>', start)]


def _hunter(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    return profile


def _owned(profile, *, progress, earned=(), plat=False):
    """A list the hunter owns, with `earned` (grades) earned and the platinum if `plat`."""
    game = GameFactory(title_name='Hero List', title_platform=['PS4'], defined_trophies=DEFINED, played_count=3)
    TrophyGroupFactory(game=game, trophy_group_id='default', defined_trophies=DEFINED)
    trophies = [TrophyFactory(game=game, trophy_type=t, trophy_earn_rate=20.0, trophy_rarity=2)
                for t in ['bronze'] * 4 + ['silver', 'gold']]
    platinum = TrophyFactory(game=game, trophy_type='platinum', trophy_earn_rate=4.2, trophy_rarity=1)
    left = list(earned)
    for t in trophies:
        if t.trophy_type in left:
            left.remove(t.trophy_type)
            EarnedTrophyFactory(profile=profile, trophy=t)
    if plat:
        EarnedTrophyFactory(profile=profile, trophy=platinum,
                            earned_date_time=timezone.make_aware(datetime.datetime(2026, 6, 25, 16, 31)))
    ProfileGameFactory(profile=profile, game=game, progress=progress, has_plat=plat)
    return game


def test_a_platinumed_list_leads_with_the_hunter(client):
    hunter = _hunter(client)
    game = _owned(hunter, progress=89, earned=['bronze'] * 4 + ['silver'], plat=True)

    hero = _hero(_list(client, game))

    assert 'gp-hero--list' in hero and 'gp-status--plat gp-status--detail' in hero
    assert 'You platinumed this' in hero
    # The date, PSN's percentage, and what is left: one line.
    assert '<time datetime="2026-06-25">Jun 25, 2026</time> &middot; 89% &middot; 1 to go' in hero
    # The detail rows: the bar, the grade counts (earned/defined), My stats with the progress it opens.
    assert 'gp-status__groupbar' in hero and 'gp-status__counts' in hero
    assert '<b>4</b>/4' in hero and '<b>1</b>/1' in hero
    status = hero[hero.index('class="gp-status '):hero.index('class="gp-hero__side')]
    assert 'data-stats-open' in status and 'My stats' in status
    # My stats left the action row: Add to list and Report (and Share once the card is earned).
    actions = hero[hero.index('class="gp-actions"'):]
    assert 'data-stats-open' not in actions and 'Add to list' in actions and 'game-flag-btn' in actions


def test_the_status_card_in_progress(client):
    hunter = _hunter(client)
    game = _owned(hunter, progress=45, earned=['bronze', 'bronze', 'gold'])

    hero = _hero(_list(client, game))

    assert 'gp-status--started' in hero and '45% &middot; 4 trophies to go' in hero
    assert '3 of 7 earned' in hero
    assert 'gp-status__bar"' not in hero, 'the list card draws its bar by group, not the Game page\'s simple bar'


def test_a_list_the_hunter_does_not_own(client):
    _hunter(client)
    game = GameFactory(defined_trophies=DEFINED)

    hero = _hero(_list(client, game))

    assert 'Not in your library yet' in hero
    assert 'data-stats-open' not in hero and 'gp-status--detail' not in hero


def test_another_hunters_list_names_them_and_never_offers_their_claim(client):
    them = ProfileFactory(is_linked=True, psn_username='rival', display_psn_username='Rival')
    game = _owned(them, progress=89, earned=['bronze'], plat=True)
    _hunter(client)

    with mock.patch('trophies.views.game_views.GameDetailView._build_pursuit_context',
                    return_value={'pursuit_contract_state': {'status': 'claimable'}, 'pursuit_jobs': []}):
        html = client.get(f'/games/{game.np_communication_id}/{them.psn_username}/', HTTP_CF_RAY='test').content.decode()
    hero = _hero(html)

    assert 'Rival platinumed this' in hero and "Rival's stats" in hero
    assert 'gp-status__claim' not in hero, 'only they can claim it'


def test_the_claim_line_on_the_hunters_own_list(client):
    hunter = _hunter(client)
    game = _owned(hunter, progress=89, earned=['bronze'], plat=True)
    with mock.patch('trophies.views.game_views.GameDetailView._build_pursuit_context',
                    return_value={'pursuit_contract_state': {'status': 'claimable'}, 'pursuit_jobs': []}):
        hero = _hero(_list(client, game))
    assert 'gp-status__claim' in hero and 'Contract ready to claim' in hero


def test_the_platinum_stat_and_facts_line(client):
    concept = ConceptFactory(release_date=datetime.date(2017, 6, 1))   # the list's date falls back to its concept's
    ConceptCompanyFactory(concept=concept, company=CompanyFactory(name='Naughty Dog'), is_developer=True)
    game = GameFactory(concept=concept, defined_trophies=DEFINED, played_count=327)
    TrophyFactory(game=game, trophy_type='platinum', trophy_earn_rate=4.2, trophy_rarity=1)

    hero = _hero(_list(client, game))

    assert 'gp-rarity" data-rarity="rare"' in hero and '4.2%' in hero and '<b>Very Rare</b>' in hero
    meta = hero[hero.index('gp-meta'):hero.index('</p>', hero.index('gp-meta'))]
    assert 'Naughty Dog' in meta and '>2017<' in meta
    assert 'href="?view=leaderboard"' in meta and '327 players' in meta


def test_a_list_without_a_platinum_says_so_in_the_corner(client):
    game = GameFactory(defined_trophies={'bronze': 10, 'silver': 2, 'gold': 0, 'platinum': 0})
    hero = _hero(_list(client, game))
    assert 'gp-rarity--none' in hero and '<b>No platinum</b>' in hero and '12 trophies' in hero


def test_the_versions_link_opens_versions_and_editions(client):
    concept = ConceptFactory()
    game = GameFactory(concept=concept, title_platform=['PS4'], defined_trophies=DEFINED)
    GameFactory(concept=concept, title_platform=['PS5'], defined_trophies=DEFINED)

    html = _list(client, game)

    assert 'data-versions-open' in _hero(html) and '1 version<' in _hero(html)
    assert 'id="gd-versions-modal"' in html


def test_signed_out_the_status_slot_invites(client):
    game = GameFactory(defined_trophies=DEFINED, played_count=3)
    hero = _hero(_list(client, game))
    assert 'gp-status--invite' in hero and 'Track your platinum journey' in hero
    assert '4 bronze &middot; 1 silver &middot; 1 gold &middot; 1 platinum to earn' in hero
    assert 'Start tracking free' in hero and 'gp-actions' not in hero

    named = _hero(_list(client, GameFactory(defined_trophies=DEFINED, played_count=327)))
    assert '<b>327</b> hunters here are chasing this platinum' in named


def test_signed_in_unlinked_the_invitation_asks_to_link(client):
    client.force_login(ProfileFactory(is_linked=False).user)
    hero = _hero(_list(client, GameFactory(defined_trophies=DEFINED)))
    assert 'Link your PSN' in hero and 'Start tracking free' not in hero


def test_the_old_hero_furniture_is_gone(client):
    hunter = _hunter(client)
    game = _owned(hunter, progress=45, earned=['bronze'])
    html = _list(client, game)
    assert 'gp-hero--list' in html, 'the page rendered its hero'
    for gone in ('gd-spine', 'gd-prog', 'gd-hero__facts', 'gd-hero__players', 'gd-cta', 'gd-outlook',
                 'gd-shots', 'gd-toolbar', 'gd-listmeta', 'Not in a badge series', 'No screenshots'):
        assert gone not in html, gone


def test_a_filter_swap_skips_the_hero(client):
    """A filter change re-renders #browse-results alone, so the hero's queries are not run."""
    hunter = _hunter(client)
    game = _owned(hunter, progress=45, earned=['bronze'])
    with mock.patch('trophies.views.game_views.plat_rarity', side_effect=AssertionError('hero built')):
        resp = client.get(f'/games/{game.np_communication_id}/?earned=unearned',
                          HTTP_HX_REQUEST='true', HTTP_HX_TARGET='browse-results')
    assert resp.status_code == 200 and 'gp-hero' not in resp.content.decode()


# ── Below the hero ─────────────────────────────────────────────────────────────────────────────────

def test_the_control_row_on_the_list_page(client):
    hunter = _hunter(client)
    game = _owned(hunter, progress=45, earned=['bronze'])

    html = _list(client, game)
    row = html[html.index('class="gd-controls"'):html.index('id="browse-results"')]

    assert 'data-gd-count>7<' in row and 'All</span>' in row and 'Unearned</span>' in row
    assert 'name="sort"' in row and 'data-gd-more' in row and 'data-gd-more-panel hidden' in row
    assert 'name="trophy_type"' in row and 'name="rarity_bracket"' in row
    assert 'name="dlc_filter"' not in row, 'no DLC field for a list without DLC'
    assert 'data-gd-listlink' not in row, 'this page IS the list: no link to itself'

    unearned = _list(client, game, QUERY_STRING='earned=unearned')
    assert 'data-gd-count>6<' in unearned and 'value="unearned" class="sr-only" checked' in unearned


def test_signed_out_there_is_no_all_unearned_switch(client):
    game = GameFactory(defined_trophies=DEFINED)
    TrophyFactory(game=game)
    html = _list(client, game)
    row = html[html.index('class="gd-controls"'):]
    assert 'name="earned"' not in row[:row.index('data-gd-more-panel')]


def test_an_old_earned_only_link_shows_what_it_applies(client):
    """"Earned only" left the controls, but a link that asks for it still filters: it shows while it applies."""
    hunter = _hunter(client)
    game = _owned(hunter, progress=45, earned=['bronze'])
    html = _list(client, game, QUERY_STRING='earned=earned')
    assert 'value="earned" class="sr-only" checked' in html and 'data-gd-count>1<' in html


def test_earned_rows_carry_the_date_with_the_time_on_hover(client):
    hunter = _hunter(client)
    game = _owned(hunter, progress=89, earned=['bronze'], plat=True)

    html = _list(client, game)

    assert 'Earned <time datetime="2026-06-25T16:31:00' in html and '>Jun 25, 2026</time>' in html
    assert 'title="Jun. 25, 2026, 04:31 PM (UTC)">Earned' in html
    assert 'gd-trophy__got' not in html, 'the green edge is the one earned mark'


def test_group_headers_use_psns_percentage(client):
    """GL-11: 2 bronze and the gold of 4 bronze, 1 silver, 1 gold is 120 of 180 points -> 66%; a count said 50%."""
    hunter = _hunter(client)
    game = _owned(hunter, progress=66, earned=['bronze', 'bronze', 'gold'])
    html = _list(client, game)
    assert 'gd-group__pct pp-tally">66%' in html


# ── The Game page's viewport takes the same filter ─────────────────────────────────────────────────

def _game_page(igdb_id):
    concept = ConceptFactory()
    IGDBMatch.objects.create(concept=concept, igdb_id=igdb_id, status='accepted')
    return GameFactory(concept=concept, title_platform=['PS4'], defined_trophies=DEFINED)


def test_the_game_page_viewport_filters(client):
    game = _game_page(980001)
    hunter = _hunter(client)
    for t in ('bronze', 'gold'):
        trophy = TrophyFactory(game=game, trophy_type=t)
        if t == 'bronze':
            EarnedTrophyFactory(profile=hunter, trophy=trophy)
    ProfileGameFactory(profile=hunter, game=game, progress=10)

    swap = client.get(f'/games/980001/?list={game.np_communication_id}&earned=unearned',
                      HTTP_HX_REQUEST='true', HTTP_HX_TARGET='gp-viewport').content.decode()

    assert '<html' not in swap and swap.count('<article') == 1
    assert 'data-count="1"' in swap and f'data-list-url="/games/{game.np_communication_id}/"' in swap

    page = client.get('/games/980001/?earned=unearned').content.decode()
    assert page.count('<article') == 1, 'without JS the page renders filtered'
    assert 'value="unearned" class="sr-only" checked' in page
    assert f'name="list" value="{game.np_communication_id}"' in page


# ── Source pins for the Game page's swap logic (no JS runner; see project_js_source_text_pins) ─────

def test_the_filter_form_and_the_picker_share_one_sync_scope():
    """Two scopes let a filter and a list switch land in either order, one list's trophies under the other's
    controls (the audit's race). One scope: the newer request replaces the older."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / 'templates' / 'trophies'
    page = (root / 'game_page.html').read_text(encoding='utf-8')
    picker = (root / 'partials' / 'game_page' / 'list_switcher.html').read_text(encoding='utf-8')
    form = page[page.index('<form id="gp-filter-form"'):page.index('>', page.index('<form id="gp-filter-form"'))]
    assert 'hx-sync="#gp-viewport:replace"' in form
    assert 'hx-sync="#gp-viewport:replace"' in picker


def test_the_swap_reads_the_list_from_the_final_request_path():
    """For the GET filter form, requestPath is the bare hx-get path; the list rides in the query htmx appends.
    Reading requestPath sent every filter back to the default list (caught in the browser, 2026-10-22)."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / 'static' / 'js' / 'game-page.js').read_text(encoding='utf-8')
    assert 'e.detail.pathInfo.finalRequestPath || e.detail.pathInfo.requestPath' in js
    swap = js[js.index('if (np === lastNp) return;'):]
    assert 'resetControls();' in swap[:swap.index('PP.syncViewParam')], 'only a list switch resets the row'
