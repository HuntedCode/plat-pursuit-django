"""
REST API views for the Welcome Tour tutorial system.
"""
import logging

from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication

from core.services.tracking import track_site_event

logger = logging.getLogger(__name__)


class WelcomeTourDismissAPIView(APIView):
    """
    POST /api/v1/tutorial/welcome/dismiss/

    Marks the Welcome Tour as completed or skipped for the authenticated
    user's profile.  Accepts an optional ``action`` ('complete' or 'skip')
    and ``last_step`` (1-4) for analytics.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication]

    def post(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'No linked profile.'},
                status=http_status.HTTP_404_NOT_FOUND,
            )

        action = request.data.get('action', 'complete')
        if action not in ('complete', 'skip'):
            action = 'complete'

        last_step = request.data.get('last_step', 4)

        profile.tour_completed_at = timezone.now()
        profile.save(update_fields=['tour_completed_at'])

        track_site_event(f'welcome_tour_{action}', f'step_{last_step}', request)

        return Response({'success': True})


class GameDetailTourDismissAPIView(APIView):
    """
    POST /api/v1/tutorial/game-detail/dismiss/

    Marks the Game Detail coach-marks tour as completed or skipped for
    the authenticated user's profile.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication]

    def post(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'No linked profile.'},
                status=http_status.HTTP_404_NOT_FOUND,
            )

        action = request.data.get('action', 'complete')
        if action not in ('complete', 'skip'):
            action = 'complete'

        last_step = request.data.get('last_step', 5)

        profile.game_detail_tour_completed_at = timezone.now()
        profile.save(update_fields=['game_detail_tour_completed_at'])

        track_site_event(f'game_detail_tour_{action}', f'step_{last_step}', request)

        return Response({'success': True})
