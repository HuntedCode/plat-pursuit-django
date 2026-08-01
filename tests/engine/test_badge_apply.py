"""Integration tests for the badge apply layer (trophies/services/badge_apply.evaluate_and_apply).

Hit the DB and assert the full earn lifecycle: award (writes UserGroupBadge + earn_rank + earned_count denorm
+ series title), lapse -> maintenance (no delete, earn_rank kept, denorm --, title revoked when orphaned),
reactivate (earn_rank preserved), holo, the one-title-per-series rule, and idempotency.
"""
import datetime as dt

import pytest
from django.utils import timezone

from trophies.models import (
    UserGroupBadge, UserTitle, ProfileGame, TrophyGroup, ProfileTrophyGroup, Title,
)
from trophies.services.badge_apply import evaluate_and_apply
from tests.factories import (
    ProfileFactory, ConceptFactory, GameFactory, StageFactory,
    PlatformGroupFactory, BadgeSeriesFactory, GroupBadgeFactory,
)

pytestmark = pytest.mark.django_db


def _ultra():
    return PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS4', 'PS5'], exclude_delisted=True)


def _game(concept, platforms=('PS5',)):
    return GameFactory(concept=concept, title_platform=list(platforms))


def _complete(profile, game, full=False, when=None):
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={'progress': 100 if full else 50})
    tg, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id='default', defaults={'trophy_group_name': 'Base'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg, defaults={'progress': 100, 'last_trophy_at': when or timezone.now()},
    )


def _one_stage_series(slug):
    title = Title.objects.create(name=f"Title {slug}")
    series = BadgeSeriesFactory(series_slug=slug, title=title)
    stage = StageFactory(series_slug=slug, stage_number=1)
    concept = ConceptFactory()
    stage.concepts.add(concept)
    return series, _game(concept), title


def test_award_writes_earn_rank_denorm_and_title():
    ultra = _ultra()
    series, game, title = _one_stage_series('gow')
    gb = GroupBadgeFactory(series=series, platform_group=ultra)
    profile = ProfileFactory()
    _complete(profile, game)

    res = evaluate_and_apply(profile, [gb])
    assert res['awarded'] == [gb.id]
    ugb = UserGroupBadge.objects.get(profile=profile, group_badge=gb)
    assert ugb.status == 'earned' and ugb.earn_rank == 1 and ugb.is_holo is False
    gb.refresh_from_db()
    assert gb.earned_count == 1
    assert UserTitle.objects.filter(profile=profile, title=title, source_type='badge_series').exists()


def test_holo_true_when_fully_complete():
    ultra = _ultra()
    series, game, _ = _one_stage_series('holo')
    gb = GroupBadgeFactory(series=series, platform_group=ultra)
    profile = ProfileFactory()
    _complete(profile, game, full=True)
    evaluate_and_apply(profile, [gb])
    assert UserGroupBadge.objects.get(profile=profile, group_badge=gb).is_holo is True


def test_lapse_to_maintenance_revokes_title_keeps_rank():
    ultra = _ultra()
    series, game, title = _one_stage_series('lap')
    gb = GroupBadgeFactory(series=series, platform_group=ultra)
    profile = ProfileFactory()
    _complete(profile, game)
    evaluate_and_apply(profile, [gb])                      # earned

    # Series grows: a new gating stage the profile hasn't done -> lapse.
    s2 = StageFactory(series_slug='lap', stage_number=2)
    c2 = ConceptFactory()
    s2.concepts.add(c2)
    _game(c2)
    res = evaluate_and_apply(profile, [gb])
    assert res['lapsed'] == [gb.id]
    ugb = UserGroupBadge.objects.get(profile=profile, group_badge=gb)
    assert ugb.status == 'maintenance' and ugb.earn_rank == 1 and ugb.is_holo is False   # rank permanent
    gb.refresh_from_db()
    assert gb.earned_count == 0
    assert not UserTitle.objects.filter(profile=profile, title=title, source_type='badge_series').exists()


def test_reactivate_from_maintenance_keeps_rank():
    ultra = _ultra()
    series, game, title = _one_stage_series('re')
    gb = GroupBadgeFactory(series=series, platform_group=ultra)
    profile = ProfileFactory()
    _complete(profile, game)
    evaluate_and_apply(profile, [gb])
    s2 = StageFactory(series_slug='re', stage_number=2)
    c2 = ConceptFactory()
    s2.concepts.add(c2)
    g2 = _game(c2)
    evaluate_and_apply(profile, [gb])                      # lapse
    _complete(profile, g2)                                 # now finish stage 2
    res = evaluate_and_apply(profile, [gb])
    assert res['reactivated'] == [gb.id]
    ugb = UserGroupBadge.objects.get(profile=profile, group_badge=gb)
    assert ugb.status == 'earned' and ugb.earn_rank == 1   # same permanent rank
    gb.refresh_from_db()
    assert gb.earned_count == 1
    assert UserTitle.objects.filter(profile=profile, title=title, source_type='badge_series').exists()


def test_series_title_survives_until_all_group_badges_lapse():
    legacy = PlatformGroupFactory(key='legacy-hd', name='Legacy HD', platforms=['PS3', 'PSVITA'], exclude_delisted=False)
    ultra = _ultra()
    title = Title.objects.create(name='Slayer')
    series = BadgeSeriesFactory(series_slug='dual', title=title)
    stage = StageFactory(series_slug='dual', stage_number=1)
    concept = ConceptFactory()
    stage.concepts.add(concept)
    ps3 = _game(concept, ('PS3',))
    ps5 = _game(concept, ('PS5',))
    gb_l = GroupBadgeFactory(series=series, platform_group=legacy)
    gb_u = GroupBadgeFactory(series=series, platform_group=ultra)
    profile = ProfileFactory()
    _complete(profile, ps3)
    _complete(profile, ps5)
    evaluate_and_apply(profile, [gb_l, gb_u])              # both earned -> title granted once
    assert UserGroupBadge.objects.filter(profile=profile, status='earned').count() == 2
    assert UserTitle.objects.filter(profile=profile, title=title).exists()

    # Grow ONLY the Ultra reach (a PS5 stage) -> Ultra lapses, Legacy still holds (its stage 2 has no PS3 game).
    s2 = StageFactory(series_slug='dual', stage_number=2)
    c2 = ConceptFactory()
    s2.concepts.add(c2)
    _game(c2, ('PS5',))
    evaluate_and_apply(profile, [gb_l, gb_u])
    assert UserGroupBadge.objects.get(profile=profile, group_badge=gb_u).status == 'maintenance'
    assert UserGroupBadge.objects.get(profile=profile, group_badge=gb_l).status == 'earned'
    assert UserTitle.objects.filter(profile=profile, title=title).exists()   # survives: Legacy still earned


def test_second_run_is_a_noop():
    ultra = _ultra()
    series, game, _ = _one_stage_series('idem')
    gb = GroupBadgeFactory(series=series, platform_group=ultra)
    profile = ProfileFactory()
    _complete(profile, game)
    evaluate_and_apply(profile, [gb])
    res2 = evaluate_and_apply(profile, [gb])
    assert res2 == {'awarded': [], 'reactivated': [], 'lapsed': [], 'holo_changed': []}
    gb.refresh_from_db()
    assert gb.earned_count == 1                            # not double-counted


def test_earned_at_uses_completion_date_not_sync_time():
    ultra = _ultra()
    series, game, _ = _one_stage_series('date')
    gb = GroupBadgeFactory(series=series, platform_group=ultra)
    profile = ProfileFactory()
    when = timezone.make_aware(dt.datetime(2024, 6, 1, 12, 0))   # completed long before "now"
    _complete(profile, game, when=when)
    evaluate_and_apply(profile, [gb])
    assert UserGroupBadge.objects.get(profile=profile, group_badge=gb).earned_at == when


def test_second_earner_gets_rank_two():
    ultra = _ultra()
    series, game, _ = _one_stage_series('rank')
    gb = GroupBadgeFactory(series=series, platform_group=ultra)
    p1, p2 = ProfileFactory(), ProfileFactory()
    _complete(p1, game)
    evaluate_and_apply(p1, [gb])
    _complete(p2, game)
    evaluate_and_apply(p2, [gb])
    assert UserGroupBadge.objects.get(profile=p1, group_badge=gb).earn_rank == 1
    assert UserGroupBadge.objects.get(profile=p2, group_badge=gb).earn_rank == 2
    gb.refresh_from_db()
    assert gb.earned_count == 2


def test_reactivate_preserves_non_one_rank():
    ultra = _ultra()
    series, game, _ = _one_stage_series('r2')
    gb = GroupBadgeFactory(series=series, platform_group=ultra)
    p1, p2 = ProfileFactory(), ProfileFactory()
    _complete(p1, game)
    evaluate_and_apply(p1, [gb])                            # rank 1
    _complete(p2, game)
    evaluate_and_apply(p2, [gb])                            # rank 2
    # Grow the series so p2 lapses, then let p2 re-qualify -> rank must stay 2, not re-COUNT.
    s2 = StageFactory(series_slug='r2', stage_number=2)
    c2 = ConceptFactory()
    s2.concepts.add(c2)
    g2 = _game(c2)
    evaluate_and_apply(p2, [gb])                            # p2 lapses
    assert UserGroupBadge.objects.get(profile=p2, group_badge=gb).status == 'maintenance'
    _complete(p2, g2)
    evaluate_and_apply(p2, [gb])                            # reactivate
    ugb = UserGroupBadge.objects.get(profile=p2, group_badge=gb)
    assert ugb.status == 'earned' and ugb.earn_rank == 2


def test_holo_flips_off_when_series_grows():
    ultra = _ultra()
    series, game, _ = _one_stage_series('hoff')
    gb = GroupBadgeFactory(series=series, platform_group=ultra)
    profile = ProfileFactory()
    _complete(profile, game, full=True)
    evaluate_and_apply(profile, [gb])
    assert UserGroupBadge.objects.get(profile=profile, group_badge=gb).is_holo is True
    gb.refresh_from_db()
    count_before = gb.earned_count
    # New gating stage: base-complete but NOT full -> holo drops, base stays, denorm untouched.
    s2 = StageFactory(series_slug='hoff', stage_number=2)
    c2 = ConceptFactory()
    s2.concepts.add(c2)
    g2 = _game(c2)
    _complete(profile, g2, full=False)
    res = evaluate_and_apply(profile, [gb])
    assert res['holo_changed'] == [gb.id]
    ugb = UserGroupBadge.objects.get(profile=profile, group_badge=gb)
    assert ugb.status == 'earned' and ugb.is_holo is False
    gb.refresh_from_db()
    assert gb.earned_count == count_before                 # holo change never touches the denorm


def test_out_of_scope_badge_is_never_lapsed():
    ultra = _ultra()
    series1, game1, _ = _one_stage_series('scope1')
    gb1 = GroupBadgeFactory(series=series1, platform_group=ultra)
    series2, game2, _ = _one_stage_series('scope2')
    gb2 = GroupBadgeFactory(series=series2, platform_group=ultra)
    profile = ProfileFactory()
    _complete(profile, game1)
    evaluate_and_apply(profile, [gb1])                     # gb1 earned
    # Evaluate ONLY gb2 (undone) -> gb1 is out of scope and must stay earned.
    evaluate_and_apply(profile, [gb2])
    assert UserGroupBadge.objects.get(profile=profile, group_badge=gb1).status == 'earned'
    assert not UserGroupBadge.objects.filter(profile=profile, group_badge=gb2).exists()
