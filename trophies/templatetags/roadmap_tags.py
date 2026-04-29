"""Template tags for the Roadmap authoring system."""
from django import template

from trophies.permissions.roadmap_permissions import can_view_editor

register = template.Library()


@register.inclusion_tag('trophies/partials/roadmap/authors_block.html')
def roadmap_authors(roadmap, variant='full'):
    """Render the contributing-authors block for a roadmap.

    Args:
        roadmap: A Roadmap instance (or None — renders nothing).
        variant: 'full' shows avatar + display name list (used on detail page).
                 'compact' shows avatars only with hover-name (used on game tab).

    Authors are any Profile with at least one owned section (Tab, Step, or
    TrophyGuide) on this roadmap, plus the original roadmap.created_by.
    """
    if roadmap is None:
        return {'authors': [], 'variant': variant}
    return {
        'authors': roadmap.contributors(),
        'variant': variant,
    }


@register.simple_tag
def can_edit_roadmap(user, roadmap):
    """Return True if user may open the editor for the given roadmap.

    Published guides are publisher-only; unpublished guides are open to any
    writer+. When `roadmap` is None (concept has no roadmap yet), any writer+
    may proceed to create one. Use this in template gates instead of bare
    `is_roadmap_author`, which doesn't account for publish status.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    profile = getattr(user, 'profile', None)
    if profile is None:
        return False
    return can_view_editor(profile, roadmap)


@register.inclusion_tag('trophies/partials/roadmap/edit_button.html')
def roadmap_edit_button(user, roadmap, game, size='xs'):
    """Render the role + lock-aware Edit button for a roadmap.

    Visible states:
      - Authoring allowed, no active lock: regular "Edit" button
      - Authoring allowed, active lock by self: "Continue editing →"
        (the writer is mid-session, encourage them to resume)
      - Authoring allowed, active lock by another author: read-only
        "Editing: <username>" indicator (no Edit button — they'd just hit
        a 409 in the editor)
      - Authoring blocked (e.g. published guide for non-publisher): hidden
    """
    can_edit = can_edit_roadmap(user, roadmap)
    profile = getattr(user, 'profile', None) if getattr(user, 'is_authenticated', False) else None
    active_lock = roadmap.active_lock if roadmap else None
    held_by_self = bool(active_lock and profile and active_lock.holder_id == profile.id)
    held_by_other = bool(active_lock and not held_by_self)
    return {
        'can_edit': can_edit,
        'roadmap': roadmap,
        'game': game,
        'size': size,  # 'xs' | 'sm'
        'active_lock': active_lock,
        'held_by_self': held_by_self,
        'held_by_other': held_by_other,
    }
