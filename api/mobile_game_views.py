import logging

from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import EarnedTrophy, Game, ProfileGame, Trophy, TrophyGroup

logger = logging.getLogger('psn_api')

SAFE_LIMIT = 100
DEFAULT_LIMIT = 50


def _serialize_game_summary(game, profile_game=None):
    """Compact game summary used in profile game list."""
    data = {
        'game_id': game.id,
        'np_communication_id': game.np_communication_id,
        'title_name': game.title_name,
        'title_icon_url': game.title_icon_url,
        'title_image': game.title_image.url if game.title_image else None,
        'title_platform': game.title_platform,
        'region': game.region,
        'defined_trophies': game.defined_trophies,
        'has_trophy_groups': game.has_trophy_groups,
        'concept_slug': game.concept.slug if game.concept else None,
    }
    if profile_game:
        data.update({
            'progress': profile_game.progress,
            'has_plat': profile_game.has_plat,
            'earned_trophies_count': profile_game.earned_trophies_count,
            'unearned_trophies_count': profile_game.unearned_trophies_count,
            'total_trophies': profile_game.total_trophies,
            'last_played': profile_game.last_played_date_time,
            'most_recent_trophy_date': profile_game.most_recent_trophy_date,
        })
    return data


class MobileProfileGamesView(APIView):
    """
    GET /api/v1/mobile/profiles/<psn_username>/games/
    Paginated list of games played by a profile, including per-game progress.
    Public endpoint (authenticated to prevent enumeration).

    Query params:
      - offset (int, default 0)
      - limit  (int, default 50, max 100)
      - sort   (str): 'recent' (default), 'progress', 'alpha', 'plat'
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

        sort = request.query_params.get('sort', 'recent')

        qs = (
            ProfileGame.objects
            .filter(profile=profile, user_hidden=False)
            .select_related('game', 'game__concept')
        )

        if sort == 'progress':
            qs = qs.order_by('-progress', '-most_recent_trophy_date')
        elif sort == 'alpha':
            from django.db.models.functions import Lower
            qs = qs.order_by(Lower('game__title_name'))
        elif sort == 'plat':
            qs = qs.order_by('-has_plat', '-most_recent_trophy_date')
        else:  # 'recent' (default)
            qs = qs.order_by('-most_recent_trophy_date')

        total = qs.count()
        page = list(qs[offset: offset + limit])

        results = [_serialize_game_summary(pg.game, profile_game=pg) for pg in page]

        return Response({
            'count': total,
            'offset': offset,
            'limit': limit,
            'has_more': (offset + limit) < total,
            'results': results,
        })


class MobileGameTrophiesView(APIView):
    """
    GET /api/v1/mobile/games/<game_id>/trophies/
    Full trophy list for a game, with the authenticated user's earn status
    and dates injected per trophy.

    Query params:
      - sort (str): 'default' (trophy_id), 'type', 'earned', 'rarity'
    """
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=False))
    def get(self, request, game_id):
        try:
            game = Game.objects.select_related('concept').get(pk=game_id)
        except Game.DoesNotExist:
            return Response({'error': 'Game not found.'}, status=status.HTTP_404_NOT_FOUND)

        sort = request.query_params.get('sort', 'default')

        trophies_qs = Trophy.objects.filter(game=game).order_by('trophy_group_id', 'trophy_id')

        # Build earned lookup for the requesting user
        profile = getattr(request.user, 'profile', None)
        earned_map = {}
        if profile:
            earned_entries = (
                EarnedTrophy.objects
                .filter(profile=profile, trophy__game=game)
                .values('trophy_id', 'earned', 'earned_date_time', 'progress', 'progress_rate')
            )
            earned_map = {e['trophy_id']: e for e in earned_entries}

        # Fetch trophy groups for DLC label lookup
        groups_qs = TrophyGroup.objects.filter(game=game)
        groups_map = {g.trophy_group_id: g for g in groups_qs}

        trophies = list(trophies_qs)

        # Apply sorting
        if sort == 'type':
            type_order = {'platinum': 0, 'gold': 1, 'silver': 2, 'bronze': 3}
            trophies.sort(key=lambda t: (type_order.get(t.trophy_type, 9), t.trophy_id))
        elif sort == 'earned':
            # Earned first (with earn date desc), then unearned
            def earned_sort_key(t):
                entry = earned_map.get(t.id)
                if entry and entry['earned']:
                    dt = entry['earned_date_time']
                    return (0, -(dt.timestamp() if dt else 0))
                return (1, 0)
            trophies.sort(key=earned_sort_key)
        elif sort == 'rarity':
            trophies.sort(key=lambda t: t.earn_rate)

        serialized = []
        for trophy in trophies:
            entry = earned_map.get(trophy.id, {})
            group = groups_map.get(trophy.trophy_group_id)
            serialized.append({
                'trophy_id': trophy.trophy_id,
                'trophy_type': trophy.trophy_type,
                'trophy_name': trophy.trophy_name,
                'trophy_detail': trophy.trophy_detail,
                'trophy_icon_url': trophy.trophy_icon_url,
                'trophy_group_id': trophy.trophy_group_id,
                'group_name': group.trophy_group_name if group else None,
                'group_icon_url': group.trophy_group_icon_url if group else None,
                'trophy_earn_rate': trophy.trophy_earn_rate,
                'earn_rate': trophy.earn_rate,
                'rarity_tier': trophy.get_pp_rarity_tier(),
                'earned_count': trophy.earned_count,
                # User-specific fields
                'earned': entry.get('earned', False),
                'earned_date_time': entry.get('earned_date_time'),
                'progress': entry.get('progress', 0),
                'progress_rate': entry.get('progress_rate', 0),
                'progress_target': trophy.progress_target_value,
            })

        # Compute per-group counts for the user
        groups_data = []
        for group in sorted(groups_map.values(), key=lambda g: g.trophy_group_id):
            group_trophy_ids = {t.id for t in trophies if t.trophy_group_id == group.trophy_group_id}
            user_earned_in_group = sum(
                1 for tid in group_trophy_ids
                if earned_map.get(tid, {}).get('earned')
            )
            groups_data.append({
                'trophy_group_id': group.trophy_group_id,
                'trophy_group_name': group.trophy_group_name,
                'trophy_group_icon_url': group.trophy_group_icon_url,
                'defined_trophies': group.defined_trophies,
                'user_earned': user_earned_in_group,
            })

        # User's ProfileGame record for this game
        profile_game = None
        if profile:
            try:
                pg = ProfileGame.objects.get(profile=profile, game=game)
                profile_game = {
                    'progress': pg.progress,
                    'has_plat': pg.has_plat,
                    'earned_trophies_count': pg.earned_trophies_count,
                    'total_trophies': pg.total_trophies,
                    'last_played': pg.last_played_date_time,
                    'most_recent_trophy_date': pg.most_recent_trophy_date,
                }
            except ProfileGame.DoesNotExist:
                pass

        return Response({
            'game': _serialize_game_summary(game),
            'concept_slug': game.concept.slug if game.concept else None,
            'profile_game': profile_game,
            'trophy_groups': groups_data,
            'trophies': serialized,
            'total_trophies': len(serialized),
        })
