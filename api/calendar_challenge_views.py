"""
Platinum Calendar Challenge API views.

Handles REST endpoints for Platinum Calendar Challenges: CRUD, day detail
(all platinums for a specific calendar day), and share card endpoints.
"""
import logging

import pytz
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import Challenge, EarnedTrophy, CALENDAR_DAYS_PER_MONTH
from trophies.services.challenge_service import create_calendar_challenge

logger = logging.getLogger('psn_api')


# ─── Helpers ─────────────────────────────────────────────────────────────────────

def _get_profile_or_error(request):
    """Return (profile, None) or (None, Response) for error."""
    profile = getattr(request.user, 'profile', None)
    if not profile:
        return None, Response(
            {'error': 'Linked profile required.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return profile, None


def _get_owned_challenge(challenge_id, profile, allow_deleted=False):
    """Return (challenge, None) or (None, Response) for error."""
    try:
        filters = {'id': challenge_id, 'challenge_type': 'calendar'}
        if not allow_deleted:
            filters['is_deleted'] = False
        challenge = Challenge.objects.get(**filters)
    except Challenge.DoesNotExist:
        return None, Response(
            {'error': 'Challenge not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    if challenge.profile_id != profile.id:
        return None, Response(
            {'error': 'Not your challenge.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return challenge, None


def _serialize_calendar_challenge(challenge):
    """Serialize a Calendar Challenge to a dict."""
    return {
        'id': challenge.id,
        'challenge_type': challenge.challenge_type,
        'name': challenge.name,
        'description': challenge.description,
        'total_items': challenge.total_items,
        'filled_count': challenge.filled_count,
        'completed_count': challenge.completed_count,
        'progress_percentage': challenge.progress_percentage,
        'view_count': challenge.view_count,
        'is_complete': challenge.is_complete,
        'completed_at': challenge.completed_at.isoformat() if challenge.completed_at else None,
        'created_at': challenge.created_at.isoformat(),
        'updated_at': challenge.updated_at.isoformat(),
        'author': {
            'psn_username': challenge.profile.psn_username,
            'avatar_url': challenge.profile.avatar_url or '',
        },
    }


# ─── API Views ───────────────────────────────────────────────────────────────────

class CalendarChallengeCreateAPIView(APIView):
    """Create a new Platinum Calendar Challenge with auto-backfill."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='10/h', method='POST', block=True))
    def post(self, request):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            name = (request.data.get('name') or 'My Platinum Calendar').strip()[:75]
            if not name:
                name = 'My Platinum Calendar'

            challenge = create_calendar_challenge(profile, name=name)
            return Response(
                _serialize_calendar_challenge(challenge),
                status=status.HTTP_201_CREATED,
            )

        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        except Exception:
            logger.exception("Calendar Challenge create error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CalendarChallengeDetailAPIView(APIView):
    """Get calendar challenge details."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []

    @method_decorator(ratelimit(key='ip', rate='60/m', method='GET', block=True))
    def get(self, request, challenge_id):
        try:
            try:
                challenge = Challenge.objects.select_related('profile').get(
                    id=challenge_id, is_deleted=False, challenge_type='calendar',
                )
            except Challenge.DoesNotExist:
                return Response(
                    {'error': 'Challenge not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            return Response(_serialize_calendar_challenge(challenge))

        except Exception:
            logger.exception("Calendar Challenge detail error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CalendarChallengeUpdateAPIView(APIView):
    """Update calendar challenge name/description."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='30/m', method='PATCH', block=True))
    def patch(self, request, challenge_id):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            challenge, error = _get_owned_challenge(challenge_id, profile)
            if error:
                return error

            update_fields = ['updated_at']

            name = request.data.get('name')
            if name is not None:
                name = name.strip()[:75]
                if name:
                    challenge.name = name
                    update_fields.append('name')

            description = request.data.get('description')
            if description is not None:
                challenge.description = description.strip()[:2000]
                update_fields.append('description')

            if len(update_fields) > 1:
                challenge.save(update_fields=update_fields)

            return Response(_serialize_calendar_challenge(challenge))

        except Exception:
            logger.exception("Calendar Challenge update error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CalendarChallengeDeleteAPIView(APIView):
    """Soft delete a calendar challenge."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='10/m', method='DELETE', block=True))
    def delete(self, request, challenge_id):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            challenge, error = _get_owned_challenge(challenge_id, profile)
            if error:
                return error

            challenge.soft_delete()
            return Response({'success': True})

        except Exception:
            logger.exception("Calendar Challenge delete error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CalendarDayDetailAPIView(APIView):
    """
    Get all platinums earned on a specific calendar day for a challenge.
    Returns game info, earn date, and game page URL for each platinum.
    Public endpoint (anyone can view a non-deleted challenge's day details).
    """
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []

    @method_decorator(ratelimit(key='ip', rate='120/m', method='GET', block=True))
    def get(self, request, challenge_id, month, day):
        try:
            # Validate month/day
            if month < 1 or month > 12:
                return Response(
                    {'error': 'Invalid month.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            max_day = CALENDAR_DAYS_PER_MONTH.get(month, 0)
            if day < 1 or day > max_day:
                return Response(
                    {'error': 'Invalid day for this month.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if month == 2 and day == 29:
                return Response(
                    {'error': 'Feb 29 is not part of the calendar challenge.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Fetch challenge
            try:
                challenge = Challenge.objects.select_related('profile').get(
                    id=challenge_id, is_deleted=False, challenge_type='calendar',
                )
            except Challenge.DoesNotExist:
                return Response(
                    {'error': 'Challenge not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Resolve user timezone (shared helper)
            from trophies.services.challenge_service import _get_user_tz
            user_tz = _get_user_tz(challenge.profile)

            # Find ALL platinums earned on this (month, day) in user's timezone (no shovelware, not hidden)
            platinum_trophies = EarnedTrophy.objects.filter(
                profile=challenge.profile,
                trophy__trophy_type='platinum',
                earned=True,
                earned_date_time__isnull=False,
                trophy__game__is_shovelware=False,
                user_hidden=False,
            ).select_related('trophy__game').order_by('earned_date_time')

            platinums = []
            for et in platinum_trophies:
                local_dt = et.earned_date_time.astimezone(user_tz)
                if local_dt.month == month and local_dt.day == day:
                    game = et.trophy.game
                    platinums.append({
                        'game_id': game.id,
                        'title_name': game.title_name,
                        'title_icon_url': game.title_icon_url or game.title_image or '',
                        'title_platform': game.title_platform or [],
                        'earned_date_time': et.earned_date_time.isoformat(),
                        'earned_year': local_dt.year,
                        'game_url': f'/games/{game.np_communication_id}/',
                    })

            return Response({
                'month': month,
                'day': day,
                'platinums': platinums,
            })

        except Exception:
            logger.exception("Calendar day detail error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
