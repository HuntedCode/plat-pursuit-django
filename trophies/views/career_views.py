"""The Career view: the Pursuer's job identity + the Contracts (job board) browse, merged.

`/career/` renders the viewer's own jobs/disciplines (the skills grid, the discipline radar,
per-job detail) AND the former Research Panel folded in as a "Contracts" tab -- so the
reward loop (accept a Contract -> its jobs level up) lives on one surface. Linked-profile
gated (the whole surface is personal).

Zones: the Pursuer hero + the jobs experience + the Contracts browse + the pending-rewards rail.
Page data: `career_service.build_career_context` + `contracts_service.contracts_page` (the
Contracts board renders page 1 server-side, then the toolbar drives the results endpoint).
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseNotFound
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView

from trophies.services import contracts_service
from trophies.services.career_service import build_career_context
from trophies.util_modules.constants import ALL_PLATFORMS, CONTRACT_XP_TOTAL

# The internal tabs a `?view=` query may deep-link to (match the template's data-view values).
_CAREER_VIEWS = frozenset({'jobs', 'radar', 'contracts'})
_VALID_PLATFORMS = frozenset(ALL_PLATFORMS)


def _page_num(raw):
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _board_params(request):
    """The Contracts board's filter/sort state from the querystring -- shared by the SSR page-1 render
    and the results endpoint so a shared/reloaded URL rebuilds the exact same filtered board."""
    g = request.GET
    return {
        'q': g.get('q', '').strip(),
        'status': g.get('status', ''),
        'disciplines': g.getlist('discipline'),   # multi, ANDed
        'jobs': g.getlist('job'),                  # multi, ANDed
        'platforms': [p for p in g.getlist('platform') if p in _VALID_PLATFORMS] or None,  # absent -> current-gen
        'sort': g.get('sort', 'relevance'),
        'scope': 'history' if g.get('scope') == 'history' else 'board',   # Board (default) | History split
    }


def _board_facets(profile, disc_levels, params, total):
    """Facet chip counts + (when the board is empty) a 'drop <label> to see N' suggestion, as one dict
    for `json_script`. `params` is `_board_params` output; `total` is the current result count."""
    facet_args = {k: params[k] for k in ('q', 'status', 'disciplines', 'jobs', 'platforms', 'scope')}
    f = contracts_service.board_facets(profile, disc_levels=disc_levels, **facet_args)   # status/platform/discipline/job
    suggest = contracts_service.suggest_relaxation(profile, disc_levels=disc_levels, **facet_args) if total == 0 else None
    return {**f, 'suggest': suggest}


class CareerView(LoginRequiredMixin, TemplateView):
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
        # Contracts board: render PAGE 1 server-side (filtered/sorted/paginated in the DB); the toolbar
        # then drives /career/contracts/results/ for filter-swaps + infinite scroll. The filter/sort
        # state comes from the querystring, so a shared/reloaded URL rebuilds the exact same board with
        # no flash. Default view = current-gen platforms, relevance sort.
        disc_levels = contracts_service.discipline_levels(profile)
        params = _board_params(self.request)
        page1 = contracts_service.contracts_page(profile, disc_levels=disc_levels, page=1, **params)
        context['contracts'] = page1['contracts']
        context['contracts_has_next'] = page1['has_next']
        context['contracts_total'] = page1['total']
        context['contract_disciplines'] = contracts_service.job_roster()   # 25-job roster for the card grid
        context['contracts_facets'] = _board_facets(profile, disc_levels, params, page1['total'])
        context['profile'] = profile
        context['viewer_has_linked_profile'] = True
        context['xp_total'] = CONTRACT_XP_TOTAL
        # Pending-rewards rail + "Claim all" count: ALL claimable contracts (one DB aggregate), not
        # just page 1 -- so the count is right no matter the paging/filters.
        claim = contracts_service.claimable_summary(profile)
        context['claimable_count'] = claim['count']
        context['claimable'] = claim
        # Active tab on load: ?view=contracts deep-links the Contracts board.
        requested = self.request.GET.get('view')
        context['active_view'] = requested if requested in _CAREER_VIEWS else 'jobs'
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Career'},
        ]
        context['seo_title'] = 'Career - Platinum Pursuit'
        # DEBUG-only: the claim-ceremony replay harness (canned payloads, no DB) lives in the template
        # behind this flag so animation iteration never touches real claim state.
        context['ceremony_debug'] = settings.DEBUG
        return context


class ContractsResultsView(LoginRequiredMixin, View):
    """Cards-only partial for the Contracts board: filtered/sorted/paginated in the DB. Serves both
    the filter-swap (page 1 -> replace the grid) and infinite scroll (page N -> append)."""

    def get(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile or not profile.is_linked:
            return HttpResponseNotFound()   # explicit 404 (the project's handler404 renders at 200)
        disc_levels = contracts_service.discipline_levels(profile)
        params = _board_params(request)
        page = _page_num(request.GET.get('page'))
        data = contracts_service.contracts_page(profile, disc_levels=disc_levels, page=page, **params)
        # Facets + smart-empty suggestion are page-1 concerns (they don't change as you scroll), so
        # infinite-scroll appends skip the extra aggregates.
        facets = _board_facets(profile, disc_levels, params, data['total']) if page == 1 else None
        resp = render(request, 'trophies/partials/contracts/_results.html', {
            **data, 'profile': profile, 'disciplines': contracts_service.job_roster(), 'facets': facets,
        })
        resp['X-Has-Next'] = '1' if data['has_next'] else '0'   # infinite-scroll stop signal
        resp['X-Total'] = str(data['total'])                    # for the board's result count
        return resp


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


class ContractModalPreviewView(View):
    """Public, ANONYMIZED contract modal -- the sign-up hook shown to logged-out / unlinked
    visitors (e.g. the game-detail contract row) instead of the linked-only board.

    Same contract card built with profile=None, so member games show their trophy composition
    rather than the viewer's progress; the shell footer carries the sign-up / link-PSN CTA. No
    auth by design: this is the pitch shown BEFORE a user has (or links) an account. Cheap --
    build_contract_modal(None, ...) does no per-user work; fetched lazily on click."""

    def get(self, request, slug):
        p = contracts_service.build_contract_modal(None, slug)
        if p is None:
            return HttpResponseNotFound()
        return render(request, 'trophies/partials/contracts/_contract_modal_preview.html',
                      {'p': p, 'profile': None, 'is_preview': True})
