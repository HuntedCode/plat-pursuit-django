"""The Career view: the Pursuer's job identity + the Contracts (job board) browse, merged.

`/career/` renders the viewer's own jobs/disciplines (the skills grid, the discipline radar,
per-job detail) AND the former Research Panel folded in as a "Contracts" tab -- so the
reward loop (accept a Contract -> its jobs level up) lives on one surface. Linked-profile
gated (the whole surface is personal).

Zones: the Pursuer hero + the jobs experience + the Contracts browse + the pending-rewards rail.
Page data: `career_service.build_career_context` + `contracts_service.build_contracts_context`.
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from trophies.mixins import ProfileHotbarMixin
from trophies.services.career_service import build_career_context
from trophies.services.contracts_service import build_contracts_context
from trophies.util_modules.constants import CONTRACT_XP_TOTAL

# The internal tabs a `?view=` query may deep-link to (match the template's data-view values).
_CAREER_VIEWS = frozenset({'jobs', 'radar', 'contracts'})


class CareerView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """The Pursuer's Career. Linked-profile gated; renders the viewer's job identity + the
    Contracts (job board) browse + the pending-rewards rail on one surface."""
    template_name = 'trophies/career.html'

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
        # The job identity (hero + skills grid + radar).
        context.update(build_career_context(profile))
        # The Contracts board, folded in as the Contracts tab (linked viewer -> real per-contract status).
        contracts_ctx = build_contracts_context(profile)
        context.update(contracts_ctx)
        context['profile'] = profile
        context['viewer_has_linked_profile'] = True
        context['xp_total'] = CONTRACT_XP_TOTAL
        # Pending-rewards rail: derived from the SAME contracts the tab renders, so the rail's count
        # can never disagree with the Contracts tab's "Accept all (N)" / the visible claimable cards.
        claimable_contracts = [p for p in contracts_ctx.get('contracts', []) if p.get('status') == 'claimable']
        context['claimable'] = {
            'count': len(claimable_contracts),
            'total_xp': sum(p.get('xp_total', 0) for p in claimable_contracts),
        }
        # Active tab on load: ?view=contracts deep-links the Contracts board.
        requested = self.request.GET.get('view')
        context['active_view'] = requested if requested in _CAREER_VIEWS else 'jobs'
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Career'},
        ]
        context['seo_title'] = 'Career - Platinum Pursuit'
        return context
