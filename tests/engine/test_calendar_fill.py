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
    CALENDAR_VIEW_CONTRACTS,
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
    assert days[CALENDAR_VIEW_ALL][(3, 3)] == dt.date(2019, 3, 3)


def test_a_day_records_the_FIRST_time_it_was_filled():
    """A hunter who platted on 3 March in 2019 and again in 2024 filled that square in 2019. The later
    one is not wrong, it is just not the moment the square was earned."""
    profile = _hunter()
    _platted(profile, _utc(2024, 3, 3))
    _platted(profile, _utc(2019, 3, 3))

    days = calendar_fill.filled_days(profile)

    assert days[CALENDAR_VIEW_ALL][(3, 3)] == dt.date(2019, 3, 3)


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
    assert tokyo_days[(3, 3)] == dt.date(2021, 3, 3), (
        'the stored date was resolved in a different timezone from the day key')
    assert london_days[(3, 2)] == dt.date(2021, 3, 2)


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

    assert days[CALENDAR_VIEW_ALL][(2, 28)] == dt.date(2015, 2, 28)


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

def test_the_contracts_view_is_keyed_on_the_contract_not_the_platinum():
    profile = _hunter()
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    _platted(profile, _utc(2019, 9, 9), concept=concept)

    contract = Contract.objects.create(
        name='Contracted', slug='contracted', is_live=True, igdb_id=880_001)
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=True)

    days = calendar_fill.filled_days(profile)

    assert (9, 9) in days[CALENDAR_VIEW_CONTRACTS]


def test_a_platinum_with_no_contract_fills_no_contracts_day():
    """The contracts view is the CURATED pool, so an uncontracted platinum fills the other two views
    and not this one. Without this the view could silently be a copy of `all`."""
    profile = _hunter()
    _platted(profile, _utc(2019, 10, 10))

    days = calendar_fill.filled_days(profile)

    assert (10, 10) in days[CALENDAR_VIEW_ALL]
    assert (10, 10) in days[CALENDAR_VIEW_CLEAN]
    assert days[CALENDAR_VIEW_CONTRACTS] == {}


def test_the_contracts_view_reuses_the_one_completion_date_rule(monkeypatch):
    """IT GOES THROUGH `eligibility.completion_dates`, which is the DRY risk this slice was flagged for.

    That function already answers "when did this hunter FIRST complete this contract": both tiers the
    contract engine reads, the earliest qualifying moment, five bounded queries. A second derivation
    here would give the Calendar its own quietly different definition of "completed, and when", and the
    two would drift the first time either tier's rule moved -- which is exactly the shape of the bug the
    shared disciplines-ring helper was extracted to prevent.

    Asserted by observing the CALL rather than the result, because a copy-pasted derivation would
    produce the same answer today and diverge later, which is the failure this is guarding.
    """
    from challenges.services import eligibility

    calls = []
    real = eligibility.completion_dates

    def spy(profile, contracts):
        calls.append(list(contracts))
        return real(profile, contracts)

    monkeypatch.setattr(eligibility, 'completion_dates', spy)

    profile = _hunter()
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    _platted(profile, _utc(2019, 11, 11), concept=concept)
    contract = Contract.objects.create(
        name='Spied', slug='spied', is_live=True, igdb_id=880_002)
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=True)

    calendar_fill.filled_days(profile)

    assert calls, 'the contracts view derived its own completion dates instead of reusing the rule'


# ── whale safety ─────────────────────────────────────────────────────────────────────────────────

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

    assert days == {CALENDAR_VIEW_ALL: {}, CALENDAR_VIEW_CLEAN: {}, CALENDAR_VIEW_CONTRACTS: {}}


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


def test_a_run_completes_on_a_genuine_view_and_records_which():
    profile = _hunter()
    run = _calendar_run(profile)
    run.calendar_days.update(in_all=True, in_clean=True)

    calendar_fill._recount_calendar(run)
    run.refresh_from_db()

    assert run.is_complete and run.completed_at is not None
    assert run.completed_view == CALENDAR_VIEW_CLEAN
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
    assert run.completed_view == ''
    assert run.filled_count == 0


def test_contracts_wins_the_tie_when_a_backfill_completes_both_views_at_once():
    """A tie is REACHABLE, not hypothetical: an opening backfill evaluates all three views in one pass,
    so a hunter who qualifies for both finishes both in the same instant and "whichever came first"
    decides nothing. The rarer, harder achievement is the more honest snapshot for the board."""
    profile = _hunter()
    run = _calendar_run(profile)
    run.calendar_days.update(in_all=True, in_clean=True, in_contracts=True)

    calendar_fill._recount_calendar(run)
    run.refresh_from_db()

    assert run.completed_view == CALENDAR_VIEW_CONTRACTS


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


def test_earned_on_never_moves_forward():
    """The correction above only ever runs BACKWARDS. A square was earned when it was first earned, and
    a newer platinum on the same day does not re-date it."""
    profile = _hunter()
    _platted(profile, _utc(2015, 4, 4))
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    _platted(profile, _utc(2024, 4, 4))
    calendar_fill.apply_to_run(run)

    assert run.calendar_days.get(month=4, day=4).earned_on == dt.date(2015, 4, 4)


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


def test_a_contracts_day_that_is_not_a_platinum_day_is_allowed():
    """THE ROW SHAPE THE THREE-BOOLEAN DESIGN EXISTS FOR, and nothing wrote it before.

    A contract reaches its 100% tier from `progress=100` with no platinum anywhere, so a contracts day
    need not be a platinum day. That is the whole structural reason `contracts` nests under neither other
    view and the reason a single `best_view` column could not represent a day. Every previous fixture
    gave its contract a platinum too, so the case was argued in comments and asserted nowhere.
    """
    profile = _hunter()
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    game = GameFactory(concept=concept)
    # 100% with NO platinum trophy on the game at all.
    ProfileGame.objects.create(
        profile=profile, game=game, has_plat=False, progress=100,
        most_recent_trophy_date=_utc(2019, 12, 12))

    contract = Contract.objects.create(
        name='Platless', slug='platless', is_live=True, igdb_id=880_003)
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=False)

    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    day = run.calendar_days.get(month=12, day=12)
    assert day.in_contracts, 'a platinum-less contract completion filled no contracts day'
    assert not day.in_all, 'it is not a platinum, so it must not fill the all-platinums view'
    assert not day.in_clean


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

    monkeypatch.setattr(calendar_fill, 'filled_days', lambda _p: {
        CALENDAR_VIEW_ALL: {},
        CALENDAR_VIEW_CLEAN: {(6, 6): dt.date(2019, 6, 6)},
        CALENDAR_VIEW_CONTRACTS: {},
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


def test_a_new_contract_completion_makes_a_run_due_without_a_platinum():
    """THE SECOND WATERMARK EARNS ITS PLACE. A contract reaches its 100% tier with no platinum term, so
    the contracts view can gain a day while `total_plats` does not move. One counter would miss it."""
    profile = _hunter()
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)
    assert run not in calendar_fill.runs_due_for_sweep()

    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    game = GameFactory(concept=concept)
    ProfileGame.objects.create(profile=profile, game=game, has_plat=False, progress=100,
                               most_recent_trophy_date=_utc(2020, 4, 4))
    contract = Contract.objects.create(
        name='Later', slug='later-contract', is_live=True, igdb_id=880_004)
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=False)

    assert run in calendar_fill.runs_due_for_sweep(), (
        'a contract completion with no platinum left the run un-due, so its day would never fill')


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


def test_a_finished_run_still_sweeps():
    """INVERTS THE HOUSE CONVENTION, deliberately. Elsewhere a finished run is never written to again.
    A Calendar run completes on the FIRST genuine view to fill, and the hunter may still be working
    toward the other view's ultimate title -- so its days must keep filling afterwards."""
    profile = _hunter()
    run = _calendar_run(profile)
    run.is_complete = True
    run.completed_at = timezone.now()
    run.completed_view = CALENDAR_VIEW_CLEAN
    run.save(update_fields=['is_complete', 'completed_at', 'completed_view'])

    profile.total_plats = 5
    profile.save(update_fields=['total_plats'])

    assert run in calendar_fill.runs_due_for_sweep(), (
        'a finished Calendar run stopped sweeping, so its other view can never reach its ultimate')


# ── the refresh command ──────────────────────────────────────────────────────────────────────────

def _run_command(**kwargs):
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command('process_challenges', stdout=out, **kwargs)
    return out.getvalue()


def test_the_command_refreshes_one_hunters_calendar_on_demand():
    """THE MANUAL DOOR. A staff member asking for one hunter's calendar to be refreshed usually suspects
    something is wrong, so this must do the work rather than consult the optimisation that exists to
    avoid work."""
    profile = _hunter()
    _platted(profile, _utc(2019, 3, 3))
    run = _calendar_run(profile)

    out = _run_command(user=profile.psn_username, only='calendar')

    assert run.calendar_days.get(month=3, day=3).in_all, 'the command filled nothing'
    assert '+1' in out and profile.psn_username in out


def test_the_named_hunter_is_refreshed_even_when_the_due_check_says_no():
    """`--user` IGNORES THE RECONCILIATION DELIBERATELY. The watermarks are an optimisation for the
    nightly pass; the manual door exists for the case where they are WRONG, so obeying them would make
    it useless in exactly the situation it is for."""
    profile = _hunter()
    _platted(profile, _utc(2019, 4, 4))
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    assert run not in calendar_fill.runs_due_for_sweep(), 'precondition: the run is not due'

    # Corrupt the stored state the way a bug would, then ask for a refresh by hand.
    run.calendar_days.update(in_all=False, in_clean=False, earned_on=None)

    _run_command(user=profile.psn_username, only='calendar')

    assert run.calendar_days.get(month=4, day=4).in_all, (
        'the manual refresh obeyed the due check and so repaired nothing')


def test_the_nightly_pass_skips_runs_that_are_not_due():
    profile = _hunter()
    _platted(profile, _utc(2019, 5, 5))
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    out = _run_command(only='calendar')

    assert 'No calendar run needs refreshing.' in out, out


def test_a_dry_run_writes_nothing():
    profile = _hunter()
    _platted(profile, _utc(2019, 6, 6))
    run = _calendar_run(profile)

    out = _run_command(user=profile.psn_username, only='calendar', dry_run=True)

    assert 'would refresh' in out
    assert not run.calendar_days.filter(in_all=True).exists(), 'a dry run wrote fills'


def test_one_hunters_bad_data_does_not_stop_the_sweep(monkeypatch):
    """A nightly pass over every calendar must not stop at the first hunter with odd data -- the same
    reason the square loop is per-square rather than one enclosing transaction."""
    good = _hunter()
    _platted(good, _utc(2019, 7, 7))
    good.total_plats = 1
    good.save(update_fields=['total_plats'])
    good_run = _calendar_run(good)

    bad = _hunter()
    bad.total_plats = 1
    bad.save(update_fields=['total_plats'])
    bad_run = _calendar_run(bad)

    real = calendar_fill.apply_to_run

    def explode(run):
        if run.pk == bad_run.pk:
            raise RuntimeError('odd data')
        return real(run)

    monkeypatch.setattr(calendar_fill, 'apply_to_run', explode)

    out = _run_command(only='calendar')

    assert 'FAILED' in out
    assert good_run.calendar_days.get(month=7, day=7).in_all, (
        'one hunter failing stopped the sweep before reaching the next'
    )


# ── the sync hook ────────────────────────────────────────────────────────────────────────────────

def test_refreshing_a_profile_fills_every_run_it_owns_from_one_history_pass():
    """THE HOOK'S ACTUAL BEHAVIOUR, which had no test at all.

    The sync hook was five inline lines in a 400-line method, and the only thing covering them was a
    substring search of the module source -- which an audit proved passes against a hook that is
    commented out, emptied, or wrapped in `if False`. Nothing in the suite failed when the hook did
    nothing. The logic is a named function now so a test can call it.

    ONE HISTORY PASS FOR ALL OF A HUNTER'S RUNS, which is the other half: a hunter can own a finished
    Calendar run and an unfinished one, the fill set depends on the hunter rather than the run, and
    computing it per run repeats an identical ~90-query pass.
    """
    profile = _hunter()
    _platted(profile, _utc(2019, 2, 14))
    first = _calendar_run(profile)
    # A second run, which the partial unique permits once the first is finished.
    first.is_complete = True
    first.completed_at = timezone.now()
    first.completed_view = CALENDAR_VIEW_CLEAN
    first.save(update_fields=['is_complete', 'completed_at', 'completed_view'])
    second = _calendar_run(profile)

    with CaptureQueriesContext(connection) as captured:
        filled = calendar_fill.refresh_for_profile(profile)

    assert first.calendar_days.get(month=2, day=14).in_all, 'the finished run stopped filling'
    assert second.calendar_days.get(month=2, day=14).in_all, 'the active run was not filled'
    assert filled == 2, 'both runs gained the square, so both count: %d' % filled

    grouped = [q for q in captured.captured_queries
               if 'earnedtrophy' in q['sql'].lower() and 'GROUP BY' in q['sql'].upper()]
    assert len(grouped) == 1, (
        'the history pass ran %d times for %d runs -- it depends on the hunter, not the run'
        % (len(grouped), 2))


def test_one_runs_failure_does_not_cost_the_others_their_refresh(monkeypatch):
    """The nightly phase already works this way and the two paths must not disagree about it. On the
    sync path it matters more: a raise escaping here would be caught by the hook's own guard and cost
    the hunter every other run's refresh for that sync."""
    profile = _hunter()
    _platted(profile, _utc(2019, 2, 15))
    first = _calendar_run(profile)
    first.is_complete = True
    first.completed_at = timezone.now()
    first.completed_view = CALENDAR_VIEW_CLEAN
    first.save(update_fields=['is_complete', 'completed_at', 'completed_view'])
    second = _calendar_run(profile)

    real = calendar_fill.apply_to_run

    def explode(run, **kwargs):
        if run.pk == first.pk:
            raise RuntimeError('odd data')
        return real(run, **kwargs)

    monkeypatch.setattr(calendar_fill, 'apply_to_run', explode)

    calendar_fill.refresh_for_profile(profile)

    assert second.calendar_days.get(month=2, day=15).in_all, (
        'one run raising stopped the others being refreshed')


def test_a_hunter_with_no_calendar_run_costs_one_select():
    """The common case by far, and the hook runs for every linked hunter on every sync."""
    profile = _hunter()
    _platted(profile, _utc(2019, 2, 16))

    with CaptureQueriesContext(connection) as captured:
        assert calendar_fill.refresh_for_profile(profile) == 0

    assert len(captured.captured_queries) == 1, (
        'a hunter with no Calendar run cost %d queries' % len(captured.captured_queries))


def test_the_sync_hook_fills_a_calendar_without_consulting_the_watermark():
    """THE HOOK READS GROUND TRUTH; THE SWEEP GETS TO BE CHEAP.

    `runs_due_for_sweep` compares `Profile.total_plats` against a stored counter, and that column is
    maintained INCREMENTALLY by a `post_save` signal on `EarnedTrophy` -- fresh on the normal path, and
    able to lag anywhere a write slips past signals. `recalc_profile_counters`' own docstring names that
    case (`bulk_update`, `queryset.update`, a handler raising) and exists to rebuild it nightly.

    A stale counter costs the SWEEP a day of latency, which a safety net can afford. It would cost the
    HOOK correctness: the one path a hunter actually watches would skip the fill and show them an empty
    square after a platinum landed. So this asserts the hook fills a run the due-check would skip.
    """
    profile = _hunter()
    run = _calendar_run(profile)

    # A platinum lands and the counter DRIFTS, simulated with the mechanism that actually causes it:
    # a `queryset.update`, which bypasses the `post_save` signal that maintains the column. That is one
    # of the three cases `recalc_profile_counters`' docstring names. (Writing it any other way does not
    # work -- the signal fires in tests too, which is how this test found its own wrong premise.)
    from trophies.models import Profile

    _platted(profile, _utc(2019, 11, 11))
    Profile.objects.filter(pk=profile.pk).update(total_plats=0)
    profile.refresh_from_db()

    assert profile.total_plats == 0, 'fixture precondition: the counter is drifted low'
    assert run not in calendar_fill.runs_due_for_sweep(), (
        'precondition: the sweep would skip this run, which is the whole point')

    calendar_fill.apply_to_run(run)

    assert run.calendar_days.get(month=11, day=11).in_all, (
        'the hook path failed to fill a square the sweep would have skipped')


def test_the_sync_pipeline_refreshes_calendars_after_contract_detection():
    """ORDERING, pinned on the source because no unit test can run the sync pipeline.

    A calendar day fills from a platinum OR a contract completion, and the contract detection block is
    what creates the `EarnedContract` rows the third view reads. Hooked earlier, a hunter watching their
    own sync land would see the platinum arrive and the square stay empty until the nightly pass -- the
    same failure the square detection block documents for its own position.
    """
    source = (ROOT / 'trophies' / 'token_keeper.py').read_text(encoding='utf-8')

    # ANCHORED ON THE CALL, not on a log string. An audit mutated this three ways -- commenting the
    # hook out, emptying its body, wrapping it in `if False` -- and the old assertions passed against
    # all three, because a comment and a log message are still text in the right order. `refresh_for_
    # profile(profile)` with no leading `#` is the narrowest thing that has to be present and live.
    contracts = source.index('check_profile_contracts')
    squares = source.index('detect_for_profile(profile)')
    call = '\n                refresh_for_profile(profile)\n'
    assert source.count(call) == 1, 'the calendar hook is not an executable call at the expected depth'
    calendar = source.index(call)

    assert contracts < squares < calendar, (
        'the calendar refresh must follow contract detection, which creates the rows its third view '
        'reads')

    # ── AND IT IS REACHABLE, which no substring can tell you. A text pin sees `refresh_for_profile(
    # profile)` identically whether it runs or sits under `if False:` -- a mutation proved exactly that
    # against the previous version of this test. The AST can tell: find the call, walk its ancestors,
    # and fail if any enclosing branch is a constant-false test. It also pins WHICH function the hook
    # lives in, so moving it somewhere that never executes fails here rather than in production.
    import ast

    tree = ast.parse(source)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    sites = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == 'refresh_for_profile']
    assert len(sites) == 1, 'expected exactly one call to refresh_for_profile, found %d' % len(sites)

    enclosing_function = None
    node = sites[0]
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.If) and isinstance(node.test, ast.Constant) and not node.test.value:
            raise AssertionError(
                'the calendar hook sits inside a constant-false branch, so it never runs')
        if isinstance(node, ast.FunctionDef) and enclosing_function is None:
            enclosing_function = node.name

    assert enclosing_function == '_job_sync_complete', (
        'the calendar hook moved out of the sync-complete job and into %r, where it may never run on '
        'a sync' % enclosing_function)

    # AND IT CONTAINS ITS OWN FAILURES. A nightly sweep can afford to skip a hunter; a sync cannot
    # abort because one hunter's calendar is odd -- the squares and contracts blocks either side both
    # wrap for the same reason.
    tail = source[calendar - 600:calendar + 200]
    assert 'except Exception' in tail and 'logger.exception' in tail, (
        'the calendar hook does not contain its own failures, so one bad run could cost a hunter the '
        'rest of their sync')


def test_the_nightly_invocation_sweeps_calendars_on_a_night_with_no_completable_square():
    """THE ONE INVOCATION `nightly` MAKES, and the only one that was untested.

    `nightly`'s STEPS entry is `('challenge detection', 'process_challenges', {})` -- no arguments. The
    Calendar phase was first added as a trailing call at the end of `handle`, AFTER the slot sweep's own
    `return`s for "nothing to complete" and "dry run". Those returns mean "this phase is done"; at the
    top level of `handle` they meant "the command is done". So on any night where no square was
    completable -- the ordinary night -- the Calendar was never swept, and the whole reconciliation
    design was inert.

    Nothing failed. The command printed "Nothing to complete." and exited zero, exactly as it had before
    the Calendar existed. Every command test in the suite passed `only='calendar'`, so the default path
    had no coverage at all, which is why a sweep that never ran looked healthy.

    This test asserts the SQUARE case is empty and the Calendar still fills -- i.e. it fails against the
    structure that shipped.
    """
    from challenges.services import challenge_service as svc

    profile = _hunter()
    _platted(profile, _utc(2019, 8, 18))
    profile.total_plats = 1
    profile.save(update_fields=['total_plats'])
    run = _calendar_run(profile)

    assert not svc.completable_slots().exists(), (
        'fixture precondition: no square is completable, which is the ordinary night')

    out = _run_command()

    assert 'Nothing to complete.' in out, 'precondition: the slot phase found nothing'
    assert 'Plat Calendar refresh' in out, (
        'the calendar phase never ran on the nightly path -- the slot phase returned first')
    assert run.calendar_days.get(month=8, day=18).in_all, 'the nightly path filled nothing'


def test_a_dry_run_still_reports_the_calendar_phase():
    """Same bug, second door: `--dry-run` returned before the Calendar phase too, so staff previewing a
    night saw no Calendar section and would reasonably conclude there was nothing to refresh."""
    profile = _hunter()
    _platted(profile, _utc(2019, 8, 19))
    profile.total_plats = 1
    profile.save(update_fields=['total_plats'])
    run = _calendar_run(profile)

    out = _run_command(dry_run=True)

    assert 'Plat Calendar refresh' in out, 'a dry run hid the calendar phase entirely'
    assert 'would refresh' in out
    assert not run.calendar_days.filter(in_all=True).exists(), 'a dry run wrote fills'


def test_a_named_hunter_with_no_completable_square_still_gets_their_calendar_swept():
    """`--user X` hit the same return. A staff member asking for one hunter got silence and no refresh
    whenever that hunter happened to have no completable square -- which is most of the time."""
    profile = _hunter()
    _platted(profile, _utc(2019, 8, 20))
    profile.total_plats = 1
    profile.save(update_fields=['total_plats'])
    run = _calendar_run(profile)

    out = _run_command(user=profile.psn_username)

    assert 'Plat Calendar refresh' in out
    assert run.calendar_days.get(month=8, day=20).in_all


def test_all_calendars_refreshes_runs_the_due_check_would_skip():
    """THE DOOR FOR THE CHANGE NO WATERMARK CAN SEE.

    The reconciliation watches the hunter's platinum and earned-contract counts. A shovelware
    reclassification moves NEITHER while genuinely changing the `clean` view -- `auto_flagged -> clean`
    is a routine outcome of `update_shovelware`, and staff write `manually_cleared` by hand. So an
    un-flagged game's day fills only on that hunter's next platinum, and for a dormant hunter never.

    A third watermark was considered and rejected: the only cheap site-wide signal is "some game's flag
    moved tonight", which marks every run due and defeats the reconciliation on any night the detector
    touches anything. This is the staff door instead, and this test is what stops it rotting.
    """
    profile = _hunter()
    game = _platted(profile, _utc(2019, 9, 21), shovelware=True)
    profile.total_plats = 1
    profile.save(update_fields=['total_plats'])
    run = _calendar_run(profile)
    calendar_fill.apply_to_run(run)

    assert run.calendar_days.get(month=9, day=21).in_all
    assert not run.calendar_days.get(month=9, day=21).in_clean

    # Staff un-flag the game. Neither counter moves.
    game.shovelware_status = 'manually_cleared'
    game.save(update_fields=['shovelware_status'])

    assert run not in calendar_fill.runs_due_for_sweep(), (
        'precondition: the reconciliation cannot see a reclassification, which is why this door exists')
    _run_command(only='calendar')
    assert not run.calendar_days.get(month=9, day=21).in_clean, (
        'precondition: the ordinary sweep skipped it')

    _run_command(only='calendar', all_calendars=True)

    assert run.calendar_days.get(month=9, day=21).in_clean, (
        '--all-calendars did not refresh a run the due check skips')


def test_a_hidden_run_keeps_filling_exactly_as_a_hidden_a_z_run_does():
    """CONSISTENCY WITH THE OTHER TYPES, which is what settled this rather than preference.

    `pending_slots` -- the detection every other challenge type goes through -- filters only
    `challenge__is_complete=False, is_completed=False` and says nothing about `is_deleted`. So a hidden
    A-Z run has always kept completing its squares. Hiding in this feature means "not shown", not
    "frozen": `hide`'s own docstring is explicit that nothing is destroyed and `start` brings the run
    back.

    THE CALENDAR WAS THE ODD ONE OUT, and the damage was invisible for the other two types: a hidden
    UNFINISHED run resumes on Start, and a hidden FINISHED one has nothing left to fill. A finished
    CALENDAR run does have something left -- the view it did not complete on -- and freezing it removed
    the only path to that view's ultimate title, permanently, because `start` resumes hidden unfinished
    runs only.
    """
    from challenges.services import challenge_service as svc

    profile = _hunter()
    run = _calendar_run(profile)
    run.is_complete = True
    run.completed_at = timezone.now()
    run.completed_view = CALENDAR_VIEW_CLEAN
    run.save(update_fields=['is_complete', 'completed_at', 'completed_view'])

    svc.hide(run, profile)
    run.refresh_from_db()
    assert run.is_deleted, 'precondition: a finished run is still hideable, as it always has been'

    # The hunter keeps playing. Their hidden run must keep filling toward the other view's ultimate.
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
    run.completed_view = CALENDAR_VIEW_CLEAN
    run.save(update_fields=['is_complete', 'completed_at', 'completed_view'])

    assert Challenge.objects.visible().filter(pk=run.pk).exists()

    svc.hide(run, profile)

    assert not Challenge.objects.visible().filter(pk=run.pk).exists(), (
        'a finished run could no longer be taken off the public board')
