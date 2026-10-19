"""Tests for the badge coverage audit (audit_badge_coverage service + command).

A badge SERIES that tracks a franchise/collection/developer should cover every non-excluded linked concept /
non-spin-off collection member / game developed by the developer's studio family (subsidiaries + former
identities) in its stages. Missing ones are flagged. Admin can suppress a specific link via
ConceptFranchise.is_excluded=True.

Repointed off the tier model in the 2026-08 cutover. The audit scanned `Badge.objects.filter(tier=1)`
because franchise/collection/developer lived on the base badge and were inherited by the other tiers;
`BadgeSeries` carries them directly, so both the tier filter and the `effective_*` inheritance are gone
(and the test that covered that inheritance with them). Coverage stays a SERIES-level question -- stages
belong to the series and every edition works the same list -- so nothing here needs a GroupBadge.
"""

import pytest
from django.core.management import call_command

from trophies.models import ConceptFranchise, Franchise
from trophies.services.badge_coverage_service import audit_badge_coverage, developer_family_ids
from tests.factories import (
    BadgeSeriesFactory, CompanyFactory, ConceptBundleFactory, ConceptCompanyFactory,
    ConceptFactory, GameFactory, StageFactory,
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


def _link_franchise(concept, franchise, is_excluded=False, is_spinoff=False):
    ConceptFranchise.objects.create(
        concept=concept, franchise=franchise,
        is_excluded=is_excluded, is_spinoff=is_spinoff,
    )


def _concept_with_game(title):
    concept = ConceptFactory(unified_title=title)
    GameFactory(concept=concept)
    return concept


def _cover(concept, series_slug, stage_number=1):
    """Put a concept into a stage of the badge's series."""
    StageFactory(series_slug=series_slug, stage_number=stage_number).concepts.add(concept)


def _developer_badge(slug, developer):
    badge = BadgeSeriesFactory(series_slug=slug)
    badge.developer = developer
    badge.save()
    return badge


def _sole_finding_missing_ids(findings):
    assert len(findings) == 1
    return sorted(c.id for c in findings[0]['missing'])


# --- service ------------------------------------------------------------------


def test_franchise_badge_flags_uncovered_concept():
    badge = BadgeSeriesFactory(series_slug="cov-fran")
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
    assert findings[0]['series'] == badge
    assert [c.id for c in findings[0]['missing']] == [missing.id]


def test_developer_badge_flags_uncovered_concept():
    badge = BadgeSeriesFactory(series_slug="cov-dev")
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


def test_developer_badge_counts_only_developed_games():
    # Publish/port/support-only credits are not the studio's game and must not be flagged. A row that is
    # developer AND publisher (a self-publishing studio) still counts.
    dev = CompanyFactory(name="Only Dev Studio")
    _developer_badge("cov-dev-roles", dev)

    developed = _concept_with_game("Developed")
    self_published = _concept_with_game("Self Published")
    published = _concept_with_game("Published Only")
    ported = _concept_with_game("Ported Only")
    supported = _concept_with_game("Supported Only")
    ConceptCompanyFactory(concept=developed, company=dev, is_developer=True)
    ConceptCompanyFactory(concept=self_published, company=dev, is_developer=True, is_publisher=True)
    ConceptCompanyFactory(concept=published, company=dev, is_developer=False, is_publisher=True)
    ConceptCompanyFactory(concept=ported, company=dev, is_developer=False, is_porting=True)
    ConceptCompanyFactory(concept=supported, company=dev, is_developer=False, is_supporting=True)

    assert _sole_finding_missing_ids(audit_badge_coverage()) == sorted([developed.id, self_published.id])


def test_developer_badge_includes_subsidiaries_at_every_depth():
    # The Rockstar shape: the umbrella label is credited as PUBLISHER, the subsidiary studios as developer.
    umbrella = CompanyFactory(name="Umbrella Games")
    studio = CompanyFactory(name="Umbrella North", parent=umbrella)
    sub_studio = CompanyFactory(name="Umbrella North Leeds", parent=studio)
    _developer_badge("cov-dev-subs", umbrella)

    by_studio = _concept_with_game("Studio Game")
    by_sub_studio = _concept_with_game("Sub Studio Game")
    ConceptCompanyFactory(concept=by_studio, company=umbrella, is_developer=False, is_publisher=True)
    ConceptCompanyFactory(concept=by_studio, company=studio, is_developer=True)
    ConceptCompanyFactory(concept=by_sub_studio, company=sub_studio, is_developer=True)

    assert _sole_finding_missing_ids(audit_badge_coverage()) == sorted([by_studio.id, by_sub_studio.id])


def test_developer_badge_subsidiary_port_credit_is_not_counted():
    # The developed-only rule applies to the whole family, not just the badge's own company.
    umbrella = CompanyFactory(name="Port Umbrella")
    porter = CompanyFactory(name="Port House", parent=umbrella)
    _developer_badge("cov-dev-sub-port", umbrella)

    ported = _concept_with_game("Ported By Subsidiary")
    ConceptCompanyFactory(concept=ported, company=porter, is_developer=False, is_porting=True)

    assert audit_badge_coverage() == []


def test_developer_badge_does_not_walk_up_or_across_the_tree():
    # Tracking a subsidiary must not pull in its parent's or its sibling's games (no Take-Two-wide sweep).
    owner = CompanyFactory(name="Holding Co")
    tracked = CompanyFactory(name="Tracked Studio", parent=owner)
    sibling = CompanyFactory(name="Sibling Studio", parent=owner)
    _developer_badge("cov-dev-scope", tracked)

    own = _concept_with_game("Own Game")
    owners = _concept_with_game("Owner Game")
    siblings = _concept_with_game("Sibling Game")
    ConceptCompanyFactory(concept=own, company=tracked, is_developer=True)
    ConceptCompanyFactory(concept=owners, company=owner, is_developer=True)
    ConceptCompanyFactory(concept=siblings, company=sibling, is_developer=True)

    assert _sole_finding_missing_ids(audit_badge_coverage()) == [own.id]


def test_developer_badge_includes_former_identities_of_the_family():
    # A studio renamed/merged INTO a family member (changed_company points at it) counts as the same
    # developer, including a former identity of a subsidiary.
    current = CompanyFactory(name="Current Studio")
    renamed_from = CompanyFactory(name="Old Name Studio", changed_company=current)
    subsidiary = CompanyFactory(name="Current Sub", parent=current)
    sub_renamed_from = CompanyFactory(name="Old Sub Name", changed_company=subsidiary)
    _developer_badge("cov-dev-renamed", current)

    old_game = _concept_with_game("Old Name Game")
    old_sub_game = _concept_with_game("Old Sub Game")
    ConceptCompanyFactory(concept=old_game, company=renamed_from, is_developer=True)
    ConceptCompanyFactory(concept=old_sub_game, company=sub_renamed_from, is_developer=True)

    assert _sole_finding_missing_ids(audit_badge_coverage()) == sorted([old_game.id, old_sub_game.id])


def test_family_published_game_developed_by_an_outsider_is_not_counted():
    # Same-row guard: "family company" and "is_developer" must hold on ONE ConceptCompany row. The
    # umbrella published it and an outside studio developed it, so it is not the family's game.
    umbrella = CompanyFactory(name="Pub Umbrella")
    outsider = CompanyFactory(name="Contract Studio")
    _developer_badge("cov-dev-same-row", umbrella)

    published = _concept_with_game("Published For Outsider")
    ConceptCompanyFactory(concept=published, company=umbrella, is_developer=False, is_publisher=True)
    ConceptCompanyFactory(concept=published, company=outsider, is_developer=True)

    assert audit_badge_coverage() == []


def test_developer_badge_pinned_to_an_old_name_covers_every_newer_name():
    # A badge pointed at the oldest identity still sees games credited to EVERY later name (two hops here,
    # so a seed that only follows one hop fails), plus its own subsidiaries.
    current = CompanyFactory(name="Third Name")
    middle = CompanyFactory(name="Second Name", changed_company=current)
    oldest = CompanyFactory(name="First Name", changed_company=middle)
    own_sub = CompanyFactory(name="First Name Sub", parent=oldest)
    _developer_badge("cov-dev-old-pin", oldest)

    games = []
    for studio in (oldest, middle, current, own_sub):
        game = _concept_with_game(f"{studio.name} Game")
        ConceptCompanyFactory(concept=game, company=studio, is_developer=True)
        games.append(game.id)

    assert _sole_finding_missing_ids(audit_badge_coverage()) == sorted(games)


def test_old_name_badge_does_not_sweep_in_a_merger_partner_or_the_successor_tree():
    # IGDB models a merger as both predecessors pointing at the merged company (Square -> Square Enix <-
    # Enix). A "Square" badge covers Square Enix's own games, but not Enix's or the successor's subsidiaries.
    merged = CompanyFactory(name="Square Enix")
    square = CompanyFactory(name="Square", changed_company=merged)
    enix = CompanyFactory(name="Enix", changed_company=merged)
    taito = CompanyFactory(name="Taito", parent=merged)
    _developer_badge("cov-dev-merger", square)

    square_game = _concept_with_game("Square Game")
    merged_game = _concept_with_game("Square Enix Game")
    enix_game = _concept_with_game("Enix Game")
    taito_game = _concept_with_game("Taito Game")
    ConceptCompanyFactory(concept=square_game, company=square, is_developer=True)
    ConceptCompanyFactory(concept=merged_game, company=merged, is_developer=True)
    ConceptCompanyFactory(concept=enix_game, company=enix, is_developer=True)
    ConceptCompanyFactory(concept=taito_game, company=taito, is_developer=True)

    assert _sole_finding_missing_ids(audit_badge_coverage()) == sorted([square_game.id, merged_game.id])


def test_current_name_badge_covers_both_merger_predecessors():
    # The other side of the merger rule: pinned to the CURRENT company, both predecessors are former
    # identities and count, as do its subsidiaries.
    merged = CompanyFactory(name="Merged Co")
    left = CompanyFactory(name="Left Co", changed_company=merged)
    right = CompanyFactory(name="Right Co", changed_company=merged)
    sub = CompanyFactory(name="Merged Sub", parent=merged)

    assert developer_family_ids(merged) == {merged.id, left.id, right.id, sub.id}


def test_subsidiary_game_covered_by_a_stage_is_not_flagged():
    umbrella = CompanyFactory(name="Covered Umbrella")
    studio = CompanyFactory(name="Covered North", parent=umbrella)
    badge = _developer_badge("cov-dev-sub-covered", umbrella)

    covered = _concept_with_game("Covered Sub Game")
    ConceptCompanyFactory(concept=covered, company=studio, is_developer=True)
    _cover(covered, badge.series_slug)

    assert audit_badge_coverage() == []


def test_mixed_series_credits_only_the_developer_side_gaps():
    # A franchise+developer series: the franchise-only gap gets no "developed by" entry.
    umbrella = CompanyFactory(name="Mixed Umbrella")
    studio = CompanyFactory(name="Mixed North", parent=umbrella)
    badge = _developer_badge("cov-dev-mixed", umbrella)
    fran = _franchise(slug="cov-dev-mixed-f")
    badge.franchise = fran
    badge.save()

    fran_gap = _concept_with_game("Franchise Side Gap")
    dev_gap = _concept_with_game("Developer Side Gap")
    _link_franchise(fran_gap, fran)
    ConceptCompanyFactory(concept=dev_gap, company=studio, is_developer=True)

    finding = audit_badge_coverage()[0]

    assert sorted(c.id for c in finding['missing']) == sorted([fran_gap.id, dev_gap.id])
    assert finding['credited_developers'] == {dev_gap.id: ["Mixed North"]}


def test_developer_family_walk_survives_a_cycle():
    # IGDB data is user-edited; a parent loop must terminate, not spin.
    a = CompanyFactory(name="Loop A")
    b = CompanyFactory(name="Loop B", parent=a)
    a.parent = b
    a.save()

    assert developer_family_ids(a) == {a.id, b.id}


def test_developer_family_walk_survives_a_rename_cycle():
    # A changed_company loop: rename_chain's own guard stops the seed, the walk's `seen` stops the rest.
    a = CompanyFactory(name="Rename A")
    b = CompanyFactory(name="Rename B", changed_company=a)
    a.changed_company = b
    a.save()

    assert developer_family_ids(a) == {a.id, b.id}


def test_rename_chain_with_a_tail_into_a_cycle_terminates():
    # tail -> a <-> b: the chain stops at the first repeat, in order.
    a = CompanyFactory(name="Cycle A")
    b = CompanyFactory(name="Cycle B", changed_company=a)
    a.changed_company = b
    a.save()
    tail = CompanyFactory(name="Cycle Tail", changed_company=a)

    assert [c.id for c in tail.rename_chain()] == [tail.id, a.id, b.id]
    assert tail.current_company == b
    assert developer_family_ids(tail) == {tail.id, a.id, b.id}


def test_developer_family_walk_is_one_query_per_level(django_assert_num_queries):
    # Guards against a per-node (N+1) walk: three levels with several studios each, no renames.
    umbrella = CompanyFactory(name="Wide Umbrella")
    for i in range(3):
        studio = CompanyFactory(name=f"Wide Studio {i}", parent=umbrella)
        for j in range(3):
            CompanyFactory(name=f"Wide Team {i}-{j}", parent=studio)

    # level 1 (studios), level 2 (teams), then the empty frontier check.
    with django_assert_num_queries(3):
        family = developer_family_ids(umbrella)
    assert len(family) == 13


def test_developer_finding_names_the_credited_studios():
    umbrella = CompanyFactory(name="Credit Umbrella")
    north = CompanyFactory(name="Credit North", parent=umbrella)
    leeds = CompanyFactory(name="Credit Leeds", parent=umbrella)
    outsider = CompanyFactory(name="Outside Partner")
    _developer_badge("cov-dev-credit", umbrella)

    co_dev = _concept_with_game("Co Developed")
    ConceptCompanyFactory(concept=co_dev, company=north, is_developer=True)
    ConceptCompanyFactory(concept=co_dev, company=leeds, is_developer=True)
    ConceptCompanyFactory(concept=co_dev, company=outsider, is_developer=True)
    ConceptCompanyFactory(concept=co_dev, company=umbrella, is_developer=False, is_publisher=True)

    findings = audit_badge_coverage()

    # Family developers only, sorted: the outside co-developer and the publish-only umbrella are left out.
    assert findings[0]['credited_developers'] == {co_dev.id: ["Credit Leeds", "Credit North"]}


def test_franchise_only_finding_has_no_credited_developers():
    badge = BadgeSeriesFactory(series_slug="cov-fran-credit")
    fran = _franchise(slug="cov-fran-credit-f")
    badge.franchise = fran
    badge.save()
    _link_franchise(_concept_with_game("Franchise Gap"), fran)

    assert audit_badge_coverage()[0]['credited_developers'] == {}


def test_collection_badge_flags_uncovered_member_via_any_link():
    # Every non-excluded linked concept is a collection member -- an
    # uncovered collection member must be flagged.
    badge = BadgeSeriesFactory(series_slug="cov-coll")
    coll = _collection(slug="cov-coll-c")
    badge.collection = coll
    badge.save()

    covered = _concept_with_game("Coll Covered")
    missing = _concept_with_game("Coll Missing")
    _link_franchise(covered, coll)
    _link_franchise(missing, coll)
    _cover(covered, badge.series_slug)

    findings = audit_badge_coverage()

    assert len(findings) == 1
    assert findings[0]['collection'] == coll
    assert [c.id for c in findings[0]['missing']] == [missing.id]


def test_collection_badge_does_not_flag_spinoff_member():
    """A game IGDB types as a spin-off of the collection (e.g. Agents of Mayhem under the
    Saints Row series) isn't expected in the collection badge's stages, so an uncovered
    spin-off must NOT be flagged. Real members still are."""
    badge = BadgeSeriesFactory(series_slug="cov-spin")
    coll = _collection(slug="cov-spin-c")
    badge.collection = coll
    badge.save()

    member = _concept_with_game("Real Member")
    spinoff = _concept_with_game("Spin-off Game")
    ConceptFranchise.objects.create(concept=member, franchise=coll, is_spinoff=False)
    ConceptFranchise.objects.create(concept=spinoff, franchise=coll, is_spinoff=True)
    # Neither is covered by a stage; only the real member should be flagged missing.

    findings = audit_badge_coverage()

    assert len(findings) == 1
    missing_ids = [c.id for c in findings[0]['missing']]
    assert member.id in missing_ids
    assert spinoff.id not in missing_ids


def test_no_gap_when_all_covered():
    badge = BadgeSeriesFactory(series_slug="cov-clean")
    fran = _franchise(slug="cov-clean-f")
    badge.franchise = fran
    badge.save()
    c = _concept_with_game("Only Game")
    _link_franchise(c, fran)
    _cover(c, badge.series_slug)

    assert audit_badge_coverage() == []


def test_badge_without_franchise_or_developer_is_skipped():
    BadgeSeriesFactory(series_slug="cov-none")  # no franchise/developer
    _concept_with_game("Loose Game")  # not linked to anything

    assert audit_badge_coverage() == []


def test_excluded_concept_not_flagged():
    # is_excluded=True suppresses a link from badge coverage. Use this for the
    # rare bad-link case where IGDB lists a game under a franchise it shouldn't
    # (cameo, tie-in cross-reference, etc.) without unlinking it entirely.
    badge = BadgeSeriesFactory(series_slug="cov-excl")
    fran = _franchise(slug="cov-excl-f")
    badge.franchise = fran
    badge.save()
    cameo = _concept_with_game("Crossover Cameo")
    _link_franchise(cameo, fran, is_excluded=True)

    assert audit_badge_coverage() == []


def test_non_excluded_concept_counts_toward_franchise_coverage():
    # Without is_main, every non-excluded linked concept counts. Previously
    # tie-ins (is_main=False) were filtered out; now they're required unless
    # an admin explicitly excludes them.
    badge = BadgeSeriesFactory(series_slug="cov-broad")
    fran = _franchise(slug="cov-broad-f")
    badge.franchise = fran
    badge.save()
    counted = _concept_with_game("Counted Tie-in")
    _link_franchise(counted, fran)  # default is_excluded=False

    findings = audit_badge_coverage()
    assert len(findings) == 1
    assert [c.id for c in findings[0]['missing']] == [counted.id]


def test_badge_with_both_franchise_and_developer_unions_dedups_and_orders():
    badge = BadgeSeriesFactory(series_slug="cov-both")
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
    # Franchises and collections share the ConceptFranchise table and can
    # co-occur on one badge. A concept that is both a franchise title AND a
    # collection member must appear exactly once in the missing set.
    badge = BadgeSeriesFactory(series_slug="cov-fc")
    fran = _franchise(slug="cov-fc-f")
    coll = _collection(slug="cov-fc-c")
    badge.franchise = fran
    badge.collection = coll
    badge.save()

    fran_only = _concept_with_game("Apple Fran Game")
    coll_only = _concept_with_game("Banana Coll Game")
    both = _concept_with_game("Cherry Both Game")
    _link_franchise(fran_only, fran)
    _link_franchise(coll_only, coll)
    _link_franchise(both, fran)
    _link_franchise(both, coll)

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
    badge = BadgeSeriesFactory(series_slug="cov-multi")
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


def test_concept_covered_via_bundle_on_stage_is_not_flagged():
    # Coverage must count concepts inside a ConceptBundle on a stage, not just
    # direct Stage.concepts members -- otherwise a bundle-covered game false-flags.
    badge = BadgeSeriesFactory(series_slug="cov-bundle")
    fran = _franchise(slug="cov-bundle-f")
    badge.franchise = fran
    badge.save()

    bundled = _concept_with_game("Bundled Game")
    _link_franchise(bundled, fran)
    stage = StageFactory(series_slug=badge.series_slug, stage_number=1)
    ConceptBundleFactory(stage=stage).concepts.add(bundled)

    assert audit_badge_coverage() == []


def test_bundle_on_another_series_stage_does_not_cover():
    # The bundle coverage path must respect series_slug: a concept bundled onto a
    # DIFFERENT series' stage is still missing from THIS badge (guards the filter).
    badge = BadgeSeriesFactory(series_slug="cov-bundle-own")
    fran = _franchise(slug="cov-bundle-own-f")
    badge.franchise = fran
    badge.save()

    orphan = _concept_with_game("Orphan Bundled Game")
    _link_franchise(orphan, fran)
    other_stage = StageFactory(series_slug="some-other-series", stage_number=1)
    ConceptBundleFactory(stage=other_stage).concepts.add(orphan)

    findings = audit_badge_coverage()
    assert len(findings) == 1
    assert [c.id for c in findings[0]['missing']] == [orphan.id]


def test_blank_series_slug_badge_is_skipped():
    # Guard: a tracked badge with no series_slug has no stages; it must NOT flag
    # every franchise concept as missing.
    badge = BadgeSeriesFactory(series_slug="")
    fran = _franchise(slug="cov-blank-f")
    badge.franchise = fran
    badge.save()
    c = _concept_with_game("Orphan Game")
    _link_franchise(c, fran)

    assert audit_badge_coverage() == []


# --- command (email) ----------------------------------------------------------


def _badge_with_gap():
    badge = BadgeSeriesFactory(series_slug="cov-cmd")
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
    badge = BadgeSeriesFactory(series_slug="cov-coll-cmd")
    coll = _collection(name="My Collection", slug="cov-coll-cmd-c")
    badge.collection = coll
    badge.save()
    missing = _concept_with_game("Coll Cmd Missing")
    _link_franchise(missing, coll)

    call_command('audit_badge_coverage')

    assert len(mailoutbox) == 1
    body = mailoutbox[0].body
    assert 'collection: My Collection' in body
    assert 'Coll Cmd Missing' in body


def test_command_email_names_the_subsidiary_that_developed_the_game(mailoutbox):
    umbrella = CompanyFactory(name="Mail Umbrella")
    studio = CompanyFactory(name="Mail North", parent=umbrella)
    _developer_badge("cov-dev-cmd", umbrella)
    gap = _concept_with_game("Mail Sub Game")
    ConceptCompanyFactory(concept=gap, company=studio, is_developer=True)

    call_command('audit_badge_coverage')

    body = mailoutbox[0].body
    assert 'developer: Mail Umbrella' in body
    gap_line = next(line for line in body.splitlines() if 'Mail Sub Game' in line)
    assert gap_line.endswith('[developed by: Mail North]')


def test_command_email_lists_co_developers_and_leaves_franchise_gaps_unlabelled(mailoutbox):
    umbrella = CompanyFactory(name="Mix Umbrella")
    north = CompanyFactory(name="Mix North", parent=umbrella)
    leeds = CompanyFactory(name="Mix Leeds", parent=umbrella)
    badge = _developer_badge("cov-dev-cmd-mix", umbrella)
    fran = _franchise(slug="cov-dev-cmd-mix-f")
    badge.franchise = fran
    badge.save()

    co_dev = _concept_with_game("Mix Co Dev Game")
    ConceptCompanyFactory(concept=co_dev, company=north, is_developer=True)
    ConceptCompanyFactory(concept=co_dev, company=leeds, is_developer=True)
    _link_franchise(_concept_with_game("Mix Franchise Game"), fran)

    call_command('audit_badge_coverage')

    lines = mailoutbox[0].body.splitlines()
    co_dev_line = next(line for line in lines if 'Mix Co Dev Game' in line)
    franchise_line = next(line for line in lines if 'Mix Franchise Game' in line)
    assert co_dev_line.endswith('[developed by: Mix Leeds, Mix North]')
    assert 'developed by' not in franchise_line


def test_command_email_flags_a_badge_pinned_to_a_former_identity(mailoutbox):
    current = CompanyFactory(name="Now Studio")
    old = CompanyFactory(name="Was Studio", changed_company=current)
    _developer_badge("cov-dev-cmd-old", old)
    gap = _concept_with_game("Was Studio Game")
    ConceptCompanyFactory(concept=gap, company=old, is_developer=True)

    call_command('audit_badge_coverage')

    assert (
        "note: Was Studio is a former identity; point the badge at Now Studio "
        "to also cover the studios under it."
    ) in mailoutbox[0].body


def test_command_email_has_no_former_identity_note_for_a_current_company(mailoutbox):
    umbrella = CompanyFactory(name="Present Studio")
    _developer_badge("cov-dev-cmd-current", umbrella)
    gap = _concept_with_game("Present Studio Game")
    ConceptCompanyFactory(concept=gap, company=umbrella, is_developer=True)

    call_command('audit_badge_coverage')

    assert 'former identity' not in mailoutbox[0].body


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
