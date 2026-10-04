"""The three Plat Calendar view predicates, and the edges where a wrong one hides.

WHY THIS FILE IS MOSTLY EDGE CASES. The happy path ("a platinum fills its day") is one assertion and
cannot really be got wrong. What can be got wrong, silently, is everything around it: which timezone
decides the day, what happens on 29 February, whether the three views agree with each other where they
should and differ where they must, and whether the queries stay bounded for a hunter with a very large
library. Each of those produces a calendar that looks plausible and is wrong.
"""
import datetime as dt
import zoneinfo

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
    assert calendar_fill.apply_to_run(run) == 1, 'the square should have gained a view'

    day = run.calendar_days.get(month=8, day=8)
    assert day.in_clean, 'the square did not gain the clean view'
    assert day.filled_at == stamped, (
        'gaining a view re-dated the square to the day the catalogue was corrected')
