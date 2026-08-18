"""The shared window parser behind every virtualized board.

`range` becomes a SQL OFFSET and `count` a LIMIT, on endpoints that are all PUBLIC. Three boards used to
hand-roll this clamp and a fourth was about to, which is three chances to leave one unbounded -- so the
clamp lives in one place and this pins it there.
"""

import pytest
from django.test import RequestFactory

from trophies.views.board_helpers import MAX_COUNT, MAX_START, clamped_int, window_params


@pytest.mark.parametrize('raw, expected', [
    ('7', 7),
    (7, 7),
    ('0', 1),                 # below lo
    ('-9', 1),
    ('abc', 50),              # unparseable -> the caller's default
    ('', 50),
    (None, 50),
    ('3.5', 50),              # int('3.5') raises; a silent truncation to 3 would be a guess
    ('10000000000', 200),     # above hi
])
def test_clamped_int_parses_and_clamps(raw, expected):
    assert clamped_int(raw, 50, lo=1, hi=200) == expected


def test_an_out_of_range_default_is_returned_as_given():
    """`default` comes from the VIEW, not the request, so it is trusted. Clamping it would hide a bug in
    the view rather than surface one, and the request is the only untrusted half here."""
    assert clamped_int('abc', 9999, lo=1, hi=10) == 9999


def test_no_upper_bound_when_hi_is_omitted():
    assert clamped_int('10000000000', 1, lo=1) == 10_000_000_000


def _get(**params):
    return RequestFactory().get('/', params)


def test_window_params_defaults_to_the_first_window():
    assert window_params(_get(), 50) == (1, 50)


def test_window_params_reads_a_window():
    assert window_params(_get(range=51, count=50), 50) == (51, 50)


def test_a_crafted_window_is_clamped_at_both_ends():
    """The two failures this prevents are different. An unbounded `count` hydrates the whole board in one
    read; an unbounded `range` is a nine-figure OFFSET that Postgres honours by WALKING every skipped row,
    which is a scan any anonymous visitor could ask for by editing a URL."""
    start, count = window_params(_get(range=10 ** 12, count=10 ** 6), 50)
    assert count == MAX_COUNT
    assert start == MAX_START
    assert MAX_START < 10 ** 12, 'the start clamp no longer bounds what this test asks for'


def test_junk_falls_back_rather_than_erroring():
    """These are public URLs, so junk arrives -- from crawlers, from a truncated link, from a client bug.
    A 500 on `?range=abc` would be an error page where a board belongs."""
    for raw in ('abc', '', '-5', '0'):
        start, count = window_params(_get(range=raw, count=raw), 50)
        assert start >= 1, f'range={raw!r} produced a negative offset'
        assert 1 <= count <= MAX_COUNT, f'count={raw!r} escaped the clamp'
