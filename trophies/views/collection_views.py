"""The Collection view: the Pursuer's badge Gallery.

`/collection/` renders a single filter / sort / search wall of the badge editions (Legacy HD /
Ultra HD) the viewer has ENGAGED with -- held or in-progress -- with per-edition state derived
live (earned / in_progress / unearned). Requires a linked profile. Page data is assembled by
`collection_service.build_collection_context` (read-only, whale-BOUNDED -- the live per-edition
eval is scoped to the engaged series' catalog). This is the personal Gallery, NOT the all-badges
browse or a badge detail page.
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseNotFound
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView

from trophies.models import GroupBadge
from trophies.services.badge_detail_service import get_badge_detail
from trophies.services.collection_service import build_collection_context


class CollectionView(LoginRequiredMixin, TemplateView):
    """The Pursuer's badge Collection Gallery. Linked-profile gated; renders the viewer's own."""
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
    """Detail modal for one collection badge (the Gallery's 'pick it up'): the GROUP badge's medallion + the
    viewer's REAL per-group state, via badge_detail_service (same modal the badge pages use). `badge_id` is a
    GroupBadge id. Linked-profile gated."""

    def get(self, request, badge_id):
        profile = getattr(request.user, 'profile', None)
        if not profile or not profile.is_linked:
            return HttpResponseNotFound()   # explicit 404 (the project's handler404 renders at 200)
        gb = (
            GroupBadge.objects
            .select_related('series', 'series__artwork_source', 'series__franchise', 'series__collection', 'series__developer',
                            'series__funded_by', 'platform_group')
            .filter(id=badge_id, is_live=True).first()
        )
        if gb is None:
            return HttpResponseNotFound()
        detail = get_badge_detail(gb.series, profile)
        gv = next((g for g in detail.groups if g.group_badge.id == gb.id), None)
        if gv is None:
            return HttpResponseNotFound()
        return render(request, 'components/group_badge_modal.html', {
            # show_detail_link: only the COLLECTION modal offers a jump to the badge detail page (deep-linked
            # to this edition's tab). The badge-detail peek omits it -- you're already on that page.
            'gv': gv, 'series': gb.series, 'detail': detail, 'viewing_other': None, 'showcase': False,
            'show_detail_link': True,
        })
