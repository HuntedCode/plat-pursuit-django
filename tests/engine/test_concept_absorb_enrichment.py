"""Spine tests for the IGDB-enrichment half of Concept.absorb().

IGDB enrichment (the developer/genre/theme/engine/franchise rows + the IGDBMatch)
is a deterministic projection of a concept's IGDBMatch, NOT independent data. So
it must travel WITH the match:

- If the survivor has NO match of its own, it inherits the doomed concept's match
  AND its enrichment rows (merging duplicates).
- If the survivor KEEPS its own match (the re-anchor / reassignment case), the
  doomed concept's enrichment describes a DIFFERENT IGDB game and must be DROPPED
  (it cascade-deletes with the doomed concept), never merged in.

That second rule is the "re-anchor bug" guard: merging enrichment unconditionally
once left survivors showing both matches' developers/genres stacked together (see
CLAUDE.md). `inherit_match` gates the whole enrichment migration on it.
"""

import pytest

from trophies.models import ConceptCompany, ConceptGenre, IGDBMatch
from tests.factories import (
    CompanyFactory,
    ConceptCompanyFactory,
    ConceptFactory,
    ConceptGenreFactory,
    GenreFactory,
    IGDBMatchFactory,
)

pytestmark = pytest.mark.django_db


def test_inherits_match_and_enrichment_when_survivor_has_none():
    """Survivor has no IGDB match → it inherits the doomed concept's match and
    enrichment rows."""
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    match = IGDBMatchFactory(concept=doomed)
    cc = ConceptCompanyFactory(concept=doomed)
    cg = ConceptGenreFactory(concept=doomed)

    survivor.absorb(doomed)

    assert IGDBMatch.objects.get(pk=match.pk).concept_id == survivor.id
    cc.refresh_from_db()
    cg.refresh_from_db()
    assert cc.concept_id == survivor.id
    assert cg.concept_id == survivor.id


def test_drops_doomed_enrichment_when_survivor_keeps_its_own_match():
    """Re-anchor guard: survivor keeps its own match, so the doomed concept's
    match + enrichment are DROPPED (cascade away), not stacked onto the survivor."""
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    survivor_match = IGDBMatchFactory(concept=survivor)
    doomed_match = IGDBMatchFactory(concept=doomed)
    doomed_company = ConceptCompanyFactory(concept=doomed)
    doomed_genre = ConceptGenreFactory(concept=doomed)

    survivor.absorb(doomed)
    doomed.delete()  # the doomed match + its enrichment cascade away here

    # survivor's own match is untouched
    assert IGDBMatch.objects.get(concept=survivor).pk == survivor_match.pk
    # the doomed match is gone, NOT re-pointed onto the survivor
    assert not IGDBMatch.objects.filter(pk=doomed_match.pk).exists()
    # survivor did NOT absorb the doomed concept's developer/genre
    assert not survivor.concept_companies.filter(
        company_id=doomed_company.company_id
    ).exists()
    assert not survivor.concept_genres.filter(genre_id=doomed_genre.genre_id).exists()


def test_inherit_merges_company_roles_on_duplicate():
    """When inheriting, a company present on both concepts has its role flags
    OR-merged rather than duplicated."""
    survivor = ConceptFactory()  # no match → will inherit
    doomed = ConceptFactory()
    IGDBMatchFactory(concept=doomed)
    company = CompanyFactory()
    # survivor knows this company as developer; doomed knows it as publisher
    ConceptCompanyFactory(
        concept=survivor, company=company, is_developer=True, is_publisher=False
    )
    ConceptCompanyFactory(
        concept=doomed, company=company, is_developer=False, is_publisher=True
    )

    survivor.absorb(doomed)

    merged = ConceptCompany.objects.get(concept=survivor, company=company)
    assert merged.is_developer is True
    assert merged.is_publisher is True
    # no duplicate row created for the survivor+company pair
    assert ConceptCompany.objects.filter(concept=survivor, company=company).count() == 1


def test_inherit_dedups_genres():
    """When inheriting, a genre on both concepts is not duplicated; unique ones move."""
    survivor = ConceptFactory()  # no match → will inherit
    doomed = ConceptFactory()
    IGDBMatchFactory(concept=doomed)
    shared_genre = GenreFactory()
    doomed_only_genre = GenreFactory()
    ConceptGenreFactory(concept=survivor, genre=shared_genre)
    ConceptGenreFactory(concept=doomed, genre=shared_genre)
    ConceptGenreFactory(concept=doomed, genre=doomed_only_genre)

    survivor.absorb(doomed)

    genre_ids = set(survivor.concept_genres.values_list("genre_id", flat=True))
    assert genre_ids == {shared_genre.id, doomed_only_genre.id}
    assert ConceptGenre.objects.filter(
        concept=survivor, genre=shared_genre
    ).count() == 1
