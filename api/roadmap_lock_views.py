"""Roadmap edit-lock API endpoints.

The branch-and-merge editor flow lives behind these endpoints:

- ``acquire``: claim a lock for the current user. If no lock exists or the
  current lock has expired, the requester takes it and the branch_payload is
  initialized from a live-state snapshot. If the lock is held by someone else
  (and not expired), the response describes the holder.
- ``heartbeat``: extend the idle timer. Returns ``lock_lost`` if the caller
  no longer holds the lock (e.g. expired and someone else acquired it, or a
  publisher force-broke it).
- ``branch``: replace the in-progress branch_payload (autosave target).
- ``release``: voluntary release on close.
- ``break``: publisher-only force release. Archives the displaced writer's
  branch_payload as a ``force_unlocked`` revision for recovery.
- ``merge``: apply the branch_payload to live records, create a revision,
  release the lock.
"""
import logging

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import IsRoadmapAuthor
from trophies.models import Roadmap, RoadmapEditLock, RoadmapRevision
from trophies.permissions.roadmap_permissions import can_view_editor
from trophies.services.roadmap_merge_service import (
    MergeError,
    archive_displaced_lock,
    force_unlock,
    merge_branch,
)
from trophies.services.roadmap_service import RoadmapService

logger = logging.getLogger('psn_api')


def _flat_to_legacy(flat_payload):
    """Wrap a v2 flat payload as a legacy {payload_version: 1, tabs: [tab]}.

    Compatibility shim for the editor JS. The editor still expects the
    old multi-tab shape; we wrap the per-CTG roadmap as a single-tab list
    so it can keep operating until the JS is rewritten for the flat shape.
    """
    if not isinstance(flat_payload, dict):
        return flat_payload
    tab = {
        'id': flat_payload.get('roadmap_id'),
        'concept_trophy_group_id': flat_payload.get('concept_trophy_group_id'),
        'general_tips': flat_payload.get('general_tips') or '',
        'youtube_url': flat_payload.get('youtube_url') or '',
        'difficulty': flat_payload.get('difficulty'),
        'estimated_hours': flat_payload.get('estimated_hours'),
        'min_playthroughs': flat_payload.get('min_playthroughs', 1),
        'created_by_id': flat_payload.get('created_by_id'),
        'last_edited_by_id': flat_payload.get('last_edited_by_id'),
        'steps': flat_payload.get('steps') or [],
        'trophy_guides': flat_payload.get('trophy_guides') or [],
    }
    return {
        'payload_version': 1,
        'roadmap_id': flat_payload.get('roadmap_id'),
        'status': flat_payload.get('status', 'draft'),
        'tabs': [tab],
    }


def _legacy_to_flat(legacy_payload):
    """Unwrap a legacy {tabs: [tab]} shape into a flat v2 payload.

    Picks the first (and only) tab. Per-CTG roadmaps mean the editor only
    ever sends a single-element tabs array; if more arrive we drop the
    extras since they don't belong to this session's CTG anyway.
    """
    if not isinstance(legacy_payload, dict):
        return legacy_payload
    tabs = legacy_payload.get('tabs')
    if not isinstance(tabs, list) or not tabs:
        return None  # malformed
    tab = tabs[0]
    return {
        'payload_version': RoadmapEditLock.PAYLOAD_VERSION,
        'roadmap_id': legacy_payload.get('roadmap_id') or tab.get('id'),
        'concept_trophy_group_id': tab.get('concept_trophy_group_id'),
        'status': legacy_payload.get('status', 'draft'),
        'general_tips': tab.get('general_tips') or '',
        'youtube_url': tab.get('youtube_url') or '',
        'difficulty': tab.get('difficulty'),
        'estimated_hours': tab.get('estimated_hours'),
        'min_playthroughs': tab.get('min_playthroughs', 1),
        'created_by_id': tab.get('created_by_id'),
        'last_edited_by_id': tab.get('last_edited_by_id'),
        'steps': tab.get('steps') or [],
        'trophy_guides': tab.get('trophy_guides') or [],
    }


def _serialize_lock(lock, viewer_profile):
    return {
        'roadmap_id': lock.roadmap_id,
        'holder_id': lock.holder_id,
        'holder_username': lock.holder.psn_username if lock.holder else None,
        'held_by_self': lock.is_held_by(viewer_profile),
        'acquired_at': lock.acquired_at.isoformat(),
        'last_heartbeat': lock.last_heartbeat.isoformat(),
        'expires_at': lock.expires_at.isoformat(),
        'seconds_until_expiry': lock.seconds_until_expiry(),
        'hard_cap_seconds_remaining': lock.hard_cap_seconds_remaining(),
        'payload_version': lock.payload_version,
        'is_stale': lock.is_expired(),  # advisory: stale locks can be auto-taken-over
    }


def _get_roadmap(roadmap_id):
    return get_object_or_404(Roadmap.objects.select_related('concept'), pk=roadmap_id)


class RoadmapLockAcquireView(APIView):
    """POST: claim or refresh the edit lock for this roadmap.

    Advisory semantics:
      - No lock present: create a fresh lock seeded with a live snapshot.
      - Lock held by self (active or stale): re-activate (heartbeat) and
        return the existing branch_payload. The session resumes intact even
        after an idle gap, as long as nobody else took over.
      - Lock held by another, active: return 409 with holder info.
      - Lock held by another, stale (idle past expiry): auto-take-over.
        Archive their branch_payload as an `auto_taken_over` revision so
        their work is recoverable, then create a fresh lock.

    The response includes ``resumed_stale`` when the caller is resuming their
    own previously-stale lock (so the JS can show a welcome-back banner).
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]

    def post(self, request, roadmap_id):
        roadmap = _get_roadmap(roadmap_id)
        profile = request.user.profile

        # Published guides: publisher-only. Writers and editors are redirected
        # by the view layer, but we re-enforce here so a direct API call also
        # gets refused.
        if not can_view_editor(profile, roadmap):
            return Response(
                {'error': 'This guide is published — only publishers can edit it directly.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        with transaction.atomic():
            lock = (
                RoadmapEditLock.objects
                .select_for_update()
                .select_related('roadmap', 'holder')
                .filter(roadmap=roadmap)
                .first()
            )

            # Held by me (active or stale). Re-activate and resume.
            if lock and lock.is_held_by(profile):
                was_stale = lock.is_expired()
                lock.heartbeat()
                return Response({
                    'lock': _serialize_lock(lock, profile),
                    'branch_payload': _flat_to_legacy(lock.branch_payload),
                    'reacquired': False,
                    'resumed_stale': was_stale,
                })

            # Held by another, still active. Hard conflict.
            if lock and not lock.is_expired():
                return Response(
                    {
                        'error': 'Roadmap is currently being edited by another author.',
                        'lock': _serialize_lock(lock, profile),
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            # Held by another but stale. Auto-takeover with branch archive.
            archived_revision_id = None
            if lock and lock.is_expired():
                archived = archive_displaced_lock(
                    lock, profile, RoadmapRevision.ACTION_AUTO_TAKEN_OVER
                )
                archived_revision_id = archived.id

            snapshot = RoadmapService.snapshot_roadmap(roadmap)
            new_lock = RoadmapEditLock.objects.create(
                roadmap=roadmap,
                holder=profile,
                branch_payload=snapshot,
            )
            return Response({
                'lock': _serialize_lock(new_lock, profile),
                'branch_payload': _flat_to_legacy(new_lock.branch_payload),
                'reacquired': True,
                'resumed_stale': False,
                'archived_predecessor_revision_id': archived_revision_id,
            })


class RoadmapLockHeartbeatView(APIView):
    """POST: extend the idle timer. Returns lock_lost only if someone else now holds the lock.

    Advisory model: a stale-but-still-mine lock heartbeats fine and re-activates.
    `lock_lost` only fires if another writer has actually taken over.
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]

    def post(self, request, roadmap_id):
        roadmap = _get_roadmap(roadmap_id)
        profile = request.user.profile

        with transaction.atomic():
            try:
                lock = RoadmapEditLock.objects.select_for_update().get(roadmap=roadmap)
            except RoadmapEditLock.DoesNotExist:
                return Response({'lock_lost': True}, status=status.HTTP_404_NOT_FOUND)

            if not lock.is_held_by(profile):
                return Response({'lock_lost': True}, status=status.HTTP_409_CONFLICT)

            was_stale = lock.is_expired()
            lock.heartbeat()
            return Response({
                'lock': _serialize_lock(lock, profile),
                'lock_lost': False,
                'was_stale': was_stale,
            })


class RoadmapLockBranchView(APIView):
    """PATCH: replace the in-progress branch_payload (autosave target)."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]

    def patch(self, request, roadmap_id):
        roadmap = _get_roadmap(roadmap_id)
        profile = request.user.profile

        incoming = request.data.get('branch_payload')
        if not isinstance(incoming, dict):
            return Response(
                {'error': 'branch_payload must be an object.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Accept legacy v1 (tabs[]) shape from the editor and unwrap to
        # the flat v2 shape used by storage + merge. Once the JS is
        # rewritten this translation can come out.
        if incoming.get('payload_version') == 1:
            payload = _legacy_to_flat(incoming)
            if payload is None:
                return Response(
                    {'error': 'Legacy payload missing tabs[].'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            payload = incoming

        if payload.get('payload_version') != RoadmapEditLock.PAYLOAD_VERSION:
            return Response(
                {'error': f'Expected payload_version={RoadmapEditLock.PAYLOAD_VERSION}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            try:
                lock = RoadmapEditLock.objects.select_for_update().get(roadmap=roadmap)
            except RoadmapEditLock.DoesNotExist:
                return Response({'lock_lost': True}, status=status.HTTP_409_CONFLICT)

            if not lock.is_held_by(profile):
                return Response({'lock_lost': True}, status=status.HTTP_409_CONFLICT)

            # Held by self (active or stale) -> write the branch and refresh timers.
            lock.branch_payload = payload
            lock.last_heartbeat = timezone.now()
            lock.expires_at = lock._compute_expires_at()
            lock.save(update_fields=['branch_payload', 'last_heartbeat', 'expires_at'])

            return Response({
                'lock': _serialize_lock(lock, profile),
                'lock_lost': False,
            })


class RoadmapLockReleaseView(APIView):
    """POST: voluntarily release the lock (e.g. on editor close)."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]

    def post(self, request, roadmap_id):
        roadmap = _get_roadmap(roadmap_id)
        profile = request.user.profile

        with transaction.atomic():
            try:
                lock = RoadmapEditLock.objects.select_for_update().get(roadmap=roadmap)
            except RoadmapEditLock.DoesNotExist:
                return Response({'released': True})

            if lock.is_held_by(profile):
                lock.delete()
                return Response({'released': True})

            return Response(
                {'error': 'Lock is held by a different author.'},
                status=status.HTTP_403_FORBIDDEN,
            )


class RoadmapLockBreakView(APIView):
    """POST: publisher-only force-break. Archives the displaced branch."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]
    min_roadmap_role = 'publisher'

    def post(self, request, roadmap_id):
        roadmap = _get_roadmap(roadmap_id)
        profile = request.user.profile

        try:
            archive_revision = force_unlock(roadmap, profile)
        except MergeError as e:
            return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)

        return Response({
            'broken': archive_revision is not None,
            'archive_revision_id': archive_revision.id if archive_revision else None,
        })


class RoadmapLockMergeView(APIView):
    """POST: merge branch_payload into live records, create revision, release lock.

    The optional preload-payload write and the merge itself happen inside a
    single outer transaction so a publisher force-break can't slip between
    them. `merge_branch` runs its own `transaction.atomic()` which behaves
    as a savepoint when nested.
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsRoadmapAuthor]

    def post(self, request, roadmap_id):
        roadmap = _get_roadmap(roadmap_id)
        profile = request.user.profile

        incoming = request.data.get('branch_payload')
        if incoming is not None and not isinstance(incoming, dict):
            return Response(
                {'error': 'branch_payload must be an object.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Same legacy translation as branch PATCH.
        if isinstance(incoming, dict) and incoming.get('payload_version') == 1:
            payload = _legacy_to_flat(incoming)
            if payload is None:
                return Response(
                    {'error': 'Legacy payload missing tabs[].'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            payload = incoming

        try:
            with transaction.atomic():
                try:
                    lock = RoadmapEditLock.objects.select_for_update().get(roadmap=roadmap)
                except RoadmapEditLock.DoesNotExist:
                    return Response({'lock_lost': True}, status=status.HTTP_409_CONFLICT)

                if not lock.is_held_by(profile):
                    return Response({'lock_lost': True}, status=status.HTTP_409_CONFLICT)

                if payload is not None:
                    lock.branch_payload = payload
                    lock.save(update_fields=['branch_payload'])

                revision = merge_branch(lock, profile)
        except MergeError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception(
                "Roadmap merge failed for roadmap=%s profile=%s",
                roadmap.id, profile.id,
            )
            return Response(
                {'error': 'Merge failed unexpectedly. Your branch is preserved.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({
            'merged': True,
            'revision_id': revision.id,
            'summary': revision.summary,
        })
