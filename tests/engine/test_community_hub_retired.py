"""The Community hub was retired (2026-08).

It was not retired because community failed -- it was retired because everything in it had gone
somewhere else: Challenges retired, Reviews archived, Lists hidden pending a revamp, Profiles moved to
Browse, Rate My Games to My Pursuit > Tools, and Leaderboards promoted to their own hub. What was left
was a landing page with nothing of its own to land on.

These pin the teardown: the door still opens for anyone holding the old link, nothing on the site leads
back to a page that no longer exists, and the code behind it is actually gone rather than orphaned.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]


def test_the_hub_sends_you_to_the_leaderboards(client):
    """Permanent: the hub is not coming back, and leaderboards are what it mostly was by the end."""
    resp = client.get('/community/')

    assert resp.status_code == 301
    assert resp['Location'] == '/leaderboards/'
    assert client.get(resp['Location']).status_code == 200


def test_the_hub_cannot_be_linked_to_by_name():
    """The redirect is deliberately UNNAMED. A named one keeps `{% url 'community_hub' %}` resolving, so
    a link to the retired hub can reappear in a template and nobody notices -- it would still 'work'."""
    from django.urls import NoReverseMatch, reverse

    with pytest.raises(NoReverseMatch):
        reverse('community_hub')


def test_the_code_behind_it_is_gone_not_orphaned():
    """A retired page whose view and service stay behind is the residue that makes the next person think
    the hub is still live -- and the service was several queries on what used to be a busy surface."""
    assert not (ROOT / 'core' / 'services' / 'community_hub_service.py').exists()
    assert not (ROOT / 'templates' / 'community').exists()

    views = (ROOT / 'core' / 'views.py').read_text(encoding='utf-8')
    assert 'CommunityHubView' not in views
    assert 'build_community_hub_context' not in views


def test_no_hub_config_claims_the_community_prefix():
    from core.hub_subnav import HUB_SUBNAV_CONFIG

    keys = {h.key for h in HUB_SUBNAV_CONFIG}
    assert 'community' not in keys
    assert 'leaderboards' in keys
    assert not [p for h in HUB_SUBNAV_CONFIG for p in h.prefixes if p.startswith('/community/')]


def test_nothing_in_the_chrome_or_the_sitemap_points_at_it():
    """The footer, the navbar and the mobile tab bar are where a link survives a teardown, because none
    of them is exercised by the page being torn down. The sitemap is worse: it would keep inviting
    crawlers into a redirect."""
    from core.sitemaps import StaticViewSitemap

    assert 'community_hub' not in StaticViewSitemap().items()

    for rel in ('partials/footer.html', 'partials/navbar.html', 'partials/mobile_tabbar.html',
                'trophies/home.html'):
        src = (ROOT / 'templates' / rel).read_text(encoding='utf-8')
        assert "url 'community_hub'" not in src, f'{rel} still links to the retired hub'


def test_what_remains_under_community_still_answers(client):
    """The prefix is not dead -- the reviews tombstone and the hidden-lists redirects still live under
    it, and they must keep working now that the hub above them has gone."""
    assert client.get('/community/reviews/').status_code == 200
    assert client.get('/community/lists/').status_code in (301, 302)


def test_live_javascript_builds_no_stale_profile_links():
    """Hand-built URLs in JS do NOT follow a rename the way `{% url %}` does. The roadmap notes'
    @mention links are the live one -- they would have quietly started hopping through a 301."""
    for name in ('roadmap_notes.js', 'review-hub.js', 'review-hub-landing.js'):
        src = (ROOT / 'static' / 'js' / name).read_text(encoding='utf-8')
        assert '/community/profiles/' not in src, f'{name} still builds the old profile path'
