"""Game Lists surfaces.

THREE PAGES, NOT FIVE (settled with the owner before the rebuild): Browse, List detail where the
owner edits in place, and My Lists with create as a modal. The system this replaces had separate
create and edit ADDRESSES, which meant three round trips to rename a list you were looking at.

GATED WHILE THE BRANCH IS OPEN. Lists and the Challenges beta ship as ONE update, so these routes
exist for tests and for a browser pass but are closed to everybody else until the commit that turns
both on. `_DevelopmentGate` is one mixin and its removal is the switch -- see `test_lists_hidden`,
which pins that nothing links here yet.
"""
from django.db.models import Q
from django.db.models.functions import Lower
from django.urls import reverse_lazy
from django.views.generic import ListView

from gamelists.models import GameList, GameListLike
from gamelists.services.covers import attach_cover_games
from trophies.mixins import HtmxListMixin, StaffRequiredMixin

#: How many covers the `.pp-gtile` mosaic composes around (`is-1` .. `is-4`).
LIST_TILE_COVERS = 4


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

        min_games = self.request.GET.get('min_games', '')
        max_games = self.request.GET.get('max_games', '')
        if min_games.isdigit():
            queryset = queryset.filter(game_count__gte=int(min_games))
        if max_games.isdigit():
            queryset = queryset.filter(game_count__lte=int(max_games))

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

        # `paginator.count` is the number the page has already paid for. A second unfiltered
        # `COUNT(*)` here would also make the header disagree with the grid the moment anybody
        # searched -- which it used to.
        context['total_lists'] = context['paginator'].count
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
