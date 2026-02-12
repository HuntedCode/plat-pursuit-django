import json
import logging
from django.contrib.staticfiles.finders import find
from django.conf import settings
from django.http import HttpResponse
from django.templatetags.static import static
from django.views.generic import TemplateView, View
from django.urls import reverse_lazy
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
from .services.playing_now import get_playing_now
from .services.featured_guide import get_featured_guide
from .services.tracking import track_page_view
from trophies.models import Concept
from trophies.mixins import ProfileHotbarMixin

logger = logging.getLogger('psn_api')

class IndexView(ProfileHotbarMixin, TemplateView):
    template_name = 'index.html'
    STATS_CACHE_KEY = 'community_stats'
    STATS_CACHE_TIMEOUT = 3600
    FEATURED_GAMES_KEY = 'featured_games'
    FEATURED_GAMES_TIMEOUT = 86400
    FEATURED_GUIDE_KEY = 'featured_guide'
    FEATURED_GUIDE_TIMEOUT = 86400
    LATEST_BADGES_KEY = 'latest_badges'
    LATEST_BADGES_TIMEOUT = 3600
    PLAYING_NOW_KEY = 'playing_now'
    PLAYING_NOW_TIMEOUT = 86400
    FEATURED_BADGES_KEY = 'featured_badges'
    FEATURED_BADGES_TIMEOUT = 86400
    FEATURED_CHECKLISTS_KEY = 'featured_checklists'
    FEATURED_CHECKLISTS_TIMEOUT = 86400
    WHATS_NEW_KEY = 'whats_new'
    WHATS_NEW_TIMEOUT = 3600


    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today_utc = timezone.now().date().isoformat()
        now_utc = timezone.now()

        # Track page view
        from core.models import SiteSettings
        track_page_view('index', 'home', self.request)
        settings = SiteSettings.get_settings()

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

        # Latest badges - cache resets hourly at top of the hour UTC (cron)
        latest_badges_key = f"{self.LATEST_BADGES_KEY}_{today_utc}_{now_utc.hour:02d}"
        latest_badges = cache.get(latest_badges_key)
        if latest_badges is None:
            prev_key = f"{self.LATEST_BADGES_KEY}_{today_utc}_{(now_utc.hour - 1):02d}"
            latest_badges = cache.get(prev_key)
        context['latestBadges'] = latest_badges

        # Playing Now - cache resets daily at midnight UTC
        playing_now_key = f"{self.PLAYING_NOW_KEY}_{today_utc}"
        playing_now = cache.get_or_set(
            playing_now_key,
            lambda: get_playing_now(),
            self.PLAYING_NOW_TIMEOUT * 2
        )
        context['playingNow'] = playing_now

        # Featured badges - cache resets daily at midnight UTC (cron)
        featured_badges_key = f"{self.FEATURED_BADGES_KEY}_{today_utc}"
        featured_badges = cache.get(featured_badges_key)
        if featured_badges is None:
            prev_day = now_utc - timedelta(days=1)
            prev_key = f"{self.FEATURED_BADGES_KEY}_{prev_day.date().isoformat()}"
            featured_badges = cache.get(prev_key)
        context['featuredBadges'] = featured_badges

        # Featured checklists - cache resets daily at midnight UTC (cron)
        featured_checklists_key = f"{self.FEATURED_CHECKLISTS_KEY}_{today_utc}"
        featured_checklists = cache.get(featured_checklists_key)
        if featured_checklists is None:
            prev_day = now_utc - timedelta(days=1)
            prev_key = f"{self.FEATURED_CHECKLISTS_KEY}_{prev_day.date().isoformat()}"
            featured_checklists = cache.get(prev_key)
        context['featuredChecklists'] = featured_checklists

        # What's New - cache resets hourly at top of the hour UTC (cron)
        whats_new_key = f"{self.WHATS_NEW_KEY}_{today_utc}_{now_utc.hour:02d}"
        whats_new = cache.get(whats_new_key)
        if whats_new is None:
            prev_key = f"{self.WHATS_NEW_KEY}_{today_utc}_{(now_utc.hour - 1):02d}"
            whats_new = cache.get(prev_key)
        context['whatsNew'] = whats_new

        return context
    
class AdsTxtView(View):
    def get(self, request):
        file_path = find('ads.txt')  # Finders search all STATICFILES_DIRS
        if file_path:
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                return HttpResponse(content, content_type='text/plain')
            except Exception as e:
                logger.error(f"Error serving ads.txt: {e}")
                return HttpResponse("ads.txt not found", status=404)
        else:
            logger.warning("ads.txt not found in static files")
            return HttpResponse("ads.txt not found", status=404)


class RobotsTxtView(View):
    def get(self, request):
        file_path = find('robots.txt')
        if file_path:
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                return HttpResponse(content, content_type='text/plain')
            except Exception as e:
                logger.error(f"Error serving robots.txt: {e}")
                return HttpResponse("robots.txt not found", status=404)
        else:
            logger.warning("robots.txt not found in static files")
            return HttpResponse("robots.txt not found", status=404)


class PrivacyPolicyView(TemplateView):
    template_name = 'pages/privacy.html'


class TermsOfServiceView(TemplateView):
    template_name = 'pages/terms.html'


class AboutView(TemplateView):
    template_name = 'pages/about.html'


class ContactView(TemplateView):
    template_name = 'pages/contact.html'