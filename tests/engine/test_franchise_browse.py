"""Tests for the franchise/collection browse page (FranchiseListView).

The browse page surfaces every visible franchise plus a curated subset of
collections. The default view hides collections whose every game also has a
franchise-type link ("Resident Evil Main Series" is redundant with the
Resident Evil franchise card). When the user explicitly picks the Collection
chip (?type=collection), that orphan-concept rule is dropped so name-shared
pairs like "Spider-Man franchise" + "Spider-Man collection" stay reachable.
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


def _collection(igdb_id, name, slug):
    return Franchise.objects.create(
        igdb_id=igdb_id, name=name, slug=slug, source_type='collection',
    )


def _link(concept, franchise):
    return ConceptFranchise.objects.create(concept=concept, franchise=franchise)


def _spiderman_pair():
    """Two same-named entries with two shared concepts (no orphans).

    Every game is linked to BOTH the Spider-Man franchise AND the
    Spider-Man collection, so the collection has zero orphan concepts and
    would normally be suppressed.
    """
    franchise = _franchise(igdb_id=4001, name='Spider-Man', slug='spider-man')
    collection = _collection(igdb_id=4002, name='Spider-Man', slug='spider-man-collection')
    for i in range(2):
        concept = ConceptFactory()
        # Ensure version_count > 0 so it isn't filtered out by the
        # "at least one Game" gate.
        GameFactory(concept=concept)
        _link(concept, franchise)
        _link(concept, collection)
    return franchise, collection


def test_default_view_hides_orphanless_collection(client):
    """The 'Spider-Man collection' shouldn't appear in the default view
    because all its games already have the Spider-Man franchise."""
    franchise, collection = _spiderman_pair()

    resp = client.get(reverse('franchises_list'))

    ids = {obj.pk for obj in resp.context['object_list']}
    assert franchise.pk in ids
    assert collection.pk not in ids


def test_type_collection_filter_surfaces_orphanless_collection(client):
    """Picking the Collection chip explicitly drops the orphan rule so
    the same Spider-Man collection becomes reachable."""
    franchise, collection = _spiderman_pair()

    resp = client.get(reverse('franchises_list'), {'type': 'collection'})

    ids = {obj.pk for obj in resp.context['object_list']}
    assert collection.pk in ids
    # type=collection narrows source_type so the franchise must NOT appear.
    assert franchise.pk not in ids


def test_type_franchise_filter_excludes_collections(client):
    """Picking the Franchise chip never shows collections, regardless of
    the orphan rule."""
    franchise, collection = _spiderman_pair()

    resp = client.get(reverse('franchises_list'), {'type': 'franchise'})

    ids = {obj.pk for obj in resp.context['object_list']}
    assert franchise.pk in ids
    assert collection.pk not in ids


def test_junk_type_value_clamps_to_all(client):
    """A ?type=garbage URL should behave like the default 'All' view:
    queryset re-applies the orphan rule and the All chip renders selected."""
    franchise, collection = _spiderman_pair()

    resp = client.get(reverse('franchises_list'), {'type': 'banana'})

    assert resp.context['current_type'] == 'all'
    ids = {obj.pk for obj in resp.context['object_list']}
    assert franchise.pk in ids
    assert collection.pk not in ids  # orphan rule re-applied
