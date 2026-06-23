"""Tests for the no-delete 'maintenance' policy and the permanent earn_rank.

A lapsed badge is moved to status='maintenance' (NEVER deleted), preserving its
permanent earn_rank. Re-qualifying re-activates the same row without re-stamping
the rank. earn_rank is the all-time "Nth profile to earn this tier" ordinal, and
maintenance earners still count toward later earners' ranks.
"""

import pytest

from trophies.models import ProfileGame, UserBadge
from trophies.services.badge_service import _build_badge_context, handle_badge
from tests.factories import (
    BadgeFactory, ConceptFactory, GameFactory, ProfileFactory,
    ProfileGameFactory, StageFactory,
)

pytestmark = pytest.mark.django_db


def _series_with_one_stage(series="rebuild-maint"):
    badge = BadgeFactory(series_slug=series, tier=1)
    concept = ConceptFactory()
    game = GameFactory(concept=concept)
    StageFactory(series_slug=series, stage_number=1).concepts.add(concept)
    return badge, game


def _evaluate(profile, badge):
    ctx = _build_badge_context(profile, [badge])
    handle_badge(profile, badge, _context=ctx)


def _earn(profile, badge, game):
    ProfileGameFactory(profile=profile, game=game, has_plat=True)
    _evaluate(profile, badge)


def _lapse(profile, badge, game):
    ProfileGame.objects.filter(profile=profile, game=game).update(has_plat=False)
    _evaluate(profile, badge)


def test_lapse_moves_to_maintenance_not_delete_and_keeps_rank():
    badge, game = _series_with_one_stage()
    profile = ProfileFactory()
    _earn(profile, badge, game)

    ub = UserBadge.objects.get(profile=profile, badge=badge)
    assert ub.status == 'earned'
    assert ub.earn_rank == 1

    _lapse(profile, badge, game)

    assert UserBadge.objects.filter(profile=profile, badge=badge).exists()  # not deleted
    ub.refresh_from_db()
    assert ub.status == 'maintenance'
    assert ub.earn_rank == 1  # preserved


def test_requalify_reactivates_same_row_without_restamping_rank():
    badge, game = _series_with_one_stage("rebuild-requal")
    profile = ProfileFactory()
    _earn(profile, badge, game)
    original_pk = UserBadge.objects.get(profile=profile, badge=badge).pk

    _lapse(profile, badge, game)
    assert UserBadge.objects.get(profile=profile, badge=badge).status == 'maintenance'

    ProfileGame.objects.filter(profile=profile, game=game).update(has_plat=True)
    _evaluate(profile, badge)

    ub = UserBadge.objects.get(profile=profile, badge=badge)
    assert ub.status == 'earned'
    assert ub.earn_rank == 1        # not re-stamped
    assert ub.pk == original_pk     # same row, never deleted


def test_earn_rank_follows_earn_order_across_profiles():
    badge, game = _series_with_one_stage("rebuild-rank")
    ranks = []
    for _ in range(3):
        profile = ProfileFactory()
        _earn(profile, badge, game)
        ranks.append(UserBadge.objects.get(profile=profile, badge=badge).earn_rank)
    assert ranks == [1, 2, 3]


def test_calculate_total_xp_excludes_maintenance_completion_bonus():
    from trophies.services.xp_service import calculate_total_xp, BADGE_TIER_XP

    profile = ProfileFactory()
    badge = BadgeFactory(series_slug="xp-maint", tier=1)
    ub = UserBadge.objects.create(profile=profile, badge=badge, status='earned')
    earned_total, _s1, earned_count, _u1 = calculate_total_xp(profile)

    UserBadge.objects.filter(pk=ub.pk).update(status='maintenance')
    maint_total, _s2, maint_count, _u2 = calculate_total_xp(profile)

    assert earned_total - maint_total == BADGE_TIER_XP  # lost exactly the completion bonus
    assert earned_count == maint_count == 1             # still counted (shows everywhere)


def test_lapse_and_repair_recompute_cached_badge_xp():
    from trophies.models import ProfileGamification

    badge, game = _series_with_one_stage("xp-cache")
    profile = ProfileFactory()
    _earn(profile, badge, game)
    earned_xp = ProfileGamification.objects.get(profile=profile).total_badge_xp
    assert earned_xp > 0

    _lapse(profile, badge, game)
    lapsed_xp = ProfileGamification.objects.get(profile=profile).total_badge_xp
    assert lapsed_xp < earned_xp  # maintenance badge no longer grants XP

    ProfileGame.objects.filter(profile=profile, game=game).update(has_plat=True)
    _evaluate(profile, badge)
    repaired_xp = ProfileGamification.objects.get(profile=profile).total_badge_xp
    assert repaired_xp == earned_xp  # XP comes back on repair


def test_maintenance_earner_still_counts_toward_later_ranks():
    badge, game = _series_with_one_stage("rebuild-rank-maint")
    p1 = ProfileFactory()
    _earn(p1, badge, game)          # rank 1
    _lapse(p1, badge, game)         # p1 -> maintenance (still an earner)

    p2 = ProfileFactory()
    _earn(p2, badge, game)          # rank 2, because p1 still counts

    assert UserBadge.objects.get(profile=p2, badge=badge).earn_rank == 2
