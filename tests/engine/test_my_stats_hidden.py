"""My Stats (`/stats/`) is HIDDEN for the 1.0 launch.

The page is being renovated into an upgraded tool (docs/design/stats-page.md + the Data Intelligence
arc), so the route bounces to Home and every user-facing entry point is stripped. These pin the hide
so a future nav/subnav/url edit can't quietly re-expose it.
"""
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import resolve, reverse

from core.hub_subnav import MY_PURSUIT_HUB, resolve_hub_subnav
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('who', ['anon', 'user', 'staff'])
def test_everyone_lands_on_home(client, who):
    """A bookmark must not 404, and must not dump the visitor on a login screen either -- including a
    staff bookmark, since the view is parked rather than staff-browsable."""
    if who != 'anon':
        profile = ProfileFactory()
        if who == 'staff':
            profile.user.is_staff = True
            profile.user.save(update_fields=['is_staff'])
        client.force_login(profile.user)

    resp = client.get('/stats/')

    assert resp.status_code == 302 and resp['Location'] == '/'


def test_the_redirect_is_temporary_not_permanent():
    """302, deliberately: browsers cache a 301 hard, and this page is coming back at the same URL."""
    assert resolve('/stats/').func.view_initkwargs['permanent'] is False


def test_the_url_name_still_reverses():
    """`my_stats` stays bound to /stats/ so the surviving legacy redirects (and any stray
    {% url 'my_stats' %}) resolve instead of raising NoReverseMatch."""
    assert reverse('my_stats') == '/stats/'


@pytest.mark.parametrize('legacy', ['/my-stats/', '/tools/stats/', '/dashboard/stats/'])
def test_legacy_paths_still_funnel_into_the_bounce(client, legacy):
    assert client.get(legacy, follow=True).redirect_chain[-1] == ('/', 302)


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
