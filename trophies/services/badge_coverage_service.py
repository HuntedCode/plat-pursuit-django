"""Audit badge coverage: franchise/developer concepts missing from badge stages.

For a badge that tracks a franchise or developer, every game (concept) of that
franchise/developer is expected to live in one of the badge's series stages. A
concept that doesn't usually means a new game shipped and needs adding to the badge
(or a data error). This module finds those gaps; the audit_badge_coverage command
formats and emails them.
"""

from django.db.models import Q
from django.db.models.functions import Lower

from trophies.models import Badge, Concept


def audit_badge_coverage():
    """For each tier-1 badge that tracks a franchise, collection, and/or developer,
    find concepts of that franchise (any non-excluded link) / collection (any
    non-excluded, non-spin-off member) / developer (developed games) that are
    NOT covered by any stage of the badge's series.

    Returns a list (sorted by badge name) of dicts, one per badge WITH gaps:
        {'badge': Badge, 'franchise': Franchise|None, 'collection': Franchise|None,
         'developer': Company|None, 'missing': [Concept]}

    Only tier-1 badges are scanned: franchise/collection/developer is set on the
    series' base (tier-1) badge and inherited by the others, so each series is
    checked once.
    """
    findings = []
    badges = (
        Badge.objects.filter(tier=1)
        .select_related('franchise', 'collection', 'developer', 'base_badge')
        .order_by(Lower('name'))
    )

    for badge in badges:
        # A badge with no series_slug has no stages of its own; skip it rather than
        # treat every candidate as missing (filtering stages__series_slug='' / None
        # would match unrelated empty-slug stages, not "this series").
        if not badge.series_slug:
            continue

        franchise = badge.effective_franchise
        collection = badge.effective_collection
        developer = badge.effective_developer
        if not franchise and not collection and not developer:
            continue

        # Concepts this badge is expected to cover.
        candidate_ids = set()
        if franchise:
            # Every non-excluded linked concept counts. is_excluded=True is an
            # admin override that says "despite IGDB linking this game to the
            # franchise, don't expect a franchise badge to cover it" (e.g. a
            # tie-in cameo that IGDB lists but isn't really a franchise title).
            candidate_ids |= set(
                Concept.objects.filter(
                    concept_franchises__franchise=franchise,
                    concept_franchises__is_excluded=False,
                ).values_list('id', flat=True)
            )
        if collection:
            # EVERY linked concept is a member except spin-offs: a game IGDB
            # types as a "Spin-off" of this series (e.g. Agents of Mayhem in
            # the Saints Row series) is not expected to live in the collection
            # badge's stages, so don't flag it missing. Same is_excluded admin
            # override applies as for franchises.
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

        # Concepts actually covered by one of this badge series' stages -- either a
        # direct stage member (Stage.concepts) OR a member of a ConceptBundle on a
        # stage (Stage.concept_bundles -> ConceptBundle.concepts). Missing the bundle
        # path falsely flagged bundle-covered games as uncovered.
        covered_ids = set(
            Concept.objects.filter(
                Q(stages__series_slug=badge.series_slug)
                | Q(bundles__stage__series_slug=badge.series_slug)
            ).values_list('id', flat=True)
        )

        missing_ids = candidate_ids - covered_ids
        if missing_ids:
            missing = list(
                Concept.objects.filter(id__in=missing_ids).order_by(Lower('unified_title'))
            )
            findings.append({
                'badge': badge,
                'franchise': franchise,
                'collection': collection,
                'developer': developer,
                'missing': missing,
            })

    return findings
