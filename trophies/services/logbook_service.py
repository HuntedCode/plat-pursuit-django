"""Logbook page context builder.

The Logbook (`/my-pursuit/logbook/`, the Pursuer's RPG-identity deep-dive) assembles
its data here, following the `dashboard_service` / `community_hub_service` pattern: a
single `build_logbook_context(profile)` entry point that delegates to one helper per
page zone, each wrapped so a single broken zone never blanks the whole page.

Built zone by zone (hero -> Lab -> collection preview -> expression -> activity). All
per-user reads aggregate in the DB or are bounded by the ~25-row Job catalog (whale-OOM
rule); never iterate a profile's trophy rows in Python.
"""
import logging

from trophies.models import UserBadge, UserTitle
from trophies.services import element_render

logger = logging.getLogger(__name__)

_COLLECTION_PREVIEW = 6


def _build_lab(profile):
    """The Lab zone: the profile's elements/families view (periodic table, radar data,
    composition summary), assembled from real ProfileJobXP via the element foundation."""
    return element_render.build_profile_elements(profile)


def _build_collection(profile):
    """Collection preview: the most-recent earned badges (deduped to one per series) +
    the total. Uses the lightweight badge-icon path (badge.get_badge_layers), NOT the
    heavy build_badge_frame; bounded to a small recent slice so it's whale-safe."""
    recent = list(
        UserBadge.objects
        .filter(profile=profile, status='earned')
        .select_related('badge', 'badge__base_badge')
        .order_by('-earned_at')[:12]
    )
    seen, badges = set(), []
    for ub in recent:
        series = ub.badge.series_slug
        if series in seen:
            continue
        seen.add(series)
        badges.append(ub.badge)
        if len(badges) >= _COLLECTION_PREVIEW:
            break
    gamification = getattr(profile, 'gamification', None)
    return {
        'badges': badges,
        'total': gamification.unique_badges_earned if gamification else 0,
    }


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


def build_logbook_context(profile):
    """Assemble the full Logbook context for `profile`. Each zone is isolated so a failure
    degrades to a missing section rather than a 500. The Lab is built first because the
    hero reads its element totals (Pursuer Level + Total XP)."""
    context = {}
    lab = None
    try:
        lab = _build_lab(profile)
    except Exception:
        logger.exception("Logbook lab build failed for profile %s", getattr(profile, 'id', '?'))
    context['lab'] = lab
    try:
        context['hero'] = _build_hero(profile, lab)
    except Exception:
        logger.exception("Logbook hero build failed for profile %s", getattr(profile, 'id', '?'))
        context['hero'] = None
    try:
        context['collection'] = _build_collection(profile)
    except Exception:
        logger.exception("Logbook collection build failed for profile %s", getattr(profile, 'id', '?'))
        context['collection'] = None
    return context
