"""Tests for the Job Board spine: the Job seed (data migration) and absorb() migration of
Contract BUNDLE links on a Concept merge. (Contract HOME membership is now DERIVED from the
concept's IGDB id -- see test_contract_xp / test_job_board_admin -- so there is no
ContractMembership to migrate in absorb.)"""
from collections import Counter

import pytest

from trophies.models import Job, Contract, ContractBundle
from tests.factories import ConceptFactory

pytestmark = pytest.mark.django_db


# --- Job seed ---

def test_jobs_seeded_five_disciplines_of_five():
    assert Job.objects.count() == 25
    by_discipline = Counter(Job.objects.values_list('discipline', flat=True))
    assert by_discipline == {'combat': 5, 'exploration': 5, 'mind': 5, 'heart': 5, 'finesse': 5}


def test_freelancer_is_the_only_fallback():
    assert list(Job.objects.filter(is_fallback=True).values_list('slug', flat=True)) == ['freelancer']


# --- absorb() migrates ContractBundle (episodic satisfier) links ---

def test_absorb_migrates_bundle_membership():
    survivor, absorbed = ConceptFactory(), ConceptFactory()
    bundle = ContractBundle.objects.create(
        contract=Contract.objects.create(name='C', slug='c'), label='episodic',
    )
    bundle.concepts.add(absorbed)

    survivor.absorb(absorbed)

    assert bundle.concepts.filter(pk=survivor.pk).exists()
    assert not bundle.concepts.filter(pk=absorbed.pk).exists()


def test_absorb_bundle_dedups_when_survivor_already_satisfier():
    # Both already in the bundle -> survivor stays once, absorbed removed (no dup).
    survivor, absorbed = ConceptFactory(), ConceptFactory()
    bundle = ContractBundle.objects.create(
        contract=Contract.objects.create(name='C', slug='c'), label='episodic',
    )
    bundle.concepts.add(survivor, absorbed)

    survivor.absorb(absorbed)

    assert list(bundle.concepts.values_list('pk', flat=True)) == [survivor.pk]


def test_absorb_propagates_anchor_when_inheriting_match():
    """Audit Finding C: absorbing an ANCHORED concept into an un-anchored survivor must
    inherit BOTH the igdb_match and the anchor flag, or the survivor silently drops from its
    Contract (membership is derived from anchor + match)."""
    from django.utils import timezone
    from tests.factories import IGDBMatchFactory
    survivor = ConceptFactory()   # un-anchored, no match
    absorbed = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=absorbed, igdb_id=77123)

    survivor.absorb(absorbed)

    survivor.refresh_from_db()
    assert survivor.anchor_migration_completed_at is not None   # anchor propagated with the match
    assert survivor.igdb_match.igdb_id == 77123
