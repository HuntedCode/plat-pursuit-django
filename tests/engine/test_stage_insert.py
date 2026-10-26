"""Making room to insert a stage (owner, 2026-10-26).

Authoring a stage between two others meant renumbering every later stage by hand first, top down, because
`unique_together(series_slug, stage_number)` refuses two stage 3s. The Stages list's "Make room" action does it:
select the stage at the number you want, and it and every later stage in its series move down one
(`badge_stage_service.open_stage_slot`), leaving the number free for the new stage.
"""
import pytest
from django.urls import reverse

from trophies.models import Stage
from trophies.services.badge_stage_service import open_stage_slot
from tests.engine.test_badge_admin_authoring import _owner
from tests.factories import StageFactory

pytestmark = pytest.mark.django_db


def _numbers(slug):
    return dict(Stage.objects.filter(series_slug=slug).values_list('title', 'stage_number'))


def _series(slug, *numbers):
    return {n: StageFactory(series_slug=slug, stage_number=n, title=f's{n}') for n in numbers}


# -- the service ---------------------------------------------------------------------------------------------

def test_making_room_moves_that_stage_and_every_later_one_down_one():
    _series('ins', 1, 2, 3, 4)
    assert open_stage_slot('ins', 2) == 3
    assert _numbers('ins') == {'s1': 1, 's2': 3, 's3': 4, 's4': 5}


def test_everything_onward_moves_gaps_and_all():
    _series('gap', 1, 2, 3, 7)
    assert open_stage_slot('gap', 2) == 3
    assert _numbers('gap') == {'s1': 1, 's2': 3, 's3': 4, 's7': 8}


def test_a_number_past_the_end_moves_nothing():
    _series('end', 1, 2)
    assert open_stage_slot('end', 3) == 0
    assert _numbers('end') == {'s1': 1, 's2': 2}


def test_other_series_never_move():
    _series('mine', 1, 2)
    _series('theirs', 1, 2)
    open_stage_slot('mine', 1)
    assert _numbers('theirs') == {'s1': 1, 's2': 2}


# -- the admin action --------------------------------------------------------------------------------------------

def _make_room(client, stages):
    """Run the action; returns the messages it left."""
    resp = client.post(reverse('admin:trophies_stage_changelist'), {
        'action': 'make_room', '_selected_action': [str(s.pk) for s in stages]}, follow=True)
    return [str(m) for m in resp.context['messages']]


def test_the_action_frees_the_selected_stages_number(client):
    _owner(client)
    stages = _series('adm', 1, 2, 3)
    assert _make_room(client, [stages[2]]) == ['adm: stage 2 is free. Moved 2 stages down one.']
    assert _numbers('adm') == {'s1': 1, 's2': 3, 's3': 4}

    StageFactory(series_slug='adm', stage_number=2, title='new')   # the number is free for the new stage
    assert _numbers('adm') == {'s1': 1, 'new': 2, 's2': 3, 's3': 4}


def test_one_stage_per_series_or_nothing_moves(client):
    _owner(client)
    stages = _series('two', 1, 2, 3)
    assert _make_room(client, [stages[1], stages[3]]) == [
        'Pick one stage per series (more than one in: two). Nothing was moved.']
    assert _numbers('two') == {'s1': 1, 's2': 2, 's3': 3}


def test_one_stage_in_each_of_two_series(client):
    _owner(client)
    a, b = _series('a', 1, 2), _series('b', 1, 2)
    _make_room(client, [a[2], b[1]])
    assert _numbers('a') == {'s1': 1, 's2': 3}
    assert _numbers('b') == {'s1': 2, 's2': 3}


def test_the_action_is_offered(client):
    _owner(client)
    _series('offer', 1)
    body = client.get(reverse('admin:trophies_stage_changelist')).content.decode()
    assert 'value="make_room"' in body and 'Make room: move this stage and every later one down one' in body
