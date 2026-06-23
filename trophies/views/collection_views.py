"""The Collection view: the Pursuer's badge album (the Binder Surface mount).

`/my-pursuit/collection/` renders the viewer's own badge collection as framed cards in a
binder -- earned badges framed, earnable ones as named slots. Requires a linked profile.
Page data is assembled by `collection_service.build_collection_context` (read-only,
whale-safe). This is the personal album, NOT the all-badges browse or a badge detail page.
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from trophies.mixins import ProfileHotbarMixin
from trophies.services.collection_service import build_collection_context


class CollectionView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """The Pursuer's badge collection album. Linked-profile gated; renders the viewer's own."""
    template_name = 'trophies/collection.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to start your Pursuit.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_collection_context(
            self.request.user.profile, sort=self.request.GET.get('sort', ''),
        ))
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Collection'},
        ]
        context['seo_title'] = 'Your Collection - Platinum Pursuit'
        return context
