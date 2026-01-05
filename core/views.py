import json
from django.views.generic import TemplateView
from django.urls import reverse_lazy
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
from .services.stats import compute_community_stats
from .services.featured import get_featured_games
from .services.latest_platinums import get_latest_platinums
from .services.playing_now import get_playing_now
from .services.featured_profile import get_featured_profile
from .services.events import get_upcoming_events
from .services.featured_guide import get_featured_guide
from trophies.models import Concept
from trophies.mixins import ProfileHotbarMixin

class IndexView(ProfileHotbarMixin, TemplateView):
    template_name = 'index.html'
    STATS_CACHE_KEY = 'community_stats'
    STATS_CACHE_TIMEOUT = 3600
    FEATURED_GAMES_KEY = 'featured_games'
    FEATURED_GAMES_TIMEOUT = 86400
    FEATURED_GUIDE_KEY = 'featured_guide'
    FEATURED_GUIDE_TIMEOUT = 86400
    LATEST_PLATINUMS_KEY = 'latest_platinums'
    LATEST_PLATINUMS_TIMEOUT = 3600
    PLAYING_NOW_KEY = 'playing_now'
    PLAYING_NOW_TIMEOUT = 86400
    FEATURED_PROFILE_KEY = 'featured_profile'
    FEATURED_PROFILE_TIMEOUT = 86400
    EVENTS_KEY = 'upcoming_events'
    EVENTS_TIMEOUT = 86400
    EVENTS_PAGE_SIZE = 3


    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today_utc = timezone.now().date().isoformat()
        now_utc = timezone.now()

        # Breadcrumb
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Dashboard'},
        ]

        # Cache community stats - cache resets hourly at top of the hour UTC (cron)
        community_stats_key = f"{self.STATS_CACHE_KEY}_{today_utc}_{now_utc.hour:02d}"
        community_stats = cache.get(community_stats_key)
        if community_stats is None:
            prev_key = f"{self.STATS_CACHE_KEY}_{today_utc}_{(now_utc.hour - 1):02d}"
            community_stats = cache.get(prev_key)
        context['communityStats'] = community_stats

        # Cache featured games - cache resets daily at midnight UTC (cron)
        featured_key = f"{self.FEATURED_GAMES_KEY}_{today_utc}"
        featured = cache.get(featured_key)
        if featured is None:
            prev_day = now_utc - timedelta(days=1)
            prev_key = f"{self.FEATURED_GAMES_KEY}_{prev_day}"
            featured = cache.get(prev_key)
        context['featuredGames'] = featured

        featured_guide_key = f"{self.FEATURED_GUIDE_KEY}_{today_utc}"
        featured_guide_id = cache.get_or_set(
            featured_guide_key,
            get_featured_guide,
            self.FEATURED_GUIDE_TIMEOUT
        )
        try:
            featured_concept = Concept.objects.get(id=featured_guide_id)
            context['featured_concept'] = featured_concept
        except Concept.DoesNotExist:
            pass

        # Latest platinums - cache resets hourly at top of the hour UTC (cron)
        latest_plats_key = f"{self.LATEST_PLATINUMS_KEY}_{today_utc}_{now_utc.hour:02d}"
        latest_plats = cache.get(latest_plats_key)
        if latest_plats is None:
            prev_key = f"{self.LATEST_PLATINUMS_KEY}_{today_utc}_{(now_utc.hour - 1):02d}"
            latest_plats = cache.get(prev_key)
        context['latestPlatinums'] = latest_plats

        # Playing Now - cache resets daily at midnight UTC
        playing_now_key = f"{self.PLAYING_NOW_KEY}_{today_utc}"
        playing_now = cache.get_or_set(
            playing_now_key,
            lambda: get_playing_now(),
            self.PLAYING_NOW_TIMEOUT * 2
        )
        context['playingNow'] = playing_now

        # Featured profile - cache resets weekly
        week_start = (now_utc - timedelta(days=now_utc.weekday())).date().isoformat()
        featured_profile_key = f"featured_profile_{week_start}"
        featured = cache.get_or_set(
            featured_profile_key,
            lambda: get_featured_profile(),
            self.FEATURED_PROFILE_TIMEOUT * 8
        )
        context['featuredProfile'] = featured

        # Upcoming Events - cache resets daily at midnight UTC
        events_key = f"{self.EVENTS_KEY}_{today_utc}"
        events = cache.get_or_set(
            events_key,
            get_upcoming_events,
            self.EVENTS_TIMEOUT * 2
        )
        context['upcomingEvents_json'] = json.dumps(events)
        context['eventsPageSize'] = self.EVENTS_PAGE_SIZE

        return context