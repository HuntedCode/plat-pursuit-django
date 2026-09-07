"""Game Lists surfaces.

THREE PAGES, NOT FIVE (settled with the owner before the rebuild): Browse, List detail where the
owner edits in place, and My Lists with create as a modal. The system this replaces had separate
create and edit ADDRESSES, which meant three round trips to rename a list you were looking at.

GATED WHILE THE BRANCH IS OPEN. Lists and the Challenges beta ship as ONE update, so these routes
exist for tests and for a browser pass but are closed to everybody else until the commit that turns
both on. `_DevelopmentGate` is one mixin and its removal is the switch -- see `test_lists_hidden`,
which pins that nothing links here yet.
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.db.models.functions import Lower
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import DetailView, ListView, View

from gamelists.models import (DESCRIPTION_MAX_LENGTH, NAME_MAX_LENGTH, GameList,
                             GameListFollow, GameListItem, GameListLike)
from gamelists.services import game_list_service as svc
from gamelists.services.covers import attach_cover_games, cover_games_for
from trophies.mixins import HtmxListMixin, StaffRequiredMixin
from trophies.models import Concept

#: How many covers the `.pp-gtile` mosaic composes around (`is-1` .. `is-4`).
LIST_TILE_COVERS = 4

#: Ceiling for the game-count filter. Anything above it is treated as "no filter" rather than passed
#: to the database: a 40-digit number compared against a PositiveIntegerField is backend-dependent
#: behaviour for a query nobody meant to run.
MAX_GAME_COUNT_FILTER = 10_000


def _count_filter(raw):
    """One `?min_games=` / `?max_games=` value, or None for "no filter".

    `str.isdigit()` alone is a 500 waiting to happen: it is True for Unicode superscripts, where
    `int()` then raises. Verified rather than assumed -- `'²'.isdigit()` is True and
    `int('²')` is a ValueError, so `?min_games=` with a superscript two took the browse page
    down. `isascii()` is what makes the two agree.

    Junk is ignored rather than refused, because a filter is not a form: somebody arriving on a
    mangled link should see the grid, not an error about a query parameter.
    """
    text = (raw or '').strip()
    if not (text.isascii() and text.isdigit()):
        return None
    value = int(text)
    return value if value <= MAX_GAME_COUNT_FILTER else None


class _DevelopmentGate(StaffRequiredMixin):
    """Temporary. Lists turn on with the Challenges beta, in one commit, not before.

    Staff-only rather than unrouted, because the alternative is testing a browse page through
    RequestFactory and never exercising the HTMX and infinite-scroll paths that are most of what
    makes it a browse page. Deleting this class and its mixin references is the whole of "turn it
    on"; nothing else about these views is conditional.
    """


class BrowseListsView(_DevelopmentGate, HtmxListMixin, ListView):
    """Public lists from every hunter, newest or most-liked first."""

    model = GameList
    template_name = 'gamelists/browse.html'
    partial_template_name = 'gamelists/partials/browse_results.html'
    context_object_name = 'game_lists'
    paginate_by = 24

    #: Sorts as DATA so the toolbar select, the mini-bar proxy and the queryset all read ONE list.
    #: The pre-rebuild template hand-wrote an `<option>` per sort, which is a second copy to keep in
    #: step and the reason a sort could appear in the dropdown and do nothing.
    SORT_CHOICES = (
        ('popular', 'Most liked'),
        ('recent', 'Newest'),
        ('updated', 'Recently updated'),
        ('most_games', 'Most games'),
        ('alpha', 'A-Z'),
    )
    _DEFAULT_SORT = 'popular'

    _ORDERING = {
        'recent': ('-created_at',),
        'updated': ('-updated_at',),
        'most_games': ('-game_count', '-like_count'),
        'popular': ('-like_count', '-created_at'),
    }

    def _selected_sort(self):
        """Clamped `?sort=`. A junk value falls back rather than dropping to NO ordering, which is
        how a browse grid ends up in whatever order the database felt like."""
        raw = self.request.GET.get('sort', self._DEFAULT_SORT)
        return raw if raw in dict(self.SORT_CHOICES) else self._DEFAULT_SORT

    def get_queryset(self):
        # `.public()` rather than a hand-written `is_public=True, is_deleted=False`: it is the one
        # supported read path for somebody else's list, and it matches the partial index predicate
        # exactly, so the safe way is also the indexed way.
        queryset = GameList.objects.public().select_related('owner')

        query = (self.request.GET.get('q') or '').strip()
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(owner__psn_username__icontains=query)
            )

        minimum = _count_filter(self.request.GET.get('min_games'))
        maximum = _count_filter(self.request.GET.get('max_games'))
        if minimum is not None:
            queryset = queryset.filter(game_count__gte=minimum)
        if maximum is not None:
            queryset = queryset.filter(game_count__lte=maximum)

        sort = self._selected_sort()
        if sort == 'alpha':
            # `Lower()` because Postgres sorts uppercase before lowercase, so a plain `name` sort
            # files "apex" after "Zenith" -- the house rule for every front-facing name column.
            return queryset.order_by(Lower('name'))
        return queryset.order_by(*self._ORDERING[sort])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lists = context['game_lists']

        # The mosaic, in two queries for the whole page regardless of length. This CANNOT be a
        # `Prefetch(to_attr=...)` the way the Game-keyed version was: the cover lives on a `Game`
        # and an item now points at a `Concept`, which has N of them, so picking one is a step
        # Django's prefetch cannot express. See `gamelists/services/covers.py`.
        attach_cover_games(lists, per_list=LIST_TILE_COVERS)

        # The header renders `paginator.count` directly -- the number the page has already paid for.
        # There was a `total_lists` key here mirroring it, which no template read; a test asserted on
        # it and therefore proved nothing about the header. A second unfiltered `COUNT(*)` is the
        # thing to avoid, and not having one is what keeps the header agreeing with the grid.
        context['sort_choices'] = self.SORT_CHOICES
        context['current_sort'] = self._selected_sort()
        context['query'] = (self.request.GET.get('q') or '').strip()

        viewer = self._viewer()
        if viewer is not None and lists:
            # One query for the page's likes, not one per card. Bounded by the page slice.
            liked = set(
                GameListLike.objects
                .filter(profile=viewer, game_list__in=lists)
                .values_list('game_list_id', flat=True)
            )
            for game_list in lists:
                game_list.viewer_has_liked = game_list.pk in liked

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Game Lists'},
        ]
        context['seo_description'] = (
            'Browse game lists built by the Platinum Pursuit community: backlogs, rankings and '
            'runs in order.'
        )
        return context

    def _viewer(self):
        if not self.request.user.is_authenticated:
            return None
        return getattr(self.request.user, 'profile', None)


class _LinkedProfileRequired:
    """A list belongs to a PROFILE, so a signed-in account without one has nothing to show.

    Same shape and same message as `CareerView`, which is the other page whose entire content hangs
    off a linked profile. Without it, `request.user.profile` raises `RelatedObjectDoesNotExist` and
    the page 500s -- which a test caught here rather than a hunter catching it in production.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, 'Link your PSN account to build lists.')
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)


class MyListsView(_DevelopmentGate, LoginRequiredMixin, _LinkedProfileRequired,
                  HtmxListMixin, ListView):
    """Your own lists, and the ones you follow.

    ONE VIEW WITH A `scope`, not two pages -- the Career Board|History pattern: a single argument
    threaded through a single pipeline, no schema, and a URL that reloads into the state you left.

    The scope chips SWAP the panel rather than reloading the page, which is what every other tab
    group on this site does (Badges' Series|Gallery, Career's Jobs|Radar|Contracts). The first cut
    shipped them as plain links; an audit then flagged that `role="tablist"` was announcing a widget
    that did not exist, and I resolved that by making the SEMANTICS honest instead of making the
    BEHAVIOUR match the site -- the cheaper of the two fixes and the wrong one. With a real panel
    swap, `role="tab"` is simply true.

    This is also where following MEANS something today. The notification surface is not built yet,
    so for now a follow shows up here and nowhere else -- and building the follow button without this
    tab would have shipped a word with nowhere to land at all. The button says "Follow" rather than
    "Save" deliberately: it is named for where the feature is going, because a social verb is a word
    people learn and renaming one later costs more than the gap now.
    """

    template_name = 'gamelists/my_lists.html'
    partial_template_name = 'gamelists/partials/my_lists_results.html'
    context_object_name = 'game_lists'
    paginate_by = 24

    SCOPES = (
        ('mine', 'Mine'),
        ('following', 'Following'),
    )
    _DEFAULT_SCOPE = 'mine'

    def _scope(self):
        raw = self.request.GET.get('scope', self._DEFAULT_SCOPE)
        return raw if raw in dict(self.SCOPES) else self._DEFAULT_SCOPE

    def get_queryset(self):
        profile = self.request.user.profile
        if self._scope() == 'following':
            # `.public()` and not `.visible()`: a list you follow can be un-published by its author,
            # and following it must not become a private window into their library afterwards.
            return (
                GameList.objects.public()
                .filter(followers__profile=profile)
                .select_related('owner')
                .order_by('-updated_at')
            )
        # Your own INCLUDES your private ones -- that is the whole point of "mine".
        return GameList.objects.owned_by(profile).select_related('owner')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lists = context['game_lists']
        attach_cover_games(lists, per_list=LIST_TILE_COVERS)

        profile = self.request.user.profile
        scope = self._scope()
        context['scope'] = scope
        context['scopes'] = self.SCOPES
        context['is_mine'] = scope == 'mine'

        # The cap, shown rather than discovered by being refused. `owned_by` excludes soft-deleted
        # rows, which is the same count `create_list` enforces against -- so the number on screen and
        # the number the service will act on cannot disagree.
        # `paginator.count` is only the real total on the NON-scroll branches. `HtmxListMixin`'s
        # countless scroll path hands back a fake paginator over one page's rows (its docstring says
        # so out loud), so reusing it there would render "3/10" on a hunter with ten lists. The
        # header is full-page-only, but a wrong number that is merely unrendered is a trap for
        # whoever renders it next.
        if scope == 'mine' and not self._is_scroll_fetch():
            context['list_count'] = context['paginator'].count
        else:
            context['list_count'] = GameList.objects.owned_by(profile).count()
        context['list_cap'] = svc.max_lists_for(profile)
        context['at_cap'] = context['list_count'] >= context['list_cap']
        context['suggested_names'] = svc.SUGGESTED_NAMES
        # From the model, so the form's `maxlength` and the counter cannot drift from
        # what the service will accept.
        context['name_max_length'] = NAME_MAX_LENGTH
        context['description_max_length'] = DESCRIPTION_MAX_LENGTH

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Lists'},
        ]
        return context


class CreateListView(_DevelopmentGate, LoginRequiredMixin, _LinkedProfileRequired, View):
    """The create modal's POST target.

    A plain form post rather than JSON: creating a list is a navigation (you land on the new list),
    and a form that works without JavaScript is the cheaper, sturdier version of that. The modal is
    progressive enhancement over a form that would submit fine on its own.

    Every rule lives in the service -- the cap, the restriction gate, the linked-account check, the
    banned words, the sanitiser. This translates a refusal into a message and a redirect and does
    nothing else, which is the point of having the service at all.
    """

    def post(self, request):
        try:
            game_list = svc.create_list(
                request.user.profile,
                name=request.POST.get('name', ''),
                description=request.POST.get('description', ''),
                # Deliberately NOT read from the form. A list is private on creation and publishing
                # is a separate, deliberate act on the list itself -- the decision that makes the
                # public/private state mean something rather than being a checkbox you tick while
                # thinking about a name.
                is_public=False,
            )
        except svc.ListError as exc:
            messages.error(request, str(exc))
            return redirect('my_lists')

        messages.success(request, f'"{game_list.name}" is ready. Add some games to it.')
        return redirect('my_lists')


class GameListDetailView(_DevelopmentGate, DetailView):
    """One list, and where its owner edits it IN PLACE.

    THREE SURFACES, NOT FIVE. The system this replaces had a separate `/edit/` address, so renaming a
    list you were looking at cost three round trips. Editing happens here, where you can see the
    result -- which is also how the rest of the rebuilt site behaves.

    PUBLIC OR YOURS, and nothing else. `readable_by` is the single supported read, so a private list
    404s for everybody but its owner rather than 403ing -- a 403 confirms the list exists and who it
    belongs to, from nothing but an id.
    """

    model = GameList
    template_name = 'gamelists/detail.html'
    context_object_name = 'game_list'
    pk_url_kwarg = 'list_id'

    #: Sorts as DATA, one list read by the toolbar and the queryset both. Deliberately fewer than the
    #: thirteen the old page carried (of which four were unreachable): a curated list has an ORDER
    #: its author chose, so that is the default and the rest are ways to interrogate it.
    SORT_CHOICES = (
        ('position', 'List order'),
        ('name', 'A-Z'),
        ('added', 'Recently added'),
    )
    _DEFAULT_SORT = 'position'

    def get_queryset(self):
        # `select_related('owner')` for the byline; the cover art is attached per-item below.
        return GameList.objects.readable_by(self._viewer()).select_related('owner')

    def _viewer(self):
        if not self.request.user.is_authenticated:
            return None
        return getattr(self.request.user, 'profile', None)

    def _selected_sort(self):
        raw = self.request.GET.get('sort', self._DEFAULT_SORT)
        return raw if raw in dict(self.SORT_CHOICES) else self._DEFAULT_SORT

    def get_template_names(self):
        # The sort swap returns the items only. Same shape as the browse pages, and the same reason:
        # re-sorting a 200-game list should not re-render the header, the toolbar and the chrome.
        is_xhr = self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if self.request.htmx or is_xhr:
            return ['gamelists/partials/detail_items.html']
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        game_list = self.object
        viewer = self._viewer()

        sort = self._selected_sort()
        order = {
            'name': ('concept__unified_title',),
            'added': ('-added_at',),
        }.get(sort, ('position',))

        # `concept__igdb_match`, deferred, because the tile calls `concept.game_page_url` -- whose
        # own docstring says callers rendering many concepts must select_related it "or this walks
        # the FK per row". It did: one query per item, each dragging the ~30 KB `raw_response` blob.
        # The flatness test could not see it (it counts gamelists_ and trophies_game tables), which
        # is the same blind spot an audit flagged on the browse page; the raw_response guard caught
        # it instead.
        items = list(
            game_list.items
            .select_related('concept', 'concept__igdb_match')
            .defer('concept__igdb_match__raw_response')
            .order_by(*order)
        )
        # One batched query for every cover on the page, not one per row -- the same helper the
        # browse tiles use, for the same reason.
        covers = cover_games_for([item.concept_id for item in items])
        for item in items:
            item.cover = covers.get(item.concept_id)

        context['items'] = items
        context['sort'] = sort
        context['sort_choices'] = self.SORT_CHOICES
        context['is_owner'] = viewer is not None and game_list.owner_id == viewer.id
        context['can_act'] = viewer is not None and not context['is_owner']
        if context['can_act']:
            context['viewer_has_liked'] = GameListLike.objects.filter(
                game_list=game_list, profile=viewer).exists()
            context['viewer_follows'] = GameListFollow.objects.filter(
                game_list=game_list, profile=viewer).exists()

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Game Lists', 'url': reverse_lazy('lists_browse')},
            {'text': game_list.name},
        ]
        return context


# ── the write half ───────────────────────────────────────────────────────────────────────────────
#
# Plain Django views returning JSON rather than DRF, because these are page behaviour rather than a
# public API: they are gated by the same `_DevelopmentGate` the pages are, they answer one template's
# fetches, and routing them through DRF would mean a second permission stack that has to agree with
# the first. `PlatPursuit.API` is the client, so a non-2xx body reaches the caller as `.response`.
#
# EVERY rule lives in `game_list_service`. These translate a refusal into a status code and a message
# and do nothing else -- which is the entire point of having the service.


class _ListActionView(_DevelopmentGate, LoginRequiredMixin, _LinkedProfileRequired, View):
    """POST-only, resolves the list through `readable_by`, and answers JSON.

    `readable_by` and not `get_object_or_404` on the bare table: a private list must 404 for anybody
    who cannot see it, so an id alone can never confirm that a list exists or whose it is.
    """

    def get_list(self, request, list_id):
        """The list, or None -- the caller answers with `self.not_found()`.

        Deliberately NOT `raise Http404`. This project installs a custom `handler404` that is a
        GET-only view, so an Http404 raised from a POST comes back as a 405 listing GET/HEAD/OPTIONS
        rather than as a 404. `ContractsResultsView` documents the same workaround for the same
        reason. A caller checking for 404 would otherwise be told the wrong thing about what went
        wrong, and a JSON client would get an HTML error page.
        """
        return GameList.objects.readable_by(self._viewer(request)).filter(pk=list_id).first()

    def not_found(self):
        return JsonResponse({'error': 'That list is not available.'}, status=404)

    def _viewer(self, request):
        return getattr(request.user, 'profile', None)

    def fail(self, exc, status=400):
        return JsonResponse({'error': str(exc)}, status=status)


class ToggleLikeView(_ListActionView):
    def post(self, request, list_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()
        if game_list is None:
            return self.not_found()
        if game_list is None:
            return self.not_found()
        liked = request.POST.get('liked') == 'true'
        try:
            count = svc.set_like(game_list, self._viewer(request), liked=liked)
        except svc.ListError as exc:
            return self.fail(exc)
        return JsonResponse({'liked': liked, 'like_count': count})


class ToggleFollowView(_ListActionView):
    def post(self, request, list_id):
        game_list = self.get_list(request, list_id)
        following = request.POST.get('following') == 'true'
        try:
            count = svc.set_follow(game_list, self._viewer(request), following=following)
        except svc.ListError as exc:
            return self.fail(exc)
        return JsonResponse({'following': following, 'follower_count': count})


class AddConceptView(_ListActionView):
    def post(self, request, list_id):
        game_list = self.get_list(request, list_id)
        concept = Concept.objects.filter(pk=request.POST.get('concept_id')).first()
        if concept is None:
            return self.fail('That game could not be found.', status=404)
        try:
            item = svc.add_concept(game_list, self._viewer(request), concept,
                                   note=request.POST.get('note', ''))
        except svc.ListError as exc:
            return self.fail(exc)
        game_list.refresh_from_db()
        return JsonResponse({
            'item_id': item.pk,
            'game_count': game_list.game_count,
            'title': concept.unified_title,
        })


class RemoveItemView(_ListActionView):
    def post(self, request, list_id, item_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()
        item = GameListItem.objects.filter(pk=item_id, game_list=game_list).first()
        if item is None:
            return self.fail('That entry is no longer on this list.', status=404)
        try:
            svc.remove_concept(game_list, self._viewer(request), item)
        except svc.ListError as exc:
            return self.fail(exc)
        game_list.refresh_from_db()
        return JsonResponse({'game_count': game_list.game_count})


class ListGameSearchView(_DevelopmentGate, LoginRequiredMixin, _LinkedProfileRequired, View):
    """Typeahead for the adder: CONCEPTS, not trophy lists.

    Mirrors `SiteSuggestView`'s query shape rather than reusing it -- that view is the nav search and
    answers five rows per group across mixed entity types, which is the wrong shape for an adder --
    but it rides the same `pg_trgm` GIN index on `Concept.unified_title` (migration 0257) and defers
    `raw_response` for the same reason.

    Deliberately NOT `Game.title_name`, which the old list search used: that is the trophy-list name,
    it is unreliable for matching, and it would return one row per stack for a game somebody wants to
    add once.
    """

    LIMIT = 12
    MIN_QUERY = 2

    def get(self, request, list_id):
        game_list = GameList.objects.readable_by(
            getattr(request.user, 'profile', None)).filter(pk=list_id).first()
        if game_list is None:
            # JSON rather than Http404: this answers a fetch, and the project's handler404 renders
            # an HTML page (at 200, per its own documented quirk) which no JSON caller can read.
            return JsonResponse({'error': 'That list is not available.'}, status=404)

        query = (request.GET.get('q') or '').strip()
        if len(query) < self.MIN_QUERY:
            return JsonResponse({'results': []})

        already = set(
            GameListItem.objects.filter(game_list=game_list).values_list('concept_id', flat=True)
        )
        concepts = list(
            Concept.objects.filter(unified_title__icontains=query)
            .exclude(unified_title='')
            .select_related('igdb_match')
            .defer('igdb_match__raw_response')
            .order_by('unified_title')[:self.LIMIT]
        )
        covers = cover_games_for([c.pk for c in concepts])
        return JsonResponse({'results': [
            {
                'concept_id': concept.pk,
                'title': concept.unified_title,
                'cover': covers[concept.pk].display_image_url if concept.pk in covers else '',
                # Marked rather than filtered out: a hunter searching for something already on the
                # list should be told it is there, not left wondering why it does not appear.
                'already_added': concept.pk in already,
            }
            for concept in concepts
        ]})
