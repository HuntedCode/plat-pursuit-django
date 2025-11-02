from django.db.models import Count
from django.utils import timezone
from datetime import timedelta
from trophies.models import Profile, EarnedTrophy, Game

def compute_community_stats():
    now = timezone.now()
    week_ago = now - timedelta(days=7)

    return {
        'profiles': {
            'total': Profile.objects.count(),
            'weekly': Profile.objects.filter(created_at__gte=week_ago).count(),
        },
        'trophies': {
            'total': EarnedTrophy.objects.filter(earned=True).count(),
            'weekly': EarnedTrophy.objects.filter(earned=True, earned_date_time__gte=week_ago).count(),
        },
        'games': {
            'total': Game.objects.count(),
            'weekly': Game.objects.filter(created_at__gte=week_ago).count(),
        },
        'platinums': {
            'total': EarnedTrophy.objects.filter(earned=True, trophy__trophy_type='platinum').count(),
            'weekly': EarnedTrophy.objects.filter(earned=True, trophy__trophy_type='platinum', earned_date_time__gte=week_ago).count(),
        },
    }