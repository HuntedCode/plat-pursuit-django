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
import re

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from challenges.models import (CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN, CALENDAR_VIEW_CONTRACTS,
                               CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_CALENDAR, CalendarDay)
from challenges.services import calendar_render
from tests.factories import ProfileFactory, UserFactory

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


def _fill(run, month, day, *, views=(CALENDAR_VIEW_ALL,)):
    written = CalendarDay.objects.filter(challenge=run, month=month, day=day).update(
        in_all=CALENDAR_VIEW_ALL in views,
        in_clean=CALENDAR_VIEW_CLEAN in views,
        in_contracts=CALENDAR_VIEW_CONTRACTS in views)
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


def test_the_year_totals_are_the_sum_of_the_month_counts():
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 1, 1, views=(CALENDAR_VIEW_ALL,))
    _fill(run, 6, 6, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))
    _fill(run, 9, 9, views=(CALENDAR_VIEW_CONTRACTS,))

    context = Client().get(_url(run)).context
    assert context['calendar_totals'] == {CALENDAR_VIEW_ALL: 2, CALENDAR_VIEW_CLEAN: 1,
                                          CALENDAR_VIEW_CONTRACTS: 1}
    # Summed from the groups already on the page rather than asked of the database again.
    assert context['calendar_totals'] == calendar_render.totals_for(context['calendar_months'])


def test_the_board_opens_on_the_lens_the_run_leads_with():
    """ALL PLATINUMS IS NEVER IT, even when it is far ahead: it is the lens shovelware inflates and the
    one that cannot finish a run, so opening on it would show a board near completion whose run
    completes on a much lower number."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    for day in range(1, 11):
        _fill(run, 1, day, views=(CALENDAR_VIEW_ALL,))
    for day in range(1, 4):
        _fill(run, 2, day, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))

    context = Client().get(_url(run)).context
    assert context['calendar_view'] == CALENDAR_VIEW_CLEAN


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
    # COUNTED ON THE DAY'S OPENING TAG. The `<li>` wrapper used to carry a class and this counted that
    # instead; the class went when it turned out to have no rule behind it. `class="pp-cal__day"` is an
    # exact attribute match, so it cannot silently match nothing the way a class-plus-attributes pattern
    # can -- the day's own per-lens attributes come after it.
    assert body.count('class="pp-cal__day"') == 365
    assert body.count('pp-cal__crest"') + body.count('pp-cal__crest ') == 12


def test_only_the_first_month_is_showing_before_any_script_runs():
    """`hidden` ON THE INACTIVE ELEVEN rather than a stylesheet rule, so the rendered HTML is a correct
    board on its own and a reader never meets twelve months of squares at once -- even with CSS off, and
    before `wireTablist` has wired anything."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    panels = re.findall(r'<section class="pp-cal__panel"[^>]*>', body)

    assert len(panels) == 12
    assert 'hidden' not in panels[0]
    assert all('hidden' in panel for panel in panels[1:])


def test_the_board_opens_on_the_servers_lens_with_no_state_attributes():
    """THE LENS IS THE CHECKED RADIO, and nothing else. The board used to carry `data-view`/`data-month`
    for the stylesheet to read, which meant the lens only worked with JavaScript; the tint rules read the
    checked radio through `:has()` now, so those attributes had no reader left and were deleted. Pinned
    because a future change would otherwise "fix" the lens by writing to a dead attribute."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 3, 3, views=(CALENDAR_VIEW_CONTRACTS,))
    body = _body(run)

    board = re.search(r'<div class="pp-cal"[^>]*>', body).group(0)
    assert board == '<div class="pp-cal">', board

    lens = _section(body, 'class="pp-cal__lens"', until='pp-cal__lenshint')
    checked = re.findall(r'<input[^>]*checked[^>]*>', lens)
    assert len(checked) == 1
    assert 'value="%s"' % CALENDAR_VIEW_CONTRACTS in checked[0]


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
    (`0` on the first crest, `-1` on the rest), which with JavaScript off meant Tab skipped eleven crests
    and no arrow key moved between them -- a keyboard-only visitor losing eleven twelfths of the board
    with no way to reach it. `wireTablist.syncTabindex()` runs at wire time, so the rover arrives in the
    same tick as the arrows that make it navigable."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    crests = re.findall(r'<button type="button" role="tab"[^>]*>', body)

    assert len(crests) == 12
    assert all('tabindex="0"' in c for c in crests)
    assert sum(1 for c in crests if 'aria-selected="true"' in c) == 1


def test_every_day_names_itself_for_a_reader():
    """A cell shows a bare numeral, so the spelled-out date and the per-lens state are all the accessible
    tree has. Nothing pinned the 365 labels the template argues about at length."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 3, 3, views=(CALENDAR_VIEW_CONTRACTS,))
    body = _body(run)

    # COUNTED ON THE CELL, not on `sr-only` inside an unbounded slice: the day cell carries exactly one
    # label, and the slice to end-of-document also caught a chrome element outside the board.
    assert body.count('class="pp-cal__day"') == 365
    march = _section(body, 'id="cal-month-mar"', until='</section>')
    assert 'March 3.' in march
    assert 'Contracts: filled.' in march


def test_a_filled_day_carries_only_the_lenses_that_hold_it():
    """The per-lens attributes ARE the tint, which is what lets a contracts-only day read as empty while
    the All platinums lens is showing. A single best-of value could not say that."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    _fill(run, 1, 1, views=(CALENDAR_VIEW_CONTRACTS,))
    _fill(run, 1, 2, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))
    body = _body(run)

    days = re.findall(r'<div class="pp-cal__day"([^>]*)>', body)
    assert 'data-contracts' in days[0] and 'data-all' not in days[0]
    assert 'data-all' in days[1] and 'data-clean' in days[1]
    assert 'data-contracts' not in days[1]
    assert 'data-' not in days[2], 'an unfilled day carries no lens attribute'


def test_a_completed_month_is_struck_and_an_incomplete_one_is_not():
    run = _run(CHALLENGE_TYPE_CALENDAR)
    CalendarDay.objects.filter(challenge=run, month=2).update(in_all=True)
    _fill(run, 3, 3, views=(CALENDAR_VIEW_ALL,))
    body = _body(run)

    crests = re.findall(r'<button type="button" role="tab"[^>]*>', body)
    assert 'pp-cal__crest--struck' in crests[1] and 'data-tier="bronze"' in crests[1]
    assert 'pp-cal__crest--struck' not in crests[2], 'one filled day is not a month'
    assert 'data-tier' not in crests[2]


def test_the_lens_switcher_is_the_shared_component_and_offers_every_lens():
    """THE HOUSE PRIMITIVE, not a third treatment of a segmented control. `.pp-switch` is the project's
    tab-group standard and this feature's own browse toolbar already uses it.

    SCOPED TO THE SWITCHER. This used to slice from `pp-cal__lens` to the END OF THE DOCUMENT, so
    `count('checked') == 1` was counting the word anywhere in the rest of the page -- the footer, the
    scripts, any checkbox added later. It passed only by luck about what follows the board.
    """
    lens = _section(_body(_run(CHALLENGE_TYPE_CALENDAR)), 'class="pp-cal__lens"',
                    until='pp-cal__lenshint')

    assert 'class="pp-switch"' in lens, 'the shared container, not just a chip class'
    assert 'pp-switch__chip' in lens
    for value in (CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN, CALENDAR_VIEW_CONTRACTS):
        assert 'value="%s"' % value in lens
    assert lens.count('name="cal-view"') == 3
    assert lens.count('checked') == 1
    # RADIOS, NOT CHECKBOXES, which is structural rather than pedantic: checkboxes let several lenses be
    # checked at once, and then several `:has()` branches match together -- all three tallies visible and
    # every day tinted for the UNION of the lenses. The counts above hold either way, so this is what
    # actually pins a single-choice control.
    assert lens.count('type="radio"') == 3
    assert 'type="checkbox"' not in lens


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
    buys the two companions a hand-rolled copy silently skipped."""
    js = _script()
    assert 'PP.wireTablist(tabs, {' in js
    assert 'ignite: true' in js, 'the activated crest blooms, as every other switcher does'
    assert 'PP.slideViewIn(' in js, 'the month panel slides in directionally'
    # The hand-rolled keyboard handling must not come back alongside the helper.
    assert 'ArrowRight' not in js and 'keydown' not in js


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
    assert body.count('pathLength="100"') == 24, 'both circles of all twelve crests'


def test_every_crest_names_its_month_without_relying_on_colour():
    """The face is a three-letter abbreviation and the state is carried in metal, so the full month name
    and the per-lens figures have to be in the accessible tree. Colour is never the only cue."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    crests = _section(body, 'pp-cal__crests', until='class="pp-cal__lens"')
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


def test_the_lens_is_switched_by_css_and_not_by_script():
    """THE DEFECT THIS PINS. The tints used to read a `data-view` attribute only JavaScript ever wrote,
    while the chip's active state was pure CSS -- so with JS off, pressing a lens lit its chip and left
    every square showing the previous one. False feedback, which is worse than a dead control."""
    block = _calendar_css()

    assert '.pp-cal:has(input[value="contracts"]:checked)' in block
    assert '.pp-cal[data-view' not in block

    code = _script_code()
    assert 'dataset.view' not in code
    assert 'data-view' not in code


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
    _fill(run, 3, 3, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))
    _fill(run, 5, 5, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))
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


def test_the_hero_tints_only_the_lens_it_leads_with():
    """All-platinums days must not light up a board drawn in the shovelware-free lens."""
    run = _run(CHALLENGE_TYPE_CALENDAR)
    for day in range(1, 8):
        _fill(run, 1, day, views=(CALENDAR_VIEW_ALL,))
    for day in range(1, 3):
        _fill(run, 2, day, views=(CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN))

    assert _hero_html(run).count('pp-chero__cday--on') == 2


def test_the_calendar_hero_takes_the_whole_row_through_the_tablet_band():
    """53 columns sharing the row with the plaque at 768 left a 3.9px cell, no better than mobile. Only
    `--jobs` was named in the rule that fixes this, so the widest board in the feature got the narrowest
    column."""
    block = _calendar_css()
    assert '.pp-chero--calendar { grid-template-columns: minmax(0, 1fr); }' in block


# ── the stylesheet, pinned by source text (there is no CSS test runner either) ───────────────────────

def _calendar_css():
    """The Calendar's own block of `challenges.css`, BOUNDED.

    `css[css.index('PLAT CALENDAR BOARD'):]` runs to the end of the file, which works only while this
    block happens to be last -- the next appended component would silently put every assertion below
    into somebody else's rules. That is the same unbounded-slice mistake `_section` exists to avoid, so
    it gets the same treatment: cut at the next top-level block banner.
    """
    css = open('static/css/components/challenges.css', encoding='utf-8').read()
    block = css[css.index('PLAT CALENDAR BOARD'):]
    nxt = block.find('* ============================================================ */',
                     block.index('============================================================ */') + 10)
    return block if nxt == -1 else block[:nxt]


def test_a_struck_month_keeps_its_metal_when_selected_or_hovered():
    """THE FIX WITH NO TEST, which a mutation pass found could be reverted in full while the suite stayed
    green -- on a bug this stylesheet records having already shipped once on `.pp-csq` ("hovering a
    finished square erased the one mark the page exists to show"). `[data-tier]`, `[aria-selected]` and
    `:hover` all carry the same specificity, so without the `:not()` scoping source order hands the
    primary colour to a gold January the moment the page loads."""
    block = _calendar_css()
    assert '.pp-cal__crest[aria-selected="true"]:not([data-tier]) { --crest-c: var(--pp-primary); }' in block
    assert '.pp-cal__crest:not([data-tier]):hover { --crest-c: var(--pp-primary); }' in block
    assert '.pp-cal__crest[data-tier]:hover { --crest-c: var(--crest-glow); }' in block


def test_the_rim_draws_twelve_segments_rather_than_a_solid_ring():
    """ROUND CAPS WERE THE DEFECT, and it is invisible in a screenshot of one crest: a cap extends each
    dash by `stroke-width / 2` at BOTH ends, so a 2.5 stroke grew each dash past its own gap and the
    twelve segments painted as one ring. The lit segment still read against the dim track, which is why
    only arithmetic caught it."""
    block = _calendar_css()
    assert 'stroke-linecap: butt' in block
    assert 'stroke-linecap: round' not in block
    # Normalised units: twelve slots of 100/12 = 8.3333, a 6-long dash leaving a 2.3333 gap.
    assert 'stroke-dasharray: 6 2.3333' in block
    assert 'stroke-dasharray: 6 94' in block


def test_the_day_cell_is_capped_so_it_does_not_balloon_on_desktop():
    """Seven columns at every width made the cell 135px at 1024 and 208px at 1920 -- a panel of enormous
    near-empty boxes each holding one 13px numeral, on the primary target."""
    block = _calendar_css()
    assert 'repeat(10, minmax(0, 1fr))' in block
    # THE CAP RIDES THE BOARD, not the grid: on the grid alone it left-aligned while the month head and
    # the crest row stayed at full panel width, so the board read as three widths at three alignments.
    assert 'max-width: 712px' in block
    assert 'margin-inline: auto' in block
    assert block.index('max-width: 712px') < block.index('.pp-cal__crests')


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
    # COMMENTS STRIPPED FIRST. The block explains at length why `min-height` had to go, so a raw
    # substring search finds the prose documenting the fix and reads it as the bug -- the same reason
    # the stylesheet readers elsewhere in this project strip comments inside the reader.
    live = re.sub(r'/\*.*?\*/', '', block, flags=re.S)
    assert 'min-height' not in live


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


def test_the_month_tally_reveals_a_figure_for_the_active_lens():
    """The per-lens tally had no behavioural coverage, so renaming the attribute it is keyed on left
    `.pp-cal__count { display: none }` with no matching reveal rule -- an empty tally on all twelve
    months, suite green."""
    body = _body(_run(CHALLENGE_TYPE_CALENDAR))
    assert body.count('class="pp-cal__count" data-view=') == 36, 'three lenses x twelve months'

    block = _calendar_css()
    for value in (CALENDAR_VIEW_ALL, CALENDAR_VIEW_CLEAN, CALENDAR_VIEW_CONTRACTS):
        assert '.pp-cal__count[data-view="%s"]' % value in block
