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
`Profile.total_trophies_all` exists because ranking on a filter-respecting figure "makes the board
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

from django.db.models import Min
from django.db.models.functions import ExtractDay, ExtractMonth, TruncDate

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


def _platinum_trophy_ids(profile, *, clean_only=False):
    """The platinum Trophy ids worth asking `EarnedTrophy` about, resolved catalogue-first.

    THE ORDER OF THESE TWO QUERIES IS THE WHALE SAFETY. Asking `EarnedTrophy.filter(profile=...,
    trophy__trophy_type='platinum')` reads as the obvious thing and is the slow thing: it starts from
    every trophy the hunter has earned and joins `Trophy` to test each one. Starting from `ProfileGame`
    bounds the first step by the hunter's LIBRARY (hundreds to low thousands) rather than their trophy
    count, and bounds the second by the catalogue.

    `has_plat` is a denormalized boolean with no date on it, which is exactly why it is useful here and
    useless on its own: it narrows the games to ask about, and `EarnedTrophy` supplies the when.
    """
    game_ids = list(
        ProfileGame.objects
        .filter(profile=profile, has_plat=True)
        .values_list('game_id', flat=True)
    )
    if not game_ids:
        return []

    trophies = Trophy.objects.filter(game_id__in=game_ids, trophy_type='platinum')
    if clean_only:
        # `SHOVELWARE_FLAGGED_STATUSES` is the ONE definition of "this is shovelware", shared with
        # `Game.is_shovelware` and the Shovelware Free board. Note the stricter rule the trophy tracker
        # uses (`status == 'clean'`, which also excludes manually-cleared games) is deliberately NOT the
        # one here: a human having looked at a game and passed it means it counts.
        trophies = trophies.exclude(game__shovelware_status__in=SHOVELWARE_FLAGGED_STATUSES)
    return list(trophies.values_list('id', flat=True))


def _days_from_platinums(profile, tz, *, clean_only=False):
    """`{(month, day): earliest local date}` for this hunter's platinums.

    GROUPED IN THE DATABASE, in the hunter's timezone, which is the project's per-user aggregation rule
    rather than a preference: a whale has hundreds of platinums and iterating them in Python to build a
    dict is the shape that has repeatedly cost this codebase a gunicorn worker. `ExtractMonth` and
    `TruncDate` both take a `tzinfo`, so Postgres does the conversion and returns at most 365 rows.

    `Min` rather than any: a day's stored date is the FIRST time it was filled. A hunter who platted on
    3 March in 2019 and again in 2024 filled that square in 2019.
    """
    trophy_ids = _platinum_trophy_ids(profile, clean_only=clean_only)
    if not trophy_ids:
        return {}

    rows = (
        EarnedTrophy.objects
        .filter(profile=profile, earned=True, trophy_id__in=trophy_ids,
                earned_date_time__isnull=False)
        .annotate(m=ExtractMonth('earned_date_time', tzinfo=tz),
                  d=ExtractDay('earned_date_time', tzinfo=tz))
        .values('m', 'd')
        .annotate(first=Min(TruncDate('earned_date_time', tzinfo=tz)))
        .values_list('m', 'd', 'first')
    )

    out = {}
    for month, day, first in rows:
        key = _fold(month, day)
        # The fold can collide a real 28 February with a folded 29 February, so keep the earlier.
        if key not in out or (first is not None and first < out[key]):
            out[key] = first
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
    return {
        CALENDAR_VIEW_ALL: _days_from_platinums(profile, tz),
        CALENDAR_VIEW_CLEAN: _days_from_platinums(profile, tz, clean_only=True),
        CALENDAR_VIEW_CONTRACTS: _days_from_contracts(profile, tz),
    }


def apply_to_run(challenge):
    """Write this hunter's filled days onto a Calendar run, and finish it if a genuine view is full.

    Returns the number of days this pass newly filled, across all three views combined, so a caller can
    say "you start at 154 days" without counting again.

    FILLS ARE MONOTONE: a day that is true is never set back to false. The predicates can legitimately
    stop matching -- a game is reclassified as shovelware, `reconcile_contracts` deletes an
    `EarnedContract` when derived membership moves, a staff `igdb_id` edit changes who qualifies -- and
    none of those are things the hunter did. Un-filling on any of them would retract an earned square for
    catalogue bookkeeping they never saw, which is the same rule `ChallengeSlot` follows by snapshotting
    its contract. So this ORs the new state onto the old.

    THE COUNTERS ARE DERIVED FROM THE ROWS, not incremented, matching `_recount`: an incremented counter
    that drifts is load-bearing for correctness and no user action repairs it, whereas
    `challenge_filled_within_total` turns a derived one going wrong into an error.

    `filled_count` AND `completed_count` ARE BOTH THE BEST GENUINE VIEW. A day has no
    assigned-but-unfinished state the way a slot does, so the two numbers are the same thing here, and
    `challenge_completed_within_filled` is satisfied by equality. "Best genuine" is the higher of clean
    and contracts: either of those completing finishes the run, so that figure is distance-to-finish.
    All-platinums is deliberately not it -- it cannot finish anything, so leading with it would show a
    card at 298/365 on a run that completes at 164.
    """
    from django.db import transaction
    from django.utils import timezone as dj_timezone

    from challenges.models import (
        CALENDAR_VIEW_FIELDS,
        CalendarDay,
        Challenge,
    )

    found = filled_days(challenge.profile)
    now = dj_timezone.now()
    newly_filled = 0

    rows = list(CalendarDay.objects.filter(challenge=challenge))
    to_update = []
    for row in rows:
        changed = False
        for view, field in CALENDAR_VIEW_FIELDS:
            if (row.month, row.day) in found[view] and not getattr(row, field):
                setattr(row, field, True)
                changed = True
        if not changed:
            continue
        newly_filled += 1
        # The EARLIEST date across the views that fill this square, and only ever set once: the square
        # was earned when it was first earned, whichever lens noticed.
        dates = [found[view][(row.month, row.day)]
                 for view, _f in CALENDAR_VIEW_FIELDS
                 if (row.month, row.day) in found[view]]
        earliest = min(d for d in dates if d is not None) if any(d for d in dates) else None
        if earliest is not None and (row.earned_on is None or earliest < row.earned_on):
            row.earned_on = earliest
        if row.filled_at is None:
            row.filled_at = now
        to_update.append(row)

    with transaction.atomic():
        if to_update:
            CalendarDay.objects.bulk_update(
                to_update, ['in_all', 'in_clean', 'in_contracts', 'earned_on', 'filled_at'],
                batch_size=500)
        _recount_calendar(challenge)

    return newly_filled


def _recount_calendar(challenge):
    """The Calendar's counterpart to `challenge_service._recount`, and the shape is deliberately its.

    ONE AGGREGATE over the day rows, counters derived rather than incremented, and the completion stamp
    riding the same UPDATE.
    """
    from django.db.models import Count, Q
    from django.utils import timezone as dj_timezone

    from challenges.models import CALENDAR_VIEW_CLEAN, CALENDAR_VIEW_CONTRACTS

    counts = challenge.calendar_days.aggregate(
        clean=Count('pk', filter=Q(in_clean=True)),
        contracts=Count('pk', filter=Q(in_contracts=True)),
    )
    clean = counts['clean'] or 0
    contracts = counts['contracts'] or 0
    best = max(clean, contracts)

    challenge.filled_count = best
    challenge.completed_count = best
    fields = ['filled_count', 'completed_count', 'updated_at']

    just_completed = not challenge.is_complete and best >= challenge.total_slots
    if just_completed:
        challenge.is_complete = True
        challenge.completed_at = dj_timezone.now()
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
        # which a hunter finishing one should get. The per-view ultimates slot in here without changing
        # the call.
        rewards.on_run_completed(challenge)
