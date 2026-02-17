"""
Custom DRF permission classes for API views.
"""
from rest_framework.permissions import BasePermission
from django.conf import settings


class IsDiscordBot(BasePermission):
    """
    Only allows requests where the DRF token matches BOT_API_KEY.

    Prevents regular authenticated users from accessing bot-only
    endpoints (verify, unlink, refresh, etc.) even if they have
    a valid DRF token.
    """
    message = "Bot authentication required."

    def has_permission(self, request, view):
        if not request.auth or not hasattr(request.auth, 'key'):
            return False
        return request.auth.key == settings.BOT_API_KEY
