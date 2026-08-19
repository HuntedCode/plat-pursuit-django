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
from django.http import HttpResponseNotFound, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy, reverse
from django.utils.http import urlencode
from django.views import View
from django.db.models import Count, F, Q
from django.db.models.functions import Lower
from django.views.generic import DetailView, ListView, TemplateView

from trophies.models import Contract, EarnedContract, Job
from trophies.mixins import HtmxListMixin
from trophies.services import badge_leaderboards as lb
from trophies.views import board_helpers
from trophies.views.board_helpers import suggest_json, window_params

from trophies.services import contracts_service
from trophies.services.career_service import build_career_context
from trophies.services.job_render import DISCIPLINE_ICON, DISCIPLINE_LABELS, discipline_order
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


class JobsBrowseView(HtmxListMixin, ListView):
    """`/jobs/` -- the public catalogue of jobs, in the BROWSE hub.

    Not under Leaderboards: a catalogue of jobs is a browse surface and sits with Games, Badges,
    Franchises and Companies. Its relationship to Career's Dossier is the one this codebase already
    settled for Collection vs Browse Badges, recorded as "SCOPE, not pagination" -- Career shows YOUR
    standing across the 24 jobs, this shows what the jobs ARE. They coexist without competing.

    Public by design. An anonymous visitor is exactly who this page is for: it is the readable surface of
    a system they have not signed up for yet. Nothing here reads the viewer, which is also what keeps the
    whole page cacheable.

    HTMX-swapped like every other browse page (2026-08). It was the last one still doing a full-page
    `form.submit()` on a 450ms debounce, so typing lost your scroll position and the focus of the field
    you were typing in.

    NO PAGINATION, and none is coming: the catalogue is 25 rows by construction (5 disciplines x 5 jobs),
    curated and seeded rather than grown. `HtmxListMixin` is here for the partial swap, not the pager.
    """
    model = Job
    template_name = 'trophies/jobs_browse.html'
    partial_template_name = 'trophies/partials/jobs_browse/browse_results.html'
    context_object_name = 'jobs'

    #: Ordering per sort value.
    #:
    #: `discipline` is the DEFAULT and it is Career's arrangement, not the column's: sorting on the
    #: `discipline` slug gives combat, exploration, finesse, heart, mind, while the canonical radar order
    #: is combat, exploration, mind, heart, finesse. The two agree for two disciplines and then diverge,
    #: which is exactly the kind of wrong that reads as right -- hence `discipline_order()`, which derives
    #: the sequence from `DISCIPLINE_LABELS` so there is one definition of it. `display_order` is 0-4
    #: WITHIN a discipline, so it can only ever be the second key.
    #:
    #: `contracts` is what feeds a job, which is the one thing a reader browsing the catalogue can act on.
    #: A "most hunters" sort was considered and dropped with the hunter count itself: how many people have
    #: touched a job says more about which games are popular than about the job.
    SORTS = {
        'discipline': [discipline_order(), 'display_order'],
        'contracts': [F('contract_count').desc(), discipline_order(), 'display_order'],
        'alpha': [Lower('name')],
    }
    DEFAULT_SORT = 'discipline'

    def get_queryset(self):
        qs = Job.objects.annotate(
            # ANNOTATED rather than counted per card, because the `contracts` sort has to happen in the
            # database. It also replaces the separate grouped query this page used to run, so gaining a
            # sort cost it a query rather than adding one. `distinct=True` because a Contract can feed
            # several jobs and the join would otherwise multiply.
            contract_count=Count('contracts', filter=Q(contracts__is_live=True), distinct=True),
        )
        q = (self.request.GET.get('q') or '').strip()
        if q:
            # `icontains` over 25 curated rows, deliberately: the site-wide PREFIX rule exists to keep a
            # search off a full scan of a large table, and this table is smaller than one page of results
            # anywhere else. Matching mid-word is worth more here than the index would be.
            qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
        disc = (self.request.GET.get('discipline') or '').strip()
        if disc in dict(Job.DISCIPLINES):
            qs = qs.filter(discipline=disc)
        return qs.order_by(*self.SORTS.get(self.sort, self.SORTS[self.DEFAULT_SORT]))

    @property
    def sort(self):
        value = (self.request.GET.get('sort') or '').strip()
        return value if value in self.SORTS else self.DEFAULT_SORT

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'q': (self.request.GET.get('q') or '').strip(),
            'sort': self.sort,
            # Zipped here rather than looked up in the template: Django has no dict-subscript filter,
            # and the two constants are one vocabulary that should travel together anyway. Order is
            # `DISCIPLINE_LABELS`', i.e. the same canonical sequence `discipline_order()` sorts the wall
            # into -- so the chips read left-to-right in the order the rows appear.
            'disciplines': [
                {'key': key, 'label': label, 'icon': DISCIPLINE_ICON.get(key, '')}
                for key, label in DISCIPLINE_LABELS.items()
            ],
            'selected_discipline': (self.request.GET.get('discipline') or '').strip(),
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'Jobs'},
            ],
        })
        return context

class JobRanksPanelView(View):
    """`/jobs/<slug>/ranks/` -- one job's board, fetched into job detail's Ranks tab on activation.

    Its own endpoint rather than a branch in `JobDetailView`, for the reason game detail's leaderboard
    panel has one: the cost scales with the job's popularity and most visitors come for the contracts. A
    page that renders both panels pays for both; a page that ships the cheap one and fetches the expensive
    one pays for what was asked for.

    The ROWS are identical for every viewer -- "this one is you" is applied in the browser from
    `data-lb-viewer-rank`, never rendered in, which is what keeps a row cacheable.

    The FRAGMENT, however, is now per-viewer, and the docstring used to claim otherwise. Jump-to-me needs
    the viewer's rank, and the rank has to reach the board root for the engine to light that row -- so
    the wrapper carries `my_rank` even though nothing inside a row does. That costs one `job_rank` count
    per tab open and means this fragment must not be given a shared cache key. It was impersonal before
    the jump bar existed; the affordance is worth the trade, but it IS a trade.

    TWO RESPONSES, one endpoint, matching `BadgeRanksPanelView`:

      no `?range=`   the full panel -- the jump bar, the board shell, the first window
      `?range=N`     bare `.lb-row`s for display positions [N, N+count), for the virtualizer

    The prev/next pager this used to carry is gone. A job board is the only per-entity board with no
    fuller surface to hand off to, so paging was the only way to reach row 300 at all -- and it meant
    twelve clicks to get there. A spacer is jumped into directly, which is the same fix applied to every
    other board.
    """
    #: Fetch granularity, shared with every other board -- see `board_helpers.PAGE_SIZE`.
    PAGE_SIZE = board_helpers.PAGE_SIZE

    def get(self, request, slug):
        job = get_object_or_404(Job, slug=slug)

        # The SLICE, applied to the rows, the count and the viewer's rank alike -- a window that ignored a
        # filter the first window applied would return different hunters halfway down the same board.
        codes = lb.job_countries(job.slug)
        country = self._country(request, codes)

        if request.GET.get('suggest') is not None:
            return JsonResponse(suggest_json(lb.board_suggest(
                lb._job_board_qs(job.slug, country or None), lb.JOB_KEYS,
                request.GET.get('suggest', ''))))

        if 'range' in request.GET:
            start, count = window_params(request, self.PAGE_SIZE)
            return render(request, 'trophies/partials/leaderboard_rows.html',
                          {'entries': self._window(job.slug, start - 1, count, country)})

        profile = getattr(request.user, 'profile', None) if request.user.is_authenticated else None
        my_rank = lb.job_rank(job.slug, profile.id, country=country or None) if profile else None
        return render(request, 'trophies/partials/job_detail/_ranks_panel.html', {
            'job': job,
            # `rows` / `total`, matching `BadgeRanksPanelView` exactly. They were `board`/`board_total`
            # here and `rows`/`total` there, for two panels deliberately written to mirror each other --
            # which is the drift this whole change is about, in miniature.
            'rows': self._window(job.slug, 0, self.PAGE_SIZE, country),
            'total': lb.job_board_count(job.slug, country=country or None),
            'page_size': self.PAGE_SIZE,
            'my_rank': my_rank,
            # The shared board card, same as badge detail's panel. `board_label` is the job, because on
            # this page the board IS the job.
            'board_label': job.name,
            # "Pursuer" rather than "hunter" here, deliberately: this is the jobs economy, and the
            # Pursuer is the identity that economy levels. Every other board says hunter.
            'board_meaning': 'Every Pursuer who has banked XP in this job, deepest first.',
            'standing': self._standing(profile, my_rank),
            # Reversed rather than read off `request.path`, so the panel does not silently depend on
            # having been reached by its canonical URL.
            'rows_url': reverse('job_ranks_panel', args=[job.slug]),
            'rows_params': urlencode({'country': country}) if country else '',
            'countries': lb.country_options(codes),
            'selected_country': country,
            'slice_applied': bool(country),
        })

    @staticmethod
    def _standing(profile, my_rank):
        """What the board card tells a signed-in viewer about themselves -- see `BadgeRanksPanelView
        ._standing`, which this deliberately mirrors. A ranked viewer gets nothing here, because the jump
        chip beneath already says "You're #N"."""
        if not (profile and profile.is_linked) or my_rank:
            return ''
        return 'Not ranked yet'

    @staticmethod
    def _country(request, codes):
        """Validated against the countries that actually have hunters on THIS board -- an unknown code
        would return an empty window, which reads as a gap in the board rather than a bad parameter."""
        raw = (request.GET.get('country') or '').strip().upper()
        return raw if raw in set(codes) else ''

    @staticmethod
    def _window(job_slug, offset, limit, country=''):
        """One window of the board, hydrated. Shared by both responses above: a rows endpoint that built
        its own `extra` mapping would be a second definition of what this board's columns MEAN, and the
        labels would be the first thing to drift -- so the rest of a board would read a different figure
        from the screenful the reader arrived on."""
        rows = lb.job_rows(job_slug, limit=limit, offset=offset, country=country or None)
        # `offset`, not 0: `page()` numbers rows by SLOT, so a window starting at 50 numbers from 51.
        return lb.page(rows, offset, extra=lambda r: {
            'primary': r[1], 'primary_label': 'XP',
            'secondary': r[2], 'secondary_label': 'level',
        })


class JobDetailView(DetailView):
    """`/jobs/<slug>/` -- what a job IS, the contracts that feed it, and its board.

    Two `.pp-switch` tabs, and the anon/authed split is PER TAB rather than per page -- the model game
    detail already uses:

      Contracts -> anon sees the games and what they pay; a linked viewer additionally sees their own
                   state on each. The aggregation by JOB exists nowhere else (a contract is keyed to one
                   Concept, so its natural home is the game).
      Ranks     -> identical for everyone. That is what keeps it cacheable, and a board a signed-out
                   visitor cannot see would defeat the discovery this page exists for.
    """
    model = Job
    slug_field = 'slug'
    slug_url_kwarg = 'slug'
    template_name = 'trophies/job_detail.html'
    context_object_name = 'job'
    CONTRACT_PAGE = 24

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        job = self.object
        profile = getattr(self.request.user, 'profile', None) if self.request.user.is_authenticated else None

        tab = self.request.GET.get('tab', 'contracts')
        tab = tab if tab in ('contracts', 'ranks') else 'contracts'
        context['active_tab'] = tab

        # PAGE CHROME, above the switcher, so it is on the page whichever tab is open. `my_rank` belongs
        # here for exactly that reason: it briefly moved into `_ranks()` and therefore vanished from the
        # default Contracts tab, which is where a signed-in hunter lands -- the standing chip only
        # appeared once you clicked Ranks, which is the tab that already shows you the board.
        context['board_total'] = lb.job_board_count(job.slug)
        context['my_rank'] = lb.job_rank(job.slug, profile.id) if profile else None
        # Distinguishes "signed out" from "signed in and not on this board". The template gated the whole
        # block on `my_rank`, so an unranked hunter got silence where the answer belonged.
        #
        # `is_linked`, not merely "has a profile": every board population is gated on it
        # (`badge_leaderboards._linked`), so telling an unverified account it is "not ranked yet" promises
        # a board it cannot enter until it verifies. Game detail already resolves its viewer this way.
        context['show_my_standing'] = bool(profile and profile.is_linked)

        # Contracts ONLY. The board moved to `JobRanksPanelView`, fetched when its tab is opened -- so a
        # visitor who never opens Ranks never pays for it, which is the saving the old `?tab=` version got
        # for free from a full page reload and would have lost when the tabs became in-place.
        context.update(self._contracts(job, profile))

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Jobs', 'url': reverse_lazy('jobs_browse')},
            {'text': job.name},
        ]
        return context

    def _contracts(self, job, profile):
        contracts = list(
            Contract.objects.filter(is_live=True, jobs=job)
            .prefetch_related('jobs')
            .order_by(Lower('name'))[:self.CONTRACT_PAGE]
        )
        # Per-row viewer state, BATCHED for the page. The trap here is a lookup per row: it looks fine at
        # 24 contracts and is not at 200, and infinite scroll bounds the rows rendered but never the
        # queries per row.
        earned_map = {}
        if profile and contracts:
            earned_map = {
                ec.contract_id: ec for ec in EarnedContract.objects.filter(
                    profile=profile, contract__in=contracts)
            }
        return {
            'contracts': contracts,
            'contract_total': Contract.objects.filter(is_live=True, jobs=job).count(),
            'earned_map': earned_map,
        }
