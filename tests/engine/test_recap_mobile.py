"""Recap on a phone.

The ceremony is a fixed full-screen takeover with no scroll, so anything that does not fit is not
merely awkward -- it is unreachable. These pin the two things a mobile pass found, both of which the
suite could not have caught: a value clipped against the viewport edge, and a control too small to tap.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_versus_row_reserves_room_for_a_three_digit_value():
    """"100%" at `.pp-tally--sm` (18px bold) is ~55px wide and the column reserved 3rem (48px), so the
    percent sign was clipped against the phone's right edge on both rows.

    The column stays a FIXED width rather than `auto`: each row is its own grid, so an auto column sizes
    to its own content and the two bars stop sharing a baseline -- which the slide exists to show.
    """
    css = (ROOT / 'static' / 'css' / 'components' / 'recap-deck.css').read_text(encoding='utf-8')
    rule = re.search(r'\.rcp-vs__row \{[^}]*\}', css).group(0)

    cols = re.search(r'grid-template-columns:\s*([^;]+);', rule).group(1)
    assert 'auto' not in cols, 'an auto column breaks the shared baseline between the two bars'
    width = float(re.search(r'([\d.]+)rem\s*;?\s*$', cols.strip()).group(1))
    assert width >= 3.5, f'the value column is {width}rem; "100%" needs ~3.5rem and clipped at 3'


def test_the_entrance_controls_are_thumb_sized():
    """The entrance is the screen most likely to be seen on a phone and never on a desktop. Its two
    secondary controls are text links by design -- making them buttons would flatten the hierarchy the
    primary action needs -- so the hit area comes from padding with an equal negative margin, which buys
    the target without changing the layout."""
    css = (ROOT / 'static' / 'css' / 'components' / 'recap-stage.css').read_text(encoding='utf-8')
    rule = re.search(r'\.rcx-enter__aside a,\s*\n\.rcx-enter__link \{[^}]*\}', css).group(0)

    # Checked numerically, not by substring: 'padding: 0' is a PREFIX of 'padding: 0.7rem', so the
    # obvious string test passes on the very value it is meant to reject.
    pad = re.search(r'padding:\s*([\d.]+)rem', rule)
    assert pad and float(pad.group(1)) >= 0.6, 'not enough padding to reach a thumb-sized target (was 19px)'
    assert 'margin: -' in rule, 'padding without the negative margin pushes the row out of place'


def test_the_community_caption_agrees_with_its_subject():
    """It read "1 hunter have played it"."""
    tpl = (ROOT / 'templates' / 'recap' / 'partials' / 'slides' / 'community.html').read_text(encoding='utf-8')

    assert 'pluralize:"has,have"' in tpl, 'the verb no longer agrees with the hunter count'
    assert 'hunter{{ played_count|pluralize }} have played' not in tpl
