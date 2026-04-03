import logging

from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import Game
from trophies.services.game_flag_service import GameFlagService

logger = logging.getLogger(__name__)


class GameFlagView(APIView):
    """Submit a community flag for a game."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='5/m', method='POST', block=True))
    def post(self, request, game_id):
        """
        POST /api/v1/games/<game_id>/flag/
        Body: { "flag_type": str, "details": str (optional) }
        """
        try:
            profile = getattr(request.user, 'profile', None)
            if not profile:
                return Response(
                    {'error': 'Profile not found.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if not profile.is_linked:
                return Response(
                    {'error': 'You must have a linked PSN profile to flag games.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            try:
                game = Game.objects.get(pk=game_id)
            except Game.DoesNotExist:
                return Response({'error': 'Game not found.'}, status=status.HTTP_404_NOT_FOUND)

            flag_type = request.data.get('flag_type')
            if not flag_type:
                return Response(
                    {'error': 'flag_type is required.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            details = request.data.get('details', '')

            flag, error = GameFlagService.submit_flag(game, profile, flag_type, details)
            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True, 'message': 'Flag submitted successfully. Thank you for helping improve our data!'})

        except Exception as e:
            logger.exception('Game flag submission error: %s', e)
            return Response(
                {'error': 'An unexpected error occurred.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
