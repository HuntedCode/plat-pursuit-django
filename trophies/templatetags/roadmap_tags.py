"""Template tags for the Roadmap authoring system."""
from django import template

from trophies.permissions.roadmap_permissions import can_view_editor

register = template.Library()


@register.inclusion_tag('trophies/partials/roadmap/authors_block.html')
def roadmap_authors(roadmap, variant='full'):
    """Render the contributing-authors block for a roadmap.

    Args:
        roadmap: A Roadmap instance (or None - renders nothing).
        variant: 'full' shows avatar + display name list (used on detail page).
                 'compact' shows avatars only with hover-name (used on game tab).

    Authors are any Profile with at least one owned section (Step or
    TrophyGuide) on this roadmap, plus the original roadmap.created_by.
    """
    if roadmap is None:
        return {'authors': [], 'variant': variant}
    return {
        'authors': roadmap.contributors(),
        'variant': variant,
    }


def _ctg_id_for_roadmap(roadmap, ctg=None):
    """Helper: derive the trophy_group_id for editor URLs from a roadmap or
    explicit CTG. Returns 'default' when nothing usable is available.
    """
    if ctg is not None:
        return getattr(ctg, 'trophy_group_id', 'default')
    if roadmap is not None and getattr(roadmap, 'concept_trophy_group_id', None):
        try:
            return roadmap.concept_trophy_group.trophy_group_id
        except Exception:
            return 'default'
    return 'default'


def _normalize_roadmap_arg(roadmap):
    """Coerce template-side roadmap inputs to either a Roadmap or None.

    Templates often look up a Roadmap via the `get_item` filter, which
    returns `{}` (empty dict) when the key is missing. Tags that operate
    on a Roadmap should treat that empty-dict-as-missing identical to
    None instead of trying to access .status on a dict.
    """
    if roadmap is None:
        return None
    if not hasattr(roadmap, 'status'):
        return None
    return roadmap


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
    return can_view_editor(profile, _normalize_roadmap_arg(roadmap))


@register.inclusion_tag('trophies/partials/roadmap/preview_button.html')
def roadmap_preview_button(user, roadmap, game, size='xs', ctg=None):
    """Render a "View Preview" button for any author-role user.

    Preview is read-only and doesn't acquire an edit lock, so it's safe
    to expose alongside the Edit button.

    Hidden when:
      - the viewer isn't authenticated or lacks the writer role,
      - no roadmap exists yet (nothing to preview),
      - the roadmap is already published (writers can just visit the
        normal public roadmap page in that case).

    `ctg` is optional; when supplied, it's used to build the per-CTG
    detail URL even when the roadmap doesn't carry the CTG eagerly.
    """
    roadmap = _normalize_roadmap_arg(roadmap)
    if not getattr(user, 'is_authenticated', False) or roadmap is None:
        return {'show': False}
    if roadmap.status == 'published':
        return {'show': False}
    profile = getattr(user, 'profile', None)
    if profile is None or not profile.has_roadmap_role('writer'):
        return {'show': False}
    return {
        'show': True,
        'roadmap': roadmap,
        'game': game,
        'size': size,
        'trophy_group_id': _ctg_id_for_roadmap(roadmap, ctg),
    }


@register.inclusion_tag('trophies/partials/roadmap/edit_button.html')
def roadmap_edit_button(user, roadmap, game, size='xs', ctg=None):
    """Render the role + lock-aware Edit button for a roadmap.

    Visible states:
      - Authoring allowed, no active lock: regular "Edit" button
      - Authoring allowed, active lock by self: "Resume" button
      - Authoring allowed, active lock by another author: read-only
        "Editing: <username>" indicator (no Edit button)
      - Authoring blocked (e.g. published guide for non-publisher): hidden

    `ctg` is optional and lets the caller specify which CTG's editor to
    open when there's no roadmap yet (e.g. a "Create" button on an empty
    DLC slot). When `roadmap` is provided we derive the CTG from it.
    """
    roadmap = _normalize_roadmap_arg(roadmap)
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
        'trophy_group_id': _ctg_id_for_roadmap(roadmap, ctg),
    }
