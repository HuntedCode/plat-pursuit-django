"""Badge XP + progress: the sealed, swappable standing core for the new grouping-badge subsystem.

DECIDED model (design doc §5): flat XP per gating stage cleared + a flat badge-completion bonus, NO holo XP.
XP accrues PER GROUP BADGE (Legacy HD and Ultra HD are different games) and sums into a per-series total.
Progress (for the "chasers" leaderboard) is the furthest-along fraction over the series' group badges.

`compute_series_standings` is PURE (no ORM) -- fed the engine's per-series GroupBadgeResults, it returns
{series_slug: SeriesStanding}. `recompute_standing` is the one write seam: it recomputes a profile's standing
from scratch off the current DesiredState and upserts SeriesBadgeStanding (per series) + ProfileBadgeStanding
(the grand total). Everything is isolated from the legacy ProfileGamification.total_badge_xp.
"""
from dataclasses import dataclass
from collections import defaultdict

# Calibratable constants -- keep all XP magnitudes here so the model is a one-file swap.
# Calibrated to the "1,000,000 Club": over a projected mature catalog of ~400 group badges (~5 gating stages
# each -> ~3,100 XP/badge), a completionist lands ~1.24M, so 1M is reachable but hard (~80% of the catalog),
# with headroom above for two-version + holo elites. See test_million_club_calibration. Revisit if the catalog
# trajectory changes materially.
XP_PER_STAGE = 500                # per gating stage cleared (base-satisfied), a drip as you work a group
XP_BADGE_COMPLETION_BONUS = 600   # flat, once, when the base badge is earned


@dataclass(frozen=True)
class SeriesStanding:
    xp: int
    progress_bp: int          # furthest-along fraction over the series' group badges, basis points (0-10000)
    stages_cleared: int       # the best group's cleared gating stages (for "N of M" display)
    stages_total: int


def _group_badge_xp(result) -> int:
    """XP for ONE group badge from its GroupBadgeResult. base_satisfied_count is the number of GATING stages
    the profile cleared; the bonus lands once the whole base badge is earned. Holo contributes nothing."""
    xp = result.base_satisfied_count * XP_PER_STAGE
    if result.base_earned:
        xp += XP_BADGE_COMPLETION_BONUS
    return xp


def _fraction(result) -> float:
    return result.base_satisfied_count / result.gating_count if result.gating_count else 0.0


def compute_series_standings(results_by_series: dict) -> dict:
    """Pure. results_by_series: {series_slug: [GroupBadgeResult, ...]}. Returns {series_slug: SeriesStanding}.
    Series XP sums the group badges' XP; progress is the single best group's cleared/gating fraction."""
    out = {}
    for slug, results in results_by_series.items():
        xp = sum(_group_badge_xp(r) for r in results)
        best = max(results, key=_fraction, default=None)
        if best is not None and best.gating_count:
            cleared, total = best.base_satisfied_count, best.gating_count
            progress_bp = round(10000 * cleared / total)
        else:
            cleared = total = progress_bp = 0
        out[slug] = SeriesStanding(xp, progress_bp, cleared, total)
    return out


def compute_badge_xp(results_by_series: dict) -> tuple:
    """Pure convenience: (total_xp, {series_slug: xp}) derived from the standings."""
    per_series = {slug: s.xp for slug, s in compute_series_standings(results_by_series).items()}
    return sum(per_series.values()), per_series


def _results_by_series(desired: dict, group_badges) -> dict:
    """Group the engine's desired {group_badge_id: GroupBadgeResult} by series_slug using the GroupBadge rows."""
    by_series = defaultdict(list)
    for gb in group_badges:
        result = desired.get(gb.id)
        if result is not None:
            by_series[gb.series.series_slug].append(result)
    return by_series


def _upsert(model, lookup: dict, defaults: dict) -> None:
    """Cheap upsert (UPDATE, else INSERT) -- avoids update_or_create's savepoint + SELECT FOR UPDATE, which is
    heavy in the per-profile recompute. Safe because a profile's recompute is never concurrent with itself."""
    if not model.objects.filter(**lookup).update(**defaults):
        model.objects.create(**lookup, **defaults)


def recompute_standing(profile_id, desired: dict, group_badges) -> None:
    """Write seam: recompute the EVALUATED series' standings from the current DesiredState and upsert them.
    Only touches series present in `group_badges` (a scoped --series run leaves other series intact); a series
    that drops to 0 XP is removed. The grand total is re-summed from ALL the profile's series rows, so scoped
    runs keep it correct. Recompute-from-scratch, so it can't drift."""
    from django.db.models import Sum
    from trophies.models import ProfileBadgeStanding, SeriesBadgeStanding

    standings = compute_series_standings(_results_by_series(desired, group_badges))
    positive = {slug: s for slug, s in standings.items() if s.xp > 0}
    zeroed = [slug for slug, s in standings.items() if s.xp == 0]

    for slug, s in positive.items():
        _upsert(SeriesBadgeStanding, {'profile_id': profile_id, 'series_slug': slug},
                {'xp': s.xp, 'progress_bp': s.progress_bp,
                 'stages_cleared': s.stages_cleared, 'stages_total': s.stages_total})
    if zeroed:
        SeriesBadgeStanding.objects.filter(profile_id=profile_id, series_slug__in=zeroed).delete()

    total = SeriesBadgeStanding.objects.filter(profile_id=profile_id).aggregate(t=Sum('xp'))['t'] or 0
    if total > 0:
        _upsert(ProfileBadgeStanding, {'profile_id': profile_id}, {'total_xp': total})
    else:
        ProfileBadgeStanding.objects.filter(profile_id=profile_id).delete()
