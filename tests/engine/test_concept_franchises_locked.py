"""Tests for the Concept.franchises_locked guard.

A locked concept keeps its hand-curated Franchise/Collection links (and their
is_main flags) across an IGDB enrichment refresh, while the rest of enrichment
(companies, genres, themes, engines) still rebuilds normally.
"""
import pytest
from django.core.management import call_command

from trophies.models import ConceptFranchise, ConceptGenre, Franchise, Genre
from trophies.services.igdb_service import IGDBService
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory

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


def test_absorb_inherits_lock_when_links_migrate():
    # inherit_match=True (survivor has no match): other's curated links migrate, so
    # the survivor inherits the lock to keep protecting them.
    survivor = ConceptFactory(franchises_locked=False)
    absorbed = ConceptFactory(franchises_locked=True)
    IGDBMatchFactory(concept=absorbed)  # gives `other` a match -> inherit_match True

    survivor.absorb(absorbed)

    survivor.refresh_from_db()
    assert survivor.franchises_locked is True


def test_absorb_does_not_inherit_lock_on_reanchor():
    # inherit_match=False (survivor keeps its OWN match): other's links are dropped,
    # so the survivor must NOT lock its own auto-generated links.
    survivor = ConceptFactory(franchises_locked=False)
    IGDBMatchFactory(concept=survivor)
    absorbed = ConceptFactory(franchises_locked=True)
    IGDBMatchFactory(concept=absorbed)

    survivor.absorb(absorbed)

    survivor.refresh_from_db()
    assert survivor.franchises_locked is False


def test_rebuild_concept_enrichment_preserves_locked_links():
    # BLOCKER guard: the catalog-wide wipe + rebuild must NOT strip a locked
    # concept's curated franchise links (it skips both the wipe and the rebuild).
    concept = ConceptFactory(franchises_locked=True)
    GameFactory(concept=concept)
    fr = Franchise.objects.create(igdb_id=7, name='Saga', slug='saga', source_type='franchise')
    ConceptFranchise.objects.create(concept=concept, franchise=fr, is_main=True)
    IGDBMatchFactory(concept=concept, status='auto_accepted', raw_response={'name': 'X'})

    call_command('rebuild_concept_enrichment')   # runs by default; --dry-run is opt-out

    link = ConceptFranchise.objects.filter(concept=concept, franchise=fr)
    assert link.count() == 1 and link.first().is_main is True


def test_rebuild_franchises_from_cache_wipe_preserves_locked():
    # BLOCKER guard: --wipe must keep a locked concept's link AND the parent
    # Franchise it references (Franchise -> ConceptFranchise is CASCADE).
    locked = ConceptFactory(franchises_locked=True)
    fr = Franchise.objects.create(igdb_id=8, name='Saga2', slug='saga2', source_type='franchise')
    ConceptFranchise.objects.create(concept=locked, franchise=fr, is_main=True)
    IGDBMatchFactory(concept=locked, status='auto_accepted', raw_response={'name': 'X'})

    call_command('rebuild_franchises_from_cache', '--wipe')

    assert ConceptFranchise.objects.filter(concept=locked, franchise=fr, is_main=True).exists()
    assert Franchise.objects.filter(pk=fr.pk).exists()   # parent survived the cascade-wipe
