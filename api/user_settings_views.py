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

from trophies.services.profile_stats_service import update_profile_trophy_counts
from users.services.timezone_service import set_user_timezone

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

        # One writer for the field + its coupled side effects (confirmation stamp, recap
        # un-finalize) -- see users/services/timezone_service.py for the semantics.
        changed, recaps_reset = set_user_timezone(request.user, timezone_value)

        return Response({
            'success': True,
            'timezone': timezone_value,
            'recaps_reset': recaps_reset,
            'changed': changed,
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
    # One-shot education flags a surface may mark as seen (users.CustomUser.ui_flags keys).
    UI_FLAGS = ('career_explainer',)

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
            # hide_hiddens / hide_zeros feed the filter-respecting trophy-count denorms, so a
            # toggle must recompute them -- the Settings page path always did, and this path
            # silently didn't (stale totals until the nightly recalc).
            profile.refresh_from_db()
            update_profile_trophy_counts(profile)

        elif setting in self.USER_BOOL_SETTINGS:
            if not isinstance(value, bool):
                return Response({'error': 'Value must be a boolean.'}, status=http_status.HTTP_400_BAD_REQUEST)
            setattr(request.user, setting, value)
            request.user.save(update_fields=[setting])

        # Timezone setting (same validation as UpdateTimezoneAPIView, same one writer --
        # this branch used to skip the confirmation stamp, a third divergent behaviour)
        elif setting == 'user_timezone':
            if not isinstance(value, str) or value not in pytz.common_timezones_set:
                return Response({'error': 'Invalid timezone.'}, status=http_status.HTTP_400_BAD_REQUEST)
            set_user_timezone(request.user, value)

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

        # One-shot UI education flags (first-visit explainers). Write-only and sticky by
        # design: dismissing a hint is not something a user should have to manage later.
        elif setting == 'ui_flag':
            if not isinstance(value, str) or value not in self.UI_FLAGS:
                return Response({'error': 'Unknown UI flag.'}, status=http_status.HTTP_400_BAD_REQUEST)
            flags = request.user.ui_flags or {}
            flags[value] = True
            request.user.ui_flags = flags
            request.user.save(update_fields=['ui_flags'])

        else:
            return Response({'error': f'Unknown setting: {setting}'}, status=http_status.HTTP_400_BAD_REQUEST)

        return Response({'success': True, 'setting': setting, 'value': value})
