"""Tests for the franchise/series browse page (FranchiseListView).

The browse page surfaces every visible franchise (source_type='franchise')
and every visible series (source_type='collection' in the DB; "Series" in
all user-facing copy). The toolbar Type chip group lets users narrow to
one type at a time and defaults to 'franchise' so the first-page-load
view matches users' existing mental model.
"""

import pytest
from django.urls import reverse

from trophies.models import ConceptFranchise, Franchise
from tests.factories import ConceptFactory, GameFactory

pytestmark = pytest.mark.django_db


def _franchise(igdb_id, name, slug):
    return Franchise.objects.create(
        igdb_id=igdb_id, name=name, slug=slug, source_type='franchise',
    )


def _series(igdb_id, name, slug):
    # source_type='collection' is the DB value (IGDB's namespace); the user-
    # facing label is "Series".
    return Franchise.objects.create(
        igdb_id=igdb_id, name=name, slug=slug, source_type='collection',
    )


def _link(concept, franchise):
    return ConceptFranchise.objects.create(concept=concept, franchise=franchise)


def _spiderman_pair():
    """Two same-named entries with two shared concepts.

    Every game is linked to BOTH the Spider-Man franchise AND the
    Spider-Man series. Previously the series would have been hidden by
    the orphan-concept rule; the rule has been dropped so both must
    surface together when the user picks the All chip.
    """
    franchise = _franchise(igdb_id=4001, name='Spider-Man', slug='spider-man')
    series = _series(igdb_id=4002, name='Spider-Man', slug='spider-man-series')
    for _ in range(2):
        concept = ConceptFactory()
        # Ensure version_count > 0 so it isn't filtered out by the
        # "at least one Game" gate.
        GameFactory(concept=concept)
        _link(concept, franchise)
        _link(concept, series)
    return franchise, series


def test_default_view_shows_only_franchises(client):
    """First page load (no ?type=) defaults to the Franchise chip; series
    are filtered out unless the user flips to Series or All."""
    franchise, series = _spiderman_pair()

    resp = client.get(reverse('franchises_list'))

    assert resp.context['current_type'] == 'franchise'
    ids = {obj.pk for obj in resp.context['object_list']}
    assert franchise.pk in ids
    assert series.pk not in ids


def test_type_series_chip_shows_series_only(client):
    """type=series narrows to source_type='collection' rows and surfaces
    them regardless of whether the franchise sibling exists — no more
    orphan-rule hiding."""
    franchise, series = _spiderman_pair()

    resp = client.get(reverse('franchises_list'), {'type': 'series'})

    ids = {obj.pk for obj in resp.context['object_list']}
    assert series.pk in ids
    assert franchise.pk not in ids


def test_type_all_chip_shows_both(client):
    """type=all returns both rows together — the name-shared pair is no
    longer suppressed."""
    franchise, series = _spiderman_pair()

    resp = client.get(reverse('franchises_list'), {'type': 'all'})

    ids = {obj.pk for obj in resp.context['object_list']}
    assert franchise.pk in ids
    assert series.pk in ids


def test_junk_type_value_clamps_to_default(client):
    """A ?type=garbage URL should behave like the default Franchise view
    and the chip should render as selected."""
    franchise, series = _spiderman_pair()

    resp = client.get(reverse('franchises_list'), {'type': 'banana'})

    assert resp.context['current_type'] == 'franchise'
    ids = {obj.pk for obj in resp.context['object_list']}
    assert franchise.pk in ids
    assert series.pk not in ids


def test_legacy_collection_type_value_does_not_secretly_work(client):
    """The old ?type=collection URL (from the unmerged PR 3 iteration) is
    not in the allow-list any more, so it clamps to the default chip
    rather than surfacing as ?type=series. Confirms the URL rename took
    effect end-to-end."""
    franchise, series = _spiderman_pair()

    resp = client.get(reverse('franchises_list'), {'type': 'collection'})

    assert resp.context['current_type'] == 'franchise'
    ids = {obj.pk for obj in resp.context['object_list']}
    assert series.pk not in ids
