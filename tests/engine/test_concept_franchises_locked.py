"""Tests for the Concept.franchises_locked guard.

A locked concept keeps its hand-curated Franchise/Collection links (and their
is_main flags) across an IGDB enrichment refresh, while the rest of enrichment
(companies, genres, themes, engines) still rebuilds normally.
"""
import pytest

from trophies.models import ConceptFranchise, ConceptGenre, Franchise, Genre
from trophies.services.igdb_service import IGDBService
from tests.factories import ConceptFactory

pytestmark = pytest.mark.django_db


def _seed_links(concept):
    fr = Franchise.objects.create(igdb_id=1, name='Saga', slug='saga', source_type='franchise')
    ConceptFranchise.objects.create(concept=concept, franchise=fr, is_main=True)
    ConceptGenre.objects.create(concept=concept, genre=Genre.objects.create(igdb_id=1, name='RPG', slug='rpg'))


def test_wipe_preserves_franchise_links_when_locked():
    concept = ConceptFactory(franchises_locked=True)
    _seed_links(concept)

    IGDBService._wipe_concept_enrichment(concept)

    # Franchise/collection links survive; everything else is still wiped.
    assert ConceptFranchise.objects.filter(concept=concept).count() == 1
    assert ConceptFranchise.objects.get(concept=concept).is_main is True
    assert ConceptGenre.objects.filter(concept=concept).count() == 0


def test_wipe_clears_franchise_links_when_unlocked():
    concept = ConceptFactory(franchises_locked=False)
    _seed_links(concept)

    IGDBService._wipe_concept_enrichment(concept)

    assert ConceptFranchise.objects.filter(concept=concept).count() == 0
    assert ConceptGenre.objects.filter(concept=concept).count() == 0


def test_absorb_inherits_franchises_lock():
    # A locked concept absorbed into an unlocked survivor passes its lock on, so
    # the franchise links it just migrated don't get wiped on the next refresh.
    survivor = ConceptFactory(franchises_locked=False)
    absorbed = ConceptFactory(franchises_locked=True)

    survivor.absorb(absorbed)

    survivor.refresh_from_db()
    assert survivor.franchises_locked is True
