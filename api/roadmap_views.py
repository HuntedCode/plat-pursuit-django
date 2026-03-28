"""
Roadmap API views (staff-only).

Handles REST endpoints for roadmap editing: tab updates, step CRUD,
step-trophy associations, trophy guides, and publish/unpublish.
All business logic lives in RoadmapService.
"""
import logging
import uuid

from django.core.files.storage import default_storage
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import Roadmap, RoadmapTab, RoadmapStep
from trophies.services.roadmap_service import RoadmapService

logger = logging.getLogger('psn_api')


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

def _get_roadmap_and_tab(roadmap_id, tab_id):
    """Resolve Roadmap + RoadmapTab from URL params.

    Returns:
        (roadmap, tab, None) on success
        (None, None, Response) on error
    """
    try:
        roadmap = Roadmap.objects.get(pk=roadmap_id)
    except Roadmap.DoesNotExist:
        return None, None, Response(
            {'error': 'Roadmap not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        tab = RoadmapTab.objects.get(pk=tab_id, roadmap=roadmap)
    except RoadmapTab.DoesNotExist:
        return None, None, Response(
            {'error': 'Tab not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    return roadmap, tab, None


# ------------------------------------------------------------------ #
#  Tab
# ------------------------------------------------------------------ #

class RoadmapTabUpdateView(APIView):
    """PATCH: Update a tab's general tips and/or YouTube URL."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser]

    def patch(self, request, roadmap_id, tab_id):
        roadmap, tab, err = _get_roadmap_and_tab(roadmap_id, tab_id)
        if err:
            return err

        general_tips = request.data.get('general_tips')
        youtube_url = request.data.get('youtube_url')

        tab, error = RoadmapService.update_tab(
            tab.id, general_tips=general_tips, youtube_url=youtube_url
        )
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'general_tips': tab.general_tips,
            'youtube_url': tab.youtube_url,
        })


# ------------------------------------------------------------------ #
#  Steps
# ------------------------------------------------------------------ #

class RoadmapStepListCreateView(APIView):
    """GET: List steps for a tab. POST: Create a new step."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser]

    def get(self, request, roadmap_id, tab_id):
        roadmap, tab, err = _get_roadmap_and_tab(roadmap_id, tab_id)
        if err:
            return err

        steps = tab.steps.prefetch_related('step_trophies').order_by('order')
        return Response({
            'steps': [
                {
                    'id': s.id,
                    'title': s.title,
                    'description': s.description,
                    'order': s.order,
                    'trophy_ids': list(
                        s.step_trophies.order_by('order').values_list('trophy_id', flat=True)
                    ),
                }
                for s in steps
            ]
        })

    def post(self, request, roadmap_id, tab_id):
        roadmap, tab, err = _get_roadmap_and_tab(roadmap_id, tab_id)
        if err:
            return err

        title = request.data.get('title', '').strip()
        if not title:
            return Response(
                {'error': 'Step title is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        description = request.data.get('description', '')
        youtube_url = request.data.get('youtube_url', '')
        step, error = RoadmapService.create_step(tab.id, title, description, youtube_url)
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'id': step.id,
            'title': step.title,
            'description': step.description,
            'youtube_url': step.youtube_url,
            'order': step.order,
            'trophy_ids': [],
        }, status=status.HTTP_201_CREATED)


class RoadmapStepDetailView(APIView):
    """PATCH: Update a step. DELETE: Remove a step."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser]

    def patch(self, request, roadmap_id, tab_id, step_id):
        roadmap, tab, err = _get_roadmap_and_tab(roadmap_id, tab_id)
        if err:
            return err

        # Verify step belongs to this tab
        if not tab.steps.filter(pk=step_id).exists():
            return Response(
                {'error': 'Step not found in this tab.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        title = request.data.get('title')
        description = request.data.get('description')
        youtube_url = request.data.get('youtube_url')

        step, error = RoadmapService.update_step(
            step_id, title=title, description=description, youtube_url=youtube_url
        )
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'id': step.id,
            'title': step.title,
            'description': step.description,
            'order': step.order,
        })

    def delete(self, request, roadmap_id, tab_id, step_id):
        roadmap, tab, err = _get_roadmap_and_tab(roadmap_id, tab_id)
        if err:
            return err

        if not tab.steps.filter(pk=step_id).exists():
            return Response(
                {'error': 'Step not found in this tab.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        success, error = RoadmapService.delete_step(step_id)
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


class RoadmapStepReorderView(APIView):
    """POST: Reorder steps within a tab."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser]

    def post(self, request, roadmap_id, tab_id):
        roadmap, tab, err = _get_roadmap_and_tab(roadmap_id, tab_id)
        if err:
            return err

        step_ids = request.data.get('step_ids', [])
        if not isinstance(step_ids, list):
            return Response(
                {'error': 'step_ids must be a list of step IDs.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        success, error = RoadmapService.reorder_steps(tab.id, step_ids)
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'status': 'ok'})


# ------------------------------------------------------------------ #
#  Step Trophy Associations
# ------------------------------------------------------------------ #

class RoadmapStepTrophyView(APIView):
    """PUT: Replace trophy associations for a step."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser]

    def put(self, request, roadmap_id, tab_id, step_id):
        roadmap, tab, err = _get_roadmap_and_tab(roadmap_id, tab_id)
        if err:
            return err

        if not tab.steps.filter(pk=step_id).exists():
            return Response(
                {'error': 'Step not found in this tab.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        trophy_ids = request.data.get('trophy_ids', [])
        if not isinstance(trophy_ids, list):
            return Response(
                {'error': 'trophy_ids must be a list of integers.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        success, error = RoadmapService.set_step_trophies(step_id, trophy_ids)
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'status': 'ok'})


# ------------------------------------------------------------------ #
#  Trophy Guides
# ------------------------------------------------------------------ #

class RoadmapTrophyGuideView(APIView):
    """PUT: Create/update a trophy guide. DELETE: Remove a trophy guide."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser]

    def put(self, request, roadmap_id, tab_id, trophy_id):
        roadmap, tab, err = _get_roadmap_and_tab(roadmap_id, tab_id)
        if err:
            return err

        body = request.data.get('body', '')

        guide, error = RoadmapService.create_or_update_trophy_guide(
            tab.id, trophy_id, body
        )
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        if guide is None:
            # Empty body resulted in deletion
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response({
            'trophy_id': guide.trophy_id,
            'body': guide.body,
        })

    def delete(self, request, roadmap_id, tab_id, trophy_id):
        roadmap, tab, err = _get_roadmap_and_tab(roadmap_id, tab_id)
        if err:
            return err

        success, error = RoadmapService.delete_trophy_guide(tab.id, trophy_id)
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ #
#  Publish / Unpublish
# ------------------------------------------------------------------ #

class RoadmapPublishView(APIView):
    """POST: Toggle roadmap publish status."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser]

    def post(self, request, roadmap_id):
        try:
            roadmap = Roadmap.objects.get(pk=roadmap_id)
        except Roadmap.DoesNotExist:
            return Response(
                {'error': 'Roadmap not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        action = request.data.get('action', 'publish')

        if action == 'publish':
            roadmap, error = RoadmapService.publish_roadmap(roadmap_id)
        elif action == 'unpublish':
            roadmap, error = RoadmapService.unpublish_roadmap(roadmap_id)
        else:
            return Response(
                {'error': 'Invalid action. Use "publish" or "unpublish".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'status': roadmap.status,
        })


# ------------------------------------------------------------------ #
#  Image Upload
# ------------------------------------------------------------------ #

class RoadmapImageUploadView(APIView):
    """POST: Upload an image for use in roadmap markdown content."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        image = request.FILES.get('image')
        if not image:
            return Response(
                {'error': 'No image file provided.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate and optimize using existing utilities
        from trophies.image_utils import validate_image, optimize_image

        try:
            validate_image(image)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        optimized = optimize_image(image)

        # Save to storage with unique filename
        ext = image.name.rsplit('.', 1)[-1].lower() if '.' in image.name else 'jpg'
        filename = f"roadmaps/images/{uuid.uuid4().hex[:12]}_{image.name}"
        saved_path = default_storage.save(filename, optimized)
        url = default_storage.url(saved_path)

        return Response({'url': url}, status=status.HTTP_201_CREATED)
