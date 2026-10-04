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

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_CALENDAR, CalendarDay
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


def _fill(run, month, day):
    """Fill one day. BOTH columns, because `in_clean` implies `in_all` and the database enforces it; a
    test wanting a shovelware day -- in `all` and off the board -- writes the row itself."""
    written = CalendarDay.objects.filter(challenge=run, month=month, day=day).update(
        in_all=True, in_clean=True)
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
    days = re.findall(r'<div class="(pp-cal__day[^"]*)"', _body(run))

    assert len(days) == 365
    assert days[0] == 'pp-cal__day', 'January 1 is unfilled'
    assert days[1] == 'pp-cal__day pp-cal__day--on'
    assert 'data-all' not in _body(run) and 'data-clean' not in _body(run)


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
    # The active cue is shape plus the brand colour, never the month's.
    assert '.pp-cal__crest[aria-selected="true"] { transform: translateY(-1px); }' in block
    assert 'var(--pp-primary) 65%' in block


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
    # THE CAP IS ON THE PANEL, not the grid inside it. On the grid alone it left-aligned while the month
    # head stayed at full width, so the head and the squares under it disagreed; the head and the grid
    # are both inside the panel, so capping there is what makes them share an edge.
    assert '.pp-cal__panel { max-width: var(--cal-body)' in ' '.join(block.split())
    assert 'margin-inline: auto' in block


def test_the_crest_band_spans_while_the_body_it_controls_stays_capped():
    """THE OWNER'S CALL ON THE BROWSER PASS, pinned because it reads like the misalignment it replaced.

    The first version capped the whole board at one width, which fixed a real defect (a grid capped on
    its own, left-aligned under a full-width head) and made the twelve medallions small. The owner asked
    for the opposite emphasis: "those medallions are pretty cool so I'd like to show them off a bit
    more... we could even make them larger to allow them to span across the whole screen. We can keep
    the tabs and dates smaller."

    So the band is full-bleed and the switcher and the month panel share ONE capped width. The part that
    keeps the old defect fixed is that the head and the grid live inside the panel together, so they
    cannot disagree -- and the lens chips sit on the same edge as the grid below them.
    """
    block = _calendar_css()

    # ONE TOKEN for the body width, so the two capped blocks cannot drift apart.
    assert '--cal-body: 712px' in block
    assert block.count('max-width: var(--cal-body)') == 1, (
        'both capped blocks must read the same token in one rule, or they will diverge')

    # The band is NOT capped to the body: it grows its coins into the full row instead.
    flat = ' '.join(block.split())
    assert '.pp-cal__crest { flex: 1 1 0;' in flat
    assert 'max-width: 112px' in flat, 'but not unbounded: twelve coins past this stop reading as a row'
    assert 'aspect-ratio: 1' in flat


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
    assert '@media (min-width: 1024px) { .pp-cal__sub { display: block;' in flat


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
        rule = '.pp-cal__crest[data-month="%d"]' % month
        assert rule in block, 'no hue declared for month %d' % month

    hues = re.findall(r'data-month="\d+"\]\s*\{\s*--cal-c: oklch\(([\d.]+) ([\d.]+) ([\d.]+)\)', block)
    assert len(hues) == 12, 'every month hue must be an oklch triple: %r' % hues
    assert len({h for _l, _c, h in hues}) == 12, 'two months share a hue angle'
    # The cycle closes: December sits nearer January than it does to the autumn run it follows.
    jan, dec, nov = float(hues[0][2]), float(hues[11][2]), float(hues[10][2])
    assert abs(dec - jan) < abs(dec - nov), 'December should turn back toward January, not trail November'


def test_the_progress_arc_is_one_colour_for_every_month():
    """ONE COLOUR AXIS, ONE MEANING -- the owner's point (2026-10-04): "would it make sense for the rings
    around each month to all be the same color so you can tell it specifically means progress". The month
    hue already says which month and whether it is earned; making it also say how far is the overloading
    that produced the `tier` and `crest` defects. One colour also makes the twelve arcs comparable.

    NEUTRAL RATHER THAN THE BRAND CYAN, for a structural reason rather than taste: `--pp-primary` is hue
    206 and January's ice is hue 210, four degrees apart, so a cyan arc would vanish into the rim beneath
    it on the coin that opens the row. A seasonal cycle needs the blue region for winter and the brand
    colour lives there, so that is not tunable. Cyan is also the ACTIVE cue on this same element.
    """
    block = _calendar_css()
    arc_rule = block[block.index('.pp-cal__arc-track,'):]
    arc_rule = arc_rule[:arc_rule.index('}')]

    assert 'stroke: var(--pp-text);' in arc_rule, 'the arc takes a neutral, not a hue'
    assert '--cal-c' not in arc_rule, 'the arc must not vary by month'
    assert '--pp-primary' not in arc_rule, 'and must not reuse the active cue'


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
    """A ZERO-LENGTH DASH WITH A ROUND CAP STILL PAINTS A DOT, which on an untouched month would read as
    "one day done" at twelve o'clock. Butt caps make the zero case render nothing on its own, which is
    why the arc does not take the rounder ends it would otherwise want."""
    block = _calendar_css()
    # ASSERTED AS AN ABSENCE across the whole block rather than inside one rule. Both rings are butt --
    # the rim because round caps ate its gaps and painted twelve segments as a solid ring, the arc
    # because a round cap on a zero-length dash paints a dot. One rule covers both, and slicing to find
    # it was how this assertion first landed on a single line and passed against nothing.
    assert 'stroke-linecap: round' not in block, 'a round cap would pip an empty month'
    assert 'stroke-linecap: butt' in block
    assert 'stroke-dasharray: var(--pct, 0) 100' in block


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
