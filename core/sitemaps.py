from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from trophies.models import Game, Profile, Badge, Checklist, GameList, Challenge, Roadmap


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


# Django's default Sitemap.get_latest_lastmod() iterates the entire `items()`
# queryset just to compute max(lastmod). On whale-scale tables (50K+ Games,
# Profiles, Roadmaps) that materializes every row on every /sitemap.xml hit —
# which was the trigger for the May 2026 sitemap-index OOM/500. Each subclass
# overrides it with a single ORDER BY ... LIMIT 1 query against the lastmod
# column instead.


class GameSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.6
    limit = 5000

    def items(self):
        return (
            Game.objects
            .filter(np_communication_id__isnull=False)
            .only('np_communication_id', 'created_at')
            .order_by('-id')
        )

    def location(self, obj):
        return reverse('game_detail', kwargs={'np_communication_id': obj.np_communication_id})

    def lastmod(self, obj):
        return obj.created_at

    def get_latest_lastmod(self):
        return (
            Game.objects.filter(np_communication_id__isnull=False)
            .order_by('-created_at')
            .values_list('created_at', flat=True)
            .first()
        )


class ProfileSitemap(Sitemap):
    changefreq = 'daily'
    priority = 0.5
    limit = 5000

    def items(self):
        return (
            Profile.objects
            .filter(psn_username__isnull=False)
            .only('psn_username', 'created_at')
            .order_by('-id')
        )

    def location(self, obj):
        return reverse('profile_detail', kwargs={'psn_username': obj.psn_username})

    def lastmod(self, obj):
        return obj.created_at

    def get_latest_lastmod(self):
        return (
            Profile.objects.filter(psn_username__isnull=False)
            .order_by('-created_at')
            .values_list('created_at', flat=True)
            .first()
        )


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

    def get_latest_lastmod(self):
        return (
            Badge.objects.filter(tier=1, is_live=True)
            .order_by('-created_at')
            .values_list('created_at', flat=True)
            .first()
        )


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

    def get_latest_lastmod(self):
        return (
            Checklist.objects.filter(status='published')
            .order_by('-updated_at')
            .values_list('updated_at', flat=True)
            .first()
        )


class RoadmapSitemap(Sitemap):
    """Published trophy roadmaps — the per-CTG guide pages.

    Each Roadmap is scoped to a single ConceptTrophyGroup (base game or
    a specific DLC), so each row maps to one detail URL. Only published
    roadmaps are surfaced; drafts stay out of the index (and they'd be
    noindexed even if a crawler reached them directly).

    Priority 0.7 — these are high-value destination pages (long-form
    trophy guides) and we want crawlers to revisit weekly to pick up
    author edits.
    """
    changefreq = 'weekly'
    priority = 0.7
    limit = 5000

    def items(self):
        return (
            Roadmap.objects
            .filter(status='published')
            .select_related('concept_trophy_group__concept')
            # `.only()` would be ideal here but the URL builder needs to
            # walk concept_trophy_group -> concept -> game (reverse FK)
            # to resolve np_communication_id; leaving the row fields in
            # so the FK navigation works without extra queries.
            .order_by('-id')
        )

    def location(self, obj):
        # Reverse FK: a concept can have multiple games (platforms). For
        # the URL we need any one — `.first()` is stable and matches the
        # `game_detail` URL the reader hits from the game page.
        concept = obj.concept_trophy_group.concept
        game = concept.games.first() if concept else None
        if game is None or not game.np_communication_id:
            return None
        group_id = obj.concept_trophy_group.trophy_group_id
        if group_id == 'default':
            return reverse('roadmap_detail', kwargs={
                'np_communication_id': game.np_communication_id,
            })
        return reverse('roadmap_detail_dlc', kwargs={
            'np_communication_id': game.np_communication_id,
            'trophy_group_id': group_id,
        })

    def lastmod(self, obj):
        return obj.updated_at

    def get_latest_lastmod(self):
        return (
            Roadmap.objects.filter(status='published')
            .order_by('-updated_at')
            .values_list('updated_at', flat=True)
            .first()
        )


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

    def get_latest_lastmod(self):
        return (
            GameList.objects.filter(is_public=True, is_deleted=False)
            .order_by('-updated_at')
            .values_list('updated_at', flat=True)
            .first()
        )


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

    def get_latest_lastmod(self):
        return (
            Challenge.objects.filter(is_deleted=False)
            .order_by('-updated_at')
            .values_list('updated_at', flat=True)
            .first()
        )
