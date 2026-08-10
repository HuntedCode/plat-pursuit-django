"""Which months the recap offers.

The month list is what actually unlocks history. `get_or_generate_recap` has always been able to build a
recap for any month on demand -- but the picker only offered months that already had a stored MonthlyRecap
row, and rows are created BY opening a month. A month with no row was never offered, so it was never
opened, so it never got a row. These pin the way out of that.
"""
from datetime import datetime

import pytest
import pytz
from django.utils import timezone

from trophies.models import MonthlyRecap
from trophies.services.monthly_recap_service import MonthlyRecapService
from tests.factories import (
    EarnedTrophyFactory, GameFactory, ProfileFactory, TrophyFactory,
)

pytestmark = pytest.mark.django_db


def _hunter(tz='UTC'):
    profile = ProfileFactory(is_linked=True, sync_status='synced')
    profile.user.user_timezone = tz
    profile.user.save(update_fields=['user_timezone'])
    profile.last_synced = timezone.now()
    profile.save(update_fields=['last_synced'])
    return profile


def _trophy_at(profile, when, game=None):
    game = game or GameFactory()
    return EarnedTrophyFactory(
        profile=profile, trophy=TrophyFactory(game=game), earned=True, earned_date_time=when,
    )


def _utc(y, m, d, h=12):
    return pytz.UTC.localize(datetime(y, m, d, h))


# ── months_with_activity ──────────────────────────────────────────────────────


def test_a_month_with_a_trophy_is_offered_without_a_stored_recap():
    """THE fix. No MonthlyRecap row exists for this month and the hunter has never opened it -- it must
    still be offered, or the row can never come into being."""
    profile = _hunter()
    _trophy_at(profile, _utc(2024, 3, 15))

    assert MonthlyRecap.objects.filter(profile=profile).count() == 0
    assert (2024, 3) in MonthlyRecapService.months_with_activity(profile)


def test_a_month_with_no_trophies_is_not_offered():
    """The recap is built from trophies; a month with none has nothing to say. `get_or_generate_recap`
    returns None for it, so offering it would be a door onto the no-activity page.

    Asserted as an exact set with the neighbouring months seeded: `x not in y` alone would pass on an
    implementation that returned nothing at all."""
    profile = _hunter()
    _trophy_at(profile, _utc(2024, 3, 15))
    _trophy_at(profile, _utc(2024, 5, 20))

    months = MonthlyRecapService.months_with_activity(profile)

    assert months == {(2024, 3), (2024, 5)}, 'April has no trophies and must not be offered'


def test_unearned_trophies_do_not_make_a_month():
    profile = _hunter()
    game = GameFactory()
    EarnedTrophyFactory(profile=profile, trophy=TrophyFactory(game=game),
                        earned=False, earned_date_time=_utc(2024, 5, 2))

    assert MonthlyRecapService.months_with_activity(profile) == set()


def test_months_are_bucketed_in_the_hunters_own_timezone():
    """A trophy at 03:00 UTC on 1 March is 19:00 on 28 February in Los Angeles. The hunter experienced
    it in February, and the recap they open should agree -- the whole service already works in local
    time, and the month list has to match or the picker offers a month the recap says is empty."""
    profile = _hunter(tz='America/Los_Angeles')
    _trophy_at(profile, _utc(2026, 3, 1, 3))

    months = MonthlyRecapService.months_with_activity(profile)

    assert months == {(2026, 2)}, 'bucketed by UTC instead of the hunter local month'


def test_another_hunters_trophies_are_not_your_months():
    mine, theirs = _hunter(), _hunter()
    _trophy_at(theirs, _utc(2024, 7, 4))

    assert MonthlyRecapService.months_with_activity(mine) == set()


# ── the flat list + the calendar agree ────────────────────────────────────────


def test_the_current_month_is_not_listed_but_past_months_are():
    """The page 404s the in-progress month (a Wrapped is a retrospective), so listing it only ever
    offered a door that does not open -- which is what the old free-tier list did, exclusively.

    The past month is the positive control. Without it the listed set is empty and the assertion holds
    for an implementation that returns nothing. The current-month trophy is placed mid-month rather than
    "an hour ago", which lands in the PREVIOUS month when the suite runs just after a boundary."""
    profile = _hunter()
    now = timezone.now()
    _trophy_at(profile, now.replace(day=15, hour=12))
    past_year, past_month = (now.year - 1, 6)
    _trophy_at(profile, _utc(past_year, past_month, 10))

    listed = {(m['year'], m['month']) for m in MonthlyRecapService.get_available_months(profile)}

    assert (past_year, past_month) in listed, 'a past month with trophies must be listed'
    assert (now.year, now.month) not in listed


def test_the_flat_list_and_the_calendar_agree_for_a_hunter_west_of_utc():
    """Two month lists render across the recap surfaces, and they must describe the same months.

    Deliberately a Los Angeles hunter whose first trophy is in the opening hours of a UTC month -- the
    exact case where they used to disagree. `months_with_activity` bucketed locally (February) while
    `earliest_month` was read straight off the UTC value (March), so the flat list offered February
    while the calendar marked it `is_before_first_trophy` and disabled it. A UTC fixture on the 15th
    cannot see that at all."""
    profile = _hunter(tz='America/Los_Angeles')
    _trophy_at(profile, _utc(2024, 3, 1, 3))     # 2024-02-29 19:00 in LA
    _trophy_at(profile, _utc(2024, 9, 2))

    flat = {(m['year'], m['month']) for m in MonthlyRecapService.get_available_months(profile)}
    cal = MonthlyRecapService.get_available_months_by_year(profile)
    calendar_has_data = {
        (y['year'], m['month'])
        for y in cal['years'] for m in y['months'] if m['has_data']
    }

    assert (2024, 2) in flat, 'the local month, not the UTC one'
    assert flat == calendar_has_data
    # ...and the calendar must not disable the month it just said has data.
    feb = next(m for y in cal['years'] if y['year'] == 2024
               for m in y['months'] if m['month'] == 2)
    assert feb['is_before_first_trophy'] is False


def test_the_calendar_reaches_back_to_a_local_year_the_utc_date_would_hide():
    """The sharper half of the same bug: a first trophy on 1 January UTC belongs to the PREVIOUS
    December locally, and the calendar's year range started at the UTC year -- so that December had no
    cell at all while the flat list offered it."""
    profile = _hunter(tz='America/Los_Angeles')
    _trophy_at(profile, _utc(2024, 1, 1, 3))     # 2023-12-31 19:00 in LA

    cal = MonthlyRecapService.get_available_months_by_year(profile)
    years = {y['year'] for y in cal['years']}

    assert 2023 in years, 'the local year must be reachable in the calendar'
    dec = next(m for y in cal['years'] if y['year'] == 2023
               for m in y['months'] if m['month'] == 12)
    assert dec['has_data'] is True


def test_the_calendar_carries_no_premium_flag():
    """Nothing is premium-required any more; leaving the key would let a template keep gating on it."""
    profile = _hunter()
    _trophy_at(profile, _utc(2024, 3, 15))

    cal = MonthlyRecapService.get_available_months_by_year(profile)
    every_month = [m for y in cal['years'] for m in y['months']]

    assert every_month, 'calendar built no months'
    assert not any('is_premium_required' in m for m in every_month)


def test_the_month_query_aggregates_in_the_database():
    """DB-aggregated: the grouping happens in Postgres and one row per month comes back, never the
    trophies themselves.

    Asserted on the SQL, not on a query COUNT: `for et in qs: months.add(...)` is also exactly one
    query, so counting cannot tell aggregation from iteration -- which is the distinction the whale rule
    in CLAUDE.md actually cares about."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    profile = _hunter()
    game = GameFactory()
    for day in range(1, 20):
        _trophy_at(profile, _utc(2024, 3, day), game=game)

    with CaptureQueriesContext(connection) as ctx:
        months = MonthlyRecapService.months_with_activity(profile)

    assert months == {(2024, 3)}
    sql = ' '.join(q['sql'] for q in ctx.captured_queries).upper()
    assert 'GROUP BY' in sql, 'the grouping must happen in the DB'
    assert 'DATE_TRUNC' in sql, 'and the month bucket must be computed there too'


# ── end to end: the chicken-and-egg ───────────────────────────────────────────


def test_a_month_with_no_stored_row_opens_and_creates_one(client):
    """The claim the whole change rests on, end to end.

    Every other test here checks the month LIST, and the access tests all pre-create a finalized recap.
    Neither exercises the actual chicken-and-egg: a month the hunter has trophies in, has never opened,
    and has no MonthlyRecap row for. It must open, build the recap, and persist it -- because that
    persistence is the only way a row ever comes into being for a historical month."""
    from django.urls import reverse

    profile = _hunter()
    now = timezone.now()
    year, month = (now.year - 1, 6)
    _trophy_at(profile, _utc(year, month, 12))
    assert not MonthlyRecap.objects.filter(profile=profile, year=year, month=month).exists()

    client.force_login(profile.user)
    resp = client.get(reverse('recap_view', kwargs={'year': year, 'month': month}))

    assert resp.status_code == 200
    row = MonthlyRecap.objects.get(profile=profile, year=year, month=month)
    assert row.total_trophies_earned == 1
    # Past months are immutable once built, so a second visit is a cheap read rather than a regenerate.
    assert row.is_finalized is True


def test_a_month_with_no_trophies_does_not_create_a_row(client):
    """The counterpart: opening a barren month must not litter the table with empty recaps, which would
    then feed the stats page's "Months Tracked" with months that never happened."""
    from django.urls import reverse

    profile = _hunter()
    now = timezone.now()
    year, month = (now.year - 1, 6)
    _trophy_at(profile, _utc(year, month, 12))          # activity in June only

    client.force_login(profile.user)
    client.get(reverse('recap_view', kwargs={'year': year, 'month': 7}))

    assert not MonthlyRecap.objects.filter(profile=profile, year=year, month=7).exists()
