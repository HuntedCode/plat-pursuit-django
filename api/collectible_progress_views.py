"""Collectible progress API.

Tiny endpoints for marking a collectible item as found / not-found by
the logged-in viewer. Anonymous viewers track found state in
localStorage on the client; the server only knows about logged-in
progress.

Single endpoint with two methods:
- POST /api/v1/collectibles/items/<item_id>/progress/ → mark found
- DELETE /api/v1/collectibles/items/<item_id>/progress/ → unmark

Scoped to authenticated users — there's no value in storing anonymous
progress server-side, and the localStorage path keeps anon use frictionless.
"""
import logging

from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import RoadmapCollectibleItem, UserCollectibleProgress

logger = logging.getLogger('psn_api')


class CollectibleProgressView(APIView):
    """Mark / unmark a collectible item as found for the current viewer."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def _profile(self, request):
        # Profile is required to write a progress row. The IsAuthenticated
        # permission catches the auth case; this catches users who are
        # logged in but somehow lack a Profile (shouldn't happen in prod
        # but the fallback keeps callers from 500-ing).
        return getattr(request.user, 'profile', None)

    def post(self, request, item_id):
        profile = self._profile(request)
        if profile is None:
            return Response({'error': 'No profile.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            item = RoadmapCollectibleItem.objects.get(pk=item_id)
        except RoadmapCollectibleItem.DoesNotExist:
            return Response({'error': 'Item not found.'}, status=status.HTTP_404_NOT_FOUND)
        # get_or_create so retries are idempotent — a flaky network that
        # double-fires the toggle won't blow up on the unique_together.
        UserCollectibleProgress.objects.get_or_create(profile=profile, item=item)
        return Response({'item_id': item.id, 'found': True})

    def delete(self, request, item_id):
        profile = self._profile(request)
        if profile is None:
            return Response({'error': 'No profile.'}, status=status.HTTP_400_BAD_REQUEST)
        # No need to check the item exists — if no row exists for this
        # profile + item, the delete is a no-op with the same observable
        # outcome (item is unfound).
        UserCollectibleProgress.objects.filter(profile=profile, item_id=item_id).delete()
        return Response({'item_id': item_id, 'found': False})
