"""
Game Players API view.

Returns a paginated, filterable, sortable list of players for a specific game.
"""
import logging
from datetime import timedelta

from django.db.models import F
from django.db.models.functions import Lower
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import Game, ProfileGame
from api.utils import safe_int

logger = logging.getLogger('psn_api')

SORT_OPTIONS = {
    'progress': ['-progress', Lower('profile__psn_username')],
    'recent': [F('most_recent_trophy_date').desc(nulls_last=True), Lower('profile__psn_username')],
    'first': [F('most_recent_trophy_date').asc(nulls_last=True), Lower('profile__psn_username')],
    'name': [Lower('profile__psn_username')],
}

MAX_LIMIT = 50
DEFAULT_LIMIT = 20


class GamePlayersAPIView(APIView):
    """List players of a game with filtering, sorting, and pagination."""
    authentication_classes = []
    permission_classes = []

    @method_decorator(ratelimit(key='ip', rate='60/m', method='GET', block=True))
    def get(self, request, np_communication_id):
        try:
            game = Game.objects.get(np_communication_id=np_communication_id)
        except Game.DoesNotExist:
            return Response({'error': 'Game not found.'}, status=404)

        qs = ProfileGame.objects.filter(
            game=game, hidden_flag=False, user_hidden=False
        ).select_related('profile')

        # Username search
        search = (request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(profile__psn_username__icontains=search)

        # Platinum filter
        if request.query_params.get('has_plat') == 'true':
            qs = qs.filter(has_plat=True)

        # Progress range
        min_progress = safe_int(request.query_params.get('min_progress', 0), 0)
        max_progress = safe_int(request.query_params.get('max_progress', 100), 100)
        min_progress = max(0, min(min_progress, 100))
        max_progress = max(0, min(max_progress, 100))
        if min_progress > 0:
            qs = qs.filter(progress__gte=min_progress)
        if max_progress < 100:
            qs = qs.filter(progress__lte=max_progress)

        # Monthly filter (last 30 days activity)
        if request.query_params.get('monthly') == 'true':
            qs = qs.filter(last_played_date_time__gte=timezone.now() - timedelta(days=30))

        # Sorting
        sort = request.query_params.get('sort', 'progress')
        order = SORT_OPTIONS.get(sort, SORT_OPTIONS['progress'])
        qs = qs.order_by(*order)

        # Pagination
        limit = min(safe_int(request.query_params.get('limit', DEFAULT_LIMIT), DEFAULT_LIMIT), MAX_LIMIT)
        offset = max(safe_int(request.query_params.get('offset', 0), 0), 0)
        total = qs.count()
        page = qs[offset:offset + limit]

        players = []
        for pg in page:
            p = pg.profile
            players.append({
                'psn_username': p.psn_username,
                'display_psn_username': p.display_psn_username or p.psn_username,
                'avatar_url': p.avatar_url or '',
                'flag': p.flag or '',
                'is_premium': p.user_is_premium,
                'progress': pg.progress,
                'has_plat': pg.has_plat,
                'earned_trophies_count': pg.earned_trophies_count,
                'most_recent_trophy_date': pg.most_recent_trophy_date.isoformat() if pg.most_recent_trophy_date else None,
                'profile_url': reverse('profile_detail', args=[p.psn_username]),
            })

        return Response({
            'count': total,
            'limit': limit,
            'offset': offset,
            'players': players,
        })
