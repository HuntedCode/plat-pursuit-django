"""Tests for the Job Board admin: Stage 'Convert to Contract' (igdb-keyed, anchored-gated)
and Contract 'Suggest jobs'. Both lean on the shared job_detection service."""
import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.utils import timezone

from trophies.admin import StageAdmin, ContractAdmin
from trophies.models import (
    Concept, ConceptGenre, ConceptTheme, Contract, Genre, Job, Stage, Theme,
)
from tests.factories import ConceptBundleFactory, ConceptFactory, IGDBMatchFactory, StageFactory

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


def _anchored(title, igdb_id):
    """An ANCHORED Concept keyed on a raw igdb_id (the contract-eligible state)."""
    c = ConceptFactory(unified_title=title, anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=c, igdb_id=igdb_id)
    return c


def _convert(stage):
    StageAdmin(Stage, AdminSite()).convert_to_contract(_request(), Stage.objects.filter(pk=stage.pk))


def test_convert_keys_contract_on_igdb_with_concept_name_and_jobs():
    c1 = _anchored('RPG Game', 5001)
    _rpg_fantasy(c1)
    stage = StageFactory(series_slug='s', stage_number=1, title='Badge Stage Title')
    stage.concepts.add(c1)

    _convert(stage)

    contract = Contract.objects.get(igdb_id=5001)
    assert contract.name == 'RPG Game'   # name = concept's IGDB-canonical title, NOT the stage title
    assert set(contract.jobs.values_list('slug', flat=True)) == {'mage'}   # RPG + Fantasy -> Mage


def test_convert_two_concepts_sharing_igdb_id_make_one_contract():
    a = _anchored('Game PS4', 5002)
    b = _anchored('Game PS5', 5002)   # same raw igdb_id
    stage = StageFactory(series_slug='s2', stage_number=1)
    stage.concepts.add(a, b)

    _convert(stage)

    assert Contract.objects.filter(igdb_id=5002).count() == 1
    assert set(Contract.objects.get(igdb_id=5002).member_concept_ids()) == {a.id, b.id}


def test_convert_skips_existing_igdb_id():
    Contract.objects.create(name='Already', slug='already', igdb_id=5003)
    stage = StageFactory(series_slug='s3', stage_number=1)
    stage.concepts.add(_anchored('Dup Game', 5003))

    _convert(stage)

    assert Contract.objects.filter(igdb_id=5003).count() == 1   # not duplicated


def test_convert_skips_unanchored_concept():
    stage = StageFactory(series_slug='s4', stage_number=1)
    stage.concepts.add(ConceptFactory(unified_title='Unanchored'))   # no anchor, no match

    _convert(stage)

    assert Contract.objects.count() == 0   # nothing created for an un-anchored concept


def test_convert_rerun_creates_no_duplicate():
    stage = StageFactory(series_slug='s5', stage_number=1)
    stage.concepts.add(_anchored('Rerun Game', 5005))

    _convert(stage)
    _convert(stage)   # re-run -> igdb id already has a contract

    assert Contract.objects.filter(igdb_id=5005).count() == 1


def test_convert_episodic_bundle_makes_null_igdb_contract():
    ep = ConceptFactory(unified_title='Episode 1')
    stage = StageFactory(series_slug='ep', stage_number=1, title='Episodic Season')
    ConceptBundleFactory(stage=stage, label='season').concepts.add(ep)

    _convert(stage)

    contract = Contract.objects.get(name='Episodic Season')
    assert contract.igdb_id is None
    assert contract.bundles.count() == 1


def test_suggest_jobs_replaces_existing():
    c = _anchored('Suggest Game', 5006)
    _rpg_fantasy(c)
    contract = Contract.objects.create(name='C', slug='c', igdb_id=5006)
    contract.jobs.set(Job.objects.filter(slug='gunslinger'))   # pre-existing wrong job

    ContractAdmin(Contract, AdminSite()).suggest_jobs(_request(), Contract.objects.filter(pk=contract.pk))

    assert set(contract.jobs.values_list('slug', flat=True)) == {'mage'}   # replaced, not added


def test_contract_live_toggle_actions():
    live = Contract.objects.create(name='L', slug='l', igdb_id=5007, is_live=True)
    dark = Contract.objects.create(name='D', slug='d', igdb_id=5008, is_live=False)
    admin = ContractAdmin(Contract, AdminSite())

    admin.make_not_live(_request(), Contract.objects.filter(pk=live.pk))
    admin.make_live(_request(), Contract.objects.filter(pk=dark.pk))

    live.refresh_from_db(); dark.refresh_from_db()
    assert live.is_live is False and dark.is_live is True
