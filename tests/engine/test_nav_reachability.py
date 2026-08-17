"""Every hub surface is reachable, and every rail item goes somewhere (2026-08).

Added after `/jobs/` shipped fully built and completely unreachable: no rail item pointing at it, and --
worse -- no `/jobs/` entry in the Browse hub's `prefixes`, so the page rendered with no sub-nav rail at
all. Both halves were missed by a nine-check audit, because every check asked "is this page correct?" and
none asked "can anyone get to it?"

These are cheap, and they fail loudly the moment a new surface is half-wired.
"""
import pytest
from django.urls import NoReverseMatch, reverse

from core.hub_subnav import HUB_SUBNAV_CONFIG as HUBS

pytestmark = pytest.mark.django_db


def _items():
    for hub in HUBS:
        for item in hub.items:
            yield hub, item


@pytest.mark.parametrize('hub_key,url_name', [(h.key, i.url_name) for h, i in _items()])
def test_every_rail_item_resolves(hub_key, url_name):
    """A rail item naming a dead url_name raises NoReverseMatch when the strip renders -- a 500 on every
    page in the hub, not just the broken one."""
    try:
        reverse(url_name)
    except NoReverseMatch:
        pytest.fail(f'{hub_key} rail item points at {url_name!r}, which does not reverse')


@pytest.mark.parametrize('hub_key,url_name', [(h.key, i.url_name) for h, i in _items()])
def test_every_rail_item_lands_inside_its_own_hub(hub_key, url_name):
    """The hub is resolved by PATH PREFIX, so an item whose URL is not under one of its hub's prefixes
    sends the reader somewhere that then shows a DIFFERENT rail -- or none.

    This is the half that bit `/jobs/`: the page existed, but Browse did not claim its path, so it
    rendered hubless.
    """
    hub = next(h for h in HUBS if h.key == hub_key)
    path = reverse(url_name)
    assert any(path.startswith(p) for p in hub.prefixes) or path == '/', (
        f'{hub_key} rail item {url_name!r} -> {path}, which is not under any of that hub\'s prefixes '
        f'{hub.prefixes}'
    )


@pytest.mark.parametrize('url_name', [
    'jobs_browse', 'badge_boards', 'game_boards', 'job_boards', 'overall_badge_leaderboards',
])
def test_the_new_leaderboards_surfaces_are_reachable_from_a_rail(url_name):
    """Every page built by the leaderboards rebuild has a way in. `/jobs/` shipped without one and nobody
    noticed until it was asked about directly, which is exactly why this is a test rather than a habit."""
    path = reverse(url_name)
    rails = {reverse(i.url_name) for h in HUBS for i in h.items}
    assert path in rails, f'{url_name} ({path}) is not on any hub rail -- it is unreachable by navigation'
