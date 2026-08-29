"""The deliberate walk that fills in PSN data for games the sync path will never revisit.

Capture only happens inside `_job_sync_title_id`, and that job is only queued for title_ids that did
not match a known game. So a game that is already anchored and already matched is never re-queued by
a normal profile sync and would never be captured. This command is the answer to that, and the tests
below pin the two properties that make it safe to point at a live catalogue: it enqueues on
bulk_priority (so it cannot starve real users), and --missing-only genuinely shrinks the work (so an
interrupted sweep resumes instead of re-spending PSN calls on rows it already has).
"""
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.factories import ConceptFactory, GameFactory, ProfileFactory
from trophies.models import ScoutAccount
from trophies.services.psn_metadata_service import capture_psn_concept_data

pytestmark = pytest.mark.django_db


def _details(psn_id='12345'):
    return {'id': psn_id, 'nameEn': 'Ghost of Tsushima'}


@pytest.fixture
def driver():
    """An active scout, which is how the command resolves a driver with no --driver-profile."""
    profile = ProfileFactory(psn_username='sweepscout')
    ScoutAccount.objects.create(profile=profile, status='active')
    return profile


def _game(**over):
    fields = {'title_ids': ['CUSA00001_00'], 'title_platform': ['PS4']}
    fields.update(over)
    return GameFactory(**fields)


def test_jobs_land_on_bulk_priority_against_the_driver(driver):
    """bulk_priority is the whole reason this is safe to run against prod: it drains beneath every
    live user sync. A default-queue sweep would put a catalogue walk ahead of real people."""
    game = _game()

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data')

    assign.assert_called_once_with(
        'sync_title_id',
        ['CUSA00001_00', game.np_communication_id],
        driver.id,
        priority_override='bulk_priority',
    )


def test_missing_only_skips_games_already_captured(driver):
    """The resume property. Without it, re-running after an interruption re-spends a PSN call on
    every game already done -- which for the full catalogue is the entire cost, twice."""
    captured_concept = ConceptFactory()
    capture_psn_concept_data(captured_concept, _details())
    _game(concept=captured_concept)

    uncaptured = _game(concept=ConceptFactory())

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--missing-only')

    assert assign.call_count == 1
    assert assign.call_args.args[1][1] == uncaptured.np_communication_id


def test_missing_only_ignores_concept_less_games(driver):
    """A game with no concept has nothing to have captured, so it matches `psn_data__isnull=True`
    forever and would be swept on every single run without ever being satisfiable."""
    _game(concept=None)

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--missing-only')

    assign.assert_not_called()


def test_a_game_with_no_title_id_is_never_enqueued(driver):
    """`title_ids[0]` is the job's first argument. An empty list would IndexError mid-sweep, after
    an arbitrary number of jobs had already been queued."""
    _game(title_ids=[])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data')

    assign.assert_not_called()


def test_a_pspc_game_resolves_to_the_console_platform_and_is_not_skipped(driver):
    """PSPC titles carry the real platform at [1]; sending 'PSPC' returns a sparse response that
    reads downstream as 'PSN has no data' rather than 'we asked the wrong question'."""
    _game(title_platform=['PSPC', 'PS5'])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data')

    assert assign.call_count == 1


def test_a_pspc_game_with_nothing_behind_it_is_skipped(driver):
    _game(title_platform=['PSPC'])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data')

    assign.assert_not_called()


def test_dry_run_queues_nothing(driver):
    """This walks the entire catalogue against a live PSN budget. Previewing must be free."""
    _game()

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--dry-run')

    assign.assert_not_called()


def test_limit_caps_the_sweep(driver):
    for _ in range(3):
        _game()

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--limit', '2')

    assert assign.call_count == 2


def test_platform_filter_narrows_the_sweep(driver):
    _game(title_platform=['PS4'])
    ps5 = _game(title_platform=['PS5'])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--platform', 'PS5')

    assert assign.call_count == 1
    assert assign.call_args.args[1][1] == ps5.np_communication_id


def test_no_driver_available_is_a_clean_error_not_a_traceback():
    """No scout, no --driver-profile. Failing here costs nothing; failing halfway through a sweep
    leaves a partially-queued catalogue."""
    _game()

    with pytest.raises(CommandError):
        call_command('backfill_psn_concept_data')


def test_an_unknown_driver_profile_is_rejected(driver):
    with pytest.raises(CommandError):
        call_command('backfill_psn_concept_data', '--driver-profile', 'nobody')
