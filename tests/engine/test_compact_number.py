"""`compact_number` -- a large figure in a narrow space ("+1.5k" in a job page's contract ring, player counts).

It truncates and never rounds up: the ring labels XP a contract pays, so 1,999 must not read "2k" (owner
asked for the short form on 2026-10-24; the audit caught `.1f` rounding an override's share upward).
"""
import pytest

from core.templatetags.custom_filters import compact_number


@pytest.mark.parametrize('value,expected', [
    (0, '0'),
    (999, '999'),
    (1000, '1k'),
    (1200, '1.2k'),
    (1500, '1.5k'),
    (1666, '1.6k'),        # 5,000 over three jobs: rounding said 1.7k, more than the job pays
    (1999, '1.9k'),
    (2000, '2k'),
    (6000, '6k'),
    (128400, '128.4k'),
    (999999, '999.9k'),
    (1000000, '1M'),
    (1550000, '1.5M'),
    ('1500', '1.5k'),
])
def test_compact_number(value, expected):
    assert compact_number(value) == expected


def test_a_non_number_passes_through():
    assert compact_number('n/a') == 'n/a'
    assert compact_number(None) is None
