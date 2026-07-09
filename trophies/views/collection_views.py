"""The Collection view: the Pursuer's badge album (the Binder Surface mount).

`/my-pursuit/collection/` renders the viewer's own badge collection as framed cards in a
binder -- earned badges framed, earnable ones as named slots. Requires a linked profile.
Page data is assembled by `collection_service.build_collection_context` (read-only,
whale-safe). This is the personal album, NOT the all-badges browse or a badge detail page.
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseNotFound
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView

from trophies.mixins import ProfileHotbarMixin
from trophies.models import Badge
from trophies.services.collection_service import build_collection_context
from trophies.services.frame_service import build_badge_frame


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
        context['dev_mint'] = settings.DEBUG   # dev-only "replay mint ceremony" button (never ships to prod)
        return context


class CollectionBadgeModalView(LoginRequiredMixin, View):
    """Detail modal for one badge (the Case's 'pick it up'): the medallion big + its full stats.
    Fetched on tap so the grid stays light -- one badge, single-hero stats. Linked-profile gated."""

    def get(self, request, badge_id):
        profile = getattr(request.user, 'profile', None)
        if not profile or not profile.is_linked:
            return HttpResponseNotFound()   # explicit 404 (the project's handler404 renders at 200)
        badge = (
            Badge.objects.filter(id=badge_id, is_live=True)
            .select_related(
                'base_badge', 'franchise', 'collection', 'developer', 'funded_by', 'submitted_by',
                'base_badge__franchise', 'base_badge__collection',
                'base_badge__developer', 'base_badge__funded_by', 'base_badge__submitted_by',
            ).first()
        )
        if badge is None:
            return HttpResponseNotFound()
        frame = build_badge_frame(badge, profile)   # single hero: full stats + live rank/XP
        frame['dom_id'] = f'card-{badge.id}'
        frame['series_slug'] = badge.series_slug
        frame['badge_id'] = badge.id
        frame['owner_name'] = profile.display_psn_username or profile.psn_username   # engraved on the earned base
        return render(request, 'components/collection_badge_modal.html', {'frame': frame})
