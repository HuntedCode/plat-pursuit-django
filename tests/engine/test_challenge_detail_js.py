"""`challenge-detail.js`, pinned by SOURCE TEXT because this project has no JS test runner.

WHY THIS FILE EXISTS AT ALL. The picker's close routine is a fourth copy of a sequence that is six separate
bug fixes deep, and every one of those fixes is invisible: drop it and the dialog still opens, still closes,
still writes. What breaks is a toast that never fires, an exit that plays twice, or a dialog stranded open.
None of that shows up in a Python suite, and there is nothing else to catch it.

SO THESE ARE STRUCTURAL PINS, not behaviour tests, and they are honest about that. They assert that a
specific guard is PRESENT, with a comment saying what its absence costs. A pin like this cannot prove the
guard works; it can only stop somebody deleting it while tidying, which is the failure mode that actually
happens. `project_js_source_text_pins` records the convention -- and that a JS-only change can therefore
fail the Python suite, which is the point.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / 'static' / 'js' / 'challenge-detail.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'css' / 'components' / 'challenges.css').read_text(encoding='utf-8')


def test_the_file_is_there_and_is_not_a_stub():
    """A pin file whose subject shrank to nothing would otherwise pass every assertion below by vacuity."""
    assert len(JS) > 4000


# ── the close routine's six subtleties ────────────────────────────────────────────────────────────

def test_the_after_callbacks_are_a_queue_not_a_single_slot():
    """Press an offer, then Escape while the request is in flight. The reply lands mid-exit, calls `close`
    with its toast callback, and hits the "already closing" early return -- so a single-slot `after` is
    dropped and the hunter is never told their square was filled. Queue first, return second."""
    assert 'pendingAfter.push(after)' in JS
    assert 'pendingAfter = []' in JS
    # The push must come BEFORE the early return, or the queue changes nothing.
    assert JS.index('pendingAfter.push(after)') < JS.index("contains('is-closing')")


def test_reduced_motion_closes_immediately_and_still_drains():
    """The exit is gated on `prefers-reduced-motion: no-preference` in CSS, so under `reduce` no
    `animationend` ever fires -- waiting for one would strand the dialog open forever."""
    assert "matchMedia('(prefers-reduced-motion: reduce)')" in JS
    assert 'if (reduced) { dialog.close(); drain(); return; }' in JS


def test_the_animationend_guard_checks_the_pseudo_element_too():
    """`animationend` bubbles, and the `::backdrop`'s own exit fires on the DIALOG with `pseudoElement`
    set -- so a target check alone ends the close early and truncates the dialog's own exit. Both are
    0.18s today, so nothing visibly breaks until somebody shortens the scrim."""
    assert 'e.target !== dialog || e.pseudoElement' in JS


def test_the_fallback_timer_is_cleared_inside_done():
    """Otherwise a stale 400ms fallback from a previous close fires inside a LATER one -- cutting that
    exit short and draining a queue that is not its own."""
    assert 'if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }' in JS


def test_there_is_a_fallback_timer_at_all():
    """A dropped `animationend` -- a backgrounded tab, a mid-animation style recalc -- would otherwise leave
    the dialog open, `.is-closing`, and un-closable."""
    assert 'closeTimer = setTimeout(' in JS
    assert '}, 400);' in JS


def test_the_swipe_closes_directly_rather_than_choreographed():
    """`dismissableSheet` has already animated the sheet off-screen and cleared its transform by the time it
    calls `onClose`. Handing it the choreographed close made a flicked sheet slide away, POP BACK into view,
    then play a second 180ms exit."""
    assert 'onClose: function () { if (dialog.close && dialog.open) { dialog.close(); } }' in JS


# ── the sheet, the focus, and the ways out ────────────────────────────────────────────────────────

def test_the_sheet_passes_a_drag_handle():
    """`dismissableSheet`'s own contract: omit a handle on a sheet you READ, pass one on a sheet you
    OPERATE. This one holds a search term and two irreversible offers, so without a handle any downward
    flick -- on a result, on the warning block -- dismisses it mid-decision."""
    assert "handle: '.pp-cpick__head'" in JS


def test_opening_focuses_the_dialog_and_not_the_search_field():
    """Focusing the field opens the soft keyboard over the panel before a hunter has seen what is in it, on
    a sheet whose first job is to SHOW options."""
    assert 'dialog.showModal();' in JS
    assert 'dialog.focus();' in JS
    assert JS.index('dialog.showModal();') < JS.index('dialog.focus();')


def test_escape_and_the_backdrop_both_route_through_the_close():
    """Escape would otherwise take the browser's instant close and skip the exit; the backdrop is the one
    two sibling dialogs honour and a third forgot."""
    assert "addEventListener('cancel', function (e) { e.preventDefault(); close(); })" in JS
    assert 'if (e.target === dialog) { close(); }' in JS


# ── the contract with the server ──────────────────────────────────────────────────────────────────

def test_the_409_is_handled_as_a_confirmation_and_not_a_failure():
    """The whole two-step rests on this. Treating 409 as a plain error would show the refusal and never
    offer the confirmation, making every already-completed offer permanently unusable."""
    assert 'response.status === 409' in JS
    assert "body.append('confirm', '1')" in JS


def test_the_confirmation_text_comes_from_the_server():
    """So the warning and the rule cannot drift apart. A second copy of "this locks the square" in the
    client is a second thing to forget to update."""
    assert 'window.confirm(' in JS
    assert 'data.error' in JS


def test_the_toast_fires_after_the_close_not_beside_it():
    """A modal `<dialog>` makes everything outside it inert and takes it out of the accessibility tree, and
    the toast region lives outside -- so a toast raised while the sheet is open renders behind the top-layer
    backdrop and announces nothing."""
    assert 'close(function () { toast(' in JS


def test_stale_replies_are_discarded_on_identity():
    """Two panels can be in flight when somebody types quickly or opens a second square. Applying the older
    reply shows the wrong pool under the right title -- guarded on a sequence number, not on nullness."""
    assert 'var seq = ++requestSeq;' in JS
    assert 'if (seq !== requestSeq) { return; }' in JS


def test_the_failure_message_helper_is_awaited_and_feature_tested():
    """`API.failureOr` is `async`, so it returns a PROMISE -- an earlier version of this file handed that
    straight to `textContent` and would have rendered "[object Promise]" at a hunter being told why their
    square did not save.

    And it is guarded on the METHOD rather than the namespace: static files are hashed independently, so a
    cached older `utils.js` can meet this fresh file, and `if (!PP.API)` passes on a bundle that merely lacks
    this one method. The exposure is worst here, inside a rejection handler, where failing to report a
    failure is invisible."""
    assert 'PP.API && PP.API.failureOr' in JS
    assert '.then(say)' in JS


def test_names_are_written_as_text_never_as_markup():
    """A contract's name is catalogue data that passes through staff hands. An escaping mistake here would be
    an XSS on every hunter who opened a picker."""
    # MATCHED AS AN ASSIGNMENT, not as a word. A bare `'innerHTML' not in JS` failed against this file's
    # own comment ("`textContent`, never `innerHTML`") -- an absence assertion has to carry the syntax of the
    # thing it forbids, or the prose explaining the rule breaks the test for the rule.
    assert 'innerHTML =' not in JS and '.innerHTML=' not in JS
    assert 'insertAdjacentHTML' not in JS
    assert 'name.textContent = row.name;' in JS


def test_offers_are_wrapped_in_list_items():
    """Both row containers are `<ul>`s, and a `<button>` is not valid as their direct child -- the parser
    tolerates it and assistive tech stops counting the list."""
    assert "var item = document.createElement('li');" in JS


# ── the CSS half of the same contract ─────────────────────────────────────────────────────────────

def test_the_closed_dialog_is_hidden():
    """The author `display: flex` on `.pp-cpick` beats the UA's `dialog:not([open]) { display: none }`, so
    without this rule the CLOSED dialog paints over the page. It is the mistake `.gl-dialog` documents
    having made."""
    assert '.pp-cpick:not([open]) { display: none; }' in CSS


def test_the_exit_animation_the_js_waits_for_exists():
    """The JS adds `.is-closing` and waits for `animationend`. If the keyframes were missing or renamed, no
    animation would run, no event would fire, and every close would sit on the 400ms fallback -- the dialog
    would visibly hang before going."""
    assert '.pp-cpick.is-closing' in CSS
    assert '@keyframes cpickOut' in CSS


def test_the_backdrop_is_a_flat_dim_not_a_blur():
    """A `backdrop-filter` re-samples the page behind it every frame, which is the mobile-GPU trap this
    project has hit before."""
    assert '.pp-cpick::backdrop { background:' in CSS
    cpick = CSS[CSS.index('.pp-cpick {'):]
    # THE DECLARATION, with its colon. Without it this matched the comment directly above the rule, which
    # says "never a `backdrop-filter`" -- so the sentence explaining the rule was failing the test for it.
    assert 'backdrop-filter:' not in cpick


def test_the_dialog_height_is_capped_in_dvh():
    """`vh` on iOS Safari and Chrome Android is the LARGE viewport and does not shrink for the soft
    keyboard, so a focused search field could push this sheet's own header off an already-short screen --
    and this sheet has a search field, so it is exactly the case that rule was written for."""
    assert 'max-height: min(88dvh' in CSS


@pytest.mark.parametrize('selector', [
    'data-cpick-open', 'data-cpick-close', 'data-cpick-q', 'data-cpick-status',
    'data-cpick-rows', 'data-cpick-catchup', 'data-cpick-catchup-rows',
    'data-cpick-current', 'data-cpick-clear', 'data-cpick-tally', 'data-challenge-id',
])
def test_every_hook_the_js_reads_is_in_the_template(selector):
    """A renamed hook is silent: `querySelector` returns null, the guard skips it, and that part of the
    picker simply stops working with nothing in the console."""
    template = (ROOT / 'templates' / 'challenges' / 'challenge_detail.html').read_text(encoding='utf-8')
    assert selector in JS, '%s is not read by the script' % selector
    assert selector in template, '%s is not rendered by the template' % selector
