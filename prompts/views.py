"""Tiers, Grids & Polls -- the browse page.

THREE URLS, ONE VIEW. `/community/tiers/`, `/grids/` and `/polls/` are the same page with a different
tab selected, and the shape comes from the URL rather than from a querystring: each shape gets an
honest link to share and its own thing for a search engine to index, while the switcher stays what it
looks like -- one page with three tabs.

THE GATE IS `PremiumRequiredMixin`, mounted here for the first time since it was written. A hunter
outside the cohort is redirected to `/beta-access/` BEFORE `get_queryset` runs, which is what keeps
this compliant with the pinned preview rule: the page they get is a static template with no provider,
no per-user query and nothing touching this app's tables. The rule exists because that has gone wrong
twice, and both times the locked UI looked fine while its data layer ran anyway.

Ending the beta is deleting one class from one base list. There is deliberately nothing else to undo.
"""
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView

from prompts.models import (SHAPE_CHOICES, SHAPE_GRID, SHAPE_POLL, SHAPE_TIER, SHAPES, Prompt,
                            PromptLike)
from django_ratelimit.decorators import ratelimit

from api.utils import safe_int
from prompts.services import prompt_service as svc
from prompts.services import social_service as social
from prompts.services.covers import attach_cover_games
from trophies.mixins import HtmxListMixin, PremiumRequiredMixin
from trophies.models import Concept

#: The tab strip, as DATA. One list read by the view, the switcher and the URLs, so a tab cannot
#: appear in the strip and route nowhere -- the failure the list browse's `SORT_CHOICES` comment
#: records from the same shape.
SHAPE_TABS = (
    (SHAPE_TIER, 'Tiers', 'prompts_browse_tiers'),
    (SHAPE_GRID, 'Grids', 'prompts_browse_grids'),
    (SHAPE_POLL, 'Polls', 'prompts_browse_polls'),
)

#: What each tab says when it has nothing in it. Per shape, because "nobody has made a tier list yet"
#: and "nobody has asked a question yet" are different sentences and a shared one is neither.
EMPTY_COPY = {
    SHAPE_TIER: 'No tier lists yet. The first one is going to look very confident.',
    SHAPE_GRID: 'No grids yet. Nine questions and somebody has to answer them first.',
    SHAPE_POLL: 'No polls yet. Somebody has to ask.',
}

#: How long a search term may be before it is a pattern rather than a term. Mirrors the list browse's
#: bound and exists for the same reason: an unbounded `q` is an unbounded LIKE across two columns
#: plus a join.
MAX_QUERY_LENGTH = 64


class BrowsePromptsView(PremiumRequiredMixin, HtmxListMixin, ListView):
    """One shape's worth of public prompts, most-answered first."""

    #: A one- or two-character `%x%` is a guaranteed full scan returning most of the table, so below
    #: this the term is IGNORED rather than refused. A browse page is not a form.
    MIN_QUERY = 3

    model = Prompt
    template_name = 'prompts/browse.html'
    partial_template_name = 'prompts/partials/browse_results.html'
    context_object_name = 'prompts'
    paginate_by = 24

    #: Set by the URL, never by the querystring. NO CLASS DEFAULT: `as_view()` without a shape must
    #: fail loudly rather than silently serve Tiers under whatever path it was mounted at -- a
    #: duplicate of /community/tiers/ with a switcher marking the wrong tab and no error anywhere.
    shape = None

    #: Sorts as data, so the toolbar and the queryset read one list.
    SORT_CHOICES = (
        ('answered', 'Most answered'),
        ('popular', 'Most liked'),
        ('recent', 'Newest'),
    )
    _DEFAULT_SORT = 'answered'

    #: Each of these matches a partial index on `Prompt`, which is why they are spelled out rather
    #: than composed: the safe read is also the indexed one only when the ORDER BY agrees with the
    #: index it rides. `Meta.ordering` is `-created_at` precisely so a read that forgets to name a
    #: sort still lands on one of them.
    _ORDERING = {
        'answered': ('-response_count', '-created_at'),
        'popular': ('-like_count', '-created_at'),
        # `-pk` TIEBREAKS, and it is not decoration. `created_at` is `auto_now_add`, set in Python, so
        # a seed, a fixture or a data migration produces ties -- and a tie straddling a page boundary
        # under LIMIT/OFFSET means one prompt renders on two pages while another never renders at
        # all. It is already the index's implicit final key, so it costs nothing.
        'recent': ('-created_at', '-pk'),
    }

    def setup(self, request, *args, **kwargs):
        """Refuse a bad shape at the door rather than 500 three context keys later.

        `View.as_view` only checks `hasattr`, so it cannot catch a missing or misspelled shape. Left
        unvalidated, `as_view(shape='tiers')` passed, ran the full paginated query AND
        `attach_cover_games`, and only then raised `KeyError` out of `dict(SHAPE_CHOICES)[...]` --
        one of three unguarded lookups in `get_context_data`. Failing here costs nothing and names
        the problem.
        """
        if self.shape not in SHAPES:
            raise ImproperlyConfigured(
                f'{type(self).__name__} needs a valid shape from as_view(shape=...); '
                f'got {self.shape!r}.'
            )
        super().setup(request, *args, **kwargs)

    def _selected_sort(self):
        """Clamped `?sort=`. Junk falls back rather than dropping to NO ordering, which is how a grid
        ends up in whatever order the database felt like -- and, with pagination, how the same row
        appears on two pages."""
        raw = self.request.GET.get('sort', self._DEFAULT_SORT)
        return raw if raw in dict(self.SORT_CHOICES) else self._DEFAULT_SORT

    def _query(self):
        raw = (self.request.GET.get('q') or '').strip()[:MAX_QUERY_LENGTH]
        return raw if len(raw) >= self.MIN_QUERY else ''

    def get_queryset(self):
        # `.public()` rather than a hand-written pair of flags: it is the one supported way to read
        # somebody else's prompt, and it matches the partial index predicate exactly.
        queryset = (Prompt.objects.public()
                    .of_shape(self.shape)
                    .select_related('owner'))

        query = self._query()
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query) | Q(owner__psn_username__icontains=query))

        # NAMED EXPLICITLY, always. `Meta.ordering` would otherwise decide, and the browse indexes
        # lead with `shape` followed by the sort column -- an un-named sort rides none of them.
        return queryset.order_by(*self._ORDERING[self._selected_sort()])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        prompts = context['prompts']

        # The mosaic, in two queries for the whole page. An open grid comes back with an empty
        # `cover_items` and the tile renders that as a state -- see `prompts/services/covers.py`.
        attach_cover_games(prompts)

        context['shape'] = self.shape
        context['shape_label'] = dict(SHAPE_CHOICES)[self.shape]
        context['shape_tabs'] = SHAPE_TABS
        # THIS shape's own route, so the filter form and the "clear search" link post back to the tab
        # the reader is on. Hardcoding one would silently move somebody to Tiers on every filter
        # change, which is the kind of bug that reads as the page being broken rather than wrong.
        context['shape_tab_url'] = dict((v, u) for v, _l, u in SHAPE_TABS)[self.shape]
        context['empty_copy'] = EMPTY_COPY[self.shape]
        context['sort_choices'] = self.SORT_CHOICES
        context['current_sort'] = self._selected_sort()
        # THROUGH THE SAME PARSER THE QUERYSET USES. The raw value made the per-shape empty copy
        # unreachable for any 1-2 character `?q=`: nothing was filtered, so `has_filters` was False
        # and no "Clear search" appeared -- but the template still branched on `query` and told the
        # reader "Nothing matches 'ab'" about a search that never ran, with no way to undo it.
        context['query'] = self._query()
        # "You narrowed it to nothing" and "there is nothing here yet" are opposite situations, and
        # offering the wrong remedy is worse than offering none. Read through the same parser the
        # queryset uses, so a term too short to filter does not claim to be filtering.
        context['has_filters'] = bool(self._query())

        viewer = self._viewer()
        if viewer is not None and prompts:
            # ONE query for the page's likes, bounded by the page slice -- never one per tile. The
            # tile shows whether YOU liked it, which is the shape that most invites an N+1.
            # A LIST OF PRIMARY KEYS, not the queryset. `context['prompts']` is a SLICED queryset,
            # and Django keeps a sliced qs's LIMIT/OFFSET and ORDER BY when it becomes an `__in`
            # subquery -- so this re-executed the entire browse query, ILIKE-plus-join and OFFSET
            # walk included, to find twenty-four ids already sitting in memory.
            liked = set(PromptLike.objects
                        .filter(profile=viewer, prompt_id__in=[p.pk for p in prompts])
                        .values_list('prompt_id', flat=True))
            for prompt in prompts:
                prompt.viewer_has_liked = prompt.pk in liked

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Tiers, Grids & Polls'},
        ]
        # `seo_title` FEEDS THE og/twitter TAGS; `{% block title %}` does not. Without it every share
        # of these three URLs previewed as the site-wide "Platinum Pursuit" with a correct
        # description under it -- the exact half-job `GameListDetailView` diagnosed and fixed for
        # detail pages while leaving its own browse page short.
        context['seo_title'] = f"{context['shape_label']}s on Platinum Pursuit"
        context['seo_description'] = (
            'Tier lists, grids and polls set by the Platinum Pursuit community. Somebody sets the '
            'prompt; you make the call.'
        )
        return context

    def _viewer(self):
        if not self.request.user.is_authenticated:
            return None
        return getattr(self.request.user, 'profile', None)


# ── the author's write endpoints ─────────────────────────────────────────────────────────────────

class _PromptActionView(PremiumRequiredMixin, View):
    """POST-only, resolves the prompt through `readable_by`, answers JSON.

    UNDER THE PAGE'S PATH RATHER THAN /api/v1/, for the reason the list endpoints give: they are this
    page's behaviour and share its gate. Routing them through the API app would mean a second
    permission stack that has to agree with the first forever, and the one that forgot would be the
    one that mattered.

    `readable_by` and not a bare lookup: a private prompt must answer 404 to anybody who cannot see
    it, so an id alone can never confirm that a prompt exists or whose it is.

    DELIBERATELY NOT `raise Http404`. This project's `handler404` is a GET-only view, so an Http404
    raised from a POST comes back as a 405 listing GET/HEAD/OPTIONS -- a JSON client would get an
    HTML error page describing the wrong problem.
    """

    #: THE GATE APPLIES HERE TOO. `PremiumRequiredMixin` redirects rather than 403s, which is odd for
    #: a JSON endpoint -- but the alternative is a second cohort check that can disagree with the
    #: page's, and a fetch that follows a redirect into HTML is a failure the client already has to
    #: handle (the list JS throws on a non-object body for exactly this reason).

    def get_prompt(self, request, prompt_id):
        return Prompt.objects.readable_by(self._viewer(request)).filter(pk=prompt_id).first()

    def not_found(self):
        return JsonResponse({'error': 'That is not available.'}, status=404)

    def _viewer(self, request):
        return getattr(request.user, 'profile', None)

    def fail(self, exc, status=400):
        return JsonResponse({'error': str(exc)}, status=status)


class CreatePromptView(PremiumRequiredMixin, View):
    """Make one. Always private -- see `prompt_service.create_prompt`."""

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
    def post(self, request):
        profile = getattr(request.user, 'profile', None)
        try:
            prompt = svc.create_prompt(
                profile,
                shape=request.POST.get('shape', ''),
                title=request.POST.get('title', ''),
                description=request.POST.get('description', ''),
                grid_columns=request.POST.get('grid_columns') or 3,
                allow_duplicates=request.POST.get('allow_duplicates', 'true') == 'true',
            )
        except svc.PromptError as exc:
            return JsonResponse({'error': str(exc)}, status=400)
        return JsonResponse({'id': prompt.pk, 'title': prompt.title, 'shape': prompt.shape})


class UpdatePromptView(_PromptActionView):
    """Retitle, re-describe, publish, unpublish, set grid columns, toggle duplicates.

    ONE ENDPOINT because they are one service call: `update_prompt` takes each field optionally and
    touches only what it is given. Splitting them would mean six permission stacks that have to agree.

    Every field is passed only when PRESENT in the body, so a client sending one field cannot
    accidentally clear another by omitting it.
    """

    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, prompt_id):
        prompt = self.get_prompt(request, prompt_id)
        if prompt is None:
            return self.not_found()

        fields = {}
        if 'title' in request.POST:
            fields['title'] = request.POST['title']
        if 'description' in request.POST:
            fields['description'] = request.POST['description']
        if 'is_public' in request.POST:
            fields['is_public'] = request.POST['is_public'] == 'true'
        if 'grid_columns' in request.POST:
            fields['grid_columns'] = request.POST['grid_columns']
        if 'allow_duplicates' in request.POST:
            fields['allow_duplicates'] = request.POST['allow_duplicates'] == 'true'

        try:
            prompt = svc.update_prompt(prompt, self._viewer(request), **fields)
        except svc.PromptError as exc:
            return self.fail(exc)
        return JsonResponse({
            'id': prompt.pk, 'title': prompt.title, 'is_public': prompt.is_public,
            'is_closed': prompt.is_closed, 'allow_duplicates': prompt.allow_duplicates,
        })


class SetClosedView(_PromptActionView):
    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
    def post(self, request, prompt_id):
        prompt = self.get_prompt(request, prompt_id)
        if prompt is None:
            return self.not_found()
        closed = request.POST.get('closed') == 'true'
        try:
            prompt = svc.set_closed(prompt, self._viewer(request), closed=closed)
        except svc.PromptError as exc:
            return self.fail(exc)
        return JsonResponse({'is_closed': prompt.is_closed})


class DeletePromptView(_PromptActionView):
    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
    def post(self, request, prompt_id):
        prompt = self.get_prompt(request, prompt_id)
        if prompt is None:
            return self.not_found()
        try:
            svc.delete_prompt(prompt, self._viewer(request))
        except svc.PromptError as exc:
            return self.fail(exc)
        return JsonResponse({'deleted': True})


class AddGameView(_PromptActionView):
    @method_decorator(ratelimit(key='user', rate='120/m', method='POST', block=True))
    def post(self, request, prompt_id):
        prompt = self.get_prompt(request, prompt_id)
        if prompt is None:
            return self.not_found()
        concept = Concept.objects.filter(pk=safe_int(request.POST.get('concept_id'))).first()
        if concept is None:
            return self.fail(svc.PromptError('That game could not be found.'))
        try:
            game = svc.add_concept(prompt, self._viewer(request), concept)
        except svc.PromptError as exc:
            return self.fail(exc)
        prompt.refresh_from_db()
        # A SERVER-BUILT ROUTE, never an id the client assembles into a path. The list endpoints
        # learned this: a hand-assembled URL is what breaks silently the day a route moves.
        return JsonResponse({
            'game_id': game.pk,
            'title': concept.unified_title,
            'game_count': prompt.game_count,
            'remove_url': reverse('prompt_remove_game', args=[prompt.pk, game.pk]),
        })


class RemoveGameView(_PromptActionView):
    @method_decorator(ratelimit(key='user', rate='120/m', method='POST', block=True))
    def post(self, request, prompt_id, game_id):
        prompt = self.get_prompt(request, prompt_id)
        if prompt is None:
            return self.not_found()
        # RESOLVED WITHIN THE PROMPT, never by bare id -- the schema does not tie a pool row to the
        # prompt a caller names, so this lookup is the thing that does.
        game = svc.get_game(prompt, game_id)
        if game is None:
            return self.not_found()
        try:
            svc.remove_concept(prompt, self._viewer(request), game)
        except svc.PromptError as exc:
            return self.fail(exc)
        prompt.refresh_from_db()
        return JsonResponse({'game_count': prompt.game_count})


class ReorderGamesView(_PromptActionView):
    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, prompt_id):
        prompt = self.get_prompt(request, prompt_id)
        if prompt is None:
            return self.not_found()
        try:
            svc.reorder_games(prompt, self._viewer(request), request.POST.getlist('game_ids[]'))
        except svc.PromptError as exc:
            return self.fail(exc)
        return JsonResponse({'ok': True})


class CreateBucketView(_PromptActionView):
    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
    def post(self, request, prompt_id):
        prompt = self.get_prompt(request, prompt_id)
        if prompt is None:
            return self.not_found()
        try:
            bucket = svc.create_bucket(prompt, self._viewer(request),
                                       label=request.POST.get('label', ''),
                                       colour=request.POST.get('colour', ''))
        except svc.PromptError as exc:
            return self.fail(exc)
        return JsonResponse({
            'bucket_id': bucket.pk, 'label': bucket.label, 'colour': bucket.colour,
            'delete_url': reverse('prompt_delete_bucket', args=[prompt.pk, bucket.pk]),
        })


class UpdateBucketView(_PromptActionView):
    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, prompt_id, bucket_id):
        prompt = self.get_prompt(request, prompt_id)
        if prompt is None:
            return self.not_found()
        bucket = svc.get_bucket(prompt, bucket_id)
        if bucket is None:
            return self.not_found()
        fields = {}
        if 'label' in request.POST:
            fields['label'] = request.POST['label']
        if 'colour' in request.POST:
            fields['colour'] = request.POST['colour']
        try:
            bucket = svc.update_bucket(bucket, self._viewer(request), **fields)
        except svc.PromptError as exc:
            return self.fail(exc)
        return JsonResponse({'bucket_id': bucket.pk, 'label': bucket.label, 'colour': bucket.colour})


class DeleteBucketView(_PromptActionView):
    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
    def post(self, request, prompt_id, bucket_id):
        prompt = self.get_prompt(request, prompt_id)
        if prompt is None:
            return self.not_found()
        bucket = svc.get_bucket(prompt, bucket_id)
        if bucket is None:
            return self.not_found()
        try:
            svc.delete_bucket(bucket, self._viewer(request))
        except svc.PromptError as exc:
            return self.fail(exc)
        return JsonResponse({'deleted': True})


class ReorderBucketsView(_PromptActionView):
    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, prompt_id):
        prompt = self.get_prompt(request, prompt_id)
        if prompt is None:
            return self.not_found()
        try:
            svc.reorder_buckets(prompt, self._viewer(request), request.POST.getlist('bucket_ids[]'))
        except svc.PromptError as exc:
            return self.fail(exc)
        return JsonResponse({'ok': True})


class TogglePromptLikeView(_PromptActionView):
    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, prompt_id):
        prompt = self.get_prompt(request, prompt_id)
        if prompt is None:
            return self.not_found()
        liked = request.POST.get('liked') == 'true'
        try:
            count = social.set_prompt_like(prompt, self._viewer(request), liked=liked)
        except svc.PromptError as exc:
            return self.fail(exc)
        return JsonResponse({'liked': liked, 'like_count': count})
