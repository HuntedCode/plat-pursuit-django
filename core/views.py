from django.views.generic import TemplateView
from django.urls import reverse_lazy
from django.core.cache import cache
from django.utils import timezone
from .services.stats import compute_community_stats
from .services.featured import get_featured_games
from .services.latest_platinums import get_latest_platinums
from .services.playing_now import get_playing_now

class IndexView(TemplateView):
    template_name = 'index.html'
    STATS_CACHE_KEY = 'community_stats'
    STATS_CACHE_TIMEOUT = 3600
    FEATURED_GAMES_KEY = 'featured_games'
    FEATURED_GAMES_TIMEOUT = 86400
    LATEST_PLATINUMS_KEY = 'latest_platinums'
    LATEST_PLATINUMS_TIMEOUT = 3600
    PLAYING_NOW_KEY = 'playing_now'
    PLAYING_NOW_TIMEOUT = 86400

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today_utc = timezone.now().date().isoformat()
        now_utc = timezone.now()

        # Breadcrumb
        context['items'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Dashboard'},
        ]

        # Cache community stats - cache resets hourly at top of the hour UTC
        community_stats_key = f"{self.STATS_CACHE_KEY}_{today_utc}_{now_utc.hour:02d}"
        community_stats = cache.get_or_set(
            community_stats_key,
            compute_community_stats,
            self.STATS_CACHE_TIMEOUT * 2
        )
        context['communityStats'] = community_stats

        # Cache featured games - cache resets daily at midnight UTC
        featured_key = f"{self.FEATURED_GAMES_KEY}_{today_utc}"
        featured = cache.get_or_set(
            featured_key,
            lambda: get_featured_games(),
            self.FEATURED_GAMES_TIMEOUT * 2
        )
        context['featuredGames'] = featured

        # Latest platinums - cache resets hourly at top of the hour UTC
        latest_plats_key = f"{self.LATEST_PLATINUMS_KEY}_{today_utc}_{now_utc.hour:02d}"
        latest_plats = cache.get_or_set(
            latest_plats_key,
            lambda: get_latest_platinums(),
            self.LATEST_PLATINUMS_TIMEOUT * 2
        )
        context['latestPlatinums'] = latest_plats

        # Playing Now - cache resets daily at midnight UTC
        playing_now_key = f"{self.PLAYING_NOW_KEY}_{today_utc}"
        playing_now = cache.get_or_set(
            playing_now_key,
            lambda: get_playing_now(),
            self.PLAYING_NOW_TIMEOUT * 2
        )
        context['playingNow'] = playing_now

        return context
