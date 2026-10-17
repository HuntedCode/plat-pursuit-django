"""The Challenge Optimizer's team preview doors (`?preview=optimizer-free`, `optimizer-free-used`, `optimizer-line`).

Team only, and nothing real is written or spent: no `OptimizerAllowance` row, no autosaved draft, no ticket in the
real build line. The pretend week lives in the team member's session and starts over on each page load.
"""
import json

import pytest
from django.urls import reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, OptimizerAllowance, OptimizerDraft
from challenges.optimizer_views import PREVIEW_LINE_START
from challenges.services import build_line, optimizer_drafts, planner
from challenges.services import challenge_service as svc
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract

pytestmark = pytest.mark.django_db

AZ = CHALLENGE_TYPE_AZ
_SEQ = {'n': 0}


def _contract(name):
    _SEQ['n'] += 1
    contract = Contract.objects.create(name=name, slug='pr-%d' % _SEQ['n'], is_live=True, igdb_id=860_000 + _SEQ['n'])
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    GameFactory(concept=concept, title_platform=['PS5'])
    return contract


def _team(client, *, staff=True):
    """A member account (as the team's are), staff unless told otherwise, with an A-Z run and one game."""
    user = UserFactory(is_staff=staff)
    profile = ProfileFactory(user=user, user_is_premium=True, is_linked=True)
    client.force_login(user)
    svc.start(profile, AZ)
    _contract('Astro Bot')
    return profile


def _page(client, door):
    return client.get(reverse('challenge_optimizer') + f'?preview={door}')


def _plan(client, door, **payload):
    payload.setdefault('types', [AZ])
    payload.setdefault('platforms', ['PS5'])
    payload.setdefault('badge_started', False)
    return client.post(reverse('challenge_optimizer_plan') + f'?preview={door}', data=json.dumps(payload),
                       content_type='application/json')


def _body(resp):
    return json.loads(resp.content)


# --- the gate ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize('door', ['optimizer-free', 'optimizer-free-used', 'optimizer-line'])
def test_a_hunter_off_the_team_never_opens_a_door(client, door):
    _team(client, staff=False)
    html = _page(client, door).content.decode()
    assert 'data-allowance="unlimited"' in html and '?preview=' not in html
    resp = _plan(client, door, from_wizard=True)
    assert resp.status_code == 200   # no pretend confirmation, no pretend line


# --- optimizer-free ---------------------------------------------------------------------------------------

def test_the_free_door_shows_an_unused_week_and_carries_itself_to_the_plan_doors(client):
    _team(client)
    html = _page(client, 'optimizer-free').content.decode()
    assert 'data-allowance="available"' in html and 'id="cplan-spend"' in html
    assert 'optimizer/plan/?preview=optimizer-free"' in html
    assert 'optimizer/alternatives/?preview=optimizer-free"' in html


def test_the_free_door_asks_spends_pretend_and_then_locks_other_answers(client):
    profile = _team(client)
    _page(client, 'optimizer-free')

    assert _plan(client, 'optimizer-free').status_code == 409                       # asks first
    spent = _plan(client, 'optimizer-free', use_free_plan=True)
    assert spent.status_code == 200 and _body(spent)['allowance']['spent'] is True
    assert _plan(client, 'optimizer-free').status_code == 200                       # same answers: free
    assert _plan(client, 'optimizer-free', platforms=['PS4']).status_code == 403    # other answers: locked
    alt = client.post(reverse('challenge_optimizer_alternatives') + '?preview=optimizer-free',
                      data=json.dumps({'types': [AZ], 'platforms': ['PS5'], 'badge_started': False,
                                       'type': AZ, 'key': 'A'}), content_type='application/json')
    assert alt.status_code == 200

    # Nothing real: no allowance row, no autosaved plan.
    assert not OptimizerAllowance.objects.filter(profile=profile).exists()
    assert not OptimizerDraft.objects.filter(profile=profile).exists()


def test_reloading_the_free_door_starts_the_week_over(client):
    _team(client)
    _page(client, 'optimizer-free')
    _plan(client, 'optimizer-free', use_free_plan=True)
    assert 'data-allowance="available"' in _page(client, 'optimizer-free').content.decode()
    assert _plan(client, 'optimizer-free').status_code == 409


def test_the_pretend_week_means_nothing_without_the_door(client):
    _team(client)
    _page(client, 'optimizer-free')
    _plan(client, 'optimizer-free', use_free_plan=True)
    # The same session, no door: a member again, and a real autosave.
    resp = client.post(reverse('challenge_optimizer_plan'), content_type='application/json',
                       data=json.dumps({'types': [AZ], 'platforms': ['PS4'], 'badge_started': False}))
    assert resp.status_code == 200 and 'allowance' not in _body(resp)


# --- optimizer-free-used ----------------------------------------------------------------------------------

def test_the_used_door_spends_the_week_on_the_saved_plan(client):
    profile = _team(client)
    optimizer_drafts.save(profile, planner.PlannerOptions(types=(AZ,), platforms=('PS4',), badge_started=False),
                          {'pins': [], 'square_pins': [], 'excluded': ['kept'], 'penalized': []})

    resp = _page(client, 'optimizer-free-used')
    html = resp.content.decode()

    assert 'data-allowance="in_use"' in html and 'Your optimized plan this week' in html
    assert resp.context['saved_plan']['answers']['platforms'] == ['PS4']
    assert resp.context['saved_plan']['tweaks']['excluded'] == ['kept']
    assert _plan(client, 'optimizer-free-used', platforms=['PS4']).status_code == 200
    assert _plan(client, 'optimizer-free-used', platforms=['PS5']).status_code == 403
    assert not OptimizerAllowance.objects.filter(profile=profile).exists()


def test_with_nothing_saved_the_used_door_spends_the_week_on_the_wizards_defaults(client):
    _team(client)
    resp = _page(client, 'optimizer-free-used')
    defaults = [p['code'] for p in resp.context['platforms'] if p['checked']]

    # Pressing through the wizard untouched is "the same plan"; changing an answer is not.
    assert _plan(client, 'optimizer-free-used', platforms=defaults).status_code == 200
    assert _plan(client, 'optimizer-free-used', platforms=['PSVITA']).status_code == 403


# --- optimizer-line ---------------------------------------------------------------------------------------

def test_the_line_door_counts_a_plan_it_down_then_builds_it(client):
    _team(client)
    places, ticket = [], None
    for _ in range(PREVIEW_LINE_START):
        body = _body(_plan(client, 'optimizer-line', from_wizard=True, **({'line_ticket': ticket} if ticket else {})))
        assert body['queued'] is True and body['eta_seconds'] > 0
        places.append(body['position'])
        ticket = body['ticket']

    assert places == list(range(PREVIEW_LINE_START, 0, -1))
    built = _plan(client, 'optimizer-line', from_wizard=True, line_ticket=ticket)
    assert built.status_code == 200 and 'lines' in _body(built)
    # The real line never saw any of it.
    assert build_line.redis_client.zcard(build_line.LINE_KEY) == 0


def test_the_line_door_never_queues_a_tweak(client):
    _team(client)
    assert _plan(client, 'optimizer-line').status_code == 200


@pytest.mark.parametrize('ticket', ['preview-x', 'preview-99', 'preview-0', 'preview-\u00b2', 'preview-', 'preview-01'])
def test_a_crafted_pretend_ticket_never_skips_the_pretend_line_or_500s(client, ticket):
    _team(client)
    resp = _plan(client, 'optimizer-line', from_wizard=True, line_ticket=ticket)
    assert resp.status_code == 202 and _body(resp)['position'] >= 1


def test_a_real_ticket_passes_through_the_pretend_line_to_the_real_one(client):
    # The real line was full when the pretend one ended: its ticket must keep its real place, not start the
    # pretend countdown over (and build again as a newcomer each time round).
    import time
    from tests.factories import BadgeSeriesFactory, GroupBadgeFactory
    _team(client)
    GroupBadgeFactory(series=BadgeSeriesFactory(), is_live=True)
    build_line.redis_client.zadd(build_line.HOLDERS_KEY, {f'busy-{i}': time.time() for i in range(build_line.SLOTS)})

    real = _body(_plan(client, 'optimizer-line', from_wizard=True, badge_all=True, line_ticket='preview-1'))
    assert real['queued'] is True and not real['ticket'].startswith('preview-')
    again = _body(_plan(client, 'optimizer-line', from_wizard=True, badge_all=True, line_ticket=real['ticket']))
    assert again['ticket'] == real['ticket'] and again['position'] == 1
    assert build_line.redis_client.zcard(build_line.LINE_KEY) == 1


def test_the_line_door_spends_a_free_team_members_real_week_for_real(client):
    # Only the free doors pretend a week; the line door is about the line, so the allowance is the real one.
    from django.test import override_settings
    user = UserFactory(is_staff=True)
    profile = ProfileFactory(user=user, user_is_premium=False, is_linked=True)
    client.force_login(user)
    with override_settings(CHALLENGES_BETA_MEMBERS_ONLY=False):
        svc.start(profile, AZ)
    _contract('Astro Bot')
    assert _plan(client, 'optimizer-line', use_free_plan=True).status_code == 200
    assert OptimizerAllowance.objects.filter(profile=profile).exists()


def test_the_line_doors_spend_reply_carries_the_real_weeks_date(client, monkeypatch):
    # The line door spends the REAL week, so its reply's date must be the real week's: a no-op repeat spend
    # (another tab spent these answers two days ago) keeps the earlier date, never "now + 7 days".
    import datetime
    from challenges.services import allowance
    from django.test import override_settings
    user = UserFactory(is_staff=True)
    profile = ProfileFactory(user=user, user_is_premium=False, is_linked=True)
    client.force_login(user)
    with override_settings(CHALLENGES_BETA_MEMBERS_ONLY=False):
        svc.start(profile, AZ)
    _contract('Astro Bot')
    earlier = timezone.now() - datetime.timedelta(days=2)
    real_spend = allowance.spend

    def other_tab_first(prof, options, active_types, now=None):
        real_spend(prof, options, active_types, now=earlier)
        return real_spend(prof, options, active_types, now=now)
    monkeypatch.setattr(allowance, 'spend', other_tab_first)

    body = _body(_plan(client, 'optimizer-line', use_free_plan=True))

    at = datetime.datetime.fromisoformat(body['allowance']['next_free_at'])
    assert abs((at - (earlier + allowance.FREE_PLAN_WINDOW)).total_seconds()) < 1
