"""Contract (Project) acceptance endpoint -- the acceptance gate.

A Project tier becomes claimable when the sync detects its completion (reached). XP is only
banked when the user accepts it. This view is the only request path that writes grants; it
delegates to contract_service.accept_contract / accept_contracts (idempotent, ledger-backed).
"""
import logging

from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status as http_status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import Contract
from trophies.services import contract_service

logger = logging.getLogger(__name__)


class AcceptContractView(APIView):
    """POST /api/v1/projects/accept/

    Body: {"slug": "<contract-slug>"} to accept one Project, or {"all": true} to accept
    every claimable Project. Banks the XP for each claimable tier and returns:
        200 {"granted": <int xp>, "accepted": [<slug>...], "claimable_count": <int>,
             "claim": {<the "what just happened" payload for the claim ceremony>}}
        403 if the user has no linked profile
        404 if the slug is not a live Project
        400 if neither slug nor all is given
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
    def post(self, request):
        profile = getattr(request.user, 'profile', None)
        if profile is None or not profile.is_linked:
            return Response(
                {'error': 'Link a PSN profile to accept Projects.'},
                status=http_status.HTTP_403_FORBIDDEN,
            )

        if request.data.get('all'):
            result = contract_service.claim(profile, all_claimable=True)
        else:
            slug = request.data.get('slug')
            if not slug:
                return Response(
                    {'error': 'Provide a Project slug or all=true.'},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )
            contract = Contract.objects.filter(slug=slug, is_live=True).first()
            if contract is None:
                return Response(
                    {'error': 'Project not found.'},
                    status=http_status.HTTP_404_NOT_FOUND,
                )
            result = contract_service.claim(profile, contract=contract)

        return Response({
            'granted': result['xp'],          # kept for back-compat with the current claim JS
            'accepted': result['accepted'],
            'claimable_count': contract_service.claimable_contracts(profile).count(),
            'claim': result,                  # the full payload the ceremony animates from
        })
