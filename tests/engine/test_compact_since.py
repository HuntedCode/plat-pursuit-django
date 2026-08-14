"""`compact_since` -- relative time in ONE unit, for a narrow stat cell.

Written for the hunter wall's sort-adaptive slot. `naturaltime` is the right filter for prose and the
wrong one for a stat cell: it returns things like "2 days, 19 hours ago", roughly three times the width
of the figure the cell was built around, so it wraps or ellipsises there.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from core.templatetags.custom_filters import compact_since


@pytest.mark.parametrize('delta,expected', [
    (timedelta(seconds=5), 'Now'),
    (timedelta(seconds=59), 'Now'),
    (timedelta(minutes=1), '1m ago'),
    (timedelta(minutes=59), '59m ago'),
    (timedelta(hours=1), '1h ago'),
    (timedelta(hours=23), '23h ago'),
    (timedelta(days=1), '1d ago'),
    (timedelta(days=6), '6d ago'),
    (timedelta(days=7), '1w ago'),
    (timedelta(days=29), '4w ago'),
    (timedelta(days=30), '1mo ago'),
    (timedelta(days=330), '11mo ago'),
    # 12 x 30d. The units are internally consistent so nothing ever prints "12mo ago".
    (timedelta(days=360), '1y ago'),
    (timedelta(days=364), '1y ago'),
    (timedelta(days=365), '1y ago'),
    (timedelta(days=900), '2y ago'),
])
def test_it_reports_one_unit_only(delta, expected):
    assert compact_since(timezone.now() - delta) == expected


def test_a_missing_value_returns_empty_so_the_caller_decides():
    """The hunter card prints "Never" for a profile that has never synced; a filter that invented its own
    wording would put that decision in the wrong place."""
    assert compact_since(None) == ''


def test_a_future_timestamp_reads_as_now_not_as_a_negative_age():
    """Clock skew between the app and the database is real, and "-3h ago" is worse than a small lie."""
    assert compact_since(timezone.now() + timedelta(hours=3)) == 'Now'


def test_a_non_datetime_does_not_explode_the_page():
    assert compact_since('not a datetime') == ''
