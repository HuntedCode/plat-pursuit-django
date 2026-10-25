"""Celebration moments: the one write the browser makes (trophies/services/moment_service.py)."""
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status as http_status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.services.moment_service import QUEUE_CAP, mark_played


class MomentsPlayedAPIView(APIView):
    """POST {"ids": [..]}: these moments have been on screen. Only the signed-in hunter's own, only once, so
    a moment seen on one device never replays on another. Returns {"played": n}."""
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request):
        ids = request.data.get('ids')
        if not isinstance(ids, list) or not ids or len(ids) > QUEUE_CAP \
                or not all(isinstance(i, int) and not isinstance(i, bool) and 0 < i < 2 ** 63 for i in ids):
            return Response({'error': 'ids must be a short list of moment ids.'}, status=http_status.HTTP_400_BAD_REQUEST)
        profile = getattr(request.user, 'profile', None) if hasattr(request.user, 'profile') else None
        if profile is None:
            return Response({'played': 0})
        return Response({'played': mark_played(profile, ids)})
