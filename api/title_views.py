"""
REST API views for title equipping.
"""
import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication

from trophies.models import UserTitle

logger = logging.getLogger(__name__)


class EquipTitleAPIView(APIView):
    """
    POST /api/v1/equip-title/
    Body: {"title_id": int | null}

    Equips the given title for the authenticated user, or unequips if null.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication]

    def post(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'Link a PSN account first.'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        title_id = request.data.get('title_id')

        if title_id is None:
            # Unequip: clear all displayed titles
            profile.user_titles.update(is_displayed=False)
            return Response({'success': True, 'title_name': None})

        # Validate the user owns this title
        try:
            user_title = UserTitle.objects.get(
                profile=profile, title_id=title_id
            )
        except UserTitle.DoesNotExist:
            return Response(
                {'error': 'You have not earned this title.'},
                status=http_status.HTTP_403_FORBIDDEN
            )

        # Equip: clear others, set this one
        profile.user_titles.update(is_displayed=False)
        user_title.is_displayed = True
        user_title.save(update_fields=['is_displayed'])

        return Response({
            'success': True,
            'title_name': user_title.title.name,
        })
