"""The Challenge Optimizer's autosaved plan: what a built plan keeps, what Apply forgets, and what the page
reopens ("Open my plan", owner 2026-10-16)."""
import json

import pytest
from django.urls import reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, OptimizerDraft
from challenges.services import allowance, optimizer_drafts, planner
from challenges.services import challenge_service as svc
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract

pytestmark = pytest.mark.django_db

AZ = CHALLENGE_TYPE_AZ
_SEQ = {'n': 0}


def _contract(name):
    _SEQ['n'] += 1
    contract = Contract.objects.create(name=name, slug='dr-%d' % _SEQ['n'], is_live=True, igdb_id=850_000 + _SEQ['n'])
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    GameFactory(concept=concept, title_platform=['PS5'])
    return contract


def _hunter(client, *, premium=True):
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=premium, is_linked=True)
    client.force_login(user)
    return profile


def _options(**kw):
    kw.setdefault('badge_started', False)
    kw.setdefault('types', (AZ,))
    return planner.PlannerOptions(**kw)


def _tweaks(**kw):
    return {'pins': [], 'square_pins': [], 'excluded': [], 'penalized': [], **kw}


def _plan(client, **payload):
    payload.setdefault('types', [AZ])
    payload.setdefault('badge_started', False)
    return client.post(reverse('challenge_optimizer_plan'), data=json.dumps(payload), content_type='application/json')


# --- the service ------------------------------------------------------------------------------------------

def test_a_saved_plan_reopens_with_its_answers_and_named_must_haves():
    profile = ProfileFactory()
    pinned = _contract('Astro Bot')
    optimizer_drafts.save(profile, _options(platforms=('PS5',), badge_weight=planner.WEIGHT_ONLY),
                          _tweaks(pins=[pinned.slug, 'gone-since'], excluded=['x']))

    found = optimizer_drafts.to_reopen(profile)

    assert found['answers']['platforms'] == ['PS5'] and found['answers']['badge_weight'] == planner.WEIGHT_ONLY
    # A must-have that stopped resolving is dropped; the rest come back named for their chips.
    assert found['tweaks']['pins'] == [{'slug': pinned.slug, 'name': 'Astro Bot'}]
    assert found['tweaks']['excluded'] == ['x'] and found['updated_at'] is not None


def test_nothing_to_reopen_without_a_saved_plan():
    assert optimizer_drafts.to_reopen(ProfileFactory()) is None


def test_saving_again_keeps_only_the_latest_plan():
    profile = ProfileFactory()
    optimizer_drafts.save(profile, _options(platforms=('PS4',)), _tweaks())
    optimizer_drafts.save(profile, _options(platforms=('PS5',)), _tweaks())
    assert OptimizerDraft.objects.filter(profile=profile).count() == 1
    assert optimizer_drafts.to_reopen(profile)['answers']['platforms'] == ['PS5']


def test_applied_games_leave_the_saved_must_haves_and_locks_only():
    profile = ProfileFactory()
    lock = {'type': AZ, 'key': 'A', 'contract': 'placed'}
    other = {'type': AZ, 'key': 'B', 'contract': 'still-planned'}
    optimizer_drafts.save(profile, _options(), _tweaks(pins=['placed', 'still-pinned'], square_pins=[lock, other],
                                                         excluded=['placed'], penalized=['rerolled']))

    optimizer_drafts.forget_applied(profile, {'placed'})

    tweaks = OptimizerDraft.objects.get(profile=profile).tweaks
    assert tweaks['pins'] == ['still-pinned'] and tweaks['square_pins'] == [other]
    # "Not this one" and rerolls are the hunter's word on the plan, not on the board: they stay.
    assert tweaks['excluded'] == ['placed'] and tweaks['penalized'] == ['rerolled']


def test_forgetting_with_nothing_applied_or_no_draft_writes_nothing():
    profile = ProfileFactory()
    optimizer_drafts.forget_applied(profile, {'anything'})   # no draft: no row appears
    assert not OptimizerDraft.objects.filter(profile=profile).exists()
    optimizer_drafts.save(profile, _options(), _tweaks(pins=['a']))
    before = OptimizerDraft.objects.get(profile=profile).updated_at
    optimizer_drafts.forget_applied(profile, set())
    optimizer_drafts.forget_applied(profile, {'not-in-it'})
    assert OptimizerDraft.objects.get(profile=profile).updated_at == before


def test_a_damaged_saved_row_reads_as_empty_tweaks_not_a_500():
    profile = ProfileFactory()
    OptimizerDraft.objects.create(profile=profile, answers={'types': [AZ]},
                                  tweaks={'pins': 'oops', 'square_pins': [{'type': AZ}, 7], 'excluded': [1, 'ok']})
    found = optimizer_drafts.to_reopen(profile)
    assert found['tweaks'] == {'pins': [], 'square_pins': [], 'excluded': ['ok'], 'penalized': []}


def test_a_free_hunters_spent_week_reopens_that_plan_with_the_drafts_tweaks_when_they_match():
    profile = ProfileFactory(user_is_premium=False)
    spent = allowance.answers_of(_options(platforms=('PS5',)))
    optimizer_drafts.save(profile, _options(platforms=('PS5',), badge_weight=planner.WEIGHT_PRIORITY),
                          _tweaks(excluded=['x']))

    found = optimizer_drafts.to_reopen(profile, spent_answers=spent, active_types={AZ})

    # The same plan (the weight is not part of it): its tweaks and its latest weight come back.
    assert found['tweaks']['excluded'] == ['x'] and found['answers']['badge_weight'] == planner.WEIGHT_PRIORITY


def test_a_free_hunters_spent_week_never_reopens_a_draft_on_other_answers():
    profile = ProfileFactory(user_is_premium=False)
    spent = allowance.answers_of(_options(platforms=('PS5',)))
    optimizer_drafts.save(profile, _options(platforms=('PS4',)), _tweaks(excluded=['x']))

    found = optimizer_drafts.to_reopen(profile, spent_answers=spent, active_types={AZ})

    # Only the answers are known: no tweaks (the page then keeps the hunter's typed must-haves), no date.
    assert found['answers']['platforms'] == ['PS5'] and found['tweaks'] is None
    assert found['updated_at'] is None


# --- the doors --------------------------------------------------------------------------------------------

def test_every_built_plan_is_autosaved_with_its_valid_tweaks(client):
    profile = _hunter(client)
    pinned = _contract('Astro Bot')
    svc.start(profile, AZ)

    resp = _plan(client, platforms=['PS5'], tweaks={
        'pins': [pinned.slug, 'not-a-contract'], 'excluded': [pinned.slug, pinned.slug],
        'square_pins': [{'type': AZ, 'key': 'A', 'contract': pinned.slug}, {'type': AZ, 'key': 'B', 'contract': 'nope'}]})

    assert resp.status_code == 200
    draft = OptimizerDraft.objects.get(profile=profile)
    assert draft.answers['platforms'] == ['PS5']
    assert draft.tweaks == {'pins': [pinned.slug], 'excluded': [pinned.slug], 'penalized': [],
                            'square_pins': [{'type': AZ, 'key': 'A', 'contract': pinned.slug}]}


def test_a_refused_plan_saves_nothing(client):
    profile = _hunter(client)
    assert _plan(client).status_code == 400   # no run to plan
    svc.start(profile, AZ)
    assert _plan(client, badge_weight='maximum').status_code == 400
    assert not OptimizerDraft.objects.filter(profile=profile).exists()


def test_a_free_hunter_who_has_not_confirmed_saves_nothing(client):
    from django.test import override_settings
    with override_settings(CHALLENGES_BETA_MEMBERS_ONLY=False):
        profile = _hunter(client, premium=False)
        svc.start(profile, AZ)
        assert _plan(client).status_code == 409
    assert not OptimizerDraft.objects.filter(profile=profile).exists()


def test_a_failed_autosave_never_costs_the_hunter_their_plan(client, monkeypatch):
    profile = _hunter(client)
    _contract('Astro Bot')
    svc.start(profile, AZ)

    def broken(*args, **kwargs):
        raise RuntimeError('db hiccup')
    monkeypatch.setattr(optimizer_drafts, 'save', broken)

    resp = _plan(client)
    assert resp.status_code == 200 and json.loads(resp.content)['lines']


def test_apply_keeps_the_saved_plan_minus_what_it_placed(client):
    profile = _hunter(client)
    placed = _contract('Astro Bot')
    svc.start(profile, AZ)
    optimizer_drafts.save(profile, _options(), _tweaks(pins=[placed.slug], excluded=['kept']))

    resp = client.post(reverse('challenge_optimizer_apply'), content_type='application/json',
                       data=json.dumps({'lines': [{'type': AZ, 'key': 'A', 'contract': placed.slug}]}))

    assert json.loads(resp.content)['applied'] == 1
    tweaks = OptimizerDraft.objects.get(profile=profile).tweaks
    assert tweaks['pins'] == [] and tweaks['excluded'] == ['kept']


def test_a_member_with_a_saved_plan_is_offered_it(client):
    profile = _hunter(client)
    pinned = _contract('Astro Bot')
    svc.start(profile, AZ)
    optimizer_drafts.save(profile, _options(), _tweaks(pins=[pinned.slug]))

    resp = client.get(reverse('challenge_optimizer'))
    html = resp.content.decode()

    assert resp.context['saved_plan']['tweaks']['pins'] == [{'slug': pinned.slug, 'name': 'Astro Bot'}]
    resume = html[html.index('data-cplan-resume'):html.index('data-cplan-wizard')]
    assert 'hidden' not in resume.split('>')[0] and 'Pick up where you left off' in resume
    assert 'data-cplan-open-saved' in resume and 'id="cplan-saved"' in html


def test_a_member_with_nothing_saved_gets_no_resume_card(client):
    profile = _hunter(client)
    svc.start(profile, AZ)
    resp = client.get(reverse('challenge_optimizer'))
    html = resp.content.decode()
    assert resp.context['saved_plan'] is None
    assert html[html.index('data-cplan-resume'):].split('>')[0].endswith('hidden')


def test_a_free_hunter_with_last_weeks_plan_can_reopen_it_from_the_banner(client):
    from django.test import override_settings
    with override_settings(CHALLENGES_BETA_MEMBERS_ONLY=False):
        profile = _hunter(client, premium=False)
        svc.start(profile, AZ)
        optimizer_drafts.save(profile, _options(), _tweaks())
        html = client.get(reverse('challenge_optimizer')).content.decode()
    # Reopening it on a fresh week spends that week's plan, so the page still asks first (the script's job).
    assert 'data-allowance="available"' in html
    button = html[html.index('data-cplan-open-saved'):].split('>')[0]
    assert 'hidden' not in button
    assert 'data-cplan-resume' not in html   # members' card only


def test_a_failed_cleanup_never_reports_a_done_apply_as_failed(client, monkeypatch):
    profile = _hunter(client)
    placed = _contract('Astro Bot')
    svc.start(profile, AZ)

    def broken(*args, **kwargs):
        raise RuntimeError('db hiccup')
    monkeypatch.setattr(optimizer_drafts, 'forget_applied', broken)

    resp = client.post(reverse('challenge_optimizer_apply'), content_type='application/json',
                       data=json.dumps({'lines': [{'type': AZ, 'key': 'A', 'contract': placed.slug}]}))
    assert resp.status_code == 200 and json.loads(resp.content)['applied'] == 1


def test_damaged_saved_answers_never_500_the_page():
    from challenges.optimizer_views import _with_pick_names
    assert _with_pick_names({'badge_picks': 5}) == {'badge_picks': 5}
    assert _with_pick_names('nonsense') == {}


def test_forgetting_applied_games_locks_the_draft_row():
    # Read, edit, write: without the row lock, a plan autosaving from another tab in between would have this
    # draft's old tweaks written over its own.
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    profile = ProfileFactory()
    optimizer_drafts.save(profile, _options(), _tweaks(pins=['placed']))
    with CaptureQueriesContext(connection) as captured:
        optimizer_drafts.forget_applied(profile, {'placed'})
    reads = [q['sql'] for q in captured.captured_queries if 'optimizerdraft' in q['sql'].lower() and 'SELECT' in q['sql']]
    assert reads and all('FOR UPDATE' in sql for sql in reads)
