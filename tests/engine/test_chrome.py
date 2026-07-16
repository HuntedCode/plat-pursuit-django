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
from tests.factories import ProfileFactory

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
