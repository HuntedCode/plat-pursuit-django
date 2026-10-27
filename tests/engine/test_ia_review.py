"""The information architecture review (docs/design/reviews/ia.md, IA-01..IA-07).

Pins the footer built from the hub config, site search's Jobs and Game Lists groups, the trails inside My
Pursuit, the rail items that lit nothing, Community's order and the storefront's name.
"""
import re
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import RequestFactory
from django.urls import resolve, reverse

from core.hub_subnav import HUB_SUBNAV_CONFIG, footer_columns, resolve_hub_subnav
from gamelists.services import game_list_service as svc
from gamelists.models import GameList
from tests.factories import BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory, ProfileFactory
from trophies.models import Franchise, Job

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clear_cache():
    # site_suggest caches whole payloads by query, and its rate limits live in the cache too.
    cache.clear()
    yield
    cache.clear()


def _footer(client):
    body = client.get(reverse('about')).content.decode()
    return body[body.index('<footer'):body.index('</footer>')]


def _column(footer, label):
    start = footer.index(f'aria-label="{label} pages"')
    return footer[start:footer.index('</nav>', start)]


def _hub(key):
    return next(h for h in HUB_SUBNAV_CONFIG if h.key == key)


# --- IA-01: the footer is built from the hub config ---

def test_the_footer_lists_every_rail_item_a_member_would_see():
    columns = {c.key: c for c in footer_columns(is_authenticated=True, is_member=True)}

    assert [c.key for c in columns.values()] == [h.key for h in HUB_SUBNAV_CONFIG], 'nav order'
    for hub in HUB_SUBNAV_CONFIG:
        if hub.items:
            assert [i.label for i in columns[hub.key].items] == [i.label for i in hub.items], hub.key


def test_a_hub_with_no_rail_items_is_one_link_to_its_landing():
    col = next(c for c in footer_columns(is_authenticated=False) if c.key == 'leaderboards')

    assert [(i.label, i.url) for i in col.items] == [('Leaderboards', reverse('overall_badge_leaderboards'))]


def test_a_hub_whose_landing_cannot_be_reached_loses_its_column_not_the_site(monkeypatch):
    """The footer renders on nearly every page, so a reverse that raises would 500 all of them."""
    import dataclasses
    from core import hub_subnav

    broken = tuple(dataclasses.replace(h, landing='no_such_page') if h.key == 'leaderboards' else h
                   for h in HUB_SUBNAV_CONFIG)
    monkeypatch.setattr(hub_subnav, 'HUB_SUBNAV_CONFIG', broken)

    keys = [c.key for c in hub_subnav.footer_columns(is_authenticated=False)]
    assert 'leaderboards' not in keys and 'browse' in keys


def test_a_visitor_is_never_offered_a_page_that_only_signs_them_in():
    urls = {i.url for c in footer_columns(is_authenticated=False) for i in c.items}

    for name in ('career', 'badge_collection', 'my_titles', 'my_lists', 'my_challenges', 'rate_my_games',
                 'subscription_management'):
        assert reverse(name) not in urls, name
    assert reverse('milestones_list') in urls


def test_the_rendered_footer_files_each_page_under_its_hub(client):
    footer = _footer(client)

    assert f'href="{reverse("jobs_browse")}"' in _column(footer, 'Browse')
    assert f'href="{reverse("badges_list")}"' in _column(footer, 'Browse')
    for name in ('challenges', 'challenges_hall_of_fame', 'lists_browse', 'profiles_list'):
        assert f'href="{reverse(name)}"' in _column(footer, 'Community'), name
    my_pursuit = _column(footer, 'My Pursuit')
    assert f'href="{reverse("badges_list")}"' not in my_pursuit, 'Badges is Browse, not My Pursuit'
    assert reverse('career') not in my_pursuit, 'Career only signs a visitor in'


def test_a_visitor_always_gets_the_way_in(client):
    """Sign in and Sign up ride the My Pursuit column, which the footer drops if it has nothing to list.
    Pinned on their own so losing them (Milestones going members-only, say) is loud, not silent."""
    footer = _footer(client)

    assert f'href="{reverse("account_login")}"' in footer and f'href="{reverse("account_signup")}"' in footer


def test_a_signed_in_hunters_footer_lists_their_tools(client):
    client.force_login(ProfileFactory(is_linked=True).user)
    my_pursuit = _column(_footer(client), 'My Pursuit')

    for name in ('career', 'my_lists', 'my_challenges', 'rate_my_games'):
        assert f'href="{reverse(name)}"' in my_pursuit, name
    assert reverse('account_signup') not in my_pursuit


def test_the_info_column_is_headed_with_the_name_not_legal(client):
    footer = _footer(client)

    assert '>Platinum Pursuit</h2>' in footer and '>Legal</h2>' not in footer
    info = _column(footer, 'Platinum Pursuit')
    for name in ('about', 'whats_new', 'badge_how_it_works', 'contact', 'privacy', 'terms'):
        assert f'href="{reverse(name)}"' in info, name


# --- IA-02: site search finds Jobs and Game Lists ---

def _groups(client, q):
    return {g['type']: g for g in client.get(reverse('site_suggest'), {'q': q}).json()['groups']}


def test_search_finds_a_job_by_name(client):
    job = Job.objects.order_by('name').first()
    groups = _groups(client, job.name[:5])

    item = next(i for i in groups['job']['items'] if i['label'] == job.name)
    assert item['url'] == reverse('job_detail', kwargs={'slug': job.slug})
    assert item['sublabel'] == job.get_discipline_display()


def test_search_finds_a_public_game_list(client):
    owner = ProfileFactory(is_linked=True)
    game_list = svc.create_list(owner, name='Cozy backlog', is_public=True)

    items = _groups(client, 'cozy')['list']['items']

    assert [(i['label'], i['url']) for i in items] == [('Cozy backlog', reverse('list_detail', kwargs={'list_id': game_list.pk}))]
    assert items[0]['sublabel'] == '0 games'


def test_search_never_offers_a_private_deleted_or_moderated_list(client):
    owner = ProfileFactory(is_linked=True)
    svc.create_list(owner, name='Cozy secret', is_public=False)
    deleted = svc.create_list(owner, name='Cozy gone', is_public=True)
    GameList.objects.filter(pk=deleted.pk).update(is_deleted=True)
    hidden = svc.create_list(owner, name='Cozy slur', is_public=True)
    GameList.objects.filter(pk=hidden.pk).update(text_hidden=True)

    assert 'list' not in _groups(client, 'cozy'), 'a match would confirm what the hidden name contains'


def test_search_groups_run_in_order_with_hunters_last(client):
    # One match of every kind, under a word nothing seeded carries (the jobs are seeded: one is named for it).
    Job.objects.filter(slug='slayer').update(name='Zyzzva Slayer')
    concept = ConceptFactory(unified_title='Zyzzva Quest')
    GameFactory(concept=concept, played_count=1, np_communication_id='NPWR_ZYZZ_00')
    GroupBadgeFactory(series=BadgeSeriesFactory(name='Zyzzva Badge', series_slug='zyzzva-badge'))
    Franchise.objects.create(igdb_id=90210, name='Zyzzva Saga', slug='zyzzva-saga', source_type='franchise')
    owner = ProfileFactory(is_linked=True, psn_username='zyzzvafan')
    svc.create_list(owner, name='Zyzzva picks', is_public=True)

    types = [g['type'] for g in client.get(reverse('site_suggest'), {'q': 'zyzzva'}).json()['groups']]

    assert types == ['game', 'badge', 'job', 'franchise', 'list', 'profile']


def test_the_search_panel_draws_a_glyph_for_jobs_and_lists():
    js = (ROOT / 'static/js/navbar-search.js').read_text(encoding='utf-8')
    glyphs = js[js.index('const GLYPHS = {'):js.index('};', js.index('const GLYPHS = {'))]

    assert re.search(r'^\s*job:', glyphs, re.M) and re.search(r'^\s*list:', glyphs, re.M)


# --- IA-03: My Pursuit trails run Home / rail item / page ---

def _crumbs(client, url):
    return [c['text'] for c in client.get(url).context['breadcrumb']]


def test_my_pursuit_trails_name_no_hub(client):
    client.force_login(ProfileFactory(is_linked=True).user)

    assert _crumbs(client, reverse('my_titles')) == ['Home', 'Titles']
    assert _crumbs(client, reverse('my_challenges')) == ['Home', 'My Challenges']
    assert _crumbs(client, reverse('challenge_optimizer')) == ['Home', 'My Challenges', 'Optimizer']


# --- IA-04: pages inside a hub light their rail item ---

def _lit(path, user=None):
    request = RequestFactory().get(path)
    request.user = user or AnonymousUser()
    request.resolver_match = resolve(path)
    match = resolve_hub_subnav(request)
    return match['hub'].key, match['active_slug']


def test_how_badges_work_lights_badges():
    assert _lit(reverse('badge_how_it_works')) == ('browse', 'badges')


def test_the_optimizer_lights_my_challenges():
    assert _lit(reverse('challenge_optimizer')) == ('my_pursuit', 'my_challenges')


def test_a_visitors_view_of_a_hunters_day_lights_hunters():
    path = reverse('profile_day', kwargs={'psn_username': 'somebody', 'day': '2026-10-01'})
    assert _lit(path) == ('community', 'profiles')


# --- IA-05: Community's button opens the rail's first item ---

def test_every_hub_button_opens_its_rails_first_item(client):
    client.force_login(ProfileFactory(is_linked=True).user)
    body = client.get(reverse('about')).content.decode()
    nav = body[body.index('class="pp-nav__hubs'):body.index('id="navbar-search"')]
    buttons = dict(re.findall(r'href="([^"]+)" class="pp-navhub[^"]*"[^>]*>\s*<svg.*?</svg>\s*([A-Za-z ]+)', nav, re.S))
    by_label = {label.strip(): href for href, label in buttons.items()}

    tabbar = body[body.index('class="mobile-tabbar'):]
    tabs = {label: href for href, label in re.findall(r'href="([^"]+)"[^>]*aria-label="([A-Za-z ]+)[",]', tabbar)}

    for hub in HUB_SUBNAV_CONFIG:
        if hub.items:
            first = reverse(hub.items[0].url_name)
            assert by_label[hub.label] == first, f'{hub.label} button'
            assert tabs[hub.label] == first, f'{hub.label} tab'


def test_hunters_lead_the_community_rail():
    assert [i.label for i in _hub('community').items] == ['Hunters', 'Game Lists', 'Challenges', 'Hall of Fame']


# --- IA-06: the storefront item is Supporter Levels ---

def test_the_storefront_rail_item_is_supporter_levels(client):
    assert _hub('support').items[0].label == 'Supporter Levels'

    body = client.get(reverse('support_hub')).content.decode()
    assert '>Supporter Levels<' in body and '>Support</a>' not in body
