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


def test_a_dated_full_complete_does_not_rescue_an_undated_default_group():
    """The completion-date branch keys on PROGRESS, not on whether a date is present:

        completion_date = base_date if base_prog == 100 else (full_date if full_complete else None)

    So when the default group is AT 100% its own date wins even when that date is null, and the
    game's `most_recent_trophy_date` is NOT consulted as a fallback. Easy to describe backwards
    (badge-system.md did, briefly), and the consequence is real: the badge is earned and holo, and
    still carries no earn date, so `apply_changes` stamps now() instead of the true completion.
    """
    _, ultra = _groups()
    series, stage = _series_with_stage(slug='undated-base')
    concept = ConceptFactory()
    stage.concepts.add(concept)
    game = _game(concept, platforms=('PS5',))
    gb = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    _complete(profile, game, full=True, day=6)
    assert evaluate_profile(profile, [gb])[gb.id].earned_date == _dt(6)

    # The default group keeps progress=100 but loses its date; the GAME keeps a real date.
    ProfileTrophyGroup.objects.filter(profile=profile, trophy_group__game=game).update(last_trophy_at=None)
    ProfileGame.objects.filter(profile=profile, game=game).update(most_recent_trophy_date=_dt(6))

    res = evaluate_profile(profile, [gb])[gb.id]
    assert res.base_earned is True and res.holo is True, 'progress is intact; only the date went'
    assert res.earned_date is None, (
        'full_complete was used as a DATE fallback -- it is only a fallback when the default group '
        'is short of 100%, not when it merely lacks a date'
    )


# ── platform routing between the two groups ──────────────────────────────────
def test_one_clear_credits_every_edition_the_stage_reaches():
    """THE cross-platform rule (owner's call, 2026-09). One stage is one WORK; clearing it on any platform
    clears it everywhere the stage is in scope.

    This test used to assert the opposite -- that the PS5 clear left Legacy HD at zero until the PS3 copy
    was done too. That made a cross-gen hunter buy and replay the same game once per edition to collect
    both badges, which is not what a badge is for.
    """
    legacy, ultra = _groups()
    series, stage = _series_with_stage()
    concept = ConceptFactory()               # one concept, two platform versions
    stage.concepts.add(concept)
    _game(concept, platforms=('PS3',))
    ps5 = _game(concept, platforms=('PS5',))
    gb_legacy = GroupBadgeFactory(series=series, platform_group=legacy)
    gb_ultra = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    _complete(profile, ps5, base=True)       # ONLY the PS5 version done
    res = evaluate_profile(profile, [gb_legacy, gb_ultra])

    assert res[gb_ultra.id].base_earned is True
    assert res[gb_legacy.id].base_earned is True, (
        'the PS5 clear did not credit Legacy HD -- satisfaction is still platform-scoped'
    )
    # Both editions still GATE on their own platform: the stage is required in each because each has an
    # obtainable copy of it. Gating never went cross-platform, only satisfaction.
    assert res[gb_legacy.id].gating_count == 1
    assert res[gb_ultra.id].gating_count == 1


def test_a_stage_with_no_copy_on_the_groups_platforms_is_out_of_scope():
    """The limit on the rule above. Cross-platform satisfaction applies only to stages the badge can SEE;
    a stage with nothing on the group's platforms is not that badge's business, so clearing it credits
    nothing there -- no gating, no satisfaction, no XP."""
    legacy, ultra = _groups()
    series, stage = _series_with_stage()
    concept = ConceptFactory()
    stage.concepts.add(concept)
    ps5 = _game(concept, platforms=('PS5',))      # PS5 ONLY -- nothing for Legacy HD to route to
    gb_legacy = GroupBadgeFactory(series=series, platform_group=legacy)
    gb_ultra = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    _complete(profile, ps5, base=True)
    res = evaluate_profile(profile, [gb_legacy, gb_ultra])

    assert res[gb_ultra.id].base_earned is True
    assert res[gb_legacy.id].gating_count == 0, 'a PS5-only stage gated a PS3 badge'
    assert res[gb_legacy.id].base_earned is False
    assert res[gb_legacy.id].xp_stage_count == 0, 'an out-of-scope stage paid XP to a badge it never reached'


def test_a_bundle_only_routes_to_platforms_every_member_runs_on():
    """A ConceptBundle is satisfied only when EVERY member is complete, so the platforms it can be
    completed on are the INTERSECTION of its members', not the union.

    The union made a badge nobody could earn: a bundle with a PS3-only member and a PS5-only member
    reported {PS3, PS5}, so it qualified for Ultra HD, GATED it, and could never be satisfied there -- a
    permanent "0 / N" chase with no path to completion, violating the engine's own rule that a badge must
    not demand work that cannot be done on its own platforms.
    """
    legacy, ultra = _groups()
    series, stage = _series_with_stage()
    from trophies.models import ConceptBundle

    bundle = ConceptBundle.objects.create(stage=stage, label='Mixed era')
    ps3_only, ps5_only = ConceptFactory(), ConceptFactory()
    _game(ps3_only, platforms=('PS3',))
    _game(ps5_only, platforms=('PS5',))
    bundle.concepts.set([ps3_only, ps5_only])
    gb_legacy = GroupBadgeFactory(series=series, platform_group=legacy)
    gb_ultra = GroupBadgeFactory(series=series, platform_group=ultra)

    res = evaluate_profile(ProfileFactory(), [gb_legacy, gb_ultra])

    assert res[gb_ultra.id].gating_count == 0, 'a bundle no Ultra HD hunter can finish gated Ultra HD'
    assert res[gb_legacy.id].gating_count == 0, 'a bundle no Legacy HD hunter can finish gated Legacy HD'


def test_a_bundle_whose_members_share_a_platform_still_gates_there():
    """The other half: intersection must not break the normal case. Both members on PS5 -> the bundle
    routes to Ultra HD exactly as before."""
    legacy, ultra = _groups()
    series, stage = _series_with_stage()
    from trophies.models import ConceptBundle

    bundle = ConceptBundle.objects.create(stage=stage, label='PS5 episodic')
    a, b = ConceptFactory(), ConceptFactory()
    _game(a, platforms=('PS5',))
    _game(b, platforms=('PS5',))
    bundle.concepts.set([a, b])
    gb_legacy = GroupBadgeFactory(series=series, platform_group=legacy)
    gb_ultra = GroupBadgeFactory(series=series, platform_group=ultra)

    res = evaluate_profile(ProfileFactory(), [gb_legacy, gb_ultra])

    assert res[gb_ultra.id].gating_count == 1
    assert res[gb_legacy.id].gating_count == 0


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


def test_concept_bundle_holo_needs_every_member_at_the_full_bar():
    """The base bar earns the badge; the HOLO bar is a separate, stricter ask on the same bundle.

    Pinned because nothing asserted it: the model docstring drifted into describing the holo rule
    (`ProfileGame.progress == 100` on every member) as if it were the earn rule, and no test
    disagreed. Base-only must leave holo off, and ONE member short of full must keep it off.
    """
    _, ultra = _groups()
    series, stage = _series_with_stage(slug='telltale-holo')
    bundle = ConceptBundleFactory(stage=stage)
    c1, c2 = ConceptFactory(), ConceptFactory()
    bundle.concepts.add(c1, c2)
    g1 = _game(c1, platforms=('PS5',))
    g2 = _game(c2, platforms=('PS5',))
    gb = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    _complete(profile, g1, base=True)
    _complete(profile, g2, base=True)
    res = evaluate_profile(profile, [gb])[gb.id]
    assert res.base_earned is True and res.holo is False, 'the base bar must not confer holo'

    _complete(profile, g1, full=True)        # one member at 100% incl DLC, the other not
    assert evaluate_profile(profile, [gb])[gb.id].holo is False, 'a partial bundle is not holo'

    _complete(profile, g2, full=True)        # every member at the full bar
    res = evaluate_profile(profile, [gb])[gb.id]
    assert res.base_earned is True and res.holo is True


def test_concept_bundle_with_one_undated_member_earns_but_carries_no_date():
    """A bundle's date is the LAST member to reach base -- unless a base-complete member has no
    dated game, in which case the bundle reports NO date at all rather than a date it only half
    knows (`_bundle_state` requires `all(d is not None ...)` before taking the max).

    Pinned because the consequence is non-obvious and reaches the badge: the stage stays satisfied,
    so the badge is still EARNED, but the bundle contributes nothing to `base_dates`, and
    `_earned_date` then returns None for the whole group badge. `apply_changes` stamps `now()` in
    that case, so the hunter keeps the badge and loses only the historical earn date.
    """
    _, ultra = _groups()
    series, stage = _series_with_stage(slug='telltale-dateless')
    bundle = ConceptBundleFactory(stage=stage)
    c1, c2 = ConceptFactory(), ConceptFactory()
    bundle.concepts.add(c1, c2)
    g1 = _game(c1, platforms=('PS5',))
    g2 = _game(c2, platforms=('PS5',))
    gb = GroupBadgeFactory(series=series, platform_group=ultra)

    profile = ProfileFactory()
    _complete(profile, g1, base=True, day=4)
    _complete(profile, g2, base=True, day=9)
    res = evaluate_profile(profile, [gb])[gb.id]
    assert res.base_earned is True
    assert res.earned_date == _dt(9), 'the LAST member to reach base dates the bundle'

    # Strip the date off the member that currently dates the bundle. PSN can leave a default group
    # at 100% with no last_trophy_at, so this is a real state, not a contrived one.
    ProfileTrophyGroup.objects.filter(
        profile=profile, trophy_group__game=g2,
    ).update(last_trophy_at=None)

    res = evaluate_profile(profile, [gb])[gb.id]
    assert res.base_earned is True, 'a missing date must not cost the hunter the badge'
    assert res.earned_date is None, 'a half-known bundle date is reported as no date'


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
