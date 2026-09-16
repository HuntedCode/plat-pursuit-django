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
from django.views.generic import DetailView, ListView

from prompts.models import (BUCKET_COLOUR_CHOICES, DESCRIPTION_MAX_LENGTH, LABEL_MAX_LENGTH,
                            MAX_BUCKETS_PER_PROMPT, MAX_GAMES_PER_PROMPT, MAX_GRID_COLUMNS,
                            MIN_GRID_COLUMNS, SHAPE_BLURBS,
                            SHAPE_CHOICES, SHAPE_GRID, SHAPE_POLL, SHAPE_TIER, SHAPES,
                            TITLE_MAX_LENGTH, Prompt, PromptGame, PromptLike, shape_options)
from django_ratelimit.decorators import ratelimit

from api.utils import safe_int
from prompts.services import prompt_service as svc
from prompts.services import response_service as responses
from prompts.services import social_service as social
from gamelists.services.covers import cover_games_for
from gamelists.services.game_search import SearchRefused, search_concepts
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

        # ── the create dialog ───────────────────────────────────────────────────────────────────
        #
        # `is_linked` because `create_prompt` refuses an unlinked profile outright. A button whose
        # every press 400s is worse than no button, and the gate on the page must agree with the gate
        # on the endpoint -- that disagreement is the bug this app keeps guarding against.
        #
        # DELIBERATELY NOT `max_prompts_for(profile)`: the cap is enforced at the write, where the
        # count is taken under a lock, and asking here would run a COUNT on every browse render to
        # decide whether to draw a button. The refusal carries its own words when somebody is full.
        # `viewer` is the one settled above for the like hydration -- not re-fetched.
        context['can_create'] = viewer is not None and viewer.is_linked
        # The shapes with their blurbs, from the model, so the dialog and the tabs cannot describe the
        # same shape differently. `shape` is already in context and preselects the current tab.
        context['shape_options'] = shape_options()
        context['title_max_length'] = TITLE_MAX_LENGTH
        context['description_max_length'] = DESCRIPTION_MAX_LENGTH
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


# ── the prompt itself ────────────────────────────────────────────────────────────────────────────

#: A backstop on the answers panel, not a page. Real pagination arrives with the response surfaces;
#: until then this is what stops a prompt that goes well from rendering four hundred cards into one
#: response. Named rather than inlined so the day it becomes a page size, there is one number to move.
MAX_RESPONSES_RENDERED = 48


class PromptDetailView(PremiumRequiredMixin, DetailView):
    """One prompt, and where its author builds it IN PLACE.

    NO SEPARATE `/edit/` ADDRESS, for the reason the list detail page gives: editing happens where you
    can see the result. That matters more here than it does on a list, because what an author is
    editing IS the thing respondents will see -- rows, their order, their colours -- so an edit screen
    that looked different from the published page would be a preview that lies.

    `readable_by` is the single supported read, so a draft 404s for everybody but its author rather
    than 403ing. Same reason as everywhere else in this app: a 403 confirms the prompt exists from
    nothing but an id.

    WHAT THE AUTHOR MAY DO IS ASKED OF THE SERVICE, never re-derived here. `frozen_acts` and
    `publish_blocker` are the two readers this page needs, and both are thin wrappers over the rules
    the write endpoints enforce -- so a control cannot be drawn for an act that would be refused, and
    a refusal cannot be phrased differently from the hint that preceded it.
    """

    model = Prompt
    template_name = 'prompts/detail.html'
    context_object_name = 'prompt'
    pk_url_kwarg = 'prompt_id'

    def _viewer(self):
        if not self.request.user.is_authenticated:
            return None
        return getattr(self.request.user, 'profile', None)

    def get_queryset(self):
        # `owner` for the byline. The pool's cover art is attached per-row below, batched.
        return Prompt.objects.readable_by(self._viewer()).select_related('owner')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        prompt = self.object
        viewer = self._viewer()
        is_owner = viewer is not None and prompt.owner_id == viewer.id

        # ── the structure ───────────────────────────────────────────────────────────────────────
        #
        # Both bounded by their per-shape caps (`MAX_BUCKETS_PER_PROMPT`, `MAX_GAMES_PER_PROMPT`), so
        # these are the bounded reads CLAUDE.md's whale rule allows rather than the unbounded
        # per-row iteration it bans. Two queries, flat, regardless of how well the prompt did.
        buckets = list(prompt.buckets.all())
        # `concept__igdb_match` deferred, because the pool cards call `display_image_url`, whose
        # first lookup is the IGDB cover -- without the select_related that is one query per game,
        # and without the `defer` each drags the ~30 KB `raw_response` blob that caused the May 2026
        # OOM. CLAUDE.md names this pairing explicitly.
        pool = list(
            prompt.games
            .select_related('concept', 'concept__igdb_match')
            .defer('concept__igdb_match__raw_response')
            .order_by('position')
        )
        covers = cover_games_for([game.concept_id for game in pool])
        for game in pool:
            game.cover = covers.get(game.concept_id)

        context['buckets'] = buckets
        context['pool'] = pool

        # ── who is looking ──────────────────────────────────────────────────────────────────────
        context['is_owner'] = is_owner
        # `is_linked` for the same reason the list page carries it: every write endpoint refuses an
        # unlinked profile, so the page and the endpoints have to agree about who can act. Drawing
        # tools for somebody whose every use of them 400s is worse than drawing none.
        context['can_edit'] = is_owner and viewer.is_linked

        # ── what the author may do, asked of the service ────────────────────────────────────────
        #
        # One call, unpacked into the five flags the template needs. The template gets booleans and
        # no rule: it must not know that a poll freezes or that a draft never does, because that
        # knowledge in a template is a copy of the rule that no test will ever fail.
        frozen = svc.frozen_acts(prompt)
        context['can_edit_rows'] = context['can_edit'] and 'rows' not in frozen
        context['can_add_games'] = context['can_edit'] and 'pool_add' not in frozen
        context['can_remove_games'] = context['can_edit'] and 'pool_remove' not in frozen
        context['can_reorder_pool'] = context['can_edit'] and 'pool_order' not in frozen
        context['can_edit_question'] = context['can_edit'] and 'question' not in frozen
        # Only meaningful to an author looking at a draft; computed only then, because it runs two
        # COUNTs and a reader can do nothing with the answer.
        context['publish_blocker'] = (
            svc.publish_blocker(prompt) if context['can_edit'] and not prompt.is_public else None)

        # ── the shape, as labels rather than as branches ────────────────────────────────────────
        context['shape_label'] = prompt.get_shape_display()
        context['shape_blurb'] = SHAPE_BLURBS[prompt.shape]
        context['is_tier'] = prompt.shape == SHAPE_TIER
        context['is_grid'] = prompt.shape == SHAPE_GRID
        context['is_poll'] = prompt.shape == SHAPE_POLL
        # An OPEN grid -- no pool -- is a different page: respondents search the catalogue per slot,
        # so there is no pool panel to draw and no tray to drag from. Derived from the pool being
        # empty rather than stored, which is the same derivation `_has_pool` makes in the response
        # service, so the two cannot disagree about which kind of grid this is.
        context['is_open_grid'] = context['is_grid'] and not pool

        context['max_games'] = MAX_GAMES_PER_PROMPT[prompt.shape]
        context['max_buckets'] = MAX_BUCKETS_PER_PROMPT[prompt.shape]
        # The column picker's options, from the same bounds the check constraint and the service
        # validator read, so the select cannot offer a value the database refuses.
        context['grid_column_choices'] = range(MIN_GRID_COLUMNS, MAX_GRID_COLUMNS + 1)
        # THE CAPS DECIDE WHETHER AN ADD CONTROL EXISTS, computed here so the template never compares
        # a length to a constant.
        #
        # AN EARLIER VERSION OF THIS COMMENT CLAIMED THE CAP IS ALSO WHAT KEEPS A POLL FROM DRAWING
        # ROW TOOLS "without a shape branch". That is false, and mutation testing is what said so: the
        # template hides the whole rows panel behind `{% if not is_poll %}`, because a poll's single
        # bucket has no label worth showing -- so `can_add_rows` could be forced True and the control
        # still would not render. The cap here is a second line, not the line.
        #
        # The actual guarantee that a poll never gains a second bucket lives in `create_bucket`, which
        # refuses it, and is pinned by `test_prompt_service`. It has to live there: "a hunter votes
        # once" rests on `unique(response, bucket) WHERE single_slot`, which allows one placement PER
        # BUCKET -- so a second bucket on a poll buys a second vote with every flag set correctly, and
        # the database cannot catch it because the count lives on the parent.
        context['can_add_rows'] = context['can_edit_rows'] and len(buckets) < context['max_buckets']
        context['can_add_more_games'] = context['can_add_games'] and len(pool) < context['max_games']
        context['bucket_colours'] = BUCKET_COLOUR_CHOICES
        context['title_max_length'] = TITLE_MAX_LENGTH
        context['description_max_length'] = DESCRIPTION_MAX_LENGTH
        context['label_max_length'] = LABEL_MAX_LENGTH

        # ── the answers ─────────────────────────────────────────────────────────────────────────
        #
        # The viewer's own answer is fetched even when it is PRIVATE -- `listed_responses` filters to
        # public, and your own answer is yours to see either way. Two queries rather than one because
        # they ask different questions; merging them would mean ORing a per-viewer predicate into the
        # public read, which is how a private row ends up in somebody else's list.
        context['viewer_response'] = responses.response_for(prompt, viewer)
        context['responses'] = list(
            responses.listed_responses(prompt)
            .select_related('profile')
            .order_by('-like_count', '-updated_at')[:MAX_RESPONSES_RENDERED]
        )

        # ── social ──────────────────────────────────────────────────────────────────────────────
        #
        # ONE `exists()`, and only for a signed-in viewer. An anonymous reader cannot have liked
        # anything, so asking is a query whose answer is known.
        context['viewer_liked'] = bool(
            viewer is not None
            and PromptLike.objects.filter(prompt=prompt, profile=viewer).exists()
        )
        # THE THREE REFUSALS `set_prompt_like` MAKES, asked before drawing the control. An owner is
        # excluded because liking your own is refused outright -- a heart that always errors is worse
        # than no heart -- and an unlinked profile is excluded for the reason `can_edit` carries it.
        # Withdrawing a like is deliberately NOT gated on restriction here even though liking is: the
        # service allows an unlike from a restricted profile, and a page that hid the lit heart would
        # strand a like nobody could take back. That was a real bug on this surface once already.
        context['can_like'] = bool(
            viewer is not None and viewer.is_linked and not is_owner
            and (context['viewer_liked'] or prompt.is_public)
        )

        # BACK TO THIS PROMPT'S OWN SHAPE, not to a generic hub: a reader who arrived on a poll wants
        # the polls tab, and `SHAPE_TABS` is the one place that maps a shape to its URL -- read rather
        # than reproduced, so a renamed route cannot leave a dead crumb here.
        shape_url = next(url for value, _label, url in SHAPE_TABS if value == prompt.shape)
        # Also where a delete returns to, which is why it is its own key rather than being dug back
        # out of the breadcrumb: the crumb is presentation and could gain or lose a level.
        context['browse_url'] = reverse(shape_url)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Tiers, Grids & Polls', 'url': context['browse_url']},
            {'text': prompt.title},
        ]

        # The share card and the tab both want the prompt's own name, not the site default.
        context['seo_title'] = f'{prompt.title} -- {context["shape_label"]}'
        context['seo_description'] = prompt.description or context['shape_blurb']
        return context


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
        return JsonResponse({
            'id': prompt.pk,
            'title': prompt.title,
            'shape': prompt.shape,
            # A SERVER-BUILT ROUTE, never an id the client assembles into a path. `AddGameView`'s
            # `remove_url` learned this: a hand-assembled URL is what breaks silently the day a route
            # moves, and the create dialog navigates straight here.
            'detail_url': reverse('prompt_detail', args=[prompt.pk]),
        })


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


class PromptGameSearchView(_PromptActionView):
    """Typeahead for the pool adder. The catalogue half is shared; the membership half is this one's.

    GET, not POST, so it inherits `_PromptActionView`'s prompt resolution and its gate while answering
    a read. The base's `post` is simply not defined, so a POST here is a 405 -- which is correct.

    `already_added` IS BOUNDED TO THE TWELVE ROWS BEING RENDERED, never computed by reading the pool.
    That is the same shape the list adder was fixed into, and for the reason CLAUDE.md's whale rule
    names: `list(qs.values_list(...))` followed by Python membership runs on every keystroke, at 120
    requests a minute, against a pool that can hold two hundred. The `WHERE concept_id IN (...)` is
    served by the `unique(prompt, concept)` index, so it is a bounded seek.

    Marked rather than filtered: somebody searching for a game already in the pool should be told it is
    there, not left wondering why their search returns nothing.
    """

    @method_decorator(ratelimit(key='user', rate='120/m', method='GET', block=True))
    def get(self, request, prompt_id):
        prompt = self.get_prompt(request, prompt_id)
        if prompt is None:
            return self.not_found()
        try:
            results = search_concepts(request.GET.get('q'))
        except SearchRefused as exc:
            return self.fail(exc)

        already = set(
            PromptGame.objects
            .filter(prompt=prompt, concept_id__in=[row['concept_id'] for row in results])
            .values_list('concept_id', flat=True)
        )
        return JsonResponse({'results': [
            dict(row, already_added=row['concept_id'] in already) for row in results
        ]})


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
