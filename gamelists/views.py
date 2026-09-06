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
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import ListView, View

from gamelists.models import (DESCRIPTION_MAX_LENGTH, NAME_MAX_LENGTH, GameList,
                             GameListFollow, GameListLike)
from gamelists.services import game_list_service as svc
from gamelists.services.covers import attach_cover_games
from trophies.mixins import HtmxListMixin, StaffRequiredMixin

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


class MyListsView(_DevelopmentGate, LoginRequiredMixin, _LinkedProfileRequired, ListView):
    """Your own lists, and the ones you follow.

    ONE VIEW WITH A `scope`, not two pages -- the Career Board|History pattern: a single argument
    threaded through a single pipeline, no schema, and a URL that reloads into the state you left.

    This is also where following finally MEANS something. There is no notification surface and there
    will not be one this release, so a follow is a bookmark; "Following" is where the bookmark
    lives. Building the follow button without this tab would have shipped a promise with nowhere to
    land.
    """

    template_name = 'gamelists/my_lists.html'
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
        # On the `mine` scope the paginator has already counted exactly this queryset, so asking
        # again is a second COUNT for the same answer.
        context['list_count'] = (
            context['paginator'].count if scope == 'mine'
            else GameList.objects.owned_by(profile).count()
        )
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
