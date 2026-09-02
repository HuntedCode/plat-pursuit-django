"""Regression coverage for the `process_contracts` command after the igdb-keyed Contract rework.

The command used to `.prefetch_related('memberships', ...)` -- but ContractMembership was removed
and members are now igdb-derived, so evaluating that queryset crashed with
`AttributeError: Cannot find 'memberships' on Contract object`. These pin that the command loads
its live contracts and resolves members via `member_concept_ids()` without touching a stored
membership relation.
"""
import itertools
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from trophies.models import Contract
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory

pytestmark = pytest.mark.django_db

_igdb_seq = itertools.count(90001)


def _live_contract_with_member():
    """A live Contract keyed on an igdb id + one anchored, trusted-matched member concept/game."""
    igdb_id = next(_igdb_seq)
    contract = Contract.objects.create(name='C', slug=f'c-{igdb_id}', is_live=True, igdb_id=igdb_id)
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=igdb_id)   # factory default status = auto_accepted
    GameFactory(concept=concept)
    return contract


def _run(*args):
    call_command('process_contracts', *args, stdout=StringIO(), stderr=StringIO())


def test_process_contracts_all_runs_without_memberships_prefetch():
    ProfileFactory()
    _live_contract_with_member()
    _run('--all', '--dry-run')   # must not raise on the live-contracts queryset evaluation


def test_process_contracts_user_runs_without_memberships_prefetch():
    ProfileFactory(psn_username='pc-user')
    _live_contract_with_member()
    _run('--user', 'pc-user', '--dry-run')
