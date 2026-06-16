"""The Lab page context builder.

The Lab (`/my-pursuit/lab/`, the Pursuer's element identity -- "your Platinum DNA")
assembles its data here, following the `dashboard_service` / `community_hub_service`
pattern: a single `build_lab_context(profile)` entry point that delegates to one helper
per page zone, each wrapped so a single broken zone never blanks the whole page.

Zones: the Pursuer hero (identity at a glance) + the element experience (periodic table,
radar, element detail). All per-user reads aggregate in the DB or are bounded by the
~25-row Job catalog (whale-OOM rule).
"""
import logging

from trophies.models import UserTitle
from trophies.services import element_render

logger = logging.getLogger(__name__)


def _build_lab(profile):
    """The Lab zone: the profile's elements/families view (periodic table, radar data,
    composition summary), assembled from real ProfileJobXP via the element foundation."""
    return element_render.build_profile_elements(profile)


def _build_hero(profile, lab):
    """The Pursuer hero: identity at a glance. Pursuer Level + Total XP come from the Lab's
    element totals (the single source of truth, with the level-1 floor applied); the rest
    is profile + equipped title + badge counts."""
    active = (
        UserTitle.objects
        .filter(profile=profile, is_displayed=True)
        .select_related('title')
        .first()
    )
    gamification = getattr(profile, 'gamification', None)
    return {
        'pursuer_name': profile.display_psn_username,
        'avatar_url': profile.avatar_url,
        'pursuer_level': lab['total_level'] if lab else 0,
        'total_job_xp': lab['total_xp'] if lab else 0,
        'active_title': active.title.name if active else None,
        'badges_earned': gamification.unique_badges_earned if gamification else 0,
        'badge_tiers_earned': gamification.total_badges_earned if gamification else 0,
    }


def build_lab_context(profile):
    """Assemble the full Lab context for `profile`. Each zone is isolated so a failure
    degrades to a missing section rather than a 500. The element experience is built first
    because the hero reads its element totals (Pursuer Level + Total XP)."""
    context = {}
    lab = None
    try:
        lab = _build_lab(profile)
    except Exception:
        logger.exception("Lab elements build failed for profile %s", getattr(profile, 'id', '?'))
    context['lab'] = lab
    try:
        context['hero'] = _build_hero(profile, lab)
    except Exception:
        logger.exception("Lab hero build failed for profile %s", getattr(profile, 'id', '?'))
        context['hero'] = None
    return context
