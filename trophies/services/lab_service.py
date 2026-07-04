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
from trophies.util_modules.leveling import pursuer_rank_for_level

logger = logging.getLogger(__name__)


def _build_lab(profile):
    """The Lab zone: the profile's elements/families view (periodic table, radar data,
    composition summary), assembled from real ProfileJobXP via the element foundation."""
    return element_render.build_profile_elements(profile)


def _compact(n):
    """Compact label for a large stat value (2.6M / 12.3K) so big totals don't overflow a
    small stat card; the full value is still shown in the card's sub-line."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


# Circumference of the disciplines ring's arc circle (r=42 in the 120x120 viewBox). The hero
# ring's discipline arcs are stroke-dash segments summing to this; keep in sync with the r=42
# in career.html.
_RING_C = 263.89


def _build_hero(profile, lab):
    """The Pursuer hero: element identity at a glance. Pursuer Level + Total XP come from
    the Lab's element totals (the single source of truth, level-1 floor applied). The DNA
    ring frames the Pursuer Level with a donut whose family arcs are each family's SHARE of
    the total level (the composition of your Platinum DNA); `dash`/`offset` are precomputed
    stroke-dash segments so the template just renders them."""
    active = (
        UserTitle.objects
        .filter(profile=profile, is_displayed=True)
        .select_related('title')
        .first()
    )
    ring = []
    if lab:
        total = lab.get('total_level') or 0
        n = len(lab['disciplines']) or 1
        cumulative = 0.0
        for d in lab['disciplines']:
            fam_total = sum(t['level'] for t in d['jobs'])
            share = (fam_total / total) if total else (1.0 / n)
            dash = round(share * _RING_C, 2)
            ring.append({
                'label': d['label'], 'slug': d['slug'], 'avg': d['avg'],
                'share_pct': round(share * 100), 'dash': dash, 'offset': round(-cumulative, 2),
            })
            cumulative += dash
    pursuer_level = lab['total_level'] if lab else 0
    return {
        'pursuer_name': profile.display_psn_username,
        'avatar_url': profile.avatar_url,
        'pursuer_level': pursuer_level,
        'pursuer_rank': pursuer_rank_for_level(pursuer_level),
        'total_job_xp': lab['total_xp'] if lab else 0,
        'element_count': lab['total'] if lab else 0,
        'active_title': active.title.name if active else None,
        'ring': ring,
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
    context['total_xp_compact'] = _compact(lab['total_xp']) if lab else '0'
    try:
        context['hero'] = _build_hero(profile, lab)
    except Exception:
        logger.exception("Lab hero build failed for profile %s", getattr(profile, 'id', '?'))
        context['hero'] = None
    return context
