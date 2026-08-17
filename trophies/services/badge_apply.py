"""Badge earn-lifecycle: diff (pure) + apply (writes).

`diff()` compares the engine's DesiredState against the profile's currently-held UserGroupBadge rows and yields
the minimal set of changes — pure, so it's unit-testable like the engine. Binary model: award (create the held
row) / revoke (delete it) / update (resync is_holo + earned_at). There is NO permanent earn_rank; the earners
rank is derived live from earned_at (current holders ordered by completion date), so awards need no ordering.
`apply_changes()` executes changes in one transaction and OWNS the earned_count denorm (no signals); the
eval->diff->apply entry points also recompute badge XP. See docs/design/rebuild/badge-backend-rebuild.md.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from trophies.models import GroupBadge, UserGroupBadge
from trophies.services.badge_orchestrator import (
    resolve_group_badges, build_catalog, evaluate_with_catalog,
)
from trophies.services.badge_adapters import (
    grant_series_title, revoke_series_title_if_orphaned, emit_badge_earned, announce_badges_earned,
)
from trophies.services.badge_xp import recompute_standing


@dataclass(frozen=True)
class CurrentBadge:
    is_holo: bool
    earned_at: Optional[datetime]


@dataclass(frozen=True)
class BadgeChange:
    group_badge_id: int
    action: str          # 'award' | 'revoke' | 'update'
    holo: bool
    earned_date: Optional[datetime] = None


def diff(desired: dict, current: dict) -> list:
    """Pure. desired: {group_badge_id: GroupBadgeResult}; current: {group_badge_id: CurrentBadge} for the rows
    the profile CURRENTLY holds. Binary model: hold iff base_earned. Only evaluated badges (desired's keys) are
    considered. 'update' resyncs a held row's is_holo / earned_at (the latter shifts when the badge iteration
    changes, keeping the leaderboard sort honest)."""
    changes = []
    for gb_id, res in desired.items():
        cur = current.get(gb_id)
        if res.base_earned:
            if cur is None:
                changes.append(BadgeChange(gb_id, 'award', res.holo, res.earned_date))
            elif cur.is_holo != res.holo or cur.earned_at != res.earned_date:
                changes.append(BadgeChange(gb_id, 'update', res.holo, res.earned_date))
            # else: held, holo + earned_at already match -> no change
        else:
            if cur is not None:
                changes.append(BadgeChange(gb_id, 'revoke', False))
            # else: not held and shouldn't be -> no change
    return changes


@transaction.atomic
def apply_changes(profile, changes, gb_map: dict) -> dict:
    """Execute changes for ONE profile. gb_map: {group_badge_id: GroupBadge with `series` select_related}.
    Binary model: award creates the held row, revoke deletes it, update resyncs is_holo/earned_at. This layer
    OWNS the earned_count denorm (no signals). Returns a summary."""
    result = {'awarded': [], 'revoked': [], 'updated': []}
    for ch in changes:
        gb = gb_map[ch.group_badge_id]
        if ch.action == 'award':
            UserGroupBadge.objects.create(
                profile=profile, group_badge=gb, is_holo=ch.holo,
                earned_at=ch.earned_date or timezone.now(),   # current-iteration completion, not the sync time
            )
            GroupBadge.objects.filter(id=gb.id).update(earned_count=F('earned_count') + 1)
            grant_series_title(profile.id, gb.series)
            emit_badge_earned(profile.id, gb.id)
            result['awarded'].append(gb.id)
        elif ch.action == 'revoke':
            UserGroupBadge.objects.filter(profile=profile, group_badge=gb).delete()
            GroupBadge.objects.filter(id=gb.id).update(earned_count=F('earned_count') - 1)
            revoke_series_title_if_orphaned(profile.id, gb.series)
            result['revoked'].append(gb.id)
        elif ch.action == 'update':
            # Held badge stays held: resync the live flags. earned_at moves when the iteration changes.
            UserGroupBadge.objects.filter(profile=profile, group_badge=gb).update(
                is_holo=ch.holo, earned_at=ch.earned_date or timezone.now(),
            )
            result['updated'].append(gb.id)
    return result


def _plan_with_catalog(profile, catalog):
    """Diff one profile against a PRE-BUILT catalog (no catalog re-fetch). Returns (changes, desired) -- the
    desired state is also fed to the XP recompute. Per-profile DB cost: the two completion reads (in
    evaluate_with_catalog) + one current-state read here."""
    desired = evaluate_with_catalog(profile, catalog)
    gb_ids = [gb.id for gb in catalog['group_badges']]
    current = {
        ugb.group_badge_id: CurrentBadge(ugb.is_holo, ugb.earned_at)
        for ugb in UserGroupBadge.objects.filter(profile=profile, group_badge_id__in=gb_ids)
    }
    return diff(desired, current), desired


def plan(profile, group_badges=None):
    """Evaluate + diff WITHOUT writing (no XP recompute either). Returns (changes, gb_map). Public so a
    --dry-run runner can preview what apply would do."""
    group_badges = resolve_group_badges(group_badges)
    if not group_badges:
        return [], {}
    catalog = build_catalog(group_badges)
    gb_map = {gb.id: gb for gb in group_badges}
    changes, _ = _plan_with_catalog(profile, catalog)
    return changes, gb_map


def evaluate_and_apply(profile, group_badges=None, notify=False) -> dict:
    """Single-profile entry point: evaluate, diff, apply, recompute XP. Returns the apply summary.

    `notify` is OPT-IN and defaults off. The Discord announcement belongs to the live sync path, not to the
    function: `evaluate_badges --all` calls this once per profile across the whole population, and a default
    of True would turn a backfill into tens of thousands of webhook sends. The one caller that wants it is
    the one reacting to a hunter actually playing.
    """
    group_badges = resolve_group_badges(group_badges)
    if not group_badges:
        return {'awarded': [], 'revoked': [], 'updated': []}
    catalog = build_catalog(group_badges)
    gb_map = {gb.id: gb for gb in group_badges}
    changes, desired = _plan_with_catalog(profile, catalog)
    result = apply_changes(profile, changes, gb_map)
    recompute_standing(profile.id, desired, group_badges)   # XP tracks current progress, not just earns
    if notify and result['awarded']:
        # ONE consolidated announcement for the whole run, after the writes land -- not one per badge from
        # inside apply_changes, which would ping a hunter three times for finishing a three-edition series.
        announce_badges_earned(profile, [gb_map[gb_id] for gb_id in result['awarded'] if gb_id in gb_map])
    return result


def evaluate_for_touched_games(profile, profilegame_ids, notify=True) -> dict:
    """The SYNC entry point: evaluate only the series the just-synced games belong to.

    Scoped, because a sync touches a handful of games and the catalogue is the whole badge system --
    re-evaluating every live badge on every sync would put the cost of the catalogue on every hunter who
    plays anything. Same narrowing the legacy `check_profile_badges` does, from the same starting point.

    Scoped by SERIES, never by individual badge, and that is load-bearing rather than incidental:
    `recompute_standing` REPLACES a series' standing from only the editions it is handed, so a scope that
    caught Ultra HD but not Legacy HD would undercount that series' XP and drop the sibling edition's
    `group_progress`. Filtering `series__series_slug__in=...` returns every live edition of every touched
    series by construction.

    A badge authored after a hunter's last sync does not evaluate for them until they next touch one of its
    games -- the legacy path has the same property, and `evaluate_badges --series <slug>` is the existing
    answer for backfilling a newly authored series.

    BOTH qualifier paths are followed. A Stage reaches its games through `concepts` AND through
    `concept_bundles -> concepts`, and `ConceptBundle`'s own membership rule is that a concept must NOT
    appear in both -- so they are disjoint by construction and matching only `concepts` cannot merely miss
    the occasional bundled game, it misses EVERY one of them. `build_catalog` walks both; so must this, or
    a hunter who finishes a bundled game gets no evaluation at all and nothing errors.
    """
    from django.db.models import Q
    from trophies.models import ProfileGame, Stage

    if not profilegame_ids:
        return {'awarded': [], 'revoked': [], 'updated': []}

    concept_ids = list(
        ProfileGame.objects.filter(id__in=profilegame_ids, profile=profile)
        .exclude(game__concept__isnull=True)
        .values_list('game__concept_id', flat=True).distinct()
    )
    series_slugs = list(
        Stage.objects.filter(
            Q(concepts__id__in=concept_ids) | Q(concept_bundles__concepts__id__in=concept_ids)
        ).values_list('series_slug', flat=True).distinct()
    )
    if not series_slugs:
        return {'awarded': [], 'revoked': [], 'updated': []}

    return evaluate_and_apply(
        profile,
        GroupBadge.objects.filter(is_live=True, series__series_slug__in=series_slugs),
        notify=notify,
    )


def evaluate_and_apply_batch(profiles, group_badges=None) -> Counter:
    """Process many profiles. The immutable catalog is built ONCE and reused per profile (no per-profile
    re-fetch). No rank stamping: earners rank is derived live from earned_at, so awards need no ordering.
    Returns count totals."""
    group_badges = resolve_group_badges(group_badges)
    totals = Counter()
    if not group_badges:
        return totals
    catalog = build_catalog(group_badges)
    gb_map = {gb.id: gb for gb in group_badges}

    for profile in profiles:
        changes, desired = _plan_with_catalog(profile, catalog)
        if changes:
            for key, ids in apply_changes(profile, changes, gb_map).items():
                totals[key] += len(ids)
        recompute_standing(profile.id, desired, group_badges)   # XP recompute is independent of the earn writes
    return totals
