"""A-Z challenge export (Lane 2 preservation): dumps Challenge(type=az)+AZChallengeSlot to durable JSON,
keyed on stable PSN ids, before the challenge teardown drops the tables."""
import json

import pytest
from django.core.management import call_command

from trophies.models import AZChallengeSlot, Challenge
from tests.factories import GameFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def test_export_captures_letters_games_and_completion(tmp_path):
    p = ProfileFactory()
    ch = Challenge.objects.create(profile=p, challenge_type='az', name='My A-Z',
                                  total_items=26, completed_count=1, cover_letter='A')
    game = GameFactory(np_communication_id='NPWR90001_00', title_name='Astro Bot')
    AZChallengeSlot.objects.create(challenge=ch, letter='A', game=game, is_completed=True)
    AZChallengeSlot.objects.create(challenge=ch, letter='B', game=None)   # empty slot (SET_NULL)

    out = tmp_path / 'az.json'
    call_command('export_az_challenges', '--output', str(out))

    data = json.loads(out.read_text(encoding='utf-8'))
    assert data['schema'] == 'az_challenge_export.v1'
    assert data['challenge_count'] == 1 and data['slot_count'] == 2
    ch_data = data['challenges'][0]
    assert ch_data['psn_username'] == p.psn_username   # keyed on stable id, not DB pk
    assert ch_data['completed_count'] == 1
    slots = {s['letter']: s for s in ch_data['slots']}
    assert slots['A']['game_np_communication_id'] == 'NPWR90001_00'
    assert slots['A']['game_title'] == 'Astro Bot'
    assert slots['A']['is_completed'] is True
    assert slots['B']['game_np_communication_id'] is None   # empty slot preserved


def test_export_excludes_soft_deleted_and_non_az(tmp_path):
    p = ProfileFactory()
    Challenge.objects.create(profile=p, challenge_type='az', name='Deleted', is_deleted=True)
    Challenge.objects.create(profile=p, challenge_type='calendar', name='Cal')   # not A-Z
    out = tmp_path / 'az.json'

    call_command('export_az_challenges', '--output', str(out))

    data = json.loads(out.read_text(encoding='utf-8'))
    assert data['challenge_count'] == 0

    call_command('export_az_challenges', '--output', str(out), '--include-deleted')
    data = json.loads(out.read_text(encoding='utf-8'))
    assert data['challenge_count'] == 1   # the soft-deleted A-Z now included
