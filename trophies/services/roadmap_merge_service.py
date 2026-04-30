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

A failure at any step rolls back the transaction and surfaces a `MergeError`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from django.db import transaction

from trophies.models import (
    Roadmap,
    RoadmapEditLock,
    RoadmapRevision,
    RoadmapStep,
    RoadmapStepTrophy,
    RoadmapTab,
    TrophyGuide,
)
from trophies.services.roadmap_service import RoadmapService

logger = logging.getLogger('psn_api')


# Tab fields by required role tier.
#
# - WRITER_TAB_FIELDS: writer-or-tab-owner can edit (general_tips is the
#   primary authoring surface; we want writers to be able to contribute).
# - EDITOR_TAB_FIELDS: editor-or-higher (author-judgment metadata that
#   shouldn't move freely between writers).
# - PUBLISHER_TAB_FIELDS: publisher-only (curated/featured content like the
#   official PlatPursuit YouTube guide that isn't open to author submissions).
WRITER_TAB_FIELDS = ('general_tips',)
EDITOR_TAB_FIELDS = (
    'difficulty',
    'estimated_hours',
    'missable_count',
    'online_required',
    'min_playthroughs',
)
PUBLISHER_TAB_FIELDS = ('youtube_url',)


class MergeError(Exception):
    """Raised when a merge would violate permissions or schema validation."""


@dataclass
class _ChangeCounts:
    tab_content_updates: int = 0
    tab_metadata_updates: int = 0
    step_creates: int = 0
    step_updates: int = 0
    step_deletes: int = 0
    guide_creates: int = 0
    guide_updates: int = 0
    guide_deletes: int = 0
    affected_tabs: set = field(default_factory=set)

    def any(self) -> bool:
        return any(
            (
                self.tab_content_updates,
                self.tab_metadata_updates,
                self.step_creates,
                self.step_updates,
                self.step_deletes,
                self.guide_creates,
                self.guide_updates,
                self.guide_deletes,
            )
        )

    def summary(self, tab_label_lookup) -> str:
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
        if self.tab_content_updates:
            parts.append(f"updated {self.tab_content_updates} tab content")
        if self.tab_metadata_updates:
            parts.append(f"updated {self.tab_metadata_updates} tab metadata")

        if not parts:
            return "No changes"

        text = ", ".join(parts).capitalize()
        if len(self.affected_tabs) == 1:
            (only_tab_id,) = self.affected_tabs
            label = tab_label_lookup.get(only_tab_id, '')
            if label:
                text = f"{text} in '{label}'"
        return text[:200]


def _can_edit_authored(profile, owner_id, is_editor) -> bool:
    """Writer may edit if `owner_id` is None (untouched) or matches them."""
    if is_editor:
        return True
    return owner_id is None or owner_id == profile.id


def _apply_tab(live_tab: RoadmapTab, tab_payload: dict, profile, is_editor: bool, changes: _ChangeCounts) -> None:
    is_publisher = profile.has_roadmap_role('publisher')

    # Tab content: writer-or-owner gate.
    content_dirty = False
    for field_name in WRITER_TAB_FIELDS:
        if field_name in tab_payload:
            new_value = tab_payload[field_name] or ''
            current = getattr(live_tab, field_name) or ''
            if new_value != current:
                if not _can_edit_authored(profile, live_tab.created_by_id, is_editor):
                    raise MergeError(
                        f"Writers can only edit tab content fields they own. "
                        f"Tab {live_tab.id} ('{live_tab.concept_trophy_group.display_name}') "
                        f"has a different owner."
                    )
                setattr(live_tab, field_name, new_value)
                content_dirty = True

    # Tab metadata: editor+.
    metadata_dirty = False
    for field_name in EDITOR_TAB_FIELDS:
        if field_name in tab_payload:
            new_value = tab_payload[field_name]
            current = getattr(live_tab, field_name)
            if new_value != current:
                if not is_editor:
                    raise MergeError(
                        f"Editor role required to change tab metadata field "
                        f"'{field_name}'."
                    )
                setattr(live_tab, field_name, new_value)
                metadata_dirty = True

    # Publisher-only fields (e.g. official YouTube guide URL).
    publisher_dirty = False
    for field_name in PUBLISHER_TAB_FIELDS:
        if field_name in tab_payload:
            new_value = tab_payload[field_name] or ''
            current = getattr(live_tab, field_name) or ''
            if new_value != current:
                if not is_publisher:
                    raise MergeError(
                        f"Publisher role required to change '{field_name}'. "
                        f"This field is reserved for curated content."
                    )
                setattr(live_tab, field_name, new_value)
                publisher_dirty = True

    if content_dirty or metadata_dirty or publisher_dirty:
        if live_tab.created_by_id is None:
            live_tab.created_by_id = profile.id
        live_tab.last_edited_by_id = profile.id
        live_tab.save()
        if content_dirty:
            changes.tab_content_updates += 1
        if metadata_dirty or publisher_dirty:
            changes.tab_metadata_updates += 1
        changes.affected_tabs.add(live_tab.id)

    _apply_steps(live_tab, tab_payload.get('steps', []), profile, is_editor, changes)
    _apply_trophy_guides(live_tab, tab_payload.get('trophy_guides', []), profile, is_editor, changes)


def _apply_steps(live_tab: RoadmapTab, steps_payload: list, profile, is_editor: bool, changes: _ChangeCounts) -> None:
    live_steps = {step.id: step for step in live_tab.steps.all()}
    payload_step_ids = set()

    for index, step_payload in enumerate(steps_payload):
        step_id = step_payload.get('id')
        # New step.
        if step_id is None:
            step = RoadmapStep.objects.create(
                tab=live_tab,
                title=(step_payload.get('title') or '').strip(),
                description=step_payload.get('description') or '',
                youtube_url=step_payload.get('youtube_url') or '',
                order=step_payload.get('order', index),
                gallery_images=_normalize_gallery(step_payload.get('gallery_images')),
                created_by_id=profile.id,
                last_edited_by_id=profile.id,
            )
            _replace_step_trophies(step, step_payload.get('trophy_ids', []))
            changes.step_creates += 1
            changes.affected_tabs.add(live_tab.id)
            continue

        if step_id not in live_steps:
            # Step in payload references an id that doesn't exist on this tab.
            # Treat as a phantom; ignore rather than fail the whole merge.
            logger.warning(
                "Roadmap merge: payload referenced step id %s not in tab %s; skipping.",
                step_id, live_tab.id,
            )
            continue

        live_step = live_steps[step_id]
        payload_step_ids.add(step_id)

        # Diff fields.
        dirty_fields = []
        for field_name in ('title', 'description', 'youtube_url', 'order'):
            if field_name in step_payload:
                new_value = step_payload[field_name]
                if field_name in ('title', 'description', 'youtube_url'):
                    new_value = new_value or ''
                current = getattr(live_step, field_name)
                if new_value != current:
                    dirty_fields.append((field_name, new_value))

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
        changes.affected_tabs.add(live_tab.id)

    # Anything in live but missing from payload's existing-id set is a deletion.
    # The branch_payload represents the FULL desired guide state (seeded from
    # a live snapshot on lock acquire), so absence means delete. Editor-only.
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
        changes.affected_tabs.add(live_tab.id)


def _replace_step_trophies(step: RoadmapStep, trophy_ids) -> None:
    """Replace the step's trophy associations to match the payload list."""
    step.step_trophies.all().delete()
    RoadmapStepTrophy.objects.bulk_create([
        RoadmapStepTrophy(step=step, trophy_id=int(tid), order=i)
        for i, tid in enumerate(trophy_ids or [])
    ])


_GALLERY_FIELDS = ('url', 'alt', 'caption')


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


def _apply_trophy_guides(live_tab: RoadmapTab, guides_payload: list, profile, is_editor: bool, changes: _ChangeCounts) -> None:
    live_guides = {tg.id: tg for tg in live_tab.trophy_guides.all()}
    explicit_payload_ids = set()

    for guide_payload in guides_payload:
        guide_id = guide_payload.get('id')
        if guide_id is None:
            # New trophy guide.
            TrophyGuide.objects.create(
                tab=live_tab,
                trophy_id=int(guide_payload['trophy_id']),
                body=guide_payload.get('body') or '',
                order=guide_payload.get('order', 0),
                is_missable=bool(guide_payload.get('is_missable', False)),
                is_online=bool(guide_payload.get('is_online', False)),
                is_unobtainable=bool(guide_payload.get('is_unobtainable', False)),
                gallery_images=_normalize_gallery(guide_payload.get('gallery_images')),
                created_by_id=profile.id,
                last_edited_by_id=profile.id,
            )
            changes.guide_creates += 1
            changes.affected_tabs.add(live_tab.id)
            continue

        explicit_payload_ids.add(guide_id)

        if guide_id not in live_guides:
            logger.warning(
                "Roadmap merge: payload referenced guide id %s not in tab %s; skipping.",
                guide_id, live_tab.id,
            )
            continue

        live_guide = live_guides[guide_id]

        dirty_fields = []
        for field_name in (
            'body', 'order', 'is_missable', 'is_online', 'is_unobtainable',
        ):
            if field_name in guide_payload:
                new_value = guide_payload[field_name]
                if field_name == 'body':
                    new_value = new_value or ''
                if field_name in ('is_missable', 'is_online', 'is_unobtainable'):
                    new_value = bool(new_value)
                current = getattr(live_guide, field_name)
                if new_value != current:
                    dirty_fields.append((field_name, new_value))

        if 'gallery_images' in guide_payload:
            normalized = _normalize_gallery(guide_payload.get('gallery_images'))
            if normalized != list(live_guide.gallery_images or []):
                dirty_fields.append(('gallery_images', normalized))

        if not dirty_fields:
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
        changes.affected_tabs.add(live_tab.id)

    actually_deleted = set(live_guides.keys()) - explicit_payload_ids
    if actually_deleted:
        if not is_editor:
            raise MergeError(
                f"Editor role required to delete trophy guides. "
                f"{len(actually_deleted)} guide(s) missing from payload."
            )
        TrophyGuide.objects.filter(id__in=actually_deleted).delete()
        changes.guide_deletes += len(actually_deleted)
        changes.affected_tabs.add(live_tab.id)


def merge_branch(lock: RoadmapEditLock, profile) -> RoadmapRevision:
    """Atomically apply lock.branch_payload to live records and create a revision.

    Raises MergeError on permission failure, schema mismatch, or stale lock.
    """
    if lock.payload_version != RoadmapEditLock.PAYLOAD_VERSION:
        raise MergeError(f"Unsupported payload version {lock.payload_version}")

    payload = lock.branch_payload or {}
    payload_tabs = payload.get('tabs') if isinstance(payload, dict) else None
    if not isinstance(payload_tabs, list):
        raise MergeError("Branch payload missing 'tabs' list.")

    is_editor = profile.has_roadmap_role('editor')

    with transaction.atomic():
        try:
            current_lock = RoadmapEditLock.objects.select_for_update().get(pk=lock.pk)
        except RoadmapEditLock.DoesNotExist:
            raise MergeError("Lock no longer exists; another save took precedence.")
        if not current_lock.is_held_by(profile):
            raise MergeError("Lock is no longer held by you.")

        roadmap = Roadmap.objects.select_for_update().get(pk=current_lock.roadmap_id)

        # Published guides are publisher-only. A lock on a published guide
        # could only be held by a non-publisher if the guide was published
        # while their session was active, or if a check elsewhere was bypassed.
        if roadmap.status == 'published' and not profile.has_roadmap_role('publisher'):
            raise MergeError(
                "This guide is published — only publishers can merge changes. "
                "Ask a publisher to unpublish before saving."
            )

        live_tabs_qs = roadmap.tabs.select_related('concept_trophy_group').prefetch_related(
            'steps__step_trophies', 'trophy_guides',
        )
        live_tabs = {tab.id: tab for tab in live_tabs_qs}
        tab_label_lookup = {
            tid: tab.concept_trophy_group.display_name for tid, tab in live_tabs.items()
        }

        changes = _ChangeCounts()
        for tab_payload in payload_tabs:
            live_tab = live_tabs.get(tab_payload.get('id'))
            if live_tab is None:
                continue
            _apply_tab(live_tab, tab_payload, profile, is_editor, changes)

        if changes.any():
            roadmap.save(update_fields=['updated_at'])

        snapshot = RoadmapService.snapshot_roadmap(roadmap)
        revision = RoadmapRevision.objects.create(
            roadmap=roadmap,
            author=profile,
            action_type=RoadmapRevision.ACTION_EDITED,
            snapshot=snapshot,
            summary=changes.summary(tab_label_lookup),
        )
        current_lock.delete()
        return revision


def restore_revision(revision: RoadmapRevision, actor) -> RoadmapRevision:
    """Restore live roadmap state to match revision.snapshot. Editor-only.

    Strategy: clear the roadmap's Steps and TrophyGuides, reset Tab fields, then
    recreate Steps + TrophyGuides from the snapshot. Tabs are not deleted (they
    are tied to ConceptTrophyGroups), only their fields are reset.

    Returns a new `restored` RoadmapRevision so the restore itself is auditable.
    """
    if not actor.has_roadmap_role('editor'):
        raise MergeError("Editor role required to restore a revision.")

    snapshot = revision.snapshot or {}
    snapshot_tabs = snapshot.get('tabs')
    if not isinstance(snapshot_tabs, list):
        raise MergeError("Revision snapshot is malformed.")

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

        live_tabs = {tab.id: tab for tab in roadmap.tabs.all()}

        for tab_snapshot in snapshot_tabs:
            tab_id = tab_snapshot.get('id')
            tab = live_tabs.get(tab_id)
            if tab is None:
                logger.warning(
                    "Restore: snapshot tab id %s no longer exists on roadmap %s; skipping.",
                    tab_id, roadmap.id,
                )
                continue

            for field_name in WRITER_TAB_FIELDS + EDITOR_TAB_FIELDS:
                if field_name in tab_snapshot:
                    setattr(tab, field_name, tab_snapshot[field_name])
            tab.created_by_id = tab_snapshot.get('created_by_id')
            tab.last_edited_by_id = actor.id
            tab.save()

            tab.steps.all().delete()
            for step_snapshot in tab_snapshot.get('steps', []):
                step = RoadmapStep.objects.create(
                    tab=tab,
                    title=step_snapshot.get('title') or '',
                    description=step_snapshot.get('description') or '',
                    youtube_url=step_snapshot.get('youtube_url') or '',
                    order=step_snapshot.get('order', 0),
                    created_by_id=step_snapshot.get('created_by_id'),
                    last_edited_by_id=actor.id,
                )
                _replace_step_trophies(step, step_snapshot.get('trophy_ids', []))

            tab.trophy_guides.all().delete()
            for guide_snapshot in tab_snapshot.get('trophy_guides', []):
                TrophyGuide.objects.create(
                    tab=tab,
                    trophy_id=int(guide_snapshot['trophy_id']),
                    body=guide_snapshot.get('body') or '',
                    order=guide_snapshot.get('order', 0),
                    is_missable=bool(guide_snapshot.get('is_missable', False)),
                    is_online=bool(guide_snapshot.get('is_online', False)),
                    is_unobtainable=bool(guide_snapshot.get('is_unobtainable', False)),
                    created_by_id=guide_snapshot.get('created_by_id'),
                    last_edited_by_id=actor.id,
                )

        if 'status' in snapshot and snapshot['status'] != roadmap.status:
            # Status restore is intentionally left to the publisher action,
            # not the restore. Don't change publish state implicitly.
            pass

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
