"""The two helpers both catalogue sweeps share, and the command that had no tests at all.

`resync_trophy_groups` predates this file and was run against production with zero coverage. When
`backfill_psn_concept_data` arrived needing the same driver-resolution and platform-resolution logic,
the choice was to copy it or extract it; extracting means one bug fixed once, but it also means a
regression in either helper now breaks TWO commands, one of which nothing was watching.

The PSPC case is the one that earns its own test. Sending 'PSPC' to PSN returns a sparse response,
and sparse reads downstream as "PSN has no data for this title" rather than "we asked the wrong
question" -- a silent wrong answer, not an error.
"""
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.factories import GameFactory, ProfileFactory
from trophies.models import ScoutAccount
from trophies.util_modules.psn_sweep import (
    SweepConfigurationError, resolve_api_platform, resolve_driver_profile,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def scout():
    profile = ProfileFactory(psn_username='sweepscout')
    ScoutAccount.objects.create(profile=profile, status='active')
    return profile


# --- resolve_api_platform ------------------------------------------------------------------------

@pytest.mark.parametrize('title_platform, expected', [
    (['PS5'], 'PS5'),
    (['PS4', 'PS5'], 'PS4'),
    (['PSPC', 'PS5'], 'PS5'),
    (['PSPC'], None),
    ([], None),
    (None, None),
])
def test_platform_resolution(title_platform, expected):
    assert resolve_api_platform(title_platform) == expected


def test_a_pspc_only_game_resolves_to_none_rather_than_raising():
    """Returning None lets the caller skip. An IndexError would abort a catalogue sweep partway
    through, with an arbitrary number of jobs already queued and no record of where it stopped."""
    assert resolve_api_platform(['PSPC']) is None


# --- resolve_driver_profile ----------------------------------------------------------------------

def test_the_first_active_scout_is_the_default_driver(scout):
    assert resolve_driver_profile() == scout


def test_a_paused_scout_is_not_selected():
    """status is the only lifecycle column on ScoutAccount, so this filter is the whole guard."""
    profile = ProfileFactory(psn_username='pausedscout')
    ScoutAccount.objects.create(profile=profile, status='paused')

    with pytest.raises(SweepConfigurationError):
        resolve_driver_profile()


def test_an_explicit_username_is_matched_case_insensitively(scout):
    """Profile.save() lowercases psn_username unconditionally, so the stored value is always
    lowercase and an operator typing the display casing must still resolve."""
    assert resolve_driver_profile('SweepScout') == scout


def test_an_unknown_username_names_the_username_it_could_not_find(scout):
    with pytest.raises(SweepConfigurationError) as exc:
        resolve_driver_profile('nobody')

    assert 'nobody' in str(exc.value)


def test_no_scout_and_no_username_is_a_clean_error():
    with pytest.raises(SweepConfigurationError):
        resolve_driver_profile()


# --- resync_trophy_groups: the command the refactor touched --------------------------------------

def test_resync_enqueues_on_bulk_priority_through_the_shared_helpers(scout):
    """This command had no tests when its two helpers were moved out from under it. The refactor was
    verified only by a green suite that never executed it."""
    game = GameFactory(title_platform=['PS4'])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('resync_trophy_groups')

    assign.assert_called_once_with(
        'sync_trophy_groups',
        [game.np_communication_id, 'PS4'],
        scout.id,
        priority_override='bulk_priority',
    )


def test_resync_skips_a_game_with_no_resolvable_platform(scout):
    GameFactory(title_platform=['PSPC'])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('resync_trophy_groups')

    assign.assert_not_called()


def test_resync_dry_run_queues_nothing(scout):
    GameFactory(title_platform=['PS4'])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('resync_trophy_groups', '--dry-run')

    assign.assert_not_called()


def test_resync_raises_command_error_when_no_driver_resolves():
    """The helper raises SweepConfigurationError; the command must translate it. A leaked
    SweepConfigurationError would surface as a traceback instead of a clean message."""
    GameFactory(title_platform=['PS4'])

    with pytest.raises(CommandError):
        call_command('resync_trophy_groups')
