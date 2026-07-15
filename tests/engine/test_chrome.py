"""Chrome (nav / footer / hotbar) rebuild-alignment tests.

The permanent chrome frames every page, so these pin the structural facts that page-level tests
would otherwise miss: the footer's 4-hub restructure (the pre-unify My Pursuit + Dashboard columns
merged into one hub sitemap, a Support column added), and its auth-gated visibility.
"""
import pytest
from django.template.loader import render_to_string
from django.urls import reverse

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
