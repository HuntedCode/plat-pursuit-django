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
from django.core.cache import cache
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.db.models.functions import Lower
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.generic import DetailView, ListView, View

from django_ratelimit.decorators import ratelimit

from api.utils import safe_bool, safe_int
from gamelists.models import (DESCRIPTION_MAX_LENGTH, LIST_TYPE_COLLECTION, LIST_TYPE_RANKED,
                              NAME_MAX_LENGTH, GameList, GameListFollow, GameListItem,
                              GameListLike, list_type_options)
from gamelists.services import game_list_service as svc
from gamelists.services.covers import attach_cover_games, cover_games_for
from trophies.mixins import HtmxListMixin, StaffRequiredMixin
from trophies.models import Concept

#: How many covers the `.pp-gtile` mosaic composes around (`is-1` .. `is-4`).
LIST_TILE_COVERS = 4

#: How many entries a list page renders at once. Not a cap on the list -- lists are uncapped, which
#: is deliberate -- but a bound on one render, so a very long list is slow to page through rather
#: than able to exhaust a worker. Real pagination here is a follow-up; this is the floor under it.
MAX_ITEMS_RENDERED = 200

#: Ceiling for the game-count filter. Anything above it is treated as "no filter" rather than passed
#: to the database: a 40-digit number compared against a PositiveIntegerField is backend-dependent
#: behaviour for a query nobody meant to run.
MAX_GAME_COUNT_FILTER = 10_000

#: Ceiling on any free-text search term before it reaches a LIKE. Shared by the browse filter and the
#: adder's typeahead so the two cannot drift: an unbounded `q` is an unbounded pattern, and the
#: browse page becomes anonymous the moment `_DevelopmentGate` comes off.
MAX_QUERY_LENGTH = 64


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

        # BOUNDED, like the typeahead in this same file. An unbounded `q` becomes an unbounded
        # LIKE pattern across three columns and a join to Profile, on the surface that becomes
        # ANONYMOUS the moment `_DevelopmentGate` comes off. `_count_filter` below bounds the
        # numeric filters and none of that discipline had reached the text one.
        query = (self.request.GET.get('q') or '').strip()[:MAX_QUERY_LENGTH]
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
            # `Lower()` is the house rule for every front-facing name column, but the reason written
            # here was wrong and is worth correcting rather than deleting: it claimed Postgres files
            # "apex" after "Zenith" without it. Not on this database -- `lc_collate` is `en_US.utf8`,
            # whose collation already ignores case for ordering, so raw and `lower()` return the same
            # order (verified against the server, not assumed). That is only true under the `C`
            # collation. Keep `Lower()` because it makes the order explicit and
            # collation-INDEPENDENT, not because the default would otherwise be wrong here.
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

        # Does the empty state offer "Clear filters" or "Make a list"? Those are opposite answers to
        # opposite situations -- "you narrowed it to nothing" versus "there is nothing yet" -- and
        # offering the wrong one is worse than offering neither.
        #
        # Read through the SAME parsers the queryset uses, not the raw querystring: `_count_filter`
        # discards junk, so `?min_games=abc` narrows nothing and must not claim a filter is on. Sort
        # is excluded on purpose -- re-ordering an empty grid is not what emptied it.
        context['has_filters'] = bool(
            context['query']
            or _count_filter(self.request.GET.get('min_games')) is not None
            or _count_filter(self.request.GET.get('max_games')) is not None
        )

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
        # so out loud), so reusing it there would render one page's worth as the total -- a member
        # with 25 lists would read "24/25" and think they were one from the cap. The
        # header is full-page-only, but a wrong number that is merely unrendered is a trap for
        # whoever renders it next.
        if scope == 'mine' and not self._is_scroll_fetch():
            context['list_count'] = context['paginator'].count
        else:
            context['list_count'] = GameList.objects.owned_by(profile).count()
        context['list_cap'] = svc.max_lists_for(profile)
        context['at_cap'] = context['list_count'] >= context['list_cap']
        context['suggested_names'] = svc.SUGGESTED_NAMES
        # The type picker's options come from the model, so the dialog cannot offer a type the
        # service would refuse -- and a type added there appears here without a template edit.
        context['list_type_options'] = list_type_options()
        # From the model, so the form's `maxlength` and the counter cannot drift from
        # what the service will accept.
        context['name_max_length'] = NAME_MAX_LENGTH
        context['description_max_length'] = DESCRIPTION_MAX_LENGTH

        # `Game Lists` is LINKED here, which it was not: this page was the one dead end in the
        # feature. Browse carries a button to My Lists, the detail page's crumb carries a link back
        # to browse, and My Lists carried nothing -- so arriving here left no route to everyone
        # else's lists short of the back button.
        #
        # Fixed in the breadcrumb rather than with another toolbar control. The crumb is the
        # structural answer (the parent link was simply absent, and the detail page already has the
        # identical trail), it is present on every state of the page including the empty ones, and it
        # does not add a third item to a row that already holds the create action and the switcher at
        # 375px. When the Community hub is re-formed, its sub-nav carries both directions and the
        # toolbar link on browse goes away too -- so a second temporary button would be churn.
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Game Lists', 'url': reverse_lazy('lists_browse')},
            {'text': 'My Lists'},
        ]
        return context


class CreateListView(_DevelopmentGate, LoginRequiredMixin, _LinkedProfileRequired, View):
    """The create modal's POST target.

    RATE LIMITED like every other write here, which it was not. The cap (3 free / 25 member) bounds
    the steady state but not the RATE: `delete_list` is a soft delete that frees a slot immediately,
    so create-delete-create is an unthrottled INSERT loop that also runs the fixpoint sanitiser and
    the banned-word scan on every pass.

    A plain form post rather than JSON: creating a list is a navigation (you land on the new list),
    and a form that works without JavaScript is the cheaper, sturdier version of that. The modal is
    progressive enhancement over a form that would submit fine on its own.

    Every rule lives in the service -- the cap, the restriction gate, the linked-account check, the
    banned words, the sanitiser. This translates a refusal into a message and a redirect and does
    nothing else, which is the point of having the service at all.
    """

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
    def post(self, request):
        try:
            game_list = svc.create_list(
                request.user.profile,
                name=request.POST.get('name', ''),
                description=request.POST.get('description', ''),
                # Unlike `is_public`, the TYPE is read from the form: it decides what the list is
                # for, so it is part of making one. The service validates it, so a hand-posted value
                # cannot reach the column. Absent means Collection, which is what a plain form post
                # from a browser with no JS sends.
                list_type=request.POST.get('list_type') or LIST_TYPE_COLLECTION,
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
    # A COLLECTION IS UNORDERED. It has no curated sequence, so it offers no "List order" and no
    # drag: what a hunter wants from a shelf is to find things on it, which is what A-Z is for.
    # An ordered list is a different TYPE -- Ranked, which adds `rank` below and nothing else.
    # `GameListItem.position` stays and stays DENSE for both: it is insertion order on a Collection
    # and the author's sequence on a Ranked list, and `attach_cover_games` bounds the tile mosaic on
    # `position__lt=4` either way.
    SORT_CHOICES = (
        ('name', 'A-Z'),
        ('name_desc', 'Z-A'),
        ('added', 'Recently added'),
        ('oldest', 'First added'),
    )
    #: A RANKED list leads with the order its author chose, and keeps every other sort as a way to
    #: interrogate it. "List order" is first because it is the default and because it is the only one
    #: that is the list's actual content rather than a view of it.
    RANKED_SORT_CHOICES = (('rank', 'List order'),) + SORT_CHOICES
    _DEFAULT_SORT = 'name'
    _RANKED_DEFAULT_SORT = 'rank'

    def get_queryset(self):
        # `select_related('owner')` for the byline; the cover art is attached per-item below.
        return GameList.objects.readable_by(self._viewer()).select_related('owner')

    def _viewer(self):
        if not self.request.user.is_authenticated:
            return None
        return getattr(self.request.user, 'profile', None)

    def _is_ranked(self):
        return self.object.list_type == LIST_TYPE_RANKED

    def _sort_choices(self):
        """The toolbar's options, which depend on the TYPE.

        `rank` is offered by ranked lists only. A Collection that accepted `?sort=rank` would answer
        200 and order by insertion, which is not a curated sequence and would read as one.
        """
        return self.RANKED_SORT_CHOICES if self._is_ranked() else self.SORT_CHOICES

    def _default_sort(self):
        return self._RANKED_DEFAULT_SORT if self._is_ranked() else self._DEFAULT_SORT

    def _selected_sort(self):
        default = self._default_sort()
        raw = self.request.GET.get('sort', default)
        return raw if raw in dict(self._sort_choices()) else default

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
        # `Lower()` on the title sorts. NOT for the reason usually given: this database is
        # `en_US.utf8`, whose collation already ignores case for ordering, so a raw column sort
        # produces the same rows in the same order (checked directly -- raw, `lower()` and the
        # documented claim disagree only under the `C` collation, which files every uppercase title
        # before every lowercase one). The real reason to keep it is that it makes the ordering
        # explicit and collation-INDEPENDENT, so a restore into a differently-collated cluster does
        # not silently reshuffle the page.
        #
        # `position` tiebreaks EVERY sort, not just the date ones. `added_at` is `auto_now_add`, set
        # in Python, so ties are rare rather than impossible and a bulk-add path would tie outright;
        # and two concepts can share a title outright. Without a total order the same list renders in
        # two different orders on two loads, which reads as a bug and cannot be reproduced on demand.
        title = Lower('concept__unified_title')
        order = {
            'rank': ('position',),
            'name': (title.asc(), 'position'),
            'name_desc': (title.desc(), 'position'),
            'added': ('-added_at', 'position'),
            'oldest': ('added_at', 'position'),
        }.get(sort, (title.asc(), 'position'))

        # `concept__igdb_match`, deferred, because the tile calls `concept.game_page_url` -- whose
        # own docstring says callers rendering many concepts must select_related it "or this walks
        # the FK per row". It did: one query per item, each dragging the ~30 KB `raw_response` blob.
        # The flatness test could not see it (it counts gamelists_ and trophies_game tables), which
        # is the same blind spot an audit flagged on the browse page; the raw_response guard caught
        # it instead.
        # BOUNDED. There is no cap on list SIZE (members always had unlimited, and removing that
        # was a perk takeback), so the row count here is attacker-controlled: one account can build a
        # 50,000-item list and hand out the URL. `cover_games_for` then fetches every Game row for
        # every concept -- several per concept once stacks are counted -- which is the largest
        # allocation on the page and invisible to a query-COUNT test, because the shape stays O(1)
        # while the bytes do not. CLAUDE.md's whale rule names exactly this: an explicit `[:N]` slice
        # is the acceptable form. The overflow is surfaced rather than silently dropped.
        # ONE EXTRA ROW, and truncation read from the ROWS rather than from `game_count`.
        #
        # `game_count > len(items)` looked equivalent and was not, because that counter drifts HIGH
        # and nothing repairs it: `GameListItem.concept` is CASCADE, so deleting a Concept removes
        # rows with no service involvement and `_recount` (whose docstring says so) only ever runs
        # from add and remove. A six-game ranked list that lost one concept then reported itself
        # truncated forever -- which cost a wrong sentence before Ranked, and costs the owner their
        # drag handles now, permanently, with the page explaining that the list is too long to
        # reorder. No user action clears it.
        #
        # Fetching `MAX_ITEMS_RENDERED + 1` answers "is there more?" from the data itself, is correct
        # in both drift directions, and costs one row.
        items = list(
            game_list.items
            .select_related('concept', 'concept__igdb_match')
            .defer('concept__igdb_match__raw_response')
            .order_by(*order)[:MAX_ITEMS_RENDERED + 1]
        )
        context['items_truncated'] = len(items) > MAX_ITEMS_RENDERED
        del items[MAX_ITEMS_RENDERED:]
        context['items_shown'] = len(items)
        # One batched query for every cover on the page, not one per row -- the same helper the
        # browse tiles use, for the same reason.
        covers = cover_games_for([item.concept_id for item in items])
        for item in items:
            item.cover = covers.get(item.concept_id)

        context['items'] = items
        context['sort'] = sort
        context['sort_choices'] = self._sort_choices()
        context['is_ranked'] = self._is_ranked()
        context['is_owner'] = viewer is not None and game_list.owner_id == viewer.id
        # DRAG IS OFFERED ONLY WHERE IT CAN SUCCEED, and each clause closes a way it could not.
        #
        # `sort == 'rank'` -- dragging row 3 above row 1 while the page is sorted A-Z posts an order
        # that means nothing, because what the hunter rearranged was a VIEW of the list rather than
        # the list. The affordance belongs to the one sort that shows the real sequence. This clause
        # also carries the type: `rank` is only ever selected on a ranked list, because
        # `_selected_sort` clamps to `_sort_choices()` and only the ranked set contains it.
        #
        # An `is_ranked` clause stood here too and was removed as dead weight. Mutation testing
        # showed no test could kill it -- which was not a gap in the tests: given the clamp above,
        # there is no reachable state where `sort == 'rank'` and the list is not ranked, so the
        # branch could never be false when the rest were true. A condition that cannot change an
        # outcome reads as a safety net and is only a place for a future reader to be confused.
        # The clamp is the real guard and `test_a_collection_cannot_reach_the_rank_sort_by_url`
        # pins it.
        #
        # `not items_truncated` -- `svc.reorder` refuses a partial ordering by design (a subset means
        # the client and the server disagree about what is on the list, and applying it would drop
        # the rest), so past `MAX_ITEMS_RENDERED` the page cannot post a complete one. Offering a
        # handle there would give every drag a refusal. `ReorderItemsView`'s own docstring asks
        # callers to withhold the affordance rather than build one whose every use fails.
        context['can_reorder'] = (
            context['is_owner']
            and sort == 'rank'
            and not context['items_truncated']
        )
        # `is_linked`, not merely "has a profile". The action ENDPOINTS carry
        # `_LinkedProfileRequired`, which 302s to link_psn -- so without this an unlinked viewer was
        # shown both buttons and got an HTML redirect back from a JSON fetch. The page and the
        # endpoint have to agree about who can act.
        context['can_act'] = (
            viewer is not None and viewer.is_linked and not context['is_owner'])
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

        # The in-place edit form needs the same ceilings the create dialog uses. Without them
        # `maxlength="{{ name_max_length }}"` renders empty, browsers ignore it, and the character
        # counter has no ceiling to count against -- so the field silently accepts more than the
        # service will store and the first a hunter hears of it is a refusal on save.
        context['name_max_length'] = NAME_MAX_LENGTH
        context['description_max_length'] = DESCRIPTION_MAX_LENGTH
        context['list_type_options'] = list_type_options()
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


class UpdateListView(_ListActionView):
    """Rename, re-describe, switch type and publish -- the owner's edits, through one endpoint.

    One view rather than four because they are one service call. `update_list` already takes each
    field optionally and touches only what it is given, and splitting them would mean four
    permission stacks that have to agree with each other forever.

    `list_type` rides `FIELDS` rather than getting a branch of its own: unlike `is_public` it needs
    no coercion (the service validates it against the types that render) and unlike a boolean it has
    no "absent means false" trap.

    `'field' in request.POST` rather than `.get('field')`: an empty description is a real edit ("clear
    it"), and `.get()` cannot tell that apart from "not sent". Absent means leave alone; present and
    empty means set to empty.
    """

    FIELDS = ('name', 'description', 'list_type')

    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, list_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()

        fields = {name: request.POST[name] for name in self.FIELDS if name in request.POST}
        if 'is_public' in request.POST:
            # `safe_bool`, not `== 'true'`. The bare comparison read 'True', '1', 'on' and 'yes' as
            # FALSE, so anything but the exact lowercase literal silently took a published list back
            # down and answered 200 -- verified: posting `is_public=on`, which is what a plain HTML
            # checkbox sends, un-published a live list. Fail-closed is the safe direction for a
            # privacy control, but "safe" is not the same as "correct", and destroying somebody's
            # publication without telling them is its own harm.
            fields['is_public'] = safe_bool(request.POST['is_public'])
        if not fields:
            return self.fail('Nothing to change.')

        try:
            updated = svc.update_list(game_list, self._viewer(request), **fields)
        except svc.ListError as exc:
            return self.fail(exc)

        # The STORED values, not the submitted ones. `_check_name` sanitizes and trims, so what the
        # hunter typed and what the list now holds are not always the same string -- and a client
        # that re-renders its own input would show a name the database does not have.
        return JsonResponse({
            'name': updated.name,
            'description': updated.description,
            'is_public': updated.is_public,
            'list_type': updated.list_type,
        })


class ReorderItemsView(_ListActionView):
    """Set the list's order to exactly the posted ids.

    REACHED BY RANKED LISTS ONLY. A Collection is unordered -- it offers A-Z and date sorts and no
    drag -- so the handle appears only on `list_type='ranked'`, and only for the owner, and only
    while the page is showing the real sequence rather than a sort of it. This endpoint was built
    ahead of that UI and kept dormant rather than deleted, and the server half needed no changes when
    the client half arrived.

    The service refuses a partial ordering rather than applying it, which is right -- a subset means
    the client and the server disagree about what is on the list, and applying it would silently drop
    whatever the client did not send. That means a list longer than `MAX_ITEMS_RENDERED` cannot be
    reordered from a rendered page, so `can_reorder` withholds the affordance there rather than
    offering one whose every use would be refused.
    """

    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, list_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()

        # `getlist`, because an order is a sequence and a single-entry list must still arrive as one.
        item_ids = request.POST.getlist('item_ids[]') or request.POST.getlist('item_ids')
        if not item_ids:
            return self.fail('That order is not valid. Reload and try again.')

        try:
            svc.reorder(game_list, self._viewer(request), item_ids)
        except svc.ListError as exc:
            return self.fail(exc)
        return JsonResponse({'ordered': len(item_ids)})


class ToggleLikeView(_ListActionView):
    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, list_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()
        liked = request.POST.get('liked') == 'true'
        try:
            count = svc.set_like(game_list, self._viewer(request), liked=liked)
        except svc.ListError as exc:
            return self.fail(exc)
        return JsonResponse({'liked': liked, 'like_count': count})


class ToggleFollowView(_ListActionView):
    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, list_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()
        following = request.POST.get('following') == 'true'
        try:
            count = svc.set_follow(game_list, self._viewer(request), following=following)
        except svc.ListError as exc:
            return self.fail(exc)
        return JsonResponse({'following': following, 'follower_count': count})


class AddConceptView(_ListActionView):
    @method_decorator(ratelimit(key='user', rate='120/m', method='POST', block=True))
    def post(self, request, list_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()

        # `safe_int`, not the raw POST value: `Concept.objects.filter(pk='abc')` raises ValueError,
        # which is a 500 from a form field anyone can post. This file already carries `_count_filter`,
        # written after `'\u00b2'.isdigit()` took the browse page down, and none of that discipline
        # had reached here.
        #
        # This comment used to ALSO claim a 20-digit id raises DataError. It does not -- Django 5.2
        # absorbs an out-of-range pk and returns an empty queryset (checked directly, which is why
        # the guard that was written for it was deleted rather than kept as insurance). The test
        # pins the behaviour so a future Django that stops absorbing it fails loudly here.
        concept_id = safe_int(request.POST.get('concept_id'), None)
        concept = (Concept.objects.filter(pk=concept_id).first()
                   if concept_id is not None else None)
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
    @method_decorator(ratelimit(key='user', rate='120/m', method='POST', block=True))
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

    Mirrors `SiteSuggestView`'s query shape rather than reusing it -- that view is the nav search,
    answering five rows per group across mixed entity types, which is the wrong shape for an adder.

    THE INDEX CLAIM THAT WAS HERE WAS FALSE, and worth recording rather than quietly deleting. It
    said this "rides the pg_trgm GIN index on Concept.unified_title". It does not: Django compiles
    `__icontains` on Postgres to `UPPER(col::text) LIKE UPPER(%q%)` (verified, not assumed), and a
    `gin_trgm_ops` index on the RAW column cannot serve a LIKE against a function of that column.
    There is no expression index on `UPPER(unified_title::text)` anywhere in the tree, so this is a
    sequential scan of the catalogue. `SiteSuggestView` has exactly the same shape -- fixing that,
    and deciding whether `trophies_concept` gains an expression index, is a change to shared
    catalogue infrastructure and belongs in its own lane with EXPLAIN output from production.

    What this view CAN do is stop being the dangerous consumer of that shape, by taking the three
    protections `SiteSuggestView` has and this copied none of: a rate limit, a short cache keyed on
    the normalized query, and an upper bound on `q`. `MIN_QUERY` is 3 rather than 2 because pg_trgm
    extracts no trigrams from a two-character pattern, so a 2-char query is a guaranteed full pass
    even once the index question is settled.

    Deliberately NOT `Game.title_name`, which the old list search used: that is the trophy-list name,
    it is unreliable for matching, and it would return one row per stack for a game somebody wants to
    add once.
    """

    LIMIT = 12
    MIN_QUERY = 3
    MAX_QUERY = MAX_QUERY_LENGTH          # shared with the browse filter, so the two cannot drift
    CACHE_TTL = 60

    @method_decorator(ratelimit(key='user', rate='120/m', method='GET', block=True))
    def get(self, request, list_id):
        game_list = GameList.objects.readable_by(
            getattr(request.user, 'profile', None)).filter(pk=list_id).first()
        if game_list is None:
            # JSON rather than Http404: this answers a fetch, and `handler404` is a GET-only
            # TemplateView, so raising from here would render an HTML page a JSON caller cannot
            # read. (An earlier version of this comment also claimed that handler renders at 200.
            # It does not -- `NotFoundView` sets status 404 explicitly. The reason to avoid Http404
            # is the method, not the status.)
            return JsonResponse({'error': 'That list is not available.'}, status=404)

        query = (request.GET.get('q') or '').strip()
        if len(query) > self.MAX_QUERY:
            # An unbounded `q` becomes an unbounded LIKE pattern. `SiteSuggestView` refuses these.
            return JsonResponse({'error': 'That search is too long.'}, status=400)
        if len(query) < self.MIN_QUERY:
            return JsonResponse({'results': []})

        # Cached on the normalized query, NOT on the list: the catalogue half of the answer is the
        # same for everybody, and it is the expensive half. `already_added` is per-list and applied
        # after the cache, so one hunter's list never leaks into another's results.
        cache_key = f'gamelists:search:{query.lower()}'
        cached = cache.get(cache_key)
        if cached is None:
            concepts = list(
                Concept.objects.filter(unified_title__icontains=query)
                .exclude(unified_title='')
                .select_related('igdb_match')
                .defer('igdb_match__raw_response')
                .order_by('unified_title')[:self.LIMIT]
            )
            covers = cover_games_for([c.pk for c in concepts])
            cached = [
                {
                    'concept_id': concept.pk,
                    'title': concept.unified_title,
                    'cover': covers[concept.pk].display_image_url if concept.pk in covers else '',
                }
                for concept in concepts
            ]
            cache.set(cache_key, cached, self.CACHE_TTL)

        already = set(
            GameListItem.objects.filter(game_list=game_list).values_list('concept_id', flat=True)
        )
        # Marked rather than filtered out: a hunter searching for something already on the list
        # should be told it is there, not left wondering why it does not appear.
        return JsonResponse({'results': [
            dict(row, already_added=row['concept_id'] in already) for row in cached
        ]})
