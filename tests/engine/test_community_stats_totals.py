"""compute_community_stats' site-wide trophy + platinum totals.

The TOTALS read the nightly denorms (Trophy.earned_count / Game.plats_earned_count, kept fresh by
recalc_earn_rates) instead of a full-table EarnedTrophy aggregate -- the live scan (especially the
platinum variant's join to Trophy) scales with the whale table and blew the statement timeout on the
hourly heartbeat cron. WEEKLY counts stay live but are date-bounded (earned_trophy_earned_time_idx).
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from core.services.stats import compute_community_stats
from tests.factories import EarnedTrophyFactory, GameFactory, TrophyFactory

pytestmark = pytest.mark.django_db


def test_trophy_and_platinum_totals_read_from_denorms():
    # Totals come from the denorm columns, NOT the (unset) live EarnedTrophy rows.
    GameFactory(plats_earned_count=3)
    GameFactory(plats_earned_count=5)
    TrophyFactory(earned_count=40)
    TrophyFactory(earned_count=2)

    stats = compute_community_stats()

    assert stats['platinums']['total'] == 8   # 3 + 5, summed off Game.plats_earned_count
    assert stats['trophies']['total'] == 42   # 40 + 2, summed off Trophy.earned_count


def test_totals_coalesce_to_zero_on_empty_db():
    # No games/trophies -> Sum() is None -> coalesced to 0 (template-safe), never None.
    stats = compute_community_stats()
    assert stats['platinums']['total'] == 0
    assert stats['trophies']['total'] == 0


def test_weekly_counts_are_live_and_date_bounded():
    now = timezone.now()
    plat = TrophyFactory(trophy_type='platinum')
    bronze = TrophyFactory(trophy_type='bronze')
    # This week: one platinum + one bronze earned.
    EarnedTrophyFactory(trophy=plat, earned=True, earned_date_time=now)
    EarnedTrophyFactory(trophy=bronze, earned=True, earned_date_time=now)
    # Outside the 7-day window: excluded from both weekly counts.
    EarnedTrophyFactory(trophy=plat, earned=True, earned_date_time=now - timedelta(days=30))

    stats = compute_community_stats()

    assert stats['trophies']['weekly'] == 2    # both this-week earns; the 30-day-old one is excluded
    assert stats['platinums']['weekly'] == 1   # only the this-week platinum
