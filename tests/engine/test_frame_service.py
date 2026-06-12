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


def test_earned_count_and_funder_in_frame():
    funder = ProfileFactory(display_psn_username='HeroHunter')
    badge = BadgeFactory(tier=1, earned_count=1234)
    badge.funded_by = funder
    badge.save()

    frame = build_badge_frame(badge)

    assert frame["earned_count"] == 1234
    assert frame["funded_by"] == 'HeroHunter'


def test_funder_credit_falls_back_to_psn_username():
    # A real donor whose display name is unset must still get credited
    # (psn_username is normalized to lowercase, which is fine for a credit).
    funder = ProfileFactory(psn_username='rawhandle', display_psn_username='')
    badge = BadgeFactory(tier=1)
    badge.funded_by = funder
    badge.save()

    frame = build_badge_frame(badge)

    assert frame["funded_by"] == 'rawhandle'


def test_effective_franchise_collection_and_developer_in_frame():
    from trophies.models import Franchise
    fr = Franchise.objects.create(igdb_id=1, name='Halo', slug='halo', source_type='franchise')
    coll = Franchise.objects.create(igdb_id=2, name='Bungie Classics', slug='bungie-classics', source_type='collection')
    dev = CompanyFactory(name='Bungie')
    badge = BadgeFactory(tier=1)
    badge.franchise = fr
    badge.collection = coll
    badge.developer = dev
    badge.save()

    frame = build_badge_frame(badge)

    # All three subject keys populate; the front face picks one by precedence
    # (franchise > collection > developer) in the template.
    assert frame["franchise"] == 'Halo'
    assert frame["collection"] == 'Bungie Classics'
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


def test_maintenance_badge_renders_maintenance_state():
    from trophies.models import UserBadge
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=5)
    ub = UserBadgeFactory(profile=profile, badge=badge)
    UserBadge.objects.filter(pk=ub.pk).update(status='maintenance', earn_rank=3)

    frame = build_badge_frame(badge, profile)

    assert frame["state"] == "maintenance"  # not 'earned'
    assert frame["engraving_rank"] == 3     # permanent, survives the lapse


def test_current_rank_uses_earners_leaderboard(monkeypatch):
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1)
    UserBadgeFactory(profile=profile, badge=badge)
    monkeypatch.setattr(
        'trophies.services.redis_leaderboard_service.get_earners_rank',
        lambda slug, pid: 7,
    )

    frame = build_badge_frame(badge, profile)

    assert frame["current_rank"] == 7  # used as-is (already 1-indexed)


def test_current_rank_omitted_when_not_on_leaderboard(monkeypatch):
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1)
    UserBadgeFactory(profile=profile, badge=badge)
    monkeypatch.setattr(
        'trophies.services.redis_leaderboard_service.get_earners_rank',
        lambda slug, pid: None,
    )

    frame = build_badge_frame(badge, profile)

    assert "current_rank" not in frame


def test_in_progress_with_zero_required_stages_no_divide_by_zero():
    # required_stages=0 must not raise ZeroDivisionError in the progress math.
    profile = ProfileFactory()
    badge = BadgeFactory(tier=1, required_stages=0)
    UserBadgeProgressFactory(profile=profile, badge=badge, completed_concepts=2)

    frame = build_badge_frame(badge, profile)

    assert frame["state"] == "in_progress"
    assert frame["progress_pct"] == 0  # guarded fallback, not an exception
