import logging
from collections import defaultdict

from django.db.models import Max
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.constants import EVALUATABLE_BADGE_TYPES
from trophies.models import Badge, Stage, UserBadge, UserBadgeProgress
from trophies.services.xp_service import get_tier_xp
from trophies.util_modules.constants import BADGE_TIER_XP

logger = logging.getLogger('psn_api')

SAFE_LIMIT = 100
DEFAULT_LIMIT = 50


def _serialize_badge_layers(badge):
    """Return the badge image layer URLs for rendering in the mobile app."""
    layers = badge.get_badge_layers()
    return {
        'backdrop': layers.get('backdrop'),
        'main': layers.get('main'),
        'foreground': layers.get('foreground'),
        'has_custom_image': layers.get('has_custom_image', False),
    }


def _serialize_badge_tier(badge, is_earned=False, completed_concepts=0, progress_percentage=0.0):
    """Serialize a single Badge tier for list or detail responses."""
    return {
        'id': badge.id,
        'tier': badge.tier,
        'name': badge.display_title,
        'series': badge.display_series,
        'series_slug': badge.series_slug,
        'badge_type': badge.badge_type,
        'description': badge.description,
        'layers': _serialize_badge_layers(badge),
        'required_stages': badge.required_stages,
        'earned_count': badge.earned_count,
        'is_earned': is_earned,
        'completed_concepts': completed_concepts,
        'progress_percentage': round(progress_percentage, 1),
    }


def _bulk_series_stats(series_slugs):
    """
    Single-query bulk fetch of total_games and trophy_type counts per series.
    Mirrors BadgeListView._calculate_all_series_stats().
    """
    from trophies.models import Game
    games_qs = Game.objects.filter(
        concept__stages__series_slug__in=series_slugs
    ).values_list('id', 'concept__stages__series_slug', 'defined_trophies').distinct()

    series_games = defaultdict(dict)
    for game_id, slug, trophies in games_qs:
        if game_id not in series_games[slug]:
            series_games[slug][game_id] = trophies

    result = {}
    for slug in series_slugs:
        games_map = series_games.get(slug, {})
        total_games = len(games_map)
        trophy_types = {'bronze': 0, 'silver': 0, 'gold': 0, 'platinum': 0}
        for trophies in games_map.values():
            if trophies:
                for t in ('bronze', 'silver', 'gold', 'platinum'):
                    trophy_types[t] += trophies.get(t, 0)
        result[slug] = {'total_games': total_games, 'trophy_types': trophy_types}

    return result


class MobileBadgeListView(APIView):
    """
    GET /api/v1/mobile/badges/
    Paginated list of all live badge series (one entry per series, showing
    the user's highest earned tier or tier 1 for unauthenticated requests).

    Query params:
      - offset (int, default 0)
      - limit  (int, default 50, max 100)
    """
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=False))
    def get(self, request):
        try:
            offset = max(0, int(request.query_params.get('offset', 0)))
        except (ValueError, TypeError):
            offset = 0
        try:
            limit = min(SAFE_LIMIT, max(1, int(request.query_params.get('limit', DEFAULT_LIMIT))))
        except (ValueError, TypeError):
            limit = DEFAULT_LIMIT

        profile = getattr(request.user, 'profile', None)

        # Fetch all live badges, ordered for stable pagination
        all_badges = list(
            Badge.objects.live()
            .select_related('base_badge', 'most_recent_concept')
            .order_by('series_slug', 'tier')
        )

        # Group by series_slug
        grouped = defaultdict(list)
        for b in all_badges:
            grouped[b.series_slug].append(b)

        # Stable ordering of series (alphabetical)
        series_slugs_ordered = sorted(grouped.keys())
        total = len(series_slugs_ordered)

        page_slugs = series_slugs_ordered[offset: offset + limit]

        # Bulk fetch user data for the visible page
        earned_dict = {}
        progress_dict = {}
        if profile and page_slugs:
            user_earned = (
                UserBadge.objects
                .filter(profile=profile, badge__series_slug__in=page_slugs)
                .values('badge__series_slug')
                .annotate(max_tier=Max('badge__tier'))
            )
            earned_dict = {e['badge__series_slug']: e['max_tier'] for e in user_earned}

            page_badge_ids = [b.id for slug in page_slugs for b in grouped[slug]]
            progress_qs = (
                UserBadgeProgress.objects
                .filter(profile=profile, badge__id__in=page_badge_ids)
                .select_related('badge')
            )
            progress_dict = {p.badge_id: p for p in progress_qs}

        # Bulk series stats (1 query)
        stats_map = _bulk_series_stats(page_slugs) if page_slugs else {}

        results = []
        for slug in page_slugs:
            group = sorted(grouped[slug], key=lambda b: b.tier)
            tier1 = next((b for b in group if b.tier == 1), None)
            if not tier1:
                continue

            highest_tier = earned_dict.get(slug, 0)

            if profile and highest_tier > 0:
                display_badge = next((b for b in group if b.tier == highest_tier), tier1)
                is_earned = True
            else:
                display_badge = tier1
                is_earned = False

            # Progress toward next tier
            next_badge = next((b for b in group if b.tier > highest_tier), None)
            progress_badge = next_badge or display_badge
            progress = progress_dict.get(progress_badge.id) if profile else None
            required_stages = progress_badge.required_stages
            if progress and progress_badge.badge_type in EVALUATABLE_BADGE_TYPES:
                completed_concepts = progress.completed_concepts
                progress_pct = (completed_concepts / required_stages * 100) if required_stages else 0
            else:
                completed_concepts = 0
                progress_pct = 0

            series_stats = stats_map.get(slug, {'total_games': 0, 'trophy_types': {}})

            results.append({
                'series_slug': slug,
                'display_badge': _serialize_badge_tier(
                    display_badge,
                    is_earned=is_earned,
                    completed_concepts=completed_concepts,
                    progress_percentage=progress_pct,
                ),
                'user_highest_tier': highest_tier,
                'tier1_earned_count': tier1.earned_count,
                'total_games': series_stats['total_games'],
                'trophy_types': series_stats['trophy_types'],
                'all_tiers': [_serialize_badge_tier(b) for b in group],
            })

        return Response({
            'count': total,
            'offset': offset,
            'limit': limit,
            'has_more': (offset + limit) < total,
            'results': results,
        })


class MobileBadgeSeriesDetailView(APIView):
    """
    GET /api/v1/mobile/badges/<series_slug>/
    Full detail for a badge series: all tiers, stages, and the authenticated
    user's progress per tier.
    """
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=False))
    def get(self, request, series_slug):
        badges = list(
            Badge.objects.live()
            .filter(series_slug=series_slug)
            .select_related('base_badge', 'most_recent_concept', 'title')
            .order_by('tier')
        )
        if not badges:
            return Response({'error': 'Badge series not found.'}, status=status.HTTP_404_NOT_FOUND)

        profile = getattr(request.user, 'profile', None)

        # User's earned tiers
        earned_badge_ids = set()
        progress_by_badge = {}
        if profile:
            earned_badge_ids = set(
                UserBadge.objects.filter(profile=profile, badge__series_slug=series_slug)
                .values_list('badge_id', flat=True)
            )
            progress_qs = UserBadgeProgress.objects.filter(
                profile=profile, badge__series_slug=series_slug
            ).select_related('badge')
            progress_by_badge = {p.badge_id: p for p in progress_qs}

        # Stages for this series
        stages = list(
            Stage.objects.filter(series_slug=series_slug)
            .prefetch_related('concepts__games')
            .order_by('stage_number')
        )

        serialized_stages = []
        for stage in stages:
            concepts = list(stage.concepts.all())
            stage_games = []
            seen_game_ids = set()
            for concept in concepts:
                for game in concept.games.all():
                    if game.id in seen_game_ids:
                        continue
                    seen_game_ids.add(game.id)
                    stage_games.append({
                        'game_id': game.id,
                        'title_name': game.title_name,
                        'title_icon_url': game.title_icon_url,
                        'title_image': game.title_image.url if game.title_image else None,
                        'title_platform': game.title_platform,
                        'region': game.region,
                        'defined_trophies': game.defined_trophies,
                        'concept_slug': concept.slug,
                    })
            serialized_stages.append({
                'stage_number': stage.stage_number,
                'title': stage.title,
                'stage_icon': stage.stage_icon,
                'required_tiers': stage.required_tiers,
                'has_online_trophies': stage.has_online_trophies,
                'game_count': len(stage_games),
                'games': stage_games,
            })

        # Series-level stats
        stats_map = _bulk_series_stats([series_slug])
        series_stats = stats_map.get(series_slug, {'total_games': 0, 'trophy_types': {}})

        tiers = []
        for badge in badges:
            is_earned = badge.id in earned_badge_ids
            progress = progress_by_badge.get(badge.id)
            completed_concepts = progress.completed_concepts if progress else 0
            required_stages = badge.required_stages
            progress_pct = (
                (completed_concepts / required_stages * 100) if required_stages else 0
            )
            # XP metadata: what's available and what the user has earned for this tier.
            # stage_xp: XP earned per concept completed (tier-dependent constant)
            # completion_bonus: flat bonus for finishing the tier
            # max_xp: total possible XP for this tier
            # user_xp: what this user has earned (progress + bonus if earned)
            tier_stage_xp = get_tier_xp(badge.tier)
            max_xp = required_stages * tier_stage_xp + BADGE_TIER_XP
            user_xp = completed_concepts * tier_stage_xp + (BADGE_TIER_XP if is_earned else 0)
            tiers.append({
                **_serialize_badge_tier(badge, is_earned, completed_concepts, progress_pct),
                'title_awarded': badge.title.display_title if badge.title else None,
                'xp': {
                    'stage_xp': tier_stage_xp,
                    'completion_bonus': BADGE_TIER_XP,
                    'max_xp': max_xp,
                    'user_xp': user_xp,
                },
            })

        return Response({
            'series_slug': series_slug,
            'series_name': badges[0].display_series,
            'badge_type': badges[0].badge_type,
            'total_games': series_stats['total_games'],
            'trophy_types': series_stats['trophy_types'],
            'tiers': tiers,
            'stages': serialized_stages,
        })


class MobileUserBadgesView(APIView):
    """
    GET /api/v1/mobile/user/badges/
    The authenticated user's earned badges, ordered by most recently earned.

    Query params:
      - offset (int, default 0)
      - limit  (int, default 50, max 100)
    """
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=False))
    def get(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response({'count': 0, 'has_more': False, 'results': []})

        try:
            offset = max(0, int(request.query_params.get('offset', 0)))
        except (ValueError, TypeError):
            offset = 0
        try:
            limit = min(SAFE_LIMIT, max(1, int(request.query_params.get('limit', DEFAULT_LIMIT))))
        except (ValueError, TypeError):
            limit = DEFAULT_LIMIT

        qs = (
            UserBadge.objects
            .filter(profile=profile)
            .select_related('badge', 'badge__base_badge', 'badge__title')
            .order_by('-earned_at')
        )
        total = qs.count()
        page = list(qs[offset: offset + limit])

        results = []
        for ub in page:
            badge = ub.badge
            results.append({
                **_serialize_badge_tier(badge, is_earned=True),
                'earned_at': ub.earned_at,
                'is_displayed': ub.is_displayed,
                'title_awarded': badge.title.display_title if badge.title else None,
            })

        return Response({
            'count': total,
            'offset': offset,
            'limit': limit,
            'has_more': (offset + limit) < total,
            'results': results,
        })


class MobileProfileBadgesView(APIView):
    """
    GET /api/v1/mobile/profiles/<psn_username>/badges/
    Any profile's earned badges. Public endpoint (no auth required), but
    requires IsAuthenticated to prevent unauthenticated enumeration.

    Query params:
      - offset (int, default 0)
      - limit  (int, default 50, max 100)
    """
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=False))
    def get(self, request, psn_username):
        from trophies.models import Profile
        try:
            profile = Profile.objects.get(psn_username__iexact=psn_username)
        except Profile.DoesNotExist:
            return Response({'error': 'Profile not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not profile.psn_history_public:
            return Response({'error': 'Profile not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            offset = max(0, int(request.query_params.get('offset', 0)))
        except (ValueError, TypeError):
            offset = 0
        try:
            limit = min(SAFE_LIMIT, max(1, int(request.query_params.get('limit', DEFAULT_LIMIT))))
        except (ValueError, TypeError):
            limit = DEFAULT_LIMIT

        qs = (
            UserBadge.objects
            .filter(profile=profile)
            .select_related('badge', 'badge__base_badge', 'badge__title')
            .order_by('-earned_at')
        )
        total = qs.count()
        page = list(qs[offset: offset + limit])

        results = []
        for ub in page:
            badge = ub.badge
            results.append({
                **_serialize_badge_tier(badge, is_earned=True),
                'earned_at': ub.earned_at,
                'is_displayed': ub.is_displayed,
                'title_awarded': badge.title.display_title if badge.title else None,
            })

        return Response({
            'count': total,
            'offset': offset,
            'limit': limit,
            'has_more': (offset + limit) < total,
            'results': results,
        })
