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
    m = re.search(r'\n    ' + re.escape(name) + r'\(.*?\n    \}\n', js, re.S)
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
