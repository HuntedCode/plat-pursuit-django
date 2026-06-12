"""Tests for the refresh_badge_series command's --all / --series dispatch.

The per-series work (handle_badge + leaderboards) is exercised elsewhere; these
pin the new routing: --all hits every DISTINCT non-blank slug exactly once,
--series hits one, and neither is a no-op.
"""
import pytest
from django.core.management import call_command

from trophies.management.commands.refresh_badge_series import Command
from tests.factories import BadgeFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def spy(monkeypatch):
    seen = []
    monkeypatch.setattr(Command, '_refresh_series', lambda self, slug, position='': seen.append(slug))
    return seen


def test_all_refreshes_each_distinct_series(spy):
    BadgeFactory(series_slug='aaa', tier=1)
    BadgeFactory(series_slug='aaa', tier=2)   # same series, two tiers -> counted once
    BadgeFactory(series_slug='bbb', tier=1)
    BadgeFactory(series_slug='', tier=1)      # blank slug -> excluded

    call_command('refresh_badge_series', '--all')

    assert sorted(spy) == ['aaa', 'bbb']


def test_series_refreshes_just_one(spy):
    call_command('refresh_badge_series', '--series', 'zzz')
    assert spy == ['zzz']


def test_no_args_is_a_noop(spy):
    call_command('refresh_badge_series')   # neither --series nor --all
    assert spy == []
