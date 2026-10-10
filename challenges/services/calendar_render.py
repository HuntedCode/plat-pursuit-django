"""The 365 day squares of one Plat Calendar run, ready to draw. Flat in queries, always.

ONE QUERY, AND IT IS THE WHOLE COST. This is the cheapest of the three board renderers rather than the
most expensive, which is the opposite of what 365 cells suggests. `slot_render.boards_for` spends
between one and six queries resolving contracts, their concept membership, their covers and the job
catalogue, because every filled square wants cover art. A day has no art -- the owner settled that:
"I don't think we do cover art for the calendar. Instead, we can let the user click on a day and it'll
pop-up a modal that shows them games that satisfy that day" -- so a whole Hall of Fame page of Calendar
runs is ONE `CalendarDay` read, and nothing here touches `Contract`, `Concept` or `Game` at all.

That is also why the 365-cell board is affordable without minting anything. The problem was never the
cell COUNT, it was the image weight: a page of eight finished Calendar runs would have been roughly
2,920 covers, each drawn at a size where the art is unreadable. Dropping the art removes the cost
instead of optimising it.

A DAY HAS NO WEEKDAY, which is the thing most likely to be assumed and is worth stating before any
template is written. A square is a (month, day) pair with NO year -- "3 March" means every 3 March in
the hunter's history -- so there is no weekday to align a seven-column grid to, and an `S M T W T F S`
header would be a different lie every year. A month panel is a plain numbered grid.

WHY THIS IS A SEPARATE MODULE AND NOT A THIRD BRANCH OF `slot_render`, which is the first thing a
reader will want to collapse. The two existing types share a renderer because they share a CARD: both
carry `cover`, `job`, `game_name`, `xp_pending` and a contract snapshot behind them. A day carries none
of those, and carries per-view state a slot has no concept of. Making `slot_groups` return day cells
under the same `groups` variable would hand one template two different dict shapes, which is the defect
`boards_for` records paying for ("the A-Z branch carried an `icon` the jobs branch had dropped, so the
function returned two shapes depending on challenge type"). The markup differs regardless, so
dispatching at the call site keeps each renderer honest about what it returns.

AND THE HERO'S CELLS ARE DELIBERATELY NOT THE DETAIL'S. `slot_render` does the same thing for the same
reason -- `boards_for` builds a four-key square inline rather than reusing `_card`'s eight-key dict --
so a hero cell carries nothing it cannot draw at its size. The hero's group key is `days`, NOT the
`squares` that `_run_hero.html` loops, so wiring a Calendar run into the wrong board map renders
visibly nothing instead of 365 cells of garbage.
"""
import calendar
from fractions import Fraction

from django.utils import timezone

from challenges.models import (CALENDAR_DAY_MARKERS, CALENDAR_MONTH_DAYS, CalendarDay,
                               calendar_day_keys)
# IMPORTED, NOT RE-SPELLED. `_fold` is the one place that knows 29 February belongs to the 28th,
# and `_hunter_timezone` is the one place that knows whose clock a Calendar runs on. A second
# spelling of either is how the marker comes to sit on a different square than the fill did.
from challenges.services.calendar_fill import _fold, _hunter_timezone
from challenges.services.rewards import CALENDAR_DAY_TITLES

#: WHAT `.pp-med` ACTUALLY PROVIDES, kept because an earlier comment oversold it and the overselling
#: was load-bearing -- it claimed the crest "needs no authored artwork". That is FALSE.
#: `badge-medallion.css` is an art-layer compositor: `.pp-med__l` are `<img>` elements fed from a
#: badge's `art_layers`, and with none supplied the stage is empty. What IS tier-driven off `data-tier`
#: is `--med-c` and `--med-glow`, and through them the aura and the plate. The ring lives inside
#: `.pp-med__back` and renders only on the detail-modal flip face; the glint, the cast shadow and the
#: holographic foil are all tier-INDEPENDENT. So reusing `.pp-med` would buy a tinted plate with an
#: aura and a size variable, which is why the crest borrows only the palette.
#:
#: THERE WAS A `CREST_METAL` MAP (deleted), zipping the three lenses against bronze/silver/gold so a month's
#: crest said WHICH lens had completed it. One lens means one state -- struck or not -- so the metal
#: axis is free, and the owner has asked for it back as a per-MONTH colour instead (2026-10-04: "give
#: each medallion its own color that matches the month it represents"). That is the next slice, and it
#: is only coherent because the collapse freed the axis: a month hue and a lens metal on one object
#: were two colour systems competing for the same coin.

#: Stable, ASCII, three-letter month slugs for DOM ids and CSS hooks.
#:
#: NOT DERIVED FROM `calendar.month_abbr`, and the difference is a real bug rather than style. That
#: list is locale-sensitive, so slicing it would make a DOM id and a CSS selector change with the
#: server's locale, and an id that moves is an `aria-labelledby` that stops resolving.
MONTH_SLUGS = ('jan', 'feb', 'mar', 'apr', 'may', 'jun',
               'jul', 'aug', 'sep', 'oct', 'nov', 'dec')

#: The twelve visible month names, resolved ONCE at import.
#:
#: `calendar.month_name` IS NOT AN INDEXED LOOKUP, which an earlier version of this module asserted
#: while reading it per cell. `calendar._localized_month.__getitem__` evaluates
#: `datetime.date(2001, i + 1, 1).strftime(fmt)` on every access -- measured at roughly eighty times
#: the cost of a tuple index. Per cell that was 365 `strftime` calls to build one detail board and
#: about 2,900 for a Hall of Fame page, for strings the hero does not even draw.
#:
#: THE HOIST COSTS NOTHING, and an earlier version of this comment invented a trade-off to be fair to
#: the code it replaced: it said a label "cannot be translated per request" once resolved at import.
#: It never could. `strftime` reads the process-global C locale (`LC_TIME`), and Django's per-request
#: `translation.activate()` does not call `locale.setlocale`, so reading `calendar.month_name` per
#: request produced the same twelve English strings every time at eighty times the price.
#:
#: Translating these IS a real gap, just not one the hoist opened. `django.utils.dates.MONTHS` is the
#: lazily-translated mapping to switch to on the day the site has a second language.
MONTH_NAMES = tuple(calendar.month_name[month] for month in range(1, 13))

#: Only the columns a board reads.
#:
#: `filled_at` STAYS OUT: it is the write-time stamp, and nothing on any surface shows it -- `earned_on`
#: is the user-facing date, which is why the two are separate fields.
#:
#: `challenge` IS IN THE LIST AND IS NOT OPTIONAL, which is the `Contract.is_live` trap `cards_for`
#: documents, arriving by a different route. `calendar_boards_for` reads `row.challenge_id` openly, so
#: that one is obvious. `calendar_groups` never touches it -- and still needs it, because a reverse
#: related manager populates each row's parent cache from the FK, so omitting the column made Django
#: issue ONE deferred single-row SELECT PER DAY: a 365-row run measured 366 queries instead of 1,
#: turning the cheapest renderer in the app into the most expensive. Caught by the flatness test rather
#: than by review, which is why that test measures instead of asserting a shape.
#:
#: ONE LIST FOR BOTH PATHS, so there is no second copy to keep in step. The Hall of Fame hero reads
#: `plat_count` (its heatmap's shade and the plaque's total and busiest day) and `earned_on` (the years the
#: calendar took to fill), so the two paths need the same columns.
_CELL_FIELDS = ('challenge', 'month', 'day', 'in_all', 'in_clean', 'earned_on', 'plat_count')


def _rows_by_key(rows):
    return {(row.month, row.day): row for row in rows}


def _is_filled(row):
    """Is this day filled? One lens, so one boolean.

    It used to be `_view_state`, returning `{view: bool}` for three lenses so a cell could carry all of
    them and let CSS reveal the active one. With one lens a cell carries whether it is filled, and the
    `:has()` machinery, the duplicated per-lens figures and the lens-blind screen-reader text go with
    the dict.
    """
    return bool(row and row.in_clean)


def _cell(month, day, row, today=None):
    """One day square on the DETAIL board.

    `row` IS ALLOWED TO BE `None`, and that is what makes the board unable to draw FEWER squares than
    the run counts. Cells are generated from `calendar_day_keys()` and the rows looked up against them,
    rather than the rows being listed -- so a run missing some of its 365 rows (a shell, a data
    migration, an interrupted `bulk_create`) still draws a complete board, empty where the rows are
    gone. `slot_groups` names that hazard, and is careful to call it a hazard rather than history
    ("`Job.DISCIPLINES` and `DISCIPLINE_LABELS` currently carry the same five, so it has not
    happened"); an earlier version of this docstring promoted it to a scar it had paid for, which is
    the thing that comment had already been corrected twice for claiming.

    THE INVARIANT IS ONE-DIRECTIONAL, and saying otherwise was the more useful error. Generating from
    the keys makes the board immune to a MISSING row and BLIND to an EXTRA one: a row on an impossible
    date is never asked for, so it is invisible here while `_recount_calendar` still counts it toward
    completion. That gap is closed in the database rather than here, by `calendarday_day_within_month`
    -- the right place for it, since this renderer cannot see the row to complain about it.

    THE WHOLLY-ABSENT CASE IS HANDLED BY THE CALLER: generating from the keys means a run with NO rows
    would otherwise produce 365 confident empty cells and a board frame around nothing. That is
    `_board_groups`' genuine scar -- a finished A-Z run whose slot rows were deleted "passed the
    template's `{% if board %}` and drew the bare grey frame above a '26/26 squares' tally" -- so every
    entry point below returns `[]` for no rows at all.

    `earned_on` IS LENS-CORRECT AT SOURCE, and this paragraph used to say the opposite. While three
    lenses rendered at once the column held `min(date across every lens holding the square)` and no
    per-lens date was representable, so the constraint recorded here was that a date "may only be
    REVEALED on a cell filled in the ACTIVE lens" -- which a reader could satisfy and still print the
    wrong year, because the day being filled says nothing about which lens supplied the date. The side
    column did exactly that: "First filled -- 3 Mar 2015" from a shovelware platinum, on a board that
    counts nothing from 2015. `calendar_fill` now stores the date from the lens the square DRAWS in.

    HONEST ON ARRIVAL, NOT HONEST FOREVER, and the first version of this paragraph said the second. The
    date is written from the drawing lens and then FROZEN -- deliberately, because a date is the record
    of an achievement and catalogue bookkeeping the hunter never saw must not rewrite it. So after a
    shovelware flag an `in_clean` square can still carry a date whose platinum the board now excludes,
    and "First filled -- 3 Mar 2015" is reachable again by that route. `CalendarDay.plat_count`'s
    comment carries the full rule; what a renderer needs to know is that this field answers WHEN THE
    SQUARE WAS EARNED and not "what the board currently counts".

    WHAT REMAINS TRUE, and it is still the reason the date needs care: a square that is `in_all` and not
    `in_clean` carries a date and draws NOTHING. `filled` is the only thing that says whether a date
    belongs to the board, so a caller reading dates must test it -- which is what
    `calendar_groups`' `first`/`last` do, and what `_is_filled` exists to make one decision rather
    than twelve.

    NO `tier` KEY, and its removal is a correctness fix rather than a trim. It used to carry the
    HARDEST view a day had reached, which is a single cross-lens value on a board that renders all
    three lenses at once -- so it was wrong in two of them. A day with `in_contracts` alone (the
    designed-for case: `_detect_tiers` reaches the 100% tier with no platinum term) carried
    `tier='contracts'`, and any CSS tinting `[data-tier]` would have painted it gold while the viewer
    was in the All platinums lens, where that day is empty. The tint has to come from the per-lens
    boolean, so `views` is the only honest source and `tier` had no correct reader.
    """
    return {
        'month': month,
        'day': day,
        # ONE ATTRIBUTE FOR THE DOM TO CARRY, so the day modal asks for a day with a single value
        # rather than two that can arrive half-parsed. `ChallengeSlot.key` is the same idea.
        'key': '%d-%d' % (month, day),
        # Spelled out, because a cell shows a bare numeral and a screen reader needs the date.
        'label': '%s %d' % (MONTH_NAMES[month - 1], day),
        # ONE BOOLEAN. This was `views`, a `{lens: bool}` dict so all three lenses could sit in the DOM
        # and CSS could reveal the active one. One lens needs one answer.
        'filled': _is_filled(row),
        # WHETHER THIS SQUARE OPENS, and it is `in_all` rather than `filled` -- the owner's call
        # (2026-10-04): "the clickable squares should be the completed days", and then, on what an
        # incomplete day would show, the useful answer turned out to be the one square that HAS an
        # answer. A day in `all` and not `clean` holds a real platinum on a flagged game, so it can
        # explain why it is still open; a day with nothing has nothing to say and is inert.
        #
        # ONE ALREADY-STORED BOOLEAN, which reads exactly as "the hunter platinumed something on this
        # date". `in_clean` implies `in_all`, so every drawing square is included by construction and
        # the affordance never has to be two predicates kept in step.
        'counts': bool(row and row.in_all),
        'earned_on': row.earned_on if row else None,
        # TODAY, IN THE OWNER'S ZONE. A (month, day) comparison rather than a date, because a square has
        # no year -- "3 March" is every 3 March -- so there is nothing to compare a full date against.
        'is_today': today is not None and (month, day) == today,
        # HOW MANY PLATINUMS SIT ON THIS SQUARE, for the month's "busiest day" and, when the day modal
        # lands, for the hover summary that reads it straight off the cell rather than fetching.
        #
        # ONLY MEANINGFUL ON A FILLED CELL. An unfilled square carries the `all` count, so
        # it is shovelware-INCLUSIVE -- show it and a hover would report a figure the board excludes
        # while the modal derived clean satisfiers and listed fewer. `busiest` tests `filled` first and
        # the hover must do the same; the trap is the key being readable without that test.
        'plats': row.plat_count if row else 0,
        # NO `index`, AND THE REASONING INVERTED RATHER THAN SIMPLY BEING WRONG. One was stamped here
        # copying `slot_cards`, which carries a run-wide position precisely because a template counter
        # restarting per shelf broke its cascade. That argument does not transfer: the slot board is ONE
        # grid drawn once, while a Calendar is twelve panels of which one is visible, each re-entering
        # every time it is switched to -- so the entrance counter MUST restart per month, which is what
        # `forloop.counter0` already is. The run-wide version was worse than redundant: with the 12-step
        # cap on the delay, every cell in February through December shared one maximum delay, so eleven
        # months had no cascade and a switched-to panel arrived blank before popping all at once.
    }


def today_key(profile, *, now=None):
    """`(month, day)` for the hunter's own today, folded onto a square that exists.

    THE OWNER'S CLOCK, NOT THE READER'S. A run page is public, so these differ -- and every other date on
    the surface is resolved in the owner's zone, so taking the reader's would leave "today" as the one
    date on the board keyed differently from the squares around it. A visitor in Tokyo would see the
    marker on a square that is not today for the hunter whose calendar it is.

    FOLDED, so 29 February marks the 28th. There is no (2, 29) square -- `calendarday_day_within_month`
    forbids the row -- so without the fold the marker would simply vanish for a day every four years,
    which is the kind of absence nobody reports and nobody can reproduce.

    `now` IS A TEST SEAM AND NOTHING ELSE. The fold above cannot be observed for four years at a time and
    this project has no `freezegun`, so the only alternative was a source-text pin asserting that this
    module imports `_fold` -- which is precisely the pin shape the suite records as proving nothing about
    what a function does with what it imports.
    KEYWORD-ONLY, AND IT REFUSES A NAIVE INSTANT. `.astimezone()` on a naive datetime silently assumes the
    SERVER's zone, which is the exact confusion this function exists to prevent: it would answer in
    server-local terms, with no error, in the one place where whose clock is being read IS the question.
    """
    if now is None:
        now = timezone.now()
    elif timezone.is_naive(now):
        raise ValueError('today_key needs an aware instant: a naive one would read as the server zone')
    now = now.astimezone(_hunter_timezone(profile))
    return _fold(now.month, now.day)


def calendar_groups(challenge):
    """The run's days grouped by month -- what the detail board draws.

    `[{label, slug, cards, total, done, all_done, is_struck, open, shovelware, first, last, busiest,
    rank, dom_id}]`, twelve of them, January first. Written out because this block has been wrong twice: it
    advertised `counts` and `crest` long after both were removed, and it carried a paragraph headed
    "NO `done` KEY" while `done` sat in the returned dict two screens below.

    `done` IS THE ONE THE BOARD DRAWS and `all_done` is the comparison figure -- the same pair the
    header's rail shows. `done` was genuinely absent once, because a month had THREE progress numbers
    that did not nest and picking one would have been arbitrary; the one-lens collapse made that
    paragraph false rather than merely stale, so it is gone.

    `open`, `shovelware`, `first`, `last`, `busiest` and `rank` feed the side column and are arithmetic
    over cells already in hand -- they add no query. `busiest` is `{day, plats}` or `None`.
    `open` stays honest on an empty month (31, not 0) and the
    TEMPLATE decides not to draw it there; a figure that lies to spare a conditional is the worse
    trade. `rank` is dense and ranks on COMPLETION, not on raw days -- see the stamping loop.

    THE CONSEQUENCE IS A WIRING REQUIREMENT, not just a shape note, and it has to be said here because
    the existing detail template would not complain. `challenge_detail.html` renders a shelf head for
    any group with a truthy `label`, and month names are truthy -- so pointing that template at these
    groups prints " of 31 done" twelve times and tints every square empty, because it also reads
    `card.is_filled`/`card.is_completed`, which a day does not have. The Calendar needs its own board
    markup; these groups are not a drop-in for that one.

    ONE QUERY. The month labels, slugs, keys and metals are all module constants.
    """
    # `.order_by()` CLEARS `Meta.ordering` HERE TOO, for the reason `calendar_boards_for` gives: the
    # cells are built by looking rows up at `(month, day)` keys, so row order cannot reach the output.
    # Only one path carried this at first, which read as an oversight rather than a decision -- the
    # argument applies identically to both, so it is made in both.
    rows = list(challenge.calendar_days.only(*_CELL_FIELDS).order_by())
    # NO ROWS, NO BOARD. See `_cell`: generating from the keys would otherwise draw a confident empty
    # 365-cell board for a run that has none, which is how `{% if board %}` stops being a usable test.
    if not rows:
        return []

    by_key = _rows_by_key(rows)
    today = today_key(challenge.profile)
    cells = [_cell(month, day, by_key.get((month, day)), today)
             for month, day in calendar_day_keys()]
    # THE YEAR OVERVIEW IS A HEATMAP, like the Hall of Fame's year (owner, 2026-10-09): the same data drawn
    # two ways on two surfaces read as two different features. One peak for the whole run, so a month's
    # shade is comparable across the year; an unfilled day is 0 and draws as an open recess.
    peak = _heat_peak([c['plats'] for c in cells if c['filled']])
    for c in cells:
        c['heat'] = _heat_level(c['plats'], peak) if c['filled'] else 0

    groups = []
    start = 0
    for index, days_in_month in enumerate(CALENDAR_MONTH_DAYS):
        # SLICED, NOT FILTERED OR BUCKETED. `calendar_day_keys()` emits the year in order, so each
        # month is a contiguous run of exactly `CALENDAR_MONTH_DAYS[index]` cells and the month
        # lengths are the only thing needed to cut them. The two earlier versions of this both did
        # more work to get here -- a filter per month over all 365 cells, then a bucket dict with two
        # redundant guards for an out-of-range month that `calendar_day_keys()` cannot emit.
        members = cells[start:start + days_in_month]
        start += days_in_month
        # COUNTED FROM THE ROWS for `all` and from the CELLS for `clean`, which is not an inconsistency:
        # `clean` is what the squares draw, so counting it off the cells is what makes a month head
        # unable to disagree with the grid beneath it. `all` is the comparison figure and no cell shows
        # it, so it has nowhere else to come from.
        month_rows = [by_key[(c['month'], c['day'])] for c in members
                      if (c['month'], c['day']) in by_key]
        # `done` IS A SINGLE HONEST NUMBER AGAIN, which is the clearest thing the collapse bought back.
        # This group deliberately had NO `done` key while there were three lenses: a month had three
        # progress numbers that did not nest, so any single figure would have been one of three picked
        # arbitrarily and every reader would silently get the wrong month some of the time. One lens
        # means one answer. (This once added "and the shape matches `slot_groups`' seven keys again" --
        # it does not; the side column grew this dict well past that. The docstring lists the keys, and
        # stating a NUMBER here was wrong twice in two slices, so it no longer states one.)
        #
        # `all_done` IS THE COMPARISON FIGURE, not a second lens: the same days without the shovelware
        # exclusion. Nothing on the board draws it; `totals_for` sums it so a page can say "297 days, of
        # 340 you hold platinums for" rather than a bare figure.
        done = sum(1 for cell in members if cell['filled'])
        all_done = sum(1 for row in month_rows if row.in_all)
        # THE DATES OF THE FILLED DAYS ONLY, which is a distinction that matters: `earned_on` is written
        # for any population holding a square, so a SHOVELWARE day has a date and is not on the board.
        # Reading dates without the `filled` test would date the month from a square it does not draw.
        dates = sorted(c['earned_on'] for c in members if c['filled'] and c['earned_on'])

        # ── THE BUSIEST SQUARE (owner, 2026-10-04: "could we maybe add a 'busiest day' to the stats?").
        # FILLED SQUARES ONLY, for the same reason the dates test it: a shovelware-only day carries a
        # count and draws nothing, so advertising it would put a figure on the board for a population
        # the board excludes.
        #
        # TWO OR MORE, OR NOTHING. Every filled square has at least one platinum on it, so a "busiest
        # day" of 1 is just "a day you filled" wearing a superlative -- it would render on every month
        # with a single square and mean nothing. The row is absent until a day actually stacks.
        #
        # STRICT `>` OVER DAYS IN ORDER, so a tie keeps the EARLIEST day without needing a sort. `cards`
        # is built ascending, which is the property this relies on.
        busiest = None
        for cell in members:
            if not cell['filled'] or cell['plats'] < 2:
                continue
            if busiest is None or cell['plats'] > busiest['plats']:
                busiest = {'day': cell['day'], 'plats': cell['plats']}
        groups.append({
            'label': MONTH_NAMES[index],
            'slug': MONTH_SLUGS[index],
            'cards': members,
            'total': days_in_month,
            'done': done,
            'all_done': all_done,
            'is_struck': done == days_in_month,
            # ── THE SIDE COLUMN'S FIGURES, all of them arithmetic over cells already in hand (owner,
            # 2026-10-04: "could we potentially float something to the right side that helps the user in
            # some way? Some cool monthly stats or something?"). The board is ONE query and these do not
            # add to it, which is the constraint that shaped the list: the genuinely interesting stat --
            # how many games landed on a given day -- needs the hunter's trophy rows and belongs to the
            # day-detail slice, not here.
            'open': days_in_month - done,
            # DAYS THAT COUNT BUT DO NOT DRAW: in `in_all`, excluded from the board. This is the one
            # place a reader can see what "shovelware-free" is actually excluding, per month.
            'shovelware': all_done - done,
            'first': dates[0] if dates else None,
            'last': dates[-1] if dates else None,
            'busiest': busiest,
            # TWELVE LITERAL SLUGS, so uniqueness is structural and `slot_render._with_dom_ids` is not
            # needed: its loop guards two groups sharing a slug-derived id, which cannot happen here.
            # A separate prefix because this is its own block, not a `.pp-csq-shelf`.
            'dom_id': 'cal-month-%s' % MONTH_SLUGS[index],
            # THE MONTH THE BOARD OPENS ON. It was always January, because the template picked
            # `forloop.first` -- so a hunter arriving in October had to find October before the board
            # told them anything about now. The flag lives here rather than in the template so the
            # tab's `aria-selected` and the panel's `hidden` read ONE decision: rendered separately they
            # would eventually disagree, and the switcher's `isActive` reads `aria-selected`.
            'is_open': index + 1 == today[0],
        })

    # ── RANK AMONG THE TWELVE, stamped after the loop because it is the one figure a month cannot know
    # on its own. Dense ranking ("1224"), so two months at the same completion are both 3rd and the next
    # is 4th -- a competition ranking would skip to 5th and read as a gap the hunter cannot explain.
    #
    # ON COMPLETION, NOT ON RAW DAYS, because the template prints this as "2nd BEST" and the months are
    # not the same length. Ranked on `done` alone a COMPLETE February (28 of 28) placed behind an
    # incomplete January (29 of 31), and no 28-day month could ever be first against a long month with
    # 29 filled -- a hunter who had just finished a month would read "2nd best" as a bug, and be right.
    #
    # `Fraction`, NOT A FLOAT, and not for performance over twelve values. Ties are the whole reason
    # dense ranking is here, and ties across DIFFERENT denominators are where binary floats stop being
    # exact: 14/28 and 15/30 are the same number and must share a place. An exact ratio makes that a
    # property of the arithmetic rather than of how the two happened to round.
    #
    # A MONTH WITH NOTHING FILLED HAS NO RANK, rather than sharing last place with the other empties.
    # On a fresh run that would hand all twelve "1st", which is worse than saying nothing.
    for group in groups:
        group['rank'] = None
    scores = sorted({Fraction(g['done'], g['total']) for g in groups if g['done']}, reverse=True)
    for group in groups:
        if group['done']:
            group['rank'] = scores.index(Fraction(group['done'], group['total'])) + 1
    return groups


def calendar_boards_for(challenges):
    """`{challenge_id: [one unlabelled group of 365 days]}` -- the board a browse entry draws.

    ONE UNLABELLED GROUP, not twelve, mirroring the A-Z branch of `_board_groups` for the reason it
    gives: a hero row is a single shape read at a glance, and twelve labelled month heads inside a
    browse card would be a second page.

    ONE LENS, SO NOTHING TO CHOOSE. This used to pick which of three lenses the hero drew and say so,
    because a board tinted by "filled in any of them" would have overstated a run's progress. With one
    lens the board and the `filled_count` tally beside it read the same column by construction.

    A DAY CARRIES ONLY WHAT A HERO CELL CAN DRAW -- its date, whether it is filled, its platinum count and
    its heat level (see `_hero_group`). `boards_for` makes the same choice (a four-key square built inline rather than `_card`'s eight),
    and at roughly 2,900 cells per page the difference is real: the detail cell's label, modal key,
    per-lens booleans and run index have no reader at this size.

    THE GROUP KEY IS `days`, NOT `squares`, on purpose. `_run_hero.html` loops `group.squares`, so
    sharing that name would let a Calendar run wired into the wrong board map render 365 cells of
    garbage without raising. Under a different key it renders visibly nothing.

    ONE QUERY FOR THE WHOLE PAGE, however many runs. Unlike `boards_for` there is no range: there are
    no covers and no catalogue, so nothing is conditional. The caller is expected to pass Calendar runs
    only -- it already dispatches on type to choose this renderer -- so there is no type filter here,
    and a non-Calendar run passed in comes back `[]` exactly as a run with no days does.

    AN ENTRY FOR EVERY RUN ASKED ABOUT, so a caller can index the result without a `get`. That is the
    contract the Hall of Fame mixin already relies on for the other types, and it is what makes
    `{% if board %}` the reliable test on both.
    """
    ids = [c.pk for c in challenges]
    if not ids:
        return {}

    rows_by_run = {pk: [] for pk in ids}
    # `.order_by()` CLEARS `Meta.ordering`, which is a saving rather than a detail: the output is built
    # by looking rows up at `(month, day)` keys, so row order cannot reach it. An earlier version
    # ordered by `challenge_id, month, day` and justified it as preventing one run's board from
    # interleaving -- which is structurally impossible here, and a mutation deleting the clause
    # survived every test. What it bought was a real sort of up to 2,920 rows on every page.
    for row in CalendarDay.objects.filter(challenge_id__in=ids).only(*_CELL_FIELDS).order_by():
        rows_by_run[row.challenge_id].append(row)

    return {pk: _hero_group(rows) for pk, rows in rows_by_run.items()}


def _hero_group(rows):
    """One run's Hall of Fame board: `[]`, or a single group -- the year, as a HEATMAP.

    `{days, months, stats}`:

    - `days`: the 365 cells in year order, `{month, day, filled, plats, level}`. The flat list stays because
      it is also what tells the template a Calendar board arrived (`board.0.days`) rather than a slot one.
    - `months`: the same cells cut into twelve rows, `{num, abbr, days}`, which is what the board DRAWS --
      the run page's year overview, a row per month in that month's hue (owner, 2026-10-08).
    - `stats`: what the plaque leads with (see below).

    WHY A HEATMAP, AND NOT A FILL. A run in the Hall of Fame is FINISHED, so every one of its 365 days is
    filled -- and a board of filled squares was the same solid block for every hunter on the page. It said
    nothing about anyone, which is the one thing a Hall of Fame entry exists to do. `plat_count` (how many
    shovelware-free platinums landed on that date, across every year) is already in the row this reads, so
    shading by it costs no query and turns the block into the hunter's own year: their busy stretches, their
    quiet weeks.

    THE SHADE IS RELATIVE TO THE RUN'S OWN COUNTS, on a square-root curve, in `HEAT_LEVELS` steps. A fixed
    scale ("5+ is the darkest") would paint a whale's whole year at the top step and a modest hunter's at the
    bottom, so neither board would show a pattern. The square root lifts the common low counts off the bottom
    step, where a linear scale would leave most of a year looking empty.

    THE PEAK IS THE 95th-PERCENTILE DAY, NOT THE BUSIEST, because one spike would otherwise flatten the year
    back into the block this replaced: with a single 40-platinum day, days of 3 to 10 all landed on one
    shade. Days above the percentile simply take the top step. Cheap: one sort over at most 365 counts.

    `stats` -- the plaque's figures, because "365/365 days" is true of every finished run and so says
    nothing in a Hall of Fame (owner, 2026-10-08): `total` platinums laid on the calendar, the `busiest` day
    (`{month, day, plats, label}`, the earliest in the year on a tie), and `first_year`/`last_year` -- the
    years of the earliest and latest `earned_on`. `earned_on` is the date a day was FIRST filled, so the
    latest of them is the moment the calendar became complete in the hunter's own history.

    ONLY FILLED DAYS COUNT, for the reason `_cell` gives: a square that is `in_all` and not `in_clean` carries
    a shovelware count and a date but draws nothing, so it must not reach the board's shades or the plaque.
    """
    if not rows:
        return []

    by_key = _rows_by_key(rows)
    days, years = [], []
    for month, day in calendar_day_keys():
        row = by_key.get((month, day))
        filled = _is_filled(row)
        days.append({
            'month': month,
            'day': day,
            'filled': filled,
            'plats': (row.plat_count or 0) if filled else 0,
        })
        if filled and row.earned_on:
            years.append(row.earned_on.year)

    peak = _heat_peak([d['plats'] for d in days if d['filled']])
    for d in days:
        d['level'] = _heat_level(d['plats'], peak) if d['filled'] else 0

    months, start = [], 0
    for index, length in enumerate(CALENDAR_MONTH_DAYS):
        months.append({'num': index + 1, 'abbr': MONTH_SLUGS[index].upper(), 'days': days[start:start + length]})
        start += length

    busiest = max((d for d in days if d['filled']), key=lambda d: d['plats'], default=None)
    stats = {
        'total': sum(d['plats'] for d in days),
        'busiest': None if busiest is None or not busiest['plats'] else {
            'month': busiest['month'], 'day': busiest['day'], 'plats': busiest['plats'],
            # DAY-FIRST, matching the plaque's own record line ("8 Oct 2026").
            'label': '%d %s' % (busiest['day'], MONTH_NAMES[busiest['month'] - 1][:3]),
        },
        'first_year': min(years) if years else None,
        'last_year': max(years) if years else None,
    }
    return [{'days': days, 'months': months, 'stats': stats}]


#: How many shades a filled day can take on the Hall of Fame heatmap. Four is what reads at the ~8px cell a
#: phone gets: more steps and neighbouring shades stop being distinguishable, fewer and the year flattens
#: back into a block.
HEAT_LEVELS = 4

#: The percentile of a run's daily counts that the top shade is anchored to. See `_hero_group`.
HEAT_PEAK_PERCENTILE = 0.95


def _heat_peak(counts):
    """The count the top shade is anchored to: the `HEAT_PEAK_PERCENTILE` day, never below 1."""
    if not counts:
        return 0
    ordered = sorted(counts)
    return max(1, ordered[int(HEAT_PEAK_PERCENTILE * (len(ordered) - 1))])


def _heat_level(plats, peak):
    """1..HEAT_LEVELS for a FILLED day, relative to `peak` (see `_heat_peak`) on a square-root curve.

    A run whose peak is one platinum has nothing to shade between, so every counted day takes the top step:
    the board reads as complete rather than as uniformly faint. A filled day whose count is ZERO takes the
    bottom step rather than vanishing -- fills are monotone, so a day stays filled after a reclassification
    takes its only platinum to shovelware, while its live count follows the aggregate down to 0. Days above
    the peak (the top 5%) cap at the top step.
    """
    if not plats:
        return 1
    if peak <= 1:
        return HEAT_LEVELS
    ratio = min(1.0, (plats - 1) / (peak - 1))
    return 1 + round((HEAT_LEVELS - 1) * ratio ** 0.5)

def totals_for(months):
    """`{'done', 'all', 'struck', 'open'}` for the whole year.

    PURE, AND THAT IS THE POINT: the page needs the year totals for its tally and, shortly, for the
    day-marker rail -- and both are sums of numbers `calendar_groups` has already computed. Asking the
    database again would be a second query for data already on the page, and asking it DIFFERENTLY
    would be two definitions of one figure.

    NAMED KEYS, not a per-lens map. This returned `{view: total}` keyed on `CALENDAR_VIEW_FIELDS`
    while there were three lenses and a switcher to feed; `done` is now the figure the board draws, and
    `all` exists so the headline can be read aloud ("297 days, of 340 you hold platinums for") rather
    than as a bare number.

    `struck` AND `open` ARRIVED WITH THE ALL CREST, which needs year-level figures where a month panel
    shows its own. They are here rather than in the template for the reason the rest of this function is
    here: a template cannot subtract, so `open` would otherwise have become a filter or a second context
    value, and `struck` would have become a loop with a counter. Both are sums over `months`, so they
    add no query and cannot disagree with the board -- which is the whole argument for this function
    existing instead of a second aggregate.

    `open` IS SUMMED FROM THE MONTHS' OWN `open`, not recomputed and not taken from a literal 365. Each
    group already carries `open`, so `month['total'] - month['done']` here was a SECOND spelling of a
    figure the data already held -- which is the thing the paragraph above objects to, two lines after
    objecting to it. (A literal 365 would be worse still: the year's length is `CALENDAR_MONTH_DAYS`'
    sum, and the Feb-29 fold is what makes it 365 rather than 366.)
    """
    return {
        'done': sum(month['done'] for month in months),
        'all': sum(month['all_done'] for month in months),
        'struck': sum(1 for month in months if month['is_struck']),
        'open': sum(month['open'] for month in months),
    }


#: The day-marker ladder: how many filled days each rung asks for.
#:
#: DAYS, NOT MONTHS, and the arithmetic is what settled it rather than taste. Covering a calendar day
#: needs a platinum earned on that month and day in ANY year, so with N platinums spread across the year
#: expected coverage is `365 * (1 - e^(-N/365))`. Inverted, 50 days is about 54 platinums, 100 about 117,
#: 200 about 290, 300 about 630 and 365 about 2,153.
#:
#: WHICH IS WHY A MONTH LADDER WAS REJECTED: one full month needs roughly 1,250 platinums and all twelve
#: about 2,150, so twelve month rungs are not twelve steps but twelve steps clustered at the ceiling --
#: and because a run backfills a whole history on creation, the few hunters who qualify earn most of them
#: in a single lump on day one. Days move from a hunter's very first platinum, so every one of them has a
#: visible next rung.
#:
#: TREAT THOSE PLATINUM FIGURES AS A FLOOR ON DIFFICULTY, not an estimate: the model assumes platinums
#: fall uniformly across the year, and real ones cluster on weekends, release windows and holidays, so
#: true coverage is worse at every N.
#: RE-EXPORTED, NOT RE-SPELLED. The numbers moved to `challenges.models` when a SECOND reader arrived:
#: while the ladder was only drawn, a presentation constant in a presentation module was right, but
#: `rewards.CALENDAR_DAY_TITLES` now grants a title per rung, and two copies of a reward threshold is a
#: drift nobody notices until a hunter is owed a title the page does not show. The paragraphs above are
#: the ARGUMENT for the numbers and stay here; the numbers themselves have one home.
DAY_MARKERS = CALENDAR_DAY_MARKERS


def marker_rail(done, *, held, total=None):
    """The day-marker ladder for one run: `{done, total, next, next_title, to_next, markers}`.

    EACH RUNG SAYS WHAT IT AWARDS (owner, 2026-10-08: the rail "doesn't really do a great job of
    explaining what you get at the various milestones"). A marker carries its title from
    `rewards.CALENDAR_DAY_TITLES`, its STATE (below), how many days remain to it, `fill` (how far through
    ITS OWN SPAN the run is, 0-100), and `current`: whether the run's progress is inside this rung's span
    right now, which is the bar a phone shows.

    PER-RUNG SPANS, NOT ONE LINEAR TRACK. The ladder draws five equal cells, each filling from the rung
    before it to its own (0-50, 50-100, 100-200, 200-300, 300-365). The linear rail it replaced put the
    50 and 100 rungs 13% apart -- about 40px on a phone -- so they could never carry a title beside
    them. Equal cells give every rung room for its name, and the day count printed on each says how
    long its span is, so nothing is hidden by the spacing.

    `held` IS THE OWNER'S TITLES (`rewards.held_calendar_titles`), REQUIRED, with no default: an empty
    default would quietly mark every reached rung as merely "reached". The STATE combines it with the
    day count, because the two disagree in two real cases:

      earned   reached, and the title is held
      reached  reached, but the title row is not there yet: the grant runs on the recount and a raise
               there is logged and retried on the next one, so this is "pending", never a claim. (A NAME
               COLLISION does not land here: `_ensure_title` hands back the other system's row of the
               same name, so the name is held.)
      held     not reached on THIS run, but the title is already theirs from an earlier one
      next     the first rung neither reached nor held -- the same rule as `next_calendar_rung`, so the
               Start card and this ladder name the same next title
      locked   everything after it

    `next` and `to_next` stay for the opening ceremony, which says the same thing in a sentence.
    """
    total = DAY_MARKERS[-1] if total is None else total
    done = max(0, min(done, total))

    markers = []
    prev = 0
    nxt = None
    current = None
    for days in DAY_MARKERS:
        title = CALENDAR_DAY_TITLES.get(days, '')
        reached = done >= days
        if reached:
            state = 'earned' if title in held else 'reached'
        elif title in held:
            state = 'held'
        elif nxt is None:
            state, nxt = 'next', days
        else:
            state = 'locked'
        span = days - prev
        if not reached and current is None:
            current = days
        markers.append({
            'days': days,
            'title': title,
            'state': state,
            'reached': reached,
            'to_go': 0 if reached else days - done,
            # Rounded to whole percent: a width, so a fraction of a percent is invisible, and an
            # integer reads cleanly in the inline style.
            'fill': 100 if reached else (round(max(0, done - prev) * 100 / span) if span else 0),
            'finishes': days == total,
            'current': current == days,
        })
        prev = days

    return {
        'done': done,
        'total': total,
        'next': nxt,
        'next_title': CALENDAR_DAY_TITLES.get(nxt, '') if nxt else '',
        'to_next': None if nxt is None else nxt - done,
        'markers': markers,
    }
