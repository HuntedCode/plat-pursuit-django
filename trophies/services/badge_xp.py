"""Badge XP: the sealed, swappable XP core for the new grouping-badge subsystem.

DECIDED model (design doc §5): flat XP per gating stage cleared + a flat badge-completion bonus, NO holo XP.
XP accrues PER GROUP BADGE (Legacy HD and Ultra HD are different games, so clearing both is real extra work)
and sums into a per-series total for the leaderboards.

`compute_badge_xp` is PURE (no ORM) -- fed the engine's per-series GroupBadgeResults, it returns
`(total, {series_slug: xp})`. `recompute_standing` is the one write seam: it recomputes a profile's standing
from scratch off the current DesiredState (so it can't drift) and upserts ProfileBadgeStanding. Everything is
isolated from the legacy ProfileGamification.total_badge_xp (tier-based; repointed at cutover).
"""
from collections import defaultdict

# Calibratable constants -- keep all XP magnitudes here so the model is a one-file swap.
XP_PER_STAGE = 100                # per gating stage cleared (base-satisfied), a drip as you work a group
XP_BADGE_COMPLETION_BONUS = 500   # flat, once, when the base badge is earned


def _group_badge_xp(result) -> int:
    """XP for ONE group badge from its GroupBadgeResult. base_satisfied_count is the number of GATING stages
    the profile cleared; the bonus lands once the whole base badge is earned. Holo contributes nothing."""
    xp = result.base_satisfied_count * XP_PER_STAGE
    if result.base_earned:
        xp += XP_BADGE_COMPLETION_BONUS
    return xp


def compute_badge_xp(results_by_series: dict) -> tuple:
    """Pure. results_by_series: {series_slug: [GroupBadgeResult, ...]} (a series' group badges for one profile).
    Returns (total_xp, {series_slug: series_xp}). Series XP is the sum of its group badges' XP."""
    per_series = {
        slug: sum(_group_badge_xp(r) for r in results)
        for slug, results in results_by_series.items()
    }
    return sum(per_series.values()), per_series


def _results_by_series(desired: dict, group_badges) -> dict:
    """Group the engine's desired {group_badge_id: GroupBadgeResult} by series_slug using the GroupBadge rows."""
    by_series = defaultdict(list)
    for gb in group_badges:
        result = desired.get(gb.id)
        if result is not None:
            by_series[gb.series.series_slug].append(result)
    return by_series


def recompute_standing(profile_id, desired: dict, group_badges) -> None:
    """Write seam: recompute the EVALUATED series' XP from the current DesiredState and merge into the profile's
    ProfileBadgeStanding. Only touches the series present in `group_badges` (a scoped --series run leaves other
    series' XP intact); a series that drops to 0 is removed. Recompute-from-scratch, so it can't go stale."""
    from trophies.models import ProfileBadgeStanding

    _, per_series = compute_badge_xp(_results_by_series(desired, group_badges))
    positive = {slug: xp for slug, xp in per_series.items() if xp > 0}
    zeroed = [slug for slug, xp in per_series.items() if xp == 0]

    standing = ProfileBadgeStanding.objects.filter(profile_id=profile_id).first()
    if standing is None:
        if not positive:
            return   # nothing to store and no row to update -- don't create empty standings
        standing = ProfileBadgeStanding(profile_id=profile_id, series_xp={})

    changed = standing.pk is None
    for slug, xp in positive.items():
        if standing.series_xp.get(slug) != xp:
            standing.series_xp[slug] = xp
            changed = True
    for slug in zeroed:
        if slug in standing.series_xp:
            del standing.series_xp[slug]
            changed = True

    total = sum(standing.series_xp.values())
    if changed or standing.total_xp != total:
        standing.total_xp = total
        standing.save()
