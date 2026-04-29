"""Permission helpers for the Roadmap authoring system.

Roles form a hierarchy: publisher > editor > writer > none. Each higher role
inherits everything the lower role can do, plus additional capabilities.

- writer: edit own Steps/TrophyGuides on unpublished guides; create new ones.
- editor: edit/delete/reorder anything on unpublished guides.
- publisher: editor power plus toggle status (publish/unpublish) and force-break locks.

These helpers operate on a `Profile` instance (or anything with a `roadmap_role`
attribute) and the relevant target object. Pass `None` for unauthenticated callers
to get a uniform `False` answer.
"""
from __future__ import annotations

from typing import Optional, Union


ROADMAP_ROLE_CHOICES = (
    ('none', 'None'),
    ('writer', 'Writer'),
    ('editor', 'Editor'),
    ('publisher', 'Publisher'),
)

ROADMAP_ROLE_HIERARCHY = {
    'none': 0,
    'writer': 1,
    'editor': 2,
    'publisher': 3,
}


def has_roadmap_role(profile, min_role: str) -> bool:
    """Return True if profile's roadmap_role meets or exceeds min_role."""
    if profile is None:
        return False
    actual = ROADMAP_ROLE_HIERARCHY.get(getattr(profile, 'roadmap_role', 'none'), 0)
    required = ROADMAP_ROLE_HIERARCHY.get(min_role, 99)
    return actual >= required


def _section_owner_id(section) -> Optional[int]:
    """Best-effort lookup of the created_by profile id on a section.

    Tolerates legacy rows that pre-date the created_by field (returns None).
    """
    return getattr(section, 'created_by_id', None)


def can_view_editor(profile, roadmap) -> bool:
    """Whether profile may open the editor for the given roadmap.

    Writers AND editors are restricted to unpublished guides. Only publishers
    may edit a published guide directly (the expected workflow for editors is
    to ask a publisher to unpublish first). When `roadmap` is None (no guide
    exists yet for this concept), any writer+ may proceed to create one.
    """
    if not has_roadmap_role(profile, 'writer'):
        return False
    if roadmap is None or roadmap.status != 'published':
        return True
    # Guide is published: only publishers may edit.
    return has_roadmap_role(profile, 'publisher')


def can_edit_metadata(profile, roadmap) -> bool:
    """Editing roadmap-level / tab-level metadata fields.

    Editor+ on unpublished guides; publisher-only on published.
    """
    if roadmap is not None and roadmap.status == 'published':
        return has_roadmap_role(profile, 'publisher')
    return has_roadmap_role(profile, 'editor')


def can_create_section(profile, roadmap) -> bool:
    """Creating a new tab/step/trophy guide.

    Writer+ on unpublished guides; publisher-only on published.
    """
    if roadmap is not None and roadmap.status == 'published':
        return has_roadmap_role(profile, 'publisher')
    return has_roadmap_role(profile, 'writer')


def can_edit_section(profile, section) -> bool:
    """Editing an existing Step or TrophyGuide.

    Writers may edit only sections they own (created_by == profile). Editors and
    publishers may edit any section. Roadmap status is checked via the section's
    parent tab when available; if the parent guide is published, editors can
    still edit (they're expected to unpublish first, but no hard block here).
    """
    if not has_roadmap_role(profile, 'writer'):
        return False
    if has_roadmap_role(profile, 'editor'):
        return True
    owner_id = _section_owner_id(section)
    return owner_id is not None and owner_id == profile.id


def can_delete_section(profile) -> bool:
    """Deleting a section. Editor+ only (writers cannot delete, even own work)."""
    return has_roadmap_role(profile, 'editor')


def can_publish(profile) -> bool:
    """Toggling roadmap status (publish/unpublish). Publisher only."""
    return has_roadmap_role(profile, 'publisher')


def can_force_unlock(profile) -> bool:
    """Breaking another user's edit lock. Publisher only."""
    return has_roadmap_role(profile, 'publisher')
