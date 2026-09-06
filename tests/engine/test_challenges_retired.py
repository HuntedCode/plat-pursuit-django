"""Challenges were demolished (2026-08), and until now nothing said so.

Every other teardown on this site carries a pin -- `test_community_hub_retired.py`,
`test_profile_banner_retired.py`, `test_lists_hidden.py`, `test_my_stats_hidden.py`,
`test_dashboard_retirement.py`. The Challenge teardown was the LARGEST of them (~13,700 lines across
40+ files, five tables dropped in `0281_drop_challenge_system`) and it is the only one with none.

The thing actually worth protecting is the last line of this file. `ArchivedAZChallenge` is the sole
surviving copy of every A-Z challenge every hunter ever built -- Calendar and Genre progress was
deliberately not preserved, and the `export_az_challenges` command that produced the durable JSON was
itself deleted in `41377e21`. There is no second copy. A migration squash or a tidy-up that drops
that table takes the data with it, silently, and the rebuild's import path with it.

This file is written BEFORE the challenge rebuild starts, on purpose: it is the thing that has to be
true while the new system is being built beside it, and it is what the rebuild will edit deliberately
rather than break by accident.
"""
import pathlib

import pytest
from django.apps import apps
from django.urls import NoReverseMatch, reverse

pytestmark = pytest.mark.django_db

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Every page route the retired system answered, from the doc's own table
#: (`docs/features/challenge-systems.md`, "Page Routes"). Listed rather than enumerated because
#: there is no URL conf left to enumerate FROM -- that is the point.
RETIRED_PATHS = (
    '/challenges/',
    '/my-challenges/',
    '/challenges/az/create/',
    '/challenges/az/1/',
    '/challenges/az/1/setup/',
    '/challenges/az/1/edit/',
    '/challenges/calendar/create/',
    '/challenges/calendar/1/',
    '/challenges/genre/create/',
    '/challenges/genre/1/',
    '/challenges/genre/1/setup/',
    '/challenges/genre/1/edit/',
)

RETIRED_API_PATHS = (
    '/api/v1/challenges/az/',
    '/api/v1/challenges/calendar/',
    '/api/v1/challenges/genre/',
)

RETIRED_MODELS = {
    'Challenge',
    'AZChallengeSlot',
    'CalendarChallengeDay',
    'GenreChallengeSlot',
    'GenreBonusSlot',
}


def test_no_challenge_page_answers(client):
    """No redirect stub was left, deliberately -- unlike Game Lists, which 302s home because it is
    coming back at the same addresses. These are 404s because the rebuild will not reuse them."""
    # A guard on the guard. Every assertion below is "this 404s", which is also what a client that
    # cannot reach the app at all would report -- a misconfigured fixture or a middleware refusing
    # the request would make this whole sweep pass while testing nothing.
    alive = client.get('/games/', HTTP_CF_RAY='8f0000000000abcd-LHR')
    assert alive.status_code != 404, 'the client 404s on a live page too; this sweep proves nothing'

    for path in RETIRED_PATHS + RETIRED_API_PATHS:
        resp = client.get(path, HTTP_CF_RAY='8f0000000000abcd-LHR')
        assert resp.status_code == 404, f'{path} answered {resp.status_code}'


@pytest.mark.parametrize('name', [
    'challenge_hub', 'my_challenges',
    'az_challenge_detail', 'calendar_challenge_detail', 'genre_challenge_detail',
])
def test_no_challenge_url_name_resolves(name):
    """Separate from the 404 sweep on purpose. A path can 404 while its NAME still reverses, and a
    name that reverses is a `{% url %}` waiting to render -- which is exactly how Game Lists keeps
    its names alive while parked. Challenges keeps none."""
    with pytest.raises(NoReverseMatch):
        reverse(name)


def test_no_challenge_model_is_back_in_the_registry():
    """The tables were dropped. A model class reappearing means a migration will try to create them
    again, and the archive below stops being the only copy of anything."""
    model_names = {m.__name__ for m in apps.get_app_config('trophies').get_models()}

    assert not RETIRED_MODELS & model_names, (
        f'a challenge model is back: {sorted(RETIRED_MODELS & model_names)}; '
        'the tables were dropped in 0281_drop_challenge_system'
    )


def test_the_service_and_its_views_stay_gone():
    """The plumbing, which is the half that comes back one import at a time."""
    for gone in (
        ROOT / 'trophies' / 'services' / 'challenge_service.py',
        ROOT / 'trophies' / 'views' / 'challenge_views.py',
        ROOT / 'api' / 'az_challenge_views.py',
        ROOT / 'api' / 'calendar_challenge_views.py',
        ROOT / 'api' / 'genre_challenge_views.py',
        ROOT / 'static' / 'js' / 'az-challenge.js',
    ):
        assert not gone.exists(), f'{gone.name} is back without a route'


def test_the_sync_pipeline_does_not_call_a_challenge_checker():
    """All three progress checkers ran from `_job_sync_complete`. The rebuilt system will hook the
    same seam, and when it does this assertion is what makes that a deliberate edit."""
    source = (ROOT / 'trophies' / 'token_keeper.py').read_text(encoding='utf-8')

    assert 'challenge' not in source.lower(), 'token_keeper references challenges again'


def test_the_az_archive_is_intact_and_still_the_shape_the_rebuild_needs():
    """THE assertion in this file.

    `ArchivedAZChallenge` is the only surviving copy of that user data, nothing reads it, and the
    command that could re-export it was deleted. Its two keys are what a rebuilt A-Z will match on,
    and both are deliberately STABLE PSN identifiers rather than FKs -- `psn_username` and, inside
    each slot, `game_np_communication_id` -- so the import cannot be broken by concept churn or by a
    Game row being renumbered.
    """
    from trophies.models import ArchivedAZChallenge

    fields = {f.name for f in ArchivedAZChallenge._meta.get_fields()}
    assert {'psn_username', 'slots', 'completed_count', 'is_complete'} <= fields

    row = ArchivedAZChallenge.objects.create(
        psn_username='hunted47',
        name='A to Z',
        completed_count=1,
        slots=[{'letter': 'A', 'game_np_communication_id': 'NPWR12345_00',
                'game_title': 'Astro Bot', 'is_completed': True, 'completed_at': None}],
    )

    row.refresh_from_db()
    assert row.slots[0]['game_np_communication_id'] == 'NPWR12345_00', (
        'the archive no longer round-trips the PSN id the rebuilt import matches on'
    )
