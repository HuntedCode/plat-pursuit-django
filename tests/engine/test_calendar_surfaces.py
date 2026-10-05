"""The Plat Calendar's surfaces: the view dispatch, the detail board, and the Hall of Fame hero.

WHAT THIS FILE IS FOR, as distinct from `test_calendar_render`. That one asks what the renderers return;
this one asks whether the right renderer is CHOSEN and whether its output actually reaches the page. The
two defects it exists to catch are both silent:

  - a Calendar run handed to `slot_render.boards_for` gets `[]`, because it owns no `ChallengeSlot` rows.
    So the WRONG dispatch does not raise -- the entry simply draws no board, which looks like a run with
    nothing in it. Same in reverse for an A-Z run handed to `calendar_boards_for`.
  - the detail page's slot board was ungated, so a Calendar run (whose `groups` is `[]`) rendered an
    empty `.pp-csq-board` frame. That is the `_board_groups` regression ("drew the bare grey frame above
    a '26/26 squares' tally") arriving from the other direction.

THE BOARD MUST WORK WITH NO JAVASCRIPT, which is the other thing pinned here -- and the two switchers get
there differently, which an earlier version of this docstring flattened into "both are CSS over an
attribute":

  - THE LENS is pure CSS. `.pp-cal:has(input[value="..."]:checked)` reads the checked radio, so the tints
    and the tallies follow the chip with no script at all.
  - THE MONTH is the `hidden` attribute, rendered by the server and moved by `PlatPursuit.wireTablist`.
    Without JS the board shows January: correct, and fixed.

The board itself carries NO state attributes, which one of the tests below asserts outright. The script is
pinned by SOURCE TEXT, because this project has no JS test runner.
"""
import datetime as dt
import re
from pathlib import Path

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from challenges.models import (CALENDAR_MONTH_DAYS, CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_CALENDAR,
                               CalendarDay)
from challenges.services import calendar_render
from tests.factories import ProfileFactory, UserFactory

#: The repo root, for the pins that read a template as text.
ROOT_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.django_db


def _hunter():
    return ProfileFactory(user=UserFactory(), is_linked=True, user_is_premium=True)


def _run(challenge_type, profile=None):
    """A real run of `challenge_type`, created through the service.

    The Calendar has no creation door yet (`TYPES_NOT_YET_CREATABLE`), so the constant is lifted around
    the call -- the same door `test_calendar_fill` and `test_calendar_render` use, for the same reason:
    these tests want the rows the real creation path makes.
    """
    from challenges.services import challenge_service as svc

    original = svc.TYPES_NOT_YET_CREATABLE
    svc.TYPES_NOT_YET_CREATABLE = frozenset()
    try:
        return svc.start(profile or _hunter(), challenge_type)
    finally:
        svc.TYPES_NOT_YET_CREATABLE = original


def _url(challenge):
    return reverse('challenge_detail', args=[challenge.id])


def _fill(run, month, day, *, on=None, plats=1):
    """Fill one day. BOTH columns, because `in_clean` implies `in_all` and the database enforces it; a
    test wanting a shovelware day -- in `all` and off the board -- writes the row itself.

    `earned_on` AND `plat_count` ARE SET, and neither was at first. The real fill always writes a date
    and a count alongside the booleans, so a helper that left them at null and zero produced rows no
    backfill could make -- and the month's side column reads both, so its rows silently rendered empty
    and the tests asserting them failed against a gap in the FIXTURE rather than in the page. The count
    defaults to 1 for the same reason: a square is filled BY a platinum.
    """
    # `dt.date(2019, month, day)`, NOT `min(day, 28)` -- see the twin helper in
    # `test_calendar_render.py`. The clamp stored a date whose day-of-month was not the square's, and
    # collapsed days 29/30 of a month onto one date so `first == last` and "Most recent" vanished.
    written = CalendarDay.objects.filter(challenge=run, month=month, day=day).update(
        in_all=True, in_clean=True, plat_count=plats,
        earned_on=on or dt.date(2019, month, day))
    assert written == 1, 'no row at (%d, %d) -- the fill was a silent no-op' % (month, day)


# ── the detail view's dispatch ───────────────────────────────────────────────────────────────────────

def test_a_calendar_run_gets_month_groups_and_no_slot_groups():
    run = _run(CHALLENGE_TYPE_CALENDAR)
    context = Client().get(_url(run)).context

    assert context['groups'] == [], (
        'the slot board must be empty, not merely unused -- its template gates on this')
    assert len(context['calendar_months']) == 12
    assert context['calendar_months'][0]['label'] == 'January'


def test_a_slot_run_gets_no_calendar_context_at_all():
    """THE DISPATCH IN THE OTHER DIRECTION. A stray `calendar_months` on an A-Z page would render a
    second board below the first, and the Calendar partial's own `{% if calendar_months %}` is the only
    thing standing between the two."""
    context = Client().get(_url(_run(CHALLENGE_TYPE_AZ))).context
    assert context['groups'], 'an A-Z run still draws the slot board'
    assert 'calendar_months' not in context


def test_the_year_totals_are_the_sum_of_the_months():
    """TWO FIGURES: the days filled, and the same count without the shovelware exclusion. The second is
    what lets the first be read aloud -- "297 days, of 340 you hold platinums for" -- rather than as a
    bare number, and nothing on the board draws it."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 1, 1)
    _fill(run, 6, 6)
    CalendarDay.objects.filter(challenge=run, month=9, day=9).update(in_all=True, in_clean=False)

    context = Client().get(_url(run)).context
    assert context['calendar_totals'] == {'done': 2, 'all': 3}
    # Summed from the groups already on the page rather than asked of the database again.
    assert context['calendar_totals'] == calendar_render.totals_for(context['calendar_months'])


def test_the_page_offers_no_lens_to_choose():
    """THREE CONTEXT KEYS STOOD HERE. `calendar_views` fed the switcher's options and `calendar_view`
    decided which lens the board opened on, so the page was correct before any script ran. One lens
    means nothing downstream has to be told which is active."""
    context = Client().get(_url(_run(CHALLENGE_TYPE_CALENDAR))).context
    assert 'calendar_views' not in context
    assert 'calendar_view' not in context
    assert context['calendar_months'], 'the board itself is still there'


def test_the_detail_page_stays_flat_however_many_days_are_filled():
    """The board is one query, so filling the whole year must not add any."""
    empty = _run(CHALLENGE_TYPE_CALENDAR)
    full = _run(CHALLENGE_TYPE_CALENDAR)
    CalendarDay.objects.filter(challenge=full).update(in_all=True, in_clean=True)

    client = Client()
    with CaptureQueriesContext(connection) as empty_cost:
        client.get(_url(empty))
    with CaptureQueriesContext(connection) as full_cost:
        client.get(_url(full))

    assert len(full_cost) == len(empty_cost), (
        'a filled board cost %d queries against an empty board\'s %d'
        % (len(full_cost), len(empty_cost)))


# ── the rendered detail board ────────────────────────────────────────────────────────────────────────

def _body(run):
    return Client().get(_url(run)).content.decode()




def _rail_css():
    """The rail's own block of `challenges.css`, bounded and comment-stripped.

    A SEPARATE READER, because `_calendar_css` cuts at the next top-level banner -- which is exactly
    what it was fixed to do -- and `.pp-cal-rail` is its own block beyond that line. Reusing the
    calendar reader for a rail assertion is how a pin passes against a slice that does not contain the
    rule it names.
    """
    css = open('static/css/components/challenges.css', encoding='utf-8').read()
    block = css[css.index('THE DAY-MARKER RAIL'):]
    return re.sub(r'/\*.*?\*/', '', block, flags=re.S)


def _css_rules(block, selector):
    """Every rule for `selector`, comment-stripped and whitespace-flattened.

    `_css_rule` TAKES THE FIRST OR THE LAST, which is the wrong handle whenever a selector appears more
    than twice -- `.pp-cal__crests` has five rules and `.pp-cal__stats` three, and in both cases the one
    worth asserting on is in the middle. Position also moves for reasons that change no computed value:
    the width-story block sits above the base rule in the stylesheet, so "last" is the `md:` gap rather
    than the cap. Callers filter these on a declaration they actually care about.

    PASS IT A COMMENT-STRIPPED BLOCK. This does not strip -- `_calendar_css()` and `_rail_css()` already
    do, and every caller goes through one of them -- so handing it raw stylesheet text would let a match
    run through commentary. It also requires exactly one space before the brace, which is this file's
    house style throughout; `.pp-cal__crests{` would return `[]`, and both callers assert a length so an
    empty result fails loudly rather than passing vacuously.
    """
    return [m.group(0) for m in
            re.finditer(re.escape(selector) + r' \{[^}]*\}', ' '.join(block.split()))]


def _css_rule(block, selector, last=False):
    """One rule's declarations, from its selector to its closing brace.

    SLICING A STYLESHEET BY HAND IS A GUESS, which this file has paid for three times now -- once
    landing on a single line and passing against nothing, once matching a comment that explained the
    thing it was asserting was absent, and once matching a SHARED rule because the selector it wanted
    also appears as the second half of `.pp-cal__arc-track, .pp-cal__arc {`. `_calendar_css` already
    strips comments; this bounds the slice to one rule.

    `last=True` IS FOR THAT THIRD CASE: when a selector appears both in a shared rule and in its own,
    the standalone one comes later. Naming it at the call site is better than a cleverer search,
    because the caller knows which of the two it means.
    """
    at = block.rindex(selector) if last else block.index(selector)
    return block[at:block.index('}', at)]

def _section(body, class_name, until):
    """The rendered markup of one block, bounded by an EXPLICIT end marker.

    An unbounded `body[body.index(...):]` slice reaches the end of the document, so any `count()` on it
    silently counts the footer and the scripts too. That is the whole reason this helper exists.

    THE END MARKER IS AN ARGUMENT, not a guessed list, and the first version learned why the hard way:
    it carried a fixed list of boundaries tuned to the document order at the time, and moving the lens
    switcher above the month panels silently made the crest section swallow three more `sr-only`
    elements. Worse, one boundary was `pp-cal__lens`, which is a PREFIX of `pp-cal__lenscount` -- so
    scoping to the lens block cut it off inside its own first chip. A caller naming its own end marker
    cannot drift when the markup is reordered.
    """
    start = body.index(class_name)
    end = body.index(until, start + len(class_name))
    return body[start:end]


def test_the_calendar_page_does_not_render_an_empty_slot_frame():
    """THE REGRESSION THIS GATE EXISTS FOR. `groups` is `[]` on a Calendar run, and the slot board's
    wrapper used to render regardless -- a bare frame with no squares in it."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    assert 'pp-csq-board' not in body
    assert 'pp-cal__grid' in body


def test_a_slot_page_does_not_render_a_calendar_board():
    """ASSERTED ON THE BOARD, NOT ON ITS GRID. This used to forbid `pp-cal__grid` alone, so changing the
    include's gate to something always-true survived: an A-Z page would have shipped an empty `.pp-cal`,
    an empty `role="tablist"` and a bare bordered `.pp-switch`, and the suite stayed green."""
    body = _body(_run(CHALLENGE_TYPE_AZ))
    assert 'pp-csq-board' in body
    assert 'pp-cal__crests' not in body
    assert 'class="pp-cal"' not in body
    assert 'pp-cal__grid' not in body


def test_the_board_draws_every_day_of_the_year():
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    # COUNTED ON THE CLASS, NOT ON AN EXACT ATTRIBUTE. It was `class="pp-cal__day"` verbatim, which was
    # chosen because it "cannot silently match nothing" -- and then silently matched 364 the moment a
    # square started carrying a modifier (today's). The pattern anchors on the class and allows whatever
    # follows, which is the same lesson `<div class="(pp-cal__day...)"` taught when the openable squares
    # became buttons.
    assert len(re.findall(r'class="pp-cal__day[ "]', body)) == 365
    assert body.count('pp-cal__crest"') + body.count('pp-cal__crest ') == 12


def test_only_the_current_month_is_showing_before_any_script_runs():
    """`hidden` ON THE INACTIVE ELEVEN rather than a stylesheet rule, so the rendered HTML is a correct
    board on its own and a reader never meets twelve months of squares at once -- even with CSS off, and
    before `wireTablist` has wired anything.

    AND THE ONE SHOWING IS THE CURRENT MONTH (owner, 2026-10-05), not January. The board opened on
    `forloop.first` whatever the date, so a hunter arriving in October had to find October before the
    page told them anything about now.

    COMPUTED THROUGH `today_key`, NOT WITH A SECOND `timezone.now()`. A test that spells the clock
    itself passes on the wrong square for anyone whose timezone differs from the runner's, and would be
    a second definition of exactly the thing this feature keeps in one place."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    body = _body(run)
    panels = re.findall(r'<section class="pp-cal__panel"[^>]*>', body)
    open_index = calendar_render.today_key(run.profile)[0] - 1

    assert len(panels) == 12
    assert 'hidden' not in panels[open_index], 'the current month must be the one showing'
    assert sum(1 for p in panels if 'hidden' not in p) == 1, 'exactly one month shows'
    assert all('hidden' in p for i, p in enumerate(panels) if i != open_index)

    # The tab and the panel must agree: the switcher finds the live tab by `aria-selected`.
    tabs = re.findall(r'<button type="button" role="tab"[^>]*>', body)
    assert sum(1 for t in tabs if 'aria-selected="true"' in t) == 1
    assert 'aria-selected="true"' in tabs[open_index]


def test_todays_square_is_marked_on_the_grid():
    """"We should somehow identify on the grid the current day" (owner, 2026-10-05).

    THE OWNER'S CLOCK, NOT THE READER'S, which is the decision worth pinning rather than the ring: a run
    page is public, and every other date on the surface is resolved in the owner's zone, so taking the
    reader's would leave "today" as the one date on the board keyed differently from the squares around
    it.

    AND IT IS SPOKEN, not only drawn. The numeral is `aria-hidden`, so the `sr-only` label is the only
    thing a screen reader gets -- a marker that exists only in colour would not exist at all for them."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    body = _body(run)
    month, day = calendar_render.today_key(run.profile)

    assert body.count('pp-cal__day--today') == 1, 'exactly one square is today'
    assert ' Today.</span>' in body, 'the marker must be spoken, not only painted'

    # It is on the right square: the label and the modifier belong to the same cell.
    cell = re.search(r'<[^>]*pp-cal__day--today[^>]*>.*?</(?:button|div)>', body, re.S).group(0)
    assert '%s %d' % (calendar_render.MONTH_NAMES[month - 1], day) in cell


def test_the_today_marker_survives_a_leap_day():
    """THERE IS NO (2, 29) SQUARE -- `calendarday_day_within_month` forbids the row -- so an unfolded
    marker would simply vanish for a day every four years, which is the kind of absence nobody reports
    and nobody can reproduce. `today_key` folds through the same `_fold` the fill uses."""
    from challenges.services import calendar_fill

    assert calendar_fill._fold(2, 29) == (2, 28)
    # And the renderer goes through it rather than spelling the fold again.
    src = (ROOT_DIR / 'challenges' / 'services' / 'calendar_render.py').read_text(encoding='utf-8')
    assert 'from challenges.services.calendar_fill import _fold' in src
    assert 'LEAP_DAY' not in src, 'the fold is re-spelled here instead of imported'


def test_the_board_carries_no_state_attributes():
    """The board used to carry `data-view`/`data-month` for the stylesheet to read, which meant the lens
    only worked with JavaScript. The tint read the checked radio through `:has()` instead, and then the
    collapse removed the lens entirely -- so there is nothing left for an attribute to say."""
    board = re.search(r'<div class="pp-cal"[^>]*>',
                      _body(_run(CHALLENGE_TYPE_CALENDAR))).group(0)
    assert board == '<div class="pp-cal">', board


def test_the_page_renders_no_lens_switcher():
    """CUT WITH THE LENSES (owner, 2026-10-04). A `.pp-switch` with three radios sat between the crests
    and the board. Pinned because the control is the obvious thing to re-add when somebody wants the
    contracts view back, and the point is that the DATA for it is gone too."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    assert 'cal-view' not in body
    assert 'pp-cal__lens' not in body
    assert 'Shovelware free' not in body, 'the lens names were the chip labels'
    assert 'pp-cal__crests' in body, 'the MONTH switcher is still there'


def test_the_crest_row_is_a_real_tablist():
    """The roles ARE the promise -- that Tab reaches the row and arrows move within it. This used to
    assert only the `role="tab"` count and the tabindex, so dropping `role="tablist"` from the row or
    `role="tabpanel"` from all twelve panels left the suite green with the keyboard contract gone."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    assert '<div class="pp-cal__crests" role="tablist" aria-label="Month">' in body
    assert body.count('role="tabpanel"') == 12
    assert body.count('role="tab"') == 12


def test_every_crest_is_reachable_before_the_script_narrows_the_row():
    """A ROVING TABINDEX IS ONLY SAFE ONCE SOMETHING IS ROVING. The server used to render it directly
    (`0` on the first crest, `-1` on the rest), which with JavaScript off meant Tab skipped eleven
    crests and no arrow key moved between them -- a keyboard-only visitor losing eleven twelfths of the
    board with no way to reach it. `wireTablist.syncTabindex()` runs at wire time, so the rover arrives
    in the same tick as the arrows that make it navigable."""
    crests = re.findall(r'<button type="button" role="tab"[^>]*>',
                        _body(_run(CHALLENGE_TYPE_CALENDAR)))
    assert len(crests) == 12
    assert all('tabindex="0"' in c for c in crests)
    assert sum(1 for c in crests if 'aria-selected="true"' in c) == 1


def test_a_filled_day_carries_a_modifier_and_an_empty_one_does_not():
    """A CLASS, NOT ATTRIBUTES. A filled day used to carry up to three of
    `data-all`/`data-clean`/`data-contracts` so `:has()` could decide which painted."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 1, 2)
    body = _body(run)
    # MATCHED ON THE CLASS, NOT ON `<div`. A square that holds a platinum renders as a `<button>` now,
    # so an element-anchored pattern silently stopped counting exactly the squares this test is about --
    # it read 364 of 365 and the missing one was the filled day.
    days = re.findall(r'class="(pp-cal__day[^"]*)"', body)

    assert len(days) == 365
    assert days[0] == 'pp-cal__day', 'January 1 is unfilled, so it is not a control'
    assert days[1] == 'pp-cal__day pp-cal__day--open pp-cal__day--on'
    assert 'data-all' not in body and 'data-clean' not in body


def test_an_openable_square_still_fills_its_cell():
    """THE DEFECT A BROWSER FOUND IN TEN SECONDS AND SIX AUDIT ROUNDS DID NOT, because nothing in a test
    suite renders CSS.

    A `<button>` with `width: auto` SHRINK-FITS to its content, where the `<div>` it replaced filled its
    parent -- and `.pp-cal__day` pairs that with `aspect-ratio: 1`, so each openable square collapsed to
    the width of its numeral and then squared THAT. A struck month is all buttons, so a finished February
    rendered as 28 tiny chips floating in a full-width grid.

    THE `<li>` IS THE GRID ITEM, not the cell, which is why the track's width never reached it.

    A SOURCE-TEXT PIN, AND A WEAK ONE BY NATURE: it cannot see layout. It exists so the declaration is
    not tidied away as redundant by someone reading `aspect-ratio` and assuming the cell is square on its
    own account."""
    rule = _css_rule(_calendar_css(), '.pp-cal__day--open {')
    assert 'width: 100%' in rule, (
        'an openable square shrinks to its numeral and the whole month turns to confetti')


def test_only_a_square_holding_a_platinum_is_a_control():
    """THE OWNER'S PREDICATE (2026-10-04): "the clickable squares should be the completed days", and then,
    on what an incomplete day would show, the useful answer was the one square that HAS an answer. So the
    rule is `in_all` -- "the hunter platinumed something on this date" -- which takes in every drawing
    square plus the shovelware-only ones that can explain why they are still open.

    A DAY WITH NOTHING IS NOT A CONTROL AT ALL: no button, no role, no tab stop. One `<button disabled>`
    for all 365 would have been a single element and the wrong semantics, and a screen reader would
    announce three hundred dimmed controls."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 4, 4)                                     # draws, and counts
    CalendarDay.objects.filter(challenge=run, month=4, day=5).update(
        in_all=True, in_clean=False, earned_on=dt.date(2019, 4, 5), plat_count=1)

    body = _body(run)
    openable = re.findall(r'data-day-url="([^"]*)"', body)

    assert len(openable) == 2, 'exactly the two squares holding a platinum'
    assert openable[0].endswith('/day/4/4/') and openable[1].endswith('/day/4/5/')
    assert 'pp-cal__day--open' in body

    april = _section(body, 'id="cal-month-apr"', until='</section>')
    assert april.count('<button type="button"') == 2, 'no other square became a control'


def test_both_day_cell_branches_carry_the_same_state():
    """THE HAZARD OF DUPLICATING AN ELEMENT, and the reason the template says so out loud. The openable
    square and the inert one are two separate blocks of markup, so anything added to one can silently
    exist on only one kind of square -- and this project has already shipped "a hook on ONE template
    branch is dead for the other" once.

    TWO THINGS MUST MATCH: `--rev`, which is the per-month entrance cascade (missing on a branch, those
    squares arrive without one), and the screen-reader label, which is the ONLY text on a cell whose
    numeral is `aria-hidden`."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 6, 1)
    body = _body(run)
    june = _section(body, 'id="cal-month-jun"', until='</section>')

    # Every cell, whichever element it is, carries its cascade index.
    assert june.count('--rev:') == 30, 'a branch is missing the entrance cascade'
    # And its spelled-out date. `_cell`'s label is "<Month> <day>", so "June 1" rather than "1 June" --
    # worth stating, because the fragment's own dates read "j F" and the two orders sit a screen apart.
    # The openable square adds "Open." and is otherwise identical to the inert one.
    assert 'June 1: filled.' in june, 'the control still names its own date'
    assert 'Open.' not in june, (
        'the name promised an action the square cannot perform without a script -- the button role\n'
        '        already says it is a control')
    assert 'June 2.' in june, 'an inert square still names itself'


def test_the_day_sheet_is_one_shell_for_the_whole_board():
    """ONE SHELL, NOT ONE PER SQUARE: 365 would be 365 dialogs in the accessibility tree. It ships empty
    and the script fills it, which is also what keeps `id="cal-day-title"` unique -- the fragment carries
    that id, so a second copy in the document would make `aria-labelledby` resolve to the wrong one."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))

    assert body.count('id="cal-day-modal"') == 1
    assert body.count('data-day-body') == 1
    assert '<div class="pp-detail-modal__body" data-day-body></div>' in body, (
        'the shell must ship EMPTY -- anything rendered into it server-side would be a second title id')
    # THE FALLBACK LABEL MATTERS because `aria-labelledby` points into a body that is empty until the
    # first open, and a failed fetch leaves it empty.
    assert 'aria-label="Day detail"' in body and 'aria-labelledby="cal-day-title"' in body

    # AND NOT ON A RUN THAT HAS NO SQUARES. The shell moved into the page's `fixed_overlays` block, which
    # renders for every challenge type -- so the `{% if calendar_months %}` gate is the only thing
    # keeping a stray dialog out of the accessibility tree on every A-Z and Job Coverage page.
    assert 'cal-day-modal' not in _body(_run(CHALLENGE_TYPE_AZ))


def test_a_shovelware_platinum_draws_no_square():
    """THE ONE LENS, stated on the rendered page: a platinum on a flagged game counts toward the
    comparison figure and draws nothing."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    CalendarDay.objects.filter(challenge=run, month=1, day=2).update(in_all=True, in_clean=False)
    body = _body(run)

    days = re.findall(r'<div class="(pp-cal__day[^"]*)"', body)
    assert days[1] == 'pp-cal__day', 'a shovelware platinum must not tint a square'
    assert Client().get(_url(run)).context['calendar_totals'] == {'done': 0, 'all': 1}


def test_a_completed_month_is_struck_and_an_incomplete_one_is_not():
    run = _run(CHALLENGE_TYPE_CALENDAR)
    CalendarDay.objects.filter(challenge=run, month=2).update(in_all=True, in_clean=True)
    _fill(run, 3, 3)

    crests = re.findall(r'<button type="button" role="tab"[^>]*>', _body(run))
    assert 'pp-cal__crest--struck' in crests[1]
    assert 'pp-cal__crest--struck' not in crests[2], 'one filled day is not a month'
    # NO METAL. The crest was bronze/silver/gold for whichever lens completed the month.
    assert 'data-tier' not in crests[1]


def test_the_calendar_script_loads_only_on_a_calendar_run():
    """A hook on one template branch is dead for the other, and the reverse -- a script loaded on every
    run would run a Calendar boot on a page with no `.pp-cal`."""
    assert 'challenges-calendar.js' in _body(_run(CHALLENGE_TYPE_CALENDAR))
    assert 'challenges-calendar.js' not in _body(_run(CHALLENGE_TYPE_AZ))


# ── the Hall of Fame hero ────────────────────────────────────────────────────────────────────────────

def test_each_renderer_is_fed_only_its_own_type():
    """THE SILENT DISPATCH BUG. `boards_for` returns `[]` for a Calendar run because it owns no
    `ChallengeSlot` rows, so the wrong dispatch draws no board rather than raising."""
    from challenges.views import HallOfFameView

    runs = [_run(CHALLENGE_TYPE_CALENDAR), _run(CHALLENGE_TYPE_AZ)]
    boards = HallOfFameView().enrich(runs)['boards']

    assert set(boards) == {runs[0].pk, runs[1].pk}, 'every run asked about gets an entry'
    assert boards[runs[0].pk][0]['days'], 'the Calendar run got a Calendar board'
    assert 'squares' in boards[runs[1].pk][0], 'the A-Z run still got a slot board'


def test_a_page_with_no_calendar_run_pays_nothing_for_the_calendar_renderer():
    """The same only-if-needed discipline `boards_for` already applies to the job catalogue: both
    renderers return `{}` without a query when handed nothing."""
    from challenges.views import HallOfFameView

    az_only = [_run(CHALLENGE_TYPE_AZ)]
    mixed = az_only + [_run(CHALLENGE_TYPE_CALENDAR)]

    with CaptureQueriesContext(connection) as without:
        HallOfFameView().enrich(az_only)
    with CaptureQueriesContext(connection) as with_calendar:
        HallOfFameView().enrich(mixed)

    assert len(with_calendar) == len(without) + 1, (
        'a Calendar run on the page should cost exactly one more query, its day rows')


def test_the_hero_board_is_chosen_by_the_key_the_renderer_returned():
    """`calendar_boards_for` returns `days` where `boards_for` returns `squares`, so the key that keeps
    the two markup branches apart is also the template's test for which one arrived. No second source of
    truth to drift from `challenge_type`."""
    hero = open('templates/challenges/partials/_run_hero.html', encoding='utf-8').read()
    assert '{% if board.0.days %}' in hero
    assert 'pp-chero__cal' in hero


# ── the script, pinned by source text ────────────────────────────────────────────────────────────────

def _script_code():
    """The script with its comments stripped.

    EVERY ASSERTION ABOUT WHAT THE SCRIPT DOES NOT DO HAS TO READ THIS, never the raw file. The
    comments explain at length why `data-view` and the ignite bloom are absent, so a raw substring
    search finds the prose documenting the fix and reports it as the bug -- which happened twice in
    this file before the reader existed. It is the same discipline the stylesheet readers use.
    """
    return re.sub(r'/\*.*?\*/|//[^\n]*', '', _script(), flags=re.S)


def _script():
    return open('static/js/challenges-calendar.js', encoding='utf-8').read()


def test_the_day_sheet_uses_the_shared_primitives_rather_than_a_fourth_hand_roll():
    """TWO SURFACES ALREADY FETCH A FRAGMENT INTO `.pp-detail-modal` AND BOTH HAND-ROLL A FOCUS TRAP --
    `game-detail.js`'s own comment calls itself a "refactor candidate: hoist this + badge-detail's copy
    into a shared PlatPursuit util". The util they wanted is `takeover`, whose docstring says it exists
    "because three surfaces now do it and every one re-implemented the accessibility parts slightly
    differently". This asserts the Calendar did not become the fourth copy.

    `aria-modal` ALONE DOES NOT TRAP TAB, which is the reason it matters: without the trap a keyboard
    reader tabs straight out of the dialog into the board behind the scrim."""
    js = _script_code()

    assert 'PP.takeover(' in js, 'the a11y half must come from the shared primitive'
    assert 'PP.dismissableSheet(' in js, 'and the touch drag from the other one'
    # SCOPED TO THE SHEET, not the whole file. These stand in for "do not hand-roll the modal a11y",
    # and file-wide they asserted something far stronger and far less durable: that this file contains
    # no keyboard handling of ANY kind. A 365-cell grid is a textbook candidate for arrow-key roving
    # (the WAI-ARIA grid pattern), which needs a `keydown` here and has nothing to do with a focus
    # trap -- and would have failed as "a hand-rolled Tab trap is back".
    sheet = js[js.index('function wireDaySheet'):]
    assert 'keydown' not in sheet, 'a hand-rolled Tab trap is back'
    assert 'activeElement' not in sheet, 'a hand-rolled focus restore is back'


def test_the_day_fetch_checks_the_response_before_injecting_it():
    """THE FRAGMENT ASKS FOR THIS BY NAME. A hidden run and a missing square both answer 404, and this
    project installs a GET-only `handler404` -- so an unchecked `.text()` would inject the whole 404
    PAGE into the sheet."""
    js = _script_code()
    assert 'r.ok ? r.text() : null' in js


def test_the_day_sheet_replaces_its_body_and_empties_it_on_close():
    """THE ONE REQUIREMENT `_calendar_day.html` STATES. The fragment carries `id="cal-day-title"` and the
    dialog's `aria-labelledby` points at it, so two copies in the document would make the dialog announce
    the square the reader had just left. Replacing on open and emptying on close both keep it unique."""
    js = _script_code()
    assert "body.innerHTML = html" in js, 'it must replace, not append'
    assert "body.innerHTML = ''" in js, 'and empty on close'
    # SCOPED TO THE BODY WRITE. The blanket `'appendChild(' not in js` was predicted to misfire and
    # did, on the very next change: the shell is now `appendChild`ed to `document.body`, which is how
    # it gets out of `#page-recede` and back after `takeover` removes it. What must not happen is the
    # FRAGMENT being appended into the body, so that is what this asks.
    assert 'insertAdjacentHTML' not in js
    assert 'body.appendChild(html' not in js and 'body.append(' not in js


def test_the_day_sheets_body_listener_binds_once():
    """`onPageReady`'s CONTRACT. Element wiring re-runs on an HTMX history restore and `document.body`
    survives it, so an unguarded delegate binds twice and every click fetches twice."""
    sheet = _script_code()
    sheet = sheet[sheet.index('function wireDaySheet'):]
    # READ AS AN EARLY RETURN, not as a substring. The first version asserted `'if (first) {'` appeared
    # somewhere after `wireDaySheet`, which it would for any unrelated use of the flag while the
    # delegate sat outside the block.
    assert sheet.index('if (!first) { return; }') < sheet.index("addEventListener('click'"), (
        'the delegate is bound before the `first` guard, so a history restore binds it again')


def test_the_months_colour_reaches_the_dialog_shell():
    """CUSTOM PROPERTIES INHERIT DOWNWARD ONLY, which is the whole reason this needs wiring at all.
    `--cal-c` is set by `data-month` on the fragment root, and the fragment sits INSIDE the dialog -- so
    the dialog that wraps it can never read the hue from its own child. The square carries its month,
    the script copies it onto the shell, and the hue table lists the shell as a third selector.

    IT FAILS SILENTLY IF ANY LINK BREAKS: the dialog simply renders grey, which is exactly the state the
    owner asked to be rid of ("maybe a gradient using the month colors"), and nothing else would notice.
    """
    board = _body(_run(CHALLENGE_TYPE_CALENDAR))
    assert 'data-month="1"' in board, 'a day square must carry its own month for the script to copy'

    sheet = _script_code()
    sheet = sheet[sheet.index('function wireDaySheet'):]
    assert "modal.setAttribute('data-month'" in sheet, 'the month never reaches the shell'

    css = _calendar_css()
    assert '.pp-detail-modal[data-month="1"]' in css, 'the hue table does not reach the shell'
    dialog = _css_rule(css, '#cal-day-modal .pp-detail-modal__dialog {')
    assert 'var(--cal-c' in dialog, 'the dialog is not wearing the month at all'


def test_only_a_stacked_day_wears_its_count():
    """OWNER, 2026-10-05, choosing the badge over a toggle. MOST FILLED DAYS HOLD EXACTLY ONE PLATINUM,
    so a count on every one would print "1" across the board -- the same information as "filled", just
    louder. The signal is the minority that stack, which is the threshold the side column's "busiest
    day" already uses.

    `x4` RATHER THAN `4`, because a bare numeral in the corner of a cell whose content is already a
    numeral reads as part of the date."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 5, 2)                 # one platinum -- no badge
    _fill(run, 5, 9, plats=4)        # four -- badged
    may = _section(_body(run), 'id="cal-month-may"', until='</section>')

    assert may.count('pp-cal__stack') == 1, 'exactly the stacked day carries a count'
    assert '&times;4' in may or '×4' in may
    assert '&times;1' not in may, 'a day holding one platinum must not announce it'


def test_a_shovelware_only_day_is_never_badged():
    """`card.filled`, NOT `card.counts`. A square that is `in_all` and not `in_clean` carries the
    SHOVELWARE-INCLUSIVE count -- `CalendarDay.plat_count`'s own comment says so -- so badging it would
    advertise a figure this board excludes, on a square the board deliberately draws empty."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    CalendarDay.objects.filter(challenge=run, month=5, day=4).update(
        in_all=True, in_clean=False, earned_on=dt.date(2019, 5, 4), plat_count=6)
    may = _section(_body(run), 'id="cal-month-may"', until='</section>')

    assert 'pp-cal__stack' not in may
    assert '&times;6' not in may


def test_the_count_is_spoken_as_well_as_drawn():
    """THE NUMERAL IS `aria-hidden`, so the `sr-only` label is the only thing a screen reader gets -- a
    count that existed only in the corner of a tile would not exist at all for them."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 5, 9, plats=4)
    may = _section(_body(run), 'id="cal-month-may"', until='</section>')

    assert 'May 9: filled, 4 platinums.' in may


def test_the_side_column_can_preview_a_day_without_fetching():
    """OWNER, 2026-10-05: "I'd like the card on the right to show information on a filled in day when
    hovered". Every figure it shows is carried on the square, which is why the per-day count is stored at
    all -- a hover that asked the server would put a request behind every mouse movement across a
    365-cell grid.

    THE SAME PREDICATE AS THE CLICK. A square previews exactly when it opens, because an element that
    previews something a click will not open is worse than one that does neither."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 3, 3, plats=4)
    body = _body(run)

    openable = re.findall(r'data-day-url=', body)
    previewable = re.findall(r'data-peek-label=', body)
    assert len(previewable) == len(openable) == 1, 'preview and open must be the same squares'
    assert 'data-peek-plats="4"' in body, 'the count rides the square, not a request'

    sheet = _script_code()
    peek = sheet[sheet.index('function wireDayPeek'):]
    assert 'fetch(' not in peek, 'the peek must not ask the server'
    # Focus mirrors hover: a keyboard reader has none, and a phone has none at all.
    assert 'focusin' in peek and 'mouseover' in peek


def test_the_peek_and_the_month_facts_occupy_one_box():
    """NOTHING MOVES WHEN A POINTER CROSSES THE GRID. Both faces sit in the same grid area, so the
    column's height is the taller of the two -- a side column that resized on hover would shift the board
    under the cursor, which on a 365-cell grid the cursor crosses constantly.

    `[hidden]` NEEDS ITS OWN RULE once the parent is a grid: a grid item is `display: block` whatever its
    own rule said, so the attribute stops hiding anything without it."""
    css = _calendar_css()
    flat = ' '.join(css.split())
    assert '.pp-cal__stats > .pp-cal__peek, .pp-cal__stats > .pp-cal__facts { grid-area: 1 / 1;' in flat
    assert '.pp-cal__stats > [hidden] { display: none; }' in flat


def test_the_peek_is_silent_to_screen_readers():
    """A LIVE REGION FIRING ON EVERY SQUARE A POINTER CROSSES WOULD BE HOSTILE, and the modal is the
    accessible path to the same information. The peek is a sighted-pointer convenience layered on top,
    so it is `aria-hidden` and announces nothing."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    peek = re.search(r'<div class="pp-cal__peek"[^>]*>', body).group(0)
    assert 'aria-hidden="true"' in peek, 'an aria-hidden subtree cannot announce, which is the guarantee'

    # SCOPED TO A PANEL, not the page: the nav's search box carries its own `aria-live` region and has
    # nothing to do with the board. A page-wide absence assertion was wrong, not merely broad.
    january = _section(body, 'id="cal-month-jan"', until='</section>')
    assert 'aria-live' not in january, 'the board must not announce a hover'


def test_the_day_sheet_obeys_takeovers_three_contract_rules():
    """REUSING A PRIMITIVE MEANS READING ITS CONTRACT, and the first version of this slice reused the
    name. Three blockers shipped, each documented elsewhere in this repo, and each contradicted by a
    comment in the commit that broke it.

    IT REMOVES ITS ROOT on close, so the root must be re-attached per open -- `monthly-recap.js` appends
    its container to `document.body` on every open for exactly this reason. Passing a server-rendered
    element and letting it be deleted meant the sheet worked ONCE, then wrote into a detached node: a
    scrim over a blank page, scroll locked, Tab dead, Escape the only way out.

    IT SCALES `#page-recede`, and a `position: fixed` overlay inside a transformed ancestor resolves
    against that ancestor rather than the viewport -- so the shell must sit outside it.

    ITS `focusSel` IS A PLAIN `querySelector().focus()`, so the target has to be focusable or focus never
    enters the dialog and the Tab trap never arms.
    """
    sheet = _script_code()
    sheet = sheet[sheet.index('function wireDaySheet'):]

    assert 'document.body.appendChild' in sheet, (
        'the root is never re-attached, so the sheet dies on its first close')
    assert 'exitMs: 0' in sheet, (
        'the default 240ms teardown leaves a window where the old close empties the new sheet')

    frag = (ROOT_DIR / 'templates' / 'challenges' / 'partials' / '_calendar_day.html').read_text(
        encoding='utf-8')
    assert 'id="cal-day-title" tabindex="-1"' in frag, (
        'the focus target cannot hold focus, so the Tab trap never arms')


def test_a_repeat_click_on_one_square_cannot_open_the_sheet_twice():
    """THE GUARD THAT WAS ITS OWN DEFECT. `pending = url` could not tell two clicks on the SAME square
    apart -- the case the slice was written for, since squares are 44px and adjacent -- so both responses
    passed `pending !== url` and `open()` ran twice. The second `takeover` overwrote the handle and
    orphaned the first: its capture-phase keydown listener leaked for the life of the page, and because
    it had captured `overflow: hidden` as the value to restore, closing left the page unscrollable.

    A PER-REQUEST TOKEN, so a repeat click supersedes its own earlier one, plus a `close()` at the top of
    `open()` so a double-open is impossible rather than merely unlikely."""
    sheet = _script_code()
    sheet = sheet[sheet.index('function wireDaySheet'):]

    assert 'token += 1' in sheet and 'mine !== token' in sheet
    assert 'pending' not in sheet, 'the url-identity guard is back'
    at = sheet.index('function open(')
    assert 'close();' in sheet[at:sheet.index('PP.takeover(', at)], (
        'a live sheet is not torn down before a new one opens')


def test_the_day_delegate_resolves_its_nodes_at_event_time():
    """THE OTHER HALF OF `onPageReady`'s CONTRACT. `if (first)` stops the double-bind; it does not stop a
    once-bound delegate holding element references from the FIRST wiring. An `htmx:historyRestore`
    replaces the page content, so those nodes are gone and the sheet went silently dead after a Back --
    a 200 in the network tab and nothing on screen.

    THE BOOT-TIME LOOKUP IS LEGITIMATE and deliberately not asserted against: it exists only to MOVE the
    shell out of `#page-recede`. What matters is that the click path re-resolves."""
    sheet = _script_code()
    sheet = sheet[sheet.index('function wireDaySheet'):]
    delegate = sheet[sheet.index("addEventListener('click'"):]

    assert 'document.getElementById' not in delegate, (
        'the delegate holds boot-time nodes instead of going through `shell()`')
    assert 'function shell()' in sheet
    opener = sheet[sheet.index('function open('):sheet.index("addEventListener('click'")]
    assert "modal.querySelector('[data-day-body]')" in opener
    assert "modal.querySelector('.pp-detail-modal__dialog')" in opener


def test_the_script_leaves_immediately_when_there_is_no_board():
    """It is gated in the template, but a gate is one branch: the guard is what makes the file safe to
    load anywhere, and it is the reason the gate can be relaxed later without a console error."""
    js = _script()
    assert "querySelector('.pp-cal')" in js
    assert re.search(r'if \(!board\)\s*\{\s*return;', js)


def test_the_month_tabs_go_through_the_shared_tablist_helper():
    """THE REUSE MISS THIS PINS. The first version hand-rolled roving tabindex, Arrow/Home/End with wrap
    and the focus-versus-activate split -- all of which `PlatPursuit.wireTablist` already does, and it is
    explicitly "markup/class-agnostic: pass the tab elements and a select callback". Going through it also
    buys the two companions a hand-rolled copy silently skipped.

    READ THROUGH `_script_code()`, WHICH STRIPS COMMENTS, and this test did not -- which made it wrong in
    both directions at once. `assert 'ignite: true' in js` was satisfied ONLY by the comment that says
    the crests take NO ignite (a square 44px button wrapping a round coin would get a square halo), so
    the assertion asserted the opposite of the code and passed. And `'keydown' not in js` fails the
    moment any comment in the file uses the word -- which is exactly how it broke when the day sheet
    added a note about `takeover`'s capture-phase listener. This file's own `_script_code` docstring
    records the same trap happening twice before the reader existed.
    """
    code = _script_code()
    assert 'PP.wireTablist(tabs, {' in code
    assert 'ignite' not in code, (
        'the crests deliberately take NO ignite bloom -- see the comment on `.pp-cal__crest`')
    assert 'PP.slideViewIn(' in code, 'the month panel slides in directionally'
    # The hand-rolled keyboard handling must not come back alongside the helper. Scoped to the TABLIST
    # wiring: the day sheet is in this file too, and a future arrow-key roving grid would legitimately
    # add a `keydown` that has nothing to do with this switcher.
    tablist = code[:code.index('function wireDaySheet')]
    assert 'ArrowRight' not in tablist and 'keydown' not in tablist


def test_the_roving_tabindex_is_resynced_after_the_selection_moves():
    """`isActive` reads `aria-selected`, which `show` rewrites -- so the helper has to be told AFTER that,
    or the one Tab-reachable crest stays pointing at the month that was active before the switch."""
    js = _script()
    assert "return tab.getAttribute('aria-selected') === 'true';" in js
    assert js.index('show(tabs.indexOf(tab));') < js.index('api.syncTabindex();')


def test_the_script_keeps_the_page_still_when_scrolling_the_crest_strip():
    """`block: 'nearest'` is load-bearing on a phone: the default would scroll the document vertically
    to centre a 44px control and throw the board off screen."""
    assert "{ block: 'nearest', inline: 'center' }" in _script()


def test_the_panel_is_resolved_through_aria_controls_rather_than_by_position():
    """Pairing the tab list against the panel list by index works only while both render in the same
    order, and fails SILENTLY when they do not -- the script showing one month while the accessible tree
    names another. Reading the attribute makes the declared relationship the only thing that decides."""
    js = _script()
    assert "document.getElementById(tab.getAttribute('aria-controls'))" in js
    assert 'panel !== shown' in js, 'panels are matched against the resolved one, not against an index'


def test_the_script_hides_panels_the_same_way_the_server_did():
    """One mechanism for "not showing". A class plus `hidden` gives a state where the two disagree, and
    the panel is then visible to a screen reader and not to an eye."""
    js = _script()
    assert 'panel.hidden = panel !== shown' in js
    assert 'classList' not in js, 'panels are toggled by `hidden`, not by a class'


# ── the holes the audit found: things nothing was pinning ────────────────────────────────────────────

def test_each_crest_lights_its_own_segment_of_the_rim():
    """THE RIM IS HOW A CREST SAYS WHICH MONTH IT IS -- position, not the three-letter face. Nothing
    pinned it, so forcing every `--seg` to the same value survived: twelve identical coins."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    segs = re.findall(r'class="pp-cal__rim-seg"[^>]*style="--seg: (\d+);"', body)
    assert [int(s) for s in segs] == list(range(12))


def test_the_rim_normalises_its_dash_units():
    """`pathLength="100"` is what removed the hardcoded circumference, and it is load-bearing: without it
    the dash arrays are in user units and a radius change silently breaks the twelve segments."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    # FOUR CIRCLES PER CREST now: the twelve-segment rim's track and lit segment, plus the completion
    # arc's track and fill. Every one of them is normalised, which is what keeps the radii free to move.
    assert body.count('pathLength="100"') == 48, 'four circles on each of twelve crests'


def test_every_crest_names_its_month_without_relying_on_colour():
    """The face is a three-letter abbreviation and the state is carried in metal, so the full month name
    and the per-lens figures have to be in the accessible tree. Colour is never the only cue."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    crests = _section(body, 'pp-cal__crests', until='class="pp-cal__panel"')
    for name in ('January', 'February', 'December'):
        assert name in crests
    assert crests.count('class="sr-only"') == 12


def test_the_day_grid_is_a_real_list():
    """Sibling divs each ending in screen-reader text read as one run-on sentence. `role="list"` is not
    belt-and-braces: Tailwind's preflight strips the implicit role from a list styled with
    `list-style: none`, which `.pp-cal__grid` sets itself."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    assert body.count('<ul class="pp-cal__grid" role="list">') == 12


def test_every_tab_and_panel_point_at_each_other():
    """THE TABLIST RELATIONSHIP, which nothing pinned -- and the script now RESOLVES the panel through
    `aria-controls` rather than by matching list positions, so dropping it breaks the switcher rather
    than only the accessible tree."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    controls = re.findall(r'id="cal-tab-(\w+)" aria-controls="([\w-]+)"', body)
    labelled = re.findall(r'id="(cal-month-[\w-]+)"\s+aria-labelledby="(cal-tab-[\w-]+)"', body)

    assert len(controls) == 12 and len(labelled) == 12
    for slug, panel_id in controls:
        assert panel_id == 'cal-month-%s' % slug
    for panel_id, tab_id in labelled:
        assert tab_id == 'cal-tab-%s' % panel_id[len('cal-month-'):]


def test_the_day_cascade_uses_the_name_the_page_already_reads():
    """`--rev` is the house name for this on this very page: the slot board stamps it per square and the
    stylesheet reads it to drive `ppRevealIn`. The Calendar first stamped `--i` and wrote no rule, so it
    shipped 365 dead inline styles and snapped in beside a board that cascades."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    # PER MONTH, so the counter tops out at 30 (a 31-day month, zero-based) and twelve cells carry 0.
    # A run-wide index would reach 364, which is exactly the bug: every cell past the twelfth shared one
    # capped delay, so eleven months had no cascade and a switched-to panel arrived blank.
    assert body.count('--rev: 0;') == 12
    assert '--rev: 30;' in body
    assert '--rev: 364;' not in body
    assert '--rev: 31;' not in body
    assert '--i:' not in body

    block = _calendar_css()
    assert 'var(--rev, 0)' in block, 'the markup half is useless without the CSS half'


def test_the_calendar_block_guards_reduced_motion():
    """`switcher.css` was corrected for this exact omission. The crest lifts on every month switch and
    every day cell cross-fades on a lens switch."""
    block = _calendar_css()
    assert 'prefers-reduced-motion: reduce' in block


def test_no_lens_machinery_survives_in_the_stylesheet_or_the_script():
    """THE COLLAPSE, PINNED. The tint read the checked radio through `:has()` and the figures were
    revealed per lens; both are gone, and so is every `data-view` the script once wrote. Pinned together
    because re-adding any one of them without the others is how a half-collapsed lens comes back."""
    block = _calendar_css()
    assert ':has(input' not in block
    # `.pp-cal__count`, THE RETIRED NAME -- not `.pp-cal__stack`, which is the live per-day count
    # badge added later. A blanket rename once swept this assertion onto the new class, where it
    # guarded the absence of something that is supposed to be there.
    assert '.pp-cal__count' not in block
    assert '.pp-cal__lens' not in block
    assert '[data-view' not in block

    code = _script_code()
    assert 'dataset.view' not in code
    assert 'cal-view' not in code


# ── the hero, which had no behavioural coverage at all ───────────────────────────────────────────────

def _hero_html(run):
    from django.template.loader import render_to_string
    from challenges.services.calendar_render import calendar_boards_for
    from challenges.services.plaque import plaques_for
    from challenges.services.rewards import granted_titles_for
    # ALL SIX VARIABLES the production caller passes (`browse_results.html` sends
    # `run index first board earned_title plaque`), so the helper renders what the page renders. The
    # first version passed three, which left the avatar's `eager` and the name mark's `index` empty --
    # harmless for the board assertions, and exactly the kind of near-miss that makes a helper
    # unreusable for the next thing that needs it.
    return render_to_string('challenges/partials/_run_hero.html', {
        'run': run,
        'index': 0,
        'first': True,
        'board': calendar_boards_for([run])[run.pk],
        'earned_title': granted_titles_for([run]).get(run.pk),
        'plaque': plaques_for([run]).get(run.pk),
    })


def test_the_hero_draws_a_cell_for_every_day_and_tints_the_filled_ones():
    """THE HOLE: nothing rendered a hero with a Calendar run, so mutations that emitted no filled class
    at all, or looped a key that does not exist, both survived -- every day blank and no test noticing."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 3, 3)
    _fill(run, 5, 5)
    html = _hero_html(run)

    # COUNTED ON THE ATTRIBUTE OPENING, not on the bare class: a filled cell carries both the base class
    # and the `--on` modifier, so the plain substring appears twice for every tinted day and the total
    # drifts with however many are filled.
    assert html.count('class="pp-chero__cday') == 365
    assert html.count('pp-chero__cday--on') == 2


def test_the_hero_draws_no_slot_shelf_for_a_calendar_run():
    html = _hero_html(_run(CHALLENGE_TYPE_CALENDAR))
    assert 'pp-chero__cal' in html
    assert 'pp-chero__shelf' not in html
    assert 'pp-chero__sq' not in html


def test_the_hero_tints_only_what_the_board_does():
    """The band and the board read the same column, so a shovelware platinum tints neither."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    for day in range(1, 3):
        _fill(run, 2, day)
    CalendarDay.objects.filter(challenge=run, month=3).update(in_all=True, in_clean=False)

    assert _hero_html(run).count('pp-cal__cday--on') == 0, 'sanity: the hero uses its own class'
    assert _hero_html(run).count('pp-chero__cday--on') == 2


def test_the_calendar_hero_takes_the_whole_row_through_the_tablet_band():
    """53 columns sharing the row with the plaque at 768 left a 3.9px cell, no better than mobile. Only
    `--jobs` was named in the rule that fixes this, so the widest board in the feature got the narrowest
    column."""
    block = _calendar_css()
    assert '.pp-chero--calendar { grid-template-columns: minmax(0, 1fr); }' in block


# ── the stylesheet, pinned by source text (there is no CSS test runner either) ───────────────────────

def _calendar_css():
    """The Calendar's own RULES from `challenges.css`: bounded, and with the comments stripped.

    BOUNDED, because `css[css.index('PLAT CALENDAR BOARD'):]` runs to the end of the file -- which works
    only while this block happens to be last, and the next appended component would silently put every
    assertion below into somebody else's rules. Same unbounded-slice mistake `_section` exists to avoid,
    so it gets the same treatment: cut at the next top-level block banner.

    STRIPPED, because this block documents its own history at length -- which rules were deleted, which
    values were wrong -- and every assertion here is about RULES. Reading the raw text made an absence
    assertion match the comment explaining the absence, three separate times in this file. Slicing
    happens first because the boundaries ARE comments; stripping happens to the result.
    """
    css = open('static/css/components/challenges.css', encoding='utf-8').read()
    block = css[css.index('PLAT CALENDAR BOARD'):]
    nxt = block.find('* ============================================================ */',
                     block.index('============================================================ */') + 10)
    if nxt != -1:
        block = block[:nxt]
    return re.sub(r'/\*.*?\*/', '', block, flags=re.S)


def test_no_state_rule_touches_the_month_hue():
    """COLOUR AND STATE ARE SEPARATE AXES, which is now enforceable rather than scoped around.

    The earlier version scoped the active colour with `:not(--struck)` because otherwise selecting an
    earned month recoloured it -- the exact bug this stylesheet records paying for on `.pp-csq`
    ("hovering a finished square erased the one mark the page exists to show"). With a hue per month
    that scoping is not enough: the honest rule is that no state declaration writes `--cal-c` at all.
    Active and hover are a lift and an outline in the BRAND colour; the hue says which month and whether
    it is earned.

    ASSERTED AS AN ABSENCE ACROSS EVERY STATE RULE, because that is the only form that cannot be
    satisfied by one more `:not()`.
    """
    block = _calendar_css()
    for line in block.splitlines():
        if '--cal-c:' not in line:
            continue
        assert 'data-month' in line or '--cal-c: var(--pp-text-mute)' in line, (
            'only a month hue or the fallback may set --cal-c, not a state: %s' % line.strip())

    assert 'data-tier' not in block, 'the lens metals went with the lenses'
    # The active cue is shape plus the brand colour, never the month's. Strengthened 2026-10-05 (the
    # owner could not tell which month was selected) -- a lift, a scale, a full-strength ring and a glow
    # instead of a 1px lift and a 65%-alpha ring. The FIRST attempt at that tinted the ring `--cal-c`,
    # which this test caught: it would have put the ring in the same colour as the coin it surrounds,
    # lowering the contrast it exists to create.
    assert '.pp-cal__crest[aria-selected="true"] { transform: translateY(-3px) scale(1.06); }' in block
    active = _css_rule(block, '.pp-cal__crest[aria-selected="true"] .pp-cal__coin {')
    assert 'var(--cal-c' not in active, 'the active ring took the month hue'
    assert 'outline: 2px solid var(--pp-primary);' in active


def test_the_rim_draws_twelve_segments_rather_than_a_solid_ring():
    """ROUND CAPS WERE THE DEFECT, and it is invisible in a screenshot of one crest: a cap extends each
    dash by `stroke-width / 2` at BOTH ends, so a 2.5 stroke grew each dash past its own gap and the
    twelve segments painted as one ring. The lit segment still read against the dim track, which is why
    only arithmetic caught it."""
    rim = _css_rule(_calendar_css(), '.pp-cal__rim-track,')
    assert 'stroke-linecap: butt' in rim
    assert 'stroke-linecap: round' not in rim, 'round caps are what ate the gaps'
    # Normalised units: twelve slots of 100/12 = 8.3333, a 6-long dash leaving a 2.3333 gap.
    block = _calendar_css()
    assert 'stroke-dasharray: 6 2.3333' in block
    assert 'stroke-dasharray: 6 94' in block


def test_the_crest_gap_is_fixed_so_the_coins_do_not_shrink_as_the_window_grows():
    """`clamp(8px, 1.4vw, 20px)` against a STEP-FUNCTION container: `.container` holds its width across
    each band while a `vw` gap keeps growing, so twelve coins shrank monotonically from 47px at 768 to
    37px at 1279 -- with a step DOWN at `lg:`, where the stats column arrived and took 304px out of a
    card that does not widen until 1280. The band was at its smallest exactly where this slice meant to
    make it generous, and no test could catch it because none computes a width."""
    flat = ' '.join(_calendar_css().split())
    assert '.pp-cal__crests { overflow-x: visible; gap: 10px; }' in flat
    assert '1.4vw' not in flat, 'a viewport-relative gap cannot divide a step-function width'


def test_the_coin_band_paints_flush_with_the_panel():
    """`box-sizing: border-box` IS APP-WIDE, so a crest row at `width: 100%` paints its coins inside
    [6, W-6] -- the row carries `padding: 7px 6px 10px`, a measured budget for the focus ring and the
    struck shadow, so the padding cannot simply go. Six pixels at each edge reads as a mistake against
    a head and a grid that both start at 0.

    SO THE BOX IS 12px WIDER AND PULLED BACK 6px. The two declarations are a PAIR: either one alone
    moves the band off the panel's edge, in opposite directions, which is why they are asserted
    together rather than one standing in for the other.

    SHAPE 3 INSET THE DAY GRID BY 6px INSTEAD, matching the band because the two were then the same
    width. Shape 4 makes the band wider on purpose -- the owner asked for the coins to "cover the area
    of the calendar + the stats on the side" -- so there is nothing to match and the grid gets its 12px
    back. The old inset must not survive: it would silently narrow every day cell for no reason.
    """
    flat = ' '.join(_calendar_css().split())
    assert 'padding: 7px 6px 10px' in flat, 'the row inset the negative margin cancels'
    assert 'width: calc(100% + 12px);' in flat and 'margin-inline: -6px;' in flat
    assert 'padding-inline: 6px' not in flat, (
        "shape 3's grid inset has no job once the band is wider than the grid")


def test_the_side_column_narrows_in_the_first_desktop_band():
    """`.container` steps at 1024 and again at 1280, so one fixed column width takes the same pixels out
    of a 954px card and a 1210px one.

    IT IS THE DAY CELL THIS PROTECTS, NOT THE COIN. Shape 4 took the coins off this arithmetic entirely
    -- the band spans the panel, so the side column no longer comes out of its width -- but the grid
    track still loses it. At 280px throughout, the track at 1024 is 650px and the cell 57.8px, DOWN from
    62.6px at 768: the cell shrinking as the window widens, which is the defect the crest gap had and is
    no better on the squares. At 230px the cell goes 62.6 -> 62.8 -> 72.4 and only grows.

    THE VALUE HAS NOW SURVIVED THREE DIFFERENT ARGUMENTS, two of which were wrong (both quoted coin
    sizes measured in a state the same slice had already changed). Worth knowing before trusting the
    reason attached to a number here."""
    flat = ' '.join(_calendar_css().split())
    assert '--cal-stats: 230px' in flat, 'the first desktop band gets the narrower column'
    assert '@media (min-width: 1280px) { .pp-cal { --cal-stats: 280px; } }' in flat


def test_the_statlist_keeps_its_description_list_roles():
    """A GRID OR FLEX `<dl>` DROPS ITS TERM/DEFINITION MAPPING in Chromium and WebKit, so the column
    announces as one run with no pairing between a label and its figure. It is the same role-stripping
    that makes `role="list"` mandatory on every `<ul>` in these templates -- and a `<dl>` has no
    attribute to answer it with, so the only fix is not to take the role off. The app-wide guard only
    scans `<ul>`, which is why this needs its own pin."""
    statlist = _css_rule(_calendar_css(), '.pp-cal__statlist {')
    assert 'display: grid' not in statlist and 'display: flex' not in statlist
    assert 'grid-template-columns' not in statlist


def test_the_day_cell_is_capped_so_it_does_not_balloon_on_desktop():
    """Seven columns at every width made the cell 135px at 1024 and 208px at 1920 -- a panel of enormous
    near-empty boxes each holding one 13px numeral, on the primary target."""
    block = _calendar_css()
    assert 'repeat(10, minmax(0, 1fr))' in block
    # THE CAP IS ON THE PANEL, not the grid inside it. On the grid alone it left-aligned while the month
    # head stayed at full width, so the head and the squares under it disagreed; the head and the grid
    # are both inside the panel, so capping there is what makes them share an edge.
    # THE CAP IS ON `.pp-cal` NOW, not on the panel: a side column arrived at `lg:` and the panel has
    # to fill the row so its two columns can divide it. The crest row is capped to the board column's
    # width by the same two tokens, which is what keeps the coins lined up with the days.
    flat = ' '.join(block.split())
    assert '.pp-cal { max-width: 1100px; margin-inline: auto; }' in flat
    # SCOPED TO THE CREST ROW. Unscoped, this assertion passed if the declaration moved onto any
    # selector at all -- so the one line that is supposed to guarantee the coins sit over the days
    # could not tell WHICH box was being capped. (The `margin-inline: auto` assertion that stood here
    # was worse: the string is in both the `md:` and the `lg:` rule, so it could not fail.)
    #
    # FOUND BY WHAT IT DECLARES, not by position. `.pp-cal__crests` has five rules and the one that
    # bounds it is not the last: the width-story block sits ABOVE the base rule in the file, so `last=`
    # lands on the `md:` gap instead. Position is the wrong handle on a selector this file reuses --
    # the same lesson `.pp-cal__stats` taught two tests below.
    #
    # THE BAND'S OWN BOUND IS THE `md:` CEILING NOW. Shape 4 removed its desktop cap, so what is left to
    # assert here is that exactly one rule bounds it and that the bound is the shared body token plus
    # the 12px the negative margin cancels -- not a second number that could drift from `--cal-body`.
    crests = [r for r in _css_rules(block, '.pp-cal__crests') if 'max-width: calc(' in r]
    assert len(crests) == 1, 'exactly one rule puts a computed ceiling on the crest row'
    assert 'max-width: calc(var(--cal-body) + 12px);' in crests[0]


def test_the_crest_band_carries_no_cap_of_its_own_at_desktop():
    """SHAPE 4 (owner, 2026-10-04): "I still think the medallions should cover the area of the calendar
    + the stats on the side." So the band is bounded only by `.pp-cal`'s own 1100px, and shape 3's
    `calc(100% - stats - gutter)` cap is gone.

    THE `md:` CEILING HAS TO BE LIFTED EXPLICITLY, which is the part that would fail silently: the
    tablet rule caps the row at `--cal-body + 12px`, and a cap set in a lower band keeps applying in a
    higher one. Left standing, a 712px band would sit inside a 954px panel -- shape 3's misalignment
    with different numbers, and nothing else in the suite would notice.
    """
    flat = ' '.join(_calendar_css().split())
    assert 'max-width: calc(100% - var(--cal-stats) - var(--cal-gutter));' not in flat, (
        "shape 3 capped the band to the board column; shape 4 spans the panel")
    assert '.pp-cal__crests { max-width: none; }' in flat


def test_the_crest_band_divides_whatever_row_it_is_given():
    """THE OWNER'S THIRD SHAPE for this width, and the test is rewritten rather than retired because the
    first two are still worth not going back to.

    1. Everything capped at one width. Fixed a real defect (a grid capped on its own, left-aligning
       under a full-width head) and made the twelve medallions small.
    2. The band full-bleed, the panel capped -- "we could even make them larger to allow them to span
       across the whole screen."
    3. BOTH MATCHED, WITH A SIDE COLUMN: "if we reduce the size of the medallions again to match the
       length of the days, could we potentially float something to the right side".

    This test described shape 2 for a slice after the CSS moved to shape 3, and still passed, because
    every string it asserted survived the change. A suite that documents two shapes as current is worse
    than one that documents neither.
    """
    block = _calendar_css()

    # ONE TOKEN for the body width, so the two capped blocks cannot drift apart.
    assert '--cal-body: 712px' in block
    assert block.count('max-width: var(--cal-body)') == 1, (
        'both capped blocks must read the same token in one rule, or they will diverge')

    # The coins divide whatever row they are given, so capping the row is what sizes them.
    flat = ' '.join(block.split())
    assert '.pp-cal__crest { flex: 1 1 0;' in flat
    assert 'aspect-ratio: 1' in flat
    # THE 112px CEILING IS PINNED AS A GUARD, NOT AS A SHAPE. It no longer binds at any width (the band
    # is bounded by `.pp-cal`'s 1100px, which puts a coin at 82.5px), and the assertion that used to
    # cover it was dropped when this test was rewritten for shape 4 -- leaving a live declaration no
    # test mentioned, which is how one gets deleted as dead. The stylesheet documents it as a
    # deliberate non-binding guard, so the pin says that rather than claiming it does work.
    assert 'max-width: 112px' in flat, (
        'a deliberate non-binding ceiling: it mattered when the band was full-bleed over a 712px panel')


def test_the_crest_face_scales_with_the_coin():
    """A fixed 12px label on a 110px medallion reads as a mistake, and the coin is fluid (`flex: 1 1 0`),
    so a per-breakpoint step would only agree with it at the widths somebody happened to write rules
    for. The clamp floor is the project's 12px type minimum, which the file-wide guard enforces."""
    block = _calendar_css()
    assert 'clamp(0.75rem' in block


def test_the_lens_switcher_carries_no_explanatory_line():
    """CUT ON THE BROWSER PASS. A sentence under the chips explained that a contract can be finished
    without a platinum, so the Contracts lens can hold a day the others do not -- true, and the owner's
    answer was that a hunter works it out: "Users can figure that out on their own, it's not really a
    huge deal." Pinned so it does not drift back in, because the explanation is genuinely tempting."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    assert 'pp-cal__lenshint' not in body
    assert 'without a platinum' not in body

    # The rule went with the markup: a dead rule is weight the next reader has to check.
    assert 'pp-cal__lenshint' not in _calendar_css()


def test_the_hero_band_never_shrinks_as_the_viewport_grows():
    """The first version capped the full-row rule at 1023, which re-created the exact discontinuity the
    `--jobs` comment above it exists to prevent: 11.2px at 768, back to 9.4px at 1024. A 53-column board
    wants the widest row it can get at every size and has no second shape to reach for."""
    block = _calendar_css()
    assert '@media (min-width: 768px) {\n    .pp-chero--calendar { grid-template-columns: minmax(0, 1fr); }' in block
    assert 'max-width: 1023px' not in block


def test_the_hero_cells_are_square():
    """`aspect-ratio` is INERT against definite row tracks. With `repeat(7, 1fr)` plus a `min-height`
    both dimensions were definite and the ratio was ignored, so every day rendered as a rectangle while
    the comment claimed squares. Auto rows let the column track set the width and the ratio the height."""
    block = _calendar_css()
    assert 'grid-template-rows: repeat(7, auto)' in block
    # `_calendar_css` strips comments now, so this reads rules only.
    assert 'min-height' not in block


def test_the_crests_take_no_ignite_bloom():
    """`ppTabIgnite` animates a `box-shadow: 0 0 20px 3px` on the element it is given. Every other
    consumer is a `.pp-switch__chip` with a 5px radius; here the element is a square button wrapping a
    round coin, inside a clipped scroll strip behind a mask fade, so the bloom would be a square halo
    around a circle with its edges cut off."""
    code = _script_code()
    assert 'ignite: true' not in code
    assert 'PP.slideViewIn(' in code, 'the directional panel slide is still taken'


# ── the view layer's own fixes ───────────────────────────────────────────────────────────────────────

def test_the_hall_of_fame_survives_a_one_shot_iterable():
    """`enrich` reads its argument THREE times now (the partition, the titles, the plaques), so the
    `list(runs)` it had once deleted as dead defence is live again. A generator drained by the partition
    leaves every hero on the page with no board, no title and no plaque, and raises nothing. Neither the
    live caller (a cached paginated queryset) nor the other tests here (lists) can show it."""
    from challenges.views import HallOfFameView

    runs = [_run(CHALLENGE_TYPE_CALENDAR), _run(CHALLENGE_TYPE_AZ)]
    out = HallOfFameView().enrich(iter(runs))

    assert set(out['boards']) == {runs[0].pk, runs[1].pk}
    assert out['boards'][runs[0].pk][0]['days']
    assert set(out['plaques']) == {runs[0].pk, runs[1].pk}


def test_the_slot_renderer_is_never_asked_about_a_calendar_run(monkeypatch):
    """THE PARTITION'S FORWARD DIRECTION, which the merge hides. `calendar_boards_for` is spread SECOND,
    so it overwrites whatever `boards_for` returned for a Calendar run -- meaning passing the whole list
    to `boards_for` produces identical output and the obvious assertion cannot see it. Recording the
    argument is what actually pins the split."""
    from challenges.services import slot_render
    from challenges.views import HallOfFameView

    seen = {}
    original = slot_render.boards_for

    def spy(challenges):
        challenges = list(challenges)
        seen['types'] = {c.challenge_type for c in challenges}
        return original(challenges)

    monkeypatch.setattr('challenges.views.boards_for', spy, raising=False)
    monkeypatch.setattr(slot_render, 'boards_for', spy)

    HallOfFameView().enrich([_run(CHALLENGE_TYPE_CALENDAR), _run(CHALLENGE_TYPE_AZ)])
    assert seen['types'] == {CHALLENGE_TYPE_AZ}, (
        'the slot renderer must not be asked about a type it has no rows for')


def test_the_hero_emits_the_class_its_own_css_rule_needs():
    """A PIN THAT SURVIVES DELETING THE FEATURE PINS NOTHING. The full-row rule is pinned by its text,
    but nothing checked that the hero emits `pp-chero--calendar` -- so replacing the class with a literal
    left the suite green and the rule dead."""
    html = _hero_html(_run(CHALLENGE_TYPE_CALENDAR))
    assert 'pp-chero--calendar' in html


def test_the_month_figure_appears_in_both_places_it_belongs():
    """TWO PLACES, ASSERTED SEPARATELY rather than as one total: the month head and inside each
    medallion. A single combined count would stay green if one place lost its figures while the other
    grew duplicates.

    ONE FIGURE EACH NOW. Both places rendered three -- one per lens -- sharing `.pp-cal__count` so a
    single set of `:has()` rules governed them and they could not disagree about which lens was active.
    The collapse left one figure printed directly, so the shared class and its rules went too.

    NOT TO BE CONFUSED WITH `.pp-cal__stack`, which is live: the per-day count badge on a stacked square.
    The retired name was deliberately NOT reused for it, so that this guard keeps meaning what it says.
    """
    run = _run(CHALLENGE_TYPE_CALENDAR)
    for day in range(1, 4):
        _fill(run, 2, day)
    body = _body(run)

    coins = _section(body, 'pp-cal__crests', until='class="pp-cal__panel"')
    assert coins.count('class="pp-cal__sub"') == 12, 'one figure per coin'
    assert '<span class="pp-cal__sub">3/28</span>' in coins, (
        "February's coin reads its own progress")

    heads = re.findall(r'<p class="pp-cal__tally">([^<]*)</p>', body)
    assert len(heads) == 12, 'one figure per month head'
    assert heads[1].strip() == '3 / 28'

    # THE SHARED CLASS AND ITS RULES ARE BOTH GONE, pinned together: a `.pp-cal__count` in the markup
    # with no reveal rule behind it rendered an empty figure on all twelve months, which is the defect
    # this test was written for in the first place.
    assert 'pp-cal__count' not in body
    assert 'pp-cal__count' not in _calendar_css()


def test_the_coin_counter_only_appears_where_it_fits():
    """A 44px coin at mobile has no room for a second line, and 52px at `md:` leaves about 41px of clear
    circle -- two lines of 12px type plus leading does not fit. So the wrapper is gated to `lg:`, where
    the coin is around 70px. The owner's own framing: "we don't have to do it for every use of the
    medallions but for these larger ones"."""
    block = _calendar_css()
    flat = ' '.join(block.split())
    assert '.pp-cal__sub { display: none; }' in flat
    # `lg:`, NOT `xl:`. This gate has been at both, each time on correct arithmetic over a different
    # band width: shape 3 capped the band to the board column and left the coin at 48.2px through the
    # 1024 band (33px of chord against the ~34px the plate needs), so it moved up; shape 4 spans the
    # panel and the same band gives 70.3px and 47.8px of chord, so it comes back down.
    assert '@media (min-width: 1024px) { .pp-cal__sub { display: inline-flex;' in flat


def test_the_coin_counter_is_not_announced_twice():
    """The coin is already `aria-hidden`, and the `sr-only` label beside it enumerates every lens's
    figure in full -- so the visual counter inside the coin must not add a second announcement."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    coin = body[body.index('class="pp-cal__coin"'):body.index('class="sr-only"',
                                                              body.index('class="pp-cal__coin"'))]
    assert 'aria-hidden="true"' in body[:body.index('class="pp-cal__coin"') + 60]
    assert 'pp-cal__sub' in coin, 'the counter is inside the aria-hidden coin, not beside it'


# ── the board's surface, which it shipped without ────────────────────────────────────────────────────

def test_the_board_sits_on_a_surface():
    """CONTENT NEVER SITS ON THE RAW PAGE BACKGROUND -- a project rule this board broke, caught by the
    owner on a browser pass: "the contents of the page are up against the raw background. This is not
    really used anywhere else on the site."

    `.scard` IS THE PRIMITIVE, not a hand-rolled surface: the reward panel on this same page uses it,
    and `challenges.css` records `.pp-csq-shelf__head` being banded for the identical reason ("the shelf
    label used to sit on the bare page background... broke the rule that content belongs on a surface").

    THE SLOT BOARD IS DELIBERATELY NOT CARDED, which is why this is asserted rather than assumed: "free
    content" means PAGE-FILLING grids, and 26 pieces of cover art at 100-176px are the page's subject.
    A numbered grid with a text head and a row of coins is a stats-shaped section, which still gets a
    card.
    """
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    at = body.index('class="pp-cal"')
    before = body[:at]

    assert '<section class="scard mb-3"' in before, 'the Calendar board must be on a `.scard`'
    # `mb-3` for the reason the reward panel carries it: `.pp-gbrowse` sets no `gap` and `.scard` no
    # `margin-bottom`, so without it the card's bottom edge meets the next block.
    assert 'pp-csq-board' not in body, 'and the slot board is a different page'


def test_a_day_cell_clears_the_touch_minimum_at_the_narrowest_width():
    """MEASURED, BECAUSE THE CARD CHANGED IT. A day cell becomes a BUTTON when the day modal lands, so
    44px is a floor rather than a preference -- and moving the board onto a `.scard` took the content
    box at 375px from 343px to 327px, which put a 6px gap's cell at 41.6px.

    The arithmetic is pinned rather than the appearance: seven columns and the gap are what decide it,
    so a change to either has to come back here and redo the sum.
    """
    block = _calendar_css()
    flat = ' '.join(block.split())
    assert '--cal-gap: 3px;' in flat, 'the base gap is what buys the 44px'
    assert 'grid-template-columns: repeat(7, minmax(0, 1fr));' in flat

    # 375px viewport - 32px page gutter = 343px, less the card's 8px padding either side.
    inner = 343 - 2 * 8
    cell = (inner - 6 * 3) / 7
    assert cell >= 44, 'a day cell is %.1fpx at 375px, under the 44px touch minimum' % cell


# ── the month hues and the completion arc ────────────────────────────────────────────────────────────

def test_every_month_declares_its_own_hue():
    """TWELVE HUES, KEYED ON `data-month`, which is the one state attribute on this board with a reader
    -- three others were deleted for having none, so this is the exception and it is pinned.

    A CYCLE RATHER THAN A SPECTRUM, because that is what a calendar is: January opens cold and December
    returns to frost so the row closes on itself. Adjacent pairs in the midsummer run are only ~17
    degrees apart, which is why lightness steps with the hue -- two coins differ on both axes.
    """
    block = _calendar_css()
    for month in range(1, 13):
        # KEYED UNDER `.pp-cal`, NOT ON THE CREST, so the month PANEL inherits its own colour from the
        # same declaration -- one set of rules for the coin and the content it switches to.
        rule = '.pp-cal [data-month="%d"]' % month
        assert rule in block, 'no hue declared for month %d' % month

    hues = re.findall(r'data-month="\d+"\]\s*\{\s*--cal-c: oklch\(([\d.]+) ([\d.]+) ([\d.]+)\)', block)
    assert len(hues) == 12, 'every month hue must be an oklch triple: %r' % hues
    assert len({h for _l, _c, h in hues}) == 12, 'two months share a hue angle'
    # The cycle closes: December sits nearer January than it does to the autumn run it follows.
    jan, dec, nov = float(hues[0][2]), float(hues[11][2]), float(hues[10][2])
    assert abs(dec - jan) < abs(dec - nov), 'December should turn back toward January, not trail November'


def test_the_progress_arc_is_one_colour_for_every_month():
    """ONE COLOUR AXIS, ONE MEANING -- the owner's point: "all be the same color so you can tell it
    specifically means progress". The month hue says WHICH month and WHETHER it is earned; the arc says
    HOW FAR. Overloading one axis with both is what produced the `tier` and `crest` defects, and one
    colour across twelve also makes the arcs comparable at a glance.

    AND IT IS THE BRAND CYAN, because that is the site's progress colour already: `.pp-phero__ring-fill`
    is `var(--pp-primary)`. An earlier version of this test asserted a NEUTRAL instead, on two grounds --
    that no such convention existed, and that cyan collided with January. The second was true and is
    fixed where it belongs, in the winter hues; the first was simply wrong, reached by grepping one
    stylesheet rather than the set.
    """
    arc = _css_rule(_calendar_css(), '.pp-cal__arc {', last=True)
    assert 'stroke: var(--pp-primary);' in arc, 'the arc is the site progress colour'
    assert '--cal-c' not in arc, 'the arc must not vary by month'

    # AND THE COLLISION IS GONE: no winter hue sits within 30 degrees of the arc.
    block = _calendar_css()
    hues = re.findall(r'--cal-c: oklch\([\d.]+ ([\d.]+) ([\d.]+)\)', block)
    for chroma, hue in hues:
        if abs(float(hue) - 206) < 30:
            assert float(chroma) < 0.07, (
                'hue %s sits near the cyan arc and is saturated enough to be confused with it' % hue)


def test_the_two_rings_split_colour_from_progress():
    """THE INNER RIM CARRIES THE HUE AND THE OUTER ARC DOES NOT, which is what makes two concentric
    rings legible instead of busy: identity on one, measure on the other."""
    block = _calendar_css()
    rim_rule = block[block.index('.pp-cal__rim-track,'):]
    rim_rule = rim_rule[:rim_rule.index('}')]
    assert 'stroke: var(--cal-c);' in rim_rule, 'the rim is the month identity'


def test_the_hue_never_reaches_the_text():
    """THE SAFETY RULE, and the reason twelve hues cannot quietly break a contrast ratio: the face and
    the counter read measured tokens, and the hue drives only the rim, the arc, the plate and the aura.
    This is the recorded lesson about shrinking a colour recipe and voiding a measured ratio."""
    block = _calendar_css()
    face = block[block.index('.pp-cal__face {'):]
    face = face[:face.index('}')]
    assert '--cal-c' not in face, 'the face must not take the month hue'

    sub = block[block.index('.pp-cal__sub {'):]
    sub = sub[:sub.index('}')]
    assert '--cal-c' not in sub, 'the counter must not take the month hue'

    # And the crest's own `color` is a token, not the hue.
    crest = block[block.index('.pp-cal__crest {'):]
    crest = crest[:crest.index('\n}')]
    assert 'color: var(--pp-text-mute);' in crest
    assert 'color: var(--cal-c)' not in crest


def test_each_crest_carries_its_months_completion_as_an_arc():
    """THE OUTER RING, added without removing anything (the owner's condition). Its length is served by
    `widthratio` rather than a new field on the group, because it is a ratio of two figures already
    there -- the same arithmetic `_run_card.html` does for its progress bar."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    for day in range(1, 15):      # half of February
        _fill(run, 2, day)
    body = _body(run)

    pcts = re.findall(r'data-month="(\d+)"\s*\n?\s*style="--pct: (\d+);"', body)
    assert len(pcts) == 12, 'every crest carries a percentage: %r' % pcts
    by_month = {int(m): int(p) for m, p in pcts}
    assert by_month[2] == 50, '14 of 28 days is half of February'
    assert by_month[1] == 0, 'an untouched month is zero'

    # TWO CIRCLES for the arc: a dim track so the ring reads as unfilled rather than absent, and the fill.
    assert body.count('class="pp-cal__arc-track"') == 12
    assert body.count('class="pp-cal__arc"') == 12


def test_an_empty_months_arc_paints_nothing():
    """THE OFFSET MECHANISM IS WHAT MAKES ZERO SAFE, which the earlier shape could not manage.

    The arc was `stroke-dasharray: var(--pct) 100`, where a zero-length dash with a round cap still
    paints a DOT -- an untouched month wearing a pip at twelve o'clock that reads as "one day done". That
    forced butt caps. Ported from `.pp-phero__ring-fill`, the length is a fixed `72 100` dash moved by
    `stroke-dashoffset`, so at 0 the dash sits entirely off the 100-unit path and nothing draws at all.
    Round caps came back with it.
    """
    arc = _css_rule(_calendar_css(), '.pp-cal__arc {', last=True)
    assert 'stroke-dasharray: 72 100;' in arc
    assert 'stroke-dashoffset: calc((72 - var(--pct, 0) * 0.72) * 1px);' in arc
    assert 'stroke-linecap: round;' in arc, 'safe again, because zero shifts the dash off the path'


def test_the_gauge_starts_at_the_bottom_with_a_notch_for_its_plate():
    """THE SHAPE THE OWNER ASKED FOR, ported from the profile hero's level ring rather than invented:
    "can we have the rings start from the bottom... align them to the bottom of the ring in a way that
    sort of overlaps and causes the ring to start a bit offset (we do this for level on the profile
    page)".

    THE ARITHMETIC IS THAT RING'S. `pathLength="100"` makes a unit 3.6 degrees. A 28-unit notch leaves a
    72-unit arc starting at unit 64, so the origin moves 64 x 3.6 = 230.4 degrees off the default
    three-o'clock start: `rotate(-90 + 230.4) = 140.4deg`. Progress then runs lower-left, over the top,
    to lower-right.
    """
    block = _calendar_css()
    shared = _css_rule(block, '.pp-cal__arc-track,')
    assert 'transform: rotate(140.4deg);' in shared, 'the gauge must start from the bottom'

    track = _css_rule(block, '.pp-cal__arc-track {')
    assert 'stroke-dasharray: 72 28;' in track, 'the notch is cut out of the track itself'

    # 72 + 28 = 100: the arc and its gap account for the whole circle, so no unit is drawn twice.
    assert 72 + 28 == 100


def test_the_counter_is_worn_in_the_notch():
    """OUT OF THE CENTRE AND ONTO THE RIM, which is what makes the notch read as deliberate rather than
    as a missing piece of ring -- and hands the coin's middle back to the face. `.pp-phero__lvl`'s shape:
    a pill on the substrate, centred on the bottom edge, overlapping the arc's gap."""
    sub = _css_rule(_calendar_css(), '.pp-cal__sub {\n        display: inline-flex;')
    assert 'bottom: -2px;' in sub, 'it sits on the coin\'s bottom edge, overlapping the ring'
    assert 'left: 50%;' in sub and 'translateX(-50%)' in sub, 'centred in the notch'
    assert 'background: var(--pp-bg-0);' in sub, 'on the substrate, so the arc cannot read through it'
    assert 'top:' not in sub, 'positioned off the coin now, not off the face'


def test_the_month_panel_takes_its_own_months_colour():
    """THE COHESION THE OWNER ASKED FOR: "when swapping tabs I think we should incorporate the colors of
    that month into the content of the tab". The panel carries `data-month`, so it inherits `--cal-c`
    from the same twelve declarations the coins read -- one source, two surfaces.

    AND THE COLOUR STOPS AT THE TEXT. The head is a BAND with a hue edge and wash; the month name stays
    on a token, because the hues run down to 0.66 lightness and November's berry on this card measures
    about 3.9:1 -- under the 4.5 a 16px name needs. A filled day may take the hue because there the text
    is white on a 22% mix into `--pp-bg-2`, which lands near 0.4 lightness whatever the hue.
    """
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    panels = re.findall(r'<section class="pp-cal__panel"[^>]*data-month="(\d+)"', body)
    assert [int(m) for m in panels] == list(range(1, 13))

    block = _calendar_css()
    # `last=True`: `.pp-cal__head` has two rules now -- the `lg:` grid-area placement comes first in
    # the file and the band is the one carrying the colour.
    head = _css_rule(block, '.pp-cal__head {', last=True)
    assert 'border-left: 3px solid var(--cal-c' in head, 'the band carries the hue on its edge'

    day = _css_rule(block, '.pp-cal__day--on {')
    assert 'var(--cal-c' in day, 'a filled square takes its month colour'
    assert 'color: #fff;' in day, 'on a dark mix, so the hue cannot break the text'

    month_name = _css_rule(block, '.pp-cal__month {')
    assert '--cal-c' not in month_name, 'the month name must stay on a measured token'


def test_the_two_rings_do_not_touch():
    """MEASURED, because concentric strokes that overlap read as one thick ring and the two say different
    things -- position and progress. In a 44-unit viewBox the half-width is 22: the rim spans 14.9 to
    17.1 and the arc 18.8 to 21.2, so 1.7 units of air between them and 0.8 inside the edge."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    assert 'class="pp-cal__rim-track" cx="22" cy="22" r="16"' in body
    assert 'class="pp-cal__arc-track" cx="22" cy="22" r="20"' in body

    block = _calendar_css()
    rim_w, arc_w = 2.2, 2.4
    rim_outer = 16 + rim_w / 2
    arc_inner, arc_outer = 20 - arc_w / 2, 20 + arc_w / 2
    assert 'stroke-width: %s' % rim_w in block
    assert 'stroke-width: %s' % arc_w in block
    assert arc_inner > rim_outer, 'the rings overlap'
    assert arc_outer < 22, 'the arc clips the viewBox edge'


# ── the rail on the page ─────────────────────────────────────────────────────────────────────────────

def test_the_rail_replaces_the_progress_bar_in_the_header():
    """IN THE HEADER CARD, NOT A CARD OF ITS OWN (owner: "can we put that rail in the header and replace
    the progress bar there?").

    IT REPLACES RATHER THAN JOINS, which is the part worth pinning. A `.pp-horizon` says how far along a
    run is, which for 25 or 26 squares is the whole story; for 365 days the interesting fact is which
    RUNG is next, and a bar cannot say that. Rendering both would also state the count three times --
    the tally, the bar and the rail.
    """
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))

    assert 'pp-cal-rail' in body
    assert 'pp-horizon' not in body, 'a Calendar run shows the ladder instead of the bar'
    assert body.index('pp-cal-rail') < body.index('class="pp-cal"'), 'header before board'
    # ONE CARD for the board; the rail rides in the page header card above it.
    assert body.count('<section class="scard mb-3"') == 1

    # AND THE SLOT TYPES KEEP THEIR BAR, which is the other half: the swap is per type, not a removal.
    az = _body(_run(CHALLENGE_TYPE_AZ))
    assert 'pp-horizon' in az
    assert 'pp-cal-rail' not in az


def test_the_header_tally_counts_days_on_a_calendar_run():
    """"Done" is a square's word. The figure beside it is the same `completed_count` for every type, so
    only the noun branches."""
    assert '>days</span>' in _body(_run(CHALLENGE_TYPE_CALENDAR))
    assert '>done</span>' in _body(_run(CHALLENGE_TYPE_AZ))


def test_the_rail_states_its_count_once():
    """It carried a headline figure on the shared `.pp-tally` face while it sat in a card of its own.
    In the header the big tally two lines above states that count, so the rail draws the LADDER and the
    number is said once."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    rail = _section(body, 'pp-cal-rail', until='class="pp-cal"')
    assert 'pp-cal-rail__figure' not in rail
    assert 'pp-cal-rail__unit' not in rail
    assert 'pp-cal-rail__figure' not in _rail_css(), 'the rule went with the markup'


def test_the_rail_shows_the_ladder_and_the_next_rung():
    from challenges.services.calendar_render import DAY_MARKERS

    run = _run(CHALLENGE_TYPE_CALENDAR)
    for day in range(1, 29):
        _fill(run, 2, day)          # 28 days
    body = _body(run)

    rail = _section(body, 'pp-cal-rail', until='class="pp-cal"')
    for days in DAY_MARKERS:
        assert '>%d</span>' % days in rail, 'rung %d is missing' % days
    assert '22 to go until 50' in rail, '28 filled leaves 22 to the first rung'
    assert rail.count('pp-cal-rail__pip--on') == 0, 'no rung reached yet'


def test_a_reached_rung_is_marked_without_relying_on_colour():
    run = _run(CHALLENGE_TYPE_CALENDAR)
    for month in (1, 2):
        for day in range(1, CALENDAR_MONTH_DAYS[month - 1] + 1):
            _fill(run, month, day)   # 59 days, past the 50 rung
    rail = _section(_body(run), 'pp-cal-rail', until='class="pp-cal"')

    assert rail.count('pp-cal-rail__pip--on') == 1
    assert rail.count('pp-cal-rail__mark--on') == 1
    assert '50 days: reached' in rail, 'and stated in text, not only in the pip'


def test_the_rail_states_the_comparison_figure_only_when_it_differs():
    """`in_all` is the same days WITHOUT the shovelware exclusion, and it is what makes the headline mean
    something read aloud. Shown only when it actually differs: an aside repeating the number beside it
    is noise."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 3, 3)
    assert 'with shovelware counted' not in _body(run), 'nothing to compare yet'

    CalendarDay.objects.filter(challenge=run, month=4, day=4).update(in_all=True, in_clean=False)
    assert '2 with shovelware counted' in _body(run)


def test_a_finished_run_shows_no_next_rung_line():
    run = _run(CHALLENGE_TYPE_CALENDAR)
    CalendarDay.objects.filter(challenge=run).update(in_all=True, in_clean=True)
    rail = _section(_body(run), 'pp-cal-rail', until='class="pp-cal"')

    assert 'to go until' not in rail, 'there is nowhere left to go'
    from challenges.services.calendar_render import DAY_MARKERS
    assert rail.count('pp-cal-rail__pip--on') == len(DAY_MARKERS)


def test_the_rail_track_is_not_announced_twice():
    """The bar is a PICTURE of numbers that are all stated in text -- the figure above it and the rung
    list below -- so it is `aria-hidden` and a reader gets the ladder as a list instead of a bar they
    cannot read."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    track = re.search(r'<div class="pp-cal-rail__track"[^>]*>', body).group(0)
    assert 'aria-hidden="true"' in track
    assert '<ul class="pp-cal-rail__marks" role="list">' in body


def test_the_rail_uses_the_same_progress_colour_as_the_coin_gauges():
    """ONE CONVENTION PER PAGE. The coins' arcs and this rail measure the same quantity two ways, so a
    rail that disagreed about the colour of progress would be a second convention -- and cyan is the
    site's, per `.pp-phero__ring-fill`."""
    fill = _css_rule(_rail_css(), '.pp-cal-rail__fill {')
    assert '--pp-primary' in fill
    assert '--cal-c' not in fill, 'progress does not vary by month'


# ── the month's side column ──────────────────────────────────────────────────────────────────────────

def test_the_stats_column_lives_inside_its_own_month_panel():
    """INSIDE THE PANEL, NOT A SIBLING COLUMN, which is the decision that makes this cheap: the figures
    are per MONTH and one month is visible, so living in the panel means they switch with it for free.
    A sibling column would need the script toggling twelve more blocks in step with the twelve panels --
    a second ordering to keep aligned, which is the class of thing `aria-controls` was introduced here to
    avoid."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    assert body.count('class="pp-cal__stats"') == 12, 'one per month, inside its panel'

    march = _section(body, 'id="cal-month-mar"', until='</section>')
    assert 'pp-cal__stats' in march
    assert 'March at a glance' in march


def test_an_untouched_month_says_so_rather_than_listing_zeroes():
    """EVERY ROW IS CONDITIONAL, because a stat reading "0" is worse than an absent one -- on a fresh run
    the column would otherwise be a stack of zeroes. Which leaves one case needing its own line: a month
    with nothing at all, where an empty column beside a full grid reads as a render fault.

    NO ROW COUNT IN THIS DOCSTRING. It said "five zeroes" while there were six, four lines from a
    template comment that had just removed the same number for having been wrong twice. A count of
    conditional rows is a fact that changes every time the column gains one, stated in prose nothing
    checks."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    january = _section(body, 'id="cal-month-jan"', until='</section>')

    assert 'No days filled in January yet.' in january
    assert 'Among your months' not in january, 'an unranked month claims no rank'
    assert 'First filled' not in january
    # THE ROW THIS TEST WAS NAMED FOR AND DID NOT CHECK. `open` is `total - done`, so an untouched
    # January is 31 open and drew "STILL OPEN / 31 days" directly above "No days filled yet" -- the
    # column of zeroes in a different costume, twelve panels deep on a fresh run.
    assert 'Still open' not in january, 'nothing is open in a month that has not started'


def test_a_month_held_only_by_shovelware_says_what_it_is_excluding():
    """THE ONE FIGURE THAT STILL RENDERS BESIDE AN EMPTY MONTH, and it is deliberate where "still open"
    was not. A hunter whose only March platinums are on flagged games has `done=0` and a real exclusion
    count, and that row ANSWERS the blank month rather than restating it -- "31 open" tells them the
    length of March, "3 excluded" tells them why March is empty.

    The empty-month test above uses a fresh run where `shovelware` is 0, so nothing covered this."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    for day in (4, 5, 6):
        CalendarDay.objects.filter(challenge=run, month=3, day=day).update(
            in_all=True, in_clean=False, earned_on=dt.date(2018, 3, day))
    march = _section(_body(run), 'id="cal-month-mar"', until='</section>')

    assert 'No days filled in March yet.' in march
    assert 'Excluded' in march and 'pp-tally">3</span>' in march
    assert 'Still open' not in march, 'the open row stays gated on a month with nothing filled'


def test_a_filled_month_lists_its_figures():
    run = _run(CHALLENGE_TYPE_CALENDAR)
    for day in range(1, 6):
        _fill(run, 3, day)
    CalendarDay.objects.filter(challenge=run, month=3, day=9).update(in_all=True, in_clean=False)
    march = _section(_body(run), 'id="cal-month-mar"', until='</section>')

    # `pp-tally">26</span>`, NOT `>26</span>`: the section this reads includes the whole 31-cell grid,
    # whose March 26 cell renders `<span class="pp-cal__num" aria-hidden="true">26</span>`. The loose
    # form matched the day numeral, so breaking `open` outright left the assertion passing.
    assert 'Still open' in march and 'pp-tally">26</span>' in march
    assert 'Among your months' in march and '1st best' in march
    assert 'First filled' in march
    assert 'Excluded' in march and 'shovelware' in march
    assert 'No days filled' not in march


def test_a_month_with_a_stacked_day_names_it():
    """THE OWNER'S ASK (2026-10-04): "could we maybe add a 'busiest day' to the stats?" The month is the
    panel's own title, so the day number alone is unambiguous and "4 on the 12th" fits a 230px column
    where "4 platinums on 12 July" would wrap."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 7, 12, plats=4)
    _fill(run, 7, 20)
    july = _section(_body(run), 'id="cal-month-jul"', until='</section>')

    assert 'Busiest day' in july
    # `pp-tally">4</span>`, NOT `>4</span>`: this section holds the whole 31-cell grid, so the loose
    # form matches the numeral in the 4 July cell. The same trap this file hit on the "still open" row.
    assert 'pp-tally">4</span>' in july
    assert 'on the 12th' in july, "humanize's `ordinal`, not a local suffix chain"


def test_a_month_of_single_platinum_days_names_no_busiest_day():
    """EVERY FILLED SQUARE HOLDS AT LEAST ONE, so the row would render on every month with a single
    filled day and say nothing. Suppressed below two."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    for day in (3, 4, 5):
        _fill(run, 8, day)
    august = _section(_body(run), 'id="cal-month-aug"', until='</section>')

    assert 'Busiest day' not in august
    assert 'First filled' in august, 'the rest of the column still renders'


def test_a_finished_month_shows_no_open_row():
    run = _run(CHALLENGE_TYPE_CALENDAR)
    # `earned_on` TOO, which this left null. The real fill always writes a date beside the booleans, so
    # the month had `first is None` and the test quietly exercised the one row shape `_fill`'s docstring
    # says no backfill can produce.
    CalendarDay.objects.filter(challenge=run, month=2).update(
        in_all=True, in_clean=True, earned_on=dt.date(2019, 2, 14))
    february = _section(_body(run), 'id="cal-month-feb"', until='</section>')

    assert 'Still open' not in february, 'nothing is open'
    assert 'Among your months' in february


def test_one_dated_day_is_not_reported_twice():
    """"Most recent" is suppressed when it is the same square as "First filled", because a month with one
    filled day would otherwise state the same date under two labels."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 6, 6)
    june = _section(_body(run), 'id="cal-month-jun"', until='</section>')

    assert 'First filled' in june
    assert 'Most recent' not in june


def test_the_board_and_its_figures_share_one_width_story():
    """THE OWNER'S FOURTH SHAPE for this page (2026-10-04): "I still think the medallions should cover
    the area of the calendar + the stats on the side." So the band is the lid of the whole panel, and
    the two columns under it divide what the coins span.

    WHAT CHANGED FROM SHAPE 3, which this test used to describe: the crest row read the same two tokens
    as the panel, capping itself to the board column so each coin sat over the day cells. The owner
    wanted the band wider than that, so the tokens now govern the SPLIT below the band and nothing
    about the band itself. Both halves are asserted here because a panel whose columns stop reading one
    pair of tokens is how the head, the grid and the figures start disagreeing about where they end.
    """
    flat = ' '.join(_calendar_css().split())
    assert '--cal-stats: 280px' in flat and '--cal-gutter: 24px' in flat
    assert 'grid-template-columns: 1fr var(--cal-stats);' in flat
    # `head stats`, NOT `head head`. The head spanned both columns at first, which put its tinted band
    # 254px past the day grid -- three right edges in a panel built around shared ones. The side column
    # spans the rows instead, so the head stops where the days stop.
    assert 'grid-template-areas: "head stats" "grid stats";' in flat
    assert '"head head"' not in flat, 'a spanning head overhangs the board column it titles'
    # THE GUTTER IS READ ONCE, by the panel. Shape 3 had the crest row reading it too; a second reader
    # is what let the band and the columns drift apart as the shapes changed.
    assert flat.count('var(--cal-gutter)') == 1, (
        'the token is declared once and consumed once -- by the panel\'s column gap')


def test_the_stats_column_sits_on_a_surface():
    """CONTENT DOES NOT SIT BARE, which is the rule that put the whole board on a `.scard` and the month
    head on a band. Recessed rather than raised -- `--pp-bg-1` on a `--pp-bg-2` card -- so it reads as a
    panel let into the card rather than a second card stacked on it."""
    # THE RULE THAT CARRIES A BACKGROUND, found rather than named, because THREE rules share this
    # selector -- the `lg:` grid-area placement, the surface, and the `lg:` margin reset -- so neither
    # the first nor the last is the one meant and `_css_rule`'s `last` flag cannot express "the middle
    # one". Naming it by its first declaration worked and pinned the test to declaration ORDER: moving
    # `padding` above `margin-top` broke it without changing a single computed value.
    # VIA `_css_rules`, which was introduced in this same slice for precisely this problem and then
    # not used here -- two hand-rolled copies of one regex, which is how they drift.
    rules = [r for r in _css_rules(_calendar_css(), '.pp-cal__stats') if 'background' in r]
    assert len(rules) == 1, 'exactly one `.pp-cal__stats` rule paints a surface'
    stats = rules[0]
    assert 'background: var(--pp-bg-1);' in stats
    assert 'border: 1px solid var(--pp-border);' in stats
