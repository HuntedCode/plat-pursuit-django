"""PP Score -- the scoring rule (leaderboards, 2026-09).

`points = 100 / earn_rate`, summed over a hunter's rarest 1,000 BASE-GAME trophies. Pure rarity: no
trophy-type base, so a 0.5% bronze outscores a 40% platinum.

What is pinned here is the rule itself -- what a trophy is worth and which trophies count. The board that
sums it lives elsewhere; these tests exist so the formula has one definition and cannot quietly acquire a
second.

See trophies/services/pp_score.py.
"""
import pytest
from django.db.models import Sum

from trophies.models import Trophy
from trophies.services import pp_score
from tests.factories import GameFactory, TrophyFactory

pytestmark = pytest.mark.django_db


def _trophy(rate, group='default', tier='bronze', game=None):
    return TrophyFactory(game=game or GameFactory(), trophy_type=tier,
                         trophy_group_id=group, trophy_earn_rate=rate)


# ---------------------------------------------------------------- the formula ---------------------------

@pytest.mark.parametrize('rate,expected', [
    (100.0, 1.0),      # everybody has it
    (50.0, 2.0),
    (10.0, 10.0),
    (1.0, 100.0),
    (0.5, 200.0),
])
def test_points_are_how_many_players_you_would_line_up_to_find_one(rate, expected):
    """The reading that makes the score explainable: a 1% trophy is worth 100 because you would line up
    100 players to find one who has it."""
    assert pp_score.points_for(rate) == expected


def test_the_scale_matches_PSNs_own_range():
    """The board's whole scale is 1 to 1,000, and that range is PSN's rather than ours: it reports no rate
    above 100% or below 0.1%. A perfect score is TOP_N * MAX_POINTS = 1,000,000.

    Worth pinning because it is the answer to "how big can this number get", which is the first thing
    anyone asks of a new score.
    """
    assert pp_score.points_for(100.0) == 1.0
    assert pp_score.points_for(pp_score.RATE_FLOOR) == pp_score.MAX_POINTS == 1000.0
    assert pp_score.TOP_N * pp_score.MAX_POINTS == 1_000_000


def test_a_corrupt_below_floor_rate_cannot_exceed_the_maximum():
    """A DATA-INTEGRITY backstop, not a noise guard -- PSN has already floored at 0.1%, so these inputs
    cannot arrive from sync. What they can arrive from is a hand-edit or a bad import, and without the
    floor one such row is worth more than every legitimate trophy a hunter owns combined."""
    assert pp_score.points_for(0.01) == pp_score.MAX_POINTS
    assert pp_score.points_for(0.0001) == pp_score.MAX_POINTS


def test_the_cap_is_derived_from_the_floor():
    """Two constants describing one thing. Typed separately they drift, and the drift is invisible -- the
    board just quietly starts paying more than its own documented maximum."""
    assert pp_score.MAX_POINTS == 100.0 / pp_score.RATE_FLOOR


def test_rarer_always_scores_higher():
    """Monotonicity is not cosmetic: the board takes a hunter's rarest 1,000 by ordering on the RATE, which
    is only equivalent to ordering on points while the function is monotonic. Break this and the top-N
    selects the wrong trophies while every individual score still looks right."""
    rates = [90.0, 50.0, 25.0, 10.0, 5.0, 1.0, 0.5, 0.2]
    points = [pp_score.points_for(r) for r in rates]
    assert points == sorted(points), 'a rarer trophy scored no higher than a commoner one'


# ---------------------------------------------------------------- what counts ----------------------------

def test_an_unknown_rate_scores_ZERO_not_infinity():
    """THE trap this rule has to survive.

    `trophy_earn_rate` DEFAULTS to 0.0 and `psn_api_service` stores 0.0 whenever PSN omits the figure, so
    zero means UNKNOWN -- never "nobody has it". Read as a rate it is infinitely rare, so under
    `100 / rate` a hunter with a few hundred unsynced trophies would top the board outright, and the
    failure is silent: no error, just an impossible score.
    """
    assert pp_score.points_for(0.0) == 0.0
    assert pp_score.points_for(None) == 0.0
    assert pp_score.points_for(-1.0) == 0.0


def test_a_trophy_with_an_unknown_rate_is_not_scorable():
    known = _trophy(5.0)
    unknown = _trophy(0.0)

    scorable = set(Trophy.objects.filter(pp_score.scorable_q()).values_list('id', flat=True))
    assert known.id in scorable
    assert unknown.id not in scorable, 'a trophy with no known rarity reached the board'


def test_DLC_trophies_are_not_scorable():
    """PSN divides a DLC trophy's earners by everyone who owns the BASE GAME rather than the DLC, so DLC
    rarity is systematically overstated. On a top-N board that is decisive rather than marginal: the
    inflated DLC trophies would fill the slots. Our own data cannot correct it -- `ProfileTrophyGroup`
    rows exist only where a hunter EARNED something in a group, so we can see engagement but never
    ownership.
    """
    game = GameFactory()
    base = _trophy(5.0, group='default', game=game)
    dlc = _trophy(0.5, group='001', game=game)     # rarer on paper, and excluded anyway

    scorable = set(Trophy.objects.filter(pp_score.scorable_q()).values_list('id', flat=True))
    assert base.id in scorable
    assert dlc.id not in scorable, 'a DLC trophy reached the board'


def test_scorable_q_reaches_through_a_prefix():
    """The board filters from EarnedTrophy, so the same Q has to work at `trophy__`. Written once and
    reached through a prefix rather than re-spelled at each call site."""
    from trophies.models import EarnedTrophy
    from tests.factories import EarnedTrophyFactory, ProfileFactory

    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    EarnedTrophyFactory(profile=profile, trophy=_trophy(2.0, game=game), earned=True)
    EarnedTrophyFactory(profile=profile, trophy=_trophy(0.0, game=game), earned=True)
    EarnedTrophyFactory(profile=profile, trophy=_trophy(1.0, group='001', game=game), earned=True)

    assert EarnedTrophy.objects.filter(pp_score.scorable_q('trophy__')).count() == 1


# ---------------------------------------------------------------- one rule, two languages ---------------

@pytest.mark.parametrize('rate', [100.0, 50.0, 12.5, 5.0, 1.0, 0.5, 0.1, 0.01, 0.0])
def test_the_ORM_expression_agrees_with_the_python_one(rate):
    """Two implementations of one rule is exactly how a leaderboard comes to disagree with the number
    shown on a trophy row. Asserted against each other, per rate, INCLUDING the zero case -- the guard in
    the expression is what stops a caller who forgot to filter from summing an infinity.
    """
    _trophy(rate)

    got = Trophy.objects.aggregate(total=Sum(pp_score.points_expression()))['total'] or 0.0

    assert got == pytest.approx(pp_score.points_for(rate)), (
        f'the SQL and Python spellings disagree at rate={rate}'
    )


def test_the_expression_sums_only_what_it_should_across_many_trophies():
    """The end-to-end shape the recompute will use: filter with `scorable_q`, sum with the expression."""
    game = GameFactory()
    _trophy(1.0, game=game)                      # 100
    _trophy(10.0, game=game)                     # 10
    _trophy(0.0, game=game)                      # unknown -- excluded
    _trophy(0.5, group='001', game=game)         # DLC -- excluded

    total = (Trophy.objects.filter(pp_score.scorable_q())
             .aggregate(total=Sum(pp_score.points_expression()))['total'])

    assert total == pytest.approx(110.0)
