from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from trophies.models import Game, Profile, Concept


class StaticViewSitemap(Sitemap):
    priority = 0.8
    changefreq = 'weekly'

    def items(self):
        return ['home', 'privacy', 'terms', 'about', 'contact', 'games_list', 'profiles_list', 'guides_list', 'badges_list']

    def location(self, item):
        return reverse(item)


class GameSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.6

    def items(self):
        return Game.objects.filter(np_communication_id__isnull=False).order_by('-id')[:5000]

    def location(self, obj):
        return reverse('game_detail', kwargs={'np_communication_id': obj.np_communication_id})

    def lastmod(self, obj):
        return obj.updated_at if hasattr(obj, 'updated_at') else None


class ProfileSitemap(Sitemap):
    changefreq = 'daily'
    priority = 0.5

    def items(self):
        return Profile.objects.filter(psn_username__isnull=False).order_by('-id')[:5000]

    def location(self, obj):
        return reverse('profile_detail', kwargs={'psn_username': obj.psn_username})

    def lastmod(self, obj):
        return obj.updated_at if hasattr(obj, 'updated_at') else None
