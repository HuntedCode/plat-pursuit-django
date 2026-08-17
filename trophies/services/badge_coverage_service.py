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
"""

from django.db.models import Q
from django.db.models.functions import Lower

from trophies.models import BadgeSeries, Concept


def audit_badge_coverage():
    """For each badge series that tracks a franchise, collection, and/or developer, find concepts of that
    franchise (any non-excluded link) / collection (any non-excluded, non-spin-off member) / developer
    (developed games) that are NOT covered by any stage of the series.

    Returns a list (sorted by series name) of dicts, one per series WITH gaps:
        {'series': BadgeSeries, 'franchise': Franchise|None, 'collection': Franchise|None,
         'developer': Company|None, 'missing': [Concept]}
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
        if developer:
            candidate_ids |= set(
                Concept.objects.filter(
                    concept_companies__company=developer,
                    concept_companies__is_developer=True,
                ).values_list('id', flat=True)
            )
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
            })

    return findings
