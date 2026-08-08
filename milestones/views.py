"""The /milestones/ page — the account-wide "Hall of Records".

A thin view over `build_milestones_context` (milestones/page.py): the heavy lifting is the whale-safe read-
model assembly there. Anonymous / unlinked viewers get the ladders as a preview (no progress).
"""
from core.services.tracking import track_page_view
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from .page import build_milestones_context


class MilestoneListView(TemplateView):
    template_name = 'milestones/milestone_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = (
            getattr(self.request.user, 'profile', None)
            if self.request.user.is_authenticated else None
        )
        context.update(build_milestones_context(profile))
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Milestones'},
        ]
        context['seo_description'] = (
            "Track your long-term PlayStation trophy-hunting milestones on Platinum Pursuit — "
            "platinums, trophies, completions, and more."
        )
        track_page_view('milestones_list', 'list', self.request)
        return context
