import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from notifications.models import DeviceToken

logger = logging.getLogger('psn_api')

VALID_PLATFORMS = {'ios', 'android'}


class DeviceTokenRegisterView(APIView):
    """
    POST /api/v1/device-tokens/
    Body: { token, platform }
    Registers (or refreshes) a push notification device token for the current user.
    Called on app launch and after login.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get('token', '').strip()
        platform = request.data.get('platform', '').strip().lower()

        if not token:
            return Response(
                {'error': 'token is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if platform not in VALID_PLATFORMS:
            return Response(
                {'error': f"platform must be one of: {', '.join(sorted(VALID_PLATFORMS))}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate token length (model max is 512)
        if len(token) > 512:
            return Response(
                {'error': 'Invalid token.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Try to get an existing token for this user and update it,
        # or create a new one. Do NOT reassign tokens belonging to other users
        # — that would allow push notifications to be redirected across accounts.
        existing = DeviceToken.objects.filter(token=token).first()
        if existing and existing.user_id != request.user.id:
            # Token belongs to a different user (e.g. old account on this device).
            # Remove the stale association and create a fresh record for the current user.
            existing.delete()
            existing = None

        if existing:
            existing.platform = platform
            existing.save(update_fields=['platform', 'last_used'])
            created = False
            obj = existing
        else:
            obj = DeviceToken.objects.create(
                user=request.user, token=token, platform=platform
            )
            created = True

        return Response(
            {'success': True, 'created': created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class DeviceTokenDeleteView(APIView):
    """
    DELETE /api/v1/device-tokens/<token>/
    Removes a specific device token. Called on logout.
    Only deletes tokens belonging to the current user.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, token):
        deleted_count, _ = DeviceToken.objects.filter(
            user=request.user, token=token
        ).delete()
        if deleted_count == 0:
            return Response(
                {'error': 'Token not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({'success': True})
