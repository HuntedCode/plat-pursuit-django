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

from trophies.models import ProgressionMilestone, UserTitle
from trophies.services import job_render
from trophies.util_modules.leveling import JOB_TIERS, pursuer_rank_ladder

logger = logging.getLogger(__name__)


def _build_jobs(profile):
    """The jobs zone: the profile's jobs/disciplines view (skills grid, radar data,
    composition summary), assembled from real ProfileJobXP via the job foundation."""
    return job_render.build_profile_jobs(profile)


def _tiers_earned(jobs):
    """Total prestige tiers held across all jobs = each job's current tier index (the Initiate floor
    excluded), summed. Derived from live levels so it always matches the visible tier state -- unlike
    the forward-only milestone log, which is the dated journey, not a current-state count."""
    if not jobs or not jobs.get('disciplines'):
        return 0
    return sum(
        sum(1 for min_lvl, _key, _name in JOB_TIERS if min_lvl <= t['level']) - 1
        for d in jobs['disciplines'] for t in d['jobs']
    )


def _job_tier_dates(profile):
    """{job_slug: {tier_key: reached_at}} for the profile in one query -- the per-job-detail tier
    ladders read this map (rather than each running its own milestone query)."""
    out = {}
    for slug, key, when in (
        ProgressionMilestone.objects
        .filter(profile=profile, kind=ProgressionMilestone.JOB_TIER)
        .values_list('job__slug', 'key', 'reached_at')
    ):
        out.setdefault(slug, {})[key] = when
    return out


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
    """The Pursuer hero: job identity at a glance. Pursuer Level + Total XP come from the jobs
    totals (the single source of truth, level-1 floor applied). The disciplines ring frames the
    Pursuer Level with a donut whose discipline arcs are each discipline's SHARE of the total
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
                'label': d['label'], 'slug': d['slug'], 'avg': d['avg'], 'total': disc_total,
                'share_pct': round(share * 100), 'dash': dash, 'offset': round(-cumulative, 2),
            })
            cumulative += dash
    pursuer_level = jobs['total_level'] if jobs else 0
    # The Pursuer rank ladder (all 11 rungs + current position), with each reached rung's date from
    # the milestone log so the hero shows the journey, not just the current label.
    rank_ladder = pursuer_rank_ladder(pursuer_level)
    reached = dict(
        ProgressionMilestone.objects
        .filter(profile=profile, kind=ProgressionMilestone.PURSUER_RANK)
        .values_list('key', 'reached_at')
    )
    for rung in rank_ladder['rungs']:
        rung['reached_at'] = reached.get(rung['key'])
    # Dominant discipline (highest average level): tints the hero's ambient glow AND labels the
    # identity chip ("Leads with Combat"). Only once you've earned some XP, so a fresh Pursuer stays
    # neutral (a "New Pursuer" fallback in the template).
    dominant = None
    if jobs and jobs.get('total_xp') and jobs.get('disciplines'):
        d = max(jobs['disciplines'], key=lambda d: d['avg'])
        dominant = {'slug': d['slug'], 'label': d['label']}
    return {
        'pursuer_name': profile.display_psn_username,
        'avatar_url': profile.avatar_url,
        'pursuer_level': pursuer_level,
        'pursuer_rank': rank_ladder['current'],
        'rank_ladder': rank_ladder,
        'total_job_xp': jobs['total_xp'] if jobs else 0,
        'job_count': jobs['total'] if jobs else 0,
        'active_title': active.title.name if active else None,
        'dominant_disc': dominant['slug'] if dominant else None,   # ambient-glow tint
        'dominant': dominant,                                      # {slug, label} for the identity chip
        'ring': ring,
    }


def build_career_context(profile):
    """Assemble the full Career context for `profile`. Each zone is isolated so a failure
    degrades to a missing section rather than a 500. The jobs experience is built first because
    the hero reads its totals (Pursuer Level + Total XP)."""
    context = {}
    jobs = None
    try:
        jobs = _build_jobs(profile)
    except Exception:
        logger.exception("Career jobs build failed for profile %s", getattr(profile, 'id', '?'))
    context['career'] = jobs
    context['total_xp_compact'] = _compact(jobs['total_xp']) if jobs else '0'
    context['job_tier_dates'] = _job_tier_dates(profile)   # {job_slug: {tier_key: reached_at}} for the modals
    context['tiers_earned'] = _tiers_earned(jobs)          # prestige tiers held across all jobs (a stat-card aggregate)
    try:
        context['hero'] = _build_hero(profile, jobs)
    except Exception:
        logger.exception("Career hero build failed for profile %s", getattr(profile, 'id', '?'))
        context['hero'] = None
    return context
