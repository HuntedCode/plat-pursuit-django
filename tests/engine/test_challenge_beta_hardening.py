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


def test_hiding_a_finished_calendar_does_not_reopen_it():
    profile = _hunter()
    run = _finish_calendar(svc.start(profile, CHALLENGE_TYPE_CALENDAR))
    svc.hide(run, profile)

    with pytest.raises(svc.ChallengeError, match='already finished'):
        svc.start(profile, CHALLENGE_TYPE_CALENDAR)


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

def test_head_requests_spend_the_picker_budget():
    """`View.setup` aliases `head` to the wrapped `get`, so `method='GET'` alone let a HEAD run the pool
    scan unmetered. Measured by behaviour: a source-text pin cannot prove what a decorator counts."""
    client = Client()
    profile = _hunter(client)
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    url = reverse('challenge_slot', args=[run.id, 'A'])
    statuses = [client.head(url).status_code for _ in range(91)]
    assert statuses[:90] == [200] * 90
    assert statuses[90] == 429, 'the 91st HEAD in a minute was not refused'


def test_a_tripped_write_limit_answers_json_the_sheet_can_show():
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


def test_a_tripped_start_limit_is_said_on_the_page():
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
    panel = _fn('renderHistoryPanel')
    assert 'ask(button,' in panel
    assert 'assign(picked.slug, row.key, true, button, true)' in panel
    assert 'assign(picked.slug, row.key, false' not in panel


def test_an_import_keeps_the_sheet_open_on_the_history_list():
    body = _fn('assign')
    assert 'if (stay && stillOpen() && !slot.is_complete)' in body
    stay = body[body.index('if (stay && stillOpen()'):]
    stay = stay[:stay.index('return;')]
    assert 'applySlot(slot, null)' in stay, 'a toast behind the modal would announce nothing'
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
