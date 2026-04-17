"""
Profile Showcase API endpoints.

Four endpoints for managing a user's Steam-style profile showcases:
- POST /api/v1/profile/showcases/                 add a showcase
- DELETE /api/v1/profile/showcases/<slug>/        remove a showcase
- POST /api/v1/profile/showcases/reorder/         reorder active showcases
- POST /api/v1/profile/showcases/<slug>/config/   update config (item selection)
"""
from rest_framework import status as http_status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.services.dashboard_service import get_effective_premium
from trophies.services.showcase_service import (
    ProfileShowcaseService,
    ShowcaseAlreadyActive,
    ShowcaseError,
    ShowcaseInvalidConfig,
    ShowcaseLimitReached,
    ShowcasePremiumRequired,
    ShowcaseTypeNotFound,
)


def _profile_or_error(request):
    """Return (profile, error_response) pair. Only one will be truthy."""
    profile = getattr(request.user, 'profile', None)
    if not profile:
        return None, Response(
            {'error': 'No profile linked.'},
            status=http_status.HTTP_400_BAD_REQUEST,
        )
    return profile, None


class AddShowcaseView(APIView):
    """POST /api/v1/profile/showcases/ - add a showcase to the user's profile."""
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def post(self, request):
        profile, err = _profile_or_error(request)
        if err:
            return err

        showcase_type = request.data.get('showcase_type')
        if not showcase_type:
            return Response(
                {'error': 'showcase_type is required.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        is_premium = get_effective_premium(request)

        try:
            showcase = ProfileShowcaseService.add_showcase(
                profile, showcase_type, is_premium=is_premium,
            )
        except ShowcaseTypeNotFound as e:
            return Response({'error': str(e)}, status=http_status.HTTP_400_BAD_REQUEST)
        except ShowcasePremiumRequired as e:
            return Response({'error': str(e)}, status=http_status.HTTP_403_FORBIDDEN)
        except (ShowcaseLimitReached, ShowcaseAlreadyActive) as e:
            return Response({'error': str(e)}, status=http_status.HTTP_400_BAD_REQUEST)
        except ShowcaseError as e:
            return Response({'error': str(e)}, status=http_status.HTTP_400_BAD_REQUEST)

        return Response({
            'status': 'ok',
            'showcase': {
                'id': showcase.id,
                'showcase_type': showcase.showcase_type,
                'sort_order': showcase.sort_order,
                'config': showcase.config,
            },
        })


class RemoveShowcaseView(APIView):
    """DELETE /api/v1/profile/showcases/<slug>/ - remove a showcase."""
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def delete(self, request, slug):
        profile, err = _profile_or_error(request)
        if err:
            return err

        try:
            ProfileShowcaseService.remove_showcase(profile, slug)
        except ShowcaseTypeNotFound as e:
            return Response({'error': str(e)}, status=http_status.HTTP_404_NOT_FOUND)
        except ShowcaseError as e:
            return Response({'error': str(e)}, status=http_status.HTTP_400_BAD_REQUEST)

        return Response({'status': 'ok'})


class ReorderShowcasesView(APIView):
    """POST /api/v1/profile/showcases/reorder/ - reorder active showcases."""
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def post(self, request):
        profile, err = _profile_or_error(request)
        if err:
            return err

        ordered_types = request.data.get('showcase_types')
        if not isinstance(ordered_types, list):
            return Response(
                {'error': 'showcase_types must be a list of slugs.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        try:
            ProfileShowcaseService.reorder_showcases(profile, ordered_types)
        except ShowcaseError as e:
            return Response({'error': str(e)}, status=http_status.HTTP_400_BAD_REQUEST)

        return Response({'status': 'ok'})


class UpdateShowcaseConfigView(APIView):
    """POST /api/v1/profile/showcases/<slug>/config/ - update config payload."""
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def post(self, request, slug):
        profile, err = _profile_or_error(request)
        if err:
            return err

        config = request.data.get('config', {})
        if not isinstance(config, dict):
            return Response(
                {'error': 'config must be an object.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        try:
            showcase = ProfileShowcaseService.update_showcase_config(
                profile, slug, config,
            )
        except ShowcaseTypeNotFound as e:
            return Response({'error': str(e)}, status=http_status.HTTP_404_NOT_FOUND)
        except ShowcaseInvalidConfig as e:
            return Response({'error': str(e)}, status=http_status.HTTP_400_BAD_REQUEST)
        except ShowcaseError as e:
            return Response({'error': str(e)}, status=http_status.HTTP_400_BAD_REQUEST)

        return Response({
            'status': 'ok',
            'config': showcase.config,
        })
