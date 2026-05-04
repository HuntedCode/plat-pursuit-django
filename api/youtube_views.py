"""
YouTube oEmbed proxy used by the roadmap editor's live attribution preview.

Login + rate-limited because each request triggers an outbound HTTP call
to YouTube. Anonymous proxying would let the endpoint be abused as a
free YouTube metadata API.
"""
import logging

from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.services.youtube_oembed_service import fetch_attribution

logger = logging.getLogger(__name__)


class YouTubeAttributionLookupView(APIView):
    """Resolve a YouTube URL to its channel attribution.

    GET /api/youtube/attribution-lookup/?url=<youtube_url>
    Returns: {"channel_name": str, "channel_url": str}
    Empty strings if the URL is unrecognized or the lookup fails — the
    editor uses that to show "no attribution available" without errors.
    """
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='30/m', method='GET', block=True))
    def get(self, request):
        url = request.query_params.get('url', '').strip()
        if not url:
            return Response(
                {'error': 'url parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = fetch_attribution(url)
        if not result:
            return Response({'channel_name': '', 'channel_url': ''})
        return Response(result)
