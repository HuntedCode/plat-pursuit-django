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

from django.urls import reverse
from django.utils.http import urlencode

from trophies.models import ProgressionMilestone, UserTitle
from trophies.services import job_render
from trophies.services.contract_service import pursuer_level_from
from trophies.util_modules.constants import ALL_PLATFORMS
from trophies.util_modules.leveling import JOB_TIERS, pursuer_rank_ladder, pursuer_rank_table

logger = logging.getLogger(__name__)


def claim_board_url():
    """Career's Contracts board on Ready to Claim with every platform lit: where every claim entry point
    lands (the claim banner's href on Career and the lobby, its JS jump, the first-sync finale's "Claim your
    contracts", job detail's Claim link). Every platform because the
    claimable count ignores platform while the board defaults to current-gen, so a new tab, a copied
    link or a no-JS tap still finds the N it was promised."""
    return reverse('career') + '?' + urlencode(
        [('view', 'contracts'), ('status', 'claimable')] + [('platform', p) for p in ALL_PLATFORMS])


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


# THE RING'S CIRCUMFERENCE AND ITS ARC ARITHMETIC NOW LIVE IN `job_render.discipline_ring`, because this
# is no longer the only host: the Hall of Fame plaque draws the same ring from page-wide aggregates, and
# `_disciplines_ring.html` promises the second host is "a scale, not a second implementation". A private
# `_RING_C` here forced that host to choose between importing a private name and copying the constant.


def _build_hero(profile, jobs):
    """The Pursuer hero: job identity at a glance. Pursuer Level + Total XP come from the jobs
    totals (the single source of truth). The disciplines ring frames the Pursuer Level with a donut
    whose discipline arcs are each discipline's SHARE of the levels gained; `dash`/`offset` are
    precomputed stroke-dash segments so the template just renders them."""
    active = (
        UserTitle.objects
        .filter(profile=profile, is_displayed=True)
        .select_related('title')
        .first()
    )
    ring = []
    if jobs:
        # Each arc is a discipline's share of the levels GAINED (each tile's level - 1), the same quantity
        # the Pursuer Level in the ring's centre counts, so the arcs add up to that number less its
        # starting 1. A newcomer has gained nothing anywhere, which the helper draws as an even split.
        ring = job_render.discipline_ring([
            {'label': d['label'], 'slug': d['slug'], 'avg': d['avg'],
             'total': sum(t['level'] - 1 for t in d['jobs'])}
            for d in jobs['disciplines']
        ])
    # With no jobs data, the starting level, never 0: no Pursuer is below 1, and at 0 the rank ladder
    # would show Newbie as not yet reached.
    pursuer_level = jobs['total_level'] if jobs else pursuer_level_from(None)
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
    # The full table behind the bar (every tier with its division floors), for the rank ladder modal the
    # bar opens. Same dates, so a reached tier says when in both places.
    rank_table = pursuer_rank_table(pursuer_level)
    for tier in rank_table['tiers']:
        tier['reached_at'] = reached.get(tier['key'])
    # Dominant discipline (highest average level): tints the hero's ambient glow AND labels the
    # identity chip ("Leads with Combat"). Only once you've earned some XP, so a fresh Pursuer stays
    # neutral (a "New Pursuer" fallback in the template).
    dominant = None
    if jobs and jobs.get('total_xp') and jobs.get('disciplines'):
        d = max(jobs['disciplines'], key=lambda d: d['avg'])
        dominant = {'slug': d['slug'], 'label': d['label']}
    return {
        'pursuer_name': profile.display_psn_username,
        'display_mark': profile.display_mark,
        'avatar_url': profile.avatar_url,
        'pursuer_level': pursuer_level,
        'pursuer_rank': rank_ladder['current'],
        'rank_ladder': rank_ladder,
        'rank_table': rank_table,
        'total_job_xp': jobs['total_xp'] if jobs else 0,
        'job_count': jobs['total'] if jobs else 0,
        'active_title': active.title.name if active else None,
        'dominant_disc': dominant['slug'] if dominant else None,   # ambient-glow tint
        'dominant': dominant,                                      # {slug, label} for the identity chip
        'ring': ring,
        # The legend keys only the disciplines with levels gained: a "+0" row keys a segment the ring does not
        # draw (owner, 2026-10-27, review DL-13; it listed all five before). A newcomer's is empty, so no legend.
        'ring_gained': [f for f in (ring or []) if f.get('total')],
    }


def build_hero_context(profile):
    """The Pursuer hero alone: jobs totals + the hero dict + compact XP, with the same per-zone
    isolation as the full build. For surfaces (the Profile Card) that need the identity but none
    of the tier dates or per-discipline radar payloads the Career page itself renders -- calling
    the full build there paid two extra milestone queries per render for keys nothing read."""
    context = {}
    jobs = None
    try:
        jobs = _build_jobs(profile)
    except Exception:
        logger.exception("Career jobs build failed for profile %s", getattr(profile, 'id', '?'))
    context['career'] = jobs
    context['total_xp_compact'] = _compact(jobs['total_xp']) if jobs else '0'
    context['tiers_earned'] = _tiers_earned(jobs)   # prestige tiers held across all jobs (free: pure Python)
    try:
        context['hero'] = _build_hero(profile, jobs)
    except Exception:
        logger.exception("Career hero build failed for profile %s", getattr(profile, 'id', '?'))
        context['hero'] = None
    return context


def build_career_context(profile):
    """Assemble the full Career context for `profile`. Each zone is isolated so a failure
    degrades to a missing section rather than a 500. The jobs experience is built first because
    the hero reads its totals (Pursuer Level + Total XP)."""
    context = build_hero_context(profile)
    context['job_tier_dates'] = _job_tier_dates(profile)   # {job_slug: {tier_key: reached_at}} for the modals
    return context
