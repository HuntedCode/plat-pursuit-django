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

Plus the failure specific to this type, which is the one that actually bit during review: the three
views do NOT nest, so any single cross-lens value is wrong in some lens. That is why a cell carries
three booleans and no `tier`.
"""
import datetime as dt

import pytest
from django.db import connection, transaction
from django.db.utils import IntegrityError
from django.test.utils import CaptureQueriesContext

from challenges.models import (
    CALENDAR_MONTH_DAYS,
    CALENDAR_VIEW_ALL,
    CALENDAR_VIEW_CLEAN,
    CALENDAR_VIEW_CONTRACTS,
    CALENDAR_VIEW_FIELDS,
    CalendarDay,
)
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


def _fill(run, month, day, *, views=(CALENDAR_VIEW_ALL,), on=None):
    """Mark one day filled in `views`, writing the row directly.

    DIRECT ROW WRITES ON PURPOSE. What fills a day is `calendar_fill`'s job and has its own file; this
    one asks what the renderer does with rows already in whatever state.

    THE ROWCOUNT IS ASSERTED, which is not pedantry: `.update()` on a mistyped month or day is a silent
    no-op, and several tests below assert that a crest is ABSENT -- so a typo would make them pass for
    the wrong reason. The database also enforces `in_clean` implying `in_all`, so callers meaning clean
    pass both.
    """
    fields = {'in_all': CALENDAR_VIEW_ALL in views,
              'in_clean': CALENDAR_VIEW_CLEAN in views,
              'in_contracts': CALENDAR_VIEW_CONTRACTS in views,
              'earned_on': on or dt.date(2019, month, min(day, 28))}
    written = CalendarDay.objects.filter(challenge=run, month=month, day=day).update(**fields)
    assert written == 1, 'no row at (%d, %d) -- the fill was a silent no-op' % (month, day)


def _fill_month(run, month, *, views=(CALENDAR_VIEW_ALL,)):
    for day in range(1, CALENDAR_MONTH_DAYS[month - 1] + 1):
        _fill(run, month, day, views=views)


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


def test_the_index_counts_across_the_whole_run_not_within_each_month():
    """REAL HISTORY, from `slot_cards`: grouping restarts a template's `forloop.counter0` at every
    group, so anything keyed on it -- a stagger, a lazy threshold -- silently breaks. Stamping the
    position server-side is what makes the number mean "position in the run"."""
    groups = calendar_render.calendar_groups(_calendar_run())
    assert groups[0]['cards'][0]['index'] == 0
    # February 1 is the 32nd day of the year, so index 31 after January's 31 days.
    assert _month(groups, 2)['cards'][0]['index'] == 31
    assert groups[-1]['cards'][-1]['index'] == 364

    every = [card['index'] for g in groups for card in g['cards']]
    assert every == list(range(365))


# ── the three lenses on one cell ─────────────────────────────────────────────────────────────────────

def test_a_cell_carries_every_lens_so_switching_costs_no_request():
    """The board renders once and the switcher reads `data-view`, the same both-states-in-the-DOM
    technique the contract CTA uses. If a cell carried only the selected lens, every change would be a
    round trip."""
    run = _calendar_run()
    _fill(run, 3, 3, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))

    card = _month(calendar_render.calendar_groups(run), 3)['cards'][2]
    assert card['views'] == {CALENDAR_VIEW_ALL: True, CALENDAR_VIEW_CLEAN: True,
                             CALENDAR_VIEW_CONTRACTS: False}


def test_a_cell_offers_no_single_cross_lens_value():
    """DELIBERATELY ABSENT, and pinned so it cannot drift back in.

    A `tier` key used to carry the hardest view a day reached. On a board that renders all three lenses
    at once that value is wrong in two of them: a contracts-only day would have read `tier='contracts'`
    and any CSS tinting `[data-tier]` would paint it gold while the viewer is in the All platinums
    lens, where the day is empty. The tint has to come from the per-lens boolean.
    """
    run = _calendar_run()
    _fill(run, 4, 4, views=(CALENDAR_VIEW_CONTRACTS,))
    card = _month(calendar_render.calendar_groups(run), 4)['cards'][3]

    assert 'tier' not in card
    assert card['views'][CALENDAR_VIEW_CONTRACTS] is True
    assert card['views'][CALENDAR_VIEW_ALL] is False, (
        'the designed-for non-nesting case: a 100% tier with no platinum anywhere')


def test_an_unfilled_cell_is_false_in_every_lens_and_has_no_date():
    card = _month(calendar_render.calendar_groups(_calendar_run()), 7)['cards'][0]
    assert set(card['views'].values()) == {False}
    assert card['earned_on'] is None


def test_a_filled_cell_carries_the_date_it_was_earned():
    run = _calendar_run()
    _fill(run, 3, 3, on=dt.date(2019, 3, 3))
    card = _month(calendar_render.calendar_groups(run), 3)['cards'][2]
    assert card['earned_on'] == dt.date(2019, 3, 3)


def test_every_lens_in_the_catalogue_reaches_a_cell():
    """The cell's lens dict is DERIVED from `CALENDAR_VIEW_FIELDS` rather than spelled out, so a fourth
    view would appear here instead of raising a `KeyError` two functions away."""
    card = _month(calendar_render.calendar_groups(_calendar_run()), 1)['cards'][0]
    assert set(card['views']) == {view for view, _field in CALENDAR_VIEW_FIELDS}


# ── per-month counts and the crest ───────────────────────────────────────────────────────────────────

def test_a_month_keeps_one_count_per_lens_rather_than_collapsing_them():
    run = _calendar_run()
    _fill(run, 1, 1, views=(CALENDAR_VIEW_ALL,))
    _fill(run, 1, 2, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))
    _fill(run, 1, 3, views=(CALENDAR_VIEW_CONTRACTS,))

    january = _month(calendar_render.calendar_groups(run), 1)
    assert january['counts'] == {CALENDAR_VIEW_ALL: 2, CALENDAR_VIEW_CLEAN: 1,
                                 CALENDAR_VIEW_CONTRACTS: 1}


def test_a_month_head_agrees_with_the_squares_beneath_it():
    """The head is counted from the CELLS, not from the rows a second time, so the two cannot drift."""
    run = _calendar_run()
    for day in (4, 9, 17):
        _fill(run, 6, day, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))

    june = _month(calendar_render.calendar_groups(run), 6)
    for view, _field in CALENDAR_VIEW_FIELDS:
        assert june['counts'][view] == sum(1 for card in june['cards'] if card['views'][view])


def test_a_month_offers_no_single_done_number():
    """DELIBERATELY ABSENT. `slot_groups` includes `done` on both branches so a Python consumer cannot
    `KeyError` on one challenge type. Here there are THREE progress numbers and they do not nest, so
    any single `done` would be one of three arbitrarily and every reader would silently get the wrong
    month some of the time. Omitting the key makes a wrong reader fail loudly instead."""
    january = _month(calendar_render.calendar_groups(_calendar_run()), 1)
    assert 'done' not in january
    assert 'counts' in january and 'total' in january


def test_an_incomplete_month_is_struck_in_no_metal():
    run = _calendar_run()
    _fill(run, 1, 1)
    january = _month(calendar_render.calendar_groups(run), 1)
    # Asserted alongside a non-zero count, so this cannot pass against a renderer that simply never
    # fills anything -- which is what makes the bare `crest == ''` assertion hollow on its own.
    assert january['counts'][CALENDAR_VIEW_ALL] == 1
    assert january['crest'] == ''


def test_one_missing_day_does_not_complete_a_month():
    run = _calendar_run()
    for day in range(1, 28):
        _fill(run, 2, day)
    february = _month(calendar_render.calendar_groups(run), 2)
    assert february['counts'][CALENDAR_VIEW_ALL] == 27
    assert february['crest'] == ''


def test_a_month_crest_shows_the_highest_lens_that_completed_it():
    run = _calendar_run()
    # February, because 28 rows is the cheapest complete month to write.
    _fill_month(run, 2, views=(CALENDAR_VIEW_ALL,))
    assert _month(calendar_render.calendar_groups(run), 2)['crest'] == 'bronze'

    _fill_month(run, 2, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))
    assert _month(calendar_render.calendar_groups(run), 2)['crest'] == 'silver'

    _fill_month(run, 2, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN, CALENDAR_VIEW_CONTRACTS))
    assert _month(calendar_render.calendar_groups(run), 2)['crest'] == 'gold'


def test_a_month_can_be_gold_without_ever_being_bronze():
    """The consequence of the lenses not nesting, and the reason only ONE crest renders per month.

    A hunter can complete February in contracts while all-platinums is still short -- gold with no
    bronze. Showing the highest achieved makes that invisible; showing all three side by side would
    expose it and read as a bug.
    """
    run = _calendar_run()
    _fill_month(run, 2, views=(CALENDAR_VIEW_CONTRACTS,))

    february = _month(calendar_render.calendar_groups(run), 2)
    assert february['counts'][CALENDAR_VIEW_ALL] == 0
    assert february['crest'] == 'gold'


def test_a_complete_all_and_contracts_month_still_shows_the_rarer_metal():
    """Gold with bronze earned and silver skipped -- the middle lens short, the hardest one complete."""
    run = _calendar_run()
    _fill_month(run, 2, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CONTRACTS))

    february = _month(calendar_render.calendar_groups(run), 2)
    assert february['counts'] == {CALENDAR_VIEW_ALL: 28, CALENDAR_VIEW_CLEAN: 0,
                                  CALENDAR_VIEW_CONTRACTS: 28}
    assert february['crest'] == 'gold'


def test_every_lens_has_a_metal_to_be_struck_in():
    """A view added to the catalogue without a metal would raise `KeyError` inside `_crest` only once
    somebody completed a month in it -- which is the slowest possible way to find out."""
    assert set(calendar_render.CREST_METAL) == {view for view, _field in CALENDAR_VIEW_FIELDS}


# ── absent and impossible rows ───────────────────────────────────────────────────────────────────────

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
    assert set(orphan['views'].values()) == {False}
    assert orphan['earned_on'] is None


def test_the_hero_also_draws_a_missing_day_as_empty():
    """The absent-row path through the OTHER entry point, which had no coverage of its own."""
    run = _calendar_run()
    _fill(run, 5, 5, views=(CALENDAR_VIEW_CLEAN, CALENDAR_VIEW_ALL))
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
    assert set(groups[0]) == {'view', 'days'}


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


def test_the_hero_leads_with_the_better_of_the_two_genuine_lenses():
    """ALL PLATINUMS IS NEVER THE HERO'S LENS. It is the lens shovelware inflates and it cannot finish
    a run, so leading with it would show an entry far along whose run completes on a much lower
    number -- and the tally beside the board reads `filled_count`, which is the better of clean and
    contracts."""
    run = _calendar_run()
    for day in range(1, 11):
        _fill(run, 1, day, views=(CALENDAR_VIEW_ALL,))
    for day in range(1, 4):
        _fill(run, 2, day, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))

    group = calendar_render.calendar_boards_for([run])[run.pk][0]
    assert group['view'] == CALENDAR_VIEW_CLEAN
    assert sum(1 for d in group['days'] if d['filled']) == 3, (
        'the ten all-platinums-only days must not be tinted in the clean lens')


def test_the_hero_switches_lens_once_contracts_pulls_ahead():
    run = _calendar_run()
    for day in range(1, 4):
        _fill(run, 2, day, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))
    for day in range(1, 6):
        _fill(run, 3, day, views=(CALENDAR_VIEW_CONTRACTS,))

    group = calendar_render.calendar_boards_for([run])[run.pk][0]
    assert group['view'] == CALENDAR_VIEW_CONTRACTS
    assert sum(1 for d in group['days'] if d['filled']) == 5


def test_the_rarer_lens_wins_a_tie_as_it_does_on_completion():
    run = _calendar_run()
    for day in range(1, 4):
        _fill(run, 2, day, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))
    for day in range(1, 4):
        _fill(run, 3, day, views=(CALENDAR_VIEW_CONTRACTS,))

    group = calendar_render.calendar_boards_for([run])[run.pk][0]
    assert group['view'] == CALENDAR_VIEW_CONTRACTS


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
    _fill(one, 3, 3, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))

    boards = calendar_render.calendar_boards_for([one, two])
    assert boards[one.pk][0]['days'][61]['filled'] is True
    assert boards[two.pk][0]['days'][61]['filled'] is False


# ── flatness, which is what lets a board onto a browse surface ───────────────────────────────────────

def test_the_detail_board_costs_one_query():
    run = _calendar_run()
    _fill_month(run, 2, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))

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
        _fill_month(full, month, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))

    with CaptureQueriesContext(connection) as empty_cost:
        calendar_render.calendar_groups(empty)
    with CaptureQueriesContext(connection) as full_cost:
        calendar_render.calendar_groups(full)

    assert len(full_cost) == len(empty_cost) == 1
