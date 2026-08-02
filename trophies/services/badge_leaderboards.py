"""Badge leaderboards: the sealed READ layer over the standing stores (Phase 4, Lane B).

All boards are live DB reads over denormalized, indexed stores -- no Redis, no rebuild cron. They stay in the
sealed subsystem and are written by the recompute the sync/apply path already runs (see badge_xp.py).

  Global XP        -> ProfileBadgeStanding.total_xp
  Per-series XP    -> SeriesBadgeStanding (series_slug, xp)          [sbs_series_xp_idx]
  Per-series chase -> SeriesBadgeStanding (series_slug, progress_bp) [sbs_series_prog_idx]
  Per-badge earners-> UserGroupBadge (group_badge, earned_at)        [ugb_badge_earned_idx]  (rank == earned order)

rank_of / earners_rank return a profile's LIVE position (the value shown on the medallion back); they're single
indexed reads, whale-safe. rows(...) returns a page of (profile_id, value) for rendering.
"""
from trophies.models import ProfileBadgeStanding, SeriesBadgeStanding, UserGroupBadge


# ------------------------------------------------------------------ global XP ----------------------------

def xp_rows(limit=50, offset=0):
    """Top profiles by total badge XP: [(profile_id, total_xp), ...]."""
    return list(
        ProfileBadgeStanding.objects.order_by('-total_xp', 'profile_id')
        .values_list('profile_id', 'total_xp')[offset:offset + limit]
    )


def xp_rank(profile_id):
    """A profile's 1-based position on the global XP board, or None if they have no standing."""
    mine = ProfileBadgeStanding.objects.filter(profile_id=profile_id).values_list('total_xp', flat=True).first()
    if mine is None:
        return None
    return ProfileBadgeStanding.objects.filter(total_xp__gt=mine).count() + 1


# ------------------------------------------------------------------ per-series XP / progress -------------

def series_xp_rows(series_slug, limit=50, offset=0):
    """Top profiles by XP in one series: [(profile_id, xp), ...]."""
    return list(
        SeriesBadgeStanding.objects.filter(series_slug=series_slug).order_by('-xp', 'profile_id')
        .values_list('profile_id', 'xp')[offset:offset + limit]
    )


def series_progress_rows(series_slug, limit=50, offset=0):
    """The 'chasers' board: profiles by furthest-along progress in one series:
    [(profile_id, progress_bp, stages_cleared, stages_total), ...]."""
    return list(
        SeriesBadgeStanding.objects.filter(series_slug=series_slug).order_by('-progress_bp', 'profile_id')
        .values_list('profile_id', 'progress_bp', 'stages_cleared', 'stages_total')[offset:offset + limit]
    )


def series_rank(series_slug, profile_id):
    """A profile's 1-based XP position within a series, or None if they have no standing there."""
    mine = (
        SeriesBadgeStanding.objects.filter(series_slug=series_slug, profile_id=profile_id)
        .values_list('xp', flat=True).first()
    )
    if mine is None:
        return None
    return SeriesBadgeStanding.objects.filter(series_slug=series_slug, xp__gt=mine).count() + 1


# ------------------------------------------------------------------ per-badge earners --------------------

def earners_rows(group_badge_id, limit=50, offset=0):
    """First-to-complete order for one group badge: [(profile_id, earned_at), ...] earliest first."""
    return list(
        UserGroupBadge.objects.filter(group_badge_id=group_badge_id).order_by('earned_at', 'profile_id')
        .values_list('profile_id', 'earned_at')[offset:offset + limit]
    )


def earners_rank(profile_id, group_badge_id):
    """A profile's LIVE earners position for a group badge (1 = first to complete the current iteration), or
    None if they don't currently hold it. This is the value shown on the medallion back."""
    mine = (
        UserGroupBadge.objects.filter(group_badge_id=group_badge_id, profile_id=profile_id)
        .values_list('earned_at', flat=True).first()
    )
    if mine is None:
        return None
    return UserGroupBadge.objects.filter(group_badge_id=group_badge_id, earned_at__lt=mine).count() + 1


def earners_ranks(profile_id, group_badge_ids):
    """Batched earners_rank for a profile's set of badges (e.g. a medallion grid): {group_badge_id: rank}. Held
    badges only; each is one bounded indexed COUNT, so this is len(held) queries -- fine for a page's medallions."""
    held = dict(
        UserGroupBadge.objects.filter(profile_id=profile_id, group_badge_id__in=group_badge_ids)
        .values_list('group_badge_id', 'earned_at')
    )
    return {
        gb_id: UserGroupBadge.objects.filter(group_badge_id=gb_id, earned_at__lt=at).count() + 1
        for gb_id, at in held.items()
    }
