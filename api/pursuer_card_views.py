"""REST API view for refreshing the Pursuer Card.

Used by the forge (static/js/pursuer-card-forge.js) after a *live* sync completes: the card on
the page is still server-rendered with pre-sync data, so we fetch a freshly-built one and swap it
in before playing the re-forge -- so the new platinum count + any new covers are actually shown
(and the client can detect + slot in the new platinum). The catch-up path (a sync that finished
while the user was away) needs no fetch: that page already rendered fresh.
"""
import logging

from django.http import HttpResponse
from django.template.loader import render_to_string
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication

from trophies.services import pursuer_card_service

logger = logging.getLogger(__name__)


class PursuerCardRefreshView(APIView):
    """GET /api/v1/pursuer-card/ -- the authenticated user's Pursuer Card, freshly built + rendered.

    Returns the rendered `_pursuer_card.html` fragment (204 when there's no linked profile or the
    card degrades). Whale-safe: build_pursuer_card uses bounded slices + a single batched query.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication]

    def get(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return HttpResponse(status=204)
        try:
            card = pursuer_card_service.build_pursuer_card(profile)
        except Exception:
            logger.exception('Pursuer card refresh failed for profile %s', getattr(profile, 'id', '?'))
            return HttpResponse(status=204)
        if not card:
            return HttpResponse(status=204)
        html = render_to_string('partials/components/_pursuer_card.html', {'card': card}, request=request)
        return HttpResponse(html)
