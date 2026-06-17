"""create_badge_group_from_form sets type-aware display defaults, so a Franchise badge
reads "Crash Franchise Master" (not "... Series Master") and an Event reads
"... Event Champion". Guards against the per-type display defaults regressing.
"""
import pytest

from trophies.models import Badge
from trophies.services.psn_api_service import PsnApiService

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("badge_type,expected_title,expected_series", [
    ("series", "Crash Series Master", "Crash Series"),
    ("franchise", "Crash Franchise Master", "Crash Franchise"),
    ("event", "Crash Event Champion", "Crash Event"),
])
def test_create_defaults_are_type_aware(badge_type, expected_title, expected_series):
    PsnApiService.create_badge_group_from_form({
        "name": "Crash", "series_slug": f"sl-{badge_type}", "badge_type": badge_type,
    })

    tier1 = Badge.objects.get(series_slug=f"sl-{badge_type}", tier=1)
    assert tier1.display_title == expected_title       # no "Series" leak for franchise/event
    assert tier1.display_series == expected_series
    assert tier1.badge_type == badge_type

    # All four tiers are created with the same badge type (keeps per-type numbering valid).
    tiers = Badge.objects.filter(series_slug=f"sl-{badge_type}")
    assert tiers.count() == 4
    assert set(tiers.values_list("badge_type", flat=True)) == {badge_type}
