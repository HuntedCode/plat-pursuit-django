"""Profiles moved out from under /community/ to /profiles/ (2026-08), with the Community hub teardown.

This is the riskiest URL move on the site: profile pages are public, indexed, and carry their own
sitemap, so every hunter's canonical URL changed at once. Every internal reference reverses by name and
followed for free -- what needs pinning is the part that does NOT: that the old paths still land, that
they land on the RIGHT hunter, and that the sitemap now advertises the new location rather than a
redirect.
"""
import pytest

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

# Profile paths sit behind CloudflareOriginGuardMiddleware, which bounces any direct-origin request for
# them back through the public host. It keys on the CF-Ray header that every request transiting
# Cloudflare carries, so a test client has to present one or it never reaches the URL conf at all.
THROUGH_CLOUDFLARE = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def test_the_browse_list_is_at_the_new_path(client):
    assert client.get('/profiles/').status_code == 200


def test_a_profile_is_at_the_new_path(client):
    profile = ProfileFactory(is_linked=True)

    assert client.get(f'/profiles/{profile.psn_username}/', **THROUGH_CLOUDFLARE).status_code == 200


@pytest.mark.parametrize('suffix', ['', 'trophy-case/'])
def test_the_old_paths_land_on_the_same_hunter(client, suffix):
    """A redirect that loses the username is worse than a 404: it silently shows a stranger's profile.

    Presents a CF-Ray header because profile paths sit behind the Cloudflare origin guard, which
    bounces direct-origin requests before the URL conf is ever consulted."""
    profile = ProfileFactory(is_linked=True)

    resp = client.get(f'/community/profiles/{profile.psn_username}/{suffix}', **THROUGH_CLOUDFLARE)

    assert resp.status_code == 301
    assert resp['Location'] == f'/profiles/{profile.psn_username}/{suffix}'
    assert client.get(resp['Location'], **THROUGH_CLOUDFLARE).status_code == 200


def test_the_old_list_path_lands(client):
    resp = client.get('/community/profiles/?sort=platinums')

    assert resp.status_code == 301
    assert resp['Location'].startswith('/profiles/')
    assert 'sort=platinums' in resp['Location'], 'the browse query was dropped'


def test_the_sitemap_advertises_the_new_location_not_a_redirect():
    """Every profile is in the sitemap. Left pointing at the old path, the whole set would be crawled
    into a 301 rather than the page itself."""
    from core.sitemaps import ProfileSitemap

    profile = ProfileFactory(is_linked=True)
    location = ProfileSitemap().location(profile)

    assert location == f'/profiles/{profile.psn_username}/'
    assert not location.startswith('/community/')


def test_profiles_are_a_browse_surface_now():
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory
    from django.urls import resolve

    from core.hub_subnav import resolve_hub_subnav

    req = RequestFactory().get('/profiles/')
    req.resolver_match = resolve('/profiles/')
    req.user = AnonymousUser()
    match = resolve_hub_subnav(req)

    assert match['hub'].key == 'browse'
    assert match['active_slug'] == 'profiles'
    catalog = [i.slug for i in match['hub'].items if i.group == 'Catalog']
    assert catalog == ['games', 'badges', 'recently-added', 'profiles'], catalog


def test_the_moved_profile_pages_are_still_behind_the_cloudflare_guard():
    """The guard that bounces direct-origin requests back through Cloudflare is a PATH REGEX, not a
    reverse() -- so unlike every internal link it does NOT follow a rename. Moving profiles without
    moving it would have silently un-guarded the most scraped page type on the site, which is what
    started the 2026-08 scraper outage. The list page stays unguarded (it has no trailing segment).
    """
    from plat_pursuit.middleware import _CLOUDFLARE_GUARDED_PATH_RE as guard

    assert guard.match('/profiles/someone/'), 'the new profile path is not guarded'
    assert guard.match('/profiles/someone/trophy-case/'), 'profile sub-pages are not guarded'
    assert guard.match('/community/profiles/someone/'), 'the old path lost its guard while it redirects'
    assert not guard.match('/profiles/'), 'the browse list should not be guarded'
