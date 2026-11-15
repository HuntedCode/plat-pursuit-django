"""Retry backoff for profiles whose syncs keep failing, and the refresh_profiles passes that use it.

Regression context (Nov 2026): three profiles whose PSN accounts answer NotFound failed on their first
PSN call, so last_synced never moved. refresh_profiles saw them as overdue on every 15-minute run and
re-queued them, failing, for 99-139 days. Separately, an 'error' profile that is public (its failure
was not privacy) waited out a tier cadence of up to a week before anything retried it.
"""
import ast
import pathlib
from datetime import timedelta
from unittest.mock import patch

import fakeredis
import pytest
from django.core.management import call_command
from django.utils import timezone

from tests.factories import ProfileFactory
from trophies.services import sync_backoff

pytestmark = pytest.mark.django_db

HOUR = 60 * 60
_accounts = iter(range(10**15, 10**16))


@pytest.fixture
def redis(monkeypatch):
    client = fakeredis.FakeStrictRedis()
    monkeypatch.setattr('trophies.util_modules.cache.redis_client', client)
    return client


@pytest.fixture
def refreshed():
    """Profiles refresh_profiles queued, by id, without touching the real job queues."""
    with patch('core.management.commands.refresh_profiles.PSNManager.profile_refresh') as refresh:
        yield lambda: [c.args[0].id for c in refresh.call_args_list]


def _profile(*, status='synced', public=True, synced_hours_ago=0, **kwargs):
    kwargs.setdefault('sync_tier', 'basic')
    return ProfileFactory(
        sync_status=status, psn_history_public=public, account_id=str(next(_accounts)),
        last_synced=timezone.now() - timedelta(hours=synced_hours_ago), **kwargs,
    )


def _back_off(profile):
    sync_backoff.record_failure(profile.id)


# --- the service ----------------------------------------------------------------


def test_the_delay_doubles_from_an_hour_and_caps_at_a_week():
    assert [sync_backoff.retry_delay(n) for n in (1, 2, 3, 4)] == [HOUR, 2 * HOUR, 4 * HOUR, 8 * HOUR]
    assert sync_backoff.retry_delay(50) == 7 * 24 * HOUR


def test_a_failure_starts_a_backoff_window(redis):
    sync_backoff.record_failure(7)

    assert sync_backoff.backing_off([7, 8]) == {7}
    assert 0 < redis.ttl(sync_backoff.BACKOFF_KEY.format(7)) <= HOUR


def test_one_failed_sync_counts_once_however_many_jobs_report_it(redis):
    for _ in range(3):  # sync_profile_data, the privacy handler and sync_complete can all set 'error'
        sync_backoff.record_failure(7)

    assert int(redis.get(sync_backoff.STREAK_KEY.format(7))) == 1


def test_the_next_failure_after_a_window_waits_twice_as_long(redis):
    sync_backoff.record_failure(7)
    redis.delete(sync_backoff.BACKOFF_KEY.format(7))  # the window expired; the retry failed again

    sync_backoff.record_failure(7)

    assert int(redis.get(sync_backoff.STREAK_KEY.format(7))) == 2
    assert HOUR < redis.ttl(sync_backoff.BACKOFF_KEY.format(7)) <= 2 * HOUR


def test_clear_resets_the_streak(redis):
    sync_backoff.record_failure(7)

    sync_backoff.clear(7)

    assert sync_backoff.backing_off([7]) == set()
    assert redis.get(sync_backoff.STREAK_KEY.format(7)) is None


def test_redis_trouble_degrades_to_no_backoff_and_never_raises(monkeypatch):
    class Down:
        def __getattr__(self, name):
            raise ConnectionError('redis down')

    monkeypatch.setattr('trophies.util_modules.cache.redis_client', Down())

    sync_backoff.record_failure(7)
    sync_backoff.clear(7)
    assert sync_backoff.backing_off([7]) == set()


def test_setting_error_status_records_the_failure(redis):
    profile = _profile()

    profile.set_sync_status('error')

    assert sync_backoff.backing_off([profile.id]) == {profile.id}


def test_a_status_reset_alone_does_not_clear_the_streak(redis):
    # Only a sync that actually succeeds clears it (sync_complete); verification and admin resets
    # flip the status without proving the account syncs.
    profile = _profile()
    profile.set_sync_status('error')

    profile.set_sync_status('synced')

    assert redis.get(sync_backoff.STREAK_KEY.format(profile.id)) is not None


def test_sync_complete_clears_the_backoff_on_success():
    tree = ast.parse(pathlib.Path('trophies/token_keeper.py').read_text(encoding='utf-8'))
    sync_complete = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_job_sync_complete'
    )

    def call_name(stmt):
        call = getattr(stmt, 'value', None)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
            owner = call.func.value
            return f"{getattr(owner, 'id', '?')}.{call.func.attr}", [getattr(a, 'value', None) for a in call.args]
        return None, []

    # The clear must sit in the SAME block, right after the success status, not in an except branch.
    for node in ast.walk(sync_complete):
        body = getattr(node, 'body', None)
        if not isinstance(body, list):
            continue
        for first, second in zip(body, body[1:]):
            if call_name(first) == ('profile.set_sync_status', ['synced']) and call_name(second)[0] == 'sync_backoff.clear':
                return
    raise AssertionError('sync_backoff.clear no longer follows the success status in sync_complete')


# --- refresh_profiles -------------------------------------------------------------


def test_an_overdue_error_profile_inside_its_backoff_is_skipped(redis, refreshed):
    # The NotFound case: last_synced never moves, so the tier pass sees it overdue every run.
    stuck = _profile(status='error', public=False, synced_hours_ago=2000)
    _back_off(stuck)

    call_command('refresh_profiles')

    assert stuck.id not in refreshed()


def test_an_overdue_error_profile_past_its_backoff_is_retried(redis, refreshed):
    stuck = _profile(status='error', public=False, synced_hours_ago=2000)

    call_command('refresh_profiles')

    assert stuck.id in refreshed()


def test_a_public_error_profile_is_retried_ahead_of_its_tier_cadence(redis, refreshed):
    # Unregistered cadence is a week; this one failed an hour ago for a non-privacy reason.
    profile = _profile(status='error', public=True, synced_hours_ago=1, is_linked=False)

    call_command('refresh_profiles')

    assert refreshed() == [profile.id]


def test_a_public_error_profile_inside_its_backoff_waits(redis, refreshed):
    profile = _profile(status='error', public=True, synced_hours_ago=1, is_linked=False)
    _back_off(profile)

    call_command('refresh_profiles')

    assert refreshed() == []


def test_a_private_error_profile_keeps_its_tier_cadence(redis, refreshed):
    _profile(status='error', public=False, synced_hours_ago=1, is_linked=False)

    call_command('refresh_profiles')

    assert refreshed() == []


def test_error_recovery_is_capped_per_run(redis, refreshed):
    from core.management.commands.refresh_profiles import Command

    for _ in range(Command.ERROR_RECOVERY_PER_RUN + 3):
        _profile(status='error', public=True, synced_hours_ago=1, is_linked=False)

    call_command('refresh_profiles')

    assert len(refreshed()) == Command.ERROR_RECOVERY_PER_RUN


def test_a_profile_due_in_two_passes_is_queued_once(redis, refreshed):
    # Overdue for its tier AND eligible for error recovery.
    profile = _profile(status='error', public=True, synced_hours_ago=2000)

    call_command('refresh_profiles')

    assert refreshed() == [profile.id]


def test_healthy_profiles_are_unaffected(redis, refreshed):
    due = _profile(synced_hours_ago=13)
    _profile(synced_hours_ago=1)

    call_command('refresh_profiles')

    assert refreshed() == [due.id]


def test_nothing_is_queued_during_a_psn_outage(redis, refreshed):
    _profile(status='error', public=True, synced_hours_ago=1, is_linked=False)
    _profile(synced_hours_ago=13)
    redis.set('site:psn_outage', '1')

    call_command('refresh_profiles')

    assert refreshed() == []


def test_a_scout_inside_its_backoff_is_skipped_and_does_not_use_the_scout_cap(redis, refreshed):
    from trophies.models import ScoutAccount

    stuck = _profile(status='error', synced_hours_ago=2000)
    healthy = _profile(synced_hours_ago=2000)
    for profile in (stuck, healthy):
        ScoutAccount.objects.create(profile=profile, status='active', refresh_frequency_hours=2)
    _back_off(stuck)

    call_command('refresh_profiles', max_scouts=1)

    assert refreshed() == [healthy.id]
