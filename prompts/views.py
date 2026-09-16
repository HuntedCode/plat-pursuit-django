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
from django.db.models import Q
from django.urls import reverse_lazy
from django.views.generic import ListView

from prompts.models import (SHAPE_CHOICES, SHAPE_GRID, SHAPE_POLL, SHAPE_TIER, Prompt, PromptLike)
from prompts.services.covers import attach_cover_games
from trophies.mixins import HtmxListMixin, PremiumRequiredMixin

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

    #: Set by the URL, never by the querystring.
    shape = SHAPE_TIER

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
        'recent': ('-created_at',),
    }

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
        context['query'] = (self.request.GET.get('q') or '').strip()
        # "You narrowed it to nothing" and "there is nothing here yet" are opposite situations, and
        # offering the wrong remedy is worse than offering none. Read through the same parser the
        # queryset uses, so a term too short to filter does not claim to be filtering.
        context['has_filters'] = bool(self._query())

        viewer = self._viewer()
        if viewer is not None and prompts:
            # ONE query for the page's likes, bounded by the page slice -- never one per tile. The
            # tile shows whether YOU liked it, which is the shape that most invites an N+1.
            liked = set(PromptLike.objects
                        .filter(profile=viewer, prompt__in=prompts)
                        .values_list('prompt_id', flat=True))
            for prompt in prompts:
                prompt.viewer_has_liked = prompt.pk in liked

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Tiers, Grids & Polls'},
        ]
        context['seo_description'] = (
            'Tier lists, grids and polls set by the Platinum Pursuit community. Somebody sets the '
            'prompt; you make the call.'
        )
        return context

    def _viewer(self):
        if not self.request.user.is_authenticated:
            return None
        return getattr(self.request.user, 'profile', None)
