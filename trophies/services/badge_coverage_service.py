"""Audit badge coverage: franchise/developer concepts missing from badge stages.

For a badge series that tracks a franchise, collection or developer, every game (concept) of that
franchise/collection/developer is expected to live in one of the series' stages. A concept that does not
usually means a new game shipped and needs adding to the series (or a data error). This module finds those
gaps; the `audit_badge_coverage` command formats and emails them.

Repointed onto `BadgeSeries` in the 2026-08 badge cutover, and it got simpler doing it. The tier-based
version scanned `Badge.objects.filter(tier=1)` -- not because tier 1 meant anything here, but because
franchise/collection/developer were set on the base badge and inherited by the rest, so tier 1 was the
one-row-per-series trick. `BadgeSeries` carries those three fields directly, so the filter and the
`effective_*` inheritance both disappear.

Coverage is a SERIES-level question, not a per-edition one: stages belong to the series, and Legacy HD and
Ultra HD work the same stage list. So this is unchanged by the edition split, and deliberately ignores
`GroupBadge` entirely.

A developer series covers the developer's whole studio FAMILY, not just the one Company row: IGDB credits
the studio that did the work (Rockstar North, Rockstar San Diego), and the umbrella label (Rockstar Games)
is usually credited only as publisher. See `developer_family_ids`.
"""

from collections import defaultdict

from django.db.models import Q
from django.db.models.functions import Lower

from trophies.models import BadgeSeries, Company, Concept, ConceptCompany


def developer_family_ids(company):
    """Ids of `company`'s studio family: `company`, every subsidiary below it (any depth), every former
    identity of any of them, and `company`'s own NEWER identities.

    Walks DOWN the IGDB tree from `company` only: `Company.subsidiaries` (the `parent` FK) and
    `Company.previous_identities` (the `changed_company` FK, i.e. studios renamed or merged INTO a family
    member). It never walks up to a parent or across to a sibling, so a Rockstar badge does not pull in the
    rest of Take-Two.

    The newer identities (`company.rename_chain()`) count for their OWN credits only, so a badge pinned to a
    studio's old name still covers what it ships under the new one. Their trees are deliberately NOT walked:
    IGDB models a merger as both predecessors pointing at the merged company (Square -> Square Enix <- Enix),
    so walking down from the successor would sweep in the merger partner and the successor's subsidiaries.
    Point the badge at the current company to cover its whole current family.

    Queries: one per rename hop, then one per level of the tree (plus the final, empty one). The `seen` set
    makes the walk cycle-safe: IGDB data is user-edited and a parent/rename loop must not spin forever.

    IGDB's `parent` is the CURRENT owner, so a studio acquired later brings its pre-acquisition games with
    it. That over-reach is accepted: the audit only flags candidates, and a curator decides.
    """
    seen = {c.id for c in company.rename_chain()}
    frontier = {company.id}
    while frontier:
        frontier = set(
            Company.objects.filter(
                Q(parent_id__in=frontier) | Q(changed_company_id__in=frontier)
            ).exclude(id__in=seen).values_list('id', flat=True)
        )
        seen |= frontier
    return seen


def _family_developer_credits(family_ids):
    """{concept_id: sorted family studio names credited as DEVELOPER of that concept}.

    The keys ARE the developer series' candidates; the values label each missing game in the email. Querying
    ConceptCompany directly keeps "family company" and "is_developer" on the SAME row, so a game the
    umbrella only published (and an outside studio developed) is not a candidate. A row that is developer
    AND publisher (a studio that self-publishes) still counts; publish/port/support-only rows never do.
    """
    credited = defaultdict(set)
    rows = ConceptCompany.objects.filter(
        company_id__in=family_ids, is_developer=True,
    ).values_list('concept_id', 'company__name')
    for concept_id, name in rows:
        credited[concept_id].add(name)
    return {concept_id: sorted(names, key=str.lower) for concept_id, names in credited.items()}


def audit_badge_coverage():
    """For each badge series that tracks a franchise, collection, and/or developer, find concepts of that
    franchise (any non-excluded link) / collection (any non-excluded, non-spin-off member) / developer
    (games DEVELOPED by the developer or any studio in its family) that are NOT covered by any stage of
    the series.

    Returns a list (sorted by series name) of dicts, one per series WITH gaps:
        {'series': BadgeSeries, 'franchise': Franchise|None, 'collection': Franchise|None,
         'developer': Company|None, 'missing': [Concept],
         'credited_developers': {concept_id: [studio name]}}
    `credited_developers` is empty when the series tracks no developer, and has no key for a missing concept
    that came from the franchise/collection side only.
    """
    findings = []
    all_series = (
        BadgeSeries.objects
        .select_related('franchise', 'collection', 'developer')
        .order_by(Lower('name'))
    )

    for series in all_series:
        # A series with no slug has no stages of its own; skip rather than treat every candidate as
        # missing. Filtering `stages__series_slug=''` would match unrelated empty-slug stages, not "this
        # series". `series_slug` is unique so there can only ever be one such row, but one is enough to
        # produce a report claiming an entire franchise is uncovered.
        if not series.series_slug:
            continue

        franchise = series.franchise
        collection = series.collection
        developer = series.developer
        if not franchise and not collection and not developer:
            continue

        # Concepts this series is expected to cover.
        candidate_ids = set()
        if franchise:
            # Every non-excluded linked concept counts. is_excluded=True is an admin override that says
            # "despite IGDB linking this game to the franchise, don't expect a franchise badge to cover
            # it" (e.g. a tie-in cameo that IGDB lists but isn't really a franchise title).
            candidate_ids |= set(
                Concept.objects.filter(
                    concept_franchises__franchise=franchise,
                    concept_franchises__is_excluded=False,
                ).values_list('id', flat=True)
            )
        if collection:
            # EVERY linked concept is a member except spin-offs: a game IGDB types as a "Spin-off" of this
            # series (e.g. Agents of Mayhem in the Saints Row series) is not expected to live in the
            # collection badge's stages, so don't flag it missing. Same is_excluded override as franchises.
            candidate_ids |= set(
                Concept.objects.filter(
                    concept_franchises__franchise=collection,
                    concept_franchises__is_spinoff=False,
                    concept_franchises__is_excluded=False,
                ).values_list('id', flat=True)
            )
        credits = _family_developer_credits(developer_family_ids(developer)) if developer else {}
        candidate_ids.update(credits)
        if not candidate_ids:
            continue

        # Concepts actually covered by one of this series' stages -- either a direct stage member
        # (Stage.concepts) OR a member of a ConceptBundle on a stage (Stage.concept_bundles ->
        # ConceptBundle.concepts). Missing the bundle path falsely flagged bundle-covered games as
        # uncovered.
        covered_ids = set(
            Concept.objects.filter(
                Q(stages__series_slug=series.series_slug)
                | Q(bundles__stage__series_slug=series.series_slug)
            ).values_list('id', flat=True)
        )

        missing_ids = candidate_ids - covered_ids
        if missing_ids:
            missing = list(
                Concept.objects.filter(id__in=missing_ids).order_by(Lower('unified_title'))
            )
            findings.append({
                'series': series,
                'franchise': franchise,
                'collection': collection,
                'developer': developer,
                'missing': missing,
                'credited_developers': {cid: credits[cid] for cid in missing_ids if cid in credits},
            })

    return findings
