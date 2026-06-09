"""Tests for Badge.recompute_rarity (the recalc_badge_rarity command logic).

rarity_pct = linked profiles who earned the badge / all linked profiles * 100;
rarity_class buckets it (lower = rarer); rarity_rank ranks LIVE badges, 1 = rarest.
"""

import pytest

from trophies.models import Badge, UserBadge
from tests.factories import BadgeFactory, ProfileFactory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("pct,expected", [
    (0.0, 'mythic'), (0.5, 'mythic'),
    (1.0, 'rare'), (4.99, 'rare'),
    (5.0, 'uncommon'), (19.99, 'uncommon'),
    (20.0, 'common'), (75.0, 'common'),
])
def test_rarity_class_for(pct, expected):
    assert Badge.rarity_class_for(pct) == expected


def test_recompute_rarity_pct_class_and_rank():
    linked = [ProfileFactory(is_linked=True) for _ in range(4)]
    rare_badge = BadgeFactory(series_slug='rar-a', tier=1, is_live=True)    # 0 earners
    common_badge = BadgeFactory(series_slug='rar-b', tier=1, is_live=True)  # 1/4 = 25%
    UserBadge.objects.create(profile=linked[0], badge=common_badge)

    result = Badge.recompute_rarity()

    rare_badge.refresh_from_db()
    common_badge.refresh_from_db()
    assert rare_badge.rarity_pct == 0.0
    assert rare_badge.rarity_class == 'mythic'
    assert common_badge.rarity_pct == 25.0
    assert common_badge.rarity_class == 'common'
    assert rare_badge.rarity_rank == 1      # rarest first
    assert common_badge.rarity_rank == 2
    assert result['linked_profiles'] == 4


def test_non_live_badges_are_not_ranked():
    ProfileFactory(is_linked=True)
    nl = BadgeFactory(series_slug='rar-nl', tier=1, is_live=False)
    Badge.recompute_rarity()
    nl.refresh_from_db()
    assert nl.rarity_rank is None
    assert nl.rarity_class == 'mythic'  # 0 earners still gets a class


def test_unlinked_profiles_excluded_from_base_and_earners():
    ProfileFactory(is_linked=True)
    unlinked = ProfileFactory(is_linked=False)
    badge = BadgeFactory(series_slug='rar-u', tier=1, is_live=True)
    UserBadge.objects.create(profile=unlinked, badge=badge)  # earner, but not linked

    Badge.recompute_rarity()
    badge.refresh_from_db()
    assert badge.rarity_pct == 0.0  # 0 linked earners / 1 linked profile


def test_recompute_handles_zero_linked_profiles():
    badge = BadgeFactory(series_slug='rar-z', tier=1, is_live=True)
    Badge.recompute_rarity()  # no linked profiles -> no div-by-zero
    badge.refresh_from_db()
    assert badge.rarity_pct == 0.0
    assert badge.rarity_rank == 1
