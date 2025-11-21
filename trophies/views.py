import json
import logging
import math
from datetime import timedelta, date
from django.core.cache import cache
from django.contrib import messages
from django.shortcuts import render
from django.http import StreamingHttpResponse, JsonResponse, HttpResponseRedirect
from django.views.generic import ListView, View, DetailView
from django.db.models import Q, F, Prefetch, OuterRef, Subquery, Value, IntegerField, FloatField, Count, Avg
from django.db.models.functions import Coalesce
from django.urls import reverse_lazy
from django.utils import timezone
from .models import Game, Trophy, Profile, EarnedTrophy, ProfileGame
from .forms import GameSearchForm, TrophySearchForm, ProfileSearchForm
from .utils import redis_client

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
        logger.info(kwargs)
        game = self.object
        user = self.request.user
        profile_username = self.kwargs.get('profile_username')
        today = date.today().isoformat()
        images_cache_key = f"game:imageurls:{game.np_communication_id}"
        images_timeout = 604800
        stats_cache_key = f"game:stats:{game.np_communication_id}:{today}"
        stats_timeout = 86400
        trophy_cache_key = f"game:trophies:{game.np_communication_id}"
        trophy_timeout = 604800

        if profile_username:
            try:
                target_profile = Profile.objects.get(psn_username__iexact=profile_username)
            except Profile.DoesNotExist:
                messages.error(self.request, "Profile not found.")
        elif user.is_authenticated and hasattr(user, 'profile') and user.profile and user.profile.is_linked:
            target_profile = user.profile

        logger.info(f"Target Profile: {target_profile} | Profile Username: {profile_username}")

        profile_progress = None
        profile_trophy_totals = {}
        profile_earned = {}
        timeline = []
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

                earned_list = list(ordered_earned_qs)
                total_trophies = len(earned_qs)
                if len(earned_list) > 0:
                    milestones = [0, 0.25, 0.5, 0.75, 1.0]
                    timeline = []
                    for pct in milestones:
                        idx = math.ceil((total_trophies - 1) * pct)
                        if idx <= len(earned_list):
                            entry = earned_list[idx]
                            timeline.append({
                                'trophy_name': entry.trophy.trophy_name,
                                'earned_date_time': entry.earned_date_time
                            })
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
                    if game.concept.media and 'images' in game.concept.media:
                        for img in game.concept.media['images']:
                            if img.get('type') == 'BACKGROUND_LAYER_ART':
                                bg_url = img.get('url')
                                break
                            elif img.get('type') == 'GAMEHUB_COVER_ART':
                                bg_url = img.get('url')
                        
                        for img in game.concept.media['images']:
                            if img.get('type') == 'SCREENSHOT':
                                screenshot_urls.append(img.get('url'))
                        
                        if len(screenshot_urls) < 1:
                            for img in game.concept.media['images']:
                                img_type = img.get('type')
                                if img_type == 'GAMEHUB_COVER_ART' or img_type == 'LOGO' or img_type == 'MASTER':
                                    screenshot_urls.append(img.get('url'))

                    if game.concept.content_rating:
                        content_rating_url = game.concept.content_rating.get('url')
                    
                    image_urls = {
                        'bg_url': bg_url,
                        'screenshot_urls': screenshot_urls,
                        'content_rating_url': content_rating_url
                    }
                    cache.set(images_cache_key, json.dumps(image_urls), timeout=images_timeout)
                else:
                    image_urls = {}
        except Exception as e:
            logger.error(f"Game stats cache failed for {game.np_communication_id}: {e}")
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
                full_trophies = list(trophies_qs.values(
                    'trophy_id', 'trophy_type', 'trophy_name', 'trophy_detail',
                    'trophy_icon_url', 'trophy_group_id', 'trophy_rarity', 'trophy_earn_rate',
                    'earned_count', 'earn_rate'
                ))
                cache.set(trophy_cache_key, json.dumps(full_trophies), timeout=trophy_timeout)
        except Exception as e:
            logger.error(f"Game trophies cache failed for {game.np_communication_id}: {e}")
            full_trophies = []

        context['profile'] = target_profile
        context['profile_progress'] = profile_progress
        context['profile_earned'] = profile_earned
        context['profile_trophy_totals'] = profile_trophy_totals
        context['game_stats'] = stats
        context['full_trophies'] = full_trophies
        context['image_urls'] = image_urls
        context['timeline'] = timeline
        return context