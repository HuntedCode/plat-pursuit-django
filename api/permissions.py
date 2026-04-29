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


class IsRoadmapAuthor(BasePermission):
    """Allow users with at least the configured min_roadmap_role.

    Reads `view.min_roadmap_role` (default 'writer') so individual endpoints
    can require a stronger role (e.g. publisher for status toggle). Per-action
    fine-grained checks (writer-can-edit-only-own-section) live in the merge
    service, not here; this gate is a coarse "is this user an author at all".
    """
    message = "Roadmap author access required."

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        profile = getattr(user, 'profile', None)
        if profile is None:
            return False
        min_role = getattr(view, 'min_roadmap_role', 'writer')
        return profile.has_roadmap_role(min_role)
