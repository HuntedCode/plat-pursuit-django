"""Behaviours in `monthly-recap.js` that trap the hunter, leak, or hand PSN text to innerHTML.

There is no JS test harness in this project, so these read the source. That is a weak instrument and the
assertions are written accordingly: each one targets the specific shape whose absence caused a real
failure, not the general correctness of the function around it. The tone-setting cases:

- **The quiz lock.** `hasAnswered` was ONE boolean shared by every quiz in the deck, and `initQuizSlide`
  (which reset it) only runs on a slide's first visit. Answer quiz A -> advance to quiz B (reset) -> go
  back to A, and A's own answer no longer counted: `canNavigate()` returned false and the hunter was
  stuck on a slide they had already answered, with the shake firing on every attempt to leave.
- **The listener leak.** The platinum modal registered a document-level Escape handler and removed it
  only when Escape fired. Closing by backdrop or by the X button left it bound, accumulating one dead
  handler per open, each holding a detached DOM node.
- **The innerHTML hole.** That modal interpolated `plat.game_name` / `plat.trophy_name` -- PSN-sourced
  text -- straight into an innerHTML template.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / 'static' / 'js' / 'monthly-recap.js'


@pytest.fixture(scope='module')
def js():
    return CONTROLLER.read_text(encoding='utf-8')


def _code(js):
    """Source with `//` and `/* */` comments stripped. The comments in this file explain the bugs these
    tests guard against, and they quote the broken code verbatim -- scanning raw source finds those
    quotations and reports them as live calls."""
    js = re.sub(r'/\*.*?\*/', '', js, flags=re.S)
    return '\n'.join(ln for ln in js.splitlines() if not ln.lstrip().startswith('//'))


def _method(js, name):
    """Body of a class method, anchored to its DEFINITION -- an unanchored search finds the call site
    first and captures the wrong function."""
    # `async ` is optional: the deck loader is async, and without this the anchor silently misses it.
    m = re.search(r'\n    (?:async )?' + re.escape(name) + r'\(.*?\n    \}\n', js, re.S)
    assert m, f'{name} not found'
    return m.group(0)


# --- Quiz state is per slide ----------------------------------------------------------------------

def test_there_is_no_shared_answered_flag(js):
    assert 'this.hasAnswered' not in _code(js), (
        'a deck-wide answered flag is back; revisiting an answered quiz will lock navigation'
    )


def test_answered_state_is_keyed_by_slide(js):
    body = _method(js, 'isAnswered')
    assert 'slideType' in body and 'quizResults' in body, (
        'isAnswered must resolve per slide, from the recorded results'
    )


def test_navigation_gate_is_asked_about_a_specific_slide(js):
    code = _code(js)
    assert re.search(r'canNavigate\(slideType\)', code), 'canNavigate must take the slide it is judging'
    # And every call site must pass one -- an argument-less call would silently gate on `undefined`.
    calls = re.findall(r'canNavigate\(([^)]*)\)', code)
    bare = [c for c in calls if not c.strip()]
    assert not bare, f'{len(bare)} canNavigate() call(s) pass no slide type'


def test_the_answer_is_recorded_before_feedback_renders(js):
    """The recorded result IS the answered flag, so it has to exist before anything can re-enter and
    re-answer. Both quiz paths."""
    body = _method(js, 'handleOptionClick')
    assert 'this.recordResult(' in body and 'showSingleSelectFeedback' in body
    assert body.index('this.recordResult(') < body.index('showSingleSelectFeedback'), (
        'feedback renders before the result is recorded, so the answered flag lags the UI'
    )


def test_the_quiz_path_guards_on_the_per_slide_state(js):
    assert 'this.isAnswered(slideType)' in _method(js, 'handleOptionClick'), 're-answering is not guarded'


def test_the_unreachable_multiselect_branch_is_gone(js):
    """It served only `get_quiz_platinum_options`, which had no caller, and no template ever emitted
    `[data-quiz-submit]`. Dead code that still had to be maintained through every quiz change."""
    code = _code(js)
    assert 'handleMultiSelectSubmit' not in code
    assert 'showMultiSelectFeedback' not in code
    assert 'data-quiz-submit' not in code


def test_the_score_beat_is_not_gated_like_a_quiz(js):
    """`quiz_score` REPORTS on the quizzes; it asks nothing. Treating it as one made canNavigate()
    permanently false there -- the auto-advance refused to start AND both nav paths refused to move, so
    the deck dead-ended one slide short of the summary every single time.

    Same prefix trap as the deck builder, where `quiz_score` counted itself when deciding whether there
    were any quizzes worth scoring. Two bugs, one cause, which is why it is pinned on both sides."""
    body = _method(js, 'isQuizSlide')
    assert "'quiz_score'" in body, (
        'isQuizSlide no longer excludes the score beat; it will gate a slide that can never be answered'
    )


def test_interactive_beats_advance_on_a_control_not_a_clock(js):
    """The calendar's platinum days open a detail dialog. A timer running behind an open modal either
    yanks the slide out from under the hunter or needs pause/resume nobody can predict."""
    code = _code(js)
    assert 'MANUAL_BEATS' in code and "'activity_calendar'" in code
    # The decision moved into syncBeatState, which runs before the bar is painted -- see
    # test_a_beats_pause_state_is_decided_before_its_bar_is_painted.
    body = _method(js, 'syncBeatState')
    assert 'MANUAL_BEATS.has' in body, 'manual beats are not exempted from the auto-advance'
    assert "dialog[open]" in body, 'an open dialog does not stop the clock'
    assert 'syncBeatState' in _method(js, 'startBeatTimer'), 'the timer never consults the state'


def test_the_platinum_dialog_lives_inside_the_stage(js):
    """Appended to <body> it is invisible to the takeover, which then answers Escape itself and tears the
    whole ceremony down on the keypress meant to dismiss the dialog."""
    body = _method(js, 'showPlatinumDetails')
    assert 'this.container' in body and 'appendChild(dialog)' in body
    assert 'document.body.appendChild(dialog)' not in body


def test_a_takeover_yields_escape_to_a_dialog_inside_it():
    """The takeover binds keydown in the CAPTURE phase, so without this it wins Escape before the dialog
    ever sees it."""
    utils = (ROOT / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')
    fn = re.search(r'function takeover\(root, opts\) \{.*?\n\}', utils, re.S)
    assert fn, 'takeover() not found'
    assert "root.querySelector('dialog[open]')" in fn.group(0), (
        'takeover does not defer to a dialog opened inside it'
    )


# --- Document-level keyboard handler ---------------------------------------------------------------

def test_arrow_keys_yield_to_typing_dialogs_and_modifiers(js):
    """The handler is bound at the document, so it has to yield. The share section below the deck has a
    background <select>, and the platinum dialog owns the keyboard while it is open."""
    handler = re.search(r"document\.addEventListener\('keydown', \(e\) => \{.*?\n        \}\);", js, re.S)
    assert handler, 'keydown handler not found'
    body = handler.group(0)
    assert 'INPUT' in body and 'TEXTAREA' in body and 'SELECT' in body, 'does not yield to form fields'
    assert 'isContentEditable' in body, 'does not yield to contenteditable'
    assert 'dialog[open]' in body, 'does not yield to an open dialog'
    assert 'metaKey' in body and 'ctrlKey' in body, 'does not yield modified presses to the browser'


# --- Overflow tracks the viewport ------------------------------------------------------------------

def test_overflow_is_rechecked_when_the_viewport_changes(js):
    """The deck height is `clamp(520px, 100vh - 360px, 720px)`, so which slides overflow moves with the
    viewport. Checking once after render left rotated phones centred and clipped."""
    body = _method(js, 'watchSlideOverflow')
    assert "window.addEventListener('resize'" in body
    assert 'visualViewport' in body, (
        'mobile browser chrome collapsing changes 100vh without firing window resize'
    )
    assert 'debounce' in body, 'an undebounced resize handler runs a layout read per frame'
    assert 'this.watchSlideOverflow()' in js, 'the watcher is never started'


# --- Platinum dialog -------------------------------------------------------------------------------

def test_platinum_details_use_a_native_dialog(js):
    body = _method(js, 'showPlatinumDetails')
    assert 'showModal()' in body, 'a hand-rolled overlay re-implements focus trapping and Escape'
    assert "createElement('dialog')" in body


def test_platinum_dialog_does_not_leak_a_document_listener(js):
    body = _method(js, 'showPlatinumDetails')
    assert "document.addEventListener('keydown'" not in body, (
        'the dialog registers its own document-level key handler again -- <dialog> already owns Escape, '
        'and the previous version only unbound on the Escape path'
    )
    assert "addEventListener('close'" in body, 'the dialog must remove itself when closed, however it closed'


def test_psn_text_is_escaped_before_it_reaches_innerhtml(js):
    """Game and trophy names come from PSN. They were interpolated raw."""
    body = _method(js, 'showPlatinumDetails')
    assert 'HTMLUtils.escape' in body, 'no escaping helper in the dialog builder'
    for field in ('game_name', 'trophy_name', 'icon_url'):
        for hit in re.findall(r'\$\{([^}]*' + field + r'[^}]*)\}', body):
            assert 'esc(' in hit, f'{field} reaches innerHTML unescaped: ${{{hit}}}'


def test_no_inline_event_handlers(js):
    """The old close button carried `onclick="document.getElementById(...).remove()"`, which both couples
    to a global id and would be blocked under a script-src CSP."""
    assert 'onclick=' not in _code(js), 'inline onclick attribute in generated markup'


def test_malformed_platinum_payload_cannot_break_the_slide(js):
    body = _method(js, 'showPlatinumDetails')
    assert 'JSON.parse' in body and 'catch' in body
    assert 'Array.isArray' in body, 'a non-array payload would throw on .map'


# --- Request budget ---------------------------------------------------------------------------------

def test_the_deck_is_fetched_in_one_request(js):
    """It used to fan out one request PER BEAT in parallel. At 20 beats that is 20 requests for a single
    month, against a 60/min per-user throttle that covers the WHOLE API -- so flicking between months
    exhausted the bucket and 429'd everything else the page did, including the notification poll.

    Aborting on navigation is not a substitute: a month view that COMPLETED still cost 20."""
    body = _method(js, 'prefetchAllSlides')
    assert '/deck/' in body, 'the deck is not fetched from the batched endpoint'
    assert 'Promise.all' not in body, 'the per-slide fan-out is back'
    assert 'this.slides.map' not in body, 'still issuing one request per beat'


def test_the_batched_endpoint_exists_and_is_routed():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    assert 'class RecapDeckView' in (root / 'api' / 'recap_views.py').read_text(encoding='utf-8')
    urls = (root / 'api' / 'urls.py').read_text(encoding='utf-8')
    assert "recap/<int:year>/<int:month>/deck/" in urls, 'the deck endpoint is not routed'


def test_an_in_flight_load_can_be_dropped(js):
    assert 'AbortController' in _code(js)
    assert 'abortLoad' in _method(js, 'abortLoad')


def test_advancing_is_handled_at_the_stage_not_by_the_tap_zones(js):
    """`.recap-slide` is absolutely positioned across the whole stage at z-index 1 and the zones sit
    beneath it at z-index 0, so a REAL click never reached a zone -- the slide always caught it. Raising
    the zones instead would break the opposite case (quiz options, calendar days), so advancement is
    handled at the stage with a guard: anything interactive owns its own click.

    This survived earlier testing because the harness dispatched `.click()` at the zone element directly,
    routing around the exact bug it should have caught. It now clicks real coordinates."""
    handler = re.search(r"this\.container\.addEventListener\('click'.*?\n        \}\);", js, re.S)
    assert handler, 'no stage-level click handler'
    body = handler.group(0)
    assert 'closest(' in body, 'no guard -- clicking a quiz option would also advance the deck'
    assert 'data-quiz-option' in body and 'button' in body
    assert 'clientX' in body, 'advancement does not depend on WHERE the click landed'


def test_holding_pins_the_bar_rather_than_ending_it(js):
    """`transition: none` does not freeze an in-flight transition -- it drops the animation and applies
    the target value, so the bar snapped to FULL on every pointerdown, which is every click."""
    body = _method(js, 'holdBeat')
    assert 'getComputedStyle' in body and 'style.width' in body, (
        'the current width is not pinned before the transition is removed'
    )
    release = _method(js, 'releaseBeat')
    assert '_beatLeft' in body and 'startBeatTimer' in release


def test_releasing_resumes_the_remainder_rather_than_restarting(js):
    """Holding to finish reading a beat should not hand you the whole beat again."""
    body = _method(js, 'startBeatTimer')
    assert 'this._beatLeft' in body, 'the remaining time is ignored on resume'


def test_a_waiting_beat_does_not_show_a_completed_timer():
    """`is-live` declares `width: 100%` and relies on a transition to get there. Removing the transition
    for a waiting beat -- an unanswered quiz, or a manual beat with its own Continue -- therefore applied
    the target INSTANTLY: every quiz opened showing a full timer bar, claiming a beat had elapsed when
    nothing had. That is the same inline-vs-class precedence trap that once left the bar stuck at zero,
    arriving from the other direction.

    The segment must be emptied explicitly, not merely un-transitioned."""
    css = (ROOT / 'static' / 'css' / 'components' / 'recap-stage.css').read_text(encoding='utf-8')
    rule = re.search(r'\.rcx\.is-waiting \.rcx__bar\.is-live i,\s*'
                     r'\.rcx\.is-manual \.rcx__bar\.is-live i \{([^}]*)\}', css)
    assert rule, 'waiting/manual bars have no rule of their own'
    body = rule.group(1)
    assert 'width: 0' in body, 'a waiting bar still inherits is-live width:100% and snaps to full'
    assert 'transition: none' in body


def test_completed_bars_are_committed_inline_not_left_to_the_cascade(js):
    """A skipped bar was reported as still animating rather than snapping full. Class-swapping relies on
    how a browser cancels an in-flight transition when the rule carrying it stops matching, which varies
    and which I could not reproduce from a harness. An inline width outranks every rule and cannot be
    interpolated toward, so the behaviour stops depending on that."""
    body = _method(js, 'paintBars')
    assert "fill.style.width = '100%'" in body, 'completed bars are not committed to full explicitly'
    assert "fill.style.width = '0'" in body, 'bars ahead of the playhead are not explicitly emptied'


def test_the_regions_have_one_definition(js):
    """The click handler and the hover affordance must not disagree about where the regions are, or the
    deck shows one thing and does another."""
    assert 'zoneAt' in _method(js, 'zoneAt')
    handler = re.search(r"this\.container\.addEventListener\('click'.*?\n        \}\);", js, re.S)
    assert 'zoneAt' in handler.group(0), 'the click handler measures the region itself'
    move = re.search(r"this\.container\.addEventListener\('pointermove'.*?\n        \}\);", js, re.S)
    assert move and 'zoneAt' in move.group(0), 'the affordance measures the region itself'


def test_the_split_in_the_controller_matches_the_stylesheet():
    """Two separate statements of the same 30/40/30 boundary: the regions you can hit (JS) and the regions
    you are shown (CSS). Drift is invisible in both files and surfaces only as clicks landing somewhere
    other than where the wash said they would."""
    code = _code(CONTROLLER.read_text(encoding='utf-8'))
    edge = re.search(r'this\.ZONE_EDGE\s*=\s*([\d.]+)', code)
    assert edge, 'the edge width is not stated once'
    pct = round(float(edge.group(1)) * 100)

    css = _css()
    for part in ('prev', 'next'):
        rule = re.search(r'\.rcx__zone--' + part + r'\s*\{([^}]*)\}', css)
        assert rule and f'width: {pct}%' in rule.group(1), f'the {part} zone is not {pct}% wide'
        wash = re.search(r'\.rcx__wash--' + part + r'\s*\{([^}]*)\}', css)
        assert wash and f'width: {pct}%' in wash.group(1), f'the {part} wash does not cover its zone'
    hold = re.search(r'\.rcx__zone--hold\s*\{([^}]*)\}', css)
    assert hold and f'width: {100 - 2 * pct}%' in hold.group(1), 'the middle does not fill what is left'


def test_the_wash_has_no_horizontal_edge_of_its_own():
    """The wash's box is `.rcx__stage`, which starts directly under the top bar and, on a manual beat,
    ends directly above the row the Continue control sits in. Flat `top: 0` / `bottom: 0` therefore drew a
    hard line along both seams: the tint appeared out of nothing mid-screen and read as the background
    being clipped rather than as a region lighting up. The horizontal gradient carries the boundary that
    matters; the vertical mask only has to keep the shape from having an edge nobody asked for."""
    css = _css()
    rule = re.search(r'\.rcx__wash\s*\{([^}]*)\}', css)
    assert rule, 'no base wash rule'
    body = rule.group(1)
    assert 'mask-image' in body, 'the wash is cut flat at the stage boundary'
    mask = re.search(r'[^-]mask-image:\s*linear-gradient\(([^)]*)\)', body)
    assert mask and 'transparent 0' in mask.group(1) and mask.group(1).rstrip().endswith('transparent 100%'), (
        'the mask does not reach transparent at BOTH ends, so one seam is still hard'
    )


def test_the_pause_latches_rather_than_following_the_finger(js):
    """Holding pauses only while a finger is down, which is the wrong gesture for the slides that most
    need it -- the dense ones you are trying to read. A tap in the middle latches instead, and a latch
    that any of the resume paths can quietly undo is not a latch."""
    code = _code(js)
    assert 'if (this.pinned) return;' in _method(code, 'releaseBeat'), (
        'lifting the finger cancels a latched pause'
    )
    assert 'if (this.pinned) return;' in _method(code, 'startBeatTimer'), (
        'a dialog closing or a quiz being answered restarts a deck the hunter paused'
    )


def test_the_summary_is_paced_by_the_beat_clock_like_every_other_slide(js):
    """The summary used to bail out of `startBeatTimer` and schedule its own `setTimeout` to hand over to
    the card. That made it the one beat governed by a second clock, and nothing that governs the first
    reached it: a held finger did not stop it, the tab-visibility restart did not re-align it, and a
    latched pause left the deck reporting "paused" while the card arrived underneath on schedule.
    Measured before the fix -- `pinned: True` and, six seconds later, `ending: True` with the card
    visible. Same shape as the quiz dwell that raced the beat timer."""
    code = _code(js)
    assert '_cardTimer' not in code, 'the summary is back on a second clock'
    start = _method(code, 'startBeatTimer')
    assert 'this.slides.length - 1' not in start, (
        'the last beat opts out of the clock again, which is what forced a private timer'
    )
    end = _method(code, 'endOfBeat')
    assert 'showCardScene' in end and 'nextSlide' in end, 'the ending is not a branch of the one clock'
    assert 'this.endOfBeat()' in start, 'the beat still expires straight into nextSlide'


def test_moving_on_from_the_summary_hands_over_rather_than_doing_nothing(js):
    """`nextSlide` had nothing to advance to on the last beat, so the forward zone, the arrow key and the
    swipe were all dead there and waiting out the hold was the only way past the summary."""
    nxt = _method(_code(js), 'nextSlide')
    assert 'showCardScene' in nxt, 'forward on the last beat is still a dead tap'


def test_moving_on_clears_the_pause(js):
    """Every beat is armed through one function, so the pause has exactly one place to be forgotten. Left
    set, navigating away from a paused slide would land on a slide whose clock never starts -- a deck that
    looks broken rather than paused."""
    arm = _method(_code(js), 'armBeat')
    assert 'this.pinned = false' in arm
    assert "classList.remove('is-pinned'" in arm, 'the paused styling outlives the paused state'


def test_a_beat_with_no_clock_cannot_be_paused(js):
    """Quizzes and the calendar already wait on the hunter. "Paused" there is a state the deck would be in
    without anything actually being suspended, with the glyph claiming to hold a beat nothing was moving."""
    toggle = _method(_code(js), 'togglePause')
    assert 'is-waiting' in toggle, 'a waiting beat can be put into a second, fictional pause'
    assert '!this.pinned' in toggle, 'a paused deck cannot be un-paused once a dialog opens over it'


def test_the_pause_is_reachable_without_a_pointer(js):
    """The other two regions have had real buttons behind them all along, for keyboard and assistive tech.
    A third region existing only as a hit-test would be a control those hunters simply do not have."""
    code = _code(js)
    assert "getElementById('hold-slide')" in code
    assert 'this.holdBtn.addEventListener' in code
    assert "setAttribute('aria-label'" in _method(code, 'togglePause'), (
        'the label describes the state instead of what the next press does'
    )
    tpl = (ROOT / 'templates' / 'recap' / 'monthly_recap.html').read_text(encoding='utf-8')
    assert 'id="hold-slide"' in tpl and 'rcx__zone--hold' in tpl


def test_going_back_is_signposted_before_the_click(js):
    """Backward was an invisible quarter of the screen: aiming to skip forward and landing on it instead
    reads as a fault, because nothing suggested backward was possible."""
    code = _code(js)
    assert 'is-aim-back' in code and 'is-aim-fwd' in code
    css = (ROOT / 'static' / 'css' / 'components' / 'recap-stage.css').read_text(encoding='utf-8')
    assert '.rcx__aim' in css
    assert 'pointer-events: none' in css[css.index('.rcx__aim'):css.index('.rcx__aim') + 600], (
        'the arrows can steal a click from a quiz option'
    )


def test_a_held_remainder_cannot_leak_into_another_slide(js):
    """`_beatLeft` is the time left on the beat you were HOLDING. If it survives into the next slide,
    that slide runs on the previous one's leftovers and advances early -- which reads as a double skip.
    Keying it to its slide makes a stale remainder unusable rather than merely unlikely."""
    hold = _method(js, 'holdBeat')
    assert 'index: this.currentSlide' in hold, 'the remainder is not keyed to the slide it came from'
    start = _method(js, 'startBeatTimer')
    assert '_beatLeft.index === this.currentSlide' in start, (
        'a remainder from another slide would still be honoured'
    )


def test_the_direction_arrows_are_not_hidden_on_touch_capable_desktops():
    """A `hover: none` guard hides them on any desktop with a touchscreen, which is very likely why they
    were reported missing. They are driven by pointer state, so a genuine touch device simply never
    lights them; nothing needs to hide them."""
    css = (ROOT / 'static' / 'css' / 'components' / 'recap-stage.css').read_text(encoding='utf-8')
    # Comments are stripped first: the note explaining WHY the guard is gone contains the phrase, and a
    # substring search on raw CSS flags the explanation as the offence.
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    rules = re.findall(r'@media\s*\([^)]*hover:\s*none[^)]*\)\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}', css)
    assert not any('.rcx__aim' in r for r in rules), (
        'the arrows are hidden behind a `hover: none` media query, which matches touch-capable desktops'
    )


def test_the_arrows_are_visible_before_the_pointer_moves(js):
    """Entering the stage is a click on Begin; the mouse then sits still. Waiting for a pointermove that
    may never come means the affordance is never discovered."""
    assert "classList.add('is-aim-fwd')" in _method(js, 'openStage')


def test_a_beats_pause_state_is_decided_before_its_bar_is_painted(js):
    """paintBars asks whether the beat is paused so it knows whether to let the bar run. That state used
    to be updated AFTERWARDS by startBeatTimer, so the slide following a quiz was painted while the stage
    still said "waiting": its bar kept an inline width of 0 and never moved, while the timeout ran
    underneath. A dead bar with the deck advancing anyway reads as a broken timer -- and with nothing on
    the bar to watch, one advance is indistinguishable from two."""
    body = _method(js, 'armBeat')
    assert 'syncBeatState' in body, 'the pause state is not established for the beat being armed'
    assert body.index('syncBeatState') < body.index('this.paintBars('), (
        'the state is synced after painting, so paintBars reads the PREVIOUS beat'
    )


def test_the_direction_arrows_do_not_depend_on_pointer_state():
    """Tied to `is-aim-*`, they were absent whenever the pointer was outside the window, had not moved
    since the stage opened, or had fired a pointerleave -- an affordance you only get once you are
    already interacting is one nobody finds."""
    css = (ROOT / 'static' / 'css' / 'components' / 'recap-stage.css').read_text(encoding='utf-8')
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    assert re.search(r'\.rcx \.rcx__aim \{[^}]*opacity: 0\.\d', css), (
        'the arrows are only visible while an is-aim-* class is present'
    )


def test_rendering_slides_does_not_destroy_the_stage_furniture(js):
    """`this.slidesContainer.innerHTML = ''` wiped the WHOLE stage on first render, and the stage is not
    just slides -- the tap zones, the direction arrows and the hint line are markup that lives there too.

    Consequences, all of which were reported as separate bugs: the arrows were never in the DOM to be
    seen; the hint line vanished; and `this.prevBtn` / `this.nextBtn`, captured at construction, became
    detached nodes, so clicking a zone could never do anything. Only slides may be removed."""
    # Comments stripped: the note explaining WHY the wipe is gone quotes the wipe verbatim, and a raw
    # substring search reports the explanation as the offence. (Third time this session.)
    body = _code(_method(js, 'renderAllSlides'))
    assert "innerHTML = ''" not in body, (
        'renderAllSlides clears the whole stage again, taking the zones, arrows and hint with it'
    )
    assert "querySelectorAll('.recap-slide')" in body, 'slides are not removed selectively'


def test_the_stage_furniture_is_present_in_the_template():
    """If it is not in the markup, no amount of CSS can show it."""
    tpl = (ROOT / 'templates' / 'recap' / 'monthly_recap.html').read_text(encoding='utf-8')
    for needed in ('rcx__aim--prev', 'rcx__aim--next', 'id="prev-slide"', 'id="next-slide"', 'rcx__hint'):
        assert needed in tpl, f'{needed} missing from the stage markup'


def test_the_quiz_dwell_runs_on_the_beat_clock_not_its_own(js):
    """Answering used to start a private `setTimeout(nextSlide, 2000)` while the beat system had a timer
    of its own. Both call nextSlide, so the deck could advance twice -- and because the private dwell runs
    from the ANSWER while the beat runs from the slide opening, how early the second landed depended on
    how fast the question was answered. That is the reported "next slide finishes early".

    One clock: the dwell is armed through armBeat, so the bar shows the advance you actually get."""
    body = _method(js, 'handleOptionClick')
    assert 'dwellOnAnswer' in body, 'the quiz still schedules its own advance'
    assert 'setTimeout' not in _code(body), 'a private timer is racing the beat clock again'
    assert 'armBeat' in _method(js, 'dwellOnAnswer'), 'the dwell does not go through the beat path'


def test_a_blocked_beat_leaves_no_stale_end_time(js):
    """A quiz arms no clock, so `_beatEndsAt` must not keep pointing at the PREVIOUS beat's end -- holdBeat
    would read it as a remainder and hand the next slide a fraction of a beat."""
    body = _method(js, 'startBeatTimer')
    assert '_beatEndsAt = null' in body, 'a blocked beat keeps the previous beat`s end time'
    # The capture is INSIDE the `beatTimer` branch. It used to be an early return, but pinning the bar's
    # visual now has to happen whether or not a clock is running (a latched pause arrives after the hold
    # has already been released), so the guard narrowed from the whole method to the remainder alone.
    hold = _method(js, 'holdBeat')
    capture = re.search(r'if \(this\.beatTimer\) \{(.*?)\n        \}', hold, re.S)
    assert capture, 'holdBeat no longer guards the remainder on a running clock'
    assert '_beatLeft' in capture.group(1), (
        'holdBeat captures a remainder even when no beat is running'
    )


# --- The card scene's composition -----------------------------------------------------------------

def _css():
    return (ROOT / 'static' / 'css' / 'components' / 'recap-stage.css').read_text(encoding='utf-8')


def _rules(css):
    """(selector, declarations, order) for every top-level rule, comments stripped. Good enough for this
    one stylesheet: it has no nested at-rules around the selectors under test."""
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    css = re.sub(r'@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}', '', css, flags=re.S)
    return [(m.group(1).strip(), m.group(2), i)
            for i, m in enumerate(re.finditer(r'([^{}]+)\{([^{}]*)\}', css))]


def _specificity(sel):
    """(ids, classes, elements). Repeated classes count separately -- that repetition is the lever used
    to settle this cascade, so a resolver that collapsed them would miss the whole point."""
    return (sel.count('#'), len(re.findall(r'\.[\w-]+|\[|:(?!:)', sel)), len(re.findall(r'(?<![\w.#:-])[a-z]+', sel)))


def _resolve(css, target_classes, element_classes, prop):
    """The winning declaration of `prop` on an element carrying `element_classes`, inside a stage carrying
    `target_classes`. Resolves by (specificity, source order), the way a browser does."""
    have = set(target_classes) | set(element_classes)
    winner = None
    for sel, decls, order in _rules(css):
        for one in sel.split(','):
            one = one.strip()
            if not one or not one.startswith('.rcx'):
                continue
            needed = set(re.findall(r'\.([\w-]+)', one))
            if not needed <= have:
                continue
            m = re.search(rf'(?:^|;)\s*{prop}\s*:\s*([^;]+)', decls)
            if not m:
                continue
            key = (_specificity(one), order)
            if winner is None or key > winner[0]:
                winner = (key, m.group(1).strip(), one)
    return winner


def test_the_ending_scene_outranks_the_pointer_aim_state():
    """`is-aim-fwd` is set on the first pointer move and nothing clears it, so it is still on the stage
    when the deck reaches its ending. The hide was written at equal specificity ABOVE the highlight, lost
    the cascade, and left a lit arrow floating beside the share card -- a control pointing at a deck that
    is no longer there. Resolved rather than pattern-matched, because the bug was the RESOLUTION."""
    css = _css()
    for scene in ('is-ending', 'is-card-only'):
        won = _resolve(css, ['rcx', scene, 'is-aim-fwd'], ['rcx__aim', 'rcx__aim--next'], 'opacity')
        assert won and won[1] == '0', (
            f'with the pointer aimed forward, {scene} leaves the arrow at {won and won[1]} (from `{won and won[2]}`)'
        )


def test_the_card_frame_hugs_the_scaled_card():
    """`transform: scale` does not change layout, so a frame left to size itself stayed full card-height
    however far the card was scaled down -- on a phone, a 630px box around a 180px card. The column then
    had nothing left to centre and pinned the label to the top edge of the stage."""
    css, code = _css(), _code(CONTROLLER.read_text(encoding='utf-8'))
    frame = css[css.index('.rcx__card-frame {'):]
    frame = frame[:frame.index('}')]
    assert 'flex: 0 0 auto' in frame, 'a growing frame makes justify-content a no-op'
    assert 'justify-content: center' in css[css.index('.rcx__card {'):css.index('.rcx__card {') + 300]

    fit = code[code.index('const fit = ()'):]
    fit = fit[:fit.index('\n        };')]
    assert 'frame.style.height' in fit, 'the frame never hugs, so the stack cannot centre'
    assert 'ch * scale' in fit, 'hugging the UNSCALED height reintroduces the empty box'


def test_the_available_height_is_not_read_from_the_frame_itself():
    """Once the frame is sized from the scale, reading its own height to compute that scale is circular:
    the second fit (a resize) would measure the hugged box and lock the card at whatever size it already
    had. The measurement has to come from the column."""
    code = _code(CONTROLLER.read_text(encoding='utf-8'))
    fit = code[code.index('const fit = ()'):]
    fit = fit[:fit.index('\n        };')]
    assert 'frame.clientHeight' not in fit, 'the fit measures the box it is about to set'
    assert 'availableHeight()' in fit


# --- The month page around the ceremony -----------------------------------------------------------

def _page_tpl():
    return (ROOT / 'templates' / 'recap' / 'monthly_recap.html').read_text(encoding='utf-8')


def test_the_preview_scaler_and_the_markup_it_measures_agree(js):
    """The scaler finds its box with `closest(...)` and bails when it finds nothing -- silently, leaving a
    1200px card unscaled inside a 600px frame. It used to anchor on `.relative`, a Tailwind utility that
    vanished the moment the panel around it was rebuilt. A utility class is not a contract; this pins the
    named one, on both sides."""
    code = _code(js)
    fn = _method(code, 'scaleSharePreview')
    anchor = re.search(r"closest\('\.([\w-]+)'\)", fn)
    assert anchor, 'the scaler no longer looks up a container at all'
    cls = anchor.group(1)
    assert cls != 'relative', 'anchored on a utility class again'

    built = code[code.index('shareContent.innerHTML = `'):]
    built = built[:built.index('`;')]
    assert cls in built, f'the scaler measures .{cls}, which the markup it builds does not contain'
    assert 'share-preview-inner' in built, 'the element the scaler transforms is not rendered'


def test_the_card_leads_the_panel_and_the_controls_follow(js):
    """The panel opened on a background dropdown, which put a settings row above the thing being made.
    The dropdown is gone entirely now -- the grounds are swatches the page renders under the preview --
    so what is pinned here is that the preview still comes before whatever controls remain."""
    built = _code(js)
    built = built[built.index('shareContent.innerHTML = `'):]
    built = built[:built.index('`;')]

    assert 'recap-background-select' not in built, 'the ~110-theme dropdown is back in the panel'
    assert built.index('share-preview-inner') < built.index('download-recap-image'), (
        'the controls are above the card again'
    )


def test_the_page_carries_no_inline_event_handlers():
    """The error state's retry was an `onclick` attribute. Everything else on this page binds in the
    controller, and one exception is how a page stops having a rule."""
    assert 'onclick=' not in _page_tpl(), 'an inline handler is back on the recap page'


def test_the_terminal_states_do_not_use_emoji_as_iconography():
    """`text-6xl` emoji were the largest thing on both terminal states -- somebody else's artwork, at the
    size of a hero image, on a page that has its own icon set."""
    tpl = _page_tpl()
    for state in ('no-activity-state', 'error-state'):
        block = tpl[tpl.index(f'id="{state}"'):]
        block = block[:block.index('</div>\n\n') if '</div>\n\n' in block else 1200]
        assert 'text-6xl' not in block, f'{state} still sizes something as a display glyph'
        assert not re.search(r'[\U0001F300-\U0001FAFF]', block), f'{state} still uses an emoji as its icon'


def test_the_entrance_sends_you_to_the_archive_for_another_month():
    """"Pick a different month" pointed at `#recap-options`, an on-page section whose month picker had
    been removed -- so it scrolled you to a timezone dropdown. The archive is where months live now."""
    tpl = _page_tpl()
    aside = tpl[tpl.index('rcx-enter__aside'):]
    aside = aside[:aside.index('</p>')]
    assert "{% url 'recap_index' %}" in aside, 'the month link still points at a section, not the archive'
    assert '#recap-options' not in tpl, 'the retired options section is still referenced'


def test_no_component_class_overrides_the_hidden_utility():
    """Tailwind's utilities live in a LAYER; these component stylesheets do not. An unlayered
    `display: flex` therefore beats `.hidden { display: none }` regardless of source order -- so any
    element toggled with `hidden` whose class ALSO carries a `display` rule here is permanently visible.

    That shipped once: both the no-activity state and the error state rendered on every month page that
    loaded fine, stacked under the share panel, telling the hunter nothing had happened on a page full of
    what happened. Generalised rather than pinned to that one class, because the next component to set
    `display` and get toggled will hit it the same way and look just as correct in the source."""
    tpl = _page_tpl()
    css = '\n'.join((ROOT / 'static' / 'css' / 'components' / name).read_text(encoding='utf-8')
                    for name in ('recap-stage.css', 'recap-deck.css', 'recap-archive.css'))

    # There are TWO ways an element gets `hidden`, and the first version of this test knew about one.
    toggled = set()

    def _collect(class_attr):
        for cls in class_attr.split():
            if re.match(r'^(rc[xsa]|rca)[\w-]*$', cls):
                toggled.add(cls)

    # (a) Written into the markup.
    for attr in re.findall(r'class="([^"]*\bhidden\b[^"]*)"', tpl):
        _collect(attr)

    # (b) Added at RUNTIME. `#share-section` is hidden this way by the no-activity and error paths, so its
    # `class=` never contains `hidden` and it sailed straight past the first version of this check -- which
    # is how `.rcs` shipped without a guard even with this test already in place.
    sources = tpl + CONTROLLER.read_text(encoding='utf-8')
    for el_id in re.findall(r"getElementById\('([\w-]+)'\)\.classList\.add\('hidden'\)", sources):
        tag = (re.search(r'id="' + re.escape(el_id) + r'"[^>]*class="([^"]*)"', tpl)
               or re.search(r'class="([^"]*)"[^>]*id="' + re.escape(el_id) + r'"', tpl))
        if tag:
            _collect(tag.group(1))

    assert toggled, 'no toggled component classes found -- has the markup changed shape?'

    for cls in sorted(toggled):
        rule = re.search(r'\.' + re.escape(cls) + r'\s*\{([^}]*)\}', css)
        if not rule or 'display:' not in rule.group(1):
            continue        # sets no display, so the utility wins on its own
        guard = re.search(r'\.' + re.escape(cls) + r'(?:\.hidden|\[hidden\])', css)
        assert guard, (
            f'.{cls} sets `display` and is toggled with the `hidden` utility, which it silently '
            f'outranks -- add `.{cls}.hidden {{ display: none; }}`'
        )


def test_the_playhead_does_not_move_while_only_the_card_is_showing(js):
    """"Just get the card" opens the stage straight at its ending, where `is-ending` is the only thing
    making `.rcx__card` visible.

    `init()` is async: it awaits the whole-deck fetch and THEN calls `goToSlide(0)`. The quick-download
    button is on screen for that entire flight, so opening the card early meant the late `goToSlide(0)`
    arrived afterwards, took the non-summary branch, stripped `is-ending` -- and the card the hunter had
    just asked for vanished. Reproduced in a browser: visibility went `visible` -> `hidden` on that call.

    There is no deck on screen in this mode, so moving the playhead cannot show anything; it can only
    hide something."""
    body = _method(_code(js), 'goToSlide')
    guard = body[:body.index('\n', body.index('goToSlide(index)')) + 400]
    assert "classList.contains('is-card-only')" in guard, (
        'goToSlide runs in card-only mode again, where its only effect is to hide the card'
    )
    assert 'return;' in guard
