"""Tests for the Job Board spine: the Job seed (data migration 0247), the Contract
membership-unique invariant (one home Contract per Concept), and absorb() migration
of Contract links on a Concept merge."""
from collections import Counter

import pytest
from django.db import IntegrityError, transaction

from trophies.models import Job, Contract, ContractMembership, ContractBundle
from tests.factories import ConceptFactory

pytestmark = pytest.mark.django_db


# --- Job seed (migration 0247) ---

def test_jobs_seeded_five_disciplines_of_five():
    assert Job.objects.count() == 25
    by_discipline = Counter(Job.objects.values_list('discipline', flat=True))
    assert by_discipline == {'combat': 5, 'exploration': 5, 'mind': 5, 'heart': 5, 'finesse': 5}


def test_freelancer_is_the_only_fallback():
    assert list(Job.objects.filter(is_fallback=True).values_list('slug', flat=True)) == ['freelancer']


# --- membership-unique invariant (the "once per game" guarantee) ---

def test_concept_can_only_have_one_home_contract():
    concept = ConceptFactory()
    c1 = Contract.objects.create(name='Contract One', slug='contract-one')
    c2 = Contract.objects.create(name='Contract Two', slug='contract-two')
    ContractMembership.objects.create(contract=c1, concept=concept)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ContractMembership.objects.create(contract=c2, concept=concept)


# --- absorb() migration on a Concept merge ---

def test_absorb_moves_membership_when_survivor_has_none():
    survivor, absorbed = ConceptFactory(), ConceptFactory()
    contract = Contract.objects.create(name='C', slug='c')
    ContractMembership.objects.create(contract=contract, concept=absorbed)

    survivor.absorb(absorbed)

    assert ContractMembership.objects.get(concept=survivor).contract == contract
    assert not ContractMembership.objects.filter(concept=absorbed).exists()


def test_absorb_keeps_survivors_own_membership():
    survivor, absorbed = ConceptFactory(), ConceptFactory()
    c_keep = Contract.objects.create(name='Keep', slug='keep')
    c_drop = Contract.objects.create(name='Drop', slug='drop')
    ContractMembership.objects.create(contract=c_keep, concept=survivor)
    ContractMembership.objects.create(contract=c_drop, concept=absorbed)

    survivor.absorb(absorbed)

    # Survivor keeps its own home Contract; absorbed's link is untouched (it
    # cascade-deletes when the caller deletes `absorbed` after the merge).
    assert ContractMembership.objects.get(concept=survivor).contract == c_keep


def test_absorb_migrates_bundle_membership():
    survivor, absorbed = ConceptFactory(), ConceptFactory()
    bundle = ContractBundle.objects.create(
        contract=Contract.objects.create(name='C', slug='c'), label='collection',
    )
    bundle.concepts.add(absorbed)

    survivor.absorb(absorbed)

    assert bundle.concepts.filter(pk=survivor.pk).exists()
    assert not bundle.concepts.filter(pk=absorbed.pk).exists()
