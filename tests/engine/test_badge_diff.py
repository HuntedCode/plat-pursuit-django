"""Unit tests for the PURE diff (trophies/services/badge_apply.diff).

diff compares the engine's DesiredState against the profile's currently-held rows and yields the minimal change
set. Binary model: award / revoke / update. No DB -- DesiredState entries only need base_earned/holo/earned_date,
so we stand them in with SimpleNamespace; a held row is a CurrentBadge(is_holo, earned_at).
"""
from datetime import date
from types import SimpleNamespace

from trophies.services.badge_apply import diff, CurrentBadge, BadgeChange


def _desired(base_earned, holo=False, earned_date=None):
    return SimpleNamespace(base_earned=base_earned, holo=holo, earned_date=earned_date)


def test_award_when_earned_and_no_row():
    changes = diff({1: _desired(True, holo=False, earned_date=date(2026, 1, 1))}, {})
    assert changes == [BadgeChange(1, 'award', False, date(2026, 1, 1))]


def test_award_carries_holo():
    changes = diff({1: _desired(True, holo=True)}, {})
    assert changes[0].action == 'award' and changes[0].holo is True


def test_revoke_when_not_earned_but_currently_held():
    changes = diff({1: _desired(False)}, {1: CurrentBadge(True, date(2026, 1, 1))})
    assert changes == [BadgeChange(1, 'revoke', False)]


def test_update_when_holo_flips():
    d = date(2026, 1, 1)
    up = diff({1: _desired(True, holo=True, earned_date=d)}, {1: CurrentBadge(False, d)})
    assert up == [BadgeChange(1, 'update', True, d)]
    down = diff({1: _desired(True, holo=False, earned_date=d)}, {1: CurrentBadge(True, d)})
    assert down == [BadgeChange(1, 'update', False, d)]


def test_update_when_earned_date_shifts():
    # Badge iteration changed -> the held row's completion date must resync (keeps the leaderboard honest).
    old, new = date(2026, 1, 1), date(2026, 6, 1)
    changes = diff({1: _desired(True, holo=False, earned_date=new)}, {1: CurrentBadge(False, old)})
    assert changes == [BadgeChange(1, 'update', False, new)]


def test_no_change_when_held_holo_and_date_match():
    d = date(2026, 1, 1)
    assert diff({1: _desired(True, holo=True, earned_date=d)}, {1: CurrentBadge(True, d)}) == []


def test_no_change_when_not_earned_and_no_row():
    assert diff({1: _desired(False)}, {}) == []
