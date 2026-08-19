"""Badge leaderboard read service (trophies/services/badge_leaderboards). DB reads over the standing stores."""
import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from trophies.models import Job, ProfileGame, TrophyGroup, ProfileTrophyGroup
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
    # its only reader. The per-series rank the badge page shows now comes from `series_board_rank`
    # (progress-ordered, matching the board) -- `series_rank` was an XP-ordered second opinion over the
    # same population and was deleted rather than reconciled.
    assert lb.series_board_rank('gow', top.id) == 1 and lb.series_board_rank('gow', partial.id) == 2


def test_series_board_orders_by_points():
    """End to end through the real engine: the board ranks on BADGE POINTS for the series.

    It ordered on `progress_bp` -- the furthest-along EDITION's fraction -- which meant the default "all
    editions" view ranked people by their best single edition and ignored the rest. `xp` is already summed
    across editions by `compute_series_standings`, so the board answers the question its label asks.

    The points VALUES are read from the rows rather than hardcoded: they come from the badge economy, and
    a literal here would pin this test to an economy tuning rather than to the ordering rule it is about.
    """
    gb, games = _series('gow', 4)
    ahead = ProfileFactory()
    for g in games[:3]:
        _complete(ahead, g)                        # 3 of 4 stages
    evaluate_and_apply(ahead, [gb])
    behind = ProfileFactory()
    _complete(behind, games[0])                    # 1 of 4 stages
    evaluate_and_apply(behind, [gb])

    rows = lb.series_board_rows('gow')

    assert [r[0] for r in rows] == [ahead.id, behind.id], 'the board is not ordered by points'
    assert rows[0][1] > rows[1][1] > 0, 'three stages did not pay more than one'
    assert all(r[2] is not None for r in rows), 'advanced_at did not reach the board rows'
    # The row is (profile_id, xp, advanced_at) -- no stage tally. Points already count what was cleared
    # AND weigh what it was worth, and the tally that used to sit beside them was the furthest-along
    # EDITION's, which made it wrong on a board that sums editions.
    assert len(rows[0]) == 3, 'the board row grew a column back'


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
    ProfileBadgeStanding.objects.create(profile=p, country_code=country, **kw, is_linked=True)
    return p


def test_the_trophies_board_ranks_platinums_first_then_total():
    """It reads Profile's OWN counters -- not a badge-scoped denorm. The board it replaced counted trophies
    in badge-covered games, which needed a full-library aggregate per profile in the badge write seam."""
    def hunter(plats, total):
        return ProfileFactory(is_linked=True, total_plats=plats, total_trophies=total)

    many_plats = hunter(9, 50)
    many_trophies = hunter(2, 400)
    tie_loser = hunter(9, 20)

    assert [r[0] for r in lb.trophy_rows()] == [many_plats.id, tie_loser.id, many_trophies.id]
    assert lb.trophy_rank(many_plats.id) == 1
    assert lb.trophy_rank(tie_loser.id) == 2, 'the tiebreak is missing from the rank'
    assert lb.trophy_rank(many_trophies.id) == 3


def test_an_unlinked_hunter_is_not_on_the_trophies_board():
    """`is_linked` is the public gate every hunter-facing board has used: an unowned or scout profile is
    catalogue data, not a competitor."""
    ProfileFactory(is_linked=False, total_plats=99, total_trophies=999)
    assert lb.trophy_rows() == []


def test_an_unlinked_hunter_is_not_on_ANY_board():
    """The gate `trophy_rows` has always applied, applied everywhere.

    Badge Points did not have it, so it ranked scraped profiles -- including the SCOUT ACCOUNTS the
    catalogue uses to discover games. Those are not hypothetical rows: `evaluate_badges --all` walks every
    profile with a PSN username, not every linked one. Career XP was linked-only by accident (claiming a
    contract requires a login) rather than by rule.

    All three tabs live on ONE page, so the disagreement was visible in a single glance: a hunter present
    on Trophies and absent from Badge Points, or the reverse.
    """
    from trophies.models import (
        ProfileBadgeStanding, ProfileCareerStanding, ProfileEditionStanding, ProfileJobXP,
        SeriesBadgeStanding,
    )

    scout = ProfileFactory(is_linked=False, total_plats=99, total_trophies=999, country_code='MT')
    # `is_linked=False` on the STANDINGS, mirroring the profile. These rows are the fixture's whole point:
    # the boards read the store's own column now (migration 0308), so a scout's standings must carry the
    # scout's flag. Setting them True here would not weaken the test, it would invert it.
    ProfileBadgeStanding.objects.create(profile=scout, total_xp=999_999, badges_held=50,
                                        country_code='MT', is_linked=False)
    # The EDITION store is a separate manager that `badge_store(edition)` swaps to, so the gate has to be
    # proven on it independently -- a fixture with no edition standing leaves that branch unreachable and
    # dropping `_linked` from it stays green.
    ProfileEditionStanding.objects.create(profile=scout, platform_group_key='ultra-hd',
                                          total_xp=999_999, badges_held=50, country_code='MT',
                                          is_linked=False)
    ProfileCareerStanding.objects.create(profile=scout, total_xp=999_999, pursuer_level=99,
                                         is_linked=False)
    SeriesBadgeStanding.objects.create(profile=scout, series_slug='aaa', xp=9999, progress_bp=10000,
                                       stages_cleared=2, stages_total=2, is_linked=False)
    job = Job.objects.create(slug='ranger', name='Ranger', discipline='combat')
    ProfileJobXP.objects.create(profile=scout, job=job, total_xp=999_999, level=99, is_linked=False)

    assert lb.xp_rows() == [], 'an unlinked profile is on Badge Points'
    assert lb.xp_rank(scout.id) is None
    assert lb.career_xp_rows() == [], 'an unlinked profile is on Career XP'
    assert lb.career_xp_rank(scout.id) is None
    assert lb.series_board_rows('aaa') == [], 'an unlinked profile is on a series board'
    assert lb.series_board_rank('aaa', scout.id) is None
    assert lb.job_rows('ranger') == [], 'an unlinked profile is on a job board'
    assert lb.job_rank('ranger', scout.id) is None
    assert lb.xp_rows(edition='ultra-hd') == [], 'an unlinked profile is on an EDITION board'
    assert lb.xp_rank(scout.id, edition='ultra-hd') is None

    # And the counts the pages print agree with the rows they print.
    assert lb.board_count('points') == 0
    assert lb.board_count('career') == 0
    assert lb.board_count('trophies') == 0
    assert lb.series_board_count('aaa') == 0
    assert lb.job_board_counts(['ranger']) == {}
    assert lb.board_count('points', edition='ultra-hd') == 0

    # ...and the country PICKER, which is the one that fails quietly: an offered country whose only
    # standings belong to scraped profiles renders an empty board on all three tabs, cached for an hour.
    assert 'MT' not in lb.active_countries(), 'the picker offers a country with no rankable hunters'


def test_an_unlinked_hunter_is_not_an_earner_either():
    """The medallion back reads `earners_rank`. A scout account holding Earn #1 on a badge is the exact
    case the gate exists for, and it also has to agree with the board: a hunter the earners LIST does not
    seat cannot be given a position in it."""
    gb, games = _series('earn', 1)
    scout = ProfileFactory(is_linked=False)
    real = ProfileFactory()
    for p in (scout, real):
        _complete(p, games[0])
        evaluate_and_apply(p, [gb])

    assert [r[0] for r in lb.earners_rows(gb.id)] == [real.id]
    assert lb.earners_rank(scout.id, gb.id) is None
    assert lb.earners_rank(real.id, gb.id) == 1


def test_a_hunter_with_no_display_name_still_gets_a_name_and_a_link():
    """`display_psn_username` is populated from the PSN API and is nullable; `psn_username` is unique and
    required. Reading only the display column rendered a perfectly identifiable hunter as an unnamed row
    -- and an unlinked one, since the row template gates the profile link on this being truthy.

    `display_psn_username or psn_username` is the site's established fallback (api/platinum_grid_views.py,
    api/recap_views.py, api/roadmap_note_views.py); the boards were the one place that skipped it.
    """
    named = ProfileFactory(psn_username='canonical', display_psn_username='CanoniCal')
    nameless = ProfileFactory(psn_username='nodisplay', display_psn_username=None)
    blank = ProfileFactory(psn_username='blankname', display_psn_username='')

    hydrated = lb.hydrate([named.id, nameless.id, blank.id])
    assert lb.entry(hydrated, named.id, 1)['psn_username'] == 'CanoniCal'
    assert lb.entry(hydrated, nameless.id, 2)['psn_username'] == 'nodisplay'
    assert lb.entry(hydrated, blank.id, 3)['psn_username'] == 'blankname'
    # A profile that is not in the page at all still degrades to a blank row rather than NoReverseMatch.
    assert lb.entry(hydrated, -1, 4)['psn_username'] == ''


def test_every_board_can_be_sliced_by_country_without_a_separate_store():
    """The decision the whole design rests on: country is a FILTER, not a board. Same rows, same indexes,
    one extra WHERE -- versus the Redis design's separate sorted set per country."""
    ca_top = _standing(country='CA', total_xp=500)
    ca_low = _standing(country='CA', total_xp=100)
    gb_only = _standing(country='GB', total_xp=900)

    assert [r[0] for r in lb.xp_rows(country='CA')] == [ca_top.id, ca_low.id]

    # The global board still contains everyone, and the GB hunter tops it.
    assert lb.xp_rows()[0][0] == gb_only.id

    # Rank is relative to the SLICE: top of Canada is 1st there and 2nd globally.
    assert lb.xp_rank(ca_top.id, country='CA') == 1
    assert lb.xp_rank(ca_top.id) == 2


def test_career_xp_board_reads_the_jobs_economy():
    from trophies.models import ProfileCareerStanding
    big, small = ProfileFactory(), ProfileFactory()
    ProfileCareerStanding.objects.create(profile=big, total_xp=5000, pursuer_level=30, is_linked=True)
    ProfileCareerStanding.objects.create(profile=small, total_xp=200, pursuer_level=4, is_linked=True)

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


def test_the_earn_rank_can_be_scoped_to_a_country(client=None):
    """`UserGroupBadge` was the last board store with no `country_code` mirror -- historical rather than
    principled (it is the earn-lifecycle table and predates the standing stores). With it, "4th in your
    country to earn this" is answerable, which reads better on a medallion back than "#847 worldwide".

    Note this buys a STAT, not a surface: `earners_rows` has no production caller.
    """
    from trophies.models import UserGroupBadge

    gb, games = _series('cc', 1)
    first_gb, second_gb, first_us = (
        ProfileFactory(country_code='GB'), ProfileFactory(country_code='GB'),
        ProfileFactory(country_code='US'),
    )
    for i, p in enumerate((first_gb, second_gb, first_us)):
        _complete(p, games[0], when=timezone.make_aware(dt.datetime(2020 + i, 1, 1)))
        evaluate_and_apply(p, [gb])

    # The award stamps the mirror -- the propagation signal only fires on CHANGE and would never reach a
    # badge earned after the hunter's last country move.
    assert UserGroupBadge.objects.get(profile=first_gb, group_badge=gb).country_code == 'GB'

    assert lb.earners_rank(first_gb.id, gb.id) == 1          # global
    assert lb.earners_rank(second_gb.id, gb.id) == 2
    assert lb.earners_rank(first_us.id, gb.id) == 3
    # ...and sliced, the US hunter leads their own country rather than trailing the GB pair.
    assert lb.earners_rank(first_us.id, gb.id, country='US') == 1
    assert lb.earners_rank(second_gb.id, gb.id, country='GB') == 2
    assert lb.earners_rank(first_us.id, gb.id, country='GB') is None, (
        'a hunter was ranked in a country they are not in'
    )
    assert [r[0] for r in lb.earners_rows(gb.id, country='GB')] == [first_gb.id, second_gb.id]


def test_a_country_move_reaches_the_badges_already_earned():
    """The edge the award stamp cannot cover, now that this store is in the propagation list: the mirror
    is derived from `_mirrored_fields`, so adding the column was enough to enrol it."""
    from trophies.models import UserGroupBadge

    gb, games = _series('moved', 1)
    hunter = ProfileFactory(country_code='GB')
    _complete(hunter, games[0])
    evaluate_and_apply(hunter, [gb])

    hunter.country_code = 'JP'
    hunter.save(update_fields=['country_code'])

    assert UserGroupBadge.objects.get(profile=hunter, group_badge=gb).country_code == 'JP'
    assert lb.earners_rank(hunter.id, gb.id, country='JP') == 1
    assert lb.earners_rank(hunter.id, gb.id, country='GB') is None
