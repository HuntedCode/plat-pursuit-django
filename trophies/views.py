import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, date
from django.core.cache import cache
from django.core.paginator import Paginator
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.csrf import ensure_csrf_cookie
from django.shortcuts import redirect, render, get_object_or_404
from django.http import Http404, JsonResponse, HttpResponseRedirect
from django.views.generic import ListView, View, DetailView, TemplateView
from django.views.generic.edit import FormView
from django.db.models import Q, F, Prefetch, OuterRef, Subquery, Value, IntegerField, FloatField, Avg, Max, Case, When
from django.db.models.functions import Coalesce
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django_ratelimit.decorators import ratelimit
from trophies.services.psn_api_service import PsnApiService
from random import choice
from urllib.parse import urlencode
from trophies.psn_manager import PSNManager
from trophies.mixins import ProfileHotbarMixin, PremiumRequiredMixin
from .models import Game, Trophy, Profile, EarnedTrophy, ProfileGame, TrophyGroup, UserTrophySelection, Badge, UserBadge, UserBadgeProgress, Concept, FeaturedGuide, Stage, Milestone, UserMilestone, UserMilestoneProgress, CommentReport, ModerationLog, Checklist
from trophies.services.checklist_service import ChecklistService
from .forms import GameSearchForm, TrophySearchForm, ProfileSearchForm, ProfileGamesForm, ProfileTrophiesForm, ProfileBadgesForm, UserConceptRatingForm, BadgeSearchForm, GuideSearchForm, LinkPSNForm, GameDetailForm, BadgeCreationForm
from trophies.util_modules.cache import redis_client
from trophies.util_modules.constants import MODERN_PLATFORMS, ALL_PLATFORMS

logger = logging.getLogger("psn_api")
    
class GamesListView(ProfileHotbarMixin, ListView):
    """
    Display paginated list of games with filtering and sorting options.

    Provides comprehensive game browsing functionality with filters for:
    - Platform (PS4, PS5, PS Vita, etc.)
    - Region (NA, EU, JP, global)
    - Alphabetical letter
    - Platinum trophy availability
    - Shovelware exclusion

    Defaults to modern platforms (PS4/PS5) and user's preferred region if authenticated.
    """
    model = Game
    template_name = 'trophies/game_list.html'
    paginate_by = 25

    def dispatch(self, request, *args, **kwargs):
        if not request.GET:
            default_params = {'platform': MODERN_PLATFORMS}
            if request.user.is_authenticated and request.user.default_region:
                default_params['regions'] = ['global', request.user.default_region]
            
            if default_params:
                query_string = urlencode(default_params, doseq=True)
                url = reverse('games_list') + '?' + query_string
                return HttpResponseRedirect(url)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = super().get_queryset()
        form = GameSearchForm(self.request.GET)
        order = ['title_name']

        platinums_earned = Subquery(Trophy.objects.filter(game=OuterRef('pk'), trophy_type='platinum').values('earned_count')[:1])
        platinums_rate = Subquery(Trophy.objects.filter(game=OuterRef('pk'), trophy_type='platinum').values('earn_rate')[:1])
        qs = qs.annotate(
            platinums_earned_count=Coalesce(platinums_earned, Value(0), output_field=IntegerField()),
            platinums_earn_rate=Coalesce(platinums_rate, Value(0.0), output_field=FloatField())
        )

        if form.is_valid():
            query = form.cleaned_data.get('query')
            platforms = form.cleaned_data.get('platform')
            regions = form.cleaned_data.get('regions')
            letter = form.cleaned_data.get('letter')
            show_only_platinum = form.cleaned_data.get('show_only_platinum')
            filter_shovelware = form.cleaned_data.get('filter_shovelware')
            sort_val = form.cleaned_data.get('sort')

            if query:
                from trophies.util_modules.roman_numerals import expand_numeral_query
                query_variants = expand_numeral_query(query)
                q_filter = Q()
                for variant in query_variants:
                    q_filter |= Q(title_name__icontains=variant)
                qs = qs.filter(q_filter)
            if platforms:
                qs = qs.for_platform(platforms)
            if regions:
                qs = qs.for_region(regions)
            if letter:
                if letter == '0-9':
                    qs = qs.filter(title_name__regex=r'^[0-9]')
                else:
                    qs = qs.filter(title_name__istartswith=letter)
            
            if show_only_platinum:
                qs = qs.filter(trophies__trophy_type='platinum').distinct()
            if filter_shovelware:
                qs = qs.filter(is_shovelware=False)

            if sort_val == 'played':
                order = ['-played_count', 'title_name']
            elif sort_val == 'played_inv':
                order = ['played_count', 'title_name']
            elif sort_val == 'plat_earned':
                order = ['-platinums_earned_count', 'title_name']
            elif sort_val == 'plat_earned_inv':
                order = ['platinums_earned_count', 'title_name']
            elif sort_val == 'plat_rate':
                order = ['-platinums_earn_rate', 'title_name']
            elif sort_val == 'plat_rate_inv':
                order = ['platinums_earn_rate', 'title_name']

        qs = qs.prefetch_related(
            Prefetch('trophies', queryset=Trophy.objects.filter(trophy_type='platinum'), to_attr='platinum_trophy')
        )
        return qs.order_by(*order)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Breadcrumb
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games'},
        ]

        context['form'] = GameSearchForm(self.request.GET)
        context['is_paginated'] = self.object_list.count() > self.paginate_by
        context['selected_platforms'] = self.request.GET.getlist('platform')
        context['selected_regions'] = self.request.GET.getlist('regions')
        context['view_type'] = self.request.GET.get('view', 'grid')
        context['show_only_platinum'] = self.request.GET.get('show_only_platinum', '')
        context['filter_shovelware'] = self.request.GET.get('filter_shovelware', '')
        return context
    
class TrophiesListView(ProfileHotbarMixin, ListView):
    """
    Display paginated list of trophies with filtering and sorting options.

    Provides trophy browsing functionality with filters for:
    - Trophy type (bronze, silver, gold, platinum)
    - Platform
    - Region
    - Alphabetical letter

    Useful for finding specific trophies or browsing rarest/most common achievements.
    """
    model = Trophy
    template_name = 'trophies/trophy_list.html'
    paginate_by = 25

    def dispatch(self, request, *args, **kwargs):
        if not request.GET:
            default_params = {'platform': MODERN_PLATFORMS}
            if request.user.is_authenticated and request.user.default_region:
                default_params['region'] = ['global', request.user.default_region]
            
            if default_params:
                query_string = urlencode(default_params, doseq=True)
                url = reverse('trophies_list') + '?' + query_string
                return HttpResponseRedirect(url)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = super().get_queryset()
        form = TrophySearchForm(self.request.GET)
        order = ['trophy_name']

        if form.is_valid():
            query = form.cleaned_data.get('query')
            platforms = form.cleaned_data.get('platform')
            types = form.cleaned_data.get('type')
            regions = form.cleaned_data.get('region')
            psn_rarity = form.cleaned_data.get('psn_rarity')
            show_only_platinum = form.cleaned_data.get('show_only_platinum')
            filter_shovelware = form.cleaned_data.get('filter_shovelware')
            sort_val = form.cleaned_data.get('sort')

            if query:
                qs = qs.filter(Q(trophy_name__icontains=query))
            if platforms:
                platform_filter = Q()
                for plat in platforms:
                    platform_filter |= Q(game__title_platform__contains=plat)
                qs = qs.filter(platform_filter)
            if types:
                types_filter = Q()
                for type in types:
                    types_filter |= Q(trophy_type=type)
                qs = qs.filter(types_filter)
            if regions:
                region_filter = Q()
                for r in regions:
                    if r == 'global':
                        region_filter |= Q(game__is_regional=False)
                    else:
                        region_filter |= Q(game__is_regional=True, game__region__contains=r)
                qs = qs.filter(region_filter)
            if psn_rarity:
                psn_rarity_filter = Q()
                for rarity in psn_rarity:
                    psn_rarity_filter |= Q(trophy_rarity=rarity)
                qs = qs.filter(psn_rarity_filter)

            if show_only_platinum:
                qs = qs.filter(game__trophies__trophy_type='platinum').distinct()
            if filter_shovelware:
                qs = qs.filter(game__is_shovelware=False)


            if sort_val == 'earned':
                order = ['-earned_count', 'trophy_name']
            elif sort_val == 'earned_inv':
                order = ['earned_count', 'trophy_name']
            elif sort_val == 'rate':
                order = ['-earn_rate', 'trophy_name']
            elif sort_val == 'rate_inv':
                order = ['earn_rate', 'trophy_name']
            elif sort_val == 'psn_rate':
                order = ['-trophy_earn_rate', 'trophy_name']
            elif sort_val == 'psn_rate_inv':
                order = ['trophy_earn_rate', 'trophy_name']

        return qs.order_by(*order)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Breadcrumb
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Trophies'},
        ]

        context['form'] = TrophySearchForm(self.request.GET)
        context['is_paginated'] = self.object_list.count() > self.paginate_by
        context['selected_platforms'] = self.request.GET.getlist('platform')
        context['selected_types'] = self.request.GET.getlist('type')
        context['selected_regions'] = self.request.GET.getlist('region')
        context['selected_psn_rarity'] = self.request.GET.getlist('psn_rarity')
        context['show_only_platinum'] = self.request.GET.get('show_only_platinum', '')
        context['filter_shovelware'] = self.request.GET.get('filter_shovelware', '')
        return context
    
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
        order = ['psn_username']

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
                order = ['-total_trophies', 'psn_username']
            elif sort_val == 'plats':
                order = ['-total_plats', 'psn_username']
            elif sort_val == 'games':
                order = ['-total_games', 'psn_username']
            elif sort_val == 'completes':
                order = ['-total_completes', 'psn_username']
            elif sort_val == 'avg_progress':
                order = ['-avg_progress', 'psn_username']

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
        return context
    
class SearchView(View):
    """
    AJAX endpoint for universal search across games, trophies, and profiles.

    Returns JSON results for autocomplete functionality in the site-wide search bar.
    Searches across game titles, trophy names, and PSN usernames based on type parameter.
    """
    def get(self, request, *args, **kwargs):
        search_type = request.GET.get('type')
        query = request.GET.get('query', '')

        if search_type == 'game':
            return HttpResponseRedirect(reverse_lazy('games_list') + f"?query={query}")
        elif search_type == 'trophy':
            return HttpResponseRedirect(reverse_lazy('trophies_list') + f"?query={query}")
        elif search_type == 'user':
            return HttpResponseRedirect(reverse_lazy('profiles_list') + f"?query={query}")
        else:
            return HttpResponseRedirect(reverse_lazy('home'))

@method_decorator(ensure_csrf_cookie, name='dispatch')
class GameDetailView(ProfileHotbarMixin, DetailView):
    """
    Display detailed game information including trophies, statistics, and user progress.

    Shows trophy list with optional filtering/sorting, game statistics (players, completions),
    milestone progress for linked profiles, and community ratings if applicable.
    """
    model = Game
    template_name = 'trophies/game_detail.html'
    slug_field = 'np_communication_id'
    slug_url_kwarg = 'np_communication_id'
    context_object_name = 'game'

    def _get_target_profile(self):
        """
        Get the target profile from URL parameter or authenticated user.

        Returns:
            Profile: Target profile instance or None if not found/authenticated
        """
        psn_username = self.kwargs.get('psn_username')
        user = self.request.user

        if psn_username:
            try:
                return Profile.objects.get(psn_username__iexact=psn_username)
            except Profile.DoesNotExist:
                messages.error(self.request, "Profile not found.")
                return None
        elif user.is_authenticated and hasattr(user, 'profile') and user.profile and user.profile.is_linked:
            return user.profile
        return None

    def _build_profile_context(self, game, profile):
        """
        Build profile-specific context including progress, earned trophies, and milestones.

        Args:
            game: Game instance
            profile: Profile instance

        Returns:
            dict: Context dictionary with profile progress, trophy totals, earned status, and milestones
        """
        context = {
            'profile_progress': None,
            'profile_earned': {},
            'profile_trophy_totals': {},
            'profile_group_totals': {},
            'milestones': [
                {'label': 'First Trophy'},
                {'label': '50% Trophy'},
                {'label': 'Platinum Trophy'},
                {'label': '100% Trophy'}
            ]
        }

        has_trophies = Trophy.objects.filter(game=game).exists()

        try:
            profile_game = ProfileGame.objects.get(profile=profile, game=game)
            context['profile_progress'] = {
                'progress': profile_game.progress,
                'play_count': profile_game.play_count,
                'play_duration': profile_game.play_duration,
                'last_played': profile_game.last_played_date_time
            }

            if has_trophies:
                # Get earned trophies data
                earned_qs = EarnedTrophy.objects.filter(profile=profile, trophy__game=game).order_by('trophy__trophy_id')
                context['profile_earned'] = {
                    e.trophy.trophy_id: {
                        'earned': e.earned,
                        'progress': e.progress,
                        'progress_rate': e.progress_rate,
                        'progressed_date_time': e.progressed_date_time,
                        'earned_date_time': e.earned_date_time
                    } for e in earned_qs
                }

                # Calculate trophy type totals
                ordered_earned_qs = earned_qs.filter(earned=True).order_by(F('earned_date_time').asc(nulls_last=True))
                context['profile_trophy_totals'] = {
                    'bronze': ordered_earned_qs.filter(trophy__trophy_type='bronze').count() or 0,
                    'silver': ordered_earned_qs.filter(trophy__trophy_type='silver').count() or 0,
                    'gold': ordered_earned_qs.filter(trophy__trophy_type='gold').count() or 0,
                    'platinum': ordered_earned_qs.filter(trophy__trophy_type='platinum').count() or 0,
                }

                # Calculate group totals
                profile_group_totals = {}
                for e in ordered_earned_qs:
                    group_id = e.trophy.trophy_group_id or 'default'
                    trophy_type = e.trophy.trophy_type
                    if group_id not in profile_group_totals:
                        profile_group_totals[group_id] = {'bronze': 0, 'silver': 0, 'gold': 0, 'platinum': 0}
                    profile_group_totals[group_id][trophy_type] += 1
                context['profile_group_totals'] = profile_group_totals

                # Build milestones
                context['milestones'] = self._build_milestones(ordered_earned_qs, len(earned_qs), context['profile_progress'])

        except ProfileGame.DoesNotExist:
            pass

        return context

    def _build_milestones(self, ordered_earned_qs, total_trophies, profile_progress):
        """
        Build milestone trophy data (first, 50%, platinum, 100%).

        Args:
            ordered_earned_qs: QuerySet of earned trophies ordered by date
            total_trophies: Total number of trophies in game
            profile_progress: Profile progress dict with 'progress' key

        Returns:
            list: List of milestone dicts with trophy info or empty label
        """
        milestones = []
        earned_list = list(ordered_earned_qs)

        # First trophy
        if len(earned_list) > 0:
            first = earned_list[0]
            milestones.append({
                'label': 'First Trophy',
                'trophy_name': first.trophy.trophy_name,
                'trophy_id': first.trophy.trophy_id,
                'trophy_icon_url': first.trophy.trophy_icon_url,
                'earned_date_time': first.earned_date_time,
                'trophy_earn_rate': first.trophy.trophy_earn_rate,
                'trophy_rarity': first.trophy.trophy_rarity
            })
        else:
            milestones.append({'label': 'First Trophy'})

        # 50% trophy
        mid_idx = math.ceil((total_trophies - 1) * 0.5)
        if len(earned_list) > mid_idx:
            mid = earned_list[mid_idx]
            milestones.append({
                'label': '50% Trophy',
                'trophy_name': mid.trophy.trophy_name,
                'trophy_id': mid.trophy.trophy_id,
                'trophy_icon_url': mid.trophy.trophy_icon_url,
                'earned_date_time': mid.earned_date_time,
                'trophy_earn_rate': mid.trophy.trophy_earn_rate,
                'trophy_rarity': mid.trophy.trophy_rarity
            })
        else:
            milestones.append({'label': '50% Trophy'})

        # Platinum trophy
        plat_entry = None
        if len(earned_list) > 0:
            plat_entry = next((e for e in reversed(earned_list) if e.trophy.trophy_type == 'platinum'), None)
        if plat_entry:
            milestones.append({
                'label': 'Platinum Trophy',
                'trophy_name': plat_entry.trophy.trophy_name,
                'trophy_id': plat_entry.trophy.trophy_id,
                'trophy_icon_url': plat_entry.trophy.trophy_icon_url,
                'earned_date_time': plat_entry.earned_date_time,
                'trophy_earn_rate': plat_entry.trophy.trophy_earn_rate,
                'trophy_rarity': plat_entry.trophy.trophy_rarity
            })
        else:
            milestones.append({'label': 'Platinum Trophy'})

        # 100% trophy
        if profile_progress and profile_progress['progress'] == 100:
            complete = earned_list[-1]
            milestones.append({
                'label': '100% Trophy',
                'trophy_name': complete.trophy.trophy_name,
                'trophy_id': complete.trophy.trophy_id,
                'trophy_icon_url': complete.trophy.trophy_icon_url,
                'earned_date_time': complete.earned_date_time,
                'trophy_earn_rate': complete.trophy.trophy_earn_rate,
                'trophy_rarity': complete.trophy.trophy_rarity
            })
        else:
            milestones.append({'label': '100% Trophy'})

        return milestones

    def _build_images_context(self, game):
        """
        Build cached image URLs for game background, screenshots, and content rating.

        Args:
            game: Game instance

        Returns:
            dict: Image URLs or empty dict on error
        """
        images_cache_key = f"game:imageurls:{game.np_communication_id}"
        images_timeout = 604800  # 1 week

        try:
            cached_images = cache.get(images_cache_key)
            if cached_images:
                return json.loads(cached_images)

            if not game.concept:
                return {}

            screenshot_urls = []
            content_rating_url = None

            if game.concept.media:
                # Prefer screenshots
                for img in game.concept.media:
                    if img.get('type') == 'SCREENSHOT':
                        screenshot_urls.append(img.get('url'))

                # Fallback to other image types if no screenshots
                if len(screenshot_urls) < 1:
                    for img in game.concept.media:
                        img_type = img.get('type')
                        if img_type in ['GAMEHUB_COVER_ART', 'LOGO', 'MASTER']:
                            screenshot_urls.append(img.get('url'))

            if game.concept.content_rating:
                content_rating_url = game.concept.content_rating.get('url')

            image_urls = {
                'bg_url': game.concept.bg_url,
                'screenshot_urls': screenshot_urls,
                'content_rating_url': content_rating_url
            }
            cache.set(images_cache_key, json.dumps(image_urls), timeout=images_timeout)
            return image_urls

        except Exception as e:
            logger.error(f"Game images cache failed for {game.np_communication_id}: {e}")
            return {}

    def _build_game_stats_context(self, game):
        """
        Build game statistics including player counts, completions, and average progress.

        Args:
            game: Game instance

        Returns:
            dict: Game statistics or empty dict on error
        """
        today = date.today().isoformat()
        now_utc = timezone.now()
        stats_cache_key = f"game:stats:{game.np_communication_id}:{today}:{now_utc.hour:02d}"
        stats_timeout = 3600  # 1 hour

        try:
            cached_stats = cache.get(stats_cache_key)
            if cached_stats:
                return json.loads(cached_stats)

            stats = {
                'total_players': game.played_count,
                'monthly_players': ProfileGame.objects.filter(
                    game=game,
                    last_played_date_time__gte=timezone.now() - timedelta(days=30)
                ).count(),
                'plats_earned': EarnedTrophy.objects.filter(
                    trophy__game=game,
                    trophy__trophy_type='platinum',
                    earned=True
                ).count(),
                'total_earns': EarnedTrophy.objects.filter(
                    trophy__game=game,
                    earned=True
                ).count(),
                'completes': ProfileGame.objects.filter(game=game).completed().count(),
                'avg_progress': ProfileGame.objects.filter(game=game).aggregate(avg=Avg('progress'))['avg'] or 0.0
            }
            cache.set(stats_cache_key, json.dumps(stats), timeout=stats_timeout)
            return stats

        except Exception as e:
            logger.error(f"Game stats cache failed for {game.np_communication_id}: {e}")
            return {}

    def _build_trophy_context(self, game, form, profile_earned):
        """
        Build trophy data with groups, filtering, and sorting.

        Args:
            game: Game instance
            form: GameDetailForm with filtering/sorting options
            profile_earned: Dict of earned trophy data by trophy_id

        Returns:
            tuple: (full_trophies list, trophy_groups dict, grouped_trophies dict, has_trophies bool)
        """
        has_trophies = Trophy.objects.filter(game=game).exists()
        if not has_trophies:
            return [], {}, {}, False

        try:
            # Get all trophies
            trophies_qs = Trophy.objects.filter(game=game).order_by('trophy_id')
            full_trophies = [
                {
                    'trophy_id': t.trophy_id,
                    'trophy_type': t.trophy_type,
                    'trophy_name': t.trophy_name,
                    'trophy_detail': t.trophy_detail,
                    'trophy_icon_url': t.trophy_icon_url,
                    'trophy_group_id': t.trophy_group_id,
                    'progress_target_value': t.progress_target_value,
                    'trophy_rarity': t.trophy_rarity,
                    'trophy_earn_rate': t.trophy_earn_rate,
                    'earned_count': t.earned_count,
                    'earn_rate': t.earn_rate,
                    'pp_rarity': t.get_pp_rarity_tier()
                } for t in trophies_qs
            ]
        except Exception as e:
            logger.error(f"Game trophies query failed for {game.np_communication_id}: {e}")
            full_trophies = []

        # Get trophy comment counts if game has a concept
        trophy_comment_counts = {}
        if game.concept:
            from django.db.models import Count
            comment_counts = game.concept.comments.filter(
                trophy_id__isnull=False
            ).values('trophy_id').annotate(
                count=Count('id')
            )
            trophy_comment_counts = {item['trophy_id']: item['count'] for item in comment_counts}

        # Add comment counts to trophy data
        for trophy in full_trophies:
            trophy['comment_count'] = trophy_comment_counts.get(trophy['trophy_id'], 0)

        # Apply filtering and sorting
        if form.is_valid():
            earned_key = form.cleaned_data['earned']
            if profile_earned:
                if earned_key == 'unearned':
                    full_trophies = [t for t in full_trophies if not profile_earned.get(t['trophy_id'], {}).get('earned', False)]
                elif earned_key == 'earned':
                    full_trophies = [t for t in full_trophies if profile_earned.get(t['trophy_id'], {}).get('earned', False)]

            sort_key = form.cleaned_data['sort']
            if sort_key == 'earned_date':
                full_trophies.sort(
                    key=lambda t: (
                        profile_earned.get(t['trophy_id'], {}).get('earned_date_time') is None,
                        profile_earned.get(t['trophy_id'], {}).get('earned_date_time') or timezone.make_aware(datetime.min)
                    )
                )
            elif sort_key == 'psn_rarity':
                full_trophies.sort(key=lambda t: t['trophy_earn_rate'], reverse=False)
            elif sort_key == 'pp_rarity':
                full_trophies.sort(key=lambda t: t['earn_rate'], reverse=False)
            elif sort_key == 'alpha':
                full_trophies.sort(key=lambda t: t['trophy_name'].lower())

        # Get trophy groups
        trophy_groups_cache_key = f"game:trophygroups:{game.np_communication_id}"
        trophy_groups_timeout = 604800  # 1 week
        try:
            cached_trophy_groups = cache.get(trophy_groups_cache_key)
            if cached_trophy_groups:
                trophy_groups = json.loads(cached_trophy_groups)
            else:
                trophy_groups_qs = TrophyGroup.objects.filter(game=game)
                trophy_groups = {
                    g.trophy_group_id: {
                        'trophy_group_name': g.trophy_group_name,
                        'trophy_group_icon_url': g.trophy_group_icon_url,
                        'defined_trophies': g.defined_trophies,
                    } for g in trophy_groups_qs
                }
                cache.set(trophy_groups_cache_key, json.dumps(trophy_groups), timeout=trophy_groups_timeout)
        except Exception as e:
            logger.error(f"Trophy groups cache failed for {game.np_communication_id}: {e}")
            trophy_groups = {}

        # Group trophies
        grouped_trophies = {}
        for trophy in full_trophies:
            group_id = trophy.get('trophy_group_id', 'default')
            if group_id not in grouped_trophies:
                grouped_trophies[group_id] = []
            grouped_trophies[group_id].append(trophy)

        # Sort groups (default first, then alphabetically)
        sorted_groups = sorted(grouped_trophies.keys(), key=lambda x: (x != 'default', x))
        sorted_grouped = {gid: grouped_trophies[gid] for gid in sorted_groups}

        return full_trophies, trophy_groups, sorted_grouped, has_trophies

    def _build_concept_context(self, game):
        """
        Build concept-related context including community ratings, badges, and other versions.

        Args:
            game: Game instance

        Returns:
            dict: Concept context data or empty dict if no concept
        """
        if not game.concept:
            return {}

        context = {}
        today = date.today().isoformat()
        stats_timeout = 3600

        # Community averages
        averages_cache_key = f"concept:averages:{game.concept.concept_id}:{today}"
        cached_averages = cache.get(averages_cache_key)
        if cached_averages:
            averages = json.loads(cached_averages)
        else:
            averages = game.concept.get_community_averages()
            if averages:
                cache.set(averages_cache_key, json.dumps(averages), timeout=stats_timeout)
        context['community_averages'] = averages

        # Related badges
        series_slugs = Stage.objects.filter(concepts__games=game).values_list('series_slug', flat=True).distinct()
        badges = Badge.objects.filter(series_slug__in=Subquery(series_slugs), tier=1).distinct().order_by('tier')
        context['badges'] = badges

        # Other platform versions
        other_versions_qs = game.concept.games.exclude(pk=game.pk)
        platform_order = {plat: idx for idx, plat in enumerate(ALL_PLATFORMS)}
        other_versions_qs = other_versions_qs.annotate(
            platform_order=Case(*[When(title_platform__contains=plat, then=Value(idx)) for plat, idx in platform_order.items()], default=999, output_field=IntegerField())
        ).order_by('platform_order', 'title_name')
        context['other_versions'] = list(other_versions_qs)

        return context

    def _build_rating_context(self, user, game):
        """
        Build user rating context if user has earned platinum.

        Args:
            user: Request user
            game: Game instance

        Returns:
            dict: Rating context or empty dict
        """
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') and user.profile and user.profile.is_linked else None
        if not profile or not game.concept:
            return {}

        has_platinum = game.concept.has_user_earned_platinum(profile)
        if not has_platinum:
            return {}

        user_rating = game.concept.user_ratings.filter(profile=profile).first()
        return {
            'has_platinum': has_platinum,
            'rating_form': UserConceptRatingForm(instance=user_rating)
        }

    def _build_breadcrumbs(self, game, target_profile):
        """
        Build breadcrumb navigation.

        Args:
            game: Game instance
            target_profile: Profile instance or None

        Returns:
            list: Breadcrumb items
        """
        return [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games', 'url': reverse_lazy('games_list')},
            {'text': f"{game.title_name}"}
        ]
    
    def get_context_data(self, **kwargs):
        """
        Build context for game detail page.

        Delegates to helper methods for profile data, images, stats, trophies, and concept info.

        Returns:
            dict: Complete context for template rendering
        """
        context = super().get_context_data(**kwargs)
        game = self.object
        user = self.request.user

        # Get target profile (from URL or authenticated user)
        target_profile = self._get_target_profile()
        psn_username = self.kwargs.get('psn_username')
        logger.info(f"Target Profile: {target_profile} | Profile Username: {psn_username}")

        # Build profile-specific context (progress, milestones, earned trophies)
        if target_profile:
            profile_context = self._build_profile_context(game, target_profile)
            context['profile'] = target_profile
            context['profile_progress'] = profile_context['profile_progress']
            context['profile_earned'] = profile_context['profile_earned']
            context['profile_trophy_totals'] = profile_context['profile_trophy_totals']
            context['profile_group_totals'] = profile_context['profile_group_totals']
            context['milestones'] = profile_context['milestones']
        else:
            context['profile'] = None
            context['profile_progress'] = None
            context['profile_earned'] = {}
            context['profile_trophy_totals'] = {}
            context['profile_group_totals'] = {}
            context['milestones'] = [
                {'label': 'First Trophy'},
                {'label': '50% Trophy'},
                {'label': 'Platinum Trophy'},
                {'label': '100% Trophy'}
            ]

        # Build game images context
        context['image_urls'] = self._build_images_context(game)

        # Build game statistics context
        context['game_stats'] = self._build_game_stats_context(game)

        # Build trophy context with filtering/sorting
        form = GameDetailForm(self.request.GET)
        context['form'] = form
        profile_earned = context.get('profile_earned', {})
        full_trophies, trophy_groups, grouped_trophies, has_trophies = self._build_trophy_context(game, form, profile_earned)

        if has_trophies:
            context['grouped_trophies'] = grouped_trophies
            context['trophy_groups'] = trophy_groups
        else:
            context['trophies_syncing'] = True
            context['grouped_trophies'] = {}
            context['trophy_groups'] = {}

        # Build concept-related context (community ratings, badges, other versions)
        concept_context = self._build_concept_context(game)
        context.update(concept_context)

        # Add checklist count for the concept (for initial badge display)
        if game.concept:
            context['checklist_count'] = Checklist.objects.active().published().filter(concept=game.concept).count()
            # Check if user has draft checklists for this concept
            if user.is_authenticated and hasattr(user, 'profile') and user.profile:
                context['user_draft_checklists'] = Checklist.objects.active().filter(
                    concept=game.concept,
                    profile=user.profile,
                    status='draft'
                )
            else:
                context['user_draft_checklists'] = []
        else:
            context['checklist_count'] = 0
            context['user_draft_checklists'] = []

        # Build user rating context (if earned platinum)
        rating_context = self._build_rating_context(user, game)
        context.update(rating_context)

        # Build breadcrumbs
        context['breadcrumb'] = self._build_breadcrumbs(game, target_profile)

        return context
    
    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        game = self.object
        concept = game.concept
        if not concept:
            return HttpResponseRedirect(request.path)
        
        user = request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') and user.profile and user.profile.is_linked else None
        if profile and concept.has_user_earned_platinum(profile):
            rating = concept.user_ratings.filter(profile=profile).first()
            form = UserConceptRatingForm(request.POST, instance=rating)
            if form.is_valid():
                rating = form.save(commit=False)
                rating.profile = profile
                rating.concept = concept
                rating.save()
                today = date.today().isoformat()
                averages_cache_key = f"concept:averages:{concept.concept_id}:{today}"
                cache.delete(averages_cache_key)

                # Check for rating milestones
                from trophies.services.milestone_service import check_all_milestones_for_user
                check_all_milestones_for_user(profile, criteria_type='rating_count')

                messages.success(request, 'Your rating has been submitted!')
            else:
                messages.error(request, "Invalid form submission.")

        return HttpResponseRedirect(request.path)
    
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
            order = ['game__title_name']
        elif sort_val == 'completion':
            order = ['-progress', 'game__title_name']
        elif sort_val == 'completion_inv':
            order = ['progress', 'game__title_name']
        elif sort_val == 'trophies':
            order = ['-annotated_total_trophies', 'game__title_name']
        elif sort_val == 'earned':
            order = ['-earned_trophies_count', 'game__title_name']
        elif sort_val == 'unearned':
            order = ['-unearned_trophies_count', 'game__title_name']

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

        # Delegate to tab-specific handler methods
        if tab == 'games':
            tab_context = self._build_games_tab_context(profile, per_page, page_number)
        elif tab == 'trophies':
            tab_context = self._build_trophies_tab_context(profile, per_page, page_number)
        elif tab == 'badges':
            tab_context = self._build_badges_tab_context(profile)
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

        return context
    
    def get_template_names(self):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            tab = self.request.GET.get('tab', 'games')
            if tab == 'games':
                return ['trophies/partials/profile_detail/game_list_items.html']
            elif tab == 'trophies':
               return ['trophies/partials/profile_detail/trophy_list_items.html'] 
        return super().get_template_names()
    
class TrophyCaseView(ProfileHotbarMixin, ListView):
    """
    Display user's platinum trophy collection for trophy case selection.

    Shows all platinum trophies earned by a user, allowing them to select
    up to 10 (premium) or 3 (free) trophies to showcase on their profile.
    Provides filtering by game name and pagination.
    """
    model = EarnedTrophy
    template_name = 'trophies/trophy_case.html'
    context_object_name = 'platinums'
    paginate_by = 25

    def get_queryset(self):
        form = TrophySearchForm(self.request.GET)
        profile = get_object_or_404(Profile, psn_username=self.kwargs['psn_username'].lower())
        if form.is_valid():
            query = form.cleaned_data.get('query')
            qs = EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='platinum').select_related('trophy', 'trophy__game').order_by(F('earned_date_time').desc(nulls_last=True))

            if query:
                qs = qs.filter(trophy__game__title_name__icontains=query)
            return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = Profile.objects.get(psn_username=self.kwargs['psn_username'].lower())
        selected_ids = list(profile.trophy_selections.values_list('earned_trophy_id', flat=True))
        
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Profiles', 'url': reverse_lazy('profiles_list')},
            {'text': f"{profile.display_psn_username}", 'url': reverse_lazy('profile_detail', kwargs={'psn_username': profile.psn_username})},
            {'text': 'Trophy Case'}
        ]
        context['is_paginated'] = self.object_list.count() > self.paginate_by
        context['profile'] = profile
        context['selected_ids'] = selected_ids
        context['selected_count'] = len(selected_ids)
        context['toggle_selection_url'] = reverse('toggle-selection')

        is_own_profile = self.request.user.is_authenticated and self.request.user.profile == profile
        max_selections = 10 if profile.user_is_premium else 3 if is_own_profile else 0
        context['max_selections'] = max_selections

        return context

class ToggleSelectionView(LoginRequiredMixin, ProfileHotbarMixin, View):
    """
    AJAX endpoint to add or remove trophies from user's trophy case selection.

    Handles toggling trophy selections with validation for:
    - Maximum selection limits (3 for free users, 10 for premium)
    - Trophy ownership verification
    - Platinum trophy type requirement

    Returns JSON response with success/error status.
    """
    def post(self, request):
        earned_trophy_id = request.POST.get('earned_trophy_id')
        if not earned_trophy_id:
            return JsonResponse({'success': False, 'error': 'earned_trophy_id required.'}, status=400)
        try:
            earned_trophy_id = int(earned_trophy_id)
            profile = request.user.profile
            earned_trophy = EarnedTrophy.objects.get(id=earned_trophy_id)

            if earned_trophy.profile != profile:
                return JsonResponse({'success': False, 'error': 'Unauthorized: Not your trophy'}, status=403)
            
            is_premium = profile.user_is_premium
            max_selections = 10 if is_premium else 3
            current_count = UserTrophySelection.objects.filter(profile=profile).count()

            selection, created = UserTrophySelection.objects.get_or_create(profile=profile, earned_trophy_id=earned_trophy_id)
            if not created:
                selection.delete()
                action = 'removed'
            else:
                if current_count >= max_selections:
                    selection.delete()
                    return JsonResponse({'success': False, 'error': f'Max selections reached ({max_selections})'}, status=400)
                action = 'added'
            return JsonResponse({'success': True, 'action': action})
        except EarnedTrophy.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Invalid earned_trophy_id'}, status=400)
        except Profile.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'No profile found'}, status=400)
        except Exception as e:
            logger.error(f"Selection toggle error: {e}")
            return JsonResponse({'success': False, 'error': 'Internal error'}, status=500)

class BadgeListView(ProfileHotbarMixin, ListView):
    """
    Display list of all badge series with progress tracking for authenticated users.

    Shows tier 1 badges for each series, with earned status and completion progress
    for logged-in users. Includes trophy totals and game counts for each series.
    """
    model = Badge
    template_name = 'trophies/badge_list.html'
    context_object_name = 'display_data'
    paginate_by = None

    def get_queryset(self):
        qs = super().get_queryset()
        form = BadgeSearchForm(self.request.GET)

        if form.is_valid():
            series_slug = slugify(form.cleaned_data.get('series_slug'))
            if series_slug:
                qs = qs.filter(series_slug__icontains=series_slug)
        return qs

    def _calculate_series_stats(self, series_slug):
        """
        Calculate total games and trophy counts for a badge series.

        Args:
            series_slug: Badge series slug

        Returns:
            tuple: (total_games, trophy_types_dict)
        """
        all_games = Game.objects.filter(concept__stages__series_slug=series_slug).distinct()
        total_games = all_games.count()
        trophy_types = {
            'bronze': sum(game.defined_trophies['bronze'] for game in all_games),
            'silver': sum(game.defined_trophies['silver'] for game in all_games),
            'gold': sum(game.defined_trophies['gold'] for game in all_games),
            'platinum': sum(game.defined_trophies['platinum'] for game in all_games),
        }
        return total_games, trophy_types

    def _build_badge_display_data(self, grouped_badges, profile=None):
        """
        Build display data for badges with optional progress tracking.

        Consolidates logic for both authenticated and unauthenticated states.

        Args:
            grouped_badges: Dict of {series_slug: [badge list]}
            profile: Profile instance or None

        Returns:
            list: Display data dicts for each badge series
        """
        display_data = []

        # Get user progress data if authenticated
        earned_dict = {}
        progress_dict = {}
        if profile:
            user_earned = UserBadge.objects.filter(profile=profile).values('badge__series_slug').annotate(max_tier=Max('badge__tier'))
            earned_dict = {e['badge__series_slug']: e['max_tier'] for e in user_earned}

            all_badges_ids = [b.id for group in grouped_badges.values() for b in group]
            progress_qs = UserBadgeProgress.objects.filter(profile=profile, badge__id__in=all_badges_ids)
            progress_dict = {p.badge.id: p for p in progress_qs}

        # Build display data for each series
        for slug, group in grouped_badges.items():
            sorted_group = sorted(group, key=lambda b: b.tier)
            if not sorted_group:
                continue

            tier1_badge = next((b for b in sorted_group if b.tier == 1), None)
            if not tier1_badge:
                continue

            # Calculate series stats
            total_games, trophy_types = self._calculate_series_stats(tier1_badge.series_slug)
            tier1_earned_count = tier1_badge.earned_count

            # Determine display badge and progress
            if profile:
                highest_tier = earned_dict.get(slug, 0)
                display_badge = next((b for b in sorted_group if b.tier == highest_tier), None) if highest_tier > 0 else tier1_badge
                if not display_badge:
                    continue

                is_earned = highest_tier > 0
                next_badge = next((b for b in sorted_group if b.tier > highest_tier), None)
                progress_badge = next_badge if next_badge else display_badge

                # Calculate progress
                progress = progress_dict.get(progress_badge.id) if progress_badge else None
                required_stages = progress_badge.required_stages
                if progress and progress_badge.badge_type in ['series', 'collection', 'megamix']:
                    completed_concepts = progress.completed_concepts
                    progress_percentage = (completed_concepts / required_stages) * 100 if required_stages > 0 else 0
                else:
                    completed_concepts = 0
                    progress_percentage = 0
            else:
                # Unauthenticated user - show tier 1
                display_badge = tier1_badge
                is_earned = False
                completed_concepts = 0
                required_stages = tier1_badge.required_stages
                progress_percentage = 0

            display_data.append({
                'badge': display_badge,
                'tier1_earned_count': tier1_earned_count,
                'completed_concepts': completed_concepts,
                'required_stages': required_stages,
                'progress_percentage': round(progress_percentage, 1),
                'trophy_types': trophy_types,
                'total_games': total_games,
                'is_earned': is_earned,
            })

        return display_data
    
    def get_context_data(self, **kwargs):
        """
        Build context for badge list page.

        Groups badges by series, calculates progress for authenticated users,
        and handles sorting and pagination.

        Returns:
            dict: Context with paginated badge display data
        """
        context = super().get_context_data(**kwargs)
        badges = context['object_list']

        # Group badges by series
        grouped_badges = defaultdict(list)
        for badge in badges:
            if badge.effective_user_title:
                grouped_badges[badge.series_slug].append(badge)

        # Build display data (unified for auth/unauth users)
        user = self.request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') else None
        display_data = self._build_badge_display_data(grouped_badges, profile)

        # Sort data
        sort_val = self.request.GET.get('sort', 'tier')
        if sort_val == 'name':
            display_data.sort(key=lambda d: d['badge'].effective_display_title or '')
        elif sort_val == 'tier':
            display_data.sort(key=lambda d: (d['badge'].tier, d['badge'].effective_display_title or ''))
        elif sort_val == 'tier_desc':
            display_data.sort(key=lambda d: (-d['badge'].tier, d['badge'].effective_display_title or ''))
        elif sort_val == 'earned':
            display_data.sort(key=lambda d: (-d['tier1_earned_count'], d['badge'].effective_display_title or ''))
        elif sort_val == 'earned_inv':
            display_data.sort(key=lambda d: (d['tier1_earned_count'], d['badge'].effective_display_title or ''))
        else:
            display_data.sort(key=lambda d: d['badge'].effective_display_series or '')

        # Paginate
        paginate_by = 25
        paginator = Paginator(display_data, paginate_by)
        page_number = self.request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context['display_data'] = page_obj
        context['page_obj'] = page_obj
        context['paginator'] = paginator
        context['is_paginated'] = page_obj.has_other_pages()

        # Breadcrumbs and form
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges'},
        ]
        context['form'] = BadgeSearchForm(self.request.GET)
        context['selected_tiers'] = self.request.GET.getlist('tier')

        return context

class BadgeDetailView(ProfileHotbarMixin, DetailView):
    """
    Display detailed badge series information with progress tracking.

    Shows all tiers in a badge series, user's progress (if authenticated),
    required games organized by stages, and completion statistics.
    Dynamically displays highest earned tier or next tier to unlock.
    """
    model = Badge
    template_name = 'trophies/badge_detail.html'
    slug_field = 'series_slug'
    slug_url_kwarg = 'series_slug'
    context_object_name = 'series_badges'

    def get_object(self, queryset=None):
        series_slug = self.kwargs[self.slug_url_kwarg]
        return Badge.objects.by_series(series_slug)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        series_badges = context['object']

        if not series_badges.exists():
            raise Http404("Series not found")

        psn_username = self.kwargs.get('psn_username')
        if psn_username:
            target_profile = get_object_or_404(Profile, psn_username__iexact=psn_username)
        elif self.request.user.is_authenticated and hasattr(self.request.user, 'profile'):
            target_profile = self.request.user.profile
        else:
            target_profile = None
        
        context['target_profile'] = target_profile

        badge = None
        is_earned = True
        if target_profile:
            highest_tier_earned = UserBadge.objects.filter(profile=target_profile, badge__series_slug=self.kwargs['series_slug']).aggregate(max_tier=Max('badge__tier'))['max_tier'] or 0
            badge = series_badges.filter(tier=highest_tier_earned).first()
            if not badge:
                badge = series_badges.order_by('tier').first()
                context['is_maxed'] = True
                is_earned = False
            else:
                context['is_maxed'] = False

            context['badge'] = badge

            progress = UserBadgeProgress.objects.filter(profile=target_profile, badge=badge).first()
            context['progress'] = progress
            context['progress_percent'] = progress.completed_concepts / badge.required_stages * 100 if progress and badge.required_stages > 0 else 0
        else:
            badge = series_badges.filter(tier=1).first()
            context['badge'] = badge

        stages = Stage.objects.filter(series_slug=badge.series_slug).order_by('stage_number').prefetch_related(
            Prefetch('concepts__games', queryset=Game.objects.all().order_by('title_name'))
        )
        context['stage_count'] = stages.count()
        
        today = date.today().isoformat()
        stats_timeout = 3600
        structured_data = []
        for stage in stages:
            games = set()
            for concept in stage.concepts.all():
                games.update(concept.games.all())
            games = sorted(games, key=lambda g: g.title_name)

            profile_games = {}
            if target_profile:
                profile_games_qs = ProfileGame.objects.filter(profile=target_profile, game__in=games).select_related('game')
                profile_games = {pg.game: pg for pg in profile_games_qs}

            community_ratings = {}
            for game in games:
                averages_cache_key = f"concept:averages:{game.concept.concept_id}:{today}"
                cached_averages = cache.get(averages_cache_key)
                if cached_averages:
                    averages = json.loads(cached_averages)
                else:
                    averages = game.concept.get_community_averages()
                    if averages:
                        cache.set(averages_cache_key, json.dumps(averages), timeout=stats_timeout)
                community_ratings[game] = averages

            structured_data.append({
                'stage': stage,
                'games': [{'game': game, 'profile_game': profile_games.get(game, None), 'community_ratings': community_ratings.get(game, None)} for game in games]
            })

        all_badges = Badge.objects.by_series(badge.series_slug)
        badge_completion = {b.tier: b.get_stage_completion(target_profile, b.badge_type) for b in all_badges}

        # Add required_stages for each tier (useful for megamix badges)
        badge_requirements = {b.tier: b.required_stages for b in all_badges}

        logger.debug(f"Badge detail loaded {len(structured_data)} stage data entries for {badge.series_slug}")
        context['stage_data'] = structured_data
        context['completion'] = badge_completion
        context['badge_requirements'] = badge_requirements
        context['is_earned'] = is_earned

        context['image_urls'] = {'bg_url': badge.most_recent_concept.bg_url, 'recent_concept_icon_url': badge.most_recent_concept.concept_icon_url}
        context['recent_concept_name'] = badge.most_recent_concept.unified_title

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': context['badge'].effective_display_series},
        ]

        return context

class BadgeLeaderboardsView(ProfileHotbarMixin, DetailView):
    """
    Display leaderboards for a specific badge series.

    Shows two leaderboards:
    1. Earners - Users who have earned the highest tier
    2. Progress - Users making progress on the badge series

    Leaderboards are cached and refreshed periodically. Shows user's rank if authenticated.
    """
    model = Badge
    template_name = 'trophies/badge_leaderboards.html'
    slug_field = 'series_slug'
    slug_url_kwarg = 'series_slug'
    context_object_name = 'series_badges'

    def get_object(self, queryset=None):
        series_slug = self.kwargs[self.slug_url_kwarg]
        return Badge.objects.get(series_slug=series_slug, tier=1)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        badge = self.object
        series_slug = badge.series_slug
        user = self.request.user

        earners_key = f"lb_earners_{series_slug}"
        progress_key = f"lb_progress_{series_slug}"

        lb_earners = cache.get(earners_key, [])
        lb_earners_paginate_by = 50
        lb_progress = cache.get(progress_key, [])
        lb_progress_paginate_by = 50

        context['lb_earners_refresh_time'] = cache.get(f"{earners_key}_refresh_time")
        context['lb_progress_refresh_time'] = cache.get(f"{progress_key}_refresh_time")

        if user.is_authenticated and hasattr(user, 'profile'):
            # Find user profile
            user_psn = user.profile.display_psn_username
            for idx, entry in enumerate(lb_earners):
                if entry['psn_username'] == user_psn:
                    context['lb_earners_user_page'] = (idx // lb_earners_paginate_by) + 1
                    context['lb_earners_user_rank'] = idx + 1
                    break
            for idx, entry in enumerate(lb_progress):
                if entry['psn_username'] == user_psn:
                    context['lb_progress_user_page'] = (idx // lb_progress_paginate_by) + 1
                    context['lb_progress_user_rank'] = idx + 1

        lb_earners_paginator = Paginator(lb_earners, lb_earners_paginate_by)
        lb_earners_page = self.request.GET.get('lb_earners_page', 1)
        context['lb_earners_page_obj'] = lb_earners_paginator.get_page(lb_earners_page)
        context['lb_earners_paginator'] = lb_earners_paginator

        lb_progress_paginator = Paginator(lb_progress, lb_progress_paginate_by)
        lb_progress_page = self.request.GET.get('lb_progress_page', 1)
        context['lb_progress_page_obj'] = lb_progress_paginator.get_page(lb_progress_page)
        context['lb_progress_paginator'] = lb_progress_paginator

        context['badge'] = badge
        context['image_urls'] = {'bg_url': badge.most_recent_concept.bg_url, 'recent_concept_icon_url': badge.most_recent_concept.concept_icon_url}
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': context['badge'].effective_display_series, 'url': reverse_lazy('badge_detail', kwargs={'series_slug': badge.series_slug})},
            {'text': 'Leaderboards'},
        ]
        
        return context

class OverallBadgeLeaderboardsView(ProfileHotbarMixin, TemplateView):
    """
    Display overall badge leaderboards across all badge series.

    Shows two global leaderboards:
    1. Total XP - Users with the most badge experience points
    2. Total Progress - Users with the most badge completion percentage

    Leaderboards are cached and refreshed periodically. Shows user's rank if authenticated.
    """
    template_name = 'trophies/overall_badge_leaderboards.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        xp_key = f"lb_total_xp"
        progress_key = f"lb_total_progress"

        lb_total_xp = cache.get(xp_key, [])
        lb_total_xp_paginate_by = 50
        lb_total_progress = cache.get(progress_key, [])
        lb_total_progress_paginate_by = 50

        context['lb_total_xp_refresh_time'] = cache.get(f"{xp_key}_refresh_time")
        context['lb_total_progress_refresh_time'] = cache.get(f"{progress_key}_refresh_time")

        if user.is_authenticated and hasattr(user, 'profile'):
            # Find user profile
            user_psn = user.profile.display_psn_username
            for idx, entry in enumerate(lb_total_xp):
                if entry['psn_username'] == user_psn:
                    context['lb_total_xp_user_page'] = (idx // lb_total_xp_paginate_by) + 1
                    context['lb_total_xp_user_rank'] = idx + 1
                    break
            for idx, entry in enumerate(lb_total_progress):
                if entry['psn_username'] == user_psn:
                    context['lb_total_progress_user_page'] = (idx // lb_total_progress_paginate_by) + 1
                    context['lb_total_progress_user_rank'] = idx + 1

        lb_total_xp_paginator = Paginator(lb_total_xp, lb_total_xp_paginate_by)
        lb_total_xp_page = self.request.GET.get('lb_total_xp_page', 1)
        context['lb_total_xp_page_obj'] = lb_total_xp_paginator.get_page(lb_total_xp_page)
        context['lb_total_xp_paginator'] = lb_total_xp_paginator

        lb_total_progress_paginator = Paginator(lb_total_progress, lb_total_progress_paginate_by)
        lb_total_progress_page = self.request.GET.get('lb_total_progress_page', 1)
        context['lb_total_progress_page_obj'] = lb_total_progress_paginator.get_page(lb_total_progress_page)
        context['lb_total_progress_paginator'] = lb_total_progress_paginator

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': 'Leaderboards'},
        ]
        
        return context


class MilestoneListView(ProfileHotbarMixin, ListView):
    """
    Display list of all milestones with progress tracking for authenticated users.

    Shows all milestones ordered by required value, with earned status and
    completion progress for logged-in users. Basic info shown for guests.
    """
    model = Milestone
    template_name = 'trophies/milestone_list.html'
    context_object_name = 'milestones'

    def get_queryset(self):
        """
        Fetch milestones ordered by required_value.

        Returns:
            QuerySet: All milestones ordered by required value ascending
        """
        return Milestone.objects.ordered_by_value()

    def _build_milestone_display_data(self, milestones, profile=None):
        """
        Build display data for milestones with optional progress tracking.

        For authenticated users, filters milestones to show only:
        - All earned milestones
        - The next unearned milestone for each criteria_type

        Args:
            milestones: QuerySet of Milestone objects
            profile: Profile instance or None

        Returns:
            list: Display data dicts for each milestone
        """
        display_data = []

        # Get user progress/earned data if authenticated
        earned_milestone_ids = set()
        progress_dict = {}

        if profile:
            # Get earned milestones
            earned_milestone_ids = set(
                UserMilestone.objects.filter(profile=profile)
                .values_list('milestone_id', flat=True)
            )

            # Get progress for all milestones
            progress_qs = UserMilestoneProgress.objects.filter(
                profile=profile,
                milestone__in=milestones
            )
            progress_dict = {p.milestone_id: p.progress_value for p in progress_qs}

        # Group milestones by criteria_type
        milestones_by_type = {}
        for milestone in milestones:
            criteria_type = milestone.criteria_type
            if criteria_type not in milestones_by_type:
                milestones_by_type[criteria_type] = []
            milestones_by_type[criteria_type].append(milestone)

        # Calculate tier info for each milestone type (total tiers and current tier)
        tier_info = {}
        for criteria_type, type_milestones in milestones_by_type.items():
            # Sort by required_value to ensure proper ordering
            sorted_milestones = sorted(type_milestones, key=lambda m: m.required_value)
            total_tiers = len(sorted_milestones)

            # Find current tier (1-indexed) - the tier the user is working on
            current_tier = 1
            for idx, m in enumerate(sorted_milestones, start=1):
                if m.id in earned_milestone_ids:
                    # User has completed this tier, move to next
                    current_tier = idx + 1
                else:
                    # Found first unearned tier - this is what they're working on
                    current_tier = idx
                    break

            # If all tiers are earned, current_tier will be total_tiers + 1
            # Cap it at total_tiers
            if current_tier > total_tiers:
                current_tier = total_tiers

            tier_info[criteria_type] = {
                'total_tiers': total_tiers,
                'current_tier': current_tier
            }

        # Filter to show only earned + next unearned per criteria_type (only for authenticated users)
        if profile:
            filtered_milestones = []
            for criteria_type, type_milestones in milestones_by_type.items():
                # Sort by required_value to ensure proper ordering
                type_milestones.sort(key=lambda m: m.required_value)

                # Add all earned milestones and track if we found the next unearned
                found_next_unearned = False
                for milestone in type_milestones:
                    is_earned = milestone.id in earned_milestone_ids

                    if is_earned:
                        # Include all earned milestones
                        filtered_milestones.append(milestone)
                    elif not found_next_unearned:
                        # Include the first unearned milestone (the next one to work towards)
                        filtered_milestones.append(milestone)
                        found_next_unearned = True
                    # Skip all other unearned milestones
        else:
            # For guests, show all milestones
            filtered_milestones = list(milestones)

        # Build display data for filtered milestones
        for milestone in filtered_milestones:
            is_earned = milestone.id in earned_milestone_ids
            progress_value = progress_dict.get(milestone.id, 0)
            required_value = milestone.required_value

            # Calculate progress percentage
            if required_value > 0:
                progress_percentage = min((progress_value / required_value) * 100, 100)
            else:
                progress_percentage = 100 if is_earned else 0

            # Get tier information for this milestone
            criteria_type = milestone.criteria_type
            milestone_tier_info = tier_info.get(criteria_type, {'total_tiers': 1, 'current_tier': 1})

            display_data.append({
                'milestone': milestone,
                'is_earned': is_earned,
                'progress_value': progress_value,
                'required_value': required_value,
                'progress_percentage': round(progress_percentage, 1),
                'earned_count': milestone.earned_count,
                'total_tiers': milestone_tier_info['total_tiers'],
                'current_tier': milestone_tier_info['current_tier'],
            })

        return display_data

    def get_context_data(self, **kwargs):
        """
        Build context for milestone list page.

        Returns:
            dict: Context with milestone display data
        """
        context = super().get_context_data(**kwargs)
        milestones = context['object_list']

        # Get profile for authenticated users
        user = self.request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') else None

        # Build display data
        display_data = self._build_milestone_display_data(milestones, profile)

        # Sort display data: unearned milestones first (by progress % descending),
        # then earned milestones (by required_value ascending)
        if profile:
            display_data.sort(
                key=lambda x: (
                    x['is_earned'],  # False (0) before True (1) - unearned first
                    -x['progress_percentage'] if not x['is_earned'] else 0,  # Higher progress first for unearned
                    x['milestone'].required_value if x['is_earned'] else 0  # Lower required_value first for earned
                )
            )

        context['display_data'] = display_data

        # Breadcrumbs
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': 'Milestones'},
        ]

        return context


class GuideListView(ProfileHotbarMixin, ListView):
    """
    Display list of available trophy guides (PPTV section).

    Shows game concepts that have associated guide content, with options to:
    - Search by game name
    - Sort by release date
    - View featured guide of the day

    Featured guide is cached daily and rotates based on priority or randomly.
    """
    model = Concept
    template_name = 'trophies/guide_list.html'
    context_object_name = 'guides'
    paginate_by = 6

    def get_queryset(self):
        qs = Concept.objects.exclude(Q(guide_slug__isnull=True) | Q(guide_slug=''))
        form = GuideSearchForm(self.request.GET)
        order = ['unified_title']

        if form.is_valid():
            query = form.cleaned_data.get('query')
            sort_val = form.cleaned_data.get('sort')
            
            if query:
                qs = qs.filter(Q(unified_title__icontains=query))
            
            if sort_val == 'release_asc':
                order = ['release_date', 'unified_title']
            elif sort_val == 'release_desc':
                order = ['-release_date', 'unified_title']
            
        return qs.order_by(*order)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today_utc = timezone.now().date().isoformat()
        cache_key = f"featured_guide:{today_utc}"

        cached_value = cache.get(cache_key)
        if cached_value is None:
            featured_qs = FeaturedGuide.objects.filter(
                Q(start_date__lte=timezone.now()) & (Q(end_date__gte=timezone.now()) | Q(end_date__isnull=True))
            ).order_by('-priority').first()
            if featured_qs:
                featured_concept = featured_qs.concept
            else:
                guides = Concept.objects.exclude(Q(guide_slug__isnull=True) | Q(guide_slug=''))
                if guides.exists():
                    featured_concept = choice(guides)
                else:
                    featured_concept = None
            
            if featured_concept:
                cache.set(cache_key, featured_concept.id, timeout=86400)
            else:
                cache.set(cache_key, -1, timeout=86400)
        else:    
            if cached_value == -1:
                featured_concept = None
            else:
                featured_concept = Concept.objects.get(id=cached_value)
        
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'PPTV'}
        ]

        context['featured_concept'] = featured_concept
        context['form'] = GuideSearchForm(self.request.GET)
        context['is_paginated'] = self.object_list.count() > self.paginate_by

        return context

# Profile Linking Views

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

# Hotbar Views

class ProfileSyncStatusView(LoginRequiredMixin, View):
    """
    AJAX endpoint for polling profile sync status in navigation hotbar.

    Returns current sync status, progress percentage, and cooldown time.
    Used by frontend to display sync progress bar and enable/disable sync button.

    Rate limited to 60 requests per minute per user.
    """
    @method_decorator(ratelimit(key='user', rate='60/m', method='GET'))
    def get(self, request):
        profile = request.user.profile
        seconds_to_next_sync = profile.get_seconds_to_next_sync()
        logger.debug(f"Sync status check for {profile.psn_username}: {seconds_to_next_sync}s until next sync")
        data = {
            'sync_status': profile.sync_status,
            'sync_progress': profile.sync_progress_value,
            'sync_target': profile.sync_progress_target,
            'sync_percentage': profile.sync_progress_value / profile.sync_progress_target * 100 if profile.sync_progress_target > 0 else 0,
            'seconds_to_next_sync': seconds_to_next_sync,
        }
        return JsonResponse(data)

class TriggerSyncView(LoginRequiredMixin, View):
    """
    AJAX endpoint to manually trigger profile sync from navigation hotbar.

    Validates cooldown period and initiates sync via job queue.
    Returns error if sync is already in progress or cooldown is active.
    """
    def post(self, request):
        profile = request.user.profile
        if not profile:
            return JsonResponse({'error': 'No linked profile'}, status=400)
        
        is_syncing = profile.attempt_sync()
        if not is_syncing:
            seconds_left = profile.get_seconds_to_next_sync()
            return JsonResponse({'error': f'Cooldown active: {seconds_left} seconds left'}, status=429)
        return JsonResponse({'success': True, 'message': 'Sync started'})

class SearchSyncProfileView(View):
    """
    AJAX endpoint to search for and add PSN profiles to the database.

    Creates profile if it doesn't exist and initiates initial sync.
    If profile exists, triggers a sync update.
    Used by admin/moderator tools for adding new profiles.
    """
    def post(self, request):
        psn_username = request.POST.get('psn_username')
        if not psn_username:
            return JsonResponse({'error': 'Username required'}, status=400)
        
        is_new = False
        try:
            profile = Profile.objects.get(psn_username__iexact=psn_username)
        except Profile.DoesNotExist:
            profile = Profile.objects.create(
                psn_username=psn_username.lower()
            )
            is_new = True
        
        if is_new:
            PSNManager.initial_sync(profile)
        else:
            profile.attempt_sync()
        return JsonResponse({
            'success': True,
            'message': f"{'Added and syncing' if is_new else 'Syncing'} {psn_username}",
            'psn_username': profile.psn_username,
        })

class AddSyncStatusView(View):
    """
    AJAX endpoint to poll sync status after adding a new profile.

    Returns sync status, account ID, and profile URL.
    Used by admin/moderator tools to track sync progress after adding profiles.
    """
    def get(self, request):
        psn_username = request.GET.get('psn_username')
        if not psn_username:
            return JsonResponse({'error': 'Username required'}, status=400)
        
        try:
            profile = Profile.objects.get(psn_username__iexact=psn_username)
        except Profile.DoesNotExist:
            data = {
                'sync_status': 'error',
                'account_id': '',
            }
            return JsonResponse(data)
        
        data = {
            'sync_status': profile.sync_status,
            'account_id': profile.account_id,
            'psn_username': profile.psn_username,
            'slug': f"/profiles/{profile.psn_username}/",
        }
        return JsonResponse(data)

# Monitoring Views

@method_decorator(staff_member_required, name='dispatch')
class TokenMonitoringView(TemplateView):
    """
    Admin dashboard for monitoring PSN API token usage and sync worker machines.

    Displays:
    - Token usage statistics per worker machine
    - Queue depth and processing rates
    - Profile sync queue statistics
    - Error rates and health metrics

    Restricted to staff members only.
    """
    template_name = 'trophies/token_monitoring.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            aggregated_stats = self.get_aggregated_stats()
            context['machines'] = aggregated_stats
            context['queue_stats'] = self.get_queue_stats()
            context['profile_queue_stats'] = self.get_profile_queue_stats()
        except Exception as e:
            logger.error(f"Error fetching aggregated stats for monitoring: {e}")
            context['machines'] = {}
            context['queue_stats'] = {}
            context['profile_queue_stats'] = {}
            context['error'] = "Unable to load stats. Check logs for details."
        return context
    
    def get_aggregated_stats(self):
        aggregated = {}
        keys = redis_client.keys("token_keeper_latest_stats:*")
        for key in keys:
            stats_json = redis_client.get(key)
            if stats_json:
                try:
                    stats = json.loads(stats_json)
                    machine_id = stats['machine_id']
                    group_id = stats.get('group_id', 'default')
                    if machine_id not in aggregated:
                        aggregated[machine_id] = {}
                    if group_id not in aggregated[machine_id]:
                        aggregated[machine_id][group_id] = {}
                    aggregated[machine_id][group_id]['instances'] = stats['instances']
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in Redis key {key}")
        return aggregated
    
    def get_queue_stats(self):
        queues = ['high_priority_jobs', 'medium_priority_jobs', 'low_priority_jobs']
        stats = {}
        for queue in queues:
            try:
                length = redis_client.llen(queue)
                stats[queue] = length
            except Exception as e:
                logger.error(f"Error fetching length for queue {queue}: {e}")
                stats[queue] = 'Error'
        return stats
    
    def get_profile_queue_stats(self):
        stats = {}
        queues = ['high_priority', 'medium_priority', 'low_priority']
        for queue in queues:
            keys = redis_client.keys(f"profile_jobs:*:{queue}")
            for key in keys:
                profile_id = key.decode().split(':')[1]
                count = int(redis_client.get(key) or 0)
                if profile_id not in stats:
                    stats[profile_id] = {}
                stats[profile_id][queue] = count
        for profile_id in stats:
            stats[profile_id]['total'] = sum(stats[profile_id].values())
        return stats
    
# Admin Views

@method_decorator(staff_member_required, name='dispatch')
class BadgeCreationView(FormView):
    """
    Admin tool for creating new badge series with multiple tiers.

    Provides form interface for defining:
    - Badge series metadata (name, slug, description)
    - Multiple badge tiers with requirements
    - Associated game concepts and stages
    - Badge icons and visual assets

    Restricted to staff members only.
    """
    template_name = 'trophies/badge_creation.html'
    form_class = BadgeCreationForm
    success_url = '/staff/badge-create/'

    def form_valid(self, form):
        try:
            badge_data = form.get_badge_data()
            PsnApiService.create_badge_group_from_form(badge_data)
            messages.success(self.request, 'Badge group created successfully!')
        except Exception as e:
            logger.error(f"Error creating badge: {e}")
            messages.error(self.request, 'Error creating badge. Check logs.')
        return super().form_valid(form)


@method_decorator(staff_member_required, name='dispatch')
class CommentModerationView(ListView):
    """
    Staff-only comment moderation dashboard.

    Displays pending reports with full context and provides actions
    to dismiss, delete, or review reports.
    """
    model = CommentReport
    template_name = 'trophies/moderation/comment_moderation.html'
    context_object_name = 'reports'
    paginate_by = 20

    def get_queryset(self):
        """Return reports based on selected tab/filter."""
        queryset = CommentReport.objects.select_related(
            'comment',
            'comment__profile',
            'comment__concept',
            'reporter',
            'reviewed_by'
        ).prefetch_related(
            'comment__reports'  # All reports for this comment
        )

        # Filter by status (from query params)
        status_filter = self.request.GET.get('status', 'pending')
        if status_filter != 'all':
            queryset = queryset.filter(status=status_filter)

        # Filter by reason
        reason_filter = self.request.GET.get('reason')
        if reason_filter:
            queryset = queryset.filter(reason=reason_filter)

        # Search by comment text or reporter username
        search_query = self.request.GET.get('search')
        if search_query:
            queryset = queryset.filter(
                Q(comment__body__icontains=search_query) |
                Q(reporter__psn_username__icontains=search_query) |
                Q(details__icontains=search_query)
            )

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Count by status for tabs
        context['pending_count'] = CommentReport.objects.filter(status='pending').count()
        context['reviewed_count'] = CommentReport.objects.filter(status='reviewed').count()
        context['dismissed_count'] = CommentReport.objects.filter(status='dismissed').count()
        context['action_taken_count'] = CommentReport.objects.filter(status='action_taken').count()

        # Current filters
        context['current_status'] = self.request.GET.get('status', 'pending')
        context['current_reason'] = self.request.GET.get('reason', '')
        context['search_query'] = self.request.GET.get('search', '')

        # Reason choices for filter dropdown
        context['reason_choices'] = CommentReport.REPORT_REASONS

        # Recent moderation activity (last 10 actions)
        context['recent_actions'] = ModerationLog.objects.select_related(
            'moderator',
            'comment_author'
        ).order_by('-timestamp')[:10]

        return context


@method_decorator(staff_member_required, name='dispatch')
class ModerationActionView(View):
    """
    Handle moderation actions (delete, dismiss, review).

    POST endpoint for AJAX requests from moderation dashboard.
    """

    def post(self, request, report_id):
        """Process moderation action."""
        report = get_object_or_404(
            CommentReport.objects.select_related('comment', 'comment__profile'),
            id=report_id
        )

        action = request.POST.get('action')
        reason = request.POST.get('reason', '')
        internal_notes = request.POST.get('internal_notes', '')

        if action == 'delete':
            # Soft-delete comment and log action
            report.comment.soft_delete(
                moderator=request.user,
                reason=reason,
                request=request
            )

            # Update report status
            report.status = 'action_taken'
            report.reviewed_by = request.user
            report.reviewed_at = timezone.now()
            report.admin_notes = internal_notes
            report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'admin_notes'])

            messages.success(request, f"Comment deleted and logged. Report marked as action taken.")

        elif action == 'dismiss':
            # Dismiss report without action
            ModerationLog.objects.create(
                moderator=request.user,
                action_type='dismiss_report',
                comment=report.comment,
                comment_id_snapshot=report.comment.id,
                comment_author=report.comment.profile,
                original_body=report.comment.body,
                concept=report.comment.concept,
                trophy_id=report.comment.trophy_id,
                related_report=report,
                reason=reason,
                internal_notes=internal_notes
            )

            report.status = 'dismissed'
            report.reviewed_by = request.user
            report.reviewed_at = timezone.now()
            report.admin_notes = internal_notes
            report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'admin_notes'])

            messages.success(request, "Report dismissed and logged.")

        elif action == 'review':
            # Mark as reviewed without action
            ModerationLog.objects.create(
                moderator=request.user,
                action_type='report_reviewed',
                comment=report.comment,
                comment_id_snapshot=report.comment.id,
                comment_author=report.comment.profile,
                original_body=report.comment.body,
                concept=report.comment.concept,
                trophy_id=report.comment.trophy_id,
                related_report=report,
                reason=reason,
                internal_notes=internal_notes
            )

            report.status = 'reviewed'
            report.reviewed_by = request.user
            report.reviewed_at = timezone.now()
            report.admin_notes = internal_notes
            report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'admin_notes'])

            messages.info(request, "Report marked as reviewed.")

        else:
            messages.error(request, f"Unknown action: {action}")

        # Return JSON for AJAX or redirect for non-AJAX
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'action': action})

        return redirect('comment_moderation')


@method_decorator(staff_member_required, name='dispatch')
class ModerationLogView(ListView):
    """
    View complete moderation action history.

    Filterable by moderator, action type, date range.
    """
    model = ModerationLog
    template_name = 'trophies/moderation/moderation_log.html'
    context_object_name = 'logs'
    paginate_by = 50

    def get_queryset(self):
        queryset = ModerationLog.objects.select_related(
            'moderator',
            'comment_author',
            'concept',
            'related_report'
        )

        # Filter by moderator
        moderator_filter = self.request.GET.get('moderator')
        if moderator_filter:
            queryset = queryset.filter(moderator_id=moderator_filter)

        # Filter by action type
        action_filter = self.request.GET.get('action_type')
        if action_filter:
            queryset = queryset.filter(action_type=action_filter)

        # Filter by author (to see all actions against a user)
        author_filter = self.request.GET.get('author')
        if author_filter:
            queryset = queryset.filter(comment_author_id=author_filter)

        # Date range filter
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        if date_from:
            queryset = queryset.filter(timestamp__gte=date_from)
        if date_to:
            queryset = queryset.filter(timestamp__lte=date_to)

        return queryset.order_by('-timestamp')

    def get_context_data(self, **kwargs):
        from users.models import CustomUser

        context = super().get_context_data(**kwargs)

        # Filter choices
        context['action_type_choices'] = ModerationLog.ACTION_TYPES
        context['moderators'] = CustomUser.objects.filter(
            is_staff=True
        ).order_by('username')

        # Current filters
        context['current_moderator'] = self.request.GET.get('moderator', '')
        context['current_action_type'] = self.request.GET.get('action_type', '')
        context['current_author'] = self.request.GET.get('author', '')
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')

        # Stats
        context['total_actions'] = ModerationLog.objects.count()
        context['actions_today'] = ModerationLog.objects.filter(
            timestamp__gte=timezone.now().date()
        ).count()
        context['actions_this_week'] = ModerationLog.objects.filter(
            timestamp__gte=timezone.now() - timedelta(days=7)
        ).count()

        return context


# Checklist Views

class ChecklistDetailView(ProfileHotbarMixin, DetailView):
    """
    Display checklist detail with sections, items, and progress tracking.

    Shows the full checklist structure with checkboxes for tracking progress.
    Anyone can view guides. Authenticated users with linked PSN accounts can interact
    with checkboxes. Premium users and checklist authors can save progress.
    """
    model = Checklist
    template_name = 'trophies/checklist_detail.html'
    context_object_name = 'checklist'
    pk_url_kwarg = 'checklist_id'

    def dispatch(self, request, *args, **kwargs):
        """Allow anyone to view guides (no authentication required)."""
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        """Return checklist with optimized prefetches."""
        return Checklist.objects.active().with_author_data().with_sections()

    def get_object(self, queryset=None):
        """Get checklist and validate access."""
        checklist = super().get_object(queryset)

        # Check if checklist is accessible
        user = self.request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') else None

        # Draft checklists are only viewable by their author
        if checklist.status == 'draft':
            if not profile or checklist.profile != profile:
                raise Http404("Checklist not found")

        return checklist

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        checklist = self.object
        user = self.request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') else None

        # Get user's completed items
        completed_items = []
        user_progress = None
        if profile:
            user_progress = ChecklistService.get_user_progress(checklist, profile)
            if user_progress:
                completed_items = user_progress.completed_items

        context['completed_items'] = completed_items
        context['user_progress'] = user_progress

        # Get earned trophy item IDs for this user (auto-check earned trophies)
        earned_trophy_item_ids = set()
        if profile and hasattr(profile, 'is_linked') and profile.is_linked:
            from trophies.models import EarnedTrophy, ChecklistItem

            # Get all trophy items in this checklist
            trophy_items = ChecklistItem.objects.filter(
                section__checklist=checklist,
                item_type='trophy',
                trophy_id__isnull=False
            ).values_list('id', 'trophy_id')

            if trophy_items:
                item_to_trophy = {item_id: trophy_id for item_id, trophy_id in trophy_items}
                trophy_ids = list(item_to_trophy.values())

                # Query which trophies the user has earned
                earned_trophy_pks = set(
                    EarnedTrophy.objects.filter(
                        profile=profile,
                        trophy_id__in=trophy_ids,
                        earned=True
                    ).values_list('trophy_id', flat=True)
                )

                # Convert back to ChecklistItem IDs
                earned_trophy_item_ids = {
                    item_id for item_id, trophy_pk in item_to_trophy.items()
                    if trophy_pk in earned_trophy_pks
                }

        context['earned_trophy_item_ids'] = earned_trophy_item_ids

        # Calculate per-section completion counts and attach to section objects
        # Include both manually completed items AND earned trophies in the count
        completed_item_ids = set(completed_items) | earned_trophy_item_ids
        sections = checklist.sections.all()
        total_items_count = 0
        total_completed_count = 0
        for section in sections:
            section_item_ids = list(section.items.filter(item_type__in=['item', 'trophy']).values_list('id', flat=True))
            completed_count = sum(1 for item_id in section_item_ids if item_id in completed_item_ids)
            # Add completion data as attributes to the section object
            section.completed_count = completed_count
            section.total_count = len(section_item_ids)
            # Track totals for overall progress
            total_items_count += len(section_item_ids)
            total_completed_count += completed_count

        # Calculate adjusted progress that includes earned trophies
        adjusted_progress_percentage = (total_completed_count / total_items_count * 100) if total_items_count > 0 else 0
        context['adjusted_items_completed'] = total_completed_count
        context['adjusted_total_items'] = total_items_count
        context['adjusted_progress_percentage'] = adjusted_progress_percentage

        # Check permissions
        context['can_edit'] = profile and checklist.profile == profile and not checklist.is_deleted
        # can_save_progress returns (bool, str reason), we just need the bool
        can_save, _ = ChecklistService.can_save_progress(checklist, profile) if profile else (False, None)
        context['can_save_progress'] = can_save
        context['is_author'] = profile and checklist.profile == profile

        # Get game info from concept
        context['game'] = checklist.concept.games.first() if checklist.concept else None

        # Check if author has platinum for this game/concept
        context['author_has_platinum'] = False
        if checklist.concept and checklist.profile:
            from trophies.models import ProfileGame
            pg = ProfileGame.objects.filter(
                profile=checklist.profile,
                game__concept=checklist.concept
            ).order_by('-progress').first()
            if pg:
                context['author_has_platinum'] = pg.has_plat

        # Breadcrumbs
        breadcrumb = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games', 'url': reverse_lazy('games_list')},
        ]
        if context['game']:
            breadcrumb.append({
                'text': context['game'].title_name,
                'url': reverse_lazy('game_detail', kwargs={'np_communication_id': context['game'].np_communication_id})
            })
        breadcrumb.append({'text': checklist.title})
        context['breadcrumb'] = breadcrumb

        # Set background image from concept if available
        if checklist.concept and checklist.concept.bg_url:
            context['image_urls'] = {'bg_url': checklist.concept.bg_url}

        # Comment section context
        context['guidelines_agreed'] = profile.guidelines_agreed if profile else False

        # Get comment count for this checklist
        from trophies.models import Comment
        comment_count = Comment.objects.filter(
            concept=checklist.concept,
            checklist_id=checklist.id,
            is_deleted=False
        ).count()
        context['comment_count'] = comment_count

        return context


class ChecklistCreateView(LoginRequiredMixin, ProfileHotbarMixin, View):
    """
    Create a new checklist for a concept.

    Redirects to the edit page after creating a draft checklist.
    """
    login_url = reverse_lazy('account_login')

    def get(self, request, concept_id, np_communication_id):
        """Create a new draft checklist and redirect to edit."""
        from trophies.models import Game

        concept = get_object_or_404(Concept, id=concept_id)
        game = get_object_or_404(Game, np_communication_id=np_communication_id, concept=concept)
        profile = request.user.profile if hasattr(request.user, 'profile') else None

        if not profile:
            messages.error(request, "You need to link your PSN account first.")
            return redirect('link_psn')

        # Check if user can create checklists
        can_create, error = ChecklistService.can_create_checklist(profile)
        if not can_create:
            # If the error is about guidelines, redirect with hash to trigger modal
            if error == "You must agree to the community guidelines.":
                return redirect(f"{reverse('game_detail', kwargs={'np_communication_id': game.np_communication_id})}#show-guidelines")
            else:
                messages.error(request, error)
                return redirect('game_detail', np_communication_id=game.np_communication_id)

        # Create the checklist with the selected game
        checklist, error = ChecklistService.create_checklist(
            profile=profile,
            concept=concept,
            title=f"New Guide for {concept.unified_title}"
        )

        if error:
            messages.error(request, error)
            return redirect('game_detail', np_communication_id=game.np_communication_id)

        # Set the selected game (default to the game they came from)
        checklist.selected_game = game
        checklist.save(update_fields=['selected_game', 'updated_at'])

        messages.success(request, "Guide created! Start adding sections and items.")
        return redirect('checklist_edit', checklist_id=checklist.id)


class ChecklistEditView(LoginRequiredMixin, ProfileHotbarMixin, DetailView):
    """
    Edit a checklist (title, description, sections, items).

    Only the checklist author can access this view.
    Requires a linked PSN account.
    """
    model = Checklist
    template_name = 'trophies/checklist_edit.html'
    context_object_name = 'checklist'
    pk_url_kwarg = 'checklist_id'
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        """Require linked PSN account."""
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to create and edit checklists.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        """Return checklist with optimized prefetches."""
        return Checklist.objects.active().with_author_data().with_sections()

    def get_object(self, queryset=None):
        """Get checklist and verify ownership."""
        checklist = super().get_object(queryset)
        user = self.request.user
        profile = user.profile if hasattr(user, 'profile') else None

        # Only author can edit
        if not profile or checklist.profile != profile:
            raise Http404("Checklist not found")

        return checklist

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        checklist = self.object

        # Get game info from concept
        context['game'] = checklist.concept.games.first() if checklist.concept else None

        # Get all games for concept (for trophy selection)
        context['concept_games'] = []
        if checklist.concept:
            context['concept_games'] = checklist.concept.games.all().order_by('title_name')

        # Breadcrumbs
        breadcrumb = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games', 'url': reverse_lazy('games_list')},
        ]
        if context['game']:
            breadcrumb.append({
                'text': context['game'].title_name,
                'url': reverse_lazy('game_detail', kwargs={'np_communication_id': context['game'].np_communication_id})
            })
        breadcrumb.append({
            'text': checklist.title,
            'url': reverse_lazy('checklist_detail', kwargs={'checklist_id': checklist.id})
        })
        breadcrumb.append({'text': 'Edit'})
        context['breadcrumb'] = breadcrumb

        # Set background image from concept if available
        if checklist.concept and checklist.concept.bg_url:
            context['image_urls'] = {'bg_url': checklist.concept.bg_url}

        return context


class MyChecklistsView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    Display user's checklists: drafts, published, and in-progress.

    Shows three tabs:
    1. My Drafts - Checklists user is working on
    2. My Published - Checklists user has published
    3. In Progress - Other users' checklists the user is tracking

    Requires a linked PSN account.
    """
    template_name = 'trophies/my_checklists.html'
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        """Require linked PSN account."""
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to use checklists.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        profile = user.profile if hasattr(user, 'profile') else None

        if not profile:
            context['drafts'] = []
            context['published'] = []
            context['in_progress'] = []
            return context

        # Get user's drafts
        context['drafts'] = ChecklistService.get_user_drafts(profile)

        # Get user's published checklists
        context['published'] = ChecklistService.get_user_published(profile)

        # Get checklists user is tracking (in progress)
        context['in_progress'] = ChecklistService.get_user_checklists_in_progress(profile)

        # Breadcrumbs
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Checklists'},
        ]

        # Active tab
        context['active_tab'] = self.request.GET.get('tab', 'drafts')

        return context


class MyShareablesView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    My Shareables hub - centralized page for all shareable content.

    Allows users to generate share images for any platinum trophy they've earned,
    not just those that triggered a notification. Designed for extensibility to
    support future shareable types (trophy cabinet, calendar, etc.).

    Shows platinum trophies grouped by year with "Share" buttons.
    Requires a linked PSN account.
    """
    template_name = 'shareables/my_shareables.html'
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        """Require linked PSN account."""
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to create shareables.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        profile = user.profile if hasattr(user, 'profile') else None

        if not profile:
            context['platinums_by_year'] = {}
            context['total_platinums'] = 0
            return context

        # Get user's platinum trophies (including shovelware - filtered client-side)
        earned_platinums = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            trophy__trophy_type='platinum',
        ).select_related(
            'trophy__game',
            'trophy__game__concept'
        ).order_by('-earned_date_time')

        # Calculate platinum number for each trophy (for milestone display)
        # We need to count platinums earned up to each one's date
        platinum_list = list(earned_platinums)
        total_count = len(platinum_list)

        # Since list is ordered by -earned_date_time (newest first),
        # the newest plat is #total_count, oldest is #1
        for idx, et in enumerate(platinum_list):
            # Platinum number = total - index (since newest is first)
            et.platinum_number = total_count - idx
            et.is_milestone = et.platinum_number % 10 == 0 and et.platinum_number > 0
            et.is_shovelware = et.trophy.game.is_shovelware

        # Count shovelware for filter toggle
        shovelware_count = sum(1 for et in platinum_list if et.trophy.game.is_shovelware)

        # Group by year for organization
        platinums_by_year = {}
        for et in platinum_list:
            year = et.earned_date_time.year if et.earned_date_time else 'Unknown'
            if year not in platinums_by_year:
                platinums_by_year[year] = []
            platinums_by_year[year].append(et)

        # Sort years descending (most recent first), with 'Unknown' at the end
        sorted_years = sorted(
            [y for y in platinums_by_year.keys() if y != 'Unknown'],
            reverse=True
        )
        if 'Unknown' in platinums_by_year:
            sorted_years.append('Unknown')

        context['platinums_by_year'] = {year: platinums_by_year[year] for year in sorted_years}
        context['total_platinums'] = earned_platinums.count()
        context['shovelware_count'] = shovelware_count

        # Active tab (for future extensibility)
        context['active_tab'] = self.request.GET.get('tab', 'platinum_images')

        # Add available themes for color grid modal
        # Include game art themes since we have game context in share cards
        from trophies.themes import get_available_themes_for_grid
        context['available_themes'] = get_available_themes_for_grid(include_game_art=True)

        # Breadcrumbs
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Shareables'},
        ]

        return context