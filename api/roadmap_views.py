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
from trophies.models import Profile, Roadmap
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


class RoadmapHiddenAuthorsView(APIView):
    """GET + POST: manage the per-roadmap "hide me from credits" list.

    Use case: a publisher makes a one-off typo fix on someone else's
    guide and doesn't want to be credited as an author. They (or any
    other publisher) can flip themselves into `hidden_authors`; the
    reader's contributor display + author block honor the suppression.

    Publisher role required for mutations (matches the existing
    publisher-only YouTube Guide / publish-status surfaces). GET is
    open to any roadmap author since the editor needs the current
    state to render the toggles.

    POST body:
        profile_id   Required. The Profile to toggle.
        hidden       Required bool. True to hide, False to show.
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]
    min_roadmap_role = 'writer'

    @staticmethod
    def _all_contributor_ids(roadmap):
        """Union of every Profile id that appears in a created_by /
        last_edited_by FK on the roadmap, its steps, or its trophy
        guides. Same data `Roadmap.contributors` derives from, but
        returned as raw ids before the `hidden_authors` filter is
        applied — the editor UI needs ALL contributors to render the
        toggles, and the POST handler uses this set to validate that
        an incoming `profile_id` is actually a contributor before
        adding it to `hidden_authors`.
        """
        profile_ids = set()
        if roadmap.created_by_id:
            profile_ids.add(roadmap.created_by_id)
        if roadmap.last_edited_by_id:
            profile_ids.add(roadmap.last_edited_by_id)
        for step in roadmap.steps.all().only('created_by_id', 'last_edited_by_id'):
            if step.created_by_id:
                profile_ids.add(step.created_by_id)
            if step.last_edited_by_id:
                profile_ids.add(step.last_edited_by_id)
        for guide in roadmap.trophy_guides.all().only('created_by_id', 'last_edited_by_id'):
            if guide.created_by_id:
                profile_ids.add(guide.created_by_id)
            if guide.last_edited_by_id:
                profile_ids.add(guide.last_edited_by_id)
        return profile_ids

    def get(self, request, roadmap_id):
        try:
            roadmap = Roadmap.objects.get(pk=roadmap_id)
        except Roadmap.DoesNotExist:
            return Response(
                {'error': 'Roadmap not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        hidden_ids = set(
            roadmap.hidden_authors.values_list('id', flat=True)
        )
        profile_ids = self._all_contributor_ids(roadmap)
        all_profiles = (
            Profile.objects.filter(id__in=profile_ids)
            .order_by('psn_username')
            .values('id', 'psn_username', 'display_psn_username', 'avatar_url')
        ) if profile_ids else []
        items = [
            {
                'id': p['id'],
                'psn_username': p['psn_username'] or '',
                'display_psn_username': p['display_psn_username'] or p['psn_username'] or '',
                'avatar_url': p['avatar_url'] or '',
                'hidden': p['id'] in hidden_ids,
            }
            for p in all_profiles
        ]
        return Response({'contributors': items})

    def post(self, request, roadmap_id):
        # Publisher gate for mutations — re-check here since the view's
        # min_roadmap_role is 'writer' (to allow GET for the editor UI).
        if not request.user.profile.has_roadmap_role('publisher'):
            return Response(
                {'error': 'Publisher role required to change author visibility.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            roadmap = Roadmap.objects.get(pk=roadmap_id)
        except Roadmap.DoesNotExist:
            return Response(
                {'error': 'Roadmap not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        profile_id = request.data.get('profile_id')
        hidden = request.data.get('hidden')
        if profile_id is None or hidden is None:
            return Response(
                {'error': 'profile_id and hidden are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            profile_id = int(profile_id)
        except (TypeError, ValueError):
            return Response(
                {'error': 'profile_id must be an integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Validate: profile must actually be a contributor on THIS
        # roadmap. Without this gate `hidden_authors.add(arbitrary_id)`
        # either 500s on IntegrityError (non-existent profile) or
        # silently pollutes the M2M with profiles who'll never appear
        # in the reader's contributor union anyway.
        contributor_ids = self._all_contributor_ids(roadmap)
        if profile_id not in contributor_ids:
            return Response(
                {'error': 'profile_id is not a contributor on this roadmap.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if hidden:
            roadmap.hidden_authors.add(profile_id)
        else:
            roadmap.hidden_authors.remove(profile_id)
        return Response({'profile_id': profile_id, 'hidden': bool(hidden)})


class RoadmapTrialWritersView(APIView):
    """GET + POST: manage the per-roadmap trial-writer assignment list.

    Publishers vet new authors by assigning them to specific roadmaps
    before granting the global writer role. A profile with `roadmap_role
    == 'trial'` and an entry in `trial_writers` for a given roadmap acts
    as a writer on that roadmap only — same edit-own-section rules, no
    delete or metadata access (those are editor+).

    GET returns the current assignment list.
    POST { profile_id, assigned: bool } adds / removes a trial writer.
    POST { action: 'search', q: '<query>' } searches profiles by PSN
    username; returns trial-role profiles only so the publisher can't
    accidentally assign a writer/editor (it'd be a no-op anyway, but
    the UX is clearer when the list is scoped).

    GET allowed for any roadmap author (the editor card needs to render
    the current state). POST publisher-only.
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]
    min_roadmap_role = 'writer'

    def get(self, request, roadmap_id):
        try:
            roadmap = Roadmap.objects.get(pk=roadmap_id)
        except Roadmap.DoesNotExist:
            return Response(
                {'error': 'Roadmap not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        assigned = list(
            roadmap.trial_writers
            .order_by('psn_username')
            .values('id', 'psn_username', 'display_psn_username', 'avatar_url')
        )
        return Response({
            'assigned': [
                {
                    'id': p['id'],
                    'psn_username': p['psn_username'] or '',
                    'display_psn_username': p['display_psn_username'] or p['psn_username'] or '',
                    'avatar_url': p['avatar_url'] or '',
                }
                for p in assigned
            ],
        })

    def post(self, request, roadmap_id):
        # Publisher gate re-checked here since min_roadmap_role='writer'
        # for GET; mutations + search are publisher-only.
        if not request.user.profile.has_roadmap_role('publisher'):
            return Response(
                {'error': 'Publisher role required to manage trial writers.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            roadmap = Roadmap.objects.get(pk=roadmap_id)
        except Roadmap.DoesNotExist:
            return Response(
                {'error': 'Roadmap not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        action = request.data.get('action', 'assign')
        if action == 'search':
            q = (request.data.get('q') or '').strip()
            if len(q) < 2:
                return Response({'results': []})
            # Cap at PSN's 16-char max + slack. The template input has
            # maxlength=50, but a direct API caller can send arbitrary
            # length; bound it here so a 1 MB query string can't blow
            # the Postgres planner on an istartswith comparison.
            if len(q) > 64:
                return Response(
                    {'error': 'Search query too long.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Trial-role only, top 10 by psn_username prefix match.
            # Anything broader risks ambiguity — multiple writers may
            # share a partial username.
            matches = (
                Profile.objects
                .filter(roadmap_role='trial', psn_username__istartswith=q)
                .order_by('psn_username')
                .values('id', 'psn_username', 'display_psn_username', 'avatar_url')
                [:10]
            )
            assigned_ids = set(roadmap.trial_writers.values_list('id', flat=True))
            return Response({
                'results': [
                    {
                        'id': p['id'],
                        'psn_username': p['psn_username'] or '',
                        'display_psn_username': p['display_psn_username'] or p['psn_username'] or '',
                        'avatar_url': p['avatar_url'] or '',
                        'already_assigned': p['id'] in assigned_ids,
                    }
                    for p in matches
                ],
            })

        # Assign / unassign path.
        profile_id = request.data.get('profile_id')
        assigned = request.data.get('assigned')
        if profile_id is None or assigned is None:
            return Response(
                {'error': 'profile_id and assigned are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            profile_id = int(profile_id)
        except (TypeError, ValueError):
            return Response(
                {'error': 'profile_id must be an integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Validate: target must currently be a trial-role profile.
        # Assigning a non-trial profile is a UX bug (the assignment
        # wouldn't change their effective role), and validating early
        # surfaces it as a clear 400 rather than a silent no-op.
        target = Profile.objects.filter(pk=profile_id).first()
        if target is None:
            return Response(
                {'error': 'Profile not found.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if target.roadmap_role != 'trial':
            return Response(
                {'error': 'Profile is not a trial-role user.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if assigned:
            roadmap.trial_writers.add(target)
        else:
            roadmap.trial_writers.remove(target)
        return Response({
            'profile_id': profile_id,
            'assigned': bool(assigned),
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

        # Wrap the processing + storage pipeline so an exception inside
        # Pillow / default_storage doesn't bubble up as an HTML 500
        # (which the editor's JS can't parse, leaving the author with
        # a generic "Image save failed." toast and no detail to share).
        # process_roadmap_image has its own fallback to the raw file on
        # Pillow errors; this catch covers the storage backend (S3
        # timeout, disk full, permissions, etc.) and any leak through
        # the fallback.
        try:
            processed = process_roadmap_image(image, watermark=watermark)
            filename = f"roadmaps/images/{uuid.uuid4().hex[:12]}_{processed.name}"
            saved_path = default_storage.save(filename, processed)
            url = default_storage.url(saved_path)
        except Exception as e:
            # Full traceback + the file metadata that's most useful for
            # reproducing: filename, byte size, declared content-type,
            # and the uploading user. `logger.exception` captures the
            # stack and exception type for the centralized error logs.
            logger.exception(
                "Roadmap image upload failed for user=%s file=%s size=%s type=%s watermark=%s",
                getattr(request.user, 'id', None),
                getattr(image, 'name', '?'),
                getattr(image, 'size', '?'),
                getattr(image, 'content_type', '?'),
                watermark,
            )
            # Return the exception class + a truncated message so the
            # author can paste it to support; truncating bounds the
            # response size if Pillow / boto3 returns a verbose error.
            return Response(
                {
                    'error': (
                        f'Image processing or storage failed: '
                        f'{type(e).__name__}: {str(e)[:200]}'
                    ),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

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
        subareas_by_key = {}
        for a in roadmap.collectible_areas.all():
            entry = {'name': a.name or a.slug or f'Area {a.id}'}
            if a.slug:
                areas_by_key[a.slug] = entry
            areas_by_key[str(a.id)] = entry
            for sa in a.subareas.all():
                sa_entry = {
                    'name': sa.name or sa.slug or f'Sub-area {sa.id}',
                    'area_slug': a.slug,
                    'area_name': a.name or a.slug or '',
                }
                if sa.slug:
                    subareas_by_key[sa.slug] = sa_entry
                subareas_by_key[str(sa.id)] = sa_entry
        html = render_roadmap_refs(html, {
            'collectibles': types_by_slug,
            'steps': steps_by_id,
            'areas': areas_by_key,
            'subareas': subareas_by_key,
        })

        # Stamp `data-trophy-type` on each `.trophy-mention` anchor so the
        # preview panel's CSS picks the right color. On the reader, the
        # initTrophyMentions() JS pass handles this by looking up the
        # corresponding trophy guide card's DOM `data-type`; the preview
        # has no such pass (innerHTML drop), so without server-side
        # stamping the CSS default (accent) kicks in and every trophy
        # ref looks like a bronze.
        import re as _re
        concept = getattr(roadmap.concept_trophy_group, 'concept', None)
        game = concept.games.first() if concept else None
        if game:
            trophy_type_by_id = dict(
                game.trophies
                .filter(trophy_group_id=roadmap.concept_trophy_group.trophy_group_id)
                .values_list('trophy_id', 'trophy_type')
            )

            def _stamp_trophy_type(m):
                pre, href, post = m.group(1), m.group(2), m.group(3)
                try:
                    tid = int(href.replace('#trophy-guide-', ''))
                except (ValueError, TypeError):
                    return m.group(0)
                ttype = trophy_type_by_id.get(tid)
                if not ttype:
                    return m.group(0)
                # Inject the attribute. `post` already starts with a
                # space before `class="trophy-mention"` from the
                # process_markdown styler.
                return f'<a {pre}href="{href}" data-trophy-type="{ttype}"{post}>'

            html = _re.sub(
                r'<a ([^>]*?)href="(#trophy-guide-\d+)"([^>]*class="trophy-mention"[^>]*)>',
                _stamp_trophy_type,
                html,
            )

        return Response({'html': html})
