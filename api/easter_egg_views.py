"""
REST API views for easter egg milestone claiming.

Server-side mapping ensures only known easter egg keys can award milestones.
The client sends an easter_egg_id (e.g. 'knife_landed') and the server maps
it to the corresponding milestone name. Unknown keys are rejected.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
import logging

logger = logging.getLogger(__name__)

# Server-side mapping: easter_egg_id -> milestone name.
# Adding a new easter egg just means adding one entry here.
EASTER_EGG_MILESTONES = {
    'knife_landed': 'Unboxed!',
}


class ClaimEasterEggView(APIView):
    """
    POST /api/v1/easter-eggs/claim/

    Claim a milestone for an easter egg discovery. Idempotent.

    Request body: { "easter_egg_id": "knife_landed" }

    Returns:
        200 { "awarded": true, "milestone_name": "...", "title_name": "..." }
        200 { "awarded": false, "already_earned": true }
        400 for invalid easter_egg_id
        403 if user has no linked profile
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='10/m', method='POST', block=True))
    def post(self, request):
        easter_egg_id = request.data.get('easter_egg_id')

        if not easter_egg_id or easter_egg_id not in EASTER_EGG_MILESTONES:
            return Response(
                {'error': 'Unknown easter egg.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'No linked PSN profile.'},
                status=http_status.HTTP_403_FORBIDDEN,
            )

        milestone_name = EASTER_EGG_MILESTONES[easter_egg_id]

        try:
            from trophies.services.milestone_service import award_manual_milestone
            milestone, created = award_manual_milestone(profile, milestone_name)

            if created:
                return Response({
                    'awarded': True,
                    'milestone_name': milestone.name,
                    'title_name': milestone.title.name if milestone.title else None,
                })
            else:
                return Response({
                    'awarded': False,
                    'already_earned': True,
                })

        except Exception:
            logger.exception(f"Error claiming easter egg '{easter_egg_id}'")
            return Response(
                {'error': 'Failed to process claim.'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
