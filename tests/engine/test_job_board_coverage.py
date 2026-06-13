"""Tests for audit_job_board_coverage: which badge games still lack a Contract."""
import io

import pytest
from django.core.management import call_command

from trophies.models import Contract, ContractBundle, ContractMembership
from tests.factories import BadgeFactory, ConceptFactory, StageFactory

pytestmark = pytest.mark.django_db


def test_flags_badge_game_without_a_contract():
    BadgeFactory(series_slug='re', tier=1, name='Resident Evil', is_live=True)
    covered = ConceptFactory(unified_title='Covered Game')
    uncovered = ConceptFactory(unified_title='Uncovered Game')
    StageFactory(series_slug='re', stage_number=1).concepts.add(covered, uncovered)
    ContractMembership.objects.create(
        contract=Contract.objects.create(name='RE', slug='re'), concept=covered,
    )

    out = io.StringIO()
    call_command('audit_job_board_coverage', stdout=out)
    report = out.getvalue()

    assert 'Uncovered Game' in report       # missing a Contract -> flagged
    assert 'Covered Game' not in report     # on the Job Board -> not flagged
    assert 'Resident Evil' in report        # grouped under its badge


def test_bundle_satisfier_counts_as_covered():
    BadgeFactory(series_slug='jak', tier=1, name='Jak', is_live=True)
    collection = ConceptFactory(unified_title='Jak Collection')
    StageFactory(series_slug='jak', stage_number=1).concepts.add(collection)
    bundle = ContractBundle.objects.create(
        contract=Contract.objects.create(name='Jak 1', slug='jak-1'), label='collection',
    )
    bundle.concepts.add(collection)

    out = io.StringIO()
    call_command('audit_job_board_coverage', stdout=out)
    report = out.getvalue()

    assert 'Job Board coverage' in report
    assert 'Jak Collection' not in report   # satisfies a Contract via a bundle -> covered


def test_live_only_excludes_draft_badges():
    BadgeFactory(series_slug='draft', tier=1, name='Draft Badge', is_live=False)
    game = ConceptFactory(unified_title='Draft Only Game')
    StageFactory(series_slug='draft', stage_number=1).concepts.add(game)

    out = io.StringIO()
    call_command('audit_job_board_coverage', '--live-only', stdout=out)
    report = out.getvalue()

    # Draft badge's uncovered game is excluded when scoped to live badges.
    assert 'Draft Only Game' not in report
