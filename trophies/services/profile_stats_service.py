"""
Profile statistics service - Handles profile stat calculations and updates.

This service manages denormalized profile statistics:
- Trophy counts (total, by type)
- Game counts and completion statistics
- Average progress calculations
"""
from django.db.models import Sum
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
    Update filter-respecting profile totals.

    Updates:
    - total_trophies: Sum of ProfileGame.earned_trophies_count (filtered)
    - total_unearned: Sum of ProfileGame.unearned_trophies_count (filtered)
    - avg_progress: Derived from the above

    Respects profile settings:
    - hide_hiddens: Excludes hidden games from totals
    - hide_zeros: Excludes games with 0 trophies from totals

    The four type counters (total_bronzes/silvers/golds/plats) are NOT
    updated here — they're maintained incrementally by the EarnedTrophy
    signals in trophies/signals.py and reconciled by the daily
    `recalc_profile_counters` cron. They're unfiltered totals so the
    signal-based maintenance is correct regardless of the filter toggles.

    Used by:
    - PSN sync_complete (token_keeper) — refresh totals after sync writes
      new ProfileGame.earned_trophies_count values in Phase 1.
    - Profile settings POST (users/views) — recompute when the user
      toggles hide_hiddens / hide_zeros, since the filter changed.

    Args:
        profile: Profile instance to update
    """
    from trophies.models import ProfileGame

    trophy_totals = ProfileGame.objects.filter(profile=profile)

    if profile.hide_hiddens:
        trophy_totals = trophy_totals.filter(user_hidden=False)
    if profile.hide_zeros:
        trophy_totals = trophy_totals.exclude(earned_trophies_count=0)

    aggregates = trophy_totals.aggregate(
        unearned=Coalesce(Sum('unearned_trophies_count'), 0),
        earned=Coalesce(Sum('earned_trophies_count'), 0),
    )

    total_earned = aggregates['earned']
    total_unearned = aggregates['unearned']
    total = total_earned + total_unearned
    avg_progress = (total_earned / total * 100) if total > 0 else 0.0

    profile.total_trophies = total_earned
    profile.total_unearned = total_unearned
    profile.avg_progress = avg_progress

    profile.save(update_fields=['total_trophies', 'total_unearned', 'avg_progress'])
