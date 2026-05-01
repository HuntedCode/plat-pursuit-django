"""
Roadmap system service layer.

Each ConceptTrophyGroup gets its own Roadmap (one for the base game, one
per DLC). They share a Concept for navigation purposes but otherwise
publish, lock, accumulate notes, and revision history independently.

This module is the read-side + lifecycle helper:
  - retrieval helpers for the public detail page, the editor, and the
    workshop CTA on game-detail
  - publish / unpublish toggles
  - snapshot/overlay helpers used by the lock+merge flow
  - workshop summary used by the staff CTA

Editor mutations (steps / trophy guides / scalar fields) flow through the
editor's BranchProxy + lock/merge cycle in `roadmap_merge_service`. The
direct-CRUD helpers that previously lived here were removed when we
collapsed RoadmapTab into Roadmap.
"""
from __future__ import annotations

import logging
import re

from django.db import transaction
from django.db.models import Count, Prefetch

logger = logging.getLogger('psn_api')


class RoadmapService:
    """Read-side helpers + lifecycle ops for the per-CTG Roadmap system."""

    YOUTUBE_PATTERN = re.compile(
        r'^https?://(?:www\.)?(?:youtube\.com|youtu\.be)/.+$'
    )

    # ------------------------------------------------------------------ #
    #  Retrieval
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_roadmap_prefetch():
        """Shared prefetch chain for a single Roadmap's children."""
        from trophies.models import RoadmapStep, RoadmapStepTrophy, TrophyGuide

        return [
            Prefetch(
                'steps',
                queryset=RoadmapStep.objects.prefetch_related(
                    Prefetch(
                        'step_trophies',
                        queryset=RoadmapStepTrophy.objects.order_by('order'),
                    )
                ).order_by('order'),
            ),
            Prefetch(
                'trophy_guides',
                queryset=TrophyGuide.objects.order_by('order', 'trophy_id'),
            ),
        ]

    @staticmethod
    def get_roadmap_for_display(concept, trophy_group_id='default'):
        """Get a single PUBLISHED Roadmap for the given CTG, prefetched.

        Returns None if no roadmap exists or it isn't published. Used by the
        public detail page (non-authors), where unpublished roadmaps must
        appear absent.
        """
        from trophies.models import Roadmap

        return (
            Roadmap.objects
            .filter(
                concept=concept,
                concept_trophy_group__trophy_group_id=trophy_group_id,
                status='published',
            )
            .select_related('concept_trophy_group')
            .prefetch_related(*RoadmapService._build_roadmap_prefetch())
            .first()
        )

    @staticmethod
    def get_roadmap_for_preview(concept, trophy_group_id='default'):
        """Get the Roadmap for the given CTG regardless of publish status.

        Used by author preview (`?preview=true`) and the editor entry path,
        where draft content must be visible.
        """
        from trophies.models import Roadmap

        return (
            Roadmap.objects
            .filter(
                concept=concept,
                concept_trophy_group__trophy_group_id=trophy_group_id,
            )
            .select_related('concept_trophy_group')
            .prefetch_related(*RoadmapService._build_roadmap_prefetch())
            .first()
        )

    @staticmethod
    def get_roadmap_for_editor(concept, trophy_group_id='default'):
        """Get-or-create the Roadmap for the given (concept, CTG).

        Each CTG gets its own Roadmap. When a writer opens the editor for a
        CTG that doesn't have one yet, we seed an empty draft on the spot
        so the editor view always has a target to attach the lock to.
        """
        from trophies.models import Roadmap, ConceptTrophyGroup

        try:
            ctg = concept.concept_trophy_groups.get(trophy_group_id=trophy_group_id)
        except ConceptTrophyGroup.DoesNotExist:
            return None

        roadmap, _ = Roadmap.objects.get_or_create(
            concept=concept,
            concept_trophy_group=ctg,
        )

        return (
            Roadmap.objects
            .filter(pk=roadmap.pk)
            .select_related('concept_trophy_group')
            .prefetch_related(*RoadmapService._build_roadmap_prefetch())
            .first()
        )

    @staticmethod
    def get_available_ctgs(concept, include_drafts=False):
        """List CTGs that have a Roadmap, with status + stats per CTG.

        Used by the DLC navigation strip on the public detail page (public
        sees only published) and by the staff Workshop CTA dispatcher
        (authors see drafts too via include_drafts=True).

        Returns a list of dicts with:
          - 'ctg': ConceptTrophyGroup
          - 'roadmap': Roadmap
          - 'status': str
          - 'has_content': bool (any steps or trophy guides)
          - 'trophy_group_id': str (the CTG's trophy_group_id)
          - 'step_count': int
          - 'guide_count': int
        """
        from trophies.models import Roadmap

        qs = (
            Roadmap.objects
            .filter(concept=concept)
            .select_related('concept_trophy_group')
            .annotate(
                step_count=Count('steps', distinct=True),
                guide_count=Count('trophy_guides', distinct=True),
            )
            .order_by(
                'concept_trophy_group__sort_order',
                'concept_trophy_group__trophy_group_id',
            )
        )
        if not include_drafts:
            qs = qs.filter(status='published')

        return [
            {
                'ctg': r.concept_trophy_group,
                'roadmap': r,
                'status': r.status,
                'trophy_group_id': r.concept_trophy_group.trophy_group_id,
                'has_content': (r.step_count + r.guide_count) > 0,
                'step_count': r.step_count,
                'guide_count': r.guide_count,
            }
            for r in qs
        ]

    @staticmethod
    def resolve_public_target(concept, requested_trophy_group_id):
        """Pick which CTG a public visitor lands on.

        Logic per the per-CTG publish design:
          - If the requested CTG has a published roadmap, return it.
          - Otherwise fall back to the first published roadmap on the
            concept (typically base, but DLC-only releases work too).
          - If nothing is published, return None — view should 404.

        Returns:
            (roadmap, trophy_group_id, redirected: bool) or (None, None, False)
        """
        # Try the explicitly-requested CTG first.
        roadmap = RoadmapService.get_roadmap_for_display(
            concept, requested_trophy_group_id,
        )
        if roadmap:
            return roadmap, requested_trophy_group_id, False

        # Fall back to any other published roadmap on the concept.
        from trophies.models import Roadmap

        fallback = (
            Roadmap.objects
            .filter(concept=concept, status='published')
            .select_related('concept_trophy_group')
            .order_by(
                'concept_trophy_group__sort_order',
                'concept_trophy_group__trophy_group_id',
            )
            .first()
        )
        if not fallback:
            return None, None, False

        # Re-fetch with the prefetches to match get_roadmap_for_display's
        # contract (the 1-row select_related/order_by query above doesn't
        # carry the children).
        full = (
            Roadmap.objects
            .filter(pk=fallback.pk)
            .select_related('concept_trophy_group')
            .prefetch_related(*RoadmapService._build_roadmap_prefetch())
            .first()
        )
        return full, fallback.concept_trophy_group.trophy_group_id, True

    # ------------------------------------------------------------------ #
    #  Branch overlay (preview rendering of unsaved edits)
    # ------------------------------------------------------------------ #

    @staticmethod
    def apply_branch_overlay(roadmap, branch_payload):
        """Mutate an in-memory Roadmap to reflect uncommitted branch state.

        Used by author preview to show unsaved edits without merging them
        to live records. Replaces the roadmap's prefetched `steps` and
        `trophy_guides` caches with payload-derived instances and updates
        scalar fields. Steps/guides that exist only in the branch (id=None
        in payload) materialize as transient unsaved instances; deleted
        ones are dropped.

        Safe to call only on a roadmap that was prefetched via
        `_build_roadmap_prefetch()`. The roadmap and its children are
        NEVER persisted as a side effect.
        """
        from trophies.models import RoadmapStep, RoadmapStepTrophy, TrophyGuide
        from trophies.services.roadmap_merge_service import (
            EDITOR_FIELDS, PUBLISHER_FIELDS, WRITER_FIELDS,
        )

        if not isinstance(branch_payload, dict):
            return roadmap

        # Scalar fields.
        for fld in WRITER_FIELDS + EDITOR_FIELDS + PUBLISHER_FIELDS:
            if fld in branch_payload:
                value = branch_payload[fld]
                if value is None and isinstance(getattr(roadmap, fld, None), str):
                    value = ''
                setattr(roadmap, fld, value)

        # Steps.
        live_steps_by_id = {s.id: s for s in roadmap.steps.all()}
        overlay_steps = []
        # Negative synthetic IDs for any new payload step that didn't carry
        # one. These need to be unique across the overlay so HTML anchors
        # and progress dict keys don't collide.
        synthetic_id = -1_000_000
        for index, step_payload in enumerate(branch_payload.get('steps') or []):
            sid = step_payload.get('id')
            if isinstance(sid, int) and sid in live_steps_by_id:
                step = live_steps_by_id[sid]
            else:
                synthetic_id -= 1
                step = RoadmapStep(roadmap=roadmap, id=synthetic_id)

            for fld in ('title', 'description', 'youtube_url'):
                if fld in step_payload:
                    setattr(step, fld, step_payload[fld] or '')
            step.order = step_payload.get('order', index)
            if 'gallery_images' in step_payload:
                step.gallery_images = list(step_payload.get('gallery_images') or [])

            trophy_ids = step_payload.get('trophy_ids')
            if trophy_ids is not None:
                transient_st = [
                    RoadmapStepTrophy(step=step, trophy_id=int(tid), order=i)
                    for i, tid in enumerate(trophy_ids)
                ]
                if not hasattr(step, '_prefetched_objects_cache'):
                    step._prefetched_objects_cache = {}
                step._prefetched_objects_cache['step_trophies'] = transient_st
            overlay_steps.append(step)

        # Trophy guides.
        live_guides_by_id = {g.id: g for g in roadmap.trophy_guides.all()}
        overlay_guides = []
        for guide_payload in branch_payload.get('trophy_guides') or []:
            gid = guide_payload.get('id')
            if isinstance(gid, int) and gid in live_guides_by_id:
                guide = live_guides_by_id[gid]
            else:
                guide = TrophyGuide(
                    roadmap=roadmap,
                    trophy_id=int(guide_payload.get('trophy_id') or 0),
                )
            if 'trophy_id' in guide_payload and guide_payload['trophy_id'] is not None:
                guide.trophy_id = int(guide_payload['trophy_id'])
            if 'body' in guide_payload:
                guide.body = guide_payload['body'] or ''
            for flag in ('is_missable', 'is_online', 'is_unobtainable'):
                if flag in guide_payload:
                    setattr(guide, flag, bool(guide_payload[flag]))
            if 'phase' in guide_payload:
                guide.phase = guide_payload.get('phase') or ''
            if 'order' in guide_payload:
                guide.order = guide_payload['order']
            if 'gallery_images' in guide_payload:
                guide.gallery_images = list(guide_payload.get('gallery_images') or [])
            overlay_guides.append(guide)

        if not hasattr(roadmap, '_prefetched_objects_cache'):
            roadmap._prefetched_objects_cache = {}
        roadmap._prefetched_objects_cache['steps'] = overlay_steps
        roadmap._prefetched_objects_cache['trophy_guides'] = overlay_guides
        return roadmap

    # ------------------------------------------------------------------ #
    #  Progress (used by the public detail page)
    # ------------------------------------------------------------------ #

    @staticmethod
    def compute_progress(roadmap, profile_earned):
        """Compute per-step and overall progress from a prefetched Roadmap.

        Args:
            roadmap: A Roadmap with prefetched steps -> step_trophies
            profile_earned: Dict of trophy_id -> {'earned': bool, ...}

        Returns:
            dict with 'steps' list, 'total_earned', 'total_trophies', 'percentage'
        """
        steps_progress = []
        total_trophies = 0
        total_earned = 0

        for step in roadmap.steps.all():
            step_trophy_ids = [st.trophy_id for st in step.step_trophies.all()]
            step_total = len(step_trophy_ids)
            step_earned = sum(
                1 for tid in step_trophy_ids
                if profile_earned.get(tid, {}).get('earned', False)
            )
            total_trophies += step_total
            total_earned += step_earned
            steps_progress.append({
                'step_id': step.id,
                'earned': step_earned,
                'total': step_total,
                'complete': step_earned == step_total and step_total > 0,
            })

        return {
            'steps': {sp['step_id']: sp for sp in steps_progress},
            'total_earned': total_earned,
            'total_trophies': total_trophies,
            'percentage': round(total_earned / total_trophies * 100) if total_trophies else 0,
        }

    # ------------------------------------------------------------------ #
    #  Publish lifecycle
    # ------------------------------------------------------------------ #

    @staticmethod
    def publish_roadmap(roadmap_id):
        from trophies.models import Roadmap
        try:
            roadmap = Roadmap.objects.get(pk=roadmap_id)
        except Roadmap.DoesNotExist:
            return None, "Roadmap not found."
        roadmap.status = 'published'
        roadmap.save(update_fields=['status', 'updated_at'])
        return roadmap, None

    @staticmethod
    def unpublish_roadmap(roadmap_id):
        from trophies.models import Roadmap
        try:
            roadmap = Roadmap.objects.get(pk=roadmap_id)
        except Roadmap.DoesNotExist:
            return None, "Roadmap not found."
        roadmap.status = 'draft'
        roadmap.save(update_fields=['status', 'updated_at'])
        return roadmap, None

    # ------------------------------------------------------------------ #
    #  Snapshot (used as branch_payload seed + revision snapshots)
    # ------------------------------------------------------------------ #

    @staticmethod
    def snapshot_roadmap(roadmap):
        """Serialize a single Roadmap to the canonical v2 JSON shape.

        Used both as the initial branch_payload when a lock is acquired
        and as the `snapshot` field on RoadmapRevision when an explicit
        save merges. Shape is versioned via `payload_version` so the
        merge service can refuse unknown versions and so future
        migrations can rewrite older snapshots.
        """
        from trophies.models import (
            RoadmapEditLock, RoadmapStep, RoadmapStepTrophy, TrophyGuide,
        )

        # Re-fetch with prefetches to guarantee a tight snapshot pass even
        # when the caller didn't pre-build the prefetch chain.
        rm = (
            type(roadmap).objects
            .filter(pk=roadmap.pk)
            .select_related('concept_trophy_group')
            .prefetch_related(
                Prefetch(
                    'steps',
                    queryset=RoadmapStep.objects.prefetch_related(
                        Prefetch(
                            'step_trophies',
                            queryset=RoadmapStepTrophy.objects.order_by('order'),
                        )
                    ).order_by('order'),
                ),
                Prefetch(
                    'trophy_guides',
                    queryset=TrophyGuide.objects.order_by('order', 'trophy_id'),
                ),
            )
            .first()
        )
        if rm is None:
            rm = roadmap

        return {
            'payload_version': RoadmapEditLock.PAYLOAD_VERSION,
            'roadmap_id': rm.id,
            'concept_id': rm.concept_id,
            'concept_trophy_group_id': rm.concept_trophy_group_id,
            'trophy_group_id': rm.concept_trophy_group.trophy_group_id,
            'status': rm.status,
            'general_tips': rm.general_tips,
            'youtube_url': rm.youtube_url,
            'difficulty': rm.difficulty,
            'estimated_hours': rm.estimated_hours,
            'min_playthroughs': rm.min_playthroughs,
            'created_by_id': rm.created_by_id,
            'last_edited_by_id': rm.last_edited_by_id,
            'steps': [
                {
                    'id': step.id,
                    'title': step.title,
                    'description': step.description,
                    'youtube_url': step.youtube_url,
                    'order': step.order,
                    'gallery_images': list(step.gallery_images or []),
                    'created_by_id': step.created_by_id,
                    'last_edited_by_id': step.last_edited_by_id,
                    'trophy_ids': [st.trophy_id for st in step.step_trophies.all()],
                }
                for step in rm.steps.all()
            ],
            'trophy_guides': [
                {
                    'id': tg.id,
                    'trophy_id': tg.trophy_id,
                    'body': tg.body,
                    'order': tg.order,
                    'is_missable': tg.is_missable,
                    'is_online': tg.is_online,
                    'is_unobtainable': tg.is_unobtainable,
                    'phase': tg.phase or '',
                    'gallery_images': list(tg.gallery_images or []),
                    'created_by_id': tg.created_by_id,
                    'last_edited_by_id': tg.last_edited_by_id,
                }
                for tg in rm.trophy_guides.all()
            ],
        }

    # ------------------------------------------------------------------ #
    #  Workshop summary (staff CTA on game-detail)
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_workshop_summary(roadmap, game, viewer_profile=None):
        """Compute the staff-facing operational summary for a roadmap.

        Args:
            roadmap: A Roadmap instance, or None for "no roadmap yet".
            game: The Game whose detail page is rendering. Used to resolve
                trophy counts per CTG (concept stacks share trophy lists, so
                any game in the concept gives accurate totals).
            viewer_profile: The current viewer's Profile, used to compute
                their open mentions. May be None.
        """
        from collections import Counter
        from trophies.models import RoadmapEditLock, RoadmapNote
        from trophies.services.roadmap_note_service import parse_mention_usernames

        if roadmap is None:
            return {
                'has_roadmap': False,
                'status': None,
                'is_published': False,
            }

        try:
            active_lock = roadmap.edit_lock
            if active_lock and active_lock.is_expired():
                active_lock = None
        except RoadmapEditLock.DoesNotExist:
            active_lock = None

        # Trophy count denominator for the CTG this roadmap covers.
        trophy_counts_by_group = dict(Counter(
            game.trophies.values_list('trophy_group_id', flat=True)
        ))
        ctg_trophy_total = trophy_counts_by_group.get(
            roadmap.concept_trophy_group.trophy_group_id, 0,
        )

        METADATA_FIELDS = (
            'difficulty', 'estimated_hours', 'min_playthroughs',
        )
        step_count = roadmap.steps.count()
        guide_count = roadmap.trophy_guides.count()
        metadata_filled = sum(
            1 for f in METADATA_FIELDS if getattr(roadmap, f, None) not in (None, 0)
        )

        # Notes summary. "My open mentions" re-parses each open note body
        # for the viewer's handle. Cheap at typical guide volumes (sub-50
        # notes); avoids a structured mentions table.
        open_notes = list(
            RoadmapNote.objects
            .filter(roadmap=roadmap, status=RoadmapNote.STATUS_OPEN)
            .only('body')
        )
        my_mentions = 0
        if viewer_profile is not None and viewer_profile.psn_username:
            handle = viewer_profile.psn_username.lower()
            for note in open_notes:
                if handle in {h.lower() for h in parse_mention_usernames(note.body)}:
                    my_mentions += 1

        return {
            'has_roadmap': True,
            'status': roadmap.status,
            'is_published': roadmap.status == 'published',
            'updated_at': roadmap.updated_at,
            'created_at': roadmap.created_at,
            'active_lock': active_lock,
            'contributors': roadmap.contributors(),
            'ctg': roadmap.concept_trophy_group,
            'step_count': step_count,
            'guide_count': guide_count,
            'trophy_total': ctg_trophy_total,
            'guide_pct': round(guide_count / ctg_trophy_total * 100) if ctg_trophy_total else 0,
            'metadata_filled': metadata_filled,
            'metadata_total': len(METADATA_FIELDS),
            'has_general_tips': bool(roadmap.general_tips),
            'open_notes_count': len(open_notes),
            'my_open_mentions': my_mentions,
        }
