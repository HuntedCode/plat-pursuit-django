"""Tests for the Job Board admin actions: Stage 'Convert to Contract' and Contract
'Suggest jobs'. Both lean on the shared job_detection service."""
import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from trophies.admin import StageAdmin, ContractAdmin
from trophies.models import (
    Concept, ConceptGenre, ConceptTheme, Contract, ContractBundle, ContractMembership,
    Genre, Job, Stage, Theme,
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


# --- contract_satisfier_only: multi-game lists become bundles, never members ---

def test_convert_flagged_concept_becomes_bundle_not_member():
    member = ConceptFactory(unified_title='Uncharted 4')
    coll = ConceptFactory(unified_title='Legacy of Thieves Collection', contract_satisfier_only=True)
    stage = StageFactory(series_slug='u4', stage_number=1, title='Uncharted 4')
    stage.concepts.add(member, coll)

    StageAdmin(Stage, AdminSite()).convert_to_contract(_request(), Stage.objects.filter(pk=stage.pk))

    contract = Contract.objects.get(name='Uncharted 4')
    assert set(contract.memberships.values_list('concept_id', flat=True)) == {member.id}   # coll NOT homed
    bundle_concept_ids = set(
        ContractBundle.objects.filter(contract=contract).values_list('concepts__id', flat=True)
    )
    assert coll.id in bundle_concept_ids   # coll rides along as a satisfier bundle


def test_convert_skips_stage_with_no_new_member():
    # All concepts already homed (or satisfier-only) -> no empty draft is created.
    homed = ConceptFactory(unified_title='Homed')
    ContractMembership.objects.create(
        contract=Contract.objects.create(name='Existing', slug='existing-empty'), concept=homed,
    )
    coll = ConceptFactory(unified_title='Coll Only', contract_satisfier_only=True)
    stage = StageFactory(series_slug='empty', stage_number=1, title='Empty Stage')
    stage.concepts.add(homed, coll)

    StageAdmin(Stage, AdminSite()).convert_to_contract(_request(), Stage.objects.filter(pk=stage.pk))

    assert not Contract.objects.filter(name='Empty Stage').exists()


def test_convert_rerun_creates_no_duplicate():
    fresh = ConceptFactory(unified_title='Fresh Game')
    stage = StageFactory(series_slug='dup', stage_number=1, title='Dup Stage')
    stage.concepts.add(fresh)
    admin = StageAdmin(Stage, AdminSite())

    admin.convert_to_contract(_request(), Stage.objects.filter(pk=stage.pk))
    admin.convert_to_contract(_request(), Stage.objects.filter(pk=stage.pk))   # re-run

    assert Contract.objects.filter(name='Dup Stage').count() == 1   # member already homed -> no dup


def test_membership_form_rejects_flagged_concept():
    from trophies.admin import ContractMembershipForm
    contract = Contract.objects.create(name='C', slug='c-form')
    coll = ConceptFactory(unified_title='Coll', contract_satisfier_only=True)
    form = ContractMembershipForm(data={'contract': contract.id, 'concept': coll.id})
    assert not form.is_valid()
    assert 'concept' in form.errors


# --- Contract admin: live-toggle actions + Concept flag actions ---

def test_contract_live_toggle_actions():
    live = Contract.objects.create(name='L', slug='l', is_live=True)
    dark = Contract.objects.create(name='D', slug='d', is_live=False)
    admin = ContractAdmin(Contract, AdminSite())

    admin.make_not_live(_request(), Contract.objects.filter(pk=live.pk))
    admin.make_live(_request(), Contract.objects.filter(pk=dark.pk))

    live.refresh_from_db(); dark.refresh_from_db()
    assert live.is_live is False and dark.is_live is True


def test_concept_satisfier_flag_actions():
    from trophies.admin import ConceptAdmin
    c = ConceptFactory(unified_title='X')
    admin = ConceptAdmin(Concept, AdminSite())

    admin.mark_contract_satisfier(_request(), Concept.objects.filter(pk=c.pk))
    assert Concept.objects.get(pk=c.pk).contract_satisfier_only is True
    admin.unmark_contract_satisfier(_request(), Concept.objects.filter(pk=c.pk))
    assert Concept.objects.get(pk=c.pk).contract_satisfier_only is False


def test_list_multi_stage_concepts_command():
    from io import StringIO
    from django.core.management import call_command
    multi = ConceptFactory(unified_title='Multi Stage Concept')
    single = ConceptFactory(unified_title='Single Stage Concept')
    StageFactory(series_slug='a', stage_number=1).concepts.add(multi)
    StageFactory(series_slug='b', stage_number=1).concepts.add(multi)   # 2 stages
    StageFactory(series_slug='c', stage_number=1).concepts.add(single)  # 1 stage

    out = StringIO()
    call_command('list_multi_stage_concepts', stdout=out)
    output = out.getvalue()
    assert 'Multi Stage Concept' in output
    assert 'Single Stage Concept' not in output   # only 2+ stage concepts are listed


def test_list_multi_stage_concepts_same_badge_flag():
    from io import StringIO
    from django.core.management import call_command
    same = ConceptFactory(unified_title='Same Badge Concept')
    cross = ConceptFactory(unified_title='Cross Badge Concept')
    StageFactory(series_slug='unc', stage_number=1).concepts.add(same)
    StageFactory(series_slug='unc', stage_number=2).concepts.add(same)     # 2 stages, SAME badge
    StageFactory(series_slug='badge-a', stage_number=1).concepts.add(cross)
    StageFactory(series_slug='badge-b', stage_number=1).concepts.add(cross)  # 2 stages, DIFFERENT badges

    out = StringIO()
    call_command('list_multi_stage_concepts', '--same-badge', stdout=out)
    output = out.getvalue()
    assert 'Same Badge Concept' in output
    assert 'Cross Badge Concept' not in output   # different badges -> excluded by --same-badge
