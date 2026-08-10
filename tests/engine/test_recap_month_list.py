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
    returns None for it, so offering it would be a door that opens onto the no-activity page."""
    profile = _hunter()
    _trophy_at(profile, _utc(2024, 3, 15))

    months = MonthlyRecapService.months_with_activity(profile)

    assert (2024, 4) not in months and (2024, 2) not in months


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


def test_the_current_month_is_not_listed():
    """The page 404s the in-progress month (a Wrapped is a retrospective), so listing it only ever
    offered a door that does not open -- which is what the old free-tier list did, exclusively."""
    profile = _hunter()
    now = timezone.now()
    _trophy_at(profile, now - timezone.timedelta(hours=1))

    listed = {(m['year'], m['month']) for m in MonthlyRecapService.get_available_months(profile)}

    assert (now.year, now.month) not in listed


def test_the_flat_list_and_the_calendar_agree():
    """Two month lists render on the same page. They used to come from different logic and disagree --
    the calendar knew about the hunter's full range while the flat list showed stored rows only."""
    profile = _hunter()
    _trophy_at(profile, _utc(2024, 3, 15))
    _trophy_at(profile, _utc(2024, 9, 2))

    flat = {(m['year'], m['month']) for m in MonthlyRecapService.get_available_months(profile)}
    cal = MonthlyRecapService.get_available_months_by_year(profile)
    calendar_has_data = {
        (y['year'], m['month'])
        for y in cal['years'] for m in y['months'] if m['has_data']
    }

    assert flat == calendar_has_data


def test_the_calendar_carries_no_premium_flag():
    """Nothing is premium-required any more; leaving the key would let a template keep gating on it."""
    profile = _hunter()
    _trophy_at(profile, _utc(2024, 3, 15))

    cal = MonthlyRecapService.get_available_months_by_year(profile)
    every_month = [m for y in cal['years'] for m in y['months']]

    assert every_month, 'calendar built no months'
    assert not any('is_premium_required' in m for m in every_month)


def test_the_month_query_does_not_scale_with_trophies(django_assert_num_queries):
    """DB-aggregated: one grouped COUNT returning a row per month, never the trophies themselves. The
    (profile, earned, earned_date_time) composite index serves it."""
    profile = _hunter()
    game = GameFactory()
    for day in range(1, 20):
        _trophy_at(profile, _utc(2024, 3, day), game=game)

    with django_assert_num_queries(1):
        months = MonthlyRecapService.months_with_activity(profile)

    assert months == {(2024, 3)}
