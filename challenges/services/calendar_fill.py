"""Which calendar days a hunter has filled, and the one place the two views are defined.

THREE CONSUMERS, TWO OF THEM ALREADY WIRED -- the nightly sweep, the sync hook (`token_keeper`'s
`stats_badges` phase) and the day modal, which is the one still to come. The thing that goes wrong when
a rule like this has no home is that each of them derives it slightly differently: the day modal listing
games that "satisfy" a day has to agree, exactly, with the predicate that filled it, or a hunter opens a
filled day to an empty list. So the predicates live here and nothing re-derives them.
(This said "the opening backfill" rather than the sweep. `challenge_service.start` does no backfill at
all -- it `bulk_create`s the 365 empty rows and the first pass fills them.)

THE TWO VIEWS:

- `all`    every platinum the hunter has earned, by its local date.
- `clean`  the same, minus platinums on shovelware games. THE ONLY ONE THE BOARD DRAWS.

`clean` NESTS INSIDE `all` -- a shovelware-free platinum is still a platinum -- and
`calendarday_clean_implies_all` enforces it in the database. That nesting is the reason `all` is worth
keeping at all now that nothing draws it: it makes the headline interpretable ("297 shovelware-free of
340 platinum days") instead of a bare number, and one statement computes both.

THERE WAS A THIRD, and this docstring went on defining it as current for several slices after it was
deleted -- "THE THREE VIEWS, and the one that is not what it looks like". `contracts` keyed a day on a
CONTRACT's earliest qualifying moment, which is not any one platinum's instant: a contract reaches its
100% tier from `progress=100` with no platinum anywhere, so it nested inside NEITHER platinum view and
a day could be filled in the hardest view and empty in the easiest. The owner collapsed the lenses to
one (2026-10-04: "perhaps we should condense down to just one view: non-shovelware plats... contracts
are more curated sets of games and this is more wholistic"), which deleted `_days_from_contracts` --
the expensive half of this module, five bounded queries per chunk of `EarnedContract` -- along with a
watermark column, a sweep exception and a whole class of cross-lens defect.

`hide_hiddens` IS IGNORED, deliberately, and the precedent is in the repo rather than in my judgement.
`Profile.total_trophies_raw` exists because ranking on a filter-respecting figure "makes the board
unreproducible by anyone but its owner" -- and the Hall of Fame is a board that Calendar runs land on.
If a hunter's display preference decided which days filled, two hunters with identical libraries would
get different runs and a finish would mean something different per row.

WHALE SAFETY IS THE SHAPE OF EVERY QUERY HERE, not a note on them. These hunters run to 250,000+ earned
trophies, so the rule from `contract_service._detect_tiers` applies in full: never start from
`EarnedTrophy.filter(profile=...)` and join outward to test a property, because the planner then walks
every one of those rows. Resolve the catalogue-bounded side first and turn the aggregate into an index
seek. Every function below that answers "which days" returns at most 365 rows. Two do something else
and say so: `runs_due_for_sweep` returns a site-wide `Challenge` queryset, and `platinums_on_day`
returns one square's PLATINUMS -- a few for most hunters, several hundred on a stacked 28 February for
a serial platter, since the fold gives that square two days and twenty years compound onto it.
"""
import datetime as dt
import logging
import zoneinfo
from collections import namedtuple

from django.db.models import Case, Count, DateField, F, Max, Min, Q, Value, When
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


#: WHAT THE AGGREGATE KNOWS ABOUT ONE SQUARE IN ONE LENS. This was a bare date, and the second field
#: arrived with the month panel's "busiest day" figure. A named pair rather than two parallel dicts
#: keyed the same way: the fold below has to combine BOTH fields on a collision (min the date, sum the
#: counts), and two dicts drifting apart at that one point is the defect that shape invites.
DayFill = namedtuple('DayFill', ('date', 'plats'))


def _fold(month, day):
    """`(2, 29)` -> `(2, 28)`; everything else unchanged."""
    return LEAP_DAY_FOLDS_TO if (month, day) == LEAP_DAY else (month, day)


def _earned_span(profile):
    """`(first, last)` instants this hunter earned ANY trophy, or `(None, None)`.

    ONE INDEXED STATEMENT. `earnedtrophy_timeline_idx` is `(profile, earned, earned_date_time)
    WHERE earned`, so `profile = X AND earned` is the index's leading prefix and Postgres takes the
    min and the max as two ends of a range scan rather than reading rows.

    EVERY TROPHY, NOT JUST PLATINUMS, and deliberately: the platinum span is inside this one, so it is
    still a superset for `_day_windows`' purposes, and asking it without the `trophy_id__in` subquery is
    what keeps it an index read instead of a scan.
    """
    bounds = (EarnedTrophy.objects
              .filter(profile=profile, earned=True, earned_date_time__isnull=False)
              .aggregate(first=Min('earned_date_time'), last=Max('earned_date_time')))
    return bounds['first'], bounds['last']


def _day_windows(keys, tz, span):
    """A `Q` of `earned_date_time` ranges covering each key's local day across `span`'s years.

    THE WHOLE POINT IS THAT THIS IS INDEXABLE. `earnedtrophy_timeline_idx` is
    `(profile, earned, earned_date_time) WHERE earned`, so a range on `earned_date_time` is a scan of
    that index; an `EXTRACT(... AT TIME ZONE ...)` on the same column is not, and cannot be made so.

    A SUPERSET, NOT A SUBSTITUTE. The caller still applies the exact month/day test afterwards, so a
    window only has to CONTAIN its day. Each is the local day plus a day either side -- and the reason
    that is correct is NOT that 24 hours absorbs a 14-hour offset, which is how an earlier version of
    this comment put it. It is that both bounds are computed IN THE KEY'S OWN ZONE and `timedelta`
    arithmetic on an aware datetime is wall-clock, so each window is literally "local days d-1 .. d+1"
    and tracks offset changes for free. Verified against all 598 zones in the tz database for every
    (month, day) from 2008 to 2026: no violations, with the tightest margin at `Pacific/Apia`'s 2011
    date-line skip, which deleted 30 December outright.

    THE YEARS COME FROM THE HUNTER'S OWN DATA, which is the fix for a silent defect rather than a tidy-
    up. This used to run `range(TROPHY_EPOCH_YEAR, timezone.now().year + 1)` with a hardcoded 2008 and
    the UTC year, and both ends leaked:

      - `timezone.now().year` is the UTC year while the key is a LOCAL day, so for any UTC+X hunter the
        first hours of local 1 January fall outside every window. That fired every year with no bad data
        at all -- fourteen hours for a `Pacific/Kiritimati` hunter -- and the square filled while the
        modal said "No platinums on this date".
      - nothing clamps `earned_date_time` on the way in. Sync copies the PSN payload verbatim and the
        admin form takes any value, so a pre-2008 stamp (a shifted console clock) or a future one filled
        the square, counted into `plat_count`, and was invisible here. A future stamp stayed invisible
        for years. `_days_from_platinums` has no floor or ceiling, which is what made the asymmetry a
        disagreement rather than a shared blind spot.

    A span taken from the rows themselves cannot have either, because whatever filled the square is by
    definition inside it. `± 1 year` is ordinary belt and braces around the local/UTC boundary.

    AN EMPTY RESULT MATCHES NOTHING, AND THAT MATTERS MORE THAN IT LOOKS. `Q()` is falsy and
    `.filter(Q())` is a NO-OP, so returning one would silently restore the full-history scan this
    function exists to prevent -- a correct, empty answer at catastrophic cost. `Q(pk__in=[])` is a
    false predicate instead.

    A LEAP DAY ONLY EXISTS IN A LEAP YEAR, which is what the `ValueError` is: `(2, 29)` is a real key
    here (it folds onto the 28th and the caller passes both), and three years in four have no such date.
    """
    first, last = span
    windows = Q()
    built = False
    if first is not None and last is not None:
        # THE SPAN'S YEARS IN THE KEY'S ZONE, not in UTC -- see above.
        for year in range(first.astimezone(tz).year - 1, last.astimezone(tz).year + 2):
            for month, day in keys:
                try:
                    local = dt.datetime(year, month, day, tzinfo=tz)
                except ValueError:
                    continue
                windows |= Q(earned_date_time__gte=local - dt.timedelta(days=1),
                             earned_date_time__lt=local + dt.timedelta(days=2))
                built = True
    return windows if built else Q(pk__in=[])


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
    """`{'all': {(month, day): DayFill}, 'clean': {...}}` -- BOTH platinum views, from ONE statement.

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

    AND A COUNT ALONGSIDE IT, for the month panel's "busiest day" (owner, 2026-10-04). Two more
    aggregates on a statement that is already grouping by (month, day), which is the whole reason the
    figure is affordable: the board is one read of `CalendarDay` and could not count trophies without a
    second whale-sized query on the request path.
    ONE SUCH QUERY, NOT EIGHT. An earlier version of this note said "eight of them on a Hall of Fame
    page" -- it is the DETAIL board that draws the figure, once per page, and `_hero_group`'s cells
    carry no count at all. The denormalization is still worth having; the page it was justified against
    does not render the thing.
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
                  clean_first=Min(clean_date),
                  plats=Count('id'),
                  # `Count` OF THE CONDITIONAL EXPRESSION: `clean_date` is NULL on a shovelware row and
                  # `Count` ignores NULLs, so counting it counts exactly the clean platinums.
                  #
                  # IT IS NOT CHEAPER THAN `filter=Q(...)`, AND THIS COMMENT SAID IT WAS. Django performs
                  # no common-subexpression elimination across annotations, so re-using the `clean_date`
                  # OBJECT buys nothing in SQL: the compiled statement inlines the `trophy_id__in`
                  # subquery twice either way -- once under `MIN(CASE ...)`, once under `COUNT(CASE ...)`
                  # -- and a `Min(filter=)` / `Count(filter=)` pair compiles to the same number. Verified
                  # by compiling both forms, because the claim is the kind a reader builds on: anyone
                  # adding a THIRD conditional clean figure here should know it adds a third full
                  # subquery over the hunter's platted library, not that it is free.
                  clean_plats=Count(clean_date))
        .values_list('m', 'd', 'first', 'clean_first', 'plats', 'clean_plats')
    )

    out = {CALENDAR_VIEW_ALL: {}, CALENDAR_VIEW_CLEAN: {}}
    for month, day, first, clean_first, plats, clean_plats in rows:
        key = _fold(month, day)
        for view, date, count in ((CALENDAR_VIEW_ALL, first, plats),
                                  (CALENDAR_VIEW_CLEAN, clean_first, clean_plats)):
            if date is None:
                continue
            held = out[view].get(key)
            # THE FOLD COLLIDES a real 28 February with a folded 29 February, and the two fields combine
            # DIFFERENTLY on that collision -- the only place in this module where they do.
            # The EARLIER DATE owns the square: a true `min`, so it does not depend on which row
            # Postgres returns first. The COUNTS ADD: a hunter who platted on both days holds two
            # platinums on the one square they share, and picking either row's count would lose one.
            out[view][key] = DayFill(
                date if held is None or date < held.date else held.date,
                count + (held.plats if held else 0))
    return out


def filled_days(profile):
    """`{key: {(month, day): DayFill(date, plats)}}` for the two stored populations.

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


def platinums_on_day(profile, month, day):
    """Every platinum this hunter earned on one calendar square, newest first.

    `[{'game', 'on', 'clean'}]` -- the `Game` (for its name and its cover), the resolved local DATE, and
    whether it counts on the board. A handful of rows for most hunters and several hundred on a stacked
    28 February for a serial platter, since the fold gives that square two days and twenty years of
    history compound onto it. (This said "at most a few dozen" while the module docstring said "several
    hundred" -- one return value, two numbers, in one commit.)

    THE PREDICATE IS `_platinum_trophies`, NOT A SECOND SPELLING OF IT, which is the whole reason this
    function is in this module. The module docstring states the failure it avoids: a day modal that
    derived "satisfies" slightly differently from the writer would open a FILLED square onto an empty
    list. Same subquery, same `has_plat` + `trophy_type` reading, same shovelware statuses.

    AND THE SAME TIMEZONE, which is the trap the public page sets. `middleware` activates the VIEWER's
    timezone and a run page is readable by anybody, so resolving dates from the request would list a
    Tokyo reader a different set than a London one -- for somebody else's calendar. `_hunter_timezone`
    takes the owner's, exactly as the fill does, so the modal and the square agree about which day it is.

    THE LEAP-DAY FOLD IS APPLIED HERE TOO, and leaving it out would have been a silent undercount. A
    platinum earned on 29 February fills the 28 February square (`_fold`), and `plat_count` SUMS the two
    -- so a 28 February square can read "3 platinums" while a modal that asked only for (2, 28) listed
    two. That is exactly the "glance figure contradicts the list the hunter just opened" failure the
    count is live to prevent, reached from the query side instead.

    ORDERED NEWEST FIRST ON THE INSTANT, not on the resolved date, so a square's rows read newest to
    oldest even where several share a local day. `-id` breaks a tie DETERMINISTICALLY and nothing more:
    PSN stamps to the second and back-to-back stack platinums collide, and without a second key those
    rows come back in whatever order the plan emits, which changes between renders. It is not "the order
    they were earned in", which an earlier version of this claimed -- `id` is `EarnedTrophy` insertion
    order from sync and has no relationship to earn order.
    """
    tz = _hunter_timezone(profile)

    # THE INPUT IS NORMALISED THROUGH `_fold`, which the first version claimed to do in a comment and
    # did not -- it hardcoded the pair and inverted the mapping by hand, putting the one fact this
    # module exists to keep in one place into two. `_fold` also means (2, 29) ARRIVING here resolves to
    # the square that actually holds it rather than being answered as a square of its own, which no
    # `CalendarDay` row can be (`calendarday_day_within_month` forbids it).
    month, day = _fold(month, day)
    keys = [(month, day)]
    if (month, day) == LEAP_DAY_FOLDS_TO:
        keys.append(LEAP_DAY)

    exact = Q()
    for key_month, key_day in keys:
        exact |= Q(m=key_month, d=key_day)

    rows = (
        EarnedTrophy.objects
        .filter(profile=profile, earned=True,
                trophy_id__in=_platinum_trophies(profile),
                earned_date_time__isnull=False)
        # AN INDEXABLE WINDOW IN FRONT OF THE EXACT TEST, and this is the difference between a modal
        # that costs an index range scan and one that walks the hunter's whole history.
        #
        # `EXTRACT(... AT TIME ZONE ...)` CANNOT USE ANY INDEX, and no index could ever serve it: the
        # timezone is a per-hunter value, so the expression is not a fixed function of the row. The only
        # selective predicate here (one day in 365) was therefore a post-fetch filter, which left
        # `earnedtrophy_timeline_idx` narrowing to `profile = X` and nothing more -- a bitmap heap scan
        # of a whale's entire 250,000-row slice, pulled through shared buffers, PER OPEN, on a page any
        # anonymous visitor can click 365 times. That is the shape this module's own docstring forbids
        # ("never start from `EarnedTrophy.filter(profile=...)` and join outward to test a property"),
        # and `_days_from_platinums` gets away with it only because it runs once per sweep.
        #
        # SO THE WINDOWS GO FIRST: `(profile, earned, earned_date_time)` is exactly the partial index's
        # leading columns, so each year's window is a range scan and the planner ORs them.
        #
        # A SUPERSET, DELIBERATELY WIDE, and the exact test still decides. A window is the local day
        # plus a day either side, which covers every UTC offset (±14h) and any DST transition at
        # midnight without this function having to reason about either. Getting the window slightly
        # wrong can only cost a few extra rows to filter; getting it slightly SHORT would silently drop
        # a platinum, so it is not a place to be clever.
        .filter(_day_windows(keys, tz, _earned_span(profile)))
        .annotate(m=ExtractMonth('earned_date_time', tzinfo=tz),
                  d=ExtractDay('earned_date_time', tzinfo=tz),
                  on=TruncDate('earned_date_time', tzinfo=tz))
        .filter(exact)
        # THE COVER CHAIN'S JOIN. `display_image_url` reads the IGDB match FIRST on every render, so
        # without this a day with a dozen platinums walks four relations per row -- `trophy`, `game`,
        # `concept`, `igdb_match` -- which is around fifty queries, not a dozen as this once said.
        # ONE PATH, not three: the longest implies its prefixes, and listing all three read as three
        # joins being asked for.
        .select_related('trophy__game__concept__igdb_match')
        # THE HEAVY COLUMNS, and `raw_response` is only the famous one. It is the ~30 KB IGDB blob that
        # triggered the May 2026 web-server OOM, and this project requires it deferred beside every
        # `igdb_match` join. The join chain hydrates seventeen more columns that no cover and no name
        # ever reads -- fifteen `JSONField`s plus `igdb_summary` and `igdb_storyline`, which are
        # `TextField`s -- spread across `IGDBMatch`, `Concept` AND `Game`, not all on the match as an
        # earlier version of this said. Several are multi-KB each.
        # DEFERRED RATHER THAN WHITELISTED WITH `.only()`, because the failure modes are not symmetric:
        # a column missed here is merely not deferred, while a column missed by `.only()` is a query per
        # row on a public path.
        .defer('trophy__game__concept__igdb_match__raw_response',
               'trophy__game__concept__igdb_match__igdb_summary',
               'trophy__game__concept__igdb_match__igdb_storyline',
               'trophy__game__concept__igdb_match__igdb_ps_release_dates',
               'trophy__game__concept__igdb_match__similar_game_igdb_ids',
               'trophy__game__concept__igdb_match__external_urls',
               'trophy__game__concept__igdb_match__igdb_screenshot_image_ids',
               'trophy__game__concept__igdb_match__igdb_artwork_image_ids',
               'trophy__game__concept__igdb_match__igdb_video_youtube_ids',
               'trophy__game__concept__igdb_match__franchise_names',
               'trophy__game__concept__media',
               'trophy__game__concept__descriptions',
               'trophy__game__concept__genres',
               'trophy__game__concept__subgenres',
               'trophy__game__concept__igdb_genres',
               'trophy__game__concept__igdb_themes',
               'trophy__game__defined_trophies',
               'trophy__game__metadata')
        # `-id` AS THE TIEBREAK, for determinism only. PSN stamps to the second and back-to-back stack
        # platinums collide, so the instant alone leaves those rows in whatever order the plan emits --
        # which changes between renders. `id` is sync insertion order and says nothing about which
        # platinum came first, so this makes the order STABLE rather than meaningful.
        .order_by('-earned_date_time', '-id')
    )

    # BOUNDED BY ONE DATE, so iterating is not the per-user aggregation the project rule forbids: the
    # row count is "platinums on 3 March", not "platinums". There is nothing to aggregate here either --
    # the modal wants the rows themselves.
    out = []
    for row in rows:
        game = row.trophy.game
        out.append({
            'game': game,
            'on': row.on,
            # WHAT THE BOARD WOULD COUNT. The square draws on the shovelware-free lens, so a flagged
            # game appears in this list and says so rather than being hidden: on a square that is `all`
            # and not `clean`, these rows ARE the explanation for why the square is still open.
            'clean': game.shovelware_status not in SHOVELWARE_FLAGGED_STATUSES,
        })
    return out


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
    contradiction the `DateField` was chosen to rule out, reached from the other direction.

    AND IT COMES FROM THE LENS THE BOARD DRAWS, which is a correctness fix and not a refinement. This
    stored `min(date across every view holding the square)` -- "whichever lens noticed" -- which was the
    right shape when three lenses rendered at once and each could reveal its own. One lens survived the
    collapse and `in_all` stayed on as a COUNTER, so that minimum became a cross-lens value on a
    single-lens board: a hunter with a shovelware platinum on 2015-03-03 and a clean one on 2021-03-03
    held `in_clean=True, earned_on=2015-03-03`, and the month's side column printed "First filled --
    3 Mar 2015" on a board that counts nothing from 2015. The date now reads `clean` for a square that
    draws and `all` for one that does not, so the figure can never describe a population the board
    excludes.

    WHICH MEANS IT NO LONGER ONLY MOVES BACKWARDS, and the exception is narrow and explicit: a square
    PROMOTED from `all`-only to `clean` this pass moves its date FORWARD, off the excluded platinum and
    onto the one that earned it. Nothing else may.

    THE EXCEPTION IS A PROMOTION TEST, NOT A COMPARISON, and the first version got this wrong in a way
    worth recording. It took the offered date whenever it DIFFERED from the stored one, reasoning that a
    higher offer could only mean the stored value came from the other lens, "because `filled_days` reads
    the whole history every pass, so a lens's own earliest never rises". The premise is false. The clean
    aggregate is filtered on `SHOVELWARE_FLAGGED_STATUSES`, and `update_shovelware` flags
    previously-clean games on a routine nightly pass -- so a hunter with clean platinums on 3 March in
    2015 and 2021 whose 2015 game gets flagged sees the clean lens's earliest RISE to 2021, and the
    square would have silently re-dated. That is the same cause the `offered is None` branch below
    refuses to act on, and it would have had the two branches applying opposite policies to it. A
    timezone change and a removed `EarnedTrophy` row reach it the same way.

    SO THE STORED DATE SURVIVES CATALOGUE BOOKKEEPING, which is the rule the whole module already
    follows: fills are monotone for the same reason, and a square's date is the only record of the
    achievement once it is filled.

    ROWS WRITTEN BEFORE THIS FIX ONLY SELF-HEAL ON A PASS, and `runs_due_for_sweep` returns a run only
    when its counters moved -- so a dormant hunter's cross-lens date persists until `process_challenges
    --all-calendars` is run by hand. No backfill ships with it: the feature is unreleased, so the only
    rows that can hold the old value are development and seeded ones.
    """
    from django.db import transaction

    from challenges.models import (CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN, CALENDAR_VIEW_FIELDS,
                                   CHALLENGE_TYPE_CALENDAR, CalendarDay, Challenge)

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
            # THE LENS THIS SQUARE DREW IN BEFORE THIS PASS, read before the loop below can change it,
            # because a PROMOTION onto the board is the one event allowed to move `earned_on` forward.
            was_clean = row.in_clean
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

            # THE DATE FROM THE LENS THIS SQUARE DRAWS IN, per the docstring: `clean` when the square
            # is on the board, `all` when it only counts. Evaluated even when no view flipped.
            #
            # THE `in_clean` READ IS AFTER THE FLIP LOOP ABOVE, deliberately: a square promoted this
            # pass must take its clean date in the same pass, not on the next one.
            lens = CALENDAR_VIEW_CLEAN if row.in_clean else CALENDAR_VIEW_ALL
            offered = found[lens].get((row.month, row.day))
            promoted = row.in_clean and not was_clean
            if offered is None:
                # THE LENS NO LONGER CLAIMS A SQUARE IT ONCE DID -- fills are monotone, so a game
                # reclassified as shovelware leaves `in_clean` standing while the clean aggregate drops
                # the day. Keep the stored date: it is the only record of the achievement, and the
                # alternative is blanking a date because of catalogue bookkeeping the hunter never saw.
                pass
            elif row.earned_on is None:
                row.earned_on = offered.date
                changed = True
            elif promoted or offered.date < row.earned_on:
                # DOWNWARD ALWAYS, FORWARD ONLY ON A PROMOTION, and the promotion is tested rather than
                # inferred from the dates. A lower offer is a deeper backfill and is always taken. A
                # HIGHER one is taken only when this pass moved the square onto the board, because that
                # is the single case where the stored value describes the wrong population.
                #
                # THE INFERENCE THIS REPLACED WAS WRONG: "a higher offer can only mean the other lens
                # wrote it" assumes a lens's own earliest never rises, and it does. `update_shovelware`
                # flags a previously-clean game on a nightly pass, which drops the earliest day out of
                # the clean aggregate -- so `!=` re-dated a square for catalogue bookkeeping the hunter
                # never saw, which is exactly what the `offered is None` branch above exists to refuse.
                row.earned_on = offered.date
                changed = True

            # THE COUNT FOLLOWS THE AGGREGATE ALL THE WAY TO ZERO, which is where the first version
            # stopped short and left a real defect. It only wrote when `offered` was non-None, so a
            # square whose lens went EMPTY kept its last count: flag every one of a hunter's four
            # platinums on 3 March and the square still draws (fills are monotone, deliberately) while
            # the panel goes on printing "busiest day -- 4 on the 3rd" and the day modal, deriving its
            # satisfiers live, lists nothing. That is precisely the contradiction this field is live to
            # avoid, and the comment claimed the branch was skipped because "there is no aggregate to
            # follow" -- there is, and its value is 0.
            #
            # A DATE IS AN ACHIEVEMENT, A COUNT IS A TALLY, and that is the whole reason the two fields
            # part company here. The date survives the same reclassification (see the branch above):
            # catalogue bookkeeping the hunter never saw must not rewrite WHEN they earned a square. So
            # a reclassified square can read "first filled 2015" beside a count that holds nothing from
            # 2015, and that is deliberate rather than an oversight -- the date answers history, the
            # count answers what the modal will show.
            #
            # GATED ON THE ROW DRAWING OR COUNTING. `changed` also drives `newly_filled` and
            # `filled_at`, so letting a count correction set it on an UNFILLED row -- a hand-written
            # row, a half-run data migration -- would report a day the hunter never earned as newly
            # filled and stamp it. An unfilled square keeps whatever it has; nothing reads it.
            live = offered.plats if offered is not None else 0
            if row.plat_count != live and (was_filled or changed):
                row.plat_count = live
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
                to_update, ['in_all', 'in_clean', 'earned_on', 'plat_count', 'filled_at'],
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
    - (THERE WAS A SECOND WATERMARK HERE, `calendar_contracts_seen`, because the contracts view could
      move WITHOUT a platinum. The lens collapse deleted the column and this function's second
      question with it, so the reconciliation is now a single-column comparison -- which is what the
      body does. This list described two watermarks for several slices after there was one.)
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
    gap is documented rather than papered over.
    AND `plat_count` NARROWED THAT GUARANTEE, which this paragraph used to state unconditionally
    ("nothing is ever WRONG as a result: fills are monotone, so the cost is a day that fills late rather
    than a day that fills incorrectly"). That held while the stored state was monotone booleans and a
    frozen date. The per-day platinum count is deliberately LIVE, so a reclassification in either
    direction -- or a hunter changing their own timezone, which re-keys every day -- leaves it simply
    stale on a dormant hunter's run: too high after a flag, too low after an un-flag. (NOT a staff
    `igdb_id` edit, which this listed: that moves a CONTRACT's member concepts, and the platinum
    aggregate reads `ProfileGame.has_plat`, `Trophy.trophy_type` and `Game.shovelware_status` only. The
    cause came across from the deleted contracts lens.) What
    stays true is the part that matters for the reward ladder: FILLS are monotone, so no day fills
    incorrectly and no run completes that should not have. A stale tally in a side column is a smaller
    thing than a wrong square, which is why the gap is still accepted -- but it is no longer nothing.

    SCOPED TO RUNS, NOT ACCOUNTS, which is the principle `completable_slots` already states: "the sweep's
    cost should scale with how many runs are in flight, not with how many accounts exist." A hunter who
    never started a Calendar challenge has no run and nothing to compute -- the backfill is reachable
    only through a run, and a run exists only because somebody pressed Start.

    FINISHED RUNS ARE OUT OF SCOPE, which REVERSES an exception this function used to carry. It read
    "NOT SCOPED TO UNFINISHED RUNS... a Calendar run completes on the FIRST genuine view to fill, and
    the hunter may still be working toward the other view's ultimate title, so its days must keep
    filling afterwards". That justification died with the second lens: a complete run is 365
    shovelware-free days and `in_all` is a superset of `in_clean`, so no FILL on a finished run can
    move and no finished run can un-complete.

    "THERE IS NO COLUMN LEFT THAT CAN MOVE" is what this used to say, and `plat_count` falsified it.
    That column is deliberately live, so a reclassification moves it on a finished run like any other --
    and because this function filters `is_complete=False`, the nightly sweep can never be the thing that
    corrects it. `refresh_for_profile` carries no such filter, so a finished run is corrected the next
    time its owner syncs; what is left stale is a finished run belonging to a hunter who has stopped
    syncing, until `process_challenges --all-calendars`. Accepted on the same terms as the watermark gap
    above: the figure is a tally in a side column, and no square and no completion can be wrong.

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
