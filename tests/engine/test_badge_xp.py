"""Badge XP: pure compute_badge_xp + the recompute_standing write seam."""
import pytest
from django.utils import timezone

from trophies.services.badge_engine import GroupBadgeResult
from trophies.services.badge_xp import compute_badge_xp, XP_PER_STAGE, XP_BADGE_COMPLETION_BONUS


def _res(base_satisfied_count, base_earned, gating_count=None):
    gc = base_satisfied_count if gating_count is None else gating_count
    return GroupBadgeResult(
        base_earned=base_earned, holo=False, gating_count=gc,
        base_satisfied_count=base_satisfied_count, holo_satisfied_count=0,
        earned_date=None, stages=[],
    )


# ------------------------------------------------------------------ pure compute -------------------------

def test_stage_xp_plus_completion_bonus():
    total, per = compute_badge_xp({'gow': [_res(3, True)]})
    assert per['gow'] == 3 * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS
    assert total == per['gow']


def test_partial_progress_no_bonus():
    total, per = compute_badge_xp({'gow': [_res(2, False, gating_count=5)]})
    assert per['gow'] == 2 * XP_PER_STAGE      # cleared 2 of 5 -> drip only, no completion bonus
    assert total == 2 * XP_PER_STAGE


def test_two_group_badges_sum_into_series():
    # Legacy HD (2 stages, earned) + Ultra HD (3 stages, earned) in ONE series -> summed.
    total, per = compute_badge_xp({'gow': [_res(2, True), _res(3, True)]})
    expected = (2 * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS) + (3 * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS)
    assert per['gow'] == expected and total == expected


def test_multiple_series_breakdown():
    total, per = compute_badge_xp({'a': [_res(1, True)], 'b': [_res(2, False, 4)]})
    assert per['a'] == XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS
    assert per['b'] == 2 * XP_PER_STAGE
    assert total == per['a'] + per['b']


def test_holo_does_not_change_xp():
    plain = compute_badge_xp({'gow': [_res(3, True)]})[0]
    holo_res = GroupBadgeResult(True, True, 3, 3, 3, None, [])   # holo flags set; must not add XP
    assert compute_badge_xp({'gow': [holo_res]})[0] == plain


def test_empty():
    assert compute_badge_xp({}) == (0, {})


# ------------------------------------------------------------------ store / wiring (DB) ------------------

def _make_series(slug, n_stages):
    from tests.factories import (
        BadgeSeriesFactory, StageFactory, ConceptFactory, GameFactory,
        PlatformGroupFactory, GroupBadgeFactory,
    )
    series = BadgeSeriesFactory(series_slug=slug)
    pg = PlatformGroupFactory(key='ultra-hd', name='Ultra', platforms=['PS4', 'PS5'], exclude_delisted=True)
    gb = GroupBadgeFactory(series=series, platform_group=pg, is_live=True)
    games = []
    for i in range(1, n_stages + 1):
        st = StageFactory(series_slug=slug, stage_number=i)
        c = ConceptFactory()
        st.concepts.add(c)
        games.append(GameFactory(concept=c, title_platform=['PS5']))
    return gb, games


def _complete(profile, game):
    from trophies.models import ProfileGame, TrophyGroup, ProfileTrophyGroup
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={'progress': 50})
    tg, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id='default', defaults={'trophy_group_name': 'B'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg, defaults={'progress': 100, 'last_trophy_at': timezone.now()},
    )


@pytest.mark.django_db
def test_standing_partial_progress_stage_xp_only():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding
    from tests.factories import ProfileFactory
    gb, games = _make_series('gow', 3)
    p = ProfileFactory()
    _complete(p, games[0])                       # 1 of 3 gating stages -> not earned
    evaluate_and_apply(p, [gb])
    s = ProfileBadgeStanding.objects.get(profile=p)
    assert s.series_xp == {'gow': XP_PER_STAGE} and s.total_xp == XP_PER_STAGE


@pytest.mark.django_db
def test_standing_earned_gets_bonus():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding
    from tests.factories import ProfileFactory
    gb, games = _make_series('gow', 3)
    p = ProfileFactory()
    for g in games:
        _complete(p, g)                          # all 3 -> earned
    evaluate_and_apply(p, [gb])
    s = ProfileBadgeStanding.objects.get(profile=p)
    assert s.total_xp == 3 * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS


@pytest.mark.django_db
def test_zero_xp_profile_gets_no_standing_row():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding
    from tests.factories import ProfileFactory
    gb, _games = _make_series('gow', 3)
    p = ProfileFactory()                         # no progress at all
    evaluate_and_apply(p, [gb])
    assert not ProfileBadgeStanding.objects.filter(profile=p).exists()


@pytest.mark.django_db
def test_scoped_series_merge_preserves_other_series():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding
    from tests.factories import ProfileFactory
    gbA, gamesA = _make_series('aaa', 2)
    gbB, gamesB = _make_series('bbb', 1)
    p = ProfileFactory()
    for g in gamesA:
        _complete(p, g)
    _complete(p, gamesB[0])
    evaluate_and_apply(p, [gbA])                 # scoped to A
    evaluate_and_apply(p, [gbB])                 # scoped to B -- must NOT wipe A's XP
    s = ProfileBadgeStanding.objects.get(profile=p)
    assert set(s.series_xp) == {'aaa', 'bbb'} and s.total_xp == sum(s.series_xp.values())


@pytest.mark.django_db
def test_standing_series_dropped_when_progress_regresses_to_zero():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding, ProfileTrophyGroup, ProfileGame
    from tests.factories import ProfileFactory
    gb, games = _make_series('gow', 1)
    p = ProfileFactory()
    _complete(p, games[0])
    evaluate_and_apply(p, [gb])
    assert ProfileBadgeStanding.objects.get(profile=p).total_xp > 0
    # Data regresses (e.g. PSN correction): no longer complete -> the series entry is removed, total zeroes.
    ProfileTrophyGroup.objects.filter(profile=p).update(progress=0)
    ProfileGame.objects.filter(profile=p).update(progress=0)
    evaluate_and_apply(p, [gb])
    s = ProfileBadgeStanding.objects.get(profile=p)
    assert 'gow' not in s.series_xp and s.total_xp == 0
