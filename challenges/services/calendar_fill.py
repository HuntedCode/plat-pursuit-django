"""Which calendar days a hunter has filled, and the one place the three views are defined.

THREE CONSUMERS ARE COMING -- the opening backfill, the sync hook and the day modal -- and the thing that
goes wrong when a rule like this has no home is that each of them derives it slightly differently. The
day modal listing games that "satisfy" a day has to agree, exactly, with the predicate that filled it, or
a hunter opens a filled day to an empty list. So the predicates live here and nothing re-derives them.

THE THREE VIEWS, and the one that is not what it looks like:

- `all`       every platinum the hunter has earned, by its local date.
- `clean`     the same, minus platinums on shovelware games.
- `contracts` the hunter's CONTRACT completions, keyed on the contract's own earliest qualifying
              moment -- which is NOT the same instant as any one platinum's. A contract reaches its
              100% tier from `progress=100` with no platinum anywhere, and a contract with several
              member concepts completes on the earliest of them.

So `clean` nests inside `all` (a shovelware-free platinum is still a platinum, and
`calendarday_clean_implies_all` enforces it) while `contracts` nests inside neither.

`hide_hiddens` IS IGNORED, deliberately, and the precedent is in the repo rather than in my judgement.
`Profile.total_trophies_raw` exists because ranking on a filter-respecting figure "makes the board
unreproducible by anyone but its owner" -- and the Hall of Fame is a board that Calendar runs land on.
If a hunter's display preference decided which days filled, two hunters with identical libraries would
get different runs and a finish would mean something different per row. It would also make the EASIEST
view stricter than the hardest, since the contract engine cannot see `user_hidden` at all.

WHALE SAFETY IS THE SHAPE OF EVERY QUERY HERE, not a note on them. These hunters run to 250,000+ earned
trophies, so the rule from `contract_service._detect_tiers` applies in full: never start from
`EarnedTrophy.filter(profile=...)` and join outward to test a property, because the planner then walks
every one of those rows. Resolve the catalogue-bounded side first and turn the aggregate into an index
seek. Every function below returns at most 365 rows.
"""
import logging
import zoneinfo

from django.db.models import Case, DateField, F, Min, Q, Value, When
from django.db.models.functions import ExtractDay, ExtractMonth, TruncDate
from django.utils import timezone

from challenges.models import CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN
from trophies.models import (
    SHOVELWARE_FLAGGED_STATUSES,
    EarnedTrophy,
    Profile,
    ProfileGame,
    Trophy,
)

logger = logging.getLogger(__name__)

#: 29 February folds into 28 February rather than being dropped.
#:
#: A run is keyed on (month, day) across every year, so there is no 29 February square -- but a platinum
#: really was earned on one, and discarding it loses a real achievement from a hunter's history for a
#: reason that is about the Gregorian calendar rather than about them. The retired system dropped it.
LEAP_DAY = (2, 29)
LEAP_DAY_FOLDS_TO = (2, 28)


def _hunter_timezone(profile):
    """The hunter's own timezone, which is what decides WHICH DAY a platinum landed on.

    Not the viewer's and not the server's: a platinum at 23:40 UTC on 2 March was earned on the 3rd by
    somebody in Tokyo and the 2nd by somebody in London, and the person it belongs to is the one whose
    calendar it fills. `CalendarDay.earned_on` stores the resolved local DATE for the same reason -- an
    instant would be re-interpreted by whoever happens to be reading the public run page.

    Falls back to UTC, which is also the field's own default, so a profile with no user row (fixtures,
    an orphaned profile) resolves rather than raising.
    """
    name = getattr(getattr(profile, 'user', None), 'user_timezone', None) or 'UTC'
    try:
        return zoneinfo.ZoneInfo(name)
    except Exception:
        # A stored timezone that the system tzdata no longer knows. Rare, and not worth failing a
        # backfill over -- UTC is the field default and the error is worth seeing.
        logger.warning('unknown timezone %r on profile %s, falling back to UTC', name, profile.pk)
        return zoneinfo.ZoneInfo('UTC')


def _fold(month, day):
    """`(2, 29)` -> `(2, 28)`; everything else unchanged."""
    return LEAP_DAY_FOLDS_TO if (month, day) == LEAP_DAY else (month, day)


def _platinum_trophies(profile, *, clean_only=False):
    """An UNEVALUATED queryset of the platinum Trophy ids worth asking `EarnedTrophy` about.

    RETURNED AS A SUBQUERY, NOT A LIST, and the difference is the whole whale story. An earlier version
    pulled `ProfileGame` ids into Python, then trophy ids into Python, then sent the second list back as
    an `IN` clause -- and its comment claimed the second step was "bounded by the catalogue". It is not:
    both lists are bounded by the hunter's PLATTED GAMES. For a serial platter that is ten to seventeen
    thousand elements, built twice per pass, sent across the wire as SQL text. Not an OOM (comfortably
    under Postgres's 65,535 bind parameters) and invisible to `profile_render`, which ranks allocation
    sites -- it is the planner that pays, in a shape the project's own diagnostic cannot see.

    Left unevaluated, Postgres plans it as a semi-join and nothing crosses the wire. The small-side-first
    idiom this was copied from (`contract_service._detect_tiers`, `eligibility.completion_dates`) is
    still right there, where the `IN` list is one contract's member concepts -- a handful. It does not
    transfer unchanged to library scale.

    `has_plat` is a denormalized boolean with no date on it, which is exactly why it is useful here and
    useless alone: it narrows which games to ask about, and `EarnedTrophy` supplies the when.
    """
    games = ProfileGame.objects.filter(profile=profile, has_plat=True).values('game_id')
    trophies = Trophy.objects.filter(game_id__in=games, trophy_type='platinum')
    if clean_only:
        trophies = trophies.exclude(game__shovelware_status__in=SHOVELWARE_FLAGGED_STATUSES)
    return trophies.values('id')


def _days_from_platinums(profile, tz):
    """`{'all': {(month, day): date}, 'clean': {...}}` -- BOTH platinum views, from ONE statement.

    ONE QUERY FOR BOTH, AND THAT IS A CORRECTNESS FIX RATHER THAN A SAVING. Computing them separately
    meant two sequences of reads outside any transaction, and Postgres is READ COMMITTED, so each
    statement takes a fresh snapshot. A platinum committed between the `all` read and the `clean` read --
    exactly what a concurrent sync does, and `token_keeper` refreshes `ProfileGame` stats immediately
    before calling the challenge hook -- lands in `clean` and not in `all`. The writer would then set
    `in_clean` on a row with `in_all` false, and `calendarday_clean_implies_all` would raise an
    `IntegrityError` out of a scheduled job. One snapshot makes `clean` a strict subset of `all` by
    construction instead of by timing.

    GROUPED IN THE DATABASE, in the hunter's timezone, per the project's per-user aggregation rule: a
    whale has hundreds of platinums and building this dict by iterating them is the shape that has cost
    this codebase a worker more than once. `ExtractMonth` and `TruncDate` both take a `tzinfo`, so
    Postgres does the conversion and at most 365 rows come back.

    `Min` rather than any: a square's date is the FIRST time it was filled. A hunter who platted on
    3 March in 2019 and again in 2024 earned that square in 2019.
    """
    clean_date = Case(
        When(trophy_id__in=_platinum_trophies(profile, clean_only=True),
             then=TruncDate('earned_date_time', tzinfo=tz)),
        default=Value(None),
        output_field=DateField(),
    )

    rows = (
        EarnedTrophy.objects
        .filter(profile=profile, earned=True,
                trophy_id__in=_platinum_trophies(profile),
                earned_date_time__isnull=False)
        .annotate(m=ExtractMonth('earned_date_time', tzinfo=tz),
                  d=ExtractDay('earned_date_time', tzinfo=tz))
        .values('m', 'd')
        .annotate(first=Min(TruncDate('earned_date_time', tzinfo=tz)),
                  clean_first=Min(clean_date))
        .values_list('m', 'd', 'first', 'clean_first')
    )

    out = {CALENDAR_VIEW_ALL: {}, CALENDAR_VIEW_CLEAN: {}}
    for month, day, first, clean_first in rows:
        key = _fold(month, day)
        # The fold collides a real 28 February with a folded 29 February, so the earlier date owns the
        # square. A true `min`, so it does not depend on which row Postgres returns first.
        for view, value in ((CALENDAR_VIEW_ALL, first), (CALENDAR_VIEW_CLEAN, clean_first)):
            if value is None:
                continue
            if key not in out[view] or value < out[view][key]:
                out[view][key] = value
    return out


def filled_days(profile):
    """`{key: {(month, day): earliest local date}}` for the two stored populations.

    The single entry point. Returns at most 365 entries each, and every date is already resolved in the
    hunter's own timezone, so a caller never has to know about timezones again.

    ONE STATEMENT PRODUCES BOTH, which is why `in_all` survives the one-lens collapse: it is free. The
    clean population is the all population minus shovelware, and `_days_from_platinums` reads them
    together with a conditional aggregate so the nesting is true by construction rather than by two
    queries agreeing.

    IT USED TO RETURN A THIRD, from `_days_from_contracts`, and that was the EXPENSIVE one: it walked
    `EarnedContract` to a contract list, chunked it, and put every chunk through
    `eligibility.completion_dates` (five bounded queries apiece). The collapse to one lens removed the
    whole predicate, so the fill is now exactly the single platinum statement.
    """
    return _days_from_platinums(profile, _hunter_timezone(profile))


def apply_to_run(challenge, *, found=None):
    """Write this hunter's filled days onto a Calendar run, and finish it if a genuine view is full.

    Returns the number of days this pass NEWLY FILLED -- squares that held no view before and hold one
    now -- so a caller can say "you start at 154 days" without counting again. A square that merely
    gained a second view is not a new day and does not count toward it.

    REFUSES ANYTHING THAT IS NOT A CALENDAR RUN, and that guard is not defensive tidiness. The recount
    below aggregates over `calendar_days`, which is EMPTY for an A-Z or Job Coverage run -- so without
    this, a sweep written as `for c in Challenge.objects.active(): apply_to_run(c)` would write
    `filled_count = 0` onto every hunter's 19-of-26 A-Z run, site-wide, in one pass. No constraint
    objects to `0 <= 0 <= 26`, so it would be silent, and it would self-heal only on that run's next
    assign or completion. `challenge_service._recount` has the mirror-image hazard from the other side.

    UNDER THE RUN'S ROW LOCK, matching `mark_slot_completed`, because two callers can arrive at once: the
    sync hook fires whenever a hunter syncs and the nightly sweep runs unscoped. Without the lock three
    things go wrong, and the third is the worst: both passes stamp `completed_at` and fire the reward
    hook twice; `just_completed` is decided against a possibly stale in-memory instance; and two passes
    that read the same rows and write all five columns back can RETRACT each other's fills, breaking the
    monotone guarantee below by concurrency rather than by a predicate.

    FILLS ARE MONOTONE: a day that is true is never set back to false. The predicates can legitimately
    stop matching -- a game is reclassified as shovelware, `reconcile_contracts` deletes an
    `EarnedContract` when derived membership moves, a staff `igdb_id` edit changes who qualifies -- and
    none of those are things the hunter did. Un-filling on any of them would retract an earned square for
    catalogue bookkeeping they never saw, which is the same rule `ChallengeSlot` follows by snapshotting
    its contract. So this ORs the new state onto the old.

    `earned_on` IS CORRECTED ON EVERY PASS, not only when a view flips. PSN returns titles by recency, so
    a later sync can legitimately discover an OLDER platinum on an already-filled square -- a deep
    backfill, or a re-queued `sync_trophies` reaching games it had not seen. Skipping unchanged rows
    would leave that square permanently claiming the later date while the day modal, which derives its
    satisfier list live, lists the older game. That is the same "a square labelled X holding Y"
    contradiction the `DateField` was chosen to rule out, reached from the other direction. It only ever
    moves BACKWARDS.
    """
    from django.db import transaction

    from challenges.models import CALENDAR_VIEW_FIELDS, CHALLENGE_TYPE_CALENDAR, CalendarDay, Challenge

    if challenge.challenge_type != CHALLENGE_TYPE_CALENDAR:
        return 0

    # THE WATERMARKS ARE READ BEFORE THE FILLS, and the order is the difference between a redundant
    # sweep and a permanently missing day. Reading them AFTER (which is how this shipped) lets them
    # describe state the fills do not include: a platinum landing during the ~90 queries `filled_days`
    # takes for a whale is counted by the watermark and absent from `found`, so the next sweep sees
    # "nothing moved" and the day is never filled. Read first, the worst case is `watermark <= reality`,
    # which costs one extra sweep and loses nothing.
    # READ FROM THE DATABASE, not through `challenge.profile`. That attribute is the CALLER's cached
    # relation -- the sync hook passes a `Challenge` it fetched earlier in the phase, and a signal has
    # bumped `total_plats` in the row since. Taking the cached value writes a watermark BELOW reality,
    # so the run reads as due forever and every sweep recomputes it: the optimisation inverted.
    plats_seen = (Profile.objects.filter(pk=challenge.profile_id)
                  .values_list('total_plats', flat=True).first() or 0)

    # `found` IS ACCEPTED FROM THE CALLER because it depends only on the PROFILE, not the run. A hunter
    # can hold more than one Calendar run (runs are sequential, so the finished ones stay),
    # and computing it per run recomputes an identical ~90-query history pass for each. The two callers
    # that loop -- the sync hook and the nightly phase -- pass it in; a single-run caller omits it.
    if found is None:
        found = filled_days(challenge.profile)
    now = timezone.now()
    newly_filled = 0

    with transaction.atomic():
        # The lock, and everything that decides a write is read inside it.
        locked = (Challenge.objects.select_for_update()
                  .filter(pk=challenge.pk).first())
        if locked is None:
            return 0

        rows = list(CalendarDay.objects.filter(challenge=locked))
        to_update = []
        for row in rows:
            was_filled = any(getattr(row, field) for _v, field in CALENDAR_VIEW_FIELDS)
            changed = False
            for view, field in CALENDAR_VIEW_FIELDS:
                if (row.month, row.day) in found[view] and not getattr(row, field):
                    setattr(row, field, True)
                    changed = True

            # BELT AND BRACES ON `calendarday_clean_implies_all`. One statement now reads both platinum
            # views, so `clean` cannot escape `all` by snapshot -- but this write is the only thing
            # standing between a future predicate change and an IntegrityError in a scheduled job, and
            # the cost of being sure is one comparison.
            if row.in_clean and not row.in_all:
                row.in_all = True
                changed = True

            # The earliest date across every view holding this square -- "whichever lens noticed" -- and
            # evaluated even when no view flipped, per the docstring.
            dates = [found[view][(row.month, row.day)]
                     for view, _f in CALENDAR_VIEW_FIELDS
                     if (row.month, row.day) in found[view]]
            earliest = min((d for d in dates if d is not None), default=None)
            if earliest is not None and (row.earned_on is None or earliest < row.earned_on):
                row.earned_on = earliest
                changed = True

            if not changed:
                continue
            if not was_filled:
                newly_filled += 1
            if row.filled_at is None:
                row.filled_at = now
            to_update.append(row)

        if to_update:
            CalendarDay.objects.bulk_update(
                to_update, ['in_all', 'in_clean', 'earned_on', 'filled_at'],
                batch_size=500)
        # THE WATERMARKS, written whether or not anything filled -- that is what makes the sweep's
        # cheap check work. Skipping them on a no-op would leave the run due every night forever.
        # Deliberately NOT `updated_at`, which must stay still on a no-op or every Calendar run floats
        # to the top of "my challenges" each morning.
        locked.calendar_plats_seen = plats_seen
        locked.save(update_fields=['calendar_plats_seen'])
        _recount_calendar(locked, wrote_rows=bool(to_update))

    return newly_filled


def _recount_calendar(challenge, *, wrote_rows=True):
    """The Calendar's counterpart to `challenge_service._recount`, and the shape is deliberately its.

    ONE AGGREGATE over the day rows, counters derived rather than incremented, and the completion stamp
    riding the same UPDATE.

    SILENT WHEN NOTHING MOVED, which `_recount` gets for free by only ever running behind a row write.
    This one is called by a nightly sweep over every Calendar run, and `updated_at` is `auto_now`, so an
    unconditional save would issue a pointless UPDATE per run per night, churn the
    `(profile, -updated_at)` partial index, and float every Calendar run to the top of "my challenges,
    newest first" each morning regardless of activity.

    The caller must hold the run's row lock; `apply_to_run` does.
    """
    # ONE FIGURE, AND IT IS THE CLEAN COUNT. This used to aggregate two counts, take the larger as the
    # progress number and record WHICH of them completed the run, because two different lenses could
    # each finish it. With one lens there is nothing to compare and nothing to record: a day is a
    # shovelware-free platinum, and the run is done when all 365 are.
    filled = challenge.calendar_days.filter(in_clean=True).count()

    just_completed = not challenge.is_complete and filled >= challenge.total_slots
    if not wrote_rows and filled == challenge.filled_count and not just_completed:
        return

    challenge.filled_count = filled
    challenge.completed_count = filled
    fields = ['filled_count', 'completed_count', 'updated_at']

    if just_completed:
        challenge.is_complete = True
        challenge.completed_at = timezone.now()
        fields += ['is_complete', 'completed_at']

    challenge.save(update_fields=fields)

    if just_completed:
        from challenges.services import rewards
        # Grants no title today -- the Calendar is in `TYPES_WITHOUT_ORDINAL_TITLES`, since a 365-day run
        # has no first/second-completion shape -- but it fires the `challenge_completed` notification,
        # which a hunter finishing one should get. That call contains its own failures (its title grant is
        # wrapped, its notification deferred to `on_commit(robust=True)`), so it cannot cost a hunter
        # their squares or abort a sweep. The per-view ultimates slot in here without changing the call.
        rewards.on_run_completed(challenge)


def runs_due_for_sweep():
    """Calendar runs whose numbers have actually MOVED since the sweep last looked. One query, site-wide.

    A RECONCILIATION CHECK, NOT A REFRESH. The expensive thing here is recomputing a hunter's whole
    platinum history -- around ninety queries for someone with three thousand earned contracts -- so the
    sweep asks a cheap question first and only pays for the runs that answer yes.

    THE CHEAP QUESTION IS NOT "HAS THIS HUNTER SYNCED". That was the first attempt and it is far too
    wide: `last_synced` moves on every sync, and a sync earns a platinum only occasionally -- most carry
    bronzes, silvers and golds, none of which can fill a calendar day. Scoping that way recomputes a full
    history for everyone who opened the app that day, to discover nothing.

    THE RIGHT QUESTION IS "COULD ANYTHING HAVE FILLED A DAY", and two counters answer it exactly:

    - `Profile.total_plats` covers the all-platinums and shovelware-free views, since a day in either
      needs a platinum; and
    - the hunter's earned-contract count covers the contracts view, which can move WITHOUT a platinum,
      because a contract reaches its 100% tier with no platinum term.

    Both are compared against watermarks stored on the run. A run whose numbers have not moved is skipped
    without reading a single trophy.

    WHAT IT CANNOT SEE, stated plainly because the cron doc briefly claimed otherwise. A SHOVELWARE
    RECLASSIFICATION moves neither counter and genuinely changes the `clean` view: `auto_flagged ->
    clean` is a routine outcome of `update_shovelware`, and staff write `manually_cleared` by hand.
    So a hunter whose platinumed game is un-flagged does not become due, and that day fills only on
    their next platinum or earned contract -- for a dormant hunter, never. Two smaller cases share the
    shape: a staff `igdb_id` edit that moves a contract's member concepts, and a hunter changing their
    timezone, which re-keys every day while moving no counter.
    A third watermark was considered and rejected. The only cheap site-wide signal is "some game's flag
    moved tonight", which marks EVERY run due and defeats the reconciliation on any night
    `update_shovelware` touches anything; targeted invalidation means shovelware detection reaching into
    challenges. So the escape hatch is a staff door -- `process_challenges --all-calendars` -- and the
    gap is documented rather than papered over. Nothing is ever WRONG as a result: fills are monotone,
    so the cost is a day that fills late rather than a day that fills incorrectly.

    SCOPED TO RUNS, NOT ACCOUNTS, which is the principle `completable_slots` already states: "the sweep's
    cost should scale with how many runs are in flight, not with how many accounts exist." A hunter who
    never started a Calendar challenge has no run and nothing to compute -- the backfill is reachable
    only through a run, and a run exists only because somebody pressed Start.

    FINISHED RUNS ARE OUT OF SCOPE, which REVERSES an exception this function used to carry. It read
    "NOT SCOPED TO UNFINISHED RUNS... a Calendar run completes on the FIRST genuine view to fill, and
    the hunter may still be working toward the other view's ultimate title, so its days must keep
    filling afterwards". That justification died with the second lens: a complete run is 365
    shovelware-free days, `in_all` is a superset of `in_clean`, so there is no column left that can
    move. Sweeping a finished run could only ever rewrite it with itself.

    NOT SCOPED TO VISIBLE RUNS, though, and that is CONSISTENCY rather than an exception.
    `pending_slots` -- the detection every other challenge type goes through -- filters only
    `challenge__is_complete=False, is_completed=False` and says nothing about `is_deleted`, so a hidden
    A-Z run has always kept completing its squares. Hiding in this feature means "not shown", not
    "frozen": `hide` is explicit that it destroys nothing and that `start` brings the run back.
    An earlier version of this filtered `is_deleted=False` and so froze any hidden Calendar run. That
    was invisible for the other two types, because a hidden UNFINISHED run resumes on Start and a hidden
    FINISHED one has nothing left to fill -- but a finished Calendar run does: the view it did not
    complete on is still reachable, and freezing it silently removed the only path to that ultimate.
    """
    from challenges.models import CHALLENGE_TYPE_CALENDAR, Challenge

    # ONE COLUMN AGAINST ONE COLUMN. There was a `Subquery` annotation here counting every row of
    # `EarnedContract` per profile, because a contracts day could move WITHOUT a platinum -- a contract
    # reaches its 100% tier with no platinum term -- so the cheap question needed two numbers. With one
    # lens a day needs a platinum, full stop, so `total_plats` IS the question and the annotation, the
    # second watermark column and the `Coalesce`/`OuterRef` imports all went with it.
    return (
        Challenge.objects
        .filter(challenge_type=CHALLENGE_TYPE_CALENDAR, is_complete=False)
        .exclude(profile__total_plats=F('calendar_plats_seen'))
    )


def refresh_for_profile(profile):
    """Refresh every Calendar run this hunter owns. Returns the days newly filled across them.

    EXTRACTED SO IT CAN BE TESTED, which the five inline lines in `token_keeper` could not be. The only
    coverage they had was a substring search of the module source, and an audit proved that pin passes
    against a hook that is commented out, emptied, or wrapped in `if False` -- so no test in the suite
    failed when the hook did nothing. A named function can be called by a test and asserted on.

    THE FILL SET IS COMPUTED ONCE. It depends on the hunter's history, not on the run, and a hunter can
    own more than one Calendar run: the one-active constraint is partial on `is_complete=False`, and a
    finished run keeps filling so its other view can still reach an ultimate. Per run, that repeats an
    identical history pass.

    PER-RUN CONTAINMENT, matching the nightly phase. One run with odd data must not cost the others
    their refresh, and on the sync path it must not cost the hunter the rest of their sync.
    """
    from challenges.models import CHALLENGE_TYPE_CALENDAR, Challenge

    # NO `is_deleted` FILTER, matching `pending_slots`: hiding is a display choice in this feature and
    # not a data freeze, so a hidden run keeps filling exactly as a hidden A-Z run keeps completing.
    runs = list(Challenge.objects.filter(
        profile=profile, challenge_type=CHALLENGE_TYPE_CALENDAR))
    if not runs:
        return 0

    found = filled_days(profile)
    total = 0
    for run in runs:
        try:
            total += apply_to_run(run, found=found)
        except Exception:
            logger.exception('calendar refresh failed for challenge %s', run.pk)
    return total
