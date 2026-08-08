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
    gamification = getattr(profile, 'gamification', None)  # OneToOne (may be absent pre-sync)
    return gamification.total_badges_earned if gamification else 0


@milestone_metric("pursuer_level")
def _pursuer_level(profile) -> int:
    # Pursuer Level = sum of the profile's ~24 per-job levels (a bounded aggregate).
    from trophies.models import ProfileJobXP
    return ProfileJobXP.objects.filter(profile=profile).aggregate(s=Sum('level'))['s'] or 0


@milestone_metric("playtime_hours")
def _playtime_hours(profile) -> int:
    from trophies.models import ProfileGame
    total = ProfileGame.objects.filter(profile=profile).aggregate(s=Sum('play_duration'))['s']
    return int(total.total_seconds() // 3600) if total else 0


@milestone_metric("community_months")
def _community_months(profile) -> int:
    # Whole months since the user SIGNED UP (user.date_joined) -- not Profile.created_at, which can predate
    # registration for a synced/scouted profile. 0 for a profile with no registered user yet.
    from django.utils import timezone
    user = getattr(profile, 'user', None)
    if not user or not user.date_joined:
        return 0
    return max((timezone.now() - user.date_joined).days // 30, 0)


@milestone_metric("premium_months")
def _premium_months(profile) -> int:
    # Whole months summed across the user's subscription periods (open periods count up to now). Bounded per
    # user (a handful of periods), so the small Python sum is whale-safe.
    from django.utils import timezone
    from users.models import SubscriptionPeriod
    user = getattr(profile, 'user', None)
    if not user:
        return 0
    now = timezone.now()
    total_days = 0
    for started, ended in SubscriptionPeriod.objects.filter(user=user).values_list('started_at', 'ended_at'):
        if started:
            total_days += max(((ended or now) - started).days, 0)
    return int(total_days // 30)
