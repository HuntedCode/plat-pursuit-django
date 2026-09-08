"""PP Score: what a trophy is worth, and which trophies count.

THE ONE DEFINITION of the scoring rule. The nightly recompute, any per-trophy display, and every test read
it from here -- a second spelling of this formula would be a second leaderboard.

    points = 100 / earn_rate        (earn_rate is a PERCENTAGE)

It reads as "how many players you would line up to find one who has this": a 1% trophy is worth 100, a
10% trophy 10, a 50% trophy 2. So a hunter's PP Score is *how many players you would line up in total,
across their rarest 1,000 base-game trophies* -- which is the whole board explained in one sentence.

The scale runs 1 to 1,000 per trophy, and that range is PSN's rather than ours: it reports no rate above
100% or below 0.1%. A perfect score is therefore 1,000,000.

PURE RARITY, no trophy-type base (owner's call, 2026-09). A 0.5% bronze outscores a 40% platinum. The
board answers "who has done the hardest things", and a platinum on an easy game is not a hard thing.

WHICH RATE. `Trophy.trophy_earn_rate` -- PSN's own global figure -- not our `Trophy.earn_rate`. PSN's
sample is tens of millions where ours is ~50,000 linked hunters, so ours is noisy on obscure games; and
PSN's is the number hunters already recognise from the console. NOTE THE UNITS DIFFER: `trophy_earn_rate`
is a PERCENTAGE (12.3 means 12.3%) and is rendered directly as `{{ trophy_earn_rate }}%`, while
`earn_rate` is a FRACTION rendered with `|multiply:100`. Feeding the wrong one in scales every score by
100 and nothing errors.

HOW THE SUM SHOULD BE TAKEN. Not by sorting a hunter's trophies and slicing the first TOP_N: for a
250,000-trophy hunter that is a 250,000-row sort, per hunter, and the codebase has already dropped one
sort of this shape on cost (Browse Hunters' `rarest_avg_plat`). Aggregate by RATE instead --

    .values('profile_id', 'trophy__trophy_earn_rate').annotate(n=Count('id'))

-- then walk the buckets rarest-first in Python, taking `min(n, remaining)` from each. PSN reports rates
to one decimal, so there are at most ~1,000 distinct values across the whole catalogue: the result set is
bounded by the RATE VOCABULARY rather than by library size, and a hash aggregate in bounded work_mem
replaces the sort. The answer is identical, because every figure this board stores is a function of the
rate alone (see the tie note below).

TIES ARE ARBITRARY AND THAT IS SAFE, but only for as long as nothing row-identified is stored. Points are
strictly decreasing in rate only ABOVE the floor; at and below it every trophy is worth MAX_POINTS. With
~1,000 distinct rates over ~1.03M trophies, the TOP_N boundary lands inside a tie bucket for essentially
every qualifying hunter, so "the rarest 1,000" is not a well-defined SET of rows. It does not matter:
`pp_score` and `avg_earn_rate` are both functions of the rate, so any choice of tie members yields the
same two numbers. That stops being true the moment something stores a row identity taken from the slice
-- a rarest-trophy pointer, a per-trophy breakdown, a cached list of the scoring thousand.

BASE GAME ONLY. DLC trophies are excluded outright, because PSN divides a DLC trophy's earners by everyone
who owns the BASE GAME rather than the DLC -- so DLC rarity is systematically overstated, often wildly.
Our own data cannot fix it either: `ProfileTrophyGroup` rows exist only where a hunter EARNED something in
a group, so we can tell who engaged with a DLC but never who owns it. On a top-N board that distortion is
not a rounding error, it is decisive: the artificially-ultra-rare DLC trophies would simply fill the
slots. Excluding them is the honest v1; revisit only with real output in front of us.
"""
from django.db.models import Case, F, FloatField, Q, Value, When
from django.db.models.functions import Greatest

#: PSN NEVER REPORTS A RATE BELOW 0.1%, so this matches its own precision rather than imposing a policy.
#: An earlier version of this comment called it a noise guard against rounding artefacts on barely-played
#: games; that was wrong, because PSN has already done that flooring for us. What it actually is now is a
#: DATA-INTEGRITY backstop -- it fires only on a corrupt or hand-edited value, never on anything PSN sent.
#:
#: The consequence worth knowing is at the TOP of the board: every trophy PSN reports at 0.1% is worth
#: exactly MAX_POINTS, so the rarest trophies are indistinguishable from each other. A 0.1% trophy and a
#: hypothetical 0.001% trophy score the same, because PSN does not tell us they differ. The very top of
#: this board is therefore "how many 0.1% trophies do you hold", which is a legitimate question but not
#: quite the same one as "who has the single rarest trophy".
RATE_FLOOR = 0.1

#: The most any single trophy can be worth -- `100 / RATE_FLOOR`, i.e. 1,000. REACHABLE, not theoretical:
#: PSN's floor means real trophies sit exactly here. So a perfect score is TOP_N * MAX_POINTS = 1,000,000,
#: and the whole scale runs 1 (a 100% trophy) to 1,000. Derived rather than typed so the two cannot drift.
MAX_POINTS = 100.0 / RATE_FLOOR

#: How many of a hunter's rarest scorable trophies are summed. Volume beyond this buys nothing.
TOP_N = 1000

#: PSN's own group id for the base game. Everything else is DLC.
BASE_GAME_GROUP = 'default'


def scorable_q(prefix=''):
    """Q for the trophies that COUNT: base game, and with a rate we actually know.

    `trophy_earn_rate > 0` is the load-bearing half, and it is easy to get backwards. The field DEFAULTS
    to 0.0 and `psn_api_service` stores 0.0 whenever PSN omits the figure, so zero means UNKNOWN -- never
    "nobody has it". Treated as a rate it is infinitely rare, so under `100 / rate` a hunter with a few
    hundred unsynced trophies would top the board outright. Excluded, loudly, and pinned by test.

    `prefix` reaches the Trophy from another model, e.g. `'trophy__'` from EarnedTrophy.
    """
    return Q(**{
        f'{prefix}trophy_group_id': BASE_GAME_GROUP,
        f'{prefix}trophy_earn_rate__gt': 0,
    })


def scorable_earned(qs, prefix='trophy__'):
    """The EARNED, scorable rows a hunter's score is built from. Use this; never re-spell it.

    It exists because the filter and the selection ORDER cannot safely be written apart.

    `scorable_q` excludes unknown rates (0.0) -- and under the points-DESC ordering that reads as
    defensive, because a 0-point row sorts last and a forgotten filter costs nothing. The recompute does
    NOT order by points: it orders by RATE, because rate is indexed and the function is monotonic. Under
    THAT ordering an unknown rate is 0.0, which sorts FIRST. So a top-N selection that forgets the filter
    fills every slot with 0-point rows: the score collapses toward zero while `scored_count` still reaches
    TOP_N, so the hunter passes the membership rule and lands on the board with a nonsense figure. Silent,
    and the wrong way round from what the ordering makes it look like.

    `earned=True` is folded in for the same reason. PSN sync writes `EarnedTrophy` rows for trophies a
    hunter has NOT earned -- that is how progress is tracked -- so a filter that omits it ranks library
    ownership rather than achievement, which is a different board that looks plausible.
    """
    return qs.filter(scorable_q(prefix), earned=True)


def points_for(rate):
    """Points for one trophy, in Python. The reference implementation the ORM expression must match.

    Returns 0 for an unknown rate (see `scorable_q`) so a caller that forgets to filter gets a zero rather
    than a division error or an enormous number.
    """
    if rate is None or rate <= 0:
        return 0.0
    return 100.0 / max(rate, RATE_FLOOR)


def points_expression(prefix=''):
    """The same formula as an ORM expression, for summing in the database.

    Kept beside `points_for` and asserted equal to it by test: two implementations of one rule is exactly
    how a leaderboard comes to disagree with the number on a trophy row.

    The `When` guard mirrors `scorable_q`'s zero-rate exclusion. It is belt-and-braces -- callers filter
    first -- but a sum is the wrong place to discover a caller forgot.
    """
    rate = f'{prefix}trophy_earn_rate'
    return Case(
        When(**{f'{rate}__gt': 0},
             then=Value(100.0) / Greatest(F(rate), Value(RATE_FLOOR))),
        default=Value(0.0),
        output_field=FloatField(),
    )
