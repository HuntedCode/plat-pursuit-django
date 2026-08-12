"""A stale sync holds back ONE month, not the archive.

It used to render the whole landing page as a gate: a hunter who had not synced this calendar month lost
access to every recap they had ever earned, including years of finished months that a sync could not
possibly change. The only month genuinely at risk is the most recent completed one -- it may still be
missing trophies that never came across -- so that is the only one held, and the page says so where the
month is rather than instead of the page.
"""
from datetime import datetime, timedelta

import pytest
import pytz
from django.utils import timezone

from tests.engine.test_recap_archive_page import _prev_month, _trophy_at, _utc
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

URL = '/recap/'


def _hunter(*, fresh):
    """`check_sync_freshness` is "synced within the current calendar month", so a stale hunter is one
    whose last sync predates the 1st -- not merely one who synced a while ago."""
    profile = ProfileFactory(is_linked=True, sync_status='synced')
    profile.user.user_timezone = 'UTC'
    profile.user.save(update_fields=['user_timezone'])
    now = timezone.now()
    profile.last_synced = now if fresh else now.replace(day=1) - timedelta(days=2)
    profile.save(update_fields=['last_synced'])
    return profile


def _page(client, profile):
    client.force_login(profile.user)
    resp = client.get(URL)
    assert resp.status_code == 200, f'expected the archive, got {resp.status_code}'
    return resp.content.decode()


def _with_two_months(profile):
    """Trophies in the most recent completed month and in one well before it."""
    recent_y, recent_m = _prev_month()
    _trophy_at(profile, _utc(recent_y, recent_m, 12))
    older = pytz.UTC.localize(datetime(recent_y - 1, 6, 12))
    _trophy_at(profile, older)
    return (recent_y, recent_m), (recent_y - 1, 6)


def test_a_stale_sync_no_longer_locks_the_whole_archive(client):
    """The bug. Every earned month disappeared behind one gate card, including months finished years ago
    whose data a sync cannot change."""
    profile = _hunter(fresh=False)
    _, (old_y, old_m) = _with_two_months(profile)

    html = _page(client, profile)

    assert f'/recap/{old_y}/{old_m}/' in html, 'an older, finished month is not reachable'
    assert 'Fresh sync needed' not in html, 'the whole-page gate is back'


def test_the_month_at_risk_is_held_and_says_why(client):
    profile = _hunter(fresh=False)
    (recent_y, recent_m), _ = _with_two_months(profile)

    html = _page(client, profile)

    assert 'Waiting on a sync' in html, 'the hero does not explain the hold'
    assert 'data-recap-refresh' in html, 'there is no way to act on it'
    assert f'/recap/{recent_y}/{recent_m}/' not in html, (
        'the held month is still a link, and following it lands on a gate that returns here'
    )


def test_nothing_is_held_when_the_sync_is_fresh(client):
    profile = _hunter(fresh=True)
    (recent_y, recent_m), (old_y, old_m) = _with_two_months(profile)

    html = _page(client, profile)

    assert 'Waiting on a sync' not in html
    assert 'data-recap-refresh' not in html
    for y, m in ((recent_y, recent_m), (old_y, old_m)):
        assert f'/recap/{y}/{m}/' in html, f'{y}-{m} should be openable'


def test_a_stale_hunter_with_nothing_listed_is_told_to_sync_not_that_they_have_nothing(client):
    """The sync that would bring their months across has not run, so "No months to wrap yet" is not just
    unhelpful, it is wrong."""
    profile = _hunter(fresh=False)

    html = _page(client, profile)

    assert 'Waiting on a sync' in html
    assert 'No months to wrap yet' not in html


def test_an_empty_archive_with_a_fresh_sync_still_says_it_is_empty(client):
    """The other side of that branch -- a genuinely new hunter is not told to go and sync."""
    profile = _hunter(fresh=True)

    html = _page(client, profile)

    assert 'No months to wrap yet' in html
    assert 'Waiting on a sync' not in html


def test_the_held_month_is_still_gated_when_opened_directly(client):
    """Scoping the landing page must not open the month itself: its data is the thing that is incomplete.
    The per-month gate was already correctly scoped to the recent month and stays as it was."""
    profile = _hunter(fresh=False)
    (recent_y, recent_m), (old_y, old_m) = _with_two_months(profile)
    client.force_login(profile.user)

    held = client.get(f'/recap/{recent_y}/{recent_m}/')
    assert 'sync_gate' in held.context, 'the held month opened despite incomplete data'

    older = client.get(f'/recap/{old_y}/{old_m}/')
    assert older.status_code == 200
    assert not older.context.get('sync_gate'), 'an older month is gated by the recent month being stale'
