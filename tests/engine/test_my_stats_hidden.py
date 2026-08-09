"""My Stats (`/stats/`) is HIDDEN for the 1.0 launch.

The page is being renovated into an upgraded tool (docs/design/stats-page.md + the Data Intelligence
arc), so it is re-gated to staff and stripped from every user-facing entry point. These pin the hide
so a future nav/subnav edit can't quietly re-expose it.
"""
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import resolve

from core.hub_subnav import MY_PURSUIT_HUB, resolve_hub_subnav
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def test_non_staff_user_is_redirected_home(client):
    profile = ProfileFactory()
    client.force_login(profile.user)

    resp = client.get('/stats/')

    assert resp.status_code == 302 and resp['Location'] == '/'


def test_anon_visitor_is_sent_to_login(client):
    resp = client.get('/stats/')

    assert resp.status_code == 302 and '/login/' in resp['Location']


def test_staff_keep_access_so_the_rebuild_can_happen_in_place(client):
    profile = ProfileFactory()
    profile.user.is_staff = True
    profile.user.save(update_fields=['is_staff'])
    client.force_login(profile.user)

    assert client.get('/stats/').status_code == 200


def test_no_subnav_entry_and_the_path_leaves_the_hub():
    """The Tools rail must not advertise a page nobody can open, and /stats/ no longer claims the
    My Pursuit strip."""
    assert 'stats' not in {item.slug for item in MY_PURSUIT_HUB.items}

    request = RequestFactory().get('/stats/')
    request.resolver_match = resolve('/stats/')
    request.user = AnonymousUser()
    assert resolve_hub_subnav(request) is None


def test_nothing_on_the_public_home_links_to_it(client):
    """The footer carried the last user-facing link. (The landing page still *markets* deep stats --
    that copy pass is tracked separately -- but nothing routes a visitor at the hidden page.)"""
    content = client.get('/').content.decode()

    assert 'href="/stats/"' not in content
