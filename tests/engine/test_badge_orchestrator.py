"""Integration tests for the badge evaluation ORCHESTRATOR (trophies/services/badge_orchestrator).

These hit the DB (real Stages/Concepts/Games/ProfileGame/ProfileTrophyGroup + the new group-badge models)
and assert the ORM->engine mapping is correct: base_complete comes from the default ProfileTrophyGroup,
full_complete from ProfileGame.progress, platform routing splits games between groups, the delisted policy
differs per group, ConceptBundles collapse to a synthesized qualifier, and the per-profile reads stay bounded.
"""
import datetime as dt

import pytest
from django.utils import timezone

from trophies.models import ProfileGame, TrophyGroup, ProfileTrophyGroup
from trophies.services.badge_orchestrator import evaluate_profile
from tests.factories import (
    ProfileFactory, ConceptFactory, GameFactory, ProfileGameFactory,
    StageFactory, ConceptBundleFactory,
    PlatformGroupFactory, BadgeSeriesFactory, GroupBadgeFactory,
)  # ProfileGameFactory used for the unrelated-library bulk in the bounded-reads test

pytestmark = pytest.mark.django_db


def _groups():
    legacy = PlatformGroupFactory(key='legacy-hd', name='Legacy HD', platforms=['PS3', 'PSVITA'], exclude_delisted=False)
    ultra = PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS4', 'PS5'], exclude_delisted=True)
    return legacy, ultra


def _game(concept, platforms=('PS5',), obtainable=True, delisted=False):
    return GameFactory(concept=concept, title_platform=list(platforms), is_obtainable=obtainable, is_delisted=delisted)


def _dt(day):
    return timezone.make_aware(dt.datetime(2026, 1, day))


def _complete(profile, game, base=False, full=False, day=None):
    """Give a profile a completion state: base -> default group at 100%; full -> whole game at 100% (implies
    base). `day` fixes the default group's last_trophy_at (the earn-date source). Idempotent (update_or_create)
    so it's safe to call again on the same game to escalate base -> full."""
    base = base or full
    ProfileGame.objects.update_or_create(
        profile=profile, game=game, defaults={'progress': 100 if full else 50 if base else 0},
    )
    if base:
        tg, _ = TrophyGroup.objects.get_or_create(
            game=game, trophy_group_id='default', defaults={'trophy_group_name': 'Base'},
        )
        ProfileTrophyGroup.objects.update_or_create(
            profile=profile, trophy_group=tg,
            defaults={'progress': 100, 'last_trophy_at': _dt(day) if day else timezone.now()},
        )


def _series_with_stage(slug='gow', stage_number=1):
    series = BadgeSeriesFactory(series_slug=slug, name='God of War')
    stage = StageFactory(series_slug=slug, stage_number=stage_number)
    return series, stage


# ── base bar from ProfileTrophyGroup ─────────────────────────────────────────
def test_base_earned_from_default_trophy_group():
    _, ultra = _groups()
    series, stage = _series_with_stage()
    concept = ConceptFactory()
    stage.concepts.add(concept)
    game = _game(concept, platforms=('PS5',))
    gb = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    # No completion yet -> not earned.
    assert evaluate_profile(profile, [gb])[gb.id].base_earned is False
    # Default group at 100% -> base earned.
    _complete(profile, game, base=True)
    assert evaluate_profile(profile, [gb])[gb.id].base_earned is True


def test_holo_needs_full_complete():
    _, ultra = _groups()
    series, stage = _series_with_stage()
    concept = ConceptFactory()
    stage.concepts.add(concept)
    game = _game(concept)
    gb = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    _complete(profile, game, base=True, full=False)     # base done, DLC left
    r = evaluate_profile(profile, [gb])[gb.id]
    assert r.base_earned is True and r.holo is False

    _complete(profile, game, full=True)                 # now 100% incl DLC
    r2 = evaluate_profile(profile, [gb])[gb.id]
    assert r2.base_earned is True and r2.holo is True


# ── platform routing between the two groups ──────────────────────────────────
def test_platform_routing_splits_between_groups():
    legacy, ultra = _groups()
    series, stage = _series_with_stage()
    concept = ConceptFactory()               # one concept, two platform versions
    stage.concepts.add(concept)
    ps3 = _game(concept, platforms=('PS3',))
    ps5 = _game(concept, platforms=('PS5',))
    gb_legacy = GroupBadgeFactory(series=series, platform_group=legacy)
    gb_ultra = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    _complete(profile, ps5, base=True)       # only the PS5 version done
    res = evaluate_profile(profile, [gb_legacy, gb_ultra])
    assert res[gb_ultra.id].base_earned is True      # Ultra: satisfied via PS5
    assert res[gb_legacy.id].base_earned is False     # Legacy: PS3 version untouched

    _complete(profile, ps3, base=True)       # now the PS3 version too
    res2 = evaluate_profile(profile, [gb_legacy, gb_ultra])
    assert res2[gb_legacy.id].base_earned is True


# ── delisted policy differs per group ────────────────────────────────────────
def test_delisted_excluded_in_ultra_but_still_satisfies():
    _, ultra = _groups()
    series = BadgeSeriesFactory(series_slug='rgg')
    s1 = StageFactory(series_slug='rgg', stage_number=1)
    s2 = StageFactory(series_slug='rgg', stage_number=2)
    c1, c2 = ConceptFactory(), ConceptFactory()
    s1.concepts.add(c1)
    s2.concepts.add(c2)
    normal = _game(c1, platforms=('PS5',))
    delisted = _game(c2, platforms=('PS5',), delisted=True)
    gb = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    _complete(profile, normal, base=True)    # stage2's only game is delisted + untouched
    r = evaluate_profile(profile, [gb])[gb.id]
    # Ultra excludes delisted from gating -> stage2 doesn't gate -> earned on stage1 alone.
    assert r.gating_count == 1 and r.base_earned is True


def test_delisted_gates_in_legacy():
    legacy, _ = _groups()
    series, stage = _series_with_stage(slug='legacy-series')
    concept = ConceptFactory()
    stage.concepts.add(concept)
    _game(concept, platforms=('PS3',), delisted=True)   # delisted PS3, untouched
    gb = GroupBadgeFactory(series=series, platform_group=legacy)

    r = evaluate_profile(ProfileFactory(), [gb])[gb.id]
    assert r.gating_count == 1 and r.base_earned is False   # Legacy counts delisted -> required


# ── ConceptBundle ────────────────────────────────────────────────────────────
def test_concept_bundle_synthesized_completion():
    _, ultra = _groups()
    series, stage = _series_with_stage(slug='telltale')
    bundle = ConceptBundleFactory(stage=stage)
    c1, c2 = ConceptFactory(), ConceptFactory()
    bundle.concepts.add(c1, c2)
    g1 = _game(c1, platforms=('PS5',))
    g2 = _game(c2, platforms=('PS5',))
    gb = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    _complete(profile, g1, base=True)        # only one member complete
    assert evaluate_profile(profile, [gb])[gb.id].base_earned is False

    _complete(profile, g2, base=True)        # both members -> bundle satisfied
    assert evaluate_profile(profile, [gb])[gb.id].base_earned is True


# ── default (all live) + whale-bounded reads ─────────────────────────────────
def test_evaluate_defaults_to_all_live():
    _, ultra = _groups()
    series, stage = _series_with_stage(slug='live-series')
    concept = ConceptFactory()
    stage.concepts.add(concept)
    game = _game(concept)
    gb = GroupBadgeFactory(series=series, platform_group=ultra, is_live=True)
    hidden = GroupBadgeFactory(series=BadgeSeriesFactory(series_slug='hidden'), platform_group=ultra, is_live=False)

    profile = ProfileFactory()
    _complete(profile, game, base=True)
    res = evaluate_profile(profile)          # no explicit list -> all live badges
    assert gb.id in res and hidden.id not in res
    assert res[gb.id].base_earned is True


def test_reads_are_bounded_by_catalog_not_library(django_assert_max_num_queries):
    _, ultra = _groups()
    series, stage = _series_with_stage(slug='bounded')
    concept = ConceptFactory()
    stage.concepts.add(concept)
    game = _game(concept)
    gb = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    _complete(profile, game, base=True)
    # A big unrelated library must not change the query shape (bounded to catalog games).
    for _ in range(40):
        ProfileGameFactory(profile=profile, progress=100)

    with django_assert_max_num_queries(15):
        res = evaluate_profile(profile, [gb])
    assert res[gb.id].base_earned is True


# ── the full=>base guard (holo-without-base is impossible) ───────────────────
def test_full_complete_without_ptg_row_infers_base():
    _, ultra = _groups()
    series, stage = _series_with_stage(slug='noptg')
    concept = ConceptFactory()
    stage.concepts.add(concept)
    game = _game(concept)
    gb = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    # Whole game at 100% but NO default ProfileTrophyGroup row (a stale/missing denorm). The orchestrator's
    # guard must infer base from full, so we get base AND holo -- never holo-without-base.
    ProfileGame.objects.create(profile=profile, game=game, progress=100)
    r = evaluate_profile(profile, [gb])[gb.id]
    assert r.base_earned is True and r.holo is True


# ── megamix (min_count) through the ORM ──────────────────────────────────────
def test_megamix_min_count_via_orm():
    _, ultra = _groups()
    series = BadgeSeriesFactory(series_slug='mm', completion_policy='min_count', min_required=1)
    s1 = StageFactory(series_slug='mm', stage_number=1)
    s2 = StageFactory(series_slug='mm', stage_number=2)
    c1, c2 = ConceptFactory(), ConceptFactory()
    s1.concepts.add(c1)
    s2.concepts.add(c2)
    g1 = _game(c1)
    _game(c2)
    gb = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    _complete(profile, g1, base=True)        # 1 of 2 satisfies min_required=1
    assert evaluate_profile(profile, [gb])[gb.id].base_earned is True


# ── earn date survives the ORM mapping ───────────────────────────────────────
def test_earned_date_through_orm():
    _, ultra = _groups()
    series = BadgeSeriesFactory(series_slug='ed')
    s1 = StageFactory(series_slug='ed', stage_number=1)
    s2 = StageFactory(series_slug='ed', stage_number=2)
    c1, c2 = ConceptFactory(), ConceptFactory()
    s1.concepts.add(c1)
    s2.concepts.add(c2)
    g1, g2 = _game(c1), _game(c2)
    gb = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    _complete(profile, g1, base=True, day=3)
    _complete(profile, g2, base=True, day=7)
    r = evaluate_profile(profile, [gb])[gb.id]
    assert r.base_earned is True and r.earned_date == _dt(7)   # last gating stage to fall


# ── stage 0 skipped through the ORM ──────────────────────────────────────────
def test_stage_zero_skipped_via_orm():
    _, ultra = _groups()
    series = BadgeSeriesFactory(series_slug='s0')
    s0 = StageFactory(series_slug='s0', stage_number=0)
    s1 = StageFactory(series_slug='s0', stage_number=1)
    c0, c1 = ConceptFactory(), ConceptFactory()
    s0.concepts.add(c0)
    s1.concepts.add(c1)
    _game(c0)                                # stage 0 game left untouched
    g1 = _game(c1)
    gb = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    _complete(profile, g1, base=True)
    r = evaluate_profile(profile, [gb])[gb.id]
    assert r.gating_count == 1 and r.base_earned is True
