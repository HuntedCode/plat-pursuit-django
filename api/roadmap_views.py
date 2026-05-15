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
            validate_image(image, max_size_mb=10)
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


class RoadmapPreviewView(APIView):
    """POST: Render a snippet of roadmap markdown to HTML for in-editor preview.

    Mirrors the reader's render pipeline 1:1 (same `process_markdown` +
    bleach allowlist + `[[slug]]` pill substitution) so the preview is
    exactly what readers will see after publish. Used by the editor's
    per-textarea Preview toggle.

    Body fields:
        text       Required. Markdown body to render.
        icon_set   Optional. 'ps4' or 'ps5' for controller-icon shortcodes.
                   Defaults to the roadmap's game's `controller_icon_set`.

    The endpoint does NOT mutate state and ignores edit-lock ownership —
    a writer with `IsRoadmapAuthor` access can preview at any time, even
    if a different writer holds the lock.

    Pill substitution uses the roadmap's *saved* collectible types only.
    Brand-new types created in the current branch but not yet merged
    will render as `is-broken` pills until the branch saves; this is a
    deliberate trade-off to avoid threading branch state into the
    render path.
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]

    # Match the model TextField ceiling (RoadmapStep.description, etc. are
    # all bounded well under this). A render bomb upper bound, not a tight
    # business limit.
    MAX_TEXT_LENGTH = 50000

    def post(self, request, roadmap_id):
        try:
            roadmap = (
                Roadmap.objects
                .select_related('concept_trophy_group__concept')
                .get(pk=roadmap_id)
            )
        except Roadmap.DoesNotExist:
            return Response(
                {'error': 'Roadmap not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        text = request.data.get('text', '') or ''
        if not isinstance(text, str):
            return Response(
                {'error': 'text must be a string.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(text) > self.MAX_TEXT_LENGTH:
            return Response(
                {'error': f'Text exceeds {self.MAX_TEXT_LENGTH} characters.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # icon_set: client may override; otherwise derive from a game on
        # the concept. Concept→Game is a reverse 1:N (a concept can span
        # multiple platform stacks), so we just sample the first one for
        # preview purposes — close enough for the controller-icon glyph
        # resolution that the property cares about.
        icon_set = (request.data.get('icon_set') or '').strip().lower()
        if icon_set not in ('ps4', 'ps5'):
            concept = getattr(roadmap.concept_trophy_group, 'concept', None)
            game = concept.games.first() if concept else None
            icon_set = getattr(game, 'controller_icon_set', 'ps4') if game else 'ps4'

        from trophies.services.checklist_service import ChecklistService
        from trophies.templatetags.markdown_filters import render_roadmap_refs

        # Same pipeline as the reader's `render_roadmap_markdown` filter:
        # markdown2 + bleach + spoilers, then ref substitution (covers
        # collectible pills + step/area/section refs). Empty input
        # renders as empty so the editor can show "(nothing to preview)"
        # without a special branch on this side.
        html = ChecklistService.process_markdown(
            text, icon_set=icon_set, enable_spoilers=True,
        )
        types_by_slug = {t.slug: t for t in roadmap.collectible_types.all() if t.slug}
        steps_by_id = {}
        for idx, step in enumerate(roadmap.steps.all()):
            steps_by_id[str(step.id)] = {
                'title': step.title or '',
                'position': idx + 1,
            }
        # Areas keyed by both slug and stringified id so an unsaved-area
        # token like `[[area:-2]]` resolves to a "broken" pill rather
        # than rendering correctly with the old slug. Negative ids
        # naturally won't match either key, which is the desired
        # preview behavior — author sees a broken pill until save +
        # translator runs.
        areas_by_key = {}
        for a in roadmap.collectible_areas.all():
            entry = {'name': a.name or a.slug or f'Area {a.id}'}
            if a.slug:
                areas_by_key[a.slug] = entry
            areas_by_key[str(a.id)] = entry
        html = render_roadmap_refs(html, {
            'collectibles': types_by_slug,
            'steps': steps_by_id,
            'areas': areas_by_key,
        })

        return Response({'html': html})
