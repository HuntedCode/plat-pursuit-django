"""The badge-list header carries ONE count, its tally, and no catalogue tiles (PR-02, 2026-10-24). It used to
show four heartbeat stats (Badge series / Stages / Points available / New this week), the "182 series" twice on
the first screen. The catalogue figures still feed How badges work (`badge_catalog_stats`).
"""
import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db


def _warm(**expanded):
    now = timezone.now()
    key = f"site_heartbeat_{now.date().isoformat()}_{now.hour:02d}"
    cache.set(key, {'expanded': expanded}, 120)
    return key


def test_badge_header_has_no_catalogue_tiles_even_when_the_heartbeat_is_warm(client):
    key = _warm(
        badges_total={'value': 142, 'delta': 6},
        badge_stages_total={'value': 2910},
        badge_earnable_xp={'value': 1834000},
    )
    try:
        content = client.get(reverse('badges_list')).content.decode()
    finally:
        cache.delete(key)

    assert 'Points available' not in content and 'New this week' not in content
    assert 'class="scard' not in content
    assert '{#' not in content       # multi-line comment leak guard


def test_badge_header_grid_absent_when_heartbeat_cold(client):
    # No warm heartbeat (cron hasn't run) -> the grid is gated off, no user-specific stats shown.
    content = client.get(reverse('badges_list')).content.decode()
    assert 'Points available' not in content   # the catalog scard grid is omitted
