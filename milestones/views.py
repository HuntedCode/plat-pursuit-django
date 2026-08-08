"""The /milestones/ page — the account-wide "Hall of Records".

A thin view over `build_milestones_context` (milestones/page.py): the heavy lifting is the whale-safe read-
model assembly there. Anonymous / unlinked viewers get the ladders as a preview (no progress).
"""
from core.services.tracking import track_page_view
from django.conf import settings
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from .page import build_demo_context, build_milestones_context


class MilestoneListView(TemplateView):
    template_name = 'milestones/milestone_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = (
            getattr(self.request.user, 'profile', None)
            if self.request.user.is_authenticated else None
        )
        # Dev/staff-only: ?preview renders the page with fabricated data (writes nothing) so the finished
        # states -- full ladders, maxed foil, a rare feat -- can be reviewed without earning anything.
        if self.request.GET.get('preview') and (settings.DEBUG or self.request.user.is_staff):
            context.update(build_demo_context(profile))
        else:
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
