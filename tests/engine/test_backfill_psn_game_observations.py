"""The forced-walk backfill: observations are captured during the trophy_titles walk, and a normal
refresh fast-paths whenever the fingerprint matches -- walking nothing. Without force_walk, an
account whose trophies have not moved can never contribute its library, which for a dedicated scout
is precisely the steady state."""
import io
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.factories import ProfileFactory
from trophies.psn_manager import PSNManager

pytestmark = pytest.mark.django_db


def _synced(username, games=0):
    # total_games is the denormalized, indexed column --top sorts on (the GROUP BY over
    # ProfileGame it replaced was a full-table aggregate racing the 60s statement timeout).
    return ProfileFactory(psn_username=username, sync_status='synced', total_games=games)


def _run(*args, answer='y'):
    out = io.StringIO()
    with patch.object(PSNManager, 'profile_refresh') as refresh, \
         patch('builtins.input', return_value=answer):
        call_command('backfill_psn_game_observations', *args, stdout=out)
    return refresh, out.getvalue()


def test_refuses_to_run_with_capture_disabled(settings):
    """The concept-sweep lesson, applied before the first run this time: forced walks with capture
    off spend PSN calls and record nothing, silently."""
    settings.PSN_METADATA_CAPTURE_ENABLED = False
    _synced('scout')

    with pytest.raises(CommandError):
        call_command('backfill_psn_game_observations', '--usernames', 'scout', '--yes')


def test_an_empty_selection_is_refused_not_defaulted():
    """Defaulting to every profile on the site would be a sitewide forced re-walk from a typo."""
    with pytest.raises(CommandError):
        call_command('backfill_psn_game_observations', '--yes')


def test_an_unknown_username_fails_before_anything_queues():
    _synced('real')

    with pytest.raises(CommandError):
        call_command('backfill_psn_game_observations', '--usernames', 'real,ghost', '--yes')


def test_named_profiles_are_queued_with_force_walk():
    profile = _synced('scout')

    refresh, _ = _run('--usernames', 'scout', '--yes')

    refresh.assert_called_once_with(profile, force_walk=True)


def test_top_selects_by_library_size():
    _synced('small', games=1)
    big = _synced('big', games=3)

    refresh, _ = _run('--top', '1', '--yes')

    refresh.assert_called_once_with(big, force_walk=True)


def test_a_mid_sync_profile_is_skipped_and_named():
    """profile_refresh silently no-ops for a syncing profile; the operator must see that rather
    than believe the account was queued."""
    ProfileFactory(psn_username='busy', sync_status='syncing')

    refresh, printed = _run('--usernames', 'busy', '--yes')

    refresh.assert_not_called()
    assert 'busy' in printed and 'skipped (not in synced state)' in printed


def test_an_error_profile_is_skipped_not_silently_unforced():
    """An 'error' profile routes through initial_sync, which queues args=[] -- force_walk silently
    dropped while the first version of this command counted it as queued. Refusing it and naming
    it in the skip list is the honest behaviour."""
    ProfileFactory(psn_username='broken', sync_status='error')

    refresh, printed = _run('--usernames', 'broken', '--yes')

    refresh.assert_not_called()
    assert 'broken (error)' in printed


def test_a_profile_that_starts_syncing_at_the_prompt_is_skipped():
    """The status is re-fetched inside the queue loop: a profile that began syncing while the
    operator read the confirmation prompt must not be queued into a silent no-op."""
    from trophies.models import Profile

    profile = _synced('racer')

    def flip_and_confirm(_prompt):
        Profile.objects.filter(pk=profile.pk).update(sync_status='syncing')
        return 'y'

    out = io.StringIO()
    with patch.object(PSNManager, 'profile_refresh') as refresh,          patch('builtins.input', side_effect=flip_and_confirm):
        call_command('backfill_psn_game_observations', '--usernames', 'racer', stdout=out)

    refresh.assert_not_called()
    assert 'racer (syncing)' in out.getvalue()


def test_top_only_considers_synced_profiles():
    """The biggest library on the site being mid-sync must not hijack the selection."""
    ProfileFactory(psn_username='hugebusy', sync_status='syncing', total_games=999)
    small = _synced('smallsynced', games=5)

    refresh, _ = _run('--top', '1', '--yes')

    refresh.assert_called_once_with(small, force_walk=True)


def test_dry_run_queues_nothing():
    _synced('scout')

    refresh, printed = _run('--usernames', 'scout', '--dry-run')

    refresh.assert_not_called()
    assert 'DRY RUN' in printed


def test_declining_the_prompt_queues_nothing():
    _synced('scout')

    refresh, printed = _run('--usernames', 'scout', answer='n')

    refresh.assert_not_called()
    assert 'cancelled' in printed
