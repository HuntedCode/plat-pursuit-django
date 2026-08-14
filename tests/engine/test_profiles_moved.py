"""The hunters section and its URL history.

It moved TWICE in 2026-08: out from under /community/ when that hub was retired, then /profiles/ ->
/hunters/ when the section was renamed to match what the site calls these people everywhere else.

This is the riskiest URL move on the site: profile pages are public, indexed, and carry their own
sitemap, so every hunter's canonical URL changed at once. Every internal reference reverses by name and
followed for free -- what needs pinning is the part that does NOT: that the old paths still land, that
they land on the RIGHT hunter, and that the sitemap now advertises the new location rather than a
redirect. Each legacy wave must also reach the canonical in a SINGLE hop rather than chaining through
the intermediate spelling.
"""
import pytest

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

# Profile paths sit behind CloudflareOriginGuardMiddleware, which bounces any direct-origin request for
# them back through the public host. It keys on the CF-Ray header that every request transiting
# Cloudflare carries, so a test client has to present one or it never reaches the URL conf at all.
THROUGH_CLOUDFLARE = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def test_the_browse_list_is_at_the_new_path(client):
    assert client.get('/hunters/').status_code == 200


def test_a_profile_is_at_the_new_path(client):
    profile = ProfileFactory(is_linked=True)

    assert client.get(f'/hunters/{profile.psn_username}/', **THROUGH_CLOUDFLARE).status_code == 200


@pytest.mark.parametrize('legacy', ['/community/profiles', '/profiles'])
@pytest.mark.parametrize('suffix', ['', 'trophy-case/'])
def test_every_legacy_path_lands_on_the_same_hunter(client, legacy, suffix):
    """A redirect that loses the username is worse than a 404: it silently shows a stranger's profile.

    Presents a CF-Ray header because profile paths sit behind the Cloudflare origin guard, which
    bounces direct-origin requests before the URL conf is ever consulted."""
    profile = ProfileFactory(is_linked=True)

    resp = client.get(f'{legacy}/{profile.psn_username}/{suffix}', **THROUGH_CLOUDFLARE)

    assert resp.status_code == 301
    assert resp['Location'] == f'/hunters/{profile.psn_username}/{suffix}'
    assert client.get(resp['Location'], **THROUGH_CLOUDFLARE).status_code == 200


@pytest.mark.parametrize('legacy', ['/community/profiles/', '/profiles/'])
def test_every_legacy_list_path_lands(client, legacy):
    resp = client.get(legacy, {'sort': 'trophies'})

    assert resp.status_code == 301
    assert resp['Location'].startswith('/hunters/')
    assert 'sort=trophies' in resp['Location'], 'the browse query was dropped'


def test_the_sitemap_advertises_the_new_location_not_a_redirect():
    """Every profile is in the sitemap. Left pointing at the old path, the whole set would be crawled
    into a 301 rather than the page itself."""
    from core.sitemaps import ProfileSitemap

    profile = ProfileFactory(is_linked=True)
    location = ProfileSitemap().location(profile)

    assert location == f'/hunters/{profile.psn_username}/'
    assert not location.startswith(('/community/', '/profiles/'))


def test_profiles_are_a_browse_surface_now():
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory
    from django.urls import resolve

    from core.hub_subnav import resolve_hub_subnav

    req = RequestFactory().get('/hunters/')
    req.resolver_match = resolve('/hunters/')
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

    assert guard.match('/hunters/someone/'), 'the new profile path is not guarded'
    assert guard.match('/hunters/someone/trophy-case/'), 'profile sub-pages are not guarded'
    assert guard.match('/profiles/someone/'), 'the first old path lost its guard while it redirects'
    assert guard.match('/community/profiles/someone/'), 'the oldest path lost its guard'
    assert not guard.match('/hunters/'), 'the browse list should not be guarded'


def test_robots_still_throttles_crawling_of_the_moved_profiles():
    """The SECOND hardcoded-path file, and the one the audit caught rather than the move.

    `robots.txt` blocks the `?tab=` / `?page=` / `?sort=` permutations of a profile -- axes that
    multiply into an unbounded crawl space, one real query each -- while leaving the canonical profile
    crawlable. Keyed on the old path it matched nothing at all, quietly un-throttling exactly the pages
    whose crawl cost started the 2026-08 outage.

    The `/` before the `?` is load-bearing in both lines: `*` matches the empty string, so `/profiles/*?*`
    would also block the profile INDEX's pagination -- the one thing a crawler should walk.
    """
    from pathlib import Path

    robots = (Path(__file__).resolve().parents[2] / 'static' / 'robots.txt').read_text(encoding='utf-8')

    assert 'Disallow: /hunters/*/?*' in robots, 'the moved profiles are no longer crawl-throttled'
    assert 'Disallow: /profiles/*/?*' in robots, 'the first old path lost its rule while it redirects'
    assert 'Disallow: /community/profiles/*/?*' in robots, 'the oldest path lost its rule'
    assert 'Disallow: /hunters/*?*\n' not in robots, 'this form also blocks the index pagination'


def test_the_oldest_paths_reach_the_canonical_in_ONE_hop(client):
    """The section moved twice, so /community/profiles/<u>/ could easily 301 to /profiles/<u>/ and then
    301 again to /hunters/<u>/. It does not, because every redirect targets a `pattern_name` that resolves
    at REQUEST time -- so the /community/ wave re-aimed itself the moment the canonical moved. Chains cost
    a round trip per hop, bleed link equity, and search engines give up following them."""
    profile = ProfileFactory(is_linked=True)

    resp = client.get(f'/community/profiles/{profile.psn_username}/', **THROUGH_CLOUDFLARE)

    assert resp['Location'] == f'/hunters/{profile.psn_username}/', 'chained through the old spelling'
    assert client.get(resp['Location'], **THROUGH_CLOUDFLARE).status_code == 200, 'not a terminal URL'
