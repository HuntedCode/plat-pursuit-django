"""Chrome (nav / footer / hotbar) rebuild-alignment tests.

The permanent chrome frames every page, so these pin the structural facts that page-level tests
would otherwise miss: the footer's 4-hub restructure (the pre-unify My Pursuit + Dashboard columns
merged into one hub sitemap, a Support column added), and its auth-gated visibility.
"""
import pytest

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
