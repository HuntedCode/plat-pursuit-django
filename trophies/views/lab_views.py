"""The Lab view: the Pursuer's element identity ("your Platinum DNA").

`/my-pursuit/lab/` renders the viewer's own elements/families -- the periodic table,
the family radar, and per-element detail. Requires a linked profile. Page data is
assembled by `lab_service.build_lab_context`.

Zones: the Pursuer hero (identity at a glance) + the element experience.
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from trophies.mixins import ProfileHotbarMixin
from trophies.services.lab_service import build_lab_context


class LabView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """The Pursuer's Lab. Linked-profile gated; renders the viewer's own element identity."""
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
        context.update(build_lab_context(self.request.user.profile))
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'The Lab'},
        ]
        context['seo_title'] = 'The Lab - Platinum Pursuit'
        return context
