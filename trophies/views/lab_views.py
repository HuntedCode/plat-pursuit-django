"""The Lab view: the Pursuer's element identity + the Research (Projects) browse, merged.

`/lab/` renders the viewer's own elements/families (the periodic table, the family radar,
per-element detail) AND the former Research Panel folded in as a "Projects" tab -- so the
reward loop (accept a Project -> its elements level up) lives on one surface. Linked-profile
gated (the whole surface is personal; the old public /research-panel/ 301s to /lab/?view=projects).

Zones: the Pursuer hero + the element experience + the Projects browse + the pending-rewards rail.
Page data: `lab_service.build_lab_context` + `research_panel_service.build_research_panel_context`.
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from trophies.mixins import ProfileHotbarMixin
from trophies.services.lab_service import build_lab_context
from trophies.services.research_panel_service import build_research_panel_context
from trophies.util_modules.constants import CONTRACT_XP_TOTAL

# The internal tabs a `?view=` query may deep-link to (match the template's data-view values).
_LAB_VIEWS = frozenset({'table', 'radar', 'projects'})


class LabView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """The Pursuer's Lab. Linked-profile gated; renders the viewer's element identity + the
    Projects (Research) browse + the pending-rewards rail on one surface."""
    template_name = 'trophies/lab.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to start your Pursuit.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile
        # The element identity (hero + periodic table + radar).
        context.update(build_lab_context(profile))
        # The Research browse, folded in as the Projects tab (linked viewer -> real per-project status).
        research = build_research_panel_context(profile)
        context.update(research)
        context['profile'] = profile
        context['viewer_has_linked_profile'] = True
        context['xp_total'] = CONTRACT_XP_TOTAL
        # Pending-rewards rail: derived from the SAME projects the tab renders, so the rail's count
        # can never disagree with the Projects tab's "Accept all (N)" / the visible claimable cards.
        claimable_projects = [p for p in research.get('projects', []) if p.get('status') == 'claimable']
        context['claimable'] = {
            'count': len(claimable_projects),
            'total_xp': sum(p.get('xp_total', 0) for p in claimable_projects),
        }
        # Active tab on load: ?view=projects deep-links the Research browse (e.g. the old URL).
        requested = self.request.GET.get('view')
        context['active_view'] = requested if requested in _LAB_VIEWS else 'table'
        # Own breadcrumb + title win over whatever the Research context set.
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'The Lab'},
        ]
        context['seo_title'] = 'The Lab - Platinum Pursuit'
        return context
