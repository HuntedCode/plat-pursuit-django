import logging

from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import Profile
from .serializers import ProfileSerializer

logger = logging.getLogger('psn_api')


class MobileMyProfileView(APIView):
    """
    GET /api/v1/mobile/me/
    Returns the authenticated user's own profile summary.
    No Discord bot key required — uses request.user directly.
    """
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=False))
    def get(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'has_psn_linked': False, 'message': 'No PSN profile linked.'},
                status=status.HTTP_200_OK,
            )

        serializer = ProfileSerializer(profile)
        return Response({
            'has_psn_linked': profile.is_linked,
            'profile': serializer.data,
        })


class MobileProfileView(APIView):
    """
    GET /api/v1/mobile/profiles/<psn_username>/
    Returns a public profile summary by PSN username.
    No Discord bot key required — any authenticated user can view any public profile.
    """
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='30/m', method='GET', block=False))
    def get(self, request, psn_username):
        try:
            profile = Profile.objects.get(psn_username__iexact=psn_username)
        except Profile.DoesNotExist:
            return Response(
                {'error': 'Profile not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ProfileSerializer(profile)
        return Response({
            'has_psn_linked': profile.is_linked,
            'profile': serializer.data,
        })
