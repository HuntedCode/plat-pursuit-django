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
from trophies.mixins import ProfileHotbarMixin
from .models import Game, Trophy, Profile, EarnedTrophy, ProfileGame, TrophyGroup, UserTrophySelection, Badge, UserBadge, UserBadgeProgress, Concept, FeaturedGuide, Stage
from .forms import GameSearchForm, TrophySearchForm, ProfileSearchForm, ProfileGamesForm, ProfileTrophiesForm, ProfileBadgesForm, UserConceptRatingForm, BadgeSearchForm, GuideSearchForm, LinkPSNForm, GameDetailForm, BadgeCreationForm
from .utils import redis_client, MODERN_PLATFORMS, ALL_PLATFORMS

logger = logging.getLogger("psn_api")
    
class GamesListView(ProfileHotbarMixin, ListView):
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
                qs = qs.filter(Q(title_name__icontains=query))
            if platforms:
                platform_filter = Q()
                for plat in platforms:
                    platform_filter |= Q(title_platform__contains=plat)
                qs = qs.filter(platform_filter)
            if regions:
                region_filter = Q()
                for r in regions:
                    if r == 'global':
                        region_filter |= Q(is_regional=False)
                    else:
                        region_filter |= Q(is_regional=True, region__contains=r)
                qs = qs.filter(region_filter)
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

class GameDetailView(ProfileHotbarMixin, DetailView):
    model = Game
    template_name = 'trophies/game_detail.html'
    slug_field = 'np_communication_id'
    slug_url_kwarg = 'np_communication_id'
    context_object_name = 'game'

    def get_queryset(self):
        return super().get_queryset()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        game: Game = self.object
        user = self.request.user
        psn_username = self.kwargs.get('psn_username')
        today = date.today().isoformat()
        now_utc = timezone.now()
        images_cache_key = f"game:imageurls:{game.np_communication_id}"
        images_timeout = 604800
        stats_cache_key = f"game:stats:{game.np_communication_id}:{today}:{now_utc.hour:02d}"
        stats_timeout = 3600
        trophy_groups_cache_key = f"game:trophygroups:{game.np_communication_id}"
        trophy_groups_timeout = 604800

        if psn_username:
            try:
                target_profile = Profile.objects.get(psn_username__iexact=psn_username)
            except Profile.DoesNotExist:
                messages.error(self.request, "Profile not found.")
        elif user.is_authenticated and hasattr(user, 'profile') and user.profile and user.profile.is_linked:
            target_profile = user.profile
        else:
            target_profile = None

        logger.info(f"Target Profile: {target_profile} | Profile Username: {psn_username}")

        profile_progress = None
        profile_trophy_totals = {}
        profile_earned = {}
        profile_group_totals = {}
        milestones = [{'label': 'First Trophy'}, {'label': '50% Trophy'}, {'label': 'Platinum Trophy'}, {'label': '100% Trophy'}]

        has_trophies = Trophy.objects.filter(game=game).exists()

        if target_profile:
            try:
                profile_game = ProfileGame.objects.get(profile=target_profile, game=game)
                profile_progress = {
                    'progress': profile_game.progress,
                    'play_count': profile_game.play_count,
                    'play_duration': profile_game.play_duration,
                    'last_played': profile_game.last_played_date_time
                }

                if has_trophies:
                    earned_qs = EarnedTrophy.objects.filter(profile=target_profile, trophy__game=game).order_by('trophy__trophy_id')
                    profile_earned = {
                        e.trophy.trophy_id: {
                            'earned': e.earned,
                            'progress': e.progress,
                            'progress_rate': e.progress_rate,
                            'progressed_date_time': e.progressed_date_time,
                            'earned_date_time': e.earned_date_time
                        } for e in earned_qs
                    }

                    ordered_earned_qs = earned_qs.filter(earned=True).order_by(F('earned_date_time').asc(nulls_last=True))

                    profile_trophy_totals = {
                        'bronze': ordered_earned_qs.filter(trophy__trophy_type='bronze').count() or 0,
                        'silver': ordered_earned_qs.filter(trophy__trophy_type='silver').count() or 0,
                        'gold': ordered_earned_qs.filter(trophy__trophy_type='gold').count() or 0,
                        'platinum': ordered_earned_qs.filter(trophy__trophy_type='platinum').count() or 0,
                    }
                    
                    for e in ordered_earned_qs:
                        group_id = e.trophy.trophy_group_id or 'default'
                        trophy_type = e.trophy.trophy_type
                        if group_id not in profile_group_totals:
                            profile_group_totals[group_id] = {'bronze': 0, 'silver': 0, 'gold': 0, 'platinum': 0}
                        profile_group_totals[group_id][trophy_type] += 1

                    milestones = []
                    earned_list = list(ordered_earned_qs)
                    total_trophies = len(earned_qs)
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
                    
                    mid_idx = math.ceil((total_trophies - 1) * 0.5)
                    if len(earned_list) >= mid_idx:
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

                    if profile_progress['progress'] == 100:
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
            except ProfileGame.DoesNotExist:
                pass
        
        try:
            cached_images = cache.get(images_cache_key)
            if cached_images:
                image_urls = json.loads(cached_images)
            else:
                bg_url = None
                screenshot_urls = []
                content_rating_url = None
                if game.concept: 
                    if game.concept.media:
                        for img in game.concept.media:
                            if img.get('type') == 'SCREENSHOT':
                                screenshot_urls.append(img.get('url'))
                        
                        if len(screenshot_urls) < 1:
                            for img in game.concept.media:
                                img_type = img.get('type')
                                if img_type == 'GAMEHUB_COVER_ART' or img_type == 'LOGO' or img_type == 'MASTER':
                                    screenshot_urls.append(img.get('url'))

                    if game.concept.content_rating:
                        content_rating_url = game.concept.content_rating.get('url')
                    
                    image_urls = {
                        'bg_url': game.concept.bg_url,
                        'screenshot_urls': screenshot_urls,
                        'content_rating_url': content_rating_url
                    }
                    cache.set(images_cache_key, json.dumps(image_urls), timeout=images_timeout)
                else:
                    image_urls = {}
        except Exception as e:
            logger.error(f"Game images cache failed for {game.np_communication_id}: {e}")
            image_urls = {}

        try:
            cached_stats = cache.get(stats_cache_key)
            if cached_stats:
                stats = json.loads(cached_stats)
            else:
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
                    'completes': ProfileGame.objects.filter(
                        game=game,
                        progress=100
                    ).count(),
                    'avg_progress': ProfileGame.objects.filter(game=game).aggregate(avg=Avg('progress'))['avg'] or 0.0
                }
                cache.set(stats_cache_key, json.dumps(stats), timeout=stats_timeout)
        except Exception as e:
            logger.error(f"Game stats cache failed for {game.np_communication_id}: {e}")
            stats = {}
        
        full_trophies = []
        trophy_groups = []
        grouped_trophies = {}

        form = GameDetailForm(self.request.GET)
        context['form'] = form
        
        if has_trophies:
            try:
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
            
            grouped_trophies = {}
            for trophy in full_trophies:
                group_id = trophy.get('trophy_group_id', 'default')
                if group_id not in grouped_trophies:
                    grouped_trophies[group_id] = []
                grouped_trophies[group_id].append(trophy)
            
            sorted_groups = sorted(grouped_trophies.keys(), key=lambda x: (x != 'default', x))
        else:
            context['trophies_syncing'] = True

        if game.concept:
            averages_cache_key = f"concept:averages:{game.concept.concept_id}:{today}"
            cached_averages = cache.get(averages_cache_key)
            if cached_averages:
                averages = json.loads(cached_averages)
            else:
                averages = game.concept.get_community_averages()
                if averages:
                    cache.set(averages_cache_key, json.dumps(averages), timeout=stats_timeout)
            context['community_averages'] = averages

            badges = Badge.objects.filter(concepts=game.concept, tier=1).order_by('display_series')
            context['badges'] = badges

            other_versions_qs = game.concept.games.exclude(pk=game.pk)
            platform_order = {plat: idx for idx, plat in enumerate(ALL_PLATFORMS)}
            other_versions_qs = other_versions_qs.annotate(
                platform_order=Case(*[When(title_platform__contains=plat, then=Value(idx)) for plat, idx in platform_order.items()], default=999, output_field=IntegerField())
            ).order_by('platform_order', 'title_name')
            other_versions = list(other_versions_qs)
            context['other_versions'] = other_versions

        
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') and user.profile and user.profile.is_linked else None
        if profile and game.concept:
            has_platinum = game.concept.has_user_earned_platinum(profile)
            context['has_platinum'] = has_platinum
            if has_platinum:
                user_rating = game.concept.user_ratings.filter(profile=profile).first()
                context['rating_form'] = UserConceptRatingForm(instance=user_rating)

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games', 'url': reverse_lazy('games_list')},
            {'text': f"{game.title_name}"}
        ]
        context['profile'] = target_profile
        context['profile_progress'] = profile_progress
        context['profile_earned'] = profile_earned
        context['profile_trophy_totals'] = profile_trophy_totals
        context['profile_group_totals'] = profile_group_totals
        context['game_stats'] = stats
        context['grouped_trophies'] = {gid: grouped_trophies[gid] for gid in sorted_groups} if has_trophies else {}
        context['trophy_groups'] = trophy_groups
        context['image_urls'] = image_urls
        context['milestones'] = milestones
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
                messages.success(request, 'Your rating has been submitted!')
            else:
                messages.error(request, "Invalid form submission.")

        return HttpResponseRedirect(request.path)
    
class ProfileDetailView(ProfileHotbarMixin, DetailView):
    model = Profile
    template_name = 'trophies/profile_detail.html'
    slug_field = 'psn_username'
    slug_url_kwarg = 'psn_username'
    context_object_name = 'profile'

    def get_object(self, queryset=None):
        psn_username = self.kwargs[self.slug_url_kwarg].lower()
        queryset = queryset or self.get_queryset()
        return get_object_or_404(queryset, **{self.slug_field: psn_username})
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile: Profile = self.object
        tab = self.request.GET.get('tab', 'games')
        per_page = 50
        page_number = self.request.GET.get('page', 1)

        earned_trophies_prefetch = Prefetch(
            'earned_trophy_entries',
            queryset=EarnedTrophy.objects.filter(earned=True).select_related('trophy', 'trophy__game'),
            to_attr='earned_trophies'
        )
        profile = Profile.objects.prefetch_related(earned_trophies_prefetch).get(id=profile.id)

        # Header
        header_stats = {}

        header_stats['total_games'] = profile.total_games
        header_stats['total_earned_trophies'] = profile.total_trophies
        header_stats['total_unearned_trophies'] = profile.total_unearned
        header_stats['total_completions'] = profile.total_completes
        header_stats['average_completion'] = profile.avg_progress
        header_stats['recent_platinum'] = {
            'trophy': profile.recent_plat.trophy,
            'game': profile.recent_plat.trophy.game,
            'earned_date': profile.recent_plat.earned_date_time,
        } if profile.recent_plat else None
        header_stats['rarest_platinum'] = {
            'trophy': profile.rarest_plat.trophy,
            'game': profile.rarest_plat.trophy.game,
            'earned_date': profile.rarest_plat.earned_date_time,
        } if profile.rarest_plat else None

        # Trophy Case Selections
        trophy_case = list(UserTrophySelection.objects.filter(profile=profile).order_by('-earned_trophy__earned_date_time'))
        max_trophies = 10
        trophy_case = trophy_case + [None] * (max_trophies - len(trophy_case))

        if tab == 'games':
            form = ProfileGamesForm(self.request.GET)
            if form.is_valid():
                query = form.cleaned_data.get('query')
                platforms = form.cleaned_data.get('platform')
                plat_status = form.cleaned_data.get('plat_status')
                sort_val = form.cleaned_data.get('sort')

                games_qs = profile.played_games.all().select_related('game').annotate(
                    annotated_total_trophies=F('earned_trophies_count') + F('unearned_trophies_count')  # Computed from denorm
                )

                if query:
                    games_qs = games_qs.filter(Q(game__title_name__icontains=query))
                if platforms:
                    platform_filter = Q()
                    for plat in platforms:
                        platform_filter |= Q(game__title_platform__contains=plat)
                    games_qs = games_qs.filter(platform_filter)
                    context['selected_platforms'] = platforms

                if plat_status:
                    if plat_status in ['plats', 'plats_100s', 'plats_no_100s']:
                        games_qs = games_qs.filter(has_plat=True)
                    elif plat_status in ['no_plats', 'no_plats_100s']:
                        games_qs = games_qs.filter(has_plat=False)
                    if plat_status in ['100s', 'plats_100s', 'no_plats_100s']:
                        games_qs = games_qs.filter(progress=100)
                    elif plat_status in ['no_100s']:
                        games_qs = games_qs.exclude(progress=100)
                    if plat_status == 'plats_no_100s':
                        games_qs = games_qs.exclude(progress=100)

                order = ['-last_updated_datetime']
                if sort_val == 'oldest':
                    order = ['last_updated_datetime']
                elif sort_val == 'alpha':
                    order = ['game__title_name',]
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

                games_paginator = Paginator(games_qs, per_page)
                if int(page_number) > games_paginator.num_pages:
                    game_page_obj = []
                else:
                    game_page_obj = games_paginator.get_page(page_number)
                context['profile_games'] = game_page_obj
                context['trophy_log'] = []
        
        elif tab == 'trophies':
            form = ProfileTrophiesForm(self.request.GET)
            if form.is_valid():
                query = form.cleaned_data.get('query')
                platforms = form.cleaned_data.get('platform')
                type = form.cleaned_data.get('type')

                trophies_qs = profile.earned_trophy_entries.filter(earned=True,).select_related('trophy', 'trophy__game').order_by(F('earned_date_time').desc(nulls_last=True))

                if query:
                    trophies_qs = trophies_qs.filter(Q(trophy__trophy_name__icontains=query) | Q(trophy__game__title_name__icontains=query))
                if platforms:
                    platform_filter = Q()
                    for plat in platforms:
                        platform_filter |= Q(trophy__game__title_platform__contains=plat)
                    trophies_qs = trophies_qs.filter(platform_filter)
                    context['selected_platforms'] = platforms
                if type:
                    trophies_qs = trophies_qs.filter(trophy__trophy_type=type)

                trophy_paginator = Paginator(trophies_qs, per_page)
                if int(page_number) > trophy_paginator.num_pages:
                    trophy_page_obj = []
                else:
                    trophy_page_obj = trophy_paginator.get_page(page_number)
                context['trophy_log'] = trophy_page_obj
                context['profile_games'] = []
        
        elif tab == 'badges':
            form = ProfileBadgesForm(self.request.GET)
            if form.is_valid():
                sort_val = form.cleaned_data.get('sort')

                earned_badges_qs = UserBadge.objects.filter(profile=profile).select_related('badge').values('badge__series_slug').annotate(max_tier=Max('badge__tier')).distinct()

                grouped_earned = []
                for entry in earned_badges_qs:
                    series_slug = entry['badge__series_slug']
                    max_tier = entry['max_tier']
                    highest_badge = Badge.objects.filter(series_slug=series_slug, tier=max_tier).first()
                    if highest_badge:
                        next_tier = max_tier + 1
                        next_badge = Badge.objects.filter(series_slug=series_slug, tier=next_tier).first()
                        is_maxed = next_badge is None
                        if is_maxed:
                            next_badge = highest_badge
                            
                        progress_entry = UserBadgeProgress.objects.filter(profile=profile, badge=next_badge).first()
                        if progress_entry and next_badge.required_stages > 0:
                            progress_percentage = (progress_entry.completed_concepts / next_badge.required_stages) * 100
                        else:
                            progress_percentage = 0
                        if is_maxed:
                            progress_percentage = 100
                        
                        grouped_earned.append({
                            'highest_badge': highest_badge,
                            'progress': progress_entry,
                            'percentage': progress_percentage,
                        })
                
                if sort_val == 'name':
                    grouped_earned.sort(key=lambda d: d['highest_badge'].effective_display_title)
                elif sort_val == 'tier':
                    grouped_earned.sort(key=lambda d: (d['max_tier'], d['highest_badge'].effective_display_title))
                elif sort_val == 'tier_desc':
                    grouped_earned.sort(key=lambda d: (-d['max_tier'], d['highest_badge'].effective_display_title))
                else:
                    grouped_earned.sort(key=lambda d: d['highest_badge'].effective_display_series)

                context['grouped_earned_badges'] = grouped_earned
  
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Profiles', 'url': reverse_lazy('profiles_list')},
            {'text': f"{profile.display_psn_username}"}
        ]
        context['form'] = form
        context['header_stats'] = header_stats
        context['trophy_case'] = trophy_case
        context['trophy_case_count'] = len(trophy_case)
        context['current_tab'] = tab
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
    model = Badge
    template_name = 'trophies/badge_list.html'
    context_object_name = 'display_data'
    paginate_by = None

    def get_queryset(self):
        qs = super().get_queryset().prefetch_related('concepts__games')
        form = BadgeSearchForm(self.request.GET)

        if form.is_valid():
            series_slug = slugify(form.cleaned_data.get('series_slug'))
            if series_slug:
                qs = qs.filter(series_slug__icontains=series_slug)
        return qs
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        badges = context['object_list']

        grouped_badges = defaultdict(list)
        for badge in badges:
            if badge.effective_user_title:
                grouped_badges[badge.series_slug].append(badge)

        display_data = []
        user = self.request.user
        if user.is_authenticated and hasattr(user, 'profile'):
            profile = user.profile
            user_earned = UserBadge.objects.filter(profile=profile).values('badge__series_slug').annotate(max_tier=Max('badge__tier'))
            earned_dict = {e['badge__series_slug']: e['max_tier'] for e in user_earned}

            all_badges_ids = [b.id for group in grouped_badges.values() for b in group]
            progress_qs = UserBadgeProgress.objects.filter(profile=profile, badge__id__in=all_badges_ids)
            progress_dict = {p.badge.id: p for p in progress_qs}

            for slug, group in grouped_badges.items():
                sorted_group = sorted(group, key=lambda b: b.tier)
                if not sorted_group:
                    continue

                tier1_badge = next((b for b in sorted_group if b.tier == 1), None)
                tier1_earned_count = tier1_badge.earned_count if tier1_badge else 0

                all_games = set()
                for badge in sorted_group:
                    for concept in badge.concepts.all():
                        for game in concept.games.all():
                            all_games.add(game)
                total_games = len(all_games)
                trophy_types = {
                    'bronze': sum(game.defined_trophies['bronze'] for game in all_games),
                    'silver': sum(game.defined_trophies['silver'] for game in all_games),
                    'gold': sum(game.defined_trophies['gold'] for game in all_games),
                    'platinum': sum(game.defined_trophies['platinum'] for game in all_games),
                }

                highest_tier = earned_dict.get(slug, 0)
                display_badge = next((b for b in sorted_group if b.tier == highest_tier), None) if highest_tier > 0 else tier1_badge                
                if not display_badge:
                    continue

                is_earned = highest_tier > 0
                next_badge = next((b for b in sorted_group if b.tier > highest_tier), None)
                progress_badge = next_badge if next_badge else display_badge

                progress = progress_dict.get(progress_badge.id) if progress_badge else None
                required_stages = progress_badge.required_stages
                completed_concepts = 0
                progress_percentage = 0

                if progress and progress_badge.badge_type == 'series':
                    completed_concepts = progress.completed_concepts
                    progress_percentage = (completed_concepts / required_stages) * 100 if required_stages > 0 else 0
                else:
                    completed_concepts = 0
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
        else:
            for slug, group in grouped_badges.items():
                sorted_group = sorted(group, key=lambda b: b.tier)
                tier1 = next((b for b in sorted_group if b.tier == 1), None)
                if tier1:
                    tier1_earned_count = tier1.earned_count
                    all_games = set()
                    for badge in sorted_group:
                        for concept in badge.concepts.all():
                            for game in concept.games.all():
                                all_games.add(game)
                    total_games = len(all_games)
                    trophy_types = {
                        'bronze': sum(game.defined_trophies['bronze'] for game in all_games),
                        'silver': sum(game.defined_trophies['silver'] for game in all_games),
                        'gold': sum(game.defined_trophies['gold'] for game in all_games),
                        'platinum': sum(game.defined_trophies['platinum'] for game in all_games),
                    }
                    display_data.append({
                        'badge': tier1,
                        'tier1_earned_count': tier1_earned_count,
                        'completed_concepts': 0,
                        'required_stages': tier1.required_stages,
                        'progress_percentage': 0,
                        'trophy_types': trophy_types,
                        'total_games': total_games,
                        'is_earned': False
                    })

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

        paginate_by = 25
        paginator = Paginator(display_data, paginate_by)
        page_number = self.request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context['display_data'] = page_obj
        context['page_obj'] = page_obj
        context['paginator'] = paginator
        context['is_paginated'] = page_obj.has_other_pages()

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges'},
        ]

        context['form'] = BadgeSearchForm(self.request.GET)
        context['selected_tiers'] = self.request.GET.getlist('tier')
        return context

class BadgeDetailView(ProfileHotbarMixin, DetailView):
    model = Badge
    template_name = 'trophies/badge_detail.html'
    slug_field = 'series_slug'
    slug_url_kwarg = 'series_slug'
    context_object_name = 'series_badges'

    def get_object(self, queryset=None):
        series_slug = self.kwargs[self.slug_url_kwarg]
        return Badge.objects.filter(series_slug=series_slug).order_by('tier')
    
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
            context['badge'] = series_badges.filter(tier=1).first()

        stages = Stage.objects.filter(series_slug=badge.series_slug).order_by('stage_number').prefetch_related(
            Prefetch('concepts__games', queryset=Game.objects.all().order_by('title_name'))
        )
        
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

            structured_data.append({
                'stage': stage,
                'games': [{'game': game, 'profile_game': profile_games.get(game, None)} for game in games]
            })

        print(len(structured_data))
        context['stage_data'] = structured_data
        context['is_earned'] = is_earned

        context['image_urls'] = {'bg_url': badge.most_recent_concept.bg_url, 'recent_concept_icon_url': badge.most_recent_concept.concept_icon_url}
        context['recent_concept_name'] = badge.most_recent_concept.unified_title

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': context['badge'].effective_display_series},
        ]

        return context

class GuideListView(ProfileHotbarMixin, ListView):
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
    @method_decorator(ratelimit(key='user', rate='60/m', method='GET'))
    def get(self, request):
        profile = request.user.profile
        seconds_to_next_sync = profile.get_seconds_to_next_sync()
        print(seconds_to_next_sync)
        data = {
            'sync_status': profile.sync_status,
            'sync_progress': profile.sync_progress_value,
            'sync_target': profile.sync_progress_target,
            'sync_percentage': profile.sync_progress_value / profile.sync_progress_target * 100 if profile.sync_progress_target > 0 else 0,
            'seconds_to_next_sync': seconds_to_next_sync,
        }
        return JsonResponse(data)

class TriggerSyncView(LoginRequiredMixin, View):
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