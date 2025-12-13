import json
import logging
import math
from collections import defaultdict
from datetime import timedelta, date
from django.core.cache import cache
from django.core.paginator import Paginator
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render, get_object_or_404
from django.http import Http404, StreamingHttpResponse, JsonResponse, HttpResponseRedirect
from django.views.generic import ListView, View, DetailView
from django.db.models import Q, F, Prefetch, OuterRef, Subquery, Value, IntegerField, FloatField, Count, Avg, Max, Exists
from django.db.models.functions import Coalesce
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from random import choice
from .models import Game, Trophy, Profile, EarnedTrophy, ProfileGame, TrophyGroup, UserTrophySelection, Badge, UserBadge, UserBadgeProgress, Concept, FeaturedGuide
from .forms import GameSearchForm, TrophySearchForm, ProfileSearchForm, ProfileGamesForm, ProfileTrophiesForm, ProfileBadgesForm, UserConceptRatingForm, BadgeSearchForm, GuideSearchForm
from .utils import redis_client, MODERN_PLATFORMS

logger = logging.getLogger("psn_api")

# Create your views here.
def monitoring_dashboard(request):
    return render(request, 'monitoring.html')

def token_stats_sse(request):
    def event_stream():
        pubsub = redis_client.pubsub()
        pubsub.subscribe("token_keeper_stats")
        try:
            for message in pubsub.listen():
                if message['type'] == 'message':
                    try:
                        stats = json.loads(message['data'])
                        redis_client.set("token_keeper_latest_stats", json.dumps(stats), ex=60)
                        yield f"data: {json.dumps(stats)}\n\n"
                    except json.JSONDecodeError as e:
                        logger.error(f"Error decoding SSE stats: {e}")
                        yield f"data: {{'error': 'Invalid stats data'}}\n\n"
        except Exception as e:
            logger.error(f"Error in SSE stream: {e}")
            yield f"data: {{'error': '{str(e)}'}}\n\n"
        finally:
            pubsub.unsubscribe()
    
    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    return response

def token_stats(request):
    try:
        stats_json = redis_client.get("token_keeper_latest_stats")
        stats = json.loads(stats_json) if stats_json else {}
        return JsonResponse(stats)
    except Exception as e:
        logger.error(f"Error fetching token stats: {e}")
        return JsonResponse({'error': str(e)}, status=500)
    
class GamesListView(ListView):
    model = Game
    template_name = 'trophies/game_list.html'
    paginate_by = 50

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
        context['selected_platforms'] = self.request.GET.getlist('platform')
        context['selected_regions'] = self.request.GET.getlist('regions')
        context['view_type'] = self.request.GET.get('view', 'grid')
        context['show_only_platinum'] = self.request.GET.get('show_only_platinum', '')
        context['filter_shovelware'] = self.request.GET.get('filter_shovelware', '')
        return context
    
    def get_template_names(self):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            view_type = self.request.GET.get('view', 'grid')
            if view_type == 'list':
                return ['trophies/partials/game_list/game_list_items.html']
            else:
                return ['trophies/partials/game_list/game_cards.html']
        return super().get_template_names()
    
class TrophiesListView(ListView):
    model = Trophy
    template_name = 'trophies/trophy_list.html'
    paginate_by = 50

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
        context['selected_platforms'] = self.request.GET.getlist('platform')
        context['selected_types'] = self.request.GET.getlist('type')
        context['selected_regions'] = self.request.GET.getlist('region')
        context['selected_psn_rarity'] = self.request.GET.getlist('psn_rarity')
        context['show_only_platinum'] = self.request.GET.get('show_only_platinum', '')
        context['filter_shovelware'] = self.request.GET.get('filter_shovelware', '')
        return context
    
    def get_template_names(self):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return ['trophies/partials/trophy_list/trophy_list_items.html']
        return super().get_template_names()
    
class ProfilesListView(ListView):
    model = Profile
    template_name = 'trophies/profile_list.html'
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset()
        form = ProfileSearchForm(self.request.GET)
        order = ['psn_username']

        if form.is_valid():
            query = form.cleaned_data.get('query')
            country = form.cleaned_data.get('country')
            filter_shovelware = form.cleaned_data.get('filter_shovelware')
            sort_val = form.cleaned_data.get('sort')

            if query:
                qs = qs.filter(Q(psn_username__icontains=query))
            if country:
                qs = qs.filter(country_code=country)
            
            earned_qs = EarnedTrophy.objects.filter(profile=OuterRef('pk'), earned=True)
            plat_qs = earned_qs.filter(trophy__trophy_type='platinum')
            game_qs = ProfileGame.objects.filter(profile=OuterRef('pk'))
            complete_qs = game_qs.filter(progress=100)
            if filter_shovelware:
                earned_qs = earned_qs.exclude(trophy__game__is_shovelware=True)
                plat_qs = plat_qs.exclude(trophy__game__is_shovelware=True)
                game_qs = game_qs.exclude(game__is_shovelware=True)
                complete_qs = complete_qs.exclude(game__is_shovelware=True)
            
            qs = qs.annotate(
                total_trophies=Coalesce(Subquery(earned_qs.values('profile').annotate(count=Count('id')).values('count')[:1]), 0),
                total_plats=Coalesce(Subquery(plat_qs.values('profile').annotate(count=Count('id')).values('count')[:1]), 0),
                total_games=Coalesce(Subquery(game_qs.values('profile').annotate(count=Count('id')).values('count')[:1]), 0),
                total_completes=Coalesce(Subquery(complete_qs.values('profile').annotate(count=Count('id')).values('count')[:1]), 0),
                avg_progress=Coalesce(Subquery(game_qs.values('profile').annotate(avg=Avg('progress')).values('avg')[:1]), 0.0),
            )

            recent_plat_qs = EarnedTrophy.objects.filter(earned=True, trophy__trophy_type='platinum')
            if filter_shovelware:
                recent_plat_qs = recent_plat_qs.exclude(trophy__game__is_shovelware=True)
            recent_plat_qs = recent_plat_qs.order_by(F('earned_date_time').desc(nulls_last=True))
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
        context['filter_shovelware'] = self.request.GET.get('filter_shovelware', '')
        return context
    
    def get_template_names(self):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return ['trophies/partials/profile_list/profile_cards.html']
        return super().get_template_names()
    
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

class GameDetailView(DetailView):
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
        images_cache_key = f"game:imageurls:{game.np_communication_id}"
        images_timeout = 604800
        stats_cache_key = f"game:stats:{game.np_communication_id}:{today}"
        stats_timeout = 86400
        trophy_cache_key = f"game:trophies:{game.np_communication_id}"
        trophy_timeout = 604800
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
        if target_profile:
            try:
                profile_game = ProfileGame.objects.get(profile=target_profile, game=game)
                profile_progress = {
                    'progress': profile_game.progress,
                    'play_count': profile_game.play_count,
                    'play_duration': profile_game.play_duration,
                    'last_played': profile_game.last_played_date_time
                }

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
        
        try:
            cached_trophies = cache.get(trophy_cache_key)
            if cached_trophies:
                full_trophies = json.loads(cached_trophies)
            else:
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
                cache.set(trophy_cache_key, json.dumps(full_trophies), timeout=trophy_timeout)
        except Exception as e:
            logger.error(f"Game trophies cache failed for {game.np_communication_id}: {e}")
            full_trophies = []

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
        
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') and user.profile and user.profile.is_linked else None
        if profile and game.concept:
            has_platinum = game.concept.has_user_earned_platinum(profile)
            context['has_platinum'] = has_platinum
            if has_platinum:
                user_rating = game.concept.user_ratings.filter(profile=profile).first()
                context['form'] = UserConceptRatingForm(instance=user_rating)

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
        context['grouped_trophies'] = {gid: grouped_trophies[gid] for gid in sorted_groups}
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
    
class ProfileDetailView(DetailView):
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
        user = self.request.user

        tab = self.request.GET.get('tab', 'games')

        # Header
        header_stats = {}

        header_stats['total_games'] = profile.played_games.count()

        earned_trophies_qs = profile.earned_trophy_entries.all()
        header_stats['total_earned_trophies'] = earned_trophies_qs.filter(earned=True).count()
        header_stats['total_unearned_trophies'] = earned_trophies_qs.filter(earned=False).count()

        profile_games_qs = profile.played_games.all()
        header_stats['total_completions'] = profile_games_qs.filter(progress=100).count()
        header_stats['average_completion'] = profile_games_qs.aggregate(avg_progress=Avg('progress'))['avg_progress'] or 0

        recent_platinum = profile.earned_trophy_entries.filter(earned=True, trophy__trophy_type='platinum').select_related('trophy', 'trophy__game').order_by(F('earned_date_time').desc(nulls_last=True)).first()
        header_stats['recent_platinum'] = {
            'trophy': recent_platinum.trophy,
            'game': recent_platinum.trophy.game,
            'earned_date': recent_platinum.earned_date_time,
        } if recent_platinum else None

        rarest_platinum = profile.earned_trophy_entries.filter(earned=True, trophy__trophy_type='platinum').select_related('trophy', 'trophy__game').order_by('trophy__trophy_earn_rate').first()
        header_stats['rarest_platinum'] = {
            'trophy': rarest_platinum.trophy,
            'game': rarest_platinum.trophy.game,
            'earned_date': rarest_platinum.earned_date_time,
        } if rarest_platinum else None

        # Trophy Case Selections
        trophy_case = list(UserTrophySelection.objects.filter(profile=profile).order_by('-earned_trophy__earned_date_time'))

        # Games/Trophies List
        per_page = 50
        page_number = self.request.GET.get('page', 1)

        if tab == 'games':
            form = ProfileGamesForm(self.request.GET)
            if form.is_valid():
                query = form.cleaned_data.get('query')
                platforms = form.cleaned_data.get('platform')
                plat_status = form.cleaned_data.get('plat_status')
                sort_val = form.cleaned_data.get('sort')

                recent_trophy_subquery = Subquery(
                    EarnedTrophy.objects.filter(profile=profile, trophy__game=OuterRef('game'), earned=True).values('trophy__game').annotate(max_date=Max('earned_date_time')).values('max_date')[:1]
                )
                earned_count_subquery = Subquery(
                    EarnedTrophy.objects.filter(profile=profile, trophy__game=OuterRef('game'), earned=True).values('trophy__game').annotate(count=Count('id')).values('count')[:1]
                )
                unearned_count_subquery = Subquery(
                    EarnedTrophy.objects.filter(profile=profile, trophy__game=OuterRef('game'), earned=False).values('trophy__game').annotate(count=Count('id')).values('count')[:1]
                )
                games_qs = profile.played_games.all().select_related('game').annotate(
                    most_recent_trophy_date=Coalesce(recent_trophy_subquery, None),
                    earned_trophies_count=Coalesce(earned_count_subquery, Value(0, output_field=IntegerField())),
                    unearned_trophies_count=Coalesce(unearned_count_subquery, Value(0, output_field=IntegerField())),
                    total_trophies=F('earned_trophies_count') + F('unearned_trophies_count'),
                )

                plat_earned_subquery = Exists(
                    EarnedTrophy.objects.filter(profile=profile, trophy__game=OuterRef('game'), trophy__trophy_type='platinum', earned=True)
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
                    if plat_status == 'plats':
                        games_qs = games_qs.filter(plat_earned_subquery)
                    elif plat_status == 'no_plats':
                        games_qs = games_qs.exclude(plat_earned_subquery)
                    elif plat_status == '100s':
                        games_qs = games_qs.filter(progress=100)
                    elif plat_status == 'no_100s':
                        games_qs = games_qs.exclude(progress=100)
                    elif plat_status == 'plats_100s':
                        games_qs = games_qs.filter(Q(plat_earned_subquery) | Q(progress=100))
                    elif plat_status == 'no_plats_100s':
                        games_qs = games_qs.exclude(Q(plat_earned_subquery) | Q(progress=100))
                    elif plat_status == 'plats_no_100s':
                        games_qs = games_qs.filter(plat_earned_subquery)
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
                    order = ['-total_trophies', 'game__title_name']
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
                        if progress_entry and progress_entry.required_concepts > 0:
                            progress_percentage = (progress_entry.completed_concepts / progress_entry.required_concepts) * 100
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

        return context
    
    def get_template_names(self):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            tab = self.request.GET.get('tab', 'games')
            if tab == 'games':
                return ['trophies/partials/profile_detail/game_list_items.html']
            elif tab == 'trophies':
               return ['trophies/partials/profile_detail/trophy_list_items.html'] 
        return super().get_template_names()
    
class TrophyCaseView(ListView):
    model = EarnedTrophy
    template_name = 'trophies/trophy_case.html'
    context_object_name = 'platinums'
    paginate_by = 50

    def get_queryset(self):
        profile = get_object_or_404(Profile, psn_username=self.kwargs['psn_username'].lower())
        return EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='platinum').select_related('trophy', 'trophy__game').order_by(F('earned_date_time').desc(nulls_last=True))
    
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
        context['profile'] = profile
        context['selected_ids'] = selected_ids
        context['selected_count'] = len(selected_ids)
        context['toggle_selection_url'] = reverse('toggle-selection')
        return context
    
    def get_template_names(self):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return ['trophies/partials/trophy_case/trophy_case_items.html']
        return super().get_template_names()

class ToggleSelectionView(LoginRequiredMixin, View):
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
            
            selection, created = UserTrophySelection.objects.get_or_create(profile=profile, earned_trophy_id=earned_trophy_id)
            if not created:
                selection.delete()
                action = 'removed'
            else:
                action = 'added'
            return JsonResponse({'success': True, 'action': action})
        except EarnedTrophy.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Invalid earned_trophy_id'}, status=400)
        except Profile.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'No profile found'}, status=400)
        except Exception as e:
            logger.error(f"Selection toggle error: {e}")
            return JsonResponse({'success': False, 'error': 'Internal error'}, status=500)

class BadgeListView(ListView):
    model = Badge
    template_name = 'trophies/badge_list.html'
    context_object_name = 'display_badges'

    def get_queryset(self):
        qs = super().get_queryset().prefetch_related('concepts')
        form = BadgeSearchForm(self.request.GET)

        if form.is_valid():
            series_slug = form.cleaned_data.get('series_slug')
            if series_slug:
                qs = qs.filter(series_slug__icontains=series_slug)
        return qs
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        badges = context['object_list']

        grouped_badges = defaultdict(list)
        for badge in badges:
            grouped_badges[badge.series_slug].append(badge)
        
        display_data = []
        user = self.request.user
        if user.is_authenticated and hasattr(user, 'profile'):
            profile = user.profile
            user_earned = UserBadge.objects.filter(profile=profile).values('badge__series_slug').annotate(max_tier=Max('badge__tier'))
            earned_dict = {e['badge__series_slug']: e['max_tier'] for e in user_earned}

            all_badges_ids = [b.id for group in grouped_badges.values() for b in group]
            progress_qs = UserBadgeProgress.objects.filter(profile=profile, badge__id__in=all_badges_ids)
            progress_dict = {p.badge_id: p for p in progress_qs}

            for slug, group in grouped_badges.items():
                sorted_group = sorted(group, key=lambda b: b.tier)
                if not sorted_group:
                    continue

                tier1_badge = next((b for b in sorted_group if b.tier == 1), None)
                tier1_earned_count = tier1_badge.earned_count if tier1_badge else 0

                if slug in earned_dict:
                    highest_tier = earned_dict[slug]
                    next_badge = next((b for b in sorted_group if b.tier > highest_tier), None)
                    if next_badge:
                        display_badge = next_badge
                        is_maxed = False
                    else:
                        display_badge = sorted_group[-1]
                        is_maxed = True
                else:
                    display_badge = next((b for b in sorted_group if b.tier == 1), sorted_group[0])
                    is_maxed = False
                    
                if display_badge:
                    progress = progress_dict.get(display_badge.id)
                    if progress and badge.badge_type == 'series':
                        if is_maxed:
                            progress_percentage = 100
                        else:
                            progress_percentage = (progress.completed_concepts / progress.required_concepts * 100) if progress.required_concepts > 0 else 0
                    else:
                        progress_percentage = 0

                    display_data.append({
                        'badge': display_badge,
                        'tier1_earned_count': tier1_earned_count,
                        'completed_concepts': progress.completed_concepts if progress else 0,
                        'required_concepts': progress.required_concepts if progress else 0,
                        'progress_percentage': round(progress_percentage, 1),
                    })
        else:
            for slug, group in grouped_badges.items():
                sorted_group = sorted(group, key=lambda b: b.tier)
                tier1 = next((b for b in sorted_group if b.tier == 1), None)
                if tier1:
                    display_data.append({
                        'badge': tier1,
                        'tier1_earned_count': tier1_earned_count,
                        'completed_concepts': 0,
                        'required_concepts': 0,
                        'progress_percentage': 0,
                    })
        

        sort_val = self.request.GET.get('sort', 'tier')
        if sort_val == 'name':
            display_data.sort(key=lambda d: d['badge'].effective_display_title)
        elif sort_val == 'tier':
            display_data.sort(key=lambda d: (d['badge'].tier, d['badge'].effective_display_title))
        elif sort_val == 'tier_desc':
            display_data.sort(key=lambda d: (-d['badge'].tier, d['badge'].effective_display_title))
        elif sort_val == 'earned':
            display_data.sort(key=lambda d: (-d['tier1_earned_count'], d['badge'].effective_display_title))
        elif sort_val == 'earned_inv':
            display_data.sort(key=lambda d: (d['tier1_earned_count'], d['badge'].effective_display_title))
        else:
            display_data.sort(key=lambda d: d['badge'].effective_display_series)

        context['display_data'] = display_data
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges'},
        ]

        context['form'] = BadgeSearchForm(self.request.GET)
        context['selected_tiers'] = self.request.GET.getlist('tier')
        context['view_type'] = self.request.GET.get('view', 'grid')
        return context
    
    def get_template_names(self):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            view_type = self.request.GET.get('view', 'grid')
            if view_type == 'list':
                return ['trophies/partials/badge_list/badge_list_items.html']
            else:
                return ['trophies/partials/badge_list/badge_cards.html']
        return super().get_template_names()

class BadgeDetailView(DetailView):
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

        if target_profile:
            highest_tier_earned = UserBadge.objects.filter(profile=target_profile, badge__series_slug=self.kwargs['series_slug']).aggregate(max_tier=Max('badge__tier'))['max_tier'] or 0
            badge = series_badges.filter(tier=highest_tier_earned).first()
            if not badge:
                badge = series_badges.order_by('tier').first()
                context['is_maxed'] = True
            else:
                context['is_maxed'] = False

            context['badge'] = badge

            progress = UserBadgeProgress.objects.filter(profile=target_profile, badge=badge).first()
            context['progress'] = progress
            context['progress_percent'] = progress.completed_concepts / progress.required_concepts * 100 if progress and progress.required_concepts > 0 else 0
        else:
            context['badge'] = series_badges.filter(tier=1).first()

        highest_tier_badge = series_badges.order_by('-tier').first()
        if highest_tier_badge:
            if highest_tier_badge.concepts.count() > 0:
                concepts = highest_tier_badge.concepts.all().order_by('-release_date')
            else:
                concepts = highest_tier_badge.base_badge.concepts.all().order_by('-release_date') if highest_tier_badge.base_badge and highest_tier_badge.base_badge.concepts.count() > 0 else Concept.objects.none()
        else:
            concepts = Concept.objects.none()

        grouped_games = []
        for concept in concepts:
            games = concept.games.all().order_by('title_name')
            game_data = []
            for game in games:
                is_modern = any(plat in MODERN_PLATFORMS for plat in game.title_platform) and game.is_obtainable
                profile_game = ProfileGame.objects.filter(profile=target_profile, game=game).first() if target_profile else None
                game_data.append({
                    'game': game,
                    'is_modern': is_modern,
                    'profile_game': profile_game,
                })
            grouped_games.append({
                'concept': concept,
                'games': game_data,
            })
        context['grouped_games'] = grouped_games

        if len(grouped_games) > 0:
            recent_concept = grouped_games[0]['concept']
            context['image_urls'] = {'bg_url': recent_concept.bg_url, 'recent_concept_icon_url': recent_concept.concept_icon_url}
            context['recent_concept_name'] = recent_concept.unified_title

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': badge.effective_display_series if badge else 'Badge Series'},
        ]

        return context

class GuideListView(ListView):
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

        return context

    def get_template_names(self):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return ['trophies/partials/guide_list/guide_list_items.html']
        return super().get_template_names()

