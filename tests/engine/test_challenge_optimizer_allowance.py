"""The Challenge Optimizer's weekly allowance: one free optimized plan (one SET OF ANSWERS) per rolling seven days,
members any time. The service decides; the plan door asks before spending and refuses other answers."""
import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS, OptimizerAllowance
from challenges.services import allowance, planner
from challenges.services import challenge_service as svc
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract

pytestmark = pytest.mark.django_db

AZ, JOBS = CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS
ACTIVE = {AZ, JOBS}


def _options(**kw):
    kw.setdefault('badge_started', False)
    return planner.PlannerOptions(**kw)


def _free(client=None):
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=False, is_linked=True)
    if client is not None:
        client.force_login(user)
    return profile


# --- the service ------------------------------------------------------------------------------------------

def test_members_are_never_counted():
    member = ProfileFactory(user_is_premium=True)
    assert allowance.decide(member, _options(), ACTIVE) == allowance.FREE
    allowance.spend(member, _options(), ACTIVE)
    assert not OptimizerAllowance.objects.filter(profile=member).exists()


def test_a_free_hunters_first_plan_of_the_week_must_be_spent():
    assert allowance.decide(ProfileFactory(user_is_premium=False), _options(), ACTIVE) == allowance.SPEND


def test_the_same_answers_are_free_all_week_and_other_answers_wait():
    profile = ProfileFactory(user_is_premium=False)
    allowance.spend(profile, _options(platforms=('PS5',)), ACTIVE)

    assert allowance.decide(profile, _options(platforms=('PS5',)), ACTIVE) == allowance.FREE
    assert allowance.decide(profile, _options(platforms=('PS5',), badge_weight=planner.WEIGHT_PRIORITY), ACTIVE) == allowance.FREE
    assert allowance.decide(profile, _options(platforms=('PS4',)), ACTIVE) == allowance.LOCKED


def test_the_next_free_plan_comes_seven_days_after_the_last_one():
    profile = ProfileFactory(user_is_premium=False)
    used = timezone.now() - timedelta(days=6, hours=23)
    allowance.spend(profile, _options(), ACTIVE, now=used)

    assert allowance.decide(profile, _options(platforms=('PS4',)), ACTIVE) == allowance.LOCKED
    assert allowance.state_for(profile).next_free_at == used + allowance.FREE_PLAN_WINDOW
    later = used + allowance.FREE_PLAN_WINDOW + timedelta(seconds=1)
    assert allowance.decide(profile, _options(platforms=('PS4',)), ACTIVE, now=later) == allowance.SPEND


def test_a_second_spend_on_other_answers_in_the_week_is_refused():
    profile = ProfileFactory(user_is_premium=False)
    allowance.spend(profile, _options(platforms=('PS5',)), ACTIVE)
    with pytest.raises(allowance.AllowanceLocked):
        allowance.spend(profile, _options(platforms=('PS4',)), ACTIVE)
    allowance.spend(profile, _options(platforms=('PS5',)), ACTIVE)   # its own answers again: a no-op, not an error
    assert OptimizerAllowance.objects.filter(profile=profile).count() == 1


def test_the_saved_answers_are_the_plan_doors_own_body_shape():
    profile = ProfileFactory(user_is_premium=False)
    options = _options(types=(AZ,), platforms=('PS5',), badge_all=True, badge_picks=(4, 2))
    allowance.spend(profile, options, ACTIVE)
    saved = allowance.state_for(profile).answers
    assert saved == {'types': [AZ], 'platforms': ['PS5'], 'source': 'both', 'badge_started': False,
                     'badge_all': True, 'badge_types': [], 'badge_picks': [2, 4], 'badge_weight': 'bonus'}


# --- the doors --------------------------------------------------------------------------------------------

_SEQ = {'n': 0}


def _contract(name):
    _SEQ['n'] += 1
    contract = Contract.objects.create(name=name, slug='al-%d' % _SEQ['n'], is_live=True, igdb_id=860_000 + _SEQ['n'])
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    GameFactory(concept=concept)
    return contract


def _plan(client, **payload):
    payload.setdefault('types', [AZ])
    payload.setdefault('badge_started', False)
    return client.post(reverse('challenge_optimizer_plan'), data=json.dumps(payload), content_type='application/json')


def _seed_run(profile):
    # A free hunter cannot START a run during the beta, so the run is made directly: the allowance is about the
    # optimizer, not about who may create a challenge.
    _contract('Astro Bot')
    from django.test import override_settings
    with override_settings(CHALLENGES_BETA_MEMBERS_ONLY=False):
        return svc.start(profile, AZ)


def test_an_unconfirmed_first_plan_is_asked_about_not_built(client):
    profile = _free(client)
    _seed_run(profile)

    resp = _plan(client)

    assert resp.status_code == 409 and json.loads(resp.content)['needs_confirmation'] is True
    assert not OptimizerAllowance.objects.filter(profile=profile).exists()


def test_a_confirmed_first_plan_spends_the_week_and_builds(client):
    profile = _free(client)
    _seed_run(profile)

    resp = _plan(client, use_free_plan=True)

    assert resp.status_code == 200 and json.loads(resp.content)['lines']
    assert OptimizerAllowance.objects.get(profile=profile).answers['types'] == [AZ]
    # Tweaks and coming back later with the same answers cost nothing more.
    assert _plan(client).status_code == 200
    assert _plan(client, tweaks={'penalized': ['al-1']}).status_code == 200


def test_other_answers_in_the_same_week_are_refused_with_the_next_date(client):
    profile = _free(client)
    _seed_run(profile)
    _plan(client, use_free_plan=True)

    resp = _plan(client, platforms=['PS4'], use_free_plan=True)

    body = json.loads(resp.content)
    assert resp.status_code == 403 and body['plan_locked'] is True and body['next_free_label']


def test_other_options_needs_the_weeks_plan(client):
    profile = _free(client)
    _seed_run(profile)
    body = {'types': [AZ], 'badge_started': False, 'type': AZ, 'key': 'A'}
    assert client.post(reverse('challenge_optimizer_alternatives'), data=json.dumps(body),
                       content_type='application/json').status_code == 403


def test_members_plan_without_ever_being_asked(client):
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=True, is_linked=True)
    client.force_login(user)
    _seed_run(profile)
    assert _plan(client).status_code == 200
    assert _plan(client, platforms=['PS4']).status_code == 200


def test_the_page_tells_a_free_hunter_before_they_spend(client):
    _free(client)
    html = client.get(reverse('challenge_optimizer')).content.decode()
    assert 'One free optimized plan this week' in html and 'id="cplan-spend"' in html
    assert 'data-allowance="available"' in html


def test_the_page_reopens_a_spent_plan_with_its_answers(client):
    profile = _free(client)
    allowance.spend(profile, _options(types=(AZ,), platforms=('PS5',)), ACTIVE)
    resp = client.get(reverse('challenge_optimizer'))
    html = resp.content.decode()
    assert 'data-allowance="in_use"' in html and 'Your optimized plan this week' in html
    assert resp.context['allowance_saved']['platforms'] == ['PS5'] and 'id="cplan-saved"' in html


def test_members_see_no_count_and_no_confirmation(client):
    user = UserFactory()
    ProfileFactory(user=user, user_is_premium=True, is_linked=True)
    client.force_login(user)
    html = client.get(reverse('challenge_optimizer')).content.decode()
    assert 'data-allowance="unlimited"' in html
    assert 'id="cplan-spend"' not in html and 'One free optimized plan this week' not in html


# --- audit follow-ups ---------------------------------------------------------------------------------------

def test_the_window_closes_exactly_seven_days_on():
    profile = ProfileFactory(user_is_premium=False)
    used = timezone.now() - timedelta(days=3)
    allowance.spend(profile, _options(), ACTIVE, now=used)
    edge = used + allowance.FREE_PLAN_WINDOW
    assert allowance.decide(profile, _options(platforms=('PS4',)), ACTIVE, now=edge - timedelta(seconds=1)) == allowance.LOCKED
    assert allowance.decide(profile, _options(platforms=('PS4',)), ACTIVE, now=edge) == allowance.SPEND


def test_a_run_that_finished_mid_week_does_not_lock_the_hunter_out():
    profile = ProfileFactory(user_is_premium=False)
    allowance.spend(profile, _options(types=(AZ, JOBS)), ACTIVE)
    # A-Z finished: only Job Coverage is running now, and the page can only send what it can tick.
    assert allowance.decide(profile, _options(types=(JOBS,)), {JOBS}) == allowance.FREE


def test_all_badges_covers_started_for_the_lock_check_too():
    profile = ProfileFactory(user_is_premium=False)
    allowance.spend(profile, _options(badge_all=True, badge_started=True), ACTIVE)
    assert allowance.decide(profile, _options(badge_all=True, badge_started=False), ACTIVE) == allowance.FREE


def test_a_lapsed_member_starts_with_a_fresh_allowance():
    profile = ProfileFactory(user_is_premium=True)
    assert allowance.decide(profile, _options(), ACTIVE) == allowance.FREE
    profile.user_is_premium = False
    profile.save(update_fields=['user_is_premium'])
    assert allowance.decide(profile, _options(), ACTIVE) == allowance.SPEND


def test_the_next_date_carries_the_time_in_the_hunters_clock():
    when = timezone.now()
    twelve = allowance.when_label(when, type('U', (), {'use_24hr_clock': False})())
    twenty_four = allowance.when_label(when, type('U', (), {'use_24hr_clock': True})())
    assert ' at ' in twelve and ('AM' in twelve or 'PM' in twelve)
    assert ' at ' in twenty_four and 'AM' not in twenty_four and 'PM' not in twenty_four


def test_a_plan_that_fails_to_build_spends_nothing(client, monkeypatch):
    profile = _free(client)
    _seed_run(profile)

    from challenges.services import build_line

    def busy(*args, **kwargs):
        raise build_line.LineWait('t', 2, 6)
    monkeypatch.setattr(planner, 'get_pool', busy)

    resp = _plan(client, use_free_plan=True)

    assert resp.status_code == 202 and json.loads(resp.content)['queued'] is True
    assert not OptimizerAllowance.objects.filter(profile=profile).exists()


def test_the_spend_reply_carries_the_servers_next_date(client):
    profile = _free(client)
    _seed_run(profile)
    body = json.loads(_plan(client, use_free_plan=True).content)
    assert body['allowance']['spent'] is True and ' at ' in body['allowance']['next_free_label']
    # The week's own plan again: no second spend to report.
    assert 'allowance' not in json.loads(_plan(client).content)


def test_other_options_work_on_the_weeks_own_plan_and_not_on_other_answers(client):
    profile = _free(client)
    _seed_run(profile)
    _plan(client, use_free_plan=True)
    url = reverse('challenge_optimizer_alternatives')
    own = {'types': [AZ], 'badge_started': False, 'type': AZ, 'key': 'A'}
    other = dict(own, platforms=['PS4'])
    assert client.post(url, data=json.dumps(own), content_type='application/json').status_code == 200
    assert client.post(url, data=json.dumps(other), content_type='application/json').status_code == 403


def test_a_locked_reply_carries_the_saved_answers(client):
    profile = _free(client)
    _seed_run(profile)
    _plan(client, use_free_plan=True)
    body = json.loads(_plan(client, platforms=['PS4']).content)
    assert body['saved_answers']['types'] == [AZ]


def test_the_page_names_a_saved_plans_picked_badges():
    from challenges.optimizer_views import allowance_context
    from tests.factories import GroupBadgeFactory
    profile = ProfileFactory(user_is_premium=False)
    gb = GroupBadgeFactory()
    allowance.spend(profile, _options(badge_picks=(gb.id,)), ACTIVE)
    names = allowance_context(profile)['allowance_saved']['badge_pick_names']
    assert names == {str(gb.id): f'{gb.series.name} ({gb.platform_group.name})'}


def test_switching_to_badge_games_only_is_the_same_plan():
    # The weight is how the plan ranks, not what it is: a free hunter can try Only on this week's plan.
    profile = _free()
    allowance.spend(profile, _options(platforms=('PS5',)), ACTIVE)
    assert allowance.decide(profile, _options(platforms=('PS5',), badge_weight=planner.WEIGHT_ONLY), ACTIVE) == allowance.FREE
