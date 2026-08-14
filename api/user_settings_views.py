"""
REST API views for user settings updates.
"""
import logging

import pytz
from django.utils import timezone
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
        # Stamped on EVERY successful save, including one that picks the same zone back. The point of the
        # stamp is not "what did you pick" -- the field above already holds that -- but "have you ever
        # answered", which `user_timezone` cannot express because it defaults to UTC and is non-null.
        # Confirming UTC is an answer, and a hunter who does it must not be asked again.
        request.user.timezone_confirmed_at = timezone.now()
        request.user.save(update_fields=['user_timezone', 'timezone_confirmed_at'])

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
            'changed': old_timezone != timezone_value,
        })


class UpdateQuickSettingsAPIView(APIView):
    """
    POST /api/v1/user/quick-settings/
    Body: {"setting": "hide_hiddens", "value": true}
      or: {"setting": "user_timezone", "value": "America/New_York"}
      or: {"setting": "browse_defaults", "value": {"page": "games", "filters": {"platform": ["PS5"]}}}

    Updates a single profile or user setting.
    Used by the dashboard Quick Settings module for auto-save.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication]

    PROFILE_BOOL_SETTINGS = {'hide_hiddens', 'hide_zeros'}
    USER_BOOL_SETTINGS = {'use_24hr_clock'}

    def post(self, request):
        setting = request.data.get('setting', '').strip()
        value = request.data.get('value')

        if not setting:
            return Response({'error': 'Setting name is required.'}, status=http_status.HTTP_400_BAD_REQUEST)

        # Boolean toggle settings
        if setting in self.PROFILE_BOOL_SETTINGS:
            if not isinstance(value, bool):
                return Response({'error': 'Value must be a boolean.'}, status=http_status.HTTP_400_BAD_REQUEST)
            profile = getattr(request.user, 'profile', None)
            if not profile:
                return Response({'error': 'Profile not found.'}, status=http_status.HTTP_404_NOT_FOUND)
            setattr(profile, setting, value)
            profile.save(update_fields=[setting])

        elif setting in self.USER_BOOL_SETTINGS:
            if not isinstance(value, bool):
                return Response({'error': 'Value must be a boolean.'}, status=http_status.HTTP_400_BAD_REQUEST)
            setattr(request.user, setting, value)
            request.user.save(update_fields=[setting])

        # Timezone setting (reuse validation from UpdateTimezoneAPIView)
        elif setting == 'user_timezone':
            if not isinstance(value, str) or value not in pytz.common_timezones_set:
                return Response({'error': 'Invalid timezone.'}, status=http_status.HTTP_400_BAD_REQUEST)
            old_tz = request.user.user_timezone or 'UTC'
            request.user.user_timezone = value
            request.user.save(update_fields=['user_timezone'])
            # Un-finalize recaps if timezone changed (they're rendered in the user's tz).
            if old_tz != value:
                profile = getattr(request.user, 'profile', None)
                if profile:
                    from trophies.models import MonthlyRecap
                    MonthlyRecap.objects.filter(profile=profile, is_finalized=True).update(is_finalized=False)

        # Browse page default filters (save/clear per page)
        elif setting == 'browse_defaults':
            if not isinstance(value, dict):
                return Response({'error': 'Value must be an object with page and filters.'}, status=http_status.HTTP_400_BAD_REQUEST)
            page = value.get('page', '')
            filters = value.get('filters', {})
            if page not in ('games', 'trophies', 'profiles'):
                return Response({'error': 'Invalid page.'}, status=http_status.HTTP_400_BAD_REQUEST)
            if not isinstance(filters, dict):
                return Response({'error': 'Filters must be an object.'}, status=http_status.HTTP_400_BAD_REQUEST)
            defaults = request.user.browse_defaults or {}
            if filters:
                defaults[page] = filters
            else:
                defaults.pop(page, None)
            request.user.browse_defaults = defaults
            request.user.save(update_fields=['browse_defaults'])

        # Premium theme setting
        elif setting == 'selected_theme':
            profile = getattr(request.user, 'profile', None)
            if not profile:
                return Response({'error': 'Profile not found.'}, status=http_status.HTTP_404_NOT_FOUND)
            if not profile.user_is_premium:
                return Response({'error': 'Premium required.'}, status=http_status.HTTP_403_FORBIDDEN)
            if not isinstance(value, str):
                return Response({'error': 'Value must be a string.'}, status=http_status.HTTP_400_BAD_REQUEST)
            value = value.strip()
            if value:
                from trophies.themes import GRADIENT_THEMES
                theme = GRADIENT_THEMES.get(value)
                if not theme:
                    return Response({'error': 'Invalid theme.'}, status=http_status.HTTP_400_BAD_REQUEST)
                if theme.get('requires_game_image'):
                    return Response({'error': 'Game art themes cannot be used as site theme.'}, status=http_status.HTTP_400_BAD_REQUEST)
            profile.selected_theme = value or None
            profile.save(update_fields=['selected_theme'])
            try:
                from trophies.services.dashboard_service import invalidate_dashboard_cache
                invalidate_dashboard_cache(profile.pk)
            except Exception:
                pass

        else:
            return Response({'error': f'Unknown setting: {setting}'}, status=http_status.HTTP_400_BAD_REQUEST)

        return Response({'success': True, 'setting': setting, 'value': value})
