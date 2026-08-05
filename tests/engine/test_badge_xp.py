"""Badge XP + progress: pure compute + the recompute_standing write seam (ProfileBadgeStanding + SeriesBadgeStanding)."""
import pytest
from django.utils import timezone

from trophies.services.badge_engine import GroupBadgeResult
from trophies.services.badge_xp import (
    compute_badge_xp, compute_series_standings, edition_display_state, XP_PER_STAGE, XP_BADGE_COMPLETION_BONUS,
)


def _res(base_satisfied_count, base_earned, gating_count=None):
    gc = base_satisfied_count if gating_count is None else gating_count
    return GroupBadgeResult(
        base_earned=base_earned, holo=False, gating_count=gc,
        base_satisfied_count=base_satisfied_count, holo_satisfied_count=0,
        earned_date=None, stages=[],
    )


# ------------------------------------------------------------------ pure XP -------------------------------

def test_stage_xp_plus_completion_bonus():
    total, per = compute_badge_xp({'gow': [_res(3, True)]})
    assert per['gow'] == 3 * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS
    assert total == per['gow']


def test_partial_progress_no_bonus():
    total, per = compute_badge_xp({'gow': [_res(2, False, gating_count=5)]})
    assert per['gow'] == 2 * XP_PER_STAGE      # cleared 2 of 5 -> drip only, no completion bonus
    assert total == 2 * XP_PER_STAGE


def test_two_group_badges_sum_into_series():
    total, per = compute_badge_xp({'gow': [_res(2, True), _res(3, True)]})
    expected = (2 * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS) + (3 * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS)
    assert per['gow'] == expected and total == expected


def test_edition_display_state():
    """The shared per-edition derivation both the Collection wall (read-model) and the badge-detail live view
    run, so they can't disagree. Held wins regardless of counts; cleared>0 -> in_progress at cleared/gating;
    else unearned; gating==0 is div-guarded."""
    assert edition_display_state(True, 0, 0) == ('earned', 100)         # held -> earned/100 regardless of counts
    assert edition_display_state(True, 3, 5) == ('earned', 100)
    assert edition_display_state(False, 3, 5) == ('in_progress', 60)
    assert edition_display_state(False, 1, 3) == ('in_progress', 33)
    assert edition_display_state(False, 0, 5) == ('unearned', 0)        # no gating stage cleared -> waiting mount
    assert edition_display_state(False, 5, 5) == ('in_progress', 100)   # fully cleared but not held (transient)
    assert edition_display_state(False, 2, 0) == ('in_progress', 0)     # gating==0 guard -> no ZeroDivisionError


def test_holo_does_not_change_xp():
    plain = compute_badge_xp({'gow': [_res(3, True)]})[0]
    holo_res = GroupBadgeResult(True, True, 3, 3, 3, None, [])
    assert compute_badge_xp({'gow': [holo_res]})[0] == plain


def test_empty():
    assert compute_badge_xp({}) == (0, {})


def test_million_club_calibration():
    # The "1,000,000 Club" target. Over the projected mature catalog (~400 group badges, ~5 gating stages each),
    # a completionist should land ~1.24M so 1M is reachable-but-hard (~80% of the catalog), with headroom above
    # for two-version + holo elites. Pins the XP constants against silent drift -- if you retune them, retune
    # this target too (and confirm the catalog assumption still holds).
    PROJECTED_BADGES, AVG_STAGES = 400, 5
    per_badge = AVG_STAGES * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS
    assert per_badge == 3100

    completionist = {f's{i}': [_res(AVG_STAGES, True)] for i in range(PROJECTED_BADGES)}
    total, _ = compute_badge_xp(completionist)
    assert total == 1_240_000                       # completionist max, headroom above 1M
    assert total > 1_000_000

    badges_for_million = 1_000_000 / per_badge
    assert 300 <= badges_for_million <= 340         # ~323 badges -> ~81% of the catalog: hard but doable


# ------------------------------------------------------------------ pure progress ------------------------

def test_progress_is_best_group_fraction():
    # one group 2/5 (40%), another 1/2 (50%) -> best = 50%, and its raw N/M is reported.
    st = compute_series_standings({'s': [_res(2, False, 5), _res(1, False, 2)]})['s']
    assert st.progress_bp == 5000 and st.stages_cleared == 1 and st.stages_total == 2


def test_progress_is_100_when_a_group_is_earned():
    st = compute_series_standings({'s': [_res(3, True)]})['s']
    assert st.progress_bp == 10000 and st.stages_cleared == 3 and st.stages_total == 3


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
def test_standing_partial_progress_writes_xp_and_progress():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding, SeriesBadgeStanding
    from tests.factories import ProfileFactory
    gb, games = _make_series('gow', 3)
    p = ProfileFactory()
    _complete(p, games[0])                       # 1 of 3 gating stages -> not earned
    evaluate_and_apply(p, [gb])
    sbs = SeriesBadgeStanding.objects.get(profile=p, series_slug='gow')
    assert sbs.xp == XP_PER_STAGE
    assert sbs.stages_cleared == 1 and sbs.stages_total == 3 and sbs.progress_bp == 3333
    assert ProfileBadgeStanding.objects.get(profile=p).total_xp == XP_PER_STAGE


@pytest.mark.django_db
def test_standing_materializes_per_edition_group_progress():
    """recompute_standing writes the per-edition read-model the Collection reads: group_progress maps each
    edition's platform_group key -> [cleared, gating], and ONLY editions with real progress appear (an edition
    the viewer has 0% on is absent, so the wall reads it as unearned instead of the series furthest-along)."""
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import SeriesBadgeStanding
    from tests.factories import (
        ProfileFactory, BadgeSeriesFactory, StageFactory, ConceptFactory, GameFactory,
        PlatformGroupFactory, GroupBadgeFactory,
    )
    series = BadgeSeriesFactory(series_slug='gow')
    ultra = GroupBadgeFactory(series=series, is_live=True,
                              platform_group=PlatformGroupFactory(key='ultra-hd', name='Ultra', platforms=['PS4', 'PS5']))
    legacy = GroupBadgeFactory(series=series, is_live=True,
                               platform_group=PlatformGroupFactory(key='legacy-hd', name='Legacy', platforms=['PS3']))
    games = {}   # each stage has a PS5 game (gates Ultra HD) + a PS3 game (gates Legacy HD)
    for i in (1, 2):
        st = StageFactory(series_slug='gow', stage_number=i)
        c = ConceptFactory(); st.concepts.add(c)
        games[('ps5', i)] = GameFactory(concept=c, title_platform=['PS5'])
        games[('ps3', i)] = GameFactory(concept=c, title_platform=['PS3'])
    p = ProfileFactory()
    _complete(p, games[('ps5', 1)])              # Ultra HD 1/2 ; Legacy HD 0/2 (no PS3 game completed)
    evaluate_and_apply(p, [ultra, legacy])

    sbs = SeriesBadgeStanding.objects.get(profile=p, series_slug='gow')
    assert sbs.group_progress == {'ultra-hd': [1, 2]}   # Ultra materialized; Legacy (0 progress) absent


@pytest.mark.django_db
def test_standing_earned_gets_bonus_and_full_progress():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding, SeriesBadgeStanding
    from tests.factories import ProfileFactory
    gb, games = _make_series('gow', 3)
    p = ProfileFactory()
    for g in games:
        _complete(p, g)                          # all 3 -> earned
    evaluate_and_apply(p, [gb])
    assert ProfileBadgeStanding.objects.get(profile=p).total_xp == 3 * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS
    assert SeriesBadgeStanding.objects.get(profile=p, series_slug='gow').progress_bp == 10000


@pytest.mark.django_db
def test_zero_xp_profile_gets_no_rows():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding, SeriesBadgeStanding
    from tests.factories import ProfileFactory
    gb, _games = _make_series('gow', 3)
    p = ProfileFactory()                         # no progress at all
    evaluate_and_apply(p, [gb])
    assert not ProfileBadgeStanding.objects.filter(profile=p).exists()
    assert not SeriesBadgeStanding.objects.filter(profile=p).exists()


@pytest.mark.django_db
def test_scoped_series_merge_preserves_other_series_and_total():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding, SeriesBadgeStanding
    from tests.factories import ProfileFactory
    gbA, gamesA = _make_series('aaa', 2)
    gbB, gamesB = _make_series('bbb', 1)
    p = ProfileFactory()
    for g in gamesA:
        _complete(p, g)
    _complete(p, gamesB[0])
    evaluate_and_apply(p, [gbA])                 # scoped to A
    evaluate_and_apply(p, [gbB])                 # scoped to B -- must NOT wipe A's row
    slugs = set(SeriesBadgeStanding.objects.filter(profile=p).values_list('series_slug', flat=True))
    assert slugs == {'aaa', 'bbb'}
    total = ProfileBadgeStanding.objects.get(profile=p).total_xp
    assert total == sum(SeriesBadgeStanding.objects.filter(profile=p).values_list('xp', flat=True))


@pytest.mark.django_db
def test_standing_removed_when_progress_regresses_to_zero():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding, SeriesBadgeStanding, ProfileTrophyGroup, ProfileGame
    from tests.factories import ProfileFactory
    gb, games = _make_series('gow', 1)
    p = ProfileFactory()
    _complete(p, games[0])
    evaluate_and_apply(p, [gb])
    assert ProfileBadgeStanding.objects.get(profile=p).total_xp > 0
    # Data regresses (e.g. PSN correction): no longer complete -> the series row + grand total are removed.
    ProfileTrophyGroup.objects.filter(profile=p).update(progress=0)
    ProfileGame.objects.filter(profile=p).update(progress=0)
    evaluate_and_apply(p, [gb])
    assert not SeriesBadgeStanding.objects.filter(profile=p, series_slug='gow').exists()
    assert not ProfileBadgeStanding.objects.filter(profile=p).exists()
