"""Tests for the Frame builder (trophies/services/frame_service.py).

The Frame's first production mounting. These pin the data-derivation contract
(state from viewer progress, tier-name mapping, art layers, progress math) so
the upcoming surfaces — and the future customization layer — build on a stable
base.
"""

import pytest

from trophies.services.frame_service import build_badge_frame
from tests.factories import (
    BadgeFactory,
    CompanyFactory,
    ProfileFactory,
    UserBadgeFactory,
    UserBadgeProgressFactory,
)

pytestmark = pytest.mark.django_db


def test_anonymous_viewer_gets_showcase_earned():
    badge = BadgeFactory(tier=1, required_stages=10)

    frame = build_badge_frame(badge)  # no profile

    assert frame["state"] == "earned"
    assert frame["tier"] == "bronze"
    assert frame["stages_total"] == 10
    assert frame["art_layers"]  # at least backdrop + main resolved to URLs


def test_tier_int_maps_to_name_and_next_label():
    assert build_badge_frame(BadgeFactory(tier=4))["tier"] == "platinum"
    assert build_badge_frame(BadgeFactory(tier=4))["next_tier_label"] == "Maxed"
    assert build_badge_frame(BadgeFactory(tier=1))["next_tier_label"] == "Silver"


def test_earned_viewer_state_and_date():
    profile = ProfileFactory()
    badge = BadgeFactory(tier=2, required_stages=8)
    UserBadgeFactory(profile=profile, badge=badge)

    frame = build_badge_frame(badge, profile)

    assert frame["state"] == "earned"
    assert frame["stages_done"] == 8
    assert "earned_date" in frame  # formatted string


def test_in_progress_viewer_progress_math():
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=10)
    UserBadgeProgressFactory(profile=profile, badge=badge, completed_concepts=3)

    frame = build_badge_frame(badge, profile)

    assert frame["state"] == "in_progress"
    assert frame["stages_done"] == 3
    assert frame["progress_pct"] == 30


def test_unearned_viewer_with_no_progress():
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=10)

    frame = build_badge_frame(badge, profile)

    assert frame["state"] == "unearned"
    assert frame["stages_done"] == 0


def test_rarity_set_number_and_type_in_frame():
    badge = BadgeFactory(
        tier=1, badge_type='series', set_number=42,
        rarity_pct=3.5, rarity_rank=7, rarity_class='rare',
    )

    frame = build_badge_frame(badge)

    assert frame["set_number"] == 42
    assert frame["rarity_pct"] == 3.5
    assert frame["rarity_rank"] == 7
    assert frame["rarity_class"] == 'rare'
    assert frame["badge_type"]  # human display label


def test_effective_franchise_and_developer_in_frame():
    from trophies.models import Franchise
    fr = Franchise.objects.create(igdb_id=1, name='Halo', slug='halo', source_type='franchise')
    dev = CompanyFactory(name='Bungie')
    badge = BadgeFactory(tier=1)
    badge.franchise = fr
    badge.developer = dev
    badge.save()

    frame = build_badge_frame(badge)

    assert frame["franchise"] == 'Halo'
    assert frame["developer"] == 'Bungie'


def test_earn_rank_becomes_engraving_and_series_xp_when_earned():
    from trophies.models import UserBadge
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=5)
    ub = UserBadgeFactory(profile=profile, badge=badge)
    UserBadge.objects.filter(pk=ub.pk).update(earn_rank=12)

    frame = build_badge_frame(badge, profile)

    assert frame["engraving_rank"] == 12
    assert frame["series_xp"] > 0  # earned -> completion bonus counts


def test_in_progress_with_zero_required_stages_no_divide_by_zero():
    # required_stages=0 must not raise ZeroDivisionError in the progress math.
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=0)
    UserBadgeProgressFactory(profile=profile, badge=badge, completed_concepts=2)

    frame = build_badge_frame(badge, profile)

    assert frame["state"] == "in_progress"
    assert frame["progress_pct"] == 0  # guarded fallback, not an exception
