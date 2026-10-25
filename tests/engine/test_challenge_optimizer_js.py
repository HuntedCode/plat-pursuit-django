"""`challenge-optimizer.js` and its stylesheet, pinned by SOURCE TEXT (this project has no JS test runner).

Structural pins, like `test_challenge_detail_js.py`: each asserts a guard is PRESENT and says what its absence
costs. They cannot prove the guard works; they stop it being deleted while tidying.
"""
import re
from pathlib import Path

from challenges.optimizer_views import OPTIMIZER_STEPS
from tests.engine.test_challenge_detail_js import _code_only

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / 'static' / 'js' / 'challenge-optimizer.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'css' / 'components' / 'challenge-optimizer.css').read_text(encoding='utf-8')
TEMPLATE = (ROOT / 'templates' / 'challenges' / 'optimizer.html').read_text(encoding='utf-8')


def test_nothing_is_written_as_html():
    # Contract names are catalogue data that passes through staff hands; one escaping bug is an XSS on every
    # hunter who opens the page. The ONE exception is the started run's card, which the template engine renders
    # (auto-escaped) and the reply carries as `html`: one insertion, of server output only.
    code = _code_only(JS)
    assert 'innerHTML' not in code
    assert 'insertAdjacentHTML' not in code
    assert code.count('createContextualFragment(') == 1
    helper = JS[JS.index('function serverFragment'):]
    assert 'createContextualFragment(html' in helper[:helper.index('\n        }\n')]


def test_a_start_in_place_never_reloads_and_keeps_the_form_as_the_fallback():
    block = JS[JS.index('function startInPlace'):]
    block = block[:block.index('// ── planning')]
    assert 'e.preventDefault()' in block and 'form.dataset.startUrl' in block
    assert 'location.reload' not in block and 'form.submit()' not in block


def test_start_in_place_guards_double_submit_and_refreshes_the_page_state():
    start = JS[JS.index('function startInPlace'):JS.index('function swapCard')]
    assert 'if (!button || button.disabled) { return; }' in start and 'button.disabled = true;' in start
    # The tally, the hint/tagline and the stale must-have results all follow a new run.
    for call in ('recountOpen(true)', 'syncRunCopy()', 'clearSearch()'):
        assert call in start
    # The new card settles in rather than hard-cutting into place.
    assert 'settleIn(fresh);' in start


def test_apply_swaps_in_the_servers_fresh_step_one_cards():
    applied = JS[JS.index('function showApplied'):]
    assert 'swapCard(t, data.cards[t])' in applied[:applied.index('refreshOpenCounts(data.runs)')]


def test_the_script_and_the_server_agree_on_the_steps():
    # The template renders the rail and panels from OPTIMIZER_STEPS; the script walks its own list. A step added
    # to one and not the other strands the wizard on a panel the script never shows.
    keys = [step['key'] for step in OPTIMIZER_STEPS]
    match = re.search(r"var STEPS = \[([^\]]*)\]", JS)
    assert match and [k.strip(" '") for k in match.group(1).split(',')] == keys
    for key in keys:
        assert f'data-step="{key}"' in TEMPLATE


def test_the_sheet_close_is_guarded_like_the_pickers():
    # `animationend` bubbles and the ::backdrop's own exit fires on the dialog with `pseudoElement` set; without
    # both guards a descendant animation ends the close early. The timeout keeps a dropped event from
    # stranding the sheet open.
    close = JS[JS.index('function closeDialog('):JS.index('function wireDialogClose(')]
    assert 'e.target !== d || e.pseudoElement' in close
    assert re.search(r'd\._closeTimer = setTimeout\(', close)


def test_escape_gets_the_choreographed_exit():
    assert re.search(r"addEventListener\('cancel', function \(e\) \{ e\.preventDefault\(\); closeSheet\(\);", JS)


def test_a_swiped_sheet_closes_directly():
    # Handing `closeSheet` to dismissableSheet made a flicked sheet slide away, pop back, then exit twice
    # (the picker's own lesson).
    block = JS[JS.index('PP.dismissableSheet(dialog'):]
    block = block[:block.index('});')]
    assert 'dialog.close()' in block and 'closeSheet' not in block


def test_an_accepted_swap_always_re_solves():
    # A swap moves games; the plan above it must be redrawn around it before anything is applied.
    block = JS[JS.index('function swapRow'):JS.index('function swapChoice')]
    use = block[block.index("action('Use this swap'"):]
    assert use.index('change(function () {') < use.index('pinGame(swap.suggested.slug, chosen);')


def test_different_games_never_penalizes_a_locked_or_pinned_game():
    block = JS[JS.index('els.reroll.addEventListener'):]
    block = block[:block.index('els.apply.addEventListener')]
    assert '!isLocked(line)' in block and '!line.pinned' in block


def test_apply_sends_what_each_square_expects_to_replace():
    # Without `replacing`, the server cannot tell a swap from an open-square fill and skips every swap.
    block = JS[JS.index('els.apply.addEventListener'):]
    assert 'replacing:' in block[:block.index('PP.API.post(urls.apply')]


def test_async_replies_are_guarded_by_identity():
    # A slow reply to an earlier request must not overwrite a newer plan, search or sheet.
    assert JS.count('if (mine !== seq) { return; }') >= 2
    assert 'if (mine !== searchSeq) { return; }' in JS
    assert 'if (mine !== altSeq || altView !== view) { return; }' in JS


def test_the_rail_never_becomes_a_sideways_scroll_strip():
    # A scroll strip widens the mobile layout viewport (only `contain: layout size` stops it).
    rail = CSS[CSS.index('.pp-cplan__rail {'):]
    rail = rail[:rail.index('}')]
    assert 'flex-wrap: wrap' in rail and 'overflow-x' not in rail


def test_a_tweak_that_re_solves_rolls_back_on_failure():
    # Without the rollback, a failed re-plan leaves tweaks that disagree with the plan on screen ("Not this one"
    # marked, the line still shown, and Apply still writing it).
    change = JS[JS.index('function change('):]
    change = change[:change.index('\n        }\n')]
    assert 'snapshot()' in change and 'restore(before)' in change
    assert 'if (opts.rollback) { opts.rollback(); }' in JS


def test_a_change_made_mid_request_is_queued_not_dropped():
    replan = JS[JS.index('function replan('):JS.index('function readyLine')]
    assert 'queued = Object.assign(' in replan and 'replan(next)' in replan


def test_busy_is_announced_on_the_review_not_the_root():
    # The root holds the status live region; a region inside an aria-busy subtree may stay silent.
    assert "els.review.setAttribute('aria-busy'" in JS
    assert "root.setAttribute('aria-busy'" not in _code_only(JS)


def test_only_lock_is_a_toggle():
    # `aria-pressed` on every action made "Not this one" and "Other options" announce as toggles.
    assert _code_only(JS).count('pressed:') == 1


def test_the_platform_chips_have_a_focus_ring_here():
    chips = CSS[CSS.index('.pp-cplan__chips {'):]
    assert '--pp-list-accent' in chips[:chips.index('}')]


def test_mobile_rail_labels_are_visually_hidden_not_removed():
    # The number is aria-hidden, so `display: none` on the label left every other step nameless.
    rail = CSS[CSS.index('.pp-cplan__rail-lbl {'):]
    assert 'display: none' not in rail[:rail.index('}')]


def test_a_pin_change_re_solves_only_while_the_review_is_showing():
    # During Edit answers the old plan still exists; re-solving it on a pin change planned against the answers
    # being replaced and slammed the wizard shut mid-edit.
    assert 'function reviewing() { return !!plan && !els.review.hidden; }' in JS
    pins = JS[JS.index('function renderPinned'):JS.index('function pinSay')]
    assert 'if (plan)' not in _code_only(pins)
    assert pins.count('if (reviewing())') == 2


def test_an_other_options_pick_closes_the_sheet_before_re_solving():
    # The sheet plays its exit while the re-plan starts; a re-plan landing inside the exit must not focus a line
    # on a page the open modal makes inert, so focus WAITS for the dialog's real close.
    block = JS[JS.index('function appendAlternatives'):JS.index('function renderAltTabs')]
    pick = block[block.index('var pick = function (row) {'):]
    assert pick.index('closeSheet();') < pick.index('change(function () {') < pick.index('pinGame(row.slug, squares);')
    focus = JS[JS.index('function restoreFocus('):JS.index('// ── rendering the plan')]
    assert "dialog.addEventListener('close', function () { restoreFocus(key, scroll); }, { once: true });" in focus


def test_other_options_shows_every_game_in_tabs_a_page_at_a_time():
    load = JS[JS.index('function loadAlternatives'):JS.index('function appendAlternatives')]
    # The line's other squares and its own game ride along; the tab and where the page starts.
    assert 'body.partners = view.partners.map(' in load and 'body.current = view.line.slug;' in load
    assert 'if (group) { body.group = group; }' in load and 'body.offset = append ? view.next : 0;' in load
    # Where the next page starts, from the page the server TOOK.
    assert 'view.next = data.offset + data.showing;' in load and 'view.more = !!data.more;' in load
    # A stale reply (an older page, another tab, a closed sheet) never draws.
    assert 'if (mine !== altSeq || altView !== view) { return; }' in load
    tabs = JS[JS.index('function renderAltTabs'):JS.index('function syncAltMore')]
    # Tabs only when the line holds two squares, each with its full count.
    assert 'alt.tabs.hidden = !view.partners.length;' in tabs
    assert 'altTabs(view).forEach(function (tab) {' in tabs
    names = JS[JS.index('function altTabs('):JS.index('function loadAlternatives')]
    # Each tab names its squares: the line's both, the opened one alone, the partner alone.
    assert "[['both', sq + ' + ' + other," in names and "['rest', sq + ' only'," in names
    assert "['partner', other + ' only'," in names
    assert 'n.textContent = String(counts[tab[0]]);' in tabs
    assert 'loadAlternatives(view, tab[0], false);' in tabs
    # Built ONCE per open: later replies only re-check and re-count, so a keyboard hunter keeps their radio.
    assert 'if (view.tabs) {' in tabs and 'syncAltTabs(view);' in tabs
    load = JS[JS.index('function loadAlternatives'):JS.index('function appendAlternatives')]
    catch = load[load.index('}).catch(function (err) {'):]
    assert 'syncAltTabs(view);' in catch   # a failed switch puts the tabs back on the tab still showing
    assert 'data-cplan-alt-tabs hidden' in TEMPLATE and 'data-cplan-alt-more hidden' in TEMPLATE


def test_the_next_page_loads_itself_as_the_hunter_scrolls():
    # The run picker's own pattern: an observer on Show more, and the re-check for a page too short to scroll.
    wire = JS[JS.index('if (dialog) {'):JS.index("alt.closeBtn.addEventListener('click', closeSheet);")]
    assert "alt.moreBtn.addEventListener('click', loadMoreAlternatives);" in wire
    assert 'new IntersectionObserver(function (entries) {' in wire and '.observe(alt.more);' in wire
    more = JS[JS.index('function loadMoreAlternatives'):JS.index('function keepAltFilling')]
    assert 'if (!view || !view.more || view.loading || !dialog.open) { return; }' in more
    fill = JS[JS.index('function keepAltFilling'):JS.index('function renderAltFoot')]
    assert 'if (spot.top - view.bottom < ALT_MORE_MARGIN) { window.requestAnimationFrame(loadMoreAlternatives); }' in fill
    assert 'keepAltFilling();' in JS[JS.index('function loadAlternatives'):JS.index('function appendAlternatives')]


def test_a_covering_pick_takes_every_square_of_the_line():
    # Pinning only the clicked square let a locked line (or an accepted swap) keep its old game on the other one.
    block = JS[JS.index('function appendAlternatives'):JS.index('function renderAltTabs')]
    # Where a pick goes, by tab: the whole line, the opened square, or the partner alone.
    assert "var squares = both ? [view.square].concat(view.partners)" in block
    assert ": view.group === 'partner' ? view.partners : [view.square];" in block
    assert 'change(function () { pinGame(row.slug, squares); }' in block
    assert "var both = view.group === 'both';" in block


def test_the_sheet_names_every_square_of_the_line_and_filters_to_your_badges():
    opener = JS[JS.index('function openAlternatives'):JS.index('//: The sheet as it stands')]
    assert ".map(function (s) { return s.label; }).join(' and ');" in opener
    # Off on every open; offered only when badges count.
    assert 'if (alt.badge) { alt.badge.checked = false; }' in opener
    assert "if (alt.filters) { alt.filters.hidden = !badgesCounted(submitted) || submitted.badge_weight === 'only'; }" in opener
    load = JS[JS.index('function loadAlternatives'):JS.index('function appendAlternatives')]
    assert 'if (badgeOnly) { body.badge_only = true; }' in load
    # A tab or filter load asks with the chip as it reads now; a next page with the filter already drawn.
    assert ': (!append && alt.badge ? alt.badge.checked : view.badgeOnly);' in load
    assert 'if (!append) { view.wanted = group; }' in load
    assert "'No game here counts toward your badges.'" in load
    wire = JS[JS.index('if (dialog) {'):JS.index("alt.closeBtn.addEventListener('click', closeSheet);")]
    # The tab ASKED for (a switch may still be loading), and the first tab with games if the filter empties it.
    assert 'loadAlternatives(altView, altView.wanted || altView.group, false,' in wire
    assert '{ badgeOnly: alt.badge.checked, fallback: true });' in wire
    # The filter and the tab change only when the reply lands; a failure puts the chip back.
    assert 'view.badgeOnly = badgeOnly;' in load and 'if (alt.badge) { alt.badge.checked = view.badgeOnly; }' in load
    assert "submitted.badge_weight === 'only'" in opener   # no chip where every game already counts
    assert 'data-cplan-alt-badge' in TEMPLATE and 'Counts toward your badges' in TEMPLATE


def test_a_swap_replacing_a_game_in_each_challenge_offers_both_or_one():
    row = JS[JS.index('function swapRow'):JS.index('function swapChoice')]
    # Every game it replaces is named, and the choice appears only when there are two.
    assert 'swap.replaces.forEach(function (r) {' in row and "'Replaces ' + (r.current.name" in row
    assert 'var choice = swap.replaces.length > 1 ? swapChoice(swap) : null;' in row
    assert 'pinGame(swap.suggested.slug, chosen);' in row
    choice = JS[JS.index('function swapChoice'):JS.index('function swapKey')]
    assert "[{ value: 'both', label: 'Both', squares:" in choice and "label: 'Only ' + r.square.label" in choice
    assert "tweaks.choices[name] : 'both';" in choice and 'input.checked = opt.value === kept;' in choice


def test_a_swap_choice_survives_a_redraw_that_is_not_a_re_plan():
    # The Letter/Job sort rebuilds every card; an "Only A" must not quietly become Both before Use this swap.
    choice = JS[JS.index('function swapChoice'):JS.index('function swapKey')]
    assert "input.addEventListener('change', function () { if (input.checked) { tweaks.choices[name] = opt.value; } });" in choice
    # A fresh plan (and Open my plan) starts every card on Both; Undo swap resets its own card.
    assert "declined: new Set(), choices: {} };" in JS[JS.index('function freshTweaks'):JS.index('// ── answers')]
    assert "declined: new Set(), choices: {} };" in JS[JS.index('function restoreTweaks'):JS.index('function sortedStrings')]
    undo = JS[JS.index("action('Undo swap'"):JS.index("action('Lock'")]
    assert "if (k.indexOf('cplan_swap_' + line.slug + '@') === 0) { delete tweaks.choices[k]; }" in undo


def test_a_declined_swap_half_stays_declined_until_the_swap_is_undone():
    # "Only C" would otherwise be offered the other half again by the very next re-solve.
    row = JS[JS.index('function swapRow'):JS.index('function swapChoice')]
    assert 'tweaks.declined.add(swapKey(swap.suggested.slug, r.square));' in row
    assert 'plan.swaps.map(offeredSwap).filter(Boolean)' in JS
    assert "declined: new Set(), choices: {} };" in JS[JS.index('function freshTweaks'):JS.index('// ── answers')]
    # Rolled back with the rest of a failed re-plan's tweaks, and present on a plan Open my plan restores (where
    # `offeredSwap` would otherwise call `.has` on undefined).
    snap = JS[JS.index('function snapshot()'):JS.index('function pinGame')]
    assert 'declined: new Set(tweaks.declined) };' in snap and 'tweaks.declined = s.declined;' in snap
    assert 'declined: new Set(), choices: {} };' in JS[JS.index('function restoreTweaks'):JS.index('function sortedStrings')]
    offered = JS[JS.index('function offeredSwap'):JS.index('function unplacedRow')]
    assert "!tweaks.declined.has(swapKey(swap.suggested.slug, r.square))" in offered
    assert 'if (!left.length) { return null; }' in offered
    undo = JS[JS.index("action('Undo swap'"):JS.index("action('Lock'")]
    assert "if (k.indexOf(line.slug + '@') === 0) { tweaks.declined.delete(k); }" in undo
    # Never sent: the server has no idea of it.
    sent = JS[JS.index('function payload'):JS.index('function summaryLine')]
    assert 'declined' not in sent and 'choices' not in sent


def test_pinning_a_game_clears_its_other_pins_in_the_same_runs():
    # The solver keeps one square pin per game per run; a second left a square empty and the game "unplaced".
    pin = JS[JS.index('function pinGame'):JS.index('function restoreTweaks')]
    assert "if (tweaks.squarePins[k] === slug && runs.indexOf(k.split('|')[0]) >= 0) {" in pin
    block = JS[JS.index('function appendAlternatives'):JS.index('function renderAltTabs')]
    assert 'change(function () { pinGame(row.slug, squares); }' in block


def test_the_job_view_groups_by_discipline_and_keeps_every_jobs_place():
    by_job = JS[JS.index('function renderByJob()'):JS.index('function squaresByKey(')]
    assert 'plan.job_groups.forEach(function (group) {' in by_job
    assert "if (byJob[key]) { rows.push(lineRow(byJob[key], 'jobs')); }" in by_job
    assert "blocked[key] ? 'No badge game fits' : 'Nothing on your terms fits'" in by_job
    assert "rows.push(slotRow(left[key], 'Leaving open', true));" in by_job
    # The run page's discipline tint, checked as a bare word before it becomes CSS.
    assert "if (/^[a-z-]+$/.test(group.slug || '')) {" in by_job
    assert "section('A-Z only', null, azOnly, lineRow);" in by_job
    # The job's own chip leads each card.
    row = JS[JS.index('function lineRow(line, lead)'):JS.index("(line.replaces || []).forEach(function (r) {")]
    assert "var ordered = lead === 'jobs' ? line.squares.slice().sort(" in row
    # Job squares are drawn in their disciplines, so Still open keeps only the letters there.
    assert "emptySection(jobView() ? 'az' : null);" in JS


def test_a_square_left_open_rides_the_tweaks_everywhere_they_go():
    fresh = JS[JS.index('function freshTweaks'):JS.index('// ── answers')]
    assert 'leftOpen: new Set(),' in fresh
    sent = JS[JS.index('function payload'):JS.index('function summaryLine')]
    assert 'left_open: Array.from(tweaks.leftOpen).map(function (sq) {' in sent
    snap = JS[JS.index('function snapshot()'):JS.index('function pinGame')]
    assert 'leftOpen: new Set(tweaks.leftOpen)' in snap and 'tweaks.leftOpen = s.leftOpen;' in snap
    restore = JS[JS.index('function restoreTweaks'):JS.index('function sortedStrings')]
    assert "new Set((t.left_open || []).map(function (e) { return e.type + '|' + e.key; }))" in restore
    assert 'left_open: body.tweaks.left_open };' in JS   # autosaved with the rest
    leave = JS[JS.index('function leaveOpen('):JS.index('function restoreTweaks')]
    # Beats a lock on that square.
    assert 'tweaks.leftOpen.add(sqKey(sq));' in leave and 'delete tweaks.squarePins[sqKey(sq)];' in leave
    plan_it = JS[JS.index('function planItAction('):JS.index('//: The stats as last shown')]
    assert "change(function () { tweaks.leftOpen.delete(sqKey(sq)); }, 'square:' + sqKey(sq));" in plan_it
    # The server's word on what is left open replaces the page's after every plan, and the page's own copy of the
    # autosave reads it from the REPLY (the previous plan is null on a first plan, and stale otherwise).
    assert 'tweaks.leftOpen = new Set((plan.left_open || []).map(sqKey));' in JS
    assert 'sentTweaks.left_open = (data.left_open || []).map(' in JS
    assert 'sentTweaks.left_open = (plan.left_open' not in JS


def test_focus_after_leave_open_or_plan_it_lands_on_that_square():
    focus = JS[JS.index('function restoreFocus('):JS.index('// ── rendering the plan')]
    assert "? els.sections.querySelector('[data-square=\"' + CSS.escape(key.slice(7)) + '\"]')" in focus
    assert "btn.setAttribute('data-square', sqKey(sq));" in JS[JS.index('function lineRow('):JS.index('function metaList(')]
    assert "btn.setAttribute('data-square', sqKey(sq));" in JS[JS.index('function planItAction('):JS.index('//: The stats')]
    foot = JS[JS.index('function renderAltFoot('):JS.index('function altList(')]
    assert "change(function () { if (old) { keepPick(old); } else { leaveOpen(sq); } }, 'square:' + sqKey(sq));" in foot


def test_step_one_asks_about_picked_games_only_while_an_included_run_has_one():
    picks = JS[JS.index('function pickedTotal()'):JS.index('function replanning()')]
    # Counted from each card's server count, only for the runs being planned.
    assert "if (box && box.checked) { total += parseInt(el.dataset.picked, 10) || 0; }" in picks
    assert 'els.picksQ.hidden = !total;' in picks
    # The answer is sent only while it is asked: with no picks, Replan would plan the same squares.
    assert "picks: picksAsked() ? radioValue('[data-cplan-picks]', 'keep') : 'keep'" in JS
    assert "badge_weight: a.badge_weight, picks: a.picks," in JS
    assert "setChecked('[data-cplan-picks]', [a.picks || 'keep']);" in JS
    # Kept in step whenever the cards change: an Include toggle, a Start, an Apply's re-rendered cards.
    assert "clearSearch(); syncPicks(); }" in JS
    assert JS.count('syncPicks();') >= 5
    # Open my plan re-ticks the runs with no change event: the question must follow before answers() reads it.
    apply_answers = JS[JS.index('function applyAnswers('):JS.index("root.querySelectorAll('[data-cplan-open-saved]')")]
    assert apply_answers.index("setChecked('[data-cplan-picks]'") < apply_answers.index('syncPicks();')
    html = open('templates/challenges/optimizer.html', encoding='utf-8').read()
    q = html[html.index('data-cplan-picks-q'):html.index('{# Step 2')]
    assert 'value="keep" checked data-cplan-picks' in q and 'value="replan" data-cplan-picks' in q
    assert 'value="fresh" data-cplan-picks' in q
    assert 'role="radiogroup" aria-labelledby="cplan-picks-label" aria-describedby="cplan-picks-hint"' in q
    assert 'id="cplan-picks-hint"' in q
    # The card is found by the DOM, never a selector built from data.
    assert "var card = el.closest('[data-cplan-run]');" in picks
    card = open('templates/challenges/partials/_optimizer_run_option.html', encoding='utf-8').read()
    assert 'data-cplan-picked-for="{{ r.type }}" data-picked="{{ r.picked }}"' in card


def test_when_the_picks_are_planned_a_pick_shows_and_can_be_kept_or_split():
    assert "var PICKS_TAGS = { replan: 'Planning around your picks', fresh: 'Starting fresh on your picks' };" in JS
    assert 'function replanning() { return !!submitted && !!PICKS_TAGS[submitted.picks]; }' in JS
    assert 'if (PICKS_TAGS[a.picks]) { parts.push(PICKS_TAGS[a.picks]); }' in JS
    row = JS[JS.index('function lineRow('):JS.index('function metaList(')]
    # A changed pick is the plan's own line, so it locks like any other (no Undo swap).
    assert "var swapped = !replanning() && (line.replaces || []).length > 0;" in row
    # A pick that stays is a line too, said so it never reads as a change.
    assert "if (sq.stays) { main.appendChild(lineNote('Already your pick in ' + sq.label + '.')); }" in row
    # Keep locks the pick back by its live slug (and undoes a "Not this one" on it); the server holds it.
    assert 'function keepPick(r) { tweaks.excluded.delete(r.slug); pinGame(r.slug, [r]); tweaks.locks.add(sqKey(r)); }' in JS
    # A lock is sent as one, so the server drops it once the square holds something else.
    assert 'if (tweaks.locks.has(sq)) { entry.lock = true; }' in JS
    assert "if (on && sq.stays) { tweaks.locks.add(sqKey(sq)); } else { tweaks.locks.delete(sqKey(sq)); }" in JS
    assert "if (e.lock === true) { locks.add(e.type + '|' + e.key); }" in JS
    # "Different games" never rerolls the hunter's own picks.
    reroll = JS[JS.index("els.reroll.addEventListener('click'"):JS.index("els.apply.addEventListener('click'")]
    assert 'var theirs = line.squares.some(function (sq) { return sq.stays; });' in reroll
    assert 'if (!theirs && !isLocked(line)' in reroll
    # The saved plan forgets only the squares an Apply wrote.
    assert "square_pins: (savedTweaks.square_pins || []).filter(function (e) { return !written.has(e.type + '|' + e.key); })" in JS
    acts = JS[JS.index('function lineActions('):JS.index('function action(')]
    assert "var kept = replanning() ? (line.replaces || []).filter(function (r) { return r.slug; }) : [];" in acts
    assert "change(function () { kept.forEach(keepPick); }, 'square:' + sqKey(kept[0]));" in acts
    assert "{ aria: keepLabel + ': ' + kept.map(" in acts
    # Only F / Only Survivalist: plain buttons, only when both picks can be locked back.
    assert 'if (kept.length === 2 && line.squares.length === 2) {' in acts
    assert "row.appendChild(action('Only ' + r.label, function () {" in acts
    assert "change(function () { pinGame(line.slug, [r]); keepPick(other); }, 'square:' + sqKey(r));" in acts
    assert 'splitChoice' not in JS
    # A pin moved off a square takes its lock with it; the ready line counts only games with a square to write.
    pin_game = JS[JS.index('function pinGame('):JS.index('function leaveOpen(')]
    assert 'delete tweaks.squarePins[k];\n                    tweaks.locks.delete(k);' in pin_game
    assert 'var games = plan.lines.filter(function (line) { return line.squares.some(changes); }).length;' in JS
    # The saved plan does not carry a stale lock.
    views = open('challenges/optimizer_views.py', encoding='utf-8').read()
    assert "or _still_holds(states, (e['type'], e['key']), tweaks)]}" in views
    # Apply never writes a pick that stays; the counts leave it out.
    assert 'function changes(sq) { return !sq.stays; }' in JS
    assert 'line.squares.filter(changes).forEach(function (sq) {' in JS
    assert "if (replanning() || changed) { cells.push(['changed', changed" in JS
    # The "Keeping" section of #195 is gone: a kept pick is a locked line.
    assert 'keptSection' not in JS and 'isKept' not in JS


def test_the_sheet_offers_to_leave_each_open_square_of_the_line_open():
    foot = JS[JS.index('function renderAltFoot('):JS.index('function altList(')]
    assert '[square].concat(partners).forEach(function (sq) {' in foot
    assert "btn.textContent = old ? 'Keep ' + (old.name || 'your pick') : 'Leave ' + sq.label + ' open';" in foot
    # An unfinished square already holds its game: Undo swap is its way back, not "leave open". Under Replan the
    # sheet offers Keep for it instead.
    assert 'if (sq.stays || (old && !(replanning() && old.slug))) { return; }' in foot
    assert 'closeSheet();' in foot and 'else { leaveOpen(sq); }' in foot
    assert "left_open: \"You're leaving the square it fits open.\"" in JS
    assert '<div class="pp-cpick__foot" data-cplan-alt-foot hidden></div>' in TEMPLATE


def test_a_covering_card_drops_also_covers():
    block = JS[JS.index('function appendAlternatives'):JS.index('function renderAltTabs')]
    assert 'var withAlso = !both;' in block


def test_every_offered_card_carries_its_platforms():
    # The sheet's and the must-have search's cards; the run page's picker cards always did (`rowTags` there).
    offer = JS[JS.index('function offerRow'):JS.index('function rowTags')]
    assert 'var tags = rowTags(row);' in offer and 'if (tags) { main.appendChild(tags); }' in offer
    # Stacked in the picker's own column, so a must-have result (laid out as a row) does not squeeze them.
    assert "main.className = 'pp-cpick__row-main';" in offer
    tags = JS[JS.index('function rowTags'):JS.index('function jobIcon')]
    assert "wrap.className = 'pp-cpick__row-plats';" in tags and 'platformTag(p)' in tags


def test_the_docks_are_sticky_and_clear_the_mobile_tab_bar():
    # The review's bar and the wizard's Back/Next share one dock rule; Next used to scroll off a long step.
    dock = CSS[CSS.index('.pp-cplan__dock {'):]
    dock = dock[:dock.index('}')]
    assert 'position: sticky' in dock and 'calc(56px + env(safe-area-inset-bottom' in dock
    assert 'class="pp-cplan__nav pp-cplan__dock" data-cplan-dock' in TEMPLATE
    assert 'class="pp-cplan__bar pp-cplan__dock" data-cplan-dock' in TEMPLATE
    assert "watchDocks(root.querySelectorAll('[data-cplan-dock]'));" in JS


def test_badge_sources_reach_the_plan_body_from_the_submitted_answers():
    body = JS[JS.index('function payload'):JS.index('function summaryLine')]
    for field in ('badge_started', 'badge_all', 'badge_types', 'badge_picks', 'badge_weight'):
        assert f'{field}: a.{field}' in body


def test_badge_picks_are_capped_client_side_and_every_copy_of_a_card_follows():
    toggle = JS[JS.index('function togglePick'):JS.index('function badgeSay')]
    assert 'badgePicks.size >= maxBadges' in toggle
    assert "querySelectorAll('[data-cplan-badge=\"' + id + '\"]')" in toggle


def test_badge_gallery_pages_are_guarded_by_identity_and_re_mark_selection():
    load = JS[JS.index('function loadBadges'):JS.index('if (els.pickToggle')]
    assert 'if (mine !== badgeSeq) { return; }' in load
    assert 'serverFragment(data.html)' in load and 'badgePicks.has(' in load


def test_the_gallery_seeds_editions_from_step_two_once():
    seed = JS[JS.index('function seedEditionFilter'):JS.index('function loadBadges')]
    assert 'answers().platforms' in seed
    toggle = JS[JS.index('if (els.pickToggle'):]
    assert 'if (open && !galleryOpened)' in toggle[:toggle.index('loadBadges(true)') + 40]



def test_the_check_and_the_art_let_taps_through_to_the_card_toggle():
    # They paint above the toggle's stretched ::after; without this a tap on the check circle hit nothing.
    assert '.pp-cplan__badge-med, .pp-cplan__badge-check { pointer-events: none; }' in CSS


def test_removing_a_picked_badge_keeps_focus_on_the_page():
    picked = JS[JS.index('function renderPicked'):JS.index('function togglePick')]
    assert ".pp-cplan__pin-x')[0]" in picked and 'els.counting.focus()' in picked


def test_a_free_hunters_first_plan_asks_before_it_is_spent():
    start = JS[JS.index('function startPlanning'):JS.index('// ── the weekly allowance')]
    assert "if (allowanceState === 'available') { openSpend(); return; }" in start
    assert 'answersKey(next) !== answersKey(saved)' in start


def test_only_the_confirmed_request_carries_the_spend():
    assert JS.count('use_free_plan = true') == 1
    assert 'if (opts.useFreePlan) { body.use_free_plan = true; }' in JS
    confirm = JS[JS.index("spendDialog.querySelector('[data-cplan-spend-confirm]').addEventListener('click'"):]
    # After the dialog's exit: the page is inert while the modal is up, so the plan's status would be lost.
    assert 'closeDialog(spendDialog, function () { replan({ fromWizard: true, useFreePlan: true }); });' in confirm[:400]


def test_the_allowance_refusals_are_read_once_and_acted_on():
    replan = JS[JS.index('function replan('):JS.index('function readyLine')]
    assert 'reply.needs_confirmation' in replan and 'reply.plan_locked' in replan
    assert 'PP.API.failureBody(err)' in replan and 'failureText(err' not in replan


def test_all_badges_greys_the_started_chip_too_and_keeps_its_tick():
    sync = JS[JS.index('function syncBadgeStep'):JS.index('function renderPicked')]
    assert "[data-cplan-type-chip], [data-cplan-started-chip]" in sync
    assert ".checked = false" not in sync   # covered, never cleared
    key = JS[JS.index('function answersKey'):JS.index('function lockedLine')]
    assert 'a.badge_all ? false : !!a.badge_started' in key   # the same rule as PlannerOptions.digest


def test_the_page_records_a_spend_only_when_the_server_reports_one():
    replan = JS[JS.index('function replan('):JS.index('function readyLine')]
    assert 'if (data.allowance && data.allowance.spent) { markSpent(sentAnswers, nextFrom(data.allowance)); }' in replan
    assert 'if (opts.useFreePlan) { markSpent' not in replan


def test_the_locked_path_leaves_the_plan_on_screen_alone():
    start = JS[JS.index('function startPlanning'):JS.index('function readSaved')]
    assert start.index('answersKey(next) !== answersKey(saved)') < start.index('submitted = next;')


def test_a_queued_request_keeps_its_spend():
    replan = JS[JS.index('function replan('):JS.index('function readyLine')]
    assert 'queued = Object.assign({}, queued || {}, opts,' in replan


def test_the_spend_dialog_focuses_not_yet_and_is_described():
    open_spend = JS[JS.index('function openSpend'):JS.index('if (spendDialog) {')]
    assert "'.gl-dialog__foot [data-cplan-spend-cancel]'" in open_spend
    assert 'aria-describedby="cplan-spend-sub cplan-spend-facts"' in TEMPLATE


def test_a_queued_reply_waits_and_re_asks_with_its_ticket():
    replan = JS[JS.index('function replan('):JS.index('//: How often a hunter waiting in line re-asks')]
    assert 'if (ticket) { body.line_ticket = ticket; }' in replan
    assert 'if (mine !== seq || !data || !data.queued) { return data; }' in replan
    assert 'return mine === seq ? attempt(data.ticket) : null;' in replan
    # Said only when it changes: a polite live region re-read every 2s is noise to a screen reader.
    assert 'if (line !== lastLine) { say(line); lastLine = line; }' in replan
    assert 'say(lineLine(data));' not in replan


def test_a_wait_in_line_can_be_stopped_and_is_capped():
    import re
    replan = JS[JS.index('function replan('):JS.index('//: How often a hunter waiting in line re-asks')]
    assert 'if (Date.now() - waitStarted > LINE_MAX_WAIT_MS) {' in replan
    assert 'showLineCancel(true);' in replan
    cap = re.search(r'var LINE_MAX_WAIT_MS = (\d+) \* (\d+) \* (\d+);', JS)
    assert cap and int(cap.group(1)) * int(cap.group(2)) * int(cap.group(3)) == 180000
    stop = replan[replan.index('stopLine = function (message) {'):replan.index('var attempt = function (ticket) {')]
    # Moving `seq` on ends the loop; the abandoned change is rolled back and what queued behind it dropped, like a
    # failure; the page stops looking busy.
    for step in ('seq++;', 'if (opts.rollback) { opts.rollback(); }', 'queued = null;', 'setBusy(false);',
                 'showLineCancel(false);'):
        assert step in stop, step
    assert "lineCancel.addEventListener('click', function () { if (stopLine) { stopLine(); } });" in JS


def test_every_ending_of_a_replan_hides_stop_waiting():
    # Not just a built plan: an error after a wait (429, plan_locked, a network failure) lands in the catch, and
    # the final step after it is the one every path passes through.
    replan = JS[JS.index('function replan('):JS.index('//: How often a hunter waiting in line re-asks')]
    final = replan[replan.rindex('}).then(function () {'):]
    assert final.index('if (mine !== seq) { return; }') < final.index('showLineCancel(false);') \
        < final.index('setBusy(false);')


def test_a_spend_landing_after_stop_waiting_is_still_recorded():
    replan = JS[JS.index('function replan('):JS.index('//: How often a hunter waiting in line re-asks')]
    attempt = replan[replan.index('var attempt = function (ticket) {'):]
    late = attempt.index('if (mine !== seq && data && data.allowance && data.allowance.spent) {')
    assert late < attempt.index('if (mine !== seq || !data || !data.queued) { return data; }')
    # It records the answers the request SENT: by the time a reply that waited lands, the hunter may have
    # submitted other answers (`startPlanning` sets `submitted` before it asks).
    assert 'var sentAnswers = submitted;' in replan
    assert replan.count('markSpent(sentAnswers, nextFrom(data.allowance));') == 2
    assert 'markSpent(submitted' not in replan


def test_the_stop_waiting_button_starts_hidden():
    html = open('templates/challenges/optimizer.html', encoding='utf-8').read()
    assert '<button type="button" class="pp-cta pp-cta--ghost" data-cplan-line-cancel hidden>Stop waiting</button>' in html


def test_the_wait_line_stays_under_the_solve_limit():
    import re
    poll = int(re.search(r'var LINE_POLL_MS = (\d+);', JS).group(1))
    assert 60000 / poll < 40   # SOLVE_RATE is 40 a minute


def test_the_weight_switch_offers_only_and_explains_it():
    html = open('templates/challenges/optimizer.html', encoding='utf-8').read()
    # "Badges only" answers "How much should badges matter?"; a bare "Only" did not.
    weight = html[html.index('value="only" class="sr-only" data-cplan-weight'):]
    assert '<span class="pp-switch__lbl">Badges only</span>' in weight[:weight.index('</label>')]
    assert "only: \"Only games that clear a badge stage. A square no badge game fits stays open for you to fill.\"" in JS
    assert "var WEIGHT_TAGS = { priority: ' (priority)', only: ' (badges only)' };" in JS
    empty = JS[JS.index('function emptySection('):JS.index('function nothingToPlan(')]
    # The Only copy is for the squares Only left open, never a square no game fits at all.
    assert 'var blocked = (plan.notes.badge_blocked || []).filter(mine).length;' in empty
    assert 'blocked === empty.length' in empty


def test_the_view_switch_chooses_the_whole_layout():
    render = JS[JS.index('function render() {'):JS.index('function renderByLetter()')]
    assert 'if (jobView()) { renderByJob(); } else { renderByLetter(); }' in render
    letter = JS[JS.index('function renderByLetter()'):JS.index('function renderByJob()')]
    assert '[both, az, jobs].forEach(function (group) { group.sort(byChosenOrder); });' in letter
    # Above the whole plan now (Job regroups all of it), only when both boards are planned.
    place = JS[JS.index('function placeSortBar()'):JS.index('function jobView()')]
    assert 'if (sortBar) { sortBar.hidden = !canSwitchView(); }' in place
    assert 'function canSwitchView() { return !!(plan.runs.az && plan.runs.jobs && plan.lines.length > 1); }' in place
    view = JS[JS.index('function jobView()'):JS.index('function render() {')]
    # A Job Coverage only plan has nothing to order by letter; a remembered Job never applies without the switch.
    assert "(!plan.runs.az || (canSwitchView() && sortMode === 'jobs'))" in view
    cmp = JS[JS.index('function byChosenOrder('):JS.index('function render() {')]
    # The chosen board first, then the other: a job-only game has no letter, an A-Z-only game no job.
    assert "var other = sortMode === 'az' ? 'jobs' : 'az';" in cmp
    assert '(sortRank(a, sortMode) - sortRank(b, sortMode)) || (sortRank(a, other) - sortRank(b, other))' in cmp


def test_the_sort_choice_is_remembered_safely():
    block = JS[JS.index("var SORT_KEY = 'pp-cplan-sort';"):JS.index('function sortRank(')]
    # Storage can throw (private windows, blocked site data): every read and write is guarded, A-Z the default.
    assert "var sortMode = 'az';" in block
    assert block.count('try {') == 2 and block.count('catch (e)') == 2


def test_the_sort_toggle_defaults_to_a_z():
    html = open('templates/challenges/optimizer.html', encoding='utf-8').read()
    bar = html[html.index('data-cplan-sortbar'):html.index('<div data-cplan-sections>')]
    assert 'value="az" class="sr-only" checked data-cplan-sort' in bar
    assert 'value="jobs" class="sr-only" data-cplan-sort' in bar


def test_open_my_plan_restores_the_saved_tweaks():
    # Deferred: nothing is restored (the typed must-haves stay) unless the lock check lets the plan go ahead.
    assert "startPlanning(function () { return restoreTweaks(savedTweaks); });" in JS
    start = JS[JS.index('function startPlanning('):JS.index('// ── the weekly allowance')]
    assert start.index('say(lockedLine(), true);') < start.index('tweaks = (restored && restored()) || freshTweaks();')
    restore = JS[JS.index('function restoreTweaks('):JS.index('function markSpent(')]
    # Must-haves are capped like the search caps them, and locks come back in the page's own "type|key" shape.
    assert 'pins = (t.pins || []).slice(0, maxPins)' in restore
    assert "squarePins[e.type + '|' + e.key] = e.contract;" in restore


def test_the_page_keeps_its_copy_of_the_autosave_in_step():
    replan = JS[JS.index('function replan('):JS.index('function readyLine')]
    # What THIS request sent, not the live state when the reply lands.
    assert 'var sentTweaks = { pins: pins.slice(), square_pins: body.tweaks.square_pins,' in replan
    assert 'rememberPlan(sentAnswers, sentTweaks);' in replan
    applied = JS[JS.index('function showApplied('):]
    assert 'forgetApplied(data.results);' in applied[:400]


def test_the_resume_card_steps_aside_while_a_plan_is_on_screen():
    review = JS[JS.index('function showReview(fresh, after) {'):JS.index('function squareCount()')]
    assert 'showResume(false);' in review
    edit = JS[JS.index("els.edit.addEventListener('click'"):JS.index('// ── badges step')]
    assert 'showResume(!!saved);' in edit
    show = JS[JS.index('function showResume('):JS.index('function rememberPlan(')]
    # The free banner's button follows the same rule as the members' card.
    assert "root.querySelector('[data-cplan-allowance] [data-cplan-open-saved]')" in show
    assert 'bannerOpen.hidden = !(on && saved);' in show


def test_the_sort_toggle_goes_away_with_the_plan():
    applied = JS[JS.index('function showApplied('):]
    assert 'if (sortBar) { sortBar.hidden = true; }' in applied[:500]


def test_a_locked_reply_on_other_answers_drops_the_tweaks_kept_for_this_one():
    show = JS[JS.index('function showInUse('):JS.index('function applyAnswers(')]
    assert 'if (!saved || answersKey(answersSaved) !== answersKey(saved)) { savedTweaks = null; }' in show


def test_a_plan_from_the_wizard_says_so_for_the_line_preview():
    replan = JS[JS.index('function replan('):JS.index('function readyLine')]
    assert 'if (opts.fromWizard) { body.from_wizard = true; }' in replan


def test_the_preview_door_rides_on_both_plan_doors():
    html = open('templates/challenges/optimizer.html', encoding='utf-8').read()
    assert """data-plan-url="{% url 'challenge_optimizer_plan' %}{{ preview_qs }}\"""" in html
    assert """data-alt-url="{% url 'challenge_optimizer_alternatives' %}{{ preview_qs }}\"""" in html


def test_a_preview_never_claims_the_plan_was_saved():
    remember = JS[JS.index('function rememberPlan('):JS.index('function forgetApplied(')]
    assert '!root.dataset.preview' in remember
    html = open('templates/challenges/optimizer.html', encoding='utf-8').read()
    assert '{% if preview_door %} data-preview="{{ preview_door }}"{% endif %}' in html


def test_the_free_banner_offers_membership_as_a_quiet_real_button():
    # A GHOST on the accent, with no stars: it must not outshout the hunter's own task beside it (owner, 2026-10-17).
    html = open('templates/challenges/optimizer.html', encoding='utf-8').read()
    banner = html[html.index('data-cplan-allowance>'):html.index('{% comment %}THE SAVED PLAN, for members')]
    door = banner[banner.index('<a class="pp-cta pp-cta--ghost pp-cplan__members"'):]
    door = door[:door.index('</a>')]
    assert "href=\"{% url 'support_hub' %}\"" in door and 'Members optimize any time' in door
    assert 'pp-cta--rise' not in door and 'sup-go__rise' not in door
    assert 'class="link link-hover"' not in banner


def test_every_switch_chip_carries_an_icon_and_a_label():
    html = open('templates/challenges/optimizer.html', encoding='utf-8').read()
    for name in ('badge_weight', 'cplan_sort'):
        chips = html.split('name="' + name + '"')[1:]
        assert chips, name
        for chip in chips:
            chip = chip[:chip.index('</label>')]
            assert '<svg' in chip and 'aria-hidden="true"' in chip and 'class="pp-switch__lbl"' in chip


def test_the_star_host_is_the_house_modifier_not_a_page_copy():
    import re
    cta = open('static/css/components/cta.css', encoding='utf-8').read()
    assert re.search(r'\.pp-cta--rise \{ position: relative; overflow: hidden; --sup-t: var\(--cta-c, var\(--pp-primary\)\); \}', cta)
    assert '.pp-cta--rise .pp-cta__lbl { position: relative; }' in cta
    # The landing page's old copy is gone, and its buttons ride the modifier.
    assert 'land-rise' not in open('static/css/components/landing.css', encoding='utf-8').read()
    landing = open('templates/home/landing.html', encoding='utf-8').read()
    assert 'land-rise' not in landing and landing.count('class="pp-cta pp-cta--rise"') == 2


def test_a_resort_glides_and_keeps_the_switchs_focus():
    change = JS[JS.index("root.querySelectorAll('[data-cplan-sort]').forEach("):JS.index('function sortRank(')]
    assert 'if (lineFlip) { lineFlip.measure(); }' in change and 'if (lineFlip) { lineFlip.play(); }' in change
    assert change.index('lineFlip.measure()') < change.index('render();') < change.index('lineFlip.play()')
    assert 'input.focus({ preventScroll: true });' in change
    # Rows are rebuilt, so identity comes from the game, not the node.
    assert "key: function (el) { var f = el.querySelector('[data-focus]');" in change


def test_the_stats_sit_on_one_row_however_many_there_are():
    # Two to five cells ("cover both" and "picks changed" come and go): a fixed column count would orphan one.
    rule = CSS[CSS.index('.pp-cplan__stats { grid-template-columns: none;'):]
    assert 'grid-auto-flow: column; grid-auto-columns: minmax(0, 1fr);' in rule[:rule.index('}')]


def test_the_view_switch_sits_on_the_right():
    rule = CSS[CSS.index('.pp-cplan__sortbar {'):]
    assert 'justify-content: flex-end;' in rule[:rule.index('}')]


def test_the_sort_chips_say_letter_and_job():
    html = open('templates/challenges/optimizer.html', encoding='utf-8').read()
    bar = html[html.index('data-cplan-sortbar'):html.index('<div data-cplan-sections>')]
    assert '<span class="pp-switch__lbl">Letter</span>' in bar and '<span class="pp-switch__lbl">Job</span>' in bar
    assert 'A-Z' not in bar


def test_every_optimizer_control_is_a_full_touch_target():
    css = open('static/css/components/challenge-optimizer.css', encoding='utf-8').read()
    touch = css[css.index('touch: every control on the optimizer'):]
    for selector in ('.pp-cplan .pp-switch__chip', '.pp-cplan .pp-cta', '#cplan-spend .pp-cta', '.pp-optdoor__go'):
        assert selector in touch[:600], selector
    assert 'min-height: 44px;' in touch[:600]


def test_the_saved_just_now_line_keeps_the_date_highlight():
    remember = JS[JS.index('function rememberPlan('):JS.index('function forgetApplied(')]
    assert "now.className = 'pp-when';" in remember and "now.textContent = 'just now';" in remember



# --- the Google/Apple pass (owner, 2026-10-17) ---------------------------------------------------------------

def _between(start, end):
    return JS[JS.index(start):JS.index(end, JS.index(start))]


def test_a_re_solve_keeps_its_place_and_a_fresh_plan_arrives():
    replan = _between('function replan(', 'function readyLine')
    # Survivors glide, arrivals settle in: measured BEFORE the new plan replaces the rows, played after.
    assert replan.index('if (glide) { lineFlip.measure(); }') < replan.index('plan = data;') < replan.index('if (glide) { lineFlip.play(); }')
    assert 'var glide = onReview && lineFlip;' in replan
    review = _between('function showReview(fresh, after) {', 'function squareCount()')
    # A fresh plan folds the wizard (hidden BEFORE its fill is cancelled, so it never flashes back) ...
    assert 'fold.finished.then(function () { reveal(); fold.cancel(); }, reveal);' in review
    # ... then the summary, the stats and the first groups settle in on a CAPPED cascade.
    assert 'settleIn(els.summary);' in review and 'settleIn(els.stats, 60);' in review
    assert 'Array.prototype.slice.call(els.sections.children, 0, 5)' in review
    flip = _between('var lineFlip = PP.flipGrid ? PP.flipGrid({', 'function sortRank(')
    assert 'enter: true' in flip


def test_a_fresh_plan_scrolls_smoothly_not_with_a_jump():
    focus = _between('function restoreFocus(', '// ── rendering the plan')
    assert 'target.focus({ preventScroll: true });' in focus
    assert "els.summary.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' });" in focus


def test_the_stats_count_and_tick_from_their_old_values():
    stats = _between('function renderStats() {', 'function sectionShell(')
    assert "num.dataset.countupSuffix = cell[2];" in stats   # "3/12": the number counts, "/12" rides along
    assert 'PP.countUp(num, before ? 600 : 900, { from: from });' in stats
    review = _between('function showReview(fresh, after) {', 'function squareCount()')
    assert 'if (fresh) { lastStats = null; }' in review      # a fresh plan counts up from zero


def test_feedback_lands_where_the_hunter_is_looking():
    fail = _between('function sayFailure(', '// ── answers')
    assert 'if (reviewing() && PP.ToastManager) { say(\'\'); PP.ToastManager.error(text); return; }' in fail
    replan = _between('function replan(', 'function readyLine')
    assert "barSay(onReview ? 'Re-planning…' : '');" in replan
    assert 'sayFailure((reply && (reply.error || reply.detail)) || fallback);' in replan
    # Every ending clears the bar's line and restores the pressed button's label, the stop path included.
    assert replan.count('unlabel();') == 2 and replan.count("barSay('');") >= 3


def test_working_buttons_say_what_they_are_doing():
    replan = _between('function replan(', 'function readyLine')
    assert "busyLabel(!els.wizard.hidden ? els.next : els.again, 'Planning…')" in replan
    apply = _between('if (els.apply) {', 'if (els.again) {')
    assert "var unlabel = busyLabel(els.apply, 'Filling…');" in apply
    label = _between('function busyLabel(', 'function barSay(')
    # It only restores what it set, so a label something else changed meanwhile is left alone.
    assert 'if (btn.textContent === text) { btn.textContent = was; }' in label


def test_lock_toggles_in_place_without_a_redraw():
    lock = JS[JS.index("row.appendChild(action('Lock', function (btn) {"):]
    lock = lock[:lock.index("}, { pressed: locked")]
    assert 'render()' not in lock and "li.classList.toggle('is-locked', on);" in lock
    assert "btn.setAttribute('aria-pressed', on ? 'true' : 'false');" in lock
    assert 'if (on && PP.igniteTab) { PP.igniteTab(btn); }' in lock
    assert 'var on = !isLocked(line);' in lock   # read fresh: the button outlives the render that built it


def test_both_dialogs_exit_on_their_own_choreography():
    close = _between('function closeDialog(', 'function wireDialogClose(')
    # Callbacks queue and run on the REAL close, so an `after` is never dropped by a close already running.
    assert '(d._afterClose = d._afterClose || []).push(after);' in close
    wire = _between('function wireDialogClose(', '// ── different games, apply')
    assert 'queue.forEach(function (fn) { fn(); });' in wire
    spend = _between('if (spendDialog) {', '/** The free plan is now')
    assert 'wireDialogClose(spendDialog);' in spend
    assert "spendDialog.addEventListener('cancel', function (e) { e.preventDefault(); closeDialog(spendDialog); });" in spend
    assert 'spendDialog.close()' not in spend


def test_chips_glide_and_the_badge_gallery_arrives():
    for render, flip in (('function renderPicked() {', 'pickFlip'), ('function renderPinned() {', 'pinFlip')):
        body = _between(render, '\n        }\n')
        assert 'if (' + flip + ') { ' + flip + '.measure(); }' in body
    picked = JS[JS.index('function renderPicked() {'):JS.index('function togglePick(')]
    assert 'if (pickFlip) { pickFlip.play(); }' in picked
    reveal = _between('function revealBadges() {', 'function seedEditionFilter()')
    # No observer, no hide: a grid left wearing `pp-reveal` with nothing to reveal it stays blank.
    assert "if (!badgeReveal) { els.badgeGrid.classList.remove('pp-reveal'); }" in reveal
    assert 'if (PP.animatePanel) { PP.animatePanel(els.pick, open); }' in JS
    toggle = _between('function togglePick(', 'function badgeSay(')
    assert "check.classList.add('pp-draw-in');" in toggle


def test_the_header_tally_counts_and_ticks():
    recount = _between('function recountOpen(tick) {', 'function showApplied(')
    assert 'var was = parseInt(els.openTotal.dataset.countup' in recount
    assert 'PP.countUp(els.openTotal, 700, { from: was });' in recount
    assert "parseInt(els.openTotal.dataset.countup || '0', 10) > 0) { PP.countUp(els.openTotal, 900); }" in JS
    html = open('templates/challenges/optimizer.html', encoding='utf-8').read()
    assert 'data-cplan-open-total data-countup="{{ open_total }}"' in html


def test_apply_lands_with_a_traced_check():
    applied = _between('function showApplied(', '// ── status')
    assert "if (data.applied) { wrap.querySelector('.pp-cplan__section-head').prepend(doneCheck()); }" in applied
    assert 'settleIn(wrap);' in applied
    check = _between('function doneCheck() {', '// ── status')
    assert check.count("setAttribute('pathLength', '1')") == 2 and 'pp-draw-in' in check


def test_arrivals_are_inert_until_they_land():
    settle = _between('function settleIn(', 'function busyLabel(')
    assert "el.classList.add('pp-arriving');" in settle and 'rise.finished.then(landed, landed);' in settle
    # A fade on an ease-out under a rise on the spring: never one spring-eased animation on opacity.
    assert settle.count('el.animate(') == 2


def test_the_polish_css_is_in_place():
    css = open('static/css/components/challenge-optimizer.css', encoding='utf-8').read()
    assert '.pp-cplan__review { transition: opacity var(--pp-dur) var(--pp-ease); }' in css   # dims back out too
    assert '.pp-cplan__pick { margin-top: 4px; overflow: hidden; transition: height' in css     # animatePanel's contract
    assert '#cplan-alt .pp-cpick__body { min-height: 12rem; }' in css
    hold = css[css.index('.pp-reveal .pp-cplan__badge { opacity: 0; }') - 200:]
    assert '@media (prefers-reduced-motion: no-preference)' in hold[:260]
    assert 'opacity 0.2s ease' in css[css.index('.pp-cplan .pp-bgal__chip {'):][:200]


def test_the_page_stays_busy_until_a_fresh_plan_is_on_screen():
    replan = _between('function replan(', 'function readyLine')
    built = replan[replan.index('var glide = onReview && lineFlip;'):]
    assert 'return new Promise(function (resolve) {' in built[:600] and 'resolve();' in built[:900]


def test_a_closing_dialog_is_already_answered():
    close = _between('function closeDialog(', 'function wireDialogClose(')
    assert close.index("if (d.classList.contains('is-closing')) { return; }") < close.index('(d._afterClose = d._afterClose || []).push(after);')
    css = open('static/css/components/challenge-optimizer.css', encoding='utf-8').read()
    assert '#cplan-alt.is-closing, #cplan-spend.is-closing { pointer-events: none; }' in css


def test_the_header_tally_reasserts_its_final_number():
    recount = _between('function recountOpen(tick) {', 'function showApplied(')
    assert "if (els.openTotal.dataset.countup === String(total)) { els.openTotal.textContent = String(total); }" in recount


def test_a_step_change_settles_the_badge_panel():
    go = _between('function goTo(index) {', 'function syncRail()')
    assert 'PP.animatePanel(els.pick, !els.pick.hidden, false);' in go



# --- None: badges don't count (owner, 2026-10-17) ------------------------------------------------------------

def test_none_is_a_real_chip_first_in_the_badge_sources():
    html = open('templates/challenges/optimizer.html', encoding='utf-8').read()
    group = html[html.index('aria-label="Badges that count"'):]
    group = group[:group.index('</div>')]
    assert group.index('data-cplan-badge-none') < group.index('data-cplan-badge-started')
    assert 'None<span class="sr-only">, badges don\'t count</span>' in group
    # The weight and the picker sit in the block None greys.
    extras = html[html.index('data-cplan-badge-extras'):html.index('WHAT COUNTS, said back')]
    assert 'cplan-weight-label' in extras and 'data-cplan-pick-toggle' in extras


def test_none_asks_for_no_badges_whatever_sits_greyed_under_it():
    a = _between('function answers() {', 'function payload() {')
    assert 'var all = !none && !!(els.badgeAll && els.badgeAll.checked);' in a
    assert "badge_started: !none && !!(els.badgeStarted && els.badgeStarted.checked)," in a
    assert 'badge_picks: none ? [] : Array.from(badgePicks.keys()),' in a
    assert "var typeBoxes = none ? [] : checked('[data-cplan-badge-type]');" in a


def test_none_greys_and_disables_what_it_turns_off():
    sync = _between('function syncBadgeStep() {', 'function renderPicked() {')
    assert "cover('[data-cplan-type-chip], [data-cplan-started-chip]', none || all);" in sync
    assert "cover('[data-cplan-all-chip]', none);" in sync
    # Inert, not just dim: nothing under None can be operated or reached by Tab.
    assert "if (none) { el.setAttribute('inert', ''); } else { el.removeAttribute('inert'); }" in sync
    assert '[data-cplan-badge-none], [data-cplan-badge-started]' in JS   # it re-syncs the step when toggled


def test_saved_answers_with_no_source_come_back_as_none():
    apply = _between('function applyAnswers(a) {', 'root.querySelectorAll(\'[data-cplan-open-saved]\')')
    assert 'els.badgeNone.checked = !a.badge_started && !a.badge_all' in apply


def test_badge_names_carry_their_type():
    # The live chip's name is the card's data-name (the server's pick_name), and a stage row names its badge's type.
    assert "togglePick(parseInt(btn.dataset.cplanBadge, 10), btn.dataset.name," in JS
    row = JS[JS.index('function stageRow('):]
    assert "var detail = [group.type, group.editions].filter(Boolean).join(', ');" in row
    assert "type: s.type_label || ''" in JS[JS.index('function stageList('):JS.index('function stageRow(')]


def test_the_plan_and_its_cards_draw_flags_as_the_sites_badges():
    """The run page picker's treatment (owner, 2026-10-24): the same glyph the game wears everywhere, feature-tested
    against a stale utils.js, with no label table of the page's own."""
    helper = JS[JS.index('function flagBadges(flags) {'):]
    helper = helper[:helper.index('}') + 1]
    assert "flags && flags.length && PP.flagBadges ? PP.flagBadges(flags, 'sm', 'row') : null" in helper
    assert JS.count('flagBadges(item.availability)') == 1 and JS.count('flagBadges(flags)') >= 2
    assert 'AVAILABILITY_LABELS' not in JS and 'availabilityChip' not in JS
