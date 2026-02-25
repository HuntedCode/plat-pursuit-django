from django.db.models import Count, Q, Sum
from django.utils import timezone
from datetime import timedelta
from trophies.models import Profile, EarnedTrophy, Game, Badge, UserBadge, Concept, ProfileGamification

def compute_community_stats():
    now = timezone.now()
    week_ago = now - timedelta(days=7)

    profile_counts = Profile.objects.aggregate(
        total=Count('id'),
        weekly=Count('id', filter=Q(created_at__gte=week_ago))
    )
    trophy_counts = EarnedTrophy.objects.aggregate(
        total=Count('id', filter=Q(earned=True)),
        weekly=Count('id', filter=Q(earned=True, earned_date_time__gte=week_ago))
    )
    game_counts = Game.objects.aggregate(
        total=Count('id'),
        weekly=Count('id', filter=Q(created_at__gte=week_ago))
    )
    platinum_counts = EarnedTrophy.objects.aggregate(
        total=Count('id', filter=Q(earned=True, trophy__trophy_type='platinum')),
        weekly=Count('id', filter=Q(earned=True, trophy__trophy_type='platinum', earned_date_time__gte=week_ago))
    )

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
