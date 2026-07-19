"""The badge-list header shows generalized CATALOG stats (from the hourly heartbeat), not the
viewer's own progress: Badge series / Stages / XP available / New this week. Mirrors the browse
header treatment. Cold cache (no cron yet) simply omits the grid.
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


def test_badge_header_scard_grid_renders_when_heartbeat_warm(client):
    key = _warm(
        badges_total={'value': 142, 'delta': 6},
        badge_stages_total={'value': 2910},
        badge_earnable_xp={'value': 1834000},
    )
    try:
        content = client.get(reverse('badges_list')).content.decode()
    finally:
        cache.delete(key)

    assert 'scard' in content
    assert 'Badge series' in content and 'Stages' in content
    assert 'XP available' in content and 'New this week' in content
    assert '142' in content          # badges_total flows through
    assert '2,910' in content        # stages (intcomma)
    assert '1,834,000' in content    # total earnable XP (intcomma)
    assert '{#' not in content       # multi-line comment leak guard


def test_badge_header_grid_absent_when_heartbeat_cold(client):
    # No warm heartbeat (cron hasn't run) -> the grid is gated off, no user-specific stats shown.
    content = client.get(reverse('badges_list')).content.decode()
    assert 'XP available' not in content   # the catalog scard grid is omitted
