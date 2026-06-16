"""Logbook view: the Pursuer's RPG-identity deep-dive.

`/my-pursuit/logbook/` is "who you've become" (the reflective counterpart to the
forward-looking Pursuit home). It requires a linked profile, since it renders the
viewer's own Pursuer. Page data is assembled by `logbook_service.build_logbook_context`.

Built zone by zone: the Pursuer hero (Pursuer card) + the Lab (element identity).
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from trophies.mixins import ProfileHotbarMixin
from trophies.services.logbook_service import build_logbook_context


class LogbookView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """The Pursuer's Logbook. Linked-profile gated; renders the viewer's own identity."""
    template_name = 'trophies/logbook.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to start your Pursuit.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_logbook_context(self.request.user.profile))
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Logbook'},
        ]
        context['seo_title'] = 'Your Logbook - Platinum Pursuit'
        return context
