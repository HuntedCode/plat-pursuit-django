"""Badge earn-lifecycle: diff (pure) + apply (writes).

`diff()` compares the engine's DesiredState against the profile's current UserGroupBadge rows and yields the
minimal set of changes (award / reactivate-from-maintenance / lapse / holo-flip) — pure, so it's unit-testable
like the engine. `apply_changes()` executes them inside one transaction: writes UserGroupBadge, stamps the
completion-ordered earn_rank, OWNS the earned_count denorm (no signals), and calls the thin title/event
adapters. `evaluate_and_apply()` ties eval -> diff -> apply. See docs/design/rebuild/badge-backend-rebuild.md.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from django.db import transaction
from django.db.models import F, QuerySet
from django.utils import timezone

from trophies.models import GroupBadge, UserGroupBadge
from trophies.services.badge_orchestrator import evaluate_profile
from trophies.services.badge_adapters import (
    grant_series_title, revoke_series_title_if_orphaned, emit_badge_earned,
)


@dataclass(frozen=True)
class CurrentBadge:
    status: str          # 'earned' | 'maintenance'
    is_holo: bool


@dataclass(frozen=True)
class BadgeChange:
    group_badge_id: int
    action: str          # 'award' | 'reactivate' | 'lapse' | 'holo'
    holo: bool
    earned_date: Optional[datetime] = None


def diff(desired: dict, current: dict) -> list:
    """Pure. desired: {group_badge_id: GroupBadgeResult}; current: {group_badge_id: CurrentBadge}. Only the
    evaluated badges (desired's keys) are considered — a badge we didn't evaluate is never lapsed."""
    changes = []
    for gb_id, res in desired.items():
        cur = current.get(gb_id)
        if res.base_earned:
            if cur is None:
                changes.append(BadgeChange(gb_id, 'award', res.holo, res.earned_date))
            elif cur.status == 'maintenance':
                changes.append(BadgeChange(gb_id, 'reactivate', res.holo, res.earned_date))
            elif cur.is_holo != res.holo:
                changes.append(BadgeChange(gb_id, 'holo', res.holo))
            # else: earned + holo already matches -> no change
        else:
            if cur is not None and cur.status == 'earned':
                changes.append(BadgeChange(gb_id, 'lapse', False))
            # else: no row, or already maintenance -> no change (no-delete, no re-lapse)
    return changes


@transaction.atomic
def apply_changes(profile, changes, gb_map: dict) -> dict:
    """Execute changes. gb_map: {group_badge_id: GroupBadge with `series` select_related}. Returns a summary."""
    result = {'awarded': [], 'reactivated': [], 'lapsed': [], 'holo_changed': []}
    for ch in changes:
        gb = gb_map[ch.group_badge_id]
        if ch.action == 'award':
            # Completion-ordered rank: a LIVE earn is the newest completer, so rank = existing earners + 1
            # (maintenance rows count -- they completed it once). Backfill seeds HISTORICAL ranks by date.
            # (Same non-serialized-COUNT race the legacy system had; single-profile apply has no in-batch tie.)
            rank = UserGroupBadge.objects.filter(group_badge=gb).count() + 1
            UserGroupBadge.objects.create(
                profile=profile, group_badge=gb, status='earned', is_holo=ch.holo, earn_rank=rank,
                earned_at=ch.earned_date or timezone.now(),   # the real completion moment, not the sync time
            )
            GroupBadge.objects.filter(id=gb.id).update(earned_count=F('earned_count') + 1)
            grant_series_title(profile, gb.series)
            emit_badge_earned(profile, gb)
            result['awarded'].append(gb.id)
        elif ch.action == 'reactivate':
            # maintenance -> earned; earn_rank is preserved (update never touches it).
            UserGroupBadge.objects.filter(profile=profile, group_badge=gb).update(status='earned', is_holo=ch.holo)
            GroupBadge.objects.filter(id=gb.id).update(earned_count=F('earned_count') + 1)
            grant_series_title(profile, gb.series)
            result['reactivated'].append(gb.id)
        elif ch.action == 'lapse':
            UserGroupBadge.objects.filter(profile=profile, group_badge=gb).update(status='maintenance', is_holo=False)
            GroupBadge.objects.filter(id=gb.id).update(earned_count=F('earned_count') - 1)
            revoke_series_title_if_orphaned(profile, gb.series)
            result['lapsed'].append(gb.id)
        elif ch.action == 'holo':
            UserGroupBadge.objects.filter(profile=profile, group_badge=gb).update(is_holo=ch.holo)
            result['holo_changed'].append(gb.id)
    return result


def evaluate_and_apply(profile, group_badges=None) -> dict:
    """Entry point: evaluate the profile, diff against current earns, apply. Returns the apply summary."""
    if group_badges is None:
        group_badges = GroupBadge.objects.filter(is_live=True)
    if isinstance(group_badges, QuerySet):
        group_badges = group_badges.select_related('series', 'platform_group')   # gb.series is read in apply
    group_badges = list(group_badges)
    if not group_badges:
        return {'awarded': [], 'reactivated': [], 'lapsed': [], 'holo_changed': []}

    desired = evaluate_profile(profile, group_badges)
    gb_map = {gb.id: gb for gb in group_badges}
    current = {
        ugb.group_badge_id: CurrentBadge(ugb.status, ugb.is_holo)
        for ugb in UserGroupBadge.objects.filter(profile=profile, group_badge_id__in=gb_map.keys())
    }
    return apply_changes(profile, diff(desired, current), gb_map)
