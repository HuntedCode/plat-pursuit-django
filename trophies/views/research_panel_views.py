"""Research Panel view: the browse of Projects (curated Contracts) to pursue.

`/my-pursuit/research-panel/` lists live Projects, foregrounding each Project's games
(member Concepts) the way a badge stage does, plus the elements it levels, the fixed XP
reward, the Compound, and the viewer's per-Project status. Public/browsable: anonymous
or unlinked viewers see every Project as "available" (no per-user status); a linked
viewer gets their real status (pursuing / claimable / accepted). Page data is assembled
by `research_panel_service.build_research_panel_context`.
"""
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from trophies.mixins import ProfileHotbarMixin
from trophies.services.research_panel_service import build_research_panel_context
from trophies.util_modules.constants import CONTRACT_XP_TOTAL


class ResearchPanelView(ProfileHotbarMixin, TemplateView):
    """The Research Panel browse. Per-user status only for a linked viewer."""
    template_name = 'trophies/research_panel.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        viewer_profile = None
        if self.request.user.is_authenticated:
            profile = getattr(self.request.user, 'profile', None)
            if profile is not None and profile.is_linked:
                viewer_profile = profile
        context.update(build_research_panel_context(viewer_profile))
        # The reused badge game-card partial links to game_detail_with_profile when a
        # `profile` is in context (and plain game_detail otherwise).
        context['profile'] = viewer_profile
        context['viewer_has_linked_profile'] = viewer_profile is not None
        context['xp_total'] = CONTRACT_XP_TOTAL
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Research Panel'},
        ]
        context['seo_title'] = 'Research Panel - Platinum Pursuit'
        return context


class ResearchPanelCompareView(ProfileHotbarMixin, TemplateView):
    """Design comparison: the same real Projects rendered in both card treatments (compact
    vial vs large focal vial) so the look can be judged side by side. Unlisted direct link,
    same data path as the real page (read-only, whale-safe)."""
    template_name = 'trophies/research_panel_compare.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        viewer_profile = None
        if self.request.user.is_authenticated:
            profile = getattr(self.request.user, 'profile', None)
            if profile is not None and profile.is_linked:
                viewer_profile = profile
        data = build_research_panel_context(viewer_profile)
        context['projects'] = data['projects']
        context['profile'] = viewer_profile
        context['seo_title'] = 'Research Panel - Card Comparison'
        return context
