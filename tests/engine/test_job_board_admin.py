"""Tests for the Job Board admin actions: Stage 'Convert to Contract' and Contract
'Suggest jobs'. Both lean on the shared job_detection service."""
import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from trophies.admin import StageAdmin, ContractAdmin
from trophies.models import (
    Concept, ConceptGenre, ConceptTheme, Contract, ContractMembership, Genre, Job, Stage, Theme,
)
from tests.factories import ConceptBundleFactory, ConceptFactory, StageFactory

pytestmark = pytest.mark.django_db


class _DummyMessages:
    """message_user() calls request._messages.add(...); swallow it (no session needed)."""
    def add(self, *args, **kwargs):
        pass


def _request():
    req = RequestFactory().post('/')
    req._messages = _DummyMessages()
    return req


def _rpg_fantasy(concept):
    ConceptGenre.objects.create(concept=concept, genre=Genre.objects.create(igdb_id=1, name='Role-playing (RPG)', slug='rpg'))
    ConceptTheme.objects.create(concept=concept, theme=Theme.objects.create(igdb_id=1, name='Fantasy', slug='fantasy'))


def test_convert_to_contract_copies_concepts_bundles_and_suggests_jobs():
    c1 = ConceptFactory(unified_title='RPG Game')
    _rpg_fantasy(c1)
    c2 = ConceptFactory(unified_title='Sidekick')
    stage = StageFactory(series_slug='s', stage_number=1, title='My Game')
    stage.concepts.add(c1, c2)
    ConceptBundleFactory(stage=stage, label='coll').concepts.add(c2)

    StageAdmin(Stage, AdminSite()).convert_to_contract(_request(), Stage.objects.filter(pk=stage.pk))

    contract = Contract.objects.get(name='My Game')
    assert set(contract.memberships.values_list('concept_id', flat=True)) == {c1.id, c2.id}
    assert contract.bundles.count() == 1
    assert set(contract.jobs.values_list('slug', flat=True)) == {'mage'}   # RPG + Fantasy -> Mage


def test_convert_skips_already_homed_concept():
    homed = ConceptFactory(unified_title='Homed')
    existing = Contract.objects.create(name='Existing', slug='existing')
    ContractMembership.objects.create(contract=existing, concept=homed)
    fresh = ConceptFactory(unified_title='Fresh')
    stage = StageFactory(series_slug='s2', stage_number=1, title='Stage Two')
    stage.concepts.add(homed, fresh)

    StageAdmin(Stage, AdminSite()).convert_to_contract(_request(), Stage.objects.filter(pk=stage.pk))

    new = Contract.objects.get(name='Stage Two')
    assert set(new.memberships.values_list('concept_id', flat=True)) == {fresh.id}  # homed skipped
    assert Concept.objects.get(pk=homed.pk).contract_membership.contract == existing  # still in its original


def test_suggest_jobs_replaces_existing():
    c = ConceptFactory()
    _rpg_fantasy(c)
    contract = Contract.objects.create(name='C', slug='c')
    ContractMembership.objects.create(contract=contract, concept=c)
    contract.jobs.set(Job.objects.filter(slug='gunslinger'))   # pre-existing wrong job

    ContractAdmin(Contract, AdminSite()).suggest_jobs(_request(), Contract.objects.filter(pk=contract.pk))

    assert set(contract.jobs.values_list('slug', flat=True)) == {'mage'}   # replaced, not added
