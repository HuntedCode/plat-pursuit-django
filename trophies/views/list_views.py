"""
Game List views.

Handles page-level views for game lists: browse, detail, edit, create, and my lists hub.
"""
import logging

from core.services.tracking import track_page_view
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import (
    Q, F, Prefetch, Subquery, OuterRef, Value, IntegerField, FloatField,
    Avg, OrderBy,
)
from django.db.models.functions import Lower, Coalesce, Cast
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse_lazy, reverse
from django.views.generic import ListView, DetailView, TemplateView

from trophies.mixins import ProfileHotbarMixin
from ..models import (
    GameList, GameListItem, GameListLike, ProfileGame,
    GAME_LIST_FREE_MAX_LISTS, GAME_LIST_FREE_MAX_ITEMS,
)
from .browse_helpers import annotate_community_ratings

logger = logging.getLogger("psn_api")


class BrowseListsView(ProfileHotbarMixin, ListView):
    """
    Public hub for browsing user-created game lists.

    Allows anyone to discover and explore community-created game lists.
    Features search and sorting options (popular, recent).
    """
    model = GameList
    template_name = 'trophies/browse_lists.html'
    context_object_name = 'game_lists'
    paginate_by = 24

    def get_queryset(self):
        """Return public game lists with optimized queries and optional filtering."""
        # Public lists from ANY user — no premium gate. The publish toggle
        # in the API (`api/game_list_views.py:247`) is open to all users, so
        # gating discoverability here was a (since-resolved) inconsistency:
        # free users could mark lists public but the lists never appeared
        # anywhere on the site. The same gate was lifted from
        # `_get_recent_lists_spotlight()` and the total_lists count below
        # in the same change.
        queryset = GameList.objects.filter(
            is_public=True,
            is_deleted=False,
        ).select_related('profile')

        # Search query
        query = self.request.GET.get('q')
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query) |
                Q(description__icontains=query) |
                Q(profile__psn_username__icontains=query)
            )

        # Min/max game count filter
        min_games = self.request.GET.get('min_games', '')
        max_games = self.request.GET.get('max_games', '')
        if min_games and min_games.isdigit():
            queryset = queryset.filter(game_count__gte=int(min_games))
        if max_games and max_games.isdigit():
            queryset = queryset.filter(game_count__lte=int(max_games))

        # Sort options
        sort = self.request.GET.get('sort', 'popular')
        if sort == 'recent':
            queryset = queryset.order_by('-created_at')
        elif sort == 'most_games':
            queryset = queryset.order_by('-game_count', '-like_count')
        elif sort == 'updated':
            queryset = queryset.order_by('-updated_at')
        elif sort == 'alpha':
            queryset = queryset.order_by(Lower('name'))
        else:
            queryset = queryset.order_by('-like_count', '-created_at')

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_lists'] = GameList.objects.filter(
            is_public=True, is_deleted=False,
        ).count()
        context['sort'] = self.request.GET.get('sort', 'popular')
        context['query'] = self.request.GET.get('q', '')

        # Annotate user_has_liked for authenticated users
        profile = None
        if self.request.user.is_authenticated:
            profile = getattr(self.request.user, 'profile', None)
        if profile:
            liked_ids = set(
                GameListLike.objects.filter(
                    profile=profile,
                    game_list__in=context['game_lists'],
                ).values_list('game_list_id', flat=True)
            )
            for gl in context['game_lists']:
                gl.user_has_liked = gl.id in liked_ids

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Browse Lists'},
        ]

        context['seo_description'] = (
            "Browse curated PlayStation game lists from the Platinum Pursuit community."
        )

        track_page_view('game_lists_browse', 'content', self.request)
        return context


class GameListDetailView(ProfileHotbarMixin, DetailView):
    """
    Display a game list with its games.

    Public lists viewable by anyone. Private lists only visible to owner.
    Shows game cards with trophy info, platform badges, and optional notes.
    """
    model = GameList
    template_name = 'trophies/game_list_detail.html'
    context_object_name = 'game_list'
    pk_url_kwarg = 'list_id'

    def get_queryset(self):
        return GameList.objects.filter(is_deleted=False).select_related('profile')

    def get_object(self, queryset=None):
        game_list = super().get_object(queryset)

        user = self.request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') else None

        # Private lists are only viewable by their owner
        is_effectively_public = game_list.is_public
        if not is_effectively_public:
            if not profile or game_list.profile_id != profile.id:
                raise Http404("List not found")

        return game_list

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        game_list = self.object
        user = self.request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') else None

        # Get items with game data
        items = game_list.items.select_related('game').order_by('position')

        # Sort option for viewing
        sort = self.request.GET.get('sort', 'custom')
        if sort == 'alpha':
            items = items.order_by('game__title_name')
        elif sort == 'added':
            items = items.order_by('-added_at')
        elif sort == 'platform':
            items = items.order_by('game__title_platform', 'game__title_name')
        elif sort == 'rating':
            items = annotate_community_ratings(items, 'game__concept_id')
            items = items.order_by(F('_avg_rating').desc(nulls_last=True), 'game__title_name')
        elif sort == 'rating_inv':
            items = annotate_community_ratings(items, 'game__concept_id')
            items = items.order_by(F('_avg_rating').asc(nulls_last=True), 'game__title_name')
        elif sort == 'played':
            items = items.order_by('-game__played_count', 'game__title_name')
        elif sort == 'played_inv':
            items = items.order_by('game__played_count', 'game__title_name')
        elif sort == 'time_to_beat':
            items = items.annotate(
                _time_to_beat=F('game__concept__igdb_match__time_to_beat_completely'),
            )
            items = items.order_by(OrderBy(F('_time_to_beat'), nulls_last=True))
        elif sort == 'time_to_beat_inv':
            items = items.annotate(
                _time_to_beat=F('game__concept__igdb_match__time_to_beat_completely'),
            )
            items = items.order_by(OrderBy(F('_time_to_beat'), descending=True, nulls_last=True))
        elif sort == 'completion' and profile:
            items = items.annotate(
                _completion=Coalesce(
                    Subquery(
                        ProfileGame.objects.filter(
                            profile=profile, game_id=OuterRef('game_id'),
                        ).values('progress')[:1],
                        output_field=IntegerField(),
                    ),
                    Value(0),
                    output_field=IntegerField(),
                ),
            )
            items = items.order_by('-_completion', 'game__title_name')
        elif sort == 'trophy_count':
            items = items.annotate(
                _trophy_count=(
                    Coalesce(Cast(F('game__defined_trophies__bronze'), IntegerField()), Value(0))
                    + Coalesce(Cast(F('game__defined_trophies__silver'), IntegerField()), Value(0))
                    + Coalesce(Cast(F('game__defined_trophies__gold'), IntegerField()), Value(0))
                    + Coalesce(Cast(F('game__defined_trophies__platinum'), IntegerField()), Value(0))
                ),
            )
            items = items.order_by('-_trophy_count', 'game__title_name')
        elif sort == 'trophy_count_inv':
            items = items.annotate(
                _trophy_count=(
                    Coalesce(Cast(F('game__defined_trophies__bronze'), IntegerField()), Value(0))
                    + Coalesce(Cast(F('game__defined_trophies__silver'), IntegerField()), Value(0))
                    + Coalesce(Cast(F('game__defined_trophies__gold'), IntegerField()), Value(0))
                    + Coalesce(Cast(F('game__defined_trophies__platinum'), IntegerField()), Value(0))
                ),
            )
            items = items.order_by('_trophy_count', 'game__title_name')
        # 'custom' keeps the default position ordering

        context['items'] = items
        context['sort'] = sort
        context['is_owner'] = profile and game_list.profile_id == profile.id
        context['is_premium'] = getattr(profile, 'user_is_premium', False) if profile else False

        # Like status
        context['user_has_liked'] = False
        if profile and game_list.is_public:
            context['user_has_liked'] = GameListLike.objects.filter(
                game_list=game_list, profile=profile
            ).exists()

        context['owner_is_premium'] = game_list.profile.user_is_premium

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Lists', 'url': reverse_lazy('lists_browse')},
            {'text': game_list.name},
        ]

        context['seo_description'] = (
            f"{game_list.name}: A curated game list by {game_list.profile.display_psn_username} "
            f"with {game_list.game_count} games."
        )

        track_page_view('game_list', game_list.id, self.request)
        return context


class GameListEditView(LoginRequiredMixin, ProfileHotbarMixin, DetailView):
    """
    Edit a game list: add/remove games, reorder, edit name/description, toggle visibility.

    Only accessible by the list owner.
    """
    model = GameList
    template_name = 'trophies/game_list_edit.html'
    context_object_name = 'game_list'
    pk_url_kwarg = 'list_id'
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to manage game lists.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return GameList.objects.filter(is_deleted=False).select_related('profile')

    def get_object(self, queryset=None):
        game_list = super().get_object(queryset)
        profile = getattr(self.request.user, 'profile', None)
        if not profile or game_list.profile_id != profile.id:
            raise Http404("List not found")
        return game_list

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        game_list = self.object
        profile = self.request.user.profile

        items = game_list.items.select_related('game').order_by('position')
        context['items'] = items
        context['is_premium'] = profile.user_is_premium
        context['max_items'] = None if profile.user_is_premium else GAME_LIST_FREE_MAX_ITEMS
        context['at_item_limit'] = not profile.user_is_premium and game_list.game_count >= GAME_LIST_FREE_MAX_ITEMS
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Lists', 'url': reverse_lazy('my_lists')},
            {'text': game_list.name, 'url': reverse('list_detail', kwargs={'list_id': game_list.id})},
            {'text': 'Edit'},
        ]

        track_page_view('game_list_edit', game_list.id, self.request)
        return context


class GameListCreateView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    Create a new game list.

    Shows a form to name the list. After creation, redirects to the edit page.
    Checks free tier limits before allowing creation.
    """
    template_name = 'trophies/game_list_create.html'
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to create game lists.")
                return redirect('link_psn')

            # Check free tier limit
            if not profile.user_is_premium:
                count = GameList.objects.filter(profile=profile, is_deleted=False).count()
                if count >= GAME_LIST_FREE_MAX_LISTS:
                    messages.warning(
                        request,
                        f"Free accounts are limited to {GAME_LIST_FREE_MAX_LISTS} lists. Upgrade to Premium for unlimited lists!"
                    )
                    return redirect('my_lists')

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile
        list_count = GameList.objects.filter(profile=profile, is_deleted=False).count()

        context['is_premium'] = profile.user_is_premium
        context['list_count'] = list_count
        context['max_lists'] = None if profile.user_is_premium else GAME_LIST_FREE_MAX_LISTS
        context['suggested_lists'] = ['The Backlog', 'Platinum Path', 'Trophy Vault', 'Hidden Gems']
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Lists', 'url': reverse_lazy('my_lists')},
            {'text': 'Create List'},
        ]
        return context


class MyListsView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    Display the user's game lists hub.

    Shows all of the user's lists with options to create, edit, and delete.
    Includes premium upsell when at free tier limit.
    """
    template_name = 'trophies/my_lists.html'
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to use game lists.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile

        lists = GameList.objects.filter(
            profile=profile, is_deleted=False,
        )

        # Visibility filter
        visibility = self.request.GET.get('visibility', '')
        if visibility == 'public':
            lists = lists.filter(is_public=True)
        elif visibility == 'private':
            lists = lists.filter(is_public=False)

        # Sort options
        sort = self.request.GET.get('sort', 'updated')
        if sort == 'newest':
            lists = lists.order_by('-created_at')
        elif sort == 'alpha':
            lists = lists.order_by(Lower('name'))
        elif sort == 'most_games':
            lists = lists.order_by('-game_count', '-updated_at')
        elif sort == 'liked':
            lists = lists.order_by('-like_count', '-updated_at')
        elif sort == 'least_games':
            lists = lists.order_by('game_count', '-updated_at')
        else:
            sort = 'updated'
            lists = lists.order_by('-updated_at')

        context['game_lists'] = lists
        context['sort'] = sort
        context['is_premium'] = profile.user_is_premium
        context['max_lists'] = None if profile.user_is_premium else GAME_LIST_FREE_MAX_LISTS
        context['can_create'] = profile.user_is_premium or lists.count() < GAME_LIST_FREE_MAX_LISTS
        context['list_count'] = lists.count()

        # Suggested starter list names for empty state
        context['suggested_lists'] = ['The Backlog', 'Platinum Path', 'Trophy Vault', 'Hidden Gems']

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Lists'},
        ]

        track_page_view('my_lists', 'user', self.request)
        return context
