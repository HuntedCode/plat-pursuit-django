"""Spine tests for badge evaluation (trophies/services/badge_service.py).

handle_badge() decides whether a profile has earned a badge. For concept-based
badges (series/collection/developer/user/genre) a badge is earned when every
non-zero stage is complete AND the previous tier is already earned. Stage
completion depends on the tier:

- plat-check tiers (1=Bronze, 3=Gold) and megamix: a stage is complete when any
  game in its concepts has ProfileGame.has_plat=True.
- progress-check tiers (2=Silver, 4=Platinum): complete when any such game has
  progress=100.

Plus: Stage 0 is optional, required_tiers can scope a stage to specific tiers,
ConceptBundles act as a single qualifier (satisfied when all members are at 100%,
or, on plat-check tiers, when any member carries a real platinum), and megamix
badges use requires_all / min_required instead of "all stages".

These tests drive the real sync path: build the prefetch context with
_build_badge_context (as check_profile_badges does) and call handle_badge with
add_role_only=True so no Discord messages fire. Test badges have no
discord_role_id or title, so award/revoke has no external side effects.
"""

import pytest

from trophies.models import UserBadge, UserBadgeProgress
from trophies.services.badge_service import _build_badge_context, handle_badge
from tests.factories import (
    BadgeFactory,
    ConceptBundleFactory,
    ConceptFactory,
    GameFactory,
    ProfileFactory,
    ProfileGameFactory,
    StageFactory,
)

pytestmark = pytest.mark.django_db


# --- helpers ------------------------------------------------------------------


def _stage_with_concept(series_slug, stage_number, required_tiers=None):
    """Create a stage in `series_slug` holding one concept (with one game)."""
    concept = ConceptFactory()
    game = GameFactory(concept=concept)
    stage = StageFactory(
        series_slug=series_slug,
        stage_number=stage_number,
        required_tiers=required_tiers or [],
    )
    stage.concepts.add(concept)
    return stage, concept, game


def _evaluate(profile, badge, all_badges=None):
    """Run handle_badge through the real prefetch-context path."""
    ctx = _build_badge_context(profile, all_badges or [badge])
    handle_badge(profile, badge, add_role_only=True, _context=ctx)


def _earned(profile, badge):
    return UserBadge.objects.filter(profile=profile, badge=badge).exists()


def _completed_count(profile, badge):
    return UserBadgeProgress.objects.get(profile=profile, badge=badge).completed_concepts


# --- tier-1 award / partial ---------------------------------------------------


def test_tier1_awarded_when_all_stages_platted():
    series = "rebuild-all-platted"
    badge = BadgeFactory(series_slug=series, tier=1)
    _, _, g1 = _stage_with_concept(series, 1)
    _, _, g2 = _stage_with_concept(series, 2)
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=g1, has_plat=True)
    ProfileGameFactory(profile=profile, game=g2, has_plat=True)

    _evaluate(profile, badge)

    assert _earned(profile, badge)
    assert _completed_count(profile, badge) == 2


def test_tier1_not_awarded_when_a_stage_incomplete():
    series = "rebuild-partial"
    badge = BadgeFactory(series_slug=series, tier=1)
    _, _, g1 = _stage_with_concept(series, 1)
    _, _, g2 = _stage_with_concept(series, 2)
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=g1, has_plat=True)
    ProfileGameFactory(profile=profile, game=g2, has_plat=False)  # not platted

    _evaluate(profile, badge)

    assert not _earned(profile, badge)
    assert _completed_count(profile, badge) == 1


# --- plat-check vs progress-check ---------------------------------------------


def test_plat_check_tier_ignores_progress_without_platinum():
    # Tier 1 (plat-check): a game at 100% but without the platinum does NOT count.
    series = "rebuild-platcheck"
    badge = BadgeFactory(series_slug=series, tier=1)
    _, _, game = _stage_with_concept(series, 1)
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=False)

    _evaluate(profile, badge)

    assert not _earned(profile, badge)
    assert _completed_count(profile, badge) == 0


def test_progress_check_tier_counts_progress_without_platinum():
    # Tier 2 (progress-check): the same 100%-no-plat game DOES complete the stage
    # (and, with tiers now independent, earns the badge outright).
    series = "rebuild-progresscheck"
    badge = BadgeFactory(series_slug=series, tier=2)
    _, _, game = _stage_with_concept(series, 1)
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=False)

    _evaluate(profile, badge)

    assert _completed_count(profile, badge) == 1


# --- tier independence (no prerequisite) --------------------------------------


def test_tiers_are_independent_no_prerequisite():
    # Each badge tier is its own framed artifact, earned on its own merits — a
    # higher tier is awarded even if a lower tier is not, with no prerequisite.
    series = "rebuild-independent"
    tier1 = BadgeFactory(series_slug=series, tier=1)
    tier2 = BadgeFactory(series_slug=series, tier=2)
    # One stage applies to all tiers; the game satisfies both plat- and
    # progress-checks, so tier 2's requirements are met.
    _, _, game = _stage_with_concept(series, 1)
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=game, has_plat=True, progress=100)

    ctx = _build_badge_context(profile, [tier1, tier2])

    # Handle ONLY tier 2 (tier 1 never earned) — it is still awarded.
    handle_badge(profile, tier2, add_role_only=True, _context=ctx)

    assert _earned(profile, tier2)
    assert not _earned(profile, tier1)  # lower tier irrelevant to earning tier 2


# --- stage 0 + required_tiers -------------------------------------------------


def test_stage_zero_is_optional():
    series = "rebuild-stage0"
    badge = BadgeFactory(series_slug=series, tier=1)
    _, _, g0 = _stage_with_concept(series, 0)  # optional, left incomplete
    _, _, g1 = _stage_with_concept(series, 1)
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=g1, has_plat=True)
    # g0 intentionally not platted

    _evaluate(profile, badge)

    assert _earned(profile, badge)  # stage 0 doesn't block
    assert _completed_count(profile, badge) == 1  # and isn't counted


def test_required_tiers_excludes_stage_from_other_tiers():
    series = "rebuild-reqtiers"
    badge = BadgeFactory(series_slug=series, tier=1)
    _, _, g_all = _stage_with_concept(series, 1, required_tiers=[])  # all tiers
    _, _, g_tier2only = _stage_with_concept(series, 2, required_tiers=[2])
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=g_all, has_plat=True)
    # the tier-2-only stage is left incomplete; it must not affect tier 1

    _evaluate(profile, badge)

    assert _earned(profile, badge)
    assert _completed_count(profile, badge) == 1


# --- megamix ------------------------------------------------------------------


def test_megamix_min_required():
    series = "rebuild-megamix-min"
    badge = BadgeFactory(
        series_slug=series, tier=1, badge_type="megamix",
        requires_all=False, min_required=2,
    )
    _, _, g1 = _stage_with_concept(series, 1)
    _, _, g2 = _stage_with_concept(series, 2)
    _, _, g3 = _stage_with_concept(series, 3)
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=g1, has_plat=True)
    ProfileGameFactory(profile=profile, game=g2, has_plat=True)
    # g3 not platted -> 2 of 3 complete, min_required=2 -> earned

    _evaluate(profile, badge)

    assert _earned(profile, badge)
    assert _completed_count(profile, badge) == 2


def test_megamix_min_required_not_met():
    series = "rebuild-megamix-min-fail"
    badge = BadgeFactory(
        series_slug=series, tier=1, badge_type="megamix",
        requires_all=False, min_required=2,
    )
    _, _, g1 = _stage_with_concept(series, 1)
    _, _, g2 = _stage_with_concept(series, 2)
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=g1, has_plat=True)  # only 1

    _evaluate(profile, badge)

    assert not _earned(profile, badge)


def test_megamix_requires_all():
    series = "rebuild-megamix-all"
    badge = BadgeFactory(
        series_slug=series, tier=1, badge_type="megamix", requires_all=True,
    )
    _, _, g1 = _stage_with_concept(series, 1)
    _, _, g2 = _stage_with_concept(series, 2)
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=g1, has_plat=True)

    _evaluate(profile, badge)
    assert not _earned(profile, badge)  # 1 of 2

    ProfileGameFactory(profile=profile, game=g2, has_plat=True)
    _evaluate(profile, badge)
    assert _earned(profile, badge)  # 2 of 2


# --- ConceptBundles -----------------------------------------------------------


def _stage_with_bundle(series_slug, stage_number, member_count=2):
    """A stage whose only qualifier is a bundle of `member_count` concepts."""
    stage = StageFactory(series_slug=series_slug, stage_number=stage_number)
    bundle = ConceptBundleFactory(stage=stage)
    games = []
    for _ in range(member_count):
        concept = ConceptFactory()
        games.append(GameFactory(concept=concept))
        bundle.concepts.add(concept)
    return stage, games


def test_bundle_satisfied_when_all_members_fully_cleared():
    # Synthesized-platinum path: a bundle is satisfied when every member is at
    # 100% (even with no real platinum on any of them).
    series = "rebuild-bundle-ok"
    badge = BadgeFactory(series_slug=series, tier=1)
    _, games = _stage_with_bundle(series, 1, member_count=2)
    profile = ProfileFactory()
    for g in games:
        ProfileGameFactory(profile=profile, game=g, progress=100, has_plat=False)

    _evaluate(profile, badge)

    assert _earned(profile, badge)
    assert _completed_count(profile, badge) == 1


def test_bundle_not_satisfied_when_a_member_incomplete():
    series = "rebuild-bundle-partial"
    badge = BadgeFactory(series_slug=series, tier=1)
    _, games = _stage_with_bundle(series, 1, member_count=2)
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=games[0], progress=100, has_plat=False)
    # games[1] left incomplete -> bundle not fully cleared

    _evaluate(profile, badge)

    assert not _earned(profile, badge)
    assert _completed_count(profile, badge) == 0
