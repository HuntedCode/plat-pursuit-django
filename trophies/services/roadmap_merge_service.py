"""Branch-and-merge save flow for the Roadmap authoring system.

The editor never mutates live records directly. While a writer is editing,
autosaves accumulate in `RoadmapEditLock.branch_payload` (a JSON snapshot of
the desired guide state). When the user clicks Save, this service:

1. Validates the lock is still held by the requester.
2. Diffs the payload against live records.
3. Enforces per-action role rules (writer-can-only-edit-own-section,
   editor-only deletes, editor-only metadata changes).
4. Applies the diff atomically.
5. Creates a `RoadmapRevision` snapshot.
6. Releases the lock.

The branch payload is flat per-roadmap (each CTG has its own Roadmap, so
there's no `tabs[]` wrapper):

    {
      "payload_version": 2,
      "roadmap_id": ...,
      "status": "draft" | "published",
      "general_tips": ..., "youtube_url": ..., "difficulty": ..., ...,
      "youtube_channel_name": ..., "youtube_channel_url": ...,
      "steps": [{"id": ..., "title": ..., "description": ...,
                 "youtube_url": ..., "youtube_channel_name": ..., ...}, ...],
      "trophy_guides": [{"id": ..., "trophy_id": ..., "youtube_url": ...,
                         "youtube_channel_name": ..., ...}, ...],
    }

  YouTube channel info is server-derived: the editor never sets the
  channel_* fields directly. On every merge that changes a youtube_url,
  the merge service hits YouTube oEmbed and overwrites the cached
  channel name + URL on the live record (a failed lookup clears them).

A failure at any step rolls back the transaction and surfaces a `MergeError`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from django.db import transaction

from trophies.models import (
    Roadmap,
    RoadmapEditLock,
    RoadmapRevision,
    RoadmapStep,
    RoadmapStepTrophy,
    TrophyGuide,
)
from trophies.services.roadmap_service import RoadmapService
from trophies.services.youtube_oembed_service import fetch_attribution

logger = logging.getLogger('psn_api')


def _resolve_youtube_attribution(youtube_url: str) -> dict:
    """Look up the channel name + URL for a YouTube link.

    Always returns a dict with both keys so callers can splat it onto a
    create() call or assign it field-by-field. Empty URL yields empty
    fields (used to clear attribution when a user removes the URL); a
    failed oEmbed lookup also yields empty fields so the embed still
    renders without attribution rather than blocking the save.
    """
    if not youtube_url:
        return {'youtube_channel_name': '', 'youtube_channel_url': ''}
    result = fetch_attribution(youtube_url)
    if not result:
        return {'youtube_channel_name': '', 'youtube_channel_url': ''}
    return {
        'youtube_channel_name': result['channel_name'],
        'youtube_channel_url': result['channel_url'],
    }


def _backfill_attribution_if_missing(record) -> None:
    """Side-effect: populate cached channel info on records that have a URL
    but no cached attribution yet, then save just the two cache fields.

    Catches three cases the change-driven path misses:
      1. Records whose URL was set before the attribution feature shipped.
      2. Records where a prior oEmbed lookup failed (network, timeout).
      3. Records being merged with no `youtube_url` change, so the
         change-driven refresh wouldn't fire on its own.

    Idempotent: once the cache is populated, subsequent calls are no-ops.
    """
    if not record.youtube_url or record.youtube_channel_name:
        return
    attribution = _resolve_youtube_attribution(record.youtube_url)
    if not attribution['youtube_channel_name']:
        return
    record.youtube_channel_name = attribution['youtube_channel_name']
    record.youtube_channel_url = attribution['youtube_channel_url']
    record.save(update_fields=['youtube_channel_name', 'youtube_channel_url'])


# Fields by required role tier.
#
# - WRITER_FIELDS: writer-or-roadmap-owner can edit. `general_tips` is the
#   primary authoring surface and we want writers to contribute to it.
# - EDITOR_FIELDS: editor-or-higher (author-judgment metadata that
#   shouldn't move freely between writers).
# - PUBLISHER_FIELDS: publisher-only (curated/featured content like the
#   official PlatPursuit YouTube guide that isn't open to author submissions).
WRITER_FIELDS = ('general_tips',)
EDITOR_FIELDS = (
    'difficulty',
    'estimated_hours',
    'min_playthroughs',
)
PUBLISHER_FIELDS = ('youtube_url',)


class MergeError(Exception):
    """Raised when a merge would violate permissions or schema validation."""


@dataclass
class _ChangeCounts:
    content_updates: int = 0
    metadata_updates: int = 0
    step_creates: int = 0
    step_updates: int = 0
    step_deletes: int = 0
    guide_creates: int = 0
    guide_updates: int = 0
    guide_deletes: int = 0

    def any(self) -> bool:
        return any((
            self.content_updates,
            self.metadata_updates,
            self.step_creates,
            self.step_updates,
            self.step_deletes,
            self.guide_creates,
            self.guide_updates,
            self.guide_deletes,
        ))

    def summary(self, ctg_label: str = '') -> str:
        parts = []
        if self.step_creates:
            parts.append(f"added {self.step_creates} step{'s' if self.step_creates != 1 else ''}")
        if self.step_updates:
            parts.append(f"edited {self.step_updates} step{'s' if self.step_updates != 1 else ''}")
        if self.step_deletes:
            parts.append(f"deleted {self.step_deletes} step{'s' if self.step_deletes != 1 else ''}")
        if self.guide_creates:
            parts.append(
                f"added {self.guide_creates} trophy guide{'s' if self.guide_creates != 1 else ''}"
            )
        if self.guide_updates:
            parts.append(
                f"edited {self.guide_updates} trophy guide{'s' if self.guide_updates != 1 else ''}"
            )
        if self.guide_deletes:
            parts.append(
                f"deleted {self.guide_deletes} trophy guide{'s' if self.guide_deletes != 1 else ''}"
            )
        if self.content_updates:
            parts.append("updated tips/content")
        if self.metadata_updates:
            parts.append("updated metadata")

        if not parts:
            return "No changes"

        text = ", ".join(parts).capitalize()
        if ctg_label:
            text = f"{text} in '{ctg_label}'"
        return text[:200]


def _can_edit_authored(profile, owner_id, is_editor) -> bool:
    """Writer may edit if `owner_id` is None (untouched) or matches them."""
    if is_editor:
        return True
    return owner_id is None or owner_id == profile.id


_GALLERY_FIELDS = ('url', 'alt', 'caption')


def _validate_phase(value) -> str:
    """Coerce a phase tag to one of the curated keys, or empty string.

    Unknown values are silently dropped (rather than raising) so a stale
    client that sends a deprecated phase doesn't break the save. Blank /
    None / non-string inputs all collapse to '' (unphased).
    """
    from trophies.util_modules.trophy_phases import PHASE_ORDER
    if not value or not isinstance(value, str):
        return ''
    return value if value in PHASE_ORDER else ''


def _normalize_gallery(value) -> list:
    """Coerce a gallery_images payload into a clean list of {url, alt, caption}.

    Drops items without a usable `url`, truncates strings, ignores extra keys
    that the editor client may send. Persisting normalized data keeps the
    JSON shape stable for downstream readers.
    """
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if not isinstance(item, dict):
            continue
        url = (item.get('url') or '').strip()
        if not url:
            continue
        out.append({
            'url': url[:500],
            'alt': (item.get('alt') or '').strip()[:200],
            'caption': (item.get('caption') or '').strip()[:300],
        })
    return out


def _replace_step_trophies(step: RoadmapStep, trophy_ids) -> None:
    """Replace the step's trophy associations to match the payload list."""
    step.step_trophies.all().delete()
    RoadmapStepTrophy.objects.bulk_create([
        RoadmapStepTrophy(step=step, trophy_id=int(tid), order=i)
        for i, tid in enumerate(trophy_ids or [])
    ])


def _apply_roadmap_fields(
    live_roadmap: Roadmap, payload: dict, profile, is_editor: bool,
    changes: _ChangeCounts,
) -> None:
    """Apply scalar fields (tips, metadata, youtube) with role gating."""
    is_publisher = profile.has_roadmap_role('publisher')

    content_dirty = False
    for field_name in WRITER_FIELDS:
        if field_name in payload:
            new_value = payload[field_name] or ''
            current = getattr(live_roadmap, field_name) or ''
            if new_value != current:
                if not _can_edit_authored(
                    profile, live_roadmap.created_by_id, is_editor,
                ):
                    raise MergeError(
                        f"Writers can only edit roadmap content fields they own. "
                        f"This roadmap has a different owner."
                    )
                setattr(live_roadmap, field_name, new_value)
                content_dirty = True

    metadata_dirty = False
    for field_name in EDITOR_FIELDS:
        if field_name in payload:
            new_value = payload[field_name]
            current = getattr(live_roadmap, field_name)
            if new_value != current:
                if not is_editor:
                    raise MergeError(
                        f"Editor role required to change metadata field "
                        f"'{field_name}'."
                    )
                setattr(live_roadmap, field_name, new_value)
                metadata_dirty = True

    publisher_dirty = False
    for field_name in PUBLISHER_FIELDS:
        if field_name in payload:
            new_value = payload[field_name] or ''
            current = getattr(live_roadmap, field_name) or ''
            if new_value != current:
                if not is_publisher:
                    raise MergeError(
                        f"Publisher role required to change '{field_name}'. "
                        f"This field is reserved for curated content."
                    )
                setattr(live_roadmap, field_name, new_value)
                publisher_dirty = True
                if field_name == 'youtube_url':
                    attribution = _resolve_youtube_attribution(new_value)
                    live_roadmap.youtube_channel_name = attribution['youtube_channel_name']
                    live_roadmap.youtube_channel_url = attribution['youtube_channel_url']

    if content_dirty or metadata_dirty or publisher_dirty:
        if live_roadmap.created_by_id is None:
            live_roadmap.created_by_id = profile.id
        live_roadmap.last_edited_by_id = profile.id
        live_roadmap.save()
        if content_dirty:
            changes.content_updates += 1
        if metadata_dirty or publisher_dirty:
            changes.metadata_updates += 1

    # Cache backfill (URL set, no channel cached yet). Independent of the
    # role-gated save above so it runs even when nothing else changed.
    _backfill_attribution_if_missing(live_roadmap)


def _apply_steps(
    live_roadmap: Roadmap, steps_payload: list, profile, is_editor: bool,
    changes: _ChangeCounts,
) -> None:
    live_steps = {step.id: step for step in live_roadmap.steps.all()}

    for index, step_payload in enumerate(steps_payload):
        step_id = step_payload.get('id')
        # New step.
        if step_id is None:
            new_youtube_url = step_payload.get('youtube_url') or ''
            step = RoadmapStep.objects.create(
                roadmap=live_roadmap,
                title=(step_payload.get('title') or '').strip(),
                description=step_payload.get('description') or '',
                youtube_url=new_youtube_url,
                order=step_payload.get('order', index),
                gallery_images=_normalize_gallery(step_payload.get('gallery_images')),
                created_by_id=profile.id,
                last_edited_by_id=profile.id,
                **_resolve_youtube_attribution(new_youtube_url),
            )
            _replace_step_trophies(step, step_payload.get('trophy_ids', []))
            changes.step_creates += 1
            continue

        if step_id not in live_steps:
            # Step in payload references an id that doesn't exist on this
            # roadmap. Treat as a phantom; ignore rather than fail the
            # whole merge.
            logger.warning(
                "Roadmap merge: payload referenced step id %s not in roadmap %s; skipping.",
                step_id, live_roadmap.id,
            )
            continue

        live_step = live_steps[step_id]

        dirty_fields = []
        for field_name in ('title', 'description', 'youtube_url', 'order'):
            if field_name in step_payload:
                new_value = step_payload[field_name]
                if field_name in ('title', 'description', 'youtube_url'):
                    new_value = new_value or ''
                current = getattr(live_step, field_name)
                if new_value != current:
                    dirty_fields.append((field_name, new_value))
                    if field_name == 'youtube_url':
                        attribution = _resolve_youtube_attribution(new_value)
                        dirty_fields.append((
                            'youtube_channel_name', attribution['youtube_channel_name'],
                        ))
                        dirty_fields.append((
                            'youtube_channel_url', attribution['youtube_channel_url'],
                        ))

        # Diff gallery_images (full-list replace).
        new_gallery = step_payload.get('gallery_images')
        gallery_changed = False
        if new_gallery is not None:
            normalized = _normalize_gallery(new_gallery)
            if normalized != list(live_step.gallery_images or []):
                dirty_fields.append(('gallery_images', normalized))
                gallery_changed = True

        # Diff trophy associations.
        new_trophy_ids = step_payload.get('trophy_ids')
        existing_trophy_ids = list(
            live_step.step_trophies.order_by('order').values_list('trophy_id', flat=True)
        )
        trophies_changed = (
            new_trophy_ids is not None
            and list(new_trophy_ids) != existing_trophy_ids
        )

        if not dirty_fields and not trophies_changed and not gallery_changed:
            _backfill_attribution_if_missing(live_step)
            continue

        if not _can_edit_authored(profile, live_step.created_by_id, is_editor):
            raise MergeError(
                f"Writers can only edit steps they created. Step {live_step.id} "
                f"('{live_step.title}') has a different owner."
            )

        for field_name, new_value in dirty_fields:
            setattr(live_step, field_name, new_value)
        if trophies_changed:
            _replace_step_trophies(live_step, new_trophy_ids)
        live_step.last_edited_by_id = profile.id
        live_step.save()
        changes.step_updates += 1
        _backfill_attribution_if_missing(live_step)

    # Anything in live but missing from payload's existing-id set is a
    # deletion. The branch_payload represents the FULL desired guide state
    # (seeded from a live snapshot on lock acquire), so absence means delete.
    # Editor-only.
    explicit_payload_ids = {
        s.get('id') for s in steps_payload if s.get('id') is not None
    }
    actually_deleted = set(live_steps.keys()) - explicit_payload_ids
    if actually_deleted:
        if not is_editor:
            raise MergeError(
                f"Editor role required to delete steps. {len(actually_deleted)} "
                f"step(s) missing from payload."
            )
        RoadmapStep.objects.filter(id__in=actually_deleted).delete()
        changes.step_deletes += len(actually_deleted)


def _apply_trophy_guides(
    live_roadmap: Roadmap, guides_payload: list, profile, is_editor: bool,
    changes: _ChangeCounts,
) -> None:
    live_guides = {tg.id: tg for tg in live_roadmap.trophy_guides.all()}
    explicit_payload_ids = set()

    for guide_payload in guides_payload:
        guide_id = guide_payload.get('id')
        if guide_id is None:
            # New trophy guide.
            new_youtube_url = guide_payload.get('youtube_url') or ''
            TrophyGuide.objects.create(
                roadmap=live_roadmap,
                trophy_id=int(guide_payload['trophy_id']),
                body=guide_payload.get('body') or '',
                youtube_url=new_youtube_url,
                order=guide_payload.get('order', 0),
                is_missable=bool(guide_payload.get('is_missable', False)),
                is_online=bool(guide_payload.get('is_online', False)),
                is_unobtainable=bool(guide_payload.get('is_unobtainable', False)),
                phase=_validate_phase(guide_payload.get('phase')),
                gallery_images=_normalize_gallery(guide_payload.get('gallery_images')),
                created_by_id=profile.id,
                last_edited_by_id=profile.id,
                **_resolve_youtube_attribution(new_youtube_url),
            )
            changes.guide_creates += 1
            continue

        explicit_payload_ids.add(guide_id)

        if guide_id not in live_guides:
            logger.warning(
                "Roadmap merge: payload referenced guide id %s not in roadmap %s; skipping.",
                guide_id, live_roadmap.id,
            )
            continue

        live_guide = live_guides[guide_id]

        dirty_fields = []
        for field_name in (
            'body', 'youtube_url', 'order',
            'is_missable', 'is_online', 'is_unobtainable', 'phase',
        ):
            if field_name in guide_payload:
                new_value = guide_payload[field_name]
                if field_name in ('body', 'youtube_url'):
                    new_value = new_value or ''
                if field_name in ('is_missable', 'is_online', 'is_unobtainable'):
                    new_value = bool(new_value)
                if field_name == 'phase':
                    new_value = _validate_phase(new_value)
                current = getattr(live_guide, field_name)
                if new_value != current:
                    dirty_fields.append((field_name, new_value))
                    if field_name == 'youtube_url':
                        attribution = _resolve_youtube_attribution(new_value)
                        dirty_fields.append((
                            'youtube_channel_name', attribution['youtube_channel_name'],
                        ))
                        dirty_fields.append((
                            'youtube_channel_url', attribution['youtube_channel_url'],
                        ))

        if 'gallery_images' in guide_payload:
            normalized = _normalize_gallery(guide_payload.get('gallery_images'))
            if normalized != list(live_guide.gallery_images or []):
                dirty_fields.append(('gallery_images', normalized))

        if not dirty_fields:
            _backfill_attribution_if_missing(live_guide)
            continue

        if not _can_edit_authored(profile, live_guide.created_by_id, is_editor):
            raise MergeError(
                f"Writers can only edit trophy guides they created. Guide for "
                f"trophy_id={live_guide.trophy_id} has a different owner."
            )

        for field_name, new_value in dirty_fields:
            setattr(live_guide, field_name, new_value)
        live_guide.last_edited_by_id = profile.id
        live_guide.save()
        changes.guide_updates += 1
        _backfill_attribution_if_missing(live_guide)

    actually_deleted = set(live_guides.keys()) - explicit_payload_ids
    if actually_deleted:
        if not is_editor:
            raise MergeError(
                f"Editor role required to delete trophy guides. "
                f"{len(actually_deleted)} guide(s) missing from payload."
            )
        TrophyGuide.objects.filter(id__in=actually_deleted).delete()
        changes.guide_deletes += len(actually_deleted)


def merge_branch(lock: RoadmapEditLock, profile) -> RoadmapRevision:
    """Atomically apply lock.branch_payload to live records and create a revision.

    Raises MergeError on permission failure, schema mismatch, or stale lock.
    """
    if lock.payload_version != RoadmapEditLock.PAYLOAD_VERSION:
        raise MergeError(f"Unsupported payload version {lock.payload_version}")

    payload = lock.branch_payload or {}
    if not isinstance(payload, dict):
        raise MergeError("Branch payload is malformed.")

    is_editor = profile.has_roadmap_role('editor')

    with transaction.atomic():
        try:
            current_lock = RoadmapEditLock.objects.select_for_update().get(pk=lock.pk)
        except RoadmapEditLock.DoesNotExist:
            raise MergeError("Lock no longer exists; another save took precedence.")
        if not current_lock.is_held_by(profile):
            raise MergeError("Lock is no longer held by you.")

        roadmap = (
            Roadmap.objects
            .select_for_update()
            .select_related('concept_trophy_group')
            .get(pk=current_lock.roadmap_id)
        )

        # Published guides are publisher-only. A lock on a published guide
        # could only be held by a non-publisher if the guide was published
        # while their session was active, or if a check elsewhere was bypassed.
        if roadmap.status == 'published' and not profile.has_roadmap_role('publisher'):
            raise MergeError(
                "This guide is published. Only publishers can merge changes. "
                "Ask a publisher to unpublish before saving."
            )

        changes = _ChangeCounts()
        _apply_roadmap_fields(roadmap, payload, profile, is_editor, changes)
        _apply_steps(roadmap, payload.get('steps') or [], profile, is_editor, changes)
        _apply_trophy_guides(
            roadmap, payload.get('trophy_guides') or [],
            profile, is_editor, changes,
        )

        if changes.any():
            roadmap.save(update_fields=['updated_at'])

        snapshot = RoadmapService.snapshot_roadmap(roadmap)
        revision = RoadmapRevision.objects.create(
            roadmap=roadmap,
            author=profile,
            action_type=RoadmapRevision.ACTION_EDITED,
            snapshot=snapshot,
            summary=changes.summary(roadmap.concept_trophy_group.display_name),
        )
        current_lock.delete()
        return revision


def restore_revision(revision: RoadmapRevision, actor) -> RoadmapRevision:
    """Restore live roadmap state to match revision.snapshot. Editor-only.

    Strategy: clear the roadmap's Steps and TrophyGuides, reset scalar
    fields, then recreate Steps + TrophyGuides from the snapshot. The
    snapshot uses the v2 flat shape; pre-v2 snapshots cannot be restored
    cleanly and are rejected.

    Returns a new `restored` RoadmapRevision so the restore itself is
    auditable.
    """
    if not actor.has_roadmap_role('editor'):
        raise MergeError("Editor role required to restore a revision.")

    snapshot = revision.snapshot or {}
    if snapshot.get('payload_version') != RoadmapEditLock.PAYLOAD_VERSION:
        raise MergeError(
            "This revision predates the per-CTG roadmap split and can't be "
            "restored under the current schema."
        )

    with transaction.atomic():
        roadmap = Roadmap.objects.select_for_update().get(pk=revision.roadmap_id)

        # Refuse to restore while a lock is held: avoids stomping on an active editor.
        try:
            existing_lock = RoadmapEditLock.objects.select_for_update().get(roadmap=roadmap)
            if not existing_lock.is_expired():
                raise MergeError(
                    "Cannot restore while a writer holds the edit lock. "
                    "Force-break the lock first."
                )
            existing_lock.delete()
        except RoadmapEditLock.DoesNotExist:
            pass

        for field_name in WRITER_FIELDS + EDITOR_FIELDS + PUBLISHER_FIELDS:
            if field_name in snapshot:
                setattr(roadmap, field_name, snapshot[field_name])
        # Derived/cached fields that piggyback on the role-gated fields
        # above. Restored from the snapshot rather than re-fetched from
        # YouTube to keep restore deterministic.
        for derived_field in ('youtube_channel_name', 'youtube_channel_url'):
            if derived_field in snapshot:
                setattr(roadmap, derived_field, snapshot[derived_field] or '')
        if 'created_by_id' in snapshot:
            roadmap.created_by_id = snapshot['created_by_id']
        roadmap.last_edited_by_id = actor.id
        roadmap.save()

        roadmap.steps.all().delete()
        for step_snapshot in snapshot.get('steps', []):
            step = RoadmapStep.objects.create(
                roadmap=roadmap,
                title=step_snapshot.get('title') or '',
                description=step_snapshot.get('description') or '',
                youtube_url=step_snapshot.get('youtube_url') or '',
                youtube_channel_name=step_snapshot.get('youtube_channel_name') or '',
                youtube_channel_url=step_snapshot.get('youtube_channel_url') or '',
                order=step_snapshot.get('order', 0),
                gallery_images=_normalize_gallery(step_snapshot.get('gallery_images')),
                created_by_id=step_snapshot.get('created_by_id'),
                last_edited_by_id=actor.id,
            )
            _replace_step_trophies(step, step_snapshot.get('trophy_ids', []))

        roadmap.trophy_guides.all().delete()
        for guide_snapshot in snapshot.get('trophy_guides', []):
            TrophyGuide.objects.create(
                roadmap=roadmap,
                trophy_id=int(guide_snapshot['trophy_id']),
                body=guide_snapshot.get('body') or '',
                youtube_url=guide_snapshot.get('youtube_url') or '',
                youtube_channel_name=guide_snapshot.get('youtube_channel_name') or '',
                youtube_channel_url=guide_snapshot.get('youtube_channel_url') or '',
                order=guide_snapshot.get('order', 0),
                is_missable=bool(guide_snapshot.get('is_missable', False)),
                is_online=bool(guide_snapshot.get('is_online', False)),
                is_unobtainable=bool(guide_snapshot.get('is_unobtainable', False)),
                phase=_validate_phase(guide_snapshot.get('phase')),
                gallery_images=_normalize_gallery(guide_snapshot.get('gallery_images')),
                created_by_id=guide_snapshot.get('created_by_id'),
                last_edited_by_id=actor.id,
            )

        # Status restore is intentionally left to the publisher action,
        # not the restore. Don't change publish state implicitly.

        roadmap.save(update_fields=['updated_at'])

        new_snapshot = RoadmapService.snapshot_roadmap(roadmap)
        return RoadmapRevision.objects.create(
            roadmap=roadmap,
            author=actor,
            action_type=RoadmapRevision.ACTION_RESTORED,
            snapshot=new_snapshot,
            summary=f"Restored revision #{revision.id} from {revision.created_at:%Y-%m-%d %H:%M}"[:200],
        )


def archive_displaced_lock(lock: RoadmapEditLock, actor, action_type: str) -> RoadmapRevision:
    """Archive a lock's branch_payload as a recovery revision and delete the lock.

    Used by both publisher force-break and the advisory auto-takeover path.
    Caller is responsible for the surrounding transaction + select_for_update.
    """
    archive_snapshot = {
        'displaced_holder_id': lock.holder_id,
        'displaced_acquired_at': lock.acquired_at.isoformat(),
        'displaced_last_heartbeat': lock.last_heartbeat.isoformat(),
        'branch_payload': lock.branch_payload,
        'live_at_takeover': RoadmapService.snapshot_roadmap(lock.roadmap),
    }
    if action_type == RoadmapRevision.ACTION_FORCE_UNLOCKED:
        summary = f"Force-unlocked from holder {lock.holder_id}; unsaved branch archived"
    elif action_type == RoadmapRevision.ACTION_AUTO_TAKEN_OVER:
        summary = (
            f"Auto-taken over from idle holder {lock.holder_id}; "
            f"unsaved branch archived (recoverable)"
        )
    else:
        summary = f"Lock archived ({action_type})"
    revision = RoadmapRevision.objects.create(
        roadmap=lock.roadmap,
        author=actor,
        action_type=action_type,
        snapshot=archive_snapshot,
        summary=summary[:200],
    )
    lock.delete()
    return revision


def force_unlock(roadmap: Roadmap, actor) -> Optional[RoadmapRevision]:
    """Publisher-only hostile takeover. Archives current branch as recovery revision."""
    if not actor.has_roadmap_role('publisher'):
        raise MergeError("Publisher role required to force-unlock.")

    with transaction.atomic():
        try:
            lock = RoadmapEditLock.objects.select_for_update().select_related('roadmap').get(
                roadmap=roadmap
            )
        except RoadmapEditLock.DoesNotExist:
            return None
        return archive_displaced_lock(lock, actor, RoadmapRevision.ACTION_FORCE_UNLOCKED)
