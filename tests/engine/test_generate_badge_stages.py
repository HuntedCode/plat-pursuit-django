"""Tests for generate_badge_stages (badge_stage_service + command).

One stage per ORIGIN game (GameFamily folds ports/remasters/editions together), titled with the origin's
name. Franchise/collection badges run oldest origin release first; developer badges run by filing studio
(the developer first, then its studios A-Z) and A-Z within. A series with stages is refused unless --append,
which only ever ADDS: to the existing stage of the same origin, or as new stages after the last one.
"""

from datetime import datetime, timezone as dt_timezone
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from trophies.models import ConceptFranchise, Franchise, GameFamily, Stage
from trophies.services.badge_coverage_service import audit_badge_coverage
from trophies.services.badge_stage_service import plan_stages
from trophies.services.developer_family_service import developer_family_ids, studio_filing_map
from tests.factories import (
    BadgeSeriesFactory, CompanyFactory, ConceptBundleFactory, ConceptCompanyFactory, ConceptFactory,
    GroupBadgeFactory, IGDBMatchFactory, StageFactory,
)

pytestmark = pytest.mark.django_db


def _date(year):
    return datetime(year, 6, 1, tzinfo=dt_timezone.utc)


def _family(name, igdb_id, year=None):
    return GameFamily.objects.create(
        canonical_name=name, igdb_id=igdb_id, origin_first_release_date=_date(year) if year else None,
    )


def _concept(title, family=None, igdb_id=None, status='auto_accepted', year=None):
    concept = ConceptFactory(unified_title=title, family=family, release_date=_date(year) if year else None)
    if igdb_id is not None:
        IGDBMatchFactory(concept=concept, igdb_id=igdb_id, igdb_name=f"IGDB {title}", status=status)
    return concept


def _franchise_badge(slug, source_type='franchise', igdb_id=1):
    franchise = Franchise.objects.create(
        igdb_id=igdb_id, name=f"{slug} source", slug=f"{slug}-src", source_type=source_type,
    )
    badge = BadgeSeriesFactory(series_slug=slug, badge_type=source_type)
    if source_type == 'franchise':
        badge.franchise = franchise
    else:
        badge.collection = franchise
    badge.save()
    return badge, franchise


def _link(concept, franchise, is_spinoff=False):
    ConceptFranchise.objects.create(concept=concept, franchise=franchise, is_spinoff=is_spinoff)


def _developer_badge(slug, developer):
    badge = BadgeSeriesFactory(series_slug=slug, badge_type='developer')
    badge.developer = developer
    badge.save()
    return badge


def _developed(concept, *studios):
    for studio in studios:
        ConceptCompanyFactory(concept=concept, company=studio, is_developer=True)


def _stages(slug):
    """[(stage_number, title, sorted concept ids)] for the series, in stage order."""
    return [
        (s.stage_number, s.title, sorted(s.concepts.values_list('id', flat=True)))
        for s in Stage.objects.filter(series_slug=slug).order_by('stage_number')
    ]


def _run(*args):
    out = StringIO()
    call_command('generate_badge_stages', *args, stdout=out)
    return out.getvalue()


# --- franchise / collection ordering -------------------------------------------


def test_franchise_badge_one_stage_per_origin_oldest_first():
    badge, fran = _franchise_badge("gen-fran")
    first = _family("First Game", 100, year=2001)
    second = _family("Second Game", 200, year=2005)
    original = _concept("First Game PS2", family=first)
    remaster = _concept("First Game Remastered", family=first)
    sequel = _concept("Second Game", family=second)
    for c in (original, remaster, sequel):
        _link(c, fran)

    _run(badge.series_slug)

    assert _stages(badge.series_slug) == [
        (1, "First Game", sorted([original.id, remaster.id])),
        (2, "Second Game", [sequel.id]),
    ]


def test_undated_origins_go_last_and_ties_break_by_title():
    badge, fran = _franchise_badge("gen-dates")
    undated = _concept("Aardvark Undated")
    beta = _concept("Beta Game", family=_family("Beta Game", 301, year=2010))
    alpha = _concept("Alpha Game", family=_family("Alpha Game", 302, year=2010))
    for c in (undated, beta, alpha):
        _link(c, fran)

    _run(badge.series_slug)

    assert [title for _, title, _ in _stages(badge.series_slug)] == ["Alpha Game", "Beta Game", "Aardvark Undated"]


def test_date_falls_back_to_trusted_match_then_concept_release():
    badge, fran = _franchise_badge("gen-date-fallback")
    by_concept = _concept("By Concept Date", year=1999)
    by_match = _concept("By Match Date", igdb_id=402, year=2010)  # concept date LOSES to the match's
    by_match.igdb_match.igdb_first_release_date = _date(2001)
    by_match.igdb_match.save()
    by_family = _concept("By Family Date", family=_family("By Family Date", 401, year=2003))
    for c in (by_family, by_match, by_concept):
        _link(c, fran)

    _run(badge.series_slug)

    assert [title for _, title, _ in _stages(badge.series_slug)] == [
        "By Concept Date", "IGDB By Match Date", "By Family Date",
    ]


def test_collection_badge_skips_spinoffs_and_franchise_plus_collection_dedups():
    badge, coll = _franchise_badge("gen-coll", source_type='collection', igdb_id=900)
    fran = Franchise.objects.create(igdb_id=901, name="Fran", slug="gen-coll-f", source_type='franchise')
    badge.franchise = fran
    badge.save()
    member = _concept("Member", family=_family("Member", 501, year=2000))
    spinoff = _concept("Spinoff", family=_family("Spinoff", 502, year=2001))
    _link(member, coll)
    _link(member, fran)
    _link(spinoff, coll, is_spinoff=True)

    _run(badge.series_slug)

    assert _stages(badge.series_slug) == [(1, "Member", [member.id])]


# --- origin grouping ----------------------------------------------------------


def test_unfamilied_trusted_origin_joins_its_family_stage():
    # The origin was enriched before its family existed: its trusted IGDB id IS the family's topmost id.
    badge, fran = _franchise_badge("gen-join")
    family = _family("Origin Name", 600, year=2002)
    port = _concept("Origin Port", family=family)
    origin = _concept("Origin Itself", igdb_id=600)
    for c in (port, origin):
        _link(c, fran)

    _run(badge.series_slug)

    assert _stages(badge.series_slug) == [(1, "Origin Name", sorted([port.id, origin.id]))]


def test_untrusted_matches_never_merge_and_unmatched_concepts_stand_alone():
    badge, fran = _franchise_badge("gen-untrusted")
    a = _concept("Shaky A", igdb_id=700, status='pending_review')
    b = _concept("Shaky B", igdb_id=700, status='pending_review')
    lone = _concept("No Match")
    for c in (a, b, lone):
        _link(c, fran)

    _run(badge.series_slug)

    assert sorted(title for _, title, _ in _stages(badge.series_slug)) == ["No Match", "Shaky A", "Shaky B"]


def test_admin_made_families_without_an_igdb_id_stay_separate():
    # Two admin families (igdb_id None) must not collapse onto one ('igdb', None) key.
    badge, fran = _franchise_badge("gen-admin-fam")
    one = _concept("Admin One", family=GameFamily.objects.create(canonical_name="Admin One"))
    two = _concept("Admin Two", family=GameFamily.objects.create(canonical_name="Admin Two"))
    sibling = _concept("Admin One Port", family=one.family)
    for c in (one, two, sibling):
        _link(c, fran)

    _run(badge.series_slug)

    assert sorted((title, ids) for _, title, ids in _stages(badge.series_slug)) == [
        ("Admin One", sorted([one.id, sibling.id])), ("Admin Two", [two.id]),
    ]


def test_title_falls_back_to_the_concept_and_is_truncated():
    badge, fran = _franchise_badge("gen-title")
    # canonical_name caps at 255; origin_name (500) is the one that can overflow a stage title.
    long_family = GameFamily.objects.create(
        canonical_name="", origin_name="L" * 400, igdb_id=1450, origin_first_release_date=_date(2000),
    )
    long_one = _concept("Long", family=long_family)
    untitled = ConceptFactory(unified_title="")
    for c in (long_one, untitled):
        _link(c, fran)

    _run(badge.series_slug)

    titles = [title for _, title, _ in _stages(badge.series_slug)]
    assert titles == ["L" * 255, f"Concept {untitled.pk}"]


def test_trusted_unfamilied_concepts_on_one_igdb_id_share_a_stage_titled_by_igdb():
    badge, fran = _franchise_badge("gen-igdb")
    ps4 = _concept("Game PS4", igdb_id=800)
    ps5 = _concept("Game PS5", igdb_id=800)
    for c in (ps4, ps5):
        _link(c, fran)

    _run(badge.series_slug)

    [(number, title, ids)] = _stages(badge.series_slug)
    assert ids == sorted([ps4.id, ps5.id])
    assert title in {"IGDB Game PS4", "IGDB Game PS5"}


# --- developer ordering + filing studio -----------------------------------------


def test_developer_badge_orders_by_studio_then_title():
    umbrella = CompanyFactory(name="Umbrella")
    north = CompanyFactory(name="North Studio", parent=umbrella)
    leeds = CompanyFactory(name="Leeds Studio", parent=umbrella)
    badge = _developer_badge("gen-dev", umbrella)

    zeta = _concept("Zeta", family=_family("Zeta", 901))
    alpha = _concept("Alpha", family=_family("Alpha", 902))
    north_game = _concept("Middle North", family=_family("Middle North", 903))
    leeds_game = _concept("Leeds Game", family=_family("Leeds Game", 904))
    _developed(zeta, umbrella)
    _developed(alpha, umbrella)
    _developed(north_game, north)
    _developed(leeds_game, leeds)

    _run(badge.series_slug)

    # Umbrella's own games first (A-Z), then Leeds, then North.
    assert [title for _, title, _ in _stages(badge.series_slug)] == ["Alpha", "Zeta", "Leeds Game", "Middle North"]


def test_co_developed_game_is_one_stage_filed_under_the_parent_else_a_to_z_first():
    umbrella = CompanyFactory(name="Co Umbrella")
    north = CompanyFactory(name="Co North", parent=umbrella)
    leeds = CompanyFactory(name="Co Leeds", parent=umbrella)
    badge = _developer_badge("gen-codev", umbrella)

    with_parent = _concept("With Parent", family=_family("With Parent", 1001))
    two_subs = _concept("Two Subs", family=_family("Two Subs", 1002))
    _developed(with_parent, north, umbrella)
    _developed(two_subs, north, leeds)

    plan = plan_stages(badge)

    assert [(p.title, p.studio[1]) for p in plan] == [("With Parent", "Co Umbrella"), ("Two Subs", "Co Leeds")]
    _run(badge.series_slug)
    assert len(_stages(badge.series_slug)) == 2


def test_studios_split_across_one_origins_versions_still_make_one_stage():
    # The original by one studio, its remaster by another: same origin, so ONE stage, filed by the rule.
    umbrella = CompanyFactory(name="Split Umbrella")
    north = CompanyFactory(name="Split North", parent=umbrella)
    porter = CompanyFactory(name="Split Ports", parent=umbrella)
    badge = _developer_badge("gen-split", umbrella)
    family = _family("Split Origin", 1101)
    original = _concept("Split Original", family=family)
    remaster = _concept("Split Remaster", family=family)
    _developed(original, north)
    _developed(remaster, porter)

    [planned] = plan_stages(badge)

    assert sorted(c.id for c in planned.concepts) == sorted([original.id, remaster.id])
    assert planned.studio[1] == "Split North"


def test_a_plain_rename_files_under_the_developer_but_merger_predecessors_keep_their_own():
    # DMA-style rename: the old name is the only predecessor, so it IS the developer. A merger (two
    # predecessors into one) means each predecessor was a real studio of its own.
    renamed = CompanyFactory(name="Zed Current")
    CompanyFactory(name="Alpha Old Name", changed_company=renamed)
    merged = CompanyFactory(name="Merged Co")
    left = CompanyFactory(name="Left Co", changed_company=merged)
    CompanyFactory(name="Right Co", changed_company=merged)
    rename_badge = _developer_badge("gen-rename-join", renamed)
    merger_badge = _developer_badge("gen-merger-split", merged)

    old_name_game = _concept("Old Name Game", family=_family("Old Name Game", 1460))
    _developed(old_name_game, renamed.previous_identities.get())
    left_game = _concept("Left Game", family=_family("Left Game", 1461))
    _developed(left_game, left)

    assert [p.studio for p in plan_stages(rename_badge)] == [(renamed.id, "Zed Current")]
    assert [p.studio for p in plan_stages(merger_badge)] == [(left.id, "Left Co")]


def test_a_subsidiarys_plain_rename_files_under_its_current_name():
    # Rockstar shape: DMA Design was renamed Rockstar North, a subsidiary. Its games join Rockstar North,
    # while a subsidiary formed by a merger keeps each predecessor's own section.
    umbrella = CompanyFactory(name="Star Games")
    north = CompanyFactory(name="Star North", parent=umbrella)
    dma = CompanyFactory(name="DMA Studio", changed_company=north)
    merged = CompanyFactory(name="Star Merged", parent=umbrella)
    left = CompanyFactory(name="Left Half", changed_company=merged)
    CompanyFactory(name="Right Half", changed_company=merged)
    badge = _developer_badge("gen-sub-rename", umbrella)

    dma_game = _concept("Old DMA Game", family=_family("Old DMA Game", 1465))
    north_game = _concept("North Game", family=_family("North Game", 1466))
    left_game = _concept("Left Game", family=_family("Left Game", 1467))
    _developed(dma_game, dma)
    _developed(north_game, north)
    _developed(left_game, left)

    plan = plan_stages(badge)

    assert [(p.title, p.studio[1]) for p in plan] == [
        ("Left Game", "Left Half"), ("North Game", "Star North"), ("Old DMA Game", "Star North"),
    ]


def test_developer_badge_files_unattributed_franchise_games_last():
    umbrella = CompanyFactory(name="Mixed Dev")
    badge = _developer_badge("gen-dev-mixed", umbrella)
    fran = Franchise.objects.create(igdb_id=3, name="Mixed Fran", slug="gen-dev-mixed-f", source_type='franchise')
    badge.franchise = fran
    badge.save()
    franchise_only = _concept("Aaa Franchise Only", family=_family("Aaa Franchise Only", 1470))
    _link(franchise_only, fran)
    developed = _concept("Zzz Developed", family=_family("Zzz Developed", 1471))
    _developed(developed, umbrella)

    assert [(p.title, p.studio) for p in plan_stages(badge)] == [
        ("Zzz Developed", (umbrella.id, "Mixed Dev")), ("Aaa Franchise Only", None),
    ]


def test_a_franchise_badge_with_a_developer_set_still_runs_chronologically():
    badge, fran = _franchise_badge("gen-fran-with-dev")
    dev = CompanyFactory(name="Some Dev")
    badge.developer = dev
    badge.save()
    newer = _concept("Aaa Newer", family=_family("Aaa Newer", 1480, year=2015))
    older = _concept("Zzz Older", family=_family("Zzz Older", 1481, year=2001))
    for c in (newer, older):
        _link(c, fran)
        _developed(c, dev)

    assert [(p.title, p.studio) for p in plan_stages(badge)] == [("Zzz Older", None), ("Aaa Newer", None)]


def test_developer_newer_identity_credit_files_under_the_developer():
    current = CompanyFactory(name="New Name")
    old = CompanyFactory(name="Old Name", changed_company=current)
    sub = CompanyFactory(name="Aaa Sub", parent=old)
    badge = _developer_badge("gen-renamed", old)
    by_new = _concept("By New Name", family=_family("By New Name", 1201))
    by_sub = _concept("By Sub", family=_family("By Sub", 1202))
    _developed(by_new, current)
    _developed(by_sub, sub)

    plan = plan_stages(badge)

    assert [(p.title, p.studio) for p in plan] == [
        ("By New Name", (old.id, "Old Name")), ("By Sub", (sub.id, "Aaa Sub")),
    ]


def test_a_co_credit_through_the_developers_newer_name_still_files_under_the_developer():
    # Credits to "New Name" (the developer's newer identity) and "Aaa Sub": the developer must win, which
    # needs the identity mapping, not a raw `developer.id in studios` check.
    current = CompanyFactory(name="Co New Name")
    old = CompanyFactory(name="Co Old Name", changed_company=current)
    sub = CompanyFactory(name="Aaa Co Sub", parent=old)
    badge = _developer_badge("gen-co-renamed", old)
    game = _concept("Co Game", family=_family("Co Game", 1203))
    _developed(game, current, sub)

    [planned] = plan_stages(badge)

    assert planned.studio == (old.id, "Co Old Name")


def test_a_multi_step_rename_chain_files_under_the_last_name():
    umbrella = CompanyFactory(name="Chain Umbrella")
    north = CompanyFactory(name="Chain North", parent=umbrella)
    middle = CompanyFactory(name="Chain Middle", changed_company=north)
    oldest = CompanyFactory(name="Chain Oldest", changed_company=middle)
    badge = _developer_badge("gen-chain", umbrella)
    game = _concept("Oldest Era Game", family=_family("Oldest Era Game", 1204))
    _developed(game, oldest)

    [planned] = plan_stages(badge)

    assert planned.studio == (north.id, "Chain North")


def test_a_rename_into_a_merger_predecessor_stops_at_that_predecessor():
    # A -> B (rename), then B + D -> C (merger): A's games file under B, and B and D keep their own rows.
    umbrella = CompanyFactory(name="Depth Umbrella")
    merged = CompanyFactory(name="Depth Merged", parent=umbrella)
    b = CompanyFactory(name="Depth B", changed_company=merged)
    d = CompanyFactory(name="Depth D", changed_company=merged)
    a = CompanyFactory(name="Depth A", changed_company=b)

    filing = studio_filing_map(umbrella, developer_family_ids(umbrella))

    assert filing[a.id] == (b.id, "Depth B")
    assert filing[b.id] == (b.id, "Depth B")
    assert filing[d.id] == (d.id, "Depth D")


def test_the_filing_map_terminates_on_a_rename_loop():
    umbrella = CompanyFactory(name="Loop Umbrella")
    x = CompanyFactory(name="Loop X", parent=umbrella)
    y = CompanyFactory(name="Loop Y", changed_company=x)
    x.changed_company = y
    x.save()

    filing = studio_filing_map(umbrella, developer_family_ids(umbrella))

    assert set(filing) == {umbrella.id, x.id, y.id}


# --- existing stages: refuse / append ------------------------------------------


def test_series_with_stages_is_refused_without_append():
    badge, fran = _franchise_badge("gen-refuse")
    _link(_concept("Any", family=_family("Any", 1301)), fran)
    StageFactory(series_slug=badge.series_slug, stage_number=1)

    with pytest.raises(CommandError, match="already has 1 stage"):
        _run(badge.series_slug)


def test_append_adds_new_versions_to_their_stage_and_new_games_after_the_last():
    badge, fran = _franchise_badge("gen-append")
    old_family = _family("Old Game", 1401, year=2000)
    in_stage = _concept("Old Game PS3", family=old_family)
    stage_two = StageFactory(
        series_slug=badge.series_slug, stage_number=2, title="Hand Titled", stage_icon="https://x.test/hand.png",
    )
    stage_two.concepts.add(in_stage)
    Stage.objects.filter(pk=stage_two.pk).update(stage_icon="https://x.test/hand.png")  # the signal re-derived it

    new_version = _concept("Old Game PS5", family=old_family)
    brand_new = _concept("Brand New", family=_family("Brand New", 1403, year=2020))
    for c in (in_stage, new_version, brand_new):
        _link(c, fran)

    _run(badge.series_slug, '--append')

    assert _stages(badge.series_slug) == [
        (2, "Hand Titled", sorted([in_stage.id, new_version.id])),
        (3, "Brand New", [brand_new.id]),
    ]
    # Appending only adds concepts: the hand-set icon survives the add's icon signal.
    stage_two.refresh_from_db()
    assert stage_two.stage_icon == "https://x.test/hand.png"


def test_append_keeps_a_hand_set_icon_when_two_origins_join_one_stage():
    # The icon signal rewrites the SHARED in-memory stage on each add; the restore must use the original.
    badge, fran = _franchise_badge("gen-append-two")
    first, second = _family("Trilogy One", 1410, year=2001), _family("Trilogy Two", 1411, year=2002)
    one, two = _concept("Trilogy One", family=first), _concept("Trilogy Two", family=second)
    stage = StageFactory(series_slug=badge.series_slug, stage_number=1, title="Trilogy")
    stage.concepts.add(one, two)
    Stage.objects.filter(pk=stage.pk).update(stage_icon="https://x.test/trilogy.png")
    remaster_one = _concept("Trilogy One Remastered", family=first)
    remaster_two = _concept("Trilogy Two Remastered", family=second)
    for c in (one, two, remaster_one, remaster_two):
        _link(c, fran)

    out = _run(badge.series_slug, '--append')

    stage.refresh_from_db()
    assert stage.stage_icon == "https://x.test/trilogy.png"
    assert sorted(stage.concepts.values_list('id', flat=True)) == sorted([one.id, two.id, remaster_one.id, remaster_two.id])
    assert "0 new stage(s), 1 existing stage(s) extended" in out


def test_append_gives_an_iconless_stage_the_icon_the_add_derived():
    # Only a stage's REAL icon is put back after an add; an empty one keeps what the signal derived. The new
    # version is created first so it has the lower pk the signal reads its icon from.
    badge, fran = _franchise_badge("gen-append-iconless")
    family = _family("Iconless", 1412, year=2001)
    new_version = ConceptFactory(unified_title="Iconless New", family=family, concept_icon_url="https://x.test/new.png")
    old_version = _concept("Iconless Old", family=family)
    stage = StageFactory(series_slug=badge.series_slug, stage_number=1)
    stage.concepts.add(old_version)
    assert Stage.objects.get(pk=stage.pk).stage_icon is None
    for c in (old_version, new_version):
        _link(c, fran)

    _run(badge.series_slug, '--append')

    assert Stage.objects.get(pk=stage.pk).stage_icon == "https://x.test/new.png"


def test_a_dry_run_where_every_addition_is_a_bundle_stage_says_nothing_would_be_written():
    badge, fran = _franchise_badge("gen-dry-bundle")
    family = _family("Dry Episodic", 1413, year=2001)
    episode_one = _concept("Dry Episode 1", family=family)
    stage = StageFactory(series_slug=badge.series_slug, stage_number=1)
    ConceptBundleFactory(stage=stage).concepts.add(episode_one)
    _link(episode_one, fran)
    _link(_concept("Dry Episode 2", family=family), fran)

    out = _run(badge.series_slug, '--append', '--dry-run')

    assert "Nothing written: every addition is for a bundle stage (1)" in out
    assert "Dry run: would write" not in out


def test_append_never_adds_to_a_bundle_stage_and_reports_it_instead():
    # Adding a new episode as a DIRECT concept would let it alone satisfy an all-episodes bundle stage.
    badge, fran = _franchise_badge("gen-append-bundle")
    family = _family("Episodic", 1404, year=2001)
    episode_one = _concept("Episode 1", family=family)
    stage = StageFactory(series_slug=badge.series_slug, stage_number=1, title="Bundle Stage")
    ConceptBundleFactory(stage=stage).concepts.add(episode_one)
    episode_two = _concept("Episode 2", family=family)
    for c in (episode_one, episode_two):
        _link(c, fran)

    out = _run(badge.series_slug, '--append')

    assert "Stage 1 (existing, uses a bundle): ADD BY HAND" in out
    assert "Episode 2" in out
    assert "Nothing written: every addition is for a bundle stage (1)" in out
    assert "Next:" not in out
    assert _stages(badge.series_slug) == [(1, "Bundle Stage", [])]
    assert list(stage.concept_bundles.get().concepts.values_list('id', flat=True)) == [episode_one.id]


def test_append_prefers_a_gating_stage_over_stage_zero_and_direct_over_bundled():
    badge, fran = _franchise_badge("gen-append-pick")
    family = _family("Picky", 1405, year=2001)
    in_zero = _concept("Picky Optional", family=family)
    in_bundle = _concept("Picky Bundled", family=family)
    in_direct = _concept("Picky Direct", family=family)
    StageFactory(series_slug=badge.series_slug, stage_number=0).concepts.add(in_zero)
    bundle_stage = StageFactory(series_slug=badge.series_slug, stage_number=2)
    ConceptBundleFactory(stage=bundle_stage).concepts.add(in_bundle)
    StageFactory(series_slug=badge.series_slug, stage_number=7).concepts.add(in_direct)
    new_version = _concept("Picky New", family=family)
    for c in (in_zero, in_bundle, in_direct, new_version):
        _link(c, fran)

    [planned] = plan_stages(badge, append=True)

    assert (planned.existing.stage_number, planned.bundled) == (7, False)


def test_append_with_nothing_missing_writes_nothing():
    badge, fran = _franchise_badge("gen-append-none")
    covered = _concept("Covered", family=_family("Covered", 1501))
    _link(covered, fran)
    StageFactory(series_slug=badge.series_slug, stage_number=1).concepts.add(covered)

    out = _run(badge.series_slug, '--append')

    assert "Nothing to add" in out
    assert len(_stages(badge.series_slug)) == 1


def test_append_after_only_an_optional_stage_starts_at_one():
    badge, fran = _franchise_badge("gen-append-zero")
    StageFactory(series_slug=badge.series_slug, stage_number=0, title="Optional")
    game = _concept("Real Game", family=_family("Real Game", 1601))
    _link(game, fran)

    _run(badge.series_slug, '--append')

    assert [number for number, _, _ in _stages(badge.series_slug)] == [0, 1]


# --- command behaviour ---------------------------------------------------------


def test_dry_run_prints_the_plan_and_writes_nothing():
    badge, fran = _franchise_badge("gen-dry")
    _link(_concept("Dry Game", family=_family("Dry Game", 1701)), fran)

    out = _run(badge.series_slug, '--dry-run')

    assert "Stage 1: Dry Game" in out
    assert "Nothing written" in out
    assert _stages(badge.series_slug) == []


def test_generated_badge_audits_clean():
    umbrella = CompanyFactory(name="Clean Umbrella")
    sub = CompanyFactory(name="Clean Sub", parent=umbrella)
    badge = _developer_badge("gen-clean", umbrella)
    for i, studio in enumerate((umbrella, sub, sub)):
        _developed(_concept(f"Clean {i}", family=_family(f"Clean {i}", 1800 + i)), studio)

    _run(badge.series_slug)

    assert audit_badge_coverage() == []


def test_franchise_badge_generated_stages_audit_clean():
    badge, fran = _franchise_badge("gen-clean-fran")
    family = _family("Clean Fran", 1810, year=2000)
    for title in ("Clean Fran PS3", "Clean Fran PS4"):
        _link(_concept(title, family=family), fran)

    _run(badge.series_slug)

    assert audit_badge_coverage() == []


def test_next_steps_say_to_release_an_edition_when_none_is_live():
    # A fresh series' editions are all dormant, and evaluate_badges --series does nothing for those.
    badge, fran = _franchise_badge("gen-next")
    _link(_concept("Next Game", family=_family("Next Game", 1901)), fran)

    out = _run(badge.series_slug)

    assert f"convert_series_to_groups {badge.series_slug}" in out
    assert "No edition is live yet: mark one live in the GroupBadge admin" in out


def test_next_steps_evaluate_when_an_edition_is_live():
    badge, fran = _franchise_badge("gen-next-live", igdb_id=2)
    GroupBadgeFactory(series=badge, is_live=True)
    _link(_concept("Live Game", family=_family("Live Game", 1902)), fran)

    out = _run(badge.series_slug)

    assert f"evaluate_badges --series {badge.series_slug}   (awards the live editions)" in out
    assert "No edition is live yet" not in out


def test_a_stage_number_collision_is_a_clean_error_and_writes_nothing(monkeypatch):
    badge, fran = _franchise_badge("gen-race")
    for i in range(2):
        _link(_concept(f"Race {i}", family=_family(f"Race {i}", 1950 + i, year=2000 + i)), fran)
    real_plan = plan_stages

    def plan_then_race(series, append=False):
        plan = real_plan(series, append)
        StageFactory(series_slug=series.series_slug, stage_number=2)  # someone else takes stage 2
        return plan

    monkeypatch.setattr('trophies.management.commands.generate_badge_stages.plan_stages', plan_then_race)

    with pytest.raises(CommandError, match="changed while this ran"):
        _run(badge.series_slug)
    assert [number for number, _, _ in _stages(badge.series_slug)] == [2]  # stage 1 rolled back


def test_series_without_a_subject_is_refused():
    BadgeSeriesFactory(series_slug="gen-bare")
    with pytest.raises(CommandError, match="no franchise, collection or developer"):
        _run("gen-bare")


def test_unknown_series_slug_is_refused():
    with pytest.raises(CommandError, match='No badge series with series_slug "nope"'):
        _run("nope")
