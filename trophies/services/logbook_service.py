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
import zlib

from django.db.models import Q

from trophies.models import EarnedContract, UserTitle
from trophies.services import element_render

logger = logging.getLogger(__name__)


def _build_lab(profile):
    """The Lab zone: the profile's elements/families view (periodic table, radar data,
    composition summary), assembled from real ProfileJobXP via the element foundation."""
    return element_render.build_profile_elements(profile)


def _build_shelf(profile):
    """The compound shelf: molecules of the Projects (Contracts) this profile has ACCEPTED.
    Bounded by accepted contracts (capped) with a cheap per-Contract compound build, so it's
    whale-safe; the molecule is deterministic per Contract (seeded by its slug)."""
    earned = (
        EarnedContract.objects
        .filter(profile=profile)
        .filter(Q(platinum_accepted_at__isnull=False) | Q(full_accepted_at__isnull=False))
        .select_related('contract')
        .prefetch_related('contract__jobs')
        .order_by('-created_at')[:24]
    )
    shelf = []
    for ec in earned:
        contract = ec.contract
        atoms = [element_render.job_atom(j) for j in contract.jobs.all()]
        if not atoms:
            continue
        shelf.append({
            'name': contract.name,
            'compound': element_render.build_compound(atoms, zlib.crc32(contract.slug.encode())),
        })
    return shelf


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
        context['shelf'] = _build_shelf(profile)
    except Exception:
        logger.exception("Logbook shelf build failed for profile %s", getattr(profile, 'id', '?'))
        context['shelf'] = None
    return context
