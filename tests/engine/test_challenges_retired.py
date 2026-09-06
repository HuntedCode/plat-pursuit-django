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
from django.urls import get_resolver

pytestmark = pytest.mark.django_db

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Every page route the retired system answered, recovered from the teardown commit itself
#: (`git show a6dc9ccd -- plat_pursuit/urls.py`) and NOT from `docs/features/challenge-systems.md`.
#: That doc's "Page Routes" table lists the `/challenges/...` paths, which were 301 shims; the
#: CANONICAL routes were all under `/community/challenges/`. Taking the doc at its word pinned the
#: redirect surface and left the hub and its ten siblings unguarded -- a doc written before a move
#: describes the world before the move.
RETIRED_PATHS = (
    # canonical
    '/community/challenges/',
    '/community/challenges/az/create/',
    '/community/challenges/az/1/',
    '/community/challenges/az/1/setup/',
    '/community/challenges/az/1/edit/',
    '/community/challenges/calendar/create/',
    '/community/challenges/calendar/1/',
    '/community/challenges/genre/create/',
    '/community/challenges/genre/1/',
    '/community/challenges/genre/1/setup/',
    '/community/challenges/genre/1/edit/',
    '/my-challenges/',
    # the legacy 301 shims, which went with them
    '/challenges/',
    '/challenges/az/1/',
    '/challenges/calendar/1/',
    '/challenges/genre/1/',
)

RETIRED_API_PATHS = (
    '/api/v1/challenges/az/',
    '/api/v1/challenges/az/game-search/',
    '/api/v1/challenges/az/1/',
    '/api/v1/challenges/az/1/slots/A/assign/',
    '/api/v1/challenges/az/1/share/png/',
    '/api/v1/challenges/calendar/',
    '/api/v1/challenges/calendar/1/',
    '/api/v1/challenges/calendar/1/day/1/1/',
    '/api/v1/challenges/genre/',
    '/api/v1/challenges/genre/concept-search/',
    '/api/v1/challenges/genre/1/bonus/add/',
    '/api/v1/challenges/genre/1/move-targets/',
)

#: Every URL NAME the system owned, page and API, from the same commit. The three `*_detail` names
#: take a `challenge_id`, which is exactly why the test below cannot use `reverse()`.
RETIRED_URL_NAMES = (
    'challenges_browse', 'my_challenges',
    'az_challenge_create', 'az_challenge_detail', 'az_challenge_setup', 'az_challenge_edit',
    'calendar_challenge_create', 'calendar_challenge_detail',
    'genre_challenge_create', 'genre_challenge_detail', 'genre_challenge_setup',
    'genre_challenge_edit',
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


@pytest.mark.parametrize('name', RETIRED_URL_NAMES)
def test_no_challenge_url_name_is_registered(name):
    """Separate from the 404 sweep on purpose. A path can 404 while its NAME still reverses, and a
    name that reverses is a `{% url %}` waiting to render -- which is exactly how Game Lists keeps
    its names alive while parked. Challenges keeps none.

    Asserted against the resolver's registry rather than with `pytest.raises(NoReverseMatch)`.
    `reverse('az_challenge_detail')` raises that for TWO different reasons -- the name is gone, or
    the name is back and needs a `challenge_id` -- so the obvious version of this test passes
    unchanged if the route it guards is restored at its original pattern. Three of the names here
    take an argument, so three quarters of the guard was decorative.
    """
    registry = get_resolver().reverse_dict

    assert name not in registry, f'{name} is a registered URL name again'


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
    same seam, and this is what makes that a deliberate edit rather than a quiet one.

    Scoped to IMPORTS AND CALLS, not to the word. A bare `'challenge' not in source.lower()` over a
    2,200-line file that is actively edited fails the build the first time somebody writes "the
    challenge here is" in a docstring, and a guard that cries wolf gets deleted rather than fixed.

    Its honest limit: a rebuilt hook named something else (`progress_hooks.run(...)`) passes this.
    That is acceptable -- what it defends against is the OLD system being wired back in, and the new
    one arriving here is a deliberate act that will edit this file anyway.
    """
    source = (ROOT / 'trophies' / 'token_keeper.py').read_text(encoding='utf-8')

    for wired in ('challenge_service', 'check_az_challenge', 'check_calendar_challenge',
                  'check_genre_challenge', 'import challenge', 'challenge_views'):
        assert wired not in source, f'token_keeper is wired to challenges again: {wired}'


def test_the_az_archive_table_still_exists_and_keeps_its_columns():
    """`ArchivedAZChallenge` is the only surviving copy of that user data and nothing reads it.

    What a test can pin is the SCHEMA: the model, its columns, and -- via the `create()` -- that the
    table is really there, since a migration that dropped it raises here rather than passing green.
    What a test cannot pin is that the production ROWS are still in it. Said plainly because the
    first draft of this file called row survival its headline assertion while only checking columns,
    which is a worse position than not claiming it: the risk reads as covered.
    """
    from trophies.models import ArchivedAZChallenge

    fields = {f.name for f in ArchivedAZChallenge._meta.get_fields()}
    assert {'psn_username', 'profile', 'name', 'completed_count',
            'is_complete', 'was_deleted', 'created_at', 'slots'} <= fields

    ArchivedAZChallenge.objects.create(psn_username='hunted47', slots=[])


def test_the_archive_still_matches_the_shape_the_migration_wrote():
    """The rebuilt import reads rows written in August by `0281`, not rows written by this test.

    So the keys are read back OUT of the migration source rather than typed here. Asserting on a
    dict this test just created proves `json.loads(json.dumps(x)) == x` and nothing about the data
    waiting in production. Both identifiers are deliberately STABLE PSN strings rather than FKs
    (`psn_username`, and `game_np_communication_id` per slot) so concept churn cannot break the
    import -- and that is the property worth pinning, because the cheap fix when the import misses
    would be to reach for a Game FK instead.
    """
    source = (ROOT / 'trophies' / 'migrations' / '0281_drop_challenge_system.py').read_text(
        encoding='utf-8')

    assert 'ArchivedAZChallenge' in source, 'the migration that filled the archive is gone'
    for key in ('"letter"', '"game_np_communication_id"', '"game_title"',
                '"is_completed"', '"completed_at"'):
        assert key in source, f'the archived slot shape changed: {key} is no longer written'
    assert 'psn_username=' in source
