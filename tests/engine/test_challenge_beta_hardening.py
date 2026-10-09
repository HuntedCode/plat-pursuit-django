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
    assert statuses[90] == 403, 'the 91st HEAD in a minute was not refused'
