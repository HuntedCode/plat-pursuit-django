"""
REST API views for easter egg milestone claiming.

Server-side mapping ensures only known easter egg keys can award milestones.
The client sends an easter_egg_id (e.g. 'knife_landed') and the server maps
it to the corresponding milestone name. Unknown keys are rejected.

The RollEasterEggView performs server-side probability rolls and issues
one-time claim tokens via Django's cache framework. The ClaimEasterEggView
verifies and consumes these tokens before awarding milestones.
"""
import random

from django.core.cache import cache
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status as http_status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
import logging

logger = logging.getLogger(__name__)

# Server-side mapping: easter_egg_id -> milestone name.
# Adding a new easter egg just means adding one entry here.
EASTER_EGG_MILESTONES = {
    'knife_landed': 'Unboxed!',
}

# Server-side probability config per easter egg.
# Probabilities were previously client-side in reel-spinner.js.
EASTER_EGG_ROLL_CHANCES = {
    'knife_landed': {
        'land_chance': 0.001,    # 1-in-1000: knife is the winner
        'appear_chance': 0.01,   # 1-in-100: knife shows up in reel (when not landing)
    },
}

# Cache key prefix and TTL for one-time claim tokens
ROLL_TOKEN_PREFIX = 'easter_roll'
ROLL_TOKEN_TTL = 300  # 5 minutes


class RollEasterEggView(APIView):
    """
    POST /api/v1/easter-eggs/roll/

    Server-side probability roll for easter eggs. Returns whether the easter
    egg should appear and/or land. If landed, a one-time claim token is stored
    in cache for the subsequent claim request.

    Request body: { "easter_egg_id": "knife_landed" }

    Returns:
        200 { "appears": bool, "landed": bool }
        400 for unknown easter_egg_id
        403 if user has no linked profile
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='20/m', method='POST', block=True))
    def post(self, request):
        easter_egg_id = request.data.get('easter_egg_id')

        if not easter_egg_id or easter_egg_id not in EASTER_EGG_ROLL_CHANCES:
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

        chances = EASTER_EGG_ROLL_CHANCES[easter_egg_id]
        landed = random.random() < chances['land_chance']
        appears = landed or random.random() < chances['appear_chance']

        if landed:
            cache_key = f'{ROLL_TOKEN_PREFIX}:{easter_egg_id}:{request.user.id}'
            cache.set(cache_key, True, timeout=ROLL_TOKEN_TTL)

        return Response({'appears': appears, 'landed': landed})


class ClaimEasterEggView(APIView):
    """
    POST /api/v1/easter-eggs/claim/

    Claim a milestone for an easter egg discovery. Requires a valid one-time
    roll token from RollEasterEggView. Idempotent once awarded.

    Request body: { "easter_egg_id": "knife_landed" }

    Returns:
        200 { "awarded": true, "milestone_name": "...", "title_name": "..." }
        200 { "awarded": false, "already_earned": true }
        400 for invalid easter_egg_id
        403 if user has no linked profile or no valid roll token
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

        # Verify and consume the one-time roll token atomically.
        # cache.delete() returns True if the key existed (Django 4.0+ / Redis).
        # This avoids a race condition between get() and delete().
        cache_key = f'{ROLL_TOKEN_PREFIX}:{easter_egg_id}:{request.user.id}'
        if not cache.delete(cache_key):
            return Response(
                {'error': 'No valid roll found.'},
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
