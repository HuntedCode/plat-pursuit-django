"""
Collectibles sub-page system service layer.

One Collectibles page per Concept (opt-in), composed of:
  - CollectibleType (per-page custom types — "Feathers", "Korok Seeds", etc.)
  - CollectibleArea (per-page custom areas — chapters, regions)
  - Collectible (the actual items)

Sibling resource to Roadmap, mirrors its lock + branch + revision lifecycle.
This module owns the read side (retrieval + progress computation); editor
mutations + lock/merge will live in `collectibles_merge_service` once
Phase 2 lands.
"""
from __future__ import annotations

import logging

from django.db.models import Prefetch

logger = logging.getLogger('psn_api')


class CollectiblesService:
    """Read-side helpers for the Collectibles sub-page system."""

    # ------------------------------------------------------------------ #
    #  Retrieval
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_prefetch():
        """Shared prefetch chain for a Collectibles page's children.

        Items are eagerly loaded with their type + area joined so the
        rendered page (which groups by area and tabs by type) doesn't
        N+1 across hundreds of rows.
        """
        from trophies.models import (
            CollectibleType, CollectibleArea, Collectible,
        )

        return [
            Prefetch(
                'types',
                queryset=CollectibleType.objects.order_by('order'),
            ),
            Prefetch(
                'areas',
                queryset=CollectibleArea.objects.order_by('order'),
            ),
            Prefetch(
                'items',
                queryset=Collectible.objects
                .select_related('type', 'area')
                .order_by('area__order', 'order', 'name'),
            ),
        ]

    @staticmethod
    def get_collectibles_for_display(concept):
        """Return the PUBLISHED Collectibles for a concept, prefetched.

        Used by the public reader page. Returns None if the concept has
        no Collectibles or it isn't published — page render falls through
        to a "no collectibles guide for this game yet" empty state.
        """
        from trophies.models import Collectibles

        return (
            Collectibles.objects
            .filter(concept=concept, status='published')
            .select_related('concept', 'created_by')
            .prefetch_related(*CollectiblesService._build_prefetch())
            .first()
        )

    @staticmethod
    def get_collectibles_for_preview(concept):
        """Return the Collectibles for a concept regardless of publish status.

        Used by author preview / staff views where draft content must be
        visible. Branch overlay (Phase 2) will hook in here once the
        editor lands.
        """
        from trophies.models import Collectibles

        return (
            Collectibles.objects
            .filter(concept=concept)
            .select_related('concept', 'created_by')
            .prefetch_related(*CollectiblesService._build_prefetch())
            .first()
        )

    # ------------------------------------------------------------------ #
    #  Progress
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_user_progress(profile, collectibles):
        """Return a set of collectible IDs the profile has marked as found.

        Single query, sparse — only collected items have UserCollectibleProgress
        rows so this stays cheap even for completionists. Caller can do
        `id in progress` membership tests during template render.

        Returns an empty set for anonymous viewers (no profile) — the
        reader-side JS layers in localStorage progress on top.
        """
        if not profile or not collectibles:
            return set()
        from trophies.models import UserCollectibleProgress

        return set(
            UserCollectibleProgress.objects
            .filter(profile=profile, collectible__collectibles=collectibles)
            .values_list('collectible_id', flat=True)
        )

    @staticmethod
    def compute_progress_summary(collectibles, found_ids):
        """Return overall + per-type + per-area progress counts.

        `found_ids` is the set returned by `get_user_progress`. Buckets are
        keyed by id so the template can look them up cheaply when rendering
        type tabs and per-area headers.

        Returns:
            {
                'total': int,
                'found': int,
                'by_type': {type_id: {'total': int, 'found': int}},
                'by_area': {area_id_or_None: {'total': int, 'found': int}},
            }
        """
        if not collectibles:
            return {'total': 0, 'found': 0, 'by_type': {}, 'by_area': {}}

        by_type = {}
        by_area = {}
        total = 0
        found = 0
        for item in collectibles.items.all():
            total += 1
            is_found = item.id in found_ids
            if is_found:
                found += 1

            t_bucket = by_type.setdefault(
                item.type_id, {'total': 0, 'found': 0},
            )
            t_bucket['total'] += 1
            if is_found:
                t_bucket['found'] += 1

            a_bucket = by_area.setdefault(
                item.area_id, {'total': 0, 'found': 0},
            )
            a_bucket['total'] += 1
            if is_found:
                a_bucket['found'] += 1

        return {
            'total': total,
            'found': found,
            'by_type': by_type,
            'by_area': by_area,
        }
