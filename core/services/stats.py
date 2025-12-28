from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta
from trophies.models import Profile, EarnedTrophy, Game

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
    }