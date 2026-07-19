from django.db.models import Case, Count, F, IntegerField, Q, Sum, Value, When
from django.utils import timezone
from datetime import timedelta
from trophies.models import Profile, EarnedTrophy, Game, Trophy, Badge, Stage, UserBadge, Concept, ProfileGamification
from trophies.util_modules.constants import (
    BADGE_TIER_XP, BRONZE_STAGE_XP, GOLD_STAGE_XP, PLAT_STAGE_XP, SILVER_STAGE_XP,
)

def compute_community_stats():
    now = timezone.now()
    week_ago = now - timedelta(days=7)

    profile_counts = Profile.objects.aggregate(
        total=Count('id'),
        weekly=Count('id', filter=Q(created_at__gte=week_ago))
    )
    game_counts = Game.objects.aggregate(
        total=Count('id'),
        weekly=Count('id', filter=Q(created_at__gte=week_ago))
    )
    # Site-wide earned-trophy + platinum TOTALS read from the nightly denorms (Trophy.earned_count /
    # Game.plats_earned_count, kept fresh by recalc_earn_rates) rather than a full-table EarnedTrophy
    # aggregate. The live scan -- especially the platinum variant's join to Trophy -- scales with the
    # whale table and blew the statement timeout on the hourly heartbeat cron. These denorms are the same
    # source the game cards use, so the numbers stay consistent; they trail live by up to a nightly cycle,
    # which is fine for an hourly-cached community ribbon. WEEKLY counts stay live but are date-bounded, so
    # they ride the earned_date_time index (earned_trophy_earned_time_idx) instead of a full scan.
    trophy_counts = {
        'total': Trophy.objects.aggregate(t=Sum('earned_count'))['t'] or 0,
        'weekly': EarnedTrophy.objects.filter(earned=True, earned_date_time__gte=week_ago).count(),
    }
    platinum_counts = {
        'total': Game.objects.aggregate(t=Sum('plats_earned_count'))['t'] or 0,
        'weekly': EarnedTrophy.objects.filter(
            earned=True, trophy__trophy_type='platinum', earned_date_time__gte=week_ago
        ).count(),
    }

    # Badge series count (Tier 1 badges = unique series)
    badge_series_counts = Badge.objects.live().filter(tier=1).aggregate(
        total=Count('id'),
        weekly=Count('id', filter=Q(created_at__gte=week_ago))
    )

    # Total Badge XP earned across all users
    badge_xp = ProfileGamification.objects.aggregate(
        total=Sum('total_badge_xp')
    )

    # Unique concepts across all badge stages
    unique_concepts_total = Concept.objects.filter(
        stages__series_slug__isnull=False
    ).distinct().count()

    # Unique badges earned: sum of per-user distinct series counts
    per_user_unique = (
        UserBadge.objects.values('profile')
        .annotate(unique_series=Count('badge__series_slug', distinct=True))
        .aggregate(total=Sum('unique_series'))
    )
    per_user_weekly = (
        UserBadge.objects.filter(earned_at__gte=week_ago)
        .values('profile')
        .annotate(unique_series=Count('badge__series_slug', distinct=True))
        .aggregate(total=Sum('unique_series'))
    )
    badges_earned_counts = {
        'total': per_user_unique['total'] or 0,
        'weekly': per_user_weekly['total'] or 0,
    }

    # --- Catalog (collection) stats: what the badge collection OFFERS, independent of who earned it ---
    # Total stages to complete across live badge series (stage 0 is the non-counting base stage).
    live_series_slugs = Badge.objects.live().filter(tier=1).values_list('series_slug', flat=True)
    badge_stages_total = (
        Stage.objects.filter(series_slug__in=live_series_slugs)
        .exclude(stage_number=0)
        .count()
    )
    # Total earnable badge XP: per live badge, (required_stages * that tier's per-stage XP) + the
    # flat completion bonus. Mirrors xp_service's formula, aggregated over the whole live catalog.
    badge_earnable_xp = Badge.objects.live().aggregate(
        xp=Sum(
            Case(
                When(tier=1, then=F('required_stages') * BRONZE_STAGE_XP),
                When(tier=2, then=F('required_stages') * SILVER_STAGE_XP),
                When(tier=3, then=F('required_stages') * GOLD_STAGE_XP),
                When(tier=4, then=F('required_stages') * PLAT_STAGE_XP),
                default=Value(0),
                output_field=IntegerField(),
            ) + Value(BADGE_TIER_XP)
        )
    )['xp'] or 0

    return {
        'profiles': {
            'total': profile_counts['total'],
            'weekly': profile_counts['weekly'],
        },
        'trophies': {
            'total': trophy_counts['total'],
            'weekly': trophy_counts['weekly'],
        },
        'games': {
            'total': game_counts['total'],
            'weekly': game_counts['weekly'],
        },
        'platinums': {
            'total': platinum_counts['total'],
            'weekly': platinum_counts['weekly'],
        },
        'badge_series': {
            'total': badge_series_counts['total'],
            'weekly': badge_series_counts['weekly'],
        },
        'badge_stages': {
            'total': badge_stages_total,
        },
        'badge_earnable_xp': {
            'total': badge_earnable_xp,
        },
        'badge_xp': {
            'total': badge_xp['total'] or 0,
        },
        'concepts': {
            'total': unique_concepts_total,
        },
        'badges_earned': {
            'total': badges_earned_counts['total'],
            'weekly': badges_earned_counts['weekly'],
        },
    }
