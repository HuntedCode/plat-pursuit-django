"""Tests for the badge coverage audit (audit_badge_coverage service + command).

A tier-1 badge that tracks a franchise/developer should cover every is_main
franchise title / developed game in its stages. Missing ones are flagged.
"""

import pytest
from django.core.management import call_command

from trophies.models import ConceptFranchise, Franchise
from trophies.services.badge_coverage_service import audit_badge_coverage
from tests.factories import (
    BadgeFactory, CompanyFactory, ConceptCompanyFactory, ConceptFactory,
    GameFactory, StageFactory,
)

pytestmark = pytest.mark.django_db


def _franchise(name="Fran", igdb_id=1, slug="fran"):
    return Franchise.objects.create(
        igdb_id=igdb_id, name=name, slug=slug, source_type='franchise',
    )


def _link_franchise(concept, franchise, is_main=True):
    ConceptFranchise.objects.create(concept=concept, franchise=franchise, is_main=is_main)


def _concept_with_game(title):
    concept = ConceptFactory(unified_title=title)
    GameFactory(concept=concept)
    return concept


def _cover(concept, series_slug, stage_number=1):
    """Put a concept into a stage of the badge's series."""
    StageFactory(series_slug=series_slug, stage_number=stage_number).concepts.add(concept)


# --- service ------------------------------------------------------------------


def test_franchise_badge_flags_uncovered_concept():
    badge = BadgeFactory(series_slug="cov-fran", tier=1)
    fran = _franchise(slug="cov-fran-f")
    badge.franchise = fran
    badge.save()

    covered = _concept_with_game("Covered Game")
    missing = _concept_with_game("New Unassigned Game")
    _link_franchise(covered, fran)
    _link_franchise(missing, fran)
    _cover(covered, badge.series_slug)

    findings = audit_badge_coverage()

    assert len(findings) == 1
    assert findings[0]['badge'] == badge
    assert [c.id for c in findings[0]['missing']] == [missing.id]


def test_developer_badge_flags_uncovered_concept():
    badge = BadgeFactory(series_slug="cov-dev", tier=1)
    dev = CompanyFactory(name="Studio X")
    badge.developer = dev
    badge.save()

    covered = _concept_with_game("Dev Covered")
    missing = _concept_with_game("Dev Missing")
    ConceptCompanyFactory(concept=covered, company=dev, is_developer=True)
    ConceptCompanyFactory(concept=missing, company=dev, is_developer=True)
    _cover(covered, badge.series_slug)

    findings = audit_badge_coverage()

    assert len(findings) == 1
    assert [c.id for c in findings[0]['missing']] == [missing.id]


def test_no_gap_when_all_covered():
    badge = BadgeFactory(series_slug="cov-clean", tier=1)
    fran = _franchise(slug="cov-clean-f")
    badge.franchise = fran
    badge.save()
    c = _concept_with_game("Only Game")
    _link_franchise(c, fran)
    _cover(c, badge.series_slug)

    assert audit_badge_coverage() == []


def test_badge_without_franchise_or_developer_is_skipped():
    BadgeFactory(series_slug="cov-none", tier=1)  # no franchise/developer
    _concept_with_game("Loose Game")  # not linked to anything

    assert audit_badge_coverage() == []


def test_tie_in_concept_not_flagged():
    # is_main=False (a tie-in) is not part of the franchise's own titles, so an
    # uncovered tie-in must not raise a false alarm.
    badge = BadgeFactory(series_slug="cov-tiein", tier=1)
    fran = _franchise(slug="cov-tiein-f")
    badge.franchise = fran
    badge.save()
    tie_in = _concept_with_game("Crossover Cameo")
    _link_franchise(tie_in, fran, is_main=False)

    assert audit_badge_coverage() == []


# --- command (email) ----------------------------------------------------------


def _badge_with_gap():
    badge = BadgeFactory(series_slug="cov-cmd", tier=1)
    fran = _franchise(slug="cov-cmd-f")
    badge.franchise = fran
    badge.save()
    missing = _concept_with_game("Cmd Missing Game")
    _link_franchise(missing, fran)
    return badge


def test_command_emails_when_gaps(mailoutbox):
    _badge_with_gap()
    call_command('audit_badge_coverage')

    assert len(mailoutbox) == 1
    msg = mailoutbox[0]
    assert msg.to == ['badge-alerts@platpursuit.com']
    assert 'unassigned concept' in msg.subject
    assert 'Cmd Missing Game' in msg.body


def test_command_sends_nothing_when_clean(mailoutbox):
    call_command('audit_badge_coverage')  # no tracked badges -> no findings
    assert len(mailoutbox) == 0


def test_command_always_sends_heartbeat_when_clean(mailoutbox):
    call_command('audit_badge_coverage', '--always')
    assert len(mailoutbox) == 1
    assert 'all clear' in mailoutbox[0].subject


def test_command_dry_run_sends_no_email(mailoutbox):
    _badge_with_gap()
    call_command('audit_badge_coverage', '--dry-run')
    assert len(mailoutbox) == 0
