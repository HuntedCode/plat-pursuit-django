import logging

from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.views.sync_views import _get_queue_position

logger = logging.getLogger('psn_api')


class MobileSyncStatusView(APIView):
    """
    GET /api/v1/mobile/sync/status/
    Returns the authenticated user's profile sync status.
    Token-auth equivalent of ProfileSyncStatusView (which uses LoginRequiredMixin).
    """
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=False))
    def get(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'No linked profile.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = {
            'sync_status': profile.sync_status,
            'sync_progress': profile.sync_progress_value,
            'sync_target': profile.sync_progress_target,
            'sync_percentage': profile.sync_percentage,
            'seconds_to_next_sync': profile.get_seconds_to_next_sync(),
        }

        if profile.sync_status == 'syncing':
            data['queue_position'] = _get_queue_position(profile.id)

        return Response(data)


class MobileTriggerSyncView(APIView):
    """
    POST /api/v1/mobile/sync/trigger/
    Triggers a manual sync for the authenticated user's profile.
    Token-auth equivalent of TriggerSyncView (which uses LoginRequiredMixin).
    """
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='3/m', method='POST', block=True))
    def post(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'No linked profile.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not profile.is_linked:
            return Response(
                {'error': 'PSN account is not linked.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_syncing = profile.attempt_sync()
        if not is_syncing:
            seconds_left = profile.get_seconds_to_next_sync()
            return Response(
                {'error': f'Sync cooldown active. Try again in {seconds_left} seconds.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        return Response({'success': True, 'message': 'Sync started.'})
