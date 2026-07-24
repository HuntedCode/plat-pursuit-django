"""Tests for the format_time template filter (companion to format_date).

Used by the leaderboard rows to show the tiebreaker time under the date. Must be timezone-aware and honour
the viewer's 12/24-hour clock preference, exactly like format_date.
"""
from datetime import datetime, timezone as dt_timezone

import pytest
from django.utils import timezone

from core.templatetags.custom_filters import format_time


def test_returns_empty_string_for_non_datetime():
    assert format_time(None) == ''
    assert format_time('not a datetime') == ''


def test_defaults_to_12_hour_when_there_is_no_authenticated_user():
    moment = datetime(2026, 7, 23, 15, 42, tzinfo=dt_timezone.utc)

    with timezone.override('UTC'):
        assert format_time(moment) == '03:42 PM'


def test_honours_the_24_hour_preference(monkeypatch):
    moment = datetime(2026, 7, 23, 15, 42, tzinfo=dt_timezone.utc)

    class _User:
        is_authenticated = True
        use_24hr_clock = True

    class _Req:
        user = _User()

    monkeypatch.setattr('core.templatetags.custom_filters.get_current_request', lambda: _Req())

    with timezone.override('UTC'):
        assert format_time(moment) == '15:42'


def test_localises_to_the_active_timezone():
    """A UTC instant renders in whatever timezone is active (the middleware activates the viewer's)."""
    moment = datetime(2026, 7, 23, 2, 0, tzinfo=dt_timezone.utc)

    with timezone.override('America/New_York'):     # UTC-4 in July
        assert format_time(moment) == '10:00 PM'    # previous evening, local
