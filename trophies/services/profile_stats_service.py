"""
Profile statistics service - Handles profile stat calculations and updates.

This service manages denormalized profile statistics:
- Trophy counts (total, by type)
- Game counts and completion statistics
- Average progress calculations
"""
from django.db.models import Sum, Count, Q
from django.db.models.functions import Coalesce


def update_profile_games(profile):
    """
    Update denormalized game counts for a profile.

    Updates:
    - total_games: Count of all games played
    - total_completes: Count of games at 100% completion

    Args:
        profile: Profile instance to update
    """
    from trophies.models import ProfileGame

    profile.total_games = ProfileGame.objects.filter(profile=profile).count()
    profile.total_completes = ProfileGame.objects.filter(
        profile=profile, progress=100
    ).count()
    profile.save(update_fields=['total_games', 'total_completes'])


def update_profile_trophy_counts(profile):
    """
    Update denormalized trophy counts and average progress for a profile.

    This function recalculates and stores:
    - total_trophies: Total earned trophies
    - total_unearned: Total unearned trophies
    - total_bronzes: Count of earned bronze trophies
    - total_silvers: Count of earned silver trophies
    - total_golds: Count of earned gold trophies
    - total_plats: Count of earned platinum trophies
    - avg_progress: Average completion percentage across all games

    Respects profile settings:
    - hide_hiddens: Excludes hidden games from calculations if enabled
    - hide_zeros: Excludes games with 0 trophies if enabled

    Args:
        profile: Profile instance to update
    """
    from trophies.models import EarnedTrophy, ProfileGame

    trophy_totals = ProfileGame.objects.filter(profile=profile)

    # Apply profile filters
    if profile.hide_hiddens:
        trophy_totals = trophy_totals.filter(user_hidden=False)
    if profile.hide_zeros:
        trophy_totals = trophy_totals.exclude(earned_trophies_count=0)

    # Aggregate trophy counts from ProfileGame denormalized fields
    aggregates = trophy_totals.aggregate(
        unearned=Coalesce(Sum('unearned_trophies_count'), 0),
        earned=Coalesce(Sum('earned_trophies_count'), 0),
    )

    total_earned = aggregates['earned']
    total_unearned = aggregates['unearned']

    # Calculate average progress
    total = total_earned + total_unearned
    avg_progress = (total_earned / total * 100) if total > 0 else 0.0

    # Update trophy counts by type using single aggregation query
    trophy_counts = EarnedTrophy.objects.filter(profile=profile, earned=True).aggregate(
        bronze=Count('id', filter=Q(trophy__trophy_type='bronze')),
        silver=Count('id', filter=Q(trophy__trophy_type='silver')),
        gold=Count('id', filter=Q(trophy__trophy_type='gold')),
        platinum=Count('id', filter=Q(trophy__trophy_type='platinum')),
    )

    profile.total_trophies = total_earned
    profile.total_unearned = total_unearned
    profile.total_bronzes = trophy_counts['bronze']
    profile.total_silvers = trophy_counts['silver']
    profile.total_golds = trophy_counts['gold']
    profile.total_plats = trophy_counts['platinum']
    profile.avg_progress = avg_progress

    profile.save(update_fields=[
        'total_trophies', 'total_unearned', 'total_bronzes',
        'total_silvers', 'total_golds', 'total_plats', 'avg_progress'
    ])
