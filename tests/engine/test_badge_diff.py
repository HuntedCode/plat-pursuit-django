"""Unit tests for the PURE diff (trophies/services/badge_apply.diff).

diff compares the engine's DesiredState against the profile's current earns and yields the minimal change set.
No DB — DesiredState entries only need base_earned/holo/earned_date, so we stand them in with SimpleNamespace.
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


def test_reactivate_from_maintenance():
    changes = diff({1: _desired(True, holo=False)}, {1: CurrentBadge('maintenance', False)})
    assert [c.action for c in changes] == ['reactivate']


def test_lapse_when_not_earned_but_currently_earned():
    changes = diff({1: _desired(False)}, {1: CurrentBadge('earned', True)})
    assert changes == [BadgeChange(1, 'lapse', False)]


def test_holo_flips_both_ways():
    up = diff({1: _desired(True, holo=True)}, {1: CurrentBadge('earned', False)})
    assert up == [BadgeChange(1, 'holo', True)]
    down = diff({1: _desired(True, holo=False)}, {1: CurrentBadge('earned', True)})
    assert down == [BadgeChange(1, 'holo', False)]


def test_no_change_when_earned_and_holo_match():
    assert diff({1: _desired(True, holo=True)}, {1: CurrentBadge('earned', True)}) == []


def test_no_change_when_not_earned_and_no_row():
    assert diff({1: _desired(False)}, {}) == []


def test_no_relapse_when_already_maintenance():
    # No-delete / no-re-lapse: an already-lapsed badge that's still not earned stays put.
    assert diff({1: _desired(False)}, {1: CurrentBadge('maintenance', False)}) == []
