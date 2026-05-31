"""Permission helpers for the Roadmap authoring system.

Roles form a hierarchy: publisher > editor > writer > trial > none. Each
higher role inherits everything the lower role can do, plus additional
capabilities.

- trial: no global authoring power. Publishers per-roadmap-assign trial
  users via `Roadmap.trial_writers`; while assigned, a trial user has
  writer powers ON THAT ROADMAP ONLY. Vetting layer before granting the
  global writer role.
- writer: edit own Steps/TrophyGuides on unpublished guides; create new ones.
- editor: edit/delete/reorder anything on unpublished guides.
- publisher: editor power plus toggle status (publish/unpublish) and force-break locks.

These helpers operate on a `Profile` instance (or anything with a `roadmap_role`
attribute) and the relevant target object. Pass `None` for unauthenticated callers
to get a uniform `False` answer.

For permission checks that should respect per-roadmap trial assignment,
pass the `roadmap` arg to `has_roadmap_role`. Without it, the trial
escalation never fires and trial users look indistinguishable from `none`
to writer-level gates.
"""
from __future__ import annotations

from typing import Optional, Union


ROADMAP_ROLE_CHOICES = (
    ('none', 'None'),
    ('trial', 'Trial'),
    ('writer', 'Writer'),
    ('editor', 'Editor'),
    ('publisher', 'Publisher'),
)

ROADMAP_ROLE_HIERARCHY = {
    'none': 0,
    'trial': 1,
    'writer': 2,
    'editor': 3,
    'publisher': 4,
}


def has_roadmap_role(profile, min_role: str, roadmap=None) -> bool:
    """Return True if profile's roadmap_role meets or exceeds min_role.

    Per-roadmap trial escalation: when `roadmap` is provided AND the
    profile's role is `trial` AND the profile is in
    `roadmap.trial_writers`, the profile is treated as a writer on that
    roadmap. The escalation only fires for min_role='writer' — trial
    users never reach editor/publisher gates via assignment.
    """
    if profile is None:
        return False
    actual = ROADMAP_ROLE_HIERARCHY.get(getattr(profile, 'roadmap_role', 'none'), 0)
    required = ROADMAP_ROLE_HIERARCHY.get(min_role, 99)
    if actual >= required:
        return True
    # Trial-on-assigned-roadmap escalation. We check the M2M only when
    # the cheap global comparison would otherwise reject the request,
    # so the extra query is paid only for actual trial users (and only
    # when callers pass a roadmap, which is the cheap-to-load opt-in).
    if (
        roadmap is not None
        and actual == ROADMAP_ROLE_HIERARCHY['trial']
        and required == ROADMAP_ROLE_HIERARCHY['writer']
        and roadmap.trial_writers.filter(id=profile.id).exists()
    ):
        return True
    return False


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

    Trial users with an assignment on this roadmap pass the writer
    check via the per-roadmap escalation in `has_roadmap_role`.
    """
    if not has_roadmap_role(profile, 'writer', roadmap):
        return False
    if roadmap is None or roadmap.status != 'published':
        return True
    # Guide is published: only publishers may edit (trial users do
    # NOT get published-guide access; the escalation tops out at
    # writer, which is itself capped to unpublished here).
    return has_roadmap_role(profile, 'publisher')


def can_edit_metadata(profile, roadmap) -> bool:
    """Editing roadmap-level / tab-level metadata fields.

    Editor+ on unpublished guides; publisher-only on published. Trial
    assignment does NOT escalate this — metadata edits require the
    editor role, and the trial escalation tops out at writer.
    """
    if roadmap is not None and roadmap.status == 'published':
        return has_roadmap_role(profile, 'publisher')
    return has_roadmap_role(profile, 'editor')


def can_create_section(profile, roadmap) -> bool:
    """Creating a new tab/step/trophy guide.

    Writer+ on unpublished guides; publisher-only on published. Trial
    users with an assignment on this roadmap pass the writer check
    via the per-roadmap escalation in `has_roadmap_role`.
    """
    if roadmap is not None and roadmap.status == 'published':
        return has_roadmap_role(profile, 'publisher')
    return has_roadmap_role(profile, 'writer', roadmap)


def can_edit_section(profile, section) -> bool:
    """Editing an existing Step or TrophyGuide.

    Writers may edit only sections they own (created_by == profile). Editors and
    publishers may edit any section. Roadmap status is checked via the section's
    parent tab when available; if the parent guide is published, editors can
    still edit (they're expected to unpublish first, but no hard block here).

    A trial user with an assignment on the section's parent roadmap is
    treated as a writer for this check (same own-section restriction
    applies). Fetches the section's roadmap once via the FK; callers
    can pass a section with the roadmap prefetched to avoid the query.
    """
    section_roadmap = getattr(section, 'roadmap', None)
    if not has_roadmap_role(profile, 'writer', section_roadmap):
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
