"""The navbar search's add-and-sync endpoints: the contract `navbar-search.js` polls against.

Both endpoints shipped without coverage, which is how the client half drifted away from them
unnoticed. The visible failure was that typing a new hunter into the navbar search and clicking
"Sync X" gave no confirmation and no link, ever: one `resetAddSync()` did double duty as "cancel the
sync" and "clear the UI", so the success handler's own `input.focus()` re-ran the typeahead, whose
render cancelled the poll ~300ms into its 2500ms start delay. The sync ran server-side and nobody
was told.

The state machine that replaced it lives in JS and this suite cannot reach it. What it CAN pin is
every server fact that machine now depends on, each of which was being read wrongly or not at all:

  * the refusal bodies (429 rate limit, 503 PSN outage) carry an `error` string. `API.request`
    throws on any non-ok response, so these used to be parsed by nobody and replaced with a generic
    "Failed to sync profile." The anon cap is 3/min, so the message a hunter hit most often was
    exactly the one always discarded.
  * `psn_username` comes back in the server's own spelling, which is what the client then polls with.
  * the status endpoint's three client-visible states: 'error' means gone-or-unfindable, a truthy
    `account_id` WITH a `slug` means there is a profile worth linking to, and 'syncing' is the
    intermediate the panel now narrates instead of sitting mute.

`POLL_CAP` and the cancel/repaint split are client-side and stay unverified here; they need a browser.
"""
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.urls import reverse

from tests.factories import ProfileFactory
from trophies.models import Profile

pytestmark = pytest.mark.django_db

SEARCH_URL = reverse('search_sync_profile')
STATUS_URL = reverse('add_sync_status')


@pytest.fixture(autouse=True)
def _quiet_externals():
    """No PSN worker, no outage flag, no shared rate-limit bucket between tests.

    `redis_client` is patched at the VIEW's module namespace, not at its source: the view does
    `from ...cache import redis_client`, so patching the origin would leave the view holding the real
    client. A MagicMock's `.get()` returns a truthy Mock by default, which would make every test in
    this file see a PSN outage and 503 -- hence the explicit `return_value=None`.
    """
    cache.clear()
    fake_redis = MagicMock()
    fake_redis.get.return_value = None
    with patch('trophies.views.sync_views.redis_client', fake_redis), \
         patch('trophies.psn_manager.PSNManager.initial_sync') as initial, \
         patch('trophies.models.Profile.attempt_sync') as refresh:
        yield {'redis': fake_redis, 'initial_sync': initial, 'attempt_sync': refresh}


# --------------------------------------------------------------- SearchSyncProfileView ----

def test_new_name_creates_the_profile_and_queues_an_initial_sync(client, _quiet_externals):
    resp = client.post(SEARCH_URL, {'psn_username': 'BrandNewHunter'})

    assert resp.status_code == 200
    assert resp.json()['success'] is True
    profile = Profile.objects.get(psn_username='brandnewhunter')
    _quiet_externals['initial_sync'].assert_called_once_with(profile)
    _quiet_externals['attempt_sync'].assert_not_called()


def test_known_name_refreshes_instead_of_creating_a_second_profile(client, _quiet_externals):
    existing = ProfileFactory(psn_username='knownhunter')

    resp = client.post(SEARCH_URL, {'psn_username': 'KNOWNHUNTER'})   # matched __iexact

    assert resp.status_code == 200
    assert Profile.objects.filter(psn_username__iexact='knownhunter').count() == 1
    assert resp.json()['psn_username'] == existing.psn_username
    _quiet_externals['attempt_sync'].assert_called_once()
    _quiet_externals['initial_sync'].assert_not_called()


def test_response_carries_the_spelling_the_client_must_poll_with(client):
    """A new profile is stored LOWERCASED (`psn_username.lower()` on create), so the name the client
    polls the status endpoint with has to be the server's, not the raw input. The client reads it off
    this key for exactly that reason; if it ever stopped being the canonical spelling, the poll would
    be asking about a name the DB does not hold under that key."""
    resp = client.post(SEARCH_URL, {'psn_username': 'MixedCaseName'})

    assert resp.json()['psn_username'] == 'mixedcasename'
    assert Profile.objects.filter(psn_username='mixedcasename').exists()


def test_surrounding_whitespace_does_not_create_a_second_profile(client):
    ProfileFactory(psn_username='spacedout')

    resp = client.post(SEARCH_URL, {'psn_username': '  spacedout  '})

    assert resp.status_code == 200
    assert Profile.objects.filter(psn_username='spacedout').count() == 1


def test_blank_name_is_refused_with_a_message(client):
    resp = client.post(SEARCH_URL, {'psn_username': '   '})

    assert resp.status_code == 400
    assert resp.json()['error']
    assert not Profile.objects.exists()


def test_psn_outage_refusal_carries_the_line_the_panel_shows(client, _quiet_externals):
    """503 with an `error` body. The client surfaces this string verbatim, and can only do so because
    it now reads the body of a non-ok response; before that the hunter was told "Failed to sync
    profile. Please try again." while the server had written a sentence explaining that retrying was
    pointless."""
    _quiet_externals['redis'].get.return_value = b'1'

    resp = client.post(SEARCH_URL, {'psn_username': 'duringoutage'})

    assert resp.status_code == 503
    assert 'unavailable' in resp.json()['error'].lower()
    assert not Profile.objects.filter(psn_username='duringoutage').exists()
    _quiet_externals['initial_sync'].assert_not_called()


def test_anon_rate_limit_refusal_carries_an_error_body(client):
    """The anon cap is 3/min by IP, low enough that a hunter correcting a typo twice can meet it.
    The 429 body's `error` is the only place the reason exists, so it is a hard part of the contract
    rather than a nicety."""
    for i in range(3):
        assert client.post(SEARCH_URL, {'psn_username': f'anonhunter{i}'}).status_code == 200

    resp = client.post(SEARCH_URL, {'psn_username': 'onetoomany'})

    assert resp.status_code == 429
    assert 'too many' in resp.json()['error'].lower()


def test_a_rate_limited_search_creates_nothing(client):
    """The refusal precedes the create, so a capped-out search must not leave a half-added hunter
    behind for the poll to then report on forever."""
    for i in range(3):
        client.post(SEARCH_URL, {'psn_username': f'anonhunter{i}'})

    client.post(SEARCH_URL, {'psn_username': 'neveradded'})

    assert not Profile.objects.filter(psn_username='neveradded').exists()


def test_a_signed_in_hunter_is_not_held_to_the_anon_cap(client):
    """15/min keyed by user, and the user bucket short-circuits the IP one, so a member behind a
    shared or NAT'd address is not punished for their neighbours."""
    member = ProfileFactory(psn_username='searchowner')
    client.force_login(member.user)

    for i in range(4):   # one past the anon ceiling
        assert client.post(SEARCH_URL, {'psn_username': f'authedlook{i}'}).status_code == 200


# ------------------------------------------------------------------ AddSyncStatusView ----

def test_unknown_name_reports_error_so_the_panel_can_stop(client):
    """'error' is the client's only terminal-failure signal: it stops the poll and prints the
    check-the-spelling line. A 404 or an empty 200 would leave it polling for five minutes about a
    hunter who does not exist."""
    resp = client.get(STATUS_URL, {'psn_username': 'nosuchhunter'})

    assert resp.status_code == 200
    assert resp.json()['sync_status'] == 'error'
    assert not resp.json()['account_id']


def test_status_lookup_is_case_insensitive(client):
    ProfileFactory(psn_username='casehunter', account_id='2000000000000000001')

    resp = client.get(STATUS_URL, {'psn_username': 'CaseHunter'})

    assert resp.json()['account_id'] == '2000000000000000001'


def test_ingested_profile_hands_over_both_keys_the_link_needs(client):
    """The client requires `account_id` AND `slug` before it reveals the Visit row. It used to gate on
    `account_id` alone and then assign `visitAnchor.href = data.slug` regardless, so a payload with
    one and not the other produced a live-looking link pointing at the empty string."""
    hunter = ProfileFactory(psn_username='ingested', account_id='2000000000000000002')

    data = client.get(STATUS_URL, {'psn_username': 'ingested'}).json()

    assert data['account_id'] == '2000000000000000002'
    assert data['slug'] == reverse('profile_detail', kwargs={'psn_username': hunter.psn_username})
    assert data['psn_username'] == 'ingested'


def test_freshly_created_profile_withholds_the_link(client):
    """The shape the client sees on its first tick after adding a brand-new hunter: the row exists,
    ingestion has not reached the account id yet. Neither terminal branch may fire, or the panel
    would announce a profile with nothing in it.

    `sync_status` is 'synced' here because that is the FIELD DEFAULT, not a finished sync -- which is
    exactly why `account_id` and not the status is what gates the link.
    """
    hunter = Profile.objects.create(psn_username='justadded')

    data = client.get(STATUS_URL, {'psn_username': 'justadded'}).json()

    assert not data['account_id']
    # The FIELD DEFAULT itself, not merely "not error": the whole reason `account_id` gates the link is
    # that a brand-new row already claims to be synced, and `docs/reference/api-endpoints.md` documents
    # that as a gotcha. `!= 'error'` alone was satisfied by any value.
    assert hunter.sync_status == 'synced'
    assert data['sync_status'] == 'synced'


def test_in_progress_sync_reports_syncing(client):
    """The intermediate the panel now narrates ("Syncing NAME. Trophies are arriving now."). It was
    always in the payload and the client simply ignored it, which is why a multi-minute first sync
    looked identical to a dead button."""
    ProfileFactory(psn_username='midsync', sync_status='syncing')

    assert client.get(STATUS_URL, {'psn_username': 'midsync'}).json()['sync_status'] == 'syncing'


def test_status_blank_name_is_refused(client):
    resp = client.get(STATUS_URL, {'psn_username': ''})

    assert resp.status_code == 400
    assert resp.json()['error']


def test_status_lookup_consumes_no_psn_tokens_for_anon(client):
    """Read-only by design, so it is open to anonymous callers to pair with the open search endpoint.
    A guard against someone "tidying up" by routing it through the sync machinery."""
    ProfileFactory(psn_username='tokenfree', account_id='2000000000000000003')

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        assert client.get(STATUS_URL, {'psn_username': 'tokenfree'}).status_code == 200
        assign.assert_not_called()


# ------------------------------------------------------------- the client-side contract ----

def _tag_around(markup, token):
    """The whole tag containing `token`, from its `<` to its `>`.

    Not `split(token)[1].split('>')[0]`: that only sees attributes written AFTER the token, so an
    `aria-live` or `role="status"` placed before it would slip past a test written to forbid it.
    """
    at = markup.index(token)
    return markup[markup.rindex('<', 0, at):markup.index('>', at) + 1]


def _navbar_search_form(client):
    """The navbar search form's markup as `(panel, form)`.

    `panel` is the results panel's own subtree, bounded by walking div depth from its opening tag to
    the matching close -- NOT "everything after the id", which is the same string as the rest of the
    form and would let anything in the form satisfy an assertion about being inside the panel. It did:
    the first version of the announcer test passed a broken slice and failed against correct markup.

    Scoped rather than asserting on bare ids across the whole page, so a same-named hook elsewhere in
    the chrome could not satisfy these on its own.
    """
    body = client.get('/support/').content.decode()
    form = body.split('id="navbar-sync-form"', 1)
    assert len(form) == 2, 'the navbar search form is not on the page'
    form = form[1].split('</form>', 1)[0]

    at = form.find('id="navbar-search-results"')
    assert at != -1, 'the navbar search results panel is not in the form'
    start = form.rindex('<div', 0, at)

    depth, i = 0, start
    while i < len(form):
        nxt_open, nxt_close = form.find('<div', i), form.find('</div>', i)
        if nxt_close == -1:
            break
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            i = nxt_open + 4
        else:
            depth -= 1
            i = nxt_close + 6
            if depth == 0:
                return form[start:i], form
    raise AssertionError('the results panel div is never closed inside the form')


def test_navbar_exposes_the_status_line_the_controller_writes_to(client):
    """`navbar-search.js` looks up `add-sync-status-text` by id. It was `add-sync-error-text` with the
    error modifier baked into its class list, which is a large part of why the successful path had
    nowhere to speak: the one element that could have carried "In the queue" was styled and named as
    a failure."""
    panel, _form = _navbar_search_form(client)

    assert 'id="add-sync-status-text"' in panel
    # The status line must not be born wearing the error colour; JS adds the modifier per phase.
    assert 'pp-navsearch__msg pp-navsearch__msg--err' not in panel
    assert 'add-sync-error-text' not in _form, 'the error-only hook survived the rename'


def test_the_announcer_sits_outside_the_listbox_it_reports_on(client):
    """The staged copy is announced from an sr-only sibling of the panel, not from the visible line
    inside it, for two reasons that each defeat the announcement on their own: a `<p>` is not a
    permitted child of `role="listbox"` so AT may prune it, and the panel carries `hidden` whenever
    it is closed -- which is precisely when a hunter who clicked away most needs telling.

    Mirrors the sync panel's own `data-nav-live` split. The pin is the ORDERING: the announcer must
    come after the panel's close, i.e. not be inside it.
    """
    panel, form = _navbar_search_form(client)

    assert 'data-search-live' in form, 'the live region is missing from the search form'
    assert 'aria-live="polite"' in form
    assert 'role="listbox"' in panel, 'the slice under test is not the listbox panel'
    assert 'data-search-live' not in panel,         'the announcer is inside role="listbox", where it can be pruned and is hidden half the time'
    # The visible line must NOT also be a live region, or every stage is announced twice.
    assert 'aria-live' not in _tag_around(form, 'id="add-sync-status-text"')


def test_the_field_spinner_does_not_double_announce_the_status(client):
    """It was `role="status" aria-label="Searching"`, which reads the same state a second time next to
    a live region that says it in words."""
    _panel, form = _navbar_search_form(client)

    spinner = _tag_around(form, 'add-sync-load')
    assert 'aria-hidden="true"' in spinner
    assert 'role="status"' not in spinner
    assert 'aria-label' not in spinner


# ------------------------------------------------- the controller's own load-bearing structure ----
#
# Source-text pins, the idiom this project already uses for JS it cannot execute (see
# `test_gamelists_actions.py`, which pins `quick-add.js` exactly this way). There is no JS test
# runner here -- `npm test` is a placeholder -- so the state machine's behaviour is unreachable, but
# the STRUCTURE that keeps it correct is not, and every pin below marks a bug that shipped.
#
# Comments are stripped first: a comment is a claim, only code is evidence, and this repo has shipped
# assertions satisfied by prose describing the very thing that had just been deleted.


def _js(relative):
    src = (Path(__file__).resolve().parents[2] / relative).read_text(encoding='utf-8')
    src = re.sub(r'/\*.*?\*/', '', src, flags=re.S)
    return re.sub(r'^\s*//.*$', '', src, flags=re.M)


def _body(js, signature):
    """A function's body, from its opening line to the first close at its own indentation."""
    assert signature in js, 'missing: %s' % signature
    return js.split(signature, 1)[1].split('\n    }', 1)[0]


def test_only_two_deliberate_acts_abandon_a_sync():
    """THE ORIGINAL BUG, pinned. One `resetAddSync()` both cancelled the in-flight sync and cleared
    the UI, so `closePanel` (any outside click) and `renderSuggestions` (any typeahead response,
    including the one the success handler's own `input.focus()` provoked) each silently destroyed the
    wait. Clicking "Sync X" posted the form and then restored the panel to its pre-click state.

    Exactly two callers may abandon: a submit superseding the last, and Clear. The count is 3 because
    the `function abandonAddSync()` declaration carries the same substring, so a bare `>= 2` would
    have been satisfied by the definition plus a single caller.
    """
    js = _js('static/js/navbar-search.js')

    assert js.count('abandonAddSync()') == 3, \
        'a third caller can abandon a sync; incidental cancellation is the bug this file was rewritten for'
    assert 'resetAddSync' not in js, 'the cancel-and-clear function is back'


def test_closing_the_panel_does_not_stop_the_poll():
    """Dismissing a dropdown is not a decision to abandon a multi-minute sync."""
    body = _body(_js('static/js/navbar-search.js'), 'function closePanel() {')

    assert 'abandonAddSync' not in body
    assert 'stopPolling' not in body
    # It must still drop the cursor class BEFORE emptying `items`, or the highlight strands on a row
    # that survives re-renders (the add row, the Visit row) and later shows up alongside a real cursor.
    assert "classList.remove('is-active')" in body
    assert body.index("classList.remove('is-active')") < body.index('items = []')


def test_async_continuations_guard_on_state_identity_not_nullness():
    """`if (!addSync)` still passes after a second submit REPLACED the object, so a superseded request
    wrote into the new wait: the panel read "Found B" with the link pointing at A's profile, and an
    overwritten timer handle became a 2.5s interval immune to every clear path for the tab's life."""
    js = _js('static/js/navbar-search.js')

    assert js.count('const mine = addSync') == 2, 'the poll and the POST each capture their own state'
    assert js.count('addSync !== mine') == 5, \
        'a continuation that writes addSync is unguarded (poll .then, POST .then, POST .catch x2, startTimer)'


def test_the_refusal_read_is_guarded_on_the_far_side_of_its_await():
    """COUNTING the guards is not enough, and the first version of the pin above proved it: the POST
    `.catch` had one, placed BEFORE the only `await` in the handler. Reading the response body yields,
    and `setAddSync` guards only `!addSync`, so a submit for a DIFFERENT name landing during that read
    had the previous request's refusal stamped into its wait -- wrong sentence, wrong phase, retry row
    re-shown. The count said 4 and the bug was live.

    So pin the ORDER: the await resolves into a local, and identity is re-checked after it.
    """
    js = _js('static/js/navbar-search.js')

    catch = js.split(".catch(async (error) => {", 1)[1].split('\n            });', 1)[0]
    assert 'const refused = await refusal(' in catch, 'the body read is not resolved into a local first'
    assert catch.index('const refused = await refusal(') < catch.index("setAddSync('error', refused)"), \
        'the await must resolve before the state is written'
    guard_after = catch.split('const refused = await refusal(', 1)[1]
    assert 'if (addSync !== mine) return;' in guard_after, \
        'nothing re-checks identity after the await; a superseded refusal can land on the new wait'


def test_the_add_row_is_computed_in_exactly_one_place():
    """It was set in `renderSuggestions` and then one-way overridden in `paintAddSync`, so a failed
    sync left a live submit button labelled with the PREVIOUS query until the next debounced refetch
    caught up."""
    js = _js('static/js/navbar-search.js')

    assert js.count('addBtn.hidden =') == 1, 'the add row has two writers again'
    assert 'function paintAddRow()' in js


def test_a_finished_sync_can_be_acted_on_and_a_stalled_one_retried():
    """The submit short-circuit was wrong at both ends.

    `rest` (still syncing after five minutes) was caught by it, so that state could not be retried at
    all: Enter did nothing, refocus did nothing, the add row is hidden for `rest`, and the list was
    emptied on submit -- one sentence, forever, escapable only by Clear.

    `ready` fell THROUGH it, so an Enter that did not land on the Visit row (the cursor may be -1,
    since `focusVisitRow(true)` declines to steal a driven one) abandoned the resolved state and
    re-POSTed, replacing the link with "Looking up X..." and spending one of the three-a-minute anon
    budget re-syncing a hunter already found.
    """
    js = _js('static/js/navbar-search.js')
    # SCOPED to the submit handler. The in-flight phase pair also appears in `paintAddSync`'s
    # `busy` check, so an unscoped assertion matched THAT occurrence and stayed green while the
    # short-circuit it was meant to pin had been mutated back to trapping `rest`. Found by mutation
    # testing, not by reading it.
    submit = js.split("form.addEventListener('submit', (e) => {", 1)[1].split("\n    });", 1)[0]

    # Resolved: Enter navigates.
    assert "addSync.phase === 'ready' && addSync.href" in submit
    assert 'window.location.href = addSync.href;' in submit, \
        'Enter on a resolved sync no longer opens it'
    # In flight: short-circuit. NOT `addSyncOwnsRow`, which also covers `rest` and `error`.
    assert "(addSync.phase === 'waiting' || addSync.phase === 'syncing')" in submit, \
        'the short-circuit is back to a phase set that traps `rest`'
    assert 'addSyncOwnsRow' not in submit, \
        'the short-circuit uses the row gate, which traps `rest`'


def test_the_keyboard_cursor_attribute_follows_the_cursor():
    """`aria-activedescendant` was written only by `setActive` and cleared only by `closePanel`, so a row
    leaving `items` while the panel stayed OPEN left the combobox naming a hidden element -- reachable by
    arrowing onto the add row and pressing Enter, since the keydown handler only intercepts anchors and
    so falls through to a submit that hides that row."""
    body = _body(_js('static/js/navbar-search.js'), 'function refreshItems() {')

    assert "input.removeAttribute('aria-activedescendant')" in body, \
        'the attribute can outlive the element it names'


def test_a_failed_sync_hands_back_the_retry_row():
    """The copy tells a hunter to check the spelling and try again, so the control that would has to be
    on screen. Suppressing the row for every non-null phase hid it exactly when it was wanted."""
    body = _body(_js('static/js/navbar-search.js'), 'function addSyncOwnsRow(q) {')

    assert "addSync.phase !== 'error'" in body


def test_the_announcer_is_painted_from_state_not_fired_per_transition():
    """The sr-only line is the ONLY channel that reaches a screen reader: `ToastManager` appends a plain
    div and the toast container has no `aria-live`.

    Driving it from `paintAddSync` rather than `setAddSync` is what makes it consistent with the screen
    in every path: it clears itself on Clear (state gone), and it updates when a resolved-out-of-view
    sync is brought back into view by retyping the name -- which previously left the region still
    instructing the hunter to do the thing they had just done.
    """
    js = _js('static/js/navbar-search.js')

    assert 'announce(announcementFor())' in _body(js, 'function paintAddSync() {'), \
        'the announcer is no longer painted from state'
    # And it must no-op on an unchanged string, or an idempotent paint running every 2.5s re-announces
    # "Syncing X" for the whole five minutes.
    assert 'liveRegion.textContent !== next' in _body(js, 'function announce(message) {'), \
        'a repaint can re-announce the same sentence'


def test_the_announcement_gate_accounts_for_a_CLOSED_panel():
    """`statusVisible()` means "the field still holds this name", NOT "this is on screen". The two differ
    exactly when the panel is shut, which is the commonest way to wait out a first sync -- and gating on
    the wrong one meant a sync landing after an Escape announced the on-screen sentence while the Visit
    link it refers to was invisible, so the one sentence naming the way back reached nobody.

    The earlier version of this pin asserted only that the gate MENTIONED `statusVisible()`, which it
    did, while being wrong. Pin the panel check instead.
    """
    js = _js('static/js/navbar-search.js')

    assert '!panel.hidden' in _body(js, 'function inView() {'), \
        'inView() no longer distinguishes a closed panel'
    gate = _body(js, 'function announcementFor() {')
    assert 'inView()' in gate, 'the gate is back on statusVisible(), which ignores a closed panel'
    assert 'statusVisible()' not in gate
    assert "phase === 'ready'" in gate and "phase === 'error'" in gate


def test_a_failure_out_of_view_reaches_a_sighted_hunter_too():
    """Only `ready` toasted, so an `error` or a five-minute `rest` reached after the bar moved on was
    written to the sr-only region and nowhere else: screen-reader users were better served than sighted
    ones, the inverse of the usual gap."""
    js = _js('static/js/navbar-search.js')

    assert js.count('const seen = inView();') == 3, \
        'a terminal phase does not check visibility before deciding whether to toast'
    assert 'ToastManager.error(addSync.query' in js, 'an out-of-view failure is silent for sighted users'
    assert 'ToastManager.info(addSync.query' in js, 'an out-of-view poll cap is silent for sighted users'


def test_the_ready_sentence_has_one_source():
    """The toast and the announced line both promise "search the name again". Two literals would drift,
    and the announced half is the only one a screen reader can reach."""
    js = _js('static/js/navbar-search.js')

    assert js.count('readyAway:') == 1
    assert js.count('COPY.readyAway(addSync.query)') == 2, 'the toast and the announcer share one string'


def test_the_shared_refusal_reader_is_feature_tested_at_every_call_site():
    """A browser can hold a cached pre-change `utils.js` against a fresh controller. Every call site is
    inside a `.catch`, where a missing method throws from within a rejection handler: the navbar strands
    its spinner forever, navsync leaves the button disabled reading "Syncing...", quick-add loses the
    toast outright. `quick-add.js` is the pointed case -- its body was self-contained and could not fail
    at all until this branch extracted it, so the guard restores what the extraction took away."""
    nav = _js('static/js/navbar-search.js')
    assert 'api && api.failureOr' in nav
    # Falsifiable: BOTH occurrences of the name in this file are the guard and the call it guards. The
    # previous form forbade the fully-qualified `PlatPursuit.API.failureOr(`, which appears in neither
    # the correct code nor any plausible careless rewrite (the file uses a local `api` alias), so it
    # could not fail.
    assert nav.count('api.failureOr') == 2, 'a call to failureOr is not paired with its guard'

    sync = _js('static/js/navsync.js')
    assert 'api && api.failureOr' in sync
    assert sync.count('api.failureOr') == 2

    assert 'PP.API.failureMessage ?' in _js('static/js/quick-add.js'), \
        'the delegation can throw from inside a catch'


def test_the_guards_do_not_hide_the_loss_of_the_helper_itself():
    """The necessary complement to feature-testing every call site: with all three guarded, deleting or
    renaming `API.failureOr` raises nothing and fails nothing, and every server refusal in the navbar
    and the sync panel silently reverts to its generic fallback forever. `failureMessage` is pinned by
    `test_gamelists_actions.py`; `failureOr` was pinned nowhere."""
    utils = _js('static/js/utils.js')

    assert 'async failureOr(err, fallback)' in utils, 'the shared fallback reader is gone'
    assert 'async failureMessage(err)' in utils
    # The one thing a caller actually depends on: it reads the server's `error` key. A rewrite returning
    # `data.message` or `data.detail` would keep every other assertion green while reverting every
    # refusal in the app to its fallback.
    assert "data.error) || null" in utils, 'failureMessage no longer reads the server\'s `error` key'
    # And the two tolerances that make a `.catch` caller safe, which moved here out of quick-add.js:
    # a non-API error (no `.response`) and a non-JSON body (an HTML error page).
    body = _body(utils, 'async failureMessage(err) {')
    assert "typeof err.response.json !== 'function'" in body, 'a non-API error now throws'
    assert 'catch' in body, 'an HTML error page now throws'
