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
from django.http import HttpResponseNotFound
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView

from trophies.mixins import ProfileHotbarMixin
from trophies.services import contracts_service
from trophies.services.career_service import build_career_context
from trophies.services.contracts_service import build_contracts_context
from trophies.util_modules.constants import ALL_PLATFORMS, CONTRACT_XP_TOTAL

# The internal tabs a `?view=` query may deep-link to (match the template's data-view values).
_CAREER_VIEWS = frozenset({'jobs', 'radar', 'contracts'})
_VALID_PLATFORMS = frozenset(ALL_PLATFORMS)


def _page_num(raw):
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


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


class ContractsResultsView(LoginRequiredMixin, View):
    """Cards-only partial for the Contracts board: filtered/sorted/paginated in the DB. Serves both
    the filter-swap (page 1 -> replace the grid) and infinite scroll (page N -> append)."""

    def get(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile or not profile.is_linked:
            return HttpResponseNotFound()   # explicit 404 (the project's handler404 renders at 200)
        g = request.GET
        platforms = [p for p in g.getlist('platform') if p in _VALID_PLATFORMS] or None  # absent -> current-gen
        data = contracts_service.contracts_page(
            profile,
            disc_levels=contracts_service.discipline_levels(profile),
            page=_page_num(g.get('page')),
            q=g.get('q', '').strip(),
            status=g.get('status', ''),
            discipline=g.get('discipline', ''),
            job=g.get('job', ''),
            platforms=platforms,
            sort=g.get('sort', 'relevance'),
        )
        return render(request, 'trophies/partials/contracts/_results.html', {
            **data, 'profile': profile, 'disciplines': contracts_service.job_roster(),
        })


class ContractModalView(LoginRequiredMixin, View):
    """One contract's modal content, fetched lazily when a card's 'view details' opens."""

    def get(self, request, slug):
        profile = getattr(request.user, 'profile', None)
        if not profile or not profile.is_linked:   # the whole Career surface is linked-profile gated
            return HttpResponseNotFound()
        p = contracts_service.build_contract_modal(profile, slug)
        if p is None:
            return HttpResponseNotFound()   # explicit 404 so the fetch JS doesn't inject the 404 page
        return render(request, 'trophies/partials/contracts/_contract_modal.html',
                      {'p': p, 'profile': profile})
