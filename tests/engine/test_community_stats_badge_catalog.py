"""Catalog badge stats in compute_community_stats + the site heartbeat: total stages to complete and
total earnable XP across the LIVE badge collection (what the collection OFFERS, not who earned it).
These feed the badge-list header's discovery stats.

Repointed onto the grouping-badge subsystem in the 2026-08 cutover. Two behaviours changed and both are
pinned below:

- XP accrues PER EDITION. A series published as both Legacy HD and Ultra HD is worth twice a single-edition
  series, because they are separately earnable badges. The legacy tier model summed per tier row instead.
- Liveness moved from the badge row to the per-edition `GroupBadge`, so "live series" now means "has at
  least one live group badge" -- a series whose only edition is dormant is invisible to every figure here.
"""
import pytest

from core.services.site_heartbeat import compute_site_heartbeat
from core.services.stats import compute_community_stats
from trophies.services.badge_xp import XP_BADGE_COMPLETION_BONUS, XP_PER_STAGE
from tests.factories import (
    BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, StageFactory,
)

pytestmark = pytest.mark.django_db

# The 'cat' series has 2 counting stages and ships in TWO editions, so it is worth twice one edition's
# (stages * per-stage + completion bonus).
_PER_EDITION_XP = 2 * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS
_EXPECTED_XP = 2 * _PER_EDITION_XP


def _seed_catalog():
    live = BadgeSeriesFactory(series_slug='cat')
    GroupBadgeFactory(series=live, platform_group=PlatformGroupFactory(key='ultra'), is_live=True)
    GroupBadgeFactory(series=live, platform_group=PlatformGroupFactory(key='legacy'), is_live=True)
    # Two counting stages + a base stage 0 that must NOT count toward the total.
    StageFactory(series_slug='cat', stage_number=1)
    StageFactory(series_slug='cat', stage_number=2)
    StageFactory(series_slug='cat', stage_number=0)

    # A series whose only edition is dormant must be excluded from every figure.
    hidden = BadgeSeriesFactory(series_slug='hidden')
    GroupBadgeFactory(series=hidden, platform_group=PlatformGroupFactory(key='hidden-grp'), is_live=False)
    StageFactory(series_slug='hidden', stage_number=1)


def test_community_stats_badge_catalog_aggregates():
    _seed_catalog()
    stats = compute_community_stats()
    # Only the live series' counting stages (1, 2): stage 0 and the dormant series are excluded. Counted
    # once per SERIES even though the series has two editions -- the stage list is series-level.
    assert stats['badge_stages']['total'] == 2
    assert stats['badge_earnable_xp']['total'] == _EXPECTED_XP


def test_earnable_xp_counts_each_edition_separately():
    """The per-edition multiplication, isolated: dropping the second edition must halve the figure."""
    series = BadgeSeriesFactory(series_slug='solo')
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='only'), is_live=True)
    StageFactory(series_slug='solo', stage_number=1)
    StageFactory(series_slug='solo', stage_number=2)

    assert compute_community_stats()['badge_earnable_xp']['total'] == _PER_EDITION_XP


def test_live_series_requires_a_live_edition():
    """A series counts as live only through its editions -- there is no badge-row liveness any more."""
    series = BadgeSeriesFactory(series_slug='dormant-only')
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='g'), is_live=False)
    StageFactory(series_slug='dormant-only', stage_number=1)

    stats = compute_community_stats()
    assert stats['badge_series']['total'] == 0
    assert stats['badge_stages']['total'] == 0
    assert stats['badge_earnable_xp']['total'] == 0


def test_heartbeat_surfaces_badge_catalog_stats():
    _seed_catalog()
    expanded = compute_site_heartbeat()['expanded']
    assert expanded['badge_stages_total']['value'] == 2
    assert expanded['badge_earnable_xp']['value'] == _EXPECTED_XP
    # badges_total carries a weekly delta so the header can show "new this week".
    assert 'delta' in expanded['badges_total']


def test_earnable_xp_zero_when_no_live_badges():
    # No badges at all -> 0, never None (template-safe).
    stats = compute_community_stats()
    assert stats['badge_earnable_xp']['total'] == 0
    assert stats['badge_stages']['total'] == 0
