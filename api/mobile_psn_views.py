import logging
import time

from django.utils import timezone
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import Profile
from trophies.psn_manager import PSNManager
from trophies.services.badge_service import initial_badge_check
from trophies.services.verification_service import VerificationService
from .serializers import GenerateCodeSerializer

logger = logging.getLogger('psn_api')


class MobilePSNGenerateCodeView(APIView):
    """
    POST /api/v1/mobile/psn/generate-code/
    Body: { psn_username }
    Returns: { code, message }

    Mobile-friendly version of GenerateCodeView — keyed to request.user
    instead of requiring a Discord bot token. Creates the Profile record
    if it doesn't exist yet and triggers an initial sync.
    """
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='5/m', method='POST', block=True))
    def post(self, request):
        # If user already has a linked profile, reject to prevent re-linking confusion
        existing_profile = getattr(request.user, 'profile', None)
        if existing_profile and existing_profile.is_linked:
            return Response(
                {'error': 'Your account already has a PSN profile linked.'},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = GenerateCodeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        psn_username = serializer.validated_data['psn_username']

        # Check if this PSN username is already claimed by another user.
        # This covers both fully-linked profiles AND unverified profiles that
        # another user has already started linking (user is set, is_linked may be False).
        existing_profile = Profile.objects.filter(psn_username=psn_username).first()
        if existing_profile:
            if existing_profile.user and existing_profile.user != request.user:
                # Belongs to a different user — reject regardless of link status.
                return Response(
                    {'error': 'This PSN account is already associated with another user.'},
                    status=status.HTTP_409_CONFLICT,
                )
            if existing_profile.is_linked and existing_profile.user == request.user:
                # Already fully linked to THIS user — no need to re-link.
                return Response(
                    {'error': 'This PSN account is already linked to your account.'},
                    status=status.HTTP_409_CONFLICT,
                )

        profile, created = Profile.objects.get_or_create(
            psn_username=psn_username,
            defaults={'user': request.user},
        )

        # Associate the requesting user if this is an unclaimed orphaned profile
        if not profile.user:
            profile.user = request.user
            profile.save(update_fields=['user'])

        profile.generate_verification_code()

        if created:
            PSNManager.initial_sync(profile)
        else:
            profile.attempt_sync()

        return Response({
            'success': True,
            'code': profile.verification_code,
            'message': (
                f"Add '{profile.verification_code}' to your PSN 'About Me' section, "
                f"then tap Verify below."
            ),
        })


class MobilePSNVerifyView(APIView):
    """
    POST /api/v1/mobile/psn/verify/
    Body: { psn_username }
    Returns: { success, message }

    Mobile-friendly version of VerifyView — verifies the PSN About Me code
    and links the profile to the current user (no Discord ID involved).
    """
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='3/m', method='POST', block=True))
    def post(self, request):
        psn_username = request.data.get('psn_username', '').strip().lower()
        if not psn_username:
            return Response(
                {'error': 'psn_username is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            profile = Profile.objects.get(psn_username=psn_username)
        except Profile.DoesNotExist:
            return Response(
                {'error': 'Profile not found. Please generate a code first.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Ensure this profile belongs to the requesting user
        if profile.user and profile.user != request.user:
            return Response(
                {'error': 'This PSN account is linked to a different user.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            start_time = timezone.now()
            timeout_seconds = 15
            poll_interval_seconds = 1

            is_syncing = profile.attempt_sync()
            if not is_syncing:
                PSNManager.sync_profile_data(profile)

            while (timezone.now() - start_time).total_seconds() < timeout_seconds:
                profile.refresh_from_db()
                if profile.last_synced and profile.last_synced > start_time:
                    break
                time.sleep(poll_interval_seconds)

            if not profile.last_synced or profile.last_synced <= start_time:
                return Response(
                    {'success': False, 'message': 'Sync timed out. Please try again.'},
                    status=status.HTTP_408_REQUEST_TIMEOUT,
                )

            if profile.verify_code(profile.about_me):
                # Delegate to VerificationService — canonical linking path that also
                # updates premium status and checks PSN-linking milestones.
                VerificationService.link_profile_to_user(profile, request.user)

                initial_badge_check(profile)

                return Response({'success': True, 'message': 'PSN account verified and linked!'})
            else:
                return Response({
                    'success': False,
                    'message': (
                        'Verification failed. Make sure the code is saved in your '
                        'PSN About Me section and try again.'
                    ),
                })

        except ValueError as e:
            return Response(
                {'success': False, 'message': str(e)},
                status=status.HTTP_409_CONFLICT,
            )
        except Exception:
            logger.exception(f"Mobile PSN verify error for {psn_username}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class MobilePSNStatusView(APIView):
    """
    GET /api/v1/mobile/psn/status/
    Returns the current user's PSN link status and basic profile info.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile or not profile.is_linked:
            return Response({'is_linked': False, 'psn_username': None})

        return Response({
            'is_linked': True,
            'psn_username': profile.display_psn_username,
            'avatar_url': profile.avatar_url,
            'last_synced': profile.last_synced,
            'sync_status': profile.sync_status,
        })
