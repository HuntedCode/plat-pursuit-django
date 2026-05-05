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
      "collectible_areas": [{"id": ..., "name": ..., "slug": ...,
                              "order": ..., "created_by_id": ...}, ...],
      "collectible_types": [{"id": ..., "name": ..., "slug": ...,
                             "color": ..., "icon": ..., "description": ...,
                             "total_count": ..., "order": ...,
                             "created_by_id": ...,
                             "items": [{"id": ..., "name": ...,
                                        "area_id": ..., "body": ...,
                                        "youtube_url": ...,
                                        "gallery_images": [...],
                                        "is_missable": ..., "is_dlc": ...,
                                        "order": ...,
                                        "created_by_id": ...}, ...]}, ...],
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

from django.utils.text import slugify

from trophies.models import (
    Roadmap,
    RoadmapCollectibleArea,
    RoadmapCollectibleItem,
    RoadmapCollectibleType,
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
    collectible_type_creates: int = 0
    collectible_type_updates: int = 0
    collectible_type_deletes: int = 0
    collectible_area_creates: int = 0
    collectible_area_updates: int = 0
    collectible_area_deletes: int = 0
    collectible_item_creates: int = 0
    collectible_item_updates: int = 0
    collectible_item_deletes: int = 0

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
            self.collectible_type_creates,
            self.collectible_type_updates,
            self.collectible_type_deletes,
            self.collectible_area_creates,
            self.collectible_area_updates,
            self.collectible_area_deletes,
            self.collectible_item_creates,
            self.collectible_item_updates,
            self.collectible_item_deletes,
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
        if self.collectible_type_creates:
            parts.append(
                f"added {self.collectible_type_creates} collectible type{'s' if self.collectible_type_creates != 1 else ''}"
            )
        if self.collectible_type_updates:
            parts.append(
                f"edited {self.collectible_type_updates} collectible type{'s' if self.collectible_type_updates != 1 else ''}"
            )
        if self.collectible_type_deletes:
            parts.append(
                f"deleted {self.collectible_type_deletes} collectible type{'s' if self.collectible_type_deletes != 1 else ''}"
            )
        if self.collectible_area_creates:
            parts.append(
                f"added {self.collectible_area_creates} collectible area{'s' if self.collectible_area_creates != 1 else ''}"
            )
        if self.collectible_area_updates:
            parts.append(
                f"edited {self.collectible_area_updates} collectible area{'s' if self.collectible_area_updates != 1 else ''}"
            )
        if self.collectible_area_deletes:
            parts.append(
                f"deleted {self.collectible_area_deletes} collectible area{'s' if self.collectible_area_deletes != 1 else ''}"
            )
        if self.collectible_item_creates:
            parts.append(
                f"added {self.collectible_item_creates} collectible item{'s' if self.collectible_item_creates != 1 else ''}"
            )
        if self.collectible_item_updates:
            parts.append(
                f"edited {self.collectible_item_updates} collectible item{'s' if self.collectible_item_updates != 1 else ''}"
            )
        if self.collectible_item_deletes:
            parts.append(
                f"deleted {self.collectible_item_deletes} collectible item{'s' if self.collectible_item_deletes != 1 else ''}"
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


def _collectibles_set_owner_id(roadmap: Roadmap) -> Optional[int]:
    """Return the Profile id that owns the collectible-type set, or None.

    Set ownership is derived from the oldest type's `created_by_id` rather
    than a stored field, so deletion of all types naturally releases the
    set for the next writer to claim.
    """
    first = (
        RoadmapCollectibleType.objects
        .filter(roadmap=roadmap)
        .exclude(created_by_id__isnull=True)
        .order_by('id')
        .first()
    )
    return first.created_by_id if first else None


def _validate_collectible_color(value) -> str:
    valid = {choice for choice, _label in RoadmapCollectibleType.COLOR_CHOICES}
    if value in valid:
        return value
    return 'primary'


def _apply_collectible_areas(
    live_roadmap: Roadmap, areas_payload: list, profile, is_editor: bool,
    changes: _ChangeCounts,
) -> dict:
    """Diff and apply the collectible-areas branch payload.

    Returns a mapping {payload_id: live_id} so subsequent item processing
    can resolve ``area_id`` references that pointed at just-created areas
    (which had negative ids in the branch). For pre-existing live areas
    the mapping is identity (id -> id).

    Set-level ownership matches `_apply_collectible_types`. Areas are
    part of the same author-curated vocabulary, gated by the same owner.
    """
    live_areas = {a.id: a for a in live_roadmap.collectible_areas.all()}
    set_owner_id = _collectibles_set_owner_id(live_roadmap)

    def _enforce_set_ownership():
        if is_editor:
            return
        if set_owner_id is not None and set_owner_id != profile.id:
            raise MergeError(
                "This roadmap's collectibles are owned by another writer. "
                "Editors+ can override."
            )

    explicit_payload_ids = set()
    payload_slugs_seen = set()
    area_id_map = {a.id: a.id for a in live_areas.values()}  # identity for live areas

    for area_payload in areas_payload:
        area_id = area_payload.get('id')
        raw_name = (area_payload.get('name') or '').strip()
        if not raw_name:
            continue

        # NEW area.
        if area_id is None or (isinstance(area_id, int) and area_id < 0):
            _enforce_set_ownership()
            slug = slugify(raw_name)[:50] or f'area-{len(live_areas) + 1}'
            existing_slugs = {a.slug for a in live_areas.values()} | payload_slugs_seen
            base = slug
            n = 2
            while slug in existing_slugs:
                slug = f"{base}-{n}"[:50]
                n += 1
            payload_slugs_seen.add(slug)
            new_area = RoadmapCollectibleArea.objects.create(
                roadmap=live_roadmap,
                name=raw_name[:100],
                slug=slug,
                order=area_payload.get('order', len(live_areas)),
                created_by_id=profile.id,
                last_edited_by_id=profile.id,
            )
            changes.collectible_area_creates += 1
            if area_id is not None:
                area_id_map[area_id] = new_area.id
            continue

        explicit_payload_ids.add(area_id)
        if area_id not in live_areas:
            logger.warning(
                "Roadmap merge: payload referenced area id %s not in roadmap %s; skipping.",
                area_id, live_roadmap.id,
            )
            continue

        live_area = live_areas[area_id]
        payload_slugs_seen.add(live_area.slug)

        # Slug is intentionally NOT in the diff — renaming the display
        # `name` keeps the original slug so deep-link anchors and existing
        # item area_id references stay stable.
        dirty_fields = []
        if 'name' in area_payload:
            new_name = (area_payload['name'] or '').strip()[:100]
            if new_name and new_name != live_area.name:
                dirty_fields.append(('name', new_name))
        if 'order' in area_payload:
            new_order = area_payload['order']
            if new_order != live_area.order:
                dirty_fields.append(('order', new_order))

        if not dirty_fields:
            continue

        _enforce_set_ownership()
        for field_name, new_value in dirty_fields:
            setattr(live_area, field_name, new_value)
        live_area.last_edited_by_id = profile.id
        live_area.save()
        changes.collectible_area_updates += 1

    actually_deleted = set(live_areas.keys()) - explicit_payload_ids
    if actually_deleted:
        _enforce_set_ownership()
        RoadmapCollectibleArea.objects.filter(id__in=actually_deleted).delete()
        changes.collectible_area_deletes += len(actually_deleted)
        # Items pointing at deleted areas get SET_NULL via FK on_delete,
        # so they fall back to the trailing "Misc" group on the reader.

    return area_id_map


def _apply_items_for_type(
    live_type: RoadmapCollectibleType, items_payload: list,
    area_id_map: dict, profile, set_owner_id, is_editor: bool,
    changes: _ChangeCounts,
) -> None:
    """Diff and apply the items array nested under a single collectible type.

    Items mirror TrophyGuide structure (body / gallery / youtube + cached
    channel attribution). Slug-less identity (use database id), so renames
    are free. Area FK references are translated through `area_id_map` so
    new items pointing at brand-new areas resolve to the just-created
    live area id.
    """
    live_items = {item.id: item for item in live_type.items.all()}

    def _enforce_set_ownership():
        if is_editor:
            return
        if set_owner_id is not None and set_owner_id != profile.id:
            raise MergeError(
                "This roadmap's collectibles are owned by another writer. "
                "Editors+ can override."
            )

    def _resolve_area(payload_area_id):
        # None or missing → no area. Otherwise look up via the id map.
        if payload_area_id is None:
            return None
        return area_id_map.get(payload_area_id)

    explicit_payload_ids = set()

    for item_payload in items_payload:
        item_id = item_payload.get('id')
        raw_name = (item_payload.get('name') or '').strip()
        if not raw_name:
            continue

        resolved_area_id = _resolve_area(item_payload.get('area_id'))

        # NEW item.
        if item_id is None or (isinstance(item_id, int) and item_id < 0):
            _enforce_set_ownership()
            new_youtube_url = (item_payload.get('youtube_url') or '').strip()
            RoadmapCollectibleItem.objects.create(
                collectible_type=live_type,
                name=raw_name[:200],
                area_id=resolved_area_id,
                body=item_payload.get('body') or '',
                youtube_url=new_youtube_url,
                gallery_images=_normalize_gallery(item_payload.get('gallery_images')),
                is_missable=bool(item_payload.get('is_missable', False)),
                is_dlc=bool(item_payload.get('is_dlc', False)),
                order=item_payload.get('order', len(live_items)),
                created_by_id=profile.id,
                last_edited_by_id=profile.id,
                **_resolve_youtube_attribution(new_youtube_url),
            )
            changes.collectible_item_creates += 1
            continue

        explicit_payload_ids.add(item_id)
        if item_id not in live_items:
            logger.warning(
                "Roadmap merge: payload referenced item id %s not in type %s; skipping.",
                item_id, live_type.id,
            )
            continue

        live_item = live_items[item_id]

        dirty_fields = []
        if 'name' in item_payload:
            new_name = (item_payload['name'] or '').strip()[:200]
            if new_name and new_name != live_item.name:
                dirty_fields.append(('name', new_name))
        if 'area_id' in item_payload:
            if resolved_area_id != live_item.area_id:
                dirty_fields.append(('area_id', resolved_area_id))
        if 'body' in item_payload:
            new_body = item_payload['body'] or ''
            if new_body != live_item.body:
                dirty_fields.append(('body', new_body))
        if 'youtube_url' in item_payload:
            new_url = (item_payload['youtube_url'] or '').strip()
            if new_url != live_item.youtube_url:
                dirty_fields.append(('youtube_url', new_url))
                attribution = _resolve_youtube_attribution(new_url)
                dirty_fields.append((
                    'youtube_channel_name', attribution['youtube_channel_name'],
                ))
                dirty_fields.append((
                    'youtube_channel_url', attribution['youtube_channel_url'],
                ))
        if 'gallery_images' in item_payload:
            normalized = _normalize_gallery(item_payload.get('gallery_images'))
            if normalized != list(live_item.gallery_images or []):
                dirty_fields.append(('gallery_images', normalized))
        for flag in ('is_missable', 'is_dlc'):
            if flag in item_payload:
                new_value = bool(item_payload[flag])
                if new_value != getattr(live_item, flag):
                    dirty_fields.append((flag, new_value))
        if 'order' in item_payload:
            new_order = item_payload['order']
            if new_order != live_item.order:
                dirty_fields.append(('order', new_order))

        if not dirty_fields:
            _backfill_attribution_if_missing(live_item)
            continue

        _enforce_set_ownership()
        for field_name, new_value in dirty_fields:
            setattr(live_item, field_name, new_value)
        live_item.last_edited_by_id = profile.id
        live_item.save()
        changes.collectible_item_updates += 1
        _backfill_attribution_if_missing(live_item)

    actually_deleted = set(live_items.keys()) - explicit_payload_ids
    if actually_deleted:
        _enforce_set_ownership()
        RoadmapCollectibleItem.objects.filter(id__in=actually_deleted).delete()
        changes.collectible_item_deletes += len(actually_deleted)


def _apply_collectible_types(
    live_roadmap: Roadmap, types_payload: list, area_id_map: dict,
    profile, is_editor: bool, changes: _ChangeCounts,
) -> None:
    """Diff and apply the collectible-types branch payload.

    Set-level ownership: the writer who created the FIRST type implicitly
    owns the whole set. Other writers can't add, edit, or delete until
    the set is empty again. Editors+ bypass. Owner is derived from the
    oldest type's created_by_id rather than a stored field.

    Slug is derived from `name` on first save (server-side) and is not
    accepted from the payload after creation, so existing `[[slug]]`
    references in markdown can't break by an in-place rename.

    Each type's `items` array is recursively processed by
    `_apply_items_for_type`; area FK references inside items are
    resolved through ``area_id_map`` so new items pointing at brand-new
    areas reach their live area id.
    """
    live_types = {ct.id: ct for ct in live_roadmap.collectible_types.all()}
    set_owner_id = _collectibles_set_owner_id(live_roadmap)

    def _enforce_set_ownership():
        if is_editor:
            return
        if set_owner_id is not None and set_owner_id != profile.id:
            raise MergeError(
                "This roadmap's collectibles are owned by another writer. "
                "Editors+ can override."
            )

    explicit_payload_ids = set()
    payload_slugs_seen = set()

    for type_payload in types_payload:
        type_id = type_payload.get('id')
        raw_name = (type_payload.get('name') or '').strip()
        if not raw_name:
            # Silently skip empty rows the editor can produce mid-edit. The
            # branch payload is the source of truth, so an unfilled stub is
            # treated as "not yet a real type".
            continue

        items_payload = type_payload.get('items') or []
        live_type = None  # populated below; passed to items helper at end

        # NEW type.
        if type_id is None or (isinstance(type_id, int) and type_id < 0):
            _enforce_set_ownership()
            slug = slugify(raw_name)[:50] or f'type-{len(live_types) + 1}'
            # Disambiguate against live-existing AND payload-pending slugs.
            existing_slugs = {ct.slug for ct in live_types.values()} | payload_slugs_seen
            base = slug
            n = 2
            while slug in existing_slugs:
                slug = f"{base}-{n}"[:50]
                n += 1
            payload_slugs_seen.add(slug)
            live_type = RoadmapCollectibleType.objects.create(
                roadmap=live_roadmap,
                name=raw_name[:100],
                slug=slug,
                color=_validate_collectible_color(type_payload.get('color')),
                icon=(type_payload.get('icon') or '')[:4],
                description=(type_payload.get('description') or '')[:200],
                total_count=type_payload.get('total_count'),
                order=type_payload.get('order', len(live_types)),
                created_by_id=profile.id,
                last_edited_by_id=profile.id,
            )
            changes.collectible_type_creates += 1
            # First type creator becomes the set owner for subsequent ops
            # in this same merge pass.
            if set_owner_id is None:
                set_owner_id = profile.id
        else:
            explicit_payload_ids.add(type_id)
            if type_id not in live_types:
                logger.warning(
                    "Roadmap merge: payload referenced collectible type id %s not in roadmap %s; skipping.",
                    type_id, live_roadmap.id,
                )
                continue

            live_type = live_types[type_id]
            payload_slugs_seen.add(live_type.slug)

            # Diff editable fields. Slug is intentionally NOT in the diff —
            # renaming a type keeps the original slug so [[slug]] references
            # don't silently break.
            dirty_fields = []
            if 'name' in type_payload:
                new_name = (type_payload['name'] or '').strip()[:100]
                if new_name and new_name != live_type.name:
                    dirty_fields.append(('name', new_name))
            if 'color' in type_payload:
                new_color = _validate_collectible_color(type_payload['color'])
                if new_color != live_type.color:
                    dirty_fields.append(('color', new_color))
            if 'icon' in type_payload:
                new_icon = (type_payload['icon'] or '')[:4]
                if new_icon != live_type.icon:
                    dirty_fields.append(('icon', new_icon))
            if 'description' in type_payload:
                new_desc = (type_payload['description'] or '')[:200]
                if new_desc != live_type.description:
                    dirty_fields.append(('description', new_desc))
            if 'total_count' in type_payload:
                new_total = type_payload['total_count']
                if new_total in ('', None):
                    new_total = None
                else:
                    try:
                        new_total = int(new_total)
                        if new_total < 0:
                            new_total = None
                    except (TypeError, ValueError):
                        new_total = None
                if new_total != live_type.total_count:
                    dirty_fields.append(('total_count', new_total))
            if 'order' in type_payload:
                new_order = type_payload['order']
                if new_order != live_type.order:
                    dirty_fields.append(('order', new_order))

            if dirty_fields:
                _enforce_set_ownership()
                for field_name, new_value in dirty_fields:
                    setattr(live_type, field_name, new_value)
                live_type.last_edited_by_id = profile.id
                live_type.save()
                changes.collectible_type_updates += 1

        # Always process items for this type, regardless of whether the
        # type itself was dirty — items can change independently. We pass
        # set_owner_id by value at this point; the items helper enforces
        # the same gate.
        _apply_items_for_type(
            live_type, items_payload, area_id_map,
            profile, set_owner_id, is_editor, changes,
        )

    # Anything in live but missing from the payload's existing-id set is
    # a deletion. Set-owner gate applies; editors+ bypass. Items cascade
    # via FK on_delete=CASCADE.
    actually_deleted = set(live_types.keys()) - explicit_payload_ids
    if actually_deleted:
        _enforce_set_ownership()
        RoadmapCollectibleType.objects.filter(id__in=actually_deleted).delete()
        changes.collectible_type_deletes += len(actually_deleted)


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
        # Areas first so types' nested items can resolve area_id FK
        # references (negative branch ids translate to live ids via the
        # returned map).
        area_id_map = _apply_collectible_areas(
            roadmap, payload.get('collectible_areas') or [],
            profile, is_editor, changes,
        )
        _apply_collectible_types(
            roadmap, payload.get('collectible_types') or [],
            area_id_map, profile, is_editor, changes,
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

        # Order matters: areas first so item area_id FK references resolve
        # against the just-recreated area ids (we map snapshot ids to new
        # ids the same way the merge service does for branch ids).
        roadmap.collectible_areas.all().delete()
        snapshot_to_new_area_id = {}
        for area_snapshot in snapshot.get('collectible_areas', []):
            new_area = RoadmapCollectibleArea.objects.create(
                roadmap=roadmap,
                name=area_snapshot.get('name') or '',
                slug=area_snapshot.get('slug') or '',
                order=area_snapshot.get('order', 0),
                created_by_id=area_snapshot.get('created_by_id'),
                last_edited_by_id=actor.id,
            )
            snapshot_id = area_snapshot.get('id')
            if snapshot_id is not None:
                snapshot_to_new_area_id[snapshot_id] = new_area.id

        roadmap.collectible_types.all().delete()
        for type_snapshot in snapshot.get('collectible_types', []):
            new_type = RoadmapCollectibleType.objects.create(
                roadmap=roadmap,
                name=type_snapshot.get('name') or '',
                slug=type_snapshot.get('slug') or '',
                color=_validate_collectible_color(type_snapshot.get('color')),
                icon=type_snapshot.get('icon') or '',
                description=type_snapshot.get('description') or '',
                total_count=type_snapshot.get('total_count'),
                order=type_snapshot.get('order', 0),
                created_by_id=type_snapshot.get('created_by_id'),
                last_edited_by_id=actor.id,
            )
            for item_snapshot in type_snapshot.get('items', []):
                snapshot_area_id = item_snapshot.get('area_id')
                resolved_area_id = (
                    snapshot_to_new_area_id.get(snapshot_area_id)
                    if snapshot_area_id is not None else None
                )
                RoadmapCollectibleItem.objects.create(
                    collectible_type=new_type,
                    name=item_snapshot.get('name') or '',
                    area_id=resolved_area_id,
                    body=item_snapshot.get('body') or '',
                    youtube_url=item_snapshot.get('youtube_url') or '',
                    youtube_channel_name=item_snapshot.get('youtube_channel_name') or '',
                    youtube_channel_url=item_snapshot.get('youtube_channel_url') or '',
                    gallery_images=_normalize_gallery(item_snapshot.get('gallery_images')),
                    is_missable=bool(item_snapshot.get('is_missable', False)),
                    is_dlc=bool(item_snapshot.get('is_dlc', False)),
                    order=item_snapshot.get('order', 0),
                    created_by_id=item_snapshot.get('created_by_id'),
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
