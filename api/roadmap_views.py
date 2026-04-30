"""
Roadmap API views (role-gated).

Limited surface: publish/unpublish + image upload. All in-editor mutations
go through `roadmap_lock_views` (acquire / heartbeat / branch / release /
break / merge). The branch payload is the canonical wire shape for editor
edits; legacy per-tab/step/guide endpoints were removed when each CTG
became its own Roadmap (no more nested tabs).

Coarse access is gated by `IsRoadmapAuthor` (writer+); the publish endpoint
overrides `min_roadmap_role` to 'publisher'. Fine-grained per-section
scoping (writers may only edit their own sections) lives in the merge
service.
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
from trophies.models import Roadmap
from trophies.services.roadmap_service import RoadmapService

logger = logging.getLogger('psn_api')


class RoadmapPublishView(APIView):
    """POST: Toggle roadmap publish status. Publisher role required.

    Creates a `published` or `unpublished` RoadmapRevision so the status
    change is visible in the revision timeline. Each Roadmap is scoped to a
    single ConceptTrophyGroup, so publishing one roadmap (e.g. the base
    game) leaves the DLCs' roadmaps in their own status untouched.
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
