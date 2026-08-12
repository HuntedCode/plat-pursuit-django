"""The time-of-day slide: the bars have to read left-to-right as the day runs.

Both of these are bugs that were invisible in source and only showed up on screen.

The order one is the interesting one. `periods` is a dict inside a JSONField, and Postgres `jsonb` does
not preserve key order -- it normalises to (key length, then bytes), so Morning/Afternoon/Evening/Late
Night comes back as Evening/Morning/Afternoon/Late Night. The slide draws them as a bar chart, so the
day was silently being drawn out of sequence, with the evening first.
"""
import re
from pathlib import Path

import pytest

from tests.factories import ProfileFactory
from trophies.models import MonthlyRecap
from trophies.services.monthly_recap_service import DECK_BY_TYPE, TIME_PERIODS, _period_bars

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.django_db


def test_jsonb_really_does_reorder_the_periods():
    """The premise, pinned against the actual database rather than assumed. If a future Django or driver
    starts preserving order this test fails loudly and the workaround below can be reconsidered -- rather
    than the workaround quietly outliving the reason for it."""
    profile = ProfileFactory(is_linked=True)
    stored = {'Morning': 28, 'Afternoon': 63, 'Evening': 41, 'Late Night': 3}
    recap = MonthlyRecap.objects.create(profile=profile, year=2026, month=3,
                                        time_analysis_data={'periods': stored})

    back = MonthlyRecap.objects.get(pk=recap.pk).time_analysis_data['periods']

    assert list(back) != list(stored), (
        'jsonb now preserves key order -- the ordered-bars workaround may no longer be needed'
    )


def test_the_bars_run_in_clock_order_whatever_the_dict_says():
    """The fix. Order comes from TIME_PERIODS at build time, so what storage hands back cannot change it."""
    profile = ProfileFactory(is_linked=True)
    recap = MonthlyRecap.objects.create(
        profile=profile, year=2026, month=3,
        time_analysis_data={'periods': {'Morning': 28, 'Afternoon': 63, 'Evening': 41, 'Late Night': 3},
                            'persona': 'day_hunter', 'peak_period': 'Afternoon', 'peak_hour_12': '5PM'},
    )
    recap = MonthlyRecap.objects.get(pk=recap.pk)          # round-trip, so the dict is jsonb's order

    payload = DECK_BY_TYPE['time_analysis'].payload(recap, {})

    assert [b['label'] for b in payload['period_bars']] == ['Morn', 'Aftn', 'Eve', 'Late']
    assert [b['count'] for b in payload['period_bars']] == [28, 63, 41, 3], (
        'the counts no longer follow their own labels'
    )


def test_a_missing_period_is_a_zero_bar_not_a_missing_one():
    """Four columns always. A month with nothing after midnight should show an empty Late column, not
    three columns and a different-shaped chart."""
    bars = _period_bars({'Morning': 5})
    assert len(bars) == len(TIME_PERIODS) == 4
    assert [b['count'] for b in bars] == [5, 0, 0, 0]


def test_the_template_does_not_iterate_the_dict():
    """Where the bug actually lived: `{% for period, count in periods.items %}` renders in whatever order
    the database returned."""
    tpl = (ROOT / 'templates' / 'recap' / 'partials' / 'slides'
           / 'time_analysis.html').read_text(encoding='utf-8')
    tpl = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', tpl, flags=re.S)
    assert 'periods.items' not in tpl, 'the slide is back to iterating the stored dict'
    assert 'period_bars' in tpl


def test_the_peak_hour_pill_can_actually_be_pushed_off_the_bars():
    """`.rcp__stamp` is a <span> in a plain block, so it was a non-replaced INLINE box -- and vertical
    margins do not apply to those. Both its own margin-top and the `.rcp-clock + .rcp__stamp` override
    written specifically to stop it crowding the bars were computing correctly and being discarded:
    measured 20px of margin and 0px of gap, with the pill sitting against the bar labels."""
    css = (ROOT / 'static' / 'css' / 'output.css').read_text(encoding='utf-8')
    rule = re.search(r'\.rcp__stamp\s*\{([^}]*)\}', css)
    assert rule, '.rcp__stamp has no rule'
    assert re.search(r'display:\s*inline-block', rule.group(1)), (
        'the stamp is an inline box again, so its margins are silently ignored'
    )
    assert re.search(r'\.rcp-clock\s*\+\s*\.rcp__stamp', css), 'the clock-specific spacing is gone'
