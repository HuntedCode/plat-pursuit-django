"""The fixes from the beta release audit (chunk 12) that are not about how a page looks.

Each test is the failure an auditor described, reproduced:

  ONE CALENDAR RUN, FOR GOOD. Every Calendar run reads the same history, so for a hunter whose history
  fills all 365 days each Start minted a finished run -- a Hall of Fame entry and a notification per press.

  A CAUGHT DATABASE ERROR MUST NOT ROLL BACK THE WORK AROUND IT. The title grants ran in a bare try/except
  inside the fill's transaction; after a database error Postgres refuses every later statement, so the
  "contained" failure took the whole fill with it at commit.

  ONE BAD SQUARE MUST NOT STOP THE OTHERS, on the sync path or in the nightly sweep.

  START MUST NOT BLOCK A SYNC. A whale's fill now runs inside Start's profile lock.

  THE HUB'S "MOST PROGRESS" COMPARES SHARES, NOT COUNTS.

  A JOB COVERAGE SQUARE THAT FINISHES ON PLACEMENT REFRESHES THE REWARD PANEL.

  THE PICKER'S READ DOORS METER HEAD.
"""
import datetime as dt
import re
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from challenges.models import (CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_CALENDAR, CHALLENGE_TYPE_JOBS, CalendarDay,
                               Challenge)
from challenges.services import calendar_fill, rewards
from challenges.services import challenge_service as svc
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract, EarnedContract, Job

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
_SEQ = {'n': 0}


def _hunter(client=None):
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=True, is_linked=True)
    if client is not None:
        client.force_login(user)
    return profile


def _contract(name, *, jobs=()):
    _SEQ['n'] += 1
    c = Contract.objects.create(name=name, slug='bh-%d' % _SEQ['n'], is_live=True, igdb_id=760_000 + _SEQ['n'])
    if jobs:
        c.jobs.set(jobs)
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=c.igdb_id, status='accepted')
    GameFactory(concept=concept)
    return c


@pytest.fixture
def frozen_window(monkeypatch):
    """django_ratelimit counts in FIXED windows keyed on `int(time.time())`, so a test that spends a whole
    minute's budget fails at random if it happens to straddle a window boundary. Pin the limiter's clock."""
    import django_ratelimit.core as rl_core

    now = rl_core.time.time()
    monkeypatch.setattr(rl_core.time, 'time', lambda: now)


def _finish_calendar(run):
    CalendarDay.objects.filter(challenge=run).update(in_all=True, in_clean=True, plat_count=1,
                                                     earned_on=dt.date(2020, 1, 1))
    calendar_fill._recount_calendar(run)
    run.refresh_from_db()
    assert run.is_complete
    return run


def _db_error(*args, **kwargs):
    """A real DATABASE error, which is what aborts a Postgres transaction -- a plain raise would not."""
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1 / 0')


# ── one Calendar run, for good ───────────────────────────────────────────────────────────────────

def test_a_finished_calendar_cannot_be_started_again():
    profile = _hunter()
    _finish_calendar(svc.start(profile, CHALLENGE_TYPE_CALENDAR))

    with pytest.raises(svc.ChallengeError, match='already finished'):
        svc.start(profile, CHALLENGE_TYPE_CALENDAR)
    assert Challenge.objects.filter(profile=profile, challenge_type=CHALLENGE_TYPE_CALENDAR).count() == 1


def test_a_hidden_finished_calendar_comes_back_through_start_and_is_never_copied():
    """Hidden runs keep filling, so one can finish while hidden. Start brings THAT run back (the only way back
    to the profile and the Hall of Fame) rather than refusing, and never deals a second copy."""
    profile = _hunter()
    run = _finish_calendar(svc.start(profile, CHALLENGE_TYPE_CALENDAR))
    svc.hide(run, profile)

    back, outcome = svc.start_reporting(profile, CHALLENGE_TYPE_CALENDAR)
    assert back.pk == run.pk and outcome == svc.RESUMED
    back.refresh_from_db()
    assert back.is_deleted is False
    assert Challenge.objects.filter(profile=profile, challenge_type=CHALLENGE_TYPE_CALENDAR).count() == 1
    with pytest.raises(svc.ChallengeError, match='already finished'):
        svc.start(profile, CHALLENGE_TYPE_CALENDAR)


def test_a_hidden_finished_calendar_card_offers_resume_not_share():
    client = Client()
    profile = _hunter(client)
    run = _finish_calendar(svc.start(profile, CHALLENGE_TYPE_CALENDAR))
    svc.hide(run, profile)
    body = client.get(reverse('my_challenges')).content.decode()
    card = body[body.index('>Plat Calendar</h2>'):]
    card = card[:card.index('</section>')]
    assert '>Hidden</span>' in card and 'Resume' in card
    assert 'data-challenge-share' not in card, 'the share endpoints refuse a hidden run'


def test_the_import_confirmation_leads_every_history_render():
    panel = _fn('renderHistoryPanel')
    assert panel.index("pendingNote = '';") < panel.index('dropPrompts();'), 'cleared before the branches'
    assert panel.count('say(lead + ') == 3, 'a branch says something without the import confirmation'
    catch = _fn('loadHistory')
    assert "pendingNote = '';" in catch[catch.index('.catch('):]


def test_focus_moves_only_when_the_square_had_it():
    assert 'var hadFocus = document.activeElement === square;' in _fn('applySlot')


def test_a_hand_typed_search_on_an_empty_site_keeps_its_toolbar():
    body = Client().get(reverse('challenges') + '?q=a').content.decode()
    assert 'aria-label="Challenge type"' in body, 'a short query is not a filter, and left no way to clear it'


def test_the_picked_types_still_allow_a_second_run():
    """A-Z and Job Coverage are picked square by square, so a second run is genuinely new."""
    profile = _hunter()
    first = svc.start(profile, CHALLENGE_TYPE_AZ)
    Challenge.objects.filter(pk=first.pk).update(is_complete=True, completed_at=timezone.now())
    second = svc.start(profile, CHALLENGE_TYPE_AZ)
    assert second.pk != first.pk


def test_my_challenges_shows_the_finished_calendar_in_place_of_start():
    client = Client()
    profile = _hunter(client)
    run = _finish_calendar(svc.start(profile, CHALLENGE_TYPE_CALENDAR))

    body = client.get(reverse('my_challenges')).content.decode()
    card = body[body.index('>Plat Calendar</h2>'):]
    card = card[:card.index('</section>')]
    assert 'href="%s"' % reverse('challenge_detail', args=[run.id]) in card
    assert '>Finished</span>' in card
    assert reverse('challenge_start', args=[CHALLENGE_TYPE_CALENDAR]) not in card, 'Start offered for a closed type'
    assert 'finished before' not in card


# ── a caught database error keeps the work around it ─────────────────────────────────────────────

def test_a_failed_title_grant_does_not_roll_back_the_fill(monkeypatch):
    profile = _hunter()
    run = svc.start_reporting(profile, CHALLENGE_TYPE_CALENDAR, backfill=False)[0]
    CalendarDay.objects.filter(challenge=run, month=1).update(in_all=True, in_clean=True, plat_count=1,
                                                              earned_on=dt.date(2020, 1, 1))
    monkeypatch.setattr(rewards, 'grant_day_markers', _db_error)

    calendar_fill.apply_to_run(run)

    run.refresh_from_db()
    assert run.filled_count == 31, 'the grant failure rolled the recount back with it'


def test_a_failed_completion_title_does_not_roll_back_the_finish(monkeypatch):
    profile = _hunter()
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    monkeypatch.setattr(rewards, 'grant_completion_title', _db_error)
    letters = [slot.key for slot in run.slots.order_by('position')]
    for letter in letters:
        slot = svc.assign(run, profile, letter, _contract('%s game' % letter))
        svc.mark_slot_completed(slot)

    run.refresh_from_db()
    assert run.is_complete and run.completed_count == len(letters)


# ── one bad square does not stop the others ──────────────────────────────────────────────────────

def _two_finishable_squares(profile):
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    slots = []
    for letter in 'AB':
        c = _contract('%s game' % letter)
        slots.append(svc.assign(run, profile, letter, c))
        EarnedContract.objects.create(profile=profile, contract=c, has_platinum=True,
                                      platinum_reached_at=timezone.now())
    return run, slots


def _fail_first(real):
    seen = []

    def fake(slot, **kwargs):
        seen.append(slot.pk)
        if len(seen) == 1:
            raise RuntimeError('odd data')
        return real(slot, **kwargs)
    return fake


def test_the_sync_path_completes_the_squares_after_a_bad_one(monkeypatch):
    profile = _hunter()
    run, slots = _two_finishable_squares(profile)
    monkeypatch.setattr(svc, 'mark_slot_completed', _fail_first(svc.mark_slot_completed))

    assert svc.detect_for_profile(profile) == 1
    assert run.slots.filter(is_completed=True).count() == 1


def test_the_nightly_sweep_survives_a_bad_square_and_still_runs_the_calendar(monkeypatch, capsys):
    profile = _hunter()
    _two_finishable_squares(profile)
    monkeypatch.setattr(svc, 'mark_slot_completed', _fail_first(svc.mark_slot_completed))

    call_command('process_challenges')

    out = capsys.readouterr().out
    assert 'failed' in out and 'completed' in out
    assert 'calendar' in out.lower(), 'the Calendar phase never ran'


# ── Start does not block a sync ──────────────────────────────────────────────────────────────────

def test_start_takes_a_no_key_lock_on_the_profile():
    """FOR UPDATE conflicts with the FOR KEY SHARE every foreign-key insert takes, so a whale's fill inside
    Start held up their own sync's trophy inserts. FOR NO KEY UPDATE still serialises two Starts."""
    profile = _hunter()
    with CaptureQueriesContext(connection) as ctx:
        svc.start(profile, CHALLENGE_TYPE_CALENDAR)
    profile_locks = [q['sql'] for q in ctx.captured_queries
                     if 'FOR' in q['sql'] and 'trophies_profile' in q['sql'].split('FROM', 1)[-1][:60]]
    assert profile_locks, 'Start no longer locks the profile'
    assert all('FOR NO KEY UPDATE' in sql for sql in profile_locks), profile_locks


# ── the hub compares shares ──────────────────────────────────────────────────────────────────────

def test_most_progress_ranks_by_share_not_by_count():
    """100 of 365 days is 27%; 20 of 26 letters is 77%. The raw count put every Calendar run first."""
    cal = svc.start_reporting(_hunter(), CHALLENGE_TYPE_CALENDAR, backfill=False)[0]
    Challenge.objects.filter(pk=cal.pk).update(filled_count=100, completed_count=100)
    az = svc.start(_hunter(), CHALLENGE_TYPE_AZ)
    Challenge.objects.filter(pk=az.pk).update(filled_count=20, completed_count=20)

    body = Client().get(reverse('challenges') + '?sort=progress').content.decode()
    assert body.index(reverse('challenge_detail', args=[az.id])) < \
        body.index(reverse('challenge_detail', args=[cal.id]))


# ── a Job Coverage square that finishes refreshes the panel ──────────────────────────────────────

def test_a_finished_job_square_carries_the_reward_panel():
    from challenges.views import _slot_json

    profile = _hunter()
    run = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.get(slug=run.slots.order_by('position').first().key)
    slot = svc.assign(run, profile, job.slug, _contract('Job game', jobs=[job]))
    assert 'rewards_html' not in _slot_json(run, slot), 'an unfinished square sent the panel'

    svc.mark_slot_completed(slot)
    slot.refresh_from_db()
    payload = _slot_json(run, slot)
    assert 'data-cpay-claim' in payload['rewards_html'], 'the owed square has no Claim button in the new panel'


def test_a_finished_letter_square_sends_no_panel():
    from challenges.views import _slot_json

    profile = _hunter()
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    slot = svc.assign(run, profile, 'A', _contract('A game'))
    svc.mark_slot_completed(slot)
    slot.refresh_from_db()
    assert 'rewards_html' not in _slot_json(run, slot), 'A-Z pays no XP, so its panel never changes'


def test_the_picker_swaps_the_panel_it_is_sent():
    js = (ROOT / 'static' / 'js' / 'challenge-detail.js').read_text(encoding='utf-8')
    body = js[js.index('function applySlot(slot, message)'):]
    body = body[:body.index('THE SHELF\'S OWN COUNTER')]
    assert 'slot.rewards_html' in body and "querySelector('[data-cpay-wrap]')" in body
    assert 'payWrap.innerHTML = slot.rewards_html' in body


# ── the picker's read doors meter HEAD ───────────────────────────────────────────────────────────

def test_head_requests_spend_the_picker_budget(frozen_window):
    """`View.setup` aliases `head` to the wrapped `get`, so `method='GET'` alone let a HEAD run the pool
    scan unmetered. Measured by behaviour: a source-text pin cannot prove what a decorator counts."""
    client = Client()
    profile = _hunter(client)
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    url = reverse('challenge_slot', args=[run.id, 'A'])
    statuses = [client.head(url).status_code for _ in range(91)]
    assert statuses[:90] == [200] * 90
    assert statuses[90] == 429, 'the 91st HEAD in a minute was not refused'


def test_a_tripped_write_limit_answers_json_the_sheet_can_show(frozen_window):
    """A blocked request used to render the HTML 403 page, so the sheet had no `error` to read and could only
    say "That did not save." A JSON 429 carries the reason."""
    from challenges.views import RATE_LIMITED_MESSAGE

    client = Client()
    profile = _hunter(client)
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    url = reverse('challenge_clear', args=[run.id, 'A'])
    for _ in range(30):     # the whole minute's budget (`svc.start` above is the service, not the door)
        client.post(url)
    resp = client.post(url)
    assert resp.status_code == 429
    assert resp['Content-Type'].startswith('application/json')
    assert resp.json()['error'] == RATE_LIMITED_MESSAGE


def test_a_tripped_start_limit_is_said_on_the_page(frozen_window):
    """Start is a FORM, so its refusal is a message on the page it returns to, never a JSON body."""
    from challenges.views import RATE_LIMITED_MESSAGE

    client = Client()
    _hunter(client)
    url = reverse('challenge_start', args=[CHALLENGE_TYPE_AZ])
    for _ in range(30):
        client.post(url)
    resp = client.post(url, follow=True)
    assert resp.status_code == 200
    assert RATE_LIMITED_MESSAGE.replace("'", '&#x27;') in resp.content.decode() or \
        RATE_LIMITED_MESSAGE in resp.content.decode()


# ── the run page's picker and sheets (batch 2 of the audit) ─────────────────────────────────────

JS = (ROOT / 'static' / 'js' / 'challenge-detail.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'css' / 'components' / 'challenges.css').read_text(encoding='utf-8')


def _fn(name):
    """One function's source, from its declaration to the next one at the same depth."""
    start = JS.index('function %s(' % name)
    nxt = JS.find('\n        function ', start + 10)
    return JS[start:nxt if nxt != -1 else len(JS)]


def test_a_history_import_asks_first_and_writes_once():
    """An import always locks its square, so the server's 409 was certain: two writes per letter against a
    30-a-minute budget. The question is asked in the sheet and the write goes out already confirmed."""
    # THE ROWS ARE BUILT BY `appendHistoryRows` since the panel became paged (2026-10-10), for the first page
    # and every later one alike, so the rule is pinned where every history offer is made.
    assert 'appendHistoryRows(panel);' in _fn('renderHistoryPanel')
    rows = _fn('appendHistoryRows')
    assert 'ask(button,' in rows
    assert 'assign(picked.slug, row.key, true, button, true)' in rows
    assert 'assign(picked.slug, row.key, false' not in rows


def test_an_import_keeps_the_sheet_open_on_the_history_list():
    body = _fn('assign')
    assert 'if (stay && stillOpen() && !slot.is_complete)' in body
    stay = body[body.index('if (stay && stillOpen()'):]
    # TO THE CLOSE, not to the first `return`: since 2026-10-10 a paged history list is updated in place
    # (`dropHistoryKey`), and the reload with its `pendingNote` is the fallback when no history list is paging.
    stay = stay[:stay.index('close(function ()')]
    assert 'applySlot(slot, null)' in stay, 'a toast behind the modal would announce nothing'
    assert 'dropHistoryKey(key,' in stay
    assert 'loadHistory(' in stay
    assert 'pendingNote = ' in stay
    assert "pendingNote + ' '" in _fn('renderHistoryPanel'), 'the reload overwrites the confirmation'


def test_a_keystroke_from_before_a_reset_never_fires():
    assert 'typedToken++' in _fn('reset')
    assert 'if (queuedToken !== typedToken) { return; }' in JS
    assert 'queuedToken = typedToken;' in JS


def test_a_read_that_fails_after_dismissal_says_nothing():
    for name in ('load', 'loadHistory'):
        assert 'if (seq !== requestSeq || !stillOpen()) { return; }' in _fn(name), name


def test_confirming_a_lock_does_not_raise_the_keyboard():
    go = JS[JS.index("els.askGo.addEventListener('click'"):]
    go = go[:go.index('if (go) { go(); }')]
    assert 'dialog.focus();' in go and 'els.q.focus()' not in go


def test_focus_moves_on_when_a_square_finishes():
    body = _fn('applySlot')
    assert "grid.querySelectorAll('[data-cpick-open]')" in body
    assert 'next.focus()' in body


def test_placeholder_icons_are_contained_and_cut_lists_say_so():
    assert "row.cover_is_art === false ? ' pp-cpick__row-art--icon'" in _fn('art')
    assert 'panel.catchup_more' in _fn('renderCatchup')


def test_the_share_sheet_swipe_closes_directly():
    share = (ROOT / 'static' / 'js' / 'challenge-share.js').read_text(encoding='utf-8')
    assert 'onClose: close' not in share, 'the swipe replays the exit'
    assert 'onClose: function () { if (dlg.open) { dlg.close(); } }' in share


def test_the_picker_search_does_not_zoom_an_iphone():
    assert '@media (max-width: 767.98px) { .pp-cpick__search { font-size: 16px; } }' in CSS


def test_the_picker_controls_meet_the_44px_target():
    hist = CSS[CSS.index('.pp-cpick__histswitch {'):]
    assert 'min-height: 44px;' in hist[:hist.index('}')]
    assert '.pp-cpick__histcta { font-size: 12px; min-height: 44px; }' in CSS


def test_the_picker_panels_say_whether_a_cover_is_art():
    from challenges.views import _cover_is_art

    assert _cover_is_art(None) is False
    client = Client()
    profile = _hunter(client)
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('A game')
    rows = client.get(reverse('challenge_slot', args=[run.id, 'A'])).json()['rows']
    assert rows and all('cover_is_art' in r for r in rows)


def test_a_visitor_sees_no_xp_prompt_on_somebody_elses_run():
    owner = _hunter()
    run = svc.start(owner, CHALLENGE_TYPE_JOBS)
    job = Job.objects.get(slug=run.slots.order_by('position').first().key)
    slot = svc.assign(run, owner, job.slug, _contract('Job game', jobs=[job]))
    svc.mark_slot_completed(slot)
    url = reverse('challenge_detail', args=[run.id])

    visitor = Client().get(url).content.decode()
    assert 'pp-csq__pip' not in visitor and 'job XP to claim' not in visitor

    me = Client()
    me.force_login(owner.user)
    mine = me.get(url).content.decode()
    assert 'pp-csq__pip' in mine and 'job XP to claim' in mine


def test_the_owner_notes_sit_in_the_header_card():
    client = Client()
    profile = _hunter(client)
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.hide(run, profile)
    body = client.get(reverse('challenge_detail', args=[run.id])).content.decode()
    assert body.index('This run is hidden') < body.index('pp-csq-board'), 'the note is still below the board'


def test_the_run_page_title_has_no_em_dash():
    run = svc.start(_hunter(), CHALLENGE_TYPE_AZ)
    body = Client().get(reverse('challenge_detail', args=[run.id])).content.decode()
    title = body[body.index('<title>'):body.index('</title>')]
    assert '—' not in title


def test_the_art_flag_tells_a_cover_from_a_placeholder():
    """The picker contains a placeholder icon and crops real art, so the flag must actually say which."""
    from challenges.views import _cover_is_art

    concept = ConceptFactory(concept_icon_url='', anchor_migration_completed_at=timezone.now())
    bare = GameFactory(concept=concept, force_title_icon=True)
    assert _cover_is_art(bare) is False

    covered = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=covered, status='accepted', igdb_cover_image_id='co1abc')
    assert _cover_is_art(GameFactory(concept=covered)) is True


def test_a_square_rerendered_after_a_write_keeps_its_xp_pip():
    """Squares sent back as JSON are only ever the owner's, so they carry the pip the owner sees on load."""
    from challenges.views import _slot_json

    profile = _hunter()
    run = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.get(slug=run.slots.order_by('position').first().key)
    slot = svc.assign(run, profile, job.slug, _contract('Job game', jobs=[job]))
    svc.mark_slot_completed(slot)
    slot.refresh_from_db()
    assert 'pp-csq__pip' in _slot_json(run, slot)['html']


# ── the Calendar page (batch 3 of the audit) ────────────────────────────────────────────────────

CAL_JS = (ROOT / 'static' / 'js' / 'challenges-calendar.js').read_text(encoding='utf-8')


def _calendar_with(profile, days):
    run = svc.start_reporting(profile, CHALLENGE_TYPE_CALENDAR, backfill=False)[0]
    for (month, day), plats in days.items():
        CalendarDay.objects.filter(challenge=run, month=month, day=day).update(
            in_all=True, in_clean=True, plat_count=plats, earned_on=dt.date(2020, month, day))
    calendar_fill._recount_calendar(run)
    return run


def test_the_year_overview_shades_each_day_by_its_count():
    from challenges.services import calendar_render

    # TEN DAYS COUNTING 1 TO 10, so the 95th-percentile anchor (9) sits near the top and the middle shades.
    run = _calendar_with(_hunter(), {(1, d): d for d in range(1, 11)})
    cells = {(c['month'], c['day']): c for m in calendar_render.calendar_groups(run) for c in m['cards']}
    assert cells[(1, 1)]['heat'] == 1 and cells[(1, 10)]['heat'] == calendar_render.HEAT_LEVELS
    assert 1 < cells[(1, 4)]['heat'] < calendar_render.HEAT_LEVELS
    assert cells[(2, 2)]['heat'] == 0, 'an unfilled day carries a shade'


def test_the_overview_cells_carry_their_shade_and_a_key():
    run = _calendar_with(_hunter(), {(1, d): d for d in range(1, 11)})
    body = Client().get(reverse('challenge_detail', args=[run.id])).content.decode()
    panel = body[body.index('id="cal-month-all"'):]
    panel = panel[:panel.index('class="pp-cal__stats"')]
    # THE DAY CELLS, BEFORE THE KEY: the key's own swatches carry every level, so a whole-panel search passed
    # with no day shaded at all.
    cells = panel[:panel.index('class="pp-cal__ykey"')]
    assert 'data-heat="1"' in cells and 'data-heat="4"' in cells
    assert '>Fewer<' in panel and '>More<' in panel
    assert 'tabindex="0"' in body[body.index('id="cal-month-all"'):][:200]


def test_the_heat_bar_keeps_the_numeral_tint_from_md():
    """From `md:` a numeral sits on the cell, on a 22% tint measured for that text -- so the count moves to a
    bar in its own shadow slot instead of changing the background."""
    css = re.sub(r'/\*.*?\*/', '', CSS, flags=re.S)
    md = css[css.index('.pp-cal__ycell--on[data-heat] {'):]
    assert '22%' in md[:md.index('}')]
    assert "--yc-heat: inset 0 -4px 0 0" in css


def _ceremony(run):
    client = Client()
    client.force_login(run.profile.user)
    body = client.get(reverse('challenge_detail', args=[run.id])).content.decode()
    start = body.index('id="cal-opening"')
    return body[start:body.index('pp-calopen__go', start)]


def test_an_empty_calendar_opens_on_how_it_fills_not_on_zero():
    ceremony = _ceremony(svc.start_reporting(_hunter(), CHALLENGE_TYPE_CALENDAR, backfill=False)[0])
    assert 'It starts now' in ceremony
    assert 'Here is where you stand' not in ceremony and 'already filled' not in ceremony
    assert 'shovelware-free platinum' in ceremony


def test_the_ceremony_draws_the_climb():
    run = _calendar_with(_hunter(), {(m, d): 1 for m in (1, 2) for d in range(1, 29)})   # 56 days
    ceremony = _ceremony(run)
    assert 'data-calopen-tally data-countup="56"' in ceremony
    assert ceremony.count('pp-calopen__rung is-reached') == 1, 'only the 50 rung is reached'
    js = CAL_JS[CAL_JS.index('function playOpening(el)'):]
    js = js[:js.index('function wireOpening(first)')]
    assert 'PP.countUp(tally, CEREMONY_MS)' in js
    assert "Math.pow(1 - share, 1 / 3)" in js, 'a rung lights when the eased count passes it'
    assert CAL_JS.count('onOpened: function () { playOpening(el); }') == 2, 'real and preview both play'


def test_the_day_sheet_says_it_is_loading_and_says_when_it_fails():
    sheet = CAL_JS[CAL_JS.index("var cell = e.target.closest('[data-day-url]');"):]
    sheet = sheet[:sheet.index('// ── the side column')]
    assert "cell.setAttribute('aria-busy', 'true')" in sheet
    assert "cell.removeAttribute('aria-busy')" in sheet
    assert "PP.ToastManager.error('That day did not load. Try again.')" in sheet
    assert sheet.index('cell.focus({ preventScroll: true })') < sheet.index("open(html, cell.getAttribute('data-month'))")
    assert '.pp-cal__day--open[aria-busy="true"]' in CSS


def test_the_peek_never_wires_on_a_touch_screen():
    peek = CAL_JS[CAL_JS.index('function wireDayPeek(first)'):]
    head = peek[:peek.index('var shown = null;')]
    assert "matchMedia('(any-hover: hover) and (any-pointer: fine)').matches" in head
    assert "'First filled ' + on" in CAL_JS


def test_a_visitor_reads_the_board_in_the_third_person():
    run = _calendar_with(_hunter(), {(3, 1): 1, (3, 2): 2})
    CalendarDay.objects.filter(challenge=run, month=4, day=4).update(in_all=True, in_clean=False)
    body = Client().get(reverse('challenge_detail', args=[run.id])).content.decode()
    for phrase in ('Days you hold', 'Among your months', '>Your calendar<'):
        assert phrase not in body, phrase
    assert 'Days with any platinum' in body and 'Plat Calendar board' in body


# ── My Challenges, the hub and the Hall of Fame (batch 4 of the audit) ──────────────────────────

def _title(body):
    return body[body.index('<title>') + 7:body.index('</title>')]


def test_the_browse_titles_name_the_page_once():
    """base.html appends the site name, so a block that also wrote it read "Platinum Pursuit - Platinum Pursuit"."""
    for name, expected in (('challenges', 'Challenges'), ('challenges_hall_of_fame', 'Hall of Fame')):
        title = _title(Client().get(reverse(name)).content.decode())
        assert title.startswith(expected) and title.count('Platinum Pursuit') == 1, title
        assert '—' not in title


def test_an_empty_launch_shows_a_way_in_and_no_toolbar():
    for name in ('challenges', 'challenges_hall_of_fame'):
        body = Client().get(reverse(name)).content.decode()
        assert 'aria-label="Challenge type"' not in body, '%s draws filters over nothing' % name
        assert 'href="%s" class="pp-cta">Start a challenge' % reverse('my_challenges') in body, name


def test_a_filter_keeps_its_toolbar_so_it_can_be_cleared():
    body = Client().get(reverse('challenges') + '?q=nobody').content.decode()
    assert 'aria-label="Challenge type"' in body
    assert 'Start a challenge' not in body, 'a search that matched nobody is not an empty site'


def test_the_browse_copy_covers_all_three_types():
    from challenges.views import ChallengesBrowseView, HallOfFameView

    for view in (ChallengesBrowseView, HallOfFameView):
        assert 'square' not in ' '.join(view.EMPTY_COPY).lower(), view.__name__
    for name in ('challenges', 'challenges_hall_of_fame'):
        body = Client().get(reverse(name)).content.decode()
        meta = body[body.index('name="description"'):][:300]
        assert 'Plat Calendar' in meta, name
    results = (ROOT / 'templates' / 'challenges' / 'partials' / 'browse_results.html').read_text(encoding='utf-8')
    assert 'Try another type, or All.' in results and 'the other type' not in results


def test_the_hall_of_fame_offers_the_intro_but_never_opens_it():
    body = Client().get(reverse('challenges_hall_of_fame')).content.decode()
    assert 'data-ctut-open="challenges-intro"' in body and 'id="challenges-intro"' in body
    tag = re.search(r'<div class="pp-detail-modal pp-howto" id="challenges-intro"[^>]*>', body).group(0)
    assert 'data-auto' not in tag


def test_the_type_switcher_fits_one_row_on_a_phone():
    assert '.pp-switch--ctypes .pp-switch__chip svg { display: none; }' in CSS
    toolbar = (ROOT / 'templates' / 'challenges' / 'partials' / '_browse_toolbar.html').read_text(encoding='utf-8')
    assert 'class="pp-switch pp-switch--ctypes"' in toolbar


def test_a_calendar_card_says_when_it_moved_not_when_it_started():
    run = svc.start(_hunter(), CHALLENGE_TYPE_CALENDAR)
    body = Client().get(reverse('challenges')).content.decode()
    card = body[body.index(reverse('challenge_detail', args=[run.id])):]
    card = card[:card.index('</a>')]
    assert '>Updated ' in card and 'Started' not in card


def test_a_hall_of_fame_entry_has_a_name_of_its_own():
    profile = _hunter()
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    Challenge.objects.filter(pk=run.pk).update(is_complete=True, completed_at=timezone.now(),
                                               filled_count=26, completed_count=26)
    body = Client().get(reverse('challenges_hall_of_fame')).content.decode()
    hero = re.search(r'<a class="pp-chero[^>]*>', body, re.S).group(0)
    assert 'aria-label="' in hero, 'the link has no name of its own'
    label = hero.split('aria-label="', 1)[1].split('"', 1)[0]
    assert "'s A-Z Challenge, finished " in label, label


def test_the_card_actions_fit_three_across_on_a_laptop():
    client = Client()
    profile = _hunter(client)
    svc.start(profile, CHALLENGE_TYPE_AZ)
    body = client.get(reverse('my_challenges')).content.decode()
    assert '<span class="lg:max-xl:sr-only">Hide</span>' in body
    assert '<span class="lg:max-xl:sr-only">Share</span>' in body


def test_the_beta_card_says_member_throughout():
    client = Client()
    user = UserFactory()
    ProfileFactory(user=user, user_is_premium=False, is_linked=True)
    client.force_login(user)
    body = client.get(reverse('my_challenges')).content.decode()
    card = body[body.index('Challenges are in beta'):]
    card = card[:card.index('</section>')]
    assert 'upporter' not in card, 'the beta card mixes "supporter" with "member"'
    assert 'Become a member' in card


# ── release readiness (batch 5 of the audit) ─────────────────────────────────────────────────────

def test_robots_keeps_the_non_pages_out_and_the_pages_in():
    robots = (ROOT / 'static' / 'robots.txt').read_text(encoding='utf-8')
    rules = [ln.strip() for ln in robots.splitlines() if ln.startswith('Disallow:')]
    assert 'Disallow: /my-challenges/' in rules
    assert 'Disallow: /community/challenges/*/day/' in rules
    assert not any(r.rstrip('/').endswith('/community/challenges') for r in rules), 'the public pages are blocked'


def test_the_deploy_checklist_carries_the_calendar():
    checklist = (ROOT / 'docs' / 'design' / 'rebuild' / 'prod-deploy-checklist.md').read_text(encoding='utf-8')
    from challenges.services.rewards import CALENDAR_DAY_TITLES
    row_q = [ln for ln in checklist.splitlines() if ln.startswith('| Q |')]
    assert row_q, 'no row checks the Calendar title names'
    for name in CALENDAR_DAY_TITLES.values():
        assert name in row_q[0], name
    assert any(ln.startswith('| P |') and '0004' in ln for ln in checklist.splitlines())


def test_the_beta_launch_is_the_newest_announcement():
    from core import whats_new

    top = whats_new.ENTRIES[0]
    assert top.id == '2026-10-challenges-beta'
    assert top.link_url == '/community/challenges/', 'a signed-out archive reader would be sent to login'


def test_the_membership_perk_no_longer_says_challenges_are_coming():
    from users.constants import PREMIUM_PERKS

    beta = next(p for p in PREMIUM_PERKS if p['slug'] == 'beta')
    assert 'returns' not in beta['example'] and 'Challenges' in beta['example']


def test_the_completion_notification_promises_nothing_it_may_not_give():
    """A third finished A-Z or Job Coverage run earns no title, and a Calendar run has days, not squares."""
    import json
    rows = json.loads((ROOT / 'notifications' / 'fixtures' / 'initial_templates.json').read_text(encoding='utf-8'))
    row = next(r for r in rows if r['fields'].get('name') == 'challenge_completed')
    message = row['fields']['message_template'].lower()
    assert 'title' not in message and 'square' not in message


def test_the_run_page_has_one_line_in_the_subnav_map():
    src = (ROOT / 'core' / 'hub_subnav.py').read_text(encoding='utf-8')
    assert src.count("'challenge_detail': (") == 1, 'a duplicate dict key silently shadows the first'
