"""The Career page context builder.

Career (`/career/`, the Pursuer's job identity) assembles its data here, following the
`dashboard_service` / `community_hub_service` pattern: a single `build_career_context(profile)`
entry point that delegates to one helper per page zone, each wrapped so a single broken zone
never blanks the whole page.

Zones: the Pursuer hero (identity at a glance) + the jobs experience (the discipline-grouped
skills grid, radar, per-job detail). All per-user reads aggregate in the DB or are bounded by
the ~25-row Job catalog (whale-OOM rule).
"""
import logging

from trophies.models import UserTitle
from trophies.services import job_render
from trophies.util_modules.leveling import pursuer_rank_for_level

logger = logging.getLogger(__name__)


def _build_jobs(profile):
    """The jobs zone: the profile's jobs/disciplines view (skills grid, radar data,
    composition summary), assembled from real ProfileJobXP via the job foundation."""
    return job_render.build_profile_jobs(profile)


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


def _build_hero(profile, jobs):
    """The Pursuer hero: job identity at a glance. Career Level + Total XP come from the jobs
    totals (the single source of truth, level-1 floor applied). The disciplines ring frames the
    Career Level with a donut whose discipline arcs are each discipline's SHARE of the total
    level; `dash`/`offset` are precomputed stroke-dash segments so the template just renders them."""
    active = (
        UserTitle.objects
        .filter(profile=profile, is_displayed=True)
        .select_related('title')
        .first()
    )
    ring = []
    if jobs:
        total = jobs.get('total_level') or 0
        n = len(jobs['disciplines']) or 1
        cumulative = 0.0
        for d in jobs['disciplines']:
            disc_total = sum(t['level'] for t in d['jobs'])
            share = (disc_total / total) if total else (1.0 / n)
            dash = round(share * _RING_C, 2)
            ring.append({
                'label': d['label'], 'slug': d['slug'], 'avg': d['avg'],
                'share_pct': round(share * 100), 'dash': dash, 'offset': round(-cumulative, 2),
            })
            cumulative += dash
    pursuer_level = jobs['total_level'] if jobs else 0
    return {
        'pursuer_name': profile.display_psn_username,
        'avatar_url': profile.avatar_url,
        'pursuer_level': pursuer_level,
        'pursuer_rank': pursuer_rank_for_level(pursuer_level),
        'total_job_xp': jobs['total_xp'] if jobs else 0,
        'job_count': jobs['total'] if jobs else 0,
        'active_title': active.title.name if active else None,
        'ring': ring,
    }


def build_career_context(profile):
    """Assemble the full Career context for `profile`. Each zone is isolated so a failure
    degrades to a missing section rather than a 500. The jobs experience is built first because
    the hero reads its totals (Career Level + Total XP)."""
    context = {}
    jobs = None
    try:
        jobs = _build_jobs(profile)
    except Exception:
        logger.exception("Career jobs build failed for profile %s", getattr(profile, 'id', '?'))
    context['career'] = jobs
    context['total_xp_compact'] = _compact(jobs['total_xp']) if jobs else '0'
    try:
        context['hero'] = _build_hero(profile, jobs)
    except Exception:
        logger.exception("Career hero build failed for profile %s", getattr(profile, 'id', '?'))
        context['hero'] = None
    return context
