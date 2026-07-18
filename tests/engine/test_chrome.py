"""Chrome (nav / sub-nav / footer) rebuild-alignment tests.

The permanent chrome frames every page, so these pin the structural facts that page-level tests
would otherwise miss: the footer's 4-hub restructure (the pre-unify My Pursuit + Dashboard columns
merged into one hub sitemap, a Support column added), and its auth-gated visibility.
"""
import pytest
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse

from plat_pursuit.context_processors import navsync
from tests.factories import BadgeFactory, ConceptFactory, GameFactory, ProfileFactory
from trophies.models import Franchise

pytestmark = pytest.mark.django_db


# --- Footer: 4-hub restructure (My Pursuit + Dashboard merged, Support added) ---

def test_footer_has_support_column(client):
    resp = client.get('/support/')
    assert resp.status_code == 200
    assert b'aria-label="Support pages"' in resp.content
    assert b'>Support Hub</a>' in resp.content


def test_footer_dropped_standalone_dashboard_column(client):
    # The pre-unify standalone "Dashboard" footer column is gone (merged into My Pursuit).
    resp = client.get('/support/')
    assert b'aria-label="Dashboard pages"' not in resp.content


def test_footer_pursuit_column_merged_for_authed(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    resp = client.get('/support/')
    # Personal-hub pages that used to live in the Dashboard column now sit under My Pursuit.
    assert b'>Overview</a>' in resp.content
    assert b'>Career</a>' in resp.content
    assert b'>My Shareables</a>' in resp.content


def test_footer_hides_personal_cockpit_from_anon(client):
    # Anon sees only the public catalog members of the hub, never the login-gated cockpit links.
    resp = client.get('/support/')
    assert b'>Career</a>' not in resp.content
    assert b'>My Shareables</a>' not in resp.content


# --- Top chrome: aria-current parity (navbar hub button + mobile tab) ---

def test_navbar_and_tabbar_mark_active_hub_with_aria_current(client):
    # Support carries no sub-nav items, so any aria-current on /support/ must come from the navbar
    # hub button + the mobile tab bar (not the sub-nav strip) -- a precise guard for both.
    resp = client.get('/support/')
    assert resp.content.count(b'aria-current="page"') >= 2


# --- Avatar partial: safe fallback (no more broken default-avatar 404) ---

def test_avatar_partial_renders_image_when_url_present():
    html = render_to_string('partials/_avatar.html', {'url': 'https://cdn.example/a.png', 'alt': 'Zed'})
    assert '<img' in html
    assert 'https://cdn.example/a.png' in html
    assert '<svg' not in html


def test_avatar_partial_falls_back_to_glyph_without_url():
    html = render_to_string('partials/_avatar.html', {'url': '', 'alt': 'Nobody'})
    assert '<svg' in html            # the person glyph, not an <img>
    assert '<img' not in html
    # the fix's whole point: never emit the non-existent default-avatar asset path
    assert 'default-avatar' not in html
    assert 'default_avatar' not in html


# --- Navbar PSN search: persistent bar + typeahead suggest endpoint ---

def test_navbar_renders_persistent_search_bar(client):
    resp = client.get('/support/')
    assert resp.status_code == 200
    # The search is now a persistent bar wired to the suggest endpoint, not a bare dropdown.
    assert b'class="pp-navsearch"' in resp.content
    assert b'data-url-suggest' in resp.content
    assert b'id="navbar-sync-form"' in resp.content


def test_profile_suggest_ranks_prefix_matches_by_plats(client):
    ProfileFactory(psn_username='zed_low', total_plats=5)
    ProfileFactory(psn_username='zed_high', total_plats=120)
    ProfileFactory(psn_username='other_hunter', total_plats=999)  # different prefix: excluded

    resp = client.get(reverse('profile_suggest'), {'q': 'zed'})
    assert resp.status_code == 200
    results = resp.json()['results']

    names = [r['psn_username'] for r in results]
    assert names == ['zed_high', 'zed_low']            # ranked by total_plats desc
    assert 'other_hunter' not in names                 # prefix filter excludes non-matches

    top = results[0]
    assert top['display'] == 'zed_high'                # falls back to psn_username when no display name
    assert top['plats'] == 120
    assert top['url'] == reverse('profile_detail', kwargs={'psn_username': 'zed_high'})
    assert 'avatar_url' in top


def test_profile_suggest_display_name_and_case_insensitive(client):
    # psn_username is stored lowercased; display_psn_username carries the original case.
    ProfileFactory(psn_username='camelhunter', display_psn_username='CamelHunter', total_plats=1)
    resp = client.get(reverse('profile_suggest'), {'q': 'CAMEL'})   # upper query still matches
    results = resp.json()['results']
    assert [r['psn_username'] for r in results] == ['camelhunter']
    assert results[0]['display'] == 'CamelHunter'                   # original-case display preferred


def test_profile_suggest_short_query_returns_empty(client):
    ProfileFactory(psn_username='ab_hunter', total_plats=1)
    resp = client.get(reverse('profile_suggest'), {'q': 'a'})   # < 2 chars
    assert resp.status_code == 200
    assert resp.json()['results'] == []


def test_profile_suggest_rejects_overlong_query(client):
    resp = client.get(reverse('profile_suggest'), {'q': 'a' * 65})
    assert resp.status_code == 400


def test_profile_suggest_open_to_anonymous(client):
    # Profiles are public pages, so the read-only lookup is anon-accessible.
    ProfileFactory(psn_username='public_one', total_plats=3)
    resp = client.get(reverse('profile_suggest'), {'q': 'public'})
    assert resp.status_code == 200
    assert [r['psn_username'] for r in resp.json()['results']] == ['public_one']


# --- Universal nav search: grouped site_suggest endpoint (games/badges/franchises/hunters) ---

@pytest.fixture(autouse=True)
def _clear_suggest_cache():
    # site_suggest caches assembled payloads by query; clear between tests so per-test
    # data (and rate-limit buckets) can't bleed across cases.
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


def _groups(resp):
    return {g['type']: g for g in resp.json()['groups']}


def test_site_suggest_groups_all_entity_types(client):
    concept = ConceptFactory(unified_title='Elden Ring')
    GameFactory(concept=concept, played_count=100, np_communication_id='NPWR_ELDEN_00')
    BadgeFactory(name='Elden Lord', series_slug='elden-lord', tier=1, is_live=True)
    Franchise.objects.create(igdb_id=9001, name='Elden Ring', slug='elden-ring-fr', source_type='franchise')
    ProfileFactory(psn_username='eldenlord', total_plats=42)

    resp = client.get(reverse('site_suggest'), {'q': 'elden'})
    assert resp.status_code == 200
    groups = _groups(resp)
    assert set(groups) == {'game', 'badge', 'franchise', 'profile'}
    assert groups['game']['items'][0]['label'] == 'Elden Ring'
    assert groups['game']['items'][0]['url'] == reverse('game_detail', kwargs={'np_communication_id': 'NPWR_ELDEN_00'})
    assert groups['badge']['items'][0]['url'] == reverse('badge_detail', kwargs={'series_slug': 'elden-lord'})
    assert groups['badge']['items'][0]['image'] == ''    # no medallion art on this test badge -> glyph fallback
    assert groups['franchise']['items'][0]['url'] == reverse('franchise_detail', kwargs={'slug': 'elden-ring-fr'})
    assert groups['profile']['items'][0]['label'] == 'eldenlord'
    assert groups['profile']['items'][0]['plats'] == 42


def test_site_suggest_game_dedups_concept_to_most_played(client):
    # A concept with several Game versions collapses to ONE row linking the most-played version.
    concept = ConceptFactory(unified_title='God of War')
    GameFactory(concept=concept, played_count=5, np_communication_id='NPWR_LOW_00')
    GameFactory(concept=concept, played_count=500, np_communication_id='NPWR_HIGH_00')

    games = _groups(client.get(reverse('site_suggest'), {'q': 'god of war'}))['game']['items']
    assert len(games) == 1
    assert games[0]['url'] == reverse('game_detail', kwargs={'np_communication_id': 'NPWR_HIGH_00'})


def test_site_suggest_substring_not_prefix(client):
    # The whole point of the trigram indexes: mid-title substring, not just prefix.
    concept = ConceptFactory(unified_title='Dark Souls')
    GameFactory(concept=concept, played_count=10, np_communication_id='NPWR_DS_00')
    labels = [i['label'] for i in _groups(client.get(reverse('site_suggest'), {'q': 'souls'}))['game']['items']]
    assert 'Dark Souls' in labels


def test_site_suggest_badge_suggests_one_row_per_series(client):
    # Only the tier-1 badge represents a series (badge_detail is keyed on series_slug).
    BadgeFactory(name='Trophy Titan', series_slug='trophy-titan', tier=1, is_live=True)
    BadgeFactory(name='Trophy Titan', series_slug='trophy-titan', tier=2, is_live=True)  # higher tier: not its own row
    items = _groups(client.get(reverse('site_suggest'), {'q': 'trophy titan'}))['badge']['items']
    assert len(items) == 1
    assert items[0]['url'] == reverse('badge_detail', kwargs={'series_slug': 'trophy-titan'})


def test_site_suggest_hides_unreleased_badges(client):
    BadgeFactory(name='Secret Badge', series_slug='secret', tier=1, is_live=False)
    resp = client.get(reverse('site_suggest'), {'q': 'secret'})
    assert 'badge' not in _groups(resp)   # .live() gate keeps hidden badges out


def test_site_suggest_skips_badge_with_blank_series_slug(client):
    # An unlinkable badge (blank series_slug) must be EXCLUDED, not 500 the whole endpoint
    # via NoReverseMatch on badge_detail. Force '' past save() with .update().
    from trophies.models import Badge
    b = BadgeFactory(name='Orphan Badge', series_slug='orphan', tier=1, is_live=True)
    Badge.objects.filter(pk=b.pk).update(series_slug='')
    resp = client.get(reverse('site_suggest'), {'q': 'orphan'})
    assert resp.status_code == 200            # no NoReverseMatch crash
    assert 'badge' not in _groups(resp)       # the unlinkable badge is dropped


def test_site_suggest_franchise_sublabel_distinguishes_series(client):
    Franchise.objects.create(igdb_id=1, name='Spider-Verse', slug='sv-coll', source_type='collection')
    Franchise.objects.create(igdb_id=2, name='Spider-Verse', slug='sv-fran', source_type='franchise')
    subs = {i['sublabel'] for i in _groups(client.get(reverse('site_suggest'), {'q': 'spider'}))['franchise']['items']}
    assert subs == {'Series', 'Franchise'}


def test_site_suggest_franchise_uses_member_game_cover(client):
    # Franchises have no art of their own -> a member game's PSN portrait cover.
    from trophies.models import ConceptFranchise
    fr = Franchise.objects.create(igdb_id=555, name='Cover Franchise', slug='cover-fr', source_type='franchise')
    concept = ConceptFactory(unified_title='Cover Member', concept_icon_url='https://cdn.example/cover.png')
    GameFactory(concept=concept, np_communication_id='NPWR_COVERFR_00')
    ConceptFranchise.objects.create(concept=concept, franchise=fr)
    item = _groups(client.get(reverse('site_suggest'), {'q': 'cover franchise'}))['franchise']['items'][0]
    assert item['image'] == 'https://cdn.example/cover.png'


def test_site_suggest_empty_groups_omitted(client):
    concept = ConceptFactory(unified_title='Bloodborne')
    GameFactory(concept=concept, played_count=1, np_communication_id='NPWR_BB_00')
    groups = _groups(client.get(reverse('site_suggest'), {'q': 'bloodborne'}))
    assert set(groups) == {'game'}   # no empty badge/franchise/hunter sections


def test_site_suggest_short_query_returns_empty(client):
    resp = client.get(reverse('site_suggest'), {'q': 'a'})
    assert resp.status_code == 200
    assert resp.json()['groups'] == []


def test_site_suggest_rejects_overlong_query(client):
    assert client.get(reverse('site_suggest'), {'q': 'a' * 65}).status_code == 400


def test_site_suggest_open_to_anonymous(client):
    concept = ConceptFactory(unified_title='Public Game')
    GameFactory(concept=concept, played_count=1, np_communication_id='NPWR_PUB_00')
    resp = client.get(reverse('site_suggest'), {'q': 'public game'})
    assert resp.status_code == 200
    assert _groups(resp)['game']['items'][0]['label'] == 'Public Game'


def test_site_suggest_caches_payload(client):
    concept = ConceptFactory(unified_title='Cache Test')
    GameFactory(concept=concept, played_count=1, np_communication_id='NPWR_CACHE_00')
    first = client.get(reverse('site_suggest'), {'q': 'cache test'}).json()
    assert len(first['groups'][0]['items']) == 1
    # A second matching game added AFTER the first call must not surface: the response is
    # served from the Redis cache, not recomputed.
    c2 = ConceptFactory(unified_title='Cache Test Two')
    GameFactory(concept=c2, played_count=1, np_communication_id='NPWR_CACHE2_00')
    second = client.get(reverse('site_suggest'), {'q': 'cache test'}).json()
    assert second == first


def test_navbar_search_wired_to_site_suggest(client):
    resp = client.get('/support/')
    assert reverse('site_suggest').encode() in resp.content       # widened suggest endpoint
    assert b'Search games, badges, hunters' in resp.content       # widened placeholder
    # Guard the multi-line template comment (must be {% comment %}, not a leaking {# #}).
    assert b'JS owns submit' not in resp.content


# --- Sub-nav: grouped rail rebuild (--pp-* house style, workshop direction) ---

def test_subnav_renders_grouped_pp_rail(client):
    # /community/ renders 200 with the Explore/Create rail (/games/ 302s to add default filters).
    resp = client.get('/community/')
    assert resp.status_code == 200
    c = resp.content
    assert b'class="pp-sub"' in c                    # house-style rail, not the old DaisyUI strip
    assert b'data-subnav-rail' in c
    assert b'data-subnav-pill' in c
    assert b'Explore' in c and b'Create' in c        # the Community groups
    assert b'data-group="Explore"' in c


def test_subnav_marks_active_pill(client):
    # Hub is the active Community item -> tinted-active + aria-current.
    resp = client.get('/community/')
    assert b'pp-subpill is-active' in resp.content
    assert b'aria-current="page"' in resp.content


def test_subnav_mobile_sheet_present(client):
    c = client.get('/community/').content
    assert b'data-subnav-sheet' in c                 # the grouped mobile sheet
    assert b'pp-sub__sheetgrid' in c
    assert b'data-subnav-toggle' in c


def test_subnav_hidden_on_itemless_hub(client):
    # Support has items=() -> the {% if hub_section and hub_subnav_items %} guard renders nothing.
    assert b'class="pp-sub"' not in client.get('/support/').content


# --- navsync: global sync state for the navbar's status-aware avatar + panel ---

def _req_with(user):
    req = RequestFactory().get('/')
    req.user = user
    return req


def test_navsync_empty_for_anonymous():
    # Anon / profile-less viewers get nothing -> the navbar renders a plain avatar, no sync ring.
    assert navsync(_req_with(AnonymousUser())) == {}


def test_navsync_returns_sync_state_for_linked_profile():
    profile = ProfileFactory(is_linked=True, total_plats=7)
    data = navsync(_req_with(profile.user)).get('navsync')
    assert data is not None
    assert data['profile'] == profile
    assert data['sync_status'] == profile.sync_status
    assert 'progress_percentage' in data
    assert 'seconds_to_next_sync' in data


def test_navsync_syncing_adds_queue_position():
    # Mid-sync the panel shows queue position; the lookup fails soft to None without a live queue.
    profile = ProfileFactory(is_linked=True, sync_status='syncing')
    data = navsync(_req_with(profile.user))['navsync']
    assert data['sync_status'] == 'syncing'
    assert 'queue_position' in data


# --- Sync-status endpoint feeds the panel's live loot + last-synced ---

def test_sync_status_endpoint_returns_live_stats(client, monkeypatch):
    import fakeredis
    monkeypatch.setattr('trophies.views.sync_views.redis_client', fakeredis.FakeStrictRedis())
    profile = ProfileFactory(is_linked=True, total_plats=12, total_golds=30, total_silvers=100, total_bronzes=400)
    client.force_login(profile.user)

    data = client.get(reverse('profile_sync_status')).json()
    assert data['sync_status'] == profile.sync_status
    assert data['stats'] == {'plats': 12, 'golds': 30, 'silvers': 100, 'bronzes': 400}
    assert data['last_synced']                 # naturaltime string for the "Synced ..." row
    assert 'seconds_to_next_sync' in data


def test_sync_status_endpoint_requires_login(client):
    resp = client.get(reverse('profile_sync_status'))
    assert resp.status_code in (302, 403)      # LoginRequiredMixin gate
