"""Migration 0289: every month becomes unwatched again.

The recap was rebuilt from a slide deck into a ceremony -- new entrance, new stage, twenty re-authored
beats, a quiz payoff that never existed and an ending that hands over to the share card. A
`has_been_viewed` set against the old presentation answers a question nobody is asking.

Both surfaces reset together on purpose: the dashboard's recap module gates its share-card preview on the
same flag, so leaving it set there would have the dashboard quietly asserting "you have seen this" about
something nobody has seen.

Migration tests are slow and usually not worth writing. This one earns its place because the operation is
a blanket UPDATE across every hunter's rows and the failure mode of a wrong filter is silent -- either
nothing resets (no New flags anywhere, and no way to tell without checking prod) or something unrelated
gets written.
"""
import importlib

import pytest
from django.apps import apps as global_apps

from trophies.models import MonthlyRecap
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

# `import` cannot name a module starting with a digit; importlib is only doing a string lookup, so it can.
MIGRATION = importlib.import_module('trophies.migrations.0289_reset_recap_viewed_for_ceremony')


def _recap(profile, month, **over):
    fields = dict(profile=profile, year=2024, month=month, total_trophies_earned=5, platinums_earned=1)
    fields.update(over)
    return MonthlyRecap.objects.create(**fields)


def test_watched_months_become_unwatched():
    profile = ProfileFactory()
    watched = _recap(profile, 1, has_been_viewed=True)
    already = _recap(profile, 2, has_been_viewed=False)

    MIGRATION.reset_viewed(global_apps, None)

    watched.refresh_from_db()
    already.refresh_from_db()
    assert watched.has_been_viewed is False
    assert already.has_been_viewed is False


def test_it_resets_every_hunter_not_just_one():
    """A blanket reset. Scoping it to a profile by accident would leave most of the site marked watched."""
    a, b = ProfileFactory(), ProfileFactory()
    _recap(a, 3, has_been_viewed=True)
    _recap(b, 3, has_been_viewed=True)

    MIGRATION.reset_viewed(global_apps, None)

    assert not MonthlyRecap.objects.filter(has_been_viewed=True).exists()


def test_nothing_but_the_flag_is_touched():
    """`has_been_viewed` is a display signal. No recap CONTENT may change -- the whole point of a finalized
    recap is that it is a fixed historical snapshot."""
    profile = ProfileFactory()
    row = _recap(profile, 4, has_been_viewed=True, is_finalized=True,
                 total_trophies_earned=147, platinums_earned=3)

    MIGRATION.reset_viewed(global_apps, None)

    row.refresh_from_db()
    assert row.total_trophies_earned == 147
    assert row.platinums_earned == 3
    assert row.is_finalized is True, 'the reset un-finalized a recap, which would let it regenerate'


def test_running_it_twice_changes_nothing_the_second_time():
    """Migrations get replayed (a rebuilt test DB, a re-run deploy). It has to be safe to apply again."""
    profile = ProfileFactory()
    _recap(profile, 5, has_been_viewed=True)

    MIGRATION.reset_viewed(global_apps, None)
    first = list(MonthlyRecap.objects.values_list('id', 'has_been_viewed'))
    MIGRATION.reset_viewed(global_apps, None)

    assert list(MonthlyRecap.objects.values_list('id', 'has_been_viewed')) == first


def test_the_reverse_does_not_invent_history():
    """Which months each hunter had watched is gone the moment this runs -- it lives nowhere else. A
    reverse that marked everything watched would be worse than one that does nothing."""
    profile = ProfileFactory()
    _recap(profile, 6, has_been_viewed=False)

    MIGRATION.noop_reverse(global_apps, None)

    assert not MonthlyRecap.objects.filter(has_been_viewed=True).exists()
