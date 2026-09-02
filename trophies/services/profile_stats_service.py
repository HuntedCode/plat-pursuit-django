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
    - total_games: Count of games played
    - total_completes: Count of games at 100% completion

    Respects profile settings:
    - hide_hiddens: Excludes hidden games from totals (matches
      update_profile_trophy_counts so total_games and total_trophies
      come from the same filter logic).

    Args:
        profile: Profile instance to update
    """
    from trophies.models import ProfileGame

    qs = ProfileGame.objects.filter(profile=profile)
    if profile.hide_hiddens:
        qs = qs.filter(user_hidden=False)

    profile.total_games = qs.count()
    profile.total_completes = qs.filter(progress=100).count()
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


def trophy_snapshot(profile):
    """The profile's trophy collection summary, for display. ZERO queries -- every value is already on
    `Profile`.

    Lives here because this module owns `update_profile_trophy_counts`, which WRITES these same
    denormalized counters. It was `dashboard_service.provide_trophy_snapshot` until the dashboard was
    retired; read and write of one set of columns now sit beside each other.

    Two live consumers: the Home lobby's trophy card and `pursuer_card_service`.
    """

    total_earned = profile.total_trophies  # total_trophies is already the earned count
    total_all = total_earned + profile.total_unearned
    return {
        'total_plats': profile.total_plats,
        'total_golds': profile.total_golds,
        'total_silvers': profile.total_silvers,
        'total_bronzes': profile.total_bronzes,
        'total_trophies': total_all,
        'total_earned': total_earned,
        'total_unearned': profile.total_unearned,
        'total_games': profile.total_games,
        'total_completes': profile.total_completes,
        'total_hiddens': profile.total_hiddens,
        'avg_progress': profile.avg_progress,
        'trophy_level': profile.trophy_level,
        'tier': profile.tier,
        'is_plus': profile.is_plus,
        'earn_rate': round(total_earned / total_all * 100, 1) if total_all else 0,
    }
