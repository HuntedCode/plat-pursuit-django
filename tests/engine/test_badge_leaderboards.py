"""Badge leaderboard read service (trophies/services/badge_leaderboards). DB reads over the standing stores."""
import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
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

    # `series_xp_rows` was removed in the 2026-08 audit -- it had no production caller and this test was
    # its only reader. `series_rank` is the live half (badge_detail_service reads it) and stays covered.
    assert lb.series_rank('gow', top.id) == 1 and lb.series_rank('gow', partial.id) == 2


def test_series_board_orders_by_furthest_along():
    """Renamed from the chasers-only `series_progress_rows`: earners and chasers are ONE board now, so the
    row carries `advanced_at` (the tiebreak) alongside the progress figures."""
    gb, games = _series('gow', 4)
    ahead = ProfileFactory()
    for g in games[:3]:
        _complete(ahead, g)                        # 3 of 4 = 7500 bp
    evaluate_and_apply(ahead, [gb])
    behind = ProfileFactory()
    _complete(behind, games[0])                    # 1 of 4 = 2500 bp
    evaluate_and_apply(behind, [gb])

    rows = lb.series_board_rows('gow')
    assert [r[:4] for r in rows] == [(ahead.id, 7500, 3, 4), (behind.id, 2500, 1, 4)]
    assert all(r[4] is not None for r in rows), 'advanced_at did not reach the board rows'


def test_the_series_board_puts_earners_above_chasers_and_breaks_ties_by_date():
    """The merge, end to end. An earner outranks every chaser however far along they are, and two hunters
    on the SAME rung are separated by who got there first -- which is the whole reason a 3-stage badge does
    not collapse into one giant tie."""
    gb, games = _series('tie', 2)

    earner = ProfileFactory()
    for g in games:
        _complete(earner, g, when=timezone.make_aware(dt.datetime(2024, 6, 1)))
    evaluate_and_apply(earner, [gb])

    # Created in REVERSE date order on purpose: profile ids then run opposite to the dates, so the
    # expected order can only come from the tiebreak. Built the other way round, `profile_id` alone
    # produces the same answer and the assertion passes with no tiebreak at all -- which is exactly what
    # mutation testing caught here.
    second_there = ProfileFactory()
    _complete(second_there, games[0], when=timezone.make_aware(dt.datetime(2023, 1, 1)))
    evaluate_and_apply(second_there, [gb])

    first_there = ProfileFactory()
    _complete(first_there, games[0], when=timezone.make_aware(dt.datetime(2021, 1, 1)))
    evaluate_and_apply(first_there, [gb])

    rows = lb.series_board_rows('tie')
    assert [r[0] for r in rows] == [earner.id, first_there.id, second_there.id]
    assert lb.series_board_rank('tie', first_there.id) == 2
    assert lb.series_board_rank('tie', second_there.id) == 3
    assert lb.series_board_rank('tie', ProfileFactory().id) is None


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


def test_earners_rank_is_first_to_complete_order():
    """The singular `earners_rank` is live -- badge_detail_service reads it for the value on the medallion
    back. Its batched sibling `earners_ranks` was removed in the 2026-08 audit: no production caller, and
    it was an N-query loop that would have been the wrong shape for the grid it was written for."""
    gb1, g1 = _series('one', 1)
    gb2, g2 = _series('two', 1)
    first = ProfileFactory()
    _complete(first, g1[0])
    _complete(first, g2[0])
    evaluate_and_apply(first, [gb1, gb2])

    assert lb.earners_rank(first.id, gb1.id) == 1
    assert lb.earners_rank(first.id, gb2.id) == 1
    assert lb.earners_rank(ProfileFactory().id, gb1.id) is None, 'a non-holder has no earners rank'


# ------------------------------------------------------------------ Lane B: the new boards ---------------

def _standing(country='', **kw):
    from trophies.models import ProfileBadgeStanding
    p = ProfileFactory(country_code=country)
    ProfileBadgeStanding.objects.create(profile=p, country_code=country, **kw)
    return p


def test_badge_trophies_board_ranks_platinums_first_then_total():
    many_plats = _standing(trophies_platinum=9, trophies_total=50)
    many_trophies = _standing(trophies_platinum=2, trophies_total=400)
    tie_loser = _standing(trophies_platinum=9, trophies_total=20)

    assert [r[0] for r in lb.badge_trophy_rows()] == [many_plats.id, tie_loser.id, many_trophies.id]


def test_badge_trophy_rank_expresses_the_same_tiebreak_as_the_order_by():
    """A two-key board needs a two-key rank. Counting only `trophies_platinum__gt` would report every
    hunter sharing a platinum count as joint-first -- the board and the rank would disagree, and the rank
    is what a viewer sees next to their own name."""
    top = _standing(trophies_platinum=5, trophies_total=300)
    same_plats_fewer = _standing(trophies_platinum=5, trophies_total=100)
    fewer_plats = _standing(trophies_platinum=1, trophies_total=999)

    assert lb.badge_trophy_rank(top.id) == 1
    assert lb.badge_trophy_rank(same_plats_fewer.id) == 2, 'the tiebreak is missing from the rank'
    assert lb.badge_trophy_rank(fewer_plats.id) == 3
    assert lb.badge_trophy_rank(ProfileFactory().id) is None


def test_every_board_can_be_sliced_by_country_without_a_separate_store():
    """The decision the whole design rests on: country is a FILTER, not a board. Same rows, same indexes,
    one extra WHERE -- versus the Redis design's separate sorted set per country."""
    ca_top = _standing(country='CA', total_xp=500, trophies_platinum=9, trophies_total=90)
    ca_low = _standing(country='CA', total_xp=100, trophies_platinum=1, trophies_total=10)
    gb_only = _standing(country='GB', total_xp=900, trophies_platinum=99, trophies_total=999)

    assert [r[0] for r in lb.xp_rows(country='CA')] == [ca_top.id, ca_low.id]
    assert [r[0] for r in lb.badge_trophy_rows(country='CA')] == [ca_top.id, ca_low.id]

    # The global board still contains everyone, and the GB hunter tops it.
    assert lb.xp_rows()[0][0] == gb_only.id

    # Rank is relative to the SLICE: top of Canada is 1st there and 2nd globally.
    assert lb.xp_rank(ca_top.id, country='CA') == 1
    assert lb.xp_rank(ca_top.id) == 2


def test_career_xp_board_reads_the_jobs_economy():
    from trophies.models import ProfileCareerStanding
    big, small = ProfileFactory(), ProfileFactory()
    ProfileCareerStanding.objects.create(profile=big, total_xp=5000, pursuer_level=30)
    ProfileCareerStanding.objects.create(profile=small, total_xp=200, pursuer_level=4)

    rows = lb.career_xp_rows()
    assert [r[0] for r in rows] == [big.id, small.id]
    assert rows[0][2] == 30, 'pursuer level is not on the row'
    assert lb.career_xp_rank(small.id) == 2
    assert lb.career_xp_rank(ProfileFactory().id) is None


def test_hydrate_is_one_query_regardless_of_page_size():
    """The Redis design denormalized display data into a hash and then had to keep it fresh -- a renamed
    hunter showed a stale name until the next rebuild, and a missing hash entry silently dropped a row
    from a page. Reading it live cannot go stale, but only if it stays ONE query.

    `displayed_title` is the trap: it is a METHOD doing two queries per profile, so a 50-row page would be
    ~100 extra round trips to print a word under each name. It is folded in as a subquery.
    """
    profiles = [ProfileFactory() for _ in range(12)]

    with CaptureQueriesContext(connection) as ctx:
        rows = lb.hydrate([p.id for p in profiles])

    assert len(rows) == 12
    assert len(ctx.captured_queries) == 1, (
        f'hydrate took {len(ctx.captured_queries)} queries for 12 rows -- it must be one'
    )
    assert lb.hydrate([]) == {}, 'the empty page should not touch the database'


def test_hydrate_carries_what_a_board_row_draws():
    p = ProfileFactory(country_code='CA')
    row = lb.hydrate([p.id])[p.id]
    for key in ('display_psn_username', 'avatar_url', 'flag', 'user_is_premium', 'country_code',
                'display_title'):
        assert key in row, f'{key} is missing from the hydrated row'
