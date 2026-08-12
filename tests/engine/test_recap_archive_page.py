"""The recap's landing page at `/recap/`.

This page spent its whole life as a redirect. `RecapIndexView.get` bounced you into your most recent
month whenever a recap existed, falling back to the current one -- so it only ever RENDERED for a hunter
with no trophy activity at all. The archive was unreachable from its own URL, and a second month picker
had to live at the bottom of the recap page to compensate for that.

Two pickers, drifting independently, neither of them at the address the nav pointed at. These tests pin
the landing behaviour, the per-month figures the tiles are built from, and the whale-safety of the
queries behind them.
"""
from datetime import datetime

import pytest
import pytz
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
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


def _trophy_at(profile, when, trophy_type='bronze', game=None):
    game = game or GameFactory()
    return EarnedTrophyFactory(
        profile=profile, trophy=TrophyFactory(game=game, trophy_type=trophy_type),
        earned=True, earned_date_time=when,
    )


def _utc(y, m, d, h=12):
    return pytz.UTC.localize(datetime(y, m, d, h))


def _prev_month():
    now = timezone.now()
    return (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)


# ── The landing page renders instead of redirecting ───────────────────────────


def test_the_index_renders_rather_than_redirecting(client):
    """The headline of this change. A hunter with a recap used to be bounced straight out of this URL,
    which is why nobody had ever seen the page the nav links to."""
    profile = _hunter()
    year, month = _prev_month()
    _trophy_at(profile, _utc(year, month, 15))

    client.force_login(profile.user)
    response = client.get(reverse('recap_index'))

    assert response.status_code == 200, 'the landing page still redirects'
    assert b'rca-hero' in response.content, 'the latest month is not featured'


def test_the_latest_month_leads_and_links_to_its_ceremony(client):
    profile = _hunter()
    year, month = _prev_month()
    _trophy_at(profile, _utc(year, month, 15))

    client.force_login(profile.user)
    body = client.get(reverse('recap_index')).content.decode()

    assert reverse('recap_view', kwargs={'year': year, 'month': month}) in body


def test_opening_the_index_does_not_generate_recaps(client):
    """It used to call `get_or_generate_recap` for two months just to decide where to send you.
    Generating a recap for every month a hunter merely glanced at is work nobody asked for -- and on a
    whale it is the expensive kind. Opening a month still generates it."""
    profile = _hunter()
    year, month = _prev_month()
    _trophy_at(profile, _utc(year, month, 15))

    client.force_login(profile.user)
    client.get(reverse('recap_index'))

    assert not MonthlyRecap.objects.filter(profile=profile).exists(), (
        'merely viewing the archive wrote recap rows'
    )


def test_a_hunter_with_no_activity_gets_the_empty_state(client):
    profile = _hunter()

    client.force_login(profile.user)
    response = client.get(reverse('recap_index'))

    assert response.status_code == 200
    assert b'No months to wrap yet' in response.content
    assert b'rca-hero' not in response.content


# ── What the tiles are built from ─────────────────────────────────────────────


def test_each_month_carries_its_totals_and_platinum_count():
    """A picker made of bare month names is what made the old page read as a list of dates. The trophy
    count was already being computed inside `months_with_activity` (`Count('id')`, purely to force the
    GROUP BY) and thrown away."""
    profile = _hunter()
    for _ in range(3):
        _trophy_at(profile, _utc(2024, 3, 5))
    _trophy_at(profile, _utc(2024, 3, 9), trophy_type='platinum')
    _trophy_at(profile, _utc(2024, 1, 2), trophy_type='gold')

    archive = MonthlyRecapService.get_archive(profile)
    by_month = {(m['year'], m['month']): m for y in archive['years'] for m in y['months']}

    assert by_month[(2024, 3)]['total'] == 4
    assert by_month[(2024, 3)]['platinums'] == 1
    assert by_month[(2024, 1)]['total'] == 1
    assert by_month[(2024, 1)]['platinums'] == 0, 'a gold counted as a platinum'


def test_the_newest_month_is_both_the_hero_and_in_the_archive():
    """`latest` is pulled out because the page leads with it, but removing it from the years would make
    the archive lie about which months exist."""
    profile = _hunter()
    _trophy_at(profile, _utc(2024, 3, 5))
    _trophy_at(profile, _utc(2024, 5, 5))

    archive = MonthlyRecapService.get_archive(profile)
    listed = {(m['year'], m['month']) for y in archive['years'] for m in y['months']}

    assert (archive['latest']['year'], archive['latest']['month']) == (2024, 5)
    assert archive['latest']['is_latest'] is True
    assert (2024, 5) in listed and (2024, 3) in listed


def test_years_and_months_both_run_newest_first():
    profile = _hunter()
    for year, month in ((2023, 4), (2024, 2), (2024, 11)):
        _trophy_at(profile, _utc(year, month, 5))

    archive = MonthlyRecapService.get_archive(profile)

    assert [y['year'] for y in archive['years']] == [2024, 2023]
    assert [m['month'] for m in archive['years'][0]['months']] == [11, 2]


def test_a_watched_month_is_marked_and_an_unopened_one_is_not():
    """Absence of a MonthlyRecap row IS "unseen": rows are created by opening a month. So the seen set
    needs no join and no per-month lookup."""
    profile = _hunter()
    _trophy_at(profile, _utc(2024, 3, 5))
    _trophy_at(profile, _utc(2024, 4, 5))
    MonthlyRecap.objects.create(profile=profile, year=2024, month=3, has_been_viewed=True)

    seen = {(m['year'], m['month']): m['seen']
            for y in MonthlyRecapService.get_archive(profile)['years'] for m in y['months']}

    assert seen[(2024, 3)] is True
    assert seen[(2024, 4)] is False


def test_the_in_progress_month_is_never_offered():
    """The recap page 404s it, so listing it is a door that does not open."""
    profile = _hunter()
    now = timezone.now()
    _trophy_at(profile, now.replace(day=15, hour=12))

    archive = MonthlyRecapService.get_archive(profile)
    listed = {(m['year'], m['month']) for y in archive['years'] for m in y['months']}

    assert (now.year, now.month) not in listed
    assert archive['latest'] is None or archive['latest']['month'] != now.month


# ── Whale safety ──────────────────────────────────────────────────────────────


def test_the_archive_aggregates_in_the_database_and_does_not_scale_with_history():
    """Per-user querysets must DB-aggregate (CLAUDE.md). The tiles need a count and a platinum count per
    month, and the tempting shape -- fetch the rows, tally them in Python -- is exactly the pattern that
    OOMs a 250k-trophy library.

    Asserted two ways, because neither alone is enough: the SQL must GROUP BY (a Python tally over
    `.values_list()` also issues few queries), and the query COUNT must not grow with the number of
    months (a per-month lookup would pass the SQL check while N+1'ing the page)."""
    profile = _hunter()
    _trophy_at(profile, _utc(2024, 3, 5))

    with CaptureQueriesContext(connection) as few:
        MonthlyRecapService.get_archive(profile)
    baseline = len(few.captured_queries)

    assert any('GROUP BY' in q['sql'].upper() for q in few.captured_queries), (
        'nothing groups in the database; the totals are being tallied in Python'
    )

    for month in range(4, 12):
        _trophy_at(profile, _utc(2024, month, 5))
        _trophy_at(profile, _utc(2023, month, 5), trophy_type='platinum')

    with CaptureQueriesContext(connection) as many:
        MonthlyRecapService.get_archive(profile)

    assert len(many.captured_queries) == baseline, (
        f'{baseline} queries for one month but {len(many.captured_queries)} for seventeen -- '
        f'the archive is querying per month'
    )
