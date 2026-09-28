"""`challenge-detail.js`, pinned by SOURCE TEXT because this project has no JS test runner.

WHY THIS FILE EXISTS AT ALL. The picker's close routine duplicates a sequence that is six separate bug
fixes deep, and every one of those fixes is invisible: drop it and the dialog still opens, still closes,
still writes. (It was called "a fourth copy" here and in the JS; the JS now declines to count, because two
successive attempts at the tally were both wrong.) What breaks is a toast that never fires, an exit that plays twice, or a dialog stranded open.
None of that shows up in a Python suite, and there is nothing else to catch it.

SO THESE ARE STRUCTURAL PINS, not behaviour tests, and they are honest about that. They assert that a
specific guard is PRESENT, with a comment saying what its absence costs. A pin like this cannot prove the
guard works; it can only stop somebody deleting it while tidying, which is the failure mode that actually
happens. `project_js_source_text_pins` records the convention -- and that a JS-only change can therefore
fail the Python suite, which is the point.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / 'static' / 'js' / 'challenge-detail.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'css' / 'components' / 'challenges.css').read_text(encoding='utf-8')


def _code_only(source):
    """`source` with its comment lines removed, for assertions about what is ABSENT.

    WHY THIS EXISTS, written down because I have made the same mistake five times on this branch: an
    assertion that something is absent will match the COMMENT explaining why it is absent. `'innerHTML' not
    in JS` failed against "never `innerHTML`"; `'backdrop-filter' not in CSS` failed against "never a
    `backdrop-filter`"; `'That square would be locked' not in JS` failed against the comment recording that
    the string used to be there. Each time I fixed the one assertion and not the habit.

    Two ways out: carry the syntax of the thing (`innerHTML =`, `backdrop-filter:`), which works when the
    thing HAS syntax, or strip the commentary, which works when it does not -- a piece of removed copy has
    no syntax to match on. This is the second.

    Line-based rather than a real parse: a `//` inside a string literal would be stripped wrongly, and there
    is no such line in either file. A tokenizer for two files' worth of absence checks is not the trade.
    """
    out = []
    in_block = False
    for line in source.splitlines():
        stripped = line.strip()
        if in_block:
            if '*/' in stripped:
                in_block = False
            continue
        if stripped.startswith('/*'):
            if '*/' not in stripped:
                in_block = True
            continue
        if stripped.startswith(('//', '*')):
            continue
        out.append(line)
    return '\n'.join(out)


#: A `{% comment %}...{% endcomment %}` block, note argument and all (`{% comment "why" %}` is legal), and
#: a `{# ... #}` comment. Non-greedy and DOTALL so each matches one span rather than everything between the
#: first opener and the last closer.
_TPL_BLOCK_COMMENT = re.compile(r'\{%\s*comment\b.*?%\}.*?\{%\s*endcomment\s*%\}', re.S)
#: `{# #}` IS SINGLE-LINE in Django, so this must not cross one. With `re.S` an unpaired `{#` -- in a URL
#: or a query string, say -- pairs with the next real `{# #}` anywhere below it and deletes every line
#: between them from the text an absence assertion inspects. That is the vacuous-pass hazard this helper
#: exists to prevent, reintroduced by the flag.
_TPL_INLINE_COMMENT = re.compile(r'\{#[^\n]*?#\}')


def _template_code(source):
    """A Django template with its `{% comment %}` blocks and `{# #}` comments removed.

    THE SAME HAZARD AS `_code_only`, in the third language this file reads. An assertion that a template no
    longer uses `forloop` matched the comment SAYING it no longer uses `forloop` -- the seventh time on this
    branch that an absence assertion has been satisfied by the prose explaining the absence.

    `_code_only` strips `//` and `/* */`, which a Django template does not use, so it could not help here.
    Same idea, different comment syntax.

    SPANS, NOT LINES, and the first version got this wrong in precisely the way it existed to prevent. It
    dropped any LINE containing `{% comment %}`, so a line carrying markup plus a trailing comment lost the
    markup too -- and an absence assertion cannot fail on code it was never shown. An audit found it; nothing
    in the suite could have, because every such assertion would have passed.
    """
    return _TPL_INLINE_COMMENT.sub('', _TPL_BLOCK_COMMENT.sub('', source))


#: The two sources with commentary removed. Use these for `not in` and the full text for `in`.
JS_CODE = _code_only(JS)
CSS_CODE = _code_only(CSS)


def _reloads():
    """Every line that navigates, so the pins can talk about HOW MANY rather than banning them.

    A blanket "no reload" was right when there were none, and is the kind of assertion someone deletes the
    day one is genuinely needed. There is exactly one: the run FINISHING is a mode change (the header gains
    a chip, the read-only note appears, `can_edit` turns false, every square becomes a `<div>`), and
    patching all of that client-side would be the second renderer this design exists to avoid.
    """
    return [ln.strip() for ln in JS_CODE.splitlines() if 'location.reload' in ln]


def test_the_file_is_there_and_is_not_a_stub():
    """A pin file whose subject shrank to nothing would otherwise pass every assertion below by vacuity.

    MEASURED ON CODE, not on bytes. This file is mostly comment by design, so a byte count would have been
    satisfied by the header alone with the whole implementation deleted."""
    code = [ln for ln in JS.splitlines()
            if ln.strip() and not ln.strip().startswith(('*', '/*', '//', '*/'))]
    assert len(code) > 250, 'only %d lines of code' % len(code)


# ── the close routine's six subtleties ────────────────────────────────────────────────────────────

def test_the_after_callbacks_are_a_queue_not_a_single_slot():
    """Press an offer, then Escape while the request is in flight. The reply lands mid-exit, calls `close`
    with its toast callback, and hits the "already closing" early return -- so a single-slot `after` is
    dropped and the hunter is never told their square was filled. Queue first, return second."""
    assert 'pendingAfter.push(after)' in JS
    # THE DRAIN'S RESET, not the declaration. `'pendingAfter = []'` alone was satisfied by
    # `var pendingAfter = [];` at the top of the closure, so it pinned nothing about draining.
    assert 'var queued = pendingAfter;' in JS
    assert 'queued.forEach(function (fn) { fn(); });' in JS
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
    # ANCHORED TO ITS OWN STATEMENT. `'}, 400);'` alone matched any 400ms timeout anywhere in the file;
    # slicing to the next `);` stopped inside `removeEventListener('animationend', onEnd);` instead.
    assert 'closeTimer = setTimeout(' in JS
    start = JS.index('closeTimer = setTimeout(')
    rest = JS[start:]
    following = rest.find('setTimeout(', len('closeTimer = setTimeout('))
    statement = rest if following == -1 else rest[:following]
    assert ', 400);' in statement, 'the fallback timer is no longer 400ms'


def test_the_swipe_closes_directly_rather_than_choreographed():
    """`dismissableSheet` has already animated the sheet off-screen and cleared its transform by the time it
    calls `onClose`. Handing it the choreographed close made a flicked sheet slide away, POP BACK into view,
    then play a second 180ms exit."""
    assert 'dismissed = true;' in JS
    assert 'if (dialog.close && dialog.open) { dialog.close(); }' in JS


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


def test_the_sheet_is_shown_before_it_is_loaded():
    """THE ORDERING THAT ACTUALLY MATTERED on those same lines, and it was the wrong way round.

    `load()` writes "Loading..." into the status region, which lives INSIDE the dialog -- so while the dialog
    was still `display: none` the mutation happened outside the rendered tree and no screen reader observed
    it. Opening the picker announced nothing at all. Showing first is safe because `reset()` has already
    blanked the sheet, so it is never displayed holding the previous square's state.
    """
    assert JS.index('dialog.showModal();') < JS.index("load(openKey, '');")


def test_escape_and_the_backdrop_both_route_through_the_close():
    """Escape would otherwise take the browser's instant close and skip the exit; the backdrop is the one
    two sibling dialogs honour and a third forgot.

    THE HANDLER NOW CHOOSES between three outcomes, narrowest first, because Escape has to answer an open
    prompt rather than dismissing the question and the offers together. What this still pins is that the
    SHEET's close goes through the choreographed routine and not the browser's instant one."""
    assert "addEventListener('cancel', function (e) {" in JS
    body = JS[JS.index("addEventListener('cancel', function (e) {"):]
    body = body[:body.index('\n        });') + 1]
    assert 'e.preventDefault();' in body
    assert 'close();' in body
    # Narrowest first: both prompts are answered before the sheet is considered.
    assert body.index('footAsking()') < body.index('close();')
    assert body.index('openPromptClose') < body.index('close();')
    assert 'if (e.target === dialog) { close(); }' in JS


# ── the contract with the server ──────────────────────────────────────────────────────────────────

def test_the_409_is_handled_as_a_confirmation_and_not_a_failure():
    """The whole two-step rests on this. Treating 409 as a plain error would show the refusal and never
    offer the confirmation, making every already-completed offer permanently unusable."""
    assert 'response.status === 409' in JS
    assert "body.append('confirm', '1')" in JS


def test_the_confirmation_text_comes_from_the_server():
    """So the warning and the rule cannot drift apart. A second copy of "this locks the square" in the client
    is a second thing to forget to update."""
    assert 'data.error' in JS
    # `ask(` is the dispatcher that picks the surface; it was `askInline(` when there was only one.
    assert 'ask(button,' in JS


def test_no_confirmation_is_a_native_pop_up():
    """NATIVE `confirm()` WAS THE FIRST VERSION and it was wrong beyond looking foreign inside a designed
    sheet: it renders chrome we do not control, it cannot say which of the two answers is the safe one, and a
    native dialog SURVIVES its sheet -- dismiss the picker with a request in flight and the confirm sat over a
    bare page, still able to lock a square permanently.

    An inline prompt removes that class STRUCTURALLY rather than by guard: a prompt that lives in the sheet
    leaves with it."""
    assert 'window.confirm' not in JS_CODE
    assert 'confirm(' not in JS_CODE.replace('askInline(', '').replace('offerConfirmation(', '')


def test_the_toast_fires_after_the_close_not_beside_it():
    """A modal `<dialog>` makes everything outside it inert and takes it out of the accessibility tree, and
    the toast region lives outside -- so a toast raised while the sheet is open renders behind the top-layer
    backdrop and announces nothing."""
    assert 'close(function () { applySlot(' in JS
    assert 'if (message) { toast(message); }' in JS


def test_stale_replies_are_discarded_by_sequence():
    """Two panels can be in flight when somebody types quickly or opens a second square, and applying the
    older reply shows the wrong pool under the right title.

    A MONOTONIC SEQUENCE, and the earlier name for this test said "identity" -- which is what the module
    comment claimed too. `openKey` is compared to nothing anywhere; the guard is `seq !== requestSeq`. The
    mechanism is right and the word was wrong, which matters because "identity" sent a reader looking for a
    comparison that does not exist."""
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
    assert '.then(fail)' in JS


def test_names_are_written_as_text_never_as_markup():
    """A contract's name is catalogue data that passes through staff hands. An escaping mistake here would be
    an XSS on every hunter who opened a picker."""
    # MATCHED AS AN ASSIGNMENT, not as a word. A bare `'innerHTML' not in JS` failed against this file's
    # own comment ("`textContent`, never `innerHTML`") -- an absence assertion has to carry the syntax of the
    # thing it forbids, or the prose explaining the rule breaks the test for the rule.
    for forbidden in ('outerHTML', 'insertAdjacentHTML', 'document.write'):
        assert forbidden not in JS_CODE, '%s reaches the DOM as markup' % forbidden

    # ONE DELIBERATE `innerHTML`, and exactly one. It swaps in the square the SERVER re-rendered after a
    # write -- the markup comes from `partials/_square_body.html`, the same template the page used, never
    # from anything composed here. Pinned as a count plus its exact shape rather than as a blanket ban,
    # because the ban is what a future reader would otherwise delete when they needed the one use.
    uses = [ln.strip() for ln in JS_CODE.splitlines() if 'innerHTML' in ln]
    assert uses == ['if (slot.html) { square.innerHTML = slot.html; }'], (
        'unexpected innerHTML use(s): %r' % uses)
    assert 'name.textContent = row.name;' in JS


def test_offers_are_wrapped_in_list_items():
    """Both row containers are `<ul>`s, and a `<button>` is not valid as their direct child -- the parser
    tolerates it and assistive tech stops counting the list.

    BOTH builders, because this pinned only `offerButton`'s and the search panel builds its own."""
    assert "var item = document.createElement('li');" in JS
    assert "var card = document.createElement('li');" in JS


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
    # SCOPED TO THE PICKER'S OWN BLOCK, not to the rest of the file. This used to slice to end-of-file, so
    # any future component appended below would have failed the PICKER's test.
    # SCOPED TO THE PICKER'S OWN BLOCK, and taken from the comment-free view: this used to slice to
    # end-of-file (so a later component could fail the PICKER's test) and to match the comment above the
    # rule, which says "never a `backdrop-filter`".
    start = CSS_CODE.index('.pp-cpick {')
    cpick = CSS_CODE[start:CSS_CODE.index('.pp-cpick__foot', start)]
    assert 'backdrop-filter' not in cpick


def test_the_dialog_height_is_capped_in_dvh():
    """`vh` on iOS Safari and Chrome Android is the LARGE viewport and does not shrink for the soft
    keyboard, so a focused search field could push this sheet's own header off an already-short screen --
    and this sheet has a search field, so it is exactly the case that rule was written for."""
    assert 'max-height: min(88dvh' in CSS


@pytest.mark.parametrize('selector', [
    'data-cpick-open', 'data-cpick-close', 'data-cpick-q', 'data-cpick-status',
    'data-cpick-rows', 'data-cpick-catchup', 'data-cpick-catchup-rows',
    'data-cpick-current', 'data-cpick-clear', 'data-cpick-tally', 'data-challenge-id',
    # THE FOUR THAT WERE MISSING, and they are the ones with no guard at all: `els.title`, `els.sub`,
    # `els.catchupTitle` and `els.catchupNote` are written unconditionally in the render path.
    'data-cpick-title', 'data-cpick-sub', 'data-cpick-catchup-title', 'data-cpick-catchup-note',
    # AND THE FIVE ADDED SINCE, which fail the OTHER way and are worse for it. `showNote`, `showNoteBlock`
    # and `askInFoot` all guard their elements and return early, so renaming one of these removes the whole
    # four-fact warning -- or the confirmation's cost line -- with no test failing and no console error.
    # A silent disappearance immediately before the only irreversible action in the feature.
    'data-cpick-note', 'data-cpick-note-lead', 'data-cpick-note-facts',
    'data-cpick-ask-cost', 'data-cpick-history-switch',
])
def test_every_hook_the_js_reads_is_in_the_template(selector):
    """A renamed hook takes the picker down, and the first version of this docstring had that backwards.

    It said the failure was silent -- "`querySelector` returns null, the guard skips it". There are no
    guards in the render path: `els.title.textContent`, `els.sub`, `els.rows.forEach`, `els.catchup.hidden`,
    `els.current` and `els.q.value` are all dereferenced unconditionally. A renamed hook is a **TypeError**
    that aborts the render and leaves the sheet stuck on "Loading...".

    Which is a better reason for this test to exist, not a worse one -- and the four hooks it omitted are
    precisely the unguarded ones."""
    template = (ROOT / 'templates' / 'challenges' / 'challenge_detail.html').read_text(encoding='utf-8')
    assert selector in JS, '%s is not read by the script' % selector
    assert selector in template, '%s is not rendered by the template' % selector


# ── the JS's hand-built URLs must agree with the urlconf ──────────────────────────────────────────

@pytest.mark.django_db
@pytest.mark.parametrize('url_name, args', [
    ('challenge_slot', (7, 'card-shark')),
    ('challenge_search', (7,)),
    ('challenge_assign', (7, 'card-shark')),
    ('challenge_clear', (7, 'card-shark')),
])
def test_the_scripts_urls_agree_with_the_urlconf(url_name, args):
    """The JS builds its endpoints by string concatenation, so a route rename breaks it SILENTLY -- the
    panel would simply say "That did not load" and nothing would reach a log with a cause in it.

    DERIVED FROM `reverse()` RATHER THAN RESTATED. Splitting the real URL on its arguments leaves the
    literal path fragments, and each of those must appear in the script. Writing the expected URLs out by
    hand would pin this test's copy of them instead of the script's.
    """
    from django.urls import reverse

    url = reverse(url_name, args=args)
    literals = [part for part in re.split(r'|'.join(re.escape(str(a)) for a in args), url) if part]

    for fragment in literals:
        assert fragment in JS, (
            '%s builds %r, but the script does not contain the fragment %r'
            % (url_name, url, fragment))


# ── the guards the audit found missing ────────────────────────────────────────────────────────────

def test_a_dismissed_sheet_cannot_confirm_an_irreversible_write():
    """THE WORST DEFECT ANY AUDIT ON THIS BRANCH FOUND.

    Press a catch-up offer, press Escape, let the 409 land: `offerConfirmation` fired a bare `window.confirm`
    over a page with no sheet behind it, quoting a warning about a decision the hunter had walked away from
    -- and pressing OK stamped the square complete and LOCKED IT FOREVER. The one action on a run that
    cannot be undone was reachable from a dismissed dialog.

    `list-detail.js` has exactly this guard and it was not ported with the rest of the routine.
    """
    assert 'function stillOpen()' in JS
    assert 'if (!stillOpen()) { return; }' in JS
    # THE THIRD CONDITION, and its absence put the original bug straight back. `dismissableSheet` touches
    # neither `dialog.open` nor `.is-closing` -- it drags the sheet with a transform, then translates it
    # off-screen and waits 200ms before calling `onClose`. So through the whole drag (hunter-controlled,
    # unbounded) plus that delay, a sheet that had visibly left the screen still reported itself open, and a
    # 409 landing in that window put the permanent-lock confirm on a bare page. Touch is the platform the
    # drag handle exists for, so this was the likely path, not the exotic one.
    assert "return dialog.open && !dialog.classList.contains('is-closing') && !dismissed;" in JS


def test_a_failure_after_dismissal_is_reported_somewhere_visible():
    """`say()` writes into the dialog. After a dismissal that element is `display: none`, so a failed write
    was invisible AND unannounced, with no reload -- the hunter was told nothing and had no reason to
    suspect anything."""
    assert 'function reportAway(' in JS
    assert 'if (!stillOpen()) { reportAway(message); return; }' in JS


def test_only_one_write_can_be_in_flight():
    """Nothing disabled an offer and `requestSeq` guarded only `load()`, so the same offer could be sent
    twice -- two writes, two reload timers and two success toasts -- and two DIFFERENT offers could toast two
    games for one square. The stylesheet already shipped the disabled look before anything set it."""
    # BOTH WRITES, and the claim used to be false: `clear` was entirely outside the flag, so two quick
    # presses meant two POSTs, two toasts and two reload timers -- the very thing the flag was added for, in
    # the one write it did not cover. `JS.count('release();') >= 2` could not tell "both paths" from "twice
    # on one path", which is how that passed.
    # COUNTED ON THE FLAG, not on the whole statement. `assign`'s early return now SAYS something before
    # returning (a confirmation that vanished in silence is worse than a stray double-press ignored in
    # silence), so the two sites no longer share one spelling.
    assert JS.count('if (writing)') == 2, 'assign and clear must both take the lock'
    assert JS.count("say('Still saving the last one. Try again in a moment.'); return; }") == 1, (
        'the write that a confirmation can reach must report when it refuses'
    )
    assert JS.count('writing = true;') == 2
    # EXCLUDING THE DECLARATION. `var writing = false;` is the initialiser, not a release, so counting the
    # bare string found three -- the same trap as counting `pendingAfter = []` and matching its `var` line.
    assert JS.count('var writing = false;') == 1
    assert JS.count('writing = false;') - JS.count('var writing = false;') == 2, (
        'each lock needs its own release')
    assert 'if (button) { button.disabled = true; }' in JS
    assert 'els.clear.disabled = true;' in JS
    # AND THE STYLE EXISTS. It was deleted as "dead CSS" by the same change that started setting the
    # attribute, so the guard was real and completely invisible -- a pressed offer looked exactly like an
    # unpressed one, with a 12px "Saving..." line as the only feedback.
    assert '.pp-cpick__row:disabled' in CSS
    assert '.pp-cpick__key:disabled' in CSS


def test_the_panel_is_fully_reset_before_it_is_shown():
    """It used to clear the lists and leave the title, the subtitle, the "Currently: <game>" line and the
    Clear button holding the LAST square's values -- so opening B after filling A showed A's title (which
    `aria-labelledby` announces) and a live Clear button whose handler posts to B."""
    assert 'function reset()' in JS
    for cleared in ("els.current.textContent = ''", 'els.clear.hidden = true',
                    "els.title.textContent = 'Choose a game'", "els.sub.textContent = ''"):
        assert cleared in JS, '%s is not reset' % cleared
    # THE ORDERING THAT MATTERS. This asserted `function reset()` came before `reset();`, which function
    # hoisting makes meaningless -- it was pinning source layout, not behaviour. What has to hold is that the
    # sheet is reset BEFORE it is shown.
    assert JS.index('reset();') < JS.index('dialog.showModal();')


def test_nothing_waits_on_a_reload_any_more():
    """THE WHOLE MECHANISM IS GONE, and the history is worth keeping because it took three attempts.

    First a 900ms reload timer that started before the toast fired, so the toast got ~700ms of a 5000ms life
    and the live-region announcement was cut off mid-sentence. Then `TOAST_MS`, one constant feeding both, so
    the reload genuinely waited -- correct, and still 2.8s of stall plus a page flash, an 840ms entrance
    replay and a scroll to top, 26 times for a full run. The owner filled a run and counted them.

    Now the write reply carries the square's markup and there is no navigation at all, so there is nothing
    for a duration to coordinate."""
    assert 'TOAST_MS' not in JS_CODE
    assert len(_reloads()) == 1, 'the per-square reloads are gone; only the run-finishing one remains'


def test_a_completed_square_stops_being_pressable_at_once():
    """The server will re-render it as a `<div>`, but until the reload it is still a button with a hover
    lift -- so it could be re-opened, and every offer inside it would then be refused."""
    assert "square.removeAttribute('data-cpick-open');" in JS
    assert 'square.disabled = true;' in JS


def test_each_square_button_in_a_search_result_names_its_game():
    """The visible pill says only the square, and the game's name sits in a sibling associated with nothing
    -- so a keyboard user heard "A, button", "B, button" with no idea which game they were placing, and two
    results fitting the same job produced two identically-named buttons.

    BUILT FROM THE VISIBLE TEXT rather than composed separately, so the accessible name always CONTAINS the
    label a voice-control user would say -- which a hand-written "Put X in Y" stopped doing the moment the
    single-square case started reading "Add this game to S"."""
    assert "pick.setAttribute('aria-label', pick.textContent + ' \\u2014 ' + row.name);" in JS


def test_a_non_json_409_does_not_invent_a_reason():
    """It used to assert "That square would be locked. Try again." -- stating the lock as fact when the cause
    is unknown, and telling the hunter to retry something that would 409 forever, because nothing on that
    path ever sends `confirm`."""
    assert 'That square would be locked' not in JS_CODE
    assert "fail('That did not go through. Reload and try again.');" in JS


def test_a_refusal_does_not_look_like_progress():
    """Every failure wrote into the status line in the same dim grey as "Loading..." and "Saving...", so a
    refusal was visually identical to progress. The donor dialog has a toned error box for exactly this."""
    assert "els.status.classList.add('pp-cpick__status--error');" in JS
    assert "els.status.classList.remove('pp-cpick__status--error');" in JS
    assert '.pp-cpick__status--error' in CSS


def test_the_result_count_reaches_the_live_region():
    """It went only to `els.sub`, which is not one -- so "12 games fit" was never announced and a
    screen-reader user got silence on every successful load."""
    assert 'say(els.sub.textContent);' in JS


def test_the_editable_square_has_a_pointer_cursor():
    """Tailwind v4 dropped preflight's `button { cursor: pointer }`, so the 26 pressable squares -- the
    page's primary interaction -- showed the default arrow. Every other button in that file sets it."""
    block = CSS[CSS.index('.pp-csq {'):CSS.index('.pp-csq--empty')]
    assert 'cursor: pointer;' in block
    # BOTH non-pressable shapes. `div.pp-csq` covers a read-only square; `button.pp-csq:disabled` covers one
    # the JS has just completed, which stays a `<button>` on purpose -- and which kept `cursor: pointer` for
    # as long as it sat there, while a comment of mine claimed `:disabled` had dealt with it.
    assert 'div.pp-csq,' in CSS
    assert 'button.pp-csq:disabled { cursor: default; }' in CSS


@pytest.mark.parametrize('selector, floor', [
    ('.pp-cpick__key {', '44px'),
    ('.pp-cpick__search {', '44px'),
])
def test_the_pickers_own_controls_clear_the_touch_floor(selector, floor):
    """`.pp-cpick__key` is the commit action for contract-first mode and was a 20px box with a 10px label;
    `.pp-cpick__search` inherited `.stg-input`'s ~36.6px, and the donor file carries that exact fix with its
    reasoning. Both are under the design system's 44px floor."""
    start = CSS.index(selector)
    rule = CSS[start:CSS.index('}', start)]
    assert 'min-height: %s' % floor in rule


# ── round-2: holes found in round-1's fixes ───────────────────────────────────────────────────────

def test_a_press_during_the_closing_animation_does_not_commit():
    """The dialog stays displayed and interactive for the 180ms of `cpickOut`, so an offer pressed inside that
    window wrote, toasted and reloaded after a dismissal the hunter believed had cancelled it. This is the
    seventh thing `list-detail.js` does that the first port of its close routine left behind."""
    body = JS[JS.index('function assign('):JS.index('function offerConfirmation(')]
    assert 'if (!stillOpen()) { return; }' in body


def test_the_loading_message_is_written_a_frame_after_the_dialog_opens():
    """Fixing the ORDER was necessary and not sufficient. `showModal()` plus a synchronous `load()` put the
    live region into the tree already holding "Loading...", and the initial content of a newly-rendered region
    is not announced -- so opening the picker stayed silent. `ToastManager` defers its own announcement for
    exactly this reason."""
    assert "window.setTimeout(function () { load(openKey, ''); }, 0);" in JS


def test_reset_clears_the_search_layout_class_and_the_catchup_copy():
    """`reset()`'s job is that nothing from the last square survives, and three things did: the rows
    container's `--search` modifier, and the catch-up title and note. None was visible today (the catch-up
    block is `hidden`, and only one renderer touches the class) which is exactly why it would have stayed
    wrong."""
    body = JS[JS.index('function reset()'):JS.index('grid.addEventListener')]
    assert "els.rows.classList.remove('pp-cpick__rows--search');" in body
    assert "els.catchupTitle.textContent = '';" in body
    assert "els.catchupNote.textContent = '';" in body
    assert 'dismissed = false;' in body, 'a new open must clear the previous dismissal'


def test_the_sheet_announces_which_square_it_opened():
    """`aria-labelledby` points at the title, but nothing announces that element changing -- so the dialog's
    announced name stayed "Choose a game" and a screen-reader user never learned which square was open."""
    assert "say(panel.label + ': ' + els.sub.textContent);" in JS


def test_a_disabled_square_stops_lifting_under_the_cursor():
    """Browsers DO apply `:hover` to a disabled button. A just-completed square is disabled by the JS and then
    waits a couple of seconds for the page to re-render it as a `<div>`, and through that window it kept the
    hover lift the JS had just taken away."""
    assert 'button.pp-csq:not(:disabled):hover' in CSS
    assert 'button.pp-csq:hover {' not in CSS_CODE


def test_the_lazy_threshold_matches_the_grids_widest_row():
    """`loading="lazy"` costs preload-scanner priority, which only matters for what is on screen at once. The
    widest a single grid gets is 7 columns, so the threshold has to be at least 7 -- it was 21 (three rows of
    seven, costing a phone fifteen fetches for row-seven images) and then 6, which made the top-right cell of
    the first row lazy on desktop.

    THIS WAS CALLED `..._the_first_grid_row_is_never_lazy_at_any_breakpoint`, and that stopped being true when
    the squares were grouped into shelves. A jobs run's shelves begin at indexes 0, 5, 10, 15 and 20, so the
    first row of shelves 2-5 IS lazy. What the threshold still pins is the relationship between it and the
    widest row a grid can draw, which is what the number was chosen from -- hence the rename rather than a
    deletion."""
    partial = (ROOT / 'templates' / 'challenges' / 'partials' / '_square_body.html').read_text(encoding='utf-8')
    # `card.index`, not `forloop.counter0`: a loop counter restarts per discipline shelf, so the
    # threshold would never be reached and every cover would load eagerly.
    assert 'card.index >= 7' in partial
    # OVER THE CODE, not the commentary -- the comment explaining that `forloop` is gone contains the word.
    assert 'forloop' not in _template_code(partial), (
        'the partial must not depend on a loop it may be rendered outside'
    )
    # AND THE GRID'S OWN WIDEST TRACK COUNT, scoped to the rule that sets it. `repeat(7, minmax(0, 1fr))`
    # appears TWICE in this stylesheet now -- once on `.pp-csq-shelf` and once on `.pp-csq-grid` -- so the
    # bare `in CSS` form could be satisfied by the shelf while the grid drifted to 8, which is the exact
    # drift it was written to catch.
    assert '.pp-csq-grid { grid-template-columns: repeat(7, minmax(0, 1fr)); } }' in CSS


# ── owner feedback, 2026-09-28 ─────────────────────────────────────────────────────────────────────

def test_a_write_no_longer_reloads_the_page():
    """THE OWNER FILLED A RUN AND COUNTED THE RELOADS. Two versions of this existed -- a 900ms timer whose
    toast never finished, then a 2.8s one that did -- and both were defensible on the same ground: a filled
    square needs cover art the reply did not carry, so a client composing it would be a second renderer free
    to drift from the template.

    The reply now carries `html` for the one square that changed, rendered by the SAME partial the page used.
    One renderer, no navigation."""
    assert 'if (slot.html) { square.innerHTML = slot.html; }' in JS
    # `TOAST_MS` existed only so a reload timer could wait for the toast.
    assert 'TOAST_MS' not in JS_CODE
    # NO PER-SQUARE RELOAD. Exactly one survives and it is gated on the RUN finishing -- see `_reloads`.
    assert len(_reloads()) == 1
    # GATED, not merely present: the reload must sit inside the run-finishing branch, close enough to it
    # that no other statement can have come between. Measured in characters because the alternative is
    # embedding a newline in the assertion, which is how the last two attempts at this broke.
    gate = 'if (slot.is_complete) {'
    assert gate in JS_CODE
    # Widened from 120 characters: the branch now also writes the session marker that stops the entrance
    # replaying the celebration from zero after the reload. Still tight enough that no unrelated statement
    # fits, which is the property being held.
    assert 0 < JS_CODE.index('location.reload') - JS_CODE.index(gate) < 500
    # And the marker is set BEFORE the reload, or it could not be read on the way back in.
    assert JS_CODE.index('sessionStorage.setItem(INTRO_DONE') < JS_CODE.index('location.reload')


def test_the_swapped_square_markup_comes_from_the_server():
    """`innerHTML` is otherwise forbidden in this file, so the one place it is used has to be unmistakably
    server-rendered markup rather than anything composed from data."""
    assert '_square_html' in (ROOT / 'challenges' / 'views.py').read_text(encoding='utf-8')
    assert 'def card_for(' in (ROOT / 'challenges' / 'services' / 'slot_render.py').read_text(encoding='utf-8')


def test_a_single_square_spells_out_the_whole_sentence():
    """In A-Z a game fits exactly one letter, and a lone pill reading "S" looked like a choice among options
    that do not exist. The owner asked for the wording spelled out."""
    assert "var single = row.keys.length === 1;" in JS
    # THE SENTENCE EITHER WAY, enabled or not. Stripping it from a disabled button left a lone "S" -- exactly
    # the bare pill this change existed to remove -- when the DISABLED STATE is what says "not possible".
    assert "pick.textContent = single ? 'Add this game to ' + keyLabel : keyLabel;" in JS
    # Several squares keep the pills, with a lead-in so they read as answers to a question.
    assert "main.appendChild(lead('Add this game to:'));" in JS
    # SIZED TO ITS TEXT. It was `width: 100%` on the reasoning that it is the row's only action, which turned
    # a short phrase into a bar across the whole panel and read heavier than what it does.
    sentence = CSS[CSS.index('.pp-cpick__key--sentence {'):]
    assert 'width: 100%' not in sentence[:sentence.index('}')]


def test_a_finished_game_is_marked_in_the_search_results():
    """The server has been sending `is_completed_by_you` all along -- one indexed query over the page -- and
    only one narrow branch read it, so a search result gave no hint that placing it would complete the square
    immediately. The chip is the house primitive, never DaisyUI's badge."""
    assert "if (row.is_completed_by_you) { head.appendChild(chip('Finished', 'success')); }" in JS
    assert "'bd-chip bd-chip--' + tone" in JS
    assert 'badge' not in JS_CODE
    # ON THE TITLE'S LINE, at the end of it. Stacked underneath it read as a separate fact about the row
    # rather than part of its heading, and spent a line of height on every finished result.
    assert "head.className = 'pp-cpick__row-head';" in JS
    assert '.pp-cpick__row-head {' in CSS
    # `min-width: 0` is what lets the clamped name shrink instead of pushing the chip out of the row.
    assert '.pp-cpick__row-head .pp-cpick__row-name { flex: 1 1 auto; min-width: 0; }' in CSS


def test_an_occupied_square_warns_before_it_is_replaced():
    """Picking a game for a square that already holds one replaced it silently, and the search panel says
    nothing about the rest of the run -- so it was easy to do by accident. Two warnings now: the button is
    warning-toned and names what it would replace, and a confirm asks."""
    assert "pick.classList.add('pp-cpick__key--taken');" in JS
    assert "swap(' (replaces ' + occupant + ')')" in JS
    assert '.pp-cpick__key--taken' in CSS
    # THE GUARD'S SHAPE, not just the copy. Asserting the sentence was present passed with the condition
    # stubbed to `false` -- the words existed and gated nothing, which is the version of this test that
    # reassures without protecting.
    # THE GUARD'S SHAPE, not just the copy. Asserting the sentence was present passed with the condition
    # stubbed to `false` -- the words existed and gated nothing.
    assert 'if (!occupant) { assign(row.slug, key, false, pick); return; }' in JS
    # THE QUESTION AND THE COST, split. It was one run-on sentence; the consequence is now its own line, so
    # the pin follows both halves rather than the sentence that joined them.
    assert "'Put ' + row.name + ' in ' + keyLabel + '?'" in JS
    assert "already has ' + occupant + ', and it would be replaced.'" in JS
    # The safe answer is NAMED after what it keeps, per the house recipe -- not "Cancel".
    assert "'Keep ' + occupant" in JS


def test_the_replace_warning_needs_the_servers_filled_map():
    """The occupant's name comes from the run's own slots, which the panel already had in memory -- so this
    costs nothing and is a snapshot of what the square displays."""
    views = (ROOT / 'challenges' / 'views.py').read_text(encoding='utf-8')
    picker = (ROOT / 'challenges' / 'services' / 'picker.py').read_text(encoding='utf-8')
    assert "'filled': filled" in picker
    assert "'filled': panel['filled']," in views
    assert "var occupant = (panel.filled || {})[key];" in JS


def test_the_run_finishing_is_the_only_thing_that_reloads():
    """THE ONE THING REMOVING THE RELOAD BROKE, found by asking what the reload had been masking.

    A reload corrects everything the client failed to update, so taking it out exposes whatever was never
    being updated. `is_complete` was sent by the server and read by nobody: filling the LAST square left the
    header with no "Finished" chip, no read-only note, and a picker that should have stopped shipping. The
    run's completion is a change of MODE rather than of one square, and patching it client-side would be the
    second renderer this whole design avoids -- so it reloads, once per run.
    """
    assert 'if (slot.is_complete) {' in JS_CODE
    assert len(_reloads()) == 1


def test_a_finished_game_cannot_be_placed_from_the_search_panel():
    """It is still worth FINDING -- so the row renders, the chip says why, and the squares it fits are shown
    and disabled.

    The ordinary path refuses a finished game outright. The two rules that DO lift it, the hatch and the
    first-run importer, are offered inside the square's own panel in the block that warns the square will
    lock -- which is where that decision belongs, with its confirmation.

    AND NO COPY PROMISING THE EXCEPTION. Whether a rule lifts it is per-slot and per-hunter, so answering it
    for every search result would cost a query each; a note saying "open the square to use it" would be a
    promise this panel cannot keep.
    """
    assert 'var finished = !!row.is_completed_by_you;' in JS
    assert 'pick.disabled = finished;' in JS
    assert '.pp-cpick__key:disabled' in CSS
    # And the replace warning is suppressed, because there is no press for it to warn about.
    assert 'if (occupant && !finished) {' in JS


def test_both_confirmation_surfaces_follow_the_house_confirm_recipe():
    """`.stg-confirm__row` (account deletion in Settings) is the pattern: two buttons, the SAFE one first and
    FOCUSED, and named after what it preserves rather than "Cancel" -- so somebody who reads only the buttons
    still knows what each does. Focusing the destructive one turns a stray Enter into the thing the prompt
    exists to prevent.

    ASSERTED FOR BOTH SURFACES. The question is asked in a row in the search panel and in the sheet's foot for
    the grids, and the recipe is the reason either is safe -- so a second surface that skipped it would be a
    second, unsafe way to ask the same irreversible question."""
    row = JS[JS.index('function askInRow('):JS.index('function offerConfirmation(')]
    assert 'row.appendChild(keep);' in row
    assert row.index('row.appendChild(keep);') < row.index('row.appendChild(go);'), 'safe answer first'
    assert 'keep.focus();' in row

    # The foot's two buttons come from the template, so the ORDER is asserted there.
    tpl = _template_code(
        (ROOT / 'templates' / 'challenges' / 'challenge_detail.html').read_text(encoding='utf-8'))
    assert tpl.index('data-cpick-ask-keep') < tpl.index('data-cpick-ask-go'), 'safe answer first'
    foot = JS[JS.index('function askInFoot('):]
    foot = foot[:foot.index('\n        }') + 1]
    assert 'els.askKeep.focus();' in foot

    assert '.pp-cpick__ask-keep' in CSS and '.pp-cpick__ask-go' in CSS


def test_both_confirmation_surfaces_return_focus_to_what_was_pressed():
    """A native confirm did this for free; a built one has to do it on purpose, or a keyboard user who answers
    "keep" lands at the top of the document.

    AND NEITHER RESTORES IT ON THE DESTRUCTIVE ANSWER, for the same reason in both: `assign` disables the
    button it was handed and re-enables it afterwards, so focus returned to a control that is about to be
    disabled is focus lost to the document."""
    row = JS[JS.index('function askInRow('):JS.index('function offerConfirmation(')]
    assert 'if (restoreFocus && anchor && anchor.focus && document.contains(anchor)) { anchor.focus(); }' in row
    assert "keep.addEventListener('click', function () { close(true); say('Nothing changed.'); });" in row
    assert "go.addEventListener('click', function () { close(false); onGo(); });" in row

    # The foot's version, whose restore lives in `closeFootAsk` and is called with `true` from Keep only.
    close_foot = JS[JS.index('function closeFootAsk('):JS.index('function askInFoot(')]
    assert 'if (restoreFocus && anchor.focus && document.contains(anchor)) { anchor.focus(); }' in close_foot
    assert 'closeFootAsk(true);' in JS_CODE          # Keep, and Escape
    assert 'closeFootAsk(false);' in JS_CODE         # the write, and every teardown


def test_escape_answers_whichever_prompt_is_open_rather_than_the_sheet():
    """The narrower thing wins, which is what somebody pressing it expects -- otherwise a prompt they were
    reading takes the whole picker with it.

    DECIDED IN ONE PLACE, AND NOT VIA `keydown`. The inline prompt used to listen for a keydown and call
    `stopPropagation`, which could never have stopped the sheet: a modal `<dialog>` turns Escape into a
    `cancel` event dispatched on the dialog, not a keydown that bubbles to it. Whether it worked at all rested
    on the browser suppressing the close request because a keydown was cancelled -- and that listener never
    called `preventDefault()`. Both prompts now publish how to dismiss them and the `cancel` handler chooses,
    which needs no assumption about key handling at all.
    """
    # No prompt handles Escape itself any more.
    assert "e.key === 'Escape'" not in JS_CODE, 'Escape is the cancel handler\'s decision, in one place'
    assert 'openPromptClose = function () { close(true); };' in JS_CODE
    body = JS[JS.index("addEventListener('cancel', function (e) {"):]
    body = body[:body.index('\n        });') + 1]
    assert 'footAsking()' in body and 'openPromptClose' in body


def test_a_re_render_drops_any_open_prompt():
    """A prompt is anchored to a row, and both renderers replace the rows wholesale -- so one left behind
    would be a question about a game that is no longer on screen."""
    assert 'function dropPrompts()' in JS
    for renderer in ('function renderSlotPanel(panel) {', 'function renderSearchPanel(panel) {'):
        after = JS[JS.index(renderer):]
        assert 'dropPrompts();' in after[:200], '%s does not drop prompts' % renderer


def test_the_prompt_spans_the_row_grid():
    """The rows are a 2-3 column grid on the slot panel, so without this a prompt would render as one narrow
    cell beside the offers it is asking about."""
    ask = CSS[CSS.index('.pp-cpick__ask {'):]
    assert 'grid-column: 1 / -1;' in ask[:ask.index('}')]


def test_the_script_binds_to_the_board_and_not_to_one_grid():
    """A jobs run draws FIVE grids, one per discipline shelf. `querySelector('.pp-csq-grid')` returns the
    FIRST -- so the click delegation would have covered Combat and left the other four shelves dead, and
    `labelFor`/`applySlot` would have searched only that shelf for the square they had just written.

    Found by mutation: swapping the selector back broke nothing in the suite, because every other test about
    the board checks the MARKUP rather than what the script reads."""
    assert "document.querySelector('.pp-csq-board')" in JS
    assert "querySelector('.pp-csq-grid')" not in JS_CODE
    # And the board is what carries the id the URLs are built from.
    assert "grid.getAttribute('data-challenge-id')" in JS


def test_the_template_stripper_keeps_code_that_shares_a_line_with_a_comment():
    """THE HELPER IS NOW LOAD-BEARING for several absence assertions, and its first version had the very hole
    it exists to close: it dropped whole LINES, so markup sharing a line with a comment disappeared from the
    text a `not in` assertion inspects -- and such an assertion cannot fail on code it never sees."""
    kept = _template_code('<a href="x">{% comment %}why{% endcomment %}</a>')
    assert 'href="x"' in kept and '</a>' in kept
    assert 'why' not in kept

    # A block spanning lines, with a note argument, and an inline `{# #}` -- all three forms.
    kept = _template_code(
        '{% comment "note" %}\nforloop\n{% endcomment %}\n<b>real</b>{# forloop #}')
    assert '<b>real</b>' in kept
    assert 'forloop' not in kept

    # Two separate blocks must not swallow the code between them.
    kept = _template_code('{% comment %}a{% endcomment %}KEEP{% comment %}b{% endcomment %}')
    assert 'KEEP' in kept


def test_the_shelf_counter_moves_when_a_square_completes():
    """THE ONE COUNTER A WRITE DID NOT MOVE. The header tally and the horizon are both patched after a
    placement and the square gets its ring -- but the discipline shelf kept reading "0 of 5 done" until a
    reload, and per-discipline progress is the entire justification for the label area existing.

    Counted off the DOM rather than from the reply, which describes one slot and knows nothing about
    disciplines."""
    assert "closest('.pp-csq-shelf')" in JS_CODE
    assert "querySelectorAll('.pp-csq--done')" in JS_CODE
    assert "pp-csq-shelf__sub" in JS_CODE
    # Inside `applySlot`, not somewhere that runs on load only.
    body = JS_CODE[JS_CODE.index('function applySlot('):]
    body = body[:body.index('\n        }') + 1]
    assert "closest('.pp-csq-shelf')" in body


def test_one_gap_declaration_feeds_both_grids():
    """THE NO-RESIZE CONSTRAINT RESTED ON TWO COPIES OF `12px`. The shelf's 7-column gap and the cards' own
    gap have to be equal or the derivation stops holding, and they were independent declarations -- changing
    the grid's `md:` gap would have GROWN every card by 1.6px with nothing to notice it, because the shelf
    still had 7 tracks (131.43 -> 133.03 at 1024: a narrower inner gap leaves more width for the same five
    cards, so the first version of this sentence had the right magnitude and the wrong sign). A comment
    claimed the arithmetic was "derived rather than hardcoded" while it was exactly hardcoded."""
    assert '--csq-gap: 8px' in CSS_CODE
    # `var(--csq-gap, <fallback>)` in both consumers. The fallback is each rule's own breakpoint value, so a
    # grid or shelf somehow rendered outside `.pp-csq-board` gets a sane gap instead of computing `normal`.
    assert 'gap: var(--csq-gap, 12px)' in CSS_CODE
    assert 'gap: var(--csq-gap, 8px)' in CSS_CODE
    # Neither grid may carry its own literal gap any more.
    shelf = CSS_CODE[CSS_CODE.index('.pp-csq-shelf:not(.pp-csq-shelf--plain) {'):]
    assert 'gap: 12px' not in shelf[:shelf.index('}')]


def test_the_search_buttons_glyph_is_built_after_its_text_and_in_the_svg_namespace():
    """TWO WAYS TO RENDER NOTHING, both of which this change hit or nearly hit.

    Assigning `textContent` REMOVES every child, so an icon appended before that line is silently discarded --
    the first version of this change did exactly that and rendered no icons at all. And an SVG element built
    through the HTML parser lands in the XHTML namespace and draws nothing, which is why the glyph is
    assembled with `createElementNS` rather than from a markup string.
    """
    text_at = JS_CODE.index("pick.textContent = single ? 'Add this game to '")
    glyph_at = JS_CODE.index('pick.insertBefore(jobIcon(')
    assert text_at < glyph_at, 'the glyph must be inserted after the text that would erase it'

    body = JS_CODE[JS_CODE.index('function jobIcon('):]
    body = body[:body.index('\n        }') + 1]
    assert "createElementNS(NS, 'svg')" in body
    assert "createElementNS(NS, 'use')" in body
    assert 'innerHTML' not in body


def test_the_glyph_references_the_sprite_rather_than_carrying_path_data():
    """One sprite serves the grid and the picker. Inlining Lucide paths into JavaScript would be a second copy
    of the registry, drifting from `job_icons._ICONS` the first time a glyph is corrected."""
    assert "'#jobicon-' + name" in JS_CODE
    assert '<path' not in JS_CODE


def test_the_discipline_tint_loses_to_the_replace_warning():
    """SAME SPECIFICITY, so the later rule wins and the order is the whole mechanism. A square that already
    holds a game is warning-toned because pressing it replaces something; a decorative discipline tint must
    not be what a hunter sees instead of that warning."""
    assert CSS_CODE.index('.pp-cpick__key--job {') < CSS_CODE.index('.pp-cpick__key--taken {')


def test_a_disabled_job_button_keeps_its_discipline_under_the_cursor():
    """Without this rule, `.pp-cpick__key:disabled:hover` paints a disabled job button the generic primary
    tint instead of its discipline.

    IT WINS ON SOURCE ORDER, NOT SPECIFICITY, and this docstring said the opposite: that the generic rule
    "outspecifies the tint whatever the order". Both selectors are one class plus two pseudo-classes --
    (0,3,0) each -- so they tie and the later one applies. Worse, the CSS comment beside the rule had ALREADY
    been corrected on exactly this point in the same round; this was the wrong version, resurrected into a
    test. If it were true the fix would be impossible rather than order-dependent.
    """
    assert '.pp-cpick__key--job:disabled:hover' in CSS_CODE
    # The ordering the fix actually rests on.
    assert (CSS_CODE.index('.pp-cpick__key:disabled:hover')
            < CSS_CODE.index('.pp-cpick__key--job:disabled:hover'))


def test_the_tint_itself_is_pinned_not_just_the_glyph():
    """THE TWO LINES THAT ARE THE FEATURE were unpinned. An audit deleted the class and the `--disc` write and
    the whole suite still passed: the buttons would have rendered as plain cyan pills carrying a glyph, which
    is half the change silently gone. The existing pins covered the sprite reference and the DOM order -- the
    mechanics -- and nothing covered the result."""
    assert "pick.classList.add('pp-cpick__key--job')" in JS_CODE
    assert "pick.style.setProperty(" in JS_CODE
    assert "'var(--disc-' + atom.disc_slug + ', var(--pp-primary))'" in JS_CODE
    # Both inside the `if (atom)` branch, so an A-Z letter gets neither.
    block = JS_CODE[JS_CODE.index('var atom = (panel.key_atoms || {})[key];'):]
    block = block[:block.index('pick.textContent')]
    assert "classList.add('pp-cpick__key--job')" in block
    assert 'setProperty(' in block


def test_the_glyph_is_sized_because_the_svg_carries_no_dimensions():
    """`jobIcon` sets no `width`/`height` attributes -- only `viewBox` -- so with no CSS rule the glyph falls
    back to the UA's default replaced-element size inside the pill. The server-rendered callers escape this by
    passing a Tailwind class; the JS-built one has only this rule."""
    assert '.pp-cpick__key svg { width: 15px; height: 15px; flex: none; }' in CSS_CODE
    # And the element genuinely has no intrinsic size to fall back on.
    body = JS_CODE[JS_CODE.index('function jobIcon('):]
    body = body[:body.index('\n        }') + 1]
    assert "setAttribute('width'" not in body
    assert "setAttribute('height'" not in body


def test_the_replace_warning_wins_the_hover_state_too():
    """WHERE THE FIRST VERSION OF THIS WAS WRONG, and the test that claimed to cover it looked at the wrong
    pair of rules. `.pp-cpick__key--job:not(:disabled):hover` is 0-3-0 (one class, `:not(:disabled)`, `:hover`)
    and `.pp-cpick__key--taken:hover` was 0-2-0 -- and specificity beats source order, so hovering a taken JOB
    button painted it the discipline fill while its text and border stayed amber. The one moment the
    replacement warning exists for was the one moment it lost.

    `:not(:disabled)` on the taken rule levels the specificity, and it is later in source, so it wins."""
    assert '.pp-cpick__key--taken:not(:disabled):hover' in CSS_CODE
    assert '.pp-cpick__key--taken:hover' not in CSS_CODE, (
        'the 0-2-0 form loses to the job tint on specificity whatever the order'
    )
    # Later in source than the job hover, which is the other half of the fix.
    assert (CSS_CODE.index('.pp-cpick__key--job:not(:disabled):hover')
            < CSS_CODE.index('.pp-cpick__key--taken:not(:disabled):hover'))


def test_the_gap_overrides_live_on_the_board_not_on_a_grid():
    """THE MECHANISM, which the sibling test above does not reach. A custom property flows DOWN, so the
    shelf's 7-column grid can only read `--csq-gap` from an ancestor -- move the breakpoint overrides onto
    `.pp-csq-grid` and the shelf stays on the base value at every width while the cards change, which is the
    silent resize the property was introduced to prevent. The previous assertions passed under exactly that
    move."""
    assert '.pp-csq-board { --csq-gap: 10px; }' in CSS_CODE
    assert '.pp-csq-board { --csq-gap: 12px; }' in CSS_CODE
    # And no grid declares its own, which would shadow the board's for its own subtree only.
    grid = CSS_CODE[CSS_CODE.index('.pp-csq-grid {'):]
    assert '--csq-gap:' not in grid[:grid.index('}')]


def test_the_shelf_counter_writes_the_same_sentence_the_server_does():
    """TWO RENDERERS FOR ONE STRING, which is the shape this file exists to guard. The server writes
    "N of M done" and the client rewrites it after a placement; if either changes shape, a shelf's label
    silently changes wording the moment a square is filled."""
    tpl = _template_code(
        (ROOT / 'templates' / 'challenges' / 'challenge_detail.html').read_text(encoding='utf-8'))
    assert '{{ group.done }} of {{ group.total }} done' in tpl
    assert "+ ' of ' +" in JS_CODE
    assert "+ ' done'" in JS_CODE


# ── the confirmation's second surface ─────────────────────────────────────────────────────────────

def test_the_grid_panels_ask_in_the_foot_and_the_search_panel_asks_in_a_row():
    """THE OWNER'S NOTE. A full-width prompt row reads well among the search panel's full-width rows, and
    reflows the square picker's multi-column grid of cover cards -- pushing the card being decided about far
    enough to clip its own art. The foot cannot reflow anything: it is `flex: none` outside the scrolling
    body, so it is always visible and the grid holds still."""
    dispatch = JS_CODE[JS_CODE.index('function ask(anchor,'):]
    dispatch = dispatch[:dispatch.index('\n        }') + 1]
    # THE CONDITION, not the order of the two calls. Asserting only that `askInRow` appears before `askInFoot`
    # was satisfied by a guard inverted to `!contains(...)` -- which sends the search panel to the foot and the
    # grids to the rows, the exact opposite of the design, with the suite green.
    guard = "if (host && list && list.classList.contains('pp-cpick__rows--search')) {"
    assert guard in dispatch, 'the POSITIVE `--search` test is what selects the row form'
    after = dispatch[dispatch.index(guard):]
    # The row form is reached only inside that guard; the foot is what everything else gets.
    assert after.index('askInRow(') < after.index('askInFoot(')
    assert '.pp-cpick__footask' in CSS_CODE
    assert '.pp-cpick__foot--asking' in CSS_CODE


def test_the_foot_prompt_never_falls_through_to_writing_without_asking():
    """THE HOLE THIS CLOSED. The old prompt called `onGo()` outright when it could not find an `<li>` to anchor
    to -- so a DOM it did not expect meant an irreversible, permanently locking write with no confirmation at
    all. The foot needs no anchor, so it is the fallback and there is nothing left to fall through to."""
    dispatch = JS_CODE[JS_CODE.index('function ask(anchor,'):]
    dispatch = dispatch[:dispatch.index('\n        }') + 1]
    assert 'onGo();' not in dispatch, 'the dispatcher must never perform the action instead of asking'
    assert 'fail(' in dispatch, 'with no surface at all it must report, not write'


def test_the_ringed_card_does_not_move_the_grid():
    """The ring is what says WHICH offer the foot is asking about, now that the question is not beside it. A
    border alone would change the card's box and shift the grid by a pixel, undoing the point of moving the
    prompt out of the rows."""
    # TWO SELECTORS NOW: `.pp-cpick__row:hover` is 0-2-0 and sets `border-color`, so hovering the ringed
    # card reverted its border to the primary tint while the ring's box-shadow stayed -- one affordance coming
    # apart under the cursor. The `:hover` spelling levels it.
    assert '.pp-cpick__row--asking,\n.pp-cpick__row--asking:hover {' in CSS_CODE
    rule = CSS_CODE[CSS_CODE.index('.pp-cpick__row--asking,'):]
    rule = rule[:rule.index('}')]
    assert 'box-shadow:' in rule
    assert 'border-width' not in rule and 'padding' not in rule


def test_the_foot_restores_the_clear_button_to_what_it_was():
    """`Clear this square` is hidden on an empty square and shown on a filled one, so unhiding it when the
    prompt closes would offer to clear a square with nothing in it. The prior state is recorded, not guessed."""
    assert "els.ask.setAttribute('data-clear-was'" in JS_CODE
    assert "els.ask.getAttribute('data-clear-was') !== 'shown'" in JS_CODE


def test_a_re_render_closes_the_foot_prompt_too():
    """A bar left up after a re-render is a question about the last square, still holding the callback that
    would write to it. `dropPrompts` owns both surfaces for that reason."""
    body = JS_CODE[JS_CODE.index('function dropPrompts()'):]
    body = body[:body.index('\n        }') + 1]
    assert 'closeFootAsk(false);' in body
    assert 'openPromptClose = null;' in body
    # And it must not sweep the template's own markup away: the foot's bar shares the element classes.
    assert "querySelectorAll('li.pp-cpick__ask')" in body


# ── the entrance, and the bar it actually moves ───────────────────────────────────────────────────

def test_the_bar_lookup_is_scoped_to_this_runs_own_horizon():
    """THE BUG THE OWNER REPORTED. `.pp-horizon` is a shared primitive and the page renders two; the nav's
    hidden sync bar is first in the document, so a bare `document.querySelector('.pp-horizon')` moved THAT and
    the run's bar sat still until a reload. One definition, scoped to the hook."""
    assert "document.querySelector('[data-cpick-horizon] .pp-horizon')" in JS_CODE
    assert "querySelector('.pp-horizon')" not in JS_CODE, (
        'a bare `.pp-horizon` lookup finds the nav bar first'
    )
    # And every reader of it goes through the one function.
    assert 'horizon: runHorizon(),' in JS_CODE


def test_the_entrance_runs_for_every_viewer_not_only_the_owner():
    """`boot` returns early when there is no dialog, which is every visitor -- so an entrance written after
    that guard would only ever play for the one hunter who can edit the run. The Hall of Fame is people
    reading somebody else's finished run."""
    body = JS_CODE[JS_CODE.index('function boot(first) {'):]
    body = body[:body.index('var dialog = document.getElementById')]
    assert 'if (first) { intro(); }' in body, 'the entrance must run before the dialog guard, and once'
    # ONCE PER LOAD. `onPageReady` calls `fn(false)` on every history restore, and both precedents for this
    # entrance gate it on that flag. Replaying it is the visible cost; the invisible one is that a restore
    # replays markup captured at push time, so a snapshot taken mid-animation would bake `0%` in for good.
    assert 'function boot(first)' in JS_CODE
    assert 'PP.onPageReady(boot)' in JS_CODE


def test_the_entrance_fills_the_bar_across_a_paint_not_inside_one_frame():
    """THE BUG THE OWNER SAW: the tally ticked and the bar did not move.

    A transition needs a committed "from" value. `void el.offsetWidth` gives it one on a REVEAL, where the
    element has already painted at its served value -- which is why `utils.js` uses that form. At LOAD there is
    no painted state, so setting 0 and the target inside a single frame leaves the browser one computed value
    and nothing to animate. The load-time precedents (`franchise-detail.js`, `company-detail.js`) wait a frame,
    and say so: "double-rAF so the 0% width lands before the transition to the real value".

    Pinned as the ORDER 0 -> rAF -> rAF -> target, because that sequence is the fix; a single rAF is the
    plausible wrong version."""
    body = JS_CODE[JS_CODE.index('function intro() {'):]
    body = body[:body.index('\n    }') + 1]
    assert 'PP.countUp(tally, 900);' in body
    assert "setProperty('--horizon-progress', '0%');" in body
    assert 'void hz.offsetWidth' not in body, 'the reveal form does not animate at load'
    zero = body.index("setProperty('--horizon-progress', '0%')")
    target = body.index("setProperty('--horizon-progress', target)")
    assert zero < target
    # Two nested frames between them, not one.
    between = body[zero:target]
    assert between.count('requestAnimationFrame') == 2, 'a single frame is not enough at load'


def test_the_shelf_head_is_a_surface_on_careers_own_recipe():
    """`.jdoss__band` and `.jsheet__disc` are the same concept -- a discipline, its icon, its `--disc` name and
    a stat -- and both put it on a surface. The shelf label sat on the bare page background, making this a third
    treatment of one idea.

    THE BAND IS ON THE HEAD, NOT THE SHELF, and that is load-bearing: the shelf's width feeds the 7-column
    grid, so a border or padding there would shave a fraction off every track and shrink all 25 cards."""
    head = CSS_CODE[CSS_CODE.index('.pp-csq-shelf__head {'):]
    head = head[:head.index('}')]
    assert 'border-left: 3px solid var(--disc' in head
    assert 'linear-gradient(180deg' in head
    assert 'padding:' in head

    # AND THE SHELF ITSELF TAKES NO BORDER OR PADDING, which is the constraint. Asserted on the selector
    # that EXISTS: an earlier version looped over two spellings and `continue`d past any that were absent,
    # and `.pp-csq-shelf {` is not one of them -- so half the loop was dead while the test read as guarding
    # two rules.
    shelf = CSS_CODE[CSS_CODE.index('.pp-csq-shelf:not(.pp-csq-shelf--plain) {'):]
    shelf = shelf[:shelf.index('}')]
    assert 'padding' not in shelf, 'the shelf must not pad: it would resize every card'
    assert 'border' not in shelf, 'the shelf must not border: it would resize every card'
    # And no OTHER rule dresses the shelf either, whatever it is spelled as. `--plain` is the A-Z wrapper and
    # is allowed nothing of its own; the head is where the band lives.
    assert '.pp-csq-shelf--plain { display' not in CSS_CODE
    for line in CSS_CODE.splitlines():
        if '.pp-csq-shelf' in line and '__' not in line and ('padding:' in line or 'border:' in line):
            raise AssertionError('a shelf rule adds box to the grid container: %s' % line.strip())


def test_the_shelf_label_uses_the_same_flat_disc_colour_career_does():
    """`.jdoss__name` and `.jsheet__head-name` are flat `var(--disc)`. The shelf used a paler
    `color-mix(..., #fff)` because it sat on the bare page background -- with the band behind it that is the
    same combination Career ships, so the five disciplines read as one palette across both pages."""
    for block in ('.pp-csq-shelf__icon {', '.pp-csq-shelf__title {'):
        rule = CSS_CODE[CSS_CODE.index(block):]
        rule = rule[:rule.index('}')]
        assert 'color: var(--disc' in rule, '%s should be flat --disc' % block
        assert '#fff' not in rule, '%s still lightens the discipline' % block


def test_the_entrance_leaves_the_bar_alone_under_reduced_motion():
    """THE HALF THAT IS EASY TO DROP. `horizon.css` disables the fill's transition under `reduce`, so writing 0
    and then the target would not animate -- and writing 0 is not harmless, because the served state IS the
    final state and there is nothing to restore it from. `utils.js`'s own reveal says the same in the same
    words. The count-up needs no branch: `countUp` checks for itself."""
    body = JS_CODE[JS_CODE.index('function intro() {'):]
    body = body[:body.index('\n    }') + 1]
    assert "matchMedia('(prefers-reduced-motion: reduce)')" in body
    # The guard is AFTER the count-up (which self-checks) and BEFORE anything touches the bar.
    assert body.index('PP.countUp(tally, 900);') < body.index('if (reduce) { return; }')
    assert body.index('if (reduce) { return; }') < body.index('runHorizon()')


def test_a_write_ticks_the_tally_from_its_old_value():
    """FROM THE NUMBER ALREADY ON SCREEN, not from zero: a tally that restarts at 0 on every placement reads as
    the page reloading, which is the thing removing the reload was for. `company-list.js` does the same when
    its filtered total changes."""
    body = JS_CODE[JS_CODE.index('function applySlot('):]
    body = body[:body.index('\n        }') + 1]
    assert 'els.tally.dataset.countup = String(slot.completed_count);' in body
    assert 'PP.countUp(els.tally, 600, { from: isNaN(before) ? 0 : before });' in body
    # And a fallback that still shows the right number if the utility is missing.
    assert 'els.tally.textContent = slot.completed_count;' in body


def test_the_entrance_does_not_replay_after_the_run_finished_reload():
    """THE CELEBRATION PLAYED TWICE. When the last square completes, the tally ticks to its final number, the
    bar fills, and then the page reloads -- and the entrance reset the tally to 0 and counted it up again while
    the bar refilled. The second showing starts with a visible snap back to zero, which is the opposite of what
    that moment should feel like.

    A session marker rather than a querystring, so a finished run's URL stays clean and shareable. Consumed on
    read, so an ordinary later visit still animates. Every access is wrapped: storage throws in a private
    window and can come back empty in previews, and the fall-through direction (animate) is the harmless one.
    """
    assert "var INTRO_DONE = 'pp-challenge-intro-done';" in JS_CODE
    body = JS_CODE[JS_CODE.index('function intro() {'):]
    body = body[:body.index('\n    }') + 1]
    assert 'sessionStorage.getItem(INTRO_DONE)' in body
    assert 'sessionStorage.removeItem(INTRO_DONE)' in body
    assert 'return;' in body
    # Guarded, both ways.
    assert body.count('try {') >= 1 and body.count('catch (e)') >= 1
    # Read before anything animates, or it would skip nothing.
    assert body.index('sessionStorage.getItem') < body.index('PP.countUp')


def test_the_tallys_previous_value_comes_from_the_attribute_not_the_rendered_text():
    """TWO FAILURES ON ONE LINE, and the attribute avoids both.

    The rendered text can be MID-ANIMATION -- the entrance's own count-up runs for 900ms after load -- so
    parsing it starts the new tick from a number that was never real. And `countUp` formats with
    `toLocaleString()`, so a four-figure count renders `1.000` on de-DE and `1 000` on fr-FR (a narrow no-break
    space), where stripping commas and parsing yields 1. Neither is reachable at 25 or 26 squares; the
    attribute is the value either way and costs nothing.
    """
    body = JS_CODE[JS_CODE.index('function applySlot('):]
    body = body[:body.index('\n        }') + 1]
    assert "parseInt(els.tally.dataset.countup || '', 10)" in body
    assert 'els.tally.textContent' not in body.split('var before')[0], 'the old value must not be parsed text'
    assert ".replace(/,/g, '')" not in body, 'a comma strip cannot cover every locale separator'


def test_a_stale_count_up_cannot_leave_the_old_number_on_screen():
    """`countUp` HAS NO CANCELLATION: each call owns its own frame loop and writes ITS captured target when it
    finishes. So the entrance's 900ms loop, started at load, can outlive a 600ms write loop started 100ms later
    and leave the OLD number on screen permanently -- with `data-countup` and the progress bar both saying
    something else.

    Reasserting the truth after the longest window is cheaper and more honest than reimplementing cancellation
    inside a shared utility that four other pages depend on. Guarded on `data-countup` still matching, so a
    LATER write's value is never stomped by an earlier write's timer.
    """
    body = JS_CODE[JS_CODE.index('function applySlot('):]
    body = body[:body.index('\n        }') + 1]
    assert 'window.setTimeout(' in body
    assert "els.tally.dataset.countup === String(slot.completed_count)" in body
    assert 'els.tally.textContent = String(slot.completed_count);' in body


# ── the confirmation audit's findings ─────────────────────────────────────────────────────────────

def test_every_dismissal_tears_the_question_down():
    """FOUR ROUTES SHUT THIS SHEET -- the close button, the backdrop, a swipe (`dismissableSheet` calls
    `dialog.close()` itself, bypassing our own `close()`), and Escape -- and only Escape answered an open
    prompt. The others left the warning bar up through the 180ms exit, the ring on a card nobody could see, and
    `openPromptClose` pointing at a detached node, which then swallowed the NEXT Escape and announced "Nothing
    changed." about a question that was never on screen.

    ON THE NATIVE `close` EVENT rather than inside our `close()`, because that is the only place that can
    promise it: the event fires however the dialog shut, including the routes that never call our function and
    including an Escape whose `cancel` could not be cancelled."""
    assert "dialog.addEventListener('close', function () { dropPrompts(); });" in JS_CODE


def test_reset_uses_the_all_surfaces_teardown():
    """`reset()` deletes the rows, which destroys the inline prompt's element -- but it called `closeFootAsk`
    only, leaving `openPromptClose` holding a closer for a node that no longer exists. `dropPrompts`' own
    comment names exactly that hazard; only one of the two teardown paths obeyed it."""
    body = JS_CODE[JS_CODE.index('function reset() {'):]
    body = body[:body.index('\n        }') + 1]
    assert 'dropPrompts();' in body


def test_only_one_inline_prompt_can_be_open_at_a_time():
    """A search result carries up to six key pills and none is disabled while a prompt is open, so pressing a
    second occupied square inserted a SECOND prompt -- both with live Go buttons, and `openPromptClose`
    overwritten so Escape answered the last one CREATED rather than the one holding focus. That was a
    regression from centralising Escape: each prompt used to own its own key handler. `askInFoot` gets this
    free by owning a single bar; the row surface had to be told."""
    body = JS_CODE[JS_CODE.index('function askInRow('):]
    body = body[:body.index('var prompt = document.createElement')]
    assert 'dropPrompts();' in body


def test_escape_is_attempted_on_the_key_as_well_as_on_cancel():
    """`cancel` IS NOT RELIABLY CANCELABLE, which is the assumption the previous design swapped in without
    saying so. Under the close-watcher rules `<dialog>` follows, `cancel` is only cancelable while the page
    holds transient user activation, and it does not fire again for a second close request until fresh
    activation arrives. Escape grants none and the window expires in seconds -- so a hunter who READS this
    prompt, which is its whole purpose, may find Escape closing the sheet.

    So: a best-effort `keydown` first, and the `close` listener to guarantee the STATE is clean either way.
    Nothing in the file asserts that cancelling the key suppresses the close request, because nothing in the
    repo can verify it."""
    assert "dialog.addEventListener('keydown', function (e) {" in JS_CODE
    body = JS_CODE[JS_CODE.index("dialog.addEventListener('keydown', function (e) {"):]
    body = body[:body.index('}, true);')]
    assert "e.key !== 'Escape'" in body
    assert 'e.preventDefault();' in body
    # It must not fire when there is no prompt, or it would break the sheet's own Escape.
    assert '!footAsking() && !openPromptClose' in body


def test_the_question_reaches_the_live_region():
    """Focus moves to a button labelled "Keep Sly Cooper", so a screen reader was told nothing about what was
    being confirmed -- including that the square locks permanently. `aria-describedby` on the CARD was the
    first attempt and describes a path nobody takes: the reader is on the button, and `closeFootAsk` strips the
    attribute before focus ever returns to the card."""
    body = JS_CODE[JS_CODE.index('function askInFoot('):]
    body = body[:body.index('els.askKeep.focus();')]
    # The cost line is announced with the question, or a screen reader hears half of it.
    assert "say(message + (cost ? ' ' + cost : ''));" in body


def test_a_detached_card_is_never_ringed():
    """A 409 can land after a re-render -- press an offer, then type in the search box -- and the button it came
    from is detached by then. Ringing it dresses a node nobody can see and points `aria-describedby` into
    nowhere. The foot still asks; there is simply no card to mark."""
    assert 'if (footAnchor && document.contains(footAnchor)) {' in JS_CODE


def test_the_dispatcher_checks_every_element_the_foot_bar_needs():
    """`askInFoot` dereferences the text and both buttons, and the listeners are wired behind
    `els.askKeep && els.askGo`. Gating the dispatcher on the container alone could raise a bar with no working
    answers -- a question only Escape could dismiss."""
    assert 'if (els.ask && els.askText && els.askKeep && els.askGo) {' in JS_CODE


def test_the_two_answers_never_stack_at_mobile_width():
    """THE OWNER'S NOTE. At 375px the sheet is 345px and the foot leaves 297px for the pair, while "Leave it
    empty" beside "Use it and lock the square" wants ~366px at its natural size -- and `flex-wrap: wrap` decides
    by natural size and wraps BEFORE it shrinks anything, so the two answers stacked. The replace pair
    ("Keep <game>" / "Replace it") fits, which is why only some prompts showed it.

    `nowrap` plus `min-width: 0` is the fix: the buttons shrink and the long label wraps to two lines inside its
    own button, which is where a sentence should break. Not a media query -- the behaviour is right at every
    width, and a breakpoint would only move where it broke."""
    row = CSS_CODE[CSS_CODE.index('.pp-cpick__ask-row {'):]
    row = row[:row.index('}')]
    assert 'flex-wrap: nowrap;' in row
    assert 'flex-wrap: wrap;' not in row

    buttons = CSS_CODE[CSS_CODE.index('.pp-cpick__ask-keep,'):]
    buttons = buttons[:buttons.index('}')]
    assert 'min-width: 0;' in buttons, 'without this the floor is min-content and the row overflows instead'
    # Mobile-first: the tighter padding is the base and `md:` restores it.
    assert 'padding: 8px 12px;' in buttons
    assert '.pp-cpick__ask-go { padding: 8px 16px; }' in CSS_CODE


# ── the sheet's third mode ────────────────────────────────────────────────────────────────────────

def test_the_sheet_knows_which_of_three_panels_it_is_showing():
    """`load()` encoded this in its own argument -- a key meant the square's pool, a null key meant the search --
    which worked while there were two. The history panel is reachable from two places and has to survive a
    keystroke in the search box, so the mode is a thing the sheet knows rather than a shape of the last call."""
    assert "var mode = 'slot';" in JS_CODE
    # SET BY THE RENDERERS, not the loaders. It used to be assigned before the fetch, so a request that FAILED
    # left `mode` naming a panel that never arrived -- and the next keystroke filtered a pool that was not on
    # screen. Each renderer owns it now, so it can only describe what a reader is looking at.
    for renderer, expected in (('function renderSlotPanel(panel) {', "mode = 'slot';"),
                               ('function renderSearchPanel(panel) {', "mode = 'search';"),
                               ('function renderHistoryPanel(panel) {', "mode = 'history';")):
        body = JS_CODE[JS_CODE.index(renderer):]
        body = body[:body.index('\n        }') + 1]
        assert expected in body, '%s does not claim its own mode' % renderer
    # And neither loader may set it.
    for loader in ('function load(key, query) {', 'function loadHistory(query) {'):
        body = JS_CODE[JS_CODE.index(loader):]
        body = body[:body.index('\n        }') + 1]
        assert 'mode =' not in body, '%s sets the mode before its reply lands' % loader


def test_typing_in_history_mode_filters_history():
    """Falling through to the catalogue search would answer a different question from the one the panel is
    asking, and an empty box here means "all of my history" rather than "back to the square"."""
    body = JS_CODE[JS_CODE.index('var run = function () {'):]
    body = body[:body.index('};')]
    assert "if (mode === 'history') { loadHistory(term); return; }" in body
    # Before the empty-box branch, or an empty box would leave history mode.
    assert body.index("mode === 'history'") < body.index('if (!term)')


def test_the_mode_does_not_survive_a_reset():
    """A sheet reopened from a square must not still be in history mode -- typing would filter history under a
    square's title, and the note would explain a rule the panel is no longer applying."""
    body = JS_CODE[JS_CODE.index('function reset() {'):]
    body = body[:body.index('\n        }') + 1]
    assert "mode = 'slot';" in body
    # `leaveHistory()` is what clears the note, the placeholder and the toggle's pressed state. Asserting
    # `showNote('')` here pinned the old inline copy of those three lines rather than the property.
    assert 'leaveHistory();' in body


def test_both_other_panels_undress_the_history_mode():
    """The note, the placeholder and the toggle's pressed state all belong to one panel, so they cannot survive
    into one they do not describe. One function, called by both, rather than three lines copied twice."""
    assert 'function leaveHistory()' in JS_CODE
    # SCOPED TO EACH RENDERER'S OWN BODY, not to an arbitrary character window. A 400-char slice passed for
    # `renderSlotPanel` and failed for `renderSearchPanel` purely because the call sits further down in the
    # second one -- a measurement of line lengths rather than of the property.
    for renderer in ('function renderSlotPanel(panel) {', 'function renderSearchPanel(panel) {'):
        body = JS_CODE[JS_CODE.index(renderer):]
        body = body[:body.index('\n        }') + 1]
        assert 'leaveHistory();' in body, '%s does not undress history mode' % renderer


def test_the_history_panel_names_the_date_it_measures_from():
    """"Since you joined" is not something a hunter can check; a date is. `TimeFormatter.absolute` is the same
    formatter the catch-up rows use, so the two blocks cannot disagree about how a date reads."""
    body = JS_CODE[JS_CODE.index("function renderHistoryPanel("):]
    body = body[:body.index('function showNote(')]
    # THE LEAD STRING, not `TimeFormatter.absolute` -- that also matches the per-row date further down, so the
    # loose form stayed green with the date removed from the note entirely.
    assert "'Since you joined Platinum Pursuit on ' + joined" in body
    assert 'showNoteBlock(' in body
    # ONE-TIME FIRST, in every place the importer is described (owner, 2026-09-28): a head start somebody
    # expects again on their second run is a disappointment we wrote ourselves.
    assert 'A one-time head start, for your first A-Z Challenge only.' in body
    # And the asymmetry the owner asked to have explained, in the place it is met.
    assert 'Job Coverage runs do not use the importer.' in body


def test_the_in_sheet_toggle_is_not_a_dead_end():
    """A hunter who arrived through a square and then pressed this should be able to get back to that square's
    pool without closing the sheet."""
    body = JS_CODE[JS_CODE.index('els.histSwitch.addEventListener'):]
    body = body[:body.index('});')]
    assert "if (mode === 'history') {" in body
    assert 'load(openKey' in body


def test_the_page_door_opens_the_sheet_with_no_square_in_mind():
    """The flow it exists for: a hunter who knows their library covers half the alphabet should not have to pick
    a letter first. So it clears `openKey` -- otherwise the toggle would offer to go "back" to a square the
    hunter never opened."""
    body = JS_CODE[JS_CODE.index("querySelectorAll('[data-cpick-history]')"):]
    body = body[:body.index('});\n            });') + 4]
    assert 'reset();' in body
    assert 'openKey = null;' in body
    assert "loadHistory('');" in body


def test_the_confirmations_safe_answer_names_what_it_keeps():
    """"LEAVE IT EMPTY" IS A PROMISE ABOUT A SQUARE THAT MAY NOT BE EMPTY.

    The history panel is the first surface to advertise `replaces X`, and its offers go straight to `assign` --
    so the only question a hunter saw said the square would be left EMPTY while it held a game the other answer
    would silently destroy forever. The house convention is that the safe button is named after what it KEEPS,
    which is exactly what makes a two-button prompt readable.

    Read off the BOARD rather than plumbed through `assign`'s four call sites: the board is the truth at the
    moment the question is asked, and it is the same frozen snapshot the square itself shows.
    """
    assert 'function occupantFor(key)' in JS_CODE
    assert "querySelector('.pp-csq__name')" in JS_CODE
    body = JS_CODE[JS_CODE.index('function offerConfirmation('):]
    body = body[:body.index('\n        }') + 1]
    assert "occupant ? 'Keep ' + occupant : 'Leave it empty'" in body
    # And the message says what it costs, which the server's sentence cannot know.
    assert 'would be replaced.' in body


def test_the_history_toggle_is_withheld_when_there_is_nowhere_to_go_back_to():
    """THE DEAD END IN THE PRIMARY FLOW. Arriving through the PAGE door means no square was ever opened, so the
    toggle's `load(openKey)` was `load(null)` -- an empty catalogue search answering "Type at least two
    letters", a panel the hunter never asked for and could not get out of except by pressing the toggle again.

    Withheld rather than relabelled: with nowhere to return to there is nothing for it to do, and the sheet's
    close button is the way out."""
    body = JS_CODE[JS_CODE.index('function renderHistoryPanel('):]
    body = body[:body.index('function showNote(')]
    assert 'els.histSwitch.hidden = (openKey === null);' in body
    # And it comes back for a panel that does have a square behind it.
    leave = JS_CODE[JS_CODE.index('function leaveHistory()'):]
    leave = leave[:leave.index('\n        }') + 1]
    assert 'els.histSwitch.hidden = false;' in leave


def test_the_page_door_focuses_the_sheet_and_defers_its_load():
    """BOTH HALVES THE SQUARE DOOR DOES DELIBERATELY, and this one was missing both. `showModal`'s own focusing
    steps land on the close button -- the least useful control in the sheet -- and content already present in a
    live region when it enters the tree is not announced, which is why the square door wraps its load in a
    zero-delay timeout and spends four lines explaining it."""
    body = JS_CODE[JS_CODE.index("querySelectorAll('[data-cpick-history]')"):]
    body = body[:body.index('});\n            });') + 4]
    assert 'dialog.focus();' in body
    assert 'window.setTimeout(function () { loadHistory(' in body


def test_the_toggle_drops_an_open_confirmation():
    """Nothing disables the toggle while the foot is asking, so without this a hunter could switch panels and
    then press "Use it and lock the square" for an offer that is no longer on screen."""
    body = JS_CODE[JS_CODE.index('els.histSwitch.addEventListener'):]
    body = body[:body.index('});')]
    assert 'dropPrompts();' in body
    assert body.index('dropPrompts();') < body.index("mode === 'history'")


def test_an_empty_history_panel_does_not_claim_the_hunter_has_nothing():
    """THE SERVER COMPUTES `scan_truncated` AND THE CLIENT HAS TO USE IT. It was computed, documented in three
    places as the thing that stops the panel lying, and then dropped by the view -- so the client said "Nothing
    here yet" for a hunter whose importable games simply sat past the window. The data fix is worthless without
    the delivery."""
    # EXACTLY ONCE. It was serialized twice (a patch script run a second time), and a duplicate dict key is
    # legal Python -- so the mutation that deleted one copy left the other and this assertion passed while
    # pinning nothing. Counting is what makes it a pin.
    views = (ROOT / 'challenges' / 'views.py').read_text(encoding='utf-8')
    assert views.count("'scan_truncated': panel['scan_truncated'],") == 1
    body = JS_CODE[JS_CODE.index('function renderHistoryPanel('):]
    body = body[:body.index('function showNote(')]
    assert 'panel.scan_truncated' in body
    assert 'Nothing in the first batch' in body
    # Split across two source lines now that the note is a list; matched in halves rather than as one literal.
    assert 'Search for a ' in body and 'game to look further' in body


def test_the_history_panel_does_not_advertise_rows_it_cannot_reach():
    """`N+ ready to place` promised a further page on a panel with NO pagination, and `more` can be true purely
    because the window filled -- which says nothing about how many offers exist. Say what is on screen and how
    to look further."""
    body = JS_CODE[JS_CODE.index('function renderHistoryPanel('):]
    body = body[:body.index('function showNote(')]
    assert "'+ ready to place'" not in body
    assert "' ready to place'" in body
    assert 'search to look further' in body


def test_the_history_note_says_the_rule_is_an_instant_not_a_day():
    """The rule is `when > joined_at` on full timestamps, and the copy named only a DATE -- so a game finished at
    breakfast on the day somebody signed up in the evening is excluded while satisfying the sentence as written.
    A hunter hunting for it would think the list was broken."""
    body = JS_CODE[JS_CODE.index('function renderHistoryPanel('):]
    body = body[:body.index('function showNote(')]
    assert 'earlier that same day' in body


def test_a_closed_history_panel_announces_why():
    """The reason is the ENTIRE content of a closed panel, and it was written into a plain `<p>` that is not a
    live region and receives no focus -- so a screen reader heard "Not available on this run" and never learned
    why."""
    body = JS_CODE[JS_CODE.index('function renderHistoryPanel('):]
    body = body[:body.index('function showNote(')]
    assert "say(els.sub.textContent + '. ' + why);" in body


# ── the design pass, 2026-09-28 ───────────────────────────────────────────────────────────────────

def test_the_importer_says_it_is_one_time_everywhere_it_is_described():
    """OWNER'S NOTE: a head start somebody expects to get again on their second run is a disappointment we
    wrote ourselves. Every surface that describes the importer has to say "first" before it says what it does --
    the page hint, the panel's own facts, and the explanation when it is spent."""
    tpl = _template_code(
        (ROOT / 'templates' / 'challenges' / 'challenge_detail.html').read_text(encoding='utf-8'))
    assert 'A one-time head start' in tpl
    assert 'first A-Z Challenge only' in tpl

    body = JS_CODE[JS_CODE.index('function renderHistoryPanel('):]
    body = body[:body.index('function showNote(')]
    assert 'A one-time head start, for your first A-Z Challenge only.' in body

    spent = JS_CODE[JS_CODE.index('function historyClosedNote('):]
    spent = spent[:spent.index('\n        }') + 1]
    assert 'one-time head start for your FIRST A-Z Challenge' in spent


def test_the_history_note_is_a_lead_and_a_list_not_a_paragraph():
    """FOUR FACTS DECIDE WHETHER A HUNTER PRESSES ANYTHING HERE, and prose hides that they are four. The date is
    the only one they cannot infer, so it leads; the rest are a list. "What would a top-tier product do with
    this text" was the owner's question, and the answer was not a paragraph."""
    assert 'function showNoteBlock(lead, facts)' in JS_CODE
    assert '.pp-cpick__note-lead' in CSS_CODE
    assert '.pp-cpick__note-facts' in CSS_CODE
    body = JS_CODE[JS_CODE.index('function renderHistoryPanel('):]
    body = body[:body.index('function showNote(')]
    assert 'showNoteBlock(' in body
    # The facts are separate strings, not one concatenated block.
    facts = body[body.index('showNoteBlock('):]
    facts = facts[:facts.index(');')]
    assert facts.count("',") >= 3, 'the facts must be separate lines, not one paragraph'


def test_the_confirmation_separates_the_question_from_its_cost():
    """A confirmation asks ONE thing and costs ANOTHER, and running them together made the cost the tail of a
    sentence nobody finishes -- on the only irreversible action in the feature. The question leads in display
    type; the consequence sits under it in the warning tone."""
    assert 'data-cpick-ask-cost' in (
        (ROOT / 'templates' / 'challenges' / 'challenge_detail.html').read_text(encoding='utf-8'))
    assert '.pp-cpick__ask-cost' in CSS_CODE
    # Both surfaces carry it: the foot sets the element, the row builds one.
    foot = JS_CODE[JS_CODE.index('function askInFoot('):]
    foot = foot[:foot.index('\n        }') + 1]
    assert 'els.askCost.textContent = cost' in foot
    row = JS_CODE[JS_CODE.index('function askInRow('):JS_CODE.index('function occupantFor(')]
    assert "costLine.className = 'pp-cpick__ask-cost';" in row
    # And the server's sentence is the COST, not the headline.
    conf = JS_CODE[JS_CODE.index('function offerConfirmation('):]
    conf = conf[:conf.index('\n        }') + 1]
    assert "'Put ' + data.contract_name + ' in ' + labelFor(key) + '?'" in conf
    assert conf.index("'Put '") < conf.index('data.error')


def test_a_history_card_weights_its_three_facts_and_cannot_wrap_them():
    """IT WAS ONE DIM RUN-ON LINE -- `Goes in A . finished Mar 3, 2024 . replaces Alan Wake` -- which wrapped to
    three or four rows in a ~128px column, made every card a different height, and buried the destructive fact
    at the end in the quietest type on the card.

    Three facts, three weights: the destination is a chip (what the panel is scanned for), the date is quiet and
    loses its day (the month is the recognisable part and the day was most of the wrap), and the replacement is
    the only one warned, because it is the only one that costs anything.
    """
    assert 'function historyLabel(row, when)' in JS_CODE
    for cls in ('.pp-cpick__dest-key', '.pp-cpick__dest-when', '.pp-cpick__dest-swap'):
        assert cls in CSS_CODE, '%s is not styled' % cls
    body = JS_CODE[JS_CODE.index('function renderHistoryPanel('):]
    body = body[:body.index('function showNote(')]
    # MONTH AND YEAR, no day -- SCOPED TO THE ROWS LOOP. The note's lead legitimately keeps its day: the join
    # date is the rule's anchor and wants precision, where a card's date only has to be recognisable. An
    # earlier version of this assertion banned the pattern across the whole function and failed on the lead.
    cards = body[body.index('panel.rows.forEach('):]
    assert "{ year: 'numeric', month: 'short' }" in cards
    assert "day: 'numeric'" not in cards, 'the day is what made these labels wrap'
    # The swap chip truncates rather than wrapping, or one long game name makes a row taller than its
    # neighbour -- which is the thing being fixed.
    swap = CSS_CODE[CSS_CODE.index('.pp-cpick__dest-swap {'):]
    swap = swap[:swap.index('}')]
    assert 'text-overflow: ellipsis' in swap and 'white-space: nowrap' in swap


def test_the_offer_cards_in_a_row_are_the_same_height():
    """The `<li>`s stretch because they are grid items, but the BUTTON inside did not fill its cell -- so a card
    with a wrapped label stood taller than the one beside it and the shorter one floated with dead space
    under it."""
    # `:not(.pp-cpick__ask)` IS PART OF THE PIN, not noise. `askInRow` inserts its confirmation as a SIBLING
    # `<li class="pp-cpick__ask">` in this same container, and the bare selector (0,1,1) beat `.pp-cpick__ask`
    # (0,1,0) -- turning the prompt into a flex ROW with the question, the cost and both answers side by side
    # in a third of the width each. Dropping the exclusion brings that back.
    assert '.pp-cpick__rows > li:not(.pp-cpick__ask) { display: flex; }' in CSS_CODE
    assert '.pp-cpick__rows > li:not(.pp-cpick__ask) > .pp-cpick__row { height: 100%; }' in CSS_CODE
    assert '.pp-cpick__rows > li { display: flex; }' not in CSS_CODE, (
        'the unscoped form flattens the inline confirmation'
    )


# ── two rules this stylesheet and these templates argue for, now enforced rather than remembered ───

def test_no_type_in_this_stylesheet_falls_below_the_12px_floor():
    """THE FLOOR IS ARGUED FOR THREE TIMES IN THIS FILE and was broken twice in one afternoon -- a 10.5px chip
    carrying the one destructive fact on a card, and an 11.5px page hint that is the whole explanation of an
    irreversible feature. Both were written directly beneath comments rejecting those exact values.

    A GENERAL GUARD, not a pin on the two classes that broke it. The rule is file-wide, so the next sub-floor
    size should fail without anybody remembering to add a test for it. `rem` is resolved at the project's 16px
    root; a genuine exception (an uppercase micro-label, say) should be added here deliberately with its reason
    rather than by lowering the bound.
    """
    import re as _re

    offenders = []
    for value, unit in _re.findall(r'font-size:\s*([0-9.]+)(px|rem)', CSS):
        px = float(value) * (16.0 if unit == 'rem' else 1.0)
        if px < 12.0:
            offenders.append('%s%s (%.1fpx)' % (value, unit, px))
    assert offenders == [], 'type below the 12px floor: %s' % ', '.join(sorted(set(offenders)))


def test_every_list_in_the_challenge_templates_declares_its_role():
    """TAILWIND'S PREFLIGHT SHIPS `ol,ul,menu{list-style:none}`, and WebKit drops the implicit `list` role from a
    list styled that way -- so on iOS Safari, the dominant screen-reader pairing for a 375px-first page, a bare
    `<ul>` announces nothing at all. `display: flex` strips it in Chrome too, which one of these lists has.

    These templates state that rule in three separate comments and then shipped a fourth list without it. A
    general guard, so the next one cannot rely on somebody remembering.
    """
    import re as _re

    for name in ('challenge_detail.html', 'my_challenges.html'):
        path = ROOT / 'templates' / 'challenges' / name
        if not path.exists():
            continue
        body = _template_code(path.read_text(encoding='utf-8'))
        for tag in _re.findall(r'<ul\b[^>]*>', body):
            assert 'role="list"' in tag, '%s has a list with no role: %s' % (name, tag)
