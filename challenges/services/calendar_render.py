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

from challenges.models import (CALENDAR_VIEW_CLEAN, CALENDAR_VIEW_CONTRACTS, CALENDAR_VIEW_FIELDS,
                               CALENDAR_MONTH_DAYS, CalendarDay, calendar_day_keys)

#: The metal a completed month is struck in, per the view that completed it. Hardest view, best metal.
#:
#: WHAT `.pp-med` ACTUALLY PROVIDES, stated carefully because an earlier version of this comment
#: oversold it and the overselling was load-bearing -- it claimed the crest "needs no authored
#: artwork". That is FALSE. `badge-medallion.css` is an art-layer compositor: `.pp-med__l` are `<img>`
#: elements fed from a badge's `art_layers`, and with none supplied the stage is empty. What IS
#: tier-driven off `data-tier` is `--med-c` and `--med-glow`, and through them the aura and the plate.
#: The ring lives inside `.pp-med__back` and renders only on the detail-modal flip face; the glint,
#: the cast shadow and the holographic foil are all tier-INDEPENDENT (the foil is gated on a per-badge
#: `is_holographic` flag, not on this attribute). So reusing `.pp-med` buys a tinted plate with an aura
#: and a size variable, and the twelve generated faces are still work to be designed.
CREST_METAL = {view: metal for (view, _field), metal in
               zip(CALENDAR_VIEW_FIELDS, ('bronze', 'silver', 'gold'))}

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
#: ONE LIST FOR BOTH PATHS, so there is no second copy to keep in step. The hero does not read
#: `earned_on`; a date column over at most 2,920 rows is not worth a divergence to avoid.
_CELL_FIELDS = ('challenge', 'month', 'day', 'in_all', 'in_clean', 'in_contracts', 'earned_on')


def _rows_by_key(rows):
    return {(row.month, row.day): row for row in rows}


def _view_state(row):
    """`{view: bool}` for one row (or for an absent one), derived from `CALENDAR_VIEW_FIELDS`.

    DERIVED RATHER THAN SPELLED OUT, which an earlier version of this module only half did: it built
    this dict from the constant and then wrote the three `in_*` keys out literally, so the generality
    was cosmetic and a fourth view would have raised `KeyError` two functions away.
    """
    return {view: bool(row and getattr(row, field)) for view, field in CALENDAR_VIEW_FIELDS}


def _counts(rows):
    """`{view: how many of `rows` are filled in it}`.

    IN PYTHON AND NOT IN THE DATABASE, which is worth defending rather than assuming, because the
    project's rule is that a per-user queryset producing aggregate output must aggregate in the
    database. The rule exists for querysets whose row count scales with the HUNTER -- a whale has
    250,000 `EarnedTrophy` rows. A Calendar run has exactly 365 `CalendarDay` rows whether the hunter
    holds three platinums or thirty thousand, and the board has to materialise every one of them to
    draw itself. So the rows are already in hand and counting them here costs nothing, where a
    `COUNT(*) FILTER (...) GROUP BY month` would add a query for data already fetched.

    A consumer that wants ONLY the counts -- a ladder row, a hub card, twelve crests with no board --
    should not come through here at all. `calendar_fill._recount_calendar` already does that aggregate
    properly, and that is the shape to copy.

    ONE PASS RATHER THAN THREE over the same list, which is the whole of the benefit. The obvious
    spelling -- a dict comprehension with a `sum(...)` per view -- walks `rows` once per view.

    IT IS ALSO GENERATOR-SAFE, AND NOTHING RELIES ON THAT, which is worth saying plainly because the
    first version of this note oversold it twice over. No caller passes a generator: `_hero_group`
    needs `rows` again for `_rows_by_key`, and `calendar_groups` counts from the cells. So the
    three-pass spelling would not have broken anything here, only been slower. And
    `rewards.granted_titles_for` was cited as a case this app had "already paid for"; its own docstring
    says the opposite -- "the hazard is worth naming even though it is now structurally absent". It was
    guarded, then designed out. Claiming a hazard as history is precisely the error the comment in
    `_cell` was corrected for, committed again one function later.
    """
    tally = {view: 0 for view, _field in CALENDAR_VIEW_FIELDS}
    for row in rows:
        for view, field in CALENDAR_VIEW_FIELDS:
            if getattr(row, field):
                tally[view] += 1
    return tally


def _cell(month, day, row, index):
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

    `earned_on` IS CROSS-LENS, AND THAT IS A RENDERING CONSTRAINT, not a detail. `calendar_fill` stores
    "the earliest date across every view holding this square -- whichever lens noticed", and the model
    keeps ONE date, so a per-lens date is not representable without a schema change. Two consequences a
    template must respect:

      - a day filled only in `contracts` carries a date while `views['all']` is False, so showing the
        date unconditionally prints "first filled 3 March 2019" on a square that is EMPTY in the lens
        being viewed;
      - a day first filled in `contracts` in 2017 and in `all` in 2019 reports 2017 in both lenses.

    So the date may only be REVEALED on a cell that is filled in the ACTIVE lens -- which the same
    `data-view` CSS that tints the cell already decides, so it costs nothing to get right and is
    invisible to get wrong. This is the one field in the dict that `views` cannot make honest, which is
    why it is called out rather than left for the template author to discover.

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
        # ALL THREE LENSES IN THE DOM, so switching lens costs no request -- the same both-states-
        # rendered technique the contract CTA uses, with CSS revealing the active one off `data-view`.
        'views': _view_state(row),
        'earned_on': row.earned_on if row else None,
        # POSITION IN THE WHOLE RUN, stamped here for the reason `slot_cards` stamps it rather than
        # letting the template read `forloop.counter0`: the cells are GROUPED into months, so a
        # template counter restarts every month and any stagger keyed on it silently dies. That one IS
        # recorded history -- it broke the lazy-image threshold on the jobs board.
        'index': index,
    }


def calendar_groups(challenge):
    """The run's days grouped by month -- what the detail board draws.

    `[{label, slug, cards, total, counts, crest, dom_id}]`, twelve of them, January first.

    NO `done` KEY, which is a deliberate departure from the seven-key shape `slot_groups` returns.
    A month has THREE progress numbers, one per view, and they do not nest -- `contracts` can be ahead
    of `all`. `slot_groups` includes `done` on both branches so a Python consumer cannot `KeyError` on
    one challenge type; here the equivalent care is to omit the key rather than pick one of three
    arbitrarily and have every reader silently get the wrong month some of the time.

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
    cells = [_cell(month, day, by_key.get((month, day)), index)
             for index, (month, day) in enumerate(calendar_day_keys())]

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
        # COUNTED FROM THE CELLS, not from the rows again, so a month head can never disagree with
        # the squares beneath it -- both read the one `views` dict `_view_state` built.
        month_counts = {view: sum(1 for cell in members if cell['views'][view])
                        for view, _field in CALENDAR_VIEW_FIELDS}
        groups.append({
            'label': MONTH_NAMES[index],
            'slug': MONTH_SLUGS[index],
            'cards': members,
            'total': days_in_month,
            # Per-view progress, which is what a month head is FOR: the page tally cannot say how much
            # of February is left, and February is the unit a crest is struck for.
            'counts': month_counts,
            'crest': _crest(month_counts, days_in_month),
            # TWELVE LITERAL SLUGS, so uniqueness is structural and `slot_render._with_dom_ids` is not
            # needed: its loop guards two groups sharing a slug-derived id, which cannot happen here.
            # A separate prefix because this is its own block, not a `.pp-csq-shelf`.
            'dom_id': 'cal-month-%s' % MONTH_SLUGS[index],
        })
    return groups


def calendar_boards_for(challenges):
    """`{challenge_id: [one unlabelled group of 365 days]}` -- the board a browse entry draws.

    ONE UNLABELLED GROUP, not twelve, mirroring the A-Z branch of `_board_groups` for the reason it
    gives: a hero row is a single shape read at a glance, and twelve labelled month heads inside a
    browse card would be a second page.

    ONE LENS, CHOSEN HERE, which is the part that cannot be left to the template. The three views are
    independent, so a board tinted by "filled in any of them" would show a run further along than any
    of its actual progress -- and the entry's tally next to it reads `filled_count`, which
    `_recount_calendar` defines as the better of CLEAN and CONTRACTS. So the hero draws exactly that
    lens and says which one it is, and the board and the tally cannot disagree.

    DERIVED FROM THE ROWS RATHER THAN FROM `Challenge.completed_view`, which is both equivalent and
    cheaper: whenever a view has completed, it is also the larger count, so the two agree -- and a tie
    at 365 resolves to contracts either way. Reading the column instead would mean touching a
    `Challenge` field, and a caller that loaded its runs with `.only(...)` would pay a deferred SELECT
    per run, reintroducing exactly the per-row fetch `_CELL_FIELDS` exists to prevent.

    A DAY CARRIES ONLY WHAT A HERO CELL CAN DRAW -- its date and whether it is filled in that lens.
    `boards_for` makes the same choice (a four-key square built inline rather than `_card`'s eight),
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
    """One run's hero board: `[]`, or a single group of 365 minimal day cells in one lens."""
    if not rows:
        return []

    view = _headline_view(_counts(rows))
    field = dict(CALENDAR_VIEW_FIELDS)[view]
    by_key = _rows_by_key(rows)
    days = []
    for month, day in calendar_day_keys():
        row = by_key.get((month, day))
        days.append({'month': month, 'day': day,
                     'filled': bool(row and getattr(row, field))})
    # `view` SO THE BOARD IS INTERPRETABLE. Without it the same grid of tints means a different
    # achievement from one entry to the next, which is the one thing a Hall of Fame must not do.
    # NO `label`/`slug`, which `_board_groups`' single-group branch does carry. They are always `''`
    # there too, but that group is drawn by `_run_hero.html`, which tests `group.label`. This one can
    # never be, because it deliberately does not use the `squares` key that template loops. Two keys
    # that are always empty and that no template can reach are what `_card`'s rule rejects, and they
    # would have survived a trim that minimised the cells beside them for exactly that reason.
    return [{'view': view, 'days': days}]


def _headline_view(counts):
    """Which lens a run leads with: the better of CLEAN and CONTRACTS.

    ALL PLATINUMS IS NEVER IT, and that is a decision rather than an omission. It is the lens
    shovelware inflates, it cannot finish a run, and leading with it would show an entry at 298/365
    whose run actually completes on a view sitting at 164. It carries the early day-marker ladder and
    nothing else.

    CONTRACTS WINS A TIE, matching `_recount_calendar`'s own tie-break on completion -- the rarer,
    harder achievement.
    """
    return (CALENDAR_VIEW_CONTRACTS
            if counts[CALENDAR_VIEW_CONTRACTS] >= counts[CALENDAR_VIEW_CLEAN]
            else CALENDAR_VIEW_CLEAN)


def _crest(counts, total):
    """The metal a month's crest is struck in, or `''` while no view has completed it.

    THE HIGHEST VIEW ACHIEVED, NOT A CHAIN, and this is load-bearing rather than a display preference.
    The views do not nest: a contract reaching its 100% tier with no platinum anywhere fills a
    contracts day and no platinum day, so a month can complete in `contracts` while `all` is still
    short. Gold without bronze. Rendering one crest per month makes that invisible; rendering all three
    side by side would expose it and read as a bug.

    IT IS THEREFORE A CROSS-LENS VALUE, WHICH ONLY THE CREST ROW MAY DRAW. This is the field `_cell`'s
    `tier` was deleted for being, so the difference has to be stated rather than assumed, and a review
    caught it being left unexamined here. `tier` sat on a cell INSIDE the lens-switched grid, where a
    single best-of value is wrong in whichever lens you are not looking at: a contracts-only day would
    have painted gold on a square that is empty in the All platinums lens.

    The crest does not sit there. The twelve crests are the month SWITCHER -- lens-independent chrome
    above the board, and the run's trophy shelf -- so "your best metal for February" is exactly the
    right thing for one to say, and switching lens must not change your trophy shelf.

    THE RULE THAT FOLLOWS: never render this inside the lens-switched board, and never in a month head
    that lives there. Anything lens-scoped reads `counts` and compares against `total` itself, which is
    the per-lens source and is already on the group for that purpose. A per-lens `{view: metal}` map was
    considered and not built: nothing needs it while the crest row is lens-independent chrome, and a
    second crest field would be the thing a template picks wrongly.

    SILVER WITHOUT BRONZE IS NOT REACHABLE, which is worth knowing before someone "fixes" it: `clean`
    implies `all` by construction and `calendarday_clean_implies_all` enforces it, so a complete clean
    month is always a complete all month too.

    `total` IS NEVER ZERO -- it is a month length from `CALENDAR_MONTH_DAYS` -- so there is no guard
    for it. An earlier version carried one, which was a guard that could not fire in a function whose
    own comment rejects a shared helper for having one.
    """
    metal = ''
    for view, _field in CALENDAR_VIEW_FIELDS:
        # `[view]` RATHER THAN `.get(view)`: the only caller builds `counts` from this same constant,
        # so every key exists -- and a `.get` would quietly SKIP an unmapped fourth view where the
        # subscript names it. Same call as dropping the `if not total` guard above.
        if counts[view] == total:
            metal = CREST_METAL[view]
    return metal
