"""
REST API views for user settings updates.
"""
import logging
from datetime import timedelta, timezone as dt_timezone

import pytz
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core import whats_new
from trophies.services import new_contracts_modal
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
    UI_FLAGS = ('career_explainer', 'launch_welcome')

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

        # What's New: the id of the newest entry this user has dismissed. Its own branch rather than a
        # value on `ui_flag` above, because that one is documented as sticky booleans and this is a
        # MOVING marker -- every new entry overwrites it. Sharing the branch would have meant one of the
        # two behaviours going undocumented in the place someone reads to learn what the flag means.
        #
        # Validated against the shipped entries, not stored as given: an arbitrary string here would let
        # a caller park a value no entry will ever match, permanently suppressing the modal for that
        # account with nothing in the UI to undo it.
        elif setting == 'whats_new_seen':
            if not isinstance(value, str) or whats_new.by_id(value) is None:
                return Response({'error': 'Unknown entry.'}, status=http_status.HTTP_400_BAD_REQUEST)
            flags = request.user.ui_flags or {}
            flags['whats_new_seen'] = value
            request.user.ui_flags = flags
            request.user.save(update_fields=['ui_flags'])

        # The Career new-contracts marker: the newest `announced_at` this hunter has been SHOWN.
        # Its own branch for the same reason `whats_new_seen` has one -- `ui_flag` is documented as
        # sticky booleans and this is a moving stamp.
        #
        # BOUNDED IN BOTH DIRECTIONS AND MONOTONIC, because every way this value can be wrong is a
        # way to break the account permanently, and nothing in the UI can undo any of them:
        #   future  -- suppresses the modal forever
        #   ancient -- defeats the no-marker 14-day floor, so every /career/ render for that account
        #              sorts and materialises the entire announced catalogue. One POST, permanent
        #              per-account load amplifier. Floored a year back: nothing legitimate predates
        #              the feature, and a hunter away that long is served by the board, not a modal
        #   backwards -- a stale tab dismissed after a newer visit rewinds the marker and re-shows a
        #              wave already read, so the stored value only ever moves forward
        elif setting == 'contracts_seen':
            if not isinstance(value, str):
                return Response({'error': 'Expected an ISO timestamp.'},
                                status=http_status.HTTP_400_BAD_REQUEST)
            # `parse_datetime` returns None when the REGEX misses, but RAISES on a well-formed
            # string with impossible values -- '2026-02-31T00:00:00', hour 25, a +99:00 offset.
            # Only the None half was handled, so those went out as a 500 rather than this 400.
            try:
                stamp = parse_datetime(value)
            except ValueError:
                stamp = None
            if stamp is None:
                return Response({'error': 'Expected an ISO timestamp.'},
                                status=http_status.HTTP_400_BAD_REQUEST)
            if timezone.is_naive(stamp):
                # UTC explicitly, not the request's activated zone. Django 5's make_aware is a bare
                # `replace(tzinfo=...)`, and the timezone middleware activates a pytz object -- whose
                # bare offset is LMT, so a naive value would land minutes off (56 for New York).
                stamp = stamp.replace(tzinfo=dt_timezone.utc)
            now = timezone.now()
            stamp = min(max(stamp, now - timedelta(days=365)), now)
            flags = request.user.ui_flags or {}
            previous = new_contracts_modal.seen_marker(request.user)
            if previous is not None:
                stamp = max(stamp, previous)
            flags['contracts_seen'] = stamp.isoformat()
            request.user.ui_flags = flags
            request.user.save(update_fields=['ui_flags'])
            # The STORED value, not the caller's. A client that sent a future or rewound stamp was
            # being told it was saved as sent while something else was written.
            return Response({'success': True, 'setting': setting, 'value': flags['contracts_seen']})

        else:
            return Response({'error': f'Unknown setting: {setting}'}, status=http_status.HTTP_400_BAD_REQUEST)

        return Response({'success': True, 'setting': setting, 'value': value})
