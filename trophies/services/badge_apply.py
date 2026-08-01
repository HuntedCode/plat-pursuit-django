"""Badge earn-lifecycle: diff (pure) + apply (writes).

`diff()` compares the engine's DesiredState against the profile's current UserGroupBadge rows and yields the
minimal set of changes (award / reactivate-from-maintenance / lapse / holo-flip) — pure, so it's unit-testable
like the engine. `apply_changes()` executes them inside one transaction: writes UserGroupBadge, stamps the
completion-ordered earn_rank, OWNS the earned_count denorm (no signals), and calls the thin title/event
adapters. `evaluate_and_apply()` ties eval -> diff -> apply for one profile; `evaluate_and_apply_batch()` does
many with completion-ordered ranks. See docs/design/rebuild/badge-backend-rebuild.md.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from django.db import transaction
from django.db.models import Count, F
from django.utils import timezone

from trophies.models import GroupBadge, UserGroupBadge
from trophies.services.badge_orchestrator import (
    resolve_group_badges, build_catalog, evaluate_with_catalog,
)
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
    """Execute changes for ONE profile. gb_map: {group_badge_id: GroupBadge with `series` select_related}.
    Awards append (rank = existing earners + 1) -- correct for a live earn (the newest completer). A BATCH
    routes awards through _apply_awards_ranked instead, to stamp completion-ordered ranks. Returns a summary."""
    result = {'awarded': [], 'reactivated': [], 'lapsed': [], 'holo_changed': []}
    for ch in changes:
        gb = gb_map[ch.group_badge_id]
        if ch.action == 'award':
            # (Same non-serialized-COUNT race the legacy system had; a single-profile apply has no in-batch tie.)
            rank = UserGroupBadge.objects.filter(group_badge=gb).count() + 1
            UserGroupBadge.objects.create(
                profile=profile, group_badge=gb, status='earned', is_holo=ch.holo, earn_rank=rank,
                earned_at=ch.earned_date or timezone.now(),   # the real completion moment, not the sync time
            )
            GroupBadge.objects.filter(id=gb.id).update(earned_count=F('earned_count') + 1)
            grant_series_title(profile.id, gb.series)
            emit_badge_earned(profile.id, gb.id)
            result['awarded'].append(gb.id)
        elif ch.action == 'reactivate':
            # maintenance -> earned; earn_rank is preserved (update never touches it).
            UserGroupBadge.objects.filter(profile=profile, group_badge=gb).update(status='earned', is_holo=ch.holo)
            GroupBadge.objects.filter(id=gb.id).update(earned_count=F('earned_count') + 1)
            grant_series_title(profile.id, gb.series)
            result['reactivated'].append(gb.id)
        elif ch.action == 'lapse':
            UserGroupBadge.objects.filter(profile=profile, group_badge=gb).update(status='maintenance', is_holo=False)
            GroupBadge.objects.filter(id=gb.id).update(earned_count=F('earned_count') - 1)
            revoke_series_title_if_orphaned(profile.id, gb.series)
            result['lapsed'].append(gb.id)
        elif ch.action == 'holo':
            UserGroupBadge.objects.filter(profile=profile, group_badge=gb).update(is_holo=ch.holo)
            result['holo_changed'].append(gb.id)
    return result


def _plan_with_catalog(profile, catalog) -> list:
    """Diff one profile against a PRE-BUILT catalog (no catalog re-fetch). The per-profile DB cost is the two
    completion reads (in evaluate_with_catalog) + one current-state read here."""
    desired = evaluate_with_catalog(profile, catalog)
    gb_ids = [gb.id for gb in catalog['group_badges']]
    current = {
        ugb.group_badge_id: CurrentBadge(ugb.status, ugb.is_holo)
        for ugb in UserGroupBadge.objects.filter(profile=profile, group_badge_id__in=gb_ids)
    }
    return diff(desired, current)


def plan(profile, group_badges=None):
    """Evaluate + diff WITHOUT writing. Returns (changes, gb_map). Public so a --dry-run runner (and the
    reconciliation harness) can preview what apply would do."""
    group_badges = resolve_group_badges(group_badges)
    if not group_badges:
        return [], {}
    catalog = build_catalog(group_badges)
    gb_map = {gb.id: gb for gb in group_badges}
    return _plan_with_catalog(profile, catalog), gb_map


def evaluate_and_apply(profile, group_badges=None) -> dict:
    """Single-profile entry point: evaluate, diff, apply. Returns the apply summary. Awards append (rank =
    existing earners + 1) -- correct for a genuine live earn. For processing MANY existing users, use
    evaluate_and_apply_batch, which orders ranks by completion date."""
    changes, gb_map = plan(profile, group_badges)
    return apply_changes(profile, changes, gb_map)


def evaluate_and_apply_batch(profiles, group_badges=None) -> Counter:
    """Process many profiles with COMPLETION-ORDERED earn_rank. The immutable catalog is built ONCE and reused
    per profile (no per-profile re-fetch). New awards across ALL profiles are collected, then applied in
    ascending completion-date order (tie-break: profile id), so #N reflects who finished FIRST -- the correct
    behavior whenever a badge is first processed for existing users (they completed it at various past dates).
    Non-award changes (reactivate / lapse / holo) apply per profile. Returns count totals."""
    group_badges = resolve_group_badges(group_badges)
    totals = Counter()
    if not group_badges:
        return totals
    catalog = build_catalog(group_badges)
    gb_map = {gb.id: gb for gb in group_badges}

    dated, undated = [], []   # lean records: (gb_id, profile_id, holo, earned_date) -- no Profile refs held
    for profile in profiles:
        changes = _plan_with_catalog(profile, catalog)
        non_awards = [c for c in changes if c.action != 'award']
        if non_awards:
            for key, ids in apply_changes(profile, non_awards, gb_map).items():
                totals[key] += len(ids)
        for c in changes:
            if c.action == 'award':
                rec = (c.group_badge_id, profile.id, c.holo, c.earned_date)
                (dated if c.earned_date is not None else undated).append(rec)

    dated.sort(key=lambda r: (r[3], r[1]))   # by completion date, then profile id (stable tie-break)
    _apply_awards_ranked(dated + undated, gb_map, totals)   # undated (no date) append last
    return totals


def _apply_awards_ranked(awards, gb_map: dict, totals: Counter) -> None:
    """Apply award records in the given (date-sorted) order, stamping earn_rank from an in-memory per-badge
    counter seeded by ONE aggregate query (instead of a COUNT per award). All awards land in a single
    transaction; earned_count is a single F() update per distinct badge; series titles dedup per profile."""
    if not awards:
        return
    next_rank = defaultdict(int, dict(
        UserGroupBadge.objects.filter(group_badge_id__in=gb_map.keys())
        .values('group_badge_id').annotate(c=Count('id')).values_list('group_badge_id', 'c')
    ))
    tally = defaultdict(int)          # gb_id -> awards this batch (one earned_count update each)
    granted = set()                   # (profile_id, series_id) titles already granted this batch
    with transaction.atomic():
        for gb_id, profile_id, holo, earned_date in awards:
            gb = gb_map[gb_id]
            next_rank[gb_id] += 1
            UserGroupBadge.objects.create(
                profile_id=profile_id, group_badge_id=gb_id, status='earned', is_holo=holo,
                earn_rank=next_rank[gb_id], earned_at=earned_date or timezone.now(),
            )
            tally[gb_id] += 1
            key = (profile_id, gb.series_id)
            if key not in granted:
                grant_series_title(profile_id, gb.series)
                granted.add(key)
            emit_badge_earned(profile_id, gb_id)
            totals['awarded'] += 1
        for gb_id, n in tally.items():
            GroupBadge.objects.filter(id=gb_id).update(earned_count=F('earned_count') + n)
