"""Milestone metrics — the one thing that needs code.

A metric is a named function returning a SINGLE whale-safe per-profile aggregate (a `.count()`,
`.aggregate(Sum/Count(distinct))`, or a denormalized field). Milestones (data) point at a metric by key;
adding a milestone on an existing metric needs no code, only a genuinely new *measurement* does.

CONTRACT (enforced by review): a metric MUST be a single bounded aggregate — NEVER per-row Python iteration.
Users range to 250K+ trophies; a Python loop here would OOM the worker (CLAUDE.md whale-safety rule).
"""
from django.db.models import Sum

# key -> callable(profile) -> int
MILESTONE_METRICS = {}


def milestone_metric(key):
    """Register a metric under `key`."""
    def deco(fn):
        if key in MILESTONE_METRICS:
            raise ValueError(f"Duplicate milestone metric key: {key!r}")
        MILESTONE_METRICS[key] = fn
        return fn
    return deco


def metric_value(key, profile) -> int:
    """Compute a metric for a profile. Returns 0 for an unknown key (caller logs)."""
    fn = MILESTONE_METRICS.get(key)
    if fn is None:
        return 0
    return fn(profile) or 0


# The short noun each metric counts, sub-labelling the card's focal number ("47 / platinums"). Display-only;
# a milestone on an unmapped metric just renders the number with no unit.
METRIC_UNITS = {
    "lifetime_platinums": "platinums",
    "lifetime_trophies": "trophies",
    "full_completions": "completions",
    "total_badges_earned": "badges",
    "pursuer_level": "levels",
    "playtime_hours": "hours",
    "community_months": "months",
    "premium_months": "months",
}


def metric_unit(key) -> str:
    """The short noun a metric counts (for the focal number's sub-label). '' when unmapped."""
    return METRIC_UNITS.get(key, "")


# ── The v1 metrics (one per starter milestone) ──────────────────────────────────────────────────────────

@milestone_metric("lifetime_platinums")
def _lifetime_platinums(profile) -> int:
    from trophies.models import ProfileGame
    return ProfileGame.objects.filter(profile=profile, has_plat=True).count()


@milestone_metric("lifetime_trophies")
def _lifetime_trophies(profile) -> int:
    return profile.total_trophies or 0  # denormalized on Profile


@milestone_metric("full_completions")
def _full_completions(profile) -> int:
    return profile.total_completes or 0  # denormalized on Profile (100% completions)


@milestone_metric("total_badges_earned")
def _total_badges_earned(profile) -> int:
    # New grouping-badge system: one UserGroupBadge row == one currently-held group badge (edition) -- the same
    # held-editions surface the Collection reads. (The legacy ProfileGamification.total_badges_earned counts the
    # retired UserBadge tiers.) Single COUNT, catalog-bounded -> whale-safe.
    from trophies.models import UserGroupBadge
    return UserGroupBadge.objects.filter(profile=profile).count()


@milestone_metric("pursuer_level")
def _pursuer_level(profile) -> int:
    """Pursuer Level -- delegated, NOT reimplemented.

    This used to be a bare `Sum(ProfileJobXP.level)`, which is a different number: a row is only
    materialized once a job is paid, so summing the rows misses the level-1 floor every untouched
    job carries. The "Pursuer Ascent" tiers were calibrated the other way -- seed_milestones says
    so outright ("A fresh linked account already sits at ~25 ... the first rung MUST clear the
    baseline") -- so measuring unfloored made every rung ~25 levels too expensive, permanently,
    since EarnedMilestoneTier rows are never deleted.

    `contract_service._pursuer_level` is the same bounded 2-query aggregate, and routing through it
    is what keeps this surface from drifting from the Career page and the leaderboard again."""
    from trophies.services.contract_service import _pursuer_level as pursuer_level
    return pursuer_level(profile)


@milestone_metric("playtime_hours")
def _playtime_hours(profile) -> int:
    from trophies.models import ProfileGame
    total = ProfileGame.objects.filter(profile=profile).aggregate(s=Sum('play_duration'))['s']
    return int(total.total_seconds() // 3600) if total else 0


@milestone_metric("community_months")
def _community_months(profile) -> int:
    # Whole months since the profile FIRST engaged with the community, via website sign-up (user.date_joined)
    # OR a verified Discord link (discord_linked_at) -- whichever came first. A Discord-only client counts
    # from their link date; a web user from sign-up. NOT Profile.created_at (can predate either for a synced
    # profile). 0 if neither.
    from django.utils import timezone
    starts = []
    user = getattr(profile, 'user', None)
    if user and user.date_joined:
        starts.append(user.date_joined)
    if getattr(profile, 'is_discord_verified', False) and getattr(profile, 'discord_linked_at', None):
        starts.append(profile.discord_linked_at)
    if not starts:
        return 0
    return max((timezone.now() - min(starts)).days // 30, 0)


@milestone_metric("premium_months")
def _premium_months(profile) -> int:
    # Whole months summed across the user's subscription periods (open periods count up to now).
    # Delegates to the one tenure implementation, shared with the membership page -- a parity test
    # pins the two surfaces to the same number.
    from users.services.subscription_service import SubscriptionService
    user = getattr(profile, 'user', None)
    if not user:
        return 0
    return SubscriptionService.premium_tenure(user)['total_months']
