"""Badge leaderboard read service (trophies/services/badge_leaderboards). DB reads over the standing stores."""
import datetime as dt

import pytest
from django.utils import timezone

from trophies.models import ProfileGame, TrophyGroup, ProfileTrophyGroup
from trophies.services.badge_apply import evaluate_and_apply
from trophies.services import badge_leaderboards as lb
from tests.factories import (
    ProfileFactory, ConceptFactory, GameFactory, StageFactory,
    PlatformGroupFactory, BadgeSeriesFactory, GroupBadgeFactory,
)

pytestmark = pytest.mark.django_db


def _series(slug, n_stages):
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


def _complete(profile, game, when=None):
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={'progress': 50})
    tg, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id='default', defaults={'trophy_group_name': 'B'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg, defaults={'progress': 100, 'last_trophy_at': when or timezone.now()},
    )


def test_global_xp_board_ranks_and_rows():
    gbA, gamesA = _series('aaa', 2)   # earning both stages = 2*100 + 500 = 700 XP
    gbB, gamesB = _series('bbb', 1)   # earning = 100 + 500 = 600 XP
    whale = ProfileFactory()
    for g in gamesA:
        _complete(whale, g)
    _complete(whale, gamesB[0])
    evaluate_and_apply(whale, [gbA])
    evaluate_and_apply(whale, [gbB])              # whale total = 1300
    minnow = ProfileFactory()
    _complete(minnow, gamesB[0])
    evaluate_and_apply(minnow, [gbB])             # minnow total = 600

    rows = lb.xp_rows()
    assert rows[0][0] == whale.id and rows[1][0] == minnow.id
    assert lb.xp_rank(whale.id) == 1 and lb.xp_rank(minnow.id) == 2
    assert lb.xp_rank(ProfileFactory().id) is None   # no standing


def test_series_xp_board_is_scoped_to_the_series():
    gb, games = _series('gow', 2)
    top = ProfileFactory()
    for g in games:
        _complete(top, g)                          # earned: 700
    evaluate_and_apply(top, [gb])
    partial = ProfileFactory()
    _complete(partial, games[0])                   # 1 stage: 100
    evaluate_and_apply(partial, [gb])

    rows = lb.series_xp_rows('gow')
    assert [r[0] for r in rows] == [top.id, partial.id]
    assert lb.series_rank('gow', top.id) == 1 and lb.series_rank('gow', partial.id) == 2


def test_progress_board_orders_by_furthest_along():
    gb, games = _series('gow', 4)
    ahead = ProfileFactory()
    for g in games[:3]:
        _complete(ahead, g)                        # 3 of 4 = 7500 bp
    evaluate_and_apply(ahead, [gb])
    behind = ProfileFactory()
    _complete(behind, games[0])                    # 1 of 4 = 2500 bp
    evaluate_and_apply(behind, [gb])

    rows = lb.series_progress_rows('gow')
    assert rows[0] == (ahead.id, 7500, 3, 4)
    assert rows[1] == (behind.id, 2500, 1, 4)


def test_earners_rank_reflects_completion_order_and_is_live():
    gb, games = _series('gow', 1)
    early, late = ProfileFactory(), ProfileFactory()
    _complete(early, games[0], when=timezone.make_aware(dt.datetime(2020, 1, 1)))
    evaluate_and_apply(early, [gb])
    _complete(late, games[0], when=timezone.make_aware(dt.datetime(2023, 1, 1)))
    evaluate_and_apply(late, [gb])

    assert lb.earners_rank(early.id, gb.id) == 1      # finished first
    assert lb.earners_rank(late.id, gb.id) == 2
    assert lb.earners_rank(ProfileFactory().id, gb.id) is None   # doesn't hold it
    assert [r[0] for r in lb.earners_rows(gb.id)] == [early.id, late.id]


def test_earners_ranks_batched():
    gb1, g1 = _series('one', 1)
    gb2, g2 = _series('two', 1)
    p = ProfileFactory()
    _complete(p, g1[0])
    _complete(p, g2[0])
    evaluate_and_apply(p, [gb1, gb2])
    ranks = lb.earners_ranks(p.id, [gb1.id, gb2.id])
    assert ranks == {gb1.id: 1, gb2.id: 1}
