"""Tests for Badge.assign_next_set_numbers (the admin 'assign next 4' logic).

Pure model logic (extracted from the admin action) so it's testable without the
admin/request/messages machinery. Set numbers are admin-assigned edition marks
engraved on the Frame; the action stamps the next contiguous block per series.
"""

import pytest

from trophies.models import Badge
from tests.factories import BadgeFactory

pytestmark = pytest.mark.django_db


def _series(slug, tiers=(1, 2, 3, 4)):
    return [BadgeFactory(series_slug=slug, tier=t) for t in tiers]


def _nums(slug):
    return sorted(
        n for n in Badge.objects.filter(series_slug=slug).values_list("set_number", flat=True)
        if n is not None
    )


def test_happy_path_stamps_four_in_tier_order():
    _series("rs-a")

    result = Badge.assign_next_set_numbers(["rs-a"])

    assert result["assigned"] == ["rs-a"]
    assert _nums("rs-a") == [1, 2, 3, 4]
    assert Badge.objects.get(series_slug="rs-a", tier=1).set_number == 1
    assert Badge.objects.get(series_slug="rs-a", tier=4).set_number == 4


def test_multiple_series_get_consecutive_non_overlapping_blocks():
    _series("rs-a")
    _series("rs-b")

    Badge.assign_next_set_numbers(["rs-a", "rs-b"])

    assert _nums("rs-a") == [1, 2, 3, 4]
    assert _nums("rs-b") == [5, 6, 7, 8]


def test_continues_from_existing_max():
    _series("rs-a")
    Badge.assign_next_set_numbers(["rs-a"])  # 1-4
    _series("rs-b")

    Badge.assign_next_set_numbers(["rs-b"])  # should continue at 5

    assert _nums("rs-b") == [5, 6, 7, 8]


def test_skips_series_without_exactly_four_tiers():
    _series("rs-c", tiers=(1, 2, 3))  # only 3 tiers

    result = Badge.assign_next_set_numbers(["rs-c"])

    assert result["invalid_tiers"] == ["rs-c"]
    assert result["assigned"] == []
    assert _nums("rs-c") == []  # untouched


def test_skips_already_numbered_series():
    badges = _series("rs-d")
    Badge.objects.filter(pk=badges[0].pk).update(set_number=99)

    result = Badge.assign_next_set_numbers(["rs-d"])

    assert result["already_numbered"] == ["rs-d"]
    assert result["assigned"] == []


def test_dedups_duplicate_slugs_in_selection():
    _series("rs-e")

    # Selecting several tiers of one series must assign the block only once.
    Badge.assign_next_set_numbers(["rs-e", "rs-e", "rs-e", "rs-e"])

    assert _nums("rs-e") == [1, 2, 3, 4]
