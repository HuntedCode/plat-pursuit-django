"""Who can open which recap month.

Written BEFORE opening the recap up, because the recap had no test coverage at all and the gates are
easy to remove by accident along with the premium one. The gates that must SURVIVE are pinned here
first; the premium tests below describe the new behaviour.

The ladder, in order (trophies/recap_views.py + api/recap_views.py):
  1. sync gate      -- no profile / not yet synced    -> gated index page
  2. valid month    -- 1..12                          -> 404
  3. current month  -- in progress                    -> 404   (a Wrapped is a retrospective)
  4. sync freshness -- must have synced this month    -> stale page
  5. future month                                     -> 404
"""
import calendar

import pytest
from django.urls import reverse
from django.utils import timezone

from trophies.models import MonthlyRecap
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def _hunter(*, premium=False, synced=True):
    """A linked, synced hunter -- the state every recap gate assumes before it does anything.

    `premium` is a real field (Profile.user_is_premium), set here so the tests below can assert that it
    makes NO difference. An earlier version took the argument and ignored it, which made the headline
    test of this change pass only because the factory happens to default to non-premium."""
    profile = ProfileFactory(is_linked=True, sync_status='synced', user_is_premium=premium)
    if synced:
        profile.last_synced = timezone.now()
        profile.save(update_fields=['last_synced'])
    return profile


def _prev_month(now=None):
    now = now or timezone.now()
    return (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)


def _recap(profile, year, month, **over):
    fields = dict(profile=profile, year=year, month=month, total_trophies_earned=10,
                  platinums_earned=1, is_finalized=True)
    fields.update(over)
    return MonthlyRecap.objects.create(**fields)


def _get(client, profile, year, month):
    client.force_login(profile.user)
    return client.get(reverse('recap_view', kwargs={'year': year, 'month': month}))


# ── Gates that must survive ───────────────────────────────────────────────────


def test_the_current_month_is_not_viewable(client):
    """A Wrapped is a retrospective. A live month is a stats lookup, which is the opposite of the
    experience -- so the in-progress month stays closed even though every other gate is being opened."""
    profile = _hunter()
    now = timezone.now()

    assert _get(client, profile, now.year, now.month).status_code == 404


def test_a_future_month_is_not_viewable(client):
    profile = _hunter()
    now = timezone.now()

    assert _get(client, profile, now.year + 1, 6).status_code == 404


def test_an_invalid_month_is_not_viewable(client):
    profile = _hunter()

    assert _get(client, profile, 2025, 13).status_code == 404
    assert _get(client, profile, 2025, 0).status_code == 404


def test_an_unsynced_profile_gets_the_sync_gate_not_a_recap(client):
    """`sync_status != 'synced'` short-circuits before any month logic."""
    profile = ProfileFactory(is_linked=True, sync_status='syncing')
    year, month = _prev_month()

    resp = _get(client, profile, year, month)

    assert resp.status_code == 200
    assert resp.context.get('sync_gate') == 'syncing'


def test_a_stale_sync_blocks_the_recent_month(client):
    """Freshness gate: the hunter must have synced within the current calendar month, or last month's
    recap would be built from data that is missing its final days."""
    profile = _hunter(synced=False)
    profile.last_synced = timezone.now() - timezone.timedelta(days=90)
    profile.save(update_fields=['last_synced'])
    year, month = _prev_month()

    resp = _get(client, profile, year, month)

    assert resp.context.get('sync_gate') == 'sync_stale'


def test_opening_a_recap_marks_it_viewed(client):
    """`has_been_viewed` is what gates the dashboard share-card module -- not the email flags."""
    profile = _hunter()
    year, month = _prev_month()
    recap = _recap(profile, year, month, has_been_viewed=False)

    _get(client, profile, year, month)

    recap.refresh_from_db()
    assert recap.has_been_viewed is True


# ── The gate that is being removed ────────────────────────────────────────────


@pytest.mark.parametrize('premium', [False, True], ids=['free', 'premium'])
def test_any_hunter_can_open_an_old_month(client, premium):
    """THE change. Previously any month older than the most recent completed one redirected a
    non-premium hunter to the index. Recaps are a record of what someone did; charging to look back at
    your own history is the wrong thing to sell.

    Parametrized over the real `user_is_premium` field so this asserts the actual claim -- that the two
    hunters are treated IDENTICALLY -- rather than passing because the factory defaults to non-premium."""
    profile = _hunter(premium=premium)
    now = timezone.now()
    old_year, old_month = (now.year - 1, 6)
    _recap(profile, old_year, old_month)

    resp = _get(client, profile, old_year, old_month)

    assert resp.status_code == 200, 'an old month must open regardless of premium'


def test_a_free_hunter_can_still_open_the_recent_month(client):
    """The month that was already free must not regress while the gate is removed."""
    profile = _hunter(premium=False)
    year, month = _prev_month()
    _recap(profile, year, month)

    assert _get(client, profile, year, month).status_code == 200


# ── The API's month bounds ────────────────────────────────────────────────────

RECAP_ENDPOINTS = [
    '/api/v1/recap/{y}/{m}/',
    '/api/v1/recap/{y}/{m}/html/',
    '/api/v1/recap/{y}/{m}/png/',
    '/api/v1/recap/{y}/{m}/slide/intro/',
]


@pytest.mark.parametrize('url', RECAP_ENDPOINTS)
def test_an_out_of_range_year_is_rejected_not_a_crash(client, url):
    """Regression. Only RecapDetailView validated the year -- on the other three the premium 403 was
    incidentally doing the job, because `is_recent_or_current` is false for year 0. Removing the gate
    exposed the real hole: `datetime(0, 1, 1)` raises deep inside get_month_date_range, so every
    logged-in hunter could 500 three endpoints with a URL."""
    profile = _hunter()
    client.force_login(profile.user)

    resp = client.get(url.format(y=0, m=1))

    assert resp.status_code == 400, 'must be rejected at the gate, not raise'


@pytest.mark.parametrize('url', RECAP_ENDPOINTS)
def test_a_future_month_is_rejected_on_every_endpoint(client, url):
    """The same non-uniformity: only one of the four checked. The bounds helper is shared now."""
    profile = _hunter()
    client.force_login(profile.user)
    now = timezone.now()

    resp = client.get(url.format(y=now.year + 1, m=1))

    assert resp.status_code == 400


@pytest.mark.parametrize('url', RECAP_ENDPOINTS)
def test_an_invalid_month_is_rejected_on_every_endpoint(client, url):
    profile = _hunter()
    client.force_login(profile.user)

    assert client.get(url.format(y=2025, m=13)).status_code == 400
