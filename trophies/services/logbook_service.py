"""Logbook page context builder.

The Logbook (`/my-pursuit/logbook/`, the Pursuer's RPG-identity deep-dive) assembles
its data here, following the `dashboard_service` / `community_hub_service` pattern: a
single `build_logbook_context(profile)` entry point that delegates to one helper per
page zone, each wrapped so a single broken zone never blanks the whole page.

Built zone by zone (hero -> Lab -> collection preview -> expression -> activity). All
per-user reads aggregate in the DB (whale-OOM rule); never iterate a profile's rows in
Python to total them.
"""
import logging

from django.db.models import Sum

from trophies.models import ProfileJobXP, UserTitle
from trophies.services import element_render

logger = logging.getLogger(__name__)


def _build_hero(profile):
    """The Pursuer hero: identity at a glance. Pursuer Level is the sum of all per-job
    levels (RuneScape Total Level); both sums aggregate in the DB."""
    agg = ProfileJobXP.objects.filter(profile=profile).aggregate(
        pursuer_level=Sum('level'),
        total_job_xp=Sum('total_xp'),
    )
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
        'pursuer_level': agg['pursuer_level'] or 0,
        'total_job_xp': agg['total_job_xp'] or 0,
        'active_title': active.title.name if active else None,
        'badges_earned': gamification.unique_badges_earned if gamification else 0,
        'badge_tiers_earned': gamification.total_badges_earned if gamification else 0,
    }


def _build_lab(profile):
    """The Lab zone: the profile's elements/families view (periodic table, radar data,
    composition summary), assembled from real ProfileJobXP via the element foundation."""
    return element_render.build_profile_elements(profile)


def build_logbook_context(profile):
    """Assemble the full Logbook context for `profile`. Each zone is isolated so a
    failure degrades to a missing section rather than a 500."""
    context = {}
    try:
        context['hero'] = _build_hero(profile)
    except Exception:
        logger.exception("Logbook hero build failed for profile %s", getattr(profile, 'id', '?'))
        context['hero'] = None
    try:
        context['lab'] = _build_lab(profile)
    except Exception:
        logger.exception("Logbook lab build failed for profile %s", getattr(profile, 'id', '?'))
        context['lab'] = None
    return context
