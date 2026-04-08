"""
Roadmap editor views (staff-only).

Provides the dedicated roadmap editor page for staff to author
game guides with steps, tips, trophy associations, and YouTube embeds.
"""
import logging

from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.utils.decorators import method_decorator
from django.views.generic import DetailView

from trophies.mixins import ProfileHotbarMixin
from trophies.models import Game
from trophies.services.roadmap_service import RoadmapService

logger = logging.getLogger('psn_api')


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
                tg.trophy_id: tg.body
                for tg in tab.trophy_guides.all()
            }
            tabs_data.append({
                'id': tab.id,
                'trophy_group_id': ctg.trophy_group_id,
                'display_name': ctg.display_name,
                'general_tips': tab.general_tips,
                'youtube_url': tab.youtube_url,
                'steps': steps_data,
                'trophy_guides': trophy_guides_data,
            })
        context['tabs_data'] = tabs_data

        # Breadcrumb
        context['breadcrumb'] = [
            {'label': 'Games', 'url': '/games/'},
            {'label': game.title_name, 'url': f'/games/{game.np_communication_id}/'},
            {'label': 'Edit Roadmap'},
        ]

        context['game'] = game
        context['concept'] = concept

        return context
