"""
REST API views for tracking events.
Provides endpoints for frontend to log site events.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from core.services.tracking import track_site_event
import logging

logger = logging.getLogger(__name__)


class TrackSiteEventView(APIView):
    """
    POST /api/v1/tracking/site-event/

    Log a site event (share card download, recap download, etc.)

    Request body:
        {
            "event_type": "share_card_download",
            "object_id": "12345"
        }

    Returns: { "success": true }
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='100/m', method='POST', block=True))
    def post(self, request):
        event_type = request.data.get('event_type')
        object_id = request.data.get('object_id')

        if not event_type or not object_id:
            return Response(
                {'error': 'event_type and object_id are required'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        # Validate event types
        valid_event_types = [
            'share_card_download',
            'recap_page_view',
            'recap_share_generate',
            'recap_image_download',
            'guide_visit',
        ]

        if event_type not in valid_event_types:
            return Response(
                {'error': f'Invalid event_type. Must be one of: {", ".join(valid_event_types)}'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        try:
            track_site_event(event_type, object_id, request)
            return Response({'success': True})
        except Exception as e:
            logger.exception(f"[TRACKING-API] Error tracking event {event_type}: {e}")
            return Response(
                {'error': 'Failed to track event'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )
