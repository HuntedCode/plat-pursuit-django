"""Game Lists surfaces.

THREE PAGES, NOT FIVE (settled with the owner before the rebuild): Browse, List detail where the
owner edits in place, and My Lists with create as a modal. The system this replaces had separate
create and edit ADDRESSES, which meant three round trips to rename a list you were looking at.

LIVE SINCE 2026-09. `_DevelopmentGate` -- a `StaffRequiredMixin` subclass that sat on all six view
classes -- is gone, and `test_lists_hidden.py` was inverted into `test_lists_live.py` in the same
commit. Removing the mixin was necessary and not sufficient: the sitemap had to be re-pointed off
the LEGACY `trophies.GameList` first (it reversed `list_detail`, which resolves to this app, so it
would have published thousands of legacy ids against new-app routes), robots needed rules for the
personal and POST-only paths, and the detail page needed its own `seo_description`.
"""
from django.contrib import messages
from django.core.cache import cache
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Case, CharField, F, Q, Value, When
from django.db.models.functions import Lower
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.generic import DetailView, ListView, View

from django_ratelimit.decorators import ratelimit

from api.utils import safe_bool, safe_int
from core.previews import previewing
from core.services.tracking import track_site_event
from gamelists.models import (DESCRIPTION_MAX_LENGTH, LIST_TYPE_COLLECTION, LIST_TYPE_RANKED,
                              MAX_ITEMS_PER_LIST, NAME_MAX_LENGTH, SECTION_NAME_MAX_LENGTH,
                              GameList, GameListFollow, GameListItem, GameListLike,
                              GameListReport, GameListSection,
                              list_type_options)
from gamelists.services import game_list_service as svc
from gamelists.services.covers import attach_cover_games, cover_games_for
from gamelists.services.game_search import (SearchRefused, page_key_expression, page_key_filter,
                                            search_concepts)
from trophies.mixins import HtmxListMixin
from trophies.models import Concept

#: How many covers the `.pp-gtile` mosaic composes around (`is-1` .. `is-4`).
LIST_TILE_COVERS = 4

#: How many covers the Spotlight's reel fetches. Deeper than the tile's mosaic on purpose: the reel
#: runs off the right edge under a fade, and the overflow IS the effect -- it says "there is more of
#: this list" where a strip that stops short of the edge just looks unfinished. Eight fills the reel
#: at desktop widths with a cover or two still cut off.
SPOTLIGHT_COVERS = 8

#: ONE BUDGET FOR ONE ACT, shared by both doors that create a list.
#:
#: `django_ratelimit` derives its group from module+qualname when `group=` is omitted, so two views
#: implementing the same act get two independent buckets -- the real create allowance was 60/m, not
#: the 30/m each decorator appears to state, and each pass runs the fixpoint sanitiser, the
#: banned-word scan and a list COUNT. `api/game_flag_views.py` shares a group across its two doors
#: for exactly this reason and says why: "one deliberate act by a person, one unit of budget".
CREATE_LIST_RATELIMIT_GROUP = 'gamelists-create'

#: How many entries a list page renders at once. DERIVED from the size cap rather than written out
#: again, because the two being equal is the design: a list cannot exceed what one page shows, so no
#: list is ever truncated, every list is fully reorderable, and section counts are always the real
#: ones. See `MAX_ITEMS_PER_LIST` for the argument.
#:
#: DECOUPLING THESE RE-CREATES A BUG FAMILY, so do it deliberately or not at all. When a list could
#: outgrow one render, the page carried a truncation notice, `can_reorder` carried a clause for it,
#: the section counts had to be omitted rather than shown wrong, and two defects came out of exactly
#: those branches. Raising the ceiling is fine -- raise BOTH, here, and they stay equal. Genuine
#: pagination is the other way to break the tie, and that is a real project rather than a constant.
MAX_ITEMS_RENDERED = MAX_ITEMS_PER_LIST

#: Ceiling for the game-count filter. Anything above it is treated as "no filter" rather than passed
#: to the database: a 40-digit number compared against a PositiveIntegerField is backend-dependent
#: behaviour for a query nobody meant to run.
MAX_GAME_COUNT_FILTER = 10_000

#: Ceiling on any free-text search term before it reaches a LIKE. Shared by the browse filter and the
#: adder's typeahead so the two cannot drift: an unbounded `q` is an unbounded pattern, and the
#: browse page is ANONYMOUS as of 2026-09, which is what this bound is for.
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


@method_decorator(
    # RATE-LIMITED BY IP, because this is the anonymous one. The typeahead below carries a limit, a
    # 60s cache and a 3-character floor; this page shared only the LENGTH bound with it, and it is
    # the path that needs them most -- `?q=` compiles to three `UPPER(col) LIKE '%x%'` scans plus a
    # join to Profile, no index serves it, and there is no login in front. `key='ip'` rather than
    # `key='user'`: the latter buckets every anonymous caller under one key, which the project
    # already uses `key='ip'` to avoid on its other public GET.
    ratelimit(key='ip', rate='60/m', method='GET', block=True), name='get')
class BrowseListsView(HtmxListMixin, ListView):
    """Public lists from every hunter, newest or most-liked first."""

    #: Matching the typeahead's floor, and for the reason its docstring gives: a one- or
    #: two-character `%x%` is a guaranteed full scan that returns most of the table. Below this the
    #: term is ignored rather than refused -- a browse page is not a form.
    MIN_QUERY = 3

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

    def _effective_query(self):
        """The `?q=` that actually NARROWS THE GRID, which is not the same string the reader typed.

        BOUNDED, like the typeahead in this same file. An unbounded `q` becomes an unbounded LIKE
        pattern across three columns and a join to Profile, on the surface that becomes ANONYMOUS as
        of 2026-09. `_count_filter` bounds the numeric filters and none of that discipline had
        reached the text one.

        EXTRACTED because two callers were deciding this independently and disagreeing. `get_queryset`
        clamped; `has_filters` read the raw string. So a one or two character `q` filtered NOTHING
        while the page believed a filter was on -- which mis-worded the empty state, and then, once
        the Spotlight arrived, made the band vanish on the first two keystrokes of live search and
        stay gone until the third. One definition, two readers.

        The raw string stays in `context['query']` on purpose: it is what the search inputs bind to,
        and clamping THAT would delete the reader's own typing out from under them mid-word.
        """
        query = (self.request.GET.get('q') or '').strip()[:MAX_QUERY_LENGTH]
        return '' if len(query) < self.MIN_QUERY else query

    def get_queryset(self):
        # `.public()` rather than a hand-written `is_public=True, is_deleted=False`: it is the one
        # supported read path for somebody else's list, and it matches the partial index predicate
        # exactly, so the safe way is also the indexed way.
        queryset = GameList.objects.public().select_related('owner')

        query = self._effective_query()
        if query:
            # THE TEXT CLAUSES SKIP MODERATED LISTS, the owner clause does not.
            #
            # Searching the raw columns made hidden words queryable by anyone: the tile comes back
            # reading "Untitled list", but the fact that it MATCHED confirms the string is in
            # there, and `data-result-count` leaks the same signal without even rendering a tile.
            # A slur could be reconstructed substring by substring on an anonymous page. Hiding
            # the words has to hide them from the index too, or it only hides them from readers
            # who were not looking.
            #
            # A hidden list stays findable by its OWNER'S NAME, which is not the moderated text and
            # is how somebody gets back to a list they know exists.
            queryset = queryset.filter(
                Q(name__icontains=query, text_hidden=False)
                | Q(description__icontains=query, text_hidden=False)
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
            #
            # A MODERATED LIST SORTS UNDER ITS PLACEHOLDER, not its real name. Excluding hidden
            # lists from the search was only half the channel: a hidden list still took its RAW
            # alphabetical position, wedged between two visible names, so an anonymous reader
            # could read its leading characters off by bisection -- one dropdown click from the
            # surface that was just closed. Weaker than `icontains`, same class, same page.
            # `Value` of the placeholder keeps them together at one predictable spot instead.
            #
            # `-pk` AS A TIEBREAK, and it matters more here than it looks. Nothing stops two lists
            # sharing a name (the model says so), but collapsing every hidden list onto the single
            # key 'untitled list' turns a rare tie into a guaranteed one for the whole moderated
            # set -- and ties across a LIMIT/OFFSET boundary are non-deterministic in Postgres, so
            # a reader paging through would see one twice and miss another. `featured()` and
            # `cover_games_for` both added a pk tiebreak for exactly this; this ordering was
            # written without one.
            return queryset.order_by(
                Lower(Case(When(text_hidden=True, then=Value(GameList.HIDDEN_NAME)),
                           default=F('name'), output_field=CharField())), '-pk')
        return queryset.order_by(*self._ORDERING[sort])

    def _spotlight(self):
        """The featured list shown above the grid, or None.

        ONE GATE: PARTIAL RENDERS. The band lives OUTSIDE `#browse-results`, so an HTMX filter swap
        and an InfiniteScroller `?page=` fetch both render the grid partial and never render this.
        But `get_context_data` still runs for them, so without this check the query would fire on
        every keystroke of live search and every scroll page, to build a value that is thrown away.
        `is_partial_render()` is the mixin's OWN test, the one `get_template_names` uses to make
        that decision -- asked rather than re-implemented, so the two cannot drift apart.

        THE FILTER GATE USED TO LIVE HERE AND WAS WRONG, in a way no server test could see. A
        filtered page must not SHOW the band, and returning None achieved that on a full render --
        but live search does not do full renders. It swaps `#browse-results`, and the band is
        outside that target, so the band the server had already sent simply stayed on screen above
        somebody's search results. The gate worked on the one path nobody takes interactively.

        So the band is now fetched for every full render and the TEMPLATE decides whether it starts
        collapsed, with `lists-browse.js` collapsing and restoring it as filters come and go. That
        also fixes the other half: landing on `?q=soulslike` and then clearing the box used to leave
        no band at all, because none had ever been rendered to reveal.

        The cost is one indexed lookup on a filtered FULL page load -- a direct link or an Enter
        press, not the hot path. Partial renders, which are the per-keystroke ones, still pay
        nothing.

        `select_related('owner')` is for the byline's mark, which reads `owner.display_mark`.
        """
        if self.is_partial_render():
            return None

        featured = GameList.objects.featured().select_related('owner').first()
        if featured is not None:
            # ITS OWN CALL, at a DEEPER slice than the grid's. This was briefly folded into the
            # grid's call to save two queries, which was right while both wanted four covers -- the
            # band showed the same mosaic a tile does, so a second fetch was the same rows twice.
            # The band now renders a REEL of `SPOTLIGHT_COVERS`, so the two want different depths
            # and the calls are no longer redundant. Merging them again would mean fetching eight
            # covers for all twenty-four grid lists to serve one band.
            attach_cover_games([featured], per_list=SPOTLIGHT_COVERS)
        return featured

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lists = context['game_lists']

        # The header renders `paginator.count` directly -- the number the page has already paid for.
        # There was a `total_lists` key here mirroring it, which no template read; a test asserted on
        # it and therefore proved nothing about the header. A second unfiltered `COUNT(*)` is the
        # thing to avoid, and not having one is what keeps the header agreeing with the grid.
        context['sort_choices'] = self.SORT_CHOICES
        context['current_sort'] = self._selected_sort()
        # RAW, unlike everything that follows: this is what the two search inputs bind to, so
        # clamping it would erase a reader's own typing mid-word. What FILTERS is
        # `_effective_query()`.
        context['query'] = (self.request.GET.get('q') or '').strip()

        # Does the empty state offer "Clear filters" or "Make a list"? Those are opposite answers to
        # opposite situations -- "you narrowed it to nothing" versus "there is nothing yet" -- and
        # offering the wrong one is worse than offering neither.
        #
        # Read through the SAME parsers the queryset uses, never the raw querystring: `_count_filter`
        # discards junk so `?min_games=abc` narrows nothing, and `_effective_query` discards a
        # one-or-two character `q` for the same reason. Sort is excluded on purpose -- re-ordering an
        # empty grid is not what emptied it.
        context['has_filters'] = bool(
            self._effective_query()
            or _count_filter(self.request.GET.get('min_games')) is not None
            or _count_filter(self.request.GET.get('max_games')) is not None
        )

        context['spotlight'] = self._spotlight()

        # The mosaic, in two queries for the whole page regardless of length. This CANNOT be a
        # `Prefetch(to_attr=...)` the way the Game-keyed version was: the cover lives on a `Game`
        # and an item now points at a `Concept`, which has N of them, so picking one is a step
        # Django's prefetch cannot express. See `gamelists/services/covers.py`.
        attach_cover_games(lists, per_list=LIST_TILE_COVERS)

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


class MyListsView(LoginRequiredMixin, _LinkedProfileRequired,
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
        # From the constant, not from `forloop.first`. Every other default reads
        # `LIST_TYPE_COLLECTION`; the dialog read whichever type happened to be declared first, so
        # reordering the choices -- which "each new type arrives with its presentation" makes likely
        # -- would silently change what a JS-enabled hunter creates, while the no-JS path (no field
        # posted) still created a Collection.
        context['default_list_type'] = LIST_TYPE_COLLECTION
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


class CreateListView(LoginRequiredMixin, _LinkedProfileRequired, View):
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

    @method_decorator(ratelimit(group=CREATE_LIST_RATELIMIT_GROUP, key='user', rate='30/m',
                                method='POST', block=True))
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

        # DECLARED SINCE 2019 AND NEVER REACHABLE. The only call site was `api/game_list_views.py`,
        # which is unrouted -- so a grep for `game_list_create` found a hit and the event had never
        # once been recorded. Wired here, where the write actually happens.
        track_site_event('game_list_create', game_list.id, request)

        messages.success(request, f'"{game_list.name}" is ready. Add some games to it.')
        return redirect('my_lists')


class GameListDetailView(DetailView):
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

    @staticmethod
    def _grouped(items, sections):
        """`[(section_or_None, [items])]`, ungrouped first and then sections in their own order.

        UNGROUPED LEADS, and always renders when it has anything in it. A list that gains sections
        has every item unassigned, so this bucket is the normal state on the way in rather than an
        error -- putting it last would hide the games somebody is about to file.

        IT NO LONGER RENDERS WHEN EMPTY, for anybody. It used to, for an owner who could arrange,
        and the reason was real at the time: filing the last loose card made the bucket disappear and
        took the drop target with it, so nothing could be un-filed by pointer (no grid to drop onto)
        or by keyboard (no group before the first section) until the owner deleted a whole section to
        get their game back.

        The 2026-09 card menu ended that. "No section" is a row on every card's menu and is appended
        by the client rather than read from the page, specifically so it is offered when this bucket
        is NOT rendered -- so un-filing has a home that does not depend on a header existing.

        What is left is the owner's actual complaint: a list whose games are all filed showed a
        permanent "Not in a section" header over nothing. A header for nothing was always noise to a
        reader; it turns out it was noise to the owner too, and the drop target was the only thing
        buying it.

        THE COST, STATED: a card can no longer be dragged out of every section, because there is
        nothing to drag it onto. The menu is the route. If a drop target is ever wanted back, it
        belongs behind `[data-gl-arranging]` -- on screen only while a drag is actually live -- and
        not on every render of every sectioned list.

        Sections keep their own `position`; the chosen SORT orders within each one. That falls out of
        iterating `items`, which arrives already sorted -- so sorting a sectioned list A-Z sorts
        inside each section rather than flattening the grouping away. Sections are structure; a sort
        is a view of it.
        """
        buckets = {section.id: [] for section in sections}
        ungrouped = []
        for item in items:
            # A section id that is not on this list cannot occur -- `assign_item` refuses it -- but
            # `.get` rather than `[]` keeps a stale id from 500ing a public page.
            buckets.get(item.section_id, ungrouped).append(item)

        groups = []
        if ungrouped:
            groups.append((None, ungrouped))
        groups.extend((section, buckets[section.id]) for section in sections)
        return groups

    @staticmethod
    def _number(items, groups, restart):
        """Attach `display_rank` to every item.

        Two modes, one field, and the reason both are cheap is that `position` stayed GLOBAL and
        dense: neither of these stores anything or reorders anything.

        ONCE A LIST HAS SECTIONS, THE SECTIONS ARE PART OF THE SEQUENCE. That is the whole of this
        function, and it took two wrong answers to get to.

        The first cut made continue-through `position + 1` in every case. That is right on a flat
        list and unreadable on a sectioned one: grouping reorders the page without touching
        `position`, so a list whose items 0-3 alternate between two sections printed "1, 3" under one
        header and "2, 4" under the next. Every numeral was individually true and the column could
        not be read down.

        The obvious repair -- number down the page -- is worse, and the reason is the invariant
        `detail_card.html` states: a rank is a fact about the ENTRY, not about the view, which is why
        the plate shows on every sort. Numbering the rendered order would make the A-Z view renumber
        the list 1..N alphabetically, claiming the alphabet was the author's ranking.

        So the rank is computed from the CANONICAL order and then displayed under whatever sort the
        page is using: sections in their own order, and `position` within each one. At the rank sort
        that is exactly the rendered order, so the column reads 1, 2, 3 straight down. Under A-Z the
        same entry keeps the same number, out of sequence on the page -- which is the point.
        """
        if groups is None:
            for item in items:
                item.display_rank = item.position + 1
            return
        # `restart` resets the counter at each header; otherwise it runs on through them.
        running = 0
        for _section, bucket in groups:
            if restart:
                running = 0
            # `sorted` by position and NOT the bucket's own order: the bucket arrives in the page's
            # sort, and using it would make the rank a function of the sort. Cheap -- the buckets
            # together hold at most `MAX_ITEMS_RENDERED` rows that are already in memory.
            for item in sorted(bucket, key=lambda entry: entry.position):
                running += 1
                item.display_rank = running

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

    def _is_fragment(self):
        """Is this the items-panel request rather than a full page render?

        Extracted so `get_template_names` and the out-of-band chrome flag cannot drift: the OOB
        fragments are only meaningful when the response IS the partial, and gating them on the
        querystring alone put a second copy of the sort control and the position bar into the full
        page.
        """
        return bool(self.request.htmx
                    or self.request.headers.get('X-Requested-With') == 'XMLHttpRequest')

    def get_template_names(self):
        # The sort swap returns the items only. Same shape as the browse pages, and the same reason:
        # re-sorting a 200-game list should not re-render the header, the toolbar and the chrome.
        if self._is_fragment():
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
        # BOUNDED. `MAX_ITEMS_PER_LIST` caps the list itself as of 2026-09-14, so this is no longer
        # the only thing standing between a public URL and an unbounded render -- but the slice stays,
        # because a cap enforced in a service binds callers of that service and not a shell. `cover_games_for` then fetches every Game row for
        # every concept -- several per concept once stacks are counted -- which is the largest
        # allocation on the page and invisible to a query-COUNT test, because the shape stays O(1)
        # while the bytes do not. CLAUDE.md's whale rule names exactly this: an explicit `[:N]` slice
        # is the acceptable form.
        #
        # THE SLICE IS A BACKSTOP, NOT A PAGE. `MAX_ITEMS_RENDERED` equals `MAX_ITEMS_PER_LIST`, and
        # `add_concept` enforces that cap, so no list the service built can reach it -- this bounds a
        # row somebody put there another way (a shell write, a future importer) rather than a state
        # the product produces.
        #
        # THE TRUNCATION FLAG IS GONE, with the `+ 1` fetch that fed it. While a list could outgrow
        # one render, the page carried a "showing the first N" notice, `can_reorder` carried a clause
        # for it, and the section counts had to be omitted rather than shown wrong -- and two real
        # defects came out of those branches: `can_arrange` silently inheriting the truncation clause
        # (sections creatable and permanently unusable on long lists), and the counts lying. Capping
        # the list at the render bound does not improve that state, it deletes it. If the ceiling is
        # ever raised, raise BOTH constants together or this comes back.
        items = list(
            game_list.items
            .select_related('concept', 'concept__igdb_match')
            .defer('concept__igdb_match__raw_response')
            .order_by(*order)[:MAX_ITEMS_RENDERED]
        )
        # One batched query for every cover on the page, not one per row -- the same helper the
        # browse tiles use, for the same reason.
        covers = cover_games_for([item.concept_id for item in items])
        for item in items:
            item.cover = covers.get(item.concept_id)

        # ── sections ────────────────────────────────────────────────────────────────────────────
        #
        # ONE QUERY for the headers, and the grouping is done in Python over rows already fetched --
        # no second pass at the database and no per-section queryset. Both sides are bounded:
        # `MAX_ITEMS_RENDERED` items and `MAX_SECTIONS_PER_LIST` headers, so this is the bounded-slice
        # form CLAUDE.md's whale rule names as acceptable rather than the per-row iteration it bans.
        # Fetched unconditionally rather than behind a `has_sections` property: a property that
        # runs its own query per call is exactly what `first_game_image` was deleted for, and
        # this is one bounded query either way -- an empty result IS the answer.
        sections = list(game_list.sections.all())
        context['sections'] = sections
        # `arrangeable` is settled HERE rather than further down where the flags are assembled. It
        # used to be the grouping's business too -- an owner who could arrange kept the ungrouped
        # bucket even when empty, because it was the only way back out of a section -- and it is not
        # any more: the card menu carries "No section" whether or not the bucket is rendered. The two
        # flags below still derive from this same local, so there is one source for the answer.
        arrangeable = (
            viewer is not None
            and game_list.owner_id == viewer.id
            and viewer.is_linked
            and bool(items)
        )
        context['groups'] = (
            self._grouped(items, sections) if sections else None)

        # THE RANK EACH CARD SHOWS, computed here rather than in the template, because one of the two
        # modes cannot be expressed there: continue-through is `position + 1`, which a filter can do,
        # and restart-per-section is the item's INDEX WITHIN ITS GROUP, which needs the grouping.
        # Doing both here also means the template has one expression instead of a branch, and the
        # `aria-label` and the visible plate cannot drift apart.
        # `self._is_ranked()` and not `context['is_ranked']`: that key is set further down, so
        # reading it here is a KeyError rather than a wrong answer -- loudly, which is why this is
        # worth noting. The predicate is the source either way.
        if self._is_ranked():
            self._number(items, context['groups'], game_list.sections_restart_numbering)

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
        # `is_linked` for the same reason `can_act` below carries it: `ReorderItemsView` is behind
        # `_LinkedProfileRequired`, which answers a JSON caller with an HTML redirect. The page and
        # the endpoint have to agree about who can act.
        #
        # `items` because an EMPTY ranked list otherwise renders the bar and `data-gl-reorder` over
        # zero rows -- a mode offered for nothing, whose one possible action the endpoint 400s.
        #
        # TWO FLAGS, because a drag now means two different acts and they are not available together.
        # `can_arrange` is "cards can be dragged AT ALL", which on a sectioned list means FILING one
        # under a header. `can_reorder` is the stricter "a drop POSITION means something", which is
        # true only at the real sequence. Merging them is what the earlier `not sections` clause was
        # standing in for: it withheld the whole affordance rather than admit that a Collection can be
        # arranged and cannot be ordered.
        # `arrangeable` was settled above, beside the grouping that also needs it -- the empty
        # ungrouped bucket the grouping keeps and the drag these flags offer are the same permission,
        # so it has one definition rather than two that must stay identical forever.
        #
        # A `not items_truncated` clause stood here and is gone with the state it described. It was
        # right while a list could outgrow one render -- `svc.reorder` refuses a partial ordering, so
        # the page could not post a complete one -- and it is now unreachable, because the size cap IS
        # the render bound. A condition that cannot change an outcome reads as a safety net and is
        # only somewhere for a future reader to get lost, which is the same argument that removed the
        # `is_ranked` clause below. It also caused a defect while it lived: shared with `can_arrange`,
        # it made sections creatable and permanently unusable on the longest lists.
        context['can_reorder'] = arrangeable and sort == 'rank'
        # Filing needs somewhere to file TO, so a list with no sections offers it only when ordering
        # is already on the table. Without the `or`, a sectioned Collection would render headers
        # nothing could be moved into -- which is every sectioned Collection, since a list that has
        # just gained its first section has everything in the loose bucket.
        context['can_arrange'] = arrangeable and (bool(sections) or context['can_reorder'])
        # MAKING a header is the member perk; arranging what you already have is not. The split is
        # the service's, repeated here only to decide which controls render -- `create_section` and
        # `rename_section` refuse regardless, so this is the affordance and not the gate.
        # No `viewer is not None`: `is_owner` already carries it, and a clause that cannot change the
        # answer is the dead weight the `is_ranked` note above was written about.
        #
        # `?preview=lists-free` renders this page as a NON-MEMBER sees it, for the team only. Sections
        # are the one membership-gated thing on this page, so a single flag is the whole surface --
        # everything else a free owner can do (arrange, delete a section, reorder, add games) is
        # already ungated and must stay that way under the preview, or it would answer a different
        # question than the one it is asked.
        #
        # Through `core.previews` rather than reading the querystring here. An anti-drift test walks
        # every module looking for a hand-rolled read of that parameter and fails it, because a door
        # that opens only half of a thing is not a preview and copies of the gate drift. That test is
        # a plain grep, so it cannot tell code from prose -- do not quote the expression it looks for
        # in a comment, which is how this line first failed it.
        context['free_preview'] = previewing(self.request, 'lists-free')
        context['can_manage_sections'] = (
            context['is_owner'] and viewer.is_linked and viewer.user_is_premium
            and not context['free_preview'])
        # WHO IS TOLD ABOUT SECTIONS WITHOUT HAVING THEM. Derived from `can_manage_sections` rather
        # than re-testing membership, so the CTA and the controls can never both be absent (which is
        # what shipped first: a free owner whose list had never had a section saw no trace of the
        # feature anywhere, so the perk was invisible to exactly the hunter who might buy it).
        #
        # `is_linked` is the clause that keeps this honest. An UNLINKED owner also fails
        # `can_manage_sections`, and selling them a membership is answering a question they did not
        # ask -- what stands between them and sections is linking a PSN account, not paying.
        #
        # `bool(items)` because sections group games, and a CTA on an empty list is asking somebody to
        # buy a way to organise nothing.
        #
        # STATIC, and that is a rule rather than an accident: CLAUDE.md's premium-preview pattern
        # exists because a locked UI twice ran its real data path for people who could not use it.
        # This is a flag, a heading and a link -- no provider, no query, nothing per-user beyond the
        # three booleans already computed above.
        context['sections_locked'] = (
            context['is_owner'] and viewer.is_linked
            and not context['can_manage_sections'] and bool(items))
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
            # The report dialog's options, from the model, so the form cannot offer a reason the
            # service would refuse. `can_act` is already the right population: signed in, linked,
            # and not the owner -- which is exactly who may report, since `report_list` refuses a
            # self-report. A restricted account is deliberately still allowed: a restriction stops
            # somebody WRITING content other people read, and flagging is not that.
            context['report_reasons'] = GameListReport.REPORT_REASONS

        # `display_name`, LIKE EVERY OTHER READER-FACING SURFACE. The templates were all moved onto
        # the moderation readers and the VIEW CONTEXT was missed, which is the `profile_views.py:670`
        # bug class the model's own docstring names: a flag honoured in one place and forgotten in
        # the next. The crumb renders visibly, two inches above an `h1` that had been corrected --
        # so a moderator hid a list's words, the page looked clean, and the hidden name was still
        # printed above it to every reader.
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Game Lists', 'url': reverse_lazy('lists_browse')},
            {'text': game_list.display_name},
        ]

        # The in-place edit form needs the same ceilings the create dialog uses. Without them
        # `maxlength="{{ name_max_length }}"` renders empty, browsers ignore it, and the character
        # counter has no ceiling to count against -- so the field silently accepts more than the
        # service will store and the first a hunter hears of it is a refusal on save.
        context['name_max_length'] = NAME_MAX_LENGTH
        context['description_max_length'] = DESCRIPTION_MAX_LENGTH
        # The section field's ceiling, for the same reason: without it the `maxlength` renders empty
        # and the field accepts a name `_check_section_name` will refuse.
        context['section_name_max_length'] = SECTION_NAME_MAX_LENGTH
        context['list_type_options'] = list_type_options()

        # THE INDEXABLE, SHAREABLE PAGE, and until 2026-09 its social card fell back to the
        # site-wide generic -- so every list anybody posted anywhere previewed as "PlatPursuit".
        #
        # The author's own description first, because they wrote it to say what the list is for.
        # Failing that, a sentence built from what the page actually contains: a bare "A game list"
        # is worse than nothing, since it tells a reader deciding whether to click precisely
        # nothing. Bounded at 300 because `description` is capped there and og:description is
        # truncated by every consumer well before it.
        #
        # THE DISPLAY READERS HERE TOO, and this is the surface where the leak travelled furthest:
        # og/twitter tags are what Discord, Slack and Google scrape, so a hidden name and
        # description were being republished by every unfurl of the URL long after the moderator
        # believed they were gone. The fallback branches on `display_description` as well, or a
        # hidden description would fall through to the generated sentence while still being used.
        owner_name = game_list.owner.display_psn_username or game_list.owner.psn_username
        if game_list.display_description:
            context['seo_description'] = game_list.display_description
        else:
            context['seo_description'] = (
                f'{game_list.display_name} — a game list of {game_list.game_count} '
                f'{"game" if game_list.game_count == 1 else "games"} by {owner_name} on Platinum '
                f'Pursuit.'
            )
        # `seo_title` feeds the og/twitter tags, which otherwise take the site-wide default even
        # though `{% block title %}` is set -- the two are separate, which is why the browse page
        # setting only a description was half a job too.
        context['seo_title'] = f'{game_list.display_name} by {owner_name}'
        # THE OUT-OF-BAND CHROME, and only on a fragment request. Gated on the querystring alone,
        # `GET ...?chrome=1` in a browser rendered the FULL page -- which includes the position slot
        # and the sort control -- and then had the items partial render both AGAIN inside
        # `#gl-items-panel`. Two elements per id, so `getElementById` picks the first and the second
        # bar is dead markup, plus a stray unlabelled <select> below the grid and inert `hx-swap-oob`
        # attributes in the initial document. Reachable by typing or sharing the URL.
        # `== '1'` rather than truthiness, so `?chrome=0` means what it says.
        context['oob_chrome'] = self._is_fragment() and self.request.GET.get('chrome') == '1'
        return context


# ── the write half ───────────────────────────────────────────────────────────────────────────────
#
# Plain Django views returning JSON rather than DRF, because these are page behaviour rather than a
# public API: they share the pages' permission stack, they answer one template's
# fetches, and routing them through DRF would mean a second permission stack that has to agree with
# the first. `PlatPursuit.API` is the client, so a non-2xx body reaches the caller as `.response`.
#
# EVERY rule lives in `game_list_service`. These translate a refusal into a status code and a message
# and do nothing else -- which is the entire point of having the service.


class _ListActionView(LoginRequiredMixin, _LinkedProfileRequired, View):
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

    def resolve_section(self, request, game_list, field='section'):
        """The posted section, `None` for the loose bucket, raising `SectionNotOnList` for a bad id.

        SHARED BY THE TWO WRITES THAT TAKE A DESTINATION -- filing an existing game (`AssignItemView`)
        and adding one straight into a section (`AddConceptView`). It was one view's inline block
        until the second needed it; copying twelve lines carrying three separate security arguments
        is how the two quietly come to disagree about which of them is the careful one.

        EMPTY IS A DESTINATION, not a missing field: "" means the loose bucket, which is the only way
        to un-file a game. So this cannot use a falsy test to mean "not supplied".

        Scoped to the list, like `_SectionActionView.get_section` and for the same reason: an id
        alone must not answer differently for "another hunter's section" and "no such section". The
        service refuses a foreign section too; this is what stops the refusal having to say which
        kind of wrong it was.

        `safe_int` and not the raw string: `filter(pk='abc')` raises ValueError, so a junk value
        would be a 500 on a route any logged-in hunter can post to.
        """
        raw = (request.POST.get(field) or '').strip()
        if not raw:
            return None
        section = GameListSection.objects.filter(
            pk=safe_int(raw), game_list=game_list).first()
        if section is None:
            raise SectionNotOnList('That section is not on this list.')
        return section


class SectionNotOnList(Exception):
    """A posted section id that does not name a section of the list being written to.

    An exception rather than a sentinel return, because `resolve_section` has THREE outcomes and two
    of them are ordinary: a section, the loose bucket (`None`), and "that is not a section of this
    list". Returning `None` for the middle one and `False` for the last is exactly the shape that
    gets read as a boolean by the next caller.
    """


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
        # A checkbox, so `safe_bool` for the same reason `is_public` uses it: 'on' is what a plain
        # HTML checkbox sends, and `== 'true'` reads that as False.
        if 'restart_numbering' in request.POST:
            fields['restart_numbering'] = safe_bool(request.POST['restart_numbering'])
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
            'restart_numbering': updated.sections_restart_numbering,
        })


class DeleteListView(_ListActionView):
    """Soft-delete a list you own.

    THE GATE WAS MASKING THIS. `delete_list` has existed in the service since the rebuild, fully
    tested, with no view, no URL and no button -- and nobody noticed, because under
    `_DevelopmentGate` the only accounts that could create a list were staff, who do not plausibly
    hit a three-list ceiling. Every free hunter now does, at list four, permanently, and the refusal
    they get names a remedy that did not exist: "Delete one to make room, or become a member."

    It also made `CreateListView`'s rate-limit rationale describe an unreachable attack --
    create-delete-create is not a loop if you cannot delete.

    Soft, and the service is idempotent: a second press answers 200 rather than "that list no longer
    exists", because somebody double-clicking has not made a mistake worth an error.
    """

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
    def post(self, request, list_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()

        try:
            svc.delete_list(game_list, self._viewer(request))
        except svc.ListError as exc:
            return self.fail(exc)
        # The list is gone, so the page it was on is gone: the client navigates rather than
        # re-rendering a detail page for a row that no longer reads.
        return JsonResponse({'deleted': True, 'redirect': reverse_lazy('my_lists')})


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

        # A CROSS-SECTION DROP ARRIVES HERE TOO, as one request. `moved_item` names the card that
        # changed section and `section` names where it landed.
        #
        # THE ASYMMETRY IS THE POINT, and this comment claimed the opposite for a slice. `section` is
        # read for PRESENCE, because empty is a real destination -- the loose bucket -- and treating
        # it as missing would make un-filing a card impossible. `moved_item` is read for TRUTH, with
        # `or None`, because an empty one names no card and there is nothing to move; the two are
        # then distinguished by `moved_item` rather than by `section` being falsy, which is what the
        # branch below depends on.
        moved_item_id = request.POST.get('moved_item') or None
        section_id = None
        if moved_item_id is not None:
            raw = (request.POST.get('section') or '').strip()
            section_id = safe_int(raw) if raw else None

        try:
            svc.reorder(game_list, self._viewer(request), item_ids,
                        moved_item_id=moved_item_id, section_id=section_id)
        except svc.ListError as exc:
            return self.fail(exc)
        return JsonResponse({'ordered': len(item_ids)})


class _SectionActionView(_ListActionView):
    """Resolves a section on a list the viewer can act on.

    Two lookups rather than one, and in this order: the LIST through `readable_by` (which answers the
    uniform 404), then the section WITHIN it. Looking the section up by id alone would answer
    differently for "exists on somebody else's list" and "does not exist", which is the oracle the
    404 rule exists to close -- and it would do it on the id space most easily walked, since sections
    are few per list.
    """

    def get_section(self, game_list, section_id):
        return GameListSection.objects.filter(pk=section_id, game_list=game_list).first()


class CreateSectionView(_ListActionView):
    """Members only, enforced in the service. The view translates the refusal and nothing more."""

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
    def post(self, request, list_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()

        try:
            section = svc.create_section(game_list, self._viewer(request),
                                         name=request.POST.get('name', ''))
        except svc.ListError as exc:
            return self.fail(exc)
        # The whole panel re-renders from the server after this, so the id is what the client needs:
        # the new section is a drop target, and the grid it belongs to does not exist yet.
        return JsonResponse({'id': section.id, 'name': section.name})


class RenameSectionView(_SectionActionView):
    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, list_id, section_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()
        section = self.get_section(game_list, section_id)
        if section is None:
            return self.not_found()

        try:
            svc.rename_section(section, self._viewer(request), name=request.POST.get('name', ''))
        except svc.ListError as exc:
            return self.fail(exc)
        # The STORED name, not the submitted one: `_check_section_name` trims and sanitizes, so a
        # client re-rendering its own input would show a name the database does not have.
        return JsonResponse({'name': section.name})


class DeleteSectionView(_SectionActionView):
    """Ungated by membership: removing your own content is not the act the perk covers."""

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
    def post(self, request, list_id, section_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()
        section = self.get_section(game_list, section_id)
        if section is None:
            return self.not_found()

        try:
            svc.delete_section(section, self._viewer(request))
        except svc.ListError as exc:
            return self.fail(exc)
        # Its games are ORPHANED rather than deleted, so the grid still holds them -- the client
        # re-renders and they appear in the loose bucket.
        return JsonResponse({'deleted': True})


class ReorderSectionsView(_ListActionView):
    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, list_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()

        section_ids = request.POST.getlist('section_ids[]') or request.POST.getlist('section_ids')
        if not section_ids:
            return self.fail('That order is not valid. Reload and try again.')

        try:
            svc.reorder_sections(game_list, self._viewer(request), section_ids)
        except svc.ListError as exc:
            return self.fail(exc)
        return JsonResponse({'ordered': len(section_ids)})


class ToggleLikeView(_ListActionView):
    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, list_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()
        # `safe_bool`, not `== 'true'`, and `UpdateListView` spells out why at length: the bare
        # comparison reads 'True', '1', 'on' and 'yes' as FALSE. These two toggles were the
        # siblings that never got the fix -- anything but the exact lowercase literal silently
        # became an UN-like, answered 200, and the response echoed the REQUESTED boolean rather
        # than the stored one, so no client could detect the mismatch.
        liked = safe_bool(request.POST.get('liked'))
        try:
            count = svc.set_like(game_list, self._viewer(request), liked=liked)
        except svc.ListError as exc:
            return self.fail(exc)
        return JsonResponse({'liked': liked, 'like_count': count})


class ReportListView(_ListActionView):
    """A hunter objects to a list's name or description.

    `_ListActionView` WITHOUT its ownership check, which is the point: every other action on that
    base is something an owner does to their own list, and this is the one a stranger does. The base
    supplies what is still wanted -- signed in, linked, POST only, `readable_by` resolution so a
    private list 404s rather than 403ing -- and ownership was never in the base to begin with. It
    lives in each service call, and `report_list` refuses a SELF-report instead.

    A LOWER RATE LIMIT than the owner actions. Reporting is not a thing anybody does sixty times a
    minute, and a report queue is a moderator's time: flooding it is the abuse this bounds.
    """

    @method_decorator(ratelimit(key='user', rate='10/m', method='POST', block=True))
    def post(self, request, list_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()
        try:
            svc.report_list(game_list, self._viewer(request),
                            reason=request.POST.get('reason', ''),
                            details=request.POST.get('details', ''))
        except svc.ListError as exc:
            return self.fail(exc)
        # No counts and no state: the reporter is told it landed, and deliberately nothing about
        # what happens next. How many reports a list carries is a moderator's information, and
        # showing it would tell somebody organising a pile-on whether it is working.
        return JsonResponse({'reported': True})


class ToggleFollowView(_ListActionView):
    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, list_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()
        following = safe_bool(request.POST.get('following'))   # see ToggleLikeView above
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
        # THE DESTINATION, optional. A per-section adder posts one; the toolbar's adder does not, and
        # an absent field reads as the loose bucket, which is where an add has always landed.
        try:
            section = self.resolve_section(request, game_list)
        except SectionNotOnList as exc:
            return self.fail(exc)

        try:
            item = svc.add_concept(game_list, self._viewer(request), concept,
                                   note=request.POST.get('note', ''), section=section)
        except svc.ListError as exc:
            return self.fail(exc)
        game_list.refresh_from_db()
        return JsonResponse({
            'item_id': item.pk,
            'game_count': game_list.game_count,
            'title': concept.unified_title,
            # THE ROUTE, not the id alone. The quick-add popover flips a row from "add" to "remove"
            # the moment this returns, and it was deriving that path by rewriting the add URL --
            # hand-assembling a route, which this project has been bitten by often enough that the
            # list card's own comment warns about it. The server owns URL shapes; it can say one.
            'remove_url': reverse_lazy('list_remove_game', args=[game_list.id, item.pk]),
            # WHERE IT LANDED, echoed back the way `AssignItemView` echoes it. The caller knows what
            # it asked for, but not what the service settled on -- the section is re-resolved under
            # the list lock, so a header deleted mid-request refuses rather than silently filing the
            # game loose. A client that repaints optimistically needs to be told which happened.
            'section': item.section_id,
        })


class RemoveItemView(_ListActionView):
    @method_decorator(ratelimit(key='user', rate='120/m', method='POST', block=True))
    def post(self, request, list_id, item_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()
        # OWNERSHIP BEFORE THE ITEM LOOKUP, the same ordering `AssignItemView` carries and for the
        # same reason. `readable_by` lets anybody reach this for a PUBLIC list, so resolving the item
        # first answered 400 "not your list" for an id that sits on it and 404 for one that does not
        # -- which reports, for any id, whether it belongs to the list being probed. `data-item-id`
        # renders only for owners, so those ids are otherwise undisclosed.
        viewer = self._viewer(request)
        if viewer is None or game_list.owner_id != viewer.id:
            return self.fail('That is not your list.')
        item = GameListItem.objects.filter(pk=item_id, game_list=game_list).first()
        if item is None:
            return self.fail('That entry is no longer on this list.', status=404)
        try:
            svc.remove_concept(game_list, viewer, item)
        except svc.ListError as exc:
            return self.fail(exc)
        game_list.refresh_from_db()
        return JsonResponse({'game_count': game_list.game_count})


class AssignItemView(_ListActionView):
    """File one game under a section, or out of every section.

    SEPARATE FROM `list_reorder`, and the split is the point rather than an omission. A drop means
    two different things depending on where the page is standing:

    - At the real sequence (a Ranked list sorted by `rank`) the drop POSITION is content, so the
      whole order travels and `reorder` carries the assignment with it, in one write.
    - Anywhere else -- a Collection, or a Ranked list sorted A-Z -- the position under the cursor is
      an artefact of the sort, and posting it would rewrite the author's sequence to match a view of
      it. So the drag reports only what it actually meant: this game now belongs under that header.

    The client picks by which the server offered (`can_reorder` vs `can_arrange`), and the drag is
    configured `sort: false` in the second case so the gesture cannot promise an order it will not
    keep.
    """

    @method_decorator(ratelimit(key='user', rate='120/m', method='POST', block=True))
    def post(self, request, list_id, item_id):
        game_list = self.get_list(request, list_id)
        if game_list is None:
            return self.not_found()
        # OWNERSHIP BEFORE THE ITEM LOOKUP, which is not merely tidier. `readable_by` lets a caller
        # reach this endpoint for anybody's PUBLIC list, and answering "no such entry" (404) for an
        # item id that belongs elsewhere while answering "not your list" (400) for one that belongs
        # HERE turns the pair into an oracle over the global `GameListItem` id space -- ids a reader
        # never sees, because `data-item-id` renders only under `can_arrange`. Asking the question in
        # this order makes every item id give a non-owner the same answer.
        viewer = self._viewer(request)
        if viewer is None or game_list.owner_id != viewer.id:
            return self.fail('That is not your list.')
        item = GameListItem.objects.filter(pk=item_id, game_list=game_list).first()
        if item is None:
            return self.fail('That entry is no longer on this list.', status=404)

        # EMPTY IS A DESTINATION -- the loose bucket. `resolve_section` owns that reading, and the
        # three security arguments behind it, for this view and for `AddConceptView`.
        try:
            section = self.resolve_section(request, game_list)
        except SectionNotOnList as exc:
            return self.fail(exc)

        try:
            svc.assign_item(game_list, viewer, item, section)
        except svc.ListError as exc:
            return self.fail(exc)
        return JsonResponse({'section': section.id if section else None})


class MyListsForConceptView(LoginRequiredMixin, _LinkedProfileRequired, View):
    """Everything the quick-add popover needs about ONE game, in one request.

    NOT PER-LIST, which is why it does not extend `_ListActionView`: the question is "where can this
    game go", and the answer spans every list the hunter owns. `owned_by` is the indexed read for
    that (it matches the `(owner, -updated_at)` partial index), and `Meta.ordering` supplies the
    most-recently-touched-first order a picker wants.

    WHAT EACH ROW CARRIES, and why each field is here rather than derived on the client:

    - `has_concept` decides the row's icon and what a tap does.
    - `remove_url` is what makes REMOVE reuse `list_remove_game` instead of needing a
      remove-by-concept endpoint. The popover knows a concept; that route wants an item; this is the
      one place that already has both in hand. It is the ROUTE rather than the bare id because the
      server owns URL shapes -- an earlier cut sent `item_id` and had the client assemble the path,
      which is the thing this feature was corrected for once already.
    - `is_full` is computed here because the cap lives in the service and the client must not carry
      a second copy of a product rule. A full list renders disabled rather than absent: "no room"
      and "not a list" are different answers and a picker that hides the first is lying.

    BOUNDED BY CONSTRUCTION, AND ONLY WHILE THE MEMBERSHIP FILTER STAYS INDEXED. A hunter holds at
    most `MEMBER_MAX_LISTS` (25) lists of `MAX_ITEMS_PER_LIST` (200), so the upper bound on "read
    every row of every list" is 5,000 rows joined twice, per popover open, at 120/m. What keeps it to
    a seek is `page_key_filter` -- the same bounded-membership shape `ListGameSearchView` uses, for
    the same reason. Putting the page-key `CASE` in the WHERE clause instead reads all 5,000.
    """

    @method_decorator(ratelimit(key='user', rate='120/m', method='GET', block=True))
    def get(self, request, concept_id):
        profile = request.user.profile
        game_lists = list(GameList.objects.owned_by(profile))

        # KEYED ON THE GAME PAGE, not the concept pk. A list holding a SIBLING of this concept holds
        # this game -- `add_concept` refuses it on exactly that ground -- so asking by pk offered Add
        # on a list that would then refuse, and offered no Remove for the row actually on it. The
        # same defect as `ListGameSearchView`'s, reached from the other direction.
        #
        # One bounded lookup for the asked-about concept's identity, then ONE INDEXED query for
        # membership across every list -- never a count per list, which is the N+1 a picker invites,
        # and never a `CASE` in the WHERE clause, which on 25 lists of 200 would read 5,000 rows to
        # answer a question about one game. A concept that does not exist yields no key, and
        # `page_key_filter` then matches nothing without issuing a query at all.
        page_key = (
            Concept.objects.filter(pk=concept_id)
            .annotate(_page_key=page_key_expression())
            .values_list('_page_key', flat=True)
            .first()
        )
        # Any surviving row IS this game, so the key itself is not needed back here.
        held = {
            row['game_list_id']: row['id']
            for row in GameListItem.objects
            .filter(game_list__in=game_lists)
            .filter(page_key_filter([page_key], 'concept__'))
            .order_by()
            .values('game_list_id', 'id')
        }

        return JsonResponse({
            'lists': [
                {
                    'id': game_list.id,
                    'name': game_list.name,
                    'game_count': game_list.game_count,
                    'has_concept': game_list.id in held,
                    # `game_count` rather than a live count: it is what the cap is felt as, it is
                    # already denormalized, and a list that drifted high simply shows full one game
                    # early -- which `add_concept` would then contradict by accepting. That is the
                    # right way round: the service is the authority and this is a hint.
                    # `svc.MAX_ITEMS_PER_LIST`, read through the SERVICE rather than from this
                    # module's own import. The service is the enforcement point and this is only a
                    # hint about it, so they must not be able to read different bindings -- a view
                    # holding its own copy is how a picker starts saying "full" about a list that
                    # `add_concept` then happily accepts into.
                    'is_full': game_list.game_count >= svc.MAX_ITEMS_PER_LIST,
                    'add_url': reverse_lazy('list_add_game', args=[game_list.id]),
                    'remove_url': (
                        reverse_lazy('list_remove_game', args=[game_list.id, held[game_list.id]])
                        if game_list.id in held else None
                    ),
                }
                for game_list in game_lists
            ],
            # The OTHER cap. A picker that offers "New list" to somebody holding three is the
            # remedy-that-refuses defect `add_concept`'s own message was fixed for.
            'can_create': len(game_lists) < svc.max_lists_for(profile),
            'max_lists': svc.max_lists_for(profile),
            # THE CEILING AND THE DESTINATION, so the client hardcodes neither. `NAME_MAX_LENGTH`
            # carries a comment about having been written three times before somebody showed the
            # number to a hunter; the popover's field was a fourth copy. `/support/` was a literal
            # too, on the one line that exists to point somebody at membership.
            'name_max_length': NAME_MAX_LENGTH,
            'support_url': reverse_lazy('support_hub'),
        })


class CreateListWithConceptView(LoginRequiredMixin, _LinkedProfileRequired, View):
    """"New list" from the popover: make it, and put this game in it.

    ONE ENDPOINT BECAUSE IT IS ONE ACT. Called as two requests, a failure between them leaves an
    empty list named after a game it does not contain -- and the hunter, having spent one of three
    slots, is worse off than before they pressed anything. Both service calls run in one transaction
    so a refusal on either leaves nothing behind.

    Separate from `CreateListView` rather than a flag on it: that one is a plain form post that
    redirects onto the new list, deliberately, because creating a list from My Lists is a navigation.
    This one answers JSON and the hunter stays where they are, looking at the game they just filed.
    Two different acts that happen to share a service call.
    """

    @method_decorator(ratelimit(group=CREATE_LIST_RATELIMIT_GROUP, key='user', rate='30/m',
                                method='POST', block=True))
    def post(self, request, concept_id):
        concept = Concept.objects.filter(pk=concept_id).first()
        if concept is None:
            return JsonResponse({'error': 'That game is not available.'}, status=404)

        try:
            with transaction.atomic():
                game_list = svc.create_list(
                    request.user.profile,
                    name=request.POST.get('name', ''),
                    # Private, like every list. Publishing stays the separate deliberate act, and a
                    # list created in passing from a browse grid is the LAST one to make public by
                    # default.
                    is_public=False,
                )
                svc.add_concept(game_list, request.user.profile, concept)
        except svc.ListError as exc:
            return JsonResponse({'error': str(exc)}, status=400)

        # THE SAME EVENT `CreateListView` RECORDS, because this is the same act by another door --
        # and on current shape the busier one, offered on four grids and two detail pages against one
        # modal. That view's docstring explains the event sat declared and unreachable from 2019; it
        # would have gone straight back to under-reporting, one commit later, if this were missed.
        track_site_event('game_list_create', game_list.id, request)

        # Just the name: the popover toasts it and stays where it is. An `id` and a `url` were
        # returned here and read by nothing, which is a response promising a navigation that the
        # design deliberately does not make.
        return JsonResponse({'name': game_list.name})


class ListGameSearchView(LoginRequiredMixin, _LinkedProfileRequired, View):
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
    the normalized query, and an upper bound on `q`. A fourth, which is this view's own and which the
    cache deliberately does not cover: the `already_added` membership check is bounded to the twelve
    ids being rendered rather than reading the whole list (see below). `MIN_QUERY` is 3 rather than 2 because pg_trgm
    extracts no trigrams from a two-character pattern, so a 2-char query is a guaranteed full pass
    even once the index question is settled.

    Deliberately NOT `Game.title_name`, which the old list search used: that is the trophy-list name,
    it is unreliable for matching, and it would return one row per stack for a game somebody wants to
    add once.
    """

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

        # The catalogue half, cached on the normalized query and shared with every other adder on the
        # site -- see `gamelists.services.game_search`. `already_added` below is the per-list half and
        # is deliberately applied AFTER, so one hunter's list never leaks into another's results.
        try:
            cached = search_concepts(request.GET.get('q'))
        except SearchRefused as exc:
            return JsonResponse({'error': str(exc)}, status=400)

        # BOUNDED TO THE PAGE OF RESULTS, which is the same idiom `api/rating_views.py` uses for its
        # prefill rows and for the same reason. This read every `concept_id` on the list into a Python
        # set on EVERY KEYSTROKE -- `list(qs.values_list(...))` followed by Python membership, the
        # third anti-pattern in CLAUDE.md's whale rule. The cache above covers the catalogue half of
        # the answer and deliberately not this half, so it ran unprotected at 120 requests a minute
        # per hunter, against a list that was uncapped when this was written. `MAX_ITEMS_PER_LIST`
        # has since bounded the damage at 200 rows -- which is a reason to keep the bound rather than
        # to drop it: reading two hundred to answer a question about twenty is still the wrong shape,
        # and the cap is a product decision that could move.
        #
        # THE BOUND HAS NOW BEEN LOST AND RESTORED ONCE, which is why it is spelled out twice. The
        # answer only ever needs membership for the `LIMIT` ids being rendered, and both the seek and
        # the identity rule below have to hold for that to stay true.
        #
        # ASKED BY PAGE IDENTITY, not by concept pk, because that is what the search elected by.
        #
        # This keyed on `concept_id` while `search_concepts` collapsed siblings onto one row, so when
        # the elected representative was the sibling NOT on the list, the row rendered as addable and
        # `add_concept` then refused it -- a dead end, on roughly half of the split games, created by
        # collapsing the read side without the membership side.
        #
        # FILTERED WITH `page_key_filter`, ANNOTATED WITH `page_key_expression`, and the order is the
        # whole point -- see `page_key_filter`. Filtering on the expression put a `CASE` over a join
        # in the WHERE clause, which no index serves, so this read all 200 rows of the list on every
        # keystroke: the bound above, re-lost. The `Q` below seeks `igdb_id` and `concept_id` on
        # their own indexes, and the key is computed only for the handful of rows that survive.
        keys = [row['page_key'] for row in cached]
        held = set(
            GameListItem.objects
            .filter(game_list=game_list)
            .filter(page_key_filter(keys, 'concept__'))
            .annotate(_page_key=page_key_expression('concept__'))
            # `Meta.ordering` would otherwise sort rows on their way into a set.
            .order_by()
            .values_list('_page_key', flat=True)
        )
        # Marked rather than filtered out: a hunter searching for something already on the list
        # should be told it is there, not left wondering why it does not appear.
        #
        # `page_key` is stripped: it is how the server recognises a game, not something the client
        # has any use for.
        results = []
        for row in cached:
            out = {key: value for key, value in row.items() if key != 'page_key'}
            out['already_added'] = row['page_key'] in held
            results.append(out)
        return JsonResponse({'results': results})
