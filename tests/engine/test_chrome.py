"""Chrome (nav / footer / hotbar) rebuild-alignment tests.

The permanent chrome frames every page, so these pin the structural facts that page-level tests
would otherwise miss: the footer's 4-hub restructure (the pre-unify My Pursuit + Dashboard columns
merged into one hub sitemap, a Support column added), and its auth-gated visibility.
"""
import pytest
from django.template.loader import render_to_string

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
    assert b'>The Lab</a>' in resp.content
    assert b'>My Shareables</a>' in resp.content


def test_footer_hides_personal_cockpit_from_anon(client):
    # Anon sees only the public catalog members of the hub, never the login-gated cockpit links.
    resp = client.get('/support/')
    assert b'>The Lab</a>' not in resp.content
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
