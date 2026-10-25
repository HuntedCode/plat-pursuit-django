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

A developer series covers the developer's whole studio FAMILY, not just the one Company row: see
`trophies.services.developer_family_service`.

`series_candidates` is the single definition of "the games this series should cover". The audit checks it
against the stages; `generate_badge_stages` builds stages from it. Keeping one definition is what makes a
freshly generated badge audit clean.
"""

from django.db.models.functions import Lower

from trophies.models import BadgeSeries, Concept
from trophies.services.stage_membership import in_series_q
from trophies.services.developer_family_service import (
    developer_family_ids, family_developer_credits, sorted_studio_names,
)


def series_candidates(series):
    """The concepts `series` is expected to cover, as `(candidate_ids, developer_credits)`.

    Franchise: any non-excluded link. Collection: any non-excluded, non-spin-off member. Developer: games
    DEVELOPED by any studio in the developer's family. The three are unioned.

    `developer_credits` is `{concept_id: {company_id: name}}` for the developer side (empty when the series
    tracks no developer); its keys are the developer-side candidates.
    """
    candidate_ids = set()
    if series.franchise_id:
        # Every non-excluded linked concept counts. is_excluded=True is an admin override that says
        # "despite IGDB linking this game to the franchise, don't expect a franchise badge to cover
        # it" (e.g. a tie-in cameo that IGDB lists but isn't really a franchise title).
        candidate_ids |= set(
            Concept.objects.filter(
                concept_franchises__franchise_id=series.franchise_id,
                concept_franchises__is_excluded=False,
            ).values_list('id', flat=True)
        )
    if series.collection_id:
        # EVERY linked concept is a member except spin-offs: a game IGDB types as a "Spin-off" of this
        # series (e.g. Agents of Mayhem in the Saints Row series) is not expected to live in the
        # collection badge's stages, so don't flag it missing. Same is_excluded override as franchises.
        candidate_ids |= set(
            Concept.objects.filter(
                concept_franchises__franchise_id=series.collection_id,
                concept_franchises__is_spinoff=False,
                concept_franchises__is_excluded=False,
            ).values_list('id', flat=True)
        )
    credits = (
        family_developer_credits(developer_family_ids(series.developer)) if series.developer_id else {}
    )
    candidate_ids.update(credits)
    return candidate_ids, credits


def covered_concept_ids(series_slug):
    """Concepts covered by one of the series' stages, by any membership path (`in_series_q`): a direct
    member, a ConceptBundle member, or the list holding a trophy-group member. Missing the bundle path
    falsely flagged bundle-covered games as uncovered."""
    return set(Concept.objects.filter(in_series_q([series_slug])).values_list('id', flat=True))


def audit_badge_coverage():
    """For each badge series that tracks a franchise, collection, and/or developer, find the
    `series_candidates` that are NOT covered by any stage of the series.

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

        candidate_ids, credits = series_candidates(series)
        if not candidate_ids:
            continue

        missing_ids = candidate_ids - covered_concept_ids(series.series_slug)
        if missing_ids:
            missing = list(
                Concept.objects.filter(id__in=missing_ids).order_by(Lower('unified_title'))
            )
            findings.append({
                'series': series,
                'franchise': series.franchise,
                'collection': series.collection,
                'developer': series.developer,
                'missing': missing,
                'credited_developers': {
                    cid: sorted_studio_names(credits[cid]) for cid in missing_ids if cid in credits
                },
            })

    return findings


def stale_developer_pins():
    """Developer badge series pinned to a company IGDB has since renamed or merged into another.

    Reported on its own, NOT as part of a coverage gap, because a stale pin is exactly what HIDES gaps: once
    IGDB re-parents the studios onto the new identity, they leave the old name's tree (see
    `developer_family_ids`), their new games stop being candidates, and the audit goes quiet instead of
    flagging them. So the alert has to fire with zero missing games.

    Returns a list (sorted by series name) of dicts:
        {'series': BadgeSeries, 'developer': Company, 'current': Company, 'is_loop': bool}
    `current` is the end of `developer.rename_chain()`. `is_loop` is True when the chain loops back on itself
    in IGDB data, so there is no clean current identity to point at and the company records need a look.
    """
    pins = []
    series_qs = (
        BadgeSeries.objects
        .filter(developer__changed_company__isnull=False)
        .select_related('developer__changed_company')
        .order_by(Lower('name'))
    )
    for series in series_qs:
        current = series.developer.current_company
        pins.append({
            'series': series,
            'developer': series.developer,
            'current': current,
            'is_loop': current.changed_company_id is not None,
        })
    return pins
