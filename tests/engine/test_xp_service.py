"""Spine tests for the Badge XP service (trophies/services/xp_service.py).

These assert the XP *contracts*, referencing the XP constants rather than
hardcoding numbers, so a future gamification rebalance that changes a value
updates in one place. A test that goes red on an intentional rule change is
doing its job (flagging that behavior moved), not being brittle.

NOTE: Badge XP is expected to evolve as the Pursuer / Job system lands. Treat
this file as the churnable layer of the spine — keep it, but expect to revise it
deliberately alongside those changes.
"""

import pytest

from trophies.models import ProfileGamification
from trophies.services.xp_service import (
    calculate_progress_xp_for_badge,
    calculate_total_xp,
    get_tier_xp,
    update_profile_gamification,
)
from trophies.util_modules.constants import (
    BADGE_TIER_XP,
    BRONZE_STAGE_XP,
    GOLD_STAGE_XP,
)
from tests.factories import (
    BadgeFactory,
    ProfileFactory,
    UserBadgeFactory,
    UserBadgeProgressFactory,
)


def test_get_tier_xp_maps_each_tier():
    # Pure mapping; references the constants so it tracks rebalances.
    from trophies.util_modules.constants import PLAT_STAGE_XP, SILVER_STAGE_XP

    assert get_tier_xp(1) == BRONZE_STAGE_XP
    assert get_tier_xp(2) == SILVER_STAGE_XP
    assert get_tier_xp(3) == GOLD_STAGE_XP
    assert get_tier_xp(4) == PLAT_STAGE_XP
    assert get_tier_xp(99) == 0  # unknown tier yields no XP


@pytest.mark.django_db
def test_progress_xp_scales_with_completed_concepts():
    badge = BadgeFactory(tier=1)
    assert calculate_progress_xp_for_badge(badge, 10) == 10 * BRONZE_STAGE_XP
    assert calculate_progress_xp_for_badge(badge, 0) == 0


@pytest.mark.django_db
def test_total_xp_combines_progress_and_badge_bonus():
    profile = ProfileFactory()
    badge = BadgeFactory(series_slug="resident-evil", tier=1)
    UserBadgeProgressFactory(profile=profile, badge=badge, completed_concepts=4)
    UserBadgeFactory(profile=profile, badge=badge)

    total, series, total_badges, unique = calculate_total_xp(profile)

    expected = 4 * BRONZE_STAGE_XP + BADGE_TIER_XP
    assert total == expected
    assert series["resident-evil"] == expected
    assert total_badges == 1
    assert unique == 1


@pytest.mark.django_db
def test_progress_on_badge_without_series_slug_contributes_no_xp():
    # Characterizes current behavior: progress XP is only counted for badges that
    # belong to a series. (Earned series-less badges still add the flat bonus.)
    profile = ProfileFactory()
    badge = BadgeFactory(series_slug=None, tier=1)
    UserBadgeProgressFactory(profile=profile, badge=badge, completed_concepts=5)

    total, series, total_badges, unique = calculate_total_xp(profile)

    assert total == 0
    assert series == {}


@pytest.mark.django_db
def test_update_profile_gamification_persists_denormalized_totals():
    profile = ProfileFactory()
    badge = BadgeFactory(series_slug="dark-souls", tier=3)
    UserBadgeProgressFactory(profile=profile, badge=badge, completed_concepts=2)

    gam = update_profile_gamification(profile)

    assert gam.total_badge_xp == 2 * GOLD_STAGE_XP
    assert gam.series_badge_xp["dark-souls"] == 2 * GOLD_STAGE_XP
    assert gam.total_badges_earned == 0


@pytest.mark.django_db
def test_saving_progress_auto_updates_gamification_via_signal():
    # The real-time denormalization contract: creating progress should update
    # ProfileGamification through the post_save signal, with no explicit call.
    profile = ProfileFactory()
    badge = BadgeFactory(series_slug="halo", tier=1)
    UserBadgeProgressFactory(profile=profile, badge=badge, completed_concepts=3)

    gam = ProfileGamification.objects.get(profile=profile)
    assert gam.total_badge_xp == 3 * BRONZE_STAGE_XP
