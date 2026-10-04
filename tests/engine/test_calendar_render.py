"""The Plat Calendar's two board renderers, and the failures each is positioned to repeat.

WHY THIS FILE IS SHAPED THE WAY IT IS. A renderer that turns 365 rows into 365 cells is hard to get
visibly wrong -- a missing cell is a hole you can see. What is NOT visible is the set of failures the
other two challenge types have already hit, or been written to avoid:

  - a run with no rows drawing a confident empty board frame. REAL HISTORY: `_board_groups` records a
    finished A-Z run whose slot rows were deleted passing `{% if board %}` and drawing a bare frame
    above a "26/26 squares" tally.
  - a per-group template counter restarting, so a stagger or threshold keyed on it silently dies. REAL
    HISTORY: `slot_cards` stamps `index` because grouping the jobs board into shelves broke its lazy
    image threshold.
  - a board drawing FEWER squares than the run's tally claims. A HAZARD, NOT HISTORY -- `slot_groups`
    is explicit that it "has not happened", and an earlier version of this file asserted it as a scar
    the project had paid for, which is the thing that comment had already been corrected twice for.

ONE LENS, SINCE 2026-10-04. A day is filled by a shovelware-free platinum. There were three lenses and
a switcher, and the failure specific to this type was that they did not nest -- so any single cross-lens
value was wrong in whichever lens you were not looking at, which cost an audit finding twice (a per-cell
`tier`, then a per-month crest metal). Those values and the switcher are gone; what survives is a
comparison figure, `all_done`, which is the same days without the shovelware exclusion and which nothing
draws.
"""
import datetime as dt

import pytest
from django.db import connection, transaction
from django.db.utils import IntegrityError
from django.test.utils import CaptureQueriesContext

from challenges.models import CALENDAR_MONTH_DAYS, CalendarDay
from challenges.services import calendar_render
from tests.factories import ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db


def _hunter():
    # A MEMBER, because `start` is gated on the members-first beta and these runs go through the real
    # creation path rather than being built by hand.
    return ProfileFactory(user=UserFactory(), is_linked=True, user_is_premium=True)


def _calendar_run(profile=None):
    """A real Calendar run, created through the service with its 365 rows.

    THE SAME DOOR `test_calendar_fill` USES, lifting `TYPES_NOT_YET_CREATABLE` around the call: the
    type exists and has no creation door yet, and a renderer test wants the rows the real creation path
    makes rather than a hand-built approximation of them.
    """
    from challenges.models import CHALLENGE_TYPE_CALENDAR
    from challenges.services import challenge_service as svc

    original = svc.TYPES_NOT_YET_CREATABLE
    svc.TYPES_NOT_YET_CREATABLE = frozenset()
    try:
        return svc.start(profile or _hunter(), CHALLENGE_TYPE_CALENDAR)
    finally:
        svc.TYPES_NOT_YET_CREATABLE = original


def _fill(run, month, day, *, on=None, plats=1):
    """Mark one day filled, writing the row directly.

    DIRECT ROW WRITES ON PURPOSE. What fills a day is `calendar_fill`'s job and has its own file; this
    one asks what the renderer does with rows already in whatever state.

    THE ROWCOUNT IS ASSERTED, which is not pedantry: `.update()` on a mistyped month or day is a silent
    no-op, and several tests below assert that a month is NOT struck -- so a typo would make them pass
    for the wrong reason.
    """
    # BOTH COLUMNS, because `in_clean` implies `in_all` and the database enforces it. A test that wants
    # a shovelware day -- in `all` and not on the board -- writes the row itself.
    # `plat_count` DEFAULTS TO 1 RATHER THAN 0, because that is what a real fill writes for a square it
    # has just filled -- a square is filled BY a platinum. The same reasoning that made this helper
    # write `earned_on`, after two stat rows rendered empty against a gap in the FIXTURE rather than in
    # the page.
    # IT IS NOT AN IMPOSSIBLE STATE, which this comment claimed ("a state no backfill can produce"). A
    # filled square with a zero count is exactly what a lens going empty produces -- the square stays
    # (fills are monotone) and the tally follows the aggregate to zero. See
    # `test_a_square_whose_lens_empties_drops_its_count_to_zero`. 1 is the right DEFAULT for a helper
    # that means "this day got filled", not a claim about the column's range.
    # `dt.date(2019, month, day)`, NOT `min(day, 28)`. The clamp made 29 of the 365 squares hold a
    # date that was not their own -- `(1, 31)` stored 28 January -- which is the same defect the seeder
    # carried and had removed, re-created in the fixture. It is also not harmless: filling days 29 and
    # 30 of one month gave both the same date, so `first == last` and the "Most recent" row silently
    # dropped. Every `CalendarDay` key is a real date (February stops at 28), so no clamp is needed.
    fields = {'in_all': True, 'in_clean': True, 'plat_count': plats,
              'earned_on': on or dt.date(2019, month, day)}
    written = CalendarDay.objects.filter(challenge=run, month=month, day=day).update(**fields)
    assert written == 1, 'no row at (%d, %d) -- the fill was a silent no-op' % (month, day)


def _fill_month(run, month):
    for day in range(1, CALENDAR_MONTH_DAYS[month - 1] + 1):
        _fill(run, month, day)


def _month(groups, month):
    return groups[month - 1]


# ── the board's shape ────────────────────────────────────────────────────────────────────────────────

def test_the_board_draws_one_square_for_every_day_row_the_run_owns():
    """DERIVED FROM THE ROWS, which the obvious spelling of this test is not.

    Asserting `sum(len(cards)) == 365` or `== run.total_slots` is tautological here: the cells are
    generated from `calendar_day_keys()`, so the left side is 365 by construction and the right side is
    a frozen 365. Neither can fail from a renderer defect. Counting the actual rows and comparing makes
    the assertion about the renderer.
    """
    run = _calendar_run()
    groups = calendar_render.calendar_groups(run)

    drawn = {(card['month'], card['day']) for group in groups for card in group['cards']}
    stored = set(run.calendar_days.values_list('month', 'day'))
    assert drawn == stored
    assert len(drawn) == run.total_slots


def test_a_run_draws_twelve_months_at_their_real_lengths():
    groups = calendar_render.calendar_groups(_calendar_run())
    assert len(groups) == 12
    assert [g['total'] for g in groups] == list(CALENDAR_MONTH_DAYS)
    assert [len(g['cards']) for g in groups] == list(CALENDAR_MONTH_DAYS)


def test_each_month_group_holds_only_its_own_days_in_order():
    """The months are SLICED out of the ordered year rather than filtered, so an off-by-one in the
    running offset would hand February a January day without changing any length."""
    for index, group in enumerate(calendar_render.calendar_groups(_calendar_run())):
        month = index + 1
        assert {card['month'] for card in group['cards']} == {month}
        assert [card['day'] for card in group['cards']] == list(
            range(1, CALENDAR_MONTH_DAYS[index] + 1))


def test_the_board_starts_on_january_first_and_ends_on_december_thirty_first():
    groups = calendar_render.calendar_groups(_calendar_run())
    first, last = groups[0]['cards'][0], groups[-1]['cards'][-1]
    assert (first['month'], first['day']) == (1, 1)
    assert (last['month'], last['day']) == (12, 31)


def test_february_has_no_twenty_ninth():
    """A leap day FOLDS into the 28th rather than getting a square of its own.

    The square count is the visible half of that decision; `test_calendar_fill` owns the other half (a
    platinum earned on 29 February fills the 28th rather than vanishing). Both halves have to agree or
    a real platinum has nowhere to land.
    """
    february = _month(calendar_render.calendar_groups(_calendar_run()), 2)
    assert february['total'] == 28
    assert max(card['day'] for card in february['cards']) == 28


def test_a_month_head_names_itself_and_a_cell_names_its_date():
    groups = calendar_render.calendar_groups(_calendar_run())
    march = _month(groups, 3)
    assert march['label'] == 'March'
    assert march['slug'] == 'mar'
    # A cell shows a bare numeral, so the spelled-out date is all a screen reader has.
    assert march['cards'][2]['label'] == 'March 3'


def test_a_cells_modal_key_names_the_month_before_the_day():
    """A NON-PALINDROMIC DATE, which is the whole point of this test.

    The only key assertion used to be March 3 -- `'3-3'` -- so a transposed `'%d-%d' % (day, month)`
    passed it, and a mutation proved it. The day modal's single-value contract needs a date whose two
    halves differ.
    """
    groups = calendar_render.calendar_groups(_calendar_run())
    assert _month(groups, 2)['cards'][0]['key'] == '2-1'
    assert _month(groups, 11)['cards'][24]['key'] == '11-25'


def test_month_dom_ids_are_unique_so_every_head_can_name_its_own_section():
    """Duplicate ids make every `aria-labelledby` resolve to the FIRST matching heading.

    `slot_render._with_dom_ids` exists because slug-derived ids there CAN collide. Twelve literal month
    slugs cannot, which is why this renderer does not borrow that helper -- but the property it was
    protecting still has to hold, so it is asserted rather than assumed.
    """
    ids = [g['dom_id'] for g in calendar_render.calendar_groups(_calendar_run())]
    assert len(set(ids)) == 12
    assert ids[0] == 'cal-month-jan' and ids[-1] == 'cal-month-dec'


def test_a_cell_carries_no_run_wide_position():
    """THE INVERSION OF `slot_cards`' RULE, pinned because it looks like that rule being broken.

    A slot card carries a run-wide `index` because its squares are ONE grid, where a template counter
    restarting per shelf broke the cascade across it. A Calendar is twelve panels of which one is ever
    visible, each re-entering whenever it is switched to -- so its entrance counter must restart per
    month, and `forloop.counter0` in the panel loop already is that.

    A run-wide index here was worse than redundant. The delay is capped at twelve steps, so every cell in
    February through December shared one maximum delay: eleven months had no cascade at all, and because
    un-hiding an element restarts its animations, a month switch slid in a completely blank panel and
    then popped all 31 squares at once.
    """
    card = _month(calendar_render.calendar_groups(_calendar_run()), 2)['cards'][0]
    assert 'index' not in card

    board = open('templates/challenges/partials/_calendar_board.html', encoding='utf-8').read()
    assert '--rev: {{ forloop.counter0 }}' in board, (
        'the cascade counter has to restart per month, which only the panel loop knows')


# ── the three lenses on one cell ─────────────────────────────────────────────────────────────────────

def test_a_filled_cell_says_so_with_one_boolean():
    """ONE LENS, ONE ANSWER. A cell used to carry `views`, a `{lens: bool}` dict, so all three lenses
    could sit in the DOM and CSS could reveal the active one. The dict went with the switcher."""
    run = _calendar_run()
    _fill(run, 3, 3)

    card = _month(calendar_render.calendar_groups(run), 3)['cards'][2]
    assert card['filled'] is True
    assert 'views' not in card
    assert 'tier' not in card, (
        'a cross-lens "best lens reached" value was deleted before the collapse and must not return')


def test_an_unfilled_cell_is_false_and_has_no_date():
    card = _month(calendar_render.calendar_groups(_calendar_run()), 7)['cards'][0]
    assert card['filled'] is False
    assert card['earned_on'] is None


def test_a_filled_cell_carries_the_date_it_was_earned():
    run = _calendar_run()
    _fill(run, 3, 3, on=dt.date(2019, 3, 3))
    card = _month(calendar_render.calendar_groups(run), 3)['cards'][2]
    assert card['earned_on'] == dt.date(2019, 3, 3)


def test_a_shovelware_platinum_fills_no_square():
    """THE ONE LENS IS SHOVELWARE-FREE, which is the whole content of the collapse: a platinum on a
    flagged game counts toward the comparison figure and draws nothing."""
    profile = _hunter()
    run = _calendar_run(profile)
    CalendarDay.objects.filter(challenge=run, month=5, day=5).update(
        in_all=True, in_clean=False, earned_on=dt.date(2019, 5, 5))

    groups = calendar_render.calendar_groups(run)
    card = _month(groups, 5)['cards'][4]
    assert card['filled'] is False, 'a shovelware platinum must not draw a square'
    assert _month(groups, 5)['done'] == 0
    assert _month(groups, 5)['all_done'] == 1, 'but it is counted in the comparison figure'


def test_a_month_reports_one_progress_number_and_its_comparison():
    """`done` IS A SINGLE HONEST NUMBER AGAIN. This group deliberately had NO `done` key while there
    were three lenses -- a month had three progress figures that did not nest, so any single one would
    have been picked arbitrarily and every reader would silently get the wrong month some of the time.
    One lens means one answer, and the shape matches `slot_groups`' again.

    `all_done` IS THE COMPARISON, not a second lens: the same days without the shovelware exclusion.
    """
    run = _calendar_run()
    _fill(run, 1, 1)
    _fill(run, 1, 2)
    CalendarDay.objects.filter(challenge=run, month=1, day=3).update(in_all=True, in_clean=False)

    january = _month(calendar_render.calendar_groups(run), 1)
    assert january['done'] == 2
    assert january['all_done'] == 3
    assert january['total'] == 31
    assert 'counts' not in january, 'the per-lens map went with the switcher'


def test_a_month_head_agrees_with_the_squares_beneath_it():
    """The head counts off the CELLS, not the rows again, so the two cannot drift."""
    run = _calendar_run()
    for day in (4, 9, 17):
        _fill(run, 6, day)

    june = _month(calendar_render.calendar_groups(run), 6)
    assert june['done'] == sum(1 for card in june['cards'] if card['filled']) == 3


def test_an_incomplete_month_is_not_struck():
    run = _calendar_run()
    _fill(run, 1, 1)
    january = _month(calendar_render.calendar_groups(run), 1)
    # Asserted beside a non-zero count, so it cannot pass against a renderer that fills nothing.
    assert january['done'] == 1
    assert january['is_struck'] is False


def test_a_month_is_struck_when_every_one_of_its_days_is_filled():
    """A BOOLEAN, NOT A METAL. The crest used to be bronze/silver/gold for the hardest of three lenses
    to complete the month, which made it a cross-lens value on a lens-switched page and cost two audit
    findings. One lens means one state."""
    run = _calendar_run()
    # February, because 28 days is the cheapest complete month to write.
    for day in range(1, 29):
        _fill(run, 2, day)

    february = _month(calendar_render.calendar_groups(run), 2)
    assert february['is_struck'] is True
    assert february['done'] == february['total'] == 28


def test_one_missing_day_does_not_strike_a_month():
    run = _calendar_run()
    for day in range(1, 28):
        _fill(run, 2, day)
    february = _month(calendar_render.calendar_groups(run), 2)
    assert february['done'] == 27
    assert february['is_struck'] is False


def test_a_month_full_of_shovelware_platinums_is_not_struck():
    """The comparison figure can be complete while the board is empty, and the crest follows the BOARD.
    This is the clearest statement of what the one lens means."""
    run = _calendar_run()
    CalendarDay.objects.filter(challenge=run, month=2).update(in_all=True, in_clean=False)

    february = _month(calendar_render.calendar_groups(run), 2)
    assert february['all_done'] == 28
    assert february['done'] == 0
    assert february['is_struck'] is False


def test_the_year_totals_are_the_sum_of_the_months():
    run = _calendar_run()
    _fill(run, 1, 1)
    _fill(run, 6, 6)
    CalendarDay.objects.filter(challenge=run, month=9, day=9).update(in_all=True, in_clean=False)

    totals = calendar_render.totals_for(calendar_render.calendar_groups(run))
    assert totals == {'done': 2, 'all': 3}


def test_a_run_with_no_day_rows_draws_no_board_at_all():
    """`_board_groups`' real scar, in a renderer that generates its cells from the KEYS and so would
    otherwise be structurally unable to notice. A finished A-Z run whose slot rows were deleted "passed
    the template's `{% if board %}` and drew the bare grey frame above a '26/26 squares' tally"."""
    run = _calendar_run()
    run.calendar_days.all().delete()

    assert calendar_render.calendar_groups(run) == []
    assert calendar_render.calendar_boards_for([run]) == {run.pk: []}


def test_a_run_missing_one_day_row_still_draws_that_square():
    """The hazard `slot_groups` names: a board drawing fewer squares than its tally claims leaves a
    square with no DOM and therefore no way to ever fill."""
    run = _calendar_run()
    run.calendar_days.filter(month=3, day=3).delete()

    groups = calendar_render.calendar_groups(run)
    assert sum(len(g['cards']) for g in groups) == 365
    orphan = _month(groups, 3)['cards'][2]
    assert (orphan['month'], orphan['day']) == (3, 3)
    assert orphan['filled'] is False
    assert orphan['earned_on'] is None


def test_the_hero_also_draws_a_missing_day_as_empty():
    """The absent-row path through the OTHER entry point, which had no coverage of its own."""
    run = _calendar_run()
    _fill(run, 5, 5)
    run.calendar_days.filter(month=5, day=6).delete()

    days = calendar_render.calendar_boards_for([run])[run.pk][0]['days']
    assert len(days) == 365
    fifth, sixth = days[124], days[125]
    assert (fifth['month'], fifth['day'], fifth['filled']) == (5, 5, True)
    assert (sixth['month'], sixth['day'], sixth['filled']) == (5, 6, False)


@pytest.mark.parametrize('month,day', [(2, 29), (2, 31), (4, 31), (6, 31), (9, 31), (11, 31)])
def test_a_day_that_does_not_exist_in_its_month_is_refused_by_the_database(month, day):
    """THE GAP THE RENDERER CANNOT SEE, so it is closed in the database instead.

    `calendarday_day_range` caps the day at 31 for every month, so these six dates were all legal.
    The renderer generates its cells from `calendar_day_keys()` and looks rows up against them, which
    makes it immune to a missing row and BLIND to an extra one -- so a row on an impossible date was
    invisible on the board forever, while `_recount_calendar` aggregates with no key filter and would
    still count it toward `filled_count` and toward completing the run. A square its owner can neither
    see nor reach could finish their Calendar.
    """
    run = _calendar_run()
    with pytest.raises(IntegrityError), transaction.atomic():
        CalendarDay.objects.create(challenge=run, month=month, day=day, in_all=True)


@pytest.mark.parametrize('month,day', [(1, 31), (4, 30), (2, 28), (12, 31)])
def test_a_day_that_does_exist_in_its_month_is_still_allowed(month, day):
    """The other half of the constraint: it must not refuse any real date. The 365 the creation path
    writes are the proof at large, but the month-end boundaries are where an off-by-one would hide."""
    run = _calendar_run()
    run.calendar_days.filter(month=month, day=day).delete()
    CalendarDay.objects.create(challenge=run, month=month, day=day, in_all=True)
    assert run.calendar_days.filter(month=month, day=day).exists()


# ── the hero board ───────────────────────────────────────────────────────────────────────────────────

def test_the_hero_board_is_one_unlabelled_group_of_every_day():
    """Twelve labelled month heads inside a browse card would be a second page. The A-Z branch of
    `_board_groups` makes the same call for the same reason: a hero row is one shape read at a
    glance."""
    run = _calendar_run()
    groups = calendar_render.calendar_boards_for([run])[run.pk]

    assert len(groups) == 1
    assert len(groups[0]['days']) == 365
    # NO always-empty `label`/`slug`. `_board_groups`' single-group branch carries them because
    # `_run_hero.html` tests `group.label`; this group is never drawn by that template, so two keys
    # that are permanently `''` and unreachable are what `_card`'s rule rejects.
    # ONE KEY. A `view` sat beside `days` naming which of three lenses the band was drawn in, so a
    # reader could tell two entries apart. One lens means every band means the same thing.
    assert set(groups[0]) == {'days'}


def test_the_hero_does_not_reuse_the_key_the_other_heroes_loop():
    """`_run_hero.html` loops `group.squares`. Sharing that name would let a Calendar run wired into
    the wrong board map render 365 cells of garbage without raising -- a day has no `cover` and its
    `label` would print "January 1" inside a 32px cell. Under a different key it renders nothing,
    which is a bug you can see."""
    run = _calendar_run()
    group = calendar_render.calendar_boards_for([run])[run.pk][0]
    assert 'squares' not in group
    assert 'days' in group


def test_a_hero_day_carries_only_what_a_hero_cell_can_draw():
    """`boards_for` builds a four-key hero square inline rather than reusing `_card`'s eight, so the
    hero carries nothing it cannot draw. At roughly 2,900 cells per page the detail cell's label,
    modal key, per-lens booleans and run index have no reader at this size."""
    run = _calendar_run()
    day = calendar_render.calendar_boards_for([run])[run.pk][0]['days'][0]
    assert set(day) == {'month', 'day', 'filled'}


def test_the_hero_draws_the_same_lens_the_board_does():
    """NOTHING TO CHOOSE ANY MORE. The hero used to pick the better of two lenses and report which one
    it had drawn, because a band tinted by "filled in any lens" would have overstated the run. One lens
    means the band and the `filled_count` tally beside it read the same column by construction."""
    run = _calendar_run()
    for day in range(1, 4):
        _fill(run, 2, day)
    CalendarDay.objects.filter(challenge=run, month=3).update(in_all=True, in_clean=False)

    group = calendar_render.calendar_boards_for([run])[run.pk][0]
    assert 'view' not in group, 'the band no longer has a lens to name'
    assert sum(1 for d in group['days'] if d['filled']) == 3, (
        'a month of shovelware platinums must not tint the band')


def test_the_hero_days_run_in_calendar_order():
    run = _calendar_run()
    days = calendar_render.calendar_boards_for([run])[run.pk][0]['days']
    assert [(d['month'], d['day']) for d in days[:2]] == [(1, 1), (1, 2)]
    assert (days[-1]['month'], days[-1]['day']) == (12, 31)
    assert days == sorted(days, key=lambda d: (d['month'], d['day']))


def test_every_run_asked_about_gets_an_entry_even_with_nothing_to_draw():
    """The contract the Hall of Fame mixin already relies on for the other types, so `{% if board %}`
    is the reliable test on both and no caller needs a `get` with a default."""
    full = _calendar_run()
    empty = _calendar_run()
    empty.calendar_days.all().delete()

    boards = calendar_render.calendar_boards_for([full, empty])
    assert set(boards) == {full.pk, empty.pk}
    assert boards[empty.pk] == []
    assert len(boards[full.pk][0]['days']) == 365


def test_no_runs_asked_about_costs_nothing():
    with CaptureQueriesContext(connection) as captured:
        assert calendar_render.calendar_boards_for([]) == {}
    assert len(captured) == 0


def test_the_hero_keeps_each_runs_days_to_that_run():
    one = _calendar_run()
    two = _calendar_run()
    # March 3 is index 61: January's 31 days, February's 28, then the 3rd.
    _fill(one, 3, 3)

    boards = calendar_render.calendar_boards_for([one, two])
    assert boards[one.pk][0]['days'][61]['filled'] is True
    assert boards[two.pk][0]['days'][61]['filled'] is False


# ── flatness, which is what lets a board onto a browse surface ───────────────────────────────────────

def test_the_detail_board_costs_one_query():
    run = _calendar_run()
    _fill_month(run, 2)

    with CaptureQueriesContext(connection) as captured:
        calendar_render.calendar_groups(run)
    assert len(captured) == 1, [q['sql'] for q in captured]


def test_a_whole_hall_of_fame_page_costs_one_query_however_many_runs():
    """THE PROPERTY, and the reason this renderer is cheaper than the other two rather than more
    expensive. `boards_for` pays one to six queries resolving contracts, membership, covers and the job
    catalogue. A day has no art, so there is nothing conditional left to read."""
    runs = [_calendar_run() for _ in range(3)]
    _fill(runs[0], 5, 5)

    with CaptureQueriesContext(connection) as one_run:
        calendar_render.calendar_boards_for(runs[:1])
    with CaptureQueriesContext(connection) as three_runs:
        calendar_render.calendar_boards_for(runs)

    assert len(one_run) == 1, [q['sql'] for q in one_run]
    assert len(three_runs) == len(one_run), (
        'the cost must not move with the number of runs on the page')


def test_neither_board_asks_the_database_to_sort():
    """THE SAVING `.order_by()` BUYS, pinned because nothing else can see it.

    Both renderers build their output by looking rows up at `(month, day)` keys, so row order cannot
    reach it -- which a mutation run proved twice over: deleting the `.order_by()` and reinstating a
    full `ORDER BY challenge_id, month, day` were BOTH invisible to every other test here. That makes
    the clause a pure cost that can silently come back, and on a Hall of Fame page it is a sort of up
    to 2,920 rows on every request.
    """
    runs = [_calendar_run() for _ in range(2)]

    with CaptureQueriesContext(connection) as hero:
        calendar_render.calendar_boards_for(runs)
    with CaptureQueriesContext(connection) as detail:
        calendar_render.calendar_groups(runs[0])

    for captured in (hero, detail):
        assert 'ORDER BY' not in captured[0]['sql'].upper(), captured[0]['sql']


def test_a_fully_filled_board_costs_no_more_than_an_empty_one():
    """Flatness in the OTHER axis: filled cells must not each resolve something of their own. That is
    the shape the project has been bitten by twice, and it is why nothing here touches `Contract`,
    `Concept` or `Game`."""
    empty = _calendar_run()
    full = _calendar_run()
    for month in range(1, 13):
        _fill_month(full, month)

    with CaptureQueriesContext(connection) as empty_cost:
        calendar_render.calendar_groups(empty)
    with CaptureQueriesContext(connection) as full_cost:
        calendar_render.calendar_groups(full)

    assert len(full_cost) == len(empty_cost) == 1


# ── the day-marker rail ──────────────────────────────────────────────────────────────────────────────

def test_the_rail_reports_the_ladder_and_where_the_hunter_stands():
    from challenges.services.calendar_render import DAY_MARKERS, marker_rail

    rail = marker_rail(297)
    assert rail['done'] == 297
    assert rail['total'] == 365
    assert [m['days'] for m in rail['markers']] == list(DAY_MARKERS)
    assert [m['reached'] for m in rail['markers']] == [True, True, True, False, False]
    assert rail['next'] == 300
    assert rail['to_next'] == 3


def test_the_rail_is_linear_in_days():
    """A DELIBERATE CHOICE, and the flattering one. The rail measures days, so a day is the same distance
    everywhere along it -- but the DIFFICULTY is wildly non-linear (`DAY_MARKERS` carries the
    coupon-collector arithmetic: the 50 rung is roughly 54 platinums, the 300 rung roughly 630). Spacing
    the rungs by effort would make the rail lie about the quantity it measures, which is the worse of the
    two errors; this pins the choice so nobody "fixes" it without reading why.
    """
    from challenges.services.calendar_render import marker_rail

    rail = marker_rail(0)
    for marker in rail['markers']:
        assert marker['pct'] == round(marker['days'] * 100.0 / 365, 2)
    # Which means the gaps are proportional to days and NOT to the climb: 50->100 is the same width as
    # 250->300, while the second costs several hundred more platinums.
    pcts = [m['pct'] for m in rail['markers']]
    assert round(pcts[1] - pcts[0], 2) == round(50 * 100.0 / 365, 2)


def test_a_finished_run_has_no_next_rung():
    from challenges.services.calendar_render import marker_rail

    rail = marker_rail(365)
    assert all(m['reached'] for m in rail['markers'])
    assert rail['next'] is None and rail['to_next'] is None
    assert rail['pct'] == 100.0


def test_the_rail_clamps_rather_than_overrunning():
    """A run cannot be past its own total, but `filled_count` is a denormalised figure and a rail that
    renders `width: 110%` would spill out of its track rather than failing visibly."""
    from challenges.services.calendar_render import marker_rail

    assert marker_rail(400)['pct'] == 100.0
    assert marker_rail(400)['done'] == 365
    assert marker_rail(-5)['pct'] == 0.0
    assert marker_rail(-5)['done'] == 0


def test_the_marker_positions_keep_their_fraction():
    """COMPUTED SERVER-SIDE RATHER THAN BY `widthratio`, which floors to an integer: the 50 rung would
    sit at 13% against a label that says 13.7% of the way along, and the pip and its number would
    visibly disagree."""
    from challenges.services.calendar_render import marker_rail

    first = marker_rail(0)['markers'][0]
    assert first['pct'] == 13.7, 'floored to 13 it would misalign against its own label'


# ── the month's side-column figures ──────────────────────────────────────────────────────────────────

def test_a_month_reports_the_figures_its_side_column_draws():
    run = _calendar_run()
    _fill(run, 3, 3, on=dt.date(2019, 3, 3))
    _fill(run, 3, 20, on=dt.date(2021, 3, 20))
    CalendarDay.objects.filter(challenge=run, month=3, day=9).update(in_all=True, in_clean=False)

    march = _month(calendar_render.calendar_groups(run), 3)
    assert march['done'] == 2
    assert march['open'] == 29
    assert march['shovelware'] == 1, 'in `all`, off the board'
    assert march['first'] == dt.date(2019, 3, 3)
    assert march['last'] == dt.date(2021, 3, 20)


def test_a_month_reports_its_busiest_square():
    run = _calendar_run()
    _fill(run, 4, 4, on=dt.date(2019, 4, 4), plats=2)
    _fill(run, 4, 18, on=dt.date(2020, 4, 18), plats=5)
    _fill(run, 4, 25, on=dt.date(2021, 4, 25))

    april = _month(calendar_render.calendar_groups(run), 4)
    assert april['busiest'] == {'day': 18, 'plats': 5}


def test_a_month_whose_squares_hold_one_platinum_each_has_no_busiest_day():
    """EVERY FILLED SQUARE HOLDS AT LEAST ONE, so a "busiest day" of 1 is "a day you filled" wearing a
    superlative -- it would render on every month with a single square and tell a hunter nothing. The
    row is absent until a day actually stacks."""
    run = _calendar_run()
    for day in (6, 7, 8):
        _fill(run, 6, day)

    assert _month(calendar_render.calendar_groups(run), 6)['busiest'] is None


def test_the_busiest_square_breaks_a_tie_on_the_earliest_day():
    """DETERMINISTIC WITHOUT A SORT: `cards` is built in ascending day order and the comparison is a
    strict `>`, so the first day to reach the maximum keeps it. Worth pinning because a `>=` would make
    the figure depend on iteration order, and the panel would silently change which day it named."""
    run = _calendar_run()
    _fill(run, 11, 2, plats=3)
    _fill(run, 11, 20, plats=3)

    assert _month(calendar_render.calendar_groups(run), 11)['busiest'] == {'day': 2, 'plats': 3}


def test_the_busiest_square_ignores_a_shovelware_only_day():
    """THE SAME TEST THE DATES APPLY. A shovelware-only day carries a count and draws nothing, so
    naming it would put a figure on the board for a population the board excludes -- and it would beat
    every real square, since nothing stops a flagged day holding the most platinums."""
    run = _calendar_run()
    CalendarDay.objects.filter(challenge=run, month=12, day=1).update(
        in_all=True, in_clean=False, earned_on=dt.date(2017, 12, 1), plat_count=9)
    _fill(run, 12, 15, plats=2)

    assert _month(calendar_render.calendar_groups(run), 12)['busiest'] == {'day': 15, 'plats': 2}


def test_the_months_dates_come_from_filled_days_only():
    """A DISTINCTION THAT MATTERS: `earned_on` is written for any population holding a square, so a
    SHOVELWARE day carries a date and draws nothing. Reading dates without the `filled` test would date
    a month from a square it does not show -- and on a month whose only dated row is shovelware, that is
    a "first filled" for a day that is empty."""
    run = _calendar_run()
    CalendarDay.objects.filter(challenge=run, month=5, day=5).update(
        in_all=True, in_clean=False, earned_on=dt.date(2017, 5, 5))
    _fill(run, 5, 20, on=dt.date(2020, 5, 20))

    may = _month(calendar_render.calendar_groups(run), 5)
    assert may['first'] == dt.date(2020, 5, 20), 'the 2017 shovelware date must not win'
    assert may['last'] == dt.date(2020, 5, 20)


def test_an_untouched_month_has_no_dates_and_no_rank():
    """A MONTH WITH NOTHING FILLED HAS NO RANK rather than sharing last place, because on a fresh run
    ranking the empties would hand all twelve "1st" -- worse than saying nothing."""
    month = _month(calendar_render.calendar_groups(_calendar_run()), 8)
    assert month['done'] == 0
    assert month['first'] is None and month['last'] is None
    assert month['rank'] is None
    assert month['open'] == 31


def test_the_months_rank_on_completion_not_on_raw_days():
    """RANKED ON RAW DAYS, A COMPLETE FEBRUARY PLACED BEHIND AN INCOMPLETE JANUARY -- 28 of 28 against
    29 of 31 -- and no 28-day month could ever be first against a long month with 29 filled. The
    template prints this as "2nd best", so a hunter who had just finished a month read a correct figure
    as a bug."""
    run = _calendar_run()
    for day in range(1, 29):
        _fill(run, 2, day)          # 28 of 28 -- complete
    for day in range(1, 30):
        _fill(run, 1, day)          # 29 of 31 -- more days, less of the month

    months = calendar_render.calendar_groups(run)
    ranks = {m['label']: m['rank'] for m in months if m['rank']}
    assert ranks == {'February': 1, 'January': 2}


def test_the_months_rank_is_dense_across_unequal_month_lengths():
    """DENSE RANKING ("1224"), so two months at the same completion share a place and the next is the one
    after; a competition ranking would skip a number and read as a gap the hunter cannot explain.

    THE TIE IS ACROSS DIFFERENT DENOMINATORS on purpose -- 14/28 and 15/30 are the same number, and
    that is exactly where binary floats stop being exact. `Fraction` makes the tie a property of the
    arithmetic rather than of how the two happened to round."""
    run = _calendar_run()
    for day in range(1, 15):
        _fill(run, 2, day)          # 14 of 28
    for day in range(1, 16):
        _fill(run, 6, day)          # 15 of 30 -- the same half, a different denominator
    for day in range(1, 6):
        _fill(run, 1, day)          # 5 of 31
    _fill(run, 4, 1)                # 1 of 30

    months = calendar_render.calendar_groups(run)
    ranks = {m['label']: m['rank'] for m in months if m['rank']}
    assert ranks == {'February': 1, 'June': 1, 'January': 2, 'April': 3}
