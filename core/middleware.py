"""
Analytics session tracking middleware.

Manages analytics session lifecycle:
- Creates session cookie for new visitors
- Refreshes session on activity
- Stores session_id in request for tracking service
"""
from django.conf import settings

from core.services.session_tracking import (
    get_or_create_session,
    SESSION_COOKIE_NAME,
    SESSION_TIMEOUT,
)


class AnalyticsSessionMiddleware:
    """
    Middleware to manage analytics sessions.

    Creates/updates session cookie and stores session_id on request.
    Runs on every request to ensure session tracking is active.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Get or create analytics session (Redis-first, fast)
        session_id = get_or_create_session(request)

        # Store session_id on request for use by tracking service
        request.analytics_session_id = session_id

        # Process request
        response = self.get_response(request)

        # Set/refresh session cookie on response
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_id,
            max_age=SESSION_TIMEOUT,
            httponly=True,
            secure=not settings.DEBUG,
            samesite='Lax',  # CSRF protection
        )

        return response
