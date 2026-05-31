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

    Roadmap-scoped endpoints have a `roadmap_id` URL kwarg; when present
    we load the roadmap and pass it to `has_roadmap_role` so trial users
    assigned to that roadmap (via Roadmap.trial_writers) pass the writer
    check. Without the roadmap context, trial users look like `none` to
    this gate — which is the right default for non-roadmap-scoped surfaces.
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
        roadmap = None
        roadmap_id = (view.kwargs or {}).get('roadmap_id')
        if roadmap_id is not None:
            from trophies.models import Roadmap
            roadmap = Roadmap.objects.filter(pk=roadmap_id).first()
            # Roadmap not found is fine — the view will 404 anyway;
            # let the global-role check decide the permission outcome.
        return profile.has_roadmap_role(min_role, roadmap)
