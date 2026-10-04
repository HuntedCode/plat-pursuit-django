"""The three Plat Calendar view predicates, and the edges where a wrong one hides.

WHY THIS FILE IS MOSTLY EDGE CASES. The happy path ("a platinum fills its day") is one assertion and
cannot really be got wrong. What can be got wrong, silently, is everything around it: which timezone
decides the day, what happens on 29 February, whether the three views agree with each other where they
should and differ where they must, and whether the queries stay bounded for a hunter with a very large
library. Each of those produces a calendar that looks plausible and is wrong.
"""
import datetime as dt
import zoneinfo
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from challenges.models import (
    CALENDAR_VIEW_ALL,
    CALENDAR_VIEW_CLEAN,
)
from challenges.services import calendar_fill
from tests.factories import (
    ConceptFactory,
    GameFactory,
    IGDBMatchFactory,
    ProfileFactory,
    UserFactory,
)
from trophies.models import (
    Contract,
    EarnedContract,
    EarnedTrophy,
    ProfileGame,
    Trophy,
)

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]

_SEQ = {'n': 0}


def _hunter(tz='UTC'):
    # A MEMBER, because `start` is gated on the members-first beta and the run-writing tests below go
    # through the real service rather than building rows by hand -- so they exercise the same creation
    # path a hunter does.
    user = UserFactory(user_timezone=tz)
    return ProfileFactory(user=user, is_linked=True, user_is_premium=True)


def _platted(profile, when, *, shovelware=False, concept=None):
    """A game this hunter platted at `when` (an aware UTC datetime). Returns its Game."""
    _SEQ['n'] += 1
    concept = concept or ConceptFactory(anchor_migration_completed_at=timezone.now())
    game = GameFactory(
        concept=concept,
        shovelware_status='auto_flagged' if shovelware else 'clean',
    )
    trophy = Trophy.objects.create(
        game=game, trophy_type='platinum', trophy_id=_SEQ['n'],
        trophy_name='Platinum %d' % _SEQ['n'],
    )
    ProfileGame.objects.create(profile=profile, game=game, has_plat=True, progress=100)
    EarnedTrophy.objects.create(
        profile=profile, trophy=trophy, earned=True, earned_date_time=when)
    return game


def _utc(y, m, d, hour=12, minute=0):
    return dt.datetime(y, m, d, hour, minute, tzinfo=dt.timezone.utc)


# ── the all-platinums view ───────────────────────────────────────────────────────────────────────

def test_a_platinum_fills_its_day_and_records_when():
    profile = _hunter()
    _platted(profile, _utc(2019, 3, 3))

    days = calendar_fill.filled_days(profile)

    assert (3, 3) in days[CALENDAR_VIEW_ALL]
    assert days[CALENDAR_VIEW_ALL][(3, 3)].date == dt.date(2019, 3, 3)
    assert days[CALENDAR_VIEW_ALL][(3, 3)].plats == 1


def test_a_day_records_the_FIRST_time_it_was_filled():
    """A hunter who platted on 3 March in 2019 and again in 2024 filled that square in 2019. The later
    one is not wrong, it is just not the moment the square was earned."""
    profile = _hunter()
    _platted(profile, _utc(2024, 3, 3))
    _platted(profile, _utc(2019, 3, 3))

    days = calendar_fill.filled_days(profile)

    assert days[CALENDAR_VIEW_ALL][(3, 3)].date == dt.date(2019, 3, 3)


def test_the_hunters_timezone_decides_which_day_a_platinum_landed_on():
    """THE SAME INSTANT IS TWO DIFFERENT DAYS, and the one that counts is the owner's.

    23:40 UTC on 2 March is the 3rd in Tokyo and the 2nd in London. Resolving it in the SERVER's zone
    (UTC) would quietly re-date a chunk of every non-UTC hunter's calendar, and resolving it in the
    VIEWER's would make a public run page show different squares to different readers.
    """
    tokyo = _hunter('Asia/Tokyo')
    london = _hunter('Europe/London')
    instant = _utc(2021, 3, 2, hour=23, minute=40)

    _platted(tokyo, instant)
    _platted(london, instant)

    tokyo_days = calendar_fill.filled_days(tokyo)[CALENDAR_VIEW_ALL]
    london_days = calendar_fill.filled_days(london)[CALENDAR_VIEW_ALL]

    assert (3, 3) in tokyo_days
    assert (3, 2) not in tokyo_days

    assert (3, 2) in london_days
    assert (3, 3) not in london_days

    # ── AND THE STORED DATE AGREES WITH THE SQUARE IT SITS ON, which is a SECOND conversion and was
    # the one with no test. The key comes from `ExtractMonth`/`ExtractDay` and the date from
    # `TruncDate`; drop the timezone from only the second and Tokyo gets a square labelled 3 March
    # holding "2 March 2021". That is precisely the contradiction `earned_on` was made a DateField to
    # rule out, and a mutation found it surviving because this test only checked the key.
    assert tokyo_days[(3, 3)].date == dt.date(2021, 3, 3), (
        'the stored date was resolved in a different timezone from the day key')
    assert london_days[(3, 2)].date == dt.date(2021, 3, 2)


def test_a_leap_day_platinum_folds_into_the_28th_rather_than_vanishing():
    """There is no 29 February square -- a run is keyed on (month, day) across every year -- but the
    platinum is real. The retired system dropped it, which silently loses an achievement for a reason
    about the Gregorian calendar rather than about the hunter."""
    profile = _hunter()
    _platted(profile, _utc(2020, 2, 29))

    days = calendar_fill.filled_days(profile)

    assert (2, 29) not in days[CALENDAR_VIEW_ALL], 'there is no 29 February square to fill'
    assert (2, 28) in days[CALENDAR_VIEW_ALL], 'the leap-day platinum was dropped'


def test_a_folded_leap_day_cannot_overwrite_an_earlier_28th():
    """The fold makes two real dates share one square, so the earlier one still owns it."""
    profile = _hunter()
    _platted(profile, _utc(2020, 2, 29))
    _platted(profile, _utc(2015, 2, 28))

    days = calendar_fill.filled_days(profile)

    assert days[CALENDAR_VIEW_ALL][(2, 28)].date == dt.date(2015, 2, 28)
    assert days[CALENDAR_VIEW_ALL][(2, 28)].plats == 2, (
        'the fold MINS the dates and SUMS the counts -- both platinums land on the one square')


# ── the shovelware-free view ─────────────────────────────────────────────────────────────────────

def test_the_clean_view_drops_shovelware_and_the_all_view_keeps_it():
    profile = _hunter()
    _platted(profile, _utc(2019, 5, 5), shovelware=True)
    _platted(profile, _utc(2019, 6, 6))

    days = calendar_fill.filled_days(profile)

    assert (5, 5) in days[CALENDAR_VIEW_ALL]
    assert (5, 5) not in days[CALENDAR_VIEW_CLEAN], 'a shovelware platinum filled a clean day'
    assert (6, 6) in days[CALENDAR_VIEW_CLEAN]


def test_a_clean_day_is_always_an_all_day():
    """THE NESTING THAT HOLDS, asserted because the database constraint depends on it.

    `clean` is `all` minus shovelware, so a shovelware-free platinum is still a platinum.
    `calendarday_clean_implies_all` enforces this at write time; this proves the PREDICATES cannot
    produce a row that violates it, which is the half a constraint cannot tell you.
    """
    profile = _hunter()
    _platted(profile, _utc(2019, 1, 1))
    _platted(profile, _utc(2019, 2, 2), shovelware=True)
    _platted(profile, _utc(2019, 3, 3))

    days = calendar_fill.filled_days(profile)

    assert set(days[CALENDAR_VIEW_CLEAN]) <= set(days[CALENDAR_VIEW_ALL]), (
        'a clean day is not an all day, which the database forbids: %s'
        % sorted(set(days[CALENDAR_VIEW_CLEAN]) - set(days[CALENDAR_VIEW_ALL])))


def test_a_manually_cleared_game_counts_as_clean():
    """`SHOVELWARE_FLAGGED_STATUSES` is the broad rule: `manually_cleared` means a human looked at a
    flagged game and passed it, so it counts. The stricter `status == 'clean'` rule belongs to the
    community trophy tracker and is deliberately not this one."""
    profile = _hunter()
    game = _platted(profile, _utc(2019, 7, 7))
    game.shovelware_status = 'manually_cleared'
    game.save(update_fields=['shovelware_status'])

    days = calendar_fill.filled_days(profile)

    assert (7, 7) in days[CALENDAR_VIEW_CLEAN]


# ── the contracts view ───────────────────────────────────────────────────────────────────────────

def test_the_query_count_does_not_grow_with_the_hunters_library():
    """THE RULE THIS CODEBASE HAS PAID FOR REPEATEDLY. A per-user queryset that produces aggregate
    output must aggregate in the DATABASE; iterating it in Python is what OOMs a worker for a hunter
    with a quarter of a million trophies.

    Query COUNT is the proxy a test can see. The shape it protects -- grouping by month and day in
    Postgres and returning at most 365 rows -- is what the count staying flat demonstrates: a
    per-platinum loop or a per-game query would scale with the fixtures below.
    """
    profile = _hunter()
    for i in range(3):
        _platted(profile, _utc(2019, 1, i + 1))

    with CaptureQueriesContext(connection) as small:
        calendar_fill.filled_days(profile)

    for i in range(12):
        _platted(profile, _utc(2019, 4, i + 1))

    with CaptureQueriesContext(connection) as large:
        calendar_fill.filled_days(profile)

    assert len(large.captured_queries) == len(small.captured_queries), (
        'the fill cost grew from %d queries to %d when the library grew by twelve games'
        % (len(small.captured_queries), len(large.captured_queries)))

    # ── AND THE GROUPING REALLY HAPPENS IN POSTGRES, which the query COUNT cannot show: a Python loop
    # over `EarnedTrophy` building the same dict is also one query and would pass everything above. That
    # is the exact anti-pattern CLAUDE.md names, so it gets a pin a runner can actually see.
    sql = ' '.join(q['sql'] for q in large.captured_queries)
    assert 'GROUP BY' in sql.upper(), 'the day grouping is not being done by the database'
    assert 'EXTRACT' in sql.upper(), 'the month/day split is not being done by the database'

    # ── BOTH PLATINUM VIEWS COME FROM ONE STATEMENT. Two statements means two READ COMMITTED snapshots,
    # and a platinum committed between them lands in `clean` but not `all` -- which the writer turns into
    # an IntegrityError against `calendarday_clean_implies_all`, out of a scheduled job.
    plat_selects = [q for q in large.captured_queries
                    if 'earnedtrophy' in q['sql'].lower() and 'GROUP BY' in q['sql'].upper()]
    assert len(plat_selects) == 1, (
        'the two platinum views are being read separately (%d statements), so `clean` can escape `all` '
        'between snapshots' % len(plat_selects))


def test_a_hunter_with_no_platinums_costs_nothing_and_returns_empty():
    """The early return matters: a brand-new hunter is the common case on an opening backfill, and
    asking `EarnedTrophy` for an empty id list is a query that can only return nothing."""
    profile = _hunter()

    days = calendar_fill.filled_days(profile)

    assert days == {CALENDAR_VIEW_ALL: {}, CALENDAR_VIEW_CLEAN: {}}


# ── writing the fills onto a run ─────────────────────────────────────────────────────────────────

def _calendar_run(profile):
    from challenges.models import CHALLENGE_TYPE_CALENDAR
    from challenges.services import challenge_service as svc

    original = svc.TYPES_NOT_YET_CREATABLE
    svc.TYPES_NOT_YET_CREATABLE = frozenset()
    try:
        return svc.start(profile, CHALLENGE_TYPE_CALENDAR)
    finally:
        svc.TYPES_NOT_YET_CREATABLE = original


def test_the_backfill_writes_the_views_onto_the_right_squares():
    profile = _hunter()
    _platted(profile, _utc(2019, 3, 3))
    _platted(profile, _utc(2019, 5, 5), shovelware=True)
    run = _calendar_run(profile)

    filled = calendar_fill.apply_to_run(run)

    assert filled == 2, 'both squares should have been newly filled'
    march = run.calendar_days.get(month=3, day=3)
    may = run.calendar_days.get(month=5, day=5)

    assert march.in_all and march.in_clean
    assert may.in_all and not may.in_clean, 'a shovelware platinum filled a clean square'
    assert march.earned_on == dt.date(2019, 3, 3)
    assert march.filled_at is not None, 'the write timestamp is what says WE recorded it'


def test_a_filled_day_is_never_un_filled():
    """THE MONOTONE RULE, and it is the reason the booleans are stored rather than computed.

    The predicates can legitimately stop matching: a game is reclassified as shovelware,
    `reconcile_contracts` deletes an `EarnedContract` when derived membership moves, a staff `igdb_id`
    edit changes who qualifies. None of those are things the hunter did, so none of them may retract a
    square they already earned -- the same rule `ChallengeSlot` follows by snapshotting its contract.
    """
    profile = _hunter()
    game = _platted(profile, _utc(2019, 4, 4))
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    assert run.calendar_days.get(month=4, day=4).in_clean

    # The catalogue changes under them: the game is flagged as shovelware after the fact.
    game.shovelware_status = 'auto_flagged'
    game.save(update_fields=['shovelware_status'])
    calendar_fill.apply_to_run(run)

    day = run.calendar_days.get(month=4, day=4)
    assert day.in_clean, 'a reclassification retracted a square the hunter had already earned'
    assert day.in_all


def test_the_backfill_is_idempotent():
    """An opening backfill, a sync hook and a nightly sweep all call this, so running it twice must not
    double-count or re-stamp. `newly_filled` is what the ceremony reports, so a second pass claiming
    work would make the copy wrong as well as the counters."""
    profile = _hunter()
    _platted(profile, _utc(2019, 6, 6))
    run = _calendar_run(profile)

    first = calendar_fill.apply_to_run(run)
    stamped = run.calendar_days.get(month=6, day=6).filled_at
    second = calendar_fill.apply_to_run(run)

    assert first == 1 and second == 0, 'the second pass claimed to fill something'
    assert run.calendar_days.get(month=6, day=6).filled_at == stamped, 'the fill was re-stamped'


def test_the_progress_number_is_the_best_genuine_view_not_all_platinums():
    """`filled_count` tracks the higher of clean and contracts, because either completing finishes the
    run. All-platinums is the lens shovelware inflates and cannot finish anything, so leading with it
    would show a card closer to done than the run actually is."""
    profile = _hunter()
    _platted(profile, _utc(2019, 1, 1), shovelware=True)
    _platted(profile, _utc(2019, 2, 2), shovelware=True)
    _platted(profile, _utc(2019, 3, 3))
    run = _calendar_run(profile)

    calendar_fill.apply_to_run(run)
    run.refresh_from_db()

    assert run.calendar_days.filter(in_all=True).count() == 3
    assert run.filled_count == 1, (
        '`filled_count` is showing the all-platinums count (3), which cannot finish a run')
    assert run.completed_count == run.filled_count, (
        'a day has no assigned-but-unfinished state, so these are the same number')


def test_a_run_completes_when_every_day_is_filled():
    profile = _hunter()
    run = _calendar_run(profile)
    run.calendar_days.update(in_all=True, in_clean=True)

    calendar_fill._recount_calendar(run)
    run.refresh_from_db()

    assert run.is_complete and run.completed_at is not None
    assert run.filled_count == 365


def test_all_platinums_filling_every_day_does_not_finish_a_run():
    """THE RULE THE WHOLE COMPLETION MODEL RESTS ON. All-platinums is the lens shovelware inflates, so
    it carries the early day-marker ladder and finishes nothing. A hunter can hold "filled all 365 days"
    and still have an unfinished run -- which looks like a bug in a screenshot and is the design."""
    profile = _hunter()
    run = _calendar_run(profile)
    run.calendar_days.update(in_all=True)

    calendar_fill._recount_calendar(run)
    run.refresh_from_db()

    assert not run.is_complete, 'the easiest view finished a run'
    assert run.filled_count == 0


def test_the_backfill_cost_does_not_grow_with_the_number_of_filled_days():
    """365 rows is the whole table for a run, so the write is one bulk_update and the recount is one
    aggregate -- not a save per square."""
    profile = _hunter()
    for i in range(3):
        _platted(profile, _utc(2019, 1, i + 1))
    run = _calendar_run(profile)

    with CaptureQueriesContext(connection) as few:
        calendar_fill.apply_to_run(run)

    profile2 = _hunter()
    for i in range(20):
        _platted(profile2, _utc(2019, 7, i + 1))
    run2 = _calendar_run(profile2)

    with CaptureQueriesContext(connection) as many:
        calendar_fill.apply_to_run(run2)

    assert len(many.captured_queries) == len(few.captured_queries), (
        'filling twenty days cost %d queries against %d for three'
        % (len(many.captured_queries), len(few.captured_queries)))


def test_a_day_gaining_a_second_view_keeps_its_original_fill_stamp():
    """`filled_at` IS WHEN WE FIRST RECORDED THE SQUARE, not when we last touched it.

    A day can gain a view on a later pass -- a game flagged as shovelware is manually cleared, so a
    square that was only `in_all` becomes `in_clean` too. That pass legitimately writes the row, so the
    idempotency test cannot see it: that one asserts an UNCHANGED row is left alone, and this row
    changed. Found by mutation rather than by reading.

    It matters because `filled_at` is the audit answer to "when did this run learn about this square",
    and moving it on a catalogue correction re-dates a square to the day the catalogue was fixed.
    """
    profile = _hunter()
    game = _platted(profile, _utc(2019, 8, 8), shovelware=True)
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    first = run.calendar_days.get(month=8, day=8)
    assert first.in_all and not first.in_clean
    stamped = first.filled_at

    # A human looks at the flagged game and passes it, so the square is now clean too.
    game.shovelware_status = 'manually_cleared'
    game.save(update_fields=['shovelware_status'])

    # ── ZERO, NOT ONE, and this assertion is the other half of the fix. `newly_filled` is what the
    # opening ceremony reports ("you start at 154 days"), so it must count SQUARES THAT WERE EMPTY and
    # are now filled -- not rows that were written. This returned 1 when a shovelware reclassification
    # gave an already-filled square a second view, which would have had a sync announce "1 new day" on a
    # day nothing was earned.
    assert calendar_fill.apply_to_run(run) == 0, (
        'a square that merely gained a second view was counted as a newly filled day')

    day = run.calendar_days.get(month=8, day=8)
    assert day.in_clean, 'the square did not gain the clean view'
    assert day.filled_at == stamped, (
        'gaining a view re-dated the square to the day the catalogue was corrected')


# ── the guards an audit found, none of which a source mutation could reach ────────────────────────

def test_applying_to_a_non_calendar_run_does_nothing_at_all():
    """THE SITE-WIDE ONE. `apply_to_run` recounts from `calendar_days`, which is EMPTY for an A-Z or
    Job Coverage run -- so without a type guard, a sweep written as
    `for c in Challenge.objects.active(): apply_to_run(c)` writes `filled_count = 0` onto every hunter's
    part-finished A-Z run in one pass.

    SILENTLY: `0 <= 0 <= 26` violates no constraint, so nothing raises and nothing logs. It would
    self-heal only on that run's next assign or completion, which for a hunter who had stopped playing
    is never. This is the cheapest guard in the module and the most expensive omission.
    """
    from challenges.models import CHALLENGE_TYPE_AZ
    from challenges.services import challenge_service as svc

    profile = _hunter()
    az = svc.start(profile, CHALLENGE_TYPE_AZ)
    az.filled_count = 19
    az.completed_count = 7
    az.save(update_fields=['filled_count', 'completed_count'])

    assert calendar_fill.apply_to_run(az) == 0

    az.refresh_from_db()
    assert az.filled_count == 19, 'an A-Z run had its progress zeroed by the Calendar writer'
    assert az.completed_count == 7


def test_the_writer_takes_the_runs_row_lock():
    """TWO CALLERS CAN ARRIVE AT ONCE -- the sync hook fires whenever a hunter syncs and the nightly
    sweep runs unscoped -- and `challenge_service.mark_slot_completed` takes `select_for_update` before
    recounting for exactly that reason.

    Without it: both passes stamp `completed_at` and fire the reward hook, `just_completed` is decided
    against a possibly stale in-memory instance, and -- worst -- two passes that read the same rows and
    write all five columns back can RETRACT each other's fills, breaking the monotone guarantee by
    concurrency rather than by a predicate.

    Asserted on the SQL rather than by racing two threads, which a test DB in a transaction cannot do
    honestly. The lock either appears in the statement or it does not.
    """
    profile = _hunter()
    _platted(profile, _utc(2019, 2, 2))
    run = _calendar_run(profile)

    with CaptureQueriesContext(connection) as captured:
        calendar_fill.apply_to_run(run)

    locking = [q for q in captured.captured_queries
               if 'FOR UPDATE' in q['sql'].upper() and 'challenges_challenge' in q['sql'].lower()]
    assert locking, 'the writer recounts and completes a run without holding its row lock'


def test_a_later_sync_discovering_an_older_platinum_corrects_the_date():
    """PSN RETURNS TITLES BY RECENCY, so a later sync legitimately discovers an OLDER platinum on a
    square that is already filled -- a deep backfill, or a re-queued `sync_trophies` reaching games it
    had not seen.

    An earlier version only wrote `earned_on` when a view FLIPPED, so the square kept claiming the later
    date forever while the day modal -- which derives its satisfier list live -- listed the older game.
    That is the same "a square labelled X holding Y" contradiction the `DateField` was chosen to rule
    out, reached from the other direction.

    No source mutation could find this: the bug was missing code, not wrong code.
    """
    profile = _hunter()
    _platted(profile, _utc(2024, 3, 3))
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    assert run.calendar_days.get(month=3, day=3).earned_on == dt.date(2024, 3, 3)

    # A later sync reaches an older title platted on the same calendar day.
    _platted(profile, _utc(2015, 3, 3))
    calendar_fill.apply_to_run(run)

    assert run.calendar_days.get(month=3, day=3).earned_on == dt.date(2015, 3, 3), (
        'the square kept the later date after an earlier platinum was discovered')


def test_earned_on_never_moves_forward_within_one_lens():
    """The correction above only ever runs BACKWARDS within a lens. A square was earned when it was
    first earned, and a newer platinum on the same day does not re-date it.

    SCOPED, because the rule gained one explicit exception: a square PROMOTED from `all`-only to `clean`
    moves its date forward onto the platinum that earned the square. Both platinums here are clean, so
    this is the within-lens case, which is still absolute."""
    profile = _hunter()
    _platted(profile, _utc(2015, 4, 4))
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    _platted(profile, _utc(2024, 4, 4))
    calendar_fill.apply_to_run(run)

    assert run.calendar_days.get(month=4, day=4).earned_on == dt.date(2015, 4, 4)


def test_a_squares_date_comes_from_the_lens_that_draws_it():
    """THE DATE MUST NOT DESCRIBE A POPULATION THE BOARD EXCLUDES. This stored the earliest date across
    every lens holding a square -- right while three lenses rendered at once and each could reveal its
    own, and wrong the moment the collapse left ONE lens on the board with `in_all` kept as a counter.

    A hunter with a shovelware platinum on 2015-03-03 and a clean one on 2021-03-03 held
    `in_clean=True, earned_on=2015-03-03`, so the month's side column printed "First filled -- 3 Mar
    2015" on a board that counts nothing from 2015. A real date, on a real square, from a game the
    board's own rule throws out.
    """
    profile = _hunter()
    _platted(profile, _utc(2015, 3, 3), shovelware=True)
    _platted(profile, _utc(2021, 3, 3))
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    day = run.calendar_days.get(month=3, day=3)
    assert day.in_clean and day.in_all
    assert day.earned_on == dt.date(2021, 3, 3), (
        'the shovelware date won -- the square prints a year the board excludes')


def test_a_reclassified_game_does_not_re_date_the_square_it_filled():
    """A LENS'S OWN EARLIEST CAN RISE, which the first version of the lens fix assumed it could not. It
    took the offered date whenever it DIFFERED from the stored one, on the reasoning that a higher offer
    could only mean the stored value came from the other lens -- "`filled_days` reads the whole history
    every pass, so a lens's own earliest never rises".

    `update_shovelware` falsifies that on a routine nightly pass: flagging a previously-clean game drops
    its day out of the clean aggregate, so the clean lens's earliest for 3 March moves from 2015 to
    2021 with no promotion anywhere, and the square silently re-dated. That is the same cause the
    `offered is None` branch refuses to act on, so the two branches were applying opposite policies to
    one trigger.

    A DATE IS THE ONLY RECORD OF THE ACHIEVEMENT once a square is filled, and catalogue bookkeeping the
    hunter never saw must not rewrite it -- the same rule that makes fills monotone.
    """
    profile = _hunter()
    old = _platted(profile, _utc(2015, 7, 7))
    _platted(profile, _utc(2021, 7, 7))
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    assert run.calendar_days.get(month=7, day=7).earned_on == dt.date(2015, 7, 7)

    # A nightly shovelware pass flags the older game. Nothing the hunter did.
    old.shovelware_status = 'auto_flagged'
    old.save(update_fields=['shovelware_status'])
    calendar_fill.apply_to_run(run)

    day = run.calendar_days.get(month=7, day=7)
    assert day.in_clean, 'fills are monotone -- the square stays on the board'
    assert day.earned_on == dt.date(2015, 7, 7), (
        'the square re-dated itself because the clean aggregate lost its earliest day')


def test_the_square_records_how_many_platinums_sit_on_it():
    """THE MONTH PANEL'S "BUSIEST DAY" READS THIS (owner, 2026-10-04). Stored rather than counted at
    render time: the board is one read of `CalendarDay`, and a count means a second timezone-aware
    aggregate over the hunter's whole trophy history per rendered board -- eight of them on a Hall of
    Fame page. The fill already groups platinums by (month, day), so the count is one more annotation."""
    profile = _hunter()
    for year in (2016, 2019, 2024):
        _platted(profile, _utc(year, 8, 8))
    _platted(profile, _utc(2020, 8, 9))
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    assert run.calendar_days.get(month=8, day=8).plat_count == 3
    assert run.calendar_days.get(month=8, day=9).plat_count == 1
    # NOTHING SCATTERED ONTO THE OTHER SQUARES. Asserting `== 0` on one unfilled day pinned the FIELD
    # DEFAULT, not the writer: no code path reaches (8, 10), so it passed whatever the write branch did
    # -- including a writer keyed on the wrong day. Asking the month as a whole is the real question.
    assert not (run.calendar_days.filter(month=8, plat_count__gt=0)
                .exclude(day__in=(8, 9)).exists()), 'a count landed on a square no platinum touched'


def test_the_count_is_the_lens_that_draws_the_square():
    """THE SAME RULE `earned_on` FOLLOWS, so the two always describe one population. A square showing
    "first filled 2021" beside a count that included platinums the board excludes would be two answers
    to one question, which is the cross-lens defect the collapse was meant to end."""
    profile = _hunter()
    _platted(profile, _utc(2015, 9, 9), shovelware=True)
    _platted(profile, _utc(2019, 9, 9))
    _platted(profile, _utc(2021, 9, 9))
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    day = run.calendar_days.get(month=9, day=9)
    assert day.in_clean
    assert day.plat_count == 2, 'the shovelware platinum was counted on a board that excludes it'
    assert day.earned_on == dt.date(2019, 9, 9), 'and the date agrees with the same two'


def test_the_count_follows_the_aggregate_down_where_the_date_does_not():
    """THE ONE PLACE THE TWO FIELDS DISAGREE ON PURPOSE, pinned because it looks like an inconsistency.

    A date is the record of an ACHIEVEMENT, so it survives a game being reclassified as shovelware --
    catalogue bookkeeping the hunter never saw must not rewrite when they earned a square. A count is a
    TALLY, and the day modal will derive the same tally live from trophy rows when a hunter opens a day.
    A stored count drifting above the modal's list would contradict the thing the hunter just opened,
    which is worse than contradicting history they cannot check.
    """
    profile = _hunter()
    old = _platted(profile, _utc(2015, 10, 10))
    _platted(profile, _utc(2021, 10, 10))
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    assert run.calendar_days.get(month=10, day=10).plat_count == 2

    old.shovelware_status = 'auto_flagged'
    old.save(update_fields=['shovelware_status'])
    calendar_fill.apply_to_run(run)

    day = run.calendar_days.get(month=10, day=10)
    assert day.plat_count == 1, 'the count must track the population the board draws'
    assert day.earned_on == dt.date(2015, 10, 10), 'while the achievement date is frozen'


def test_a_square_whose_lens_empties_drops_its_count_to_zero():
    """THE CASE THE FIRST VERSION OF THE LIVE COUNT MISSED, and it is the one where a stale tally is most
    visible. The write only happened when the aggregate OFFERED a value, so a square whose lens went
    entirely empty kept its last count: flag every one of a hunter's platinums on a day and the square
    still draws -- fills are monotone, deliberately -- while the panel goes on printing "busiest day: 3"
    and the day modal, deriving its satisfiers live, lists nothing.

    THE SQUARE STAYS FILLED. That is not in question and must not change: an earned square is never
    retracted for catalogue bookkeeping. It is the TALLY that has to tell the truth, which is the whole
    reason this field is live rather than frozen.
    """
    profile = _hunter()
    games = [_platted(profile, _utc(year, 11, 11)) for year in (2017, 2019, 2022)]
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    assert run.calendar_days.get(month=11, day=11).plat_count == 3

    for game in games:
        game.shovelware_status = 'auto_flagged'
        game.save(update_fields=['shovelware_status'])
    calendar_fill.apply_to_run(run)

    day = run.calendar_days.get(month=11, day=11)
    assert day.in_clean, 'the square is still the hunter\'s -- fills are monotone'
    assert day.plat_count == 0, (
        'the panel would print a busiest day the day modal cannot list a single game for')
    assert day.earned_on == dt.date(2017, 11, 11), 'while the achievement date is still frozen'


def test_a_square_promoted_off_shovelware_moves_its_date_forward():
    """THE ONE CASE WHERE `earned_on` RISES, and it is the whole reason the backwards-only rule had to
    gain an exception rather than the lens fix being bolted on in the renderer. A square held only by a
    shovelware platinum carries that platinum's date, correctly: it is the only date the square has, and
    it never prints because the square does not draw. When a clean platinum later lands on the same day
    the square is promoted onto the board, and its date has to travel with it -- off the excluded
    platinum and onto the one that earned the square.

    A square's own lens still only moves BACKWARDS: `filled_days` recomputes the whole history every
    pass, so the earliest a lens knows never rises, which is what makes "take the offer" correct in both
    directions without storing which lens wrote the value.
    """
    profile = _hunter()
    _platted(profile, _utc(2015, 5, 5), shovelware=True)
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    off_board = run.calendar_days.get(month=5, day=5)
    assert off_board.in_all and not off_board.in_clean
    assert off_board.earned_on == dt.date(2015, 5, 5), 'the only date the square has'

    _platted(profile, _utc(2021, 5, 5))
    calendar_fill.apply_to_run(run)

    promoted = run.calendar_days.get(month=5, day=5)
    assert promoted.in_clean
    assert promoted.earned_on == dt.date(2021, 5, 5), (
        'promoted onto the board still dated from the platinum the board excludes')


def test_a_no_op_pass_writes_nothing():
    """THE NIGHTLY SWEEP RUNS OVER EVERY CALENDAR RUN. `updated_at` is `auto_now` and
    `Challenge.Meta.ordering` is `-updated_at`, so an unconditional save would issue a pointless UPDATE
    per run per night, churn the `(profile, -updated_at)` partial index, and float every Calendar run to
    the top of "my challenges, newest first" every morning regardless of activity.

    `challenge_service._recount` gets this for free by only ever running behind a row write; this one is
    called speculatively, so it has to earn its write.
    """
    profile = _hunter()
    _platted(profile, _utc(2019, 5, 5))
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    run.refresh_from_db()
    before = run.updated_at

    calendar_fill.apply_to_run(run)

    run.refresh_from_db()
    assert run.updated_at == before, (
        'a pass that changed nothing still wrote the run, bumping it up the hunter\'s list')


def test_a_clean_day_without_an_all_day_is_repaired_rather_than_raising(monkeypatch):
    """THE BELT AND BRACES, and it is what actually protects the scheduled job.

    `calendarday_clean_implies_all` forbids `in_clean` without `in_all`. Both platinum views now come
    from ONE statement, so `clean` is a strict subset of `all` by construction -- but that is a
    RACE-SAFETY property and no deterministic test can observe a race. What a test can observe is the
    writer's behaviour if the predicates ever hand it that pair anyway: it must repair the row, not
    raise an IntegrityError out of a nightly sweep and leave the hunter's calendar silently unfilled.

    So this forces the shape directly rather than trying to provoke it.
    """
    profile = _hunter()
    run = _calendar_run(profile)

    # `DayFill`, NOT A BARE DATE, because the fake has to be a value the real aggregate could return.
    # A fabricated shape that the writer merely tolerates tests the fake, not the writer.
    monkeypatch.setattr(calendar_fill, 'filled_days', lambda _p: {
        CALENDAR_VIEW_ALL: {},
        CALENDAR_VIEW_CLEAN: {(6, 6): calendar_fill.DayFill(dt.date(2019, 6, 6), 1)},
    })

    calendar_fill.apply_to_run(run)

    day = run.calendar_days.get(month=6, day=6)
    assert day.in_clean
    assert day.in_all, (
        'a clean-without-all pair was written through instead of repaired, which the database forbids')


# ── the reconciliation check ─────────────────────────────────────────────────────────────────────

def test_a_run_whose_numbers_have_not_moved_is_not_due():
    """THE CHEAP QUESTION, and the whole reason the sweep is affordable.

    Recomputing a hunter's platinum history is the expensive thing -- around ninety queries for someone
    with three thousand earned contracts -- so the sweep must not do it speculatively. A run whose
    platinum count and earned-contract count are both unchanged cannot have gained a day, so it is
    skipped without reading a single trophy.
    """
    profile = _hunter()
    _platted(profile, _utc(2019, 3, 3))
    profile.total_plats = 1
    profile.save(update_fields=['total_plats'])
    run = _calendar_run(profile)

    assert run in calendar_fill.runs_due_for_sweep(), 'a never-swept run must be due'

    calendar_fill.apply_to_run(run)

    assert run not in calendar_fill.runs_due_for_sweep(), (
        'a run was still due immediately after being swept, so every night would recompute it')


def test_a_sync_that_earns_no_platinum_does_not_make_a_run_due():
    """THE OBJECTION THIS REPLACED. Scoping on `last_synced` catches every hunter who opened the app,
    and a sync earns a platinum only occasionally -- most carry bronzes, silvers and golds, none of
    which can fill a calendar day. That is a full history recomputation per account per night to
    discover nothing."""
    profile = _hunter()
    _platted(profile, _utc(2019, 3, 3))
    profile.total_plats = 1
    profile.save(update_fields=['total_plats'])
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    # They sync, earn a pile of bronzes, and plat nothing.
    profile.last_synced = timezone.now()
    profile.save(update_fields=['last_synced'])

    assert run not in calendar_fill.runs_due_for_sweep(), (
        'syncing with no new platinum made the run due for a full recomputation')


def test_a_new_platinum_makes_a_run_due_again():
    profile = _hunter()
    _platted(profile, _utc(2019, 3, 3))
    profile.total_plats = 1
    profile.save(update_fields=['total_plats'])
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    _platted(profile, _utc(2019, 9, 9))
    profile.total_plats = 2
    profile.save(update_fields=['total_plats'])

    assert run in calendar_fill.runs_due_for_sweep()


def test_a_contract_completion_no_longer_makes_a_run_due():
    """THE SECOND WATERMARK IS GONE, and this test records what it was for rather than being deleted.

    A `calendar_contracts_seen` counter sat beside the platinum one, and the sweep compared BOTH against
    live values, because a CONTRACTS day could move without a platinum: `contract_service._detect_tiers`
    reaches the 100% tier from `progress=100` with no platinum term. So completing a contract had to make
    a run due even when the hunter's platinum count had not budged, and the sweep paid a `Subquery`
    annotation counting `EarnedContract` rows on every row to notice.

    One lens means a day needs a platinum, full stop. A contract completion with no platinum behind it
    now changes nothing a Calendar board can draw, so leaving the run un-due is correct -- and the
    counter, the annotation and the column went with the question.
    """
    profile = _hunter()
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)
    assert run not in calendar_fill.runs_due_for_sweep(), 'precondition: nothing has moved'

    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    game = GameFactory(concept=concept)
    ProfileGame.objects.create(profile=profile, game=game, has_plat=False, progress=100,
                               most_recent_trophy_date=_utc(2020, 4, 4))
    contract = Contract.objects.create(
        name='Later', slug='later-contract', is_live=True, igdb_id=880_004)
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=False)

    assert run not in calendar_fill.runs_due_for_sweep(), (
        'a contract completion with no platinum cannot change a one-lens board, so it must not '
        'make the run due')


def test_the_due_check_is_one_query_however_many_runs_exist():
    """It runs nightly over every Calendar run site-wide, so it must not be a query per run -- the
    expensive part is what it decides to skip, not the deciding."""
    for i in range(4):
        p = _hunter()
        _platted(p, _utc(2019, 1, i + 1))
        _calendar_run(p)

    with CaptureQueriesContext(connection) as captured:
        list(calendar_fill.runs_due_for_sweep())

    assert len(captured.captured_queries) == 1, (
        'the due check cost %d queries for four runs' % len(captured.captured_queries))


def test_a_finished_run_is_not_swept_again():
    """THIS REVERSES AN EXCEPTION, and the reversal is the point rather than a tightening.

    The sweep deliberately did NOT scope to unfinished runs, against the house convention, because a
    Calendar run completed on the FIRST of two genuine lenses to fill and the hunter could still be
    working toward the other one's ultimate title. Freezing a finished run removed the only path to it.

    One lens removes the second destination. A complete run is 365 shovelware-free days, and `in_all` is
    a superset of `in_clean`, so there is no column left that can move: sweeping a finished run could
    only rewrite it with itself.
    """
    profile = _hunter()
    run = _calendar_run(profile)
    run.is_complete = True
    run.completed_at = timezone.now()
    run.save(update_fields=['is_complete', 'completed_at'])

    _platted(profile, _utc(2019, 10, 31))
    profile.total_plats = 1
    profile.save(update_fields=['total_plats'])

    assert run not in calendar_fill.runs_due_for_sweep(), (
        'a finished run has nothing left to fill, so sweeping it is wasted work')


def test_a_hidden_run_keeps_filling_exactly_as_a_hidden_a_z_run_does():
    """CONSISTENCY WITH THE OTHER TYPES, which is what settled this rather than preference.

    `pending_slots` -- the detection every other challenge type goes through -- filters only
    `challenge__is_complete=False, is_completed=False` and says nothing about `is_deleted`. So a hidden
    A-Z run has always kept completing its squares. Hiding in this feature means "not shown", not
    "frozen": `hide`'s own docstring is explicit that nothing is destroyed and `start` brings the run
    back.

    AN UNFINISHED RUN IS THE SUBJECT, and it used to be a finished one. That was right while a finished
    Calendar run still had somewhere to go -- the lens it had not completed -- so freezing it removed the
    only path to that lens's ultimate. With one lens a finished run genuinely has nothing left to fill
    and is now out of the sweep's scope by design, so testing hiding through a finished run would assert
    the completeness rule and say nothing about hiding.
    """
    from challenges.services import challenge_service as svc

    profile = _hunter()
    run = _calendar_run(profile)

    svc.hide(run, profile)
    run.refresh_from_db()
    assert run.is_deleted, 'precondition: the run is hidden'
    assert not run.is_complete, 'precondition: and unfinished, so only hiding is under test'

    # The hunter keeps playing. Their hidden run must keep filling.
    _platted(profile, _utc(2019, 10, 31))
    profile.total_plats = 1
    profile.save(update_fields=['total_plats'])

    assert run in calendar_fill.runs_due_for_sweep(), 'a hidden run stopped being swept'
    assert calendar_fill.refresh_for_profile(profile) == 1, 'the hook skipped a hidden run'
    assert run.calendar_days.get(month=10, day=31).in_all, (
        'a hidden run stopped filling, which removes the only path to its other view\'s ultimate')


def test_hiding_a_finished_run_still_takes_it_off_the_public_board():
    """THE CAPABILITY THAT WAS NEARLY TRADED AWAY. Excluding finished runs from hiding would have fixed
    the freeze above by removing a hunter's only way to take a finished run off the Hall of Fame --
    which uses `visible()` -- and out of their own history. This pins that the fix did not cost it."""
    from challenges.models import Challenge
    from challenges.services import challenge_service as svc

    profile = _hunter()
    run = _calendar_run(profile)
    run.is_complete = True
    run.completed_at = timezone.now()
    run.save(update_fields=['is_complete', 'completed_at'])

    assert Challenge.objects.visible().filter(pk=run.pk).exists()

    svc.hide(run, profile)

    assert not Challenge.objects.visible().filter(pk=run.pk).exists(), (
        'a finished run could no longer be taken off the public board')
