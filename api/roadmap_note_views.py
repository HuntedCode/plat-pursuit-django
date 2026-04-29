"""Roadmap notes API endpoints.

CRUD + resolve + read-marker endpoints for `RoadmapNote`. Notes are an
author-only back-channel — these endpoints all gate on `IsRoadmapAuthor`
(writer+) but DO NOT require holding the edit lock. Anyone can comment any
time, including while another author is mid-session.

See `trophies/services/roadmap_note_service.py` for permission rules and
business logic; the views are a thin REST shell around that.
"""
import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import IsRoadmapAuthor
from trophies.models import Roadmap, RoadmapNote
from trophies.services import roadmap_note_service
from trophies.services.roadmap_note_service import NoteError

logger = logging.getLogger('psn_api')


def _serialize_note(note: RoadmapNote, viewer_profile) -> dict:
    author = note.author
    resolver = note.resolved_by
    # For trophy_guide notes, expose the (tab_id, trophy_id) composite the
    # editor uses to match notes to rendered rows. The server still stores
    # a TrophyGuide FK; we hydrate the pair from the related row.
    target_tab_id = note.target_tab_id
    target_trophy_id = None
    if note.target_kind == RoadmapNote.TARGET_TROPHY_GUIDE and note.target_trophy_guide_id:
        target_tab_id = note.target_trophy_guide.tab_id if note.target_trophy_guide else None
        target_trophy_id = note.target_trophy_guide.trophy_id if note.target_trophy_guide else None
    return {
        'id': note.id,
        'roadmap_id': note.roadmap_id,
        'target_kind': note.target_kind,
        'target_tab_id': target_tab_id,
        'target_step_id': note.target_step_id,
        'target_trophy_guide_id': note.target_trophy_guide_id,
        'target_trophy_id': target_trophy_id,
        'body': note.body,
        'status': note.status,
        'is_resolved': note.is_resolved,
        'created_at': note.created_at.isoformat(),
        'updated_at': note.updated_at.isoformat(),
        'resolved_at': note.resolved_at.isoformat() if note.resolved_at else None,
        'author': (
            {
                'id': author.id,
                'username': author.psn_username,
                'display_name': author.display_psn_username or author.psn_username,
                'avatar_url': author.avatar_url or '',
            }
            if author else None
        ),
        'resolved_by': (
            {
                'id': resolver.id,
                'username': resolver.psn_username,
                'display_name': resolver.display_psn_username or resolver.psn_username,
            }
            if resolver else None
        ),
        # Author of the note can edit; author or editor+ can delete; same for resolve.
        'can_edit': bool(viewer_profile and author and author.id == viewer_profile.id),
        'can_delete': bool(
            viewer_profile and (
                (author and author.id == viewer_profile.id)
                or viewer_profile.has_roadmap_role('editor')
            )
        ),
        'can_resolve': bool(
            viewer_profile and (
                (author and author.id == viewer_profile.id)
                or viewer_profile.has_roadmap_role('editor')
            )
        ),
    }


def _get_roadmap(roadmap_id):
    return get_object_or_404(Roadmap.objects.select_related('concept'), pk=roadmap_id)


def _get_note(roadmap, note_id):
    return get_object_or_404(
        RoadmapNote.objects.select_related('author', 'resolved_by', 'target_trophy_guide'),
        pk=note_id, roadmap=roadmap,
    )


class RoadmapNoteListCreateView(APIView):
    """GET: list notes on a roadmap. POST: create a new note."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]

    def get(self, request, roadmap_id):
        roadmap = _get_roadmap(roadmap_id)
        viewer = request.user.profile

        qs = (
            roadmap.notes
            .select_related('author', 'resolved_by', 'target_trophy_guide')
            .order_by('created_at')
        )

        # Optional filters
        status_filter = request.query_params.get('status')
        if status_filter in ('open', 'resolved'):
            qs = qs.filter(status=status_filter)

        target_kind = request.query_params.get('target_kind')
        if target_kind in ('guide', 'tab', 'step', 'trophy_guide'):
            qs = qs.filter(target_kind=target_kind)

        target_tab_id = request.query_params.get('target_tab_id')
        if target_tab_id:
            qs = qs.filter(target_tab_id=target_tab_id)

        target_step_id = request.query_params.get('target_step_id')
        if target_step_id:
            qs = qs.filter(target_step_id=target_step_id)

        target_trophy_guide_id = request.query_params.get('target_trophy_guide_id')
        if target_trophy_guide_id:
            qs = qs.filter(target_trophy_guide_id=target_trophy_guide_id)

        return Response({
            'notes': [_serialize_note(n, viewer) for n in qs],
        })

    def post(self, request, roadmap_id):
        roadmap = _get_roadmap(roadmap_id)
        viewer = request.user.profile

        body = request.data.get('body', '')
        target_kind = request.data.get('target_kind', RoadmapNote.TARGET_GUIDE)
        target_tab_id = request.data.get('target_tab_id')
        target_step_id = request.data.get('target_step_id')
        target_trophy_guide_id = request.data.get('target_trophy_guide_id')
        target_trophy_id = request.data.get('target_trophy_id')

        try:
            note = roadmap_note_service.create_note(
                roadmap=roadmap,
                author=viewer,
                body=body,
                target_kind=target_kind,
                target_tab_id=target_tab_id,
                target_step_id=target_step_id,
                target_trophy_guide_id=target_trophy_guide_id,
                target_trophy_id=target_trophy_id,
            )
        except NoteError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(_serialize_note(note, viewer), status=status.HTTP_201_CREATED)


class RoadmapNoteDetailView(APIView):
    """PATCH: edit a note's body. DELETE: remove a note."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]

    def patch(self, request, roadmap_id, note_id):
        roadmap = _get_roadmap(roadmap_id)
        viewer = request.user.profile
        note = _get_note(roadmap, note_id)

        body = request.data.get('body', '')
        try:
            note = roadmap_note_service.edit_note(note=note, actor=viewer, body=body)
        except NoteError as e:
            return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)

        return Response(_serialize_note(note, viewer))

    def delete(self, request, roadmap_id, note_id):
        roadmap = _get_roadmap(roadmap_id)
        viewer = request.user.profile
        note = _get_note(roadmap, note_id)

        try:
            roadmap_note_service.delete_note(note=note, actor=viewer)
        except NoteError as e:
            return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)

        return Response(status=status.HTTP_204_NO_CONTENT)


class RoadmapNoteResolveView(APIView):
    """POST: toggle a note's resolved/open status."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]

    def post(self, request, roadmap_id, note_id):
        roadmap = _get_roadmap(roadmap_id)
        viewer = request.user.profile
        note = _get_note(roadmap, note_id)

        resolved = request.data.get('resolved')
        if resolved is None:
            return Response(
                {'error': 'resolved must be true or false.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            note = roadmap_note_service.set_note_status(
                note=note, actor=viewer, resolved=bool(resolved),
            )
        except NoteError as e:
            return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)

        return Response(_serialize_note(note, viewer))


class RoadmapNoteMarkReadView(APIView):
    """POST: bump the viewer's last_read_at for this roadmap to now."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]

    def post(self, request, roadmap_id):
        roadmap = _get_roadmap(roadmap_id)
        viewer = request.user.profile
        record = roadmap_note_service.mark_read(profile=viewer, roadmap=roadmap)
        return Response({
            'last_read_at': record.last_read_at.isoformat(),
            'unread_count': 0,
        })
