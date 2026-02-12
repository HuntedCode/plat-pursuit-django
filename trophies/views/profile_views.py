import logging

from core.services.tracking import track_page_view
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q, F, Prefetch, Max
from django.db.models.functions import Lower
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import ListView, DetailView, View
from django_ratelimit.decorators import ratelimit
from urllib.parse import urlencode

from ..forms import (
    ProfileSearchForm,
    ProfileGamesForm,
    ProfileTrophiesForm,
    ProfileBadgesForm,
    LinkPSNForm,
)
from ..models import (
    Profile,
    EarnedTrophy,
    ProfileGame,
    UserTrophySelection,
    Badge,
    UserBadge,
    UserBadgeProgress,
    GameList,
)
from trophies.mixins import ProfileHotbarMixin
from trophies.psn_manager import PSNManager

logger = logging.getLogger("psn_api")


class ProfilesListView(ProfileHotbarMixin, ListView):
    """
    Display paginated list of user profiles with filtering and sorting.

    Provides profile browsing functionality with filters for:
    - Username search
    - Country
    - Sort options (trophies, platinums, games, completions, average progress)

    Useful for discovering other trophy hunters and viewing leaderboards.
    """
    model = Profile
    template_name = 'trophies/profile_list.html'
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset()
        form = ProfileSearchForm(self.request.GET)
        order = [Lower('psn_username')]

        #qs = qs.exclude(psn_history_public=False)

        if form.is_valid():
            query = form.cleaned_data.get('query')
            country = form.cleaned_data.get('country')
            sort_val = form.cleaned_data.get('sort')

            if query:
                qs = qs.filter(Q(psn_username__icontains=query))
            if country:
                qs = qs.filter(country_code=country)

            recent_plat_qs = EarnedTrophy.objects.filter(earned=True, trophy__trophy_type='platinum').order_by(F('earned_date_time').desc(nulls_last=True))[:1]
            qs = qs.prefetch_related(Prefetch('earned_trophy_entries', queryset=recent_plat_qs, to_attr='recent_platinum'))

            if sort_val == 'trophies':
                order = ['-total_trophies', Lower('psn_username')]
            elif sort_val == 'plats':
                order = ['-total_plats', Lower('psn_username')]
            elif sort_val == 'games':
                order = ['-total_games', Lower('psn_username')]
            elif sort_val == 'completes':
                order = ['-total_completes', Lower('psn_username')]
            elif sort_val == 'avg_progress':
                order = ['-avg_progress', Lower('psn_username')]

        return qs.order_by(*order)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Breadcrumb
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Profiles'},
        ]

        context['form'] = ProfileSearchForm(self.request.GET)
        context['is_paginated'] = self.object_list.count() > self.paginate_by
        context['filter_shovelware'] = self.request.GET.get('filter_shovelware', '')

        track_page_view('profiles_list', 'list', self.request)
        return context


class ProfileDetailView(ProfileHotbarMixin, DetailView):
    """
    Display profile detail page with tabbed interface for games, trophies, and badges.

    Shows header stats, trophy case selections, and tab-specific content with
    filtering, sorting, and pagination.
    """
    model = Profile
    template_name = 'trophies/profile_detail.html'
    slug_field = 'psn_username'
    slug_url_kwarg = 'psn_username'
    context_object_name = 'profile'

    def get_object(self, queryset=None):
        psn_username = self.kwargs[self.slug_url_kwarg].lower()
        queryset = queryset or self.get_queryset()
        return get_object_or_404(queryset, **{self.slug_field: psn_username})

    def _build_header_stats(self, profile):
        """
        Build header statistics for profile.

        Args:
            profile: Profile instance

        Returns:
            dict: Header stats with trophy counts, completions, and notable trophies
        """
        header_stats = {
            'total_games': profile.total_games,
            'total_earned_trophies': profile.total_trophies,
            'total_unearned_trophies': profile.total_unearned,
            'total_completions': profile.total_completes,
            'average_completion': profile.avg_progress,
        }

        # Recent platinum
        if profile.recent_plat:
            header_stats['recent_platinum'] = {
                'trophy': profile.recent_plat.trophy,
                'game': profile.recent_plat.trophy.game,
                'earned_date': profile.recent_plat.earned_date_time,
            }
        else:
            header_stats['recent_platinum'] = None

        # Rarest platinum
        if profile.rarest_plat:
            header_stats['rarest_platinum'] = {
                'trophy': profile.rarest_plat.trophy,
                'game': profile.rarest_plat.trophy.game,
                'earned_date': profile.rarest_plat.earned_date_time,
            }
        else:
            header_stats['rarest_platinum'] = None

        return header_stats

    def _build_trophy_case(self, profile):
        """
        Build trophy case selections list.

        Args:
            profile: Profile instance

        Returns:
            list: Trophy case selections padded to max_trophies
        """
        max_trophies = 10
        trophy_case = list(UserTrophySelection.objects.filter(profile=profile).order_by('-earned_trophy__earned_date_time'))
        # Pad with None to reach max_trophies
        trophy_case = trophy_case + [None] * (max_trophies - len(trophy_case))
        return trophy_case

    def _build_games_tab_context(self, profile, per_page, page_number):
        """
        Build context for games tab with filtering and pagination.

        Args:
            profile: Profile instance
            per_page: Items per page
            page_number: Current page number

        Returns:
            dict: Context with profile_games and form
        """
        form = ProfileGamesForm(self.request.GET)
        context = {'trophy_log': []}

        if not form.is_valid():
            context['profile_games'] = []
            context['form'] = form
            return context

        # Get form data
        query = form.cleaned_data.get('query')
        platforms = form.cleaned_data.get('platform')
        plat_status = form.cleaned_data.get('plat_status')
        sort_val = form.cleaned_data.get('sort')

        # Build queryset
        games_qs = profile.played_games.all().select_related('game').annotate(
            annotated_total_trophies=F('earned_trophies_count') + F('unearned_trophies_count')
        )

        # Apply profile settings
        if profile.hide_hiddens:
            games_qs = games_qs.exclude(user_hidden=True)
        if profile.hide_zeros:
            games_qs = games_qs.exclude(earned_trophies_count=0)

        # Apply filters
        if query:
            games_qs = games_qs.filter(Q(game__title_name__icontains=query))
        if platforms:
            platform_filter = Q()
            for plat in platforms:
                platform_filter |= Q(game__title_platform__contains=plat)
            games_qs = games_qs.filter(platform_filter)
            context['selected_platforms'] = platforms

        # Apply plat status filters
        if plat_status:
            if plat_status in ['plats', 'plats_100s', 'plats_no_100s']:
                games_qs = games_qs.platinum_earned()
            elif plat_status in ['no_plats', 'no_plats_100s']:
                games_qs = games_qs.filter(has_plat=False)
            if plat_status in ['100s', 'plats_100s', 'no_plats_100s']:
                games_qs = games_qs.completed()
            elif plat_status in ['no_100s']:
                games_qs = games_qs.exclude(progress=100)
            if plat_status == 'plats_no_100s':
                games_qs = games_qs.exclude(progress=100)

        # Apply sorting
        order = ['-last_updated_datetime']
        if sort_val == 'oldest':
            order = ['last_updated_datetime']
        elif sort_val == 'alpha':
            order = [Lower('game__title_name')]
        elif sort_val == 'completion':
            order = ['-progress', Lower('game__title_name')]
        elif sort_val == 'completion_inv':
            order = ['progress', Lower('game__title_name')]
        elif sort_val == 'trophies':
            order = ['-annotated_total_trophies', Lower('game__title_name')]
        elif sort_val == 'earned':
            order = ['-earned_trophies_count', Lower('game__title_name')]
        elif sort_val == 'unearned':
            order = ['-unearned_trophies_count', Lower('game__title_name')]

        games_qs = games_qs.order_by(*order)

        # Paginate
        games_paginator = Paginator(games_qs, per_page)
        if int(page_number) > games_paginator.num_pages:
            game_page_obj = []
        else:
            game_page_obj = games_paginator.get_page(page_number)

        context['profile_games'] = game_page_obj
        context['form'] = form
        return context

    def _build_trophies_tab_context(self, profile, per_page, page_number):
        """
        Build context for trophies tab with filtering and pagination.

        Args:
            profile: Profile instance
            per_page: Items per page
            page_number: Current page number

        Returns:
            dict: Context with trophy_log and form
        """
        form = ProfileTrophiesForm(self.request.GET)
        context = {'profile_games': []}

        if not form.is_valid():
            context['trophy_log'] = []
            context['form'] = form
            return context

        # Get form data
        query = form.cleaned_data.get('query')
        platforms = form.cleaned_data.get('platform')
        type = form.cleaned_data.get('type')

        # Build queryset
        trophies_qs = profile.earned_trophy_entries.filter(earned=True).select_related(
            'trophy', 'trophy__game'
        ).order_by(F('earned_date_time').desc(nulls_last=True))

        # Apply filters
        if query:
            trophies_qs = trophies_qs.filter(
                Q(trophy__trophy_name__icontains=query) | Q(trophy__game__title_name__icontains=query)
            )
        if platforms:
            platform_filter = Q()
            for plat in platforms:
                platform_filter |= Q(trophy__game__title_platform__contains=plat)
            trophies_qs = trophies_qs.filter(platform_filter)
            context['selected_platforms'] = platforms
        if type:
            trophies_qs = trophies_qs.filter(trophy__trophy_type=type)

        # Paginate
        trophy_paginator = Paginator(trophies_qs, per_page)
        if int(page_number) > trophy_paginator.num_pages:
            trophy_page_obj = []
        else:
            trophy_page_obj = trophy_paginator.get_page(page_number)

        context['trophy_log'] = trophy_page_obj
        context['form'] = form
        return context

    def _build_badges_tab_context(self, profile):
        """
        Build context for badges tab with earned badges and progress.

        Args:
            profile: Profile instance

        Returns:
            dict: Context with grouped_earned_badges and form
        """
        form = ProfileBadgesForm(self.request.GET)
        context = {}

        if not form.is_valid():
            context['grouped_earned_badges'] = []
            context['form'] = form
            return context

        sort_val = form.cleaned_data.get('sort')

        # Get earned badges
        earned_badges_qs = UserBadge.objects.filter(profile=profile).select_related('badge').values(
            'badge__series_slug'
        ).annotate(max_tier=Max('badge__tier')).distinct()

        grouped_earned = []
        for entry in earned_badges_qs:
            series_slug = entry['badge__series_slug']
            max_tier = entry['max_tier']
            highest_badge = Badge.objects.by_series(series_slug).filter(tier=max_tier).first()
            if not highest_badge:
                continue

            next_tier = max_tier + 1
            next_badge = Badge.objects.by_series(series_slug).filter(tier=next_tier).first()
            is_maxed = next_badge is None
            if is_maxed:
                next_badge = highest_badge

            # Calculate progress
            progress_entry = UserBadgeProgress.objects.filter(profile=profile, badge=next_badge).first()
            if progress_entry and next_badge.required_stages > 0:
                progress_percentage = (progress_entry.completed_concepts / next_badge.required_stages) * 100
            else:
                progress_percentage = 0
            if is_maxed:
                progress_percentage = 100

            grouped_earned.append({
                'highest_badge': highest_badge,
                'next_badge': next_badge,
                'progress': progress_entry,
                'percentage': progress_percentage,
                'max_tier': max_tier,  # For sorting
            })

        # Sort
        if sort_val == 'name':
            grouped_earned.sort(key=lambda d: d['highest_badge'].effective_display_title)
        elif sort_val == 'tier':
            grouped_earned.sort(key=lambda d: (d['max_tier'], d['highest_badge'].effective_display_title))
        elif sort_val == 'tier_desc':
            grouped_earned.sort(key=lambda d: (-d['max_tier'], d['highest_badge'].effective_display_title))
        else:
            grouped_earned.sort(key=lambda d: d['highest_badge'].effective_display_series)

        context['grouped_earned_badges'] = grouped_earned
        context['form'] = form
        return context

    def _build_lists_tab_context(self, profile):
        """Build context for lists tab — public game lists for this profile."""
        lists = GameList.objects.filter(
            profile=profile, is_public=True, is_deleted=False
        ).order_by('-like_count', '-created_at')
        return {'profile_lists': lists}

    def get_context_data(self, **kwargs):
        """Build context for profile detail page with tab-specific content.

        This method delegates to tab-specific helper methods to keep the code
        organized and maintainable. Each tab (games, trophies, badges) has its
        own focused handler method.

        Args:
            **kwargs: Standard Django context keyword arguments

        Returns:
            dict: Context dictionary with profile data, tab content, and metadata
        """
        context = super().get_context_data(**kwargs)
        profile: Profile = self.object
        tab = self.request.GET.get('tab', 'games')
        per_page = 50
        page_number = self.request.GET.get('page', 1)

        # Prefetch earned trophies for efficiency
        earned_trophies_prefetch = Prefetch(
            'earned_trophy_entries',
            queryset=EarnedTrophy.objects.filter(earned=True).select_related('trophy', 'trophy__game'),
            to_attr='earned_trophies'
        )
        profile = Profile.objects.prefetch_related(earned_trophies_prefetch).get(id=profile.id)

        # Build shared context (header stats and trophy case)
        context['header_stats'] = self._build_header_stats(profile)
        context['trophy_case'] = self._build_trophy_case(profile)
        context['trophy_case_count'] = len(context['trophy_case'])

        # Public game lists count (shown in tab header regardless of active tab)
        public_lists = GameList.objects.filter(profile=profile, is_public=True, is_deleted=False)
        context['profile_lists_count'] = public_lists.count()

        # Delegate to tab-specific handler methods
        if tab == 'games':
            tab_context = self._build_games_tab_context(profile, per_page, page_number)
        elif tab == 'trophies':
            tab_context = self._build_trophies_tab_context(profile, per_page, page_number)
        elif tab == 'badges':
            tab_context = self._build_badges_tab_context(profile)
        elif tab == 'lists':
            tab_context = self._build_lists_tab_context(profile)
        else:
            # Default to games tab if invalid tab specified
            tab_context = self._build_games_tab_context(profile, per_page, page_number)

        context.update(tab_context)

        # Add shared metadata
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Profiles', 'url': reverse_lazy('profiles_list')},
            {'text': f"{profile.display_psn_username}"}
        ]
        context['current_tab'] = tab

        # Add premium background if applicable
        if profile.user_is_premium and profile.selected_background:
            context['image_urls'] = {'bg_url': profile.selected_background.bg_url}

        track_page_view('profile', profile.id, self.request)
        context['view_count'] = profile.view_count

        return context

    def get_template_names(self):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            tab = self.request.GET.get('tab', 'games')
            if tab == 'games':
                return ['trophies/partials/profile_detail/game_list_items.html']
            elif tab == 'trophies':
               return ['trophies/partials/profile_detail/trophy_list_items.html']
        return super().get_template_names()


class LinkPSNView(LoginRequiredMixin, View):
    """
    Multi-step view for linking PSN account to web account.

    Steps:
    1. User enters PSN username
    2. System generates verification code and syncs profile
    3. User adds code to PSN "About Me" section
    4. System verifies code presence via PSN API
    5. Profile is linked to authenticated user account

    Handles profile creation, sync, verification code generation, and final verification.
    """
    template_name = 'account/link_psn.html'
    login_url = reverse_lazy('login')
    form_class = LinkPSNForm

    def get(self, request):
        if hasattr(request.user, 'profile') and request.user.profile.is_linked:
            messages.info(request, 'This PSN account is already linked to a web account.')
            return redirect('link_psn')

        form = self.form_class()
        context = {'form': form, 'step': 1}
        return render(request, self.template_name, context)

    def post(self, request):
        action = request.POST.get('action')

        if action == 'submit_username':
            form = self.form_class(request.POST)
            if form.is_valid():
                psn_username = form.cleaned_data['psn_username'].lower().strip()
                try:
                    profile, created = Profile.objects.get_or_create(psn_username=psn_username)
                    if profile.user and profile.user != request.user:
                        raise ValueError('This PSN account is already linked to another user.')

                    time_since_last_sync = profile.get_time_since_last_sync()
                    if created:
                        PSNManager.initial_sync(profile)
                    else:
                        profile.attempt_sync()

                    if not profile.verification_code or profile.verification_expires_at < timezone.now():
                        profile.generate_verification_code()

                    context = {
                        'form': form,
                        'step': 2,
                        'verification_code': profile.verification_code,
                        'profile': profile,
                    }
                    return render(request, self.template_name, context)
                except ValueError as e:
                    messages.error(request, str(e))
                except Exception as e:
                    messages.error(request, 'An error occured during sync. Please try again later.')
            return render(request, self.template_name, {'form': form, 'step': 1})
        elif action == 'verify':
            form = self.form_class(request.POST)
            if form.is_valid():
                psn_username = form.cleaned_data['psn_username'].lower()
                try:
                    start_time = timezone.now().timestamp()
                    profile = Profile.objects.get(psn_username=psn_username.lower())
                    is_syncing = profile.attempt_sync()
                    if not is_syncing:
                        PSNManager.sync_profile_data(profile)

                    messages.info(request, "Verification in progress...")
                    context = {
                        'form': self.form_class(initial={'psn_username': psn_username}),
                        'step': 3,
                        'verification_code': profile.verification_code,
                        'profile': profile,
                        'start_time': str(start_time),
                    }
                    return render(request, self.template_name, context)
                except Profile.DoesNotExist:
                    messages.error(request, "Profile not found. Please start over.")
                    return redirect('link_psn')
                except Exception as e:
                    messages.error(request, f"An error occurred during verification. Please try again.")
                    return render(request, self.template_name, {'form': self.form_class(initial={'psn_username': psn_username}), 'step': 2})

        return redirect('link_psn')


class ProfileVerifyView(LoginRequiredMixin, View):
    """
    AJAX endpoint for polling PSN verification status during link flow.

    Checks if profile has been synced since verification started and
    if verification code appears in PSN "About Me" section.
    Links profile to user account upon successful verification.

    Rate limited to 60 requests per minute per user.
    """
    @method_decorator(ratelimit(key='user', rate='60/m', method='GET'))
    def get(self, request):
        user = request.user
        profile_id = request.GET.get('profile_id')
        start_time = request.GET.get('start_time')
        if not profile_id:
            return JsonResponse({'error': 'Profile id required'}, status=400)
        if not start_time:
            return JsonResponse({'error': 'start_time required'}, status=400)

        try:
            start_time_float = float(start_time)
        except ValueError:
            return JsonResponse({'error': 'Invalid start_time format'}, status=400)

        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            return JsonResponse({'error': 'Profile not found'}, status=404)

        if profile.sync_status == 'error':
            return JsonResponse({'error': 'Sync error. Make sure your "Gaming History" permission is set to "Anybody"'}, status=400)

        verified = False
        synced = False
        if profile.last_synced.timestamp() > start_time_float:
            synced = True
            verified = profile.verify_code(profile.about_me)
            if verified:
                profile.link_to_user(user)

        return JsonResponse({
            'synced': synced,
            'verified': verified,
        })
