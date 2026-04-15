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
