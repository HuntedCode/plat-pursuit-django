from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from trophies.models import Game, Profile, Badge, Checklist, GameList, Challenge


class StaticViewSitemap(Sitemap):
    priority = 0.8
    changefreq = 'weekly'

    def items(self):
        return [
            'home', 'privacy', 'terms', 'about', 'contact',
            'games_list', 'profiles_list', 'badges_list',
            'challenges_browse', 'lists_browse',
            'community_hub',
        ]

    def location(self, item):
        return reverse(item)


# Per-row .only() drops the field set to just what location()/lastmod() need.
# Game and Profile carry JSONFields and large text columns that the sitemap
# render never reads; full ORM objects allocated ~1-2 KB each, while the
# slim version is ~50 bytes. For tens of thousands of items that's the
# difference between a 160 MB allocation and a few MB.
#
# Sitemap.limit sets the max URLs per page when the sitemap_index view is
# in use (see plat_pursuit/urls.py). 5000 is well under the sitemap-protocol
# 50000 cap and keeps any single request bounded; crawlers fetch additional
# pages via ?p=N as needed.


class GameSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.6
    limit = 5000

    def items(self):
        return (
            Game.objects
            .filter(np_communication_id__isnull=False)
            .only('np_communication_id', 'updated_at')
            .order_by('-id')
        )

    def location(self, obj):
        return reverse('game_detail', kwargs={'np_communication_id': obj.np_communication_id})

    def lastmod(self, obj):
        return obj.updated_at if hasattr(obj, 'updated_at') else None


class ProfileSitemap(Sitemap):
    changefreq = 'daily'
    priority = 0.5
    limit = 5000

    def items(self):
        return (
            Profile.objects
            .filter(psn_username__isnull=False)
            .only('psn_username', 'updated_at')
            .order_by('-id')
        )

    def location(self, obj):
        return reverse('profile_detail', kwargs={'psn_username': obj.psn_username})

    def lastmod(self, obj):
        return obj.updated_at if hasattr(obj, 'updated_at') else None


class BadgeSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.6
    limit = 5000

    def items(self):
        return (
            Badge.objects
            .filter(tier=1, is_live=True)
            .only('series_slug', 'created_at')
            .order_by('-id')
        )

    def location(self, obj):
        return reverse('badge_detail', kwargs={'series_slug': obj.series_slug})

    def lastmod(self, obj):
        return obj.created_at


class GuideSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.5
    limit = 5000

    def items(self):
        return (
            Checklist.objects
            .filter(status='published')
            .only('id', 'updated_at')
            .order_by('-id')
        )

    def location(self, obj):
        return reverse('guide_detail', kwargs={'guide_id': obj.id})

    def lastmod(self, obj):
        return obj.updated_at


class GameListSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.4
    limit = 5000

    def items(self):
        return (
            GameList.objects
            .filter(is_public=True, is_deleted=False)
            .only('id', 'updated_at')
            .order_by('-id')
        )

    def location(self, obj):
        return reverse('list_detail', kwargs={'list_id': obj.id})

    def lastmod(self, obj):
        return obj.updated_at


class ChallengeSitemap(Sitemap):
    changefreq = 'daily'
    priority = 0.4
    limit = 5000

    def items(self):
        return (
            Challenge.objects
            .filter(is_deleted=False)
            .only('id', 'challenge_type', 'updated_at')
            .order_by('-id')
        )

    def location(self, obj):
        prefix_map = {'az': 'az', 'calendar': 'calendar', 'genre': 'genre'}
        prefix = prefix_map.get(obj.challenge_type, 'az')
        return reverse(f'{prefix}_challenge_detail', kwargs={'challenge_id': obj.id})

    def lastmod(self, obj):
        return obj.updated_at
