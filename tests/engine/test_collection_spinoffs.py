"""Tests for collection (Series) spin-off handling.

IGDB types a game's membership in a series as "Member" or "Spin-off" (e.g. Agents of
Mayhem is a Member of the Agents of Mayhem series but a Spin-off of Saints Row). That
signal lives only on /collection_memberships, so we fetch it during enrichment and store
it on ConceptFranchise.is_spinoff (collection links only). Spin-offs are then suppressed
from the Series' game list and from collection badge stage coverage.

Covers: the membership-fetch parser, ingestion wiring, the backfill command, absorb
preservation, and the Series-detail display exclusion.
"""
import pytest
from django.core.management import call_command
from django.urls import reverse

from trophies.models import ConceptFranchise, Franchise
from trophies.services.badge_coverage_service import audit_badge_coverage
from trophies.services.igdb_service import IGDBService
from tests.factories import (
    BadgeFactory, ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory,
)

pytestmark = pytest.mark.django_db


def _collection(igdb_id, name, slug):
    return Franchise.objects.create(
        igdb_id=igdb_id, name=name, slug=slug, source_type='collection',
    )


# --- fetch_collection_memberships parser --------------------------------------


def test_fetch_memberships_maps_game_collection_to_spinoff(monkeypatch):
    """type 2 -> True (Spin-off), anything else -> False (Member)."""
    rows = [
        {'id': 1, 'game': 19440, 'collection': 2836, 'type': 1},  # Member
        {'id': 2, 'game': 19440, 'collection': 197, 'type': 2},   # Spin-off
    ]
    monkeypatch.setattr(IGDBService, '_request', classmethod(lambda cls, ep, q: rows))

    out = IGDBService.fetch_collection_memberships([19440])

    assert out == {19440: {2836: False, 197: True}}


def test_fetch_memberships_empty_for_no_ids():
    assert IGDBService.fetch_collection_memberships([]) == {}


# --- ingestion (_create_concept_franchises) -----------------------------------


def test_ingestion_sets_spinoff_on_collection_links(monkeypatch):
    monkeypatch.setattr(
        IGDBService, 'fetch_collection_memberships',
        staticmethod(lambda ids: {19440: {2836: False, 197: True}}),
    )
    concept = ConceptFactory()
    igdb_data = {
        'id': 19440,
        'franchises': [{'id': 50, 'name': 'Volition Games'}],
        'collections': [
            {'id': 2836, 'name': 'Agents of Mayhem'},
            {'id': 197, 'name': 'Saints Row'},
        ],
    }

    IGDBService._create_concept_franchises(concept, igdb_data)

    def _link(igdb_id, source_type):
        return ConceptFranchise.objects.get(
            concept=concept, franchise__igdb_id=igdb_id, franchise__source_type=source_type,
        )
    assert _link(2836, 'collection').is_spinoff is False   # Member of its own series
    assert _link(197, 'collection').is_spinoff is True      # Spin-off of Saints Row
    assert _link(50, 'franchise').is_spinoff is False        # franchises never spin-off


def test_ingestion_skips_membership_fetch_without_collections(monkeypatch):
    """No collections on the payload -> no /collection_memberships call at all."""
    called = []
    monkeypatch.setattr(
        IGDBService, 'fetch_collection_memberships',
        staticmethod(lambda ids: called.append(ids) or {}),
    )
    concept = ConceptFactory()

    IGDBService._create_concept_franchises(concept, {'id': 1, 'franchises': [{'id': 9, 'name': 'X'}]})

    assert called == []  # never queried memberships


def test_ingestion_skips_membership_fetch_when_disabled(monkeypatch):
    """fetch_memberships=False (cache-based rebuild path) makes no IGDB call even when the
    game has collections; the link is created with is_spinoff defaulting to False."""
    called = []
    monkeypatch.setattr(
        IGDBService, 'fetch_collection_memberships',
        staticmethod(lambda ids: called.append(ids) or {19440: {197: True}}),
    )
    concept = ConceptFactory()

    IGDBService._create_concept_franchises(
        concept, {'id': 19440, 'collections': [{'id': 197, 'name': 'Saints Row'}]},
        fetch_memberships=False,
    )

    assert called == []  # no membership query despite the collection
    link = ConceptFranchise.objects.get(concept=concept, franchise__igdb_id=197)
    assert link.is_spinoff is False


def test_writer_does_not_clobber_admin_is_excluded(monkeypatch):
    """A link the admin has flagged is_excluded=True must survive a re-run of the
    writer. Otherwise admin curation would be wiped every enrichment refresh."""
    monkeypatch.setattr(
        IGDBService, 'fetch_collection_memberships',
        staticmethod(lambda ids: {}),
    )
    concept = ConceptFactory()
    coll = _collection(197, 'Saints Row', 'saints-row')
    # Admin has marked this link as excluded.
    ConceptFranchise.objects.create(
        concept=concept, franchise=coll, is_excluded=True,
    )

    # Re-run the writer with the same IGDB data.
    IGDBService._create_concept_franchises(
        concept, {'id': 19440, 'collections': [{'id': 197, 'name': 'Saints Row'}]},
    )

    # is_excluded must still be True; writer is forbidden from touching it.
    assert ConceptFranchise.objects.get(concept=concept, franchise=coll).is_excluded is True


def test_ingestion_corrects_stale_spinoff_on_refresh(monkeypatch):
    """A link previously stored as spin-off that IGDB now reports as a normal member
    gets corrected (and vice-versa) on re-enrichment."""
    monkeypatch.setattr(
        IGDBService, 'fetch_collection_memberships',
        staticmethod(lambda ids: {19440: {197: False}}),  # now a normal member
    )
    concept = ConceptFactory()
    coll = _collection(197, 'Saints Row', 'saints-row')
    ConceptFranchise.objects.create(concept=concept, franchise=coll, is_spinoff=True)  # stale

    IGDBService._create_concept_franchises(
        concept, {'id': 19440, 'collections': [{'id': 197, 'name': 'Saints Row'}]},
    )

    assert ConceptFranchise.objects.get(concept=concept, franchise=coll).is_spinoff is False


# --- backfill command ---------------------------------------------------------


def test_backfill_command_flags_spinoffs(monkeypatch):
    monkeypatch.setattr(
        IGDBService, 'fetch_collection_memberships',
        staticmethod(lambda ids: {19440: {197: True}}),
    )
    concept = ConceptFactory()
    IGDBMatchFactory(concept=concept, igdb_id=19440)
    coll = _collection(197, 'Saints Row', 'saints-row')
    cf = ConceptFranchise.objects.create(concept=concept, franchise=coll, is_spinoff=False)

    call_command('backfill_collection_spinoffs')

    cf.refresh_from_db()
    assert cf.is_spinoff is True


def test_backfill_dry_run_writes_nothing(monkeypatch):
    monkeypatch.setattr(
        IGDBService, 'fetch_collection_memberships',
        staticmethod(lambda ids: {19440: {197: True}}),
    )
    concept = ConceptFactory()
    IGDBMatchFactory(concept=concept, igdb_id=19440)
    coll = _collection(197, 'Saints Row', 'saints-row')
    cf = ConceptFranchise.objects.create(concept=concept, franchise=coll, is_spinoff=False)

    call_command('backfill_collection_spinoffs', '--dry-run')

    cf.refresh_from_db()
    assert cf.is_spinoff is False  # untouched


# --- absorb() preserves the flag ----------------------------------------------


def test_absorb_preserves_spinoff_flag():
    """absorb re-points the ConceptFranchise row intact, so is_spinoff travels with it
    (inherit_match path: survivor has no match, absorbed does)."""
    survivor = ConceptFactory()
    absorbed = ConceptFactory()
    IGDBMatchFactory(concept=absorbed)  # gives `other` a match -> links migrate
    coll = _collection(197, 'Saints Row', 'saints-row')
    ConceptFranchise.objects.create(concept=absorbed, franchise=coll, is_spinoff=True)

    survivor.absorb(absorbed)

    assert ConceptFranchise.objects.get(concept=survivor, franchise=coll).is_spinoff is True


# --- cross-collection: Member of A, Spin-off of B (the join-aliasing trap) -----


def test_member_of_one_series_spinoff_of_another_is_scoped_correctly():
    """One concept is a normal Member of series A and a Spin-off of series B (the Agents
    of Mayhem shape). The spin-off suppression must be SCOPED to series B: a collection-A
    badge still expects the game; a collection-B badge does not. Guards the M2M two-join
    trap (a single is_spinoff=True link must not hide the game from its OTHER series)."""
    coll_a = _collection(2836, 'Agents of Mayhem', 'agents-of-mayhem')
    coll_b = _collection(197, 'Saints Row', 'saints-row')
    concept = ConceptFactory(unified_title='Agents of Mayhem')
    GameFactory(concept=concept)
    ConceptFranchise.objects.create(concept=concept, franchise=coll_a, is_spinoff=False)  # Member of A
    ConceptFranchise.objects.create(concept=concept, franchise=coll_b, is_spinoff=True)   # Spin-off of B

    badge_a = BadgeFactory(series_slug='ser-a', tier=1)
    badge_a.collection = coll_a
    badge_a.save()
    badge_b = BadgeFactory(series_slug='ser-b', tier=1)
    badge_b.collection = coll_b
    badge_b.save()

    findings = {f['collection']: f for f in audit_badge_coverage()}

    # Series A (where it's a real member) flags the uncovered game...
    assert coll_a in findings
    assert concept.id in [c.id for c in findings[coll_a]['missing']]
    # ...but series B (where it's a spin-off) does NOT expect it, so no finding for B.
    assert coll_b not in findings


# --- Series detail display exclusion ------------------------------------------


def test_series_detail_hides_spinoff_games(client):
    coll = _collection(197, 'Saints Row', 'saints-row')
    member = ConceptFactory(unified_title='Saints Row IV')
    IGDBMatchFactory(concept=member, igdb_id=1001)
    GameFactory(concept=member, title_name='Saints Row IV')
    spinoff = ConceptFactory(unified_title='Agents of Mayhem')
    IGDBMatchFactory(concept=spinoff, igdb_id=1002)
    GameFactory(concept=spinoff, title_name='Agents of Mayhem')
    ConceptFranchise.objects.create(concept=member, franchise=coll, is_spinoff=False)
    ConceptFranchise.objects.create(concept=spinoff, franchise=coll, is_spinoff=True)

    resp = client.get(reverse('franchise_detail', kwargs={'slug': coll.slug}))

    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'Saints Row IV' in body       # the real member shows
    assert 'Agents of Mayhem' not in body  # the spin-off is suppressed
