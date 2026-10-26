"""Inserting a stage at a number that is taken (owner, 2026-10-26).

Authoring a stage between two others meant renumbering every later stage by hand first, top down, because
`unique_together(series_slug, stage_number)` refuses two stage 3s. The admin's "Insert here" box does it: the
stage at that number, and the run straight after it, move up one (`badge_stage_service.open_stage_slot`).
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

def test_inserting_moves_the_stage_there_and_every_one_after_it_up_one():
    _series('ins', 1, 2, 3, 4)
    assert open_stage_slot('ins', 2) == 3
    assert _numbers('ins') == {'s1': 1, 's2': 3, 's3': 4, 's4': 5}


def test_a_free_number_moves_nothing():
    _series('free', 1, 2, 5)
    assert open_stage_slot('free', 3) == 0
    assert _numbers('free') == {'s1': 1, 's2': 2, 's5': 5}


def test_the_shift_stops_at_the_first_gap():
    _series('gap', 1, 2, 3, 7)
    assert open_stage_slot('gap', 2) == 2
    assert _numbers('gap') == {'s1': 1, 's2': 3, 's3': 4, 's7': 7}, 'a later gap is left alone, none is made'


def test_other_series_never_move():
    _series('mine', 1, 2)
    _series('theirs', 1, 2)
    open_stage_slot('mine', 1)
    assert _numbers('theirs') == {'s1': 1, 's2': 2}


def test_moving_a_stage_down_the_list_leaves_no_gap():
    stages = _series('move', 1, 2, 3, 4, 5)
    moving = stages[5]
    assert open_stage_slot('move', 2, moving_pk=moving.pk) == 3
    moving.stage_number = 2
    moving.save()
    assert _numbers('move') == {'s1': 1, 's5': 2, 's2': 3, 's3': 4, 's4': 5}


def test_moving_a_stage_up_the_list_frees_its_old_number():
    stages = _series('up', 1, 2, 3)
    moving = stages[1]
    assert open_stage_slot('up', 3, moving_pk=moving.pk) == 1
    moving.stage_number = 3
    moving.save()
    assert _numbers('up') == {'s2': 2, 's1': 3, 's3': 4}


# -- the admin -------------------------------------------------------------------------------------------------

def _add(client, slug, number, insert):
    data = {'series_slug': slug, 'stage_number': str(number), 'title': 'new',
            'concept_bundles-TOTAL_FORMS': '0', 'concept_bundles-INITIAL_FORMS': '0'}
    if insert:
        data['insert_here'] = 'on'
    return client.post(reverse('admin:trophies_stage_add'), data, follow=True)


def test_the_add_form_inserts_at_a_taken_number_when_asked(client):
    _owner(client)
    _series('adm', 1, 2, 3)
    resp = _add(client, 'adm', 2, insert=True)
    assert resp.status_code == 200
    assert _numbers('adm') == {'s1': 1, 'new': 2, 's2': 3, 's3': 4}
    assert 'Moved 2 stages in adm up one to make room for stage 2.' in resp.content.decode()


def test_without_the_box_a_taken_number_is_still_refused(client):
    _owner(client)
    _series('adm2', 1, 2)
    resp = _add(client, 'adm2', 2, insert=False)
    assert 'already exists' in resp.content.decode()
    assert _numbers('adm2') == {'s1': 1, 's2': 2}, 'nothing moved, nothing added'


def test_the_change_form_moves_a_stage_into_place(client):
    _owner(client)
    stages = _series('chg', 1, 2, 3)
    resp = _change(client, stages[3], 'chg', 1)
    assert resp.status_code == 200 and 'Moved 2 stages in chg up one to make room for stage 1.' in resp.content.decode()
    assert _numbers('chg') == {'s3': 1, 's1': 2, 's2': 3}


def _change(client, stage, slug, number):
    return client.post(reverse('admin:trophies_stage_change', args=[stage.pk]), {
        'series_slug': slug, 'stage_number': str(number), 'title': stage.title, 'insert_here': 'on',
        'concept_bundles-TOTAL_FORMS': '0', 'concept_bundles-INITIAL_FORMS': '0'}, follow=True)


def test_the_change_form_moves_a_stage_into_another_series(client):
    """The case the parking exists for: the stage's number is taken in BOTH series."""
    _owner(client)
    _series('from', 2)
    mover = StageFactory(series_slug='from', stage_number=1, title='mover')
    _series('into', 1, 2)
    _change(client, mover, 'into', 1)
    assert _numbers('into') == {'mover': 1, 's1': 2, 's2': 3}
    assert _numbers('from') == {'s2': 2}


def test_ticking_the_box_on_an_unchanged_number_moves_nothing(client):
    _owner(client)
    stages = _series('same', 1, 2)
    resp = _change(client, stages[1], 'same', 1)
    assert 'Moved' not in resp.content.decode()
    assert _numbers('same') == {'s1': 1, 's2': 2}


def test_ticking_the_box_at_a_free_number_just_saves(client):
    _owner(client)
    _series('open', 1)
    resp = _add(client, 'open', 2, insert=True)
    assert 'Moved' not in resp.content.decode()
    assert _numbers('open') == {'s1': 1, 'new': 2}


def test_the_box_never_lets_a_stage_zero_through(client):
    """It skips the duplicate-number check only: stage numbers still start at 1."""
    _owner(client)
    _series('zero', 1)
    _add(client, 'zero', 0, insert=True)
    assert _numbers('zero') == {'s1': 1}


def test_the_stage_form_offers_the_box(client):
    _owner(client)
    body = client.get(reverse('admin:trophies_stage_add')).content.decode()
    assert 'name="insert_here"' in body and 'Insert here' in body
