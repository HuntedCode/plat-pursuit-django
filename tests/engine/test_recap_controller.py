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
    body = _method(js, 'startBeatTimer')
    assert 'MANUAL_BEATS.has' in body, 'manual beats are not exempted from the auto-advance'
    assert "dialog[open]" in body, 'an open dialog does not stop the clock'


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
