"""Leaderboards became its own hub (2026-08), moving out from under /community/.

Community had been hollowed out -- Challenges retired, Reviews archived, Lists hidden, Rate My Games
rehoused -- and leaderboards were the substance left in it. What these pin is that the promotion did not
strand anything: every path anyone could be holding still lands, and it lands in ONE hop.
"""
import pytest

from tests.factories import BadgeSeriesFactory

pytestmark = pytest.mark.django_db

LANDING = '/leaderboards/'


def test_the_landing_is_the_leaderboards(client):
    resp = client.get(LANDING)

    assert resp.status_code == 200


@pytest.mark.parametrize('old', [
    '/community/leaderboards/badges/',      # where they lived under Community
    '/leaderboard/badges/',                 # the pre-2026 path
])
def test_every_old_overall_path_lands_in_one_hop(client, old):
    """One hop matters as much as landing at all. RedirectView resolves `pattern_name` at REQUEST time,
    so the oldest redirects re-point themselves at each rename instead of forming a chain -- but only
    while they keep using pattern_name, which is what this guards."""
    resp = client.get(old)

    assert resp.status_code == 301, f'{old} did not redirect'
    assert resp['Location'] == LANDING, f'{old} -> {resp["Location"]} (expected a single hop)'
    assert client.get(resp['Location']).status_code == 200, 'the destination is not a live page'


def test_the_badge_boards_path_still_lands_somewhere(client):
    """`/leaderboards/badges/` redirects to the landing, which is what it did before the Badge Boards
    directory briefly took the path and what it does again now the directory is gone.

    It is not decoration: the per-series redirect below is still live, so this is the parent of a URL
    people can be holding, and chopping a URL back to its parent is a thing readers do."""
    resp = client.get('/leaderboards/badges/')

    assert resp.status_code == 301
    assert resp['Location'] == LANDING


def test_the_per_series_path_moved_and_keeps_its_series(client):
    """Retargeted 2026-08: the per-series board is a SECTION of badge detail now, not a page, so this
    legacy path lands on the badge itself.

    It still keeps the slug, which is the part that matters -- a redirect that dropped it would send
    every old inbound link to a generic index instead of the board it asked for. And it had to be
    repointed in the same change that retired the url NAME: a RedirectView naming a dead pattern raises
    NoReverseMatch, so these would have started 500ing rather than 404ing."""
    series = BadgeSeriesFactory()
    old = f'/community/leaderboards/badges/{series.series_slug}/'

    resp = client.get(old)

    assert resp.status_code == 301
    assert resp['Location'] == f'/badges/{series.series_slug}/', resp['Location']


def test_the_query_string_survives_the_move(client):
    """The leaderboards carry filters/pagination in the query string, so a redirect that drops it sends
    people to a different view of the data than the one they were linked to."""
    resp = client.get('/community/leaderboards/badges/?platform=ps5&page=3')

    assert 'platform=ps5' in resp['Location'] and 'page=3' in resp['Location']


def test_the_chrome_points_at_the_new_hub():
    """The navbar and the mobile tab bar are the two places a hub link survives a rename, because
    neither is exercised by the page being renamed."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / 'templates' / 'partials'
    for name in ('navbar.html', 'mobile_tabbar.html'):
        src = (root / name).read_text(encoding='utf-8')
        assert "hub_section == 'leaderboards'" in src, f'{name} never highlights the new hub'
        assert "url 'community_hub'" not in src, f'{name} still links to the Community hub'


def test_the_landing_is_in_the_sitemap():
    from core.sitemaps import StaticViewSitemap

    assert 'overall_badge_leaderboards' in StaticViewSitemap().items()
