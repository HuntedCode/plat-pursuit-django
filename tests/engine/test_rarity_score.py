"""Rarity Score -- the scoring rule (leaderboards, 2026-09).

`points = 100 / earn_rate`, summed over a hunter's rarest 1,000 BASE-GAME trophies. Pure rarity: no
trophy-type base, so a 0.5% bronze outscores a 40% platinum.

What is pinned here is the rule itself -- what a trophy is worth and which trophies count. The board that
sums it lives elsewhere; these tests exist so the formula has one definition and cannot quietly acquire a
second.

See trophies/services/rarity_score.py.
"""
import pytest
from django.db.models import Sum

from trophies.models import Trophy
from trophies.services import rarity_score
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
    assert rarity_score.points_for(rate) == expected


def test_the_scale_matches_PSNs_own_range():
    """The board's whole scale is 1 to 1,000, and that range is PSN's rather than ours: it reports no rate
    above 100% or below 0.1%. A perfect score is TOP_N * MAX_POINTS = 1,000,000.

    Worth pinning because it is the answer to "how big can this number get", which is the first thing
    anyone asks of a new score.
    """
    assert rarity_score.points_for(100.0) == 1.0
    assert rarity_score.points_for(rarity_score.RATE_FLOOR) == rarity_score.MAX_POINTS == 1000.0
    assert rarity_score.TOP_N * rarity_score.MAX_POINTS == 1_000_000


def test_a_corrupt_below_floor_rate_cannot_exceed_the_maximum():
    """A DATA-INTEGRITY backstop, not a noise guard -- PSN has already floored at 0.1%, so these inputs
    cannot arrive from sync. What they can arrive from is a hand-edit or a bad import, and without the
    floor one such row is worth more than every legitimate trophy a hunter owns combined."""
    assert rarity_score.points_for(0.01) == rarity_score.MAX_POINTS
    assert rarity_score.points_for(0.0001) == rarity_score.MAX_POINTS


def test_the_cap_is_what_the_FORMULA_yields_at_the_floor():
    """Two constants describing one thing, tied through the formula rather than through the arithmetic
    that defines one of them.

    `MAX_POINTS == 100.0 / RATE_FLOOR` restates the definition, so it holds by construction and cannot
    notice a hardcoded literal. Going via `points_for` catches the case that actually matters -- the two
    DIVERGING, whenever either moves without the other. (A literal typed while it still happens to be
    correct is not a bug; the divergence is, and this fires on it.)
    """
    assert rarity_score.MAX_POINTS == rarity_score.points_for(rarity_score.RATE_FLOOR)
    assert rarity_score.MAX_POINTS == 1000.0, 'the documented ceiling moved; the docs and board copy say 1,000'


def test_rarer_scores_STRICTLY_higher_above_the_floor():
    """Monotonicity is not cosmetic: the recompute takes a hunter's rarest 1,000 by ordering on the RATE,
    which is only equivalent to ordering on points while the function is monotonic. Break it and the
    top-N selects the wrong trophies while every individual score still looks right.

    STRICT, and that word is the test. This asserted `points == sorted(points)`, which passes for any
    non-decreasing sequence -- including a CONSTANT one. Verified by mutation: `points_for` returning 1.0
    for every rate passed it, and so did a floor set 50x too high, which flattens most of the range. It
    detected only a total inversion. Its rates also all sat above the floor, so it never touched the
    region where the function legitimately IS flat -- which is where the tie question lives.
    """
    rates = [90.0, 50.0, 25.0, 10.0, 5.0, 1.0, 0.5, 0.2]
    points = [rarity_score.points_for(r) for r in rates]

    assert all(a < b for a, b in zip(points, points[1:])), (
        f'points are not strictly increasing as rate falls: {list(zip(rates, points))}'
    )


def test_every_trophy_at_or_below_the_floor_is_worth_the_SAME():
    """The flat region, asserted as INTENDED rather than left to be discovered.

    PSN reports no rate below 0.1%, so the floor is where its precision stops rather than a policy of
    ours -- which means real trophies sit exactly there (3,683 of them in the catalogue) and are genuinely
    indistinguishable. The recompute must not assume its rarest-1,000 is a well-defined set of ROWS.
    """
    assert (rarity_score.points_for(rarity_score.RATE_FLOOR)
            == rarity_score.points_for(rarity_score.RATE_FLOOR / 2)
            == rarity_score.points_for(0.0001)
            == rarity_score.MAX_POINTS)


def test_ordering_by_RATE_gives_the_same_points_as_ordering_by_POINTS():
    """THE property the recompute depends on, asserted directly instead of inferred from monotonicity.

    It selects a hunter's rarest trophies by ordering on `trophy_earn_rate` ASC -- because rate is indexed
    and points are not -- then sums points. That substitution is valid only if the two orderings produce
    the same sequence of POINTS. Ties may pick different rows; they must not produce different numbers.
    """
    import random

    rates = [90.0, 50.0, 25.0, 10.0, 5.0, 1.0, 0.5, 0.2, 0.1, 0.1, 0.1, 12.3, 12.3]
    shuffled = rates[:]
    random.shuffle(shuffled)

    by_rate = [rarity_score.points_for(r) for r in sorted(shuffled)]
    by_points = [rarity_score.points_for(r)
                 for r in sorted(shuffled, key=rarity_score.points_for, reverse=True)]

    assert by_rate == by_points, 'ordering by rate and by points disagree on the points sequence'


# ---------------------------------------------------------------- what counts ----------------------------

def test_an_unknown_rate_scores_ZERO_not_infinity():
    """THE trap this rule has to survive.

    `trophy_earn_rate` DEFAULTS to 0.0 and `psn_api_service` stores 0.0 whenever PSN omits the figure, so
    zero means UNKNOWN -- never "nobody has it". Read as a rate it is infinitely rare, so under
    `100 / rate` a hunter with a few hundred unsynced trophies would top the board outright, and the
    failure is silent: no error, just an impossible score.
    """
    assert rarity_score.points_for(0.0) == 0.0
    assert rarity_score.points_for(None) == 0.0
    assert rarity_score.points_for(-1.0) == 0.0


def test_a_trophy_with_an_unknown_rate_is_not_scorable():
    known = _trophy(5.0)
    unknown = _trophy(0.0)

    scorable = set(Trophy.objects.filter(rarity_score.scorable_q()).values_list('id', flat=True))
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

    scorable = set(Trophy.objects.filter(rarity_score.scorable_q()).values_list('id', flat=True))
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

    assert EarnedTrophy.objects.filter(rarity_score.scorable_q('trophy__')).count() == 1


def test_an_UNEARNED_trophy_never_scores():
    """`scorable_q` alone does NOT exclude it, and that is why `scorable_earned` exists.

    PSN sync writes `EarnedTrophy` rows for trophies a hunter has not earned -- that is how progress is
    tracked -- so a filter that omits `earned=True` ranks library OWNERSHIP rather than achievement. That
    is a different board, and a plausible-looking one: the figures are all in range, they are just
    measuring the wrong thing.
    """
    from trophies.models import EarnedTrophy
    from tests.factories import EarnedTrophyFactory, ProfileFactory

    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    EarnedTrophyFactory(profile=profile, trophy=_trophy(1.0, game=game), earned=True)
    EarnedTrophyFactory(profile=profile, trophy=_trophy(0.5, game=game), earned=False)

    rows = rarity_score.scorable_earned(EarnedTrophy.objects.filter(profile=profile))

    assert rows.count() == 1, 'an unearned trophy reached the scoring set'
    total = rows.aggregate(t=Sum(rarity_score.points_expression('trophy__')))['t']
    assert total == pytest.approx(100.0), 'the unearned trophy contributed points'


def test_an_UNKNOWN_rate_row_sorts_FIRST_by_rate_which_is_why_the_filter_is_mandatory():
    """The trap the ordering substitution creates, pinned as behaviour.

    The recompute orders by RATE ascending, because rate is indexed and points are not. An unknown rate is
    stored as 0.0, so it sorts FIRST -- ahead of every genuine ultra-rare. A top-N selection that forgets
    the filter therefore fills its slots with 0-point rows: the score collapses toward zero while
    `scored_count` still reaches TOP_N, so the hunter passes the membership rule and lands on the board
    with a nonsense figure.

    Under a points-DESC ordering the same omission is harmless, since 0-point rows sort last. This asserts
    the danger is real so the coupling in `scorable_earned` is not later "simplified" apart.
    """
    from trophies.models import EarnedTrophy
    from tests.factories import EarnedTrophyFactory, ProfileFactory

    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    EarnedTrophyFactory(profile=profile, trophy=_trophy(0.0, game=game), earned=True)   # unknown
    EarnedTrophyFactory(profile=profile, trophy=_trophy(0.5, game=game), earned=True)   # genuinely rare

    unfiltered = list(EarnedTrophy.objects.filter(profile=profile)
                      .order_by('trophy__trophy_earn_rate')
                      .values_list('trophy__trophy_earn_rate', flat=True))
    assert unfiltered[0] == 0.0, 'the premise changed: unknown rates no longer sort first'

    filtered = list(rarity_score.scorable_earned(EarnedTrophy.objects.filter(profile=profile))
                    .order_by('trophy__trophy_earn_rate')
                    .values_list('trophy__trophy_earn_rate', flat=True))
    assert filtered == [0.5], 'the helper let an unknown rate into the scoring set'


# ---------------------------------------------------------------- one rule, two languages ---------------

@pytest.mark.parametrize('rate', [100.0, 50.0, 12.5, 5.0, 1.0, 0.5, 0.1, 0.01, 0.0])
def test_the_ORM_expression_agrees_with_the_python_one(rate):
    """Two implementations of one rule is exactly how a leaderboard comes to disagree with the number
    shown on a trophy row. Asserted against each other, per rate, INCLUDING the zero case -- the guard in
    the expression is what stops a caller who forgot to filter from summing an infinity.
    """
    _trophy(rate)

    got = Trophy.objects.aggregate(total=Sum(rarity_score.points_expression()))['total'] or 0.0

    assert got == pytest.approx(rarity_score.points_for(rate)), (
        f'the SQL and Python spellings disagree at rate={rate}'
    )


def test_the_expression_sums_only_what_it_should_across_many_trophies():
    """The end-to-end shape the recompute will use: filter with `scorable_q`, sum with the expression."""
    game = GameFactory()
    _trophy(1.0, game=game)                      # 100
    _trophy(10.0, game=game)                     # 10
    _trophy(0.0, game=game)                      # unknown -- excluded
    _trophy(0.5, group='001', game=game)         # DLC -- excluded

    total = (Trophy.objects.filter(rarity_score.scorable_q())
             .aggregate(total=Sum(rarity_score.points_expression()))['total'])

    assert total == pytest.approx(110.0)
