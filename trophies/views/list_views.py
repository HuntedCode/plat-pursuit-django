"""
Game List views.

Handles page-level views for game lists: browse, detail, edit, create, and my lists hub.
"""
import logging

from core.services.tracking import track_page_view
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q, Prefetch
from django.db.models.functions import Lower
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse_lazy, reverse
from django.views.generic import ListView, DetailView, TemplateView

from trophies.mixins import ProfileHotbarMixin
from ..models import (
    GameList, GameListItem, GameListLike,
    GAME_LIST_FREE_MAX_LISTS, GAME_LIST_FREE_MAX_ITEMS,
)

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
        queryset = GameList.objects.filter(
            is_public=True,
            is_deleted=False,
            profile__user_is_premium=True,
        ).select_related('profile')

        # Search query
        query = self.request.GET.get('q')
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query) |
                Q(description__icontains=query) |
                Q(profile__psn_username__icontains=query)
            )

        # Sort options
        sort = self.request.GET.get('sort', 'popular')
        if sort == 'recent':
            queryset = queryset.order_by('-created_at')
        else:
            queryset = queryset.order_by('-like_count', '-created_at')

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_lists'] = GameList.objects.filter(
            is_public=True, is_deleted=False, profile__user_is_premium=True,
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

        # Private lists (or public lists whose owner lost premium) are only viewable by their owner
        is_effectively_public = game_list.is_public and game_list.profile.user_is_premium
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

        # Apply list background theme (only if owner is still premium)
        if game_list.selected_theme and game_list.profile.user_is_premium:
            from trophies.themes import get_theme_style
            context['user_theme_style'] = get_theme_style(game_list.selected_theme)

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Lists', 'url': reverse_lazy('lists_browse')},
            {'text': game_list.name},
        ]

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
        context['has_lapsed_premium_settings'] = (
            not profile.user_is_premium
            and (game_list.is_public or game_list.selected_theme)
        )

        # Background theme grid
        from trophies.themes import get_available_themes_for_grid
        context['available_themes'] = get_available_themes_for_grid(include_game_art=False)

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
            profile=profile, is_deleted=False
        )

        # Sort options
        sort = self.request.GET.get('sort', 'updated')
        if sort == 'newest':
            lists = lists.order_by('-created_at')
        elif sort == 'alpha':
            lists = lists.order_by(Lower('name'))
        elif sort == 'most_games':
            lists = lists.order_by('-game_count', '-updated_at')
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
