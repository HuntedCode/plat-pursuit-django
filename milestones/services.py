"""Milestone evaluation + Discord role reconciliation.

`recompute_milestones(profile)` is the single sweep: it computes each active milestone's metric once
(cached), awards newly-crossed tiers, writes the materialized progress read-model, bumps the rarity
counters, and (when a role-bearing tier was just earned) reconciles Discord roles. Whale-safe: one bounded
aggregate per distinct metric per profile.

Discord roles are reconciled (not fire-and-forget): `reconcile_discord_roles(profile)` computes the FULL
desired set and makes the member's milestone-managed roles exactly match it (add missing, remove superseded
-- highest-only). Called on a role-bearing crossing, on Discord link, and by the nightly safety-net.
"""
import logging

from django.core.cache import cache
from django.db import transaction
from django.db.models import F

from .metrics import MILESTONE_METRICS, metric_value
from .models import EarnedMilestoneTier, Milestone, MilestoneTier, UserMilestone

logger = logging.getLogger("milestones")

# Rarity denominator ("X% of hunters reached this") -- a cached total-hunters count, refreshed nightly so
# the per-render rarity is a plain division on denormalized numbers (no per-request COUNT). See spec §6.
TOTAL_HUNTERS_CACHE_KEY = 'milestones:total_hunters'

# Metrics we've already warned about this process, so a single misconfigured milestone doesn't log once
# per profile across a 100K-profile sweep.
_warned_unknown_metrics: set[str] = set()


def recompute_milestones(profile, *, reconcile_discord=True):
    """Recompute every active milestone for `profile`.

    Returns the list of newly-earned `MilestoneTier` rows (for notifications / callers). Idempotent: rungs
    already earned are never re-awarded. One aggregate per distinct metric (whale-safe).
    """
    actives = list(Milestone.objects.filter(is_active=True).prefetch_related('tiers'))
    if not actives:
        return []

    earned_tier_ids = set(
        EarnedMilestoneTier.objects
        .filter(profile=profile, tier__milestone__in=actives)
        .values_list('tier_id', flat=True)
    )

    metric_cache: dict[str, int] = {}
    newly: list[MilestoneTier] = []

    for m in actives:
        if m.metric not in MILESTONE_METRICS:
            if m.metric not in _warned_unknown_metrics:   # warn once per process, not once per profile
                _warned_unknown_metrics.add(m.metric)
                logger.warning("Milestone %r references unknown metric %r; skipping.", m.slug, m.metric)
            continue
        if m.metric not in metric_cache:
            metric_cache[m.metric] = metric_value(m.metric, profile)
        value = metric_cache[m.metric]

        # highest_tier_index ratchets off EARNED rungs (already-earned OR newly-crossed) -- never off the
        # current thresholds alone. So an upward threshold re-seed can't make this read-model under-report a
        # rung the permanent EarnedMilestoneTier record says was earned (spec §11: index is the authority, and
        # earned rows are never deleted).
        earned_indices = []
        for tier in sorted(m.tiers.all(), key=lambda t: t.index):
            already = tier.id in earned_tier_ids
            crossed = value >= tier.threshold
            if already or crossed:
                earned_indices.append(tier.index)
            if crossed and not already:
                newly.append(tier)

        UserMilestone.objects.update_or_create(
            profile=profile, milestone=m,
            defaults={'current_value': value, 'highest_tier_index': max(earned_indices, default=0)},
        )

    if newly:
        _award_tiers(profile, newly)

    # Reconcile whenever the caller asks: it's idempotent and converges the FULL desired set, so this is the
    # path --profile / Discord-link / cutover use to grant roles a hunter ALREADY earned (not just fresh
    # crossings). The high-frequency sync trigger (Phase 2) instead passes reconcile_discord=False and
    # reconciles itself only when a role-bearing tier was just crossed (avoids re-asserting roles every sync).
    if reconcile_discord:
        reconcile_discord_roles(profile)

    return newly


def _award_tiers(profile, tiers):
    """Persist the permanent earn records + bump the rarity counters."""
    with transaction.atomic():
        EarnedMilestoneTier.objects.bulk_create(
            [EarnedMilestoneTier(profile=profile, tier=t) for t in tiers],
            ignore_conflicts=True,
        )
        # F()-bump per-tier earned_count (rarity). Any concurrent-race drift is corrected by the nightly
        # recompute_tier_earned_counts (see the recompute_milestones command).
        MilestoneTier.objects.filter(id__in=[t.id for t in tiers]).update(earned_count=F('earned_count') + 1)


# ── Discord role reconciliation ─────────────────────────────────────────────────────────────────────────

def desired_milestone_roles(profile) -> set[int]:
    """The exact milestone Discord role ids the profile should hold now: per milestone with role-bearing
    tiers, the `discord_role_id` of the HIGHEST earned role-bearing rung (highest-only)."""
    rows = (
        EarnedMilestoneTier.objects
        .filter(profile=profile, tier__discord_role_id__isnull=False, tier__milestone__is_active=True)
        .values_list('tier__milestone_id', 'tier__index', 'tier__discord_role_id')
    )
    best: dict[int, tuple[int, int]] = {}   # milestone_id -> (tier_index, role_id)
    for milestone_id, index, role_id in rows:
        if milestone_id not in best or index > best[milestone_id][0]:
            best[milestone_id] = (index, role_id)
    return {role_id for _index, role_id in best.values()}


def managed_milestone_roles() -> set[int]:
    """Every discord_role_id configured across ALL milestone tiers (active or not) -- the universe
    reconciliation is allowed to touch, so it never disturbs non-milestone server roles.

    Retired (is_active=False) milestones are deliberately INCLUDED here but EXCLUDED from
    `desired_milestone_roles`: their role becomes managed-but-not-desired and is stripped on the next
    reconcile. (If it filtered on is_active, retiring a milestone would orphan its role forever.)"""
    return set(
        MilestoneTier.objects
        .filter(discord_role_id__isnull=False)
        .values_list('discord_role_id', flat=True)
    )


def reconcile_discord_roles(profile):
    """Make the profile's milestone-managed Discord roles exactly equal `desired_milestone_roles` -- add
    missing, remove superseded. Idempotent + self-healing. No-op unless the profile is Discord-verified.

    This is the robust version of the legacy "just send all their roles": it also removes stale brackets
    (needed for highest-only), and re-running is always safe. Fired on-commit so it never runs mid-transaction.
    """
    if not getattr(profile, 'is_discord_verified', False) or not getattr(profile, 'discord_id', None):
        return

    desired = desired_milestone_roles(profile)
    to_remove = managed_milestone_roles() - desired

    def _fire():
        # Imported here (not at module load) to avoid an import cycle with trophies.services.
        from trophies.services.badge_service import notify_bot_role_earned, notify_bot_role_removed
        for role_id in desired:
            notify_bot_role_earned(profile, role_id)
        for role_id in to_remove:
            notify_bot_role_removed(profile, role_id)

    transaction.on_commit(_fire)


def total_hunters() -> int:
    """The cached rarity denominator (registered-member count). 0 until the nightly refresh has run."""
    return cache.get(TOTAL_HUNTERS_CACHE_KEY) or 0


def refresh_total_hunters() -> int:
    """Recompute + cache the rarity denominator. Counts REGISTERED members only (user__isnull=False) --
    synced/scouted profiles without a site account are excluded so the '% of hunters' isn't skewed by
    sync noise. Called by the nightly sweep."""
    from trophies.models import Profile
    n = Profile.objects.filter(user__isnull=False).count()
    cache.set(TOTAL_HUNTERS_CACHE_KEY, n, None)   # no TTL; overwritten each nightly run
    return n


def tier_rarity_pct(tier_earned_count, denom=None):
    """Percent of hunters who reached a tier. None when the denominator isn't cached yet (hide the line)."""
    denom = total_hunters() if denom is None else denom
    if not denom:
        return None
    return round(tier_earned_count / denom * 100, 1)


def recompute_tier_earned_counts():
    """Nightly drift-correction: recompute every tier's denormalized `earned_count` from the source of
    truth. Bounded (one grouped COUNT over the catalog)."""
    from django.db.models import Count
    counts = dict(
        EarnedMilestoneTier.objects.values('tier_id').annotate(c=Count('id')).values_list('tier_id', 'c')
    )
    to_update = []
    for tier in MilestoneTier.objects.all().only('id', 'earned_count'):
        fresh = counts.get(tier.id, 0)
        if tier.earned_count != fresh:
            tier.earned_count = fresh
            to_update.append(tier)
    if to_update:
        MilestoneTier.objects.bulk_update(to_update, ['earned_count'])
    return len(to_update)
