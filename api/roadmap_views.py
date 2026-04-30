"""
Roadmap API views (role-gated).

Handles REST endpoints for roadmap editing: tab updates, step CRUD,
step-trophy associations, trophy guides, and publish/unpublish.
All business logic lives in RoadmapService.

Coarse access is gated by `IsRoadmapAuthor` (writer+); the publish endpoint
overrides `min_roadmap_role` to 'publisher'. Fine-grained per-section scoping
(writers may only edit their own sections) lives in the merge service.
"""
import logging
import uuid

from django.core.files.storage import default_storage
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import IsRoadmapAuthor
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


def _require_editor(request):
    """Return a 403 Response if the requester lacks the editor role, else None."""
    profile = getattr(request.user, 'profile', None)
    if profile is None or not profile.has_roadmap_role('editor'):
        return Response(
            {'error': 'Editor role required for this action.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


# ------------------------------------------------------------------ #
#  Tab
# ------------------------------------------------------------------ #

class RoadmapTabUpdateView(APIView):
    """PATCH: Update a tab's content fields and/or guide metadata."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]

    def patch(self, request, roadmap_id, tab_id):
        roadmap, tab, err = _get_roadmap_and_tab(roadmap_id, tab_id)
        if err:
            return err

        tab, error = RoadmapService.update_tab(
            tab.id,
            general_tips=request.data.get('general_tips'),
            youtube_url=request.data.get('youtube_url'),
            difficulty=request.data.get('difficulty'),
            estimated_hours=request.data.get('estimated_hours'),
            missable_count=request.data.get('missable_count'),
            online_required=request.data.get('online_required'),
            min_playthroughs=request.data.get('min_playthroughs'),
        )
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'general_tips': tab.general_tips,
            'youtube_url': tab.youtube_url,
            'difficulty': tab.difficulty,
            'estimated_hours': tab.estimated_hours,
            'missable_count': tab.missable_count,
            'online_required': tab.online_required,
            'min_playthroughs': tab.min_playthroughs,
        })


# ------------------------------------------------------------------ #
#  Steps
# ------------------------------------------------------------------ #

class RoadmapStepListCreateView(APIView):
    """GET: List steps for a tab. POST: Create a new step."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]

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
    permission_classes = [IsRoadmapAuthor]

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
        forbidden = _require_editor(request)
        if forbidden:
            return forbidden
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
    """POST: Reorder steps within a tab. Editor role required."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]

    def post(self, request, roadmap_id, tab_id):
        forbidden = _require_editor(request)
        if forbidden:
            return forbidden
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
    permission_classes = [IsRoadmapAuthor]

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
    permission_classes = [IsRoadmapAuthor]

    def put(self, request, roadmap_id, tab_id, trophy_id):
        roadmap, tab, err = _get_roadmap_and_tab(roadmap_id, tab_id)
        if err:
            return err

        body = request.data.get('body', '')

        guide, error = RoadmapService.create_or_update_trophy_guide(
            tab.id, trophy_id, body,
            is_missable=request.data.get('is_missable'),
            is_online=request.data.get('is_online'),
            is_unobtainable=request.data.get('is_unobtainable'),
        )
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        if guide is None:
            # Empty body resulted in deletion
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response({
            'trophy_id': guide.trophy_id,
            'body': guide.body,
            'is_missable': guide.is_missable,
            'is_online': guide.is_online,
            'is_unobtainable': guide.is_unobtainable,
        })

    def delete(self, request, roadmap_id, tab_id, trophy_id):
        forbidden = _require_editor(request)
        if forbidden:
            return forbidden
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
    """POST: Toggle roadmap publish status. Publisher role required.

    Creates a `published` or `unpublished` RoadmapRevision so the status
    change is visible in the revision timeline.
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]
    min_roadmap_role = 'publisher'

    def post(self, request, roadmap_id):
        from trophies.models import RoadmapRevision

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
            revision_action = RoadmapRevision.ACTION_PUBLISHED
            summary = "Published roadmap"
        elif action == 'unpublish':
            roadmap, error = RoadmapService.unpublish_roadmap(roadmap_id)
            revision_action = RoadmapRevision.ACTION_UNPUBLISHED
            summary = "Unpublished roadmap"
        else:
            return Response(
                {'error': 'Invalid action. Use "publish" or "unpublish".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        RoadmapRevision.objects.create(
            roadmap=roadmap,
            author=request.user.profile,
            action_type=revision_action,
            snapshot=RoadmapService.snapshot_roadmap(roadmap),
            summary=summary,
        )

        return Response({
            'status': roadmap.status,
        })


# ------------------------------------------------------------------ #
#  Image Upload
# ------------------------------------------------------------------ #

class RoadmapImageUploadView(APIView):
    """POST: Upload an image for use in roadmap markdown content.

    Form fields:
        image       Required. The file to upload.
        watermark   Optional. 'true' (default) to bake the
                    `www.platpursuit.com` watermark into the bottom-right
                    corner; 'false' to skip it.
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        image = request.FILES.get('image')
        if not image:
            return Response(
                {'error': 'No image file provided.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from trophies.image_utils import process_roadmap_image, validate_image

        try:
            validate_image(image)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        watermark_raw = (request.data.get('watermark') or 'true').strip().lower()
        watermark = watermark_raw not in ('false', '0', 'no', 'off')

        processed = process_roadmap_image(image, watermark=watermark)

        filename = f"roadmaps/images/{uuid.uuid4().hex[:12]}_{processed.name}"
        saved_path = default_storage.save(filename, processed)
        url = default_storage.url(saved_path)

        # Encode the watermark state into the returned URL as a query param
        # so the editor can read it back when opening the image in edit
        # mode. Source of truth lives next to the URL itself; we don't
        # otherwise persist this flag.
        url += ('&wm=' if '?' in url else '?wm=') + ('1' if watermark else '0')

        return Response({'url': url, 'watermarked': watermark}, status=status.HTTP_201_CREATED)
