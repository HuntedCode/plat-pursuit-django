"""
REST API view for easter egg probability rolls.

The client sends an easter_egg_id (e.g. 'knife_landed') and the server decides
whether the egg appears and/or lands, keeping the odds off the client. Unknown
keys are rejected.

The find is its own reward: the spinner celebrates the landing client-side. (It
used to also award a hidden 'Unboxed!' milestone; that grant retired with the
legacy milestone engine -- existing holders keep the title.)
"""
import random

from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status as http_status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
import logging

logger = logging.getLogger(__name__)

# Server-side probability config per easter egg.
# Probabilities were previously client-side in reel-spinner.js.
EASTER_EGG_ROLL_CHANCES = {
    'knife_landed': {
        'land_chance': 0.001,    # 1-in-1000: knife is the winner
        'appear_chance': 0.01,   # 1-in-100: knife shows up in reel (when not landing)
    },
}


class RollEasterEggView(APIView):
    """
    POST /api/v1/easter-eggs/roll/

    Server-side probability roll for easter eggs. Returns whether the easter
    egg should appear and/or land; the client handles the celebration.

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

        return Response({'appears': appears, 'landed': landed})
