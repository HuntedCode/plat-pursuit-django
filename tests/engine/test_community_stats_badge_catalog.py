"""Catalog badge stats in compute_community_stats + the site heartbeat: total stages to complete
and total earnable XP across the LIVE badge collection (what the collection OFFERS, not who earned
it). These feed the badge-list header's discovery stats.
"""
import pytest

from core.services.site_heartbeat import compute_site_heartbeat
from core.services.stats import compute_community_stats
from trophies.util_modules.constants import BADGE_TIER_XP, BRONZE_STAGE_XP, SILVER_STAGE_XP
from tests.factories import BadgeFactory, StageFactory

pytestmark = pytest.mark.django_db

# Bronze (tier 1, 2 stages) + Silver (tier 2, 3 stages), both live, in one series.
_EXPECTED_XP = (2 * BRONZE_STAGE_XP + BADGE_TIER_XP) + (3 * SILVER_STAGE_XP + BADGE_TIER_XP)


def _seed_catalog():
    BadgeFactory(series_slug='cat', tier=1, is_live=True, required_stages=2)
    BadgeFactory(series_slug='cat', tier=2, is_live=True, required_stages=3)
    # Two counting stages + a base stage 0 that must NOT count toward the total.
    StageFactory(series_slug='cat', stage_number=1)
    StageFactory(series_slug='cat', stage_number=2)
    StageFactory(series_slug='cat', stage_number=0)
    # A hidden (non-live) badge + stage must be excluded from both stats entirely.
    BadgeFactory(series_slug='hidden', tier=1, is_live=False, required_stages=5)
    StageFactory(series_slug='hidden', stage_number=1)


def test_community_stats_badge_catalog_aggregates():
    _seed_catalog()
    stats = compute_community_stats()
    # Only the live series' counting stages (1, 2): stage 0 and the hidden series are excluded.
    assert stats['badge_stages']['total'] == 2
    # Earnable XP sums (required_stages * tier_xp + completion bonus) over live badges only.
    assert stats['badge_earnable_xp']['total'] == _EXPECTED_XP


def test_heartbeat_surfaces_badge_catalog_stats():
    _seed_catalog()
    expanded = compute_site_heartbeat()['expanded']
    assert expanded['badge_stages_total']['value'] == 2
    assert expanded['badge_earnable_xp']['value'] == _EXPECTED_XP
    # badges_total carries a weekly delta so the header can show "new this week".
    assert 'delta' in expanded['badges_total']


def test_earnable_xp_zero_when_no_live_badges():
    # No badges at all -> the aggregate coalesces to 0, never None (template-safe).
    stats = compute_community_stats()
    assert stats['badge_earnable_xp']['total'] == 0
    assert stats['badge_stages']['total'] == 0
