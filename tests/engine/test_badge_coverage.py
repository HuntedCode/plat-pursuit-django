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


def _collection(name="Coll", igdb_id=900, slug="coll"):
    return Franchise.objects.create(
        igdb_id=igdb_id, name=name, slug=slug, source_type='collection',
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


def test_collection_badge_flags_uncovered_member_via_any_link():
    # Collections never set is_main, so EVERY linked concept is a member -- an
    # uncovered collection member must be flagged even though its link is is_main=False.
    badge = BadgeFactory(series_slug="cov-coll", tier=1)
    coll = _collection(slug="cov-coll-c")
    badge.collection = coll
    badge.save()

    covered = _concept_with_game("Coll Covered")
    missing = _concept_with_game("Coll Missing")
    _link_franchise(covered, coll, is_main=False)   # collection links carry is_main=False
    _link_franchise(missing, coll, is_main=False)
    _cover(covered, badge.series_slug)

    findings = audit_badge_coverage()

    assert len(findings) == 1
    assert findings[0]['collection'] == coll
    assert [c.id for c in findings[0]['missing']] == [missing.id]


def test_effective_collection_inherits_from_base_badge():
    base = BadgeFactory(series_slug="cov-inh", tier=1)
    coll = _collection(slug="cov-inh-c")
    base.collection = coll
    base.save()
    child = BadgeFactory(series_slug="cov-inh", tier=2, base_badge=base)
    assert child.effective_collection == coll


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


def test_badge_with_both_franchise_and_developer_unions_dedups_and_orders():
    badge = BadgeFactory(series_slug="cov-both", tier=1)
    fran = _franchise(slug="cov-both-f")
    dev = CompanyFactory(name="Both Studio")
    badge.franchise = fran
    badge.developer = dev
    badge.save()

    fran_only = _concept_with_game("Apple Franchise Game")
    dev_only = _concept_with_game("Banana Dev Game")
    both = _concept_with_game("Cherry Both Game")
    _link_franchise(fran_only, fran)
    ConceptCompanyFactory(concept=dev_only, company=dev, is_developer=True)
    _link_franchise(both, fran)
    ConceptCompanyFactory(concept=both, company=dev, is_developer=True)

    findings = audit_badge_coverage()

    assert len(findings) == 1
    missing = findings[0]['missing']
    # union of both sources, the 'both' concept appears exactly once (deduped)
    assert sorted(c.id for c in missing) == sorted([fran_only.id, dev_only.id, both.id])
    # ordered by unified_title
    assert [c.unified_title for c in missing] == [
        "Apple Franchise Game", "Banana Dev Game", "Cherry Both Game",
    ]


def test_badge_with_franchise_and_collection_unions_and_dedups():
    # Franchises and collections share the ConceptFranchise table and can co-occur
    # on one badge. A concept that is both an is_main franchise title AND a
    # collection member must appear exactly once in the missing set.
    badge = BadgeFactory(series_slug="cov-fc", tier=1)
    fran = _franchise(slug="cov-fc-f")
    coll = _collection(slug="cov-fc-c")
    badge.franchise = fran
    badge.collection = coll
    badge.save()

    fran_only = _concept_with_game("Apple Fran Game")
    coll_only = _concept_with_game("Banana Coll Game")
    both = _concept_with_game("Cherry Both Game")
    _link_franchise(fran_only, fran, is_main=True)
    _link_franchise(coll_only, coll, is_main=False)
    _link_franchise(both, fran, is_main=True)     # an is_main franchise title...
    _link_franchise(both, coll, is_main=False)    # ...and a collection member

    findings = audit_badge_coverage()

    assert len(findings) == 1
    finding = findings[0]
    assert finding['franchise'] == fran
    assert finding['collection'] == coll
    # union of both sources; the 'both' concept appears exactly once (deduped)
    assert sorted(c.id for c in finding['missing']) == sorted([fran_only.id, coll_only.id, both.id])
    assert [c.unified_title for c in finding['missing']] == [
        "Apple Fran Game", "Banana Coll Game", "Cherry Both Game",
    ]


def test_covered_across_multiple_stages():
    badge = BadgeFactory(series_slug="cov-multi", tier=1)
    fran = _franchise(slug="cov-multi-f")
    badge.franchise = fran
    badge.save()
    c1 = _concept_with_game("Stage One Game")
    c2 = _concept_with_game("Stage Two Game")
    c3 = _concept_with_game("Uncovered Game")
    for c in (c1, c2, c3):
        _link_franchise(c, fran)
    _cover(c1, badge.series_slug, stage_number=1)
    _cover(c2, badge.series_slug, stage_number=2)

    findings = audit_badge_coverage()
    assert len(findings) == 1
    assert [c.id for c in findings[0]['missing']] == [c3.id]


def test_blank_series_slug_badge_is_skipped():
    # Guard: a tracked badge with no series_slug has no stages; it must NOT flag
    # every franchise concept as missing.
    badge = BadgeFactory(series_slug="", tier=1)
    fran = _franchise(slug="cov-blank-f")
    badge.franchise = fran
    badge.save()
    c = _concept_with_game("Orphan Game")
    _link_franchise(c, fran)

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


def test_command_email_labels_collection_source(mailoutbox):
    badge = BadgeFactory(series_slug="cov-coll-cmd", tier=1)
    coll = _collection(name="My Collection", slug="cov-coll-cmd-c")
    badge.collection = coll
    badge.save()
    missing = _concept_with_game("Coll Cmd Missing")
    _link_franchise(missing, coll, is_main=False)

    call_command('audit_badge_coverage')

    assert len(mailoutbox) == 1
    body = mailoutbox[0].body
    assert 'collection: My Collection' in body
    assert 'Coll Cmd Missing' in body


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
