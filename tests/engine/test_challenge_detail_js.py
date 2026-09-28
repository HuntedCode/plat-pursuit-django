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
    assert JS.count('if (writing) { return; }') == 2, 'assign and clear must both take the lock'
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


def test_the_first_grid_row_is_never_lazy_at_any_breakpoint():
    """`loading="lazy"` costs preload-scanner priority, which only matters for what is on screen at once. The
    widest the grid gets is 7 columns, so the threshold has to be at least 7 -- it was 21 (three rows of
    seven, costing a phone fifteen fetches for row-seven images) and then 6, which made the top-right cell of
    the FIRST row lazy on desktop."""
    partial = (ROOT / 'templates' / 'challenges' / 'partials' / '_square_body.html').read_text(encoding='utf-8')
    assert 'forloop.counter0 >= 7' in partial
    # And the grid's widest track count, so the two cannot drift apart silently.
    assert 'repeat(7, minmax(0, 1fr))' in CSS


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
    assert 0 < JS_CODE.index('location.reload') - JS_CODE.index(gate) < 120


def test_the_swapped_square_markup_comes_from_the_server():
    """`innerHTML` is otherwise forbidden in this file, so the one place it is used has to be unmistakably
    server-rendered markup rather than anything composed from data."""
    assert '_square_html' in (ROOT / 'challenges' / 'views.py').read_text(encoding='utf-8')
    assert 'def card_for(' in (ROOT / 'challenges' / 'services' / 'slot_render.py').read_text(encoding='utf-8')


def test_a_single_square_spells_out_the_whole_sentence():
    """In A-Z a game fits exactly one letter, and a lone pill reading "S" looked like a choice among options
    that do not exist. The owner asked for the wording spelled out."""
    assert "pick.textContent = single ? 'Add this game to ' + keyLabel : keyLabel;" in JS
    assert "var single = row.keys.length === 1;" in JS
    # Several squares keep the pills, with a lead-in so they read as answers to a question.
    assert "main.appendChild(lead('Add this game to:'));" in JS


def test_a_finished_game_is_marked_in_the_search_results():
    """The server has been sending `is_completed_by_you` all along -- one indexed query over the page -- and
    only one narrow branch read it, so a search result gave no hint that placing it would complete the square
    immediately. The chip is the house primitive, never DaisyUI's badge."""
    assert "if (row.is_completed_by_you) { main.appendChild(chip('Finished', 'success')); }" in JS
    assert "'bd-chip bd-chip--' + tone" in JS
    assert 'badge' not in JS_CODE


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
    assert 'if (occupant && !window.confirm(' in JS
    assert "already has ' + occupant + '." in JS
    # And the refusal has to stop the write, not merely be reachable.
    guard = JS[JS.index('if (occupant && !window.confirm('):]
    assert guard[:guard.index('assign(')].count('return;') == 1


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
