"""Integration tests for the badge apply layer (trophies/services/badge_apply.evaluate_and_apply).

Hit the DB and assert the full binary lifecycle: award (writes UserGroupBadge + earned_count denorm + series
title), revoke (row deleted when the bar is no longer met, denorm --, title revoked when orphaned), update
(holo flip / earned_at resync), the one-title-per-series rule, and idempotency. There is no earn_rank or
maintenance -- the earners rank is derived live from earned_at (see the leaderboard layer).
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


def test_award_writes_denorm_and_title():
    series, game, title = _one_stage_series('gow')
    gb = GroupBadgeFactory(series=series, platform_group=_ultra())
    profile = ProfileFactory()
    _complete(profile, game)

    res = evaluate_and_apply(profile, [gb])
    assert res['awarded'] == [gb.id]
    ugb = UserGroupBadge.objects.get(profile=profile, group_badge=gb)
    assert ugb.is_holo is False
    gb.refresh_from_db()
    assert gb.earned_count == 1
    assert UserTitle.objects.filter(profile=profile, title=title, source_type='badge_series').exists()


def test_award_adopts_a_title_row_another_system_already_wrote():
    """UserTitle is unique on (profile, title) WITHOUT source_type, so when a series reuses a legacy
    Badge's Title, get_or_create returns the existing row. Left as-is, the grant records NOTHING: the
    hunter holds the title but no count filtered to 'badge_series' can see them, and the orphan revoke
    can never clean it up. That shipped -- titles showed "Be the first" to the hunter wearing them."""
    series, game, title = _one_stage_series('adopt')
    gb = GroupBadgeFactory(series=series, platform_group=_ultra())
    profile = ProfileFactory()
    UserTitle.objects.create(profile=profile, title=title, source_type='badge', source_id=1)
    _complete(profile, game)

    evaluate_and_apply(profile, [gb])

    rows = UserTitle.objects.filter(profile=profile, title=title)
    assert rows.count() == 1, 'adopted in place, not duplicated'
    assert rows.first().source_type == 'badge_series'


def test_earned_count_reconciled_on_profile_delete():
    # A Profile deletion cascade-drops the UserGroupBadge hold WITHOUT going through apply's revoke, so the
    # pre_delete reconcile signal must decrement earned_count (else the denorm inflates and rarity / the
    # earners tally read high forever). Guards the sibling-of-Badge drift the old UserBadge signals avoided.
    series, game, _ = _one_stage_series('del')
    gb = GroupBadgeFactory(series=series, platform_group=_ultra())
    profile = ProfileFactory()
    _complete(profile, game)
    evaluate_and_apply(profile, [gb])
    gb.refresh_from_db()
    assert gb.earned_count == 1

    profile.delete()                                       # cascade drops the hold, bypassing apply
    gb.refresh_from_db()
    assert gb.earned_count == 0                            # reconciled, not left inflated


def test_holo_true_when_fully_complete():
    series, game, _ = _one_stage_series('holo')
    gb = GroupBadgeFactory(series=series, platform_group=_ultra())
    profile = ProfileFactory()
    _complete(profile, game, full=True)
    evaluate_and_apply(profile, [gb])
    assert UserGroupBadge.objects.get(profile=profile, group_badge=gb).is_holo is True


def test_revoke_deletes_row_and_revokes_title_when_bar_no_longer_met():
    series, game, title = _one_stage_series('rev')
    gb = GroupBadgeFactory(series=series, platform_group=_ultra())
    profile = ProfileFactory()
    _complete(profile, game)
    evaluate_and_apply(profile, [gb])                      # earned

    # Series grows: a new gating stage the profile hasn't done -> no longer meets the bar -> revoke.
    s2 = StageFactory(series_slug='rev', stage_number=2)
    c2 = ConceptFactory()
    s2.concepts.add(c2)
    _game(c2)
    res = evaluate_and_apply(profile, [gb])
    assert res['revoked'] == [gb.id]
    assert not UserGroupBadge.objects.filter(profile=profile, group_badge=gb).exists()   # binary: row gone
    gb.refresh_from_db()
    assert gb.earned_count == 0
    assert not UserTitle.objects.filter(profile=profile, title=title, source_type='badge_series').exists()


def test_re_earn_after_revoke_recreates_the_row():
    series, game, title = _one_stage_series('re')
    gb = GroupBadgeFactory(series=series, platform_group=_ultra())
    profile = ProfileFactory()
    _complete(profile, game)
    evaluate_and_apply(profile, [gb])
    s2 = StageFactory(series_slug='re', stage_number=2)
    c2 = ConceptFactory()
    s2.concepts.add(c2)
    g2 = _game(c2)
    evaluate_and_apply(profile, [gb])                      # revoke (stage 2 undone)
    assert not UserGroupBadge.objects.filter(profile=profile, group_badge=gb).exists()
    _complete(profile, g2)                                 # now finish stage 2
    res = evaluate_and_apply(profile, [gb])
    assert res['awarded'] == [gb.id]                       # fresh hold
    assert UserGroupBadge.objects.filter(profile=profile, group_badge=gb).exists()
    gb.refresh_from_db()
    assert gb.earned_count == 1
    assert UserTitle.objects.filter(profile=profile, title=title, source_type='badge_series').exists()


def test_series_title_survives_until_all_group_badges_revoked():
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
    evaluate_and_apply(profile, [gb_l, gb_u])              # both held -> title granted once
    assert UserGroupBadge.objects.filter(profile=profile).count() == 2
    assert UserTitle.objects.filter(profile=profile, title=title).exists()

    # Grow ONLY the Ultra reach (a PS5 stage) -> Ultra revoked, Legacy still held (its stage 2 has no PS3 game).
    s2 = StageFactory(series_slug='dual', stage_number=2)
    c2 = ConceptFactory()
    s2.concepts.add(c2)
    _game(c2, ('PS5',))
    evaluate_and_apply(profile, [gb_l, gb_u])
    assert not UserGroupBadge.objects.filter(profile=profile, group_badge=gb_u).exists()
    assert UserGroupBadge.objects.filter(profile=profile, group_badge=gb_l).exists()
    assert UserTitle.objects.filter(profile=profile, title=title).exists()   # survives: Legacy still held


def test_second_run_is_a_noop():
    series, game, _ = _one_stage_series('idem')
    gb = GroupBadgeFactory(series=series, platform_group=_ultra())
    profile = ProfileFactory()
    _complete(profile, game)
    evaluate_and_apply(profile, [gb])
    res2 = evaluate_and_apply(profile, [gb])
    assert res2 == {'awarded': [], 'revoked': [], 'updated': []}
    gb.refresh_from_db()
    assert gb.earned_count == 1                            # not double-counted


def test_earned_at_uses_completion_date_not_sync_time():
    series, game, _ = _one_stage_series('date')
    gb = GroupBadgeFactory(series=series, platform_group=_ultra())
    profile = ProfileFactory()
    when = timezone.make_aware(dt.datetime(2024, 6, 1, 12, 0))   # completed long before "now"
    _complete(profile, game, when=when)
    evaluate_and_apply(profile, [gb])
    assert UserGroupBadge.objects.get(profile=profile, group_badge=gb).earned_at == when


def test_earners_ordered_by_completion_date():
    # The leaderboard sort key: whoever completed EARLIER has the earlier earned_at (derived rank #1).
    series, game, _ = _one_stage_series('order')
    gb = GroupBadgeFactory(series=series, platform_group=_ultra())
    early, late = ProfileFactory(), ProfileFactory()
    _complete(early, game, when=timezone.make_aware(dt.datetime(2020, 1, 1)))
    evaluate_and_apply(early, [gb])
    _complete(late, game, when=timezone.make_aware(dt.datetime(2023, 1, 1)))
    evaluate_and_apply(late, [gb])
    e = UserGroupBadge.objects.get(profile=early, group_badge=gb)
    l = UserGroupBadge.objects.get(profile=late, group_badge=gb)
    assert e.earned_at < l.earned_at
    gb.refresh_from_db()
    assert gb.earned_count == 2


def test_earned_at_resyncs_when_iteration_changes():
    # A still-held badge whose completion date shifts (series grew, profile had already done the new stage) must
    # resync earned_at so the leaderboard reflects the current iteration.
    series, game, _ = _one_stage_series('resync')
    gb = GroupBadgeFactory(series=series, platform_group=_ultra())
    profile = ProfileFactory()
    _complete(profile, game, when=timezone.make_aware(dt.datetime(2020, 1, 1)))
    evaluate_and_apply(profile, [gb])
    # Grow the series with a stage the profile ALSO already completed, but LATER -> becomes the last required.
    s2 = StageFactory(series_slug='resync', stage_number=2)
    c2 = ConceptFactory()
    s2.concepts.add(c2)
    g2 = _game(c2)
    later = timezone.make_aware(dt.datetime(2022, 6, 1))
    _complete(profile, g2, when=later)
    res = evaluate_and_apply(profile, [gb])
    assert res['updated'] == [gb.id]
    assert UserGroupBadge.objects.get(profile=profile, group_badge=gb).earned_at == later


def test_holo_flips_off_via_update_when_series_grows():
    series, game, _ = _one_stage_series('hoff')
    gb = GroupBadgeFactory(series=series, platform_group=_ultra())
    profile = ProfileFactory()
    _complete(profile, game, full=True)
    evaluate_and_apply(profile, [gb])
    assert UserGroupBadge.objects.get(profile=profile, group_badge=gb).is_holo is True
    gb.refresh_from_db()
    count_before = gb.earned_count
    # New gating stage: base-complete but NOT full -> still held (base), holo drops -> update, denorm untouched.
    s2 = StageFactory(series_slug='hoff', stage_number=2)
    c2 = ConceptFactory()
    s2.concepts.add(c2)
    g2 = _game(c2)
    _complete(profile, g2, full=False)
    res = evaluate_and_apply(profile, [gb])
    assert res['updated'] == [gb.id]
    ugb = UserGroupBadge.objects.get(profile=profile, group_badge=gb)
    assert ugb.is_holo is False
    gb.refresh_from_db()
    assert gb.earned_count == count_before                # update never touches the denorm


def test_out_of_scope_badge_is_never_revoked():
    ultra = _ultra()
    series1, game1, _ = _one_stage_series('scope1')
    gb1 = GroupBadgeFactory(series=series1, platform_group=ultra)
    series2, game2, _ = _one_stage_series('scope2')
    gb2 = GroupBadgeFactory(series=series2, platform_group=ultra)
    profile = ProfileFactory()
    _complete(profile, game1)
    evaluate_and_apply(profile, [gb1])                     # gb1 held
    # Evaluate ONLY gb2 (undone) -> gb1 is out of scope and must stay held.
    evaluate_and_apply(profile, [gb2])
    assert UserGroupBadge.objects.filter(profile=profile, group_badge=gb1).exists()
    assert not UserGroupBadge.objects.filter(profile=profile, group_badge=gb2).exists()
