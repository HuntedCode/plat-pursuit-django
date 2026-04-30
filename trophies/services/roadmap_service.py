"""
Roadmap system service layer.

Handles all business logic for staff-authored game roadmaps:
CRUD for tabs, steps, step-trophy associations, and trophy guides.
"""
import logging
import re

from django.db import transaction
from django.db.models import Count, Prefetch

logger = logging.getLogger('psn_api')


class RoadmapService:
    """Handles roadmap operations for staff-authored game guides."""

    YOUTUBE_PATTERN = re.compile(
        r'^https?://(?:www\.)?(?:youtube\.com|youtu\.be)/.+$'
    )

    # ------------------------------------------------------------------ #
    #  Retrieval
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_roadmap_for_display(concept):
        """Get a published roadmap with all nested data prefetched for rendering.

        Returns the Roadmap instance or None if no published roadmap exists.
        """
        from trophies.models import (
            Roadmap, RoadmapTab, RoadmapStep, RoadmapStepTrophy, TrophyGuide,
        )

        try:
            return (
                Roadmap.objects
                .filter(concept=concept, status='published')
                .select_related('edit_lock', 'edit_lock__holder')
                .prefetch_related(
                    Prefetch(
                        'tabs',
                        queryset=RoadmapTab.objects.select_related(
                            'concept_trophy_group'
                        ).prefetch_related(
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
                                queryset=TrophyGuide.objects.order_by('trophy_id'),
                            ),
                        ),
                    )
                )
                .first()
            )
        except Roadmap.DoesNotExist:
            return None

    @staticmethod
    def get_roadmap_for_preview(concept):
        """Get a roadmap for staff preview regardless of publish status.

        Same prefetching as get_roadmap_for_display but without the
        status='published' filter. Used for ?preview=true on game detail page.
        """
        from trophies.models import (
            Roadmap, RoadmapTab, RoadmapStep, RoadmapStepTrophy, TrophyGuide,
        )

        return (
            Roadmap.objects
            .filter(concept=concept)
            .select_related('edit_lock', 'edit_lock__holder')
            .prefetch_related(
                Prefetch(
                    'tabs',
                    queryset=RoadmapTab.objects.select_related(
                        'concept_trophy_group'
                    ).prefetch_related(
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
                            queryset=TrophyGuide.objects.order_by('trophy_id'),
                        ),
                    ),
                )
            )
            .first()
        )

    @staticmethod
    def get_roadmap_for_editor(concept):
        """Get or create a roadmap with all nested data for the editor.

        Unlike get_roadmap_for_display, this returns draft roadmaps too
        and auto-creates the Roadmap + tabs if they don't exist.
        """
        from trophies.models import (
            Roadmap, RoadmapTab, RoadmapStep, RoadmapStepTrophy, TrophyGuide,
        )

        roadmap, _ = Roadmap.objects.get_or_create(concept=concept)

        # Auto-create tabs for any ConceptTrophyGroups that don't have one yet
        existing_ctg_ids = set(
            roadmap.tabs.values_list('concept_trophy_group_id', flat=True)
        )
        ctgs = concept.concept_trophy_groups.all()
        new_tabs = [
            RoadmapTab(roadmap=roadmap, concept_trophy_group=ctg)
            for ctg in ctgs if ctg.id not in existing_ctg_ids
        ]
        if new_tabs:
            RoadmapTab.objects.bulk_create(new_tabs)

        # Re-fetch with prefetches
        return (
            Roadmap.objects
            .filter(pk=roadmap.pk)
            .prefetch_related(
                Prefetch(
                    'tabs',
                    queryset=RoadmapTab.objects.select_related(
                        'concept_trophy_group'
                    ).prefetch_related(
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
                            queryset=TrophyGuide.objects.order_by('trophy_id'),
                        ),
                    ),
                )
            )
            .first()
        )

    # ------------------------------------------------------------------ #
    #  Detail Page Retrieval
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_tab_prefetch():
        """Shared prefetch chain for a single RoadmapTab's children."""
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
                queryset=TrophyGuide.objects.order_by('trophy_id'),
            ),
        ]

    @staticmethod
    def get_tab_for_display(concept, trophy_group_id='default'):
        """Get a single published roadmap tab with full prefetch for the detail page.

        Returns:
            tuple: (RoadmapTab or None, Roadmap or None)
        """
        from trophies.models import Roadmap, RoadmapTab

        roadmap = (
            Roadmap.objects
            .filter(concept=concept, status='published')
            .first()
        )
        if not roadmap:
            return None, None

        tab = (
            RoadmapTab.objects
            .filter(
                roadmap=roadmap,
                concept_trophy_group__trophy_group_id=trophy_group_id,
            )
            .select_related('concept_trophy_group')
            .prefetch_related(*RoadmapService._build_tab_prefetch())
            .first()
        )
        return tab, roadmap

    @staticmethod
    def apply_branch_overlay(tab, branch_payload):
        """Mutate an in-memory RoadmapTab to reflect uncommitted branch state.

        Used by author preview to show unsaved edits without merging them
        to live records. Replaces the tab's prefetched `steps` and
        `trophy_guides` caches with payload-derived instances and updates
        scalar tab fields. Steps that exist only in the branch (id=null in
        payload, treated as new) are returned as transient, unsaved
        RoadmapStep instances; deleted steps are dropped from the list.

        Safe to call only on a tab that was prefetched via
        `_build_tab_prefetch()`. The tab and its children are NEVER
        persisted as a side effect.

        Args:
            tab: A RoadmapTab instance with prefetched steps + trophy_guides.
            branch_payload: dict matching `RoadmapEditLock.branch_payload`.

        Returns:
            The same `tab` instance, mutated in place.
        """
        from trophies.models import RoadmapStep, RoadmapStepTrophy, TrophyGuide
        from trophies.services.roadmap_merge_service import (
            EDITOR_TAB_FIELDS, PUBLISHER_TAB_FIELDS, WRITER_TAB_FIELDS,
        )

        if not isinstance(branch_payload, dict):
            return tab
        payload_tabs = branch_payload.get('tabs')
        if not isinstance(payload_tabs, list):
            return tab

        tab_payload = next(
            (t for t in payload_tabs if isinstance(t, dict) and t.get('id') == tab.id),
            None,
        )
        if tab_payload is None:
            return tab

        for fld in WRITER_TAB_FIELDS + EDITOR_TAB_FIELDS + PUBLISHER_TAB_FIELDS:
            if fld in tab_payload:
                value = tab_payload[fld]
                if value is None and isinstance(getattr(tab, fld, None), str):
                    value = ''
                setattr(tab, fld, value)

        live_steps_by_id = {s.id: s for s in tab.steps.all()}
        overlay_steps = []
        # Negative synthetic IDs for any new payload step that didn't carry
        # one (the editor sends id=null on the wire). These IDs need to be
        # unique across the overlay so HTML anchors and progress dict keys
        # don't collide.
        synthetic_id = -1_000_000
        for index, step_payload in enumerate(tab_payload.get('steps', [])):
            sid = step_payload.get('id')
            if isinstance(sid, int) and sid in live_steps_by_id:
                step = live_steps_by_id[sid]
            else:
                synthetic_id -= 1
                step = RoadmapStep(tab=tab, id=synthetic_id)

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

        live_guides_by_id = {g.id: g for g in tab.trophy_guides.all()}
        overlay_guides = []
        for guide_payload in tab_payload.get('trophy_guides', []):
            gid = guide_payload.get('id')
            if isinstance(gid, int) and gid in live_guides_by_id:
                guide = live_guides_by_id[gid]
            else:
                guide = TrophyGuide(
                    tab=tab,
                    trophy_id=int(guide_payload.get('trophy_id') or 0),
                )
            if 'trophy_id' in guide_payload and guide_payload['trophy_id'] is not None:
                guide.trophy_id = int(guide_payload['trophy_id'])
            if 'body' in guide_payload:
                guide.body = guide_payload['body'] or ''
            for flag in ('is_missable', 'is_online', 'is_unobtainable'):
                if flag in guide_payload:
                    setattr(guide, flag, bool(guide_payload[flag]))
            if 'order' in guide_payload:
                guide.order = guide_payload['order']
            if 'gallery_images' in guide_payload:
                guide.gallery_images = list(guide_payload.get('gallery_images') or [])
            overlay_guides.append(guide)

        if not hasattr(tab, '_prefetched_objects_cache'):
            tab._prefetched_objects_cache = {}
        tab._prefetched_objects_cache['steps'] = overlay_steps
        tab._prefetched_objects_cache['trophy_guides'] = overlay_guides
        return tab

    @staticmethod
    def get_tab_for_preview(concept, trophy_group_id='default'):
        """Get a roadmap tab for staff preview regardless of publish status.

        Returns:
            tuple: (RoadmapTab or None, Roadmap or None)
        """
        from trophies.models import Roadmap, RoadmapTab

        roadmap = Roadmap.objects.filter(concept=concept).first()
        if not roadmap:
            return None, None

        tab = (
            RoadmapTab.objects
            .filter(
                roadmap=roadmap,
                concept_trophy_group__trophy_group_id=trophy_group_id,
            )
            .select_related('concept_trophy_group')
            .prefetch_related(*RoadmapService._build_tab_prefetch())
            .first()
        )
        return tab, roadmap

    @staticmethod
    def get_available_tabs(concept, include_drafts=False):
        """Get all CTGs with roadmap tab presence info for DLC navigation.

        Returns a list of dicts with 'ctg', 'has_content', 'trophy_group_id',
        'step_count', and 'guide_count' for each tab.
        """
        from trophies.models import Roadmap, RoadmapTab

        filters = {'concept': concept}
        if not include_drafts:
            filters['status'] = 'published'
        roadmap = Roadmap.objects.filter(**filters).first()
        if not roadmap:
            return []

        tabs = (
            RoadmapTab.objects
            .filter(roadmap=roadmap)
            .select_related('concept_trophy_group')
            .annotate(
                step_count=Count('steps'),
                guide_count=Count('trophy_guides'),
            )
            .order_by(
                'concept_trophy_group__sort_order',
                'concept_trophy_group__trophy_group_id',
            )
        )
        return [
            {
                'ctg': t.concept_trophy_group,
                'has_content': (t.step_count + t.guide_count) > 0,
                'trophy_group_id': t.concept_trophy_group.trophy_group_id,
                'step_count': t.step_count,
                'guide_count': t.guide_count,
            }
            for t in tabs
        ]

    @staticmethod
    def compute_progress(tab, profile_earned):
        """Compute per-step and overall progress from prefetched tab data.

        Args:
            tab: A RoadmapTab with prefetched steps -> step_trophies
            profile_earned: Dict of trophy_id -> {'earned': bool, ...}

        Returns:
            dict with 'steps' list, 'total_earned', 'total_trophies', 'percentage'
        """
        steps_progress = []
        total_trophies = 0
        total_earned = 0

        for step in tab.steps.all():
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
    #  Tab Operations
    # ------------------------------------------------------------------ #

    @staticmethod
    def update_tab(tab_id, general_tips=None, youtube_url=None,
                   difficulty=None, estimated_hours=None, missable_count=None,
                   online_required=None, min_playthroughs=None):
        """Update a roadmap tab's content and/or metadata fields.

        Returns:
            tuple: (RoadmapTab instance, error_message or None)
        """
        from trophies.models import RoadmapTab

        try:
            tab = RoadmapTab.objects.get(pk=tab_id)
        except RoadmapTab.DoesNotExist:
            return None, 'Tab not found.'

        update_fields = []

        if general_tips is not None:
            tab.general_tips = general_tips
            update_fields.append('general_tips')

        if youtube_url is not None:
            youtube_url = youtube_url.strip()
            if youtube_url and not RoadmapService.YOUTUBE_PATTERN.match(youtube_url):
                return None, 'Invalid YouTube URL. Must be a youtube.com or youtu.be link.'
            tab.youtube_url = youtube_url
            update_fields.append('youtube_url')

        # Guide metadata fields
        if difficulty is not None:
            # Allow clearing by passing empty string or None-like value
            tab.difficulty = int(difficulty) if difficulty != '' else None
            update_fields.append('difficulty')

        if estimated_hours is not None:
            tab.estimated_hours = int(estimated_hours) if estimated_hours != '' else None
            update_fields.append('estimated_hours')

        if missable_count is not None:
            tab.missable_count = int(missable_count) if missable_count != '' else 0
            update_fields.append('missable_count')

        if online_required is not None:
            tab.online_required = bool(online_required)
            update_fields.append('online_required')

        if min_playthroughs is not None:
            tab.min_playthroughs = int(min_playthroughs) if min_playthroughs != '' else 1
            update_fields.append('min_playthroughs')

        if update_fields:
            tab.save(update_fields=update_fields)
            tab.roadmap.save(update_fields=['updated_at'])

        return tab, None

    # ------------------------------------------------------------------ #
    #  Step Operations
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def create_step(tab_id, title, description='', youtube_url=''):
        """Create a new step at the end of a tab's step list.

        Returns:
            tuple: (RoadmapStep instance, error_message or None)
        """
        from trophies.models import RoadmapTab, RoadmapStep

        try:
            tab = RoadmapTab.objects.select_related('roadmap').get(pk=tab_id)
        except RoadmapTab.DoesNotExist:
            return None, 'Tab not found.'

        if youtube_url and not RoadmapService.YOUTUBE_PATTERN.match(youtube_url.strip()):
            return None, 'Invalid YouTube URL.'

        max_order = tab.steps.order_by('-order').values_list('order', flat=True).first()
        next_order = (max_order or 0) + 1

        step = RoadmapStep.objects.create(
            tab=tab, title=title, description=description,
            youtube_url=youtube_url.strip() if youtube_url else '', order=next_order
        )
        tab.roadmap.save(update_fields=['updated_at'])
        return step, None

    @staticmethod
    def update_step(step_id, title=None, description=None, youtube_url=None):
        """Update a step's title, description, and/or YouTube URL.

        Returns:
            tuple: (RoadmapStep instance, error_message or None)
        """
        from trophies.models import RoadmapStep

        try:
            step = RoadmapStep.objects.select_related('tab__roadmap').get(pk=step_id)
        except RoadmapStep.DoesNotExist:
            return None, 'Step not found.'

        update_fields = []
        if title is not None:
            step.title = title
            update_fields.append('title')
        if description is not None:
            step.description = description
            update_fields.append('description')
        if youtube_url is not None:
            youtube_url = youtube_url.strip()
            if youtube_url and not RoadmapService.YOUTUBE_PATTERN.match(youtube_url):
                return None, 'Invalid YouTube URL.'
            step.youtube_url = youtube_url
            update_fields.append('youtube_url')

        if update_fields:
            step.save(update_fields=update_fields)
            step.tab.roadmap.save(update_fields=['updated_at'])

        return step, None

    @staticmethod
    @transaction.atomic
    def delete_step(step_id):
        """Delete a step and its associated trophies.

        Returns:
            tuple: (True, error_message or None)
        """
        from trophies.models import RoadmapStep

        try:
            step = RoadmapStep.objects.select_related('tab__roadmap').get(pk=step_id)
        except RoadmapStep.DoesNotExist:
            return False, 'Step not found.'

        roadmap = step.tab.roadmap
        step.delete()
        roadmap.save(update_fields=['updated_at'])
        return True, None

    @staticmethod
    @transaction.atomic
    def reorder_steps(tab_id, step_ids):
        """Reorder steps within a tab.

        Args:
            tab_id: RoadmapTab PK
            step_ids: List of step PKs in desired order

        Returns:
            tuple: (True, error_message or None)
        """
        from trophies.models import RoadmapTab, RoadmapStep

        try:
            tab = RoadmapTab.objects.select_related('roadmap').get(pk=tab_id)
        except RoadmapTab.DoesNotExist:
            return False, 'Tab not found.'

        existing_ids = set(tab.steps.values_list('id', flat=True))
        if set(step_ids) != existing_ids:
            return False, 'Step IDs do not match existing steps for this tab.'

        for order, step_id in enumerate(step_ids):
            RoadmapStep.objects.filter(pk=step_id, tab=tab).update(order=order)

        tab.roadmap.save(update_fields=['updated_at'])
        return True, None

    # ------------------------------------------------------------------ #
    #  Step Trophy Associations
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def set_step_trophies(step_id, trophy_ids):
        """Replace a step's trophy associations.

        Args:
            step_id: RoadmapStep PK
            trophy_ids: List of trophy_id integers in desired order

        Returns:
            tuple: (True, error_message or None)
        """
        from trophies.models import RoadmapStep, RoadmapStepTrophy

        try:
            step = RoadmapStep.objects.select_related('tab__roadmap').get(pk=step_id)
        except RoadmapStep.DoesNotExist:
            return False, 'Step not found.'

        # Clear existing and recreate
        step.step_trophies.all().delete()
        RoadmapStepTrophy.objects.bulk_create([
            RoadmapStepTrophy(step=step, trophy_id=tid, order=i)
            for i, tid in enumerate(trophy_ids)
        ])

        step.tab.roadmap.save(update_fields=['updated_at'])
        return True, None

    # ------------------------------------------------------------------ #
    #  Trophy Guide Operations
    # ------------------------------------------------------------------ #

    @staticmethod
    def create_or_update_trophy_guide(tab_id, trophy_id, body,
                                      is_missable=None, is_online=None,
                                      is_unobtainable=None):
        """Create or update a trophy guide within a tab.

        Returns:
            tuple: (TrophyGuide instance, error_message or None)
        """
        from trophies.models import RoadmapTab, TrophyGuide

        try:
            tab = RoadmapTab.objects.select_related('roadmap').get(pk=tab_id)
        except RoadmapTab.DoesNotExist:
            return None, 'Tab not found.'

        defaults = {'body': body}
        if is_missable is not None:
            defaults['is_missable'] = is_missable
        if is_online is not None:
            defaults['is_online'] = is_online
        if is_unobtainable is not None:
            defaults['is_unobtainable'] = is_unobtainable

        guide, created = TrophyGuide.objects.update_or_create(
            tab=tab, trophy_id=trophy_id,
            defaults=defaults,
        )

        if not created and not body.strip():
            guide.delete()
            tab.roadmap.save(update_fields=['updated_at'])
            return None, None  # Deleted empty guide, not an error

        tab.roadmap.save(update_fields=['updated_at'])
        return guide, None

    @staticmethod
    def delete_trophy_guide(tab_id, trophy_id):
        """Delete a trophy guide.

        Returns:
            tuple: (True, error_message or None)
        """
        from trophies.models import RoadmapTab, TrophyGuide

        try:
            tab = RoadmapTab.objects.select_related('roadmap').get(pk=tab_id)
        except RoadmapTab.DoesNotExist:
            return False, 'Tab not found.'

        deleted, _ = TrophyGuide.objects.filter(tab=tab, trophy_id=trophy_id).delete()
        if not deleted:
            return False, 'Trophy guide not found.'

        tab.roadmap.save(update_fields=['updated_at'])
        return True, None

    # ------------------------------------------------------------------ #
    #  Publish / Unpublish
    # ------------------------------------------------------------------ #

    @staticmethod
    def publish_roadmap(roadmap_id):
        """Publish a roadmap (makes it visible on game detail page).

        Returns:
            tuple: (Roadmap instance, error_message or None)
        """
        from trophies.models import Roadmap

        try:
            roadmap = Roadmap.objects.get(pk=roadmap_id)
        except Roadmap.DoesNotExist:
            return None, 'Roadmap not found.'

        roadmap.status = 'published'
        roadmap.save(update_fields=['status', 'updated_at'])
        return roadmap, None

    @staticmethod
    def unpublish_roadmap(roadmap_id):
        """Unpublish a roadmap (hides from game detail page).

        Returns:
            tuple: (Roadmap instance, error_message or None)
        """
        from trophies.models import Roadmap

        try:
            roadmap = Roadmap.objects.get(pk=roadmap_id)
        except Roadmap.DoesNotExist:
            return None, 'Roadmap not found.'

        roadmap.status = 'draft'
        roadmap.save(update_fields=['status', 'updated_at'])
        return roadmap, None

    # ------------------------------------------------------------------ #
    #  Snapshot (canonical JSON serialization of a roadmap)
    # ------------------------------------------------------------------ #

    @staticmethod
    def snapshot_roadmap(roadmap):
        """Serialize a roadmap and all nested content to the canonical JSON shape.

        Used both as the initial branch_payload when a lock is acquired and as
        the `snapshot` field on RoadmapRevision when an explicit save merges.
        The shape is versioned via `payload_version` so the merge service can
        refuse unknown versions and migrations can rewrite older snapshots.
        """
        from trophies.models import (
            RoadmapEditLock, RoadmapTab, RoadmapStep, RoadmapStepTrophy, TrophyGuide,
        )
        from django.db.models import Prefetch

        tabs_qs = (
            roadmap.tabs
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
        )

        return {
            'payload_version': RoadmapEditLock.PAYLOAD_VERSION,
            'roadmap_id': roadmap.id,
            'status': roadmap.status,
            'tabs': [
                {
                    'id': tab.id,
                    'concept_trophy_group_id': tab.concept_trophy_group_id,
                    'general_tips': tab.general_tips,
                    'youtube_url': tab.youtube_url,
                    'difficulty': tab.difficulty,
                    'estimated_hours': tab.estimated_hours,
                    'missable_count': tab.missable_count,
                    'online_required': tab.online_required,
                    'min_playthroughs': tab.min_playthroughs,
                    'created_by_id': tab.created_by_id,
                    'last_edited_by_id': tab.last_edited_by_id,
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
                        for step in tab.steps.all()
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
                            'gallery_images': list(tg.gallery_images or []),
                            'created_by_id': tg.created_by_id,
                            'last_edited_by_id': tg.last_edited_by_id,
                        }
                        for tg in tab.trophy_guides.all()
                    ],
                }
                for tab in tabs_qs
            ],
        }

    @staticmethod
    def get_workshop_summary(roadmap, game, viewer_profile=None):
        """Compute the staff-facing operational summary for a roadmap.

        Surfaces the data the writing team wants to see on the game detail
        page without entering the editor: status, last edit, lock state,
        per-CTG coverage stats, contributor list, and notes counts.

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

        # Trophy counts per `trophy_group_id` string, sourced from the
        # current game's trophies. Concept stacks share trophy structure,
        # so counting on any one game gives the right denominator.
        trophy_counts_by_group = dict(Counter(
            game.trophies.values_list('trophy_group_id', flat=True)
        ))

        tabs = list(
            roadmap.tabs
            .select_related('concept_trophy_group')
            .prefetch_related('steps', 'trophy_guides')
        )

        METADATA_FIELDS = ('difficulty', 'estimated_hours', 'missable_count',
                           'online_required', 'min_playthroughs')
        tab_summaries = []
        total_steps = 0
        total_guides = 0
        total_trophies = 0
        for tab in tabs:
            step_count = tab.steps.count()
            guide_count = tab.trophy_guides.count()
            ctg_total = trophy_counts_by_group.get(
                tab.concept_trophy_group.trophy_group_id, 0
            )
            metadata_filled = sum(
                1 for f in METADATA_FIELDS if getattr(tab, f, None) not in (None, 0)
            )
            tab_summaries.append({
                'ctg': tab.concept_trophy_group,
                'step_count': step_count,
                'guide_count': guide_count,
                'trophy_total': ctg_total,
                'guide_pct': round(guide_count / ctg_total * 100) if ctg_total else 0,
                'metadata_filled': metadata_filled,
                'metadata_total': len(METADATA_FIELDS),
                'has_general_tips': bool(tab.general_tips),
            })
            total_steps += step_count
            total_guides += guide_count
            total_trophies += ctg_total

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
            'tabs': tab_summaries,
            'total_steps': total_steps,
            'total_guides': total_guides,
            'total_trophies': total_trophies,
            'overall_guide_pct': round(total_guides / total_trophies * 100) if total_trophies else 0,
            'open_notes_count': len(open_notes),
            'my_open_mentions': my_mentions,
        }
