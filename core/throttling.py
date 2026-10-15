"""DRF throttling keyed on the visitor, not on whatever X-Forwarded-For says.

DRF's default `get_ident` returns the raw X-Forwarded-For header for anonymous callers, which the
caller writes: a fresh value per request is a fresh bucket, so the anonymous API had no throttle.
"""
from rest_framework.throttling import UserRateThrottle

from core.client_ip import client_ip


class VisitorUserRateThrottle(UserRateThrottle):
    """`UserRateThrottle` (signed-in callers by user id), with anonymous callers by real IP."""

    def get_ident(self, request):
        return client_ip(request)
