"""
REST API views for user settings updates.
"""
import logging

import pytz
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication

logger = logging.getLogger(__name__)


class UpdateTimezoneAPIView(APIView):
    """
    POST /api/v1/user/timezone/
    Body: {"timezone": "America/New_York"}

    Updates the authenticated user's timezone preference.
    When the timezone actually changes, un-finalizes all monthly recaps
    so they regenerate with the new timezone boundaries on next access.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication]

    def post(self, request):
        timezone_value = request.data.get('timezone', '').strip()

        if not timezone_value:
            return Response(
                {'error': 'Timezone is required.'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        if timezone_value not in pytz.common_timezones_set:
            return Response(
                {'error': 'Invalid timezone.'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        old_timezone = request.user.user_timezone or 'UTC'
        request.user.user_timezone = timezone_value
        request.user.save(update_fields=['user_timezone'])

        recaps_reset = 0
        if old_timezone != timezone_value:
            profile = getattr(request.user, 'profile', None)
            if profile:
                from trophies.models import MonthlyRecap
                recaps_reset = MonthlyRecap.objects.filter(
                    profile=profile,
                    is_finalized=True,
                ).update(is_finalized=False)
                if recaps_reset:
                    logger.info(
                        "Un-finalized %d recaps for profile %s after timezone change: %s -> %s",
                        recaps_reset, profile.id, old_timezone, timezone_value,
                    )

        return Response({
            'success': True,
            'timezone': timezone_value,
            'recaps_reset': recaps_reset,
        })
