"""
Roadmap views: public detail page and staff-only editor.

The detail page provides a full guide experience with sticky TOC, scrollspy,
progress tracking, and per-DLC pages. The editor is staff-only for authoring
roadmap content (steps, tips, trophy associations, YouTube embeds, metadata).
"""
import logging

from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.utils.decorators import method_decorator
from django.views.generic import DetailView

from trophies.mixins import ProfileHotbarMixin
from trophies.models import EarnedTrophy, Game
from trophies.services.rating_service import RatingService
from trophies.services.roadmap_service import RoadmapService

logger = logging.getLogger('psn_api')


class RoadmapDetailView(ProfileHotbarMixin, DetailView):
    """Public roadmap detail page for a specific trophy group.

    Serves the full guide experience with sticky TOC, progress tracking,
    and guide metadata. Each DLC/trophy group gets its own page.
    Base game is served at /games/<id>/roadmap/, DLC at /games/<id>/roadmap/<group>/.
    """
    model = Game
    template_name = 'trophies/roadmap_detail.html'
    slug_field = 'np_communication_id'
    slug_url_kwarg = 'np_communication_id'

    def get_queryset(self):
        return super().get_queryset().select_related('concept')

    def get_object(self, queryset=None):
        game = super().get_object(queryset)
        if not game.concept:
            raise Http404("Game has no concept.")
        return game

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        game = self.object
        concept = game.concept
        user = self.request.user
        trophy_group_id = self.kwargs.get('trophy_group_id', 'default')

        # Staff preview support
        preview_mode = (
            self.request.GET.get('preview') == 'true'
            and user.is_authenticated
            and user.is_staff
        )
        context['roadmap_preview_mode'] = preview_mode

        # Fetch the specific tab + roadmap
        if preview_mode:
            tab, roadmap = RoadmapService.get_tab_for_preview(concept, trophy_group_id)
        else:
            tab, roadmap = RoadmapService.get_tab_for_display(concept, trophy_group_id)

        if not tab:
            raise Http404("Roadmap not found.")

        context['tab'] = tab
        context['roadmap'] = roadmap
        context['active_trophy_group_id'] = trophy_group_id

        # DLC navigation strip
        context['available_tabs'] = RoadmapService.get_available_tabs(
            concept, include_drafts=preview_mode
        )

        # Resolve trophy display data for trophies referenced in steps/guides
        roadmap_trophy_ids = set()
        for step in tab.steps.all():
            for st in step.step_trophies.all():
                roadmap_trophy_ids.add(st.trophy_id)
        for tg in tab.trophy_guides.all():
            roadmap_trophy_ids.add(tg.trophy_id)

        if roadmap_trophy_ids:
            context['roadmap_trophies'] = {
                t.trophy_id: t
                for t in game.trophies.filter(
                    trophy_group_id=trophy_group_id,
                    trophy_id__in=roadmap_trophy_ids,
                )
            }
        else:
            context['roadmap_trophies'] = {}

        # Profile earned data + progress computation
        profile_earned = {}
        if (user.is_authenticated and hasattr(user, 'profile')
                and user.profile and user.profile.is_linked):
            earned_qs = EarnedTrophy.objects.filter(
                profile=user.profile, trophy__game=game,
            ).select_related('trophy')
            profile_earned = {
                e.trophy.trophy_id: {
                    'earned': e.earned,
                    'earned_date_time': e.earned_date_time,
                }
                for e in earned_qs
            }
        context['profile_earned'] = profile_earned
        context['progress'] = RoadmapService.compute_progress(tab, profile_earned)

        # Community rating averages for this trophy group
        context['community_averages'] = (
            RatingService.get_cached_community_averages_for_group(
                concept, tab.concept_trophy_group
            )
        )

        # Header background (used in header card only, not full-page)
        context['header_bg_url'] = getattr(concept, 'bg_url', None) or ''

        # Breadcrumbs
        group_name = tab.concept_trophy_group.display_name
        context['breadcrumb'] = [
            {'text': 'Games', 'url': '/games/'},
            {'text': game.title_name, 'url': f'/games/{game.np_communication_id}/'},
            {'text': f'Roadmap: {group_name}'},
        ]

        # SEO
        step_count = len(tab.steps.all())
        guide_count = len(tab.trophy_guides.all())
        context['seo_description'] = (
            f"Trophy roadmap for {game.title_name} ({group_name}). "
            f"{step_count} steps, {guide_count} trophy guides."
        )

        context['game'] = game
        return context


@method_decorator(staff_member_required, name='dispatch')
class RoadmapEditorView(ProfileHotbarMixin, DetailView):
    """Staff-only roadmap editor page.

    Gets or creates a Roadmap for the game's Concept, auto-creates tabs
    for any ConceptTrophyGroups, and provides all trophies for the picker.
    """
    model = Game
    template_name = 'trophies/roadmap_edit.html'
    slug_field = 'np_communication_id'
    slug_url_kwarg = 'np_communication_id'

    def get_object(self, queryset=None):
        game = super().get_object(queryset)
        if not game.concept:
            raise Http404("Game has no concept.")
        return game

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        game = self.object
        concept = game.concept

        # Get or create roadmap with all nested data
        roadmap = RoadmapService.get_roadmap_for_editor(concept)
        context['roadmap'] = roadmap

        # Build trophy data for the picker, organized by trophy_group_id
        trophies_by_group = {}
        for trophy in game.trophies.order_by('trophy_group_id', 'trophy_id'):
            group_id = trophy.trophy_group_id
            if group_id not in trophies_by_group:
                trophies_by_group[group_id] = []
            trophies_by_group[group_id].append({
                'trophy_id': trophy.trophy_id,
                'name': trophy.trophy_name,
                'detail': trophy.trophy_detail or '',
                'type': trophy.trophy_type,
                'icon_url': trophy.trophy_icon_url or '',
            })
        context['trophies_by_group'] = trophies_by_group

        # Build tab data for JS initialization
        tabs_data = []
        for tab in roadmap.tabs.all():
            ctg = tab.concept_trophy_group
            steps_data = []
            for step in tab.steps.all():
                steps_data.append({
                    'id': step.id,
                    'title': step.title,
                    'description': step.description,
                    'youtube_url': step.youtube_url,
                    'order': step.order,
                    'trophy_ids': list(
                        step.step_trophies.order_by('order').values_list('trophy_id', flat=True)
                    ),
                })
            trophy_guides_data = {
                tg.trophy_id: {
                    'body': tg.body,
                    'is_missable': tg.is_missable,
                    'is_online': tg.is_online,
                    'is_unobtainable': tg.is_unobtainable,
                }
                for tg in tab.trophy_guides.all()
            }
            tabs_data.append({
                'id': tab.id,
                'trophy_group_id': ctg.trophy_group_id,
                'display_name': ctg.display_name,
                'general_tips': tab.general_tips,
                'youtube_url': tab.youtube_url,
                'difficulty': tab.difficulty,
                'estimated_hours': tab.estimated_hours,
                'missable_count': tab.missable_count,
                'online_required': tab.online_required,
                'min_playthroughs': tab.min_playthroughs,
                'steps': steps_data,
                'trophy_guides': trophy_guides_data,
            })
        context['tabs_data'] = tabs_data

        # Breadcrumb
        context['breadcrumb'] = [
            {'text': 'Games', 'url': '/games/'},
            {'text': game.title_name, 'url': f'/games/{game.np_communication_id}/'},
            {'text': 'Edit Roadmap'},
        ]

        context['game'] = game
        context['concept'] = concept

        return context
