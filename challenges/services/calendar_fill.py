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

from django.db.models import (Case, Count, DateField, F, IntegerField, Min, OuterRef, Q,
                              Subquery, Value, When)
from django.db.models.functions import Coalesce, ExtractDay, ExtractMonth, TruncDate
from django.utils import timezone

from challenges.models import CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN, CALENDAR_VIEW_CONTRACTS
from trophies.models import (
    SHOVELWARE_FLAGGED_STATUSES,
    EarnedContract,
    EarnedTrophy,
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


def _days_from_contracts(profile, tz, *, batch=200):
    """`{(month, day): earliest local date}` for this hunter's CONTRACT completions.

    THROUGH `eligibility.completion_dates`, NOT A SECOND DERIVATION, and this is the DRY risk this whole
    slice was flagged for. That function already answers "when did this hunter FIRST complete this
    contract" -- reading both tiers the contract engine reads, taking the earliest qualifying moment,
    and doing it in five bounded queries with the small-side-first shape. Writing the same rule again
    here would give the Calendar its own quietly different definition of "completed, and when", and the
    two would drift the first time either tier's rule moved.

    BATCHED, because that function is sized for a PICKER PAGE. Its cost is a fixed number of queries
    however many contracts are passed, but the `IN` lists are not fixed, and a whole career is hundreds
    of contracts rather than a page of twenty.
    """
    from challenges.services.eligibility import completion_dates

    contract_ids = list(
        EarnedContract.objects.filter(profile=profile).values_list('contract_id', flat=True)
    )
    if not contract_ids:
        return {}

    from trophies.models import Contract

    out = {}
    for start in range(0, len(contract_ids), batch):
        chunk = list(Contract.objects.filter(id__in=contract_ids[start:start + batch]))
        for moment in completion_dates(profile, chunk).values():
            if moment is None:
                continue
            local = moment.astimezone(tz)
            key = _fold(local.month, local.day)
            date = local.date()
            if key not in out or date < out[key]:
                out[key] = date
    return out


def filled_days(profile):
    """`{view: {(month, day): earliest local date}}` for all three views.

    The single entry point. Returns at most 365 entries per view, and every date is already resolved in
    the hunter's own timezone, so a caller never has to know about timezones again.
    """
    tz = _hunter_timezone(profile)
    platinum_views = _days_from_platinums(profile, tz)
    return {
        CALENDAR_VIEW_ALL: platinum_views[CALENDAR_VIEW_ALL],
        CALENDAR_VIEW_CLEAN: platinum_views[CALENDAR_VIEW_CLEAN],
        CALENDAR_VIEW_CONTRACTS: _days_from_contracts(profile, tz),
    }


def apply_to_run(challenge):
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
                to_update, ['in_all', 'in_clean', 'in_contracts', 'earned_on', 'filled_at'],
                batch_size=500)
        # THE WATERMARKS, written whether or not anything filled -- that is what makes the sweep's
        # cheap check work. Skipping them on a no-op would leave the run due every night forever.
        # Deliberately NOT `updated_at`, which must stay still on a no-op or every Calendar run floats
        # to the top of "my challenges" each morning.
        locked.calendar_plats_seen = locked.profile.total_plats
        locked.calendar_contracts_seen = EarnedContract.objects.filter(
            profile_id=locked.profile_id).count()
        locked.save(update_fields=['calendar_plats_seen', 'calendar_contracts_seen'])
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
    from challenges.models import CALENDAR_VIEW_CLEAN, CALENDAR_VIEW_CONTRACTS

    counts = challenge.calendar_days.aggregate(
        clean=Count('pk', filter=Q(in_clean=True)),
        contracts=Count('pk', filter=Q(in_contracts=True)),
    )
    clean = counts['clean'] or 0
    contracts = counts['contracts'] or 0
    best = max(clean, contracts)

    just_completed = not challenge.is_complete and best >= challenge.total_slots
    if not wrote_rows and best == challenge.filled_count and not just_completed:
        return

    challenge.filled_count = best
    challenge.completed_count = best
    fields = ['filled_count', 'completed_count', 'updated_at']

    if just_completed:
        challenge.is_complete = True
        challenge.completed_at = timezone.now()
        # CONTRACTS WINS A TIE, and a tie is reachable: an opening backfill evaluates every view in one
        # pass, so a hunter who qualifies for both finishes both at once and "whichever came first"
        # decides nothing. The rarer, harder achievement is the more honest snapshot to put on the board.
        challenge.completed_view = (
            CALENDAR_VIEW_CONTRACTS if contracts >= challenge.total_slots else CALENDAR_VIEW_CLEAN)
        fields += ['is_complete', 'completed_at', 'completed_view']

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

    SCOPED TO RUNS, NOT ACCOUNTS, which is the principle `completable_slots` already states: "the sweep's
    cost should scale with how many runs are in flight, not with how many accounts exist." A hunter who
    never started a Calendar challenge has no run and nothing to compute -- the backfill is reachable
    only through a run, and a run exists only because somebody pressed Start.

    NOT SCOPED TO UNFINISHED RUNS, which inverts the usual convention and is deliberate. The house rule
    elsewhere is that a finished run is never written to again. A Calendar run completes on the FIRST
    genuine view to fill, and the hunter may still be working toward the other view's ultimate title, so
    its days must keep filling afterwards.
    """
    from challenges.models import CHALLENGE_TYPE_CALENDAR, Challenge

    earned_contracts = (
        EarnedContract.objects
        .filter(profile=OuterRef('profile_id'))
        .values('profile_id')
        .annotate(n=Count('pk'))
        .values('n')
    )

    return (
        Challenge.objects
        .filter(challenge_type=CHALLENGE_TYPE_CALENDAR, is_deleted=False)
        .annotate(live_contracts=Coalesce(Subquery(earned_contracts, output_field=IntegerField()), 0))
        .exclude(profile__total_plats=F('calendar_plats_seen'),
                 live_contracts=F('calendar_contracts_seen'))
    )
